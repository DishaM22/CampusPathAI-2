"""Tests for promotion, history, corrections and eligibility recalculation.
Run from backend/:  python -m unittest tests.test_academic_progression
They use a tiny in-memory fake database, so no MongoDB / FastAPI / login is needed."""
import os, sys, types, unittest, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---- stubs for packages that may not be installed where tests run -------------
class ObjectId(str):
    def __new__(cls, value=None):
        v = str(value) if value is not None else ("%024x" % next(_counter))
        if len(v) != 24 or any(c not in "0123456789abcdef" for c in v.lower()):
            raise ValueError("bad id")
        return str.__new__(cls, v)
_counter = itertools.count(1)
class DuplicateKeyError(Exception): pass
class HTTPException(Exception):
    def __init__(self, status_code, detail=""): self.status_code, self.detail = status_code, detail
sys.modules.setdefault("bson", types.SimpleNamespace(ObjectId=ObjectId))
_pm = types.ModuleType("pymongo"); _pe = types.ModuleType("pymongo.errors"); _pe.DuplicateKeyError = DuplicateKeyError
sys.modules["pymongo"], sys.modules["pymongo.errors"] = _pm, _pe
sys.modules.setdefault("fastapi", types.SimpleNamespace(HTTPException=HTTPException))
ObjectId = sys.modules["bson"].ObjectId

import academic_progression as ap
from event_audience import student_snapshot

# ---- minimal fake MongoDB ----------------------------------------------------------
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
        if isinstance(v, dict) and any(op.startswith("$") for op in v):
            for op, arg in v.items():
                if op == "$exists":
                    if (val is not MISSING) != arg: return False
                elif op == "$in":
                    present = [val] if not isinstance(val, list) else val
                    hit = any((x is None and val in (MISSING, None)) or (val is not MISSING and (x in present)) for x in arg)
                    if not hit: return False
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
            if any(x.get(u) == d.get(u) for x in self.docs): raise DuplicateKeyError(u)
        self.docs.append(d); return Res(1, d["_id"])
    def insert_many(self, ds, ordered=True):
        for d in ds: self.insert_one(d)
    def find(self, flt=None, proj=None): return Cursor([d for d in self.docs if match(d, flt or {})])
    def find_one(self, flt=None): 
        r = self.find(flt); return r[0] if r else None
    def update_one(self, flt, upd):
        d = self.find_one(flt)
        if not d: return Res(0)
        for k, v in upd.get("$set", {}).items(): d[k] = v
        for k, v in upd.get("$inc", {}).items(): d[k] = (d.get(k) or 0) + v
        for k in upd.get("$unset", {}): d.pop(k, None)
        return Res(1)
    def count_documents(self, flt): return len(self.find(flt))
    def create_index(self, *a, **k): pass
class FakeDB:
    UNIQUE = {"promotion_batches": ("request_id",)}
    def __init__(self): self.c = {}
    def __getitem__(self, n): return self.c.setdefault(n, Coll(self.UNIQUE.get(n, ())))
    def __getattr__(self, n): return self[n]

def hid(n): return "%024x" % n
ADMIN = {"_id": ObjectId(hid(900)), "name": "Admin", "role": "admin"}

def make():
    db = FakeDB()
    cls = lambda n, dept, ay, y, s, sec: db.classes.insert_one(dict(_id=ObjectId(hid(n)), name=f"{dept} Y{y} S{s} {sec}", department=dept, academic_year=ay, year_of_study=y, semester=s, section=sec, is_active=True))
    cls(10, "CSE", "2026-2027", 2, 4, "A"); cls(11, "CSE", "2027-2028", 3, 5, "A"); cls(12, "EEE", "2027-2028", 3, 5, "A")
    def stu(n, dept="CSE", cid=hid(10), **kw):
        d = dict(_id=ObjectId(hid(n)), name=f"S{n}", email=f"s{n}@mail.com", role="student", department=dept, class_id=cid); d.update(kw)
        db.students.insert_one(d)
    for n in (1, 2, 3, 4): stu(n)
    stu(5, "EEE", hid(10)); stu(6, cid="")        # 6 has no class
    for n, y in ((1, "4th Year"), (2, "Year 2"), (3, "2"), (4, "")):
        db.profiles.insert_one(dict(student_id=hid(n), year=y, interests=["AI"]))
    ev = lambda n, aud: db.events.insert_one(dict(_id=ObjectId(hid(n)), title=f"E{n}", approval_status="published", audience=aud))
    A = lambda t, **k: {"type": t, "departments": [], "class_ids": [], "student_ids": [], "version": 1, **k}
    ev(100, A("classes", class_ids=[hid(10)])); ev(101, A("classes", class_ids=[hid(11)]))
    ev(102, A("all")); ev(103, A("departments", departments=["CSE"])); ev(104, A("students", student_ids=[hid(1)]))
    return db, ap.ProgressionService(db)

