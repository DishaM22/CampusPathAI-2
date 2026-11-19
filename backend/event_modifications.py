"""
event_modifications.py
======================
Phases 6, 7 and 8: changing an event AFTER it has been published.

Put this file next to main.py, event_audience.py and academic_progression.py.

The one rule everything here is built around
--------------------------------------------
The live event (db.events) is NEVER edited while a modification is being
requested, edited or reviewed. Students keep seeing the old date, venue and
resource person. The proposed version lives in its own record
(db.event_modifications). Only the final Admin approval copies the approved
values onto the same event document (same _id, so registrations, attendance,
saves and the audience all stay attached).

Life of a modification   (status field)
---------------------------------------
  pending_admin_review   organiser asked; Admin decides if they may prepare a change
     -> request_rejected   (end; live event unchanged)
     -> editing            Admin said yes; organiser edits the PROPOSED version
  editing / changes_requested -> submit -> the existing chain:
        pending_faculty (club coordinators, if the club has faculty)
        -> pending_hod -> pending_dean -> pending_admin
        (exactly what edited events already use: resubmission_status())
  any reviewer may:  approve (next stage) | ask for changes | reject
     changes_requested  organiser edits again and resubmits
     rejected           end; live event unchanged
  pending_admin + Admin approves -> published  (changes applied, version +1)
  cancelled              organiser withdrew it

Only ONE open modification per event (enforced by a unique index).

Collections (all additive)
--------------------------
  event_modifications  the request / proposed version / decisions
  event_versions       one row per applied change: before, after, who approved
  notifications        reused; student rows carry a unique `dedupe_key`
Fields added to the live event: live_version (1 = original), last_modified_at.
"""
import copy
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from event_audience import audience_allows, student_snapshot

MODS = "event_modifications"
VERSIONS = "event_versions"

# Fields an organiser may ask to change. (The publishing audience is Admin-only
# and is NOT here. Budget is validated by the existing budget code.)
EDITABLE_FIELDS = (
    "title", "description", "category", "date", "time", "location",
    "event_mode", "school_name", "department", "capacity", "skills",
    "poster_url", "spoc_name", "spoc_designation", "spoc_email",
    "spoc_contact", "resource_persons", "budget_breakup",
)
OPEN_STATUSES = (
    "pending_admin_review", "editing", "changes_requested",
    "pending_faculty", "pending_hod", "pending_dean", "pending_admin",
)
REVIEW_STATUS = {"faculty": "pending_faculty", "hod": "pending_hod",
                 "dean": "pending_dean", "admin": "pending_admin"}
NEXT_STATUS = {"faculty": "pending_hod", "hod": "pending_dean",
               "dean": "pending_admin", "admin": "published"}
TERMINAL = ("request_rejected", "rejected", "published", "cancelled")
IMPORTANT_FOR_STUDENTS = ("title", "date", "time", "location", "event_mode", "resource_persons")
MIN_TEXT = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_live(event: Dict[str, Any]) -> bool:
    """Published, or an older event with no approval status (students see both)."""
    return event.get("approval_status") in (None, "published")


class ModError(Exception):
    def __init__(self, status: int, message: Any):
        super().__init__(str(message))
        self.status = status
        self.message = message


# ============================================================
# 1. PURE HELPERS
# ============================================================

def stored_keys(field: str) -> Tuple[str, ...]:
    """The event keys a field is stored under (budget is stored twice, as before)."""
    return ("budget_breakup", "budget") if field == "budget_breakup" else (field,)


def check_fields(fields: Any) -> List[str]:
    if not isinstance(fields, list) or not fields:
        raise ModError(422, "Select at least one field to modify.")
    out: List[str] = []
    for f in fields:
        f = str(f or "").strip()
        if f not in EDITABLE_FIELDS:
            raise ModError(422, f"'{f}' cannot be modified through a request.")
        if f not in out:
            out.append(f)
    return out


def clean_reason(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) < MIN_TEXT:
        raise ModError(422, f"Please give a reason (at least {MIN_TEXT} characters).")
    if len(text) > 1000:
        raise ModError(422, "The reason must be 1000 characters or fewer.")
    return text


def changed_fields(live: Dict[str, Any], proposed: Dict[str, Any], fields: List[str]) -> List[str]:
    """Fields whose proposed value really differs from the live event."""
    out = []
    for f in fields:
        if any(k in proposed and proposed[k] != live.get(k) for k in stored_keys(f)):
            out.append(f)
    return out


def _names(persons: Any) -> str:
    if not isinstance(persons, list):
        return "—"
    names = [str(p.get("name", "")).strip() for p in persons if isinstance(p, dict) and p.get("name")]
    return ", ".join(names) or "—"


