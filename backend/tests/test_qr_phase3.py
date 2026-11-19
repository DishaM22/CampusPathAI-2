"""
Phase 3 tests (Attendance Pass for No Registration Required events).
Run from backend/:   pytest tests/ -v          (uses mongomock; no real database is touched)
"""
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mongomock
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from qr_system import Adapters, QrError, QrService, TokenVault
from qr_system.routes import build_router


def future_date(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


class Base(unittest.TestCase):
    use_provider = False

    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.blocked = set()
        self.provider_calls = 0
        adapters = Adapters(is_student_eligible=lambda event, sid: sid not in self.blocked)
        if self.use_provider:
            def provider(event):
                self.provider_calls += 1
                return [str(s["_id"]) for s in self.db.students.find({"status": {"$ne": "disabled"}})
                        if s.get("role") == "student" and str(s["_id"]) not in self.blocked]
            adapters.eligible_student_ids = provider
        self.marked = []
        self.svc = QrService(self.db, TokenVault("unit-test-secret"), adapters,
                             svg_renderer=lambda t: f"<svg>{t}</svg>",
                             on_attendance_marked=lambda s, e, ev: self.marked.append((s, e)))
        self.svc.ensure_indexes()
        self.students = [self._user("student", f"S{i}", otp_verified_at="x") for i in range(4)]
        self.leader = self._user("club", "Leader", club_id="club-1")
        self.other_leader = self._user("club", "Other", club_id="club-2")
        self.admin = self._user("admin", "Admin")
        self.reg_event = self._event(title="Hackathon")                                  # registration required
        self.open_event = self._event(title="Open Talk", registration_required=False)    # No Registration Required
        self.open_event2 = self._event(title="Open Talk 2", registration_required=False)

    def _user(self, role, name, **extra):
        doc = {"name": name, "email": f"{name}@x.edu", "role": role, "status": "active", **extra}
        return str(self.db.students.insert_one(doc).inserted_id)

    def _event(self, **kw):
        doc = {"title": "E", "date": future_date(), "time": "10:00", "created_by": self.leader,
               "club_id": "club-1", "approval_status": "published", **kw}
        return str(self.db.events.insert_one(doc).inserted_id)

    def _sync(self, eid, **kw):
        return self.svc.sync_event_passes(eid, **kw)

    def _pass_token(self, sid, eid):
        return self.svc.get_event_qr_for_student(sid, eid)["qr_data"]

    def _code(self, token, eid, scanner=None):
        with self.assertRaises(QrError) as ctx:
            self.svc.scan(token, eid, scanner or self.leader)
        return ctx.exception.code

    def _count(self, **q):
        return self.db.event_attendance_passes.count_documents(q)


class PassTests:
    """Runs twice: with the per-student fallback and with the fast eligible-ids provider."""

    def test_generated_for_every_eligible_student_on_publication(self):
        self.blocked.add(self.students[3])
        stats = self._sync(self.open_event)
        self.assertEqual((stats["created"], stats["eligible"]), (3, 3))
        self.assertEqual(self._count(event_id=self.open_event), 3)
        self.assertEqual(self._count(student_id=self.students[3]), 0)
        self.assertEqual(self._count(student_id=self.leader), 0)          # staff never get passes
        self.assertEqual(self._count(student_id=self.admin), 0)

    def test_one_unique_pass_per_student_per_event(self):
        self._sync(self.open_event); self._sync(self.open_event2)
        tokens = [p["token_hash"] for p in self.db.event_attendance_passes.find({})]
        self.assertEqual(len(tokens), 8)
        self.assertEqual(len(set(tokens)), 8)

    def test_repeated_jobs_create_no_duplicates_and_keep_tokens(self):
        self._sync(self.open_event)
        before = {p["student_id"]: p["token_hash"] for p in self.db.event_attendance_passes.find({})}
        for batch in (500, 2, 1):
            self.assertEqual(self._sync(self.open_event, batch_size=batch)["created"], 0)
        for _ in range(3):
            self.svc.run_job(self.svc.enqueue_pass_job(self.open_event, run_async=False))
        after = {p["student_id"]: p["token_hash"] for p in self.db.event_attendance_passes.find({})}
        self.assertEqual(before, after)

    def test_background_job_records_status(self):
        job = self.svc.on_event_published(self.open_event, run_async=False)
        self.svc.run_job(job)
        info = self.svc.get_job(job)
        self.assertEqual((info["status"], info["stats"]["created"]), ("done", 4))

    def test_job_that_was_interrupted_runs_again(self):
        job = self.svc.enqueue_pass_job(self.open_event, run_async=False)
        self.db.qr_generation_jobs.update_one({"_id": ObjectId(job)}, {"$set": {"status": "running"}})
        self.svc.resume_pending_jobs()
        for _ in range(100):
            if self.svc.get_job(job)["status"] == "done":
                break
            time.sleep(0.02)
        self.assertEqual(self.svc.get_job(job)["status"], "done")
        self.assertEqual(self._count(event_id=self.open_event), 4)

    def test_unique_index_blocks_duplicate_student_event_pass(self):
        self._sync(self.open_event)
        with self.assertRaises(DuplicateKeyError):
            self.db.event_attendance_passes.insert_one(
                {"student_id": self.students[0], "event_id": self.open_event, "token_hash": "zzz"})

    def test_many_students_in_batches(self):
        for i in range(120):
            self._user("student", f"Bulk{i}")
        self.assertEqual(self._sync(self.open_event, batch_size=25)["created"], 124)
        self.assertEqual(self._count(event_id=self.open_event), 124)

    def test_nothing_for_registration_required_or_unpublished_events(self):
        self.assertEqual(self._sync(self.reg_event)["skipped"], "registration_required")
        draft = self._event(registration_required=False, approval_status="pending_admin")
        self.assertEqual(self._sync(draft)["skipped"], "not_published")
        self.assertEqual(self._count(), 0)

    def test_registration_mode_field_variants(self):
        by_mode = self._event(registration_mode="No Registration Required")
        self.assertEqual(self._sync(by_mode)["created"], 4)
        legacy = self._event()                                 # no field at all = registration required
        self.assertEqual(self._sync(legacy)["skipped"], "registration_required")

    def test_scan_marks_attendance_once_and_rejects_duplicate(self):
        self._sync(self.open_event)
        sid = self.students[0]
        token = self._pass_token(sid, self.open_event)
        ok = self.svc.scan(token, self.open_event, self.leader)
        self.assertEqual((ok["success"], ok["qr_type"], ok["student_id"]), (True, "event_attendance_pass", sid))
        self.assertEqual(self.marked, [(sid, self.open_event)])
        self.assertEqual(self._code(token, self.open_event), "ALREADY_ATTENDED")
        self.assertEqual(self.db.attendance.count_documents({"student_id": sid}), 1)
        self.assertEqual(self.db.event_attendance_passes.find_one({"student_id": sid, "event_id": self.open_event})["status"], "used")

    def test_pass_only_works_for_its_own_event(self):
        self._sync(self.open_event); self._sync(self.open_event2)
        token = self._pass_token(self.students[0], self.open_event)
        self.assertEqual(self._code(token, self.open_event2), "WRONG_EVENT")
        self.assertEqual(self.db.attendance.count_documents({}), 0)

    def test_eligibility_rechecked_at_scan_then_pass_suspended(self):
        self._sync(self.open_event)
        sid = self.students[0]
        token = self._pass_token(sid, self.open_event)
        self.blocked.add(sid)
        self.assertEqual(self._code(token, self.open_event), "NOT_ELIGIBLE")
        self.assertEqual(self.db.event_attendance_passes.find_one({"student_id": sid})["status"], "suspended")
        self.assertEqual(self.db.attendance.count_documents({}), 0)
        self.blocked.discard(sid)
        self.assertTrue(self.svc.scan(token, self.open_event, self.leader)["success"])

    def test_sync_suspends_ineligible_and_creates_for_newly_eligible(self):
        self._sync(self.open_event)
        gone, newcomer = self.students[0], self._user("student", "New")
        self.blocked.update({gone, newcomer})
        stats = self._sync(self.open_event)
        self.assertEqual(stats["suspended"], 1)
        self.assertEqual(self._count(student_id=newcomer), 0)
        self.blocked.discard(newcomer)                         # becomes eligible AFTER publication
        self.assertEqual(self._sync(self.open_event)["created"], 1)
        self.blocked.discard(gone)                             # and the first one returns
        self.assertEqual(self._sync(self.open_event)["reactivated"], 1)
        self.assertEqual(self.db.event_attendance_passes.find_one({"student_id": gone})["status"], "active")

    def test_changing_the_audience_after_publication(self):
        self._sync(self.open_event)
        self.blocked.update(self.students[:2])                 # Admin narrowed the audience
        self.svc.run_job(self.svc.on_event_eligibility_changed(self.open_event, run_async=False))
        statuses = {p["student_id"]: p["status"] for p in self.db.event_attendance_passes.find({})}
        self.assertEqual([statuses[s] for s in self.students], ["suspended", "suspended", "active", "active"])

    def test_promotion_refreshes_individual_students(self):
        self._sync(self.open_event)
        sid = self._user("student", "Late"); self.blocked.add(sid)
        self._sync(self.open_event)
        self.blocked.discard(sid)
        stats = self.svc.on_students_eligibility_changed([sid], run_async=False)
        self.assertEqual(stats["created"], 2)                  # open_event + open_event2
        self.blocked.add(sid)
        self.assertEqual(self.svc.on_students_eligibility_changed([sid], run_async=False)["suspended"], 2)

    def test_disabled_students_lose_their_pass(self):
        self._sync(self.open_event)
        self.db.students.update_one({"_id": ObjectId(self.students[0])}, {"$set": {"status": "disabled"}})
        self.assertEqual(self._sync(self.open_event)["suspended"], 1)

    def test_cancelled_event_kills_unused_passes_but_keeps_history(self):
        self._sync(self.open_event)
        used_sid, unused_sid = self.students[0], self.students[1]
        used, unused = self._pass_token(used_sid, self.open_event), self._pass_token(unused_sid, self.open_event)
        self.svc.scan(used, self.open_event, self.leader)
        self.db.events.update_one({"_id": ObjectId(self.open_event)}, {"$set": {"status": "cancelled"}})
        self.svc.on_event_cancelled(self.open_event)
        self.assertEqual(self._code(unused, self.open_event), "EVENT_CANCELLED")
        status = {p["student_id"]: p["status"] for p in self.db.event_attendance_passes.find({"event_id": self.open_event})}
        self.assertEqual((status[used_sid], status[unused_sid]), ("used", "cancelled"))
        self.assertEqual(self.db.attendance.count_documents({"student_id": used_sid}), 1)
        self.assertEqual(self._sync(self.open_event)["skipped"], "event_cancelled")     # never resurrected

    def test_history_survives_losing_eligibility(self):
        self._sync(self.open_event)
        sid = self.students[0]
        self.svc.scan(self._pass_token(sid, self.open_event), self.open_event, self.leader)
        self.blocked.add(sid)
        self._sync(self.open_event)
        self.assertEqual(self.db.attendance.count_documents({"student_id": sid}), 1)
        self.assertEqual(self.db.event_attendance_passes.find_one({"student_id": sid})["status"], "used")

    def test_event_under_reapproval_pauses_then_resumes(self):
        self._sync(self.open_event)
        token = self._pass_token(self.students[0], self.open_event)
        self.db.events.update_one({"_id": ObjectId(self.open_event)}, {"$set": {"approval_status": "pending_faculty"}})
        self.assertEqual(self._code(token, self.open_event), "EVENT_NOT_PUBLISHED")
        shown = {i["event_id"] for i in self.svc.list_student_event_qrs(self.students[0])}
        self.assertNotIn(self.open_event, shown)                                      # hidden while pending
        self.db.events.update_one({"_id": ObjectId(self.open_event)}, {"$set": {"approval_status": "published"}})
        self.assertTrue(self.svc.scan(token, self.open_event, self.leader)["success"])  # same pass works again

    def test_pass_void_if_event_becomes_registration_required(self):
        self._sync(self.open_event)
        token = self._pass_token(self.students[0], self.open_event)
        self.db.events.update_one({"_id": ObjectId(self.open_event)}, {"$set": {"registration_required": True}})
        self.assertEqual(self._code(token, self.open_event), "PASS_NOT_APPLICABLE")
        self.assertEqual(self._sync(self.open_event)["skipped"], "registration_required")
        self.assertEqual(self._count(event_id=self.open_event, status="active"), 0)

    def test_expired_pass_is_rejected(self):
        old = self._event(registration_required=False, date="2020-01-01")
        self._sync(old)
        self.assertEqual(self._code(self._pass_token(self.students[0], old), old), "QR_EXPIRED")

    def test_pass_has_no_personal_data_and_only_a_hash_is_stored(self):
        self._sync(self.open_event)
        sid = self.students[0]
        token = self._pass_token(sid, self.open_event)
        for private in (sid, "S0", "S0@x.edu", self.open_event):
            self.assertNotIn(private, token)
        stored = self.db.event_attendance_passes.find_one({"student_id": sid})
        self.assertNotIn(token, str(stored))
        self.assertEqual(stored["token_hash"], self.svc.vault.hash("event_attendance_pass", token))

    def test_only_owner_or_admin_can_scan_a_pass(self):
        self._sync(self.open_event)
        token = self._pass_token(self.students[0], self.open_event)
        self.assertEqual(self._code(token, self.open_event, self.other_leader), "SCANNER_NOT_ALLOWED")
        self.assertTrue(self.svc.scan(token, self.open_event, self.admin)["success"])

    def test_identity_and_registration_codes_are_not_passes(self):
        self._sync(self.open_event)
        sid = self.students[0]
        self.db.students.update_one({"_id": ObjectId(sid)}, {"$set": {"otp_verified_at": "x"}})
        ident = self.svc.get_or_create_identity_qr(sid)["qr_data"]
        self.assertEqual(self._code(ident, self.open_event), "IDENTITY_QR_NOT_ACCEPTED")
        pass_token = self._pass_token(sid, self.open_event)
        forged = "CPQ-ER." + pass_token.split(".", 1)[1]        # a pass token dressed up as a registration QR
        self.assertEqual(self._code(forged, self.open_event), "INVALID_QR")

    def test_pass_is_created_lazily_for_a_late_eligible_student(self):
        late = self._user("student", "Late2")                   # no sync has run for them
        view = self.svc.get_event_qr_for_student(late, self.open_event)
        self.assertEqual(view["qr_type"], "event_attendance_pass")

    def test_ineligible_student_cannot_view_a_pass(self):
        self.blocked.add(self.students[0])
        with self.assertRaises(QrError) as ctx:
            self.svc.get_event_qr_for_student(self.students[0], self.open_event)
        self.assertEqual(ctx.exception.code, "NOT_ELIGIBLE")

    def test_my_events_lists_passes_and_registration_qrs_together(self):
        sid = self.students[0]
        self.db.event_registrations.insert_one({"student_id": sid, "event_id": self.reg_event, "status": "registered"})
        items = self.svc.list_student_event_qrs(sid)            # nothing synced yet: passes are made on demand
        kinds = sorted((i["qr_type"], i["event_id"]) for i in items)
        self.assertEqual(kinds, sorted([("event_registration", self.reg_event),
                                        ("event_attendance_pass", self.open_event),
                                        ("event_attendance_pass", self.open_event2)]))
        self.assertTrue(all(i["qr_svg"].startswith("<svg>") for i in items))
        self.assertEqual(self._count(student_id=sid), 2)         # no duplicates on a second call
        self.svc.list_student_event_qrs(sid)
        self.assertEqual(self._count(student_id=sid), 2)

    def test_my_events_hides_ineligible_expired_and_cancelled(self):
        sid = self.students[0]
        self._event(registration_required=False, date="2020-01-01")
        self._event(registration_required=False, status="cancelled")
        self.blocked.add(sid)
        self.assertEqual(self.svc.list_student_event_qrs(sid), [])


class TestPassesPerStudentFallback(PassTests, Base):
    use_provider = False


class TestPassesWithFastProvider(PassTests, Base):
    use_provider = True

    def test_provider_is_used_once_per_job_not_once_per_student(self):
        self._sync(self.open_event)
        self.assertEqual(self.provider_calls, 1)


class TestRoutes(Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_router(self.svc, lambda auth: auth.split()[1]))
        self.client = TestClient(app)

    def auth(self, uid):
        return {"authorization": f"Bearer {uid}"}

    def test_student_gets_a_pass_over_http(self):
        sid = self.students[0]
        r = self.client.get(f"/api/qr/events/{self.open_event}", self.auth(sid))
        self.assertEqual((r.status_code, r.json()["qr_type"]), (200, "event_attendance_pass"))
        items = self.client.get("/api/qr/my-events", self.auth(sid)).json()["items"]
        self.assertEqual({i["event_id"] for i in items}, {self.open_event, self.open_event2})

    def test_scan_over_http(self):
        sid = self.students[0]
        token = self.client.get(f"/api/qr/events/{self.open_event}", self.auth(sid)).json()["qr_data"]
        url = f"/api/qr/events/{self.open_event}/scan"
        self.assertEqual(self.client.post(url, self.auth(sid), {"qr_data": token}).status_code, 403)
        ok = self.client.post(url, self.auth(self.leader), {"qr_data": token})
        self.assertEqual((ok.status_code, ok.json()["success"]), (200, True))
        dup = self.client.post(url, self.auth(self.leader), {"qr_data": token})
        self.assertEqual((dup.status_code, dup.json()["code"]), (409, "ALREADY_ATTENDED"))

    def test_admin_generate_passes_and_job_status(self):
        url = f"/api/qr/admin/events/{self.open_event}/generate-passes"
        self.assertEqual(self.client.post(url, self.auth(self.leader)).status_code, 403)
        r = self.client.post(url, self.auth(self.admin))
        job_id = r.json()["job_id"]
        status = {}
        for _ in range(100):
            status = self.client.get(f"/api/qr/admin/jobs/{job_id}", self.auth(self.admin)).json()
            if status["status"] in ("done", "failed"):
                break
            time.sleep(0.03)
        self.assertEqual((status["status"], status["stats"]["created"]), ("done", 4))
        self.assertEqual(self.client.get(f"/api/qr/admin/jobs/{job_id}", self.auth(self.students[0])).status_code, 403)


if __name__ == "__main__":
    unittest.main()