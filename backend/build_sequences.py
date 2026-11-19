"""
build_sequences.py
==================
PHASE B of the recommender roadmap: turn recorded student behaviour into
training data for the content-aware sequential model (BERT4Rec-style).

What it does
------------
1. Reads interactions (real ones from `interactions`, and/or simulated ones
   from `interactions_simulated`).
2. Groups them per student, in time order.
3. Turns every interaction into a token made of STABLE event properties:
       action  +  category  +  organizer/club
   Event ids are NOT used, because every event happens once and expires; a
   brand-new event can still be scored through its category and club.
4. Splits the data by TIME (not randomly), so the model is never trained on
   anything that happened after what it is tested on.
5. Writes train / val / test files, a vocabulary built from the training
   part only, and a report.

Output folder (default data/sequences/<source>/, e.g. data/sequences/simulated/)
-------------------------------------------------------------------------------
Real and simulated builds go to DIFFERENT folders by default, so one can never
overwrite the other. Pass --out to choose a folder yourself.

    train.jsonl   one training sequence per line
    val.jsonl     {history, targets} examples from the validation period
    test.jsonl    {history, targets} examples from the test period
    vocab.json    ids for actions / categories / organizers (+ [PAD] [MASK] [UNK])
    report.json   machine-readable counts
    report.md     human-readable summary (warns when data is simulated)

Run (from the backend folder)
-----------------------------
    python build_sequences.py --source simulated
    python build_sequences.py --source real
    python build_sequences.py --source both

Honesty rules built in
----------------------
* Impressions are left out by default (a page load makes many of them and
  they only mean "was in a list"). Use --include-impressions to add them.
* Simulated sequences carry "simulated": true and the report says so loudly.
* This file never imports the database at import time, so it can be tested
  without MongoDB. `database` is imported only when the script is run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------
REAL_COLLECTION = "interactions"
SIM_COLLECTION = "interactions_simulated"

PAD, MASK, UNK = "[PAD]", "[MASK]", "[UNK]"
SPECIAL_TOKENS: Dict[str, int] = {PAD: 0, MASK: 1, UNK: 2}
FIRST_FREE_ID = len(SPECIAL_TOKENS)

# Actions that become part of a sequence. "impression" is excluded by default.
DEFAULT_ACTIONS: Tuple[str, ...] = (
    "view", "save", "unsave", "register", "unregister", "attend", "not_interested",
)
IMPRESSION = "impression"

DEFAULT_MAX_LEN = 30        # the roadmap suggests 20-50
DEFAULT_MIN_LEN = 3         # same threshold the engine will use in Phase D
DEFAULT_TRAIN_FRAC = 0.70
DEFAULT_VAL_FRAC = 0.15
DEFAULT_MAX_TARGETS = 10


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Any) -> Optional[datetime]:
    """datetime / ISO text -> timezone-aware UTC datetime (or None)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return as_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def norm_text(value: Any, default: str = "unknown", limit: int = 60) -> str:
    """Lower-case, single-spaced, trimmed. Empty -> `default`."""
    text = " ".join(str(value or "").split()).lower()[:limit]
    return text or default


def make_token(action: str, category: str, organizer: str) -> str:
    return f"{action}:{category}@{organizer}"