def student_message(before: Dict[str, Any], after: Dict[str, Any], changed: List[str],
                    reason: str = "") -> Tuple[str, str]:
    """(title, message) telling a student what changed. Pure, so it is easy to test."""
    name = after.get("title") or before.get("title") or "An event"
    parts: List[str] = []
    rescheduled = "date" in changed or "time" in changed
    if rescheduled:
        was = f"{before.get('date', '')} {before.get('time', '')}".strip() or "—"
        now = f"{after.get('date', '')} {after.get('time', '')}".strip() or "—"
        parts.append(f"Previous date/time: {was}. New date/time: {now}.")
    if "location" in changed or rescheduled:
        venue = after.get("location") or "To be announced"
        if "location" in changed:
            parts.append(f"Venue changed from {before.get('location') or '—'} to {venue}.")
        else:
            parts.append(f"Venue: {venue}.")
    if "event_mode" in changed:
        parts.append(f"Mode changed from {before.get('event_mode') or '—'} to {after.get('event_mode') or '—'}.")
    if "resource_persons" in changed:
        parts.append(f"Resource person(s): {_names(before.get('resource_persons'))} → {_names(after.get('resource_persons'))}.")
    if "title" in changed:
        parts.append(f"The event was renamed (was \"{before.get('title', '')}\").")
    if reason:
        parts.append(f"Reason: {reason}")
    title = f"Event rescheduled: {name}" if rescheduled else f"Event updated: {name}"
    return title, f'"{name}" has been updated. ' + " ".join(parts)


def stage_decisions(mod: Dict[str, Any]) -> Dict[str, Any]:
    """The latest decision of every stage, for the version history."""
    latest: Dict[str, Any] = {}
    for h in mod.get("approval_history") or []:
        latest[h.get("reviewer_role", "")] = h
    pick = lambda r: latest.get(r)
    return {"initial_admin": mod.get("initial_review"), "faculty": pick("faculty"),
            "hod": pick("hod"), "dean": pick("dean"), "final_admin": pick("admin")}


