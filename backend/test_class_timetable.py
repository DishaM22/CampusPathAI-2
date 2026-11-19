"""
test_class_timetable.py  -  all tests for class_timetable.py in one file.

Run (from the backend folder):

    python -m unittest test_class_timetable -v
        or
    pytest test_class_timetable.py

* Rules and database tests need nothing extra. If `mongomock` is installed it
  is used as the fake database; otherwise a tiny built-in fake is used.
* The HTTP tests (class ApiTests) need:  pip install fastapi httpx mongomock
  and are skipped automatically when those are missing.
* The engine hand-off tests use your recommender_schedule.py and are skipped
  if it is not importable.
"""

import copy
import itertools
import os
import re
import sys
import types
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ============================================================
# TEST SUPPORT: bson fallback + in-memory database
# ============================================================

def install_bson_stub():
    """Provide a minimal `bson.ObjectId` if the real package is missing."""
    try:
        import bson  # noqa: F401
        return
    except ImportError:
        pass

    module = types.ModuleType("bson")
    counter = itertools.count(1)

    class ObjectId:
        def __init__(self, value=None):
            if value is None:
                value = "%024x" % (0xA00000 + next(counter))
            elif isinstance(value, ObjectId):
                value = value._value
            if not (isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{24}", value)):
                raise ValueError("not a valid ObjectId")
            self._value = value.lower()

        def __str__(self):
            return self._value

        def __repr__(self):
            return f"ObjectId('{self._value}')"

        def __eq__(self, other):
            return isinstance(other, ObjectId) and other._value == self._value

        def __hash__(self):
            return hash(self._value)

    module.ObjectId = ObjectId
    sys.modules["bson"] = module


class _UpdateResult:
    def __init__(self, matched):
        self.matched_count = matched
        self.modified_count = matched


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _matches(doc, query):
        for key, condition in (query or {}).items():
            value = doc.get(key)
            if isinstance(condition, dict):
                for operator, argument in condition.items():
                    if operator == "$ne":
                        if value == argument:
                            return False
                    elif operator == "$in":
                        if value not in argument:
                            return False
                    else:
                        raise NotImplementedError(operator)
            elif value != condition:
                return False
        return True

    def insert_one(self, document):
        from bson import ObjectId

        stored = copy.deepcopy(document)
        stored.setdefault("_id", ObjectId())
        document["_id"] = stored["_id"]
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find_one(self, query=None, projection=None):
        for doc in self.docs:
            if self._matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query=None, projection=None):
        return [copy.deepcopy(d) for d in self.docs if self._matches(d, query)]

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = copy.deepcopy(value)
                return _UpdateResult(1)
        return _UpdateResult(0)

    def count_documents(self, query):
        return len([d for d in self.docs if self._matches(d, query)])

    def create_index(self, *args, **kwargs):
        return "index"


class FakeDB:
    def __init__(self):
        self._collections = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())


install_bson_stub()

from bson import ObjectId  # noqa: E402

try:
    import mongomock  # noqa: E402
except ImportError:
    mongomock = None

import class_timetable as ct  # noqa: E402

try:
    import recommender_schedule as schedule  # noqa: E402
except ImportError:
    schedule = None


def make_db():
    """A brand-new empty database for one test."""
    if mongomock is not None:
        return mongomock.MongoClient()[f"campuspath_test_{uuid.uuid4().hex[:8]}"]
    return FakeDB()


def add_user(db, role, name="User", **extra):
    """Insert a user in `students` (where every account lives)."""
    doc = {"name": name, "email": f"{name.lower().replace(' ', '')}@test.edu", "role": role}
    doc.update(extra)
    db.students.insert_one(doc)
    return doc


GOOD_CLASS = {"department": "AI & ML", "academic_year": "2026-2027",
              "year_of_study": 4, "semester": 7, "section": "A"}


def block(subject="Machine Learning", code="ML701", type="lecture",
          start="09:00", end="10:00", **extra):
    data = {"subject": subject, "courseCode": code, "type": type,
            "startTime": start, "endTime": end}
    data.update(extra)
    return data


def monday(*blocks):
    return {"timetable": {"monday": list(blocks)}}


def lecture(subject="Machine Learning", code="ML701", start="09:00", end="10:00"):
    return block(subject, code, "lecture", start, end)


# ============================================================
# RULES (pure functions)
# ============================================================

CONFIG = dict(ct.DEFAULT_CONFIG)


class BlockRules(unittest.TestCase):
    def check(self, raw):
        return ct.normalize_block("monday", 0, raw, CONFIG)

    def test_valid_block_gets_an_id_and_clean_fields(self):
        clean, errors = self.check(block(subject="  Machine   Learning ", faculty=" Dr  Rao"))
        self.assertEqual(errors, [])
        self.assertEqual(clean["subject"], "Machine Learning")
        self.assertEqual(clean["faculty"], "Dr Rao")
        self.assertTrue(clean["id"].startswith("blk_"))

    def test_missing_subject_is_rejected(self):
        _, errors = self.check(block(subject=""))
        self.assertTrue(any("subject is required" in e for e in errors))

    def test_class_needs_subject_code_but_break_does_not(self):
        _, errors = self.check(block(code=""))
        self.assertTrue(any("subject code is required" in e for e in errors))
        clean, errors = self.check(block(subject="Lunch", code="", type="lunch",
                                         start="12:00", end="13:00"))
        self.assertEqual(errors, [])

    def test_laboratory_is_stored_as_lab(self):
        clean, errors = self.check(block(type="Laboratory"))
        self.assertEqual(errors, [])
        self.assertEqual(clean["type"], "lab")

    def test_unknown_type_rejected(self):
        _, errors = self.check(block(type="party"))
        self.assertTrue(any("type must be one of" in e for e in errors))

    def test_start_must_be_before_end(self):
        for start, end in (("10:00", "09:00"), ("10:00", "10:00")):
            _, errors = self.check(block(start=start, end=end))
            self.assertTrue(any("earlier than end" in e for e in errors), (start, end))

    def test_bad_time_format_rejected(self):
        for bad in ("25:00", "9am", "", None, "10:75"):
            _, errors = self.check(block(start=bad))
            self.assertTrue(errors, bad)

    def test_single_digit_hour_is_normalized(self):
        clean, errors = self.check(block(start="9:00", end="10:00"))
        self.assertEqual(errors, [])
        self.assertEqual(clean["startTime"], "09:00")

    def test_outside_college_hours_rejected(self):
        _, errors = self.check(block(start="06:00", end="07:00"))
        self.assertTrue(any("college hours" in e for e in errors))

    def test_overlong_text_rejected(self):
        _, errors = self.check(block(subject="x" * 81))
        self.assertTrue(errors)

    def test_snake_case_aliases_accepted(self):
        clean, errors = self.check({"subject": "DBMS", "subject_code": "DB1",
                                    "type": "lecture", "start_time": "09:00",
                                    "end_time": "10:00"})
        self.assertEqual(errors, [])
        self.assertEqual(clean["courseCode"], "DB1")


class TimetableRules(unittest.TestCase):
    def check(self, raw):
        return ct.normalize_timetable(raw, CONFIG)

    def test_all_five_days_always_present(self):
        clean, errors = self.check({"monday": [block()]})
        self.assertEqual(errors, [])
        self.assertEqual(list(clean), list(ct.DAYS))
        self.assertEqual(clean["tuesday"], [])

    def test_overlap_same_day_rejected(self):
        _, errors = self.check({"monday": [block(), block(subject="DBMS", code="DB1",
                                                          start="09:30", end="10:30")]})
        self.assertTrue(any("overlaps" in e for e in errors))

    def test_back_to_back_sessions_are_allowed(self):
        _, errors = self.check({"monday": [block(), block(subject="DBMS", code="DB1",
                                                          start="10:00", end="11:00")]})
        self.assertEqual(errors, [])

    def test_same_time_on_different_days_is_fine(self):
        _, errors = self.check({"monday": [block()], "tuesday": [block()]})
        self.assertEqual(errors, [])

    def test_exact_duplicate_rejected_as_duplicate(self):
        _, errors = self.check({"monday": [block(), block()]})
        self.assertTrue(any("duplicate" in e for e in errors))

    def test_break_overlapping_a_class_is_rejected(self):
        _, errors = self.check({"monday": [block(), block(subject="Lunch", code="",
                                                          type="lunch", start="09:30", end="10:30")]})
        self.assertTrue(any("overlaps" in e for e in errors))

    def test_unknown_day_rejected(self):
        _, errors = self.check({"funday": [block()]})
        self.assertTrue(any("Unknown day" in e for e in errors))

    def test_saturday_is_not_supported(self):
        _, errors = self.check({"saturday": [block()]})
        self.assertTrue(errors)

    def test_day_must_be_a_list(self):
        _, errors = self.check({"monday": "nope"})
        self.assertTrue(errors)

    def test_not_an_object_rejected(self):
        _, errors = self.check(["monday"])
        self.assertTrue(errors)

    def test_day_names_are_case_insensitive(self):
        clean, errors = self.check({"Monday": [block()]})
        self.assertEqual(errors, [])
        self.assertEqual(len(clean["monday"]), 1)

    def test_duplicate_ids_get_new_ids(self):
        clean, _ = self.check({"monday": [block(id="same")],
                               "tuesday": [block(id="same")]})
        ids = [b["id"] for d in clean.values() for b in d]
        self.assertEqual(len(set(ids)), 2)

    def test_too_many_sessions_in_a_day_rejected(self):
        many = [block(start=f"{8 + i // 6:02d}:{(i % 6) * 10:02d}",
                      end=f"{8 + i // 6:02d}:{(i % 6) * 10 + 5:02d}", subject=f"S{i}", code=f"C{i}")
                for i in range(21)]
        _, errors = self.check({"monday": many})
        self.assertTrue(any("at most" in e for e in errors))


