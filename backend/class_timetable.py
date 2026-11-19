"""
class_timetable.py

Admin-managed OFFICIAL class timetable system for CampusPath AI, in one file.

  1. RULES     pure validation (no database, no web server)
  2. DATABASE  classes, versioned timetables, student class, resolver
  3. ROUTER    FastAPI endpoints

Wired into main.py with:

    from class_timetable import (
        create_class_timetable_router, ensure_class_indexes, resolve_student_timetable,
    )
    app.include_router(create_class_timetable_router(db, get_student_id_from_token))

Collections
-----------
classes            one document per class (Admin creates / edits / deactivates)
class_timetables   one document per timetable VERSION:
                     "draft"      editable, never shown to students
                     "published"  the live version (immutable)
                     "archived"   an older published version (kept for audit)
students           existing; gains `class_id` (string ObjectId)
profiles           existing; gains `availability_preferences`
timetables         existing legacy per-student collection: READ ONLY here

How students always see a complete timetable
--------------------------------------------
Students never query class_timetables by status; they follow the pointer
`classes.published_timetable_id`. Publishing writes the new version first
(invisible to students) and then switches that one pointer with a single
document update, which MongoDB applies atomically. No multi-document
transaction is needed.

Timetable blocks use the SAME shape the rest of the project already uses
(timeUtils.js, TimetableGrid.jsx, recommender_schedule.py):
{"monday": [{"id", "subject", "courseCode", "type", "faculty", "room",
             "startTime": "09:00", "endTime": "10:00", "notes"}], ...}
"""

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from bson import ObjectId

__all__ = [
    "create_class_timetable_router",
    "ensure_class_indexes",
    "resolve_student_timetable",
]


# ============================================================
# PART 1 - RULES (pure functions)
# ============================================================

# ============================================================
# CONSTANTS
# ============================================================

# The engine (recommender_schedule.TIMETABLE_DAYS) only understands Mon-Fri.
DAYS: Tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday")

CLASS_TYPES: Tuple[str, ...] = ("lecture", "lab", "tutorial", "seminar", "other")

# Soft blocks in recommender_schedule.SOFT_BLOCK_TYPES. Only the two an admin
# would put in an official timetable are offered here.
BREAK_TYPES: Tuple[str, ...] = ("lunch", "break")

ALL_TYPES: Tuple[str, ...] = CLASS_TYPES + BREAK_TYPES

TYPE_ALIASES: Dict[str, str] = {"laboratory": "lab"}

DEFAULT_CONFIG: Dict[str, Any] = {
    "collegeStartTime": "08:00",
    "collegeEndTime": "17:00",
    "slotDuration": 60,
}

SLOT_DURATIONS: Tuple[int, ...] = (15, 30, 45, 60, 90, 120)

MAX_BLOCKS_PER_DAY = 20
MAX_BUFFER_MINUTES = 120

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_ACADEMIC_YEAR_RE = re.compile(r"^(\d{4})-(\d{4})$")


# ============================================================
# TIME HELPERS
# ============================================================

def to_minutes(value: Any) -> Optional[int]:
    """'09:30' -> 570. Returns None if the value is not a valid 24h HH:MM."""
    if not isinstance(value, str):
        return None

    match = _TIME_RE.match(value.strip())
    if not match:
        return None

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return hour * 60 + minute


def normalize_time(value: Any) -> Optional[str]:
    """'9:00' -> '09:00'. None if invalid."""
    minutes = to_minutes(value)
    if minutes is None:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clean_text(value: Any, max_len: int) -> Tuple[str, bool]:
    """Trim + collapse whitespace. Returns (text, too_long)."""
    if value is None:
        return "", False
    text = " ".join(str(value).split())
    return text, len(text) > max_len


def _day_label(day: str) -> str:
    return day.capitalize()


# ============================================================
# CONFIG
# ============================================================

