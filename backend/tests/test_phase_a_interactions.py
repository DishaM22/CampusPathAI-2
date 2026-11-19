"""
Phase A tests: interaction logging (backend).

SAFE: this test replaces the `database` module with an in-memory MongoDB
(mongomock) BEFORE importing main.py, and refuses to run if the real
database was already imported. It never touches MongoDB Atlas.

Run from the backend folder:   python -m pytest tests/test_phase_a_interactions.py -q
Needs:                         pip install pytest mongomock httpx
"""
import sys
import types
from datetime import timedelta
from unittest import mock

import jwt
import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

if "database" in sys.modules and "mongomock" not in type(sys.modules["database"].db).__module__:
    pytest.skip("real database already imported; refusing to run against it", allow_module_level=True)

_stub = types.ModuleType("database")
_stub.db = mongomock.MongoClient()["phase_a_test"]
sys.modules["database"] = _stub

import main  # noqa: E402
import interactions  # noqa: E402

assert "mongomock" in type(main.db).__module__, "tests must use the in-memory database"
db = main.db
client = TestClient(main.app)


def token(user_id):
    return {"Authorization": "Bearer " + jwt.encode({"student_id": str(user_id)}, main.SECRET_KEY, algorithm="HS256")}


def make_user(role, email, **extra):
    return db.students.insert_one({
        "name": role.title(), "email": email, "role": role, "status": "active",
        "password": main.password_hash.hash("Passw0rd!"), "department": "CSE", **extra,
    }).inserted_id


def make_event(title, status="published", **extra):
    doc = {
        "title": title, "description": "d", "category": "Technical", "organizer": "AI Club",
        "date": "2026-12-01", "time": "10:00 AM – 11:00 AM", "location": "Hall",
        "skills": ["Python", "ML"], "capacity": 50, "created_by": "x", "created_by_role": "club", **extra,
    }
    if status:
        doc["approval_status"] = status
    return str(db.events.insert_one(doc).inserted_id)


def rows(student_id=None, action=None):
    query = {}
    if student_id:
        query["student_id"] = str(student_id)
    if action:
        query["action"] = action
    return list(db.interactions.find(query))


@pytest.fixture(autouse=True)
def clean():
    for name in ("students", "events", "interactions", "event_registrations", "saved_events", "attendance"):
        db[name].delete_many({})
    yield


def setup_world():
    w = types.SimpleNamespace()
    w.student = make_user("student", "s@x.com", student_id="S1", college_name="X")
    w.other = make_user("student", "s2@x.com", student_id="S2", college_name="X")
    w.club = make_user("club", "c@x.com", club_name="AI Club", college_name="X")
    w.admin = make_user("admin", "a@x.com")
    w.pub = make_event("Published workshop")
    w.pub2 = make_event("Second workshop", category="Hackathon")
    w.pending = make_event("Pending event", status="pending_hod")
    return w


# --------------------------------------------------------------------
# 1. SERVER-SIDE LOGGING: written only after the real action succeeds
# --------------------------------------------------------------------
def test_register_cancel_save_unsave_are_recorded():
    w = setup_world()
    h = token(w.student)

    assert client.post(f"/api/events/{w.pub}/register", headers=h).status_code == 200
    reg = rows(w.student, "register")
    assert len(reg) == 1
    assert reg[0]["event_id"] == w.pub and reg[0]["category"] == "Technical"
    assert reg[0]["organizer"] == "AI Club" and reg[0]["skills"] == ["Python", "ML"]
    assert reg[0]["source"] == "server" and reg[0]["timestamp"]

    assert client.delete(f"/api/events/{w.pub}/register", headers=h).status_code == 200
    assert len(rows(w.student, "unregister")) == 1

    assert client.post(f"/api/events/{w.pub2}/save", headers=h).status_code == 200
    assert client.post(f"/api/events/{w.pub2}/save", headers=h).json()["status"] == "already_saved"
    assert len(rows(w.student, "save")) == 1          # the repeat did not create a second row
    assert client.delete(f"/api/events/{w.pub2}/save", headers=h).status_code == 200
    assert len(rows(w.student, "unsave")) == 1