# ------------------------------------------------------------------
# Rows -> per-student, time-ordered items
# ------------------------------------------------------------------
def make_item(row: Dict[str, Any], allowed_actions: Iterable[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Returns (item, status). status: "ok" | "filtered" | "invalid".
    "filtered" = action not wanted (e.g. impression); "invalid" = unusable row.
    """
    action = str(row.get("action") or "").strip().lower()
    if action not in allowed_actions:
        return None, "filtered"

    student_id = str(row.get("student_id") or "").strip()
    timestamp = as_utc(row.get("timestamp"))
    if not student_id or timestamp is None:
        return None, "invalid"

    category = norm_text(row.get("category"))
    organizer = norm_text(row.get("organizer"))

    return {
        "student_id": student_id,
        "event_id": str(row.get("event_id") or ""),
        "action": action,
        "category": category,
        "organizer": organizer,
        "ts": timestamp,
        "simulated": bool(row.get("simulated")),
        "token": make_token(action, category, organizer),
    }, "ok"


def prepare_items(
    rows: Iterable[Dict[str, Any]],
    allowed_actions: Iterable[str],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Counter]:
    allowed = set(allowed_actions)
    stats: Counter = Counter()
    grouped: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}

    for index, row in enumerate(rows):
        stats["read"] += 1
        item, status = make_item(row, allowed)
        if status != "ok":
            stats[status] += 1
            continue
        stats["used"] += 1
        stats["by_action:" + item["action"]] += 1
        grouped.setdefault(item["student_id"], []).append((index, item))

    by_student: Dict[str, List[Dict[str, Any]]] = {}
    for student_id, pairs in grouped.items():
        pairs.sort(key=lambda pair: (pair[1]["ts"], pair[0]))   # stable
        by_student[student_id] = [item for _, item in pairs]
    return by_student, stats


# ------------------------------------------------------------------
# Temporal split
# ------------------------------------------------------------------
def compute_cutoffs(
    timestamps: Sequence[datetime],
    train_frac: float,
    val_frac: float,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Two moments in time. Everything before the first is training data,
    between the two is the validation period, from the second on is test.
    """
    ordered = sorted(timestamps)
    if not ordered:
        return None, None
    n = len(ordered)
    i1 = min(n - 1, int(n * train_frac))
    i2 = min(n - 1, int(n * (train_frac + val_frac)))
    return ordered[i1], ordered[i2]


def window_items(items: Sequence[Dict[str, Any]], max_len: int, stride: int) -> List[List[Dict[str, Any]]]:
    """
    Sequences longer than max_len are cut into overlapping windows, newest
    window aligned to the end, so no training data is thrown away.
    """
    n = len(items)
    if n <= max_len:
        return [list(items)] if n else []

    windows: List[List[Dict[str, Any]]] = []
    end = n
    while True:
        start = max(0, end - max_len)
        windows.append(list(items[start:end]))
        if start == 0:
            break
        end -= max(1, stride)
    return windows[::-1]


def pack(items: Sequence[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Parallel lists, one entry per interaction (this is what Phase C reads)."""
    return {
        "actions": [i["action"] for i in items],
        "categories": [i["category"] for i in items],
        "organizers": [i["organizer"] for i in items],
        "event_ids": [i["event_id"] for i in items],     # for evaluation only, not a model input
        "timestamps": [i["ts"].isoformat() for i in items],
        "tokens": [i["token"] for i in items],            # human-readable
    }


# ------------------------------------------------------------------
# Vocabulary (built from TRAINING data only) and encoding
# ------------------------------------------------------------------
def build_vocab(
    train_items: Iterable[Dict[str, Any]],
    actions: Sequence[str],
    max_len: int,
) -> Dict[str, Any]:
    categories: Counter = Counter()
    organizers: Counter = Counter()
    for item in train_items:
        categories[item["category"]] += 1
        organizers[item["organizer"]] += 1

    def ids(counter: Counter) -> Dict[str, int]:
        ordered = sorted(counter, key=lambda key: (-counter[key], key))
        return {**SPECIAL_TOKENS, **{k: FIRST_FREE_ID + n for n, k in enumerate(ordered)}}

    # The action list is fixed by configuration (not by the data) so that
    # action ids never change between training runs.
    action_ids = {**SPECIAL_TOKENS, **{a: FIRST_FREE_ID + n for n, a in enumerate(actions)}}

    return {
        "special_tokens": dict(SPECIAL_TOKENS),
        "actions": action_ids,
        "categories": ids(categories),
        "organizers": ids(organizers),
        "max_len": max_len,
    }


def encode_sequence(packed: Dict[str, List[Any]], vocab: Dict[str, Any]) -> Dict[str, List[int]]:
    """Strings -> ids. Anything not seen in training becomes [UNK]."""
    unk = SPECIAL_TOKENS[UNK]
    return {
        "actions": [vocab["actions"].get(a, unk) for a in packed["actions"]],
        "categories": [vocab["categories"].get(c, unk) for c in packed["categories"]],
        "organizers": [vocab["organizers"].get(o, unk) for o in packed["organizers"]],
    }


# ------------------------------------------------------------------
# The main pipeline (pure: rows in, datasets out)
# ------------------------------------------------------------------
def build_datasets(
    rows: Iterable[Dict[str, Any]],
    *,
    actions: Sequence[str] = DEFAULT_ACTIONS,
    max_len: int = DEFAULT_MAX_LEN,
    min_len: int = DEFAULT_MIN_LEN,
    train_frac: float = DEFAULT_TRAIN_FRAC,
    val_frac: float = DEFAULT_VAL_FRAC,
    max_targets: int = DEFAULT_MAX_TARGETS,
    stride: Optional[int] = None,
    train_end: Optional[datetime] = None,
    val_end: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not 2 <= max_len <= 200:
        raise ValueError("max_len must be between 2 and 200")
    if min_len < 1 or min_len > max_len:
        raise ValueError("min_len must be between 1 and max_len")
    if not (0 < train_frac < 1) or val_frac < 0 or train_frac + val_frac >= 1:
        raise ValueError("need 0 < train_frac < 1, val_frac >= 0 and train_frac + val_frac < 1")

    rows = list(rows)
    stride = stride or max(1, max_len // 2)
    by_student, stats = prepare_items(rows, actions)

    all_ts = [item["ts"] for items in by_student.values() for item in items]
    if train_end is not None and val_end is not None:
        t1, t2 = as_utc(train_end), as_utc(val_end)
    else:
        t1, t2 = compute_cutoffs(all_ts, train_frac, val_frac)

    train: List[Dict[str, Any]] = []
    val: List[Dict[str, Any]] = []
    test: List[Dict[str, Any]] = []
    train_items_all: List[Dict[str, Any]] = []

    if t1 is not None and t2 is not None:
        for student_id in sorted(by_student):
            items = by_student[student_id]
            simulated = bool(items[0]["simulated"])

            train_part = [i for i in items if i["ts"] < t1]
            val_part = [i for i in items if t1 <= i["ts"] < t2]
            test_part = [i for i in items if i["ts"] >= t2]
            train_items_all.extend(train_part)

            for window in window_items(train_part, max_len, stride):
                if len(window) >= min_len:
                    train.append({"student_id": student_id, "simulated": simulated, "sequence": pack(window)})

            history = train_part[-max_len:]
            if len(history) >= min_len and val_part:
                val.append({
                    "student_id": student_id, "simulated": simulated,
                    "history": pack(history), "targets": pack(val_part[:max_targets]),
                })

            history = (train_part + val_part)[-max_len:]
            if len(history) >= min_len and test_part:
                test.append({
                    "student_id": student_id, "simulated": simulated,
                    "history": pack(history), "targets": pack(test_part[:max_targets]),
                })

    vocab = build_vocab(train_items_all, actions, max_len)

    report = _build_report(
        stats, by_student, train, val, test, vocab, t1, t2,
        settings={
            "actions": list(actions), "max_len": max_len, "min_len": min_len,
            "train_frac": train_frac, "val_frac": val_frac,
            "max_targets": max_targets, "window_stride": stride,
        },
    )
    return {"train": train, "val": val, "test": test, "vocab": vocab, "report": report}


def _count_split(records: List[Dict[str, Any]]) -> Dict[str, int]:
    simulated = sum(1 for r in records if r["simulated"])
    return {"total": len(records), "real": len(records) - simulated, "simulated": simulated}


def _unseen_fraction(records: List[Dict[str, Any]], vocab: Dict[str, Any], key: str, vocab_key: str) -> Optional[float]:
    values = [v for r in records for v in r["targets"][key]]
    if not values:
        return None
    known = vocab[vocab_key]
    return round(sum(1 for v in values if v not in known) / len(values), 4)


def _build_report(stats, by_student, train, val, test, vocab, t1, t2, settings) -> Dict[str, Any]:
    simulated_students = sum(1 for items in by_student.values() if items[0]["simulated"])
    real_students = len(by_student) - simulated_students
    min_len = settings["min_len"]
    real_enough = sum(
        1 for items in by_student.values()
        if not items[0]["simulated"] and len(items) >= min_len
    )
    eval_records = val + test
    return {
        "generated_at": utcnow().isoformat(),
        "settings": settings,
        "cutoffs": {
            "train_end": t1.isoformat() if t1 else None,
            "val_end": t2.isoformat() if t2 else None,
            "note": "train < train_end <= validation < val_end <= test",
        },
        "rows": {
            "read": stats["read"],
            "used": stats["used"],
            "left_out_by_action_filter": stats["filtered"],
            "invalid": stats["invalid"],
            "by_action": {k.split(":", 1)[1]: v for k, v in sorted(stats.items()) if k.startswith("by_action:")},
        },
        "students": {
            "with_interactions": len(by_student),
            "real": real_students,
            "simulated": simulated_students,
            "real_with_at_least_min_len_interactions": real_enough,
        },
        "sequences": {
            "train": _count_split(train),
            "val": _count_split(val),
            "test": _count_split(test),
        },
        "vocab_sizes": {
            "actions": len(vocab["actions"]),
            "categories": len(vocab["categories"]),
            "organizers": len(vocab["organizers"]),
        },
        "unseen_in_evaluation_targets": {
            "categories_not_in_train_vocab": _unseen_fraction(eval_records, vocab, "categories", "categories"),
            "organizers_not_in_train_vocab": _unseen_fraction(eval_records, vocab, "organizers", "organizers"),
        },
        "contains_simulated_data": simulated_students > 0,
    }


def render_report_md(report: Dict[str, Any], source: str) -> str:
    lines = ["# Sequence data report", "", f"Data source requested: **{source}**", ""]
    seq = report["sequences"]
    sim_total = sum(seq[k]["simulated"] for k in ("train", "val", "test"))
    if report["contains_simulated_data"]:
        lines += [
            "> **SIMULATED DATA WARNING**",
            f"> {report['students']['simulated']} students and {sim_total} sequences in these files are "
            "SIMULATED (generated by simulate_interactions.py). They are not real student behaviour.",
            "> A model trained or evaluated on them can only reflect the assumptions built into the "
            "simulator. Do not report results on them as real-world accuracy.",
            "",
        ]
    st = report["students"]
    lines += [
        "## Students",
        f"- with interactions: {st['with_interactions']} (real {st['real']}, simulated {st['simulated']})",
        f"- real students with at least {report['settings']['min_len']} interactions: "
        f"{st['real_with_at_least_min_len_interactions']}",
        "",
        "## Sequences",
    ]
    for name in ("train", "val", "test"):
        c = seq[name]
        lines.append(f"- {name}: {c['total']} (real {c['real']}, simulated {c['simulated']})")
    cut = report["cutoffs"]
    lines += [
        "",
        "## Temporal split",
        f"- train: before {cut['train_end']}",
        f"- validation: from {cut['train_end']} to {cut['val_end']}",
        f"- test: from {cut['val_end']}",
        "",
        "## Vocabulary (from training data only)",
        f"- sizes: {report['vocab_sizes']}",
        f"- evaluation targets whose category / club was never seen in training: "
        f"{report['unseen_in_evaluation_targets']}",
        "",
        "## Rows",
        f"- read {report['rows']['read']}, used {report['rows']['used']}, "
        f"left out by action filter {report['rows']['left_out_by_action_filter']}, "
        f"invalid {report['rows']['invalid']}",
        f"- by action: {report['rows']['by_action']}",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------
# Database + files (db is passed in; nothing is imported at module level)
# ------------------------------------------------------------------
def load_rows(
    db,
    source: str,
    actions: Sequence[str],
    sim_run_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    source: "real" | "simulated" | "both".
    Real rows come only from `interactions`, and any row flagged simulated
    that ended up there is still left out. Simulated rows come only from
    `interactions_simulated`.
    """
    if source not in ("real", "simulated", "both"):
        raise ValueError("source must be real, simulated or both")

    rows: List[Dict[str, Any]] = []

    if source in ("real", "both"):
        rows += list(db[REAL_COLLECTION].find(
            {"action": {"$in": list(actions)}, "simulated": {"$ne": True}},
            {"_id": 0},
        ))

    if source in ("simulated", "both"):
        query: Dict[str, Any] = {"action": {"$in": list(actions)}}
        if sim_run_ids:
            query["sim_run_id"] = {"$in": list(sim_run_ids)}
        sim_rows = list(db[SIM_COLLECTION].find(query, {"_id": 0}))
        for row in sim_rows:
            row["simulated"] = True       # always, even if the flag was lost
        rows += sim_rows

    return rows


def default_out_dir(source: str) -> str:
    """One folder per data source, so a real build can't overwrite a simulated one."""
    return os.path.join("data", "sequences", source)


def write_outputs(out_dir: str, datasets: Dict[str, Any], source: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    def dump_lines(name: str, records: List[Dict[str, Any]]) -> None:
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    dump_lines("train.jsonl", datasets["train"])
    dump_lines("val.jsonl", datasets["val"])
    dump_lines("test.jsonl", datasets["test"])

    with open(os.path.join(out_dir, "vocab.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(datasets["vocab"], handle, ensure_ascii=False, indent=2)

    report = dict(datasets["report"])
    report["data_source"] = source
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_report_md(datasets["report"], source))


def main(argv: Optional[Sequence[str]] = None, db=None) -> int:
    parser = argparse.ArgumentParser(description="Build sequence data for the sequential model.")
    parser.add_argument("--source", choices=["real", "simulated", "both"], default="real")
    parser.add_argument("--sim-run-id", action="append", default=None,
                        help="only use these simulation runs (repeatable)")
    parser.add_argument("--max-len", type=int, default=DEFAULT_MAX_LEN)
    parser.add_argument("--min-len", type=int, default=DEFAULT_MIN_LEN)
    parser.add_argument("--train-frac", type=float, default=DEFAULT_TRAIN_FRAC)
    parser.add_argument("--val-frac", type=float, default=DEFAULT_VAL_FRAC)
    parser.add_argument("--include-impressions", action="store_true")
    parser.add_argument("--out", default=None,
                        help="output folder (default: data/sequences/<source>)")
    args = parser.parse_args(argv)

    if db is None:
        from database import db as real_db  # imported only when run as a script
        db = real_db

    actions = list(DEFAULT_ACTIONS) + ([IMPRESSION] if args.include_impressions else [])
    rows = load_rows(db, args.source, actions, args.sim_run_id)
    datasets = build_datasets(
        rows, actions=actions, max_len=args.max_len, min_len=args.min_len,
        train_frac=args.train_frac, val_frac=args.val_frac,
    )
    out_dir = args.out or default_out_dir(args.source)
    write_outputs(out_dir, datasets, args.source)

    report = datasets["report"]
    print(f"rows read: {report['rows']['read']}, used: {report['rows']['used']}")
    print(f"students: {report['students']}")
    print(f"sequences: {report['sequences']}")
    if report["contains_simulated_data"]:
        print("\n*** WARNING: these files contain SIMULATED data. See report.md. ***")
    if report["students"]["real_with_at_least_min_len_interactions"] == 0 and args.source != "simulated":
        print("\nNote: no real student has enough interactions yet to form a sequence.")
    print(f"\nfiles written to: {os.path.abspath(out_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())