T5 = {"academic_year": "2027-2028", "year_of_study": 3, "semester": 5}
def ids(*ns): return [hid(n) for n in ns]
def log(db): return db["event_eligibility_log"].docs
def stud(db, n): return db.students.find_one({"_id": ObjectId(hid(n))})

class Target(unittest.TestCase):
    def test_ok(self): self.assertEqual(ap.validate_target(T5)[1], [])
    def test_bad(self):
        for t in ({}, None, {**T5, "academic_year": "2027-2029"}, {**T5, "semester": 9}, {**T5, "year_of_study": 2},
                  {**T5, "academic_year": "x"}, {**T5, "semester": True}):
            self.assertTrue(ap.validate_target(t)[1], t)

class Promotion(unittest.TestCase):
    def test_promote_all_matching_with_class(self):
        db, s = make()
        r = s.apply_promotion(ADMIN, "req-0000-0001", ids(1, 2, 3, 4), T5, hid(11))
        self.assertEqual((r["promoted"], r["failed"]), (4, 0))
        for n in (1, 2, 3, 4):
            st = stud(db, n); self.assertEqual(st["academic"]["semester"], 5); self.assertEqual(st["class_id"], hid(11)); self.assertEqual(st["academic_version"], 1)
        self.assertEqual(len(db.academic_history.docs), 4)
        h = db.academic_history.docs[0]; self.assertEqual((h["previous"]["semester"], h["new"]["semester"], h["admin_id"]), (4, 5, str(ADMIN["_id"])))
        self.assertEqual(h["previous"]["class_id"], hid(10)); self.assertEqual(h["new"]["class_id"], hid(11)); self.assertTrue(h["batch_id"])

    def test_eligibility_recalculated(self):
        db, s = make()
        r = s.apply_promotion(ADMIN, "req-0000-0002", ids(1, 2), T5, hid(11))
        by_event = {}
        for row in log(db): by_event.setdefault(row["event_id"], []).append(row)
        self.assertEqual({x["new_eligible"] for x in by_event[hid(100)]}, {False})   # lost old-class event
        self.assertEqual({x["new_eligible"] for x in by_event[hid(101)]}, {True})    # gained new-class event
        for e in (102, 103, 104): self.assertNotIn(hid(e), by_event)                  # all / dept / selected students unaffected
        self.assertEqual((r["eligibility_gained"], r["eligibility_lost"]), (2, 2))
        self.assertTrue(all(x["reason"] == "promotion" for x in log(db)))

    def test_selected_only_and_exclusions(self):
        db, s = make()
        s.apply_promotion(ADMIN, "req-0000-0003", ids(1, 2), T5, hid(11))   # 3 and 4 excluded (backlogs)
        self.assertEqual(stud(db, 1)["academic"]["semester"], 5)
        for n in (3, 4): self.assertNotIn("academic", stud(db, n)); self.assertEqual(stud(db, n)["class_id"], hid(10))

    def test_duplicate_request_and_repeat_promotion(self):
        db, s = make()
        s.apply_promotion(ADMIN, "req-0000-0004", ids(1, 2), T5, hid(11))
        again = s.apply_promotion(ADMIN, "req-0000-0004", ids(1, 2), T5, hid(11))   # same click twice
        self.assertTrue(again["duplicate"]); self.assertEqual(stud(db, 1)["academic_version"], 1); self.assertEqual(len(db.promotion_batches.docs), 1)
        other = s.apply_promotion(ADMIN, "req-0000-0005", ids(1, 2), T5, hid(11))   # new id, same students
        self.assertEqual((other["promoted"], other["failed"]), (0, 2)); self.assertIn("Already in semester 5", other["failures"][0]["reason"])
        self.assertEqual(stud(db, 1)["academic_version"], 1); self.assertEqual(len(db.academic_history.docs), 2)

    def test_without_class_keeps_class_and_timetable_link(self):
        db, s = make()
        r = s.apply_promotion(ADMIN, "req-0000-0006", ids(1), T5, None)
        self.assertEqual(r["promoted"], 1); st = stud(db, 1)
        self.assertEqual(st["class_id"], hid(10)); self.assertNotIn("class_assigned_at", st)   # timetable untouched
        self.assertEqual(log(db), [])                                                           # class unchanged -> no eligibility change

    def test_failures_reported(self):
        db, s = make()
        r = s.apply_promotion(ADMIN, "req-0000-0007", ids(5, 6, 1), T5, hid(11))
        reasons = {f["student_id"]: f["reason"] for f in r["failures"]}
        self.assertIn("another department", reasons[hid(5)]); self.assertIn("No academic record", reasons[hid(6)])
        self.assertEqual(r["promoted"], 1)
        r2 = s.apply_promotion(ADMIN, "req-0000-0008", [hid(777)], T5, None); self.assertEqual(r2["failures"][0]["reason"], "Student not found.")

    def test_expected_semester_guard(self):
        db, s = make()
        r = s.apply_promotion(ADMIN, "req-0000-0009", ids(1), T5, None, expected_semester=3)
        self.assertEqual(r["promoted"], 0); self.assertIn("changed since", r["failures"][0]["reason"])

    def test_target_class_must_match_target(self):
        db, s = make()
        with self.assertRaises(ap.ProgressionError): s.apply_promotion(ADMIN, "req-0000-0010", ids(1), {**T5, "semester": 6}, hid(11))

    def test_concurrent_edit_is_rejected(self):
        db, s = make()
        class Stale(ap.ProgressionService):
            def _student(self, sid):
                doc = dict(super()._student(sid) or {}); db.students.find_one({"_id": ObjectId(sid)})["academic_version"] = 7
                return doc                                      # stale copy, DB moved on
        r = Stale(db).apply_promotion(ADMIN, "req-0000-0011", ids(1), T5, None)
        self.assertEqual(r["promoted"], 0); self.assertIn("someone else", r["failures"][0]["reason"])

    def test_preview_changes_nothing(self):
        db, s = make()
        p = s.preview(ids(1, 2, 6), T5, hid(11))
        self.assertEqual((p["will_promote"], p["will_skip"]), (2, 1)); self.assertEqual(db.academic_history.docs, []); self.assertNotIn("academic", stud(db, 1))

    def test_batch_row_and_request_id_required(self):
        db, s = make()
        r = s.apply_promotion(ADMIN, "req-0000-0012", ids(1, 2, 6), T5, hid(11))
        b = db.promotion_batches.docs[0]; self.assertEqual((b["status"], b["success_count"], b["failed_count"], b["selected_count"]), ("completed", 2, 1, 3))
        with self.assertRaises(ap.ProgressionError): s.apply_promotion(ADMIN, "x", ids(1), T5, None)
        with self.assertRaises(ap.ProgressionError): s.apply_promotion(ADMIN, "req-0000-0013", [], T5, None)