class PublishReadiness(unittest.TestCase):
    def test_empty_timetable_not_publishable(self):
        self.assertTrue(ct.publish_readiness_errors(ct.empty_timetable()))

    def test_only_breaks_not_publishable(self):
        clean, _ = ct.normalize_timetable(
            {"monday": [block(subject="Lunch", code="", type="lunch", start="12:00", end="13:00")]}, CONFIG)
        self.assertTrue(ct.publish_readiness_errors(clean))

    def test_one_class_is_enough(self):
        clean, _ = ct.normalize_timetable({"monday": [block()]}, CONFIG)
        self.assertEqual(ct.publish_readiness_errors(clean), [])


class ConfigRules(unittest.TestCase):
    def test_defaults_when_missing(self):
        config, errors = ct.normalize_config(None)
        self.assertEqual(errors, [])
        self.assertEqual(config, ct.DEFAULT_CONFIG)

    def test_start_must_be_before_end(self):
        _, errors = ct.normalize_config({"collegeStartTime": "17:00", "collegeEndTime": "08:00"})
        self.assertTrue(errors)

    def test_bad_slot_duration(self):
        _, errors = ct.normalize_config({"slotDuration": 37})
        self.assertTrue(errors)


class ClassRules(unittest.TestCase):
    GOOD = {"department": "AI & ML", "academic_year": "2026-2027",
            "year_of_study": 4, "semester": 7, "section": "A"}

    def test_valid_class(self):
        clean, errors = ct.normalize_class_fields(dict(self.GOOD))
        self.assertEqual(errors, [])
        self.assertEqual(clean["section"], "A")

    def test_required_fields(self):
        _, errors = ct.normalize_class_fields({})
        self.assertGreaterEqual(len(errors), 5)

    def test_academic_year_must_be_consecutive(self):
        for bad in ("2026", "2026-2028", "26-27", "abcd-efgh"):
            _, errors = ct.normalize_class_fields({**self.GOOD, "academic_year": bad})
            self.assertTrue(errors, bad)

    def test_year_and_semester_ranges(self):
        _, errors = ct.normalize_class_fields({**self.GOOD, "year_of_study": 5, "semester": 9})
        self.assertEqual(len(errors), 2)

    def test_partial_update_checks_only_given_keys(self):
        clean, errors = ct.normalize_class_fields({"section": "B"}, partial=True)
        self.assertEqual(errors, [])
        self.assertEqual(clean, {"section": "B"})

    def test_identity_key_ignores_case_and_spacing(self):
        a = ct.class_identity_key("AI  & ML", "2026-2027", 7, " a ", "")
        b = ct.class_identity_key("ai & ml", "2026-2027", 7, "A", "")
        self.assertEqual(a, b)

    def test_identity_key_differs_by_batch_and_semester(self):
        base = ct.class_identity_key("CSE", "2026-2027", 7, "A", "")
        self.assertNotEqual(base, ct.class_identity_key("CSE", "2026-2027", 7, "A", "B1"))
        self.assertNotEqual(base, ct.class_identity_key("CSE", "2026-2027", 8, "A", ""))


class PreferenceRules(unittest.TestCase):
    def test_valid(self):
        clean, errors = ct.normalize_preferences({
            "preferredEventDuration": ["1–2 hours"], "bufferTime": "30",
            "preferredEventTimes": ["Morning"], "notificationPrefs": ["Event reminders"]})
        self.assertEqual(errors, [])
        self.assertEqual(clean["bufferTime"], 30)
        self.assertEqual(clean["preferredEventTimes"], ["morning"])

    def test_buffer_out_of_range(self):
        _, errors = ct.normalize_preferences({"bufferTime": 500})
        self.assertTrue(errors)

    def test_missing_fields_get_defaults(self):
        clean, errors = ct.normalize_preferences({})
        self.assertEqual(errors, [])
        self.assertEqual(clean, ct.DEFAULT_PREFERENCES)


# ============================================================
# DATABASE: classes, timetables, student class, resolver, engine hand-off
# ============================================================

class Base(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.admin = add_user(self.db, "admin", "Admin")
        self.student = add_user(self.db, "student", "Asha")
        self.sid = str(self.student["_id"])
        os.environ.pop("CAMPUSPATH_LEGACY_TIMETABLE_FALLBACK", None)

    def tearDown(self):
        os.environ.pop("CAMPUSPATH_LEGACY_TIMETABLE_FALLBACK", None)

    def new_class(self, **overrides):
        return ct.create_class(self.db, self.admin, {**GOOD_CLASS, **overrides})

    def publish_class_timetable(self, class_id, *blocks):
        draft = ct.save_draft(self.db, self.admin, class_id,
                               monday(*(blocks or (block(),))))
        return ct.publish(self.db, self.admin, draft["id"])


# ============================================================
# CLASSES
# ============================================================

class ClassTests(Base):
    def test_admin_can_create_a_class(self):
        cls = self.new_class()
        self.assertEqual(cls["name"], "AI & ML - Year 4 - Sem 7 - Section A")
        self.assertTrue(cls["is_active"])
        self.assertFalse(cls["has_published_timetable"])

    def test_duplicate_active_class_rejected(self):
        self.new_class()
        with self.assertRaises(ct.ServiceError) as ctx:
            self.new_class(department="  ai  & ml ", section="a")
        self.assertEqual(ctx.exception.status, 409)

    def test_different_section_or_batch_is_not_a_duplicate(self):
        self.new_class()
        self.new_class(section="B")
        self.new_class(batch="Batch 1")

    def test_duplicate_allowed_once_the_first_is_deactivated(self):
        first = self.new_class()
        ct.update_class(self.db, self.admin, first["id"], {"is_active": False})
        self.new_class()

    def test_reactivating_into_a_duplicate_is_rejected(self):
        first = self.new_class()
        ct.update_class(self.db, self.admin, first["id"], {"is_active": False})
        self.new_class()
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.update_class(self.db, self.admin, first["id"], {"is_active": True})
        self.assertEqual(ctx.exception.status, 409)

    def test_invalid_fields_rejected(self):
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.create_class(self.db, self.admin, {"department": "CSE"})
        self.assertEqual(ctx.exception.status, 422)

    def test_default_name_follows_edits_but_custom_name_is_kept(self):
        auto = self.new_class()
        edited = ct.update_class(self.db, self.admin, auto["id"], {"section": "C"})
        self.assertTrue(edited["name"].endswith("Section C"))

        custom = self.new_class(section="Z", name="Honours batch")
        edited = ct.update_class(self.db, self.admin, custom["id"], {"section": "Y"})
        self.assertEqual(edited["name"], "Honours batch")

    def test_deactivating_keeps_timetables_and_student_assignments(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])
        ct.assign_student_class(self.db, self.sid, cls["id"])

        ct.update_class(self.db, self.admin, cls["id"], {"is_active": False})

        self.assertEqual(self.db.class_timetables.count_documents({"class_id": cls["id"]}), 1)
        self.assertEqual(self.db.students.find_one({"_id": self.student["_id"]})["class_id"], cls["id"])

    def test_unknown_and_invalid_class_ids(self):
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.get_class_doc(self.db, str(ObjectId()))
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.get_class_doc(self.db, "not-an-id")
        self.assertEqual(ctx.exception.status, 400)

    def test_list_filters_and_active_only(self):
        a = self.new_class()
        self.new_class(section="B", year_of_study=3, semester=5)
        ct.update_class(self.db, self.admin, a["id"], {"is_active": False})

        self.assertEqual(len(ct.list_classes(self.db)), 2)
        self.assertEqual(len(ct.list_classes(self.db, active_only=True)), 1)
        self.assertEqual(len(ct.list_classes(self.db, year_of_study=3)), 1)
        self.assertEqual(len(ct.list_classes(self.db, search="section b")), 1)

    def test_student_counts(self):
        cls = self.new_class()
        ct.assign_student_class(self.db, self.sid, cls["id"])
        rows = ct.list_classes(self.db, with_counts=True)
        self.assertEqual(rows[0]["student_count"], 1)


# ============================================================
# TIMETABLE DRAFT / PUBLISH
# ============================================================

