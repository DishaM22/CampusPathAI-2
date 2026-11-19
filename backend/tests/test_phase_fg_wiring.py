"""
Phase F/G tests: how main.py hands the learned signals to the engine.

SAFE: replaces the `database` module with an in-memory MongoDB (mongomock) BEFORE
importing main.py, and refuses to run if the real database was already imported.

Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_phase_fg_wiring.py -q
Needs:  pip install pytest mongomock httpx
"""
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISABLE_SEMANTIC_MODEL", "true")

import jwt
import mongomock
import pytest
from fastapi.testclient import TestClient

if "database" in sys.modules and "mongomock" not in type(sys.modules["database"].db).__module__:
    pytest.skip("real database already imported; refusing to run against it", allow_module_level=True)

_stub = types.ModuleType("database")
_stub.db = mongomock.MongoClient()["phase_fg_test"]
sys.modules["database"] = _stub

import main  # noqa: E402
import sequential_recommender  # noqa: E402
import trained_ranker  # noqa: E402

assert "mongomock" in type(main.db).__module__, "tests must use the in-memory database"
db = main.db
client = TestClient(main.app)

COLLECTIONS = ("students", "profiles", "events", "interactions", "not_interested_events",
               "event_registrations", "saved_events", "attendance")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def token(user_id):
    return {"Authorization": "Bearer " + jwt.encode({"student_id": str(user_id)}, main.SECRET_KEY, algorithm="HS256")}


def make_user(role, email, **extra):
    return db.students.insert_one({"name": role.title(), "email": email, "role": role, "status": "active",
                                   "password": main.password_hash.hash("Passw0rd!"), "department": "CSE", **extra}).inserted_id


def make_event(title, category="Workshop", skills=None):
    return str(db.events.insert_one({
        "title": title, "description": f"{title} description", "category": category, "organizer": "AI Club",
        "date": "2099-12-01", "time": "10:00 AM – 1:00 PM", "location": "Hall", "skills": skills or ["Python"],
        "capacity": 100, "created_by": "x", "created_by_role": "club", "approval_status": "published"}).inserted_id)


@pytest.fixture(autouse=True)
def clean():
    for name in COLLECTIONS:
        db[name].delete_many({})
    yield


def world():
    w = types.SimpleNamespace()
    w.student = make_user("student", "s1@x.com", student_id="S1", college_name="X")
    db.profiles.insert_one({"student_id": str(w.student), "interests": ["Machine Learning"], "skills": ["Python"],
                            "career_goal": "AI Engineer", "preferred_domains": ["Workshop"], "year": "3"})
    w.ml = make_event("Machine Learning Bootcamp", skills=["Python", "Machine Learning"])
    w.ai = make_event("AI Hackathon", category="Hackathon")
    w.music = make_event("Classical Music Evening", category="Cultural", skills=["Singing"])
    return w


def dashboard(who):
    r = client.get("/api/dashboard/events", headers=token(who))
    assert r.status_code == 200, r.text
    return r.json()


def by_id(items):
    return {str(i.get("id") or i.get("_id")): i for i in items}


class FixedRanker:
    def __init__(self, value):
        self.value = value

    def score_event_features(self, **kwargs):
        return self.value


# ------------------------------------------------------------------ off by default
def test_by_default_both_learned_signals_are_off_and_nothing_about_them_leaks_into_results():
    w = world()
    data = dashboard(w.student)
    assert data["learned_signals"] == {"sequential": False, "ranker": False}
    assert data["recommended"]
    for r in data["recommended"]:
        assert "sequential_score" not in r and "sequential" not in r["signals_used"]
        assert r["recommendation_model"] != "Hybrid + Learned Ranker"


def test_the_recommendations_are_identical_when_the_helpers_return_nothing():
    w = world()
    plain = dashboard(w.student)["recommended"]
    with mock.patch.object(main, "get_sequential_scores_for_student", return_value={}), \
            mock.patch.object(main, "get_ranker_for_engine", return_value=None):
        again = dashboard(w.student)["recommended"]
    assert [(r.get("id") or r.get("_id"), r["matchScore"]) for r in plain] == \
           [(r.get("id") or r.get("_id"), r["matchScore"]) for r in again]


# ------------------------------------------------------------------ on: the signals reach the engine
def test_sequential_scores_reach_the_engine_and_change_the_explanation():
    w = world()
    scores = {w.ml: 90.0, w.ai: 55.0, w.music: 10.0}
    with mock.patch.object(main, "get_sequential_scores_for_student", return_value=scores):
        data = dashboard(w.student)
    assert data["learned_signals"]["sequential"] is True
    out = by_id(data["recommended"])
    assert out[w.ml]["sequential_score"] == 90.0 and "sequential" in out[w.ml]["signals_used"]
    assert "Fits your recent activity" in out[w.ml]["reasons"]
    assert "Fits your recent activity" not in out[w.music]["reasons"]


