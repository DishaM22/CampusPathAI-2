"""
Phase B tests, part 2: the database layer, using an in-memory MongoDB.

SAFE: replaces the `database` module with mongomock BEFORE importing the
Phase B modules and refuses to run if the real database is already loaded.
It never touches MongoDB Atlas.

Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_phase_b_db.py -q
Needs: pip install pytest mongomock
"""
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import mongomock
import pytest

if "database" in sys.modules and "mongomock" not in type(sys.modules["database"].db).__module__:
    pytest.skip("real database already imported; refusing to run against it", allow_module_level=True)

_stub = types.ModuleType("database")
_stub.db = mongomock.MongoClient()["phase_b_test"]
sys.modules["database"] = _stub

import build_sequences as bs  # noqa: E402
import simulate_interactions as sim  # noqa: E402

db = _stub.db
assert "mongomock" in type(db).__module__, "tests must use the in-memory database"

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
COLLECTIONS = ("interactions", "interactions_simulated", "events", "profiles")


@pytest.fixture(autouse=True)
def clean():
    for name in COLLECTIONS:
        db[name].delete_many({})
    yield


def real_row(student, minutes, action="view", **extra):
    return {
        "student_id": student, "event_id": "e1", "action": action,
        "timestamp": T0 + timedelta(minutes=minutes), "source": "server",
        "category": "Technical", "organizer": "AI Club", **extra,
    }


def seed_world():
    """3+ published events, 1 pending event, 2 profiles."""
    published = []
    for i, (cat, org, title) in enumerate([
        ("Technical", "AI Club", "Machine Learning Bootcamp"),
        ("Technical", "Cyber Club", "Cybersecurity Challenge"),
        ("Cultural", "Music Club", "Music Night"),
        ("Sports", "Sports Club", "Football Cup"),
    ]):
        published.append(str(db.events.insert_one({
            "title": title, "category": cat, "organizer": org, "skills": ["Python"],
            "date": "2026-12-01", "approval_status": "published",
        }).inserted_id))
    pending = str(db.events.insert_one({
        "title": "Not yet approved", "category": "Technical", "organizer": "AI Club",
        "skills": [], "date": "2026-12-02", "approval_status": "pending_hod",
    }).inserted_id)
    db.profiles.insert_many([
        {"student_id": "real-student-1", "interests": ["machine learning"], "skills": ["Python"],
         "preferred_domains": ["Technical"], "career_goal": "AI engineer"},
        {"student_id": "real-student-2", "interests": ["music"], "skills": [],
         "preferred_domains": ["Cultural"], "career_goal": ""},
    ])
    return published, pending


# ------------------------------------------------------------ loading rows
def test_load_rows_real_excludes_simulated_even_if_one_is_in_the_real_collection():
    db.interactions.insert_many([
        real_row("a", 1), real_row("a", 2, simulated=True),          # a mistake that must not leak
        real_row("a", 3, "impression"),
    ])
    db.interactions_simulated.insert_one({**real_row("sim-p000-c00", 1), "simulated": True, "sim_run_id": "r1"})

    real = bs.load_rows(db, "real", bs.DEFAULT_ACTIONS)
    assert len(real) == 1 and not real[0].get("simulated")

    simulated = bs.load_rows(db, "simulated", bs.DEFAULT_ACTIONS)
    assert len(simulated) == 1 and simulated[0]["simulated"] is True

    both = bs.load_rows(db, "both", bs.DEFAULT_ACTIONS)
    assert len(both) == 2


def test_load_rows_can_pick_simulation_runs_and_forces_the_flag():
    db.interactions_simulated.insert_many([
        {**real_row("sim-1", 1), "sim_run_id": "r1"},                 # flag missing on purpose
        {**real_row("sim-2", 1), "sim_run_id": "r2", "simulated": True},
    ])
    rows = bs.load_rows(db, "simulated", bs.DEFAULT_ACTIONS, sim_run_ids=["r1"])
    assert [r["student_id"] for r in rows] == ["sim-1"]
    assert rows[0]["simulated"] is True


# ------------------------------------------------------------ the simulator script
def test_simulate_dry_run_writes_nothing():
    seed_world()
    assert sim.main(["--dry-run", "--copies", "3"], db=db) == 0
    assert db.interactions_simulated.count_documents({}) == 0
    assert db.interactions.count_documents({}) == 0


def test_simulate_writes_only_to_the_simulated_collection_and_marks_every_row():
    published, pending = seed_world()
    db.interactions.insert_one(real_row("a", 1))                        # one real row that must stay as it is
    assert sim.main(["--copies", "4", "--seed", "11", "--run-id", "run-a"], db=db) == 0

    assert db.interactions.count_documents({}) == 1                     # real data untouched
    rows = list(db.interactions_simulated.find({}))
    assert rows and all(r["simulated"] is True and r["sim_run_id"] == "run-a" for r in rows)
    assert all(r["student_id"].startswith("sim-p") for r in rows)
    assert not any(r["student_id"].startswith("real-") for r in rows)  # no real ids
    used = {r["event_id"] for r in rows}
    assert used <= set(published) and pending not in used               # unpublished events never used


def test_two_runs_are_both_kept():
    seed_world()
    sim.main(["--copies", "2", "--run-id", "run-1"], db=db)
    first = db.interactions_simulated.count_documents({})
    sim.main(["--copies", "2", "--run-id", "run-2", "--seed", "5"], db=db)
    assert db.interactions_simulated.count_documents({"sim_run_id": "run-1"}) == first
    assert db.interactions_simulated.count_documents({"sim_run_id": "run-2"}) > 0


def test_simulate_refuses_when_there_is_not_enough_input(capsys):
    db.events.insert_one({"title": "only one", "category": "Technical", "organizer": "x", "approval_status": "published"})
    assert sim.main(["--copies", "2"], db=db) == 1
    assert "Cannot simulate" in capsys.readouterr().out
    assert db.interactions_simulated.count_documents({}) == 0


# ------------------------------------------------------------ the builder script
def test_build_from_simulated_data_writes_all_files_and_warns(tmp_path):
    seed_world()
    sim.main(["--copies", "15", "--run-id", "run-z"], db=db)
    out = tmp_path / "seq"
    assert bs.main(["--source", "simulated", "--out", str(out)], db=db) == 0

    for name in ("train.jsonl", "val.jsonl", "test.jsonl", "vocab.json", "report.json", "report.md"):
        assert (out / name).exists(), name

    train = [json.loads(line) for line in (out / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    assert train and all(r["simulated"] is True for r in train)

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["data_source"] == "simulated" and report["contains_simulated_data"] is True
    assert "SIMULATED DATA WARNING" in (out / "report.md").read_text(encoding="utf-8")


def test_build_from_tiny_real_data_reports_zero_sequences_honestly(tmp_path, capsys):
    db.interactions.insert_many([real_row("s1", 1), real_row("s1", 2, "save"), real_row("s2", 1)])
    out = tmp_path / "seq"
    assert bs.main(["--source", "real", "--out", str(out)], db=db) == 0
    assert (out / "train.jsonl").read_text(encoding="utf-8") == ""
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["students"]["real_with_at_least_min_len_interactions"] == 0
    assert report["contains_simulated_data"] is False
    assert "no real student has enough interactions" in capsys.readouterr().out


def test_real_source_never_picks_up_simulated_rows(tmp_path):
    seed_world()
    sim.main(["--copies", "5"], db=db)                                   # only simulated rows exist
    out = tmp_path / "seq"
    bs.main(["--source", "real", "--out", str(out)], db=db)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["rows"]["read"] == 0 and report["students"]["simulated"] == 0