class Correction(unittest.TestCase):
    def promoted(self):
        db, s = make(); s.apply_promotion(ADMIN, "req-0000-0100", ids(1), T5, hid(11))
        return db, s, str(db.academic_history.docs[0]["_id"])
    def test_reverse_promotion(self):
        db, s, h = self.promoted()
        r = s.correct(ADMIN, h, None, None, "Promoted by mistake, has backlogs")
        st = stud(db, 1); self.assertEqual(st["academic"]["semester"], 4); self.assertEqual(st["class_id"], hid(10)); self.assertEqual(st["academic_version"], 2)
        rows = db.academic_history.docs; self.assertEqual(len(rows), 2)                  # history kept, nothing deleted
        c = rows[1]; self.assertEqual((c["kind"], c["corrects"], c["reason"]), ("correction", h, "Promoted by mistake, has backlogs"))
        self.assertEqual(c["original_promotion"]["new"]["semester"], 5); self.assertEqual(rows[0]["corrected_by"], r["correction_id"])
        self.assertTrue(any(x["reason"] == "promotion_correction" and x["new_eligible"] for x in log(db)))   # regained old-class event
    def test_custom_correction(self):
        db, s, h = self.promoted()
        s.correct(ADMIN, h, {"academic_year": "2026-2027", "year_of_study": 2, "semester": 3}, hid(10), "Wrong semester")
        self.assertEqual(stud(db, 1)["academic"]["semester"], 3)
    def test_cannot_correct_twice_or_without_reason(self):
        db, s, h = self.promoted()
        with self.assertRaises(ap.ProgressionError): s.correct(ADMIN, h, None, None, "no")
        s.correct(ADMIN, h, None, None, "first correction")
        with self.assertRaises(ap.ProgressionError) as e: s.correct(ADMIN, h, None, None, "second correction")
        self.assertEqual(e.exception.status, 409)
    def test_student_moved_on(self):
        db, s, h = self.promoted()
        stud(db, 1)["academic"]["semester"] = 6
        with self.assertRaises(ap.ProgressionError) as e: s.correct(ADMIN, h, None, None, "late correction")
        self.assertEqual(e.exception.status, 409)
    def test_unknown_record(self):
        db, s, h = self.promoted()
        with self.assertRaises(ap.ProgressionError): s.correct(ADMIN, hid(999), None, None, "does not exist")