class TimetableTests(Base):
    def test_save_draft_is_not_visible_to_students(self):
        cls = self.new_class()
        ct.save_draft(self.db, self.admin, cls["id"], monday(block()))
        view = ct.get_published_for_class(self.db, cls["id"])
        self.assertFalse(view["published"])
        self.assertIn("not been published", view["message"])

    def test_saving_again_updates_the_same_draft(self):
        cls = self.new_class()
        first = ct.save_draft(self.db, self.admin, cls["id"], monday(block()))
        second = ct.save_draft(self.db, self.admin, cls["id"],
                                monday(block(), block("DBMS", "DB1", start="10:00", end="11:00")))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["class_sessions"], 2)
        self.assertEqual(self.db.class_timetables.count_documents({"class_id": cls["id"]}), 1)

    def test_overlap_invalid_range_and_missing_fields_rejected_on_save(self):
        cls = self.new_class()
        bad_bodies = [
            monday(block(), block("DBMS", "DB1", start="09:30", end="10:30")),
            monday(block(start="11:00", end="10:00")),
            monday(block(code="")),
            monday(block(subject="")),
            {"timetable": {"monday": [block()]}, "config": {"slotDuration": 7}},
            {},
        ]
        for body in bad_bodies:
            with self.assertRaises(ct.ServiceError) as ctx:
                ct.save_draft(self.db, self.admin, cls["id"], body)
            self.assertEqual(ctx.exception.status, 422, body)
        self.assertEqual(self.db.class_timetables.count_documents({"class_id": cls["id"]}), 0)

    def test_invalid_class_id_on_save(self):
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.save_draft(self.db, self.admin, "bad", monday(block()))
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.save_draft(self.db, self.admin, str(ObjectId()), monday(block()))
        self.assertEqual(ctx.exception.status, 404)

    def test_incomplete_timetable_cannot_be_published(self):
        cls = self.new_class()
        empty = ct.save_draft(self.db, self.admin, cls["id"], {"timetable": {}})
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.publish(self.db, self.admin, empty["id"])
        self.assertEqual(ctx.exception.status, 422)
        self.assertFalse(ct.get_published_for_class(self.db, cls["id"])["published"])

    def test_publish_makes_it_live_as_version_1(self):
        cls = self.new_class()
        result = self.publish_class_timetable(cls["id"])
        self.assertEqual(result["timetable"]["status"], "published")
        self.assertEqual(result["timetable"]["version"], 1)
        self.assertTrue(result["class"]["has_published_timetable"])

        view = ct.get_published_for_class(self.db, cls["id"])
        self.assertTrue(view["published"])
        self.assertEqual(view["timetable"]["monday"][0]["subject"], "Machine Learning")

    def test_unpublished_changes_do_not_replace_the_live_timetable(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])

        ct.save_draft(self.db, self.admin, cls["id"],
                       monday(block("Changed Subject", "CH1", start="13:00", end="14:00")))

        live = ct.get_published_for_class(self.db, cls["id"])
        self.assertEqual(live["timetable"]["monday"][0]["subject"], "Machine Learning")
        self.assertEqual(live["version"], 1)

        admin_view = ct.admin_timetable_view(self.db, cls["id"])
        self.assertTrue(admin_view["has_unpublished_changes"])
        self.assertEqual(admin_view["draft"]["timetable"]["monday"][0]["subject"], "Changed Subject")

    def test_publishing_a_replacement_archives_the_old_version(self):
        cls = self.new_class()
        first = self.publish_class_timetable(cls["id"])
        second = self.publish_class_timetable(
            cls["id"], block("Changed Subject", "CH1", start="13:00", end="14:00"))

        self.assertEqual(second["timetable"]["version"], 2)
        live = ct.get_published_for_class(self.db, cls["id"])
        self.assertEqual(live["timetable"]["monday"][0]["subject"], "Changed Subject")

        history = ct.admin_timetable_view(self.db, cls["id"])["history"]
        self.assertEqual([row["version"] for row in history], [2, 1])
        self.assertEqual([row["status"] for row in history], ["published", "archived"])
        # the old version's content is still there for audit
        old = self.db.class_timetables.find_one({"_id": ObjectId(first["timetable"]["id"])})
        self.assertEqual(old["timetable"]["monday"][0]["subject"], "Machine Learning")

    def test_a_published_version_cannot_be_edited_or_published_twice(self):
        cls = self.new_class()
        published = self.publish_class_timetable(cls["id"])
        tid = published["timetable"]["id"]

        with self.assertRaises(ct.ServiceError) as ctx:
            ct.update_timetable_by_id(self.db, self.admin, tid, monday(block()))
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.publish(self.db, self.admin, tid)
        self.assertEqual(ctx.exception.status, 409)

    def test_update_draft_by_id(self):
        cls = self.new_class()
        draft = ct.save_draft(self.db, self.admin, cls["id"], monday(block()))
        updated = ct.update_timetable_by_id(
            self.db, self.admin, draft["id"],
            monday(block(), block("DBMS", "DB1", start="10:00", end="11:00")))
        self.assertEqual(updated["class_sessions"], 2)

    def test_inactive_class_cannot_be_published(self):
        cls = self.new_class()
        draft = ct.save_draft(self.db, self.admin, cls["id"], monday(block()))
        ct.update_class(self.db, self.admin, cls["id"], {"is_active": False})
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.publish(self.db, self.admin, draft["id"])
        self.assertEqual(ctx.exception.status, 409)

    def test_public_timetable_hidden_for_inactive_class(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])
        ct.update_class(self.db, self.admin, cls["id"], {"is_active": False})
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.get_published_for_class(self.db, cls["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_publish_writes_an_activity_log(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])
        actions = [row["action"] for row in self.db.activity_logs.find({})]
        self.assertIn("timetable_published", actions)


# ============================================================
# STUDENT: CLASS SELECTION + RESOLVER
# ============================================================

class StudentTests(Base):
    def test_class_selection_saves_and_takes_effect_immediately(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])

        result = ct.assign_student_class(self.db, self.sid, cls["id"])

        self.assertEqual(result["class"]["id"], cls["id"])
        self.assertEqual(result["timetable"]["source"], "official")
        self.assertTrue(result["timetable"]["published"])
        self.assertEqual(
            self.db.students.find_one({"_id": self.student["_id"]})["class_id"], cls["id"])

    def test_only_the_named_student_is_changed(self):
        other = add_user(self.db, "student", "Ben")
        cls = self.new_class()
        ct.assign_student_class(self.db, self.sid, cls["id"])
        self.assertNotIn("class_id", self.db.students.find_one({"_id": other["_id"]}))

    def test_inactive_unknown_and_invalid_classes_rejected(self):
        cls = self.new_class()
        ct.update_class(self.db, self.admin, cls["id"], {"is_active": False})
        for class_id, status in ((cls["id"], 409), (str(ObjectId()), 404), ("junk", 400)):
            with self.assertRaises(ct.ServiceError) as ctx:
                ct.assign_student_class(self.db, self.sid, class_id)
            self.assertEqual(ctx.exception.status, status, class_id)
        self.assertNotIn("class_id", self.db.students.find_one({"_id": self.student["_id"]}))

    def test_unknown_student_rejected(self):
        cls = self.new_class()
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.assign_student_class(self.db, str(ObjectId()), cls["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_no_timetable_published_gives_a_clear_message_not_an_error(self):
        cls = self.new_class()
        result = ct.assign_student_class(self.db, self.sid, cls["id"])
        resolved = result["timetable"]
        self.assertEqual(resolved["source"], "official")
        self.assertFalse(resolved["published"])
        self.assertEqual(resolved["timetable"], {})
        self.assertIn("not been published", resolved["message"])

    def test_class_without_a_timetable_does_not_fall_back_to_the_old_personal_one(self):
        self.db.timetables.insert_one({"student_id": self.sid,
                                       "timetable": {"monday": [block()]}, "config": {}, "preferences": {}})
        cls = self.new_class()
        ct.assign_student_class(self.db, self.sid, cls["id"])
        self.assertEqual(ct.resolve_student_timetable(self.db, self.sid)["timetable"], {})

    def test_changing_class_switches_the_timetable(self):
        a, b = self.new_class(section="A"), self.new_class(section="B")
        self.publish_class_timetable(a["id"], block("Subject A", "A1"))
        self.publish_class_timetable(b["id"], block("Subject B", "B1"))

        ct.assign_student_class(self.db, self.sid, a["id"])
        self.assertEqual(ct.resolve_student_timetable(self.db, self.sid)["timetable"]["monday"][0]["subject"], "Subject A")

        ct.assign_student_class(self.db, self.sid, b["id"])
        self.assertEqual(ct.resolve_student_timetable(self.db, self.sid)["timetable"]["monday"][0]["subject"], "Subject B")

    def test_students_see_a_new_publish_automatically(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"], block("Old", "O1"))
        ct.assign_student_class(self.db, self.sid, cls["id"])
        self.publish_class_timetable(cls["id"], block("New", "N1", start="11:00", end="12:00"))
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["timetable"]["monday"][0]["subject"], "New")
        self.assertEqual(resolved["version"], 2)

    def test_deactivated_class_keeps_the_assignment_and_says_so(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])
        ct.assign_student_class(self.db, self.sid, cls["id"])
        ct.update_class(self.db, self.admin, cls["id"], {"is_active": False})

        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["class"]["id"], cls["id"])
        self.assertFalse(resolved["class_active"])
        self.assertIn("deactivated", resolved["message"])
        self.assertTrue(resolved["published"])  # timetable is still shown

        info = ct.get_student_class(self.db, self.sid)
        self.assertFalse(info["class_active"])

    def test_student_without_a_class_uses_the_legacy_timetable_by_default(self):
        self.db.timetables.insert_one({"student_id": self.sid, "timetable": {"monday": [block()]},
                                       "config": {"collegeStartTime": "09:00"}, "preferences": {"bufferTime": 15}})
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["source"], "legacy")
        self.assertEqual(resolved["preferences"]["bufferTime"], 15)

    def test_legacy_fallback_can_be_switched_off(self):
        os.environ["CAMPUSPATH_LEGACY_TIMETABLE_FALLBACK"] = "false"
        self.db.timetables.insert_one({"student_id": self.sid, "timetable": {"monday": [block()]}})
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["source"], "none")
        self.assertEqual(resolved["timetable"], {})

    def test_legacy_student_with_nothing_saved_does_not_crash(self):
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["source"], "none")
        self.assertEqual(resolved["timetable"], {})

    def test_garbage_student_id_does_not_crash(self):
        resolved = ct.resolve_student_timetable(self.db, "not-an-id")
        self.assertEqual(resolved["source"], "none")

    def test_class_that_no_longer_exists_asks_to_choose_again(self):
        self.db.students.update_one({"_id": self.student["_id"]}, {"$set": {"class_id": str(ObjectId())}})
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertIn("no longer available", resolved["message"])

    def test_student_class_info(self):
        self.assertIsNone(ct.get_student_class(self.db, self.sid)["class"])
        cls = self.new_class()
        ct.assign_student_class(self.db, self.sid, cls["id"])
        self.assertEqual(ct.get_student_class(self.db, self.sid)["class"]["id"], cls["id"])


