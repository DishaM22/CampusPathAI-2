"""
Phase 2 tests (Event Registration QR).   Run from backend/:   pytest tests/ -v
Uses mongomock - no real database is touched.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mongomock
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from qr_system import Adapters, QrError, QrService, TokenVault, legacy_scan
from qr_system.routes import build_router


def future_date(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


class Base(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.blocked = set()                       # students the (fake) audience rule rejects
        self.marked = []
        self.svc = QrService(
            self.db, TokenVault("unit-test-secret"),
            Adapters(is_student_eligible=lambda event, sid: sid not in self.blocked),
            svg_renderer=lambda t: f"<svg>{t}</svg>",
            on_attendance_marked=lambda s, e, ev: self.marked.append((s, e)))
        self.svc.ensure_indexes()
        self.students = [self._user("student", f"S{i}", otp_verified_at="x") for i in range(3)]
        self.leader = self._user("club", "Leader", club_id="club-1")
        self.other_leader = self._user("club", "Other", club_id="club-2")
        self.admin = self._user("admin", "Admin")
        self.event = self._event(title="Hackathon")
        self.event2 = self._event(title="Workshop")

    def _user(self, role, name, **extra):
        doc = {"name": name, "email": f"{name}@x.edu", "role": role, "status": "active", **extra}
        return str(self.db.students.insert_one(doc).inserted_id)

    def _event(self, **kw):
        doc = {"title": "E", "date": future_date(), "time": "10:00", "created_by": self.leader,
               "club_id": "club-1", "approval_status": "published", **kw}
        return str(self.db.events.insert_one(doc).inserted_id)

    def _register(self, sid, eid):
        return str(self.db.event_registrations.insert_one(
            {"student_id": sid, "event_id": eid, "status": "registered"}).inserted_id)

    def _cancel(self, sid, eid):
        self.db.event_registrations.update_one({"student_id": sid, "event_id": eid, "status": "registered"},
                                               {"$set": {"status": "cancelled"}})
        self.svc.on_registration_cancelled(sid, eid)

    def _qr(self, sid, eid):
        self._register(sid, eid)
        return self.svc.issue_registration_qr(sid, eid)["qr_data"]

    def _code(self, token, eid, scanner=None):
        with self.assertRaises(QrError) as ctx:
            self.svc.scan(token, eid, scanner or self.leader)
        return ctx.exception.code


class TestGeneration(Base):
    def test_only_after_a_successful_registration(self):
        sid = self.students[0]
        with self.assertRaises(QrError) as ctx:
            self.svc.issue_registration_qr(sid, self.event)
        self.assertEqual(ctx.exception.code, "NOT_REGISTERED")
        self.assertEqual(self.db.event_registration_qrs.count_documents({}), 0)
        rid = self._register(sid, self.event)
        view = self.svc.issue_registration_qr(sid, self.event)
        self.assertEqual((view["registration_id"], view["event_id"], view["status"]), (rid, self.event, "active"))

    def test_cancelled_registration_cannot_get_a_qr(self):
        sid = self.students[0]
        self._register(sid, self.event)
        self._cancel(sid, self.event)
        with self.assertRaises(QrError):
            self.svc.issue_registration_qr(sid, self.event)

    def test_hook_creates_the_qr_and_is_silent_when_not_registered(self):
        sid = self.students[0]
        self.assertIsNone(self.svc.on_event_registered(sid, self.event))          # not registered: no raise
        self._register(sid, self.event)
        self.assertIsNotNone(self.svc.on_event_registered(sid, self.event))
        self.assertEqual(self.db.event_registration_qrs.count_documents({}), 1)

    def test_no_registration_required_events_have_no_registration_qr(self):
        open_event = self._event(registration_required=False)
        sid = self.students[0]
        self._register(sid, open_event)
        with self.assertRaises(QrError) as ctx:
            self.svc.issue_registration_qr(sid, open_event)
        self.assertEqual(ctx.exception.code, "REGISTRATION_NOT_REQUIRED")
        self.svc.on_event_registered(sid, open_event)
        self.assertEqual(self.db.event_registration_qrs.count_documents({}), 0)
        mode = self._event(registration_mode="No Registration Required")
        self._register(sid, mode)
        with self.assertRaises(QrError):
            self.svc.issue_registration_qr(sid, mode)

    def test_every_registration_has_its_own_unique_qr(self):
        a, b = self._qr(self.students[0], self.event), self._qr(self.students[1], self.event)
        c = self._qr(self.students[0], self.event2)
        self.assertEqual(len({a, b, c}), 3)

    def test_no_duplicate_qr_for_the_same_registration(self):
        sid = self.students[0]
        self._register(sid, self.event)
        first = self.svc.issue_registration_qr(sid, self.event)["qr_data"]
        for _ in range(4):
            self.assertEqual(self.svc.issue_registration_qr(sid, self.event)["qr_data"], first)
        self.assertEqual(self.db.event_registration_qrs.count_documents({}), 1)

    def test_unique_indexes_block_duplicate_rows(self):
        sid = self.students[0]
        self._register(sid, self.event)
        view = self.svc.issue_registration_qr(sid, self.event)
        with self.assertRaises(DuplicateKeyError):
            self.db.event_registration_qrs.insert_one(
                {"registration_id": view["registration_id"], "token_hash": "other"})

    def test_ineligible_student_gets_no_qr(self):
        sid = self.students[0]
        self._register(sid, self.event)
        self.blocked.add(sid)
        with self.assertRaises(QrError) as ctx:
            self.svc.issue_registration_qr(sid, self.event)
        self.assertEqual(ctx.exception.code, "NOT_ELIGIBLE")

    def test_registrations_made_before_the_module_get_their_qr_lazily(self):
        sid = self.students[0]
        self._register(sid, self.event)            # no hook ran for this one
        items = self.svc.list_student_event_qrs(sid)
        self.assertEqual([i["event_id"] for i in items], [self.event])
        self.assertTrue(items[0]["qr_svg"].startswith("<svg>"))
        self.assertEqual(self.db.event_registration_qrs.count_documents({}), 1)

    def test_qr_has_no_personal_data_and_only_a_hash_is_stored(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        for private in (sid, "S0", "S0@x.edu", self.event):
            self.assertNotIn(private, token)
        stored = self.db.event_registration_qrs.find_one({"student_id": sid})
        self.assertNotIn(token, str(stored))
        self.assertEqual(stored["token_hash"], self.svc.vault.hash("event_registration", token))


class TestInvalidation(Base):
    def test_cancelled_registration_invalidates_the_qr(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        self._cancel(sid, self.event)
        self.assertEqual(self._code(token, self.event), "QR_CANCELLED")

    def test_cancellation_is_caught_even_if_the_hook_never_ran(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        self.db.event_registrations.update_one({"student_id": sid}, {"$set": {"status": "cancelled"}})
        self.assertEqual(self._code(token, self.event), "REGISTRATION_INACTIVE")

    def test_reregistering_gives_a_fresh_qr_and_the_old_one_stays_dead(self):
        sid = self.students[0]
        old = self._qr(sid, self.event)
        self._cancel(sid, self.event)
        new = self._qr(sid, self.event)
        self.assertNotEqual(old, new)
        self.assertEqual(self._code(old, self.event), "QR_CANCELLED")
        self.assertTrue(self.svc.scan(new, self.event, self.leader)["success"])

    def test_cancelled_event_invalidates_unused_qrs_and_keeps_used_ones(self):
        used_sid, unused_sid = self.students[0], self.students[1]
        used, unused = self._qr(used_sid, self.event), self._qr(unused_sid, self.event)
        self.svc.scan(used, self.event, self.leader)
        self.svc.on_event_cancelled(self.event)
        rows = {q["student_id"]: q["status"] for q in self.db.event_registration_qrs.find({})}
        self.assertEqual(rows, {used_sid: "used", unused_sid: "cancelled"})
        self.db.events.update_one({"_id": ObjectId(self.event)}, {"$set": {"status": "cancelled"}})
        self.assertEqual(self._code(unused, self.event), "EVENT_CANCELLED")
        self.assertEqual(self.db.attendance.count_documents({"student_id": used_sid}), 1)

    def test_expired_qr_is_rejected(self):
        old = self._event(date="2020-01-01")
        self.assertEqual(self._code(self._qr(self.students[0], old), old), "QR_EXPIRED")


class TestScanning(Base):
    def test_scan_marks_attendance_once_and_rejects_the_second_scan(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        ok = self.svc.scan(token, self.event, self.leader)
        self.assertEqual((ok["success"], ok["student_id"], ok["status"]), (True, sid, "success"))
        self.assertEqual(self.marked, [(sid, self.event)])
        self.assertEqual(self._code(token, self.event), "ALREADY_ATTENDED")
        self.assertEqual(self.db.attendance.count_documents({"student_id": sid}), 1)
        self.assertEqual(self.db.event_registration_qrs.find_one({"student_id": sid})["status"], "used")

    def test_student_already_marked_by_another_route_cannot_scan_again(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        self.db.attendance.insert_one({"event_id": self.event, "student_id": sid, "status": "present"})
        self.assertEqual(self._code(token, self.event), "ALREADY_ATTENDED")
        self.assertEqual(self.db.attendance.count_documents({"student_id": sid}), 1)

    def test_one_events_qr_never_works_for_another_event(self):
        token = self._qr(self.students[0], self.event)
        self.assertEqual(self._code(token, self.event2), "WRONG_EVENT")
        self.assertEqual(self.db.attendance.count_documents({}), 0)

    def test_eligibility_is_rechecked_at_scan_time(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        self.blocked.add(sid)
        self.assertEqual(self._code(token, self.event), "NOT_ELIGIBLE")
        self.assertEqual(self.db.attendance.count_documents({}), 0)
        self.blocked.discard(sid)
        self.assertTrue(self.svc.scan(token, self.event, self.leader)["success"])

    def test_identity_qr_is_never_accepted_for_attendance(self):
        sid = self.students[0]
        identity = self.svc.get_or_create_identity_qr(sid)["qr_data"]
        self._register(sid, self.event)
        self.assertEqual(self._code(identity, self.event), "IDENTITY_QR_NOT_ACCEPTED")
        self.assertEqual(self.db.attendance.count_documents({}), 0)

    def test_forged_garbage_and_cross_type_tokens_are_rejected(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        self.assertEqual(self._code("garbage", self.event), "INVALID_QR")
        self.assertEqual(self._code(self.svc.vault.new_token("event_registration"), self.event), "INVALID_QR")
        swapped = "CPQ-AP." + token.split(".", 1)[1]
        self.assertEqual(self._code(swapped, self.event), "INVALID_QR")   # wrong type -> not found in the pass collection
        self.assertEqual(self._code("CPQ-ID." + token.split(".", 1)[1], self.event), "IDENTITY_QR_NOT_ACCEPTED")

    def test_only_the_event_owner_or_an_admin_can_scan(self):
        token = self._qr(self.students[0], self.event)
        self.assertEqual(self._code(token, self.event, self.other_leader), "SCANNER_NOT_ALLOWED")
        self.assertEqual(self._code(token, self.event, self.students[1]), "SCANNER_NOT_ALLOWED")
        self.assertTrue(self.svc.scan(token, self.event, self.admin)["success"])

    def test_validate_only_does_not_mark(self):
        token = self._qr(self.students[0], self.event)
        self.assertTrue(self.svc.scan(token, self.event, self.leader, mark=False)["validated_only"])
        self.assertEqual(self.db.attendance.count_documents({}), 0)
        self.assertTrue(self.svc.scan(token, self.event, self.leader)["success"])

    def test_history_survives_losing_eligibility_or_cancelling_later(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        self.svc.scan(token, self.event, self.leader)
        self.blocked.add(sid)
        self._cancel(sid, self.event)
        self.assertEqual(self.db.attendance.count_documents({"student_id": sid}), 1)
        self.assertEqual(self.db.event_registration_qrs.find_one({"student_id": sid})["status"], "used")

    def test_legacy_scanner_adapter_formats(self):
        sid = self.students[0]
        token = self._qr(sid, self.event)
        ok = legacy_scan(self.svc, token, self.event, self.leader)
        self.assertEqual((ok["status"], ok["student_id"]), ("success", sid))
        again = legacy_scan(self.svc, token, self.event, self.leader)
        self.assertEqual((again["status"], again["student_id"]), ("already_marked", sid))
        with self.assertRaises(HTTPException) as ctx:
            legacy_scan(self.svc, self._qr(self.students[1], self.event2), self.event, self.leader)
        self.assertEqual(ctx.exception.status_code, 409)


class TestRoutes(Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(build_router(self.svc, lambda auth: auth.split()[1]))
        self.client = TestClient(app)

    def auth(self, uid):
        return {"authorization": f"Bearer {uid}"}

    def test_student_routes(self):
        sid = self.students[0]
        self._register(sid, self.event)
        r = self.client.get(f"/api/qr/events/{self.event}", self.auth(sid))
        self.assertEqual((r.status_code, r.json()["qr_type"]), (200, "event_registration"))
        items = self.client.get("/api/qr/my-events", self.auth(sid)).json()["items"]
        self.assertEqual([i["event_id"] for i in items], [self.event])
        nope = self.client.get(f"/api/qr/events/{self.event2}", self.auth(sid))
        self.assertEqual((nope.status_code, nope.json()["code"]), (403, "NOT_REGISTERED"))
        self.assertEqual(self.client.get("/api/qr/my-events", self.auth(self.leader)).status_code, 403)

    def test_scan_route_status_codes(self):
        sid = self.students[0]
        self._register(sid, self.event)
        token = self.client.get(f"/api/qr/events/{self.event}", self.auth(sid)).json()["qr_data"]
        url = f"/api/qr/events/{self.event}/scan"
        self.assertEqual(self.client.post(url, self.auth(sid), {"qr_data": token}).status_code, 403)
        ok = self.client.post(url, self.auth(self.leader), {"qr_data": token})
        self.assertEqual((ok.status_code, ok.json()["success"]), (200, True))
        dup = self.client.post(url, self.auth(self.leader), {"qr_data": token})
        self.assertEqual((dup.status_code, dup.json()["code"]), (409, "ALREADY_ATTENDED"))


if __name__ == "__main__":
    unittest.main()