def _ser(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _ser(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ser(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# ============================================================
# 2. SERVICE
# ============================================================

class ModificationService:
    """
    `helpers` is a dict of functions passed in from main.py (so this file never
    imports main.py):
      validate(merged_event, user, role) -> cleaned dict   (existing event validation + budget rules)
      resubmission_status(role, user)    -> 'pending_faculty' | 'pending_hod'
      belongs_to_club(event, user)       -> bool
      hod_block_reason(hod_user, event)  -> None | text
      user_club_id(user)                 -> text
      notify_faculty(club_id, club_name, title, message, ntype, related_id, remarks, reviewer_role)
    """

    def __init__(self, db, helpers: Dict[str, Callable]):
        self.db = db
        self.h = helpers

    # ---------------- small lookups
    def _oid(self, value: Any, label: str):
        from bson import ObjectId
        try:
            return ObjectId(str(value))
        except Exception:
            raise ModError(400, f"Invalid {label}.")

    def _event(self, event_id: Any) -> Dict[str, Any]:
        ev = self.db.events.find_one({"_id": self._oid(event_id, "event ID")})
        if not ev:
            raise ModError(404, "Event not found.")
        return ev

    def _mod(self, mod_id: Any) -> Dict[str, Any]:
        m = self.db[MODS].find_one({"_id": self._oid(mod_id, "modification ID")})
        if not m:
            raise ModError(404, "Modification request not found.")
        return m

    def _owner_scope(self, event: Dict[str, Any], user: Dict[str, Any]) -> bool:
        role = str(user.get("role") or "").lower()
        if role not in ("club", "faculty"):
            return False
        return bool(self.h["belongs_to_club"](event, user)) or str(event.get("created_by") or "") == str(user.get("_id"))

    def _can_view(self, mod: Dict[str, Any], event: Dict[str, Any], user: Dict[str, Any]) -> bool:
        role = str(user.get("role") or "").lower()
        if role in ("admin", "dean"):
            return True
        if role == "hod":
            return self.h["hod_block_reason"](user, event) is None
        if role in ("club", "faculty"):
            return self._owner_scope(event, user) or str(mod.get("requested_by")) == str(user.get("_id"))
        return False

    def _unset_if_closed(self, status: str, update: Dict[str, Any]) -> Dict[str, Any]:
        if status in TERMINAL:
            update.setdefault("$unset", {})["open_key"] = ""
        return update

    def _transition(self, mod: Dict[str, Any], expected_status: str, new_status: str,
                    set_fields: Optional[Dict[str, Any]] = None,
                    push: Optional[Dict[str, Any]] = None,
                    version: Optional[int] = None) -> None:
        """Atomic: only succeeds if nobody changed the record since it was read."""
        guard: Dict[str, Any] = {"_id": mod["_id"], "status": expected_status}
        if version is not None:
            guard["submission_version"] = version
        update: Dict[str, Any] = {"$set": {"status": new_status, "updated_at": _now(), **(set_fields or {})}}
        if push:
            update["$push"] = push
        self._unset_if_closed(new_status, update)
        if self.db[MODS].update_one(guard, update).modified_count != 1:
            raise ModError(409, "This request changed while you were working on it. Refresh and try again.")

    # ---------------- validation of a proposed version
    def _validate(self, event: Dict[str, Any], proposed: Dict[str, Any], fields: List[str],
                  user: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs the SAME validation an edited event gets, on (live event + proposed),
        and returns the cleaned values for the requested fields only.
        """
        merged = {k: v for k, v in event.items()}
        merged.update(proposed)
        if "budget_breakup" not in fields:           # leave the saved budget alone
            merged.pop("budget_breakup", None)
            merged.pop("budget", None)
        role = str(user.get("role") or "").lower()
        cleaned = self.h["validate"](merged, user, role)
        out: Dict[str, Any] = {}
        for f in fields:
            for k in stored_keys(f):
                if k in cleaned:
                    out[k] = cleaned[k]
        missing = [f for f in fields if not any(k in out for k in stored_keys(f))]
        if missing:
            raise ModError(422, "Please provide a value for: " + ", ".join(missing) + ".")
        return out

    def _proposal_from_input(self, fields: List[str], raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ModError(422, "Enter the proposed new values.")
        extra = [k for k in raw if k not in fields and not (k == "budget" and "budget_breakup" in fields)]
        if extra:
            raise ModError(422, "Values were sent for fields that were not selected: " + ", ".join(extra) + ".")
        return {k: v for k, v in raw.items() if k in fields or (k == "budget" and "budget_breakup" in fields)}

    # ---------------- notifications
    def _insert_notifications(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        keys = [r["dedupe_key"] for r in rows if r.get("dedupe_key")]
        if keys:
            seen = {n.get("dedupe_key") for n in self.db.notifications.find({"dedupe_key": {"$in": keys}})}
            rows = [r for r in rows if r.get("dedupe_key") not in seen]
        if not rows:
            return 0
        try:
            self.db.notifications.insert_many(rows, ordered=False)
        except Exception as error:                  # a duplicate race or a failure: never break the action
            print("NOTIFICATION INSERT WARNING:", repr(error))
        return len(rows)

    def _notify_user(self, user_id: Any, title: str, message: str, ntype: str, related_id: str,
                     remarks: str = "", reviewer_role: str = "", dedupe: str = "") -> None:
        if not user_id:
            return
        row = {"title": title, "message": message, "type": ntype, "recipient": str(user_id),
               "related_id": related_id, "remarks": remarks, "reviewer_role": reviewer_role,
               "is_read": False, "created_at": _now()}
        if dedupe:
            row["dedupe_key"] = dedupe
        self._insert_notifications([row])

    def _notify_admins(self, title: str, message: str, mod_id: str, dedupe: str) -> None:
        self._insert_notifications([{
            "title": title, "message": message, "type": "event_modification",
            "recipient_role": "admin", "recipient": "admin", "related_id": mod_id,
            "is_read": False, "created_at": _now(), "dedupe_key": dedupe}])

    def _notify_next_stage(self, mod: Dict[str, Any], event: Dict[str, Any], status: str) -> None:
        mid, title = str(mod["_id"]), event.get("title", "an event")
        text = f'A modified version of "{title}" is waiting for your review.'
        stamp = f"{mid}:{status}:{mod.get('submission_version', 1)}"
        try:
            if status == "pending_faculty":
                self.h["notify_faculty"](mod.get("club_id"), "", "Modified event awaiting review", text,
                                         "event_modification", mid, "", "")
            elif status == "pending_hod":
                from departments import canonical_department
                dept = canonical_department(event.get("department"))
                rows = []
                for hod in self.db.students.find({"role": "hod", "status": "active"}):
                    if dept is None or canonical_department(hod.get("department")) == dept:
                        rows.append({"title": "Modified event awaiting review", "message": text,
                                     "type": "event_modification", "recipient": str(hod["_id"]),
                                     "related_id": mid, "is_read": False, "created_at": _now(),
                                     "dedupe_key": f"modreview:{stamp}:{hod['_id']}"})
                self._insert_notifications(rows)
            elif status == "pending_dean":
                rows = [{"title": "Modified event awaiting review", "message": text,
                         "type": "event_modification", "recipient": str(d["_id"]), "related_id": mid,
                         "is_read": False, "created_at": _now(), "dedupe_key": f"modreview:{stamp}:{d['_id']}"}
                        for d in self.db.students.find({"role": "dean", "status": "active"})]
                self._insert_notifications(rows)
            elif status == "pending_admin":
                self._notify_admins("Modified event awaiting final approval", text, mid, f"modreview:{stamp}:admin")
        except Exception as error:
            print("NEXT-STAGE NOTIFICATION WARNING:", repr(error))

    # ============================================================
    # ORGANISER: request, edit, submit, withdraw
    # ============================================================
    def create_request(self, user: Dict[str, Any], event_id: str, fields: Any,
                       proposed: Any, reason: Any) -> Dict[str, Any]:
        role = str(user.get("role") or "").lower()
        if role not in ("club", "faculty"):
            raise ModError(403, "Only Club Coordinators and Faculty can request a modification.")
        event = self._event(event_id)
        if not is_live(event):
            raise ModError(409, "Only published events can be modified through a request.")
        if not self._owner_scope(event, user):
            raise ModError(403, "You can only request changes to your own club's events.")
        fields = check_fields(fields)
        reason = clean_reason(reason)
        cleaned = self._validate(event, self._proposal_from_input(fields, proposed), fields, user)
        if not changed_fields(event, cleaned, fields):
            raise ModError(422, "The proposed values are the same as the current ones.")

        now = _now()
        doc = {
            "event_id": str(event["_id"]), "event_title": event.get("title", ""),
            "club_id": event.get("club_id") or self.h["user_club_id"](user),
            "department": event.get("department", ""), "organizer": event.get("organizer", ""),
            "requested_by": str(user["_id"]), "requested_by_name": user.get("name", ""),
            "requested_by_role": role, "fields": fields, "reason": reason,
            "original_values": {k: event.get(k) for f in fields for k in stored_keys(f)},
            "proposed": cleaned, "base_version": int(event.get("live_version") or 1),
            "status": "pending_admin_review", "open_key": str(event["_id"]),
            "submission_version": 1, "initial_review": None, "approval_history": [],
            "edit_history": [], "created_at": now, "updated_at": now,
        }
        try:
            result = self.db[MODS].insert_one(doc)
        except Exception as error:
            if error.__class__.__name__ == "DuplicateKeyError":
                raise ModError(409, "This event already has an open modification request.")
            raise
        mid = str(result.inserted_id)
        self._notify_admins(
            "Published event modification request",
            f'{user.get("name", "An organiser")} asked to modify "{event.get("title", "an event")}": {reason}',
            mid, f"modrequest:{mid}")
        return {"status": "success", "modification_id": mid, "state": "pending_admin_review"}

    def update_draft(self, user: Dict[str, Any], mod_id: str, raw: Any) -> Dict[str, Any]:
        mod = self._mod(mod_id)
        event = self._event(mod["event_id"])
        self._require_owner(mod, event, user)
        if mod["status"] not in ("editing", "changes_requested"):
            raise ModError(409, "This request can be edited only after the Admin approves it "
                                "(or when a reviewer returns it for changes).")
        fields = list(mod["fields"])
        incoming = self._proposal_from_input(fields, raw)
        if not incoming:
            raise ModError(422, "Nothing to save.")
        merged = {**mod.get("proposed", {}), **incoming}
        cleaned = self._validate(event, merged, fields, user)
        entry = {"editor_id": str(user["_id"]), "editor_name": user.get("name", ""),
                 "editor_role": str(user.get("role") or ""), "edited_at": _now().isoformat(),
                 "changed_fields": changed_fields(event, cleaned, fields)}
        self._transition(mod, mod["status"], mod["status"], {"proposed": cleaned}, {"edit_history": entry})
        return {"status": "success", "proposed": _ser(cleaned)}

    def submit(self, user: Dict[str, Any], mod_id: str) -> Dict[str, Any]:
        mod = self._mod(mod_id)
        event = self._event(mod["event_id"])
        self._require_owner(mod, event, user)
        if mod["status"] not in ("editing", "changes_requested"):
            raise ModError(409, "This request is not ready to submit.")
        if not is_live(event) or int(event.get("live_version") or 1) != int(mod.get("base_version") or 1):
            raise ModError(409, "The event changed since this request was made. Please start a new request.")
        fields = list(mod["fields"])
        cleaned = self._validate(event, mod.get("proposed", {}), fields, user)
        if not changed_fields(event, cleaned, fields):
            raise ModError(422, "There are no changes to submit.")
        role = str(user.get("role") or "").lower()
        next_status = self.h["resubmission_status"](role, user)
        version = int(mod.get("submission_version") or 1) + (1 if mod["status"] == "changes_requested" else 0)
        self._transition(mod, mod["status"], next_status,
                         {"proposed": cleaned, "submission_version": version, "submitted_at": _now()})
        fresh = self._mod(mod_id)
        self._notify_next_stage(fresh, event, next_status)
        return {"status": "success", "state": next_status, "submission_version": version}

    def withdraw(self, user: Dict[str, Any], mod_id: str) -> Dict[str, Any]:
        mod = self._mod(mod_id)
        event = self._event(mod["event_id"])
        self._require_owner(mod, event, user)
        if mod["status"] not in ("pending_admin_review", "editing", "changes_requested"):
            raise ModError(409, "A request that is already under review cannot be withdrawn.")
        self._transition(mod, mod["status"], "cancelled")
        return {"status": "success", "state": "cancelled"}

    def _require_owner(self, mod, event, user) -> None:
        if not self._owner_scope(event, user) and str(mod.get("requested_by")) != str(user.get("_id")):
            raise ModError(403, "You can only manage modification requests of your own club.")
        if str(user.get("role") or "").lower() not in ("club", "faculty"):
            raise ModError(403, "Only the organiser can do this.")

    # ============================================================
    # ADMIN: initial review (permission to PREPARE a change, nothing is published)
    # ============================================================
    def initial_review(self, admin: Dict[str, Any], mod_id: str, decision: Any, remarks: Any) -> Dict[str, Any]:
        decision = str(decision or "").strip().lower()
        remarks = " ".join(str(remarks or "").split())
        if decision not in ("approved", "rejected"):
            raise ModError(400, "Decision must be approved or rejected.")
        if decision == "rejected" and len(remarks) < MIN_TEXT:
            raise ModError(400, f"Please give remarks of at least {MIN_TEXT} characters when rejecting.")
        mod = self._mod(mod_id)
        if mod["status"] != "pending_admin_review":
            raise ModError(409, "This request is not waiting for initial review.")
        entry = {"decision": decision, "remarks": remarks, "reviewer_id": str(admin["_id"]),
                 "reviewer_name": admin.get("name", ""), "reviewed_at": _now().isoformat()}
        new_status = "editing" if decision == "approved" else "request_rejected"
        self._transition(mod, "pending_admin_review", new_status, {"initial_review": entry})
        title = mod.get("event_title", "your event")
        if decision == "approved":
            self._notify_user(mod["requested_by"], "Modification request approved",
                              f'The Admin approved your request to modify "{title}". You can now prepare the revised '
                              "event. The live event stays unchanged until it is fully approved.",
                              "event_modification", str(mod["_id"]), remarks, "admin", f"modinitial:{mod['_id']}")
        else:
            self._notify_user(mod["requested_by"], "Modification request rejected",
                              f'The Admin rejected your request to modify "{title}". The published event is unchanged.',
                              "event_modification", str(mod["_id"]), remarks, "admin", f"modinitial:{mod['_id']}")
        return {"status": "success", "state": new_status}

    # ============================================================
    # RE-APPROVAL  (called from the existing /api/approvals/events/{id}/review)
    # ============================================================
    def is_modification(self, oid: Any) -> bool:
        return self.db[MODS].find_one({"_id": oid}) is not None

    def review(self, oid: Any, reviewer_id: str, user: Dict[str, Any], role: str,
               decision: str, remarks: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        mod = self._mod(oid)
        event = self._event(mod["event_id"])
        if role == "faculty" and not self.h["belongs_to_club"](event, user):
            raise ModError(403, "You can only review events of your own club.")
        if role == "hod":
            why = self.h["hod_block_reason"](user, event)
            if why:
                raise ModError(403, why)
        if mod["status"] != REVIEW_STATUS[role]:
            raise ModError(409, "This event is not awaiting your review.")
        version = int(mod.get("submission_version") or 1)
        entry = {"reviewer_id": reviewer_id, "reviewer_role": role, "reviewer_name": user.get("name", ""),
                 "decision": decision, "remarks": remarks, "submission_version": version,
                 "reviewed_at": _now().isoformat()}

        if decision == "changes":
            self._transition(mod, mod["status"], "changes_requested", None, {"approval_history": entry}, version)
            self._returned(mod, event, role, "requested changes to", remarks)
            return self._review_reply(oid, decision, "changes_requested", version)
        if decision == "rejected":
            self._transition(mod, mod["status"], "rejected", None, {"approval_history": entry}, version)
            self._returned(mod, event, role, "rejected", remarks)
            return self._review_reply(oid, decision, "rejected", version)

        if role == "admin":
            result = self._apply(mod, event, user, entry, bool(payload.get("share_reason_with_students")))
            reply = self._review_reply(oid, decision, "published", version)
            reply.update(result)
            return reply

        nxt = NEXT_STATUS[role]
        self._transition(mod, mod["status"], nxt, None, {"approval_history": entry}, version)
        self._notify_user(mod["requested_by"], "Modification moved to the next stage",
                          f'{role.upper() if role == "hod" else role.capitalize()} approved the modified version '
                          f'of "{mod.get("event_title", "your event")}".',
                          "event_modification", str(mod["_id"]), remarks, role, f"modstage:{mod['_id']}:{role}:{version}")
        self._notify_next_stage(self._mod(oid), event, nxt)
        return self._review_reply(oid, decision, nxt, version)

    def _review_reply(self, oid, decision, state, version) -> Dict[str, Any]:
        return {"status": "success", "message": "Review decision saved.", "event_id": str(oid),
                "decision": decision, "approval_status": state, "submission_version": version,
                "is_modification": True}

    def _returned(self, mod, event, role, verb, remarks) -> None:
        label = "HOD" if role == "hod" else role.capitalize()
        self._notify_user(mod["requested_by"], f"{label} {'requested changes' if 'changes' in verb else 'rejected your modification'}",
                          f'The {label} has {verb} the modified version of "{mod.get("event_title", "your event")}". '
                          + ("Please edit it and resubmit." if "changes" in verb else "The published event is unchanged."),
                          "event_modification", str(mod["_id"]), remarks, role,
                          f"modreturn:{mod['_id']}:{role}:{mod.get('submission_version', 1)}:{verb[:4]}")

    # ---------------- final approval: copy the approved values onto the live event
    def _apply(self, mod, event, admin, entry, share_reason: bool) -> Dict[str, Any]:
        event = copy.deepcopy(event)           # the "before" snapshot must never change under us
        base = int(mod.get("base_version") or 1)
        if not is_live(event) or int(event.get("live_version") or 1) != base:
            raise ModError(409, "The event changed since this request was made. Reject it and ask for a new request.")
        proposed = mod.get("proposed") or {}
        changes = {k: v for k, v in proposed.items() if v != event.get(k)}
        fields = changed_fields(event, proposed, list(mod["fields"]))
        if not changes or not fields:
            raise ModError(409, "There is nothing to apply: the proposal equals the live event.")
        version = int(mod.get("submission_version") or 1)
        now = _now()

        # 1) claim the request (a second Admin click or a late rejection cannot also apply it)
        self._transition(mod, "pending_admin", "published",
                         {"applied_version": base + 1, "applied_at": now}, {"approval_history": entry}, version)
        # 2) change the SAME event document, only if nobody changed it meanwhile
        guard = {"_id": event["_id"], "approval_status": {"$in": ["published", None]},
                 "live_version": base if base > 1 else {"$in": [1, None]}}
        update = {"$set": {**changes, "live_version": base + 1, "last_modified_at": now, "updated_at": now},
                  "$push": {"edit_history": {
                      "editor_id": str(admin["_id"]), "editor_name": admin.get("name", ""), "editor_role": "admin",
                      "edited_at": now.isoformat(), "changed_fields": fields, "kind": "modification_applied",
                      "modification_id": str(mod["_id"]), "submission_version": event.get("submission_version", 1),
                      "status_after": "published"}}}
        if self.db.events.update_one(guard, update).modified_count != 1:
            self.db[MODS].update_one({"_id": mod["_id"]}, {
                "$set": {"status": "pending_admin", "open_key": mod.get("open_key") or str(event["_id"])},
                "$unset": {"applied_version": "", "applied_at": ""}, "$pop": {"approval_history": 1}})
            raise ModError(409, "The event changed while you were approving. Refresh and try again.")

        fresh = self._mod(mod["_id"])
        after = {**event, **changes}
        self._write_version(fresh, event, after, fields, admin, now, share_reason)
        notified = self._notify_students(fresh, event, after, fields, share_reason)
        self._notify_user(mod["requested_by"], "Modification published",
                          f'The Admin approved the modified version of "{mod.get("event_title", "your event")}". '
                          "The updated details are now live.", "event_modification", str(mod["_id"]),
                          entry.get("remarks", ""), "admin", f"modfinal:{mod['_id']}")
        registered = self._registered_ids(str(event["_id"]))
        warn = ""
        if "capacity" in fields:
            try:
                if int(after.get("capacity") or 0) and len(registered) > int(after["capacity"]):
                    warn = (f"{len(registered)} students are registered but the new capacity is {after['capacity']}. "
                            "Nobody was removed; no new registrations will be accepted.")
            except (TypeError, ValueError):
                pass
        return {"applied_version": base + 1, "changed_fields": fields,
                "students_notified": notified, "registered_students": len(registered), "capacity_warning": warn}

    def _write_version(self, mod, before, after, fields, admin, now, share_reason) -> None:
        keys = [k for f in fields for k in stored_keys(f)]
        row = {
            "event_id": str(before["_id"]), "version": int(mod["applied_version"]), "kind": "modification",
            "modification_id": str(mod["_id"]), "changed_fields": fields,
            "previous_data": {k: before.get(k) for k in keys}, "new_data": {k: after.get(k) for k in keys},
            "reason": mod.get("reason", ""),
            "requested_by": {"id": mod.get("requested_by"), "name": mod.get("requested_by_name"),
                             "role": mod.get("requested_by_role")},
            "decisions": stage_decisions(mod), "reason_shared_with_students": share_reason,
            "submitted_at": mod.get("created_at"), "approved_at": now, "approved_by": str(admin["_id"]),
        }
        try:
            self.db[VERSIONS].insert_one(row)
        except Exception as error:
            print("VERSION HISTORY WARNING:", repr(error))

    def _registered_ids(self, event_id: str) -> List[str]:
        return list(dict.fromkeys(str(r.get("student_id")) for r in self.db.event_registrations.find(
            {"event_id": event_id, "status": "registered"}) if r.get("student_id")))

    def _notify_students(self, mod, before, after, fields, share_reason) -> int:
        """Only registered students who are STILL in the audience; once per modification."""
        if not any(f in IMPORTANT_FOR_STUDENTS for f in fields):
            return 0
        from bson import ObjectId
        ids = self._registered_ids(str(before["_id"]))
        if not ids:
            return 0
        valid = []
        for i in ids:
            try:
                valid.append(ObjectId(i))
            except Exception:
                pass
        students = {str(s["_id"]): s for s in self.db.students.find(
            {"_id": {"$in": valid}, "role": "student"},
            {"department": 1, "class_id": 1, "status": 1})}
        title, message = student_message(before, after, [f for f in fields if f in IMPORTANT_FOR_STUDENTS],
                                         mod.get("reason", "") if share_reason else "")
        now, rows = _now(), []
        for sid, s in students.items():
            if str(s.get("status") or "").lower() == "disabled":
                continue
            if not audience_allows(before.get("audience"), student_snapshot(s)):
                continue
            rows.append({"title": title, "message": message, "type": "event_modified", "audience": "student",
                         "recipient": sid, "related_id": str(before["_id"]), "is_read": False, "created_at": now,
                         "dedupe_key": f"event_modification:{mod['_id']}:{sid}"})
        return self._insert_notifications(rows)

    # ============================================================
    # READ MODELS
    # ============================================================
    def _summary(self, m: Dict[str, Any]) -> Dict[str, Any]:
        return _ser({"id": str(m["_id"]), "event_id": m.get("event_id"), "event_title": m.get("event_title"),
                     "organizer": m.get("organizer"), "requested_by_name": m.get("requested_by_name"),
                     "requested_by_role": m.get("requested_by_role"), "fields": m.get("fields"),
                     "reason": m.get("reason"), "status": m.get("status"),
                     "submission_version": m.get("submission_version"), "created_at": m.get("created_at"),
                     "updated_at": m.get("updated_at"), "applied_version": m.get("applied_version")})

    def detail(self, user: Dict[str, Any], mod_id: str) -> Dict[str, Any]:
        mod = self._mod(mod_id)
        event = self._event(mod["event_id"])
        if not self._can_view(mod, event, user):
            raise ModError(403, "You cannot view this modification.")
        fields = list(mod["fields"])
        published = mod.get("status") == "published"
        rows = []
        for f in fields:
            k = stored_keys(f)[0]
            original = mod.get("original_values", {}).get(k)
            rows.append({"field": f, "original": original, "current_live": event.get(k),
                         "proposed": (mod.get("proposed") or {}).get(k),
                         "changed": (mod.get("proposed") or {}).get(k) != (original if published else event.get(k))})
        out = self._summary(mod)
        out.update(_ser({
            "comparison": rows, "initial_review": mod.get("initial_review"),
            "approval_history": mod.get("approval_history"), "edit_history": mod.get("edit_history"),
            "base_version": mod.get("base_version"),
            "live_version": int(event.get("live_version") or 1),
            "registered_students": len(self._registered_ids(mod["event_id"])),
            "editable": mod.get("status") in ("editing", "changes_requested"),
        }))
        return out

    def mine(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        club = self.h["user_club_id"](user)
        flt: Dict[str, Any] = {"$or": [{"requested_by": str(user["_id"])}] + ([{"club_id": club}] if club else [])}
        rows = list(self.db[MODS].find(flt).sort("created_at", -1))
        return [self._summary(m) for m in rows]

    def admin_list(self, status: str = "pending_admin_review") -> List[Dict[str, Any]]:
        flt = {} if status == "all" else {"status": status}
        return [self._summary(m) for m in self.db[MODS].find(flt).sort("created_at", -1).limit(300)]

    def approval_items(self, user_id: str, user: Dict[str, Any], role: str, json_safe: Callable) -> Tuple[List, List]:
        """Modifications shaped like events, so the existing HOD/Dean/Admin queue shows them."""
        queue, reviewed = [], []
        for m in self.db[MODS].find({}).sort("created_at", -1):
            ev = self.db.events.find_one({"_id": self._oid(m["event_id"], "event ID")})
            if not ev:
                continue
            if role == "faculty" and not self.h["belongs_to_club"](ev, user):
                continue
            item = None
            if m.get("status") == REVIEW_STATUS[role]:
                if role != "hod" or self.h["hod_block_reason"](user, ev) is None:
                    item = True
                    queue.append(self._as_event_item(m, ev, json_safe))
            if item is None and any(str(h.get("reviewer_id")) == user_id and h.get("reviewer_role") == role
                                    for h in (m.get("approval_history") or [])):
                reviewed.append(self._as_event_item(m, ev, json_safe))
        return queue, reviewed

    def faculty_review_events(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Modifications waiting for THIS faculty (their club's coordinators submitted them),
        shaped like events, for the Faculty dashboard's own "Pending Event Reviews" list.
        """
        out = []
        for m in self.db[MODS].find({"status": "pending_faculty"}).sort("created_at", 1):
            ev = self.db.events.find_one({"_id": self._oid(m["event_id"], "event ID")})
            if ev and self.h["belongs_to_club"](ev, user):
                out.append(self._as_event_item(m, ev, lambda v: v))
        return out

    def _as_event_item(self, m, ev, json_safe) -> Dict[str, Any]:
        item = json_safe({**ev, **(m.get("proposed") or {})})
        fields = list(m["fields"])
        item.update({
            "_id": str(m["_id"]), "event_id": m["event_id"],
            "title": "✏️ Modification: " + str((m.get("proposed") or {}).get("title") or ev.get("title", "")),
            "approval_status": m.get("status"), "approval_history": json_safe(m.get("approval_history") or []),
            "submission_version": m.get("submission_version", 1), "is_modification": True,
            "modification": json_safe({
                "id": str(m["_id"]), "event_id": m["event_id"], "reason": m.get("reason"),
                "changed_fields": fields, "original_values": m.get("original_values"),
                "proposed_values": m.get("proposed"), "requested_by_name": m.get("requested_by_name"),
                "initial_review": m.get("initial_review")}),
        })
        return item

    def versions(self, user: Dict[str, Any], event_id: str) -> Dict[str, Any]:
        event = self._event(event_id)
        role = str(user.get("role") or "").lower()
        allowed = role in ("admin", "dean") or (role == "hod" and self.h["hod_block_reason"](user, event) is None) \
            or self._owner_scope(event, user)
        if not allowed:
            raise ModError(403, "You cannot view this event's history.")
        rows = list(self.db[VERSIONS].find({"event_id": str(event["_id"])}).sort("version", 1))
        published_at = None
        for h in reversed(event.get("approval_history") or []):
            if h.get("reviewer_role") == "admin" and h.get("decision") == "approved":
                published_at = h.get("reviewed_at")
                break
        first = {"version": 1, "kind": "original", "summary": "Original event published", "approved_at": published_at}
        return {"event_id": str(event["_id"]), "current_version": int(event.get("live_version") or 1),
                "versions": [first] + [_ser({**r, "id": str(r["_id"])}) for r in rows]}

    # ============================================================
    # STUDENT NOTIFICATIONS (own rows only)
    # ============================================================
    def student_notifications(self, user: Dict[str, Any], limit: int = 50) -> Dict[str, Any]:
        rows = list(self.db.notifications.find({"recipient": str(user["_id"])}).sort("created_at", -1).limit(max(1, min(limit, 200))))
        items = [_ser({"id": str(n["_id"]), "title": n.get("title", ""), "message": n.get("message", ""),
                       "type": n.get("type", ""), "related_id": n.get("related_id"),
                       "is_read": bool(n.get("is_read")), "created_at": n.get("created_at")}) for n in rows]
        return {"notifications": items, "unread": sum(1 for n in items if not n["is_read"])}

    def mark_read(self, user: Dict[str, Any], notification_id: Optional[str]) -> int:
        flt: Dict[str, Any] = {"recipient": str(user["_id"]), "is_read": False}
        if notification_id:
            flt["_id"] = self._oid(notification_id, "notification ID")
        n = 0
        for doc in list(self.db.notifications.find(flt)):
            n += self.db.notifications.update_one({"_id": doc["_id"]}, {"$set": {"is_read": True}}).modified_count
        return n

    def ensure_indexes(self) -> None:
        specs = [
            (self.db[MODS], [("open_key", 1)], {"unique": True, "sparse": True}),
            (self.db[MODS], [("event_id", 1), ("created_at", -1)], {}),
            (self.db[MODS], [("status", 1)], {}),
            (self.db[VERSIONS], [("event_id", 1), ("version", 1)], {"unique": True}),
            (self.db.notifications, [("dedupe_key", 1)], {"unique": True, "sparse": True}),
            (self.db.notifications, [("recipient", 1), ("created_at", -1)], {}),
        ]
        for coll, keys, kw in specs:
            try:
                coll.create_index(keys, **kw)
            except Exception as error:
                print("MODIFICATION INDEX WARNING:", repr(error))


# ============================================================
# 3. API
# ============================================================

def install(app, db, get_user_id: Callable[[Optional[str]], str], helpers: Dict[str, Callable]) -> ModificationService:
    from bson import ObjectId
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel

    service = ModificationService(db, helpers)
    router = APIRouter()

    class RequestBody(BaseModel):
        fields: List[str]
        proposed: Dict[str, Any]
        reason: str

    class DraftBody(BaseModel):
        proposed: Dict[str, Any]

    class InitialBody(BaseModel):
        decision: str
        remarks: Optional[str] = ""

    def current_user(authorization) -> Dict[str, Any]:
        user_id = get_user_id(authorization)
        try:
            user = db.students.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            user = None
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user

    def role_of(user) -> str:
        return str(user.get("role") or "").lower()

    def need(authorization, *roles) -> Dict[str, Any]:
        user = current_user(authorization)
        if role_of(user) not in roles or str(user.get("status") or "active").lower() == "disabled":
            raise HTTPException(status_code=403, detail="You are not allowed to do this.")
        return user

    def run(fn):
        try:
            return fn()
        except ModError as error:
            raise HTTPException(status_code=error.status, detail=error.message)

    @router.post("/api/events/{event_id}/modification-requests")
    def request_modification(event_id: str, body: RequestBody, authorization: str = Header(None)):
        user = need(authorization, "club", "faculty")
        return run(lambda: service.create_request(user, event_id, body.fields, body.proposed, body.reason))

    @router.get("/api/modifications/mine")
    def my_modifications(authorization: str = Header(None)):
        user = need(authorization, "club", "faculty")
        return {"modifications": run(lambda: service.mine(user))}

    @router.get("/api/modifications/{mod_id}")
    def modification_detail(mod_id: str, authorization: str = Header(None)):
        user = need(authorization, "club", "faculty", "hod", "dean", "admin")
        return run(lambda: service.detail(user, mod_id))

    @router.put("/api/modifications/{mod_id}/draft")
    def modification_draft(mod_id: str, body: DraftBody, authorization: str = Header(None)):
        user = need(authorization, "club", "faculty")
        return run(lambda: service.update_draft(user, mod_id, body.proposed))

    @router.post("/api/modifications/{mod_id}/submit")
    def modification_submit(mod_id: str, authorization: str = Header(None)):
        user = need(authorization, "club", "faculty")
        return run(lambda: service.submit(user, mod_id))

    @router.post("/api/modifications/{mod_id}/withdraw")
    def modification_withdraw(mod_id: str, authorization: str = Header(None)):
        user = need(authorization, "club", "faculty")
        return run(lambda: service.withdraw(user, mod_id))

    @router.get("/api/admin/modifications")
    def admin_modifications(status: str = "pending_admin_review", authorization: str = Header(None)):
        need(authorization, "admin")
        return {"modifications": service.admin_list(status)}

    @router.post("/api/admin/modifications/{mod_id}/initial-review")
    def admin_initial_review(mod_id: str, body: InitialBody, authorization: str = Header(None)):
        admin = need(authorization, "admin")
        return run(lambda: service.initial_review(admin, mod_id, body.decision, body.remarks))

    @router.get("/api/events/{event_id}/versions")
    def event_versions(event_id: str, authorization: str = Header(None)):
        user = need(authorization, "club", "faculty", "hod", "dean", "admin")
        return run(lambda: service.versions(user, event_id))

    @router.get("/api/student/notifications")
    def student_notifications(limit: int = 50, authorization: str = Header(None)):
        user = need(authorization, "student")
        return service.student_notifications(user, limit)

    @router.post("/api/student/notifications/read-all")
    def student_read_all(authorization: str = Header(None)):
        user = need(authorization, "student")
        return {"status": "success", "marked": service.mark_read(user, None)}

    @router.post("/api/student/notifications/{notification_id}/read")
    def student_read_one(notification_id: str, authorization: str = Header(None)):
        user = need(authorization, "student")
        return {"status": "success", "marked": run(lambda: service.mark_read(user, notification_id))}

    app.include_router(router)

    @app.on_event("startup")
    def _create_modification_indexes():
        try:
            service.ensure_indexes()
        except Exception as error:
            print("MODIFICATION INDEX WARNING:", repr(error))

    return service