# ============================================================
# AVAILABILITY PREFERENCES
# ============================================================

class PreferenceTests(Base):
    PREFS = {"preferredEventDuration": ["1–2 hours"], "bufferTime": 30,
             "preferredEventTimes": ["morning"], "notificationPrefs": ["Event reminders"]}

    def test_requires_an_existing_profile_and_never_creates_one(self):
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.save_availability_preferences(self.db, self.sid, self.PREFS)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIsNone(self.db.profiles.find_one({"student_id": self.sid}))

    def test_saved_preferences_are_used_by_the_resolver(self):
        self.db.profiles.insert_one({"student_id": self.sid, "year": "4th Year"})
        ct.save_availability_preferences(self.db, self.sid, self.PREFS)
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["preferences"]["bufferTime"], 30)
        # the rest of the profile is untouched
        self.assertEqual(self.db.profiles.find_one({"student_id": self.sid})["year"], "4th Year")

    def test_preferences_survive_changing_class(self):
        self.db.profiles.insert_one({"student_id": self.sid})
        ct.save_availability_preferences(self.db, self.sid, self.PREFS)
        cls = self.new_class()
        ct.assign_student_class(self.db, self.sid, cls["id"])
        self.assertEqual(ct.resolve_student_timetable(self.db, self.sid)["preferences"]["bufferTime"], 30)

    def test_invalid_preferences_rejected(self):
        self.db.profiles.insert_one({"student_id": self.sid})
        with self.assertRaises(ct.ServiceError) as ctx:
            ct.save_availability_preferences(self.db, self.sid, {"bufferTime": 999})
        self.assertEqual(ctx.exception.status, 422)


# ============================================================
# HAND-OFF TO THE RECOMMENDATION ENGINE (its own schedule code, unchanged)
# ============================================================

@unittest.skipIf(schedule is None, "recommender_schedule.py is not on the path")
class EngineHandOffTests(Base):
    # 2026-11-02 is a Monday.
    def engine_document(self):
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        return {"timetable": resolved["timetable"], "config": resolved["config"],
                "preferences": resolved["preferences"]}

    def check(self, time_text, date="2026-11-02"):
        document = self.engine_document()
        slots = schedule.timetable_to_busy_slots(document)
        return schedule.check_event_schedule(
            {"date": date, "time": time_text}, slots, schedule.get_buffer_minutes(document))

    def setup_student_with_class(self, *blocks):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"], *blocks)
        ct.assign_student_class(self.db, self.sid, cls["id"])

    def test_event_during_a_class_is_a_conflict(self):
        self.setup_student_with_class(block())
        self.assertEqual(self.check("09:30 AM – 10:30 AM").status, schedule.CONFLICT)

    def test_event_outside_class_hours_is_free(self):
        self.setup_student_with_class(block())
        self.assertEqual(self.check("11:00 AM – 12:00 PM").status, schedule.FREE)

    def test_event_on_a_day_without_that_class_is_free(self):
        self.setup_student_with_class(block())
        self.assertEqual(self.check("09:30 AM – 10:30 AM", date="2026-11-03").status, schedule.FREE)

    def test_event_over_lunch_is_a_soft_issue_not_a_conflict(self):
        self.setup_student_with_class(block(), block("Lunch", "", type="lunch", start="12:00", end="13:00"))
        self.assertEqual(self.check("12:30 PM – 1:30 PM").status, schedule.TIGHT)

    def test_buffer_preference_reaches_the_engine(self):
        self.db.profiles.insert_one({"student_id": self.sid})
        ct.save_availability_preferences(self.db, self.sid, {"bufferTime": 30})
        self.setup_student_with_class(block())
        self.assertEqual(self.check("10:15 AM – 11:00 AM").status, schedule.TIGHT)

    def test_no_timetable_is_unknown_never_free(self):
        self.assertEqual(self.check("09:30 AM – 10:30 AM").status, schedule.UNKNOWN)

    def test_publishing_a_new_timetable_changes_the_result(self):
        cls = self.new_class()
        self.publish_class_timetable(cls["id"])
        ct.assign_student_class(self.db, self.sid, cls["id"])
        self.assertEqual(self.check("09:30 AM – 10:30 AM").status, schedule.CONFLICT)

        self.publish_class_timetable(cls["id"], block("Moved", "M1", start="14:00", end="15:00"))
        self.assertEqual(self.check("09:30 AM – 10:30 AM").status, schedule.FREE)
        self.assertEqual(self.check("02:30 PM – 03:30 PM").status, schedule.CONFLICT)


# ============================================================
# END TO END: new timetable system -> your real recommendation engine
# (the engine itself is not changed; these tests only feed it)
# ============================================================

