"""
Phase H tests, part 2: the "not interested" endpoints and the dashboard.

SAFE: replaces the `database` module with an in-memory MongoDB (mongomock)
BEFORE importing main.py, and refuses to run if the real database was already
imported. It never touches MongoDB Atlas.

Run from the backend folder:
    $env:DISABLE_SEMANTIC_MODEL="true"
    python -m pytest tests/test_phase_h_not_interested.py -q
Needs:  pip install pytest mongomock httpx
"""
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISABLE_SEMANTIC_MODEL", "true")

import jwt
import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

if "database" in sys.modules and "mongomock" not in type(sys.modules["database"].db).__module__:
    pytest.skip("real database already imported; refusing to run against it", allow_module_level=True)

_stub = types.ModuleType("database")
_stub.db = mongomock.MongoClient()["phase_h_test"]
sys.modules["database"] = _stub

import main  # noqa: E402
import interactions  # noqa: E402

assert "mongomock" in type(main.db).__module__, "tests must use the in-memory database"
db = main.db                                   # whichever in-memory database main actually uses
client = TestClient(main.app)

COLLECTIONS = ("students", "profiles", "events", "interactions", "not_interested_events",
               "event_registrations", "saved_events", "attendance")


def token(user_id):
    return {"Authorization": "Bearer " + jwt.encode({"student_id": str(user_id)}, main.SECRET_KEY, algorithm="HS256")}


def make_user(role, email, **extra):
    return db.students.insert_one({
        "name": role.title(), "email": email, "role": role, "status": "active",
        "password": main.password_hash.hash("Passw0rd!"), "department": "CSE", **extra,
    }).inserted_id


def make_event(title, category="Workshop", status="published", skills=None, **extra):
    doc = {
        "title": title, "description": f"{title} description", "category": category, "organizer": "AI Club",
        "date": "2099-12-01", "time": "10:00 AM – 1:00 PM", "location": "Hall", "skills": skills or ["Python"],
        "capacity": 100, "created_by": "x", "created_by_role": "club", **extra,
    }
    if status:
        doc["approval_status"] = status
    return str(db.events.insert_one(doc).inserted_id)


@pytest.fixture(autouse=True)
def clean():
    for name in COLLECTIONS:
        db[name].delete_many({})
    yield


def world():
    w = types.SimpleNamespace()
    w.student = make_user("student", "s1@x.com", student_id="S1", college_name="X")
    w.other = make_user("student", "s2@x.com", student_id="S2", college_name="X")
    w.club = make_user("club", "c@x.com", club_name="AI Club", college_name="X")
    for who in (w.student, w.other):
        db.profiles.insert_one({"student_id": str(who), "interests": ["Machine Learning"], "skills": ["Python"],
                                "career_goal": "AI Engineer", "preferred_domains": ["Workshop"], "year": "3"})
    w.ml = make_event("Machine Learning Bootcamp", skills=["Python", "Machine Learning"])
    w.ai = make_event("AI Hackathon", category="Hackathon", skills=["Python"])
    w.music = make_event("Classical Music Evening", category="Cultural", skills=["Singing"])
    w.pending = make_event("Not yet approved", status="pending_hod")
    return w


def hide(w, event, who=None, **body):
    return client.post(f"/api/events/{event}/not-interested", json=body or None, headers=token(who or w.student))


def undo(w, event, who=None):
    return client.delete(f"/api/events/{event}/not-interested", headers=token(who or w.student))


def dashboard(who):
    r = client.get("/api/dashboard/events", headers=token(who))
    assert r.status_code == 200, r.text
    return r.json()


def event_ids(items):
    return {str(i.get("id") or i.get("_id")) for i in items}


def rows(**query):
    return list(db.not_interested_events.find(query))


# ------------------------------------------------------------------ hide / undo
def test_hide_and_undo_round_trip_and_nothing_is_deleted():
    w = world()
    r = hide(w, w.ml)
    assert r.status_code == 200 and r.json() == {"status": "success", "not_interested": True, "event_id": w.ml}
    (row,) = rows(student_id=str(w.student))
    assert row["active"] is True and row["event_id"] == w.ml
    assert row["event_title"] == "Machine Learning Bootcamp" and row["category"] == "Workshop" and row["created_at"]

    listed = client.get("/api/me/not-interested", headers=token(w.student)).json()["events"]
    assert [e["event_id"] for e in listed] == [w.ml] and listed[0]["title"] == "Machine Learning Bootcamp"

    r = undo(w, w.ml)
    assert r.status_code == 200 and r.json()["not_interested"] is False
    (row,) = rows(student_id=str(w.student))                         # the row is still there, just switched off
    assert row["active"] is False and row["undone_at"]
    assert client.get("/api/me/not-interested", headers=token(w.student)).json()["events"] == []


def test_hiding_again_after_an_undo_reuses_the_same_row():
    w = world()
    hide(w, w.ml); undo(w, w.ml); hide(w, w.ml)
    (row,) = rows(student_id=str(w.student))
    assert row["active"] is True


def test_hiding_twice_keeps_one_row_and_one_learning_log_entry():
    w = world()
    hide(w, w.ml); hide(w, w.ml)
    assert db.not_interested_events.count_documents({"student_id": str(w.student)}) == 1
    assert db.interactions.count_documents({"student_id": str(w.student), "action": "not_interested"}) == 1


