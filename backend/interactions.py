"""
interactions.py
===============
PHASE A of the recommender roadmap: record what students DO with events.

Why this exists
---------------
The sequential (BERT4Rec-style) model, the trained ranker and "not
interested" handling all need a history of student behaviour. Until now only
registrations, saved events and attendance were stored, and views were not
recorded at all. This module adds one append-only collection, `interactions`:

    {
      student_id, event_id, action, timestamp, source,
      category, organizer, skills, event_date, event_title   <- snapshot
    }

The event snapshot is copied into every row on purpose: events are one-off
and may later be edited or removed, but a future model must still know
"this student attended a Technical event run by the AI Club".

Who may record what
-------------------
SERVER-SIDE (written by main.py right after the real action succeeds, so
students can never fake them):
    register, unregister, save, unsave, attend
CLIENT-SIDE (sent by the student's browser, validated here):
    view, not_interested, and impressions (events shown on the dashboard)

Privacy: only student id, event id, action and time are stored. No free text.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SERVER_ACTIONS = {"register", "unregister", "save", "unsave", "attend", "undo_not_interested"}
CLIENT_ACTIONS = {"view", "not_interested"}
ALL_ACTIONS = SERVER_ACTIONS | CLIENT_ACTIONS | {"impression"}

SOURCES = {
    "dashboard_recommended", "dashboard_upcoming", "events_list",
    "event_details", "search", "server", "other",
}

# A view is counted once per event per 30 minutes (refreshing the page or
# re-opening a modal must not inflate the history).
DEDUPE_WINDOWS = {
    "view": timedelta(minutes=30),
    "not_interested": timedelta(hours=24),
    "impression": timedelta(hours=6),
}
MAX_IMPRESSIONS_PER_REQUEST = 30
CLIENT_HOURLY_LIMIT = 400          # protects the collection from floods


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    if not isinstance(value, datetime):
        return str(value or "")
    return value.isoformat() + ("Z" if value.tzinfo is None else "")


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def event_snapshot(event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The few event fields a future model needs, copied at record time."""
    event = event or {}
    skills = event.get("skills")
    if not isinstance(skills, list):
        skills = []
    return {
        "category": _text(event.get("category"), 60),
        "organizer": _text(event.get("organizer"), 80),
        "skills": [_text(s, 40) for s in skills[:10] if _text(s, 40)],
        "event_date": _text(event.get("date"), 10),
        "event_title": _text(event.get("title"), 120),
    }


def record_interaction(
    db,
    student_id: Any,
    event_id: Any,
    action: str,
    event: Optional[Dict[str, Any]] = None,
    source: str = "server",
) -> bool:
    """
    Append one interaction row. NEVER raises: if logging fails the student's
    real action (register / save / ...) must still succeed.
    """
    try:
        if action not in ALL_ACTIONS or not student_id or not event_id:
            return False

        if event is None:
            try:
                event = db.events.find_one({"_id": ObjectId(str(event_id))})
            except Exception:
                event = None

        db.interactions.insert_one({
            "student_id": str(student_id),
            "event_id": str(event_id),
            "action": action,
            "timestamp": utcnow(),
            "source": source if source in SOURCES else "other",
            **event_snapshot(event),
        })
        return True
    except Exception as error:  # logging must never break the app
        logger.warning("could not record interaction: %s", error)
        return False


class InteractionIn(BaseModel):
    event_id: str
    action: str
    source: Optional[str] = "other"


class ImpressionsIn(BaseModel):
    event_ids: List[str] = Field(default_factory=list)
    source: Optional[str] = "other"


