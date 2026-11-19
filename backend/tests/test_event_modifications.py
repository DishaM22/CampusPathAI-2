"""Tests for published-event modifications, re-approval, versioning, notifications.
Run from backend/:  python -m unittest tests.test_event_modifications
Uses a small in-memory fake database: no MongoDB / FastAPI / login needed."""
import os, sys, types, unittest, itertools, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._stubs import ObjectId, DuplicateKeyError, HTTPException

import event_modifications as em
import event_audience as ea

MISSING = object()
def getp(doc, path):
    cur = doc
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur: return MISSING
        cur = cur[p]
    return cur
def match(doc, flt):
    for k, v in flt.items():
        if k == "$or":
            if not any(match(doc, f) for f in v): return False
            continue
        val = getp(doc, k)
        if isinstance(v, dict) and any(o.startswith("$") for o in v):
            for op, arg in v.items():
                if op == "$exists":
                    if (val is not MISSING) != arg: return False
                elif op == "$in":
                    pres = val if isinstance(val, list) else [val]
                    if not any((x is None and val in (MISSING, None)) or (val is not MISSING and x in pres) for x in arg): return False
                elif op == "$ne":
                    if val is not MISSING and val == arg: return False
                else: raise NotImplementedError(op)
        elif v is None:
            if val not in (MISSING, None): return False
        else:
            if val is MISSING: return False
            if isinstance(val, list):
                if v not in val: return False
            elif val != v: return False
    return True
class Res:
    def __init__(self, m=0, i=None): self.modified_count = self.matched_count = m; self.inserted_id = i
class Cursor(list):
    def sort(self, key, d=1): super().sort(key=lambda r: (r.get(key) is None, r.get(key)), reverse=(d == -1)); return self
    def limit(self, n): return Cursor(self[:n])
class Coll:
    def __init__(self, unique=()): self.docs, self.unique = [], unique
    def insert_one(self, d):
        d = dict(d); d.setdefault("_id", ObjectId())
        for u in self.unique:
            if d.get(u) is not None and any(x.get(u) == d.get(u) for x in self.docs): raise DuplicateKeyError(u)
        self.docs.append(d); return Res(1, d["_id"])
    def insert_many(self, ds, ordered=True):
        for d in ds:
            try: self.insert_one(d)
            except DuplicateKeyError:
                if ordered: raise
    def find(self, flt=None, proj=None): return Cursor([copy.deepcopy(d) for d in self.docs if match(d, flt or {})])   # like MongoDB: copies
    def find_one(self, flt=None):
        r = self.find(flt); return r[0] if r else None
    def update_one(self, flt, upd):
        d = next((x for x in self.docs if match(x, flt)), None)       # the STORED document
        if not d: return Res(0)
        for k, v in upd.get("$set", {}).items(): d[k] = v
        for k, v in upd.get("$inc", {}).items(): d[k] = (d.get(k) or 0) + v
        for k in upd.get("$unset", {}): d.pop(k, None)
        for k, v in upd.get("$push", {}).items(): d.setdefault(k, []).append(v)
        for k in upd.get("$pop", {}): d.get(k, []).pop()
        return Res(1)
    def count_documents(self, flt): return len(self.find(flt))
    def create_index(self, *a, **k): pass
class FakeDB:
    UNIQUE = {"event_modifications": ("open_key",), "event_versions": ("version_key",)}
    def __init__(self): self.c = {}
    def __getitem__(self, n): return self.c.setdefault(n, Coll(self.UNIQUE.get(n, ())))
    def __getattr__(self, n): return self[n]

def hid(n): return "%024x" % n
CLUB = "ai club::pu"
def fake_validate(merged, user, role):
    if not str(merged.get("title") or "").strip(): raise HTTPException(422, [{"loc": ["body", "title"], "msg": "Title required"}])
    if "date" in merged and len(str(merged["date"])) != 10: raise HTTPException(422, [{"loc": ["body", "date"], "msg": "Bad date"}])
    keys = ("title","description","category","date","time","location","event_mode","school_name","department","capacity","skills",
            "poster_url","spoc_name","spoc_designation","spoc_email","spoc_contact","resource_persons","budget_breakup","budget")
    return {k: merged[k] for k in keys if k in merged}

