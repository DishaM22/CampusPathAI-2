"""
train_ranker.py
===============
PHASE G: train the learned ranker (see trained_ranker.py) from REAL student
behaviour, then judge it on held-out data. READ-ONLY on the database.

    python train_ranker.py
    python train_ranker.py --out models/ranker --seed 42

Where the labels come from
--------------------------
Positive  (1)  the student registered for the event (cancelled registrations
               do not count) or was marked as attending it.
Negative  (0)  the event was shown in the student's RECOMMENDED list and the
               student did nothing with it (impression, source
               "dashboard_recommended"), or the student pressed "not
               interested". A hidden event the student later un-hid is NOT a
               negative. An event that is also a positive is never a negative.
Simulated interactions are never used.

Honest limits
-------------
* Negatives are events the engine itself chose to show, so they are
  "better than average" non-choices. That is a biased sample, and it is
  recorded here rather than hidden.
* Features are computed from the student's CURRENT profile and the event as
  stored now, not as they were at the time. Profiles that changed since then
  add noise.
* With little data the script refuses to train and says why. That is the
  expected outcome until enough real behaviour exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import trained_ranker as tr

DEFAULT_IMPRESSION_SOURCES = ("dashboard_recommended",)


def as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return as_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------
# Step 1 (pure): who did what, with which label
# ------------------------------------------------------------------
def build_label_pairs(
    registrations: Iterable[Dict[str, Any]],
    attendance: Iterable[Dict[str, Any]],
    interactions: Iterable[Dict[str, Any]],
    undone_pairs: Optional[Set[Tuple[str, str]]] = None,
    impression_sources: Sequence[str] = DEFAULT_IMPRESSION_SOURCES,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Returns ([{"student_id","event_id","label","ts","kind"}], statistics)."""
    stats: Counter = Counter()
    undone_pairs = undone_pairs or set()

    positives: Dict[Tuple[str, str], datetime] = {}

    def add_positive(student: Any, event: Any, when: Any, kind: str) -> None:
        if not student or not event:
            return
        timestamp = as_utc(when)
        if timestamp is None:
            stats["positive_without_time"] += 1
            return
        key = (str(student), str(event))
        if key not in positives or timestamp < positives[key]:
            positives[key] = timestamp
        stats[f"positive_from_{kind}"] += 1

    for row in registrations:
        if str(row.get("status", "")).lower() == "cancelled":
            stats["cancelled_registration_ignored"] += 1
            continue
        add_positive(row.get("student_id"), row.get("event_id"), row.get("registered_at"), "registration")
    for row in attendance:
        add_positive(row.get("student_id"), row.get("event_id"), row.get("marked_at"), "attendance")

    negatives: Dict[Tuple[str, str], Tuple[datetime, str]] = {}
    for row in interactions:
        if row.get("simulated"):
            stats["simulated_ignored"] += 1
            continue
        action = str(row.get("action", ""))
        if action == "not_interested":
            kind = "not_interested"
        elif action == "impression" and row.get("source") in impression_sources:
            kind = "impression"
        else:
            continue
        student, event, timestamp = row.get("student_id"), row.get("event_id"), as_utc(row.get("timestamp"))
        if not student or not event or timestamp is None:
            continue
        key = (str(student), str(event))
        previous = negatives.get(key)
        if previous is None:
            negatives[key] = (timestamp, kind)
        else:
            earliest = min(previous[0], timestamp)
            negatives[key] = (earliest, "not_interested" if "not_interested" in (previous[1], kind) else "impression")

    pairs = [{"student_id": s, "event_id": e, "label": 1, "ts": ts, "kind": "positive"} for (s, e), ts in positives.items()]
    for (student, event), (timestamp, kind) in negatives.items():
        key = (student, event)
        if key in positives:
            stats["negative_dropped_because_also_positive"] += 1
            continue
        if kind == "not_interested" and key in undone_pairs:
            stats["negative_dropped_because_undone"] += 1
            continue
        pairs.append({"student_id": student, "event_id": event, "label": 0, "ts": timestamp, "kind": kind})
    pairs.sort(key=lambda p: (p["ts"], p["student_id"], p["event_id"]))
    stats["positives"] = sum(1 for p in pairs if p["label"] == 1)
    stats["negatives"] = sum(1 for p in pairs if p["label"] == 0)
    return pairs, dict(stats)


# ------------------------------------------------------------------
# Step 2: attach the ranker's inputs, computed by the engine itself
# ------------------------------------------------------------------
def student_from_profile(student_id: str, profile: Dict[str, Any], department: str = "") -> Dict[str, Any]:
    """The same shape the dashboard hands to the engine."""
    return {
        "student_id": student_id,
        "department": department or profile.get("department", ""),
        "year": profile.get("year", ""),
        "interests": profile.get("interests", []),
        "skills": profile.get("skills", []),
        "career_goal": profile.get("career_goal", ""),
        "preferred_domains": profile.get("preferred_domains", []),
    }


def features_from_result(result: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, float], float]]:
    """(text_signal, {"content","text_match"}, hybrid) from one engine result, or None if no text signal."""
    used = result.get("signals_used", [])
    signal = "semantic" if "semantic" in used else "lexical" if "lexical" in used else None
    if signal is None:
        return None
    text = result.get("semantic_score") if signal == "semantic" else result.get("lexical_score")
    content = result.get("content_score")
    hybrid = result.get("hybrid_score")
    if text is None or content is None or hybrid is None:
        return None
    return signal, {"content": float(content), "text_match": float(text)}, float(hybrid)