def build_router(
    db,
    get_user_id: Callable[[Optional[str]], str],
    is_event_public: Callable[[Dict[str, Any]], bool],
    student_can_view: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None,
) -> APIRouter:
    """
    get_user_id(authorization) -> id of the logged-in user (raises 401).
    is_event_public(event)     -> True when students may see the event.
    student_can_view(event, student) -> optional publishing-audience check
                                  (event_audience.py); None = no audience rule.
    Both are passed in from main.py so this module never imports main.py.
    """
    router = APIRouter()

    # ---------------------------------------------------------- helpers
    def current_user(authorization):
        user_id = get_user_id(authorization)
        try:
            user = db.students.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            user = None
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user

    def require_student(authorization):
        user = current_user(authorization)
        if str(user.get("role", "")).lower() != "student":
            raise HTTPException(
                status_code=403,
                detail="Only students can record event interactions."
            )
        return user

    def load_visible_event(event_id, student=None):
        try:
            oid = ObjectId(str(event_id))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid event ID.")
        event = db.events.find_one({"_id": oid})
        if not event or not is_event_public(event):
            raise HTTPException(status_code=404, detail="Event not found")
        if student is not None and student_can_view and not student_can_view(event, student):
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    def check_rate_limit(student_id):
        since = utcnow() - timedelta(hours=1)
        count = db.interactions.count_documents({
            "student_id": student_id,
            "source": {"$ne": "server"},
            "timestamp": {"$gte": since},
        })
        if count >= CLIENT_HOURLY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many interactions recorded. Please slow down."
            )

    def already_recorded(student_id, event_id, action):
        cutoff = utcnow() - DEDUPE_WINDOWS[action]
        return db.interactions.find_one({
            "student_id": student_id,
            "event_id": event_id,
            "action": action,
            "timestamp": {"$gte": cutoff},
        }) is not None

    # --------------------------------------------------------- endpoints
    @router.post("/api/interactions")
    def post_interaction(
        body: InteractionIn,
        authorization: str = Header(None),
    ):
        """A student viewed an event, or marked it 'not interested'."""
        student = require_student(authorization)

        if body.action not in CLIENT_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unsupported action. Registrations, saves and attendance "
                    "are recorded by the server automatically."
                ),
            )

        event = load_visible_event(body.event_id, student)
        student_id = str(student["_id"])
        event_id = str(event["_id"])

        check_rate_limit(student_id)

        if already_recorded(student_id, event_id, body.action):
            return {"status": "duplicate"}

        record_interaction(
            db, student_id, event_id, body.action,
            event=event, source=body.source or "other",
        )
        return {"status": "success"}

    @router.post("/api/interactions/impressions")
    def post_impressions(
        body: ImpressionsIn,
        authorization: str = Header(None),
    ):
        """Events that were SHOWN to the student (future negative examples)."""
        student = require_student(authorization)
        student_id = str(student["_id"])

        ids = list(dict.fromkeys(str(i) for i in body.event_ids))
        if not ids:
            return {"status": "success", "recorded": 0}
        if len(ids) > MAX_IMPRESSIONS_PER_REQUEST:
            raise HTTPException(
                status_code=400,
                detail=f"At most {MAX_IMPRESSIONS_PER_REQUEST} events per request."
            )

        check_rate_limit(student_id)

        valid = []
        for event_id in ids:
            try:
                valid.append(ObjectId(event_id))
            except Exception:
                continue

        events = {
            str(e["_id"]): e
            for e in db.events.find({"_id": {"$in": valid}})
            if is_event_public(e)
            and (student_can_view is None or student_can_view(e, student))
        }

        recent = {
            row["event_id"]
            for row in db.interactions.find({
                "student_id": student_id,
                "action": "impression",
                "event_id": {"$in": list(events)},
                "timestamp": {"$gte": utcnow() - DEDUPE_WINDOWS["impression"]},
            })
        }

        recorded = 0
        for event_id, event in events.items():
            if event_id in recent:
                continue
            if record_interaction(
                db, student_id, event_id, "impression",
                event=event, source=body.source or "other",
            ):
                recorded += 1

        return {"status": "success", "recorded": recorded}

    @router.get("/api/interactions/me")
    def my_interactions(limit: int = 50, authorization: str = Header(None)):
        """Transparency: a student can see what has been recorded about them."""
        student = require_student(authorization)
        limit = max(1, min(int(limit), 200))
        rows = db.interactions.find(
            {"student_id": str(student["_id"])}
        ).sort("timestamp", -1).limit(limit)
        return {
            "status": "success",
            "interactions": [
                {
                    "event_id": r["event_id"],
                    "action": r["action"],
                    "timestamp": _iso(r.get("timestamp")),
                    "event_title": r.get("event_title", ""),
                    "category": r.get("category", ""),
                }
                for r in rows
            ],
        }

    @router.get("/api/admin/interactions/summary")
    def interactions_summary(authorization: str = Header(None)):
        """Admin: how much behaviour data has been collected so far."""
        user = current_user(authorization)
        if str(user.get("role", "")).lower() != "admin":
            raise HTTPException(status_code=403, detail="Admins only.")

        by_action: Dict[str, int] = {}
        per_student: Dict[str, int] = {}
        first = last = None

        for row in db.interactions.find({}):
            by_action[row.get("action", "?")] = by_action.get(row.get("action", "?"), 0) + 1
            per_student[row.get("student_id", "?")] = per_student.get(row.get("student_id", "?"), 0) + 1
            ts = row.get("timestamp")
            if isinstance(ts, datetime):
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last

        return {
            "status": "success",
            "total": sum(by_action.values()),
            "byAction": by_action,
            "students": len(per_student),
            "studentsWith3PlusInteractions": sum(1 for n in per_student.values() if n >= 3),
            "firstRecordedAt": _iso(first) if first else None,
            "lastRecordedAt": _iso(last) if last else None,
        }

    return router