def make():
    db = FakeDB(); notes = []
    def user(n, role, **kw):
        d = dict(_id=ObjectId(hid(n)), name=f"{role}{n}", role=role, status="active", email=f"{role}{n}@x.com"); d.update(kw)
        db.students.insert_one(d); return db.students.find_one({"_id": ObjectId(hid(n))})
    admin = user(1, "admin"); club = user(2, "club", club_id=CLUB); fac = user(3, "faculty", club_id=CLUB)
    hod = user(4, "hod", department="CSE"); hod2 = user(5, "hod", department="EEE"); dean = user(6, "dean")
    other_fac = user(7, "faculty", club_id="other::pu")
    stu = {n: user(n, "student", department=("EEE" if n == 15 else "CSE"), class_id=hid(900)) for n in (11, 12, 13, 14, 15)}
    db.events.insert_one(dict(_id=ObjectId(hid(100)), title="AI Workshop", description="d", category="Technical", organizer="AI Club",
        date="2026-10-10", time="10:00 AM", location="Seminar Hall 1", event_mode="Offline", department="CSE", capacity=100,
        skills=["AI"], approval_status="published", club_id=CLUB, created_by=str(club["_id"]), submission_version=1,
        approval_history=[{"reviewer_role": "admin", "decision": "approved", "reviewed_at": "2026-09-01T00:00:00Z"}],
        resource_persons=[{"name": "Person A"}], audience={"type": "departments", "departments": ["CSE"], "class_ids": [], "student_ids": [], "version": 1}))
    for n, st in ((11, "registered"), (12, "registered"), (13, "cancelled"), (15, "registered")):
        db.event_registrations.insert_one(dict(event_id=hid(100), student_id=hid(n), status=st))
    helpers = dict(
        validate=fake_validate,
        resubmission_status=lambda role, u: "pending_faculty" if role == "club" else "pending_hod",
        belongs_to_club=lambda e, u: bool(e.get("club_id")) and e.get("club_id") == u.get("club_id"),
        hod_block_reason=lambda h, e: None if h.get("department") == e.get("department") else "Wrong department",
        user_club_id=lambda u: u.get("club_id", ""),
        notify_faculty=lambda *a, **k: notes.append(a))
    return db, em.ModificationService(db, helpers), types.SimpleNamespace(admin=admin, club=club, fac=fac, hod=hod, hod2=hod2, dean=dean, other_fac=other_fac, stu=stu, notes=notes)

def live(db): return db.events.find_one({"_id": ObjectId(hid(100))})
def stored(db): return db.events.docs[0]
R = lambda db, s, w, **k: s.create_request(w.club, hid(100), ["date", "location"], {"date": "2026-10-20", "location": "Auditorium"}, "Venue is under repair, moved a week")
def to_editing(db, s, w):
    mid = R(db, s, w)["modification_id"]; s.initial_review(w.admin, mid, "approved", "ok"); return mid
def to_review(db, s, w, user=None):
    mid = to_editing(db, s, w); s.submit(user or w.club, mid); return mid
def go(db, s, w, mid, role_user, role, decision="approved", remarks="", payload=None):
    return s.review(ObjectId(mid), str(role_user["_id"]), role_user, role, decision, remarks, payload or {})
def fully_approve(db, s, w, mid, share=False):
    go(db, s, w, mid, w.fac, "faculty"); go(db, s, w, mid, w.hod, "hod"); go(db, s, w, mid, w.dean, "dean")
    return go(db, s, w, mid, w.admin, "admin", payload={"share_reason_with_students": share})
def raises(testcase, status, fn, *a, **k):
    with testcase.assertRaises((em.ModError, HTTPException)) as e: fn(*a, **k)
    got = getattr(e.exception, "status", getattr(e.exception, "status_code", None))
    testcase.assertEqual(got, status); return e.exception