def normalize_config(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validates the grid configuration (college hours + slot length).
    Missing values fall back to DEFAULT_CONFIG.
    """
    errors: List[str] = []
    config = dict(DEFAULT_CONFIG)

    if raw is None or raw == {}:
        return config, errors

    if not isinstance(raw, dict):
        return config, ["config must be an object."]

    if raw.get("collegeStartTime") is not None:
        value = normalize_time(raw.get("collegeStartTime"))
        if value is None:
            errors.append("config.collegeStartTime must be a time like 08:00.")
        else:
            config["collegeStartTime"] = value

    if raw.get("collegeEndTime") is not None:
        value = normalize_time(raw.get("collegeEndTime"))
        if value is None:
            errors.append("config.collegeEndTime must be a time like 17:00.")
        else:
            config["collegeEndTime"] = value

    if raw.get("slotDuration") is not None:
        try:
            slot = int(raw.get("slotDuration"))
        except (TypeError, ValueError):
            slot = None
        if slot not in SLOT_DURATIONS:
            errors.append(
                "config.slotDuration must be one of "
                + ", ".join(str(item) for item in SLOT_DURATIONS)
                + "."
            )
        else:
            config["slotDuration"] = slot

    if not errors:
        if to_minutes(config["collegeStartTime"]) >= to_minutes(config["collegeEndTime"]):
            errors.append("College start time must be earlier than college end time.")

    return config, errors


# ============================================================
# BLOCKS
# ============================================================

def _block_key(block: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        block["subject"].casefold(),
        block["type"],
        block["startTime"],
        block["endTime"],
    )


def normalize_block(
    day: str,
    index: int,
    raw: Any,
    config: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate and clean one session. Returns (block or None, errors)."""
    where = f"{_day_label(day)} session {index + 1}"
    errors: List[str] = []

    if not isinstance(raw, dict):
        return None, [f"{where}: must be an object."]

    subject, too_long = _clean_text(raw.get("subject"), 80)
    if not subject:
        errors.append(f"{where}: subject is required.")
    elif too_long:
        errors.append(f"{where}: subject must be 80 characters or fewer.")

    block_type = str(raw.get("type") or "").strip().lower()
    block_type = TYPE_ALIASES.get(block_type, block_type)
    if block_type not in ALL_TYPES:
        errors.append(
            f"{where}: type must be one of {', '.join(ALL_TYPES)}."
        )

    code_raw = (
        raw.get("courseCode")
        if raw.get("courseCode") is not None
        else raw.get("subject_code", raw.get("subjectCode"))
    )
    course_code, too_long = _clean_text(code_raw, 40)
    if too_long:
        errors.append(f"{where}: subject code must be 40 characters or fewer.")
    elif block_type in CLASS_TYPES and not course_code:
        errors.append(f"{where}: subject code is required.")

    faculty, too_long = _clean_text(raw.get("faculty"), 200)
    if too_long:
        errors.append(f"{where}: faculty name must be 200 characters or fewer.")

    room, too_long = _clean_text(raw.get("room"), 120)
    if too_long:
        errors.append(f"{where}: room must be 120 characters or fewer.")

    notes, too_long = _clean_text(raw.get("notes"), 600)
    if too_long:
        errors.append(f"{where}: notes must be 600 characters or fewer.")

    start_raw = raw.get("startTime", raw.get("start_time"))
    end_raw = raw.get("endTime", raw.get("end_time"))
    start = normalize_time(start_raw)
    end = normalize_time(end_raw)

    if start is None:
        errors.append(f"{where}: start time must be a valid time like 09:00.")
    if end is None:
        errors.append(f"{where}: end time must be a valid time like 10:00.")

    if start is not None and end is not None:
        if to_minutes(start) >= to_minutes(end):
            errors.append(f"{where}: start time must be earlier than end time.")
        else:
            college_start = to_minutes(config["collegeStartTime"])
            college_end = to_minutes(config["collegeEndTime"])
            if to_minutes(start) < college_start or to_minutes(end) > college_end:
                errors.append(
                    f"{where}: must be inside college hours "
                    f"({config['collegeStartTime']}-{config['collegeEndTime']})."
                )

    if errors:
        return None, errors

    block_id = raw.get("id")
    if not (isinstance(block_id, str) and _ID_RE.match(block_id)):
        block_id = f"blk_{uuid.uuid4().hex[:12]}"

    return (
        {
            "id": block_id,
            "subject": subject,
            "courseCode": course_code,
            "type": block_type,
            "faculty": faculty,
            "room": room,
            "startTime": start,
            "endTime": end,
            "notes": notes,
        },
        [],
    )


# ============================================================
# TIMETABLE
# ============================================================

def empty_timetable() -> Dict[str, List[Dict[str, Any]]]:
    return {day: [] for day in DAYS}


def normalize_timetable(
    raw: Any,
    config: Dict[str, Any],
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """
    Validates a whole week.

    Rejects: unknown weekdays, missing/invalid fields, start >= end, sessions
    outside college hours, exact duplicates and overlapping sessions on the
    same day. Returns (clean timetable with all five days, errors).
    """
    errors: List[str] = []
    result = empty_timetable()

    if raw is None:
        return result, errors

    if not isinstance(raw, dict):
        return result, ["timetable must be an object with weekday keys."]

    for key in raw:
        if str(key).strip().lower() not in DAYS:
            errors.append(
                f"Unknown day '{key}'. Use monday to friday."
            )

    seen_ids = set()

    for day in DAYS:
        blocks_raw = None
        for key, value in raw.items():
            if str(key).strip().lower() == day:
                blocks_raw = value
                break

        if blocks_raw is None:
            continue

        if not isinstance(blocks_raw, list):
            errors.append(f"{_day_label(day)}: must be a list of sessions.")
            continue

        if len(blocks_raw) > MAX_BLOCKS_PER_DAY:
            errors.append(
                f"{_day_label(day)}: at most {MAX_BLOCKS_PER_DAY} sessions per day."
            )
            continue

        day_blocks: List[Dict[str, Any]] = []

        for index, raw_block in enumerate(blocks_raw):
            block, block_errors = normalize_block(day, index, raw_block, config)
            errors.extend(block_errors)

            if block is None:
                continue

            if block["id"] in seen_ids:
                block["id"] = f"blk_{uuid.uuid4().hex[:12]}"
            seen_ids.add(block["id"])
            day_blocks.append(block)

        # Overlaps and duplicates inside the day.
        day_blocks.sort(key=lambda b: (to_minutes(b["startTime"]), to_minutes(b["endTime"])))

        for i, first in enumerate(day_blocks):
            for second in day_blocks[i + 1:]:
                if to_minutes(second["startTime"]) >= to_minutes(first["endTime"]):
                    break

                if _block_key(first) == _block_key(second):
                    errors.append(
                        f"{_day_label(day)}: duplicate entry "
                        f"'{first['subject']}' {first['startTime']}-{first['endTime']}."
                    )
                else:
                    errors.append(
                        f"{_day_label(day)}: '{first['subject']}' "
                        f"({first['startTime']}-{first['endTime']}) overlaps "
                        f"'{second['subject']}' "
                        f"({second['startTime']}-{second['endTime']})."
                    )

        result[day] = day_blocks

    return result, errors


def count_class_sessions(timetable: Dict[str, List[Dict[str, Any]]]) -> int:
    """Number of real classes (lecture/lab/tutorial/seminar/other), not breaks."""
    return sum(
        1
        for blocks in (timetable or {}).values()
        for block in blocks
        if block.get("type") in CLASS_TYPES
    )


def count_sessions(timetable: Dict[str, List[Dict[str, Any]]]) -> int:
    return sum(len(blocks) for blocks in (timetable or {}).values())


def publish_readiness_errors(
    timetable: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """Extra checks that only apply when publishing (a draft may be empty)."""
    errors: List[str] = []

    if count_class_sessions(timetable) == 0:
        errors.append(
            "The timetable has no classes. Add at least one lecture, lab, "
            "tutorial or seminar before publishing."
        )

    return errors


# ============================================================
# CLASS FIELDS
# ============================================================

def _collapse(value: Any) -> str:
    return " ".join(str(value or "").split())


def class_identity_key(
    department: str,
    academic_year: str,
    semester: int,
    section: str,
    batch: str = "",
) -> str:
    """
    Normalized identity used to stop accidental duplicate ACTIVE classes.
    Case and extra spaces do not matter: 'AI  & ML' == 'ai & ml'.
    """
    return "|".join(
        [
            _collapse(department).casefold(),
            _collapse(academic_year),
            str(int(semester)),
            _collapse(section).casefold(),
            _collapse(batch).casefold(),
        ]
    )


def default_class_name(
    department: str,
    year_of_study: int,
    semester: int,
    section: str,
    batch: str = "",
) -> str:
    name = f"{department} - Year {year_of_study} - Sem {semester} - Section {section}"
    if batch:
        name += f" - {batch}"
    return name


def normalize_class_fields(
    data: Dict[str, Any],
    partial: bool = False,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validates class fields from an Admin request.

    partial=False (create): department, academic_year, year_of_study,
    semester and section are required.
    partial=True (update): only the keys present are checked.
    """
    errors: List[str] = []
    clean: Dict[str, Any] = {}

    def present(key: str) -> bool:
        return key in data and data[key] is not None

    # --- department
    if present("department") or not partial:
        text, too_long = _clean_text(data.get("department"), 80)
        if not text:
            errors.append("Department is required.")
        elif too_long:
            errors.append("Department must be 80 characters or fewer.")
        else:
            clean["department"] = text

    # --- academic year
    if present("academic_year") or not partial:
        text = _collapse(data.get("academic_year"))
        match = _ACADEMIC_YEAR_RE.match(text)
        if not match:
            errors.append("Academic year must look like 2026-2027.")
        elif int(match.group(2)) != int(match.group(1)) + 1:
            errors.append("Academic year must span consecutive years, e.g. 2026-2027.")
        else:
            clean["academic_year"] = text

    # --- year of study
    if present("year_of_study") or not partial:
        try:
            year = int(data.get("year_of_study"))
        except (TypeError, ValueError):
            year = None
        if year is None or not 1 <= year <= 4:
            errors.append("Year of study must be a number from 1 to 4.")
        else:
            clean["year_of_study"] = year

    # --- semester
    if present("semester") or not partial:
        try:
            semester = int(data.get("semester"))
        except (TypeError, ValueError):
            semester = None
        if semester is None or not 1 <= semester <= 8:
            errors.append("Semester must be a number from 1 to 8.")
        else:
            clean["semester"] = semester

    # --- section
    if present("section") or not partial:
        text, too_long = _clean_text(data.get("section"), 10)
        if not text:
            errors.append("Section is required.")
        elif too_long:
            errors.append("Section must be 10 characters or fewer.")
        else:
            clean["section"] = text

    # --- batch (optional; an explicit empty value clears it)
    if "batch" in data:
        text, too_long = _clean_text(data.get("batch"), 40)
        if too_long:
            errors.append("Batch must be 40 characters or fewer.")
        else:
            clean["batch"] = text

    # --- name (optional; an explicit empty value regenerates it)
    if "name" in data:
        text, too_long = _clean_text(data.get("name"), 120)
        if too_long:
            errors.append("Class name must be 120 characters or fewer.")
        else:
            clean["name"] = text

    if "is_active" in data and data["is_active"] is not None:
        clean["is_active"] = bool(data["is_active"])

    return clean, errors


# ============================================================
# AVAILABILITY PREFERENCES (kept per student, NOT part of the class timetable)
# ============================================================

DEFAULT_PREFERENCES: Dict[str, Any] = {
    "preferredEventDuration": [],
    "bufferTime": 0,
    "preferredEventTimes": [],
    "notificationPrefs": [],
}


def _clean_list(value: Any, name: str, errors: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{name} must be a list.")
        return []
    if len(value) > 30:
        errors.append(f"{name} has too many items.")
        return []

    cleaned: List[str] = []
    for item in value:
        text, too_long = _clean_text(item, 60)
        if too_long:
            errors.append(f"{name} contains an item longer than 60 characters.")
            return []
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def normalize_preferences(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Validates the four availability preference fields."""
    errors: List[str] = []

    if not isinstance(raw, dict):
        return dict(DEFAULT_PREFERENCES), ["preferences must be an object."]

    buffer_value = raw.get("bufferTime", 0)
    try:
        buffer_minutes = int(buffer_value if buffer_value is not None else 0)
    except (TypeError, ValueError):
        buffer_minutes = None

    if buffer_minutes is None or not 0 <= buffer_minutes <= MAX_BUFFER_MINUTES:
        errors.append(f"bufferTime must be between 0 and {MAX_BUFFER_MINUTES} minutes.")
        buffer_minutes = 0

    clean = {
        "preferredEventDuration": _clean_list(
            raw.get("preferredEventDuration"), "preferredEventDuration", errors
        ),
        "bufferTime": buffer_minutes,
        "preferredEventTimes": [
            item.lower()
            for item in _clean_list(
                raw.get("preferredEventTimes"), "preferredEventTimes", errors
            )
        ],
        "notificationPrefs": _clean_list(
            raw.get("notificationPrefs"), "notificationPrefs", errors
        ),
    }

    return clean, errors


def format_errors(errors: List[str], limit: int = 8) -> str:
    """One readable message for an HTTP error body."""
    shown = errors[:limit]
    message = " ".join(shown)
    if len(errors) > limit:
        message += f" (+{len(errors) - limit} more)"
    return message


# ============================================================
# PART 2 - DATABASE OPERATIONS
# ============================================================

logger = logging.getLogger("campuspath.class_timetable")


class ServiceError(Exception):
    """A request problem with an HTTP status and a message safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ============================================================
# SMALL HELPERS
# ============================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: Any) -> Optional[str]:
    """datetime -> '2026-10-21T09:35:40Z' (same style as main.py's iso_utc)."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _oid(value: Any, label: str = "ID") -> ObjectId:
    try:
        return ObjectId(str(value))
    except Exception:
        raise ServiceError(400, f"Invalid {label}.")


def _legacy_fallback_enabled() -> bool:
    """
    Students with NO class selected yet keep using their old personal timetable
    (so recommendations do not suddenly lose their schedule). Set
    CAMPUSPATH_LEGACY_TIMETABLE_FALLBACK=false in .env to ignore it.
    """
    value = os.getenv("CAMPUSPATH_LEGACY_TIMETABLE_FALLBACK", "true")
    return value.strip().lower() not in ("0", "false", "no", "off")


def log_activity(
    db,
    admin: Optional[Dict[str, Any]],
    action: str,
    module: str,
    description: str,
    related_id: Any = None,
) -> None:
    """Same document shape as main.create_activity_log; never breaks a request."""
    admin = admin or {}
    try:
        db.activity_logs.insert_one(
            {
                "user_id": str(admin.get("_id", "")),
                "user_name": admin.get("name", ""),
                "user_email": admin.get("email", ""),
                "role": admin.get("role", ""),
                "action": action,
                "module": module,
                "description": description,
                "related_id": str(related_id) if related_id else None,
                "status": "success",
                "created_at": _now(),
            }
        )
    except Exception:
        logger.exception("Could not write activity log")


# ============================================================
# OUTPUT SHAPES
# ============================================================

def class_out(doc: Dict[str, Any], student_count: Optional[int] = None) -> Dict[str, Any]:
    out = {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "department": doc.get("department", ""),
        "academic_year": doc.get("academic_year", ""),
        "year_of_study": doc.get("year_of_study"),
        "semester": doc.get("semester"),
        "section": doc.get("section", ""),
        "batch": doc.get("batch") or "",
        "is_active": bool(doc.get("is_active", True)),
        "has_published_timetable": bool(doc.get("published_timetable_id")),
        "published_version": doc.get("published_version"),
        "published_at": iso_utc(doc.get("published_at")),
        "created_at": iso_utc(doc.get("created_at")),
        "updated_at": iso_utc(doc.get("updated_at")),
    }
    if student_count is not None:
        out["student_count"] = student_count
    return out


def timetable_out(doc: Dict[str, Any]) -> Dict[str, Any]:
    timetable = doc.get("timetable") or empty_timetable()
    return {
        "id": str(doc["_id"]),
        "class_id": doc.get("class_id", ""),
        "version": doc.get("version"),
        "status": doc.get("status", ""),
        "timetable": timetable,
        "config": doc.get("config") or dict(DEFAULT_CONFIG),
        "effective_from": doc.get("effective_from"),
        "sessions": count_sessions(timetable),
        "class_sessions": count_class_sessions(timetable),
        "created_at": iso_utc(doc.get("created_at")),
        "updated_at": iso_utc(doc.get("updated_at")),
        "published_at": iso_utc(doc.get("published_at")),
        "published_by": doc.get("published_by"),
    }


# ============================================================
# CLASSES
# ============================================================

def get_class_doc(db, class_id: Any) -> Dict[str, Any]:
    doc = db.classes.find_one({"_id": _oid(class_id, "class ID")})
    if not doc:
        raise ServiceError(404, "Class not found.")
    return doc


def _ensure_no_active_duplicate(db, key: str, exclude_id: Any = None) -> None:
    query: Dict[str, Any] = {"identity_key": key, "is_active": True}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}

    found = db.classes.find_one(query)
    if found:
        raise ServiceError(
            409,
            "An active class already exists for this department, academic "
            f"year, semester, section and batch: '{found.get('name', '')}'.",
        )


def create_class(db, admin: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    clean, errors = normalize_class_fields(data, partial=False)
    if errors:
        raise ServiceError(422, format_errors(errors))

    batch = clean.get("batch", "")
    is_active = clean.get("is_active", True)
    key = class_identity_key(
        clean["department"],
        clean["academic_year"],
        clean["semester"],
        clean["section"],
        batch,
    )

    if is_active:
        _ensure_no_active_duplicate(db, key)

    now = _now()
    doc = {
        "name": clean.get("name")
        or default_class_name(
            clean["department"],
            clean["year_of_study"],
            clean["semester"],
            clean["section"],
            batch,
        ),
        "department": clean["department"],
        "academic_year": clean["academic_year"],
        "year_of_study": clean["year_of_study"],
        "semester": clean["semester"],
        "section": clean["section"],
        "batch": batch,
        "is_active": is_active,
        "identity_key": key,
        "published_timetable_id": None,
        "published_version": None,
        "published_at": None,
        "created_by": str(admin.get("_id", "")),
        "created_at": now,
        "updated_at": now,
    }

    try:
        result = db.classes.insert_one(doc)
    except Exception as error:  # unique index hit by a concurrent request
        if "E11000" in str(error) or "duplicate" in str(error).lower():
            raise ServiceError(
                409, "An active class with the same details already exists."
            )
        raise

    doc["_id"] = result.inserted_id
    log_activity(
        db, admin, "class_created", "classes",
        f"Created class '{doc['name']}'.", doc["_id"],
    )
    return class_out(doc)


def update_class(
    db, admin: Dict[str, Any], class_id: Any, data: Dict[str, Any]
) -> Dict[str, Any]:
    existing = get_class_doc(db, class_id)

    clean, errors = normalize_class_fields(data, partial=True)
    if errors:
        raise ServiceError(422, format_errors(errors))

    def merged(field: str, default: Any = "") -> Any:
        return clean[field] if field in clean else (existing.get(field) or default)

    department = merged("department")
    academic_year = merged("academic_year")
    year_of_study = clean.get("year_of_study", existing.get("year_of_study"))
    semester = clean.get("semester", existing.get("semester"))
    section = merged("section")
    batch = clean["batch"] if "batch" in clean else (existing.get("batch") or "")
    is_active = clean.get("is_active", bool(existing.get("is_active", True)))

    key = class_identity_key(department, academic_year, semester, section, batch)

    if is_active:
        _ensure_no_active_duplicate(db, key, exclude_id=existing["_id"])

    old_default = default_class_name(
        existing.get("department", ""),
        existing.get("year_of_study"),
        existing.get("semester"),
        existing.get("section", ""),
        existing.get("batch") or "",
    )
    new_default = default_class_name(
        department, year_of_study, semester, section, batch
    )

    if "name" in clean:
        name = clean["name"] or new_default
    elif existing.get("name") == old_default:
        name = new_default
    else:
        name = existing.get("name") or new_default

    updates = {
        "name": name,
        "department": department,
        "academic_year": academic_year,
        "year_of_study": year_of_study,
        "semester": semester,
        "section": section,
        "batch": batch,
        "is_active": is_active,
        "identity_key": key,
        "updated_at": _now(),
    }

    try:
        db.classes.update_one({"_id": existing["_id"]}, {"$set": updates})
    except Exception as error:
        if "E11000" in str(error) or "duplicate" in str(error).lower():
            raise ServiceError(
                409, "An active class with the same details already exists."
            )
        raise

    was_active = bool(existing.get("is_active", True))
    if was_active and not is_active:
        action, text = "class_deactivated", f"Deactivated class '{name}'."
    elif not was_active and is_active:
        action, text = "class_reactivated", f"Reactivated class '{name}'."
    else:
        action, text = "class_updated", f"Updated class '{name}'."

    log_activity(db, admin, action, "classes", text, existing["_id"])

    return class_out(db.classes.find_one({"_id": existing["_id"]}))


def list_classes(
    db,
    active_only: Optional[bool] = None,
    search: str = "",
    department: str = "",
    year_of_study: Optional[int] = None,
    semester: Optional[int] = None,
    with_counts: bool = False,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {}
    if active_only is not None:
        query["is_active"] = bool(active_only)

    docs = list(db.classes.find(query))

    dept = " ".join((department or "").split()).casefold()
    needle = " ".join((search or "").split()).casefold()

    def keep(doc: Dict[str, Any]) -> bool:
        if dept and " ".join(str(doc.get("department", "")).split()).casefold() != dept:
            return False
        if year_of_study is not None and doc.get("year_of_study") != year_of_study:
            return False
        if semester is not None and doc.get("semester") != semester:
            return False
        if needle:
            haystack = " ".join(
                str(doc.get(field, ""))
                for field in ("name", "department", "academic_year", "section", "batch")
            ).casefold()
            if needle not in haystack:
                return False
        return True

    docs = [doc for doc in docs if keep(doc)]
    docs.sort(
        key=lambda d: (
            str(d.get("department", "")).casefold(),
            d.get("year_of_study") or 0,
            d.get("semester") or 0,
            str(d.get("section", "")).casefold(),
        )
    )

    out = []
    for doc in docs:
        count = None
        if with_counts:
            try:
                count = db.students.count_documents({"class_id": str(doc["_id"])})
            except Exception:
                logger.exception("Could not count students for a class")
        out.append(class_out(doc, count))
    return out


# ============================================================
# TIMETABLES (ADMIN)
# ============================================================

def _published_doc(db, class_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pointer = class_doc.get("published_timetable_id")
    if not pointer:
        return None
    try:
        return db.class_timetables.find_one({"_id": ObjectId(str(pointer))})
    except Exception:
        return None


def admin_timetable_view(db, class_id: Any) -> Dict[str, Any]:
    cls = get_class_doc(db, class_id)
    cid = str(cls["_id"])

    published = _published_doc(db, cls)
    draft = db.class_timetables.find_one({"class_id": cid, "status": "draft"})

    history = [
        {
            "id": str(doc["_id"]),
            "version": doc.get("version"),
            "status": doc.get("status"),
            "published_at": iso_utc(doc.get("published_at")),
            "published_by": doc.get("published_by"),
        }
        for doc in db.class_timetables.find({"class_id": cid})
        if doc.get("status") in ("published", "archived")
    ]
    history.sort(key=lambda row: row.get("version") or 0, reverse=True)

    return {
        "class": class_out(cls),
        "published": timetable_out(published) if published else None,
        "draft": timetable_out(draft) if draft else None,
        "has_unpublished_changes": draft is not None,
        "history": history,
    }


def _validated_payload(
    body: Dict[str, Any],
    draft: Optional[Dict[str, Any]],
    published: Optional[Dict[str, Any]],
):
    if body.get("timetable") is None:
        raise ServiceError(422, "timetable is required.")

    if body.get("config") is not None:
        raw_config = body.get("config")
    elif draft and draft.get("config"):
        raw_config = draft.get("config")
    elif published and published.get("config"):
        raw_config = published.get("config")
    else:
        raw_config = None

    config, config_errors = normalize_config(raw_config)
    timetable, timetable_errors = normalize_timetable(body.get("timetable"), config)

    errors = config_errors + timetable_errors
    if errors:
        raise ServiceError(422, format_errors(errors))

    return timetable, config


def save_draft(
    db, admin: Dict[str, Any], class_id: Any, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Create the class's draft, or replace the existing draft's content."""
    cls = get_class_doc(db, class_id)
    cid = str(cls["_id"])

    draft = db.class_timetables.find_one({"class_id": cid, "status": "draft"})
    timetable, config = _validated_payload(body, draft, _published_doc(db, cls))
    now = _now()

    if draft:
        db.class_timetables.update_one(
            {"_id": draft["_id"], "status": "draft"},
            {
                "$set": {
                    "timetable": timetable,
                    "config": config,
                    "updated_at": now,
                    "updated_by": str(admin.get("_id", "")),
                }
            },
        )
        doc = db.class_timetables.find_one({"_id": draft["_id"]})
    else:
        doc = {
            "class_id": cid,
            "version": None,
            "status": "draft",
            "timetable": timetable,
            "config": config,
            "effective_from": None,
            "created_by": str(admin.get("_id", "")),
            "created_at": now,
            "updated_at": now,
            "published_at": None,
            "published_by": None,
        }
        try:
            result = db.class_timetables.insert_one(doc)
        except Exception as error:  # two admins creating the first draft at once
            if "E11000" in str(error) or "duplicate" in str(error).lower():
                raise ServiceError(409, "A draft already exists. Reload and try again.")
            raise
        doc["_id"] = result.inserted_id

    log_activity(
        db, admin, "timetable_draft_saved", "timetables",
        f"Saved timetable draft for class '{cls.get('name', '')}'.", doc["_id"],
    )
    return timetable_out(doc)


def update_timetable_by_id(
    db, admin: Dict[str, Any], timetable_id: Any, body: Dict[str, Any]
) -> Dict[str, Any]:
    doc = db.class_timetables.find_one({"_id": _oid(timetable_id, "timetable ID")})
    if not doc:
        raise ServiceError(404, "Timetable not found.")

    if doc.get("status") != "draft":
        raise ServiceError(
            409,
            "Only a draft can be edited. Published versions are kept unchanged; "
            "save your changes as a new draft for the class instead.",
        )

    cls = get_class_doc(db, doc.get("class_id"))
    return save_draft(db, admin, str(cls["_id"]), body)


def publish(db, admin: Dict[str, Any], timetable_id: Any) -> Dict[str, Any]:
    doc = db.class_timetables.find_one({"_id": _oid(timetable_id, "timetable ID")})
    if not doc:
        raise ServiceError(404, "Timetable not found.")

    if doc.get("status") != "draft":
        raise ServiceError(
            409,
            f"This timetable is already {doc.get('status')}. "
            "Edit the draft to publish changes.",
        )

    cls = get_class_doc(db, doc.get("class_id"))

    if not cls.get("is_active", True):
        raise ServiceError(409, "Activate this class before publishing its timetable.")

    # Validate again from what is stored, never trusting the earlier save.
    config, config_errors = normalize_config(doc.get("config"))
    timetable, timetable_errors = normalize_timetable(doc.get("timetable"), config)
    errors = (
        config_errors
        + timetable_errors
        + publish_readiness_errors(timetable)
    )
    if errors:
        raise ServiceError(
            422, "This timetable cannot be published yet. " + format_errors(errors)
        )

    cid = str(cls["_id"])
    versions = [
        row.get("version") or 0 for row in db.class_timetables.find({"class_id": cid})
    ]
    version = (max(versions) if versions else 0) + 1
    previous_id = cls.get("published_timetable_id")
    now = _now()

    # 1. Claim the draft (invisible to students: they only follow the pointer).
    claim = db.class_timetables.update_one(
        {"_id": doc["_id"], "status": "draft"},
        {
            "$set": {
                "status": "published",
                "version": version,
                "timetable": timetable,
                "config": config,
                "effective_from": now.date().isoformat(),
                "published_at": now,
                "published_by": str(admin.get("_id", "")),
                "updated_at": now,
            }
        },
    )
    if claim.matched_count == 0:
        raise ServiceError(409, "This draft was just published or changed. Reload and try again.")

    # 2. Switch the class to the new version in ONE atomic update.
    try:
        db.classes.update_one(
            {"_id": cls["_id"]},
            {
                "$set": {
                    "published_timetable_id": str(doc["_id"]),
                    "published_version": version,
                    "published_at": now,
                    "updated_at": now,
                }
            },
        )
    except Exception:
        logger.exception("Could not switch class to the new timetable; reverting")
        db.class_timetables.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": "draft", "version": None, "published_at": None,
                      "published_by": None}},
        )
        raise ServiceError(500, "Could not publish the timetable. Nothing was changed.")

    # 3. Keep the previous version for audit.
    if previous_id and previous_id != str(doc["_id"]):
        try:
            db.class_timetables.update_one(
                {"_id": ObjectId(str(previous_id)), "status": "published"},
                {"$set": {"status": "archived", "archived_at": now}},
            )
        except Exception:
            logger.exception("Could not archive the previous timetable version")

    log_activity(
        db, admin, "timetable_published", "timetables",
        f"Published timetable v{version} for class '{cls.get('name', '')}'.",
        doc["_id"],
    )

    return {
        "class": class_out(db.classes.find_one({"_id": cls["_id"]})),
        "timetable": timetable_out(db.class_timetables.find_one({"_id": doc["_id"]})),
    }


def get_published_for_class(db, class_id: Any) -> Dict[str, Any]:
    """Public view for an ACTIVE class: its published timetable, if any."""
    cls = get_class_doc(db, class_id)
    if not cls.get("is_active", True):
        raise ServiceError(404, "Class not found or no longer active.")

    published = _published_doc(db, cls)
    if not published:
        return {
            "class": class_out(cls),
            "published": False,
            "message": "The official timetable for this class has not been published yet.",
            "timetable": {},
            "config": {},
            "version": None,
            "published_at": None,
        }

    return {
        "class": class_out(cls),
        "published": True,
        "message": "",
        "timetable": published.get("timetable") or empty_timetable(),
        "config": published.get("config") or dict(DEFAULT_CONFIG),
        "version": published.get("version"),
        "published_at": iso_utc(published.get("published_at")),
    }


# ============================================================
# STUDENT
# ============================================================

def _student_doc(db, student_id: Any) -> Dict[str, Any]:
    doc = db.students.find_one({"_id": _oid(student_id, "student ID")})
    if not doc:
        raise ServiceError(404, "Student not found.")
    return doc


_INACTIVE_MESSAGE = (
    "This class has been deactivated by the Admin. Your assignment has been "
    "kept. Choose your current class when you can."
)
_MISSING_CLASS_MESSAGE = (
    "Your selected class is no longer available. Please select your class again."
)
_NOT_PUBLISHED_MESSAGE = (
    "The official timetable for your class has not been published yet."
)


def get_student_class(db, student_id: Any) -> Dict[str, Any]:
    student = _student_doc(db, student_id)
    class_id = student.get("class_id")

    if not class_id:
        return {
            "class": None,
            "class_active": None,
            "message": "You have not selected a class yet.",
        }

    cls = None
    try:
        cls = db.classes.find_one({"_id": ObjectId(str(class_id))})
    except Exception:
        cls = None

    if not cls:
        return {"class": None, "class_active": None, "message": _MISSING_CLASS_MESSAGE}

    active = bool(cls.get("is_active", True))
    return {
        "class": class_out(cls),
        "class_active": active,
        "message": "" if active else _INACTIVE_MESSAGE,
    }


def assign_student_class(db, student_id: Any, class_id: Any) -> Dict[str, Any]:
    """Takes effect immediately; no Admin approval. Active classes only."""
    student = _student_doc(db, student_id)
    cls = db.classes.find_one({"_id": _oid(class_id, "class ID")})

    if not cls:
        raise ServiceError(404, "Class not found.")

    if not cls.get("is_active", True):
        raise ServiceError(409, "This class is not active and cannot be selected.")

    db.students.update_one(
        {"_id": student["_id"]},
        {"$set": {"class_id": str(cls["_id"]), "class_assigned_at": _now()}},
    )

    return {
        "class": class_out(cls),
        "timetable": resolve_student_timetable(db, str(student["_id"])),
    }


def save_availability_preferences(db, student_id: str, raw: Any) -> Dict[str, Any]:
    prefs, errors = normalize_preferences(raw)
    if errors:
        raise ServiceError(422, format_errors(errors))

    result = db.profiles.update_one(
        {"student_id": student_id},
        {"$set": {"availability_preferences": prefs}},
    )
    # upsert is deliberately OFF: creating a bare profile here would make the
    # student look as if they had finished the profile steps.
    if result.matched_count == 0:
        raise ServiceError(
            404, "Please save your profile before setting availability preferences."
        )
    return prefs


def _load_preferences(
    db, student_id: str, legacy_doc: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    profile = db.profiles.find_one({"student_id": student_id})
    prefs = (profile or {}).get("availability_preferences")
    if isinstance(prefs, dict) and prefs:
        return prefs

    legacy_prefs = (legacy_doc or {}).get("preferences")
    return legacy_prefs if isinstance(legacy_prefs, dict) else {}


def _has_blocks(timetable: Any) -> bool:
    return isinstance(timetable, dict) and any(
        isinstance(blocks, list) and blocks for blocks in timetable.values()
    )


def resolve_student_timetable(db, student_id: Any) -> Dict[str, Any]:
    """
    The ONE place that decides which timetable a student gets.

      class assigned           -> that class's published timetable ("official")
      class, none published    -> empty + clear message (NOT the old personal one)
      no class (legacy)        -> the old personal timetable, if the fallback is
                                  enabled ("legacy"), otherwise empty
    The result has the keys the dashboard, the iframe and the recommendation
    engine already read: `timetable`, `config`, `preferences`.
    """
    student_id = str(student_id)

    result: Dict[str, Any] = {
        "source": "none",
        "class": None,
        "class_active": None,
        "published": False,
        "version": None,
        "published_at": None,
        "message": "Select your class to see your official timetable.",
        "timetable": {},
        "config": {},
        "preferences": {},
    }

    student = None
    try:
        student = db.students.find_one({"_id": ObjectId(student_id)})
    except Exception:
        student = None

    legacy = db.timetables.find_one({"student_id": student_id})
    result["preferences"] = _load_preferences(db, student_id, legacy)

    class_id = (student or {}).get("class_id")
    cls = None
    if class_id:
        try:
            cls = db.classes.find_one({"_id": ObjectId(str(class_id))})
        except Exception:
            cls = None

    if cls:
        active = bool(cls.get("is_active", True))
        result["source"] = "official"
        result["class"] = class_out(cls)
        result["class_active"] = active
        result["message"] = "" if active else _INACTIVE_MESSAGE

        published = _published_doc(db, cls)
        if published:
            result["published"] = True
            result["version"] = published.get("version")
            result["published_at"] = iso_utc(published.get("published_at"))
            result["timetable"] = published.get("timetable") or empty_timetable()
            result["config"] = published.get("config") or dict(DEFAULT_CONFIG)
        else:
            result["message"] = (
                (result["message"] + " " if result["message"] else "")
                + _NOT_PUBLISHED_MESSAGE
            )
        return result

    if class_id and not cls:
        result["message"] = _MISSING_CLASS_MESSAGE

    if _legacy_fallback_enabled() and legacy and _has_blocks(legacy.get("timetable")):
        result["source"] = "legacy"
        result["timetable"] = legacy.get("timetable") or {}
        result["config"] = legacy.get("config") or {}
        result["message"] = (
            "This is the timetable you entered earlier. Select your official "
            "class to use your university timetable."
        )

    return result


# ============================================================
# INDEXES
# ============================================================

def ensure_class_indexes(db) -> None:
    """
    Created at startup; safe to run repeatedly. A failure is logged, never
    fatal: the checks in this module still enforce the same 
    Existing collections (students, profiles, timetables) are not touched.
    """
    specs = [
        (
            db.classes,
            [("identity_key", 1)],
            {
                "unique": True,
                "partialFilterExpression": {"is_active": True},
                "name": "uniq_active_class_identity",
            },
        ),
        (
            db.class_timetables,
            [("class_id", 1), ("status", 1)],
            {"name": "class_timetables_class_status"},
        ),
        (
            db.class_timetables,
            [("class_id", 1)],
            {
                "unique": True,
                "partialFilterExpression": {"status": "draft"},
                "name": "uniq_draft_per_class",
            },
        ),
    ]

    for collection, keys, options in specs:
        try:
            collection.create_index(keys, **options)
        except Exception:
            logger.warning(
                "Could not create index %s", options.get("name"), exc_info=True
            )


# ============================================================
# PART 3 - FASTAPI ROUTER
# ============================================================


def create_class_timetable_router(
    db,
    get_user_id_from_token: Callable[[Optional[str]], str],
):
    # FastAPI / Pydantic are imported here so the rules and database
    # functions above can be used and tested without a web server installed.
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel

    class ClassBody(BaseModel):
        name: Optional[str] = None
        department: Optional[str] = None
        academic_year: Optional[str] = None
        year_of_study: Optional[int] = None
        semester: Optional[int] = None
        section: Optional[str] = None
        batch: Optional[str] = None
        is_active: Optional[bool] = None

    class TimetableBody(BaseModel):
        timetable: Optional[Dict[str, Any]] = None
        config: Optional[Dict[str, Any]] = None

    class StudentClassBody(BaseModel):
        class_id: str

    class PreferencesBody(BaseModel):
        preferredEventDuration: Optional[List[str]] = None
        bufferTime: Optional[int] = None
        preferredEventTimes: Optional[List[str]] = None
        notificationPrefs: Optional[List[str]] = None

    def _dump(model) -> Dict[str, Any]:
        """Only the fields the client actually sent (Pydantic v1 and v2)."""
        if hasattr(model, "model_dump"):
            return model.model_dump(exclude_unset=True)
        return model.dict(exclude_unset=True)

    router = APIRouter()

    # --------------------------------------------------------
    # AUTH HELPERS (server-side; nothing from the client is trusted)
    # --------------------------------------------------------

    def current_user(authorization: Optional[str]) -> Dict[str, Any]:
        user_id = get_user_id_from_token(authorization)

        try:
            user = db.students.find_one({"_id": ObjectId(user_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return user

    def require_admin(authorization: Optional[str]) -> Dict[str, Any]:
        user = current_user(authorization)
        if user.get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail="Only administrators can manage classes and timetables.",
            )
        return user

    def require_student(authorization: Optional[str]) -> Dict[str, Any]:
        user = current_user(authorization)
        if user.get("role") != "student":
            raise HTTPException(
                status_code=403,
                detail="Only students can choose a class.",
            )
        return user

    def run(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except ServiceError as error:
            raise HTTPException(status_code=error.status, detail=error.message)

    # --------------------------------------------------------
    # ADMIN: CLASSES
    # --------------------------------------------------------

    @router.get("/api/admin/classes")
    def admin_list_classes(
        search: str = "",
        department: str = "",
        year_of_study: Optional[int] = None,
        semester: Optional[int] = None,
        status: str = "all",  # all | active | inactive
        authorization: str = Header(None),
    ):
        require_admin(authorization)

        active_only: Optional[bool] = None
        if status == "active":
            active_only = True
        elif status == "inactive":
            active_only = False

        classes = run(
            list_classes,
            db,
            active_only=active_only,
            search=search,
            department=department,
            year_of_study=year_of_study,
            semester=semester,
            with_counts=True,
        )
        return {"status": "success", "count": len(classes), "classes": classes}

    @router.post("/api/classes", status_code=201)
    def api_create_class(body: ClassBody, authorization: str = Header(None)):
        admin = require_admin(authorization)
        created = run(create_class, db, admin, _dump(body))
        return {"status": "success", "class": created}

    @router.patch("/api/classes/{class_id}")
    def api_update_class(
        class_id: str, body: ClassBody, authorization: str = Header(None)
    ):
        admin = require_admin(authorization)
        updated = run(update_class, db, admin, class_id, _dump(body))
        return {"status": "success", "class": updated}

    # --------------------------------------------------------
    # ANY LOGGED-IN USER: CLASS LIST / DETAILS / PUBLISHED TIMETABLE
    # --------------------------------------------------------

    @router.get("/api/classes")
    def list_active_classes(
        department: str = "",
        year_of_study: Optional[int] = None,
        semester: Optional[int] = None,
        search: str = "",
        authorization: str = Header(None),
    ):
        current_user(authorization)
        classes = run(
            list_classes,
            db,
            active_only=True,
            search=search,
            department=department,
            year_of_study=year_of_study,
            semester=semester,
        )
        return {"status": "success", "count": len(classes), "classes": classes}

    @router.get("/api/classes/{class_id}")
    def api_get_class(class_id: str, authorization: str = Header(None)):
        current_user(authorization)
        cls = run(get_class_doc, db, class_id)
        return {"status": "success", "class": class_out(cls)}

    @router.get("/api/classes/{class_id}/timetable")
    def get_class_timetable(class_id: str, authorization: str = Header(None)):
        current_user(authorization)
        data = run(get_published_for_class, db, class_id)
        return {"status": "success", **data}

    # --------------------------------------------------------
    # ADMIN: TIMETABLES
    # --------------------------------------------------------

    @router.get("/api/admin/classes/{class_id}/timetable")
    def admin_get_timetable(class_id: str, authorization: str = Header(None)):
        require_admin(authorization)
        data = run(admin_timetable_view, db, class_id)
        return {"status": "success", **data}

    @router.post("/api/admin/classes/{class_id}/timetable")
    def admin_save_draft(
        class_id: str, body: TimetableBody, authorization: str = Header(None)
    ):
        admin = require_admin(authorization)
        draft = run(save_draft, db, admin, class_id, _dump(body))
        return {
            "status": "success",
            "message": "Draft saved. Students still see the published timetable.",
            "timetable": draft,
        }

    @router.put("/api/admin/timetables/{timetable_id}")
    def admin_update_timetable(
        timetable_id: str, body: TimetableBody, authorization: str = Header(None)
    ):
        admin = require_admin(authorization)
        draft = run(
            update_timetable_by_id, db, admin, timetable_id, _dump(body)
        )
        return {"status": "success", "message": "Draft updated.", "timetable": draft}

    @router.post("/api/admin/timetables/{timetable_id}/publish")
    def admin_publish_timetable(
        timetable_id: str, authorization: str = Header(None)
    ):
        admin = require_admin(authorization)
        result = run(publish, db, admin, timetable_id)
        return {
            "status": "success",
            "message": "Timetable published. Students in this class now see it.",
            **result,
        }

    # --------------------------------------------------------
    # STUDENT: CLASS + PREFERENCES
    # (the student comes from the token; no student id is ever accepted)
    # --------------------------------------------------------

    @router.get("/api/student/class")
    def student_get_class(authorization: str = Header(None)):
        student = require_student(authorization)
        data = run(get_student_class, db, str(student["_id"]))
        return {"status": "success", **data}

    @router.patch("/api/student/class")
    def student_set_class(
        body: StudentClassBody, authorization: str = Header(None)
    ):
        student = require_student(authorization)

        # Official academic data: after the first choice, only the Admin can
        # change a student's class (so audience restrictions cannot be dodged).
        from academic_progression import (
            guard_student_class_change,
            record_student_class_change,
        )
        from event_audience import student_snapshot

        guard_student_class_change(db, student)
        before_snapshot = student_snapshot(student)

        data = run(
            assign_student_class, db, str(student["_id"]), body.class_id
        )

        record_student_class_change(
            db, str(student["_id"]), before_snapshot, "student_selected_class"
        )
        response = dict(data["timetable"])
        response["timetable_message"] = response.pop("message", "")
        response["status"] = "success"
        response["message"] = "Class saved."
        return response

    @router.put("/api/student/availability-preferences")
    def student_save_preferences(
        body: PreferencesBody, authorization: str = Header(None)
    ):
        student = require_student(authorization)
        prefs = run(
            save_availability_preferences,
            db,
            str(student["_id"]),
            _dump(body),
        )
        return {"status": "success", "preferences": prefs}

    return router