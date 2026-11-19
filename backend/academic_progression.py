"""
academic_progression.py
=======================
Phase 4 + 5: dynamic eligibility and Admin-controlled bulk promotion.

Put this file next to main.py and event_audience.py.

How the academic data is organised (read this first)
----------------------------------------------------
* A class (db.classes) carries department, academic_year, year_of_study,
  semester and section. A student points to it with `students.class_id`.
* Promotion needs a record that belongs to the STUDENT, so the official
  academic record is stored on the student as:
        students.academic = {academic_year, year_of_study, semester,
                             updated_at, updated_by, batch_id}
        students.academic_version = 0, 1, 2 ...   (optimistic lock)
  A student's CURRENT academic state = `academic` if present, otherwise
  the values of the class they are in.
* The class (`class_id`, which also drives the timetable) is changed ONLY
  when the Admin explicitly picks a target class. Student timetable
  data is never copied or edited here: it is resolved from the class.

Collections added (all additive, nothing existing is changed)
-------------------------------------------------------------
  promotion_batches  one row per "Promote Selected Students" click
  academic_history   one row per student per change (promotion, correction,
                     class/department change). Never deleted.
  event_eligibility_log  (from event_audience.py) who gained / lost an event.

Safety rules enforced on the server
-----------------------------------
* Only an Admin can promote, correct, or change a student's class/department.
* Duplicate clicks / retries: a unique `request_id` per batch, AND every
  student is only promoted if their current semester is below the target.
* Concurrent edits: each student update checks `academic_version`.
* Corrections never delete history; they add a new row that points to the
  original, with a mandatory reason.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from departments import OFFICIAL_DEPARTMENTS, canonical_department
from event_audience import (
    ELIGIBILITY_LOG,
    MAX_SELECTED,
    _same_department,
    audience_allows,
    mask_email,
    student_snapshot,
)

BATCHES = "promotion_batches"
HISTORY = "academic_history"
MAX_BATCH = 2000
_ACADEMIC_YEAR_RE = re.compile(r"^(\d{4})-(\d{4})$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProgressionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ============================================================
# 1. PURE RULES
# ============================================================

def validate_target(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """academic_year 'YYYY-YYYY' (consecutive), year 1-4, semester 1-8, and they must agree."""
    errors: List[str] = []
    if not isinstance(raw, dict):
        return {}, ["Choose the target academic year, year and semester."]

    text = " ".join(str(raw.get("academic_year") or "").split())
    m = _ACADEMIC_YEAR_RE.match(text)
    if not m:
        errors.append("Target academic year must look like 2026-2027.")
    elif int(m.group(2)) != int(m.group(1)) + 1:
        errors.append("Target academic year must span consecutive years, e.g. 2026-2027.")

    def whole(key: str) -> Optional[int]:
        value = raw.get(key)
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    year, sem = whole("year_of_study"), whole("semester")
    if year is None or not 1 <= year <= 4:
        errors.append("Target year must be 1 to 4.")
    if sem is None or not 1 <= sem <= 8:
        errors.append("Target semester must be 1 to 8.")
    if year and sem and not errors and (sem + 1) // 2 != year:
        errors.append(f"Semester {sem} belongs to Year {(sem + 1) // 2}, not Year {year}.")
    if errors:
        return {}, errors
    return {"academic_year": text, "year_of_study": year, "semester": sem}, []


def effective_academic(student: Dict[str, Any], cls: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The student's current academic state, or None if nothing is known yet."""
    own = student.get("academic")
    if isinstance(own, dict) and own.get("semester"):
        return {"academic_year": own.get("academic_year", ""),
                "year_of_study": own.get("year_of_study"),
                "semester": own.get("semester"), "source": "student"}
    if cls and cls.get("semester"):
        return {"academic_year": cls.get("academic_year", ""),
                "year_of_study": cls.get("year_of_study"),
                "semester": cls.get("semester"), "source": "class"}
    return None