class Request(unittest.TestCase):
    def test_create_keeps_live_event_untouched(self):
        db, s, w = make(); before = dict(live(db)); r = R(db, s, w)
        self.assertEqual(r["state"], "pending_admin_review"); self.assertEqual(live(db), before)
        m = db.event_modifications.docs[0]; self.assertEqual((m["status"], m["fields"], m["base_version"]), ("pending_admin_review", ["date", "location"], 1))
        self.assertEqual(m["original_values"], {"date": "2026-10-10", "location": "Seminar Hall 1"}); self.assertEqual(m["proposed"]["date"], "2026-10-20")
        self.assertTrue(any(n.get("recipient_role") == "admin" for n in db.notifications.docs))
    def test_rules(self):
        db, s, w = make(); mk = lambda user=w.club, fields=["date"], prop={"date": "2026-10-20"}, reason="a long enough reason": s.create_request(user, hid(100), fields, prop, reason)
        raises(self, 403, mk, user=w.stu[11]); raises(self, 403, mk, user=w.admin); raises(self, 403, mk, user=w.other_fac)
        raises(self, 422, mk, fields=[]); raises(self, 422, mk, fields=["audience"]); raises(self, 422, mk, fields=["approval_status"])
        raises(self, 422, mk, reason="short"); raises(self, 422, mk, prop={"date": "2026-10-10"})       # same value
        raises(self, 422, mk, prop={"date": "2026-10-20", "title": "sneaky"})                          # value for an unselected field
        raises(self, 422, mk, prop={"date": "bad"})                                                     # validation reused
        raises(self, 400, s.create_request, w.club, "zzz", ["date"], {"date": "2026-10-20"}, "a long enough reason")
        stored(db)["approval_status"] = "pending_hod"; raises(self, 409, mk)
    def test_one_open_request_per_event(self):
        db, s, w = make(); R(db, s, w); exc = raises(self, 409, R, db, s, w); self.assertIn("open", exc.message)
    def test_legacy_event_without_status_is_allowed(self):
        db, s, w = make(); del db.events.docs[0]["approval_status"]; self.assertEqual(R(db, s, w)["state"], "pending_admin_review")

class InitialReview(unittest.TestCase):
    def test_reject_keeps_event_and_frees_event(self):
        db, s, w = make(); mid = R(db, s, w)["modification_id"]; before = dict(live(db))
        raises(self, 400, s.initial_review, w.admin, mid, "rejected", "no"); raises(self, 400, s.initial_review, w.admin, mid, "maybe", "")
        s.initial_review(w.admin, mid, "rejected", "Cannot move the venue this late")
        m = db.event_modifications.docs[0]; self.assertEqual((m["status"], m["initial_review"]["remarks"]), ("request_rejected", "Cannot move the venue this late"))
        self.assertNotIn("open_key", m); self.assertEqual(live(db), before)
        self.assertTrue(any(n["recipient"] == str(w.club["_id"]) and "rejected" in n["title"].lower() and n["remarks"] for n in db.notifications.docs))
        R(db, s, w)                                                                     # a new request is possible now
        raises(self, 409, s.initial_review, w.admin, mid, "approved", "")               # cannot be decided twice
    def test_approve_is_permission_only(self):
        db, s, w = make(); mid = R(db, s, w)["modification_id"]; before = dict(live(db))
        s.initial_review(w.admin, mid, "approved", "")
        self.assertEqual(db.event_modifications.docs[0]["status"], "editing"); self.assertEqual(live(db), before)
        self.assertEqual(len(db.event_versions.docs), 0)