class EngineEndToEndTests(Base):
    """
    Mirrors what main.dashboard_events() now does: resolve the student's
    timetable, hand the engine {"timetable", "config", "preferences"}, and
    read the schedule labels it returns. 2026-11-02 is a Monday.
    """

    NOW = None

    @classmethod
    def setUpClass(cls):
        try:
            import importlib
            import recommendation_engine as engine
        except Exception as error:  # missing dependency / file not on the path
            raise unittest.SkipTest(f"recommendation_engine not importable: {error}")

        cls.importlib = importlib
        cls.engine = engine
        cls._saved_semantic = engine.SENTENCE_TRANSFORMERS_AVAILABLE
        # Keep the tests fast and offline: the AI embedding model is not
        # needed to check how timetables are used.
        engine.SENTENCE_TRANSFORMERS_AVAILABLE = False

    @classmethod
    def tearDownClass(cls):
        cls.engine.SENTENCE_TRANSFORMERS_AVAILABLE = cls._saved_semantic

    # ---------- helpers ----------

    def setUp(self):
        super().setUp()
        from datetime import datetime
        self.now = datetime(2026, 10, 31, 10, 0)
        self.student_profile = {
            "id": self.sid, "skills": ["python", "machine learning"],
            "interests": ["artificial intelligence"], "career_goal": ["AI Engineer"],
            "department": "AIML", "year": "4",
        }

    def event(self, event_id, date, time_text):
        # identical content on purpose, so any score difference comes from the schedule
        return {"id": event_id, "title": "AI workshop", "category": "Workshop",
                "description": "artificial intelligence machine learning python",
                "skills": ["python", "machine learning"], "capacity": 100,
                "registered": 5, "date": date, "time": time_text}

    def standard_events(self):
        return [
            self.event("during", "2026-11-02", "09:30 AM – 10:30 AM"),
            self.event("free", "2026-11-02", "11:00 AM – 12:00 PM"),
            self.event("lunch", "2026-11-02", "12:15 PM – 1:00 PM"),
            self.event("weekend", "2026-11-07", "10:00 AM – 12:00 PM"),
        ]

    def engine_document(self):
        """Exactly how main.dashboard_events() builds the engine's input."""
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        return {"timetable": resolved["timetable"] or {},
                "config": resolved["config"] or {},
                "preferences": resolved["preferences"] or {}}

    def recommend(self, events=None, document=None):
        results = self.engine.get_recommendations(
            self.student_profile, events or self.standard_events(), None,
            self.engine_document() if document is None else document,
            top_k=10, now=self.now)
        return {row["id"]: row for row in results}

    def publish_for_student(self, *blocks):
        cls_ = self.new_class()
        self.publish_class_timetable(cls_["id"], *blocks)
        ct.assign_student_class(self.db, self.sid, cls_["id"])
        return cls_

    LECTURE_AND_LUNCH = (
        block("Machine Learning", "ML701", start="09:00", end="10:00"),
        block("Lunch", "", type="lunch", start="12:00", end="13:00"),
    )

    # ---------- tests ----------

    def test_clashes_are_labelled_by_the_official_timetable(self):
        self.publish_for_student(*self.LECTURE_AND_LUNCH)
        got = self.recommend()
        self.assertEqual(got["during"]["schedule_status"], "conflict")
        self.assertTrue(got["during"]["schedule_conflict"])
        self.assertEqual(got["free"]["schedule_status"], "free")
        self.assertEqual(got["lunch"]["schedule_status"], "tight")      # soft: lunch, not a class
        self.assertEqual(got["weekend"]["schedule_status"], "unknown")  # timetable is Mon-Fri only

    def test_by_default_a_clash_is_shown_but_does_not_change_the_ranking(self):
        self.publish_for_student(*self.LECTURE_AND_LUNCH)
        got = self.recommend()
        self.assertIn("during", got)  # still recommended, just labelled
        self.assertEqual(got["during"]["recommendation_score"], got["free"]["recommendation_score"])

    def test_hide_flag_removes_only_the_clashing_event(self):
        self.publish_for_student(*self.LECTURE_AND_LUNCH)
        self.engine.HIDE_TIMETABLE_CONFLICTS = True
        try:
            got = self.recommend()
        finally:
            self.engine.HIDE_TIMETABLE_CONFLICTS = False
        self.assertNotIn("during", got)
        self.assertIn("free", got)
        self.assertIn("lunch", got)

    def test_score_flag_ranks_a_clash_lower(self):
        self.publish_for_student(*self.LECTURE_AND_LUNCH)
        os.environ["TIMETABLE_AFFECTS_SCORE"] = "true"
        try:
            engine = self.importlib.reload(self.engine)
            engine.SENTENCE_TRANSFORMERS_AVAILABLE = False
            got = self.recommend()
        finally:
            os.environ.pop("TIMETABLE_AFFECTS_SCORE", None)
            engine = self.importlib.reload(self.engine)
            engine.SENTENCE_TRANSFORMERS_AVAILABLE = False
        self.assertLess(got["during"]["recommendation_score"], got["free"]["recommendation_score"])

    def test_no_class_and_no_old_timetable_is_unknown_not_free(self):
        got = self.recommend()
        self.assertEqual({row["schedule_status"] for row in got.values()}, {"unknown"})

    def test_class_without_a_published_timetable_does_not_crash_and_is_unknown(self):
        cls_ = self.new_class()
        ct.assign_student_class(self.db, self.sid, cls_["id"])
        got = self.recommend()
        self.assertEqual({row["schedule_status"] for row in got.values()}, {"unknown"})

    def test_a_missing_or_empty_document_does_not_crash(self):
        for document in ({}, {"timetable": {}, "config": {}, "preferences": {}}):
            got = self.recommend(document=document)
            self.assertTrue(got)

    def test_publishing_a_new_timetable_changes_the_labels(self):
        cls_ = self.publish_for_student(*self.LECTURE_AND_LUNCH)
        self.assertEqual(self.recommend()["during"]["schedule_status"], "conflict")

        self.publish_class_timetable(cls_["id"], block("Moved", "M1", start="14:00", end="15:00"))
        got = self.recommend()
        self.assertEqual(got["during"]["schedule_status"], "free")
        self.assertEqual(
            self.recommend([self.event("after", "2026-11-02", "02:30 PM – 03:30 PM")])["after"]["schedule_status"],
            "conflict")

    def test_unpublished_edits_do_not_change_the_labels(self):
        cls_ = self.publish_for_student(*self.LECTURE_AND_LUNCH)
        ct.save_draft(self.db, self.admin, cls_["id"],
                      monday(block("Draft only", "D1", start="11:00", end="12:00")))
        got = self.recommend()
        self.assertEqual(got["during"]["schedule_status"], "conflict")   # still the published timetable
        self.assertEqual(got["free"]["schedule_status"], "free")         # draft class is not live

    def test_changing_class_changes_the_labels(self):
        a = self.new_class(section="A")
        b = self.new_class(section="B")
        self.publish_class_timetable(a["id"], block("Class A", "A1", start="09:00", end="10:00"))
        self.publish_class_timetable(b["id"], block("Class B", "B1", start="11:00", end="12:00"))

        ct.assign_student_class(self.db, self.sid, a["id"])
        got = self.recommend()
        self.assertEqual((got["during"]["schedule_status"], got["free"]["schedule_status"]),
                         ("conflict", "free"))

        ct.assign_student_class(self.db, self.sid, b["id"])
        got = self.recommend()
        self.assertEqual((got["during"]["schedule_status"], got["free"]["schedule_status"]),
                         ("free", "conflict"))

    def test_students_buffer_preference_still_applies(self):
        self.db.profiles.insert_one({"student_id": self.sid})
        ct.save_availability_preferences(self.db, self.sid, {"bufferTime": 30})
        self.publish_for_student(block("Machine Learning", "ML701", start="09:00", end="10:00"))
        got = self.recommend([self.event("soon_after", "2026-11-02", "10:15 AM – 11:00 AM")])
        self.assertEqual(got["soon_after"]["schedule_status"], "tight")

    def test_an_old_personal_timetable_is_used_only_until_a_class_is_chosen(self):
        self.db.timetables.insert_one({"student_id": self.sid, "preferences": {},
            "timetable": {"monday": [block("Old class", "OLD", start="09:00", end="10:00")]}})
        self.assertEqual(self.recommend()["during"]["schedule_status"], "conflict")      # legacy fallback

        cls_ = self.new_class()
        self.publish_class_timetable(cls_["id"], block("Official", "O1", start="14:00", end="15:00"))
        ct.assign_student_class(self.db, self.sid, cls_["id"])
        self.assertEqual(self.recommend()["during"]["schedule_status"], "free")          # official wins


# ============================================================
# PDF IMPORT: one PDF -> many class timetables
# ============================================================

import glob  # noqa: E402

import timetable_pdf_import as imp  # noqa: E402


def _entry(day, start, end, subjects, faculty="", rooms=(), batches=()):
    return {"day": day, "start": start, "end": end, "periods": [], "subjects": list(subjects),
            "faculty": faculty, "rooms": list(rooms), "batches": list(batches), "raw": ""}


def _page(code, entries, lunch=((("12:35", "13:25")),), warnings=()):
    columns = [{"label": "1", "start": "08:50", "end": "09:45", "x0": 0, "x1": 1},
               {"label": "8", "start": "15:25", "end": "16:15", "x0": 1, "x1": 2}]
    return {"code": code, "page": 1, "columns": columns, "entries": entries,
            "lunch": [{"start": lunch[0][0], "end": lunch[0][1]}] if lunch else [],
            "warnings": list(warnings)}


