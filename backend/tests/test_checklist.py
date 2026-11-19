"""Checklist endpoints against an in-memory fake database."""
import ast, pathlib, sys, types
from datetime import datetime, timezone
import pytest
from pydantic import BaseModel

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import checklist as ck  # noqa: E402
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
    return all(get(doc, k) == v for k, v in query.items())


class Cursor(list):
    def sort(self, *a, **k): return self
    def limit(self, n): return Cursor(self[:n])


class Col:
    def __init__(self): self.docs = []; self.n = 0
    def insert_one(self, d):
        self.n += 1; d.setdefault("_id", ObjectId(f"id{self.n}")); self.docs.append(d)
    def find(self, q=None, proj=None): return Cursor(d for d in self.docs if match(d, q or {}))
    def find_one(self, q): return next(iter(self.find(q)), None)
    def update_one(self, q, ch, upsert=False):
        d = self.find_one(q)
        if not d:
            if not upsert: return types.SimpleNamespace(matched_count=0)
            d = dict(q); self.insert_one(d)
        for k, v in ch.get("$set", {}).items():
            parts = k.split("."); t = d
            for part in parts[:-1]: t = t.setdefault(part, {})
            t[parts[-1]] = v
        for k, v in ch.get("$push", {}).items(): d.setdefault(k, []).append(v)
        return types.SimpleNamespace(matched_count=1)


class DB:
    def __init__(self): self.students, self.events, self.event_checklists = Col(), Col(), Col()


def load(db, who):
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
    want_f = {"_deadline_actor", "_deadline_event", "_checklist_access", "get_event_checklist",
              "update_checklist_item", "is_event_public", "event_belongs_to_club", "user_club_id",
              "make_club_id", "normalize_text", "hod_review_block_reason"}
    want_c = {"ChecklistItemBody"}
    class App:
        def get(self, *a, **k): return lambda f: f
        put = get
    ns = {"HTTPException": HTTPException, "ObjectId": ObjectId, "BaseModel": BaseModel, "Header": lambda d=None: d,
          "datetime": datetime, "timezone": timezone, "app": App(), "db": db,
          "get_student_id_from_token": lambda auth: who["id"],
          "CHECKLIST_ACTIVITIES": ck.ACTIVITIES, "ChecklistError": ck.ChecklistError,
          "checklist_view": ck.build_view, "checklist_clean": ck.clean_update,
          "checklist_default": ck.default_status,
          "canonical_department": lambda v: (str(v).strip().upper() or None) if v else None}
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and node.name in want_f) or (isinstance(node, ast.ClassDef) and node.name in want_c):
            node.decorator_list = []
            exec(compile(ast.Module([node], []), "main.py", "exec"), ns)
    missing = want_f - set(ns)
    assert not missing, missing
    return types.SimpleNamespace(**ns)


@pytest.fixture
def world():
    db, who = DB(), {"id": "u-club"}
    mk = lambda i, role, **k: db.students.insert_one({"_id": ObjectId(i), "role": role, "status": "active", "name": i, **k})
    mk("u-club", "club", club_id="c1", club_name="C1")
    mk("u-other", "club", club_id="c2", club_name="C2")
    mk("u-fac", "faculty", club_id="c1", club_name="C1")
    mk("u-hod", "hod", department="CSE")
    mk("u-dean", "dean"); mk("u-admin", "admin"); mk("u-stud", "student")
    db.events.insert_one({"_id": ObjectId("e1"), "title": "Hack", "club_id": "c1", "created_by": "u-club",
                          "approval_status": "published", "department": "CSE"})
    db.events.insert_one({"_id": ObjectId("e2"), "title": "Draft", "club_id": "c1", "created_by": "u-club",
                          "approval_status": "pending_hod"})
    return db, who, load(db, who)


def code(fn, *a, **k):
    with pytest.raises(HTTPException) as e:
        fn(*a, **k)
    return e.value.status_code