class Editing(unittest.TestCase):
    def test_edit_rules(self):
        db, s, w = make(); mid = R(db, s, w)["modification_id"]
        raises(self, 409, s.update_draft, w.club, mid, {"date": "2026-11-01"})          # before Admin approval
        s.initial_review(w.admin, mid, "approved", "")
        r = s.update_draft(w.club, mid, {"date": "2026-11-01"}); self.assertEqual(r["proposed"]["date"], "2026-11-01"); self.assertEqual(r["proposed"]["location"], "Auditorium")
        raises(self, 403, s.update_draft, w.other_fac, mid, {"date": "2026-11-02"}); raises(self, 403, s.update_draft, w.admin, mid, {"date": "2026-11-02"})
        raises(self, 422, s.update_draft, w.club, mid, {"title": "New"})                # only approved fields
        raises(self, 422, s.update_draft, w.club, mid, {"date": "bad"})
        self.assertEqual(live(db)["date"], "2026-10-10"); self.assertEqual(len(db.event_modifications.docs[0]["edit_history"]), 1)
    def test_submit_routing(self):
        db, s, w = make(); mid = to_editing(db, s, w); r = s.submit(w.club, mid); self.assertEqual(r["state"], "pending_faculty")
        raises(self, 409, s.submit, w.club, mid); raises(self, 409, s.update_draft, w.club, mid, {"date": "2026-11-01"})   # locked while in review
        db2, s2, w2 = make(); m2 = to_editing(db2, s2, w2); self.assertEqual(s2.submit(w2.fac, m2)["state"], "pending_hod")
    def test_submit_needs_real_change_and_fresh_event(self):
        db, s, w = make(); mid = to_editing(db, s, w)
        db.events.docs[0]["live_version"] = 4; raises(self, 409, s.submit, w.club, mid)
    def test_withdraw(self):
        db, s, w = make(); mid = R(db, s, w)["modification_id"]; s.withdraw(w.club, mid)
        self.assertEqual(db.event_modifications.docs[0]["status"], "cancelled"); R(db, s, w)
        mid2 = [str(m["_id"]) for m in db.event_modifications.docs][1]; s.initial_review(w.admin, mid2, "approved", ""); s.submit(w.club, mid2)
        raises(self, 409, s.withdraw, w.club, mid2)