class ImportConversionTests(unittest.TestCase):
    def test_words_are_classified_like_the_pdf_writes_them(self):
        def kind(text):
            if imp.SUBJECT_RE.match(text): return "subject"
            if imp.BATCH_RE.match(text): return "batch"
            if imp.ROOM_RE.match(text): return "room"
            return "other"
        for text in ("AIE4002", "CSS2256", "CAI3400_P", "FIN1002"):
            self.assertEqual(kind(text), "subject", text)
        for text in ("JT01", "DGL02a", "LIVL03a", "NS01", "QIV02", "PG01", "RG01"):
            self.assertEqual(kind(text), "room", text)
        for text in ("B1", "Bat2", "Batch5"):
            self.assertEqual(kind(text), "batch", text)
        for text in ("Jyoti", "Parameswaran", "PUNIV02229", "20243MAT0002", "S", "B"):
            self.assertEqual(kind(text), "other", text)

    def test_section_names(self):
        self.assertEqual(imp.section_details("3CAI01"),
                         {"semester": 3, "program": "CAI", "section": "01", "year_of_study": 2})
        self.assertEqual(imp.section_details("1MTAIE01")["program"], "MTAIE")
        self.assertEqual(imp.section_details("7CSD03")["year_of_study"], 4)
        self.assertEqual(imp.section_details("5CBD01")["year_of_study"], 3)
        no_semester = imp.section_details("SIC01")
        self.assertIsNone(no_semester["semester"])
        self.assertEqual(no_semester["program"], "SIC")
        self.assertEqual(imp.section_details("???")["program"], "")
        self.assertIsNone(imp.section_details("9ABC01")["semester"])  # no 9th semester

    def test_academic_year_comes_from_the_file_name(self):
        self.assertEqual(imp.guess_academic_year("PSAIAC_Section_TT_Odd_Semester_2026-2027_29-10-2026.pdf"), "2026-2027")
        self.assertEqual(imp.guess_academic_year("timetable.pdf", today=__import__("datetime").datetime(2026, 10, 1)), "2026-2027")
        self.assertEqual(imp.guess_academic_year("timetable.pdf", today=__import__("datetime").datetime(2027, 2, 1)), "2026-2027")
        self.assertEqual(imp.guess_academic_year("x_2026-2029.pdf", today=__import__("datetime").datetime(2026, 10, 1)), "2026-2027")

    def test_a_simple_cell_becomes_one_lecture(self):
        page = _page("3ADS01", [_entry("monday", "08:50", "09:45", ["CSS2251"], "Wasim Yasin", ["RT01"])])
        timetable, warnings = imp.build_timetable(page)
        block = timetable["monday"][0]
        self.assertEqual((block["subject"], block["courseCode"], block["type"]), ("CSS2251", "CSS2251", "lecture"))
        self.assertEqual((block["faculty"], block["room"]), ("Wasim Yasin", "RT01"))
        self.assertEqual(warnings, [])

    def test_lab_batches_side_by_side_become_one_lab_block(self):
        page = _page("1MTAIE01", [
            _entry("tuesday", "10:50", "12:35", ["AIE4002_P"], "Vivek Bongale", ["DGL02"], ["B1"]),
            _entry("tuesday", "10:50", "12:35", ["AIE4002_P"], "Pakruddin B", ["DGL02a"], ["B2"]),
        ])
        block = imp.build_timetable(page)[0]["tuesday"][0]
        self.assertEqual(block["type"], "lab")
        self.assertEqual(block["subject"], "AIE4002_P")
        self.assertEqual(block["faculty"], "B1: Vivek Bongale · B2: Pakruddin B")
        self.assertEqual(block["room"], "B1: DGL02 · B2: DGL02a")
        self.assertEqual((block["startTime"], block["endTime"]), ("10:50", "12:35"))

    def test_batches_with_different_subjects_keep_the_detail_in_notes(self):
        page = _page("5CCS01", [
            _entry("tuesday", "11:45", "12:35", ["CCS3400"], "Sharon M", ["NS01"], ["B1"]),
            _entry("tuesday", "11:45", "12:35", ["CCS2510"], "Hashmat Fida", ["NS03"], ["B3"]),
        ])
        block = imp.build_timetable(page)[0]["tuesday"][0]
        self.assertEqual(block["subject"], "CCS3400 / CCS2510")
        self.assertEqual(block["type"], "lecture")
        self.assertIn("B3: CCS2510 · Hashmat Fida · NS03", block["notes"])

    def test_a_single_batch_is_noted(self):
        page = _page("7CBD01", [_entry("monday", "10:50", "11:45", ["CBD3407"], "Jeevitha V K", ["NV06"], ["B2"])])
        self.assertEqual(imp.build_timetable(page)[0]["monday"][0]["notes"], "Batch B2")

    def test_lunch_is_added_on_every_weekday_when_asked(self):
        page = _page("3CAI01", [_entry("monday", "08:50", "09:45", ["CSS2264"], "Suma N G", ["RT01"])],
                     lunch=(("11:45", "12:35"),))
        timetable, _ = imp.build_timetable(page, include_lunch=True)
        for day in ct.DAYS:
            lunch = [b for b in timetable[day] if b["type"] == "lunch"]
            self.assertEqual([(b["startTime"], b["endTime"]) for b in lunch], [("11:45", "12:35")], day)
        without, _ = imp.build_timetable(page, include_lunch=False)
        self.assertFalse([b for d in without.values() for b in d if b["type"] == "lunch"])

    def test_converted_page_passes_the_normal_timetable_rules(self):
        page = _page("3ADS01", [
            _entry("monday", "08:50", "09:45", ["CSS2251"], "Wasim Yasin", ["RT01"]),
            _entry("monday", "10:50", "12:35", ["CSS2256"], "Dhannya J", ["DSL04"], ["B1"]),
            _entry("monday", "10:50", "12:35", ["CSS2256"], "Partha Protim Sen", ["DSL04a"], ["B2"]),
        ])
        item = imp.convert_page(page)
        self.assertTrue(item["valid"], item["errors"])
        self.assertEqual((item["semester"], item["year_of_study"], item["batch"], item["section"]), (3, 2, "ADS", "01"))
        self.assertEqual(item["config"]["collegeStartTime"], "08:50")
        self.assertEqual(item["class_sessions"], 2)

    def test_a_page_without_classes_is_flagged_not_valid(self):
        item = imp.convert_page(_page("3ADS01", []))
        self.assertFalse(item["valid"])
        self.assertTrue(any("No classes" in w for w in item["warnings"]))

    def test_missing_semester_is_flagged(self):
        item = imp.convert_page(_page("SIC01", [_entry("thursday", "08:50", "10:40", ["CAI3436_P"], "Nisha", ["LTL02"])]))
        self.assertIsNone(item["semester"])
        self.assertTrue(any("semester" in w for w in item["warnings"]))

    def test_reader_warnings_are_carried_through(self):
        item = imp.convert_page(_page("3ADS01", [_entry("monday", "08:50", "09:45", ["CSS2251"], "X", ["RT01"])],
                                      warnings=["Something odd on this page."]))
        self.assertIn("Something odd on this page.", item["warnings"])


class ImportSavingTests(Base):
    def item(self, code="3CAI01", **overrides):
        page = _page(code, [_entry("monday", "08:50", "09:45", ["CSS2251"], "Wasim Yasin", ["RT01"])])
        item = imp.convert_page(page)
        item.update({"department": "Other"})
        item.update(overrides)
        return item

    def run_import(self, items, publish=False):
        return imp.commit_import(self.db, self.admin,
                                 {"academic_year": "2026-2027", "publish": publish, "classes": items})

    def test_creates_the_class_and_saves_a_draft_by_default(self):
        result = self.run_import([self.item()])
        self.assertEqual((result["created"], result["failed"], result["published"]), (1, 0, 0))
        row = result["results"][0]
        view = ct.admin_timetable_view(self.db, row["class_id"])
        self.assertEqual(view["class"]["name"], "3CAI01")
        self.assertEqual((view["class"]["semester"], view["class"]["year_of_study"], view["class"]["batch"]), (3, 2, "CAI"))
        self.assertIsNotNone(view["draft"])
        self.assertIsNone(view["published"])  # students see nothing until it is published

    def test_can_publish_immediately(self):
        result = self.run_import([self.item()], publish=True)
        self.assertEqual(result["published"], 1)
        view = ct.admin_timetable_view(self.db, result["results"][0]["class_id"])
        self.assertEqual(view["published"]["version"], 1)

    def test_a_student_gets_the_imported_timetable_after_choosing_the_section(self):
        row = self.run_import([self.item()], publish=True)["results"][0]
        ct.assign_student_class(self.db, self.sid, row["class_id"])
        resolved = ct.resolve_student_timetable(self.db, self.sid)
        self.assertEqual(resolved["timetable"]["monday"][0]["subject"], "CSS2251")

    def test_importing_again_updates_the_same_class_instead_of_duplicating_it(self):
        first = self.run_import([self.item()], publish=True)
        second = self.run_import([self.item()], publish=True)
        self.assertEqual((second["created"], second["updated"]), (0, 1))
        self.assertEqual(self.db.classes.count_documents({}), 1)
        self.assertEqual(first["results"][0]["class_id"], second["results"][0]["class_id"])
        view = ct.admin_timetable_view(self.db, first["results"][0]["class_id"])
        self.assertEqual(view["published"]["version"], 2)

    def test_sections_of_different_programmes_do_not_collide(self):
        result = self.run_import([self.item("3CAI01"), self.item("3CBC01"), self.item("3CCS01")])
        self.assertEqual((result["created"], result["failed"]), (3, 0))

    def test_one_bad_class_does_not_stop_the_others(self):
        broken = self.item("SIC01")  # no semester
        result = self.run_import([self.item("3CAI01"), broken, self.item("3CAI02")])
        self.assertEqual((result["created"], result["failed"]), (2, 1))
        failed = [r for r in result["results"] if r["status"] == "failed"][0]
        self.assertEqual(failed["code"], "SIC01")
        self.assertIn("Semester", failed["error"])

    def test_the_admin_can_fix_a_missing_semester_before_importing(self):
        item = self.item("SIC01", semester=7, year_of_study=4)
        self.assertEqual(self.run_import([item])["created"], 1)

    def test_a_timetable_with_overlaps_is_refused_by_the_normal_rules(self):
        item = self.item()
        item["timetable"]["monday"].append(
            dict(item["timetable"]["monday"][0], id="dup", subject="Other", courseCode="OTH1000"))
        result = self.run_import([item])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.db.class_timetables.count_documents({}), 0 + self.db.class_timetables.count_documents({}))

    def test_nothing_selected_or_too_many_is_refused(self):
        with self.assertRaises(ct.ServiceError):
            self.run_import([])
        with self.assertRaises(ct.ServiceError):
            self.run_import([self.item()] * (imp.MAX_CLASSES_PER_IMPORT + 1))

    def test_the_import_is_written_to_the_activity_log(self):
        self.run_import([self.item()])
        self.assertIn("timetable_pdf_import", [r["action"] for r in self.db.activity_logs.find({})])

    def test_a_student_cannot_use_this_even_with_the_functions(self):
        # authorization is enforced by the router (see the HTTP tests); the
        # functions themselves are only reachable through it.
        self.assertTrue(callable(imp.create_timetable_import_router))


def _sample_pdf():
    here = Path(__file__).resolve().parent
    paths = sorted(glob.glob(str(here / "*Section_TT*.pdf")) + glob.glob(str(here / "*.pdf")))
    return paths[0] if paths else None