def test_the_learning_log_gets_a_snapshot_and_the_source():
    w = world()
    hide(w, w.ml, source="dashboard_recommended")
    (log,) = list(db.interactions.find({"student_id": str(w.student), "action": "not_interested"}))
    assert log["event_id"] == w.ml and log["source"] == "dashboard_recommended"
    assert log["category"] == "Workshop" and log["organizer"] == "AI Club"
    hide(w, w.ai, source="<script>")                                   # an unknown source is stored as "other"
    other = db.interactions.find_one({"event_id": w.ai, "action": "not_interested"})
    assert other["source"] == "other"
    assert client.post(f"/api/events/{w.music}/not-interested", headers=token(w.student)).status_code == 200  # no body at all


def test_undo_is_logged_and_undoing_twice_is_a_clear_404():
    w = world()
    hide(w, w.ml); assert undo(w, w.ml).status_code == 200
    assert db.interactions.count_documents({"student_id": str(w.student), "action": "undo_not_interested"}) == 1
    assert undo(w, w.ml).status_code == 404
    assert undo(w, w.ai).status_code == 404                            # never hidden


def test_undo_all():
    w = world()
    hide(w, w.ml); hide(w, w.ai)
    hide(w, w.music, who=w.other)                                       # another student's hidden event
    r = client.delete("/api/me/not-interested", headers=token(w.student))
    assert r.status_code == 200 and r.json() == {"status": "success", "restored": 2}
    assert client.get("/api/me/not-interested", headers=token(w.student)).json()["events"] == []
    assert len(client.get("/api/me/not-interested", headers=token(w.other)).json()["events"]) == 1   # untouched


def test_bad_requests_are_refused():
    w = world()
    assert hide(w, "not-an-id").status_code == 400
    assert hide(w, str(ObjectId())).status_code == 404
    assert hide(w, w.pending).status_code == 404                        # unpublished events cannot be hidden
    for call in (lambda: hide(w, w.ml, who=w.club), lambda: undo(w, w.ml, who=w.club),
                 lambda: client.get("/api/me/not-interested", headers=token(w.club)),
                 lambda: client.delete("/api/me/not-interested", headers=token(w.club))):
        assert call().status_code == 403                               # only students
    assert client.post(f"/api/events/{w.ml}/not-interested").status_code in (401, 422)
    assert client.get("/api/me/not-interested").status_code in (401, 422)
    assert db.not_interested_events.count_documents({}) == 0


def test_the_interactions_endpoint_cannot_be_used_to_fake_an_undo():
    w = world()
    r = client.post("/api/interactions", json={"event_id": w.ml, "action": "undo_not_interested"}, headers=token(w.student))
    assert r.status_code == 400
    assert "undo_not_interested" in interactions.SERVER_ACTIONS and "undo_not_interested" not in interactions.CLIENT_ACTIONS


# ------------------------------------------------------------------ the dashboard
def test_the_dashboard_leaves_a_hidden_event_out_of_recommendations_but_keeps_it_browsable():
    w = world()
    before = dashboard(w.student)
    assert w.ml in event_ids(before["recommended"]), "precondition: the event is recommended before hiding"
    assert before["not_interested_ids"] == []

    hide(w, w.ml)
    after = dashboard(w.student)
    assert w.ml not in event_ids(after["recommended"])                  # gone from recommendations
    assert w.ml in event_ids(after["upcoming"])                         # still browsable (so it can be undone)
    assert after["not_interested_ids"] == [w.ml]
    assert (event_ids(before["recommended"]) - {w.ml}) <= event_ids(after["recommended"])   # nothing else disappeared


def test_undo_brings_the_event_back_into_recommendations():
    w = world()
    hide(w, w.ml)
    assert w.ml not in event_ids(dashboard(w.student)["recommended"])
    undo(w, w.ml)
    back = dashboard(w.student)
    assert w.ml in event_ids(back["recommended"]) and back["not_interested_ids"] == []


def test_hiding_is_per_student():
    w = world()
    hide(w, w.ml)
    assert w.ml in event_ids(dashboard(w.other)["recommended"])          # the other student still sees it
    assert dashboard(w.other)["not_interested_ids"] == []


def test_hiding_does_not_change_the_scores_of_the_other_events():
    w = world()

    def scores(items):                       # the dashboard keeps the event's "_id"; accept "id" too
        return {str(i.get("id") or i.get("_id")): i.get("matchScore") for i in items}

    before = scores(dashboard(w.student)["recommended"])
    assert before and all(score is not None for score in before.values()), "precondition: scored recommendations"
    hide(w, w.ml)
    after = scores(dashboard(w.student)["recommended"])
    assert w.ml not in after and len(after) >= 1
    for event_id, score in after.items():
        assert score == before[event_id], event_id


def test_a_failure_reading_the_hidden_list_never_breaks_the_dashboard():
    w = world()
    hide(w, w.ml)
    with mock.patch.object(db.not_interested_events, "find", side_effect=RuntimeError("db down")):
        data = dashboard(w.student)
    assert data["status"] == "success" and data["not_interested_ids"] == []
    assert w.ml in event_ids(data["recommended"])                       # fails open: shows it rather than breaking