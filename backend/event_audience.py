"""
event_audience.py
=================
Admin-controlled "Publish Event To" audience for CampusPath AI.

Put this file next to main.py. It adds ONE field to the existing event
document (`audience`) and one small log collection (`event_eligibility_log`).
It does not touch the budget code.

The audience object stored on the event
---------------------------------------
    audience = {
        "type":        "all" | "departments" | "classes" | "students",
        "departments": ["CSE", ...],          # type == departments
        "class_ids":   ["<classes._id>", ...],# type == classes
        "student_ids": ["<students._id>", ...],# type == students
        "class_snapshot": [ {id, name, department, academic_year,
                             year_of_study, semester, section}, ... ],
        "version":     1, 2, 3 ...            # +1 on every change
        "set_by", "set_at", "updated_by", "updated_at"
    }

How eligibility is decided (always LIVE, never copied per student)
------------------------------------------------------------------
A student's department comes from `students.department`, and the class
from `students.class_id` (the existing class system). Because eligibility
is computed from those fields every time, a promotion or class change takes
effect immediately, and "explicitly selected students" stay valid because
they are matched by id only.

Old events with no `audience` (legacy)
--------------------------------------
They keep behaving exactly as before: visible to everyone, as they were
when they were published. They are listed in the Admin "needs audience"
view so the Admin can restrict them. A malformed audience is never
treated as "everyone": it fails closed (nobody but staff can see it).
"""
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from departments import canonical_department, department_key, OFFICIAL_DEPARTMENTS

AUDIENCE_TYPES = ("all", "departments", "classes", "students")
STAFF_ROLES = {"admin", "hod", "dean", "faculty", "club"}
MAX_SELECTED = 5000
_HEX24 = re.compile(r"^[0-9a-fA-F]{24}$")

ELIGIBILITY_LOG = "event_eligibility_log"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# 1. PURE RULES (no database, no web server)
# ============================================================