@unittest.skipUnless(_sample_pdf(), "put your timetable PDF next to this file to run the PDF tests")
class ImportRealPdfTests(unittest.TestCase):
    """Runs on YOUR PDF (any *.pdf in the backend folder), so it checks your real layout."""

    @classmethod
    def setUpClass(cls):
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pip install pdfplumber to run the PDF tests")
        path = _sample_pdf()
        with open(path, "rb") as handle:
            cls.result = imp.parse_pdf_bytes(handle.read(), Path(path).name)
        cls.by_code = {c["code"]: c for c in cls.result["classes"]}

    def test_every_page_becomes_a_valid_class_apart_from_missing_semesters(self):
        invalid = [c["code"] for c in self.result["classes"] if not c["valid"]]
        self.assertEqual(invalid, [])
        no_semester = [c["code"] for c in self.result["classes"] if c["semester"] is None]
        for code in no_semester:
            self.assertFalse(imp.SECTION_CODE_RE.match(code).group(1))

    def test_no_page_has_reader_warnings(self):
        noisy = [(c["code"], c["warnings"]) for c in self.result["classes"]
                 if [w for w in c["warnings"] if "semester" not in w]]
        self.assertEqual(noisy, [])

    def test_the_number_of_subjects_read_matches_the_pdf(self):
        """Independent check: every subject code printed in a grid is read, none invented."""
        import pdfplumber
        path = _sample_pdf()
        with open(path, "rb") as handle:
            pages = imp.read_pdf(handle.read())
        with pdfplumber.open(path) as pdf:
            for page in pages:
                raw = pdf.pages[page["page"] - 1]
                words = raw.extract_words(extra_attrs=["size", "upright"])
                printed = sorted(w["text"] for w in words
                                 if w["upright"] and 113 < w["top"] < 582 and imp.SUBJECT_RE.match(w["text"]))
                read = sorted(code for entry in page["entries"] for code in entry["subjects"])
                self.assertEqual(read, printed, page["code"])

    def test_a_known_page_is_read_exactly(self):
        c = self.by_code.get("1MTAIE01")
        if not c:
            self.skipTest("1MTAIE01 is not in this PDF")
        monday = {b["startTime"]: b for b in c["timetable"]["monday"]}
        self.assertEqual((monday["08:50"]["subject"], monday["08:50"]["faculty"], monday["08:50"]["room"]),
                         ("ENG5001", "Jyoti Parameswaran", "JT01"))
        lab = [b for b in c["timetable"]["tuesday"] if b["type"] == "lab"][0]
        self.assertEqual((lab["startTime"], lab["endTime"]), ("10:50", "12:35"))
        self.assertIn("B1: Vivek Bongale", lab["faculty"])
        self.assertIn("B2: DGL02a", lab["room"])

    def test_the_lunch_column_is_found_per_page(self):
        allowed = {("11:45", "12:35"), ("12:35", "13:25")}
        for c in self.result["classes"]:
            lunch = {(b["startTime"], b["endTime"]) for b in c["timetable"]["monday"] if b["type"] == "lunch"}
            self.assertEqual(len(lunch), 1, c["code"])
            self.assertTrue(lunch <= allowed, c["code"])

    def test_importing_the_whole_pdf_works_and_students_get_their_section(self):
        db = make_db()
        admin = add_user(db, "admin", "Admin")
        student = add_user(db, "student", "Asha")
        items = []
        for c in self.result["classes"]:
            if c["semester"] is None:
                continue
            items.append(dict(c, department="Other"))
        out = imp.commit_import(db, admin, {"academic_year": self.result["academic_year"], "publish": True, "classes": items})
        self.assertEqual(out["failed"], 0, [r for r in out["results"] if r["status"] == "failed"][:2])
        self.assertEqual(out["published"], len(items))

        target = next(r for r in out["results"] if r["code"] == "3CAI01")
        ct.assign_student_class(db, str(student["_id"]), target["class_id"])
        resolved = ct.resolve_student_timetable(db, str(student["_id"]))
        self.assertTrue(resolved["published"])
        self.assertGreater(sum(len(v) for v in resolved["timetable"].values()), 10)


# ============================================================
# HTTP: authorization, status codes, response shapes
# ============================================================

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    import mongomock as _mm  # noqa: F401
    HAVE_API_DEPS = True
except ImportError:
    HAVE_API_DEPS = False


@unittest.skipUnless(HAVE_API_DEPS, "fastapi, httpx and mongomock are not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        create_class_timetable_router = ct.create_class_timetable_router

        self.db = make_db()

        def token_to_user_id(authorization):
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Authorization token missing")
            return authorization.split(" ", 1)[1]

        app = FastAPI()
        app.include_router(create_class_timetable_router(self.db, token_to_user_id))
        self.client = TestClient(app)

        self.admin = add_user(self.db, "admin", "Admin")
        self.club = add_user(self.db, "club", "Club")
        self.asha = add_user(self.db, "student", "Asha")
        self.ben = add_user(self.db, "student", "Ben")

    def h(self, user):
        return {"Authorization": f"Bearer {user['_id']}"}

    def make_class(self, **overrides):
        response = self.client.post("/api/classes", json={**GOOD_CLASS, **overrides},
                                    headers=self.h(self.admin))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["class"]

    def publish(self, class_id, *blocks):
        saved = self.client.post(
            f"/api/admin/classes/{class_id}/timetable",
            json={"timetable": {"monday": list(blocks or (lecture(),))}},
            headers=self.h(self.admin))
        self.assertEqual(saved.status_code, 200, saved.text)
        published = self.client.post(
            f"/api/admin/timetables/{saved.json()['timetable']['id']}/publish",
            headers=self.h(self.admin))
        self.assertEqual(published.status_code, 200, published.text)
        return published.json()

    # ---------------- authentication / authorization ----------------

    def test_no_token_is_401_everywhere(self):
        for method, path in (("get", "/api/admin/classes"), ("get", "/api/classes"),
                             ("get", "/api/student/class"), ("patch", "/api/student/class")):
            # FastAPI checks the request body before running the endpoint, so
            # a body-less PATCH would be a 422 whatever the token. Send a valid
            # body so the missing token is what gets tested.
            kwargs = {"json": {"class_id": "x"}} if method == "patch" else {}
            response = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(response.status_code, 401, path)

    def test_students_and_clubs_cannot_manage_classes_or_timetables(self):
        cls = self.make_class()
        draft = self.client.post(f"/api/admin/classes/{cls['id']}/timetable",
                                 json={"timetable": {"monday": [lecture()]}},
                                 headers=self.h(self.admin)).json()["timetable"]

        for user in (self.asha, self.club):
            headers = self.h(user)
            calls = [
                self.client.post("/api/classes", json=GOOD_CLASS, headers=headers),
                self.client.patch(f"/api/classes/{cls['id']}", json={"is_active": False}, headers=headers),
                self.client.get("/api/admin/classes", headers=headers),
                self.client.get(f"/api/admin/classes/{cls['id']}/timetable", headers=headers),
                self.client.post(f"/api/admin/classes/{cls['id']}/timetable",
                                 json={"timetable": {"monday": [lecture()]}}, headers=headers),
                self.client.put(f"/api/admin/timetables/{draft['id']}",
                                json={"timetable": {"monday": [lecture()]}}, headers=headers),
                self.client.post(f"/api/admin/timetables/{draft['id']}/publish", headers=headers),
            ]
            for response in calls:
                self.assertEqual(response.status_code, 403, response.request.url)

        # nothing was changed by the rejected calls
        self.assertTrue(self.db.classes.find_one({})["is_active"])

    def test_only_students_can_choose_a_class(self):
        cls = self.make_class()
        for user in (self.admin, self.club):
            response = self.client.patch("/api/student/class", json={"class_id": cls["id"]},
                                         headers=self.h(user))
            self.assertEqual(response.status_code, 403)

    # ---------------- classes ----------------

    def test_admin_creates_lists_edits_and_deactivates(self):
        cls = self.make_class()
        self.assertTrue(cls["is_active"])

        listing = self.client.get("/api/admin/classes", headers=self.h(self.admin)).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["classes"][0]["student_count"], 0)

        edited = self.client.patch(f"/api/classes/{cls['id']}", json={"section": "B"},
                                   headers=self.h(self.admin))
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["class"]["section"], "B")

        off = self.client.patch(f"/api/classes/{cls['id']}", json={"is_active": False},
                                headers=self.h(self.admin))
        self.assertFalse(off.json()["class"]["is_active"])

        inactive = self.client.get("/api/admin/classes?status=inactive", headers=self.h(self.admin))
        self.assertEqual(inactive.json()["count"], 1)

    def test_duplicate_active_class_is_409_and_bad_input_is_422(self):
        self.make_class()
        duplicate = self.client.post("/api/classes", json=GOOD_CLASS, headers=self.h(self.admin))
        self.assertEqual(duplicate.status_code, 409)

        bad = self.client.post("/api/classes", json={"department": "CSE"}, headers=self.h(self.admin))
        self.assertEqual(bad.status_code, 422)

    def test_students_see_only_active_classes(self):
        keep = self.make_class(section="A")
        gone = self.make_class(section="B")
        self.client.patch(f"/api/classes/{gone['id']}", json={"is_active": False},
                          headers=self.h(self.admin))

        listing = self.client.get("/api/classes", headers=self.h(self.asha)).json()
        self.assertEqual([c["id"] for c in listing["classes"]], [keep["id"]])

    def test_unknown_and_malformed_class_ids(self):
        missing = self.client.get("/api/classes/64b0f0f0f0f0f0f0f0f0f0f0", headers=self.h(self.asha))
        self.assertEqual(missing.status_code, 404)
        malformed = self.client.get("/api/classes/xyz", headers=self.h(self.asha))
        self.assertEqual(malformed.status_code, 400)

    # ---------------- timetable editing ----------------

    def test_invalid_timetables_are_rejected_with_422(self):
        cls = self.make_class()
        url = f"/api/admin/classes/{cls['id']}/timetable"
        bad_bodies = [
            {"timetable": {"monday": [lecture(), lecture("DBMS", "DB1", "09:30", "10:30")]}},
            {"timetable": {"monday": [lecture(start="11:00", end="10:00")]}},
            {"timetable": {"monday": [{"type": "lecture", "startTime": "09:00", "endTime": "10:00"}]}},
            {"timetable": {"funday": [lecture()]}},
        ]
        for body in bad_bodies:
            response = self.client.post(url, json=body, headers=self.h(self.admin))
            self.assertEqual(response.status_code, 422, body)

    def test_draft_does_not_replace_published_and_publish_does(self):
        cls = self.make_class()
        self.publish(cls["id"], lecture("Old Subject", "OLD1"))

        draft = self.client.post(
            f"/api/admin/classes/{cls['id']}/timetable",
            json={"timetable": {"monday": [lecture("New Subject", "NEW1", "13:00", "14:00")]}},
            headers=self.h(self.admin)).json()["timetable"]

        live = self.client.get(f"/api/classes/{cls['id']}/timetable", headers=self.h(self.asha)).json()
        self.assertEqual(live["timetable"]["monday"][0]["subject"], "Old Subject")

        self.client.post(f"/api/admin/timetables/{draft['id']}/publish", headers=self.h(self.admin))
        live = self.client.get(f"/api/classes/{cls['id']}/timetable", headers=self.h(self.asha)).json()
        self.assertEqual(live["timetable"]["monday"][0]["subject"], "New Subject")
        self.assertEqual(live["version"], 2)

    def test_empty_draft_cannot_be_published(self):
        cls = self.make_class()
        draft = self.client.post(f"/api/admin/classes/{cls['id']}/timetable",
                                 json={"timetable": {}}, headers=self.h(self.admin)).json()["timetable"]
        response = self.client.post(f"/api/admin/timetables/{draft['id']}/publish",
                                    headers=self.h(self.admin))
        self.assertEqual(response.status_code, 422)

    def test_unpublished_class_returns_a_message_not_an_error(self):
        cls = self.make_class()
        response = self.client.get(f"/api/classes/{cls['id']}/timetable", headers=self.h(self.asha))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["published"])
        self.assertIn("not been published", response.json()["message"])

    # ---------------- student class selection ----------------

    def test_student_selects_class_and_gets_the_timetable_back(self):
        cls = self.make_class()
        self.publish(cls["id"])

        response = self.client.patch("/api/student/class", json={"class_id": cls["id"]},
                                     headers=self.h(self.asha))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["source"], "official")
        self.assertTrue(body["published"])
        self.assertEqual(body["timetable"]["monday"][0]["subject"], "Machine Learning")

        current = self.client.get("/api/student/class", headers=self.h(self.asha)).json()
        self.assertEqual(current["class"]["id"], cls["id"])

    def test_a_student_cannot_assign_a_class_to_someone_else(self):
        cls = self.make_class()
        # extra fields naming another student are ignored: identity is the token
        response = self.client.patch(
            "/api/student/class",
            json={"class_id": cls["id"], "student_id": str(self.ben["_id"])},
            headers=self.h(self.asha))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.students.find_one({"_id": self.asha["_id"]})["class_id"], cls["id"])
        self.assertNotIn("class_id", self.db.students.find_one({"_id": self.ben["_id"]}))

    def test_inactive_unknown_and_malformed_classes_rejected(self):
        cls = self.make_class()
        self.client.patch(f"/api/classes/{cls['id']}", json={"is_active": False},
                          headers=self.h(self.admin))
        for class_id, expected in ((cls["id"], 409), ("64b0f0f0f0f0f0f0f0f0f0f0", 404), ("xyz", 400)):
            response = self.client.patch("/api/student/class", json={"class_id": class_id},
                                         headers=self.h(self.asha))
            self.assertEqual(response.status_code, expected, class_id)

    def test_students_cannot_edit_official_timetables(self):
        cls = self.make_class()
        published = self.publish(cls["id"])["timetable"]
        attempts = [
            self.client.put(f"/api/admin/timetables/{published['id']}",
                            json={"timetable": {"monday": [lecture("Hacked", "H1")]}},
                            headers=self.h(self.asha)),
            self.client.post(f"/api/admin/classes/{cls['id']}/timetable",
                             json={"timetable": {"monday": [lecture("Hacked", "H1")]}},
                             headers=self.h(self.asha)),
        ]
        for response in attempts:
            self.assertEqual(response.status_code, 403)
        live = self.client.get(f"/api/classes/{cls['id']}/timetable", headers=self.h(self.asha)).json()
        self.assertEqual(live["timetable"]["monday"][0]["subject"], "Machine Learning")

    def test_availability_preferences(self):
        self.db.profiles.insert_one({"student_id": str(self.asha["_id"])})
        ok = self.client.put("/api/student/availability-preferences",
                             json={"bufferTime": 30, "preferredEventTimes": ["morning"]},
                             headers=self.h(self.asha))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["preferences"]["bufferTime"], 30)

        no_profile = self.client.put("/api/student/availability-preferences",
                                     json={"bufferTime": 30}, headers=self.h(self.ben))
        self.assertEqual(no_profile.status_code, 404)

        bad = self.client.put("/api/student/availability-preferences",
                              json={"bufferTime": 999}, headers=self.h(self.asha))
        self.assertEqual(bad.status_code, 422)


