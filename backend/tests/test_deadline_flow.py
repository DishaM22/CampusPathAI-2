"""
End-to-end check of the deadline-change endpoints against an in-memory fake
database (the real handler code is pulled out of main.py with ast).
"""
import ast, pathlib, sys, types
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import BaseModel

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import registration_deadline as rd  # noqa: E402


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail


class ObjectId(str):
    pass


def get(doc, path):
    for part in path.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def match(doc, query):
    for key, cond in query.items():
        value = get(doc, key)
        if isinstance(cond, dict) and "$ne" in cond:
            if value == cond["$ne"]:
                return False
        elif value != cond:
            return False
    return True


class Cursor(list):
    def sort(self, *a, **k): return self
    def limit(self, n): return Cursor(self[:n])


class Col:
    def __init__(self): self.docs = []; self.n = 0
    def insert_one(self, d):
        self.n += 1; d.setdefault("_id", ObjectId(f"id{self.n}")); self.docs.append(d)
    def insert_many(self, ds): [self.insert_one(d) for d in ds]
    def find(self, q=None, proj=None): return Cursor(d for d in self.docs if match(d, q or {}))
    def find_one(self, q): return next(iter(self.find(q)), None)
    def count_documents(self, q): return len(self.find(q))
    def update_one(self, q, ch):
        d = self.find_one(q)
        if not d: return types.SimpleNamespace(matched_count=0)
        for k, v in ch.get("$set", {}).items(): d[k] = v
        for k in ch.get("$unset", {}): d.pop(k, None)
        for k, v in ch.get("$push", {}).items(): d.setdefault(k, []).append(v)
        return types.SimpleNamespace(matched_count=1)


class DB:
    def __init__(self): self.students, self.events, self.notifications, self.event_registrations = Col(), Col(), Col(), Col()


def load(db, who):
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    want_f = {"_deadline_actor", "_deadline_event", "_deadline_label", "_deadline_error",
              "request_deadline_change", "admin_deadline_requests", "review_deadline_request",
              "event_requires_registration", "is_event_public", "event_belongs_to_club",
              "user_club_id", "make_club_id", "normalize_text"}
    want_c = {"DeadlineChangeBody", "DeadlineReviewBody"}
    class App:
        def post(self, *a, **k): return lambda f: f
        get = post
    ns = {"HTTPException": HTTPException, "ObjectId": ObjectId, "BaseModel": BaseModel, "Header": lambda d=None: d,
          "datetime": datetime, "timezone": timezone, "app": App(), "db": db,
          "get_student_id_from_token": lambda auth: who["id"],
          "deadline_problem": rd.deadline_problem, "parse_deadline": rd.parse_deadline,
          "stored_deadline": rd.stored_deadline, "to_iso": rd.to_iso}
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and node.name in want_f) or (isinstance(node, ast.ClassDef) and node.name in want_c):
            node.decorator_list = []
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
    return types.SimpleNamespace(**ns)


NOW = rd.now_ist()
fmt = lambda dt: dt.strftime("%Y-%m-%dT%H:%M")


@pytest.fixture
def world():
    db, who = DB(), {"id": "u-club"}
    db.students.docs += [
        {"_id": ObjectId("u-club"), "role": "club", "status": "active", "name": "Coord", "club_id": "c1", "club_name": "C1"},
        {"_id": ObjectId("u-other"), "role": "club", "status": "active", "name": "Other", "club_id": "c2", "club_name": "C2"},
        {"_id": ObjectId("u-admin"), "role": "admin", "name": "Admin"},
        {"_id": ObjectId("u-stud"), "role": "student", "name": "S"},
    ]
    d = (NOW + timedelta(days=10)).strftime("%Y-%m-%d")
    db.events.insert_one({"_id": ObjectId("e1"), "title": "Hack", "date": d, "time": "10:00 - 12:00", "club_id": "c1",
                          "created_by": "u-club", "approval_status": "published", "registration_required": True,
                          "registration_deadline": rd.to_iso(NOW + timedelta(days=3))})
    db.event_registrations.docs += [
        {"event_id": "e1", "student_id": "s1", "status": "registered"},
        {"event_id": "e1", "student_id": "s2", "status": "registered"},
        {"event_id": "e1", "student_id": "s3", "status": "cancelled"},
    ]
    return db, who, load(db, who)


