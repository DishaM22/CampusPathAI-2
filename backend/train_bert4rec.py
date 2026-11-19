"""
train_bert4rec.py
=================
PHASE C: train the content-aware BERT4Rec model on the files written by
build_sequences.py, and write an honest report.

Run (from the backend folder)
-----------------------------
    python train_bert4rec.py
    python train_bert4rec.py --data data/sequences/simulated --out models/bert4rec --seed 42

Reads   <data>/train.jsonl  val.jsonl  test.jsonl  vocab.json  report.json
Writes  <out>/model.pt  vocab.json  train_report.json  train_report.md

What it does, in order
----------------------
1. Refuses to train if there are too few training sequences.
2. Trains with the Cloze task (random category/club masking), dropout and
   weight decay, and STOPS EARLY when the validation loss stops improving.
   The test set is never used for any decision; it is evaluated once at the end.
3. Compares the model with two trivial baselines on the same questions:
   "most popular category/club in training" and "same category/club as the
   student's previous interaction", plus random chance.
4. Reports 95% error margins, because the evaluation sets are small.
5. Records in the checkpoint whether the training data was SIMULATED.

Remember: on simulated data the numbers measure how well the model recovers
the simulator's rules, not how well it predicts real students.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import torch
import torch.nn.functional as F

from bert4rec_data import (
    PAD_ID,
    build_eval_examples,
    build_train_examples,
    load_jsonl,
    load_vocab,
    sequences_to_questions,
)
from bert4rec_model import (
    Bert4Rec,
    Bert4RecConfig,
    apply_cloze_mask,
    cloze_loss,
    mask_special_logits,
    save_checkpoint,
)
from build_sequences import FIRST_FREE_ID

DEFAULTS = dict(
    epochs=300, patience=30, batch_size=32, lr=1e-3, weight_decay=0.01,
    dropout=0.3, hidden=64, layers=2, heads=2, mask_prob=0.2, mask_last_prob=0.25,
    seed=42, min_train=30,
)


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------
def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)


def examples_to_tensors(examples: Sequence[Dict[str, Any]]):
    cat = torch.tensor([e["cat"] for e in examples], dtype=torch.long)
    org = torch.tensor([e["org"] for e in examples], dtype=torch.long)
    act = torch.tensor([e["act"] for e in examples], dtype=torch.long)
    return cat, org, act


def examples_to_groups(examples: Sequence[Dict[str, Any]]):
    """Event-group ids (same event -> same number), or None if the examples do not carry them."""
    if not examples or any("evt" not in e for e in examples):
        return None
    return torch.tensor([e["evt"] for e in examples], dtype=torch.long)


def margin95(p: Optional[float], n: int) -> Optional[float]:
    """Normal-approximation 95% error margin for an accuracy p measured on n cases."""
    if p is None or n <= 0:
        return None
    return round(1.96 * math.sqrt(max(p * (1 - p), 0.0) / n), 4)


# ------------------------------------------------------------------
# Scoring (used for the model AND for the baselines, so they are compared fairly)
# ------------------------------------------------------------------
def score_logits(
    cat_logits: torch.Tensor,
    org_logits: torch.Tensor,
    label_cat: torch.Tensor,
    label_org: torch.Tensor,
    known_cat: torch.Tensor,
    known_org: torch.Tensor,
    ks=(1, 3),
) -> Dict[str, Any]:
    """Loss and top-k accuracy for categories and clubs. Unknown (UNK) labels are skipped and counted."""
    out: Dict[str, Any] = {"n": int(label_cat.shape[0])}
    losses = []

    for name, logits, labels, known in (
        ("cat", cat_logits, label_cat, known_cat),
        ("org", org_logits, label_org, known_org),
    ):
        n_known = int(known.sum())
        out[f"n_{name}"] = n_known
        if n_known == 0:
            out[f"{name}_loss"] = None
            for k in ks:
                out[f"{name}_acc@{k}"] = None
                out[f"{name}_acc@{k}_margin95"] = None
            continue

        chosen_logits = logits[known]
        chosen_labels = labels[known]
        loss = float(F.cross_entropy(chosen_logits, chosen_labels))
        out[f"{name}_loss"] = round(loss, 4)
        losses.append(loss)

        top = chosen_logits.topk(min(max(ks), chosen_logits.shape[1]), dim=1).indices
        for k in ks:
            acc = float((top[:, :k] == chosen_labels.unsqueeze(1)).any(dim=1).float().mean())
            out[f"{name}_acc@{k}"] = round(acc, 4)
            out[f"{name}_acc@{k}_margin95"] = margin95(acc, n_known)

    out["loss"] = round(sum(losses), 4) if losses else float("nan")
    return out


def _label_tensors(examples):
    label_cat = torch.tensor([e["label_cat"] for e in examples], dtype=torch.long)
    label_org = torch.tensor([e["label_org"] for e in examples], dtype=torch.long)
    known_cat = torch.tensor([e["cat_known"] for e in examples], dtype=torch.bool)
    known_org = torch.tensor([e["org_known"] for e in examples], dtype=torch.bool)
    return label_cat, label_org, known_cat, known_org


@torch.no_grad()
def evaluate(model: Bert4Rec, examples: Sequence[Dict[str, Any]], ks=(1, 3)) -> Dict[str, Any]:
    if not examples:
        return {"n": 0}
    model.eval()
    cat, org, act = examples_to_tensors(examples)
    cat_logits, org_logits = model(cat, org, act)
    label_cat, label_org, known_cat, known_org = _label_tensors(examples)
    return score_logits(
        mask_special_logits(cat_logits[:, -1, :]), mask_special_logits(org_logits[:, -1, :]),
        label_cat, label_org, known_cat, known_org, ks,
    )


# ------------------------------------------------------------------
# Baselines
# ------------------------------------------------------------------
def frequency_row(counter: Counter, size: int) -> torch.Tensor:
    row = torch.zeros(size)
    row[:FIRST_FREE_ID] = -1e9
    for index, count in counter.items():
        if index >= FIRST_FREE_ID and index < size:
            row[index] = math.log(count + 1)
    return row


def train_counts(train_examples: Sequence[Dict[str, Any]]):
    cats: Counter = Counter()
    orgs: Counter = Counter()
    for e in train_examples:
        for c, o, a in zip(e["cat"], e["org"], e["act"]):
            if a != PAD_ID:
                cats[c] += 1
                orgs[o] += 1
    return cats, orgs


def chance_scores(known_cat, known_org, n_categories: int, n_organizers: int, ks=(1, 3)) -> Dict[str, Any]:
    """Exact random-guess level: k / (number of real categories or clubs). No sampling, no ties."""
    out: Dict[str, Any] = {"n": int(known_cat.shape[0])}
    losses = []
    for name, known, size in (("cat", known_cat, n_categories), ("org", known_org, n_organizers)):
        real = size - FIRST_FREE_ID
        out[f"n_{name}"] = int(known.sum())
        if real <= 0 or out[f"n_{name}"] == 0:
            out[f"{name}_loss"] = None
            for k in ks:
                out[f"{name}_acc@{k}"] = None
                out[f"{name}_acc@{k}_margin95"] = None
            continue
        out[f"{name}_loss"] = round(math.log(real), 4)
        losses.append(math.log(real))
        for k in ks:
            out[f"{name}_acc@{k}"] = round(min(k, real) / real, 4)
            out[f"{name}_acc@{k}_margin95"] = None
    out["loss"] = round(sum(losses), 4) if losses else float("nan")
    return out


def baseline_scores(
    examples: Sequence[Dict[str, Any]],
    train_examples: Sequence[Dict[str, Any]],
    n_categories: int,
    n_organizers: int,
    ks=(1, 3),
) -> Dict[str, Dict[str, Any]]:
    """popularity = overall training frequency; repeat_last = the student's previous category/club first."""
    if not examples:
        return {"popularity": {"n": 0}, "repeat_last": {"n": 0}, "chance": {"n": 0}}

    cats, orgs = train_counts(train_examples)
    cat_row = frequency_row(cats, n_categories)
    org_row = frequency_row(orgs, n_organizers)
    label_cat, label_org, known_cat, known_org = _label_tensors(examples)
    n = len(examples)

    pop_cat = cat_row.unsqueeze(0).repeat(n, 1)
    pop_org = org_row.unsqueeze(0).repeat(n, 1)

    last_cat = pop_cat.clone()
    last_org = pop_org.clone()
    for i, e in enumerate(examples):
        previous_cat, previous_org = e["cat"][-2], e["org"][-2]     # the slot before the question
        if previous_cat >= FIRST_FREE_ID:
            last_cat[i, previous_cat] += 1000.0
        if previous_org >= FIRST_FREE_ID:
            last_org[i, previous_org] += 1000.0

    return {
        "popularity": score_logits(pop_cat, pop_org, label_cat, label_org, known_cat, known_org, ks),
        "repeat_last": score_logits(last_cat, last_org, label_cat, label_org, known_cat, known_org, ks),
        "chance": chance_scores(known_cat, known_org, n_categories, n_organizers, ks),
    }


