"""
simulate_interactions.py
========================
PHASE B helper: generate SIMULATED student behaviour so the sequential model
can be built and tested before enough real data exists.

READ THIS FIRST
---------------
Simulated rows are NOT real behaviour. They are produced by a small set of
rules written in this file, so a model trained on them can only learn those
rules. Every simulated row therefore:

  * lives in its own collection, `interactions_simulated`, never in the real
    `interactions` collection (so real analytics, the admin summary and any
    future "real data only" training can never see them),
  * carries "simulated": true and a "sim_run_id",
  * uses made-up student ids ("sim-p003-c07"), never a real student's id.

The assumptions planted in the simulator (all of them are guesses):

  1. A student's interest in an event depends on how well its category,
     title and skills match the student's profile (interests, skills,
     preferred domains, career goal), plus a liking for 1-2 clubs, plus
     random taste.
  2. Higher interest makes saving / registering / attending more likely and
     "not interested" less likely.
  3. MOMENTUM: after saving / registering / attending an event, the student
     becomes a little more interested in the same category for a while.
     This is the only sequential pattern in the data. A sequential model
     that "beats" a non-sequential one on simulated data is only recovering
     this planted pattern, not proving it exists in real students.
  4. Impressions are not simulated (they carry no behavioural choice).
  5. Timestamps are synthetic: event dates are not modelled.

Nothing is ever deleted or overwritten. Every run gets its own run id.

Run (from the backend folder)
-----------------------------
    python simulate_interactions.py --dry-run          # show what would be made
    python simulate_interactions.py --copies 20        # write to interactions_simulated

Needs at least 3 published events and at least 1 student profile with
content (interests, skills, preferred domains or career goal).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

REAL_COLLECTION = "interactions"
SIM_COLLECTION = "interactions_simulated"
SIM_SOURCE = "simulated"
MIN_EVENTS = 3
DAY = 86400.0

# Same rule main.py uses for what students may see (kept here so this file
# never imports main.py).
PUBLIC_EVENT_FILTER = {
    "$or": [
        {"approval_status": "published"},
        {"approval_status": {"$exists": False}},
    ]
}

STOP_WORDS = frozenset({
    "and", "the", "for", "with", "from", "into", "your", "you", "our", "all",
    "new", "event", "events", "club", "day",
})


# ------------------------------------------------------------------
# Normalisation helpers
# ------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _words(text: Any) -> set:
    found = re.findall(r"[a-z0-9+#]+", str(text or "").lower())
    return {w for w in found if len(w) > 2 and not w.isdigit() and w not in STOP_WORDS}


def _as_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        items = value.split(",")
    else:
        items = []
    return [str(x).strip() for x in items if str(x).strip()]


def normalize_event(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event_id = doc.get("_id", doc.get("id"))
    if event_id is None:
        return None
    event = {
        "id": str(event_id),
        "category": str(doc.get("category") or "").strip() or "General",
        "organizer": str(doc.get("organizer") or "").strip() or "Unknown",
        "skills": _as_list(doc.get("skills")),
        "date": str(doc.get("date") or "")[:10],
        "title": str(doc.get("title") or "").strip(),
    }
    event["_words"] = _words(event["category"] + " " + event["title"])
    event["_skill_words"] = _words(" ".join(event["skills"]))
    return event


def normalize_profile(doc: Dict[str, Any]) -> Optional[Dict[str, List[str]]]:
    profile = {
        "interests": _as_list(doc.get("interests")),
        "skills": _as_list(doc.get("skills")),
        "domains": _as_list(doc.get("preferred_domains")),
        "goals": _as_list(doc.get("career_goal") or doc.get("career_goals")),
    }
    if not any(profile.values()):
        return None
    return profile


# ------------------------------------------------------------------
# One simulated student
# ------------------------------------------------------------------
def _make_prefs(profile: Dict[str, List[str]], events: List[Dict[str, Any]], rng: random.Random) -> Dict[str, set]:
    """A slightly different copy of the profile, so copies are not identical."""
    def keep(items: List[str]) -> List[str]:
        return [x for x in items if rng.random() < 0.8]

    topic = keep(profile["interests"]) + keep(profile["domains"])
    if rng.random() < 0.3:                       # a random new curiosity
        topic.append(rng.choice(events)["category"])

    return {
        "topic_words": _words(" ".join(topic)),
        "skill_words": _words(" ".join(keep(profile["skills"]))),
        "goal_words": _words(" ".join(keep(profile["goals"]))),
    }


def _affinity(prefs, event, favorites, noise, momentum) -> float:
    topic = min(1.0, len(prefs["topic_words"] & event["_words"]) / 2.0)
    skill_total = max(1, min(3, len(event["_skill_words"])))
    skill = min(1.0, len(prefs["skill_words"] & event["_skill_words"]) / skill_total)
    goal = min(1.0, len(prefs["goal_words"] & event["_words"]) / 2.0)
    club = 0.15 if event["organizer"] in favorites else 0.0
    score = 0.40 * topic + 0.35 * skill + 0.15 * goal + club + noise + momentum.get(event["category"], 0.0)
    return max(0.0, min(1.0, score))


def _simulate_student(rng: random.Random, prefs, events: List[Dict[str, Any]], momentum_strength: float):
    """Returns [(seconds_from_start, action, event), ...] unsorted."""
    organizers = sorted({e["organizer"] for e in events})
    favorites = set(rng.sample(organizers, k=min(len(organizers), rng.choice([1, 1, 2]))))
    temperature = rng.uniform(0.15, 0.35)            # low = picky, high = explores
    noise = {e["id"]: rng.gauss(0.0, 0.10) for e in events}
    n_events = max(MIN_EVENTS, min(len(events), int(rng.lognormvariate(1.8, 0.5))))

    remaining = list(events)
    momentum: Dict[str, float] = {}
    clock = 0.0
    out = []

    for _ in range(n_events):
        affinities = [_affinity(prefs, e, favorites, noise[e["id"]], momentum) for e in remaining]
        weights = [math.exp(a / temperature) for a in affinities]
        index = rng.choices(range(len(remaining)), weights=weights, k=1)[0]
        event = remaining.pop(index)
        a = affinities[index]

        clock += rng.uniform(1, 14) * DAY
        out.append((clock, "view", event))

        saved = rng.random() < 0.10 + 0.45 * a
        registered = rng.random() < 0.05 + 0.60 * a + (0.15 if saved else 0.0)
        attended = False

        if saved:
            out.append((clock + rng.uniform(60, 7200), "save", event))

        if registered:
            t_reg = clock + rng.uniform(3600, 72 * 3600)
            out.append((t_reg, "register", event))
            if rng.random() < 0.10 * (1 - a):
                out.append((t_reg + rng.uniform(3600, 48 * 3600), "unregister", event))
            elif rng.random() < 0.55 + 0.35 * a:
                out.append((t_reg + rng.uniform(1, 6) * DAY, "attend", event))
                attended = True
        elif saved and rng.random() < 0.15 * (1 - a):
            out.append((clock + rng.uniform(1, 5) * DAY, "unsave", event))

        dismissed = False
        if not saved and not registered and rng.random() < 0.40 * (1 - a) + 0.02:
            out.append((clock + rng.uniform(10, 1800), "not_interested", event))
            dismissed = True

        # decay old momentum, then add the effect of this journey
        for key in list(momentum):
            momentum[key] *= 0.85
        category = event["category"]
        units = (1.0 if saved else 0.0) + (1.0 if registered else 0.0) + (0.5 if attended else 0.0)
        if units:
            momentum[category] = min(0.5, momentum.get(category, 0.0) + momentum_strength * units)
        if dismissed:
            momentum[category] = max(-0.3, momentum.get(category, 0.0) - momentum_strength * 0.8)

    return out


# ------------------------------------------------------------------
# The simulator (pure: profiles + events in, rows out)
# ------------------------------------------------------------------
def simulate(
    profiles: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    *,
    copies: int = 20,
    days: int = 120,
    seed: int = 42,
    now: Optional[datetime] = None,
    run_id: Optional[str] = None,
    momentum: float = 0.12,
) -> List[Dict[str, Any]]:
    if copies < 1 or days < 1:
        raise ValueError("copies and days must be at least 1")

    now = now or utcnow()
    run_id = run_id or f"sim-{now:%Y%m%d-%H%M%S}-s{seed}"

    catalog = [e for e in (normalize_event(d) for d in events) if e]
    catalog.sort(key=lambda e: e["id"])                          # same order every run
    if len(catalog) < MIN_EVENTS:
        raise ValueError(f"need at least {MIN_EVENTS} published events, found {len(catalog)}")

    cleaned = [p for p in (normalize_profile(d) for d in profiles) if p]
    cleaned.sort(key=lambda p: json.dumps(p, sort_keys=True))
    if not cleaned:
        raise ValueError("no student profile has any interests, skills, domains or career goal")

    window = days * DAY
    rows: List[Dict[str, Any]] = []

    for p_index, profile in enumerate(cleaned):
        for copy in range(copies):
            student_id = f"sim-p{p_index:03d}-c{copy:02d}"
            rng = random.Random(f"{seed}|{p_index}|{copy}")
            prefs = _make_prefs(profile, catalog, rng)
            steps = _simulate_student(rng, prefs, catalog, momentum)

            span = max(offset for offset, _, _ in steps)
            scale = 1.0
            if span > 0.95 * window:                              # squeeze into the window
                scale = 0.95 * window / span
                span *= scale
            start = now - timedelta(seconds=window) + timedelta(seconds=rng.uniform(0, max(0.0, window - span)))

            for offset, action, event in steps:
                rows.append({
                    "student_id": student_id,
                    "event_id": event["id"],
                    "action": action,
                    "timestamp": start + timedelta(seconds=offset * scale),
                    "source": SIM_SOURCE,
                    "category": event["category"],
                    "organizer": event["organizer"],
                    "skills": event["skills"][:10],
                    "event_date": event["date"],
                    "event_title": event["title"][:120],
                    "simulated": True,
                    "sim_run_id": run_id,
                    "sim_seed": seed,
                })

    rows.sort(key=lambda r: (r["student_id"], r["timestamp"]))
    return rows


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    per_student = Counter(r["student_id"] for r in rows)
    lengths = sorted(per_student.values())
    return {
        "rows": len(rows),
        "students": len(per_student),
        "by_action": dict(Counter(r["action"] for r in rows)),
        "interactions_per_student": {
            "min": lengths[0] if lengths else 0,
            "median": lengths[len(lengths) // 2] if lengths else 0,
            "max": lengths[-1] if lengths else 0,
        },
        "by_category": dict(Counter(r["category"] for r in rows)),
    }


# ------------------------------------------------------------------
# Database (db is passed in)
# ------------------------------------------------------------------
def load_profiles(db) -> List[Dict[str, Any]]:
    return list(db.profiles.find({}, {"_id": 0, "interests": 1, "skills": 1,
                                      "preferred_domains": 1, "career_goal": 1, "career_goals": 1}))


def load_events(db) -> List[Dict[str, Any]]:
    return list(db.events.find(PUBLIC_EVENT_FILTER))


def write_rows(db, rows: Sequence[Dict[str, Any]], collection: str = SIM_COLLECTION) -> int:
    """Insert simulated rows. Refuses to write anywhere except the simulated collection."""
    if collection != SIM_COLLECTION or collection == REAL_COLLECTION:
        raise ValueError("simulated rows may only be written to " + SIM_COLLECTION)
    if any(not r.get("simulated") for r in rows):
        raise ValueError("refusing to write rows that are not flagged simulated")

    rows = list(rows)
    for start in range(0, len(rows), 500):
        db[collection].insert_many(rows[start:start + 500])

    try:                                         # additive, safe to repeat
        db[collection].create_index([("student_id", 1), ("timestamp", 1)], name="sim_by_student_time")
        db[collection].create_index([("sim_run_id", 1)], name="sim_by_run")
    except Exception:
        pass
    return len(rows)


def main(argv: Optional[Sequence[str]] = None, db=None) -> int:
    parser = argparse.ArgumentParser(description="Generate SIMULATED interactions (clearly flagged).")
    parser.add_argument("--copies", type=int, default=20, help="virtual students per real profile")
    parser.add_argument("--days", type=int, default=120, help="length of the simulated timeline")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--momentum", type=float, default=0.12, help="0 turns the sequential pattern off")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true", help="show statistics, write nothing")
    args = parser.parse_args(argv)

    if db is None:
        from database import db as real_db  # imported only when run as a script
        db = real_db

    profiles = load_profiles(db)
    events = load_events(db)
    print(f"profiles found: {len(profiles)}   published events found: {len(events)}")

    try:
        rows = simulate(profiles, events, copies=args.copies, days=args.days,
                        seed=args.seed, run_id=args.run_id, momentum=args.momentum)
    except ValueError as error:
        print("Cannot simulate:", error)
        return 1

    print(json.dumps(summarize(rows), indent=2))
    print("\nEvery row is flagged simulated=true. They are NOT real student behaviour.")

    if args.dry_run:
        print("dry run: nothing was written.")
        return 0

    existing = db[SIM_COLLECTION].distinct("sim_run_id")
    written = write_rows(db, rows)
    print(f"\nwrote {written} rows to '{SIM_COLLECTION}' as run '{rows[0]['sim_run_id']}'.")
    if existing:
        print(f"earlier simulation runs kept untouched: {sorted(existing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())