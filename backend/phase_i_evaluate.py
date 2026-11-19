"""
phase_i_evaluate.py  (Phase I, step 2)   reads CSV files only, no database.

    python phase_i_evaluate.py ^
        --scores evaluation/engine_scores_PRIVATE.csv ^
        --rater evaluation/relevance_rater_A.csv ^
        --rater evaluation/relevance_rater_B.csv

Compares: random, most popular, profile match (skills/interests/goal/category),
keyword only, semantic only, and the full hybrid (engine without feedback,
timetable or learned signals), against HUMAN relevance judgments (0/1/2).

Writes into evaluation/results_*: summary, paired differences, per-student
values and a short report. Everything is small-sample and subjective by
design; the report says so.
"""
import argparse
import csv
import math
import os
import sys
import warnings

import numpy as np

METHODS = ["random", "popularity", "profile_match", "keyword_only",
           "semantic_only", "hybrid_full"]
LABELS = {
    "random": "Random", "popularity": "Most popular",
    "profile_match": "Profile match", "keyword_only": "Keyword only",
    "semantic_only": "Semantic only", "hybrid_full": "Full hybrid",
}
REL_THRESHOLD = 1.0      # consensus relevance >= 1 ("somewhat relevant") counts as relevant
METRICS = ["precision", "hit", "ndcg", "map", "diversity"]


# ---------------------------------------------------------------- reading
def read_csv(path):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Cannot read {path} (unknown encoding)")


def load_ratings(path):
    """-> (ratings {pair_id: float}, missing [pair_id], invalid [(pair_id, text)])"""
    ratings, missing, invalid = {}, [], []
    for row in read_csv(path):
        pid = (row.get("pair_id") or "").strip()
        raw = (row.get("relevance") or "").strip()
        if not pid:
            continue
        if raw == "":
            missing.append(pid)
            continue
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            invalid.append((pid, raw))
            continue
        if v not in (0.0, 1.0, 2.0):
            invalid.append((pid, raw))
            continue
        ratings[pid] = v
    return ratings, missing, invalid


def load_scores(path):
    """-> list of students; each {"label", "items": [{pair_id,event_id,category,scores}]}.
    A method with any blank value is dropped for everybody."""
    rows = read_csv(path)
    if not rows:
        raise SystemExit("Score file is empty.")
    available = [m for m in METHODS if m != "random" and m in rows[0]
                 and all((r.get(m) or "").strip() != "" for r in rows)]
    by = {}
    for r in rows:
        label = r["pair_id"].split("-")[0]
        item = {"pair_id": r["pair_id"], "event_id": r["event_id"],
                "category": r.get("category", ""),
                "scores": {m: float(r[m]) for m in available}}
        by.setdefault(label, []).append(item)
    students = [{"label": k, "items": v} for k, v in by.items()]
    students.sort(key=lambda s: int(s["label"][1:]))
    return students, ["random"] + available


# --------------------------------------------------------------- agreement
def quadratic_kappa(a, b, levels=3):
    a, b = np.asarray(a, int), np.asarray(b, int)
    if len(a) == 0:
        return None
    O = np.zeros((levels, levels))
    for x, y in zip(a, b):
        O[x, y] += 1
    n = O.sum()
    W = np.array([[(i - j) ** 2 / (levels - 1) ** 2 for j in range(levels)]
                  for i in range(levels)])
    E = np.outer(O.sum(1), O.sum(0)) / n
    den = (W * E).sum()
    if den == 0:
        return None
    return float(1 - (W * O).sum() / den)


def agreement(r1, r2):
    ids = sorted(set(r1) & set(r2))
    a = [int(r1[i]) for i in ids]
    b = [int(r2[i]) for i in ids]
    exact = float(np.mean([x == y for x, y in zip(a, b)])) if ids else None
    both_bin = float(np.mean([(x >= 1) == (y >= 1) for x, y in zip(a, b)])) if ids else None
    return {"n": len(ids), "quadratic_kappa": quadratic_kappa(a, b),
            "exact_agreement": exact, "relevant_vs_not_agreement": both_bin}


def consensus(rating_dicts):
    ids = set.intersection(*[set(r) for r in rating_dicts])
    return {i: float(np.mean([r[i] for r in rating_dicts])) for i in ids}