class Chain(unittest.TestCase):
    def test_original_stays_visible_until_final_approval(self):
        db, s, w = make(); mid = to_review(db, s, w)
        for role_user, role in ((w.fac, "faculty"), (w.hod, "hod"), (w.dean, "dean")):
            ev = live(db); self.assertEqual((ev["date"], ev["location"], ev["approval_status"], ev.get("live_version")), ("2026-10-10", "Seminar Hall 1", "published", None))
            go(db, s, w, mid, role_user, role)
        ev = live(db); self.assertEqual((ev["date"], ev["location"], ev["approval_status"]), ("2026-10-10", "Seminar Hall 1", "published"))    # still old at pending_admin
        self.assertEqual(db.event_modifications.docs[0]["status"], "pending_admin")
        go(db, s, w, mid, w.admin, "admin")
        ev = live(db); self.assertEqual((ev["date"], ev["location"], ev["live_version"], ev["approval_status"]), ("2026-10-20", "Auditorium", 2, "published"))
    def test_full_chain_applies_same_event_and_keeps_data(self):
        db, s, w = make(); regs_before = [dict(r) for r in db.event_registrations.docs]; mid = to_review(db, s, w)
        reply = fully_approve(db, s, w, mid)
        self.assertEqual((reply["approval_status"], reply["applied_version"], reply["changed_fields"]), ("published", 2, ["date", "location"]))
        self.assertEqual(len(db.events.docs), 1); self.assertEqual(db.events.docs[0]["_id"], hid(100))      # same event, no duplicate
        ev = live(db); self.assertEqual(ev["audience"]["departments"], ["CSE"]); self.assertEqual(ev["resource_persons"], [{"name": "Person A"}])
        self.assertEqual(ev["approval_history"][0]["reviewer_role"], "admin")                              # old approval history kept
        self.assertEqual(ev["edit_history"][-1]["kind"], "modification_applied")
        self.assertEqual(db.event_registrations.docs, regs_before)                                         # registrations untouched
        m = db.event_modifications.docs[0]; self.assertEqual((m["status"], m["applied_version"]), ("published", 2)); self.assertNotIn("open_key", m)
        v = db.event_versions.docs[0]; self.assertEqual((v["version"], v["changed_fields"]), (2, ["date", "location"]))
        self.assertEqual(v["previous_data"], {"date": "2026-10-10", "location": "Seminar Hall 1"}); self.assertEqual(v["new_data"], {"date": "2026-10-20", "location": "Auditorium"})
        d = v["decisions"]; self.assertEqual([d[k]["decision"] for k in ("initial_admin", "faculty", "hod", "dean", "final_admin")], ["approved"] * 5)
        self.assertEqual(v["requested_by"]["name"], w.club["name"]); self.assertTrue(v["approved_at"] and v["submitted_at"])
        raises(self, 422, R, db, s, w)                                                                      # same values as the NEW live event: refused
        s.create_request(w.club, hid(100), ["capacity"], {"capacity": 60}, "A different change is possible")   # next modification possible
    def test_second_modification_makes_version_three(self):
        db, s, w = make(); fully_approve(db, s, w, to_review(db, s, w))
        mid = s.create_request(w.club, hid(100), ["title"], {"title": "AI Workshop 2"}, "Renaming the workshop")["modification_id"]
        s.initial_review(w.admin, mid, "approved", ""); s.submit(w.club, mid); fully_approve(db, s, w, mid)
        self.assertEqual((live(db)["live_version"], live(db)["title"]), (3, "AI Workshop 2")); self.assertEqual([v["version"] for v in db.event_versions.docs], [2, 3])
        h = s.versions(w.admin, hid(100)); self.assertEqual([v["version"] for v in h["versions"]], [1, 2, 3]); self.assertEqual(h["current_version"], 3); self.assertEqual(h["versions"][0]["kind"], "original")
    def test_rejection_at_every_stage(self):
        for stage in ("faculty", "hod", "dean", "admin"):
            db, s, w = make(); mid = to_review(db, s, w); before = dict(live(db))
            order = [("faculty", w.fac), ("hod", w.hod), ("dean", w.dean), ("admin", w.admin)]
            for role, u in order:
                if role == stage:
                    raises(self, 400 if False else 409, lambda: None) if False else None
                    go(db, s, w, mid, u, role, "rejected", "Not acceptable for this event"); break
                go(db, s, w, mid, u, role)
            m = db.event_modifications.docs[0]; self.assertEqual(m["status"], "rejected", stage); self.assertNotIn("open_key", m)
            self.assertEqual(live(db), before, stage); self.assertEqual(len(db.event_versions.docs), 0)
            self.assertFalse(any(n.get("audience") == "student" for n in db.notifications.docs), stage)
            self.assertTrue(any(n["recipient"] == str(w.club["_id"]) and n["remarks"] == "Not acceptable for this event" for n in db.notifications.docs), stage)
            R(db, s, w)
    def test_changes_requested_then_resubmission_restarts_chain(self):
        db, s, w = make(); mid = to_review(db, s, w); go(db, s, w, mid, w.fac, "faculty")
        go(db, s, w, mid, w.hod, "hod", "changes", "Please also give a reason for the venue")
        m = db.event_modifications.docs[0]; self.assertEqual(m["status"], "changes_requested"); self.assertEqual(live(db)["date"], "2026-10-10")
        s.update_draft(w.club, mid, {"location": "Main Auditorium"})
        r = s.submit(w.club, mid); self.assertEqual((r["state"], r["submission_version"]), ("pending_faculty", 2))
        self.assertEqual(len(db.event_modifications.docs[0]["approval_history"]), 2)                      # history preserved
        fully_approve(db, s, w, mid); self.assertEqual(live(db)["location"], "Main Auditorium")
        self.assertEqual(db.event_versions.docs[0]["decisions"]["hod"]["submission_version"], 2)
    def test_reviewer_permissions(self):
        db, s, w = make(); mid = to_review(db, s, w)
        raises(self, 409, go, db, s, w, mid, w.hod, "hod")                                                  # wrong stage
        raises(self, 403, go, db, s, w, mid, w.other_fac, "faculty")                                        # other club's faculty
        go(db, s, w, mid, w.fac, "faculty"); raises(self, 403, go, db, s, w, mid, w.hod2, "hod")           # other department's HOD
        raises(self, 409, go, db, s, w, mid, w.admin, "admin")                                              # admin before dean
        raises(self, 409, go, db, s, w, mid, w.fac, "faculty")                                              # already past that stage
    def test_apply_conflict_rolls_back(self):
        db, s, w = make(); mid = to_review(db, s, w); go(db, s, w, mid, w.fac, "faculty"); go(db, s, w, mid, w.hod, "hod"); go(db, s, w, mid, w.dean, "dean")
        db.events.docs[0]["live_version"] = 3                                                               # someone changed the event meanwhile
        raises(self, 409, go, db, s, w, mid, w.admin, "admin")
        m = db.event_modifications.docs[0]; self.assertEqual(m["status"], "pending_admin"); self.assertEqual(m["open_key"], hid(100)); self.assertEqual(len(m["approval_history"]), 3)
        self.assertEqual(live(db)["date"], "2026-10-10"); self.assertEqual(db.event_versions.docs, [])
    def test_double_final_approval_applies_once(self):
        db, s, w = make(); mid = to_review(db, s, w); fully_approve(db, s, w, mid)
        raises(self, 409, go, db, s, w, mid, w.admin, "admin"); self.assertEqual(live(db)["live_version"], 2); self.assertEqual(len(db.event_versions.docs), 1)

