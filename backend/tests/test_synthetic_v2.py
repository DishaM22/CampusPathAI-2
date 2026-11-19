"""
tests/test_synthetic_v2.py
Pure tests: no Atlas, no torch. Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_synthetic_v2.py -q
"""
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import build_sequences as bs            # noqa: E402
import simulate_interactions as v1      # noqa: E402
import synthetic_v2 as s2               # noqa: E402

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
REAL = [
    {"_id": "r1", "category": "Technical", "organizer": "Beta club"},
    {"_id": "r2", "category": "Workshop", "organizer": "Beta Club"},
    {"_id": "r3", "category": "Hackathon", "organizer": "CyberPhantom"},
    {"_id": "r4", "category": "Workshop", "organizer": "Alpha club"},
]


def make(seed=7, students=300):
    return s2.generate(REAL, students=students, seed=seed, now=NOW)


def test_every_row_is_flagged_synthetic_and_uses_fake_ids():
    rows = make()
    assert rows
    for r in rows:
        assert r["simulated"] is True and r["sim_version"] == "v2"
        assert r["sim_run_id"].startswith("sim2-")
        assert r["student_id"].startswith("sim2-s")
        assert r["event_id"].startswith("sx2-")
        assert r["source"] == "simulated"


def test_same_seed_same_rows_different_seed_differs():
    a, b, c = make(7), make(7), make(8)
    key = lambda rs: [(r["student_id"], r["event_id"], r["action"], r["timestamp"]) for r in rs]
    assert key(a) == key(b)
    assert key(a) != key(c)


def test_no_future_timestamps_and_steps_in_order():
    rows = make()
    assert all(r["timestamp"] <= NOW for r in rows)
    by = {}
    for r in rows:
        by.setdefault((r["student_id"], r["event_id"]), {}).setdefault(r["action"], r["timestamp"])
    checked = 0
    for steps in by.values():
        if "view" in steps:
            for later in ("save", "register", "not_interested"):
                if later in steps:
                    assert steps["view"] <= steps[later]
                    checked += 1
        if "register" in steps and "attend" in steps:
            assert steps["register"] <= steps["attend"]
            checked += 1
    assert checked > 100


def test_students_only_see_events_before_they_happen():
    for r in make():
        if r["action"] == "view":
            assert r["timestamp"].date().isoformat() <= r["event_date"]


def test_variety_cold_start_activity_levels_categories_clubs():
    s = s2.summarize(make(students=500))
    assert set(s["by_activity_level"]) == {"cold", "casual", "heavy"}
    assert s["students_with_3plus_rows"] < s["students_with_rows"]      # cold-start students exist
    assert s["distinct_categories"] >= 15 and s["distinct_clubs"] >= 20
    assert s["rows_per_student"]["max"] > 3 * s["rows_per_student"]["min"]


def test_real_categories_and_clubs_reach_the_vocabulary():
    """Needed for the demo: the model can only score text it saw in training."""
    data = bs.build_datasets(make(students=600))
    vocab = data["vocab"]
    assert len(data["train"]) > 100 and len(data["val"]) > 20 and len(data["test"]) > 20
    for club in ("beta club", "alpha club", "cyberphantom"):
        assert club in vocab["organizers"]
    for cat in ("technical", "workshop", "hackathon"):
        assert cat in vocab["categories"]


class _Col:
    def __init__(self): self.docs = []
    def insert_many(self, docs): self.docs += list(docs)
    def create_index(self, *a, **k): return "ok"


class _FakeDb(dict):
    def __missing__(self, key):
        self[key] = _Col()
        return self[key]


def test_rows_go_only_to_the_simulated_collection():
    db = _FakeDb()
    n = v1.write_rows(db, make(students=50))
    assert n == len(db["interactions_simulated"].docs) > 0
    assert "interactions" not in db                              # never touched
    bad = make(students=5)
    bad[0] = {**bad[0], "simulated": False}
    try:
        v1.write_rows(db, bad)
        assert False, "unflagged row must be refused"
    except ValueError:
        pass