def prob_row(counter: Counter, size: int) -> torch.Tensor:
    """Smoothed share of each real id (specials get 0)."""
    row = torch.zeros(size)
    for index, count in counter.items():
        if FIRST_FREE_ID <= index < size:
            row[index] = count
    row[FIRST_FREE_ID:] += 1.0
    return row / row.sum()


def transition_tables(train_questions: Sequence[Dict[str, Any]]):
    """Counts of: previous event's category (club)  ->  the category (club) of the event the student then engaged with."""
    cat_table: Dict[int, Counter] = defaultdict(Counter)
    org_table: Dict[int, Counter] = defaultdict(Counter)
    for e in train_questions:
        previous_cat, previous_org = e["cat"][-2], e["org"][-2]
        if e["cat_known"] and previous_cat >= FIRST_FREE_ID:
            cat_table[previous_cat][e["label_cat"]] += 1
        if e["org_known"] and previous_org >= FIRST_FREE_ID:
            org_table[previous_org][e["label_org"]] += 1
    return cat_table, org_table


def markov_scores(
    examples: Sequence[Dict[str, Any]],
    train_questions: Sequence[Dict[str, Any]],
    train_examples: Sequence[Dict[str, Any]],
    n_categories: int,
    n_organizers: int,
    ks=(1, 3),
    strength: float = 5.0,
) -> Dict[str, Any]:
    """
    A simple sequential baseline with no neural network: guess from what students
    went on to engage with after an event of the previous event's category (club),
    blended with overall popularity. If the neural model cannot beat THIS, it has
    not learned anything a lookup table could not.
    """
    if not examples:
        return {"n": 0}
    cat_counts, org_counts = train_counts(train_examples)
    pop_cat = prob_row(cat_counts, n_categories)
    pop_org = prob_row(org_counts, n_organizers)
    cat_table, org_table = transition_tables(train_questions)

    def row(table, previous, pop, size):
        counts = torch.zeros(size)
        for index, count in table.get(previous, {}).items():
            if index < size:
                counts[index] = count
        probs = (counts + strength * pop) / (counts.sum() + strength)
        logits = torch.log(probs.clamp_min(1e-12))
        logits[:FIRST_FREE_ID] = -1e9
        return logits

    cat_logits = torch.stack([row(cat_table, e["cat"][-2], pop_cat, n_categories) for e in examples])
    org_logits = torch.stack([row(org_table, e["org"][-2], pop_org, n_organizers) for e in examples])
    label_cat, label_org, known_cat, known_org = _label_tensors(examples)
    return score_logits(cat_logits, org_logits, label_cat, label_org, known_cat, known_org, ks)