def _string_list(value: Any, label: str, errors: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{label} must be a list.")
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    if len(out) > MAX_SELECTED:
        errors.append(f"{label}: at most {MAX_SELECTED} can be selected.")
    return out


def clean_audience(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Checks the SHAPE of an audience sent by the Admin."""
    errors: List[str] = []
    if not isinstance(raw, dict):
        return {}, ["Choose who this event is published to."]

    kind = str(raw.get("type") or "").strip().lower()
    if kind not in AUDIENCE_TYPES:
        return {}, ["Audience type must be: all, departments, classes or students."]

    clean: Dict[str, Any] = {
        "type": kind, "departments": [], "class_ids": [], "student_ids": [],
    }

    if kind == "departments":
        names = _string_list(raw.get("departments"), "Departments", errors)
        official: List[str] = []
        for name in names:
            canon = canonical_department(name)
            if not canon:
                errors.append(f"'{name}' is not an official department.")
            elif canon not in official:
                official.append(canon)
        if not official and not errors:
            errors.append("Select at least one department.")
        clean["departments"] = official

    elif kind == "classes":
        ids = _string_list(raw.get("class_ids"), "Classes", errors)
        bad = [i for i in ids if not _HEX24.match(i)]
        if bad:
            errors.append("One or more class ids are invalid.")
        if not ids and not errors:
            errors.append("Select at least one class.")
        clean["class_ids"] = ids

    elif kind == "students":
        ids = _string_list(raw.get("student_ids"), "Students", errors)
        bad = [i for i in ids if not _HEX24.match(i)]
        if bad:
            errors.append("One or more student ids are invalid.")
        if not ids and not errors:
            errors.append("Select at least one student.")
        clean["student_ids"] = ids

    return clean, errors


def student_snapshot(student: Dict[str, Any]) -> Dict[str, str]:
    """The only student facts eligibility depends on."""
    dept = str(student.get("department") or "")
    return {
        "id": str(student.get("_id", "")),
        "department": canonical_department(dept) or dept.strip(),
        "class_id": str(student.get("class_id") or ""),
    }


def audience_allows(audience: Any, snap: Optional[Dict[str, str]]) -> bool:
    """
    May a student (described by `snap`) see an event with this audience?
    snap=None means a signed-out visitor / non-student: only 'all' passes.
    """
    if audience is None or audience == {}:
        return True                       # legacy event: unchanged behaviour
    if not isinstance(audience, dict):
        return False                      # malformed -> closed
    kind = audience.get("type")
    if kind == "all":
        return True
    if snap is None:
        return False
    if kind == "departments":
        return bool(snap["department"]) and snap["department"] in (audience.get("departments") or [])
    if kind == "classes":
        return bool(snap["class_id"]) and snap["class_id"] in (audience.get("class_ids") or [])
    if kind == "students":
        return bool(snap["id"]) and snap["id"] in (audience.get("student_ids") or [])
    return False                          # unknown type -> closed


def viewer_can_view(event: Dict[str, Any], viewer: Optional[Dict[str, Any]]) -> bool:
    """Audience rule for a logged-in user document (or None for a visitor)."""
    role = str((viewer or {}).get("role") or "").lower()
    if role in STAFF_ROLES:
        return True                       # organisers/reviewers see all published events as before
    snap = student_snapshot(viewer) if role == "student" else None
    return audience_allows(event.get("audience"), snap)


def mongo_filter_for_student(snap: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """MongoDB filter equal to audience_allows() for list endpoints."""
    clauses: List[Dict[str, Any]] = [
        {"audience": {"$exists": False}},
        {"audience": None},
        {"audience.type": "all"},
    ]
    if snap:
        if snap["department"]:
            clauses.append({"audience.type": "departments",
                            "audience.departments": snap["department"]})
        if snap["class_id"]:
            clauses.append({"audience.type": "classes",
                            "audience.class_ids": snap["class_id"]})
        if snap["id"]:
            clauses.append({"audience.type": "students",
                            "audience.student_ids": snap["id"]})
    return {"$or": clauses}


def _same_department(a: Any, b: Any) -> bool:
    ca, cb = canonical_department(a), canonical_department(b)
    if ca and cb:
        return ca == cb
    return department_key(a) == department_key(b)


def mask_email(email: Any) -> str:
    text = str(email or "")
    if "@" not in text:
        return ""
    local, domain = text.rsplit("@", 1)
    if len(local) <= 2:
        return local[:1] + "***@" + domain
    return local[0] + "***" + local[-1] + "@" + domain


def describe_audience(audience: Any) -> str:
    if not audience:
        return "Everyone (legacy event, no audience set)"
    kind = audience.get("type")
    if kind == "all":
        return "All students"
    if kind == "departments":
        return "Departments: " + ", ".join(audience.get("departments") or [])
    if kind == "classes":
        return f"{len(audience.get('class_ids') or [])} class(es)"
    if kind == "students":
        return f"{len(audience.get('student_ids') or [])} selected student(s)"
    return "Unknown"


# ============================================================
# 2. SERVICE (database work)
# ============================================================

class AudienceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AudienceService:

    def __init__(self, db, get_user_id: Callable[[Optional[str]], str]):
        self.db = db
        self.get_user_id = get_user_id

    # ---------------------------------------------- who is asking
    def viewer(self, authorization: Optional[str]) -> Optional[Dict[str, Any]]:
        """The logged-in account, or None for a visitor / bad token. Never raises."""
        try:
            from bson import ObjectId
            user_id = self.get_user_id(authorization)
            return self.db.students.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            return None

    def can_view(self, event: Dict[str, Any], authorization: Optional[str]) -> bool:
        return viewer_can_view(event, self.viewer(authorization))

    def filter_events(self, events: List[Dict[str, Any]],
                      authorization: Optional[str]) -> List[Dict[str, Any]]:
        viewer = self.viewer(authorization)
        return [e for e in events if viewer_can_view(e, viewer)]

    def student_filter(self, authorization: Optional[str]) -> Dict[str, Any]:
        """Mongo filter for list queries; staff get an empty (match-all) filter."""
        viewer = self.viewer(authorization)
        role = str((viewer or {}).get("role") or "").lower()
        if role in STAFF_ROLES:
            return {}
        snap = student_snapshot(viewer) if role == "student" else None
        return mongo_filter_for_student(snap)

    def require_student_access(self, event: Dict[str, Any], student_id: Any,
                               status_code: int = 404,
                               detail: str = "Event not found") -> None:
        """Student endpoints: refuse (404 by default, so existence is not leaked) if not in audience."""
        from fastapi import HTTPException
        from bson import ObjectId
        try:
            student = self.db.students.find_one({"_id": ObjectId(str(student_id))})
        except Exception:
            student = None
        if student is None:
            return                       # the endpoint's own checks handle this
        if not viewer_can_view(event, student):
            raise HTTPException(status_code=status_code, detail=detail)

    # ---------------------------------------------- building a document
    def _verify_references(self, clean: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
        from bson import ObjectId
        errors: List[str] = []
        snapshot: List[Dict[str, Any]] = []
        if clean["type"] == "classes":
            ids = [ObjectId(i) for i in clean["class_ids"]]
            found = {str(c["_id"]): c for c in self.db.classes.find({"_id": {"$in": ids}})}
            for cid in clean["class_ids"]:
                cls = found.get(cid)
                if cls is None:
                    errors.append("A selected class does not exist.")
                    break
                snapshot.append({
                    "id": cid, "name": cls.get("name", ""),
                    "department": cls.get("department", ""),
                    "academic_year": cls.get("academic_year", ""),
                    "year_of_study": cls.get("year_of_study"),
                    "semester": cls.get("semester"),
                    "section": cls.get("section", ""),
                })
        elif clean["type"] == "students":
            ids = [ObjectId(i) for i in clean["student_ids"]]
            n = self.db.students.count_documents({"_id": {"$in": ids}, "role": "student"})
            if n != len(ids):
                errors.append("A selected student does not exist or is not a student.")
        return errors, snapshot

    def build_document(self, raw: Any, admin: Dict[str, Any],
                       previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        clean, errors = clean_audience(raw)
        if not errors:
            ref_errors, snapshot = self._verify_references(clean)
            errors.extend(ref_errors)
            clean["class_snapshot"] = snapshot
        if errors:
            raise AudienceError(422, " ".join(errors))

        admin_id = str(admin.get("_id", ""))
        now = _now()
        prev = previous if isinstance(previous, dict) else {}
        clean["version"] = int(prev.get("version") or 0) + 1
        clean["set_by"] = prev.get("set_by") or admin_id
        clean["set_at"] = prev.get("set_at") or now
        clean["updated_by"] = admin_id
        clean["updated_at"] = now
        return clean

    # ---------------------------------------------- final approval hook
    def final_audience_for_review(self, payload: Dict[str, Any],
                                  admin: Dict[str, Any],
                                  event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Called by the Admin's final approval. The audience is mandatory: it
        comes from the request, or (for a re-approval) the event's existing one.
        Raises HTTPException(400/422) so the event is NOT published without it.
        """
        from fastapi import HTTPException
        raw = payload.get("audience") if isinstance(payload, dict) else None
        existing = event.get("audience")
        try:
            if raw is None:
                if isinstance(existing, dict) and not clean_audience(existing)[1]:
                    return existing
                raise AudienceError(
                    400, "Select who this event is published to (Publish Event To) before final approval.")
            return self.build_document(raw, admin, existing)
        except AudienceError as error:
            raise HTTPException(status_code=error.status, detail=error.message)

    # ---------------------------------------------- change after publication
    def _active_students(self):
        return list(self.db.students.find(
            {"role": "student"}, {"department": 1, "class_id": 1, "status": 1}))

    def eligible_ids(self, audience: Any) -> List[str]:
        out = []
        for s in self._active_students():
            if str(s.get("status") or "").lower() == "disabled":
                continue
            snap = student_snapshot(s)
            if audience_allows(audience, snap):
                out.append(snap["id"])
        return out

    def preview(self, raw: Any, admin: Dict[str, Any]) -> Dict[str, Any]:
        doc = self.build_document(raw, admin, None)
        total = sum(1 for s in self._active_students()
                    if str(s.get("status") or "").lower() != "disabled")
        return {"eligible_students": len(self.eligible_ids(doc)),
                "total_students": total, "summary": describe_audience(doc)}

    def set_audience(self, admin: Dict[str, Any], event_id: str, raw: Any) -> Dict[str, Any]:
        from bson import ObjectId
        try:
            oid = ObjectId(event_id)
        except Exception:
            raise AudienceError(400, "Invalid event ID.")
        event = self.db.events.find_one({"_id": oid})
        if not event:
            raise AudienceError(404, "Event not found.")
        status = event.get("approval_status")
        if status is not None and status != "published":
            raise AudienceError(
                409, "The audience can be changed after publication. Before that it is "
                     "chosen during final approval.")

        previous = event.get("audience") if isinstance(event.get("audience"), dict) else None
        doc = self.build_document(raw, admin, previous)

        if previous:
            guard = {"_id": oid, "audience.version": previous.get("version")}
        else:
            guard = {"_id": oid, "$or": [{"audience": {"$exists": False}}, {"audience": None}]}

        history = {
            "version": doc["version"],
            "previous": describe_audience(previous),
            "new": describe_audience(doc),
            "changed_by": str(admin.get("_id", "")),
            "changed_by_name": admin.get("name", ""),
            "changed_at": _now(),
        }
        result = self.db.events.update_one(
            guard, {"$set": {"audience": doc}, "$push": {"audience_history": history}})
        if result.modified_count != 1:
            raise AudienceError(409, "Someone else changed this audience. Refresh and try again.")

        gained, lost, gained_ids = self._log_changes(event_id, previous, doc, admin)
        notified = self._notify_gained(event, doc, gained_ids)
        return {
            "status": "success",
            "event_id": event_id,
            "audience": serialise_audience(doc),
            "newly_eligible": gained,
            "no_longer_eligible": lost,
            "students_notified": notified,
            "eligible_students": len(self.eligible_ids(doc)),
        }

    def _notify_gained(self, event, audience, student_ids) -> int:
        """Tell newly eligible students once per audience version (never twice)."""
        if not student_ids:
            return 0
        event_id = str(event["_id"])
        keys = {sid: f"audience:{event_id}:{audience.get('version')}:{sid}" for sid in student_ids}
        try:
            seen = {n.get("dedupe_key") for n in self.db.notifications.find(
                {"dedupe_key": {"$in": list(keys.values())}})}
            now = _now()
            rows = [{
                "title": "New event for you",
                "message": f'"{event.get("title", "An event")}" is now open to you. '
                           f'It takes place on {event.get("date", "")} {event.get("time", "")}.'.replace("  ", " "),
                "type": "event_available", "audience": "student", "recipient": sid,
                "related_id": event_id, "is_read": False, "created_at": now, "dedupe_key": key,
            } for sid, key in keys.items() if key not in seen]
            if rows:
                self.db.notifications.insert_many(rows, ordered=False)
            return len(rows)
        except Exception as error:               # a notification problem must not undo the audience change
            print("AUDIENCE NOTIFICATION WARNING:", repr(error))
            return 0

    def _log_changes(self, event_id, old, new, admin) -> Tuple[int, int, List[str]]:
        """Record who gained / lost access. Registrations and attendance are untouched."""
        rows, gained, lost, gained_ids = [], 0, 0, []
        now = _now()
        for s in self._active_students():
            if str(s.get("status") or "").lower() == "disabled":
                continue
            snap = student_snapshot(s)
            before = audience_allows(old, snap)
            after = audience_allows(new, snap)
            if before == after:
                continue
            gained += 1 if after else 0
            lost += 0 if after else 1
            if after:
                gained_ids.append(snap["id"])
            rows.append({
                "student_id": snap["id"], "event_id": event_id,
                "previous_eligible": before, "new_eligible": after,
                "reason": "audience_changed", "audience_version": new.get("version"),
                "changed_by": str(admin.get("_id", "")), "changed_at": now,
            })
        if rows:
            try:
                self.db[ELIGIBILITY_LOG].insert_many(rows, ordered=False)
            except Exception as error:      # logging must not undo the change
                print("ELIGIBILITY LOG ERROR:", repr(error))
        return gained, lost, gained_ids

    # ---------------------------------------------- pickers for the Admin UI
    def list_classes(self, department="", academic_year="", year_of_study=None,
                     semester=None) -> List[Dict[str, Any]]:
        out = []
        for c in self.db.classes.find({"is_active": {"$ne": False}}):
            if department and not _same_department(c.get("department"), department):
                continue
            if academic_year and c.get("academic_year") != academic_year:
                continue
            if year_of_study is not None and c.get("year_of_study") != year_of_study:
                continue
            if semester is not None and c.get("semester") != semester:
                continue
            out.append({
                "id": str(c["_id"]), "name": c.get("name", ""),
                "department": c.get("department", ""),
                "academic_year": c.get("academic_year", ""),
                "year_of_study": c.get("year_of_study"),
                "semester": c.get("semester"),
                "section": c.get("section", ""), "batch": c.get("batch") or "",
            })
        out.sort(key=lambda r: (r["department"], r["academic_year"],
                                r["semester"] or 0, r["section"]))
        return out

    def search_students(self, q: str, department="", class_id="", limit=25) -> List[Dict[str, Any]]:
        q = (q or "").strip()
        if len(q) < 2 and not class_id and not department:
            return []
        limit = max(1, min(int(limit), 50))
        query: Dict[str, Any] = {"role": "student", "status": {"$ne": "disabled"}}
        if q:
            pattern = {"$regex": re.escape(q), "$options": "i"}
            query["$or"] = [{"name": pattern}, {"email": pattern}]
        if department:
            canon = canonical_department(department)
            query["department"] = canon or department
        if class_id:
            query["class_id"] = class_id
        rows = list(self.db.students.find(
            query, {"name": 1, "email": 1, "department": 1, "class_id": 1}).limit(limit))
        class_names = {}
        ids = [r.get("class_id") for r in rows if r.get("class_id")]
        if ids:
            from bson import ObjectId
            valid = []
            for i in ids:
                try:
                    valid.append(ObjectId(i))
                except Exception:
                    pass
            class_names = {str(c["_id"]): c.get("name", "")
                           for c in self.db.classes.find({"_id": {"$in": valid}})}
        # Only what the Admin needs to tell two students apart.
        return [{
            "id": str(r["_id"]), "name": r.get("name", ""),
            "email_masked": mask_email(r.get("email")),
            "department": r.get("department", ""),
            "class_name": class_names.get(str(r.get("class_id") or ""), ""),
        } for r in rows]

    def students_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        from bson import ObjectId
        valid = []
        for i in ids[:MAX_SELECTED]:
            try:
                valid.append(ObjectId(i))
            except Exception:
                pass
        rows = list(self.db.students.find(
            {"_id": {"$in": valid}, "role": "student"},
            {"name": 1, "email": 1, "department": 1, "class_id": 1}))
        return [{"id": str(r["_id"]), "name": r.get("name", ""),
                 "email_masked": mask_email(r.get("email")),
                 "department": r.get("department", ""), "class_name": ""} for r in rows]

    def events_needing_audience(self) -> List[Dict[str, Any]]:
        rows = self.db.events.find(
            {"$and": [
                {"$or": [{"approval_status": "published"}, {"approval_status": {"$exists": False}}]},
                {"$or": [{"audience": {"$exists": False}}, {"audience": None}]},
            ]},
            {"title": 1, "date": 1, "organizer": 1})
        return [{"id": str(r["_id"]), "title": r.get("title", ""),
                 "date": r.get("date", ""), "organizer": r.get("organizer", "")}
                for r in rows]

    def published_events_overview(self) -> List[Dict[str, Any]]:
        """Every published event with a one-line audience summary (Admin screen)."""
        rows = self.db.events.find(
            {"$or": [{"approval_status": "published"}, {"approval_status": {"$exists": False}}]},
            {"title": 1, "date": 1, "organizer": 1, "audience": 1}).sort("_id", -1)
        out = []
        for r in rows:
            aud = r.get("audience") if isinstance(r.get("audience"), dict) else None
            out.append({
                "id": str(r["_id"]), "title": r.get("title", ""),
                "date": r.get("date", ""), "organizer": r.get("organizer", ""),
                "summary": describe_audience(aud),
                "type": (aud or {}).get("type", ""),
                "version": (aud or {}).get("version"),
                "is_legacy": aud is None,
            })
        return out

    def ensure_indexes(self) -> None:
        for coll, keys in (
            (self.db[ELIGIBILITY_LOG], [("event_id", 1), ("student_id", 1), ("changed_at", -1)]),
            (self.db.events, [("audience.type", 1)]),
        ):
            try:
                coll.create_index(keys)
            except Exception as error:
                print("AUDIENCE INDEX WARNING:", repr(error))


def serialise_audience(audience: Any) -> Optional[Dict[str, Any]]:
    """JSON-safe copy (datetimes -> text) of an audience document."""
    if not isinstance(audience, dict):
        return None
    out = dict(audience)
    for key in ("set_at", "updated_at"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out


# ============================================================
# 3. ADMIN API  (all checks happen here, on the server)
# ============================================================

def install(app, db, get_user_id: Callable[[Optional[str]], str]) -> AudienceService:
    """
    One call from main.py:   audience = install(app, db, get_student_id_from_token)
    Registers the Admin routes and the index creation at startup.
    """
    from bson import ObjectId
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel

    service = AudienceService(db, get_user_id)
    router = APIRouter()

    class AudienceBody(BaseModel):
        type: str
        departments: Optional[List[str]] = None
        class_ids: Optional[List[str]] = None
        student_ids: Optional[List[str]] = None

    def require_admin(authorization: Optional[str]) -> Dict[str, Any]:
        user_id = get_user_id(authorization)          # 401 / disabled handled here
        try:
            user = db.students.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            user = None
        if not user or user.get("role") != "admin" or \
                str(user.get("status") or "active").lower() == "disabled":
            raise HTTPException(status_code=403, detail="Only the Admin can manage event audiences.")
        return user

    def run(fn):
        try:
            return fn()
        except AudienceError as error:
            raise HTTPException(status_code=error.status, detail=error.message)

    def raw_body(body: AudienceBody) -> Dict[str, Any]:
        return body.model_dump() if hasattr(body, "model_dump") else body.dict()

    @router.get("/api/admin/audience/options")
    def audience_options(authorization: str = Header(None)):
        require_admin(authorization)
        classes = service.list_classes()
        return {
            "departments": list(OFFICIAL_DEPARTMENTS),
            "academic_years": sorted({c["academic_year"] for c in classes if c["academic_year"]}),
            "years_of_study": sorted({c["year_of_study"] for c in classes if c["year_of_study"]}),
            "semesters": sorted({c["semester"] for c in classes if c["semester"]}),
        }

    @router.get("/api/admin/audience/classes")
    def audience_classes(department: str = "", academic_year: str = "",
                         year_of_study: Optional[int] = None,
                         semester: Optional[int] = None,
                         authorization: str = Header(None)):
        require_admin(authorization)
        return {"classes": service.list_classes(department, academic_year, year_of_study, semester)}

    @router.get("/api/admin/audience/students")
    def audience_students(q: str = "", department: str = "", class_id: str = "",
                          limit: int = 25, authorization: str = Header(None)):
        require_admin(authorization)
        return {"students": service.search_students(q, department, class_id, limit)}

    @router.post("/api/admin/audience/preview")
    def audience_preview(body: AudienceBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        return run(lambda: service.preview(raw_body(body), admin))

    @router.get("/api/admin/audience/events")
    def audience_events(authorization: str = Header(None)):
        require_admin(authorization)
        return {"events": service.published_events_overview()}

    @router.get("/api/admin/audience/needs-audience")
    def audience_needs(authorization: str = Header(None)):
        require_admin(authorization)
        return {"events": service.events_needing_audience()}

    @router.get("/api/admin/events/{event_id}/audience")
    def get_audience(event_id: str, authorization: str = Header(None)):
        require_admin(authorization)
        try:
            event = db.events.find_one({"_id": ObjectId(event_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid event ID.")
        if not event:
            raise HTTPException(status_code=404, detail="Event not found.")
        audience = event.get("audience") if isinstance(event.get("audience"), dict) else None
        history = []
        for h in event.get("audience_history") or []:
            h = dict(h)
            if isinstance(h.get("changed_at"), datetime):
                h["changed_at"] = h["changed_at"].isoformat()
            history.append(h)
        return {
            "event_id": event_id,
            "audience": serialise_audience(audience),
            "summary": describe_audience(audience),
            "selected_students": (service.students_by_ids(audience.get("student_ids") or [])
                                 if audience and audience.get("type") == "students" else []),
            "is_legacy": audience is None,
            "history": history,
        }

    @router.put("/api/admin/events/{event_id}/audience")
    def put_audience(event_id: str, body: AudienceBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        return run(lambda: service.set_audience(admin, event_id, raw_body(body)))

    app.include_router(router)

    # Same startup style main.py already uses (@app.on_event). Creating the
    # indexes is a convenience, so a failure here must never stop the server.
    @app.on_event("startup")
    def _create_audience_indexes():
        try:
            service.ensure_indexes()
        except Exception as error:
            print("AUDIENCE INDEX WARNING:", repr(error))

    return service