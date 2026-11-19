"""
QR business logic - Phase 1 (Student Identity QR), Phase 2 (Event Registration QR), Phase 3 (Attendance Pass).

  student_identity_qrs      identifies a student, NEVER marks attendance
  event_registration_qrs    one per student registration (events that need registration)
  event_attendance_passes   one per eligible student per "No Registration Required" event
  qr_generation_jobs        background batch jobs that create / refresh the passes

Attendance is written to the EXISTING `attendance` collection so history and
reports keep working.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from bson import ObjectId
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError

from .adapters import Adapters
from .tokens import TYPE_IDENTITY, TYPE_PASS, TYPE_REGISTRATION, TokenVault

log = logging.getLogger("campuspath.qr")

COL_IDENTITY = "student_identity_qrs"
COL_REGISTRATION = "event_registration_qrs"
COL_PASS = "event_attendance_passes"
COL_JOBS = "qr_generation_jobs"


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


class QrError(Exception):
    """A business-rule refusal. `code` is stable for the frontend."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


class QrService:
    def __init__(self, db, vault: Optional[TokenVault] = None, adapters: Optional[Adapters] = None,
                 svg_renderer: Optional[Callable[[str], str]] = None,
                 on_attendance_marked: Optional[Callable] = None):
        self.db = db
        self.vault = vault or TokenVault()
        self.adapters = adapters or Adapters()
        self.svg_renderer = svg_renderer
        self.on_attendance_marked = on_attendance_marked
        self.identity = db[COL_IDENTITY]
        self.reg_qrs = db[COL_REGISTRATION]
        self.passes = db[COL_PASS]
        self.jobs = db[COL_JOBS]

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def ensure_indexes(self):
        self.identity.create_index("student_id", unique=True, name="uq_identity_student")
        self.identity.create_index("token_hash", unique=True, name="uq_identity_token")
        self.reg_qrs.create_index("registration_id", unique=True, name="uq_regqr_registration")
        self.reg_qrs.create_index("token_hash", unique=True, name="uq_regqr_token")
        self.reg_qrs.create_index([("event_id", 1), ("student_id", 1)], name="ix_regqr_event_student")
        self.passes.create_index([("student_id", 1), ("event_id", 1)], unique=True, name="uq_pass_student_event")
        self.passes.create_index("token_hash", unique=True, name="uq_pass_token")
        self.passes.create_index("event_id", name="ix_pass_event")
        self.jobs.create_index([("event_id", 1), ("status", 1)], name="ix_job_event_status")
        try:  # existing collection: never break startup if old data has duplicates
            self.db.attendance.create_index(
                [("event_id", 1), ("student_id", 1)], unique=True, name="uq_attendance_event_student")
        except Exception:
            log.warning("Could not create the unique attendance index (old duplicate rows?). "
                        "Duplicate scans are still blocked in code.", exc_info=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _oid(value, what="ID"):
        try:
            return ObjectId(str(value))
        except Exception:
            raise QrError(400, "INVALID_ID", f"Invalid {what}")

    def get_user(self, user_id) -> Optional[dict]:
        try:
            return self.db.students.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            return None

    def load_event(self, event_id) -> dict:
        event = self.db.events.find_one({"_id": self._oid(event_id, "event ID")})
        if not event:
            raise QrError(404, "EVENT_NOT_FOUND", "Event not found")
        return event

    def _eligible(self, event, student_id) -> bool:
        try:
            return bool(self.adapters.is_student_eligible(event, student_id))
        except Exception:  # a broken eligibility rule must fail CLOSED
            log.exception("eligibility check failed for student %s", student_id)
            return False

    def _svg(self, token):
        if not self.svg_renderer or not token:
            return None
        try:
            return self.svg_renderer(token)
        except Exception:
            log.exception("QR image rendering failed")
            return None

    def _view(self, qr_type, doc, **extra):
        token = self.vault.decrypt(doc.get("token_enc"))
        out = {"qr_type": qr_type, "status": doc.get("status"), "qr_data": token,
               "qr_svg": self._svg(token), "created_at": _iso(doc.get("created_at")),
               "used_at": _iso(doc.get("used_at"))}
        out.update(extra)
        return out

    def _insert_unique(self, collection, doc, lookup):
        """Insert; if a concurrent request won the race, return ITS document."""
        try:
            collection.insert_one(doc)
            return doc
        except DuplicateKeyError:
            return collection.find_one(lookup)

    # ------------------------------------------------------------------
    # PHASE 1 - student identity QR
    # ------------------------------------------------------------------
    def get_or_create_identity_qr(self, student_id) -> dict:
        student = self.get_user(student_id)
        if not student:
            raise QrError(404, "STUDENT_NOT_FOUND", "Student not found")
        if str(student.get("role") or "student") != "student":
            raise QrError(403, "NOT_A_STUDENT", "Only students have an identity QR.")
        if str(student.get("status") or "").lower() == "disabled":
            raise QrError(403, "ACCOUNT_DISABLED", "This account is disabled.")
        sid = str(student["_id"])

        doc = self.identity.find_one({"student_id": sid})
        if doc is None:
            if not self.adapters.is_student_verified(student):
                raise QrError(403, "NOT_VERIFIED",
                              "Your student QR is issued after registration and OTP verification.")
            token = self.vault.new_token(TYPE_IDENTITY)
            doc = self._insert_unique(
                self.identity,
                {"student_id": sid, "token_hash": self.vault.hash(TYPE_IDENTITY, token),
                 "token_enc": self.vault.encrypt(token), "status": "active", "created_at": _now()},
                {"student_id": sid})
        if doc.get("status") != "active":
            raise QrError(409, "QR_REVOKED", "This QR was revoked. Contact the Admin.")
        return self._view(TYPE_IDENTITY, doc, name=student.get("name", ""))

    # ------------------------------------------------------------------
    # PHASE 2 - event registration QR
    # ------------------------------------------------------------------
    def issue_registration_qr(self, student_id, event_id, _event=None, _reg=None) -> dict:
        """Idempotent: one QR per registration record (unique index)."""
        event = _event or self.load_event(event_id)
        if not self.adapters.event_requires_registration(event):
            raise QrError(409, "REGISTRATION_NOT_REQUIRED",
                          "This event does not use registration, so it has no registration QR.")
        reg = _reg or self.db.event_registrations.find_one(
            {"student_id": student_id, "event_id": event_id, "status": "registered"})
        if not reg:
            raise QrError(403, "NOT_REGISTERED", "You are not registered for this event.")
        if self.adapters.is_event_cancelled(event):
            raise QrError(409, "EVENT_CANCELLED", "This event was cancelled.")
        if not self._eligible(event, student_id):
            raise QrError(403, "NOT_ELIGIBLE", "You are no longer eligible for this event.")

        reg_id = str(reg["_id"])
        doc = self.reg_qrs.find_one({"registration_id": reg_id})
        if doc is None:
            token = self.vault.new_token(TYPE_REGISTRATION)
            doc = self._insert_unique(
                self.reg_qrs,
                {"registration_id": reg_id, "student_id": student_id, "event_id": event_id,
                 "token_hash": self.vault.hash(TYPE_REGISTRATION, token),
                 "token_enc": self.vault.encrypt(token), "status": "active", "created_at": _now()},
                {"registration_id": reg_id})
        if doc.get("status") == "cancelled":
            raise QrError(410, "QR_CANCELLED", "This registration QR is no longer valid.")
        return self._view(TYPE_REGISTRATION, doc, event_id=event_id, registration_id=reg_id)

    def cancel_registration_qr(self, student_id, event_id) -> int:
        """Cancelled registration -> its UNUSED QR dies. Used QRs stay as history."""
        return self.reg_qrs.update_many(
            {"student_id": student_id, "event_id": event_id, "status": "active"},
            {"$set": {"status": "cancelled", "cancelled_at": _now(),
                      "cancel_reason": "registration_cancelled"}}).modified_count

    def cancel_event_qrs(self, event_id) -> int:
        """Event cancelled: every UNUSED registration QR and attendance pass dies."""
        n = self.reg_qrs.update_many(
            {"event_id": event_id, "status": "active"},
            {"$set": {"status": "cancelled", "cancelled_at": _now(),
                      "cancel_reason": "event_cancelled"}}).modified_count
        return n + self._cancel_unused_passes(event_id, "event_cancelled")

    # ------------------------------------------------------------------
    # PHASE 3 - attendance pass (No Registration Required events)
    # ------------------------------------------------------------------
    def _new_pass_doc(self, student_id, event_id, source="auto"):
        token = self.vault.new_token(TYPE_PASS)
        return {"student_id": student_id, "event_id": event_id,
                "token_hash": self.vault.hash(TYPE_PASS, token),
                "token_enc": self.vault.encrypt(token),
                "status": "active", "source": source, "created_at": _now()}

    def issue_pass(self, student_id, event_id, _event=None) -> dict:
        """Idempotent: one pass per student per event (unique index)."""
        event = _event or self.load_event(event_id)
        if self.adapters.event_requires_registration(event):
            raise QrError(409, "REGISTRATION_REQUIRED",
                          "This event requires registration, so it has no attendance pass.")
        if self.adapters.is_event_cancelled(event):
            raise QrError(409, "EVENT_CANCELLED", "This event was cancelled.")
        if not self.adapters.is_event_published(event):
            raise QrError(409, "EVENT_NOT_PUBLISHED", "This event is not published yet.")
        if not self._eligible(event, student_id):
            raise QrError(403, "NOT_ELIGIBLE", "You are not eligible for this event.")

        lookup = {"student_id": student_id, "event_id": event_id}
        doc = self.passes.find_one(lookup)
        if doc is None:
            doc = self._insert_unique(self.passes, self._new_pass_doc(student_id, event_id, "on_demand"), lookup)
        elif doc.get("status") == "suspended":  # eligible again
            self.passes.update_one({"_id": doc["_id"], "status": "suspended"},
                                   {"$set": {"status": "active"}, "$unset": {"suspended_at": ""}})
            doc = self.passes.find_one(lookup)
        if doc.get("status") == "cancelled":
            raise QrError(410, "QR_CANCELLED", "This pass is no longer valid.")
        return self._view(TYPE_PASS, doc, event_id=event_id)

    def _eligible_ids_for(self, event) -> set:
        """Everyone eligible for `event` right now (fast path, else one by one)."""
        provider = self.adapters.eligible_student_ids
        if provider is not None:
            return {str(i) for i in provider(event)}
        ids = set()
        for row in self.db.students.find({"status": {"$ne": "disabled"}}, {"role": 1}):
            if row.get("role") in (None, "", "student") and self._eligible(event, str(row["_id"])):
                ids.add(str(row["_id"]))
        return ids

    def sync_event_passes(self, event_id, batch_size=500) -> dict:
        """
        Make the passes of one event match who is eligible RIGHT NOW: create the
        missing ones, re-activate returning students, suspend students who are no
        longer eligible. Used / cancelled passes are never touched (history).
        Safe to run any number of times.
        """
        event = self.load_event(event_id)
        stats = {"eligible": 0, "created": 0, "reactivated": 0, "suspended": 0, "cancelled": 0}
        if self.adapters.is_event_cancelled(event):
            stats["cancelled"] = self.cancel_event_qrs(event_id)
            stats["skipped"] = "event_cancelled"
            return stats
        if self.adapters.event_requires_registration(event):
            stats["cancelled"] = self._cancel_unused_passes(event_id, "registration_now_required")
            stats["skipped"] = "registration_required"
            return stats
        if not self.adapters.is_event_published(event):
            stats["skipped"] = "not_published"
            return stats

        eligible = self._eligible_ids_for(event)
        stats["eligible"] = len(eligible)
        existing = {p["student_id"]: p.get("status") for p in self.passes.find({"event_id": event_id})}

        missing = sorted(eligible - existing.keys())
        for i in range(0, len(missing), batch_size):
            ops = [UpdateOne({"student_id": sid, "event_id": event_id},
                             {"$setOnInsert": self._new_pass_doc(sid, event_id)}, upsert=True)
                   for sid in missing[i:i + batch_size]]
            try:
                stats["created"] += self.passes.bulk_write(ops, ordered=False).upserted_count
            except BulkWriteError as err:  # a parallel job created some: fine
                if any(e.get("code") != 11000 for e in err.details.get("writeErrors", [])):
                    raise
                stats["created"] += err.details.get("nUpserted", 0)

        back = [sid for sid, st in existing.items() if st == "suspended" and sid in eligible]
        gone = [sid for sid, st in existing.items() if st == "active" and sid not in eligible]
        for i in range(0, len(back), batch_size):
            stats["reactivated"] += self.passes.update_many(
                {"event_id": event_id, "student_id": {"$in": back[i:i + batch_size]}, "status": "suspended"},
                {"$set": {"status": "active"}, "$unset": {"suspended_at": ""}}).modified_count
        for i in range(0, len(gone), batch_size):
            stats["suspended"] += self._suspend(event_id, gone[i:i + batch_size])
        return stats

    def _suspend(self, event_id, student_ids) -> int:
        return self.passes.update_many(
            {"event_id": event_id, "student_id": {"$in": student_ids}, "status": "active"},
            {"$set": {"status": "suspended", "suspended_at": _now()}}).modified_count

    def _cancel_unused_passes(self, event_id, reason) -> int:
        return self.passes.update_many(
            {"event_id": event_id, "status": {"$in": ["active", "suspended"]}},
            {"$set": {"status": "cancelled", "cancelled_at": _now(), "cancel_reason": reason}}).modified_count

    def _open_events(self) -> list:
        """Published, not-cancelled, not-expired No-Registration events."""
        now, out = _now(), []
        query = {"$or": [{"registration_required": False}, {"registration_mode": {"$exists": True}},
                         {"registration_type": {"$exists": True}}]}
        for event in self.db.events.find(query):
            if (self.adapters.event_requires_registration(event)
                    or self.adapters.is_event_cancelled(event)
                    or not self.adapters.is_event_published(event)):
                continue
            expiry = self.adapters.event_expiry(event)
            if expiry and now > expiry:
                continue
            out.append(event)
        return out

    def sync_students_passes(self, student_ids) -> dict:
        """Eligibility of some students changed (promotion, class change ...):
        re-evaluate them against every open No-Registration event."""
        stats = {"created": 0, "reactivated": 0, "suspended": 0}
        events = self._open_events()
        for sid in {str(i) for i in student_ids}:
            student = self.get_user(sid)
            if not student or str(student.get("role") or "student") != "student":
                continue
            for event in events:
                eid = str(event["_id"])
                existing = self.passes.find_one({"student_id": sid, "event_id": eid})
                eligible = self._eligible(event, sid)
                if eligible and existing is None:
                    self.issue_pass(sid, eid, _event=event); stats["created"] += 1
                elif eligible and existing.get("status") == "suspended":
                    self.issue_pass(sid, eid, _event=event); stats["reactivated"] += 1
                elif not eligible and existing and existing.get("status") == "active":
                    stats["suspended"] += self._suspend(eid, [sid])
        return stats

    # ---- background batch jobs ---------------------------------------
    def enqueue_pass_job(self, event_id, run_async=True, reason="published") -> str:
        job = self.jobs.find_one({"event_id": event_id, "status": "queued"})
        if job is None:
            job = {"_id": ObjectId(), "event_id": event_id, "status": "queued",
                   "reason": reason, "created_at": _now()}
            self.jobs.insert_one(job)
        job_id = str(job["_id"])
        if run_async:
            threading.Thread(target=self.run_job, args=(job_id,), daemon=True).start()
        return job_id

    def run_job(self, job_id):
        job = self.jobs.find_one_and_update(
            {"_id": ObjectId(str(job_id)), "status": "queued"},
            {"$set": {"status": "running", "started_at": _now()}},
            return_document=ReturnDocument.AFTER)
        if not job:
            return None  # another worker has it / already done
        try:
            stats = self.sync_event_passes(job["event_id"])
            self.jobs.update_one({"_id": job["_id"]},
                                 {"$set": {"status": "done", "finished_at": _now(), "stats": stats}})
            return stats
        except Exception as err:
            log.exception("QR pass job failed")
            self.jobs.update_one({"_id": job["_id"]},
                                 {"$set": {"status": "failed", "finished_at": _now(), "error": str(err)}})
            return None

    def get_job(self, job_id) -> dict:
        job = self.jobs.find_one({"_id": self._oid(job_id, "job ID")})
        if not job:
            raise QrError(404, "JOB_NOT_FOUND", "Job not found")
        return {"job_id": str(job["_id"]), "event_id": job["event_id"], "status": job["status"],
                "stats": job.get("stats"), "error": job.get("error"),
                "created_at": _iso(job.get("created_at")), "finished_at": _iso(job.get("finished_at"))}

    def resume_pending_jobs(self):
        """Startup: jobs interrupted by a restart run again (they are idempotent)."""
        self.jobs.update_many({"status": "running"}, {"$set": {"status": "queued"}})
        ids = [str(j["_id"]) for j in self.jobs.find({"status": "queued"})]
        if ids:
            threading.Thread(target=lambda: [self.run_job(i) for i in ids], daemon=True).start()

    # ------------------------------------------------------------------
    # what a student sees
    # ------------------------------------------------------------------
    def get_event_qr_for_student(self, student_id, event_id) -> dict:
        """Registration QR for events that need registration, attendance pass otherwise."""
        event = self.load_event(event_id)
        if self.adapters.event_requires_registration(event):
            return self.issue_registration_qr(student_id, event_id, _event=event)
        existing = self.passes.find_one({"student_id": student_id, "event_id": event_id})
        if existing and existing.get("status") == "suspended" and not self._eligible(event, student_id):
            raise QrError(403, "NOT_ELIGIBLE", "You are no longer eligible for this event.")
        return self.issue_pass(student_id, event_id, _event=event)  # also covers late-eligible students

    @staticmethod
    def _event_card(event):
        return {"event_id": str(event["_id"]), "title": event.get("title", ""),
                "date": event.get("date", ""), "time": event.get("time", ""),
                "location": event.get("location", "")}

    def list_student_event_qrs(self, student_id) -> list:
        """Registration QRs (events with registration) + attendance passes (open events).
        Anything missing is created lazily, so events/registrations that predate the
        module, and students who became eligible later, are covered."""
        items = []
        for reg in self.db.event_registrations.find({"student_id": student_id, "status": "registered"}):
            try:
                event = self.load_event(reg["event_id"])
                if not self.adapters.event_requires_registration(event):
                    continue
                view = self.issue_registration_qr(student_id, reg["event_id"], _event=event, _reg=reg)
                items.append({**self._event_card(event), **view})
            except QrError:
                continue
        for event in self._open_events():
            eid = str(event["_id"])
            try:
                view = self.issue_pass(student_id, eid, _event=event)
            except QrError:
                continue
            items.append({**self._event_card(event), **view})
        items.sort(key=lambda i: str(i.get("date", "")))
        return items

    # ------------------------------------------------------------------
    # SCANNING
    # ------------------------------------------------------------------
    def scan(self, token, event_id, scanner_id, mark=True) -> dict:
        """Validate a scanned QR for `event_id` and (mark=True) record attendance.
        Raises QrError for every refusal; returns a dict on success."""
        event = self.load_event(event_id)
        scanner = self.get_user(scanner_id)
        if not scanner or not self.adapters.can_scan_event(scanner, event):
            raise QrError(403, "SCANNER_NOT_ALLOWED", "You cannot mark attendance for this event.")

        qr_type = self.vault.type_of(token)
        if qr_type is None:
            raise QrError(404, "INVALID_QR", "This is not a valid CampusPath QR code.")
        if qr_type == TYPE_IDENTITY:
            raise QrError(400, "IDENTITY_QR_NOT_ACCEPTED",
                          "A Student Identity QR identifies a student only. "
                          "It cannot be used for event attendance.")

        collection = self.reg_qrs if qr_type == TYPE_REGISTRATION else self.passes
        rec = collection.find_one({"token_hash": self.vault.hash(qr_type, token)})
        if rec is None:
            raise QrError(404, "INVALID_QR", "This QR code is not recognised.")
        if rec["event_id"] != event_id:
            raise QrError(409, "WRONG_EVENT", "This QR code belongs to a different event.")

        if self.adapters.is_event_cancelled(event):
            raise QrError(409, "EVENT_CANCELLED", "This event was cancelled.")
        if not self.adapters.is_event_published(event):
            raise QrError(409, "EVENT_NOT_PUBLISHED", "This event is not published.")
        expiry = self.adapters.event_expiry(event)
        if expiry and _now() > expiry:
            raise QrError(410, "QR_EXPIRED", "This QR code has expired.")

        student_id, status = rec["student_id"], rec.get("status")
        if status == "used":
            raise QrError(409, "ALREADY_ATTENDED", "Attendance was already recorded for this QR.",
                          student_id=student_id)
        if status == "cancelled":
            raise QrError(410, "QR_CANCELLED", "This QR code was cancelled.")
        if status not in ("active", "suspended"):
            raise QrError(410, "QR_REVOKED", "This QR code was revoked.")

        student = self.get_user(student_id)
        if not student or str(student.get("status") or "").lower() == "disabled":
            raise QrError(403, "ACCOUNT_DISABLED", "This student's account is not active.")

        registration_id = None
        if qr_type == TYPE_REGISTRATION:
            registration_id = rec["registration_id"]
            reg = self.db.event_registrations.find_one({"_id": self._oid(registration_id)})
            if (not reg or reg.get("status") != "registered"
                    or reg.get("student_id") != student_id or reg.get("event_id") != event_id):
                collection.update_one({"_id": rec["_id"], "status": "active"},
                                      {"$set": {"status": "cancelled", "cancelled_at": _now(),
                                                "cancel_reason": "registration_inactive"}})
                raise QrError(410, "REGISTRATION_INACTIVE", "This registration is no longer active.")
        elif self.adapters.event_requires_registration(event):
            raise QrError(409, "PASS_NOT_APPLICABLE",
                          "This event now requires registration; attendance passes are void.")

        if not self._eligible(event, student_id):  # rechecked at scan time
            if qr_type == TYPE_PASS:
                self._suspend(event_id, [student_id])
            raise QrError(403, "NOT_ELIGIBLE", "This student is not eligible for this event.")
        if qr_type == TYPE_PASS and status == "suspended":  # eligible again
            collection.update_one({"_id": rec["_id"], "status": "suspended"}, {"$set": {"status": "active"}})

        prior = self.db.attendance.find_one({"event_id": event_id, "student_id": student_id})
        if prior:
            collection.update_one({"_id": rec["_id"], "status": "active"},
                                  {"$set": {"status": "used", "used_at": prior.get("marked_at") or _now()}})
            raise QrError(409, "ALREADY_ATTENDED", "Attendance was already recorded for this student.",
                          student_id=student_id)

        result = {"success": True, "status": "success", "code": "OK", "qr_type": qr_type,
                  "event_id": event_id, "student_id": student_id, "student_name": student.get("name", "")}
        if not mark:
            result.update(message="QR code is valid.", validated_only=True)
            return result

        now = _now()
        claimed = collection.find_one_and_update(      # atomic: only ONE scan can win
            {"_id": rec["_id"], "status": {"$in": ["active", "suspended"]}},
            {"$set": {"status": "used", "used_at": now, "used_by": str(scanner_id)}},
            return_document=ReturnDocument.AFTER)
        if not claimed:
            raise QrError(409, "ALREADY_ATTENDED", "Attendance was already recorded for this QR.",
                          student_id=student_id)
        row = {"event_id": event_id, "student_id": student_id,
               "status": "present", "marked_at": now, "marked_by": str(scanner_id),
               "method": "qr", "qr_type": qr_type}
        if registration_id:
            row["registration_id"] = registration_id
        try:
            self.db.attendance.insert_one(row)
        except DuplicateKeyError:
            raise QrError(409, "ALREADY_ATTENDED", "Attendance was already recorded for this student.",
                          student_id=student_id)
        except Exception:
            collection.update_one({"_id": rec["_id"]}, {"$set": {"status": "active"},
                                                            "$unset": {"used_at": "", "used_by": ""}})
            raise
        if self.on_attendance_marked:
            try:
                self.on_attendance_marked(student_id, event_id, event)
            except Exception:
                log.exception("on_attendance_marked callback failed")
        result.update(message="Attendance marked successfully.", marked_at=now.isoformat())
        return result

    # ------------------------------------------------------------------
    # HOOKS - called from the rest of the app. NEVER raise.
    # ------------------------------------------------------------------
    def _safe(self, name, fn, *args):
        try:
            return fn(*args)
        except QrError as err:
            log.info("QR hook %s skipped: %s (%s)", name, err.message, err.code)
        except Exception:
            log.exception("QR hook %s failed", name)

    def on_student_registered(self, student_id):
        return self._safe("on_student_registered", self.get_or_create_identity_qr, str(student_id))

    def on_event_registered(self, student_id, event_id):
        return self._safe("on_event_registered", self.issue_registration_qr, str(student_id), str(event_id))

    def on_registration_cancelled(self, student_id, event_id):
        return self._safe("on_registration_cancelled", self.cancel_registration_qr, str(student_id), str(event_id))

    def on_event_cancelled(self, event_id):
        return self._safe("on_event_cancelled", self.cancel_event_qrs, str(event_id))

    def on_event_published(self, event_id, run_async=True):
        """Final Admin approval / re-publication: create the attendance passes in the background."""
        return self._safe("on_event_published", self.enqueue_pass_job, str(event_id), run_async)

    def on_event_eligibility_changed(self, event_id, run_async=True):
        """The Admin changed an event's audience (or its date / registration mode)."""
        return self._safe("on_event_eligibility_changed", self.enqueue_pass_job,
                          str(event_id), run_async, "eligibility_changed")

    def on_students_eligibility_changed(self, student_ids, run_async=True):
        """Promotion / class change: refresh these students' passes in the background."""
        ids = [str(i) for i in student_ids]
        if not ids:
            return None
        if not run_async:
            return self._safe("on_students_eligibility_changed", self.sync_students_passes, ids)
        threading.Thread(target=self._safe, args=("on_students_eligibility_changed",
                                                  self.sync_students_passes, ids), daemon=True).start()