def plan_one(student: Dict[str, Any], cls: Optional[Dict[str, Any]], target: Dict[str, Any],
             target_cls: Optional[Dict[str, Any]],
             expected_semester: Optional[int]) -> Tuple[bool, str]:
    """May this student be promoted to `target`? (ok, reason-if-not)."""
    if str(student.get("status") or "").lower() == "disabled":
        return False, "Account is disabled."
    eff = effective_academic(student, cls)
    if eff is None:
        return False, "No academic record yet. The student must have a class first."
    if expected_semester is not None and eff["semester"] != expected_semester:
        return False, f"Semester changed since you previewed (now semester {eff['semester']})."
    if int(eff["semester"]) >= target["semester"]:
        return False, f"Already in semester {eff['semester']} (target is {target['semester']})."
    if target_cls is not None and not _same_department(student.get("department"), target_cls.get("department")):
        return False, "The selected class belongs to another department."
    return True, ""


_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def year_label(year: int, example: Any = "") -> str:
    """
    '3rd Year' in the same style the student's profile already uses
    ('4th Year' / 'Year 4' / '4'), so existing screens keep working.
    """
    ex = str(example or "")
    m = re.match(r"^\s*\d(?:st|nd|rd|th)(\s+)(year)\s*$", ex, re.I)
    if m:
        return f"{_ORD[year]}{m.group(1)}{m.group(2)}"
    m = re.match(r"^\s*(year)(\s*)\d\s*$", ex, re.I)
    if m:
        return f"{m.group(1)}{m.group(2)}{year}"
    if re.match(r"^\s*\d\s*$", ex):
        return str(year)
    return f"{_ORD[year]} Year"