def ask(M, **k):
    return M.request_deadline_change("e1", M.DeadlineChangeBody(**k), "tok")


def code(fn, *a, **k):
    with pytest.raises(HTTPException) as e:
        fn(*a, **k)
    return e.value.status_code


GOOD = lambda: dict(registration_deadline=fmt(NOW + timedelta(days=5)), reason="Exams clash with the old date")


def test_request_keeps_old_deadline_until_approved(world):
    db, who, M = world
    old = db.events.docs[0]["registration_deadline"]
    ask(M, **GOOD())
    ev = db.events.docs[0]
    assert ev["registration_deadline"] == old and ev["deadline_change_request"]["status"] == "pending"
    assert ev["registration_deadline_history"][0]["action"] == "requested"
    assert any(n["type"] == "deadline_change" and n["recipient"] == "admin" for n in db.notifications.docs)


def test_request_validation_and_permissions(world):
    db, who, M = world
    assert code(ask, M, registration_deadline=GOOD()["registration_deadline"], reason="short") == 422
    assert code(ask, M, registration_deadline=fmt(NOW - timedelta(days=1)), reason="long enough reason") == 422
    assert code(ask, M, registration_deadline=fmt(NOW + timedelta(days=11)), reason="long enough reason") == 422   # after start
    who["id"] = "u-other"; assert code(ask, M, **GOOD()) == 403          # other club
    who["id"] = "u-stud"; assert code(ask, M, **GOOD()) == 403
    who["id"] = "u-club"; ask(M, **GOOD())
    assert code(ask, M, **GOOD()) == 409                                  # already pending


def test_unpublished_and_no_registration_events_refused(world):
    db, who, M = world
    db.events.docs[0]["approval_status"] = "pending_hod"
    assert code(ask, M, **GOOD()) == 409
    db.events.docs[0].update(approval_status="published", registration_required=False)
    assert code(ask, M, **GOOD()) == 400


def test_admin_approve_updates_and_notifies_registered_students_only(world):
    db, who, M = world
    ask(M, **GOOD())
    who["id"] = "u-admin"
    assert len(M.admin_deadline_requests("tok")["requests"]) == 1
    out = M.review_deadline_request("e1", M.DeadlineReviewBody(decision="approved"), "tok")
    ev = db.events.docs[0]
    assert out["students_notified"] == 2 and "deadline_change_request" not in ev
    assert ev["registration_deadline"].endswith("+05:30") and ev["registration_deadline"] != ""
    assert [h["action"] for h in ev["registration_deadline_history"]] == ["requested", "approved"]
    students = [n for n in db.notifications.docs if n.get("audience") == "student"]
    assert sorted(n["recipient"] for n in students) == ["s1", "s2"]
    assert len(db.event_registrations.docs) == 3 and all(r["status"] != "x" for r in db.event_registrations.docs)  # untouched
    assert code(M.review_deadline_request, "e1", M.DeadlineReviewBody(decision="approved"), "tok") == 409   # no longer pending


def test_admin_reject_keeps_deadline_and_needs_remarks(world):
    db, who, M = world
    old = db.events.docs[0]["registration_deadline"]
    ask(M, **GOOD()); who["id"] = "u-admin"
    assert code(M.review_deadline_request, "e1", M.DeadlineReviewBody(decision="rejected", remarks="no"), "tok") == 400
    M.review_deadline_request("e1", M.DeadlineReviewBody(decision="rejected", remarks="Deadline stays as announced"), "tok")
    ev = db.events.docs[0]
    assert ev["registration_deadline"] == old and "deadline_change_request" not in ev
    assert not [n for n in db.notifications.docs if n.get("audience") == "student"]
    assert ev["registration_deadline_history"][-1]["action"] == "rejected"


def test_only_admin_can_review(world):
    db, who, M = world
    ask(M, **GOOD())
    assert code(M.review_deadline_request, "e1", M.DeadlineReviewBody(decision="approved"), "tok") == 403
    assert code(M.admin_deadline_requests, "tok") == 403


def test_approve_refused_if_requested_deadline_already_passed(world):
    db, who, M = world
    ask(M, **GOOD())
    db.events.docs[0]["deadline_change_request"]["new_deadline"] = rd.to_iso(NOW - timedelta(hours=1))
    who["id"] = "u-admin"
    assert code(M.review_deadline_request, "e1", M.DeadlineReviewBody(decision="approved"), "tok") == 409