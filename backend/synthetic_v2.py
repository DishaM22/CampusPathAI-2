"""
synthetic_v2.py
===============
DEMO / PIPELINE-TEST data: a more varied simulator than simulate_interactions.py.

READ THIS FIRST
---------------
Everything here is SYNTHETIC. A model trained on it learns only the rules
written in this file (listed below), so its scores say nothing about how real
students behave. Every row:
  * goes ONLY to the `interactions_simulated` collection (never `interactions`),
  * carries simulated=true, sim_run_id, sim_version="v2",
  * uses made-up student ids "sim2-s00123" and made-up event ids "sx2-...".
  * sequential_recommender.py keeps blocking a model trained on it unless the
    developer override switches are set (that is what a labelled demo does).

What is different from v1 (simulate_interactions.py)
----------------------------------------------------
  * ~300 short-lived synthetic events (different lifetimes) across ~20 clubs and
    the campus categories, instead of 11 events / 4 clubs. Your REAL events'
    categories and clubs are added too, so a model trained on this data can
    score real events in the demo (the model only knows text it saw in training).
  * ~60 student archetypes (tech-heavy, mixed, culture/sports, career-minded),
    different activity levels (cold-start, casual, heavy), late joiners.
  * Students only choose among events that are OPEN at that time.
  * Planted rules (all guesses): topic match, club loyalty, year effects,
    momentum, "Workshop -> Hackathon -> Career" style progressions, taste drift
    for some students, popularity, noise and random exploration.

Run from the backend folder:
    python synthetic_v2.py --dry-run
    python synthetic_v2.py --students 800
Nothing is ever deleted; every run gets its own run id.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

SIM_VERSION = "v2"
SIM_SOURCE = "simulated"
DAY = 86400.0

CATEGORIES = [
    "Technical", "Workshop", "Hackathon", "Seminar", "Sports", "Cultural",
    "Career", "Conference", "Masterclass", "Webinar", "Industry Visit",
    "Camp", "Guest Lecturers", "Competition", "Entrepreneurship", "Others",
]
TOPICS = ["ai", "ml", "web", "mobile", "security", "cloud", "iot", "robotics",
          "data", "design", "startup", "research", "career", "music", "dance",
          "sports", "arts", "social"]
TECH_TOPICS = ["ai", "ml", "web", "mobile", "security", "cloud", "iot",
               "robotics", "data", "design"]

# club -> (topics, {category: weight})   (ASSUMPTIONS)
CLUBS = {
    "AI Club": (["ai", "ml", "data"], {"Workshop": 3, "Seminar": 2, "Hackathon": 2, "Technical": 2, "Masterclass": 1}),
    "Coding Club": (["web", "mobile", "cloud"], {"Workshop": 3, "Hackathon": 3, "Competition": 2, "Technical": 2}),
    "Cyber Security Club": (["security", "cloud"], {"Competition": 3, "Workshop": 2, "Technical": 2, "Webinar": 1}),
    "Robotics Club": (["robotics", "iot"], {"Workshop": 3, "Camp": 2, "Competition": 2, "Technical": 2}),
    "IoT Club": (["iot", "cloud"], {"Workshop": 3, "Industry Visit": 1, "Technical": 2}),
    "Data Science Club": (["data", "ml", "ai"], {"Workshop": 3, "Seminar": 2, "Masterclass": 2, "Webinar": 1}),
    "Design Club": (["design", "web"], {"Workshop": 3, "Competition": 2, "Seminar": 1}),
    "Open Source Club": (["web", "cloud", "mobile"], {"Hackathon": 3, "Workshop": 2, "Webinar": 1}),
    "Entrepreneurship Cell": (["startup", "career"], {"Entrepreneurship": 3, "Seminar": 2, "Guest Lecturers": 2, "Competition": 1}),
    "Placement Cell": (["career"], {"Career": 4, "Industry Visit": 2, "Workshop": 1, "Seminar": 1}),
    "Research Cell": (["research", "ai"], {"Conference": 3, "Seminar": 3, "Guest Lecturers": 2, "Masterclass": 1}),
    "Higher Studies Cell": (["research", "career"], {"Seminar": 3, "Webinar": 2, "Guest Lecturers": 2}),
    "Cultural Club": (["music", "dance", "arts"], {"Cultural": 5, "Competition": 1}),
    "Music Club": (["music"], {"Cultural": 4, "Competition": 1, "Workshop": 1}),
    "Dance Club": (["dance"], {"Cultural": 4, "Competition": 1, "Workshop": 1}),
    "Drama Club": (["arts"], {"Cultural": 4, "Workshop": 1}),
    "Sports Club": (["sports"], {"Sports": 5, "Competition": 2, "Camp": 1}),
    "NSS": (["social"], {"Camp": 3, "Others": 2, "Seminar": 1}),
    "Literary Club": (["arts", "social"], {"Competition": 2, "Seminar": 2, "Cultural": 2}),
    "Photography Club": (["arts", "design"], {"Workshop": 2, "Competition": 2, "Camp": 1}),
}

CATEGORY_TOPICS = {   # used for events from clubs we do not know (your real clubs)
    "Hackathon": TECH_TOPICS, "Workshop": TECH_TOPICS, "Technical": TECH_TOPICS,
    "Competition": TECH_TOPICS, "Masterclass": ["ai", "ml", "data", "career"],
    "Seminar": ["research", "career", "ai"], "Conference": ["research", "ai"],
    "Webinar": ["career", "research", "ai"], "Guest Lecturers": ["research", "career"],
    "Career": ["career"], "Industry Visit": ["career", "startup"],
    "Entrepreneurship": ["startup", "career"], "Cultural": ["music", "dance", "arts"],
    "Sports": ["sports"], "Camp": ["social", "robotics"], "Others": ["social"],
}

PROGRESSION = {   # after a positive step on X, interest in Y rises for a few steps
    "Workshop": {"Hackathon": 0.30, "Masterclass": 0.12},
    "Seminar": {"Workshop": 0.25, "Conference": 0.15},
    "Webinar": {"Workshop": 0.20},
    "Hackathon": {"Career": 0.25, "Competition": 0.15},
    "Career": {"Industry Visit": 0.25},
    "Technical": {"Hackathon": 0.20},
    "Cultural": {"Sports": 0.10},
}
YEAR_BOOST = {
    "1st": {"Workshop": 0.15, "Cultural": 0.12, "Sports": 0.10, "Camp": 0.12, "Career": -0.15, "Conference": -0.15},
    "2nd": {"Workshop": 0.10, "Hackathon": 0.10, "Technical": 0.08},
    "3rd": {"Hackathon": 0.10, "Masterclass": 0.08, "Industry Visit": 0.10, "Career": 0.08},
    "4th": {"Career": 0.25, "Industry Visit": 0.15, "Conference": 0.10, "Camp": -0.15},
}
ACTIVITY = {"cold": (0.15, (1, 2)), "casual": (0.45, (3, 6)), "heavy": (0.40, (8, 20))}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


# ------------------------------------------------------------------
# Event catalog
# ------------------------------------------------------------------
def build_catalog(real_events: Sequence[Dict[str, Any]], n_events: int, days: int,
                  run_id: str, rng: random.Random) -> List[Dict[str, Any]]:
    """Synthetic events with lifetimes. Real categories/clubs are included."""
    club_info: Dict[str, Dict[str, Any]] = {
        name: {"topics": list(t), "cats": dict(c), "weight": rng.uniform(0.6, 1.6)}
        for name, (t, c) in CLUBS.items()
    }
    pairs = Counter()
    for e in real_events:
        cat = str(e.get("category") or "").strip() or "Others"
        org = str(e.get("organizer") or "").strip()
        if not org:
            continue
        pairs[(cat, org)] += 1
        known = next((k for k in club_info if _norm(k) == _norm(org)), None)
        if known is None:
            club_info[org] = {"topics": rng.sample(CATEGORY_TOPICS.get(cat, TOPICS), k=min(2, len(CATEGORY_TOPICS.get(cat, TOPICS)))),
                              "cats": {}, "weight": 1.2}
            known = org
        club_info[known]["cats"][cat] = club_info[known]["cats"].get(cat, 0) + 3

    names = sorted(club_info)
    weights = [club_info[n]["weight"] for n in names]
    events: List[Dict[str, Any]] = []

    def add(club: str, category: str, idx: int) -> None:
        event_day = rng.uniform(6, days - 1)
        life = rng.choices([rng.uniform(3, 7), rng.uniform(8, 20), rng.uniform(21, 35)],
                           weights=[0.35, 0.45, 0.20])[0]
        topics = list(club_info[club]["topics"])
        if category in CATEGORY_TOPICS and rng.random() < 0.5:
            topics.append(rng.choice(CATEGORY_TOPICS[category]))
        events.append({
            "id": f"sx2-{run_id[-6:]}-e{idx:04d}", "category": category, "organizer": club,
            "topics": sorted(set(topics)), "event_day": event_day,
            "open_day": max(0.0, event_day - life), "pop": rng.betavariate(2, 5),
        })

    idx = 0
    for _ in range(n_events):
        club = rng.choices(names, weights=weights)[0]
        cats = club_info[club]["cats"] or {"Others": 1}
        category = rng.choices(list(cats), weights=list(cats.values()))[0]
        if rng.random() < 0.12:
            category = rng.choice(CATEGORIES)
        add(club, category, idx); idx += 1
    for (cat, org), _n in pairs.items():          # make sure every real pair is well represented
        club = next((k for k in club_info if _norm(k) == _norm(org)), org)
        for _ in range(6):
            add(club, cat, idx); idx += 1
    return events


# ------------------------------------------------------------------
# Students
# ------------------------------------------------------------------
def make_archetypes(n: int, rng: random.Random) -> List[Dict[str, float]]:
    pools = [(0.50, TECH_TOPICS), (0.25, TOPICS), (0.15, ["music", "dance", "arts", "sports", "social"]),
             (0.10, ["career", "startup", "research"] + TECH_TOPICS[:3])]
    out = []
    for _ in range(n):
        pool = rng.choices([p for _, p in pools], weights=[w for w, _ in pools])[0]
        chosen = rng.sample(pool, k=min(len(pool), rng.randint(2, 4)))
        w = [rng.uniform(0.3, 1.0) for _ in chosen]
        s = sum(w)
        out.append({t: v / s for t, v in zip(chosen, w)})
    return out


def blend(a: Dict[str, float], b: Dict[str, float], alpha: float) -> Dict[str, float]:
    keys = set(a) | set(b)
    return {k: (1 - alpha) * a.get(k, 0.0) + alpha * b.get(k, 0.0) for k in keys}


def simulate_student(sid: str, rng: random.Random, catalog, archetypes, clubs, days: int,
                     now: datetime, run_id: str, seed: int) -> List[Dict[str, Any]]:
    activity = rng.choices(list(ACTIVITY), weights=[v[0] for v in ACTIVITY.values()])[0]
    lo, hi = ACTIVITY[activity][1]
    n_journeys = rng.randint(lo, hi)
    year = rng.choice(["1st", "2nd", "3rd", "4th"])
    base = dict(rng.choice(archetypes))
    for t in list(base):                                   # personal variation
        base[t] *= rng.uniform(0.7, 1.3)
    drift_to = dict(rng.choice(archetypes)) if rng.random() < 0.35 else None
    drift_day = rng.uniform(0.3, 0.7) * days
    favorites = set(rng.sample(clubs, k=rng.choice([1, 2, 2, 3])))
    temperature = rng.uniform(0.15, 0.35)
    join = rng.uniform(0, 0.75 * days) if rng.random() < 0.4 else rng.uniform(0, 0.15 * days)
    times = sorted(rng.uniform(join, days - 0.5) for _ in range(n_journeys))

    momentum: Dict[str, float] = {}
    prog: Dict[str, float] = {}
    seen: set = set()
    rows: List[Dict[str, Any]] = []
    start = now - timedelta(days=days)

    def emit(day: float, action: str, ev: Dict[str, Any]) -> None:
        ts = start + timedelta(days=day)
        if ts > now:
            return
        rows.append({
            "student_id": sid, "event_id": ev["id"], "action": action, "timestamp": ts,
            "source": SIM_SOURCE, "category": ev["category"], "organizer": ev["organizer"],
            "skills": ev["topics"][:10], "event_date": (start + timedelta(days=ev["event_day"])).date().isoformat(),
            "event_title": f"{ev['organizer']} {ev['category']}"[:120],
            "simulated": True, "sim_run_id": run_id, "sim_seed": seed,
            "sim_version": SIM_VERSION, "sim_activity": activity,
        })

    for t in times:
        open_events = [e for e in catalog if e["open_day"] <= t <= e["event_day"] and e["id"] not in seen]
        if not open_events:
            continue
        alpha = 0.0 if drift_to is None else min(1.0, max(0.0, (t - drift_day) / (0.15 * days)))
        topics = blend(base, drift_to, alpha) if drift_to else base

        def affinity(e: Dict[str, Any]) -> float:
            topic = min(1.0, sum(topics.get(x, 0.0) for x in e["topics"]) * 1.4)
            return max(0.0, min(1.0, 0.45 * topic + (0.20 if e["organizer"] in favorites else 0.0)
                                + YEAR_BOOST[year].get(e["category"], 0.0)
                                + momentum.get(e["category"], 0.0) + prog.get(e["category"], 0.0)
                                + 0.10 * e["pop"] + rng.gauss(0, 0.08)))

        affs = [affinity(e) for e in open_events]
        if rng.random() < 0.12:                              # random exploration
            i = rng.randrange(len(open_events))
        else:
            i = rng.choices(range(len(open_events)), weights=[math.exp(a / temperature) for a in affs])[0]
        ev, a = open_events[i], affs[i]
        seen.add(ev["id"])

        scale = 0.5 if activity == "cold" else 1.0
        emit(t, "view", ev)
        saved = rng.random() < (0.08 + 0.45 * a) * scale
        registered = rng.random() < (0.04 + 0.55 * a + (0.12 if saved else 0.0)) * scale
        attended = False
        if saved:
            emit(t + rng.uniform(0.001, 0.08), "save", ev)
        if registered:
            t_reg = min(t + rng.uniform(0.05, 2.5), ev["event_day"] - 0.01)
            emit(max(t, t_reg), "register", ev)
            if rng.random() < 0.10 * (1 - a):
                emit(min(max(t, t_reg) + rng.uniform(0.05, 1.5), ev["event_day"]), "unregister", ev)
            elif rng.random() < 0.55 + 0.35 * a:
                emit(ev["event_day"] + rng.uniform(0.0, 0.2), "attend", ev)
                attended = True
        elif saved and rng.random() < 0.15 * (1 - a):
            emit(t + rng.uniform(0.5, 4), "unsave", ev)
        dismissed = False
        if not saved and not registered and rng.random() < 0.40 * (1 - a) + 0.02:
            emit(t + rng.uniform(0.001, 0.02), "not_interested", ev)
            dismissed = True

        for k in list(momentum):
            momentum[k] *= 0.85
        for k in list(prog):
            prog[k] *= 0.70
        units = (1.0 if saved else 0.0) + (1.0 if registered else 0.0) + (0.5 if attended else 0.0)
        if units:
            momentum[ev["category"]] = min(0.45, momentum.get(ev["category"], 0.0) + 0.10 * units)
            for nxt, v in PROGRESSION.get(ev["category"], {}).items():
                prog[nxt] = min(0.5, prog.get(nxt, 0.0) + v)
        if dismissed:
            momentum[ev["category"]] = max(-0.3, momentum.get(ev["category"], 0.0) - 0.08)
    return rows


# ------------------------------------------------------------------
# The generator (pure: real events in, rows out)
# ------------------------------------------------------------------
def generate(real_events: Sequence[Dict[str, Any]], *, students: int = 800, events: int = 300,
             days: int = 150, seed: int = 7, now: Optional[datetime] = None,
             run_id: Optional[str] = None, archetypes: int = 60) -> List[Dict[str, Any]]:
    if students < 1 or events < 10 or days < 30:
        raise ValueError("need students >= 1, events >= 10 and days >= 30")
    now = now or utcnow()
    run_id = run_id or f"sim2-{now:%Y%m%d-%H%M%S}-s{seed}"
    rng = random.Random(f"{seed}|catalog")
    catalog = build_catalog(real_events, events, days, run_id, rng)
    clubs = sorted({e["organizer"] for e in catalog})
    arche = make_archetypes(archetypes, random.Random(f"{seed}|arche"))
    rows: List[Dict[str, Any]] = []
    for i in range(students):
        rows += simulate_student(f"sim2-s{i:05d}", random.Random(f"{seed}|student|{i}"),
                                 catalog, arche, clubs, days, now, run_id, seed)
    rows.sort(key=lambda r: (r["student_id"], r["timestamp"]))
    return rows


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    per = Counter(r["student_id"] for r in rows)
    lengths = sorted(per.values())
    act = {}
    for r in rows:
        act.setdefault(r["student_id"], r["sim_activity"])
    return {
        "rows": len(rows), "students_with_rows": len(per),
        "students_with_3plus_rows": sum(1 for n in per.values() if n >= 3),
        "by_action": dict(Counter(r["action"] for r in rows)),
        "by_activity_level": dict(Counter(act.values())),
        "rows_per_student": {"min": lengths[0], "median": lengths[len(lengths) // 2], "max": lengths[-1]} if lengths else {},
        "distinct_categories": len({r["category"] for r in rows}),
        "distinct_clubs": len({r["organizer"] for r in rows}),
    }


def main(argv: Optional[Sequence[str]] = None, db=None) -> int:
    p = argparse.ArgumentParser(description="Generate SYNTHETIC v2 interactions (clearly flagged).")
    p.add_argument("--students", type=int, default=800)
    p.add_argument("--events", type=int, default=300)
    p.add_argument("--days", type=int, default=150)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--run-id", default=None)
    p.add_argument("--dry-run", action="store_true", help="show statistics, write nothing")
    a = p.parse_args(argv)

    if db is None:
        from database import db as real_db
        db = real_db
    from simulate_interactions import load_events, write_rows, SIM_COLLECTION   # reuse v1's safety checks

    real_events = load_events(db)
    print(f"real published events used only for their categories/clubs: {len(real_events)}")
    rows = generate(real_events, students=a.students, events=a.events, days=a.days,
                    seed=a.seed, run_id=a.run_id)
    print(json.dumps(summarize(rows), indent=2))
    print("\nEvery row is flagged simulated=true. NOT real student behaviour.")
    if a.dry_run:
        print("dry run: nothing was written.")
        return 0
    existing = db[SIM_COLLECTION].distinct("sim_run_id")
    n = write_rows(db, rows)
    print(f"\nwrote {n} rows to '{SIM_COLLECTION}' as run '{rows[0]['sim_run_id']}'.")
    if existing:
        print(f"earlier simulation runs kept untouched: {sorted(existing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())