# ------------------------------------------------------------------
# Training
# ------------------------------------------------------------------
def train_model(
    train_examples: Sequence[Dict[str, Any]],
    val_examples: Sequence[Dict[str, Any]],
    cfg: Bert4RecConfig,
    *,
    epochs: int = DEFAULTS["epochs"],
    patience: int = DEFAULTS["patience"],
    batch_size: int = DEFAULTS["batch_size"],
    lr: float = DEFAULTS["lr"],
    weight_decay: float = DEFAULTS["weight_decay"],
    mask_prob: float = DEFAULTS["mask_prob"],
    mask_last_prob: float = DEFAULTS["mask_last_prob"],
    mask_by_event: bool = True,
    seed: int = DEFAULTS["seed"],
    log_every: int = 0,
):
    """Returns (best_model, history, best_info). Early stopping uses the VALIDATION loss only."""
    if not train_examples:
        raise ValueError("no training examples")
    if not val_examples:
        raise ValueError("no validation examples (needed for early stopping)")

    set_seed(seed)
    generator = torch.Generator().manual_seed(seed)

    model = Bert4Rec(cfg)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    cat, org, act = examples_to_tensors(train_examples)
    groups = examples_to_groups(train_examples) if mask_by_event else None
    n = cat.shape[0]

    history: List[Dict[str, Any]] = []
    best = {"val_loss": float("inf"), "epoch": 0, "state": None}
    waited = 0

    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(n, generator=generator)
        running, batches = 0.0, 0

        for start in range(0, n, batch_size):
            idx = order[start:start + batch_size]
            in_cat, in_org, lab_cat, lab_org = apply_cloze_mask(
                cat[idx], org[idx], act[idx], mask_prob, mask_last_prob, generator,
                groups[idx] if groups is not None else None,
            )
            cat_logits, org_logits = model(in_cat, in_org, act[idx])
            loss = cloze_loss(cat_logits, org_logits, lab_cat, lab_org)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            running += loss.item()
            batches += 1

        val = evaluate(model, val_examples)
        row = {
            "epoch": epoch,
            "train_loss": round(running / max(1, batches), 4),
            "val_loss": val["loss"],
            "val_cat_acc@1": val.get("cat_acc@1"),
            "val_org_acc@1": val.get("org_acc@1"),
        }
        history.append(row)
        if log_every and (epoch == 1 or epoch % log_every == 0):
            print(f"epoch {epoch:4d}  train {row['train_loss']:.4f}  val {row['val_loss']:.4f}  "
                  f"cat@1 {row['val_cat_acc@1']}  org@1 {row['val_org_acc@1']}")

        if not math.isnan(val["loss"]) and val["loss"] < best["val_loss"] - 1e-4:
            best = {"val_loss": val["loss"], "epoch": epoch, "state": copy.deepcopy(model.state_dict())}
            waited = 0
        else:
            waited += 1
            if waited >= patience:
                break

    if best["state"] is None:                    # never improved (e.g. NaN): keep the last weights
        best["state"] = copy.deepcopy(model.state_dict())
        best["epoch"] = len(history)
    model.load_state_dict(best["state"])
    model.eval()
    return model, history, {"best_epoch": best["epoch"], "best_val_loss": best["val_loss"],
                            "epochs_run": len(history), "stopped_early": len(history) < epochs}