class DynamicEligibility(unittest.TestCase):
    def test_admin_class_change(self):
        db, s = make()
        r = s.admin_update_student(ADMIN, hid(1), hid(11), None, "Moved to new section")
        self.assertEqual((r["eligibility_gained"], r["eligibility_lost"]), (1, 1))
        st = stud(db, 1); self.assertEqual((st["class_id"], st["academic"]["semester"]), (hid(11), 5))
        self.assertEqual(db.academic_history.docs[0]["kind"], "academic_change")
    def test_admin_department_change_moves_dept_events(self):
        db, s = make()
        r = s.admin_update_student(ADMIN, hid(2), None, "Electrical and Electronics", "Branch change approved")
        self.assertEqual(stud(db, 2)["department"], "EEE")
        self.assertTrue(any(x["event_id"] == hid(103) and not x["new_eligible"] for x in log(db)))   # lost CSE-department event
        self.assertFalse(any(x["event_id"] == hid(104) for x in log(db)))                            # explicit student list unaffected
    def test_validation(self):
        db, s = make()
        for args in ((None, None, "valid reason"), (hid(11), None, "x"), (None, "Law", "valid reason"), (hid(999), None, "valid reason")):
            with self.assertRaises(ap.ProgressionError): s.admin_update_student(ADMIN, hid(1), *args)
    def test_explicit_student_survives(self):
        db, s = make()
        s.admin_update_student(ADMIN, hid(1), hid(12), "EEE", "Moved class and branch")
        self.assertFalse(any(x["event_id"] == hid(104) for x in log(db)))
    def test_all_students_event_unaffected(self):
        db, s = make(); s.apply_promotion(ADMIN, "req-0000-0200", ids(1, 2, 3, 4), T5, hid(11))
        self.assertFalse(any(x["event_id"] == hid(102) for x in log(db)))

class ClassLock(unittest.TestCase):
    def test_lock(self):
        db, s = make()
        with self.assertRaises(HTTPException) as e: ap.guard_student_class_change(db, stud(db, 1))
        self.assertEqual(e.exception.status_code, 403)
        ap.guard_student_class_change(db, stud(db, 6))                        # no class yet: allowed
        db.classes.find_one({"_id": ObjectId(hid(10))})["is_active"] = False
        ap.guard_student_class_change(db, stud(db, 1))                        # class deactivated: may re-choose
        stud(db, 1)["class_id"] = hid(999); ap.guard_student_class_change(db, stud(db, 1))   # class missing: may re-choose
    def test_student_choice_recorded(self):
        db, s = make(); before = student_snapshot(stud(db, 6))
        stud(db, 6)["class_id"] = hid(10)
        ap.record_student_class_change(db, hid(6), before, "student_selected_class")
        self.assertEqual(db.academic_history.docs[0]["kind"], "class_change")
        self.assertTrue(any(x["event_id"] == hid(100) and x["new_eligible"] for x in log(db)))

class Listing(unittest.TestCase):
    def test_filters(self):
        db, s = make()
        self.assertEqual(s.list_students(semester=4)["total_matching"], 5)                 # S1-S5 (S6 has no class)
        self.assertEqual(s.list_students(department="CSE", semester=4)["total_matching"], 4)
        self.assertEqual(s.list_students(class_id=hid(10), q="s2")["total_matching"], 1)
        self.assertEqual(s.list_students(semester=5)["total_matching"], 0)
        s.apply_promotion(ADMIN, "req-0000-0300", ids(1), T5, None)
        self.assertEqual(s.list_students(semester=5)["students"][0]["name"], "S1")        # reads the student's own record
        self.assertTrue(all("password" not in x for x in s.list_students()["students"]))
        self.assertTrue(s.list_students()["students"][0]["email_masked"].endswith("@mail.com"))
    def test_effective(self):
        cls = {"semester": 4, "academic_year": "2026-2027", "year_of_study": 2}
        self.assertEqual(ap.effective_academic({}, cls)["semester"], 4)
        self.assertEqual(ap.effective_academic({"academic": {"semester": 5}}, cls)["semester"], 5)
        self.assertIsNone(ap.effective_academic({}, None))