def test_failed_actions_record_nothing():
    w = setup_world()
    h = token(w.student)
    assert client.post(f"/api/events/{w.pending}/register", headers=h).status_code == 404
    assert client.post(f"/api/events/{w.pending}/save", headers=h).status_code == 404
    assert client.delete(f"/api/events/{w.pub}/register", headers=h).status_code == 404   # never registered
    assert client.delete(f"/api/events/{w.pub}/save", headers=h).status_code == 404       # never saved
    assert db.interactions.count_documents({}) == 0


def test_attendance_scan_is_recorded_for_the_student():
    w = setup_world()
    client.post(f"/api/events/{w.pub}/register", headers=token(w.student))
    qr = client.get(f"/api/events/{w.pub}/attendance/qr", headers=token(w.student)).json()["qr_data"]
    db.events.update_one({"_id": ObjectId(w.pub)}, {"$set": {"created_by": str(w.club)}})
    r = client.post(f"/api/events/{w.pub}/attendance/scan", json={"qr_data": qr}, headers=token(w.club))
    assert r.status_code == 200
    attended = rows(w.student, "attend")
    assert len(attended) == 1 and attended[0]["event_id"] == w.pub
    assert not rows(w.club)                     # the organizer's own id is never logged


def test_logging_failure_never_breaks_the_real_action():
    w = setup_world()
    with mock.patch.object(db.interactions, "insert_one", side_effect=RuntimeError("db down")):
        r = client.post(f"/api/events/{w.pub}/register", headers=token(w.student))
    assert r.status_code == 200                                   # registration still worked
    assert db.event_registrations.count_documents({"student_id": str(w.student)}) == 1
    assert interactions.record_interaction(db, "s", "e", "register") in (True, False)  # never raises


# --------------------------------------------------------------------
# 2. CLIENT-SIDE LOGGING: POST /api/interactions
# --------------------------------------------------------------------
def post(w, body, who=None):
    return client.post("/api/interactions", json=body, headers=token(who or w.student))


def test_view_is_recorded_once_per_window():
    w = setup_world()
    r = post(w, {"event_id": w.pub, "action": "view", "source": "event_details"})
    assert r.status_code == 200 and r.json()["status"] == "success"
    assert post(w, {"event_id": w.pub, "action": "view"}).json()["status"] == "duplicate"
    assert len(rows(w.student, "view")) == 1
    assert rows(w.student, "view")[0]["source"] == "event_details"
    assert post(w, {"event_id": w.pub2, "action": "view"}).json()["status"] == "success"
    # after the 30-minute window the same view counts again
    db.interactions.update_many({}, {"$set": {"timestamp": interactions.utcnow() - timedelta(minutes=45)}})
    assert post(w, {"event_id": w.pub, "action": "view"}).json()["status"] == "success"


def test_not_interested_is_recorded_and_deduplicated():
    w = setup_world()
    assert post(w, {"event_id": w.pub, "action": "not_interested"}).json()["status"] == "success"
    assert post(w, {"event_id": w.pub, "action": "not_interested"}).json()["status"] == "duplicate"
    assert len(rows(w.student, "not_interested")) == 1


def test_students_cannot_forge_server_actions():
    w = setup_world()
    for action in ("register", "attend", "save", "unsave", "unregister", "impression", "anything"):
        r = post(w, {"event_id": w.pub, "action": action})
        assert r.status_code == 400, action
    assert db.interactions.count_documents({}) == 0


def test_validation_and_access_rules():
    w = setup_world()
    assert post(w, {"event_id": w.pending, "action": "view"}).status_code == 404        # unpublished
    assert post(w, {"event_id": str(ObjectId()), "action": "view"}).status_code == 404  # unknown
    assert post(w, {"event_id": "not-an-id", "action": "view"}).status_code == 400
    assert client.post("/api/interactions", json={"event_id": w.pub, "action": "view"}).status_code in (401, 422)
    assert post(w, {"event_id": w.pub, "action": "view"}, who=w.club).status_code == 403      # not a student
    assert post(w, {"event_id": w.pub, "action": "view"}, who=w.admin).status_code == 403
    assert db.interactions.count_documents({}) == 0