def eligibility_rows(events: List[Dict[str, Any]], changes: List[Dict[str, Any]],
                     reason: str, actor_id: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Pure: which (student, event) pairs changed eligibility."""
    now = now or _now()
    rows = []
    for ch in changes:
        before, after = ch["before"], ch["after"]
        if before == after:
            continue
        for ev in events:
            aud = ev.get("audience")
            was, now_ok = audience_allows(aud, before), audience_allows(aud, after)
            if was != now_ok:
                rows.append({
                    "student_id": ch["student_id"], "event_id": str(ev["_id"]),
                    "previous_eligible": was, "new_eligible": now_ok,
                    "reason": reason, "audience_version": aud.get("version"),
                    "changed_by": actor_id, "changed_at": now,
                })
    return rows


# ============================================================
# 2. ELIGIBILITY RECALCULATION  (also used by the class hooks)
# ============================================================

def reconcile_eligibility(db, changes: List[Dict[str, Any]], reason: str,
                          actor_id: str = "") -> Tuple[int, int]:
    """
    After academic changes, record who gained / lost access to published
    events that target departments or classes. (Events for 'all' or for
    individually selected students are unaffected by definition.)
    Access itself is computed live, so nothing needs to be 'revoked' here;
    this writes the history. Registrations / attendance are never touched.
    """
    changes = [c for c in changes if c["before"] != c["after"]]
    if not changes:
        return 0, 0
    events = list(db.events.find({
        "audience.type": {"$in": ["departments", "classes"]},
        "$or": [{"approval_status": "published"}, {"approval_status": {"$exists": False}}],
    }, {"audience": 1}))
    rows = eligibility_rows(events, changes, reason, actor_id)
    if rows:
        try:
            for i in range(0, len(rows), 1000):
                db[ELIGIBILITY_LOG].insert_many(rows[i:i + 1000], ordered=False)
        except Exception as error:         # history must not undo the change
            print("ELIGIBILITY LOG ERROR:", repr(error))
    gained = sum(1 for r in rows if r["new_eligible"])
    return gained, len(rows) - gained


# ============================================================
# 3. HOOKS FOR class_timetable.py  (student chooses their own class)
# ============================================================

def guard_student_class_change(db, student: Dict[str, Any]) -> None:
    """
    Official academic data may only be changed by staff. A student may pick a
    class when they have none, or when their class was removed / deactivated.
    """
    from bson import ObjectId
    from fastapi import HTTPException
    class_id = student.get("class_id")
    if not class_id:
        return
    try:
        cls = db.classes.find_one({"_id": ObjectId(str(class_id))})
    except Exception:
        cls = None
    if cls is None or not cls.get("is_active", True):
        return
    raise HTTPException(
        status_code=403,
        detail="Your class can only be changed by the Admin. Please contact the Admin office.")


def _official_year(db, student: Dict[str, Any]) -> Optional[int]:
    from bson import ObjectId
    cls = None
    if student.get("class_id"):
        try:
            cls = db.classes.find_one({"_id": ObjectId(str(student["class_id"]))})
        except Exception:
            cls = None
    eff = effective_academic(student, cls)
    year = (eff or {}).get("year_of_study")
    return year if isinstance(year, int) and 1 <= year <= 4 else None


def official_profile_year(db, student_id: Any, submitted: Any = "") -> Any:
    """
    The year a student's profile must show. Once the university has an official
    record for the student it wins over anything typed by the student, so the
    profile cannot be used to claim a different year. No record yet -> keep as typed.
    """
    try:
        from bson import ObjectId
        student = db.students.find_one({"_id": ObjectId(str(student_id))})
        year = _official_year(db, student) if student else None
        return year_label(year, submitted) if year else submitted
    except Exception:
        return submitted


def sync_profile_year(db, student_id: Any) -> bool:
    """Copy the official year onto the profile (never creates a profile). Never raises."""
    try:
        from bson import ObjectId
        student = db.students.find_one({"_id": ObjectId(str(student_id))})
        if not student:
            return False
        year = _official_year(db, student)
        profile = db.profiles.find_one({"student_id": str(student_id)})
        if not year or not profile:
            return False
        label = year_label(year, profile.get("year"))
        if profile.get("year") == label:
            return False
        db.profiles.update_one({"student_id": str(student_id)}, {"$set": {"year": label}})
        return True
    except Exception as error:
        print("PROFILE YEAR SYNC ERROR:", repr(error))
        return False


def record_student_class_change(db, student_id: str, before: Dict[str, str], reason: str) -> None:
    """Never raises. Writes history + eligibility log after a class was set."""
    try:
        from bson import ObjectId
        after_doc = db.students.find_one({"_id": ObjectId(str(student_id))})
        if not after_doc:
            return
        after = student_snapshot(after_doc)
        if before == after:
            return
        db[HISTORY].insert_one({
            "kind": "class_change", "student_id": str(student_id),
            "student_name": after_doc.get("name", ""),
            "previous": {"class_id": before.get("class_id", ""), "department": before.get("department", "")},
            "new": {"class_id": after.get("class_id", ""), "department": after.get("department", "")},
            "reason": reason, "admin_id": "", "promoted_at": _now(),
        })
        reconcile_eligibility(db, [{"student_id": str(student_id), "before": before, "after": after}],
                              reason, str(student_id))
        sync_profile_year(db, student_id)
    except Exception as error:
        print("CLASS CHANGE RECORD ERROR:", repr(error))


# ============================================================
# 4. SERVICE
# ============================================================

def _ser(value: Any) -> Any:
    """JSON-safe copy (ObjectId -> str, datetime -> iso)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _ser(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ser(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class ProgressionService:

    def __init__(self, db):
        self.db = db

    # ---------------- lookups
    def _oid(self, value: Any, label: str):
        from bson import ObjectId
        try:
            return ObjectId(str(value))
        except Exception:
            raise ProgressionError(400, f"Invalid {label}.")

    def _classes(self) -> Dict[str, Dict[str, Any]]:
        return {str(c["_id"]): c for c in self.db.classes.find({})}

    def _student(self, student_id: Any) -> Optional[Dict[str, Any]]:
        return self.db.students.find_one({"_id": self._oid(student_id, "student ID"), "role": "student"})

    def _target_class(self, class_id: Optional[str], target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not class_id:
            return None
        cls = self.db.classes.find_one({"_id": self._oid(class_id, "class ID")})
        if not cls:
            raise ProgressionError(404, "The selected target class does not exist.")
        if not cls.get("is_active", True):
            raise ProgressionError(409, "The selected target class is not active.")
        if (cls.get("academic_year") != target["academic_year"]
                or cls.get("year_of_study") != target["year_of_study"]
                or cls.get("semester") != target["semester"]):
            raise ProgressionError(
                422, "The selected class does not match the target academic year, year and semester.")
        return cls

    # ---------------- filters + student list
    def filter_options(self) -> Dict[str, Any]:
        classes = [c for c in self._classes().values() if c.get("is_active", True)]
        return {
            "departments": list(OFFICIAL_DEPARTMENTS),
            "academic_years": sorted({c.get("academic_year") for c in classes if c.get("academic_year")}),
            "years_of_study": [1, 2, 3, 4],
            "semesters": list(range(1, 9)),
            "classes": sorted([{
                "id": str(c["_id"]), "name": c.get("name", ""),
                "department": c.get("department", ""), "academic_year": c.get("academic_year", ""),
                "year_of_study": c.get("year_of_study"), "semester": c.get("semester"),
                "section": c.get("section", ""),
            } for c in classes], key=lambda r: (r["department"], r["academic_year"], r["semester"] or 0, r["section"])),
        }

    def list_students(self, department="", academic_year="", year_of_study=None, semester=None,
                      class_id="", q="", limit=500) -> Dict[str, Any]:
        limit = max(1, min(int(limit), MAX_BATCH))
        classes = self._classes()
        q = (q or "").strip().lower()
        out = []
        for s in self.db.students.find({"role": "student"}):
            cls = classes.get(str(s.get("class_id") or ""))
            eff = effective_academic(s, cls)
            if department and not _same_department(s.get("department"), department):
                continue
            if class_id and str(s.get("class_id") or "") != class_id:
                continue
            if academic_year or year_of_study is not None or semester is not None:
                if eff is None:
                    continue
                if academic_year and eff["academic_year"] != academic_year:
                    continue
                if year_of_study is not None and eff["year_of_study"] != year_of_study:
                    continue
                if semester is not None and eff["semester"] != semester:
                    continue
            if q and q not in f"{s.get('name', '')} {s.get('email', '')}".lower():
                continue
            out.append({
                "id": str(s["_id"]), "name": s.get("name", ""), "email_masked": mask_email(s.get("email")),
                "department": s.get("department", ""),
                "class_id": str(s.get("class_id") or ""), "class_name": (cls or {}).get("name", ""),
                "academic_year": (eff or {}).get("academic_year", ""),
                "year_of_study": (eff or {}).get("year_of_study"),
                "semester": (eff or {}).get("semester"),
                "has_academic": eff is not None,
                "disabled": str(s.get("status") or "").lower() == "disabled",
            })
        out.sort(key=lambda r: r["name"].lower())
        return {"total_matching": len(out), "students": out[:limit]}

    # ---------------- planning / preview / apply
    def _prepare(self, student_ids: Any, raw_target: Any, target_class_id: Optional[str]):
        target, errors = validate_target(raw_target)
        if errors:
            raise ProgressionError(422, " ".join(errors))
        if not isinstance(student_ids, list) or not student_ids:
            raise ProgressionError(422, "Select at least one student.")
        ids = list(dict.fromkeys(str(i) for i in student_ids))
        if len(ids) > MAX_BATCH:
            raise ProgressionError(422, f"At most {MAX_BATCH} students can be promoted at once.")
        return target, ids, self._target_class(target_class_id, target)

    def preview(self, student_ids, raw_target, target_class_id=None, expected_semester=None) -> Dict[str, Any]:
        target, ids, tcls = self._prepare(student_ids, raw_target, target_class_id)
        classes = self._classes()
        ok, skipped = 0, []
        for sid in ids:
            s = self._student(sid)
            if not s:
                skipped.append({"student_id": sid, "name": "", "reason": "Student not found."})
                continue
            good, why = plan_one(s, classes.get(str(s.get("class_id") or "")), target, tcls, expected_semester)
            if good:
                ok += 1
            else:
                skipped.append({"student_id": sid, "name": s.get("name", ""), "reason": why})
        return {"selected": len(ids), "will_promote": ok, "will_skip": len(skipped),
                "skipped": skipped[:100], "target": target}

    def _guarded_update(self, student: Dict[str, Any], set_fields: Dict[str, Any]) -> bool:
        version = int(student.get("academic_version") or 0)
        guard: Dict[str, Any] = {"_id": student["_id"]}
        guard["academic_version"] = version if version else {"$in": [0, None]}
        result = self.db.students.update_one(guard, {"$set": set_fields, "$inc": {"academic_version": 1}})
        return result.modified_count == 1

    def apply_promotion(self, admin: Dict[str, Any], request_id: Any, student_ids, raw_target,
                        target_class_id=None, expected_semester=None) -> Dict[str, Any]:
        from pymongo.errors import DuplicateKeyError
        request_id = str(request_id or "").strip()
        if not 8 <= len(request_id) <= 80:
            raise ProgressionError(422, "A request id is required (it prevents double promotion).")
        target, ids, tcls = self._prepare(student_ids, raw_target, target_class_id)

        admin_id = str(admin.get("_id", ""))
        batch_id = uuid.uuid4().hex
        now = _now()
        try:
            self.db[BATCHES].insert_one({
                "batch_id": batch_id, "request_id": request_id, "status": "running",
                "admin_id": admin_id, "admin_name": admin.get("name", ""), "created_at": now,
                "target": target, "target_class_id": str(tcls["_id"]) if tcls else "",
                "selected_count": len(ids), "success_count": 0, "failed_count": 0, "failures": [],
            })
        except DuplicateKeyError:
            existing = self.db[BATCHES].find_one({"request_id": request_id}) or {}
            return {"duplicate": True, **self._batch_summary(existing)}

        classes = self._classes()
        done, failures, changes = [], [], []
        for sid in ids:
            s = self._student(sid)
            if not s:
                failures.append({"student_id": sid, "name": "", "reason": "Student not found."})
                continue
            cls = classes.get(str(s.get("class_id") or ""))
            good, why = plan_one(s, cls, target, tcls, expected_semester)
            if not good:
                failures.append({"student_id": sid, "name": s.get("name", ""), "reason": why})
                continue

            eff = effective_academic(s, cls)
            before = student_snapshot(s)
            new_class_id = str(tcls["_id"]) if tcls else before["class_id"]
            set_fields: Dict[str, Any] = {"academic": {
                **target, "updated_at": now, "updated_by": admin_id, "batch_id": batch_id}}
            if tcls:
                set_fields["class_id"] = new_class_id
                set_fields["class_assigned_at"] = now
            if not self._guarded_update(s, set_fields):
                failures.append({"student_id": sid, "name": s.get("name", ""),
                                 "reason": "Changed by someone else just now. Try again."})
                continue

            self.db[HISTORY].insert_one({
                "kind": "promotion", "batch_id": batch_id, "student_id": sid,
                "student_name": s.get("name", ""),
                "previous": {"academic_year": eff["academic_year"], "year_of_study": eff["year_of_study"],
                             "semester": eff["semester"], "class_id": before["class_id"]},
                "new": {**target, "class_id": new_class_id},
                "promoted_at": now, "admin_id": admin_id, "reason": "",
            })
            done.append(sid)
            sync_profile_year(self.db, sid)
            changes.append({"student_id": sid, "before": before,
                            "after": {**before, "class_id": new_class_id}})

        gained, lost = reconcile_eligibility(self.db, changes, "promotion", admin_id)
        self.db[BATCHES].update_one({"batch_id": batch_id}, {"$set": {
            "status": "completed", "success_count": len(done), "failed_count": len(failures),
            "failures": failures[:200], "eligibility_gained": gained, "eligibility_lost": lost,
            "completed_at": _now()}})
        return {"duplicate": False, "batch_id": batch_id, "selected": len(ids), "promoted": len(done),
                "failed": len(failures), "failures": failures[:100],
                "eligibility_gained": gained, "eligibility_lost": lost}

    def _batch_summary(self, b: Dict[str, Any]) -> Dict[str, Any]:
        return {"batch_id": b.get("batch_id", ""), "status": b.get("status", ""),
                "selected": b.get("selected_count", 0), "promoted": b.get("success_count", 0),
                "failed": b.get("failed_count", 0), "failures": (b.get("failures") or [])[:100],
                "eligibility_gained": b.get("eligibility_gained", 0),
                "eligibility_lost": b.get("eligibility_lost", 0)}

    # ---------------- history
    def history(self, batch_id="", student_id="", limit=100) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        if batch_id or student_id:
            query: Dict[str, Any] = {}
            if batch_id:
                query["batch_id"] = batch_id
            if student_id:
                query["student_id"] = student_id
            rows = list(self.db[HISTORY].find(query).sort("promoted_at", -1).limit(limit))
            return {"rows": [_ser({**r, "id": str(r["_id"])}) for r in rows]}
        batches = list(self.db[BATCHES].find({}).sort("created_at", -1).limit(50))
        return {"batches": [_ser({**self._batch_summary(b), "created_at": b.get("created_at"),
                                  "admin_name": b.get("admin_name", ""), "target": b.get("target", {})})
                            for b in batches]}

    # ---------------- correction (never deletes history)
    def correct(self, admin: Dict[str, Any], history_id: str, raw_corrected: Any,
                class_id: Optional[str], reason: Any) -> Dict[str, Any]:
        reason = " ".join(str(reason or "").split())
        if len(reason) < 5:
            raise ProgressionError(422, "A reason (at least 5 characters) is required for a correction.")
        hid = self._oid(history_id, "history ID")
        row = self.db[HISTORY].find_one({"_id": hid})
        if not row or row.get("kind") != "promotion":
            raise ProgressionError(404, "Promotion record not found.")
        if row.get("corrected_by"):
            raise ProgressionError(409, "This promotion was already corrected.")

        prev = row.get("previous") or {}
        if raw_corrected in (None, {}):
            corrected = {"academic_year": prev.get("academic_year"),
                         "year_of_study": prev.get("year_of_study"), "semester": prev.get("semester")}
        else:
            corrected = raw_corrected
        clean, errors = validate_target(corrected)
        if errors:
            raise ProgressionError(422, " ".join(errors))

        student = self._student(row.get("student_id"))
        if not student:
            raise ProgressionError(404, "Student not found.")
        classes = self._classes()
        current = effective_academic(student, classes.get(str(student.get("class_id") or ""))) or {}
        new_of_row = row.get("new") or {}
        if current.get("semester") != new_of_row.get("semester"):
            raise ProgressionError(
                409, "This student has moved on since that promotion. Correct their latest record instead.")

        # class: explicit choice, else restore the old class if the promotion changed it
        target_class_id = class_id
        if not target_class_id and prev.get("class_id") != new_of_row.get("class_id"):
            target_class_id = prev.get("class_id") or None
        tcls = None
        if target_class_id:
            tcls = self.db.classes.find_one({"_id": self._oid(target_class_id, "class ID")})
            if not tcls:
                raise ProgressionError(404, "The class to restore no longer exists.")

        # claim the record so two Admins cannot correct it twice
        claim = self.db[HISTORY].update_one(
            {"_id": hid, "corrected_by": {"$in": [None]}}, {"$set": {"corrected_by": "in_progress"}})
        if claim.modified_count != 1:
            raise ProgressionError(409, "This promotion was already corrected.")

        admin_id, now = str(admin.get("_id", "")), _now()
        before = student_snapshot(student)
        new_class = str(tcls["_id"]) if tcls else before["class_id"]
        set_fields: Dict[str, Any] = {"academic": {
            **clean, "updated_at": now, "updated_by": admin_id, "batch_id": row.get("batch_id", "")}}
        if tcls:
            set_fields["class_id"] = new_class
            set_fields["class_assigned_at"] = now
        if not self._guarded_update(student, set_fields):
            self.db[HISTORY].update_one({"_id": hid}, {"$unset": {"corrected_by": ""}})
            raise ProgressionError(409, "Changed by someone else just now. Try again.")

        result = self.db[HISTORY].insert_one({
            "kind": "correction", "corrects": str(hid), "batch_id": row.get("batch_id", ""),
            "student_id": row.get("student_id"), "student_name": student.get("name", ""),
            "previous": {**new_of_row}, "new": {**clean, "class_id": new_class},
            "original_promotion": {"previous": prev, "new": new_of_row},
            "promoted_at": now, "admin_id": admin_id, "admin_name": admin.get("name", ""),
            "reason": reason,
        })
        self.db[HISTORY].update_one({"_id": hid}, {"$set": {"corrected_by": str(result.inserted_id)}})
        sync_profile_year(self.db, str(student["_id"]))
        gained, lost = reconcile_eligibility(
            self.db, [{"student_id": str(student["_id"]), "before": before,
                       "after": {**before, "class_id": new_class}}], "promotion_correction", admin_id)
        return {"status": "success", "correction_id": str(result.inserted_id),
                "eligibility_gained": gained, "eligibility_lost": lost}

    # ---------------- Admin changes one student's official class / department
    def admin_update_student(self, admin: Dict[str, Any], student_id: str,
                             class_id: Optional[str], department: Optional[str], reason: Any) -> Dict[str, Any]:
        reason = " ".join(str(reason or "").split())
        if len(reason) < 5:
            raise ProgressionError(422, "A reason (at least 5 characters) is required.")
        if not class_id and not department:
            raise ProgressionError(422, "Choose a class and/or a department to change.")
        student = self._student(student_id)
        if not student:
            raise ProgressionError(404, "Student not found.")

        set_fields: Dict[str, Any] = {}
        before = student_snapshot(student)
        now, admin_id = _now(), str(admin.get("_id", ""))
        if class_id:
            cls = self.db.classes.find_one({"_id": self._oid(class_id, "class ID")})
            if not cls:
                raise ProgressionError(404, "Class not found.")
            if not cls.get("is_active", True):
                raise ProgressionError(409, "That class is not active.")
            set_fields["class_id"] = str(cls["_id"])
            set_fields["class_assigned_at"] = now
            if cls.get("semester"):               # the class is the Admin's explicit confirmation
                set_fields["academic"] = {
                    "academic_year": cls.get("academic_year", ""), "year_of_study": cls.get("year_of_study"),
                    "semester": cls.get("semester"), "updated_at": now, "updated_by": admin_id, "batch_id": ""}
        if department:
            canon = canonical_department(department)
            if not canon:
                raise ProgressionError(422, "Department must be one of: " + ", ".join(OFFICIAL_DEPARTMENTS) + ".")
            set_fields["department"] = canon
        if not self._guarded_update(student, set_fields):
            raise ProgressionError(409, "Changed by someone else just now. Try again.")

        after = {"id": before["id"], "department": set_fields.get("department", before["department"]),
                 "class_id": set_fields.get("class_id", before["class_id"])}
        self.db[HISTORY].insert_one({
            "kind": "academic_change", "student_id": str(student["_id"]),
            "student_name": student.get("name", ""),
            "previous": {"class_id": before["class_id"], "department": before["department"]},
            "new": {"class_id": after["class_id"], "department": after["department"]},
            "reason": reason, "admin_id": admin_id, "admin_name": admin.get("name", ""), "promoted_at": now,
        })
        sync_profile_year(self.db, before["id"])
        gained, lost = reconcile_eligibility(
            self.db, [{"student_id": before["id"], "before": before, "after": after}],
            "admin_academic_change", admin_id)
        return {"status": "success", "eligibility_gained": gained, "eligibility_lost": lost}

    def sync_profile_years(self, dry_run: bool = True, include_class_derived: bool = False) -> Dict[str, Any]:
        """
        Make every profile show the official year. By default only students that
        have an official record of their own (promoted / changed by the Admin).
        include_class_derived=True also fixes students whose year comes from their class.
        """
        classes = self._classes()
        profiles = {p.get("student_id"): p for p in self.db.profiles.find({})}
        changes, checked = [], 0
        for s in self.db.students.find({"role": "student"}):
            own = isinstance(s.get("academic"), dict) and s["academic"].get("semester")
            if not own and not include_class_derived:
                continue
            checked += 1
            eff = effective_academic(s, classes.get(str(s.get("class_id") or "")))
            year = (eff or {}).get("year_of_study")
            profile = profiles.get(str(s["_id"]))
            if not profile or not isinstance(year, int) or not 1 <= year <= 4:
                continue
            label = year_label(year, profile.get("year"))
            if profile.get("year") != label:
                changes.append({"student_id": str(s["_id"]), "name": s.get("name", ""),
                                "old": profile.get("year", ""), "new": label})
        if not dry_run:
            for c in changes:
                self.db.profiles.update_one({"student_id": c["student_id"]}, {"$set": {"year": c["new"]}})
        return {"dry_run": dry_run, "checked": checked, "to_change": len(changes),
                "changed": 0 if dry_run else len(changes), "sample": changes[:20]}

    def ensure_indexes(self) -> None:
        specs = [
            (self.db[BATCHES], [("request_id", 1)], {"unique": True}),
            (self.db[BATCHES], [("batch_id", 1)], {"unique": True}),
            (self.db[BATCHES], [("created_at", -1)], {}),
            (self.db[HISTORY], [("student_id", 1), ("promoted_at", -1)], {}),
            (self.db[HISTORY], [("batch_id", 1)], {}),
        ]
        for coll, keys, kw in specs:
            try:
                coll.create_index(keys, **kw)
            except Exception as error:
                print("ACADEMIC INDEX WARNING:", repr(error))


# ============================================================
# 5. ADMIN API
# ============================================================

def install(app, db, get_user_id: Callable[[Optional[str]], str]) -> ProgressionService:
    from bson import ObjectId
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel

    service = ProgressionService(db)
    router = APIRouter()

    class TargetBody(BaseModel):
        academic_year: str = ""
        year_of_study: Optional[int] = None
        semester: Optional[int] = None

    class PromoteBody(BaseModel):
        student_ids: List[str]
        target: TargetBody
        target_class_id: Optional[str] = None
        expected_semester: Optional[int] = None

    class ApplyBody(PromoteBody):
        request_id: str

    class CorrectBody(BaseModel):
        history_id: str
        reason: str
        corrected: Optional[TargetBody] = None
        class_id: Optional[str] = None

    class SyncBody(BaseModel):
        dry_run: bool = True
        include_class_derived: bool = False

    class StudentChangeBody(BaseModel):
        class_id: Optional[str] = None
        department: Optional[str] = None
        reason: str

    def dump(model) -> Dict[str, Any]:
        return model.model_dump() if hasattr(model, "model_dump") else model.dict()

    def require_admin(authorization: Optional[str]) -> Dict[str, Any]:
        user_id = get_user_id(authorization)
        try:
            user = db.students.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            user = None
        if not user or user.get("role") != "admin" or \
                str(user.get("status") or "active").lower() == "disabled":
            raise HTTPException(status_code=403, detail="Only the Admin can manage academic progression.")
        return user

    def run(fn):
        try:
            return fn()
        except ProgressionError as error:
            raise HTTPException(status_code=error.status, detail=error.message)

    @router.get("/api/admin/academic/filters")
    def academic_filters(authorization: str = Header(None)):
        require_admin(authorization)
        return service.filter_options()

    @router.get("/api/admin/academic/students")
    def academic_students(department: str = "", academic_year: str = "",
                          year_of_study: Optional[int] = None, semester: Optional[int] = None,
                          class_id: str = "", q: str = "", limit: int = 500,
                          authorization: str = Header(None)):
        require_admin(authorization)
        return service.list_students(department, academic_year, year_of_study, semester, class_id, q, limit)

    @router.post("/api/admin/academic/promotion/preview")
    def promotion_preview(body: PromoteBody, authorization: str = Header(None)):
        require_admin(authorization)
        d = dump(body)
        return run(lambda: service.preview(d["student_ids"], d["target"], d["target_class_id"], d["expected_semester"]))

    @router.post("/api/admin/academic/promotion/apply")
    def promotion_apply(body: ApplyBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        d = dump(body)
        return run(lambda: service.apply_promotion(
            admin, d["request_id"], d["student_ids"], d["target"], d["target_class_id"], d["expected_semester"]))

    @router.get("/api/admin/academic/promotion/history")
    def promotion_history(batch_id: str = "", student_id: str = "", limit: int = 100,
                          authorization: str = Header(None)):
        require_admin(authorization)
        return service.history(batch_id, student_id, limit)

    @router.post("/api/admin/academic/promotion/correct")
    def promotion_correct(body: CorrectBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        d = dump(body)
        return run(lambda: service.correct(admin, d["history_id"], d["corrected"], d["class_id"], d["reason"]))

    @router.patch("/api/admin/academic/students/{student_id}")
    def change_student(student_id: str, body: StudentChangeBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        d = dump(body)
        return run(lambda: service.admin_update_student(
            admin, student_id, d["class_id"], d["department"], d["reason"]))

    @router.post("/api/admin/academic/sync-profile-years")
    def sync_years(body: SyncBody, authorization: str = Header(None)):
        require_admin(authorization)
        d = dump(body)
        return service.sync_profile_years(d["dry_run"], d["include_class_derived"])

    app.include_router(router)

    @app.on_event("startup")
    def _create_academic_indexes():
        try:
            service.ensure_indexes()
        except Exception as error:
            print("ACADEMIC INDEX WARNING:", repr(error))

    return service