# ------------------------------------------------------------------
# Quality gate: is this model allowed to influence recommendations?
# ------------------------------------------------------------------
GATE_MIN_QUESTIONS = 100
GATE_RULE = (
    "Evaluated once on the TEST recommendation question. Passes only if the model beats BOTH popularity and "
    "markov with non-overlapping 95% error margins on category or club accuracy@1, is not clearly worse than "
    "either baseline on any of those two measures, and at least "
    f"{GATE_MIN_QUESTIONS} test questions exist."
)


def _clearly_better(a: Dict[str, Any], b: Dict[str, Any], metric: str) -> bool:
    x, y = a.get(metric), b.get(metric)
    ma, mb = a.get(metric + "_margin95"), b.get(metric + "_margin95")
    if None in (x, y, ma, mb):
        return False
    return (x - y) > (ma + mb)


def evaluate_gate(block: Dict[str, Any], min_questions: int = GATE_MIN_QUESTIONS) -> Dict[str, Any]:
    """block = results["test"]["recommendation"]  ->  {"passed": bool, "reasons": [...], ...}"""
    model = block.get("model", {})
    n = int(model.get("n", 0) or 0)
    base = {"rule": GATE_RULE, "evaluated_on": "test recommendation question", "n_questions": n}

    if n < min_questions:
        return {**base, "passed": False, "reasons": [f"only {n} test questions (need at least {min_questions})"]}

    baselines = {name: block[name] for name in ("popularity", "markov") if name in block and block[name].get("n", 0)}
    if len(baselines) < 2:
        return {**base, "passed": False, "reasons": ["a required baseline (popularity or markov) is missing"]}

    reasons: List[str] = []
    wins, losses = [], []
    for metric, label in (("cat_acc@1", "category accuracy@1"), ("org_acc@1", "club accuracy@1")):
        if all(_clearly_better(model, b, metric) for b in baselines.values()):
            wins.append(label)
        for name, b in baselines.items():
            if _clearly_better(b, model, metric):
                losses.append(f"{label}: clearly worse than {name}")

    if wins:
        reasons.append("clearly beats popularity and markov on: " + ", ".join(wins))
    else:
        reasons.append("does not clearly beat BOTH popularity and markov on category or club accuracy@1")
    reasons += losses
    return {**base, "passed": bool(wins) and not losses, "reasons": reasons}