def test_unknown_source_is_stored_as_other():
    w = setup_world()
    post(w, {"event_id": w.pub, "action": "view", "source": "<script>alert(1)</script>"})
    assert rows(w.student, "view")[0]["source"] == "other"


def test_hourly_rate_limit():
    w = setup_world()
    now = interactions.utcnow()
    db.interactions.insert_many([
        {"student_id": str(w.student), "event_id": "e", "action": "view", "source": "other", "timestamp": now}
        for _ in range(interactions.CLIENT_HOURLY_LIMIT)
    ])
    assert post(w, {"event_id": w.pub, "action": "view"}).status_code == 429
    assert post(w, {"event_id": w.pub, "action": "view"}, who=w.other).status_code == 200   # other students unaffected


# --------------------------------------------------------------------
# 3. IMPRESSIONS (events that were shown)
# --------------------------------------------------------------------
def test_impressions_only_for_visible_events_and_deduplicated():
    w = setup_world()
    h = token(w.student)
    body = {"event_ids": [w.pub, w.pub2, w.pub, w.pending, "bad-id", str(ObjectId())], "source": "dashboard_recommended"}
    r = client.post("/api/interactions/impressions", json=body, headers=h)
    assert r.status_code == 200 and r.json()["recorded"] == 2           # only the 2 published, no duplicates
    assert {x["event_id"] for x in rows(w.student, "impression")} == {w.pub, w.pub2}
    again = client.post("/api/interactions/impressions", json=body, headers=h).json()
    assert again["recorded"] == 0                                       # page refresh does not inflate history
    assert client.post("/api/interactions/impressions", json={"event_ids": []}, headers=h).json()["recorded"] == 0
    too_many = {"event_ids": [str(ObjectId()) for _ in range(interactions.MAX_IMPRESSIONS_PER_REQUEST + 1)]}
    assert client.post("/api/interactions/impressions", json=too_many, headers=h).status_code == 400
    assert client.post("/api/interactions/impressions", json=body, headers=token(w.club)).status_code == 403


# --------------------------------------------------------------------
# 4. TRANSPARENCY AND ADMIN SUMMARY
# --------------------------------------------------------------------
def test_student_sees_only_own_history_newest_first():
    w = setup_world()
    post(w, {"event_id": w.pub, "action": "view"})
    client.post(f"/api/events/{w.pub2}/save", headers=token(w.student))
    post(w, {"event_id": w.pub, "action": "view"}, who=w.other)
    r = client.get("/api/interactions/me", headers=token(w.student)).json()
    assert [x["action"] for x in r["interactions"]] == ["save", "view"]
    assert all(x["event_id"] in (w.pub, w.pub2) for x in r["interactions"])
    assert len(client.get("/api/interactions/me", headers=token(w.other)).json()["interactions"]) == 1
    assert client.get("/api/interactions/me", headers=token(w.club)).status_code == 403


def test_admin_summary():
    w = setup_world()
    post(w, {"event_id": w.pub, "action": "view"})
    post(w, {"event_id": w.pub2, "action": "view"})
    client.post(f"/api/events/{w.pub}/register", headers=token(w.student))
    post(w, {"event_id": w.pub, "action": "view"}, who=w.other)
    r = client.get("/api/admin/interactions/summary", headers=token(w.admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4 and body["byAction"] == {"view": 3, "register": 1}
    assert body["students"] == 2 and body["studentsWith3PlusInteractions"] == 1
    assert body["firstRecordedAt"] and body["lastRecordedAt"]
    assert client.get("/api/admin/interactions/summary", headers=token(w.student)).status_code == 403
    assert client.get("/api/admin/interactions/summary").status_code in (401, 422)


# --------------------------------------------------------------------
# 5. REGRESSION: the earlier safety rules still hold
# --------------------------------------------------------------------
def test_safety_rules_still_hold():
    w = setup_world()
    base = {"name": "Bad", "email": "bad@x.com", "password": "Passw0rd!", "department": "CSE"}
    assert client.post("/api/register", json={**base, "role": "admin"}).json()["success"] is False
    assert client.get(f"/api/events/{w.pending}").status_code == 404
    assert client.get(f"/api/events/{w.pub}").status_code == 200