# ----------------------------------------------------------------- metrics
def student_metrics(order, gains, categories, k, kn):
    """order: indices best first. gains: consensus relevance per item (0..2)."""
    g = np.asarray(gains, float)
    n = len(g)
    k, kn = min(k, n), min(kn, n)
    rel = g >= REL_THRESHOLD
    out = {"precision": float(rel[order[:k]].sum() / k),
           "hit": math.nan, "ndcg": math.nan, "map": math.nan,
           "diversity": len({categories[i] for i in order[:k]}) / k}
    if g.max() > 0:
        disc = 1 / np.log2(np.arange(2, kn + 2))
        dcg = ((2 ** g[order[:kn]] - 1) * disc).sum()
        ideal = np.sort(g)[::-1][:kn]
        out["ndcg"] = float(dcg / ((2 ** ideal - 1) * disc).sum())
    if rel.sum() > 0:
        out["hit"] = 1.0 if rel[order[:k]].any() else 0.0
        hits, s = 0, 0.0
        for rank, idx in enumerate(order[:kn], 1):
            if rel[idx]:
                hits += 1
                s += hits / rank
        out["map"] = s / min(kn, int(rel.sum()))
    return out


def rank_with_ties(scores, rng):
    """Best first; equal scores are ordered randomly (so ties get no free ride)."""
    return np.lexsort((rng.random(len(scores)), -np.asarray(scores, float)))


def evaluate(students, methods, gains_by_pair, k=3, kn=5, draws=200, seed=0):
    """-> per_student {method: {metric: array over students}}, coverage {method: float}"""
    rng = np.random.default_rng(seed)
    n_students = len(students)
    all_events = sorted({it["event_id"] for s in students for it in s["items"]})
    per = {m: {x: np.full(n_students, math.nan) for x in METRICS} for m in methods}
    cov = {}
    for m in methods:
        acc = {x: np.zeros(n_students) for x in METRICS}
        cov_draws = []
        for _ in range(draws):
            shown = set()
            for si, s in enumerate(students):
                items = s["items"]
                scores = ([0.0] * len(items) if m == "random"
                          else [it["scores"][m] for it in items])
                order = rank_with_ties(scores, rng)
                if m == "random":
                    order = rng.permutation(len(items))
                gains = [gains_by_pair[it["pair_id"]] for it in items]
                cats = [it["category"] for it in items]
                res = student_metrics(order, gains, cats, k, kn)
                for x in METRICS:
                    acc[x][si] += res[x]            # nan stays nan
                shown.update(items[i]["event_id"] for i in order[:min(k, len(items))])
            cov_draws.append(len(shown) / max(1, len(all_events)))
        for x in METRICS:
            per[m][x] = acc[x] / draws
        cov[m] = float(np.mean(cov_draws))
    return per, cov


# --------------------------------------------------------------- bootstrap
def _nanmean(a, axis):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(a, axis=axis)


def bootstrap_ci(values, idx):
    v = np.asarray(values, float)
    means = _nanmean(v[idx], axis=1)
    if np.isnan(means).all():
        return math.nan, math.nan
    return float(np.nanpercentile(means, 2.5)), float(np.nanpercentile(means, 97.5))


def summarise(per, methods, n_students, boot=5000, seed=0):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_students, size=(boot, n_students))   # same resamples for all
    rows, diffs = [], []
    for m in methods:
        for x in METRICS:
            v = per[m][x]
            lo, hi = bootstrap_ci(v, idx)
            rows.append({"method": m, "metric": x,
                         "mean": float(_nanmean(v, 0)) if not np.isnan(v).all() else math.nan,
                         "ci_low": lo, "ci_high": hi,
                         "n_students": int((~np.isnan(v)).sum())})
    if "hybrid_full" in methods:
        for m in methods:
            if m == "hybrid_full":
                continue
            for x in METRICS:
                d = per["hybrid_full"][x] - per[m][x]
                lo, hi = bootstrap_ci(d, idx)
                diffs.append({"hybrid_minus": m, "metric": x,
                              "mean_diff": float(_nanmean(d, 0)) if not np.isnan(d).all() else math.nan,
                              "ci_low": lo, "ci_high": hi,
                              "clearly_better": bool(lo > 0), "clearly_worse": bool(hi < 0)})
    return rows, diffs