class StudentNotifications(unittest.TestCase):
    def student_rows(self, db): return [n for n in db.notifications.docs if n.get("audience") == "student"]
    def test_only_registered_and_eligible_students_after_final_approval(self):
        db, s, w = make(); mid = to_review(db, s, w); go(db, s, w, mid, w.fac, "faculty"); go(db, s, w, mid, w.hod, "hod")
        self.assertEqual(self.student_rows(db), [])                                                         # nothing before final approval
        go(db, s, w, mid, w.dean, "dean"); self.assertEqual(self.student_rows(db), [])
        reply = go(db, s, w, mid, w.admin, "admin"); rows = self.student_rows(db)
        self.assertEqual(sorted(r["recipient"] for r in rows), [hid(11), hid(12)])                          # not s13 (cancelled), s14 (not registered), s15 (outside audience)
        self.assertEqual(reply["students_notified"], 2); self.assertEqual(reply["registered_students"], 3)
        msg = rows[0]["message"]
        for text in ("AI Workshop", "2026-10-10", "10:00 AM", "2026-10-20", "Auditorium", "Seminar Hall 1"): self.assertIn(text, msg)
        self.assertNotIn("repair", msg); self.assertIn("rescheduled", rows[0]["title"].lower())            # reason not shared by default
    def test_reason_only_when_admin_allows(self):
        db, s, w = make(); fully_approve(db, s, w, to_review(db, s, w), share=True)
        self.assertIn("Venue is under repair", self.student_rows(db)[0]["message"])
    def test_no_duplicates(self):
        db, s, w = make(); fully_approve(db, s, w, to_review(db, s, w)); n = len(self.student_rows(db))
        m = db.event_modifications.docs[0]; after = live(db)
        self.assertEqual(s._notify_students(m, {**after, "date": "2026-10-10", "location": "Seminar Hall 1"}, after, ["date", "location"], False), 0)
        self.assertEqual(len(self.student_rows(db)), n)
    def test_unimportant_change_sends_nothing(self):
        db, s, w = make()
        mid = s.create_request(w.club, hid(100), ["capacity"], {"capacity": 50}, "Smaller room available now")["modification_id"]
        s.initial_review(w.admin, mid, "approved", ""); s.submit(w.club, mid); r = fully_approve(db, s, w, mid)
        self.assertEqual(self.student_rows(db), []); self.assertEqual(live(db)["capacity"], 50); self.assertEqual(r["students_notified"], 0)
        self.assertEqual(r["capacity_warning"], "")                                                         # 3 registered <= 50
    def test_capacity_below_registrations_warns_but_keeps_registrations(self):
        db, s, w = make()
        mid = s.create_request(w.club, hid(100), ["capacity"], {"capacity": 2}, "Much smaller room available")["modification_id"]
        s.initial_review(w.admin, mid, "approved", ""); s.submit(w.club, mid); r = fully_approve(db, s, w, mid)
        self.assertIn("Nobody was removed", r["capacity_warning"]); self.assertEqual(len([x for x in db.event_registrations.docs if x["status"] == "registered"]), 3)
    def test_student_inbox_is_private(self):
        db, s, w = make(); fully_approve(db, s, w, to_review(db, s, w))
        mine = s.student_notifications(w.stu[11]); self.assertEqual((len(mine["notifications"]), mine["unread"]), (1, 1))
        self.assertEqual(len(s.student_notifications(w.stu[14])["notifications"]), 0)
        nid = mine["notifications"][0]["id"]
        self.assertEqual(s.mark_read(w.stu[14], nid), 0); self.assertEqual(s.mark_read(w.stu[11], nid), 1); self.assertEqual(s.student_notifications(w.stu[11])["unread"], 0)