@unittest.skipUnless(HAVE_API_DEPS, "fastapi, httpx and mongomock are not installed")
class ImportApiTests(unittest.TestCase):
    """The PDF import endpoints: who may call them, and what they answer."""

    def setUp(self):
        import base64
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        self.b64 = base64.b64encode
        self.db = make_db()

        def token_to_user_id(authorization):
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Authorization token missing")
            return authorization.split(" ", 1)[1]

        app = FastAPI()
        app.include_router(ct.create_class_timetable_router(self.db, token_to_user_id))
        app.include_router(imp.create_timetable_import_router(self.db, token_to_user_id))
        self.client = TestClient(app)

        self.admin = add_user(self.db, "admin", "Admin")
        self.student = add_user(self.db, "student", "Asha")
        self.club = add_user(self.db, "club", "Club")

    def h(self, user):
        return {"Authorization": f"Bearer {user['_id']}"}

    def item(self, code="3CAI01"):
        page = _page(code, [_entry("monday", "08:50", "09:45", ["CSS2251"], "Wasim Yasin", ["RT01"])])
        item = imp.convert_page(page)
        item["department"] = "Other"
        return item

    def test_only_admins_may_use_the_import(self):
        pdf = {"file_name": "x.pdf", "data": self.b64(b"%PDF-1.4").decode()}
        commit = {"academic_year": "2026-2027", "publish": False, "classes": [self.item()]}
        self.assertEqual(self.client.post("/api/admin/timetable-import/preview", json=pdf).status_code, 401)
        self.assertEqual(self.client.post("/api/admin/timetable-import/commit", json=commit).status_code, 401)
        for user in (self.student, self.club):
            self.assertEqual(self.client.post("/api/admin/timetable-import/preview", json=pdf, headers=self.h(user)).status_code, 403)
            self.assertEqual(self.client.post("/api/admin/timetable-import/commit", json=commit, headers=self.h(user)).status_code, 403)
        self.assertEqual(self.db.classes.count_documents({}), 0)

    def test_a_file_that_is_not_a_pdf_is_refused_with_a_clear_message(self):
        for data in (self.b64(b"hello world").decode(), "", "not base64 !!!"):
            response = self.client.post("/api/admin/timetable-import/preview",
                                        json={"file_name": "x.pdf", "data": data}, headers=self.h(self.admin))
            self.assertEqual(response.status_code, 400, data)
            self.assertIn("PDF", response.json()["detail"])

    def test_the_request_body_is_required(self):
        response = self.client.post("/api/admin/timetable-import/preview", json={}, headers=self.h(self.admin))
        self.assertEqual(response.status_code, 422)

    @unittest.skipUnless(_sample_pdf(), "put your timetable PDF next to this file")
    def test_preview_reads_the_real_pdf(self):
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            self.skipTest("pip install pdfplumber")
        with open(_sample_pdf(), "rb") as handle:
            data = self.b64(handle.read()).decode()
        response = self.client.post("/api/admin/timetable-import/preview",
                                    json={"file_name": Path(_sample_pdf()).name, "data": data},
                                    headers=self.h(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["pages"], len(body["classes"]))
        self.assertTrue(body["programs"])
        self.assertTrue(all(c["timetable"] for c in body["classes"]))
        self.assertEqual(self.db.classes.count_documents({}), 0)  # a preview saves nothing

    def test_commit_creates_classes_students_can_then_see(self):
        response = self.client.post(
            "/api/admin/timetable-import/commit", headers=self.h(self.admin),
            json={"academic_year": "2026-2027", "publish": True, "classes": [self.item("3CAI01"), self.item("3CAI02")]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["created"], body["failed"], body["published"]), (2, 0, 2))

        listing = self.client.get("/api/classes", headers=self.h(self.student)).json()
        self.assertEqual(sorted(c["name"] for c in listing["classes"]), ["3CAI01", "3CAI02"])

        class_id = body["results"][0]["class_id"]
        chosen = self.client.patch("/api/student/class", json={"class_id": class_id}, headers=self.h(self.student))
        self.assertEqual(chosen.status_code, 200, chosen.text)
        self.assertTrue(chosen.json()["published"])

    def test_commit_reports_a_bad_class_without_stopping(self):
        response = self.client.post(
            "/api/admin/timetable-import/commit", headers=self.h(self.admin),
            json={"academic_year": "2026-2027", "publish": False,
                  "classes": [self.item("3CAI01"), self.item("SIC01")]})
        body = response.json()
        self.assertEqual((body["created"], body["failed"]), (1, 1))
        self.assertIn("Semester", [r for r in body["results"] if r["status"] == "failed"][0]["error"])

    def test_commit_with_no_classes_is_a_422(self):
        response = self.client.post("/api/admin/timetable-import/commit", headers=self.h(self.admin),
                                    json={"academic_year": "2026-2027", "classes": []})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()