def put(M, key="banner", status="Ready", challenges="", event="e1"):
    return M.update_checklist_item(event, key, M.ChecklistItemBody(status=status, challenges=challenges), "tok")


def test_exactly_17_activities_in_order():
    assert len(ck.ACTIVITIES) == 17
    assert [l for _, l in ck.ACTIVITIES][0] == "Event Creative (Poster/Brochure)"
    assert [l for _, l in ck.ACTIVITIES][-1] == "Website Updates Status (Live or Yet to)"
    assert len(set(ck.KEYS)) == 17


def test_default_view_for_untouched_event(world):
    db, who, M = world
    out = M.get_event_checklist("e1", "tok")
    assert len(out["activities"]) == 17 and out["can_edit"] is True
    assert out["activities"][0]["status"] == "Not Started"
    assert out["activities"][-1]["status"] == "Yet to" and out["activities"][-1]["options"] == ["Yet to", "Live"]
    assert out["history"] == []


def test_save_status_and_challenges_persist_per_event(world):
    db, who, M = world
    put(M, "banner", "In Progress", "Printer delayed")
    out = M.get_event_checklist("e1", "tok")
    row = next(a for a in out["activities"] if a["key"] == "banner")
    assert row["status"] == "In Progress" and row["challenges"] == "Printer delayed"
    assert row["updated_by_name"] == "u-club"
    assert len(db.event_checklists.docs) == 1 and db.event_checklists.docs[0]["event_id"] == "e1"


def test_history_only_on_status_change(world):
    db, who, M = world
    put(M, "photography", "Ready"); put(M, "photography", "Ready", "added note"); put(M, "photography", "Not Applicable", "added note")
    h = M.get_event_checklist("e1", "tok")["history"]
    assert [(x["from"], x["to"]) for x in h] == [("Ready", "Not Applicable"), ("Not Started", "Ready")]   # newest first
    assert M.get_event_checklist("e1", "tok")["activities"][6]["challenges"] == "added note"


def test_validation(world):
    db, who, M = world
    assert code(put, M, "banner", "Live") == 422                    # not an option for this item
    assert code(put, M, "website_updates_status", "Ready") == 422   # only Yet to / Live
    assert code(put, M, "nope", "Ready") == 422
    assert code(put, M, "banner", "Ready", "x" * 501) == 422
    put(M, "website_updates_status", "Live")


def test_faculty_can_edit_same_club(world):
    db, who, M = world
    who["id"] = "u-fac"; put(M, "indent", "Ready")


@pytest.mark.parametrize("uid", ["u-hod", "u-dean", "u-admin"])
def test_view_only_roles(world, uid):
    db, who, M = world
    who["id"] = uid
    out = M.get_event_checklist("e1", "tok")
    assert out["can_edit"] is False
    assert code(put, M) == 403


def test_students_and_other_clubs_blocked(world):
    db, who, M = world
    who["id"] = "u-stud"; assert code(M.get_event_checklist, "e1", "tok") == 403
    who["id"] = "u-other"; assert code(M.get_event_checklist, "e1", "tok") == 403
    assert code(put, M) == 403


def test_only_after_publication(world):
    db, who, M = world
    assert code(M.get_event_checklist, "e2", "tok") == 409
    assert code(put, M, event="e2") == 409
    db.events.docs[1]["approval_status"] = "published"
    M.get_event_checklist("e2", "tok")


def test_hod_other_department_blocked(world):
    db, who, M = world
    db.events.docs[0]["department"] = "MECH"
    who["id"] = "u-hod"
    assert code(M.get_event_checklist, "e1", "tok") == 403


def test_accessible_after_event_date(world):
    db, who, M = world
    db.events.docs[0]["date"] = "2020-01-01"
    put(M, "post_event_report", "In Progress")
    assert M.get_event_checklist("e1", "tok")["can_edit"] is True