# ------------------------------------------------------------------ output
def fmt(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_report(path, rows, diffs, cov, agree, n_students, n_events, n_raters, k, kn):
    L = []
    L.append("# Phase I: human-judgment evaluation\n")
    L.append(f"{n_students} real student profiles x {n_events} real published events = "
             f"{n_students * n_events} pairs, judged 0/1/2 by {n_raters} rater(s). "
             "Relevant means consensus rating >= 1. This is a SMALL, SUBJECTIVE sample: "
             "intervals are wide and differences are indications, not proof. It is not "
             "an evaluation on real student behaviour.\n")
    if agree:
        L.append(f"Rater agreement (n={agree['n']} pairs): quadratic-weighted kappa "
                 f"{fmt(agree['quadratic_kappa'])}, exact agreement {fmt(agree['exact_agreement'])}, "
                 f"relevant-vs-not agreement {fmt(agree['relevant_vs_not_agreement'])}.\n")
    names = {"precision": f"Precision@{k}", "hit": f"HitRate@{k}", "ndcg": f"nDCG@{kn}",
             "map": f"MAP@{kn}", "diversity": f"CategoryDiversity@{k}"}
    L.append("## Mean over students (95% bootstrap interval)\n")
    L.append("| Method | " + " | ".join(names[x] for x in METRICS) + f" | Coverage@{k} |")
    L.append("|---|" + "---|" * (len(METRICS) + 1))
    for m in dict.fromkeys(r["method"] for r in rows):
        cells = []
        for x in METRICS:
            r = next(q for q in rows if q["method"] == m and q["metric"] == x)
            cells.append(f"{fmt(r['mean'])} [{fmt(r['ci_low'])}, {fmt(r['ci_high'])}]")
        L.append(f"| {LABELS[m]} | " + " | ".join(cells) + f" | {fmt(cov[m])} |")
    if diffs:
        L.append("\n## Full hybrid minus each baseline (paired, 95% interval)\n")
        L.append("| Baseline | Metric | Difference | Interval | Verdict |")
        L.append("|---|---|---|---|---|")
        for d in diffs:
            hi_w, lo_w = (("higher", "lower") if d["metric"] == "diversity"
                          else ("clearly better", "clearly worse"))
            verdict = (f"hybrid {hi_w}" if d["clearly_better"]
                       else f"hybrid {lo_w}" if d["clearly_worse"] else "no clear difference")
            L.append(f"| {LABELS[d['hybrid_minus']]} | {names[d['metric']]} | "
                     f"{fmt(d['mean_diff'])} | [{fmt(d['ci_low'])}, {fmt(d['ci_high'])}] | {verdict} |")
    L.append("\nMany comparisons are made on very few students and nothing is corrected "
             "for that, so a single 'clearly' verdict can be chance. Treat Precision and nDCG as "
             "the main results and read the rest as supporting detail.\n")
    L.append("\nTies in a method's scores are broken at random and averaged over many draws. "
             "Students with no relevant event are left out of HitRate, nDCG and MAP "
             "(the table shows how many students each value uses in results_summary.csv). "
             "Coverage is the share of events shown to at least one student.\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default="evaluation/engine_scores_PRIVATE.csv")
    ap.add_argument("--rater", action="append", required=True,
                    help="filled sheet; give it twice for two raters")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--kn", type=int, default=5)
    ap.add_argument("--draws", type=int, default=200)
    ap.add_argument("--boot", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="evaluation/results")
    a = ap.parse_args()

    students, methods = load_scores(a.scores)
    expected = {it["pair_id"] for s in students for it in s["items"]}

    rating_dicts = []
    for path in a.rater:
        ratings, missing, invalid = load_ratings(path)
        if invalid:
            raise SystemExit(f"{path}: invalid values (use 0, 1 or 2 only): {invalid[:10]}")
        if missing or expected - set(ratings):
            left = sorted(set(missing) | (expected - set(ratings)))
            raise SystemExit(f"{path}: {len(left)} pairs still blank, e.g. {left[:10]}")
        rating_dicts.append(ratings)

    gains = consensus(rating_dicts)
    agree = agreement(rating_dicts[0], rating_dicts[1]) if len(rating_dicts) >= 2 else None
    n_events = len(students[0]["items"])
    per, cov = evaluate(students, methods, gains, a.k, a.kn, a.draws, a.seed)
    rows, diffs = summarise(per, methods, len(students), a.boot, a.seed)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    write_csv(a.out + "_summary.csv", rows)
    write_csv(a.out + "_hybrid_vs_baselines.csv", diffs)
    write_csv(a.out + "_per_student.csv",
              [{"student": s["label"], "method": m, "metric": x, "value": per[m][x][i]}
               for m in methods for x in METRICS for i, s in enumerate(students)])
    write_report(a.out + "_report.md", rows, diffs, cov, agree,
                 len(students), n_events, len(rating_dicts), min(a.k, n_events), min(a.kn, n_events))
    print(open(a.out + "_report.md", encoding="utf-8").read())
    print(f"\nwrote {a.out}_summary.csv, _hybrid_vs_baselines.csv, _per_student.csv, _report.md")


if __name__ == "__main__":
    sys.exit(main())