def test_the_learned_ranker_reaches_the_engine():
    w = world()
    with mock.patch.object(main, "get_ranker_for_engine", return_value=FixedRanker(100.0)):
        data = dashboard(w.student)
    assert data["learned_signals"]["ranker"] is True
    for r in data["recommended"]:
        assert r["recommendation_model"] == "Hybrid + Learned Ranker" and r["mlp_score"] == 100.0


def test_a_ranker_with_no_opinion_changes_nothing():
    w = world()
    plain = [(str(r.get("id") or r.get("_id")), r["matchScore"]) for r in dashboard(w.student)["recommended"]]
    with mock.patch.object(main, "get_ranker_for_engine", return_value=FixedRanker(None)):
        data = dashboard(w.student)
    assert data["learned_signals"]["ranker"] is True                      # a ranker was supplied...
    assert [(str(r.get("id") or r.get("_id")), r["matchScore"]) for r in data["recommended"]] == plain   # ...but did not alter results


def test_hidden_events_stay_hidden_even_with_both_signals_on():
    w = world()
    client.post(f"/api/events/{w.ml}/not-interested", headers=token(w.student))
    with mock.patch.object(main, "get_sequential_scores_for_student", return_value={w.ml: 99.0, w.ai: 50.0, w.music: 20.0}), \
            mock.patch.object(main, "get_ranker_for_engine", return_value=FixedRanker(90.0)):
        data = dashboard(w.student)
    assert w.ml not in by_id(data["recommended"]) and w.ml in by_id(data["upcoming"])


# ------------------------------------------------------------------ the helper itself
def candidates(w):
    return [{"_id": w.ml, "category": "Workshop", "organizer": "AI Club"},
            {"_id": w.ai, "category": "Hackathon", "organizer": "AI Club"},
            {"_id": w.music, "category": "Cultural", "organizer": "AI Club"}]


def test_the_helper_does_nothing_and_reads_nothing_when_no_model_is_available():
    w = world()
    with mock.patch.object(sequential_recommender, "status", return_value={"available": False}), \
            mock.patch.object(db.interactions, "find") as spy:
        assert main.get_sequential_scores_for_student(str(w.student), candidates(w)) == {}
    spy.assert_not_called()                                      # no database read at all while the signal is off


def test_the_helper_passes_only_this_students_real_history_oldest_first_and_normalises():
    w = world()
    other = make_user("student", "s2@x.com")
    for minutes, action in ((30, "register"), (10, "view"), (20, "save")):
        db.interactions.insert_one({"student_id": str(w.student), "event_id": w.ml, "action": action, "category": "Workshop",
                                    "organizer": "AI Club", "timestamp": T0 + timedelta(minutes=minutes)})
    db.interactions.insert_one({"student_id": str(other), "event_id": w.ai, "action": "view", "category": "Hackathon",
                                "organizer": "AI Club", "timestamp": T0})
    seen = {}

    def fake_scores(rows, candidate_events):
        seen["rows"], seen["n"] = rows, len(candidate_events)
        return {w.ml: 0.50, w.ai: 0.20, w.music: 0.05}

    with mock.patch.object(sequential_recommender, "status", return_value={"available": True}), \
            mock.patch.object(sequential_recommender, "score_events", side_effect=fake_scores):
        out = main.get_sequential_scores_for_student(str(w.student), candidates(w))
    assert [r["action"] for r in seen["rows"]] == ["view", "save", "register"]       # this student only, oldest first
    assert seen["n"] == 3
    assert out == {w.ml: 100.0, w.ai: 50.0, w.music: 0.0}                           # rank-percentile, 0-100


def test_the_helper_returns_nothing_when_the_scores_carry_no_information_or_anything_fails():
    w = world()
    with mock.patch.object(sequential_recommender, "status", return_value={"available": True}):
        with mock.patch.object(sequential_recommender, "score_events", return_value={w.ml: 0.3}):          # too few -> neutral for all
            assert main.get_sequential_scores_for_student(str(w.student), candidates(w)) == {}
        with mock.patch.object(sequential_recommender, "score_events", return_value={}):
            assert main.get_sequential_scores_for_student(str(w.student), candidates(w)) == {}
        with mock.patch.object(sequential_recommender, "score_events", side_effect=RuntimeError("boom")):
            assert main.get_sequential_scores_for_student(str(w.student), candidates(w)) == {}
    with mock.patch.object(sequential_recommender, "status", side_effect=RuntimeError("boom")):
        assert main.get_sequential_scores_for_student(str(w.student), candidates(w)) == {}


def test_the_ranker_helper_returns_none_instead_of_raising():
    with mock.patch.object(trained_ranker, "get_active_ranker", side_effect=RuntimeError("boom")):
        assert main.get_ranker_for_engine() is None
    with mock.patch.object(trained_ranker, "get_active_ranker", return_value=None):
        assert main.get_ranker_for_engine() is None