# ------------------------------------------------------------------
# Report
# ------------------------------------------------------------------
def _fmt(metrics: Dict[str, Any], key: str) -> str:
    value = metrics.get(key)
    if value is None:
        return "n/a"
    margin = metrics.get(key + "_margin95")
    return f"{value:.3f}" + (f" ± {margin:.3f}" if margin is not None else "")


def render_report_md(report: Dict[str, Any]) -> str:
    lines = ["# BERT4Rec training report", ""]
    if report["trained_on_simulated"] is not False:
        lines += [
            "> **TRAINED ON SIMULATED DATA**" if report["trained_on_simulated"] else "> **DATA ORIGIN UNKNOWN**",
            "> The training data was generated by simulate_interactions.py (or its origin could not be "
            "confirmed). The model has only learned the rules built into the simulator. These numbers are NOT "
            "accuracy on real students and must not be reported as such.",
            "",
        ]
    t = report["training"]
    lines += [
        f"- trained at: {report['trained_at']}   seed: {report['seed']}   torch: {report['torch_version']}",
        f"- sequences: train {report['data']['n_train_sequences']}, "
        f"validation questions {report['data']['n_val_questions']}, test questions {report['data']['n_test_questions']}",
        f"- parameters: {report['n_parameters']:,}   config: {report['config']}",
        f"- epochs run: {t['epochs_run']} (best epoch {t['best_epoch']}, "
        f"stopped early: {t['stopped_early']}); best validation loss {t['best_val_loss']}",
        "",
        "Accuracy is shown as value ± 95% error margin. The evaluation sets are small, so differences "
        "smaller than the margins are NOT meaningful.",
        "",
    ]
    for split in ("validation", "test"):
        for subset, title in (
            ("all", "next-interaction question, all held-out interactions"),
            ("positive", "next-interaction question, only save / register / attend"),
            ("recommendation", "RECOMMENDATION question: save / register / attend asked BEFORE the student has seen that event"),
        ):
            block = report["results"][split][subset]
            n = block["model"].get("n", 0)
            lines += [f"## {split.capitalize()} - {title} (n = {n})", "",
                      "| method | category acc@1 | category acc@3 | club acc@1 | club acc@3 |", "|---|---|---|---|---|"]
            for name in ("model", "markov", "repeat_last", "popularity", "chance"):
                m = block.get(name)
                if m is None:
                    continue
                lines.append(f"| {name} | {_fmt(m, 'cat_acc@1')} | {_fmt(m, 'cat_acc@3')} | "
                             f"{_fmt(m, 'org_acc@1')} | {_fmt(m, 'org_acc@3')} |")
            lines.append("")
    gate = report.get("quality_gate")
    if gate:
        lines += [
            "## Quality gate",
            f"**{'PASSED' if gate['passed'] else 'FAILED'}** - "
            + ("the recommender may use this model." if gate["passed"]
               else "the recommender will NOT use this model; the sequential signal stays off."),
            f"- rule: {gate['rule']}",
            f"- test questions: {gate['n_questions']}",
            *[f"- {reason}" for reason in gate["reasons"]],
            "",
        ]
    lines += [
        "How to read this: `repeat_last` = guess the same category / club as the student's previous "
        "interaction; `popularity` = guess the most frequent in training; `chance` = random guess; "
        "`markov` (recommendation question only) = a lookup table of what students engaged with after an "
        "event of the previous event's category / club, blended with popularity.",
        "A model that does not clearly beat `popularity` and `markov` on the RECOMMENDATION question has not "
        "learned anything useful for recommending.",
        "",
        "The next-interaction questions are easy to game: a student's view, save, register and attend of one event "
        "all share its category and club, so copying the previous interaction already looks almost perfect. "
        "The RECOMMENDATION question removes the student's earlier steps on the same event from the history, "
        "which is what the live recommender will face. Judge the model on that one.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def _results_for(model, examples, recommendation_examples, train_examples, cfg, train_questions=None) -> Dict[str, Any]:
    out = {}
    subsets = (
        ("all", list(examples)),
        ("positive", [e for e in examples if e["positive"]]),
        ("recommendation", [e for e in recommendation_examples if e["positive"]]),
    )
    for subset, chosen in subsets:
        block = {"model": evaluate(model, chosen)}
        block.update(baseline_scores(chosen, train_examples, cfg.n_categories, cfg.n_organizers))
        if subset == "recommendation" and train_questions:
            block["markov"] = markov_scores(chosen, train_questions, train_examples, cfg.n_categories, cfg.n_organizers)
        out[subset] = block
    return out


def run(
    data_dir: str,
    out_dir: str,
    *,
    cfg_overrides: Optional[Dict[str, Any]] = None,
    min_train: int = DEFAULTS["min_train"],
    verbose: bool = True,
    **train_kwargs,
) -> int:
    vocab = load_vocab(os.path.join(data_dir, "vocab.json"))
    max_len = int(vocab["max_len"])
    train_records = load_jsonl(os.path.join(data_dir, "train.jsonl"))
    val_records = load_jsonl(os.path.join(data_dir, "val.jsonl"))
    test_records = load_jsonl(os.path.join(data_dir, "test.jsonl"))

    data_report_path = os.path.join(data_dir, "report.json")
    data_report = json.load(open(data_report_path, encoding="utf-8")) if os.path.exists(data_report_path) else {}
    simulated: Optional[bool] = data_report.get("contains_simulated_data")
    if simulated is None:
        simulated = any(r.get("simulated") for r in train_records) or None     # None = unknown

    if len(train_records) < min_train:
        print(f"Not enough training sequences: {len(train_records)} (need at least {min_train}).\n"
              "Collect more interaction data, or build simulated data with simulate_interactions.py.")
        return 1

    train_examples = build_train_examples(train_records, vocab, max_len)
    val_examples = build_eval_examples(val_records, vocab, max_len)
    test_examples = build_eval_examples(test_records, vocab, max_len)
    train_questions = sequences_to_questions(train_records, vocab, max_len)
    val_rec = build_eval_examples(val_records, vocab, max_len, exclude_same_event=True)
    test_rec = build_eval_examples(test_records, vocab, max_len, exclude_same_event=True)
    if not val_examples:
        print("No validation questions: cannot do early stopping. Rebuild the sequences with more data.")
        return 1

    cfg_values = dict(
        n_actions=len(vocab["actions"]), n_categories=len(vocab["categories"]),
        n_organizers=len(vocab["organizers"]), max_len=max_len,
        hidden=DEFAULTS["hidden"], layers=DEFAULTS["layers"], heads=DEFAULTS["heads"],
        dropout=DEFAULTS["dropout"],
    )
    cfg_values.update(cfg_overrides or {})
    if "ff" not in (cfg_overrides or {}):
        cfg_values["ff"] = cfg_values["hidden"] * 4
    cfg = Bert4RecConfig(**cfg_values)

    if verbose:
        if simulated is not False:
            print("*** The training data is SIMULATED (or its origin is unknown). The model will only learn the simulator's rules. ***")
        print(f"train sequences {len(train_examples)}, validation questions {len(val_examples)}, "
              f"test questions {len(test_examples)}")

    model, history, info = train_model(train_examples, val_examples, cfg, log_every=10 if verbose else 0, **train_kwargs)

    results = {
        "validation": _results_for(model, val_examples, val_rec, train_examples, cfg, train_questions),
        "test": _results_for(model, test_examples, test_rec, train_examples, cfg, train_questions),   # evaluated once, after all choices
    }
    gate = evaluate_gate(results["test"]["recommendation"])
    seed = train_kwargs.get("seed", DEFAULTS["seed"])
    n_params = sum(p.numel() for p in model.parameters())
    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "torch_version": str(torch.__version__),
        "trained_on_simulated": simulated,
        "data_dir": os.path.abspath(data_dir),
        "data": {"n_train_sequences": len(train_examples), "n_val_questions": len(val_examples),
                 "n_test_questions": len(test_examples)},
        "config": cfg.to_dict(),
        "n_parameters": n_params,
        "hyperparameters": {k: v for k, v in train_kwargs.items()},
        "training": info,
        "quality_gate": gate,
        "results": results,
        "history": history,
    }

    os.makedirs(out_dir, exist_ok=True)
    meta = {
        "trained_at": report["trained_at"], "seed": seed, "torch_version": str(torch.__version__),
        "trained_on_simulated": simulated, "n_train_sequences": len(train_examples),
        "mask_by_event": bool(train_kwargs.get("mask_by_event", True)),
        "quality_gate_passed": bool(gate["passed"]),
        "quality_gate_reasons": list(gate["reasons"]),
        "best_epoch": info["best_epoch"], "best_val_loss": info["best_val_loss"],
        "test_cat_acc@1": results["test"]["all"]["model"].get("cat_acc@1"),
        "test_org_acc@1": results["test"]["all"]["model"].get("org_acc@1"),
    }
    save_checkpoint(os.path.join(out_dir, "model.pt"), model, meta)
    shutil.copyfile(os.path.join(data_dir, "vocab.json"), os.path.join(out_dir, "vocab.json"))
    with open(os.path.join(out_dir, "train_report.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, default=lambda o: None if (isinstance(o, float) and math.isnan(o)) else str(o))
    with open(os.path.join(out_dir, "train_report.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_report_md(report))

    if verbose:
        print()
        print(render_report_md(report))
        print("QUALITY GATE:", "PASSED" if gate["passed"] else "FAILED (the recommender will not use this model)")
        print(f"files written to: {os.path.abspath(out_dir)}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Train the content-aware BERT4Rec model.")
    p.add_argument("--data", default=os.path.join("data", "sequences", "simulated"))
    p.add_argument("--out", default=os.path.join("models", "bert4rec"))
    p.add_argument("--epochs", type=int, default=DEFAULTS["epochs"])
    p.add_argument("--patience", type=int, default=DEFAULTS["patience"])
    p.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    p.add_argument("--lr", type=float, default=DEFAULTS["lr"])
    p.add_argument("--weight-decay", type=float, default=DEFAULTS["weight_decay"])
    p.add_argument("--dropout", type=float, default=DEFAULTS["dropout"])
    p.add_argument("--hidden", type=int, default=DEFAULTS["hidden"])
    p.add_argument("--layers", type=int, default=DEFAULTS["layers"])
    p.add_argument("--heads", type=int, default=DEFAULTS["heads"])
    p.add_argument("--mask-prob", type=float, default=DEFAULTS["mask_prob"])
    p.add_argument("--mask-last-prob", type=float, default=DEFAULTS["mask_last_prob"])
    p.add_argument("--no-mask-by-event", action="store_true",
                   help="hide single interactions instead of whole events (for comparison only)")
    p.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    p.add_argument("--min-train", type=int, default=DEFAULTS["min_train"])
    a = p.parse_args(argv)

    return run(
        a.data, a.out,
        cfg_overrides={"hidden": a.hidden, "layers": a.layers, "heads": a.heads, "dropout": a.dropout},
        min_train=a.min_train,
        epochs=a.epochs, patience=a.patience, batch_size=a.batch_size, lr=a.lr,
        weight_decay=a.weight_decay, mask_prob=a.mask_prob, mask_last_prob=a.mask_last_prob,
        mask_by_event=not a.no_mask_by_event, seed=a.seed,
    )


if __name__ == "__main__":
    sys.exit(main())