class ReadModels(unittest.TestCase):
    def test_approval_queue_items(self):
        db, s, w = make(); mid = to_review(db, s, w); js = lambda v: v
        q, r = s.approval_items(str(w.fac["_id"]), w.fac, "faculty", js); self.assertEqual([i["_id"] for i in q], [mid])
        item = q[0]; self.assertTrue(item["title"].startswith("✏️ Modification")); self.assertEqual(item["approval_status"], "pending_faculty")
        self.assertEqual((item["date"], item["location"]), ("2026-10-20", "Auditorium")); self.assertTrue(item["is_modification"])
        self.assertEqual(item["modification"]["original_values"]["date"], "2026-10-10"); self.assertEqual(item["modification"]["reason"], "Venue is under repair, moved a week")
        self.assertEqual(s.approval_items(str(w.hod["_id"]), w.hod, "hod", js)[0], [])                       # not at HOD yet
        self.assertEqual(s.approval_items(str(w.other_fac["_id"]), w.other_fac, "faculty", js)[0], [])       # other club
        go(db, s, w, mid, w.fac, "faculty")
        self.assertEqual(len(s.approval_items(str(w.hod["_id"]), w.hod, "hod", js)[0]), 1); self.assertEqual(s.approval_items(str(w.hod2["_id"]), w.hod2, "hod", js)[0], [])
        q2, r2 = s.approval_items(str(w.fac["_id"]), w.fac, "faculty", js); self.assertEqual((q2, len(r2)), ([], 1))
    def test_detail_access(self):
        db, s, w = make(); mid = R(db, s, w)["modification_id"]
        d = s.detail(w.admin, mid); self.assertEqual([(c["field"], c["original"], c["proposed"]) for c in d["comparison"]], [("date", "2026-10-10", "2026-10-20"), ("location", "Seminar Hall 1", "Auditorium")])
        self.assertEqual(d["registered_students"], 3); self.assertFalse(d["editable"]); s.detail(w.club, mid); s.detail(w.hod, mid)
        for u in (w.stu[11], w.other_fac, w.hod2): raises(self, 403, s.detail, u, mid)
        mine = s.mine(w.club); self.assertEqual(len(mine), 1); self.assertEqual(s.mine(w.other_fac), [])
        self.assertEqual(len(s.admin_list()), 1); self.assertEqual(s.admin_list("rejected"), [])
    def test_versions_access(self):
        db, s, w = make(); raises(self, 403, s.versions, w.other_fac, hid(100)); raises(self, 403, s.versions, w.stu[11], hid(100))
        self.assertEqual(len(s.versions(w.club, hid(100))["versions"]), 1)

class AudienceNotifications(unittest.TestCase):
    def test_newly_eligible_students_notified_once(self):
        db, s, w = make(); svc = ea.AudienceService(db, lambda a: "x")
        r = svc.set_audience(w.admin, hid(100), {"type": "departments", "departments": ["CSE", "EEE"]})
        self.assertEqual((r["newly_eligible"], r["no_longer_eligible"], r["students_notified"]), (1, 0, 1))
        rows = [n for n in db.notifications.docs if n.get("type") == "event_available"]; self.assertEqual([x["recipient"] for x in rows], [hid(15)])
        self.assertIn("AI Workshop", rows[0]["message"])
        r2 = svc.set_audience(w.admin, hid(100), {"type": "departments", "departments": ["EEE"]})              # CSE removed: nobody newly eligible
        self.assertEqual((r2["newly_eligible"], r2["no_longer_eligible"], r2["students_notified"]), (0, 4, 0))

if __name__ == "__main__": unittest.main(verbosity=1)