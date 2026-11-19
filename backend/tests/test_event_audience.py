"""Tests for the pure audience rules. Run:  python -m unittest tests.test_event_audience  (from backend/)
They need no MongoDB, FastAPI or login."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from event_audience import (clean_audience, audience_allows, student_snapshot, viewer_can_view,
                            mongo_filter_for_student, mask_email, describe_audience)

H = lambda n: ("%024x" % n)           # fake 24-hex ids
def stu(i, dept="CSE", cls=None):
    return {"_id": H(i), "role": "student", "department": dept, "class_id": cls or ""}

# --- tiny evaluator for the Mongo filters we build (equality, dotted paths, arrays, $or, $exists)
def get(doc, path):
    cur = doc
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur: return ("MISSING",)
        cur = cur[p]
    return cur
def match(doc, flt):
    for k, v in flt.items():
        if k == "$or":
            if not any(match(doc, f) for f in v): return False
            continue
        val = get(doc, k)
        if isinstance(v, dict) and "$exists" in v:
            if (val != ("MISSING",)) != v["$exists"]: return False
        elif v is None:
            if val not in (None, ("MISSING",)): return False
        else:
            if val == ("MISSING",): return False
            if isinstance(val, list): 
                if v not in val: return False
            elif val != v: return False
    return True

def aud(kind, **kw):
    base = {"type": kind, "departments": [], "class_ids": [], "student_ids": []}
    base.update(kw); return base

class Shape(unittest.TestCase):
    def test_needs_type(self):
        self.assertTrue(clean_audience(None)[1]); self.assertTrue(clean_audience({"type": "x"})[1])
    def test_all_ok(self):
        c, e = clean_audience({"type": "all"}); self.assertEqual(e, []); self.assertEqual(c["type"], "all")
    def test_departments_canonical_and_multi(self):
        c, e = clean_audience({"type": "departments", "departments": ["cs", "AIML", "CSE"]})
        self.assertEqual(e, []); self.assertEqual(c["departments"], ["CSE", "AIML & Robotics"])
    def test_unknown_department_rejected(self):
        self.assertTrue(clean_audience({"type": "departments", "departments": ["Law"]})[1])
    def test_empty_selection_rejected(self):
        for t, k in (("departments","departments"),("classes","class_ids"),("students","student_ids")):
            self.assertTrue(clean_audience({"type": t, k: []})[1], t)
    def test_bad_ids_rejected(self):
        self.assertTrue(clean_audience({"type": "classes", "class_ids": ["abc"]})[1])
        self.assertTrue(clean_audience({"type": "students", "student_ids": ["zz"]})[1])
    def test_multi_classes_students(self):
        self.assertEqual(clean_audience({"type":"classes","class_ids":[H(1),H(2),H(1)]})[0]["class_ids"], [H(1),H(2)])
        self.assertEqual(len(clean_audience({"type":"students","student_ids":[H(1),H(2),H(3)]})[0]["student_ids"]), 3)

class Eligibility(unittest.TestCase):
    def test_all_students(self):
        self.assertTrue(audience_allows(aud("all"), student_snapshot(stu(1, "EEE"))))
    def test_departments_multi(self):
        a = aud("departments", departments=["CSE", "EEE"])
        self.assertTrue(audience_allows(a, student_snapshot(stu(1, "CSE"))))
        self.assertTrue(audience_allows(a, student_snapshot(stu(2, "Electrical and Electronics"))))  # alias
        self.assertFalse(audience_allows(a, student_snapshot(stu(3, "Mechanical"))))
    def test_classes(self):
        a = aud("classes", class_ids=[H(10), H(11)])
        self.assertTrue(audience_allows(a, student_snapshot(stu(1, cls=H(11)))))
        self.assertFalse(audience_allows(a, student_snapshot(stu(2, cls=H(12)))))
        self.assertFalse(audience_allows(a, student_snapshot(stu(3))))            # no class
    def test_selected_students(self):
        a = aud("students", student_ids=[H(1), H(2)])
        self.assertTrue(audience_allows(a, student_snapshot(stu(2))))
        self.assertFalse(audience_allows(a, student_snapshot(stu(3))))
    def test_legacy_event_unchanged(self):
        self.assertTrue(audience_allows(None, student_snapshot(stu(1)))); self.assertTrue(audience_allows(None, None))
    def test_malformed_fails_closed(self):
        for bad in ("all", ["x"], {"type": "weird"}, {"type": ""}):
            self.assertFalse(audience_allows(bad, student_snapshot(stu(1))), bad)
    def test_visitor_only_sees_all_or_legacy(self):
        self.assertTrue(audience_allows(aud("all"), None))
        self.assertFalse(audience_allows(aud("departments", departments=["CSE"]), None))

class DynamicChanges(unittest.TestCase):
    def test_class_change_moves_access(self):
        a = aud("classes", class_ids=[H(10)])
        s = stu(1, cls=H(10)); self.assertTrue(audience_allows(a, student_snapshot(s)))
        s["class_id"] = H(99); self.assertFalse(audience_allows(a, student_snapshot(s)))   # revoked
        s["class_id"] = H(10); self.assertTrue(audience_allows(a, student_snapshot(s)))    # regained
    def test_department_change(self):
        a = aud("departments", departments=["CSE"]); s = stu(1, "CSE")
        self.assertTrue(audience_allows(a, student_snapshot(s)))
        s["department"] = "Mechanical"; self.assertFalse(audience_allows(a, student_snapshot(s)))
    def test_explicit_student_survives_academic_change(self):
        a = aud("students", student_ids=[H(1)]); s = stu(1, "CSE", H(10))
        s["department"], s["class_id"] = "Mechanical", H(77)
        self.assertTrue(audience_allows(a, student_snapshot(s)))
    def test_all_unaffected_by_change(self):
        s = stu(1, "CSE", H(10)); s["department"], s["class_id"] = "EEE", H(5)
        self.assertTrue(audience_allows(aud("all"), student_snapshot(s)))

class Roles(unittest.TestCase):
    ev = {"audience": aud("students", student_ids=[H(1)])}
    def test_staff_see_all(self):
        for r in ("admin", "hod", "dean", "faculty", "club"):
            self.assertTrue(viewer_can_view(self.ev, {"role": r}), r)
    def test_student_outside_blocked(self): self.assertFalse(viewer_can_view(self.ev, stu(2)))
    def test_student_inside_allowed(self): self.assertTrue(viewer_can_view(self.ev, stu(1)))
    def test_visitor_blocked(self): self.assertFalse(viewer_can_view(self.ev, None))

class FilterMatchesRule(unittest.TestCase):
    """The Mongo filter used by list endpoints must give the same answer as audience_allows."""
    def test_equivalence(self):
        audiences = [None, aud("all"), aud("departments", departments=["CSE"]),
                     aud("classes", class_ids=[H(10)]), aud("students", student_ids=[H(1)]), {"type": "weird"}]
        students = [stu(1, "CSE", H(10)), stu(2, "EEE", H(11)), stu(3, "Mechanical"), stu(1, "Mechanical", H(77))]
        for a in audiences:
            ev = {} if a is None else {"audience": a}
            for s in students:
                snap = student_snapshot(s)
                self.assertEqual(match(ev, mongo_filter_for_student(snap)), audience_allows(a, snap), (a, s))
        # visitor
        for a in audiences:
            ev = {} if a is None else {"audience": a}
            self.assertEqual(match(ev, mongo_filter_for_student(None)), audience_allows(a, None), a)
    def test_explicit_none_audience_is_legacy(self):
        self.assertTrue(match({"audience": None}, mongo_filter_for_student(None)))

class Misc(unittest.TestCase):
    def test_mask_email(self): self.assertEqual(mask_email("sutarsumeeth@gmail.com"), "s***h@gmail.com"); self.assertEqual(mask_email("ab@x.com"), "a***@x.com"); self.assertEqual(mask_email("nope"), "")
    def test_describe(self): self.assertIn("All", describe_audience(aud("all"))); self.assertIn("legacy", describe_audience(None))

if __name__ == "__main__": unittest.main(verbosity=1)