def profile(db, n): return db.profiles.find_one({"student_id": hid(n)})

class ProfileYear(unittest.TestCase):
    def test_label_styles(self):
        self.assertEqual(ap.year_label(3, "4th Year"), "3rd Year"); self.assertEqual(ap.year_label(1, "2nd year"), "1st year")
        self.assertEqual(ap.year_label(3, "Year 4"), "Year 3"); self.assertEqual(ap.year_label(2, "4"), "2")
        self.assertEqual(ap.year_label(4, ""), "4th Year"); self.assertEqual(ap.year_label(2, "Third Year"), "2nd Year")
    def test_promotion_fixes_profile_year(self):          # the reported bug
        db, s = make()
        self.assertEqual(profile(db, 1)["year"], "4th Year")
        s.apply_promotion(ADMIN, "req-0000-0400", ids(1, 2, 3, 4), T5, hid(11))
        self.assertEqual(profile(db, 1)["year"], "3rd Year")    # same style as before, correct number
        self.assertEqual(profile(db, 2)["year"], "Year 3"); self.assertEqual(profile(db, 3)["year"], "3")
        self.assertEqual(profile(db, 4)["year"], "3rd Year")
        self.assertEqual(profile(db, 1)["interests"], ["AI"])    # nothing else in the profile touched
    def test_no_profile_is_not_created(self):
        db, s = make(); s.apply_promotion(ADMIN, "req-0000-0401", ids(6, 1), T5, None)
        self.assertIsNone(db.profiles.find_one({"student_id": hid(6)}))
        self.assertEqual(len(db.profiles.docs), 4)
    def test_correction_restores_profile_year(self):
        db, s = make(); s.apply_promotion(ADMIN, "req-0000-0402", ids(2), T5, None)
        self.assertEqual(profile(db, 2)["year"], "Year 3")
        s.correct(ADMIN, str(db.academic_history.docs[0]["_id"]), None, None, "Promoted by mistake")
        self.assertEqual(profile(db, 2)["year"], "Year 2")
    def test_admin_change_syncs_year(self):
        db, s = make(); s.admin_update_student(ADMIN, hid(2), hid(11), None, "Moved to year 3 class")
        self.assertEqual(profile(db, 2)["year"], "Year 3")
    def test_profile_save_cannot_override_official_year(self):
        db, s = make(); s.apply_promotion(ADMIN, "req-0000-0403", ids(1), T5, None)
        self.assertEqual(ap.official_profile_year(db, hid(1), "4th Year"), "3rd Year")     # student tries to claim 4th
        self.assertEqual(ap.official_profile_year(db, hid(1), "Year 4"), "Year 3")
        self.assertEqual(ap.official_profile_year(db, hid(6), "2nd Year"), "2nd Year")       # no class -> keeps what they typed
        self.assertEqual(ap.official_profile_year(db, hid(999), "x"), "x")                    # unknown student -> unchanged
    def test_bulk_sync_dry_run_then_apply(self):
        db, s = make(); s.apply_promotion(ADMIN, "req-0000-0404", ids(1), T5, None)
        profile(db, 1)["year"] = "4th Year"                                 # simulate the old bad value
        dry = s.sync_profile_years(dry_run=True)
        self.assertEqual((dry["to_change"], dry["changed"]), (1, 0)); self.assertEqual(profile(db, 1)["year"], "4th Year")
        done = s.sync_profile_years(dry_run=False)
        self.assertEqual(done["changed"], 1); self.assertEqual(profile(db, 1)["year"], "3rd Year")
        self.assertEqual(profile(db, 2)["year"], "Year 2")                  # never promoted: untouched by default
        wide = s.sync_profile_years(dry_run=True, include_class_derived=True)
        self.assertEqual((wide["to_change"], wide["sample"][0]["name"]), (1, "S4"))   # only S4's EMPTY year would be filled; S2/S3 match
        self.assertEqual(s.sync_profile_years(dry_run=False)["changed"], 0) # default mode is idempotent
    def test_class_derived_mismatch_found_when_asked(self):
        db, s = make(); self.assertEqual(s.sync_profile_years(True, True)["to_change"], 2)    # S1 "4th" and S4 "" vs class year 2

if __name__ == "__main__": unittest.main(verbosity=1)