def attach_features(
    label_pairs: Sequence[Dict[str, Any]],
    students: Dict[str, Dict[str, Any]],
    events: Dict[str, Dict[str, Any]],
    engine: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    students: student id -> student dict (see student_from_profile); events: event id -> event document.
    The hybrid baseline is computed with NO feedback, so it cannot know the label.
    """
    stats: Counter = Counter()
    out: List[Dict[str, Any]] = []
    for pair in label_pairs:
        student, event = students.get(pair["student_id"]), events.get(pair["event_id"])
        if student is None:
            stats["skipped_no_profile"] += 1
            continue
        if event is None:
            stats["skipped_event_missing"] += 1
            continue
        try:
            result = engine.score_event(student=student, event=event, feedback={}, timetable=None)
        except Exception:
            stats["skipped_engine_error"] += 1
            continue
        extracted = features_from_result(result)
        if extracted is None:
            stats["skipped_no_text_signal"] += 1
            continue
        signal, features, hybrid = extracted
        out.append({**pair, "features": features, "hybrid": hybrid, "text_signal": signal})
    return out, dict(stats)


def majority_signal(pairs: Sequence[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]], int]:
    """The ranker uses ONE kind of text signal. Keep the most common kind, drop the rest (and count them)."""
    if not pairs:
        return None, [], 0
    counts = Counter(p["text_signal"] for p in pairs)
    signal = counts.most_common(1)[0][0]
    kept = [p for p in pairs if p["text_signal"] == signal]
    return signal, kept, len(pairs) - len(kept)


# ------------------------------------------------------------------
# Step 3: read the database (read-only), train, report
# ------------------------------------------------------------------
def _read(db, collection: str, fields: Sequence[str]) -> List[Dict[str, Any]]:
    projection = {name: 1 for name in fields}
    return list(db[collection].find({}, projection))


def run(db, engine, out_dir: str, *, seed: int = 42, impression_sources: Sequence[str] = DEFAULT_IMPRESSION_SOURCES,
        verbose: bool = True) -> int:
    registrations = _read(db, "event_registrations", ["student_id", "event_id", "status", "registered_at"])
    attendance = _read(db, "attendance", ["student_id", "event_id", "marked_at"])
    interactions = _read(db, "interactions", ["student_id", "event_id", "action", "source", "timestamp", "simulated"])
    hidden_rows = _read(db, "not_interested_events", ["student_id", "event_id", "active"])
    undone = {(str(r["student_id"]), str(r["event_id"])) for r in hidden_rows if r.get("active") is False}

    label_pairs, label_stats = build_label_pairs(registrations, attendance, interactions, undone, impression_sources)

    profiles = {str(p.get("student_id")): p for p in _read(
        db, "profiles", ["student_id", "interests", "skills", "career_goal", "preferred_domains", "year", "department"])}
    students = {sid: student_from_profile(sid, profile) for sid, profile in profiles.items()}
    event_docs: Dict[str, Dict[str, Any]] = {}
    for doc in db.events.find({}):
        event_id = str(doc["_id"])
        doc = dict(doc)
        doc["_id"] = event_id
        event_docs[event_id] = doc

    pairs, feature_stats = attach_features(label_pairs, students, event_docs, engine)
    signal, pairs, dropped_other_signal = majority_signal(pairs)
    if dropped_other_signal:
        feature_stats["dropped_other_text_signal"] = dropped_other_signal

    if verbose:
        print("labels:", label_stats)
        print("features:", feature_stats)
        print(f"usable labelled pairs: {len(pairs)}   text signal: {signal}")

    if not pairs or signal is None:
        print("\nNot enough usable data to train a ranker. Nothing was written.")
        return 1

    outcome = tr.train_and_evaluate(pairs, signal, seed=seed, data_origin="real")
    if outcome["status"] == "refused":
        print("\nNot training: " + "; ".join(outcome["report"]["reasons"]))
        print("Nothing was written. This is expected until enough real behaviour has been collected.")
        return 1

    report = dict(outcome["report"])
    report["label_statistics"] = label_stats
    report["feature_statistics"] = feature_stats
    report["known_limits"] = [
        "negatives are events the engine itself chose to show (a biased sample)",
        "features use today's profile and event data, not the values at the time",
    ]
    tr.save_ranker(out_dir, outcome["ranker"], report)

    gate = report["quality_gate"]
    print("\nQUALITY GATE:", "PASSED" if gate["passed"] else "FAILED (the engine will not use this ranker)")
    for reason in gate["reasons"]:
        print(" -", reason)
    print("files written to:", os.path.abspath(out_dir))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Train the learned ranker from real student behaviour (read-only DB).")
    parser.add_argument("--out", default=tr.DEFAULT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--include-upcoming-impressions", action="store_true",
                        help="also treat unclicked events from the Upcoming list as negatives (weaker evidence)")
    args = parser.parse_args(argv)

    from database import db           # imported only when run as a script
    import recommendation_engine as engine

    sources = DEFAULT_IMPRESSION_SOURCES + (("dashboard_upcoming",) if args.include_upcoming_impressions else ())
    return run(db, engine, args.out, seed=args.seed, impression_sources=sources)


if __name__ == "__main__":
    sys.exit(main())