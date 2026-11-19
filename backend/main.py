from fastapi import FastAPI, Header, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from database import db
from models import Student, LoginRequest, StudentProfile, Event
from typing import List, Optional
import re
from pwdlib import PasswordHash
import jwt
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from otp import create_otp, verify_otp, consume_verified_email
import secrets
#from recommendation_engine import get_recommendations
import os
from dotenv import load_dotenv
from google import genai
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Security
from pydantic import BaseModel, Field
import secrets
import hashlib
import logging
import smtplib
from reviewer_auth import router as reviewer_auth_router
from interactions import (
    record_interaction,
    build_router as build_interactions_router,
)
from departments import OFFICIAL_DEPARTMENTS, canonical_department
from budget import BudgetError, clean_budget_breakup
from pydantic import StrictBool
import report_files
from report_files import ReportFileError, validate_upload as report_validate
from checklist import (
    ACTIVITIES as CHECKLIST_ACTIVITIES,
    ChecklistError,
    build_view as checklist_view,
    clean_update as checklist_clean,
    default_status as checklist_default,
)
from registration_deadline import (
    deadline_problem,
    now_ist,
    parse_deadline,
    registration_is_closed,
    stored_deadline,
    to_iso,
)
from class_timetable import (
    create_class_timetable_router,
    ensure_class_indexes,
    resolve_student_timetable,
)

from email.message import EmailMessage

from pydantic import BaseModel, EmailStr

import time
from google.genai.errors import ServerError

# ============================================================
# GENERATIVE AI CONFIGURATION
# ============================================================

load_dotenv()

# ============================================================
# GENERATIVE AI CHAT REQUEST
# ============================================================

class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list)
# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI()

# HOD / Dean registration
app.include_router(reviewer_auth_router)

# ============================================================
# EVENT POSTERS
# ============================================================

POSTER_DIR = Path(__file__).resolve().parent / "uploads" / "posters"
POSTER_DIR.mkdir(parents=True, exist_ok=True)

POSTER_URL_PREFIX = "/uploads/posters/"
MAX_POSTER_BYTES = 3 * 1024 * 1024  # 3 MB

app.mount(
    POSTER_URL_PREFIX.rstrip("/"),
    StaticFiles(directory=str(POSTER_DIR)),
    name="event-posters",
)


# ============================================================
# LEGACY EVENT REPORT PDFs
# ------------------------------------------------------------
# Reports can no longer be uploaded, but older events may still
# point at a PDF here, so the folder stays mounted (read-only use).
# ============================================================

REPORT_DIR = Path(__file__).resolve().parent / "uploads" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

REPORT_URL_PREFIX = "/uploads/reports/"

app.mount(
    REPORT_URL_PREFIX.rstrip("/"),
    StaticFiles(directory=str(REPORT_DIR)),
    name="event-reports",
)


def detect_poster_type(data: bytes):
    """'png' / 'jpg' / 'webp' from the file's first bytes (not its name), else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def clean_poster_url(value):
    """None or "" -> None. Otherwise it must be a poster this server stored."""
    import re

    if value is None or str(value).strip() == "":
        return None

    value = str(value).strip()

    if not value.startswith(POSTER_URL_PREFIX):
        raise HTTPException(status_code=400, detail="Invalid poster address.")

    name = value[len(POSTER_URL_PREFIX):]

    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|webp)", name):
        raise HTTPException(status_code=400, detail="Invalid poster address.")

    if not (POSTER_DIR / name).is_file():
        raise HTTPException(status_code=400, detail="Poster file not found. Please upload it again.")

    return value


@app.post("/api/events/poster")
def upload_event_poster(
    file: UploadFile = File(...),
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(authorization)

    try:
        user = db.students.find_one({"_id": ObjectId(student_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    role = user.get("role", "student")
    status = user.get("status", "active")

    if role not in ["club", "faculty", "admin"]:
        raise HTTPException(status_code=403, detail="Only event organizers can upload posters.")

    if role in ["club", "faculty"] and status != "active":
        raise HTTPException(status_code=403, detail="Your organization account is pending admin approval.")

    data = file.file.read(MAX_POSTER_BYTES + 1)

    if len(data) > MAX_POSTER_BYTES:
        raise HTTPException(status_code=413, detail="Poster is too large (maximum 3 MB).")

    extension = detect_poster_type(data)

    if not extension:
        raise HTTPException(status_code=400, detail="Poster must be a PNG, JPG or WEBP image.")

    name = secrets.token_hex(16) + "." + extension
    (POSTER_DIR / name).write_bytes(data)

    return {"status": "success", "poster_url": POSTER_URL_PREFIX + name}


security = HTTPBearer()


import threading


def _warm_up_backend():
    """Pay the one-time start-up costs before the first student opens the dashboard."""
    import logging

    log = logging.getLogger("campuspath.startup")

    try:
        db.command("ping")  # opens the MongoDB connection
    except Exception:
        log.exception("MongoDB warm-up failed")

    try:
        import recommendation_engine  # slow first import

        # Load the semantic model now, not during the first dashboard
        # request (that request used to wait for it, or load it twice).
        recommendation_engine.get_embedding_model()
    except Exception:
        log.exception("Recommendation engine warm-up failed")


@app.on_event("startup")
def start_warm_up():
    threading.Thread(target=_warm_up_backend, daemon=True).start()
# ============================================================
# CORS
# ============================================================

# ============================================================
# CORS
# ============================================================

# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ============================================================
# PASSWORD HASHING
# ============================================================

password_hash = PasswordHash.recommended()

# Password recovery email configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("CAMPUSPATH_SMTP_EMAIL", "")
# Google shows app passwords in 4 groups with spaces; SMTP wants them without.
SMTP_APP_PASSWORD = os.getenv("CAMPUSPATH_SMTP_APP_PASSWORD", "").replace(" ", "")

# Shows up in the uvicorn terminal
mail_logger = logging.getLogger("uvicorn.error")


def send_password_reset_otp(recipient_email: str, otp: str):
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        raise RuntimeError("Password recovery email is not configured.")

    message = EmailMessage()
    message["Subject"] = "CampusPath AI - Password Reset OTP"
    message["From"] = SMTP_EMAIL
    message["To"] = recipient_email

    message.set_content(
        f"""Hello,

Your CampusPath AI password reset OTP is: {otp}

This OTP expires in 10 minutes and can only be used once.

If you did not request a password reset, please ignore this email.
"""
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.send_message(message)


def hash_reset_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


# ============================================================
# JWT CONFIGURATION
# ============================================================

SECRET_KEY = "campuspath-development-secret"

ALGORITHM = "HS256"


# ============================================================
# JWT HELPER
# ============================================================

def get_student_id_from_token(authorization: str):
    """
    The logged-in account's id from the token. A disabled or deleted account
    is refused here, so it stops working immediately even if its token is
    still valid (tokens themselves do not expire).
    """
    student_id = _decode_student_id_from_token(authorization)
    _ensure_account_usable(student_id)
    return student_id


def _ensure_account_usable(student_id):
    try:
        object_id = ObjectId(student_id)
    except Exception:
        return  # not an account id: leave it to the endpoint's own handling

    account = db.students.find_one({"_id": object_id}, {"status": 1})

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="This account no longer exists. Please sign in again.",
        )

    if str(account.get("status") or "").lower() == "disabled":
        raise HTTPException(
            status_code=403,
            detail="Your account has been disabled. Please contact the Admin.",
        )


def _decode_student_id_from_token(authorization: str):

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization token missing"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header"
        )

    token = authorization[len("Bearer "):].strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authorization token missing"
        )

    try:

        decoded = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        student_id = decoded.get("student_id")

        if not student_id:
            raise HTTPException(
                status_code=401,
                detail="Student ID missing from token"
            )

        return student_id

    except jwt.ExpiredSignatureError as e:

        print("JWT ERROR: token expired:", repr(e))

        raise HTTPException(
            status_code=401,
            detail="Token expired. Please login again."
        )

    except jwt.InvalidTokenError as e:

        print("JWT ERROR: invalid token:", repr(e))

        raise HTTPException(
            status_code=401,
            detail="Invalid token. Please login again."
        )

    except Exception as e:

        print("JWT ERROR:", repr(e))

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    return {
        "message": "CampusPath AI Backend is running!"
    }


# ============================================================
# MONGODB TEST
# ============================================================

@app.get("/api/test")
def test():

    try:

        db.command("ping")

        return {
            "status": "success",
            "message": "FastAPI and MongoDB are connected!"
        }

    except Exception as e:

        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================
# EVENT VISIBILITY + INTERACTION LOGGING (Phase A)
# ------------------------------------------------------------
# PUBLIC_EVENT_FILTER: the events students may see. Events must have
# completed the approval chain (Faculty -> HOD -> Dean -> Admin =
# "published"). Events created before the approval workflow existed
# have no approval_status and stay visible.
# ============================================================

PUBLIC_EVENT_FILTER = {
    "$or": [
        {"approval_status": "published"},
        {"approval_status": {"$exists": False}},
    ]
}


def is_event_public(event):
    """Python twin of PUBLIC_EVENT_FILTER for one event document."""
    if not event:
        return False
    return (
        "approval_status" not in event
        or event.get("approval_status") == "published"
    )


# Records what students DO with events (views, saves, registrations ...)
# so the recommender can learn from it. See interactions.py.
# Admin-controlled publishing audience (see event_audience.py)
from event_audience import (
    install as install_event_audience,
    viewer_can_view as _audience_viewer_can_view,
)
audience_gate = install_event_audience(app, db, get_student_id_from_token)

# Admin-controlled promotion + dynamic eligibility (see academic_progression.py)
from academic_progression import (
    install as install_academic_progression,
    official_profile_year,
)
install_academic_progression(app, db, get_student_id_from_token)

# Published-event modification requests, re-approval, versions (see event_modifications.py)
from event_modifications import (
    install as install_event_modifications,
    ModError as EventModificationError,
)


def _modification_validate(merged, user, role):
    """
    Exactly the validation an edited event already gets (details + budget rules),
    run on "live event + proposed values". Returns the cleaned values.
    """
    from pydantic import ValidationError

    allowed = set(EventWithResources.model_fields)
    try:
        model = EventWithResources(
            **{key: value for key, value in merged.items() if key in allowed}
        )
    except ValidationError as problem:
        raise HTTPException(
            status_code=422,
            detail=[
                {"loc": ["body"] + [str(p) for p in err["loc"]], "msg": err["msg"]}
                for err in problem.errors()
            ],
        )

    details = clean_event_details(model)
    cleaned = {
        **resolve_event_budget(model, role, creating=False),
        "title": model.title,
        "description": model.description,
        "category": model.category,
        "date": model.date,
        "time": model.time,
        "location": details["location"],
        "school_name": details["school_name"],
        "department": details["department"],
        "event_mode": details["event_mode"],
        "spoc_name": details["spoc_name"],
        "spoc_designation": details["spoc_designation"],
        "spoc_email": details["spoc_email"],
        "spoc_contact": details["spoc_contact"],
        "skills": model.skills,
    }
    if model.capacity is not None:
        cleaned["capacity"] = model.capacity
    if model.poster_url is not None:
        cleaned["poster_url"] = clean_poster_url(model.poster_url)
    if model.resource_persons is not None:
        cleaned["resource_persons"] = clean_resource_persons(model.resource_persons)
    return cleaned


event_modifications = install_event_modifications(
    app,
    db,
    get_student_id_from_token,
    helpers=dict(
        validate=_modification_validate,
        resubmission_status=lambda role, user: resubmission_status(role, user),
        belongs_to_club=lambda event, user: event_belongs_to_club(event, user),
        hod_block_reason=lambda hod, event: hod_review_block_reason(hod, event),
        user_club_id=lambda user: user_club_id(user),
        notify_faculty=lambda *a, **k: notify_faculty_of_club(*a, **k),
    ),
)

app.include_router(
    build_interactions_router(
        db,
        get_student_id_from_token,
        is_event_public,
        student_can_view=_audience_viewer_can_view,
    )
)


# ============================================================
# "NOT INTERESTED" (Phase H)
# ------------------------------------------------------------
# A student's thumbs-down on an event. That event is left out of THAT
# student's recommendations until they undo it. Nothing is ever deleted:
# undoing only switches the row to inactive. The state lives in
# `not_interested_events`; the interactions log (used for learning) gets a
# matching row.
# ============================================================

class NotInterestedIn(BaseModel):
    source: Optional[str] = "other"


def _hide_require_student(authorization):
    student_id = str(get_student_id_from_token(authorization))
    try:
        user = db.students.find_one({"_id": ObjectId(student_id)})
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if str(user.get("role", "")).lower() != "student":
        raise HTTPException(status_code=403, detail="Only students can hide events.")
    return student_id


def get_not_interested_ids(student_id):
    """Ids of the events this student hid. Never raises: on a problem it returns []."""
    try:
        rows = db.not_interested_events.find(
            {"student_id": str(student_id), "active": True},
            {"event_id": 1},
        )
        return [str(row["event_id"]) for row in rows if row.get("event_id") is not None]
    except Exception:
        logging.getLogger("campuspath.recommendations").exception(
            "could not read the not-interested events"
        )
        return []


@app.post("/api/events/{event_id}/not-interested")
def mark_not_interested(
    event_id: str,
    body: Optional[NotInterestedIn] = None,
    authorization: str = Header(None),
):
    student_id = _hide_require_student(authorization)

    try:
        event = db.events.find_one({"_id": ObjectId(event_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid event ID.")
    if not event or not is_event_public(event):
        raise HTTPException(status_code=404, detail="Event not found")
    audience_gate.require_student_access(event, student_id)

    now = datetime.now(timezone.utc)
    db.not_interested_events.update_one(
        {"student_id": student_id, "event_id": event_id},
        {
            "$set": {
                "active": True,
                "updated_at": now,
                "event_title": str(event.get("title", ""))[:120],
                "category": str(event.get("category", ""))[:60],
                "organizer": str(event.get("organizer", ""))[:80],
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    # Learning log: at most one row per event per 24 hours, so toggling
    # the button can never flood it.
    recent = db.interactions.find_one({
        "student_id": student_id,
        "event_id": event_id,
        "action": "not_interested",
        "timestamp": {"$gte": now - timedelta(hours=24)},
    })
    if not recent:
        record_interaction(
            db, student_id, event_id, "not_interested",
            event=event, source=(body.source if body else None) or "other",
        )

    return {"status": "success", "not_interested": True, "event_id": event_id}


@app.delete("/api/events/{event_id}/not-interested")
def undo_not_interested(event_id: str, authorization: str = Header(None)):
    student_id = _hide_require_student(authorization)
    now = datetime.now(timezone.utc)

    result = db.not_interested_events.update_one(
        {"student_id": student_id, "event_id": event_id, "active": True},
        {"$set": {"active": False, "undone_at": now, "updated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="This event is not hidden.")

    record_interaction(db, student_id, event_id, "undo_not_interested")
    return {"status": "success", "not_interested": False, "event_id": event_id}


@app.get("/api/me/not-interested")
def list_not_interested(authorization: str = Header(None)):
    student_id = _hide_require_student(authorization)
    rows = list(
        db.not_interested_events.find(
            {"student_id": student_id, "active": True}
        ).sort("updated_at", -1)
    )
    return {
        "status": "success",
        "events": [
            {
                "event_id": str(row.get("event_id", "")),
                "title": row.get("event_title", ""),
                "category": row.get("category", ""),
                "hidden_at": (
                    row["updated_at"].isoformat()
                    if hasattr(row.get("updated_at"), "isoformat")
                    else ""
                ),
            }
            for row in rows
        ],
    }


@app.delete("/api/me/not-interested")
def undo_all_not_interested(authorization: str = Header(None)):
    student_id = _hide_require_student(authorization)
    now = datetime.now(timezone.utc)
    result = db.not_interested_events.update_many(
        {"student_id": student_id, "active": True},
        {"$set": {"active": False, "undone_at": now, "updated_at": now}},
    )
    return {"status": "success", "restored": int(result.modified_count)}


# ============================================================
# LEARNED SIGNALS (Phases F and G)
# ------------------------------------------------------------
# Both are OFF unless a model has passed its quality gate on REAL data
# (see sequential_recommender.py and trained_ranker.py). Any problem
# returns "nothing" and the recommender behaves exactly as before.
# ============================================================

def get_sequential_scores_for_student(student_id, candidate_events):
    """
    Sequential scores for this student's candidate events, normalised per
    request to 0-100 (rank percentile), or {} when the signal is unavailable
    or carries no information. Never raises.
    """
    try:
        import sequential_recommender as sequential

        # Cheap and PyTorch-free once the first check is done; nothing is read
        # from the database unless a usable model exists.
        if not sequential.status().get("available"):
            return {}

        rows = list(
            db.interactions.find(
                {"student_id": str(student_id)},
                {"_id": 0, "action": 1, "category": 1, "organizer": 1,
                 "event_id": 1, "timestamp": 1},
            ).sort("timestamp", -1).limit(100)
        )
        rows.reverse()

        raw = sequential.score_events(rows, candidate_events)
        if not raw:
            return {}

        from score_normalization import normalize_scores
        normalized = normalize_scores(raw, method="rank")
        if len(set(normalized.values())) <= 1:
            return {}
        return normalized
    except Exception:
        logging.getLogger("campuspath.recommendations").exception(
            "sequential signal failed; continuing without it"
        )
        return {}


def get_ranker_for_engine():
    """The learned ranker if one passed its quality gate on real data, else None. Never raises."""
    try:
        from trained_ranker import get_active_ranker
        return get_active_ranker()
    except Exception:
        logging.getLogger("campuspath.recommendations").exception(
            "learned ranker unavailable; continuing without it"
        )
        return None


# ============================================================
# STUDENT REGISTRATION
# ============================================================

@app.post("/api/register")
def register_student(student: Student):

    # Normalize email
    email = student.email.strip().lower()

    # Get selected role
    role = getattr(student, "role", "student")

        # The Inter-College Leader role has been replaced by Faculty.
    if role == "intercollege":
        return {
            "success": False,
            "status": "error",
            "message": (
                "Inter-College accounts are no longer available. "
                "Please register as Faculty or Club Leader."
            ),
        }

    # Public sign-up is for students, club leaders and faculty only.
    # HOD / Dean accounts register through their own pages, and Admin
    # accounts are never created from a public form.
    if role not in ("student", "club", "faculty"):
        return {
            "success": False,
            "status": "error",
            "message": "This account type cannot be created from public sign-up.",
        }

    # Check if email already exists
    existing_student = db.students.find_one(
        {"email": email}
    )

    if existing_student:

        return {
            "success": False,
            "status": "error",
            "message": "Email already registered"
        }

    # Hash password
    hashed_password = password_hash.hash(
        student.password
    )

    # Base student data
    student_data = {
        "name": student.name,
        "email": email,
        "password": hashed_password,
        "department": student.department,
        "role": role,
        "status": "active" if role == "student" else "pending",
        "submitted_at": datetime.now(timezone.utc)
    }

    # Students can give their college at signup (organizers see it)
    if role == "student":
        student_data["college_name"] = str(
            getattr(student, "college_name", "") or ""
        ).strip()

    # ========================================================
    # CLUB LEADER
    # DO NOT CHANGE THIS LOGIC
    # ========================================================

    if role == "club":

        student_data.update({
            "student_id": student.student_id,
            "college_name": student.college_name,
            "club_name": student.club_name,
            "club_category": student.club_category,
            "position": student.position,
            "coordinator_name": student.coordinator_name,
            "coordinator_email":
                str(student.coordinator_email).strip().lower()
                if student.coordinator_email
                else ""
        })

    # Faculty accounts belong to one club (same fields as a club account)
    if role == "faculty":
        student_data.update({
            "college_name": str(getattr(student, "college_name", "") or "").strip(),
            "club_name": str(getattr(student, "club_name", "") or "").strip(),
            "club_category": str(getattr(student, "club_category", "") or "").strip(),
            "position": "Faculty",
        })

    # Shared key linking a club's coordinator(s), faculty and events
    if role in ("club", "faculty"):
        student_data["club_id"] = make_club_id(
            student_data.get("club_name"),
            student_data.get("college_name"),
        )

    # ========================================================
    # SAVE TO MONGODB
    # ========================================================

    result = db.students.insert_one(
        student_data
    )

    # QR: the OTP step is proven by the server (see otp.py), not by the browser.
    if consume_verified_email(email):
        db.students.update_one(
            {"_id": result.inserted_id},
            {"$set": {"otp_verified_at": datetime.now(timezone.utc)}},
        )
        # Student Identity QR (students only; it never raises)
        if role == "student":
            qr_hook("on_student_registered", str(result.inserted_id))

    print("=" * 50)
    print("ACCOUNT CREATED SUCCESSFULLY")
    print(f"Name: {student.name}")
    print(f"Email: {email}")
    print(f"Role: {role}")
    print(f"MongoDB ID: {result.inserted_id}")

    print("=" * 50)

    # ========================================================
    # RESPONSE
    # IMPORTANT: THIS RETURN IS INSIDE THE FUNCTION
    # ========================================================

    return {

        "success": True,

        "status": "success",

        "message": "Account created successfully!",

        "student_id": str(
            result.inserted_id
        ),

        "name": student.name,

        "email": email,

        "department": student.department,

        "role": role

    }


# ============================================================
# LOGIN
# ============================================================


# ============================================================
# LOGIN
# ============================================================

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str


@app.post("/api/auth/forgot-password")
def forgot_password(request: ForgotPasswordRequest):
    email = str(request.email).strip().lower()

    # Use the existing users collection.
    user = db.students.find_one({"email": email})

    # Don't reveal whether an account exists.
    generic_response = {
        "success": True,
        "message": (
            "If an account exists for this email, "
            "a password reset OTP has been sent."
        ),
    }

    if not user:
        return generic_response

    otp = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)

    # Store the OTP hash, not the plain OTP.
    db.students.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_reset_otp_hash": hash_reset_otp(otp),
                "password_reset_expires_at": now + timedelta(minutes=10),
                "password_reset_attempts": 0,
            }
        },
    )

    try:
        send_password_reset_otp(email, otp)
    except Exception as exc:
        # Print the REAL reason in the backend terminal.
        mail_logger.error(
            "PASSWORD RESET EMAIL FAILED: %s: %s",
            type(exc).__name__,
            exc,
        )

        if isinstance(exc, RuntimeError):
            failure_detail = (
                "Password recovery email is not configured on the server."
            )
        elif isinstance(exc, smtplib.SMTPAuthenticationError):
            failure_detail = (
                "The server's email login was rejected. "
                "Check the Gmail app password."
            )
        else:
            failure_detail = (
                "Unable to send the recovery email. Please try again later."
            )

        # Invalidate the OTP if email delivery fails.
        db.students.update_one(
            {"_id": user["_id"]},
            {
                "$unset": {
                    "password_reset_otp_hash": "",
                    "password_reset_expires_at": "",
                }
            },
        )
        raise HTTPException(
            status_code=503,
            detail=failure_detail,
        )

    return generic_response


@app.post("/api/auth/reset-password")
def reset_password(request: ResetPasswordRequest):
    email = str(request.email).strip().lower()
    otp = request.otp.strip()
    new_password = request.new_password

    if not otp.isdigit() or len(otp) != 6:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    if len(new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 8 characters.",
        )

    now = datetime.now(timezone.utc)
    invalid_otp = HTTPException(
        status_code=400,
        detail="Invalid or expired OTP. Request a new OTP if needed.",
    )

    user = db.students.find_one({"email": email})

    if (
        not user
        or not user.get("password_reset_otp_hash")
        or not user.get("password_reset_expires_at")
    ):
        raise invalid_otp

    expires_at = user["password_reset_expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    attempts = int(user.get("password_reset_attempts", 0))

    # Expired, or too many wrong guesses: burn the OTP.
    if expires_at <= now or attempts >= 5:
        db.students.update_one(
            {"_id": user["_id"]},
            {"$unset": {
                "password_reset_otp_hash": "",
                "password_reset_expires_at": "",
                "password_reset_attempts": "",
            }},
        )
        raise invalid_otp

    if not secrets.compare_digest(
        user["password_reset_otp_hash"], hash_reset_otp(otp)
    ):
        db.students.update_one(
            {"_id": user["_id"]},
            {"$inc": {"password_reset_attempts": 1}},
        )
        raise invalid_otp

    # Use the same hasher as registration/login.
    new_password_hash = password_hash.hash(new_password)

    # Consume the OTP and update the password.
    result = db.students.update_one(
        {
            "_id": user["_id"],
            "password_reset_otp_hash": hash_reset_otp(otp),
            "password_reset_expires_at": {"$gt": now},
        },
        {
            "$set": {"password": new_password_hash},
            "$unset": {
                "password_reset_otp_hash": "",
                "password_reset_expires_at": "",
                "password_reset_attempts": "",
            },
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=400,
            detail="OTP already used or expired. Request a new OTP.",
        )

    return {
        "success": True,
        "message": "Password reset successfully. You can now log in.",
    }

@app.post("/api/login")
def login_student(login: LoginRequest):

    # Normalize login email
    email = login.email.strip().lower()

    # Find account by email
    student = db.students.find_one(
        {"email": email}
    )

    # Account not found
    if not student:

        return {

            "status": "error",

            "message": "Invalid email or password"

        }
    # ========================================================
    # VERIFY PASSWORD
    # ========================================================

    password_correct = password_hash.verify(
        login.password,
        student["password"]
    )

    if not password_correct:

        return {

            "status": "error",

            "message": "Invalid email or password"

        }

    # ========================================================
    # VERIFY ROLE
    # ========================================================
    #
    # The role selected on the login page must match
    # the role stored in MongoDB for this email.
    #
    # Example:
    #
    # MongoDB:
    # email = student@gmail.com
    # role  = student
    #
    # If user selects "club", login will be rejected.
    #
    # ========================================================

    registered_role = student.get(
        "role",
        "student"
    )

    # The Inter-College Leader role has been replaced by Faculty.
    if registered_role == "intercollege":
        return {
            "status": "error",
            "message": (
                "Inter-College accounts have been retired. Please contact "
                "the administrator to be set up as Faculty."
            ),
        }

    if login.role != registered_role:

        return {

            "status": "error",

            "message":
                "This email is not registered for the selected role."

        }

    # ========================================================
    # ========================================================
    # CHECK ORGANIZATION ACCOUNT APPROVAL
    # ========================================================

    account_status = student.get(
        "status",
        "active"
    )

    # An Admin has disabled this account (checked after the password, so
    # it is not revealed to someone who does not know it).
    if str(account_status).lower() == "disabled":
        return {
            "status": "error",
            "message": "Your account has been disabled. Please contact the Admin.",
        }

    # ========================================================
    # REJECTED ORGANIZATION ACCOUNT
    # ========================================================
    #
    # If an admin rejected the application, do not allow login.
    # Return the rejection message so the login page can display it.
    #
    if registered_role in ["club", "faculty", "hod", "dean"] and account_status == "rejected":

        rejection_reason = str(
            student.get("rejection_reason", "")
        ).strip()

        rejection_note = str(
            student.get("rejection_note", "")
        ).strip()

        message = "Your organization registration has been rejected by the admin."

        if rejection_reason:
            message += f" Reason: {rejection_reason}"

        if rejection_note:
            message += f" Note: {rejection_note}"

        return {
            "status": "rejected",
            "message": message,
            "role": registered_role,
            "rejection_reason": rejection_reason,
            "rejection_note": rejection_note
        }

    # ========================================================
    # PENDING ORGANIZATION ACCOUNT
    # ========================================================

    if registered_role != "student" and account_status != "active":

        return {
            "status": "pending",

            "message":
                "Your organization account is pending admin approval.",

            "role": registered_role
        }

    # ========================================================
    # CREATE PERMANENT JWT TOKEN
    # ========================================================
    #
    # No "exp" claim is included.
    # Therefore this token does not expire.
    #
    # ========================================================

    token_data = {

        "student_id": str(
            student["_id"]
        ),

        "email": student["email"],

        "role": registered_role

    }

    token = jwt.encode(
        token_data,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    # ========================================================
    # LOGIN SUCCESS
    # ========================================================

    # ========================================================
    # CHECK ONBOARDING COMPLETION
    # ========================================================

    
    # ========================================================
    # CHECK ONBOARDING COMPLETION
    # ========================================================

    student_id = str(student["_id"])

    profile_exists = db.profiles.find_one({
        "student_id": student_id
    })

    timetable_exists = db.timetables.find_one({
        "student_id": student_id
    })

    # The timetable step is done once the student has chosen an official
    # class (or, for older accounts, saved a personal timetable).
    onboarding_completed = (
        profile_exists is not None
        and (
            timetable_exists is not None
            or bool(student.get("class_id"))
        )
    )

    print("=================================")
    print("ONBOARDING STATUS")
    print("Student ID:", student_id)
    print("Profile Exists:", profile_exists is not None)
    print("Timetable Exists:", timetable_exists is not None)
    print("Onboarding Completed:", onboarding_completed)
    print("=================================")

    # ========================================================
    # LOGIN SUCCESS
    # ========================================================

    response_data = {
        "status": "success",
        "message": "Login successful!",
        "token": token,
        "student_id": student_id,
        "name": student.get("name", ""),
        "email": student.get("email", ""),
        "department": student.get("department", ""),
        "role": registered_role,
        "onboarding_completed": onboarding_completed
    }

    print("LOGIN SUCCESS RESPONSE IS BEING RETURNED")
    print("LOGIN RESPONSE DATA:", response_data)

    return response_data



# ============================================================
# STUDENT PROFILE
# ============================================================

@app.post("/api/profile")
def save_profile(
    profile: StudentProfile,
    authorization: str = Header(None)
):

    student_id = get_student_id_from_token(
        authorization
    )

    profile_data = {

        "student_id": student_id,

        "year": official_profile_year(db, student_id, profile.year),

        "interests": profile.interests,

        "skills": profile.skills,

        "career_goal": profile.career_goal,

        "preferred_domains": profile.preferred_domains

    }

    db.profiles.update_one(
        {"student_id": student_id},
        {"$set": profile_data},
        upsert=True
    )

    return {

        "status": "success",

        "message": "Profile saved successfully!"

    }

@app.get("/api/profile")
def get_profile(
    authorization: str = Header(None)
):

    student_id = get_student_id_from_token(
        authorization
    )

    profile = db.profiles.find_one(
        {
            "student_id": student_id
        }
    )

    if not profile:
        return {
            "interests": [],
            "skills": [],
            "career_goal": "",
            "preferred_domains": [],
            "year": official_profile_year(db, student_id, "")
        }

    return {
        "interests": profile.get(
            "interests",
            []
        ),

        "skills": profile.get(
            "skills",
            []
        ),

        "career_goal": profile.get(
            "career_goal",
            ""
        ),

        "preferred_domains": profile.get(
            "preferred_domains",
            []
        ),

        "year": official_profile_year(
            db, student_id, profile.get("year", "")
        )
    }
# ============================================================
# STUDENT TIMETABLE
# ============================================================

@app.post("/api/student/timetable")
def save_student_timetable(
    timetable_data: dict,
    authorization: str = Header(None)
):
    """
    RETIRED. Students no longer enter their own timetable: it comes from the
    official timetable the Admin publishes for the class the student chose
    (PATCH /api/student/class). Availability preferences are saved with
    PUT /api/student/availability-preferences.

    Timetables students saved earlier are kept (read only) as a fallback for
    students who have not chosen a class yet.
    """

    # Still require a valid login.
    get_student_id_from_token(
        authorization
    )

    raise HTTPException(
        status_code=410,
        detail=(
            "Students can no longer upload their own timetable. "
            "Choose your official class instead."
        )
    )
# ============================================================
# GET STUDENT TIMETABLE
# ============================================================

@app.get("/api/student/timetable")
def get_student_timetable(
    authorization: str = Header(None)
):
    """
    The student's timetable.

    It now comes from the Admin-published timetable of the class the student
    selected (see class_timetable.py). The keys the dashboard already reads
    (timetable, config, preferences) are unchanged; source / class /
    published / version / message are new. Students with no class yet fall
    back to their old personal timetable (source = "legacy").
    """

    # Decode and validate JWT
    student_id = get_student_id_from_token(
        authorization
    )

    resolved = resolve_student_timetable(db, student_id)

    return {
        "status": "success",
        **resolved
    }


# ============================================================
# CLASS & TIMETABLE MANAGEMENT (Admin-managed official timetables)
# ============================================================

app.include_router(
    create_class_timetable_router(db, get_student_id_from_token)
)


@app.on_event("startup")
def create_class_timetable_indexes():
    ensure_class_indexes(db)


# ============================================================
# SHARED JSON HELPERS
# ============================================================

def iso_utc(value):
    """datetime -> '2026-10-21T09:35:40Z' so the browser can show local time."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


def json_safe(value):
    """
    Make Mongo documents safe to send as JSON: ObjectId -> text,
    datetime -> ISO text. One odd field in a single event used to make
    the whole /api/events request fail with a 500.
    """
    if isinstance(value, ObjectId):
        return str(value)

    if isinstance(value, datetime):
        return iso_utc(value)

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]

    return value


# ============================================================
# APPLICATION KIND HELPERS
# Faculty / HOD / Dean accounts are "faculty" applications;
# clubs are "organization" applications.
# ============================================================

FACULTY_ROLES = ("faculty", "hod", "dean")
ROLE_LABELS = {"faculty": "Faculty", "hod": "HOD", "dean": "Dean", "club": "Club"}


def application_kind(role):
    return "faculty" if role in FACULTY_ROLES else "organization"


def role_label(role):
    return ROLE_LABELS.get(role, "Organization")


def date_out(value):
    """datetime -> ISO UTC text; already-text values pass through."""
    return iso_utc(value) if isinstance(value, datetime) else str(value or "")


# ============================================================
# ADMIN - PENDING ORGANIZATION APPLICATIONS
# ============================================================

@app.get("/api/admin/pending-applications")
def get_pending_applications(
    authorization: str = Header(None)
):

    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({"_id": ObjectId(admin_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid admin user ID")

    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can view pending applications."
        )

    applications = db.students.find({
        "role": {"$in": ["club", "faculty", "hod", "dean"]},
        "status": "pending"
    }).sort("_id", -1)

    result = []

    for application in applications:
        role = application.get("role", "club")

        kind = application_kind(role)
        is_org = kind == "organization"

        result.append({
            "id": str(application["_id"]),
            "name": application.get("name", ""),
            "email": application.get("email", ""),
            "phone": application.get("phone", ""),
            "department": application.get("department", ""),
            "role": role,
            "kind": kind,
            "designation": application.get("designation", ""),
            "employee_id": application.get("employee_id", ""),
            "college_name": application.get("college_name", "") or application.get("college", ""),
            # club-only fields stay empty for Faculty / HOD / Dean
            "student_id": application.get("student_id", "") if is_org else "",
            "club_name": application.get("club_name", "") if is_org else "",
            "club_category": application.get("club_category", "") if is_org else "",
            "position": application.get("position", "") if is_org else "",
            "coordinator_name": application.get("coordinator_name", "") if is_org else "",
            "coordinator_email": application.get("coordinator_email", "") if is_org else "",
            "submitted_at": date_out(application.get("submitted_at", "")),
            "status": application.get("status", "pending")
        })

    return {
        "status": "success",
        "applications": result
    }


# ============================================================
# ADMIN - ORGANIZATIONS
# ============================================================

@app.get("/api/admin/organizations")
def get_admin_organizations(
    authorization: str = Header(None),
    status: str = "all",
    role: str = "all",
    search: str = ""
):
    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({"_id": ObjectId(admin_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid admin user ID")

    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can view organizations."
        )

    query = {
        "role": {"$in": ["club", "faculty"]}
    }

    if status in ["pending", "active", "rejected"]:
        query["status"] = status

    if role in ["club", "faculty"]:
        query["role"] = role

    applications = db.students.find(query).sort("_id", -1)

    q = search.strip().lower()
    result = []

    for organization in applications:
        organization_role = organization.get("role", "club")

        organization_name = (
            organization.get("club_name")
            or organization.get("organization_name")
            or "Club"
        )

        college_name = (
            organization.get("college_name")
            or organization.get("college")
            or ""
        )

        applicant_name = organization.get("name", "")
        email = organization.get("email", "")
        coordinator_name = organization.get("coordinator_name", "")
        coordinator_email = organization.get("coordinator_email", "")
        position = organization.get("position", "")
        organization_status = organization.get("status", "pending")

        if q:
            searchable = " ".join([
                organization_name,
                applicant_name,
                email,
                college_name,
                coordinator_name,
                coordinator_email,
                position,
                organization_role,
            ]).lower()

            if q not in searchable:
                continue

        submitted_at = organization.get("submitted_at")
        if hasattr(submitted_at, "strftime"):
            submitted_date = submitted_at.strftime("%b %d, %Y")
        elif submitted_at:
            submitted_date = str(submitted_at)
        else:
            submitted_date = ""

        result.append({
            "id": str(organization["_id"]),
            "organizationName": organization_name,
            "organizationType": organization_role,
            "college": college_name,
            "applicantName": applicant_name,
            "email": email,
            "phone": organization.get("phone", "") or organization.get("mobile", ""),
            "department": organization.get("department", ""),
            "studentId": organization.get("student_id", ""),
            "category": organization.get("club_category", "") or organization.get("category", ""),
            "position": position,
            "coordinatorName": coordinator_name,
            "coordinatorEmail": coordinator_email,
            "submittedAt": submitted_date,
            "status": organization_status,
        })

    return {
        "status": "success",
        "organizations": result,
        "count": len(result)
    }


# ============================================================
# ADMIN - REGISTRATIONS
# ============================================================

@app.get("/api/admin/registrations")
def get_admin_registrations(
    authorization: str = Header(None)
):
    """
    Return all event registrations for the admin Registrations page.
    If no students have registered yet, return an empty list.
    Passwords are never returned.
    """

    # --------------------------------------------------------
    # Verify JWT
    # --------------------------------------------------------

    admin_id = get_student_id_from_token(authorization)

    # --------------------------------------------------------
    # Verify admin account
    # --------------------------------------------------------

    try:
        admin_user = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin user ID"
        )

    if not admin_user:
        raise HTTPException(
            status_code=401,
            detail="Admin account not found."
        )

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required."
        )

    # --------------------------------------------------------
    # Read registrations from MongoDB
    # --------------------------------------------------------

    registrations = db.event_registrations.find({}).sort(
        "_id",
        -1
    )

    result = []

    for registration in registrations:

        item = dict(registration)

    # --------------------------------------------------------
    # Registration ID
    # --------------------------------------------------------

        if "_id" in item:
            item["id"] = str(item.pop("_id"))

    # --------------------------------------------------------
    # GET STUDENT DETAILS
    # --------------------------------------------------------

        student = None

        try:
            student_id = registration.get("student_id")

            if student_id:
                student = db.students.find_one({
                    "_id": ObjectId(str(student_id))
                })
        except Exception:
            student = None

        if student:

            item["student_name"] = (
                student.get("name")
                or student.get("student_name")
                or student.get("studentName")
                or "Unknown Student"
            )

            item["student_email"] = (
                student.get("email")
                or ""
            )

            item["department"] = (
                student.get("department")
                or ""
            )

        else:

            item["student_name"] = "Unknown Student"
            item["student_email"] = ""
            item["department"] = ""

    # --------------------------------------------------------
    # GET EVENT DETAILS
    # --------------------------------------------------------

        event = None

        try:
            event_id = registration.get("event_id")

            if event_id:
                event = db.events.find_one({
                    "_id": ObjectId(str(event_id))
                })
        except Exception:
            event = None

        if event:

            item["event_name"] = (
                event.get("title")
                or event.get("event_name")
                or "Unknown Event"
            )

            item["organizer"] = (
                event.get("organizer")
                or event.get("organizer_name")
                or "—"
            )

            item["event_category"] = (
                event.get("category")
                or ""
            )

        else:

            item["event_name"] = (
                registration.get("event_title")
                or "Unknown Event"
            )

            item["organizer"] = "—"
            item["event_category"] = ""

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

        item["status"] = registration.get(
            "status",
            "registered"
        )

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

        registered_at = registration.get(
            "registered_at"
        )

        if isinstance(registered_at, datetime):
            item["registered_at"] = (
                registered_at.isoformat()
            )

    # --------------------------------------------------------
    # CONVERT REMAINING OBJECT IDS
    # --------------------------------------------------------

        for key, value in list(item.items()):

            if isinstance(value, ObjectId):
                item[key] = str(value)

            elif isinstance(value, datetime):
                item[key] = value.isoformat()

        result.append(item)

    print(
    "ADMIN REGISTRATIONS:",
    result
)

    print("REGISTRATIONS FROM DATABASE:", result)

    # Empty collection is a valid state, not an error.
    return {
        "status": "success",
        "registrations": result,
        "total": len(result)
    }




# ============================================================
# ADMIN - EVENTS
# ============================================================

@app.get("/api/admin/events")
def get_admin_events(
    authorization: str = Header(None)
):
    """Return all events for the admin Events page."""

    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin user ID"
        )

    if not admin_user:
        raise HTTPException(
            status_code=401,
            detail="Admin account not found."
        )

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required."
        )

    events = db.events.find({}).sort("_id", -1)

    result = []

    for event in events:
        item = dict(event)

        if "_id" in item:
            item["id"] = str(item.pop("_id"))

        for key, value in list(item.items()):
            if isinstance(value, datetime):
                item[key] = value.isoformat()
            elif isinstance(value, ObjectId):
                item[key] = str(value)

        result.append(item)

    return {
        "status": "success",
        "events": result,
        "total": len(result)
    }


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@app.get("/api/admin/dashboard")
def admin_dashboard(
    authorization: str = Header(None)
):
    # --------------------------------------------------------
    # Get logged-in user's ID from JWT
    # --------------------------------------------------------

    admin_id = get_student_id_from_token(
        authorization
    )

    # --------------------------------------------------------
    # Find logged-in account
    # --------------------------------------------------------

    try:
        admin_user = db.students.find_one(
            {
                "_id": ObjectId(admin_id)
            }
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user ID"
        )

    if not admin_user:
        raise HTTPException(
            status_code=404,
            detail="Admin user not found"
        )

    # --------------------------------------------------------
    # Only administrators can access the dashboard
    # --------------------------------------------------------

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can view the admin dashboard."
        )

    # ========================================================
    # ORGANIZATION APPLICATION COUNTS
    # ========================================================

    # Organizations are clubs only; Faculty / HOD / Dean accounts are counted
    # separately as faculty accounts.
    organization_roles = {
        "$in": ["club"]
    }

    faculty_account_roles = {
        "$in": ["faculty", "hod", "dean"]
    }

    # Pending / rejected applications also include HOD and Dean requests.
    application_roles = {
        "$in": ["club", "faculty", "hod", "dean"]
    }

    pending_applications = db.students.count_documents(
        {
            "role": application_roles,
            "status": "pending"
        }
    )

    approved_organizations = db.students.count_documents(
        {
            "role": organization_roles,
            "status": "active"
        }
    )

    approved_faculty = db.students.count_documents(
        {
            "role": faculty_account_roles,
            "status": "active"
        }
    )

    rejected_applications = db.students.count_documents(
        {
            "role": application_roles,
            "status": "rejected"
        }
    )

    # ========================================================
    # TOTAL USERS
    # ========================================================

    total_users = db.students.count_documents({})

    # ========================================================
    # PUBLISHED EVENTS
    # ========================================================

    published_events = db.events.count_documents(
        {
            "status": {
                "$in": ["published", "PUBLISHED"]
            }
        }
    )

    # ========================================================
    # TOTAL REGISTRATIONS
    # ========================================================

    total_registrations = db.event_registrations.count_documents({
    "status": "registered"
})

    # ========================================================
    # PENDING APPLICATION PREVIEW
    # ========================================================

    pending_documents = db.students.find(
        {
            "role": {
                "$in": ["club", "faculty", "hod", "dean"]
            },
            "status": "pending"
        }
    ).sort(
        "_id",
        -1
    ).limit(10)

    pending_preview = []

    for application in pending_documents:

        organization_type = application.get(
            "role",
            "club"
        )

        kind = application_kind(organization_type)

        if kind == "faculty":
            organization_name = application.get("department", "")
        else:
            organization_name = application.get("club_name", "")

        submitted_at = application.get(
            "submitted_at",
            ""
        )

        if submitted_at:
            try:
                if hasattr(submitted_at, "strftime"):
                    submitted_date = submitted_at.strftime(
                        "%b %d, %Y"
                    )
                else:
                    submitted_date = str(
                        submitted_at
                    )
            except Exception:
                submitted_date = str(
                    submitted_at
                )
        else:
            submitted_date = ""

        pending_preview.append(
            {
                "id": str(
                    application["_id"]
                ),
                "applicantName": application.get(
                    "name",
                    ""
                ),
                "role": organization_type,
                "kind": kind,
                "organizationName": organization_name,
                "organizationType": organization_type,
                "college": (
                    application.get("college_name", "")
                    or application.get("college", "")
                ),
                "position": (
                    application.get("designation", "")
                    if kind == "faculty"
                    else application.get("position", "")
                ),
                "date": submitted_date,
                "status": application.get(
                    "status",
                    "pending"
                )
            }
        )

    # ========================================================
    # RETURN LIVE DATABASE DATA
    # ========================================================

    return {
        "status": "success",

        "kpis": {
            "pendingApplications": pending_applications,
            "approvedOrganizations": approved_organizations,
            "approvedFaculty": approved_faculty,
            "rejectedApplications": rejected_applications,
            "totalUsers": total_users,
            "publishedEvents": published_events,
            "totalRegistrations": total_registrations
        },

        "pendingPreview": pending_preview
    }


@app.get("/api/admin/users")
def get_admin_users(authorization: str = Header(None)):
    """
    Return all registered users for the admin Users page.
    Passwords are never returned.
    """

    # Check JWT
    admin_id = get_student_id_from_token(authorization)

    # Verify logged-in user is an admin
    admin_user = db.students.find_one({
        "_id": ObjectId(admin_id)
    })

    if not admin_user:
        raise HTTPException(
            status_code=401,
            detail="Admin account not found."
        )

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required."
        )

    # Get all users
    users = db.students.find({}).sort("_id", -1)

    result = []

    for user in users:
        result.append({
            "id": str(user["_id"]),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "role": user.get("role", "student"),
            "department": user.get("department", ""),
            "status": user.get("status", "active"),

            "student_id": user.get("student_id", ""),
            "college_name": user.get("college_name", ""),
            "club_name": user.get("club_name", ""),
            "club_category": user.get("club_category", ""),
            "position": user.get("position", ""),

            "phone": user.get("phone", ""),
            "year": user.get("year", ""),

            "submitted_at": (
                str(user["submitted_at"])
                if user.get("submitted_at")
                else None
            )
        })

    return {
        "status": "success",
        "users": result,
        "total": len(result)
    }

# ============================================================
# ADMIN - APPROVE APPLICATION
# ============================================================

@app.post("/api/admin/applications/{application_id}/approve")
def approve_admin_application(
    application_id: str,
    authorization: str = Header(None)
):

    # --------------------------------------------------------
    # Verify the logged-in user is an admin
    # --------------------------------------------------------

    admin_id = get_student_id_from_token(authorization)

    try:
        admin_object_id = ObjectId(admin_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin user ID"
        )

    admin_user = db.students.find_one({
        "_id": admin_object_id
    })

    if not admin_user:
        raise HTTPException(
            status_code=404,
            detail="Admin user not found"
        )

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can approve applications."
        )

    # --------------------------------------------------------
    # Validate application ID
    # --------------------------------------------------------

    try:
        application_object_id = ObjectId(application_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid application ID"
        )

    # --------------------------------------------------------
    # Find the organization application
    # --------------------------------------------------------

    application = db.students.find_one({
        "_id": application_object_id,
        "role": {"$in": ["club", "faculty", "hod", "dean"]}
    })

    if not application:
        raise HTTPException(
            status_code=404,
            detail="Application not found"
        )

    # Wording that matches the kind of account being approved
    approved_role_label = role_label(application.get("role"))
    approved_who = (
        application.get("name", "applicant")
        if application_kind(application.get("role")) == "faculty"
        else (
            application.get("club_name")
            or application.get("college_name")
            or application.get("name", "organization")
        )
    )

    # --------------------------------------------------------
    # Approve the organization
    # --------------------------------------------------------

    update_result = db.students.update_one(
        {
            "_id": application_object_id,
            "role": {"$in": ["club", "faculty", "hod", "dean"]}
        },
        {
            "$set": {
                "status": "active",
                "approved_at": datetime.now(timezone.utc),
                "approved_by": admin_id
            },
            "$unset": {
                "rejection_reason": "",
                "rejection_note": "",
                "rejected_at": "",
                "rejected_by": ""
            }
        }
    )

    if update_result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Application not found"
        )

    # --------------------------------------------------------
    # Record the approval in Activity Logs
    # --------------------------------------------------------

    create_activity_log(
        user_id=admin_id,
        user_name=admin_user.get("name", "Admin"),
        user_email=admin_user.get("email", ""),
        role="admin",
        action=f"{approved_role_label} Application Approved",
        module="Applications",
        description=(
            f"Administrator approved the {approved_role_label} application for "
            f"{approved_who}."
        ),
        related_id=application_id,
        status="success"
    )

    # --------------------------------------------------------
    # Create an admin notification for the approval action
    # --------------------------------------------------------

    try:
        db.notifications.insert_one({
            "title": f"{approved_role_label} Application Approved",
            "message": f"{approved_who} was approved successfully.",
            "type": "application",
            "recipient_role": "admin",
            "recipient": "admin",
            "related_id": application_id,
            "is_read": False,
            "created_at": datetime.now(timezone.utc)
        })
    except Exception as e:
        # Notification failure must not undo a successful approval.
        print("NOTIFICATION CREATE ERROR:", repr(e))

    # --------------------------------------------------------
    # Return the updated status to the frontend
    # --------------------------------------------------------

    return {
        "status": "success",
        "message": "Application approved successfully.",
        "application_id": application_id,
        "application_status": "active",
        "applicationStatus": "active"
    }


# ============================================================
# ADMIN - REJECT APPLICATION
# ============================================================

@app.post("/api/admin/applications/{application_id}/reject")
def reject_admin_application(
    application_id: str,
    rejection_data: dict,
    authorization: str = Header(None)
):

    # Verify the logged-in user is an admin
    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({"_id": ObjectId(admin_id)})
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin user ID"
        )

    if not admin_user:
        raise HTTPException(
            status_code=404,
            detail="Admin user not found"
        )

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can reject applications."
        )

    # Find the organization application
    try:
        application = db.students.find_one({
            "_id": ObjectId(application_id),
            "role": {"$in": ["club", "faculty", "hod", "dean"]}
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid application ID"
        )

    if not application:
        raise HTTPException(
            status_code=404,
            detail="Application not found"
        )

    # Read rejection details sent by RejectModal
    reason = str(rejection_data.get("reason", "")).strip()
    note = str(rejection_data.get("note", "")).strip()

    if not reason:
        raise HTTPException(
            status_code=400,
            detail="Rejection reason is required."
        )

    # Reject the organization
    db.students.update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {
                "status": "rejected",
                "rejection_reason": reason,
                "rejection_note": note,
                "rejected_at": datetime.now(timezone.utc),
                "rejected_by": admin_id
            }
        }
    )

    return {
        "status": "success",
        "message": "Application rejected successfully.",
        "application_id": application_id,
        "application_status": "rejected",
        "reason": reason,
        "note": note
    }


# ============================================================
# ADMIN - VIEW FULL APPLICATION
# ============================================================

@app.get("/api/admin/applications/{application_id}")
def get_admin_application(
    application_id: str,
    authorization: str = Header(None)
):

    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({"_id": ObjectId(admin_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid admin ID")

    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can view applications."
        )

    try:
        application = db.students.find_one({
            "_id": ObjectId(application_id),
            "role": {"$in": ["club", "faculty", "hod", "dean"]}
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid application ID")

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    role = application.get("role", "club")

    submitted_at = application.get("submitted_at")
    if submitted_at:
        submitted_at = str(submitted_at)
    else:
        try:
            submitted_at = application["_id"].generation_time.strftime("%B %d, %Y")
        except Exception:
            submitted_at = ""

    # Never expose password or MongoDB ObjectId directly.
    raw = dict(application)
    raw.pop("password", None)
    raw.pop("_id", None)
    raw["id"] = str(application["_id"])
    if "submitted_at" in raw:
        raw["submitted_at"] = date_out(raw["submitted_at"])

    kind = application_kind(role)
    college = application.get("college_name", "") or application.get("college", "")

    applicant = {
        "fullName": application.get("name", ""),
        "email": application.get("email", ""),
        "phone": application.get("phone", "") or application.get("mobile", ""),
        "department": application.get("department", ""),
        "college": college,
    }

    if kind == "faculty":
        # Faculty / HOD / Dean: no club, coordinator or student-ID data.
        applicant.update({
            "role": role,
            "designation": application.get("designation", ""),
            "employeeId": application.get("employee_id", ""),
        })
        organization = None
    else:
        applicant.update({
            "studentId": application.get("student_id", ""),
            "year": application.get("year", ""),
        })
        organization = {
            "type": role,
            "name": application.get("club_name", "") or application.get("organization_name", ""),
            "category": application.get("club_category", "") or application.get("category", ""),
            "position": application.get("position", ""),
            "description": application.get("description", ""),
            "coordinatorName": application.get("coordinator_name", ""),
            "coordinatorEmail": application.get("coordinator_email", ""),
            "officialContact": application.get("official_email", ""),
            "facultyCoordinator": application.get("faculty_coordinator_name", ""),
            "facultyCoordinatorContact": application.get("faculty_coordinator_contact", "")
        }

    return {
        "status": "success",
        "id": str(application["_id"]),
        "role": role,
        "kind": kind,
        "applicationStatus": application.get("status", "pending"),
        "submittedAt": date_out(application.get("submitted_at")) or submitted_at,
        "applicant": applicant,
        "organization": organization,
        "raw": raw
    }



# ============================================================
# ACTIVITY LOG HELPER
# ============================================================

def create_activity_log(
    user_id,
    user_name,
    user_email,
    role,
    action,
    module,
    description,
    related_id=None,
    status="success"
):
    """Save an activity log without breaking the main request."""

    try:
        db.activity_logs.insert_one({
            "user_id": str(user_id) if user_id else "",
            "user_name": user_name or "",
            "user_email": user_email or "",
            "role": role or "",
            "action": action or "",
            "module": module or "",
            "description": description or "",
            "related_id": str(related_id) if related_id else None,
            "status": status or "success",
            "created_at": datetime.now(timezone.utc)
        })
    except Exception as e:
        print("ACTIVITY LOG ERROR:", repr(e))


# ============================================================
# ADMIN - ACTIVITY LOGS
# ============================================================

@app.get("/api/admin/activity-logs")
def get_admin_activity_logs(
    authorization: str = Header(None)
):
    """Return real activity logs for administrators."""

    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin user ID"
        )

    if not admin_user:
        raise HTTPException(
            status_code=401,
            detail="Admin account not found."
        )

    if admin_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required."
        )

    logs = db.activity_logs.find({}).sort(
        "created_at",
        -1
    )

    result = []

    for log in logs:
        created_at = log.get("created_at")

        if created_at and hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        elif created_at:
            created_at = str(created_at)
        else:
            created_at = ""

        result.append({
            "id": str(log["_id"]),
            "user_id": str(log.get("user_id", "")),
            "user_name": log.get("user_name", "Unknown User"),
            "user_email": log.get("user_email", ""),
            "role": log.get("role", ""),
            "action": log.get("action", ""),
            "module": log.get("module", ""),
            "description": log.get("description", ""),
            "related_id": (
                str(log.get("related_id"))
                if log.get("related_id")
                else None
            ),
            "status": log.get("status", "success"),
            "created_at": created_at
        })

    return {
        "status": "success",
        "logs": result,
        "total": len(result)
    }


# ============================================================
# ADMIN NOTIFICATIONS
# ============================================================

@app.get("/api/admin/notifications")
def get_admin_notifications(
    authorization: str = Header(None)
):
    admin_id = get_student_id_from_token(authorization)

    try:
        admin = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin ID"
        )

    if not admin:
        raise HTTPException(
            status_code=404,
            detail="Admin account not found"
        )

    if admin.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )

    notifications = list(
        db.notifications.find(
            {
                "$or": [
                    {"recipient_role": "admin"},
                    {"recipient": "admin"},
                    {"recipient": admin_id}
                ]
            }
        ).sort(
            "created_at",
            -1
        )
    )

    result = []

    for notification in notifications:
        result.append({
            "id": str(notification["_id"]),
            "title": notification.get("title", ""),
            "message": notification.get("message", ""),
            "type": notification.get("type", "system"),
            "is_read": bool(
                notification.get("is_read", False)
            ),
            "related_id": notification.get(
                "related_id"
            ),
            "created_at": str(
                notification.get(
                    "created_at",
                    ""
                )
            )
        })

    unread_count = sum(
        1
        for notification in result
        if not notification["is_read"]
    )

    return {
        "status": "success",
        "notifications": result,
        "total": len(result),
        "unread": unread_count
    }


@app.post("/api/admin/notifications/read-all")
def mark_all_notifications_read(
    authorization: str = Header(None)
):
    admin_id = get_student_id_from_token(authorization)

    try:
        admin = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin ID"
        )

    if not admin or admin.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )

    db.notifications.update_many(
        {
            "$or": [
                {"recipient_role": "admin"},
                {"recipient": "admin"},
                {"recipient": admin_id}
            ],
            "is_read": {
                "$ne": True
            }
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.now(timezone.utc)
            }
        }
    )

    return {
        "status": "success",
        "message": "All notifications marked as read."
    }


@app.post("/api/admin/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    authorization: str = Header(None)
):
    admin_id = get_student_id_from_token(authorization)

    try:
        admin = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin ID"
        )

    if not admin or admin.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )

    try:
        object_id = ObjectId(notification_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid notification ID"
        )

    result = db.notifications.update_one(
        {
            "_id": object_id
        },
        {
            "$set": {
                "is_read": True,
                "read_at": datetime.now(timezone.utc)
            }
        }
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Notification not found"
        )

    return {
        "status": "success",
        "message": "Notification marked as read."
    }


@app.delete("/api/admin/notifications/{notification_id}")
def delete_admin_notification(
    notification_id: str,
    authorization: str = Header(None)
):
    admin_id = get_student_id_from_token(authorization)

    try:
        admin = db.students.find_one({
            "_id": ObjectId(admin_id)
        })
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid admin ID"
        )

    if not admin or admin.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )

    try:
        object_id = ObjectId(notification_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid notification ID"
        )

    result = db.notifications.delete_one({
        "_id": object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Notification not found"
        )

    return {
        "status": "success",
        "message": "Notification deleted."
    }
# ============================================================
# RESOURCE PERSONS (speakers / judges / mentors)
# ============================================================

class ResourcePerson(BaseModel):
    name: str = ""
    designation: str = ""
    organization: str = ""
    linkedin_url: str = ""


class EventWithResources(Event):
    # Venue is optional ONLY for Online events (checked in
    # clean_event_details); Event.location itself is left untouched.
    location: Optional[str] = None
    # Organiser / SPOC details. They are optional here so the model still
    # loads, and are enforced by clean_event_details on create and update.
    school_name: Optional[str] = None
    department: Optional[str] = None
    event_mode: Optional[str] = None

    # Estimated budget in rupees. PRIVATE: only Faculty, HODs, Deans, club
    # accounts and Admin may see it; it is never sent to students.
    budget: Optional[float] = None
    # Budget Breakup Form (Presidency University). Validated and re-added up
    # by backend/budget.py; the total sent by the browser is only cross-checked.
    budget_breakup: Optional[dict] = None

    # Event Registration Requirement. True  = students register (existing
    # flow). False = no registration, students just attend. None = not sent:
    # new events default to True, edits leave the saved value alone. StrictBool
    # so "yes", 1 or other look-alikes are rejected with a 422.
    registration_required: Optional[StrictBool] = None
    # Registration deadline (IST). "YYYY-MM-DDTHH:MM" from the form. Only for
    # Registration Required events; see resolve_registration_deadline().
    registration_deadline: Optional[str] = None
    spoc_name: Optional[str] = None
    spoc_designation: Optional[str] = None
    spoc_email: Optional[str] = None
    spoc_contact: Optional[str] = None

    # None = the client did not send the field (leave existing data alone)
    resource_persons: Optional[List[ResourcePerson]] = None


def clean_resource_persons(persons):
    cleaned = []

    for person in (persons or [])[:20]:
        raw = (
            person.model_dump()
            if hasattr(person, "model_dump")
            else person.dict()
        )

        item = {
            key: str(raw.get(key) or "").strip()[:200]
            for key in (
                "name",
                "designation",
                "organization",
                "linkedin_url",
            )
        }

        if not any(item.values()):
            continue

        # Only http(s) links are kept
        if item["linkedin_url"] and not re.match(
            r"^https?://", item["linkedin_url"], re.I
        ):
            item["linkedin_url"] = ""

        cleaned.append(item)

    return cleaned


# ============================================================
# EVENT ORGANISER / SPOC DETAILS
# ============================================================

EVENT_MODES = {"online": "Online", "offline": "Offline", "hybrid": "Hybrid"}

SPOC_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$"
)


BUDGET_MAX = 100_000_000  # rupees (10 crore)

# Who may see an event's budget. Students and signed-out visitors may not.
# ("intercollege" is the old name of the faculty role.)
BUDGET_VIEW_ROLES = ("admin", "hod", "dean", "faculty", "club", "intercollege")


def strip_event_budget(event):
    """Remove the private budget (total AND breakup) before it leaves the server."""
    if isinstance(event, dict):
        event.pop("budget", None)
        event.pop("budget_breakup", None)
        # pending deadline-change request and its history are staff-only too
        event.pop("deadline_change_request", None)
        event.pop("registration_deadline_history", None)
    return event


def budget_viewer(authorization):
    """
    The signed-in Faculty / HOD / Dean / club account / Admin, or None.
    A student, a signed-out visitor, a bad token or a disabled account all
    give None, so they never receive the budget.
    """
    if not authorization:
        return None

    try:
        account_id = get_student_id_from_token(authorization)
        account = db.students.find_one({"_id": ObjectId(account_id)})
    except Exception:
        return None

    if not account:
        return None
    if str(account.get("role", "")).lower() not in BUDGET_VIEW_ROLES:
        return None
    return account


def account_can_see_event_budget(account, event):
    """
    HOD, Dean and Admin see every event's budget. Club Coordinators and
    Faculty see it only for events of their own club.
    """
    if not account:
        return False

    role = str(account.get("role", "")).lower()
    if role in ("hod", "dean", "admin"):
        return True
    if role in ("club", "faculty", "intercollege"):
        return event_belongs_to_club(event, account)
    return False


def viewer_can_see_budget(authorization, event=None):
    """
    Without `event`: is the caller a budget-eligible staff account (used for
    "may see unpublished events"). With `event`: may they see THIS event's
    budget (own club only for Club and Faculty).
    """
    account = budget_viewer(authorization)
    if event is None:
        return account is not None
    return account_can_see_event_budget(account, event)


def event_requires_registration(event):
    """Older events have no field: they require registration, as before."""
    return not (isinstance(event, dict) and event.get("registration_required") is False)


def with_registration_flag(event):
    """Make the flag explicit (True for older events) in an outgoing event."""
    if isinstance(event, dict):
        event["registration_required"] = event_requires_registration(event)
        # True once the deadline has passed (never for events without one)
        event["registration_closed"] = registration_is_closed(event)
    return event


def resolve_registration_required(event, user_role, creating):
    """
    Fields to store for the registration type.
      create, nothing sent -> registration required (True)
      update, nothing sent -> {} (the saved value is kept)
      sent by anyone except Club Coordinator / Faculty -> 403
    """
    value = getattr(event, "registration_required", None)

    if value is None:
        return {"registration_required": True} if creating else {}

    if user_role not in ("club", "faculty"):
        raise HTTPException(
            status_code=403,
            detail="Only Club Coordinators and Faculty can set the registration requirement.",
        )

    return {"registration_required": bool(value)}


def _deadline_error(message):
    return HTTPException(
        status_code=422,
        detail=[{"loc": ["body", "registration_deadline"], "msg": message}],
    )


def resolve_registration_deadline(event, user_role, creating, existing=None):
    """
    Deadline fields to store for a create / update.

      Registration Required (Club / Faculty): the deadline is required on
        create, must be before the event starts and in the future.
      No Registration Required: no deadline (an old one is cleared).
      Update without a deadline in the request: the saved one is kept (but is
        re-checked against the new date/time).
      Published event: the deadline cannot be changed here - it needs an
        Admin-approved change request (409).
      Admin / others sending a deadline: 403. Admin-created events have none.
    """
    raw = getattr(event, "registration_deadline", None)
    can_set = user_role in ("club", "faculty")

    if raw is not None and not can_set:
        raise HTTPException(
            status_code=403,
            detail="Only Club Coordinators and Faculty can set the registration deadline.",
        )
    if not can_set:
        return {}

    sent_required = getattr(event, "registration_required", None)
    if sent_required is None:
        required = True if creating else event_requires_registration(existing)
    else:
        required = bool(sent_required)

    old = stored_deadline(existing) if existing else None

    if not required:
        return {"registration_deadline": None} if old else {}

    text = str(raw).strip() if raw is not None else ""
    was_required = True if creating else event_requires_registration(existing)
    need_new = creating or not was_required  # a deadline must exist from now on

    if not text:
        if raw is not None and old:
            raise _deadline_error("The registration deadline cannot be removed.")
        if need_new:
            raise _deadline_error(
                "Registration deadline is required for events with registration."
            )
        if old:  # keep it, but it must still fit the (possibly new) event time
            problem = deadline_problem(old, event.date, event.time, check_future=False)
            if problem:
                raise _deadline_error(problem)
        return {}

    try:
        deadline = parse_deadline(text)
    except ValueError as problem:
        raise _deadline_error(str(problem))

    unchanged = bool(old) and old == deadline
    if (
        not creating
        and existing
        and existing.get("approval_status") == "published"
        and not unchanged
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This event is published. Use 'Request deadline change' so the "
                "Admin can approve the new deadline."
            ),
        )

    problem = deadline_problem(
        deadline, event.date, event.time, check_future=not unchanged
    )
    if problem:
        raise _deadline_error(problem)

    return {"registration_deadline": to_iso(deadline)}


def resolve_event_budget(event, user_role, creating):
    """
    Budget fields to store for a create / update, decided on the server.

      admin          view only: sending any budget data is refused (403)
      club, faculty  budget_breakup is checked, the total is recalculated
                     here, and a mismatching total is rejected (422)
      update with no budget_breakup -> {} (the saved budget is left alone)
    """
    breakup = getattr(event, "budget_breakup", None)
    legacy_total = getattr(event, "budget", None)

    if user_role not in ("club", "faculty"):
        if breakup is not None or legacy_total is not None:
            raise HTTPException(
                status_code=403,
                detail="Only Club Coordinators and Faculty can enter the event budget.",
            )
        return {}

    if breakup is None:
        if creating:
            raise HTTPException(
                status_code=422,
                detail=[{
                    "loc": ["body", "budget_breakup"],
                    "msg": "The Budget Breakup Form is required.",
                }],
            )
        return {}

    try:
        clean, total = clean_budget_breakup(breakup)
    except BudgetError as problem:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["body"] + problem.path.split("."), "msg": problem.message}],
        )

    # The plain "budget" figure must agree with the breakup if it was sent
    if legacy_total is not None:
        try:
            if round(float(legacy_total), 2) != float(total):
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=[{
                    "loc": ["body", "budget"],
                    "msg": "The budget total does not match the breakup.",
                }],
            )

    return {
        "budget_breakup": clean,
        # legacy single figure kept in step for existing readers/reports
        "budget": clean["total_estimated_budget"],
    }


def clean_event_details(event):
    """
    Validate and normalise School, Department, Event Mode, Venue and the
    SPOC (single point of contact) details.

    Returns a dict ready to store. Problems are raised as a 422 whose
    detail is a list of {"loc": [...], "msg": "..."} items, the same shape
    FastAPI uses, so the form shows them with its existing error handling.
    """
    errors = []

    def text(field):
        # trim and collapse repeated spaces
        return " ".join(str(getattr(event, field, None) or "").split())

    def error(field, message):
        errors.append({"loc": ["body", field], "msg": message})

    def required_text(field, label, max_length=150):
        value = text(field)
        if not value:
            error(field, f"{label} is required.")
        elif len(value) > max_length:
            error(field, f"{label} must be {max_length} characters or fewer.")
        return value

    school_name = required_text("school_name", "School name")
    # Department must be an official one: it decides which HOD reviews the event.
    department_raw = text("department")
    department = canonical_department(department_raw)
    if not department_raw:
        error("department", "Department is required.")
    elif not department:
        error(
            "department",
            "Select the department from the list: "
            + ", ".join(OFFICIAL_DEPARTMENTS) + ".",
        )
    spoc_name = required_text("spoc_name", "SPOC name")
    spoc_designation = required_text("spoc_designation", "SPOC designation")

    # ---- mode of event ----
    mode_raw = text("event_mode")
    event_mode = EVENT_MODES.get(mode_raw.lower())
    if not mode_raw:
        error("event_mode", "Please select the mode of the event.")
    elif not event_mode:
        error("event_mode", "Mode of event must be Online, Offline or Hybrid.")

    # ---- venue: required unless the event is fully online ----
    location = text("location")
    if not location and event_mode != "Online":
        error("location", "Venue is required for Offline and Hybrid events.")
    elif len(location) > 300:
        error("location", "Venue must be 300 characters or fewer.")

    # ---- SPOC email ----
    spoc_email = text("spoc_email").lower()
    if not spoc_email:
        error("spoc_email", "SPOC email address is required.")
    elif len(spoc_email) > 254 or not SPOC_EMAIL_PATTERN.match(spoc_email):
        error("spoc_email", "Enter a valid SPOC email address.")

    # ---- SPOC contact: 10-digit Indian mobile, optional +91 / 91 / 0 ----
    contact_raw = text("spoc_contact")
    spoc_contact = re.sub(r"[\s\-().]", "", contact_raw)
    if spoc_contact.startswith("+91"):
        spoc_contact = spoc_contact[3:]
    elif len(spoc_contact) == 12 and spoc_contact.startswith("91"):
        spoc_contact = spoc_contact[2:]
    elif len(spoc_contact) == 11 and spoc_contact.startswith("0"):
        spoc_contact = spoc_contact[1:]

    if not contact_raw:
        error("spoc_contact", "SPOC contact number is required.")
    elif not re.fullmatch(r"[6-9][0-9]{9}", spoc_contact):
        error(
            "spoc_contact",
            "Enter a valid 10-digit mobile number (starting with 6, 7, 8 or 9).",
        )

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    return {
        "school_name": school_name,
        "department": department,
        "event_mode": event_mode,
        "location": location,
        "spoc_name": spoc_name,
        "spoc_designation": spoc_designation,
        "spoc_email": spoc_email,
        "spoc_contact": spoc_contact,
    }


# ============================================================
# CREATE EVENT
# ============================================================

@app.post("/api/events")
def create_event(
    event: EventWithResources,
    authorization: str = Header(None)
):

    # --------------------------------------------------------
    # Get the logged-in user's ID from the JWT
    # --------------------------------------------------------

    student_id = get_student_id_from_token(
        authorization
    )

    # --------------------------------------------------------
    # Find the logged-in account
    # --------------------------------------------------------

    try:
        user = db.students.find_one(
            {
                "_id": ObjectId(student_id)
            }
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user ID"
        )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # --------------------------------------------------------
    # Check role and account status
    #
    # Students cannot create events.
    # Club / Intercollege accounts must be approved.
    # Admin accounts are allowed.
    # --------------------------------------------------------

    user_role = user.get(
        "role",
        "student"
    )

    user_status = user.get(
        "status",
        "active"
    )

    if user_role == "student":
        raise HTTPException(
            status_code=403,
            detail="Students are not allowed to create events."
        )

    if user_role in ["club", "faculty"] and user_status != "active":
        raise HTTPException(
            status_code=403,
            detail="Your organization account is pending admin approval."
        )

    if user_role not in [
        "club",
        "faculty",
        "admin"
    ]:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to create events."
        )

    # --------------------------------------------------------
    # Create event
    # --------------------------------------------------------

    details = clean_event_details(event)
    budget_fields = resolve_event_budget(event, user_role, creating=True)
    registration_fields = resolve_registration_required(
        event, user_role, creating=True
    )
    deadline_fields = resolve_registration_deadline(
        event, user_role, creating=True
    )

    event_data = {
        **budget_fields,
        **registration_fields,
        **deadline_fields,
        "title": event.title,
        "description": event.description,
        "category": event.category,
        # Club / faculty events always carry their own club name
        # (the club form used to hard-code the organizer).
        "organizer": (
            (user.get("club_name") or event.organizer)
            if user_role in ("club", "faculty")
            else event.organizer
        ),
        "date": event.date,
        "time": event.time,
        "location": details["location"],
        "school_name": details["school_name"],
        "department": details["department"],
        "event_mode": details["event_mode"],
        "spoc_name": details["spoc_name"],
        "spoc_designation": details["spoc_designation"],
        "spoc_email": details["spoc_email"],
        "spoc_contact": details["spoc_contact"],
        "capacity": event.capacity,
        "skills": event.skills,
        "poster_url": clean_poster_url(event.poster_url),
        "resource_persons": clean_resource_persons(
            event.resource_persons
        ),
        "created_by": student_id,
        "created_by_role": user_role,
        "created_at": datetime.now(
            timezone.utc
        
        ),
         
        # Club the event belongs to (shared by coordinator and faculty)
        "club_id": user_club_id(user) if user_role in ("club", "faculty") else "",

        # Approval workflow
        #   admin     -> published
        #   club      -> Faculty review first (if the club has a faculty)
        #   faculty   -> straight to HOD (no self-approval)
        "approval_status": (
            "published" if user_role == "admin"
            else resubmission_status(user_role, user)
        ),
        "submission_version": 1,
        "approval_history": []

        
    }

    result = db.events.insert_one(
        event_data
    )

    # QR: events an Admin publishes directly
    if event_data["approval_status"] == "published":
        qr_hook("on_event_published", str(result.inserted_id))

    if event_data["approval_status"] == "pending_faculty":
        notify_faculty_of_club(
            event_data.get("club_id"),
            user.get("club_name"),
            "New event awaiting your review",
            f'{user.get("name", "A coordinator")} submitted '
            f'"{event.title}" for Faculty review.',
            "review_requested",
            str(result.inserted_id),
        )

    return {
        "status": "success",
        "message": "Event created successfully!",
        "event_id": str(
            result.inserted_id
        )
    }


# ============================================================
# GET ALL EVENTS
# ============================================================


@app.get("/api/events")
def get_events(authorization: str = Header(None)):
    # Published events are visible to students.
    # Events created before this workflow existed are
    # retained as visible legacy events.
    events = list(
        db.events.find(PUBLIC_EVENT_FILTER).sort("_id", -1)
    )
    events = audience_gate.filter_events(events, authorization)

    account = budget_viewer(authorization)

    result = []
    for event in events:
        item = json_safe(event)
        # Students / visitors: never. Club and Faculty: own club only.
        if not account_can_see_event_budget(account, event):
            strip_event_budget(item)
        result.append(item)

    return result

# ============================================================
# REGISTRATION DEADLINE CHANGE (published events)
# Organizer asks (with a reason) -> Admin approves / rejects.
# The original deadline stays active until approval; registrations
# are never touched. Stored on the event itself (no new collection):
#   deadline_change_request      the single pending request
#   registration_deadline_history  requested / approved / rejected log
# ============================================================

class DeadlineChangeBody(BaseModel):
    registration_deadline: str
    reason: str


class DeadlineReviewBody(BaseModel):
    decision: str
    remarks: str = ""


def _deadline_actor(authorization, roles):
    user_id = str(get_student_id_from_token(authorization))
    try:
        user = db.students.find_one({"_id": ObjectId(user_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    role = str(user.get("role", "")).lower()
    if role not in roles:
        raise HTTPException(
            status_code=403, detail="You are not authorized to do this."
        )
    return user_id, user, role


def _deadline_event(event_id):
    try:
        event = db.events.find_one({"_id": ObjectId(event_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid event ID")
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _deadline_label(value):
    parsed = stored_deadline({"registration_deadline": value})
    return parsed.strftime("%d %b %Y, %I:%M %p IST") if parsed else "not set"


@app.post("/api/events/{event_id}/deadline-change")
def request_deadline_change(
    event_id: str,
    body: DeadlineChangeBody,
    authorization: str = Header(None),
):
    """Club Coordinator / Faculty of the event's club ask for a new deadline."""
    user_id, user, role = _deadline_actor(authorization, ("club", "faculty"))

    if user.get("status", "active") != "active":
        raise HTTPException(
            status_code=403,
            detail="Your organization account is pending admin approval.",
        )

    event = _deadline_event(event_id)

    if not (
        str(event.get("created_by")) == user_id
        or event_belongs_to_club(event, user)
    ):
        raise HTTPException(
            status_code=403,
            detail="You can only change the deadline of your own club's events.",
        )

    if not is_event_public(event):
        raise HTTPException(
            status_code=409,
            detail=(
                "Only published events need an Admin-approved deadline change. "
                "Edit the event directly instead."
            ),
        )

    if not event_requires_registration(event):
        raise HTTPException(
            status_code=400,
            detail="This event does not require registration, so it has no deadline.",
        )

    if (event.get("deadline_change_request") or {}).get("status") == "pending":
        raise HTTPException(
            status_code=409,
            detail="A deadline change request is already waiting for Admin approval.",
        )

    reason = " ".join(str(body.reason or "").split())
    if len(reason) < 10:
        raise HTTPException(
            status_code=422,
            detail=[{
                "loc": ["body", "reason"],
                "msg": "Please give a reason (at least 10 characters).",
            }],
        )
    if len(reason) > 500:
        raise HTTPException(
            status_code=422,
            detail=[{
                "loc": ["body", "reason"],
                "msg": "The reason must be 500 characters or fewer.",
            }],
        )

    try:
        new_deadline = parse_deadline(body.registration_deadline)
    except ValueError as problem:
        raise _deadline_error(str(problem))

    problem = deadline_problem(new_deadline, event.get("date"), event.get("time"))
    if problem:
        raise _deadline_error(problem)

    old_deadline = stored_deadline(event)
    if old_deadline and old_deadline == new_deadline:
        raise _deadline_error("That is already the current deadline.")

    now_text = datetime.now(timezone.utc).isoformat()
    request_doc = {
        "status": "pending",
        "new_deadline": to_iso(new_deadline),
        "old_deadline": to_iso(old_deadline) if old_deadline else None,
        "reason": reason,
        "requested_by": user_id,
        "requested_by_name": user.get("name", ""),
        "requested_by_role": role,
        "requested_at": now_text,
    }
    history_entry = {
        "action": "requested",
        "by": user_id,
        "by_name": user.get("name", ""),
        "by_role": role,
        "old_deadline": request_doc["old_deadline"],
        "new_deadline": request_doc["new_deadline"],
        "reason": reason,
        "at": now_text,
    }

    result = db.events.update_one(
        {
            "_id": event["_id"],
            "deadline_change_request.status": {"$ne": "pending"},
        },
        {
            "$set": {"deadline_change_request": request_doc},
            "$push": {"registration_deadline_history": history_entry},
        },
    )
    if result.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail="A deadline change request is already waiting for Admin approval.",
        )

    try:
        db.notifications.insert_one({
            "title": "Registration deadline change requested",
            "message": (
                f'{user.get("name", "An organizer")} asked to change the deadline '
                f'of "{event.get("title", "an event")}" to '
                f'{_deadline_label(request_doc["new_deadline"])}.'
            ),
            "type": "deadline_change",
            "recipient_role": "admin",
            "recipient": "admin",
            "related_id": event_id,
            "is_read": False,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        print("DEADLINE REQUEST NOTIFICATION ERROR:", repr(e))

    return {
        "status": "success",
        "message": "Deadline change sent to the Admin for approval.",
        "event_id": event_id,
        "deadline_change_request": request_doc,
    }


@app.get("/api/admin/deadline-requests")
def admin_deadline_requests(authorization: str = Header(None)):
    """Pending deadline change requests for the Admin."""
    _deadline_actor(authorization, ("admin",))

    rows = []
    for event in db.events.find(
        {"deadline_change_request.status": "pending"}
    ).sort("_id", -1):
        request_doc = event.get("deadline_change_request") or {}
        rows.append({
            "event_id": str(event["_id"]),
            "title": event.get("title", ""),
            "organizer": event.get("organizer", ""),
            "date": event.get("date", ""),
            "time": event.get("time", ""),
            "current_deadline": event.get("registration_deadline"),
            "registered": db.event_registrations.count_documents({
                "event_id": str(event["_id"]),
                "status": "registered",
            }),
            **{
                key: request_doc.get(key)
                for key in (
                    "new_deadline", "reason", "requested_by_name",
                    "requested_by_role", "requested_at",
                )
            },
        })

    return {"status": "success", "requests": rows}


@app.post("/api/admin/deadline-requests/{event_id}/review")
def review_deadline_request(
    event_id: str,
    body: DeadlineReviewBody,
    authorization: str = Header(None),
):
    """Admin approves or rejects a pending deadline change."""
    admin_id, admin, _ = _deadline_actor(authorization, ("admin",))

    decision = str(body.decision or "").strip().lower()
    remarks = " ".join(str(body.remarks or "").split())
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="Decision must be approved or rejected."
        )
    if decision == "rejected" and len(remarks) < 10:
        raise HTTPException(
            status_code=400,
            detail="Please give at least 10 characters of remarks when rejecting.",
        )

    event = _deadline_event(event_id)
    request_doc = event.get("deadline_change_request") or {}
    if request_doc.get("status") != "pending":
        raise HTTPException(
            status_code=409, detail="There is no pending deadline change for this event."
        )

    new_deadline = stored_deadline({"registration_deadline": request_doc.get("new_deadline")})
    if decision == "approved":
        problem = (
            deadline_problem(new_deadline, event.get("date"), event.get("time"))
            if new_deadline else "The requested deadline is not valid."
        )
        if problem:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{problem} Ask the organizer to request a new deadline "
                    "and reject this one."
                ),
            )

    now_text = datetime.now(timezone.utc).isoformat()
    history_entry = {
        "action": decision,
        "by": admin_id,
        "by_name": admin.get("name", ""),
        "by_role": "admin",
        "old_deadline": request_doc.get("old_deadline"),
        "new_deadline": request_doc.get("new_deadline"),
        "reason": request_doc.get("reason", ""),
        "remarks": remarks,
        "at": now_text,
    }

    changes = {"$unset": {"deadline_change_request": ""},
               "$push": {"registration_deadline_history": history_entry}}
    if decision == "approved":
        changes["$set"] = {"registration_deadline": request_doc["new_deadline"]}

    result = db.events.update_one(
        {"_id": event["_id"], "deadline_change_request.status": "pending"},
        changes,
    )
    if result.matched_count != 1:
        raise HTTPException(
            status_code=409, detail="This request was already reviewed."
        )

    title = event.get("title", "an event")
    new_label = _deadline_label(request_doc.get("new_deadline"))
    notified = 0

    try:
        # The organizer who asked
        requester_id = request_doc.get("requested_by")
        if requester_id:
            organizer_note = {
                "title": (
                    "Deadline change approved" if decision == "approved"
                    else "Deadline change rejected"
                ),
                "message": (
                    f'The Admin approved the new registration deadline '
                    f'({new_label}) for "{title}".'
                    if decision == "approved"
                    else f'The Admin rejected the deadline change for "{title}". '
                         f'Remarks: {remarks}'
                ),
                "type": "event_update",
                "recipient": str(requester_id),
                "related_id": event_id,
                "remarks": remarks,
                "reviewer_role": "admin",
                "is_read": False,
                "created_at": datetime.now(timezone.utc),
            }
            if request_doc.get("requested_by_role") == "faculty":
                organizer_note["audience"] = "faculty"
            db.notifications.insert_one(organizer_note)

        # Affected students: everyone registered for the event
        if decision == "approved":
            student_ids = sorted({
                str(row.get("student_id"))
                for row in db.event_registrations.find(
                    {"event_id": event_id, "status": "registered"},
                    {"student_id": 1},
                )
                if row.get("student_id")
            })
            if student_ids:
                created = datetime.now(timezone.utc)
                db.notifications.insert_many([
                    {
                        "title": "Registration deadline changed",
                        "message": (
                            f'The registration deadline for "{title}" is now '
                            f'{new_label}. Your registration is unchanged.'
                        ),
                        "type": "deadline_changed",
                        "audience": "student",
                        "recipient": student_id,
                        "related_id": event_id,
                        "is_read": False,
                        "created_at": created,
                    }
                    for student_id in student_ids
                ])
                notified = len(student_ids)
    except Exception as e:
        # A notification problem must not undo the Admin's decision.
        print("DEADLINE REVIEW NOTIFICATION ERROR:", repr(e))

    return {
        "status": "success",
        "decision": decision,
        "event_id": event_id,
        "registration_deadline": (
            request_doc.get("new_deadline") if decision == "approved"
            else event.get("registration_deadline")
        ),
        "students_notified": notified,
    }


@app.get("/api/student/notifications")
def student_notifications(authorization: str = Header(None)):
    """Notifications addressed to the logged-in student (e.g. deadline changes)."""
    student_id = str(get_student_id_from_token(authorization))
    rows = list(
        db.notifications.find(
            {"recipient": student_id, "audience": "student"}
        ).sort("created_at", -1).limit(50)
    )
    return {
        "status": "success",
        "notifications": [
            {
                "id": str(n["_id"]),
                "title": n.get("title", ""),
                "message": n.get("message", ""),
                "type": n.get("type", ""),
                "is_read": bool(n.get("is_read", False)),
                "event_id": n.get("related_id"),
                "created_at": (
                    n["created_at"].isoformat()
                    if hasattr(n.get("created_at"), "isoformat")
                    else str(n.get("created_at", ""))
                ),
            }
            for n in rows
        ],
    }



# ============================================================
# EVENT CHECKLIST (published events only)
# Club Coordinator + Faculty (own club): view and edit.
# HOD (own department) + Dean + Admin: view only. Students: no access.
# One document per event in db.event_checklists:
#   {event_id, items: {activity_key: {status, challenges, updated_*}},
#    history: [{item, label, from, to, by, role, at}]}
# ============================================================

class ChecklistItemBody(BaseModel):
    status: str
    challenges: str = ""


def _checklist_access(authorization, event_id):
    """(user_id, user, role, event, can_edit) or the proper HTTP error."""
    user_id, user, role = _deadline_actor(
        authorization, ("club", "faculty", "hod", "dean", "admin")
    )
    event = _deadline_event(event_id)

    if role in ("club", "faculty"):
        if user.get("status", "active") != "active":
            raise HTTPException(
                status_code=403,
                detail="Your organization account is pending admin approval.",
            )
        if not (
            str(event.get("created_by")) == user_id
            or event_belongs_to_club(event, user)
        ):
            raise HTTPException(
                status_code=403,
                detail="You can only open the checklist of your own club's events.",
            )
    elif role == "hod":
        reason = hod_review_block_reason(user, event)
        if reason:
            raise HTTPException(status_code=403, detail=reason)

    if not is_event_public(event):
        raise HTTPException(
            status_code=409,
            detail="The checklist is available only after the event is published.",
        )

    return user_id, user, role, event, role in ("club", "faculty")


@app.get("/api/events/{event_id}/checklist")
def get_event_checklist(event_id: str, authorization: str = Header(None)):
    _, _, role, event, can_edit = _checklist_access(authorization, event_id)

    doc = db.event_checklists.find_one({"event_id": event_id}) or {}
    activities, history = checklist_view(doc)

    return {
        "status": "success",
        "event_id": event_id,
        "title": event.get("title", ""),
        "role": role,
        "can_edit": can_edit,
        "activities": activities,
        "history": history,
    }


@app.put("/api/events/{event_id}/checklist/{item_key}")
def update_checklist_item(
    event_id: str,
    item_key: str,
    body: ChecklistItemBody,
    authorization: str = Header(None),
):
    user_id, user, role, event, can_edit = _checklist_access(
        authorization, event_id
    )
    if not can_edit:
        raise HTTPException(
            status_code=403,
            detail="Only the Club Coordinator and Faculty can edit the checklist.",
        )

    try:
        status, challenges = checklist_clean(item_key, body.status, body.challenges)
    except ChecklistError as problem:
        raise HTTPException(status_code=422, detail=str(problem))

    existing = db.event_checklists.find_one({"event_id": event_id}) or {}
    previous = ((existing.get("items") or {}).get(item_key) or {}).get("status")
    previous = previous or checklist_default(item_key)

    now_text = datetime.now(timezone.utc).isoformat()
    item = {
        "status": status,
        "challenges": challenges,
        "updated_at": now_text,
        "updated_by": user_id,
        "updated_by_name": user.get("name", ""),
    }
    changes = {"$set": {f"items.{item_key}": item, "updated_at": now_text}}
    if previous != status:
        label = dict(CHECKLIST_ACTIVITIES)[item_key]
        changes["$push"] = {"history": {
            "item": item_key,
            "label": label,
            "from": previous,
            "to": status,
            "by": user_id,
            "by_name": user.get("name", ""),
            "role": role,
            "at": now_text,
        }}

    db.event_checklists.update_one({"event_id": event_id}, changes, upsert=True)

    return {
        "status": "success",
        "event_id": event_id,
        "item": {"key": item_key, **item},
        "status_changed": previous != status,
    }


# ============================================================
# POST-EVENT DETAILS / REPORT  (published events only)
# Organizers (Club Coordinator / Faculty of the event's club): draft,
# upload, replace/remove files, preview, submit and resubmit.
# Review chain: Faculty -> HOD -> Dean -> Admin (a Faculty submission
# starts at HOD, like events). Reject returns it to the organizers.
# One document per event in db.event_reports; file bytes live in a PRIVATE
# folder (never mounted as static) and are served only through the
# permission-checked download endpoint below.
#   {event_id, status, main_report, attachments (working set),
#    submissions: [{version, files, decisions}], history: [...]}
# ============================================================

POST_REPORT_DIR = Path(__file__).resolve().parent / "uploads" / "event_reports"
POST_REPORT_DIR.mkdir(parents=True, exist_ok=True)

REPORT_REVIEW_STATUS = {
    "faculty": "pending_faculty",
    "hod": "pending_hod",
    "dean": "pending_dean",
    "admin": "pending_admin",
}
REPORT_NEXT_STATUS = {
    "pending_faculty": "pending_hod",
    "pending_hod": "pending_dean",
    "pending_dean": "pending_admin",
    "pending_admin": "approved",
}
REPORT_EDITABLE = (None, "draft", "rejected")
_REPORT_FILE_KEYS = (
    "file_id", "name", "size", "ext", "content_type",
    "uploaded_at", "uploaded_by_name",
)


report_previewable = report_files.PREVIEWABLE
report_limits = {
    "main": report_files.MAX_MAIN_BYTES,
    "attachment": report_files.MAX_ATTACHMENT_BYTES,
    "total": report_files.MAX_TOTAL_BYTES,
    "count": report_files.MAX_ATTACHMENTS,
}


class ReportReviewBody(BaseModel):
    decision: str
    remarks: str = ""


def _report_public_file(meta):
    if not meta:
        return None
    out = {key: meta.get(key) for key in _REPORT_FILE_KEYS}
    out["previewable"] = str(meta.get("ext", "")).lower() in report_previewable
    return out


_REPORT_STAGE_RANK = {"faculty": 1, "hod": 2, "dean": 3, "admin": 4}


def _report_reviewer_can_see(role, report):
    """
    A report stays with the organizer and the Faculty until the Faculty approves
    it; then it becomes visible to the HOD, and so on up the chain. A reviewer
    who already decided on it (e.g. a rejection) keeps seeing it.
    """
    submissions = (report or {}).get("submissions") or []
    if not submissions:
        return False
    status = (report or {}).get("status")
    if status == "approved":
        return True
    if status in REPORT_NEXT_STATUS:  # pending_*: visible from its own stage upward
        stage_role = status.replace("pending_", "")
        return _REPORT_STAGE_RANK.get(role, 0) <= _REPORT_STAGE_RANK.get(stage_role, 0)
    decisions = submissions[-1].get("decisions") or []
    return any(d.get("role") == role for d in decisions)


def _report_context(authorization, event_id):
    """Who is asking, for which event, and what they may do with its report."""
    user_id, user, role = _deadline_actor(
        authorization, ("club", "faculty", "hod", "dean", "admin")
    )
    event = _deadline_event(event_id)

    if not is_event_public(event):
        raise HTTPException(
            status_code=409,
            detail="The post-event report is available only after the event is published.",
        )

    organizer = False
    if role in ("club", "faculty"):
        if user.get("status", "active") != "active":
            raise HTTPException(
                status_code=403,
                detail="Your organization account is pending admin approval.",
            )
        organizer = (
            str(event.get("created_by")) == user_id
            or event_belongs_to_club(event, user)
        )
        if not organizer:
            raise HTTPException(
                status_code=403,
                detail="You can only open the report of your own club's events.",
            )
    elif role == "hod":
        reason = hod_review_block_reason(user, event)
        if reason:
            raise HTTPException(status_code=403, detail=reason)

    report = db.event_reports.find_one({"event_id": event_id})
    status = (report or {}).get("status")

    if not organizer and not _report_reviewer_can_see(role, report):
        raise HTTPException(
            status_code=404, detail="No report has been submitted for this event yet."
        )

    return {
        "user_id": user_id,
        "user": user,
        "role": role,
        "event": event,
        "event_id": event_id,
        "report": report,
        "status": status,
        "organizer": organizer,
        "can_edit": organizer and status in REPORT_EDITABLE,
        "can_review": (
            role in REPORT_REVIEW_STATUS
            and status == REPORT_REVIEW_STATUS[role]
            and not (role == "faculty" and not organizer)
        ),
    }


def _report_require_edit(ctx):
    if not ctx["organizer"]:
        raise HTTPException(
            status_code=403,
            detail="Only the Club Coordinator and Faculty can edit the report.",
        )
    if not ctx["can_edit"]:
        raise HTTPException(
            status_code=409,
            detail="This report is under review or approved, so it cannot be changed.",
        )


def _report_view(ctx):
    report = ctx["report"] or {}
    submissions = report.get("submissions") or []

    if ctx["organizer"]:
        main = report.get("main_report")
        attachments = report.get("attachments") or []
    else:  # reviewers always see the submitted version, never a working draft
        latest = submissions[-1] if submissions else {}
        main = latest.get("main_report")
        attachments = latest.get("attachments") or []

    return {
        "status": "success",
        "event_id": ctx["event_id"],
        "title": ctx["event"].get("title", ""),
        "report_status": ctx["status"] or "not_started",
        "role": ctx["role"],
        "is_organizer": ctx["organizer"],
        "can_edit": ctx["organizer"] and ctx["status"] in REPORT_EDITABLE,
        "can_review": (
            ctx["role"] in REPORT_REVIEW_STATUS
            and ctx["status"] == REPORT_REVIEW_STATUS[ctx["role"]]
            and not (ctx["role"] == "faculty" and not ctx["organizer"])
        ),
        "main_report": _report_public_file(main),
        "attachments": [_report_public_file(item) for item in attachments],
        "limits": {
            "main_mb": report_limits["main"] // (1024 * 1024),
            "attachment_mb": report_limits["attachment"] // (1024 * 1024),
            "total_mb": report_limits["total"] // (1024 * 1024),
            "max_attachments": report_limits["count"],
        },
        "submissions": [
            {
                "version": item.get("version"),
                "submitted_at": item.get("submitted_at"),
                "submitted_by_name": item.get("submitted_by_name", ""),
                "submitted_by_role": item.get("submitted_by_role", ""),
                "main_report": _report_public_file(item.get("main_report")),
                "attachments": [
                    _report_public_file(f) for f in item.get("attachments") or []
                ],
                "decisions": item.get("decisions") or [],
            }
            for item in submissions
        ],
        "history": list(reversed((report.get("history") or [])[-100:])),
    }


def _report_store_file(ctx, data, info):
    """Write the bytes to the private folder and return the metadata record."""
    file_id = secrets.token_hex(16)
    stored_name = file_id + info["ext"]
    folder = POST_REPORT_DIR / ctx["event_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / stored_name).write_bytes(data)
    return {
        "file_id": file_id,
        "stored_name": stored_name,
        "name": info["name"],
        "size": info["size"],
        "ext": info["ext"],
        "content_type": info["content_type"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by": ctx["user_id"],
        "uploaded_by_name": ctx["user"].get("name", ""),
    }


def _report_discard_file(event_id, meta, report_after):
    """Delete the bytes only if no submission and no working file still uses them."""
    if not meta:
        return
    file_id = meta.get("file_id")
    for item in report_after.get("submissions") or []:
        used = [item.get("main_report")] + list(item.get("attachments") or [])
        if any(f and f.get("file_id") == file_id for f in used):
            return
    working = [report_after.get("main_report")] + list(report_after.get("attachments") or [])
    if any(f and f.get("file_id") == file_id for f in working):
        return
    try:
        (POST_REPORT_DIR / event_id / meta["stored_name"]).unlink()
    except OSError:
        pass


def _report_read_upload(file, main):
    limit = report_limits["main"] if main else report_limits["attachment"]
    data = file.file.read(limit + 1)
    try:
        info = report_validate(file.filename, data, main)
    except ReportFileError as problem:
        raise HTTPException(status_code=problem.status, detail=str(problem))
    return data, info


def _report_total_size(report, extra, skip_main=False):
    total = extra
    main = (report or {}).get("main_report")
    if main and not skip_main:
        total += int(main.get("size", 0))
    for item in (report or {}).get("attachments") or []:
        total += int(item.get("size", 0))
    if total > report_limits["total"]:
        raise HTTPException(
            status_code=413,
            detail=(
                "The report files together are too large "
                f"(maximum {report_limits['total'] // (1024 * 1024)} MB)."
            ),
        )


def _report_notify_next(ctx, next_status, version):
    """Tell the next reviewer that a report is waiting (never blocks the action)."""
    try:
        event = ctx["event"]
        title = event.get("title", "an event")
        message = f'The post-event report for "{title}" (version {version}) is waiting for your review.'
        if next_status == "pending_faculty":
            notify_faculty_of_club(
                event.get("club_id"),
                event.get("club_name") or event.get("organizer"),
                "Post-event report awaiting your review",
                message,
                "report_review",
                ctx["event_id"],
            )
            return
        role = next_status.replace("pending_", "")
        if role not in ("hod", "dean", "admin"):
            return
        db.notifications.insert_one({
            "title": "Post-event report awaiting your review",
            "message": message,
            "type": "report_review",
            "recipient_role": role,
            "recipient": role,
            "department": event.get("department"),
            "related_id": ctx["event_id"],
            "is_read": False,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        print("REPORT NEXT-REVIEWER NOTIFICATION ERROR:", repr(e))


@app.get("/api/events/{event_id}/report")
def get_event_report(event_id: str, authorization: str = Header(None)):
    return _report_view(_report_context(authorization, event_id))


@app.put("/api/events/{event_id}/report/main")
def upload_report_main(
    event_id: str,
    file: UploadFile = File(...),
    authorization: str = Header(None),
):
    """The single mandatory main report (PDF / DOC / DOCX). Replaces any earlier one."""
    ctx = _report_context(authorization, event_id)
    _report_require_edit(ctx)

    data, info = _report_read_upload(file, main=True)
    report = ctx["report"] or {}
    _report_total_size(report, len(data), skip_main=True)

    old_main = report.get("main_report")
    meta = _report_store_file(ctx, data, info)
    now_text = datetime.now(timezone.utc).isoformat()
    db.event_reports.update_one(
        {"event_id": event_id},
        {
            "$set": {
                "main_report": meta,
                "status": ctx["status"] or "draft",
                "updated_at": now_text,
            },
            "$setOnInsert": {
                "created_at": now_text,
                "created_by": ctx["user_id"],
                "submissions": [],
            },
        },
        upsert=True,
    )

    after = db.event_reports.find_one({"event_id": event_id}) or {}
    _report_discard_file(event_id, old_main, after)
    return _report_view({**ctx, "report": after, "status": after.get("status")})


@app.post("/api/events/{event_id}/report/attachments")
def upload_report_attachment(
    event_id: str,
    file: UploadFile = File(...),
    authorization: str = Header(None),
):
    """One optional attachment (photo or supporting document)."""
    ctx = _report_context(authorization, event_id)
    _report_require_edit(ctx)

    report = ctx["report"] or {}
    if len(report.get("attachments") or []) >= report_limits["count"]:
        raise HTTPException(
            status_code=409,
            detail=f"A report can have at most {report_limits['count']} attachments.",
        )

    data, info = _report_read_upload(file, main=False)
    _report_total_size(report, len(data))

    meta = _report_store_file(ctx, data, info)
    now_text = datetime.now(timezone.utc).isoformat()
    db.event_reports.update_one(
        {"event_id": event_id},
        {
            "$push": {"attachments": meta},
            "$set": {"status": ctx["status"] or "draft", "updated_at": now_text},
            "$setOnInsert": {
                "created_at": now_text,
                "created_by": ctx["user_id"],
                "submissions": [],
            },
        },
        upsert=True,
    )

    after = db.event_reports.find_one({"event_id": event_id}) or {}
    return _report_view({**ctx, "report": after, "status": after.get("status")})


@app.delete("/api/events/{event_id}/report/files/{file_id}")
def remove_report_file(
    event_id: str, file_id: str, authorization: str = Header(None)
):
    """Remove a file from the report before submission (earlier submissions keep theirs)."""
    ctx = _report_context(authorization, event_id)
    _report_require_edit(ctx)

    report = ctx["report"] or {}
    main = report.get("main_report")
    attachments = list(report.get("attachments") or [])

    changes = {"updated_at": datetime.now(timezone.utc).isoformat()}
    removed = None
    if main and main.get("file_id") == file_id:
        removed, changes["main_report"] = main, None
    else:
        kept = [item for item in attachments if item.get("file_id") != file_id]
        if len(kept) == len(attachments):
            raise HTTPException(status_code=404, detail="File not found in this report.")
        removed = next(item for item in attachments if item.get("file_id") == file_id)
        changes["attachments"] = kept

    db.event_reports.update_one({"event_id": event_id}, {"$set": changes})
    after = db.event_reports.find_one({"event_id": event_id}) or {}
    _report_discard_file(event_id, removed, after)
    return _report_view({**ctx, "report": after, "status": after.get("status")})


@app.post("/api/events/{event_id}/report/submit")
def submit_event_report(event_id: str, authorization: str = Header(None)):
    """Submit (or resubmit after a rejection) for review."""
    ctx = _report_context(authorization, event_id)
    _report_require_edit(ctx)

    report = ctx["report"] or {}
    if not report.get("main_report"):
        raise HTTPException(
            status_code=422,
            detail="The main report (PDF, DOC or DOCX) is required before submitting.",
        )

    submissions = list(report.get("submissions") or [])
    version = len(submissions) + 1
    now_text = datetime.now(timezone.utc).isoformat()
    submissions.append({
        "version": version,
        "submitted_at": now_text,
        "submitted_by": ctx["user_id"],
        "submitted_by_name": ctx["user"].get("name", ""),
        "submitted_by_role": ctx["role"],
        "main_report": report["main_report"],
        "attachments": list(report.get("attachments") or []),
        "decisions": [],
    })

    next_status = "pending_hod" if ctx["role"] == "faculty" else "pending_faculty"
    result = db.event_reports.update_one(
        {"event_id": event_id, "status": report.get("status")},
        {
            "$set": {
                "status": next_status,
                "submissions": submissions,
                "updated_at": now_text,
            },
            "$push": {"history": {
                "action": "submitted" if version == 1 else "resubmitted",
                "version": version,
                "by": ctx["user_id"],
                "by_name": ctx["user"].get("name", ""),
                "role": ctx["role"],
                "at": now_text,
            }},
        },
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="The report was changed. Please reload.")

    _report_notify_next(ctx, next_status, version)

    after = db.event_reports.find_one({"event_id": event_id}) or {}
    return _report_view({**ctx, "report": after, "status": after.get("status")})


@app.post("/api/events/{event_id}/report/review")
def review_event_report(
    event_id: str,
    body: ReportReviewBody,
    authorization: str = Header(None),
):
    """Faculty / HOD / Dean / Admin approve or reject the report at their stage."""
    ctx = _report_context(authorization, event_id)

    decision = str(body.decision or "").strip().lower()
    remarks = " ".join(str(body.remarks or "").split())
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be approved or rejected.")
    if decision == "rejected" and len(remarks) < 10:
        raise HTTPException(
            status_code=400,
            detail="Please give at least 10 characters of remarks when rejecting.",
        )
    if not ctx["can_review"]:
        raise HTTPException(status_code=409, detail="This report is not awaiting your review.")

    report = ctx["report"]
    submissions = list(report.get("submissions") or [])
    latest = dict(submissions[-1])
    now_text = datetime.now(timezone.utc).isoformat()
    stage = ctx["status"]
    latest["decisions"] = list(latest.get("decisions") or []) + [{
        "stage": stage,
        "role": ctx["role"],
        "decision": decision,
        "remarks": remarks,
        "by": ctx["user_id"],
        "by_name": ctx["user"].get("name", ""),
        "at": now_text,
    }]
    submissions[-1] = latest
    next_status = REPORT_NEXT_STATUS[stage] if decision == "approved" else "rejected"

    result = db.event_reports.update_one(
        {"event_id": event_id, "status": stage},
        {
            "$set": {"status": next_status, "submissions": submissions, "updated_at": now_text},
            "$push": {"history": {
                "action": decision,
                "version": latest.get("version"),
                "by": ctx["user_id"],
                "by_name": ctx["user"].get("name", ""),
                "role": ctx["role"],
                "remarks": remarks,
                "at": now_text,
            }},
        },
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="This report was already reviewed.")

    # Approved at this stage: tell the next reviewer
    if decision == "approved" and next_status != "approved":
        _report_notify_next(ctx, next_status, latest.get("version"))

    # Tell the organizer when it is returned or finally approved
    if decision == "rejected" or next_status == "approved":
        try:
            note = {
                "title": "Post-event report returned" if decision == "rejected"
                         else "Post-event report approved",
                "message": (
                    f'The {ctx["role"].upper()} returned the report for '
                    f'"{ctx["event"].get("title", "")}". Remarks: {remarks}'
                    if decision == "rejected"
                    else f'The post-event report for "{ctx["event"].get("title", "")}" is approved.'
                ),
                "type": "event_update",
                "recipient": str(latest.get("submitted_by")),
                "related_id": event_id,
                "remarks": remarks,
                "reviewer_role": ctx["role"],
                "is_read": False,
                "created_at": datetime.now(timezone.utc),
            }
            if latest.get("submitted_by_role") == "faculty":
                note["audience"] = "faculty"
            db.notifications.insert_one(note)
        except Exception as e:
            print("REPORT REVIEW NOTIFICATION ERROR:", repr(e))

    after = db.event_reports.find_one({"event_id": event_id}) or {}
    return _report_view({**ctx, "report": after, "status": after.get("status"),
                         "can_review": False, "can_edit": False})


@app.get("/api/reports/pending")
def pending_event_reports(authorization: str = Header(None)):
    """Reports waiting for the logged-in Faculty / HOD / Dean / Admin."""
    _, user, role = _deadline_actor(authorization, ("faculty", "hod", "dean", "admin"))
    rows = []
    for report in db.event_reports.find({"status": REPORT_REVIEW_STATUS[role]}):
        try:
            event = db.events.find_one({"_id": ObjectId(report["event_id"])})
        except Exception:
            event = None
        if not event:
            continue
        if role == "faculty" and not event_belongs_to_club(event, user):
            continue
        if role == "hod" and hod_review_block_reason(user, event):
            continue
        latest = (report.get("submissions") or [{}])[-1]
        rows.append({
            "event_id": report["event_id"],
            "title": event.get("title", ""),
            "organizer": event.get("organizer", ""),
            "version": latest.get("version"),
            "submitted_at": latest.get("submitted_at"),
            "submitted_by_name": latest.get("submitted_by_name", ""),
            "main_report_name": (latest.get("main_report") or {}).get("name", ""),
        })
    return {"status": "success", "reports": rows}


@app.get("/api/events/{event_id}/report/files/{file_id}")
def download_report_file(
    event_id: str, file_id: str, authorization: str = Header(None)
):
    """Permission-checked file download / preview. Files are never public."""
    ctx = _report_context(authorization, event_id)
    report = ctx["report"] or {}

    pool = []
    for item in report.get("submissions") or []:
        pool += [item.get("main_report")] + list(item.get("attachments") or [])
    if ctx["organizer"]:  # organizers also see their not-yet-submitted files
        pool += [report.get("main_report")] + list(report.get("attachments") or [])

    meta = next((f for f in pool if f and f.get("file_id") == file_id), None)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found.")

    folder = (POST_REPORT_DIR / event_id).resolve()
    path = (folder / meta["stored_name"]).resolve()
    if folder not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    inline = str(meta.get("ext", "")).lower() in report_previewable
    from urllib.parse import quote
    disposition = (
        f"{'inline' if inline else 'attachment'}; "
        f"filename*=UTF-8''{quote(meta.get('name', 'file'))}"
    )
    return FileResponse(
        str(path),
        media_type=meta.get("content_type", "application/octet-stream"),
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


# -----------------------------
# Student Event Registration
# -----------------------------

@app.post("/api/events/{event_id}/register")
def register_for_event(
    event_id: str,
    authorization: str = Header(None)
):
    # Get logged-in student from JWT
    student_id = get_student_id_from_token(
        authorization
    )

    # Validate MongoDB event ID
    try:
        event_object_id = ObjectId(event_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    # Check whether event exists
    event = db.events.find_one(
        {"_id": event_object_id}
    )

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # Students may only register for published events.
    if not is_event_public(event):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # ...and only if they are in the event's publishing audience.
    audience_gate.require_student_access(event, student_id)

    # No-registration events never create a registration record.
    if not event_requires_registration(event):
        raise HTTPException(
            status_code=400,
            detail="Registration is not required for this event.",
        )

    # Registration closes automatically when the deadline (IST) passes.
    if registration_is_closed(event):
        raise HTTPException(
            status_code=400,
            detail="Registration is closed. The registration deadline has passed.",
        )

    # Check existing active registration
    existing_registration = db.event_registrations.find_one({
        "student_id": student_id,
        "event_id": event_id,
        "status": "registered"
    })

    if existing_registration:
        return {
            "status": "already_registered",
            "message": "You are already registered for this event."
        }

    # --------------------------------------------------------
# CHECK AVAILABLE SEATS
# --------------------------------------------------------

    capacity = int(event.get("capacity", 0))

    registered_count = db.event_registrations.count_documents({
        "event_id": event_id,
        "status": "registered"
    })

    if capacity > 0 and registered_count >= capacity:
        raise HTTPException(
        status_code=400,
        detail="This event is full. No seats are available."
    )
    # Create registration
    registration = {
        "student_id": student_id,
        "event_id": event_id,
        "event_title": event.get("title", ""),
        "status": "registered",
        "registered_at": datetime.now(timezone.utc)
    }

    db.event_registrations.insert_one(
        registration
    )

    # Phase A: remember this action (never raises)
    record_interaction(
        db, student_id, event_id, "register", event=event
    )

    # QR: Event Registration QR for this registration
    qr_hook("on_event_registered", student_id, event_id)

    return {
        "status": "success",
        "message": "Successfully registered for the event.",
        "event_id": event_id
    }


# -----------------------------
# Get Student Registrations
# -----------------------------


@app.get("/api/events/registrations")
def get_student_registrations(
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(authorization)

    if not student_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing authentication token"
        )

    registrations = list(
        db.event_registrations.find({
            "student_id": student_id
        })
    )

    registration_map = {}

    for registration in registrations:
        event_id = str(registration.get("event_id", ""))
        status = str(
            registration.get("status", "registered")
        ).lower()

        if not event_id:
            continue

        # Only show active registrations in My Registrations.
        if status == "registered":
            registration_map[event_id] = "Registered"
        elif status == "completed":
            registration_map[event_id] = "Completed"

    return registration_map


# -----------------------------
# Cancel Event Registration
# -----------------------------

@app.delete("/api/events/{event_id}/register")
def cancel_event_registration(
    event_id: str,
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(
        authorization
    )

    result = db.event_registrations.update_one(
        {
            "student_id": student_id,
            "event_id": event_id,
            "status": "registered"
        },
        {
            "$set": {
                "status": "cancelled",
                "cancelled_at": datetime.now(
                    timezone.utc
                )
            }
        }
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Registration not found"
        )

    # Phase A: remember this action (never raises)
    record_interaction(
        db, student_id, event_id, "unregister"
    )

    # QR: a cancelled registration invalidates its QR
    qr_hook("on_registration_cancelled", student_id, event_id)

    return {
        "status": "success",
        "message": "Registration cancelled."
    }
# -----------------------------
# Get Student Saved Events
# -----------------------------

@app.get("/api/events/saved")
def get_saved_events(
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(
        authorization
    )

    saved_events = list(
        db.saved_events.find({
            "student_id": student_id
        })
    )

    saved_event_ids = []

    for item in saved_events:
        saved_event_ids.append(
            item.get("event_id")
        )

    return {
        "savedEventIds": saved_event_ids
    }
# ============================================================
# DASHBOARD EVENTS
# ============================================================

# ============================================================
# DASHBOARD EVENTS
# ============================================================

@app.get("/api/dashboard/events")
def dashboard_events(
    authorization: str = Header(None)
):
    """
    Get dashboard events and generate hybrid AI recommendations.

    The response keeps its original shape ("recommended", "upcoming").
    "engine_status" is new: "ok" or "error". On "error" the client should
    show plain upcoming events instead of inventing scores.
    """

    import logging

    recommendation_logger = logging.getLogger("campuspath.recommendations")

    # ---------------------------------------------------------
    # 1. GET STUDENT ID FROM JWT
    # ---------------------------------------------------------

    student_id = get_student_id_from_token(authorization)

    if not student_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing authentication token"
        )

    # ---------------------------------------------------------
    # 2. GET STUDENT PROFILE
    # ---------------------------------------------------------

    profile = db.profiles.find_one({
        "student_id": student_id
    }) or {}

    # ---------------------------------------------------------
    # 3. GET STUDENT BASIC DATA
    # ---------------------------------------------------------

    try:
        student = db.students.find_one({
            "_id": ObjectId(student_id)
        }) or {}
    except Exception:
        student = db.students.find_one({
            "student_id": student_id
        }) or {}

    # ---------------------------------------------------------
    # 4. GET PUBLISHED EVENTS ONLY
    #
    # Students must only see events that completed the whole approval
    # chain (Faculty -> HOD -> Dean -> Admin = "published"). Events
    # created before the approval workflow existed have no
    # approval_status and stay visible, exactly as in GET /api/events.
    # Both "recommended" and "upcoming" are built from this one list.
    # ---------------------------------------------------------

    events = list(
        db.events.find(PUBLIC_EVENT_FILTER)
    )
    events = audience_gate.filter_events(events, authorization)

    # Convert ObjectId to string. The budget is private, so it is removed
    # here, before the recommender, the AI assistant or the response see it.
    for event in events:
        strip_event_budget(event)

        if "_id" in event:
            event["_id"] = str(event["_id"])

    # ---------------------------------------------------------
    # 5. GET REGISTERED EVENTS
    # ---------------------------------------------------------

    registrations = list(
        db.event_registrations.find({
            "student_id": student_id
        })
    )

    registered_ids = []

    for registration in registrations:
        status = registration.get("status", "registered")

        if status == "registered":
            event_id = registration.get("event_id")

            if event_id is not None:
                registered_ids.append(str(event_id))

    # ---------------------------------------------------------
    # 6. GET SAVED EVENTS
    # ---------------------------------------------------------

    saved_events = list(
        db.saved_events.find({
            "student_id": student_id
        })
    )

    saved_ids = []

    for saved_event in saved_events:
        event_id = saved_event.get("event_id")

        if event_id is not None:
            saved_ids.append(str(event_id))

    # ---------------------------------------------------------
    # 7. GET ATTENDED EVENTS
    # ---------------------------------------------------------

    attendance_records = list(
        db.attendance.find({
            "student_id": student_id
        })
    )

    attended_ids = []

    for attendance in attendance_records:
        event_id = attendance.get("event_id")

        if event_id is not None:
            attended_ids.append(str(event_id))

    # ---------------------------------------------------------
    # 7b. EVENTS THE STUDENT MARKED "NOT INTERESTED"
    # They are left out of this student's recommendations. They stay
    # in "upcoming" so the student can still browse and undo.
    # ---------------------------------------------------------
    not_interested_ids = get_not_interested_ids(student_id)

    # ---------------------------------------------------------
    # 8. GET STUDENT TIMETABLE
    #
    # The WHOLE saved document is passed to the engine (not only the
    # "timetable" part) so it can also read preferences.bufferTime.
    # ---------------------------------------------------------

    # The timetable now comes from the Admin-published timetable of the
    # class the student selected. The document keeps the same shape
    # (timetable / config / preferences), so the engine is unchanged.
    # A student with no timetable gives an empty document: the engine
    # reports "unknown" for schedule fit, never "free".
    try:
        resolved_timetable = resolve_student_timetable(db, student_id)
        timetable_document = {
            "timetable": resolved_timetable.get("timetable") or {},
            "config": resolved_timetable.get("config") or {},
            "preferences": resolved_timetable.get("preferences") or {},
        }
    except Exception:
        recommendation_logger.exception(
            "Could not load the official timetable"
        )
        timetable_document = {}

    # ---------------------------------------------------------
    # 9. SEATS TAKEN PER EVENT (used for availability)
    #
    # Cancelled registrations do not take a seat. If this query
    # fails, counts are simply left out and the engine treats
    # availability as unknown.
    # ---------------------------------------------------------

    registered_counts = {}

    try:
        for row in db.event_registrations.aggregate([
            {"$match": {"status": {"$ne": "cancelled"}}},
            {"$group": {"_id": "$event_id", "count": {"$sum": 1}}},
        ]):
            if row.get("_id") is not None:
                registered_counts[str(row["_id"])] = int(
                    row.get("count", 0)
                )
    except Exception:
        recommendation_logger.exception(
            "Could not count registrations per event"
        )
        registered_counts = None

    # ---------------------------------------------------------
    # 10. STUDENT FEATURES FOR THE ENGINE
    #
    # Only the fields the recommender needs. The full student
    # document (which includes the password hash) is NOT passed on.
    # ---------------------------------------------------------

    student_data = {
        "student_id": student_id,
        "department": student.get("department", ""),
        "year": profile.get("year", ""),
        "interests": profile.get("interests", []),
        "skills": profile.get("skills", []),
        "career_goal": profile.get("career_goal", ""),
        "preferred_domains": profile.get("preferred_domains", []),
    }

    # ---------------------------------------------------------
    # 11. GENERATE HYBRID RECOMMENDATIONS
    # ---------------------------------------------------------

    engine_status = "ok"
    learned_signals = {"sequential": False, "ranker": False}

    try:
        # Import only when this endpoint is called, so a problem in the
        # engine cannot stop the whole FastAPI app from starting.
        from recommendation_engine import get_recommendations

        engine_events = []

        for event in events:
            engine_event = dict(event)

            if registered_counts is not None:
                engine_event["registered_count"] = registered_counts.get(
                    str(event.get("_id")), 0
                )

            engine_events.append(engine_event)

        # Learned signals: each is {} / None unless a model passed its gate.
        sequential_scores = get_sequential_scores_for_student(
            student_id, engine_events
        )
        ranker = get_ranker_for_engine()
        learned_signals["sequential"] = bool(sequential_scores)
        learned_signals["ranker"] = ranker is not None

        recommended = get_recommendations(
            student=student_data,
            events=engine_events,
            feedback={
                "registered_ids": registered_ids,
                "saved_ids": saved_ids,
                "attended_ids": attended_ids,
                "not_interested_ids": not_interested_ids,
            },
            timetable=timetable_document,
            top_k=10,
            mlp_model=ranker,
            sequential_scores=sequential_scores,
        )

        recommendation_logger.info(
            "recommendations generated: %s of %s events",
            len(recommended),
            len(events),
        )

    except Exception:
        recommendation_logger.exception("Recommendation engine failed")

        # Do NOT silently return events as recommendations,
        # because that would remove the scores.
        recommended = []
        engine_status = "error"

    # ---------------------------------------------------------
    # 12. RETURN DASHBOARD DATA
    # ---------------------------------------------------------

    response = {
        "status": "success",
        "recommended": recommended,
        "upcoming": events,
        "engine_status": engine_status,
        "not_interested_ids": not_interested_ids,
        "learned_signals": learned_signals
    }

    # DEMO ONLY: this key is present only while a developer override is
    # switched on (see demo_mode.py). Otherwise the response is unchanged.
    try:
        from demo_mode import get_demo_info
        demo_info = get_demo_info()
    except Exception:
        demo_info = None
    if demo_info:
        response["demo_mode"] = demo_info

    return response


@app.get("/api/demo/status")
def demo_status(authorization: str = Header(None)):
    """
    Read-only. Tells the student dashboard whether a demo-only learned signal
    is switched on, so it can show a clear label. Never raises on its own.
    """
    _hide_require_student(authorization)
    try:
        from demo_mode import get_demo_info
        info = get_demo_info()
    except Exception:
        info = None
    if not info:
        return {"status": "success", "demo_active": False}
    return {
        "status": "success",
        "demo_active": True,
        "message": info["message"],
        "detail": info["detail"],
        "trained_on_simulated": info["trained_on_simulated"],
        "quality_gate_passed": info["quality_gate_passed"],
    }
# ============================================================
# CLUB DASHBOARD
# ============================================================

# ============================================================
# CLUB DASHBOARD
# ============================================================

# ============================================================
# CLUB DASHBOARD
# ============================================================

@app.get("/api/club/notifications")
def get_club_notifications(
    authorization: str = Header(None)
):
    """Notifications (e.g. event edited) for the logged-in organizer."""
    user_id = str(get_student_id_from_token(authorization))

    notifications = list(
        db.notifications.find(
            {
                "recipient": user_id,
                "type": {"$in": ["event_update", "changes_requested"]},
            }
        ).sort("created_at", -1).limit(50)
    )

    return {
        "status": "success",
        "notifications": [
            {
                "id": str(n["_id"]),
                "title": n.get("title", ""),
                "message": n.get("message", ""),
                "type": n.get("type", "event_update"),
                "is_read": bool(n.get("is_read", False)),
                "related_id": n.get("related_id"),
                "event_id": n.get("related_id"),
                "remarks": n.get("remarks", ""),
                "reviewer_role": n.get("reviewer_role", ""),
                "created_at": (
                    n["created_at"].isoformat()
                    if hasattr(n.get("created_at"), "isoformat")
                    else str(n.get("created_at", ""))
                ),
            }
            for n in notifications
        ],
    }


@app.get("/api/club/dashboard")
def club_dashboard(
    authorization: str = Header(None)
):
    """
    Return dashboard data ONLY for the currently logged-in club.
    No sample/fallback data is used.
    """

    # --------------------------------------------------------
    # 1. Get logged-in user ID from JWT
    # --------------------------------------------------------

    try:
        student_id = get_student_id_from_token(
            authorization
        )
    except Exception as e:
        print("CLUB DASHBOARD JWT ERROR:", e)

        raise HTTPException(
            status_code=401,
            detail="Invalid or missing authentication token."
        )

    print("========================================")
    print("CLUB DASHBOARD")
    print("LOGGED-IN USER ID:", student_id)
    print("========================================")

    # --------------------------------------------------------
    # 2. Find the logged-in club
    # --------------------------------------------------------

    try:

        club = None

        # First try ObjectId
        try:
            club_object_id = ObjectId(str(student_id))

            club = db.students.find_one({
                "_id": club_object_id
            })

        except Exception:
            club_object_id = None

        # If ObjectId lookup did not find the user,
        # also check string-based IDs.
        if not club:
            club = db.students.find_one({
                "student_id": str(student_id)
            })

        if not club:
            raise HTTPException(
                status_code=404,
                detail="Club account not found."
            )

    except HTTPException:
        raise

    except Exception as e:

        print(
            "CLUB DATABASE ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to load club account."
        )

    # --------------------------------------------------------
    # 3. Verify this is actually a club account
    # --------------------------------------------------------

    role = str(
        club.get("role", "")
    ).lower().strip()

    if role != "club":

        raise HTTPException(
            status_code=403,
            detail="This account is not registered as a club."
        )

    # --------------------------------------------------------
    # 4. Get club name
    # --------------------------------------------------------

    club_name = str(
        club.get("club_name")
        or club.get("name")
        or club.get("organization_name")
        or "Your Club"
    ).strip()

    print("CLUB NAME:", club_name)

    # --------------------------------------------------------
    # 5. Find ONLY this club's events
    # --------------------------------------------------------

    try:

        event_query = {
            "$or": [

                # Events created using current system
                {
                    "created_by": str(student_id)
                },

                {
                    "createdBy": str(student_id)
                },

                # ObjectId versions for compatibility
                *(
                    [
                        {
                            "created_by":
                                club_object_id
                        },
                        {
                            "createdBy":
                                club_object_id
                        }
                    ]
                    if club_object_id
                    else []
                ),

                # Older event records
                {
                    "organizer": club_name
                },

                {
                    "club_name": club_name
                },

                {
                    "clubName": club_name
                }
            ]
        }

                # Events of the same club created by its Faculty member
        # (shared by club_id) are visible to the coordinator too.
        shared_club_id = user_club_id(club)
        if shared_club_id:
            event_query["$or"].append({"club_id": shared_club_id})

        events = list(
            db.events.find(
                event_query
            ).sort(
                "_id",
                -1
            )
        )

    except Exception as e:

        print(
            "CLUB EVENTS DATABASE ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to load club events."
        )

    print(
        "CLUB EVENTS FOUND:",
        len(events)
    )

    # --------------------------------------------------------
    # 6. Prepare events for frontend
    # --------------------------------------------------------

    # Real registration counts (events do not store a live counter)
    event_ids = [str(e.get("_id", "")) for e in events]
    registration_counts, total_registrations, unique_students = (
        registration_stats(event_ids)
    )

    upcoming_events = []

    for event in events:

        event_id = str(
            event.get("_id", "")
        )

        title = str(
            event.get("title", "")
        )

        category = str(
            event.get("category", "")
        )

        date = str(
            event.get("date", "")
        )

        time = str(
            event.get("time", "")
        )

        location = str(
            event.get("location")
            or event.get("venue")
            or ""
        )

        status = str(
            event.get("status")
            or "PUBLISHED"
        )

        registered = registration_counts.get(event_id, 0)

        capacity = event.get(
            "capacity",
            0
        )

        approval_status = event.get("approval_status") or ""
        history = event.get("approval_history") or []
        latest_review = None
        if approval_status in ("changes_requested", "rejected") and history:
            last = history[-1]
            latest_review = {
                "role": last.get("reviewer_role"),
                "name": last.get("reviewer_name"),
                "decision": last.get("decision"),
                "remarks": last.get("remarks", ""),
                "at": last.get("reviewed_at"),
                "version": last.get("submission_version"),
            }

        approval_labels = {
            "pending_faculty": "AWAITING FACULTY",
            "pending_hod": "AWAITING HOD",
            "pending_dean": "AWAITING DEAN",
            "pending_admin": "AWAITING ADMIN",
            "changes_requested": "CHANGES REQUESTED",
            "rejected": "REJECTED",
            "published": "PUBLISHED",
        }

        upcoming_events.append({
            "id": event_id,
            "title": title,
            "category": category,
            "date": date,
            "time": time,
            "venue": location,
            "registered": registered,
            "capacity": capacity,
            # Real approval stage (events without one are legacy events
            # that were already visible to students).
            "status": approval_labels.get(approval_status, status),
            "approval_status": approval_status,
            "submission_version": event.get("submission_version", 1),
            "created_by_role": event.get("created_by_role", ""),
            "can_edit": approval_status not in PENDING_REVIEW_STATUSES,
            "latest_review": latest_review,
        })

    # --------------------------------------------------------
    # 7. Calculate basic statistics
    # --------------------------------------------------------

    total_events = len(
        events
    )

    # Total registrations = every active registration for this club's events.
    # Total participants = distinct students who registered.
    total_participants = unique_students

    # --------------------------------------------------------
    # 8. Return REAL club data
    # --------------------------------------------------------

    response = {

        "club": {

            "name": club_name,

            "initial": (
                club_name[0].upper()
                if club_name
                else "C"
            )

        },

        "kpis": {

            "totalEvents":
                total_events,

            "upcomingEvents":
                total_events,

            "totalRegistrations":
                total_registrations,

            "totalParticipants":
                total_participants

        },

        "upcomingEvents":
            upcoming_events,

        "recentRegistrations":
            recent_club_registrations(event_ids)

    }

    print(
        "CLUB DASHBOARD RESPONSE:",
        response
    )

    return response
# -----------------------------
# Get Student Saved Events
# -----------------------------

@app.get("/api/events/saved")
def get_saved_events(
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(
        authorization
    )

    saved_events = list(
        db.saved_events.find({
            "student_id": student_id
        })
    )

    saved_event_ids = []

    for item in saved_events:
        saved_event_ids.append(
            item.get("event_id")
        )

    return {
        "savedEventIds": saved_event_ids
    }



# ============================================================
# ATTENDANCE QR CODE
# ============================================================

# ============================================================
# SMALL, DEPENDENCY-FREE QR CODE DRAWER
# ------------------------------------------------------------
# Encodes text (as bytes) into a QR code with error correction level M
# and returns it as a compact SVG string. Nothing needs installing, so
# it behaves the same on every machine.
# ============================================================

# error-correction codewords per block / number of blocks, level M, by
# version 1..40 (index 0 unused)
_QR_ECC_PER_BLOCK = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26,
                     26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
                     28, 28, 28, 28]
_QR_BLOCKS = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17,
              18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49]
_QR_FORMAT_BITS_M = 0  # the 2-bit code for level M


def _qr_raw_data_modules(version):
    result = (16 * version + 128) * version + 64

    if version >= 2:
        align = version // 7 + 2
        result -= (25 * align - 10) * align - 55

        if version >= 7:
            result -= 36

    return result


def _qr_data_codewords(version):
    return (
        _qr_raw_data_modules(version) // 8
        - _QR_ECC_PER_BLOCK[version] * _QR_BLOCKS[version]
    )


def _qr_gf_multiply(x, y):
    result = 0

    for bit in range(7, -1, -1):
        result = (result << 1) ^ ((result >> 7) * 0x11D)
        result ^= ((y >> bit) & 1) * x

    return result


def _qr_rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1

    for _ in range(degree):
        for j in range(degree):
            result[j] = _qr_gf_multiply(result[j], root)

            if j + 1 < degree:
                result[j] ^= result[j + 1]

        root = _qr_gf_multiply(root, 0x02)

    return result


def _qr_rs_remainder(data, divisor):
    result = [0] * len(divisor)

    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)

        for i, coefficient in enumerate(divisor):
            result[i] ^= _qr_gf_multiply(coefficient, factor)

    return result


def _qr_alignment_positions(version):
    if version == 1:
        return []

    count = version // 7 + 2
    step = (
        26 if version == 32
        else (version * 4 + count * 2 + 1) // (count * 2 - 2) * 2
    )
    positions = [(version * 4 + 10) - i * step for i in range(count - 1)] + [6]
    return list(reversed(positions))


def _qr_codewords(data, version):
    """Data bytes -> data + error-correction codewords, interleaved."""
    capacity = _qr_data_codewords(version)
    bits = [0, 1, 0, 0]  # byte mode
    count_bits = 8 if version <= 9 else 16
    bits += [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]

    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]

    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)

    codewords = [
        int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)
    ]

    pad = 0xEC
    while len(codewords) < capacity:
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11

    blocks_total = _QR_BLOCKS[version]
    ecc_length = _QR_ECC_PER_BLOCK[version]
    raw_codewords = _qr_raw_data_modules(version) // 8
    short_blocks = blocks_total - raw_codewords % blocks_total
    short_length = raw_codewords // blocks_total

    divisor = _qr_rs_divisor(ecc_length)
    blocks = []
    start = 0

    for index in range(blocks_total):
        take = short_length - ecc_length + (0 if index < short_blocks else 1)
        block = codewords[start:start + take]
        start += take
        ecc = _qr_rs_remainder(block, divisor)

        if index < short_blocks:
            block = block + [0]  # keeps short and long blocks aligned

        blocks.append(block + ecc)

    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_length - ecc_length or j >= short_blocks:
                result.append(block[i])

    return result


def _qr_bit(value, index):
    return (value >> index) & 1 == 1


class _QrGrid:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set_fixed(self, x, y, dark):
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def draw_patterns(self):
        size = self.size

        for i in range(size):
            self.set_fixed(6, i, i % 2 == 0)
            self.set_fixed(i, 6, i % 2 == 0)

        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy

                    if 0 <= x < size and 0 <= y < size:
                        distance = max(abs(dx), abs(dy))
                        self.set_fixed(x, y, distance not in (2, 4))

        positions = _qr_alignment_positions(self.version)
        last = len(positions) - 1

        for i, cy in enumerate(positions):
            for j, cx in enumerate(positions):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue

                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fixed(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

        self.draw_format(0)  # placeholder, marks the area as fixed

        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)

            bits = self.version << 12 | remainder

            for i in range(18):
                a = size - 11 + i % 3
                b = i // 3
                self.set_fixed(a, b, _qr_bit(bits, i))
                self.set_fixed(b, a, _qr_bit(bits, i))

    def draw_format(self, mask):
        size = self.size
        data = _QR_FORMAT_BITS_M << 3 | mask
        remainder = data

        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)

        bits = (data << 10 | remainder) ^ 0x5412

        for i in range(0, 6):
            self.set_fixed(8, i, _qr_bit(bits, i))

        self.set_fixed(8, 7, _qr_bit(bits, 6))
        self.set_fixed(8, 8, _qr_bit(bits, 7))
        self.set_fixed(7, 8, _qr_bit(bits, 8))

        for i in range(9, 15):
            self.set_fixed(14 - i, 8, _qr_bit(bits, i))

        for i in range(0, 8):
            self.set_fixed(size - 1 - i, 8, _qr_bit(bits, i))

        for i in range(8, 15):
            self.set_fixed(8, size - 15 + i, _qr_bit(bits, i))

        self.set_fixed(8, size - 8, True)

    def place_codewords(self, codewords):
        size = self.size
        index = 0
        total_bits = len(codewords) * 8

        for right in range(size - 1, 0, -2):
            if right <= 6:
                right -= 1

            for vertical in range(size):
                for offset in range(2):
                    x = right - offset
                    upward = ((right + 1) & 2) == 0
                    y = (size - 1 - vertical) if upward else vertical

                    if not self.fixed[y][x] and index < total_bits:
                        self.dark[y][x] = _qr_bit(codewords[index >> 3], 7 - (index & 7))
                        index += 1

    def apply_mask(self, mask):
        for y in range(self.size):
            for x in range(self.size):
                if self.fixed[y][x]:
                    continue

                if mask == 0:
                    invert = (x + y) % 2 == 0
                elif mask == 1:
                    invert = y % 2 == 0
                elif mask == 2:
                    invert = x % 3 == 0
                elif mask == 3:
                    invert = (x + y) % 3 == 0
                elif mask == 4:
                    invert = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    invert = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    invert = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    invert = ((x + y) % 2 + x * y % 3) % 2 == 0

                self.dark[y][x] ^= invert

    def penalty(self):
        size = self.size
        score = 0
        lines = ["".join("1" if cell else "0" for cell in row) for row in self.dark]
        columns = ["".join("1" if self.dark[y][x] else "0" for y in range(size)) for x in range(size)]

        for line in lines + columns:
            run = 1
            for i in range(1, size):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run = 1
            if run >= 5:
                score += 3 + run - 5

            for pattern in ("10111010000", "00001011101"):
                start = line.find(pattern)
                while start != -1:
                    score += 40
                    start = line.find(pattern, start + 1)

        for y in range(size - 1):
            for x in range(size - 1):
                if self.dark[y][x] == self.dark[y][x + 1] == self.dark[y + 1][x] == self.dark[y + 1][x + 1]:
                    score += 3

        dark = sum(row.count(True) for row in self.dark)
        total = size * size
        score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
        return score


def make_qr_svg(text):
    """The QR code for `text` as a small SVG string (level M, byte mode)."""
    data = list(str(text).encode("utf-8"))

    version = None
    for candidate in range(1, 41):
        count_bits = 8 if candidate <= 9 else 16
        if 4 + count_bits + 8 * len(data) <= _qr_data_codewords(candidate) * 8:
            version = candidate
            break

    if version is None:
        raise ValueError("Text is too long for a QR code.")

    grid = _QrGrid(version)
    grid.draw_patterns()
    grid.place_codewords(_qr_codewords(data, version))

    best_mask, best_score = 0, None
    for mask in range(8):
        grid.apply_mask(mask)
        grid.draw_format(mask)
        score = grid.penalty()
        grid.apply_mask(mask)  # undo

        if best_score is None or score < best_score:
            best_mask, best_score = mask, score

    grid.apply_mask(best_mask)
    grid.draw_format(best_mask)

    quiet = 4  # blank border every scanner expects
    size = grid.size + quiet * 2
    runs = []

    for y, row in enumerate(grid.dark):
        x = 0
        while x < grid.size:
            if row[x]:
                start = x
                while x < grid.size and row[x]:
                    x += 1
                runs.append(f"M{start + quiet} {y + quiet}h{x - start}v1h-{x - start}z")
            else:
                x += 1

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path d="{"".join(runs)}" fill="#000"/></svg>'
    )


@app.get("/api/events/{event_id}/attendance/qr")
def get_attendance_qr(
    event_id: str,
    authorization: str = Header(None)
):
    """
    Generate/retrieve the student's attendance QR token
    for a registered event.
    """

    # --------------------------------------------------------
    # Get logged-in student
    # --------------------------------------------------------

    student_id = get_student_id_from_token(
        authorization
    )

    # --------------------------------------------------------
    # Validate event ID
    # --------------------------------------------------------

    try:
        event_object_id = ObjectId(event_id)

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    # --------------------------------------------------------
    # Check event
    # --------------------------------------------------------

    event = db.events.find_one({
        "_id": event_object_id
    })

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # --------------------------------------------------------
    # Check student's registration
    # --------------------------------------------------------

    registration = db.event_registrations.find_one({
        "student_id": student_id,
        "event_id": event_id,
        "status": "registered"
    })

    if not registration:
        raise HTTPException(
            status_code=403,
            detail="You are not registered for this event."
        )

    # --------------------------------------------------------
    # Students who left the audience can no longer get a QR (records are kept).
    audience_gate.require_student_access(
        event, student_id, 403, "You are no longer eligible for this event."
    )

    # Generate attendance token if it doesn't exist
    # --------------------------------------------------------

    attendance_token = registration.get(
        "attendance_token"
    )

    if not attendance_token:

        attendance_token = secrets.token_urlsafe(32)

        db.event_registrations.update_one(
            {
                "_id": registration["_id"]
            },
            {
                "$set": {
                    "attendance_token": attendance_token
                }
            }
        )

    # --------------------------------------------------------
    # Return QR data
    # --------------------------------------------------------

    qr_svg = None
    qr_error = ""

    try:
        qr_svg = make_qr_svg(attendance_token)
    except Exception as error:
        qr_error = f"{type(error).__name__}: {error}"
        print("ATTENDANCE QR IMAGE ERROR:", qr_error)

    return {
        "status": "success",
        "event_id": event_id,
        "event_title": event.get(
            "title",
            ""
        ),
        "event_date": event.get("date", ""),
        "event_time": event.get("time", ""),
        "qr_data": attendance_token,
        # ready-to-show image of the same code; if it could not be drawn,
        # qr_svg is None and qr_error says why (the code itself still works)
        "qr_svg": qr_svg,
        "qr_error": qr_error
    }


# ============================================================
# SCAN ATTENDANCE QR
# ============================================================

@app.post("/api/events/{event_id}/attendance/scan")
def scan_attendance_qr(
    event_id: str,
    payload: dict,
    authorization: str = Header(None)
):
    """
    Event leader scans student's QR code.

    Attendance is marked only when:
    1. The event exists.
    2. The scanner owns the event.
    3. The QR belongs to a registered student.
    4. The student has not already been marked present.
    """

    # --------------------------------------------------------
    # Get logged-in event leader
    # --------------------------------------------------------

    leader_id = get_student_id_from_token(
        authorization
    )

    # --------------------------------------------------------
    # Validate event ID
    # --------------------------------------------------------

    try:
        event_object_id = ObjectId(event_id)

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    # --------------------------------------------------------
    # Find event
    # --------------------------------------------------------

    event = db.events.find_one({
        "_id": event_object_id
    })

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # --------------------------------------------------------
    # Verify event leader
    # --------------------------------------------------------

    if str(event.get("created_by")) != str(leader_id):

        raise HTTPException(
            status_code=403,
            detail="Only the event leader can mark attendance."
        )

    # --------------------------------------------------------
    # Get QR data
    # --------------------------------------------------------

    qr_data = payload.get(
        "qr_data"
    )

    if not qr_data:
        raise HTTPException(
            status_code=400,
            detail="QR code data is missing."
        )

    # QR system: new-style QR codes (Event Registration QR) are validated there
    if str(qr_data).startswith("CPQ-"):
        return qr_scan_for_scanner(qr_data, event_id, leader_id)

    # --------------------------------------------------------
    # Find registration using QR token
    # --------------------------------------------------------

    registration = db.event_registrations.find_one({
        "event_id": event_id,
        "attendance_token": qr_data,
        "status": "registered"
    })

    if not registration:

        raise HTTPException(
            status_code=404,
            detail="Invalid QR code or student is not registered for this event."
        )

    # --------------------------------------------------------
    # The scanned student must still be in the event's audience.
    audience_gate.require_student_access(
        event, registration["student_id"], 403,
        "This student is no longer in the audience for this event."
    )

    # Check if attendance already exists
    # --------------------------------------------------------

    existing_attendance = db.attendance.find_one({
        "event_id": event_id,
        "student_id": registration["student_id"]
    })

    if existing_attendance:

        return {
            "status": "already_marked",
            "message": "Attendance has already been marked.",
            "student_id": registration["student_id"],
            "event_id": event_id
        }

    # --------------------------------------------------------
    # Mark attendance
    # --------------------------------------------------------

    attendance_data = {
        "event_id": event_id,
        "student_id": registration["student_id"],
        "registration_id": str(
            registration["_id"]
        ),
        "status": "present",
        "marked_at": datetime.now(
            timezone.utc
        ),
        "marked_by": leader_id
    }

    db.attendance.insert_one(
        attendance_data
    )

    # Phase A: log the STUDENT'S attendance (never the organizer's id)
    record_interaction(
        db, registration["student_id"], event_id, "attend", event=event
    )

    # --------------------------------------------------------
    # Return success
    # --------------------------------------------------------

    return {
        "status": "success",
        "message": "Attendance marked successfully.",
        "event_id": event_id,
        "student_id": registration["student_id"],
        "marked_at": attendance_data[
            "marked_at"
        ].isoformat()
    }


# ============================================================
# GET EVENT ATTENDANCE
# ============================================================

# ============================================================
# GET EVENT ATTENDANCE
# ============================================================

@app.get("/api/events/{event_id}/attendance")
def get_event_attendance(
    event_id: str,
    authorization: str = Header(None)
):
    """
    Event leader can view attendance for their event.

    Returns complete attendance information including:
    - Student name
    - Student ID
    - Email
    - Department
    - Event
    - Attendance status
    - Marked date/time
    """

    # --------------------------------------------------------
    # GET LOGGED-IN CLUB LEADER
    # --------------------------------------------------------

    leader_id = get_student_id_from_token(
        authorization
    )

    # --------------------------------------------------------
    # VALIDATE EVENT ID
    # --------------------------------------------------------

    try:
        event_object_id = ObjectId(event_id)

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    # --------------------------------------------------------
    # FIND EVENT
    # --------------------------------------------------------

    event = db.events.find_one({
        "_id": event_object_id
    })

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # --------------------------------------------------------
    # VERIFY EVENT LEADER
    # --------------------------------------------------------

    if str(event.get("created_by")) != str(leader_id):

        raise HTTPException(
            status_code=403,
            detail="Only the event leader can view attendance."
        )

    # --------------------------------------------------------
    # GET ATTENDANCE
    # --------------------------------------------------------

    attendance_records = list(
        db.attendance.find({
            "event_id": event_id
        }).sort(
            "marked_at",
            1
        )
    )

    result = []

    # --------------------------------------------------------
    # PREPARE ATTENDANCE RECORDS
    # --------------------------------------------------------

    for index, record in enumerate(
        attendance_records,
        start=1
    ):

        student_id = str(
            record.get(
                "student_id",
                ""
            )
        )

        # ----------------------------------------------------
        # FIND STUDENT
        # ----------------------------------------------------

        student = None

        try:

            student = db.students.find_one({
                "_id": ObjectId(student_id)
            })

        except Exception:

            student = db.students.find_one({
                "student_id": student_id
            })

        # ----------------------------------------------------
        # STUDENT DETAILS
        # ----------------------------------------------------

        student_name = ""
        student_email = ""
        student_department = ""
        actual_student_id = student_id

        if student:

            student_name = student.get(
                "name",
                ""
            )

            student_email = student.get(
                "email",
                ""
            )

            student_department = student.get(
                "department",
                ""
            )

            if student.get("student_id"):

                actual_student_id = student.get(
                    "student_id"
                )

        # ----------------------------------------------------
        # MARKED TIME
        # ----------------------------------------------------

        marked_at = record.get(
            "marked_at"
        )

        if isinstance(
            marked_at,
            datetime
        ):

            india_timezone = timezone(
                timedelta(
                    hours=5,
                    minutes=30
                )
            )

            india_time = marked_at.astimezone(
                india_timezone
            )

            formatted_time = india_time.strftime(
                "%d %b %Y, %I:%M %p"
            )

        else:

            formatted_time = str(
                marked_at or ""
            )

        # ----------------------------------------------------
        # ADD RECORD
        # ----------------------------------------------------

        result.append({

            "no": index,

            "attendance_id": str(
                record.get(
                    "_id",
                    ""
                )
            ),

            "event_id": event_id,

            "event_name":
                event.get(
                    "title",
                    ""
                ),

            "student_id":
                actual_student_id,

            "name":
                student_name,

            "email":
                student_email,

            "department":
                student_department,

            "status":
                record.get(
                    "status",
                    "present"
                ),

            "marked_at":
                formatted_time,

            "marked_by":
                str(
                    record.get(
                        "marked_by",
                        ""
                    )
                )
        })

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return {

        "status": "success",

        "event_id":
            event_id,

        "event_name":
            event.get(
                "title",
                ""
            ),

        "total_attendance":
            len(result),

        "attendance":
            result
    }
# ============================================================
# GET SINGLE EVENT
# ============================================================

@app.get("/api/events/{event_id}")
def get_event(event_id: str, authorization: str = Header(None)):

    try:

        event = db.events.find_one(
            {
                "_id": ObjectId(event_id)
            }
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    if not event:

        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    event["_id"] = str(
        event["_id"]
    )

    # Unpublished events are visible to staff / admin only, never to
    # students or signed-out visitors.
    if not is_event_public(event) and not viewer_can_see_budget(authorization):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # Publishing audience: students outside it cannot open the event by URL.
    if not audience_gate.can_view(event, authorization):
        raise HTTPException(status_code=404, detail="Event not found")

    # Live registration count (not a stored counter)
    event["registered"] = db.event_registrations.count_documents({
        "event_id": event["_id"],
        "status": "registered",
    })

    # The budget is private: HOD / Dean / Admin, and Club / Faculty for
    # their own club's events only. Everyone else gets neither field.
    if not viewer_can_see_budget(authorization, event):
        strip_event_budget(event)

    return event

# ============================================================
# UPDATE EVENT
# ============================================================

# ============================================================
# UPDATE EVENT
# ============================================================

@app.put("/api/events/{event_id}")
def update_event(
    event_id: str,
    event: EventWithResources,
    authorization: str = Header(None)
):

    # --------------------------------------------------------
    # Get logged-in user
    # --------------------------------------------------------

    student_id = get_student_id_from_token(
        authorization
    )

    # --------------------------------------------------------
    # Validate event ID
    # --------------------------------------------------------

    try:

        event_object_id = ObjectId(event_id)

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    # --------------------------------------------------------
    # Get existing event
    # --------------------------------------------------------

    existing_event = db.events.find_one(
        {
            "_id": event_object_id
        }
    )

    if not existing_event:

        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # --------------------------------------------------------
    # Get logged-in user
    # --------------------------------------------------------

    try:

        user = db.students.find_one(
            {
                "_id": ObjectId(student_id)
            }
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid user ID"
        )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # --------------------------------------------------------
    # CHECK ROLE
    # --------------------------------------------------------

    user_role = str(
        user.get(
            "role",
            ""
        )
    ).lower()

    if user_role not in [
        "club",
        "faculty",
        "admin"
    ]:

        raise HTTPException(
            status_code=403,
            detail="You are not authorized to update events."
        )

    # --------------------------------------------------------
    # CLUB OWNERSHIP CHECK
    # --------------------------------------------------------

    if user_role in ("club", "faculty"):

        event_owner = existing_event.get(
            "created_by"
        )

        event_organizer = str(
            existing_event.get(
                "organizer",
                ""
            )
        ).strip().lower()

        club_name = str(
            user.get(
                "club_name",
                user.get(
                    "name",
                    ""
                )
            )
        ).strip().lower()

        # Allow the club that created the event
        # OR the club whose name matches organizer.

        owner_matches = (
            event_owner
            and str(event_owner) == str(student_id)
        )

        organizer_matches = (
            event_organizer
            and event_organizer == club_name
        )

        club_matches = bool(existing_event.get("club_id")) and (
            existing_event.get("club_id") == user_club_id(user)
        )

        if not owner_matches and not organizer_matches and not club_matches:

            raise HTTPException(
                status_code=403,
                detail="You can only edit your own club events."
            )

    # --------------------------------------------------------
    # EVENTS UNDER ACTIVE REVIEW CANNOT BE EDITED
    # --------------------------------------------------------

    # A published event is live for students. Editing it here used to restart
    # the approval chain and hide it, so organisers must use a modification request.
    if user_role in ("club", "faculty") and existing_event.get(
        "approval_status"
    ) in (None, "published"):
        raise HTTPException(
            status_code=409,
            detail=(
                "This event is already published and cannot be edited directly. "
                "Use Request Modification so the Admin can review the change."
            ),
        )

    faculty_correcting = (
        user_role == "faculty"
        and existing_event.get("approval_status") == "pending_faculty"
    )

    if (
        user_role != "admin"
        and not faculty_correcting
        and existing_event.get("approval_status") in PENDING_REVIEW_STATUSES
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This event is currently under review and cannot be edited. "
                "You can edit it if a reviewer returns it for changes."
            ),
        )

    # --------------------------------------------------------
    # PREPARE UPDATED EVENT
    # --------------------------------------------------------

    details = clean_event_details(event)
    budget_fields = resolve_event_budget(event, user_role, creating=False)
    registration_fields = resolve_registration_required(
        event, user_role, creating=False
    )
    deadline_fields = resolve_registration_deadline(
        event, user_role, creating=False, existing=existing_event
    )

    update_data = {

        **budget_fields,

        **registration_fields,

        **deadline_fields,

        "title": event.title,

        "description": event.description,

        "category": event.category,

        "organizer": event.organizer,

        "date": event.date,

        "time": event.time,

        "location": details["location"],

        "school_name": details["school_name"],

        "department": details["department"],

        "event_mode": details["event_mode"],

        "spoc_name": details["spoc_name"],

        "spoc_designation": details["spoc_designation"],

        "spoc_email": details["spoc_email"],

        "spoc_contact": details["spoc_contact"],

        "skills": event.skills,

        "updated_at": datetime.now(timezone.utc),

    }

    # Seats available / capacity (was never saved on edit before)
    if event.capacity is not None:
        update_data["capacity"] = event.capacity

    if event.poster_url is not None:
        update_data["poster_url"] = clean_poster_url(event.poster_url)

    # Only touch resource persons when the client actually sent them
    if event.resource_persons is not None:
        update_data["resource_persons"] = clean_resource_persons(
            event.resource_persons
        )

    # Any edit by a club / inter-college organizer restarts the approval
    # chain: HOD -> Dean -> Admin -> published (visible to students).
    # Admin edits do not restart it.
    if faculty_correcting:
        # Faculty correcting a coordinator's submission: it stays in the
        # faculty review queue (same version) until they forward it.
        pass
    elif user_role in ("club", "faculty"):
        # Coordinator edit -> Faculty review; faculty edit -> HOD.
        update_data["approval_status"] = resubmission_status(user_role, user)
        if user_role in ("club", "faculty"):
            update_data["club_id"] = user_club_id(user)
        update_data["submission_version"] = (
            int(existing_event.get("submission_version", 1)) + 1
        )

    # --------------------------------------------------------
    # UPDATE MONGODB
    # --------------------------------------------------------

    changed_fields = [
        key
        for key in (
            "title", "description", "category", "date", "time",
            "location", "capacity", "skills", "poster_url",
            "school_name", "department", "event_mode", "spoc_name",
            "spoc_designation", "spoc_email", "spoc_contact", "budget",
            "budget_breakup", "registration_required", "registration_deadline",
            "resource_persons",
        )
        if key in update_data and update_data[key] != existing_event.get(key)
    ]

    edit_entry = {
        "editor_id": str(student_id),
        "editor_name": user.get("name", ""),
        "editor_role": user_role,
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "changed_fields": changed_fields,
        "submission_version": update_data.get(
            "submission_version",
            existing_event.get("submission_version", 1),
        ),
        "status_after": update_data.get(
            "approval_status", existing_event.get("approval_status")
        ),
    }

    result = db.events.update_one(

        {
            "_id": event_object_id
        },

        {
            "$set": update_data,
            "$push": {"edit_history": edit_entry},
        }

    )

    # --------------------------------------------------------
    # CHECK UPDATE
    # --------------------------------------------------------

    if result.matched_count == 0:

        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # --------------------------------------------------------
    # NOTIFY THE CLUB THAT THE EVENT WAS EDITED
    # --------------------------------------------------------

    try:
        event_title = event.title or existing_event.get("title", "Event")

        message = f'"{event_title}" was updated successfully.'

        recipients = {str(student_id)}

        if existing_event.get("created_by"):
            recipients.add(str(existing_event["created_by"]))

        for recipient_id in recipients:
            db.notifications.insert_one({
                "title": "Event Updated",
                "message": message,
                "type": "event_update",
                "recipient": recipient_id,
                "related_id": event_id,
                "is_read": False,
                "created_at": datetime.now(timezone.utc)
            })
    except Exception as e:
        # A notification failure must not undo a successful update.
        print("EVENT UPDATE NOTIFICATION ERROR:", repr(e))

    if user_role == "club" and update_data.get("approval_status") == "pending_faculty":
        notify_faculty_of_club(
            existing_event.get("club_id") or user_club_id(user),
            user.get("club_name"),
            "Event resubmitted for your review",
            f'{user.get("name", "A coordinator")} updated and resubmitted '
            f'"{event.title or existing_event.get("title", "an event")}" '
            f'(version {update_data.get("submission_version", "")}).',
            "review_requested",
            event_id,
            skip_user_id=student_id,
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    return {

        "status": "success",

        "message": "Event updated successfully!",

        "event_id": event_id

    }


# ============================================================
# CANCEL EVENT REGISTRATION
# ============================================================

@app.delete("/api/events/{event_id}/register")
def cancel_event_registration(
    event_id: str,
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(
        authorization
    )

    result = db.event_registrations.update_one(
        {
            "student_id": student_id,
            "event_id": event_id,
            "status": "registered"
        },
        {
            "$set": {
                "status": "cancelled",
                "cancelled_at": datetime.now(
                    timezone.utc
                )
            }
        }
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Registration not found"
        )

    return {
        "status": "success",
        "message": "Registration cancelled."
    }


# ============================================================
# SAVE EVENT
# ============================================================

@app.post("/api/events/{event_id}/save")
def save_event(
    event_id: str,
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(
        authorization
    )

    try:
        event_object_id = ObjectId(event_id)

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid event ID"
        )

    # Check event exists

    event = db.events.find_one(
        {
            "_id": event_object_id
        }
    )

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # Students may only save published events.
    if not is_event_public(event):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )
    audience_gate.require_student_access(event, student_id)

    # Check existing saved event

    existing = db.saved_events.find_one(
        {
            "student_id": student_id,
            "event_id": event_id
        }
    )

    if existing:
        return {
            "status": "already_saved",
            "message": "Event is already saved."
        }

    # Save event

    db.saved_events.insert_one(
        {
            "student_id": student_id,
            "event_id": event_id,
            "saved_at": datetime.now(
                timezone.utc
            )
        }
    )

    # Phase A: remember this action (never raises)
    record_interaction(
        db, student_id, event_id, "save", event=event
    )

    return {
        "status": "success",
        "message": "Event saved successfully.",
        "event_id": event_id
    }


# ============================================================
# REMOVE SAVED EVENT
# ============================================================

@app.delete("/api/events/{event_id}/save")
def remove_saved_event(
    event_id: str,
    authorization: str = Header(None)
):
    student_id = get_student_id_from_token(
        authorization
    )

    result = db.saved_events.delete_one(
        {
            "student_id": student_id,
            "event_id": event_id
        }
    )

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Saved event not found"
        )

    # Phase A: remember this action (never raises)
    record_interaction(
        db, student_id, event_id, "unsave"
    )

    return {
        "status": "success",
        "message": "Event removed from saved events."
    }
    # --------------------------------------------------------
    # Only club / intercollege / admin can update
    # --------------------------------------------------------

    user_role = user.get(
        "role",
        "student"
    )

    if user_role not in [
        "club",
        "faculty",
        "admin"
    ]:

        raise HTTPException(
            status_code=403,
            detail="You are not authorized to update events."
        )

    # --------------------------------------------------------
    # Make sure club can edit its own event
    # --------------------------------------------------------

    if user_role == "club":

        event_owner = existing_event.get(
            "created_by"
        )

        event_organizer = existing_event.get(
            "organizer",
            ""
        )

        club_name = user.get(
            "club_name",
            user.get(
                "name",
                ""
            )
        )

        # Allow if the event was created by this club
        # OR the organizer matches the club name.

        if (
            event_owner
            and str(event_owner) != str(student_id)
            and event_organizer != club_name
        ):

            raise HTTPException(
                status_code=403,
                detail="You can only edit your own club events."
            )

    # --------------------------------------------------------
    # Update event
    # --------------------------------------------------------

    update_data = {

        "title": event.title,

        "description": event.description,

        "category": event.category,

        "organizer": event.organizer,

        "date": event.date,

        "time": event.time,

        "location": event.location,

        "skills": event.skills,

    }

    result = db.events.update_one(

        {
            "_id": ObjectId(event_id)
        },

        {
            "$set": update_data
        }

    )

    # --------------------------------------------------------
    # Check update
    # --------------------------------------------------------

    if result.matched_count == 0:

        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # --------------------------------------------------------
    # Success
    # --------------------------------------------------------

    return {

        "status": "success",

        "message": "Event updated successfully!",

        "event_id": event_id

    }
# ============================================================
# SEND OTP
# ============================================================

@app.post("/send-otp")
def send_otp(email: str):

    try:

        # Generate OTP and send it to user's email
        create_otp(email)

        return {

            "success": True,

            "message":
                "OTP sent successfully to your email."

        }

    except Exception as e:

        print("=" * 50)
        print("OTP EMAIL ERROR")
        print(str(e))
        print("=" * 50)

        return {

            "success": False,

            "message":
                "Unable to send OTP email. Please try again."

        }


# ============================================================
# VERIFY OTP
# ============================================================

@app.post("/verify-otp")
def verify_otp_endpoint(
    email: str,
    otp: str
):

    success, message = verify_otp(
        email,
        otp
    )

    return {

        "success": success,

        "message": message

    }


# ============================================================
# CAMPUSPATH AI - GENERATIVE AI CHAT ASSISTANT
# ============================================================

@app.post("/api/ai/chat")
def ai_chat(
    payload: AIChatRequest,
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    authorization = f"Bearer {credentials.credentials}"
    """
    Generative AI assistant for CampusPathAI.

    Uses:
    - Authenticated student profile
    - Existing hybrid recommendations
    - Available campus events
    - Recent chat history
    """

    # --------------------------------------------------------
    # 1. VALIDATE MESSAGE AND API KEY
    # --------------------------------------------------------

    message = payload.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Please enter a message."
        )

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Generative AI is not configured. Check the backend .env file."
        )

    # --------------------------------------------------------
    # 2. AUTHENTICATE STUDENT
    # --------------------------------------------------------

    student_id = get_student_id_from_token(authorization)

    # --------------------------------------------------------
    # 3. LOAD STUDENT PROFILE
    # --------------------------------------------------------

    profile = db.profiles.find_one({
        "student_id": student_id
    }) or {}

    try:
        student = db.students.find_one({
            "_id": ObjectId(student_id)
        }) or {}
    except Exception:
        student = db.students.find_one({
            "student_id": student_id
        }) or {}

    # Only include fields relevant to event recommendations.
    student_context = {
        "name": profile.get("name") or student.get("name", ""),
        "year": profile.get("year") or student.get("year", ""),
        "department": (
            profile.get("department")
            or student.get("department", "")
        ),
        "interests": profile.get("interests", []),
        "skills": profile.get("skills", []),
        "career_goal": profile.get("career_goal", ""),
        "preferred_domains": profile.get(
            "preferred_domains", []
        ),
    }

    # --------------------------------------------------------
    # 4. GET EXISTING EVENTS AND RECOMMENDATIONS
    # --------------------------------------------------------

    try:
        dashboard_data = dashboard_events(authorization)

        all_events = dashboard_data.get("upcoming", [])
        recommendations = dashboard_data.get("recommended", [])

    except Exception as exc:
        print("AI CHAT CONTEXT ERROR:", repr(exc))

        raise HTTPException(
            status_code=500,
            detail="Unable to load campus events and recommendations."
        )

    # --------------------------------------------------------
    # 5. PREPARE COMPACT EVENT CONTEXT
    # --------------------------------------------------------

    def compact_event(item):
        if not isinstance(item, dict):
            return {}

        # Recommendation results may contain an inner event object.
        event = item.get("event", item)

        if not isinstance(event, dict):
            event = item

        return {
            "id": str(
                event.get("id")
                or event.get("_id")
                or event.get("event_id")
                or ""
            ),
            "title": event.get("title", ""),
            "category": (
                event.get("category")
                or event.get("cat")
                or ""
            ),
            "description": (
                event.get("description")
                or event.get("desc")
                or ""
            ),
            "date": str(event.get("date", "")),
            "time": event.get("time", ""),
            "venue": (
                event.get("venue")
                or event.get("location")
                or ""
            ),
            "organizer": (
                event.get("organizer")
                or event.get("org")
                or ""
            ),
            "skills": event.get("skills", []),
            "mode": event.get("mode", ""),
            "match_score": item.get(
                "matchScore",
                item.get("recommendationScore")
            ),
            "recommendation_reasons": item.get(
                "reasons",
                item.get("recommendationReason", [])
            ),
        }

    event_context = [
        compact_event(event)
        for event in all_events[:40]
    ]

    recommendation_context = [
        compact_event(event)
        for event in recommendations[:10]
    ]

    # --------------------------------------------------------
    # 6. PREPARE CONVERSATION HISTORY
    # --------------------------------------------------------

    history_messages = []

    for entry in payload.history[-8:]:
        if not isinstance(entry, dict):
            continue

        role = entry.get("role")
        content = entry.get("content", "")

        if (
            role in ("user", "assistant")
            and isinstance(content, str)
            and content.strip()
        ):
            history_messages.append({
                "role": role,
                "content": content[:2000]
            })

    # --------------------------------------------------------
    # 7. BUILD AI INSTRUCTIONS
    # --------------------------------------------------------

    system_prompt = f"""
You are CampusPath AI, a helpful generative AI assistant
integrated into a university campus event platform.

Your responsibilities:
1. Help students discover relevant campus events.
2. Explain why existing recommendations may be suitable.
3. Suggest events based on skills, interests and career goals.
4. Explain event details using the supplied event information.
5. Help students identify skills they can develop through events.
6. Answer general questions about learning and career preparation.

IMPORTANT RULES:
- Use the supplied student profile, events and recommendations.
- Prefer the existing hybrid recommendation results when suggesting
  personalized events.
- Do not invent events, dates, times, venues or registration details.
- If an event detail is unavailable, clearly say it is not available.
- Distinguish personalized recommendations from general suggestions.
- If no relevant event exists, say so honestly.
- Do not claim to register the student for an event.
- Be friendly, concise and practical.
- Use readable bullet points when listing events.
- Reply in plain text only. Do not use markdown symbols such as ** or #.
- Keep answers under about 150 words unless the student asks for more detail.

STUDENT PROFILE:
{__import__("json").dumps(student_context, default=str)}

EXISTING HYBRID RECOMMENDATIONS:
{__import__("json").dumps(recommendation_context, default=str)}

AVAILABLE CAMPUS EVENTS:
{__import__("json").dumps(event_context, default=str)}
"""

    # --------------------------------------------------------
    # 8. CALL GEMINI API
    # --------------------------------------------------------

    import logging
    from ai_gemini import AIServiceError, generate_with_fallback

    chat_logger = logging.getLogger("campuspath.chat")

    conversation = []

    for entry in history_messages:
        role = "Student" if entry["role"] == "user" else "CampusPath AI"
        conversation.append(f"{role}: {entry['content']}")

    conversation.append(f"Student: {message}")

    full_prompt = (
        system_prompt
        + "\n\nCONVERSATION:\n"
        + "\n".join(conversation)
        + "\n\nCampusPath AI:"
    )

    try:
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 20000},  # milliseconds
        )

        answer = generate_with_fallback(client, full_prompt)

    except AIServiceError as exc:
        # Friendly message for the student; details stay in the server log.
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
        )

    except Exception:
        chat_logger.exception("Unexpected AI chat error")
        raise HTTPException(
            status_code=502,
            detail="The AI assistant ran into a problem. Please try again.",
        )

    if not answer:
        answer = (
            "I couldn't generate a response just now. "
            "Please try asking your question again."
        )

    return {
        "status": "success",
        "answer": answer,
    }


# ============================================================
# CAMPUSPATH AI — EVENT APPROVAL WORKFLOW
# Add at the end of backend/main.py
# ============================================================

from datetime import datetime, timezone
from bson import ObjectId
from fastapi import Header, HTTPException


# ============================================================
# CLUB / FACULTY HELPERS
# ============================================================

PENDING_REVIEW_STATUSES = (
    "pending_faculty",
    "pending_hod",
    "pending_dean",
    "pending_admin",
)


def normalize_text(value):
    return " ".join(str(value or "").strip().lower().split())


def make_club_id(club_name, college_name=""):
    """Stable key shared by a club's coordinator(s), its faculty and events."""
    name = normalize_text(club_name)
    college = normalize_text(college_name)
    if not name:
        return ""
    return f"{name}::{college}" if college else name


def user_club_id(user):
    """club_id stored on the account, or derived for older accounts."""
    return (
        user.get("club_id")
        or make_club_id(user.get("club_name"), user.get("college_name"))
    )


def event_belongs_to_club(event, user):
    """True if this event belongs to the club of the given club/faculty user."""
    my_club_id = user_club_id(user)
    my_club_name = normalize_text(user.get("club_name"))
    event_club_id = event.get("club_id")

    if event_club_id and my_club_id and event_club_id == my_club_id:
        return True

    # Older events have no club_id: fall back to the organizer name.
    if not event_club_id and my_club_name:
        return normalize_text(event.get("organizer")) == my_club_name

    return False


def active_faculty_for_club(club_id, club_name=""):
    """The active faculty account assigned to a club, if any."""
    if not club_id and not club_name:
        return None
    query = {"role": "faculty", "status": "active"}
    candidates = db.students.find(query)
    for faculty in candidates:
        if club_id and user_club_id(faculty) == club_id:
            return faculty
        if (
            club_name
            and normalize_text(faculty.get("club_name"))
            == normalize_text(club_name)
        ):
            return faculty
    return None


def resubmission_status(user_role, user):
    """Where an edited (resubmitted) event goes next."""
    if user_role == "club" and active_faculty_for_club(
        user_club_id(user), user.get("club_name")
    ):
        return "pending_faculty"
    return "pending_hod"


def notify_faculty_of_club(
    club_id, club_name, title, message, ntype, event_id,
    remarks="", reviewer_role="", skip_user_id=None,
):
    """Create an in-app notification for the club's active faculty member."""
    try:
        faculty = active_faculty_for_club(club_id, club_name)
        if not faculty:
            return
        faculty_id = str(faculty["_id"])
        if skip_user_id and faculty_id == str(skip_user_id):
            return
        db.notifications.insert_one({
            "title": title,
            "message": message,
            "type": ntype,
            "audience": "faculty",
            "recipient": faculty_id,
            "related_id": event_id,
            "remarks": remarks,
            "reviewer_role": reviewer_role,
            "is_read": False,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        print("FACULTY NOTIFICATION ERROR:", repr(e))


def registration_stats(event_ids):
    """
    Real registration numbers, read from the event_registrations
    collection (the single source of truth that student registration
    writes to).

    Returns (per_event_counts, total_registrations, unique_students).
    """
    per_event = {}
    students = set()
    total = 0
    if not event_ids:
        return per_event, 0, 0

    for reg in db.event_registrations.find(
        {"event_id": {"$in": list(event_ids)}, "status": "registered"},
        {"event_id": 1, "student_id": 1},
    ):
        per_event[reg["event_id"]] = per_event.get(reg["event_id"], 0) + 1
        students.add(str(reg.get("student_id", "")))
        total += 1

    return per_event, total, len(students)


def recent_club_registrations(event_ids, limit=100):
    """Latest registrations for the given events, with student details."""
    if not event_ids:
        return []

    regs = list(
        db.event_registrations.find(
            {"event_id": {"$in": list(event_ids)}, "status": "registered"}
        ).sort("registered_at", -1).limit(limit)
    )

    wanted = {str(r.get("student_id", "")) for r in regs}
    object_ids = []
    for value in wanted:
        try:
            object_ids.append(ObjectId(value))
        except Exception:
            pass

    directory = {}
    if wanted:
        for account in db.students.find(
            {"$or": [
                {"_id": {"$in": object_ids}},
                {"student_id": {"$in": list(wanted)}},
            ]},
            {"name": 1, "email": 1, "student_id": 1},
        ):
            record = {
                "name": account.get("name", ""),
                "email": account.get("email", ""),
            }
            directory[str(account["_id"])] = record
            if account.get("student_id"):
                directory[str(account["student_id"])] = record

    rows = []
    for reg in regs:
        student = directory.get(str(reg.get("student_id", "")), {})
        registered_at = reg.get("registered_at")
        rows.append({
            "id": str(reg["_id"]),
            "student_id": str(reg.get("student_id", "")),
            "name": student.get("name", ""),
            "email": student.get("email", ""),
            "event_id": reg.get("event_id"),
            "event_title": reg.get("event_title", ""),
            "registered_at": (
                registered_at.isoformat()
                if hasattr(registered_at, "isoformat")
                else str(registered_at or "")
            ),
        })
    return rows


# ------------------------------------------------------------
# HOD ROUTING: an event goes only to the HOD of its department
# ------------------------------------------------------------

def hod_review_block_reason(hod_user, event):
    """
    None when this HOD may review the event, otherwise the reason why not.

    - Event has an official department  -> only a HOD of that department.
    - Older event with no official department (blank or free text) -> any
      HOD, exactly as before, so nothing already in the pipeline is stuck.
    """
    event_department = canonical_department(event.get("department"))

    if event_department is None:
        return None

    hod_department = canonical_department(hod_user.get("department"))

    if hod_department is None:
        return (
            "Your account's department is not one of the official departments, "
            "so it cannot review department events. Please ask an Admin to correct it."
        )

    if hod_department != event_department:
        return (
            f"This event belongs to the {event_department} department and can "
            f"only be reviewed by its HOD."
        )

    return None


def get_approval_user(authorization: str):
    """Authenticate the reviewer and return their database account."""
    user_id = get_student_id_from_token(authorization)

    try:
        user = db.students.find_one({"_id": ObjectId(user_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    role = str(user.get("role", "")).lower()

    if role not in {"faculty", "hod", "dean", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to review events."
        )

    return user_id, user, role


@app.get("/api/approvals/events")
def get_approval_events(authorization: str = Header(None)):
    """Return the current reviewer's queue and review history."""
    user_id, user, role = get_approval_user(authorization)

    queue_status = {
        "faculty": "pending_faculty",
        "hod": "pending_hod",
        "dean": "pending_dean",
        "admin": "pending_admin",
    }[role]

    # Keep this simple initially; the dashboard can filter and search
    # the returned records. MongoDB remains the source of truth.
    documents = list(db.events.find().sort("_id", -1))

    queue = []
    reviewed = []

    for event in documents:
        event_id = str(event.get("_id", ""))
        status = event.get("approval_status")

        # Older events have no approval status. Do not place them
        # into a new approval queue automatically.
        # Faculty only see events of their own club.
        if role == "faculty" and not event_belongs_to_club(event, user):
            continue

        if status == queue_status:
            # HODs only get events of their own department
            if role != "hod" or hod_review_block_reason(user, event) is None:
                queue.append(json_safe(event))

        history = event.get("approval_history", [])
        if any(
            str(item.get("reviewer_id", "")) == user_id
            and item.get("reviewer_role") == role
            for item in history
        ):
            reviewed.append(json_safe(event))

    # Modifications of published events use the same queue (see event_modifications.py).
    try:
        modification_queue, modification_reviewed = (
            event_modifications.approval_items(user_id, user, role, json_safe)
        )
        queue = queue + modification_queue
        reviewed = reviewed + modification_reviewed
    except Exception as error:
        print("MODIFICATION QUEUE WARNING:", repr(error))

    return {
        "status": "success",
        "role": role,
        "queue": queue,
        "reviewed": reviewed,
    }


@app.post("/api/approvals/events/{event_id}/review")
def review_event(
    event_id: str,
    payload: dict,
    authorization: str = Header(None),
):
    """Record an HOD, Dean, or Admin decision for the current event."""
    user_id, user, role = get_approval_user(authorization)

    decision = str(payload.get("decision", "")).strip().lower()
    remarks = str(payload.get("remarks", "")).strip()

    if decision not in {"approved", "changes", "rejected"}:
        raise HTTPException(
            status_code=400,
            detail="Decision must be approved, changes, or rejected."
        )

    if decision in {"changes", "rejected"} and len(remarks) < 10:
        raise HTTPException(
            status_code=400,
            detail="Please provide at least 10 characters of remarks."
        )

    try:
        oid = ObjectId(event_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid event ID")

    # A decision on a modification of a published event is handled by
    # event_modifications.py (same HOD -> Dean -> Admin rules).
    if event_modifications.is_modification(oid):
        try:
            return event_modifications.review(
                oid, user_id, user, role, decision, remarks, payload
            )
        except EventModificationError as error:
            raise HTTPException(
                status_code=error.status, detail=error.message
            )

    event = db.events.find_one({"_id": oid})

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if role == "faculty" and not event_belongs_to_club(event, user):
        raise HTTPException(
            status_code=403,
            detail="You can only review events of your own club.",
        )

    if role == "hod":
        block_reason = hod_review_block_reason(user, event)
        if block_reason:
            raise HTTPException(status_code=403, detail=block_reason)

    expected_status = {
        "faculty": "pending_faculty",
        "hod": "pending_hod",
        "dean": "pending_dean",
        "admin": "pending_admin",
    }[role]

    if event.get("approval_status") != expected_status:
        raise HTTPException(
            status_code=409,
            detail="This event is not awaiting your review."
        )

    version = int(event.get("submission_version", 1))

    # Only an approval advances to the next review stage.
    if decision == "approved":
        next_status = {
            "faculty": "pending_hod",
            "hod": "pending_dean",
            "dean": "pending_admin",
            "admin": "published",
        }[role]
    elif decision == "changes":
        next_status = "changes_requested"
    else:
        next_status = "rejected"

    entry = {
        "reviewer_id": user_id,
        "reviewer_role": role,
        "reviewer_name": user.get("name", ""),
        "decision": decision,
        "remarks": remarks,
        "submission_version": version,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }

    set_fields = {"approval_status": next_status}

    # Final Admin approval publishes the event, so the Admin must have
    # chosen "Publish Event To" (or the event already has an audience).
    if role == "admin" and decision == "approved":
        set_fields["audience"] = audience_gate.final_audience_for_review(
            payload, user, event
        )

    result = db.events.update_one(
        {
            "_id": oid,
            "approval_status": expected_status,
            "submission_version": version,
        },
        {
            "$set": set_fields,
            "$push": {"approval_history": entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="The event changed while you were reviewing it. Refresh and try again."
        )

    # QR: final Admin approval publishes the event -> create attendance passes in the
    # background (does nothing for events that require registration)
    if role == "admin" and decision == "approved":
        qr_hook("on_event_published", event_id)

    # When the Dean approves, tell the admin an event is waiting for
    # final approval (shows up in the admin notification bell).
    if next_status == "pending_admin":
        try:
            db.notifications.insert_one({
                "title": "Event awaiting final approval",
                "message": (
                    f'"{event.get("title", "An event")}" was approved by '
                    f'{user.get("name", "the Dean")} and needs your approval.'
                ),
                "type": "event_approval",
                "recipient_role": "admin",
                "recipient": "admin",
                "related_id": event_id,
                "is_read": False,
                "created_at": datetime.now(timezone.utc),
            })
        except Exception as e:
            print("ADMIN APPROVAL NOTIFICATION ERROR:", repr(e))

    # Tell the club (event owner) that changes were requested / the event
    # was rejected, so they can open the event, edit it and resubmit.
    if decision in {"changes", "rejected"}:
        try:
            owner_id = event.get("created_by")
            if owner_id:
                reviewer_label = role.upper() if role == "hod" else role.capitalize()  # Faculty / Dean / Admin
                verb = (
                    "requested changes to"
                    if decision == "changes"
                    else "rejected"
                )
                db.notifications.insert_one({
                    "title": (
                        f"{reviewer_label} requested changes"
                        if decision == "changes"
                        else f"{reviewer_label} rejected your event"
                    ),
                    "message": (
                        f'The {reviewer_label} has {verb} '
                        f'"{event.get("title", "your event")}". '
                        "Please edit the event and resubmit."
                    ),
                    "type": "changes_requested",
                    "recipient": str(owner_id),
                    "related_id": event_id,
                    "remarks": remarks,
                    "reviewer_role": role,
                    "is_read": False,
                    "created_at": datetime.now(timezone.utc),
                })
        except Exception as e:
            # Must not undo the saved review decision.
            print("REVIEW NOTIFICATION ERROR:", repr(e))

    # Faculty approval: tell the coordinator it moved on to the HOD.
    if role == "faculty" and decision == "approved":
        try:
            if event.get("created_by"):
                db.notifications.insert_one({
                    "title": "Event forwarded to the HOD",
                    "message": (
                        f'Faculty approved "{event.get("title", "your event")}" '
                        "and forwarded it to the HOD."
                    ),
                    "type": "event_update",
                    "recipient": str(event["created_by"]),
                    "related_id": event_id,
                    "is_read": False,
                    "created_at": datetime.now(timezone.utc),
                })
        except Exception as e:
            print("FORWARD NOTIFICATION ERROR:", repr(e))

    # HOD / Dean / Admin decisions: tell the club's faculty member.
    if role in ("hod", "dean", "admin"):
        label = "HOD" if role == "hod" else role.capitalize()
        title_text = event.get("title", "an event")
        if decision == "approved":
            if role == "admin":
                ntype, ntitle = "event_published", "Event published"
                nmessage = f'The Admin approved "{title_text}". It is now published to students.'
            else:
                ntype, ntitle = "event_approved", f"{label} approved an event"
                nmessage = f'The {label} approved "{title_text}" and sent it to the next stage.'
        else:
            ntype, ntitle = "event_returned", f"{label} returned an event"
            nmessage = (
                f'The {label} '
                f'{"requested changes to" if decision == "changes" else "rejected"} '
                f'"{title_text}".'
            )
        notify_faculty_of_club(
            event.get("club_id"),
            "",
            ntitle,
            nmessage,
            ntype,
            event_id,
            remarks=remarks,
            reviewer_role=role,
            # The faculty member who owns the event already gets the
            # "changes requested" notification as its creator.
            skip_user_id=(
                event.get("created_by")
                if decision in ("changes", "rejected")
                else None
            ),
        )

    return {
        "status": "success",
        "message": "Review decision saved.",
        "event_id": event_id,
        "decision": decision,
        "approval_status": next_status,
        "submission_version": version,
    }


# ============================================================
# FACULTY DASHBOARD API
# ============================================================
# Faculty are assigned to one club. They see every event of that
# club (their own and the coordinators'), review coordinator
# submissions, and forward approved ones to the HOD.
# ============================================================


def get_faculty_user(authorization: str):
    """Authenticate and return an active faculty account."""
    user_id = get_student_id_from_token(authorization)

    try:
        user = db.students.find_one({"_id": ObjectId(user_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if str(user.get("role", "")).lower() != "faculty":
        raise HTTPException(
            status_code=403, detail="Faculty account required."
        )

    if user.get("status", "active") != "active":
        raise HTTPException(
            status_code=403,
            detail="Your faculty account is pending admin approval.",
        )

    return user_id, user


def parse_event_date(value):
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def club_events_for(user):
    """All events of the faculty's club, newest first."""
    return [
        event
        for event in db.events.find().sort("_id", -1)
        if event_belongs_to_club(event, user)
    ]


def creator_names(events):
    ids = set()
    for event in events:
        try:
            ids.add(ObjectId(str(event.get("created_by"))))
        except Exception:
            pass
    names = {}
    if ids:
        for account in db.students.find(
            {"_id": {"$in": list(ids)}}, {"name": 1, "role": 1}
        ):
            names[str(account["_id"])] = account.get("name", "")
    return names


def last_updated(event):
    history = event.get("approval_history") or []
    if history and history[-1].get("reviewed_at"):
        return history[-1]["reviewed_at"]
    stamp = event.get("updated_at") or event.get("created_at")
    return stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp or "")


def faculty_event_row(event, names):
    row = json_safe(event)
    creator_id = str(event.get("created_by", ""))
    row["id"] = str(event.get("_id", ""))
    row["created_by_name"] = names.get(creator_id, "")
    row["created_by_role"] = event.get("created_by_role", "")
    row["last_updated"] = last_updated(event)
    return row


def faculty_status_counts(events):
    today = datetime.now(timezone.utc).date()
    counts = {
        "total": len(events),
        "pendingFacultyReviews": 0,
        "awaitingHod": 0,
        "awaitingDean": 0,
        "awaitingAdmin": 0,
        "returnedForCorrections": 0,
        "published": 0,
        "upcoming": 0,
    }
    for event in events:
        status = event.get("approval_status")
        if status == "pending_faculty":
            counts["pendingFacultyReviews"] += 1
        elif status == "pending_hod":
            counts["awaitingHod"] += 1
        elif status == "pending_dean":
            counts["awaitingDean"] += 1
        elif status == "pending_admin":
            counts["awaitingAdmin"] += 1
        elif status in ("changes_requested", "rejected"):
            counts["returnedForCorrections"] += 1
        elif status == "published":
            counts["published"] += 1
            event_date = parse_event_date(event.get("date"))
            if event_date and event_date >= today:
                counts["upcoming"] += 1
    return counts


@app.get("/api/faculty/dashboard")
def faculty_dashboard(authorization: str = Header(None)):
    """Club overview, KPI counts, review queue and recent activity."""
    user_id, user = get_faculty_user(authorization)

    events = club_events_for(user)
    names = creator_names(events)
    club_id = user_club_id(user)

    coordinators = []
    for account in db.students.find({"role": "club", "status": "active"}):
        if (
            user_club_id(account) == club_id
            or normalize_text(account.get("club_name"))
            == normalize_text(user.get("club_name"))
        ):
            coordinators.append({
                "id": str(account["_id"]),
                "name": account.get("name", ""),
                "email": account.get("email", ""),
                "position": account.get("position", ""),
            })

    activity = []
    for event in events:
        for item in event.get("approval_history") or []:
            activity.append({
                "event_id": str(event["_id"]),
                "event_title": event.get("title", ""),
                "reviewer_role": item.get("reviewer_role"),
                "reviewer_name": item.get("reviewer_name"),
                "decision": item.get("decision"),
                "remarks": item.get("remarks", ""),
                "submission_version": item.get("submission_version"),
                "reviewed_at": item.get("reviewed_at"),
            })
    activity.sort(key=lambda a: a.get("reviewed_at") or "", reverse=True)

    pending = [
        faculty_event_row(e, names)
        for e in events
        if e.get("approval_status") == "pending_faculty"
    ]

    # Modifications of published events also wait for this faculty's review.
    try:
        faculty_modification_events = event_modifications.faculty_review_events(user)
    except Exception as error:
        print("FACULTY MODIFICATION COUNT WARNING:", repr(error))
        faculty_modification_events = []
    pending = pending + [
        faculty_event_row(e, names) for e in faculty_modification_events
    ]
    returned = [
        faculty_event_row(e, names)
        for e in events
        if e.get("approval_status") in ("changes_requested", "rejected")
    ]

    return {
        "status": "success",
        "faculty": {
            "id": user_id,
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "department": user.get("department", ""),
            "role": "faculty",
        },
        "club": {
            "id": club_id,
            "name": user.get("club_name", ""),
            "category": user.get("club_category", ""),
            "college": user.get("college_name", ""),
            "description": user.get("club_description", ""),
        },
        "coordinators": coordinators,
        "kpis": {
            **faculty_status_counts(events),
            "pendingFacultyReviews": faculty_status_counts(events)[
                "pendingFacultyReviews"
            ] + len(faculty_modification_events),
            "unreadNotifications": db.notifications.count_documents({
                "recipient": user_id,
                "audience": "faculty",
                "is_read": False,
            }),
        },
        "pendingReviews": pending,
        "returnedEvents": returned,
        "recentActivity": activity[:10],
    }


@app.get("/api/faculty/events")
def faculty_events(
    q: str = "",
    creator: str = "",          # "faculty", "coordinator" or a user id
    category: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    sort: str = "created",       # created | date | title | updated
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
    authorization: str = Header(None),
):
    """Every event of the faculty's club with search, filters and paging."""
    user_id, user = get_faculty_user(authorization)

    events = club_events_for(user)
    names = creator_names(events)

    wanted_statuses = [x.strip() for x in status.split(",") if x.strip()]
    text = normalize_text(q)
    start = parse_event_date(date_from) if date_from else None
    end = parse_event_date(date_to) if date_to else None

    def keep(event):
        if text and text not in normalize_text(event.get("title")):
            return False
        creator_role = event.get("created_by_role", "")
        if creator == "faculty" and creator_role != "faculty":
            return False
        if creator == "coordinator" and creator_role != "club":
            return False
        if creator not in ("", "faculty", "coordinator") and str(
            event.get("created_by")
        ) != creator:
            return False
        if category and normalize_text(event.get("category")) != normalize_text(category):
            return False
        if wanted_statuses and event.get("approval_status") not in wanted_statuses:
            return False
        event_date = parse_event_date(event.get("date"))
        if start and (not event_date or event_date < start):
            return False
        if end and (not event_date or event_date > end):
            return False
        return True

    rows = [faculty_event_row(e, names) for e in events if keep(e)]

    # Modifications of published events waiting for this faculty's review.
    if "pending_faculty" in wanted_statuses:
        try:
            for modification_event in event_modifications.faculty_review_events(user):
                if not text or text in normalize_text(modification_event.get("title")):
                    rows.append(faculty_event_row(modification_event, names))
        except Exception as error:
            print("FACULTY MODIFICATION LIST WARNING:", repr(error))

    sort_keys = {
        "created": lambda r: str(r.get("created_at") or ""),
        "date": lambda r: str(r.get("date") or ""),
        "title": lambda r: normalize_text(r.get("title")),
        "updated": lambda r: str(r.get("last_updated") or ""),
    }
    rows.sort(
        key=sort_keys.get(sort, sort_keys["created"]),
        reverse=(order != "asc"),
    )

    page_size = max(1, min(int(page_size), 100))
    page = max(1, int(page))
    total = len(rows)
    begin = (page - 1) * page_size

    return {
        "status": "success",
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "events": rows[begin:begin + page_size],
    }


@app.get("/api/faculty/analytics")
def faculty_analytics(authorization: str = Header(None)):
    """Aggregates for the faculty's club, from real database records only."""
    user_id, user = get_faculty_user(authorization)

    events = club_events_for(user)
    event_ids = [str(e["_id"]) for e in events]
    today = datetime.now(timezone.utc).date()

    by_category = {}
    by_status = {}
    by_month = {}
    upcoming = completed = 0

    for event in events:
        category = event.get("category") or "Uncategorized"
        by_category[category] = by_category.get(category, 0) + 1

        status = event.get("approval_status") or "no_status"
        by_status[status] = by_status.get(status, 0) + 1

        created = event.get("created_at")
        if hasattr(created, "strftime"):
            key = created.strftime("%Y-%m")
            by_month[key] = by_month.get(key, 0) + 1

        event_date = parse_event_date(event.get("date"))
        if event.get("approval_status") == "published" and event_date:
            if event_date >= today:
                upcoming += 1
            else:
                completed += 1

    registrations = (
        db.event_registrations.count_documents(
            {"event_id": {"$in": event_ids}, "status": "registered"}
        )
        if event_ids
        else 0
    )
    attended = (
        db.attendance.count_documents({"event_id": {"$in": event_ids}})
        if event_ids
        else 0
    )

    # Registrations and attendance per event (for the club's own events only)
    reg_map, att_map = {}, {}
    if event_ids:
        for row in db.event_registrations.aggregate([
            {"$match": {"event_id": {"$in": event_ids}, "status": "registered"}},
            {"$group": {"_id": "$event_id", "n": {"$sum": 1}}},
        ]):
            reg_map[row["_id"]] = row["n"]
        for row in db.attendance.aggregate([
            {"$match": {"event_id": {"$in": event_ids}}},
            {"$group": {"_id": "$event_id", "n": {"$sum": 1}}},
        ]):
            att_map[row["_id"]] = row["n"]

    by_event = []
    for event in events:
        eid = str(event["_id"])
        regs = reg_map.get(eid, 0)
        att = att_map.get(eid, 0)
        if regs == 0 and att == 0:
            continue
        try:
            capacity = int(event.get("capacity"))
        except (TypeError, ValueError):
            capacity = None
        by_event.append({
            "id": eid,
            "title": event.get("title", ""),
            "date": event.get("date", ""),
            "capacity": capacity,
            "registrations": regs,
            "attendance": att,
            "attendanceRate": round(att * 100 / regs, 1) if regs else None,
            "fillRate": (
                round(regs * 100 / capacity, 1) if capacity else None
            ),
        })
    by_event.sort(key=lambda row: row["registrations"], reverse=True)

    return {
        "status": "success",
        "totals": {
            "events": len(events),
            "registrations": registrations,
            "attendance": attended,
            "attendanceRate": (
                round(attended * 100 / registrations, 1)
                if registrations
                else None
            ),
            "upcoming": upcoming,
            "completed": completed,
        },
        "byCategory": [
            {"label": k, "count": v} for k, v in sorted(by_category.items())
        ],
        "byStatus": [
            {"label": k, "count": v} for k, v in sorted(by_status.items())
        ],
        "activityByMonth": [
            {"month": k, "count": v} for k, v in sorted(by_month.items())
        ],
        "byEvent": by_event[:15],
    }


# ------------------------------------------------------------
# Faculty notifications
# ------------------------------------------------------------

def faculty_notification_row(n):
    created = n.get("created_at")
    return {
        "id": str(n["_id"]),
        "title": n.get("title", ""),
        "message": n.get("message", ""),
        "type": n.get("type", ""),
        "is_read": bool(n.get("is_read", False)),
        "event_id": n.get("related_id"),
        "remarks": n.get("remarks", ""),
        "reviewer_role": n.get("reviewer_role", ""),
        "created_at": (
            created.isoformat() if hasattr(created, "isoformat") else str(created or "")
        ),
    }


@app.get("/api/faculty/notifications")
def faculty_notifications(authorization: str = Header(None)):
    user_id, user = get_faculty_user(authorization)
    rows = list(
        db.notifications.find(
            {"recipient": user_id, "audience": "faculty"}
        ).sort("created_at", -1).limit(100)
    )
    return {
        "status": "success",
        "unread": sum(1 for n in rows if not n.get("is_read")),
        "notifications": [faculty_notification_row(n) for n in rows],
    }


@app.post("/api/faculty/notifications/read-all")
def faculty_notifications_read_all(authorization: str = Header(None)):
    user_id, user = get_faculty_user(authorization)
    result = db.notifications.update_many(
        {"recipient": user_id, "audience": "faculty", "is_read": False},
        {"$set": {"is_read": True}},
    )
    return {"status": "success", "updated": result.modified_count}


@app.post("/api/faculty/notifications/{notification_id}/read")
def faculty_notification_read(
    notification_id: str, authorization: str = Header(None)
):
    user_id, user = get_faculty_user(authorization)
    try:
        oid = ObjectId(notification_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid notification ID")
    result = db.notifications.update_one(
        {"_id": oid, "recipient": user_id, "audience": "faculty"},
        {"$set": {"is_read": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


# ------------------------------------------------------------
# Faculty review history (decisions + edits across all club events)
# ------------------------------------------------------------

@app.get("/api/faculty/review-history")
def faculty_review_history(
    q: str = "",
    kind: str = "",        # "review" | "edit" | "" (both)
    role: str = "",        # faculty | club | hod | dean | admin
    decision: str = "",    # approved | changes | rejected (reviews only)
    page: int = 1,
    page_size: int = 15,
    authorization: str = Header(None),
):
    user_id, user = get_faculty_user(authorization)

    text = normalize_text(q)
    items = []

    for event in club_events_for(user):
        title = event.get("title", "")
        if text and text not in normalize_text(title):
            continue
        event_id = str(event["_id"])

        if kind in ("", "review"):
            for entry in event.get("approval_history") or []:
                if role and entry.get("reviewer_role") != role:
                    continue
                if decision and entry.get("decision") != decision:
                    continue
                items.append({
                    "type": "review",
                    "event_id": event_id,
                    "event_title": title,
                    "role": entry.get("reviewer_role"),
                    "name": entry.get("reviewer_name"),
                    "decision": entry.get("decision"),
                    "remarks": entry.get("remarks", ""),
                    "submission_version": entry.get("submission_version"),
                    "at": entry.get("reviewed_at") or "",
                })

        if kind in ("", "edit") and not decision:
            for entry in event.get("edit_history") or []:
                if role and entry.get("editor_role") != role:
                    continue
                items.append({
                    "type": "edit",
                    "event_id": event_id,
                    "event_title": title,
                    "role": entry.get("editor_role"),
                    "name": entry.get("editor_name"),
                    "changed_fields": entry.get("changed_fields") or [],
                    "status_after": entry.get("status_after"),
                    "submission_version": entry.get("submission_version"),
                    "at": entry.get("edited_at") or "",
                })

    items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)

    page_size = max(1, min(int(page_size), 100))
    page = max(1, int(page))
    total = len(items)
    begin = (page - 1) * page_size

    return {
        "status": "success",
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "items": items[begin:begin + page_size],
    }


# ------------------------------------------------------------
# Club analytics (used by the coordinator's Analytics page)
# ------------------------------------------------------------

@app.get("/api/club/analytics")
def club_analytics(authorization: str = Header(None)):
    """Totals for the logged-in club, from real registration records."""
    data = club_dashboard(authorization)
    kpis = data["kpis"]
    return {
        "total_events": kpis["totalEvents"],
        "total_registrations": kpis["totalRegistrations"],
        "total_students": kpis["totalParticipants"],
        "events": data["upcomingEvents"],
        "registrations": data["recentRegistrations"],
    }



# ============================================================
# ADMIN REPORTS (PDF): department-wise and club-wise
# ------------------------------------------------------------
# Admin only. Only PUBLISHED events are included (plus older events
# that have no approval_status, which students already see).
# Registration data is reported as NUMBERS only: no student names,
# e-mails or other personal details are ever written into the PDF.
#
# Needs:   pip install reportlab
# ------------------------------------------------------------
#   GET /api/admin/reports/options
#   GET /api/admin/reports/department?department=<name>&date_from=&date_to=
#   GET /api/admin/reports/club?club_id=<id>&date_from=&date_to=
# Leave department / club_id empty (or "all") for ONE PDF that holds
# every department / club.
# ============================================================

from collections import Counter
from fastapi.responses import Response
from xml.sax.saxutils import escape as _report_escape
import io as _report_io

REPORT_PUBLISHED_FILTER = {
    "$or": [
        {"approval_status": "published"},
        {"approval_status": {"$exists": False}},
    ]
}

REPORT_NO_DEPARTMENT = "Unassigned / not specified"
_REPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REPORT_PAGE_WIDTH = 842 - 72 - 12  # A4 landscape minus margins and the frame's 6pt padding
_report_font_cache = {}


# ------------------------------------------------------------
# small helpers
# ------------------------------------------------------------

def _report_require_admin(authorization):
    admin_id = get_student_id_from_token(authorization)

    try:
        admin_user = db.students.find_one({"_id": ObjectId(admin_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid admin user ID")

    if not admin_user:
        raise HTTPException(status_code=401, detail="Admin account not found.")

    if admin_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    return admin_user


def _report_clean_date(value, label):
    value = (value or "").strip()

    if not value:
        return ""

    if not _REPORT_DATE_RE.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"{label} must be in YYYY-MM-DD format.",
        )

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"{label} is not a valid date."
        )

    return value


def _report_period(date_from, date_to):
    date_from = _report_clean_date(date_from, "Start date")
    date_to = _report_clean_date(date_to, "End date")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="Start date must not be after the end date.",
        )

    return date_from, date_to


def _report_period_text(date_from, date_to):
    if date_from and date_to:
        return f"Event dates: {date_from} to {date_to}"
    if date_from:
        return f"Event dates: from {date_from}"
    if date_to:
        return f"Event dates: up to {date_to}"
    return "Event dates: all"


def _report_key(value):
    """
    Grouping key. Official departments (and their known older spellings, e.g.
    'Computer Science' -> 'CSE') share one key; anything else is grouped
    case/space-insensitively.
    """
    official = canonical_department(value)
    if official:
        return official.lower()
    return " ".join(str(value or "").split()).lower()


def _report_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _report_pct(part, whole):
    return f"{round(part * 100 / whole)}%" if whole else "—"


def _report_filename(prefix, name):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "All")).strip("_")[:60]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"CampusPathAI_{prefix}_{safe or 'All'}_{stamp}.pdf"


# ------------------------------------------------------------
# data loading: a handful of queries, no per-row lookups
# ------------------------------------------------------------

def _report_load(date_from="", date_to=""):
    events = []

    for event in db.events.find(REPORT_PUBLISHED_FILTER):
        event_date = str(event.get("date") or "")[:10]

        if date_from or date_to:
            if not _REPORT_DATE_RE.match(event_date):
                continue
            if date_from and event_date < date_from:
                continue
            if date_to and event_date > date_to:
                continue

        events.append(event)

    event_ids = [str(event["_id"]) for event in events]
    id_filter = {"$in": event_ids + [event["_id"] for event in events]}

    registrations = (
        list(db.event_registrations.find({"event_id": id_filter}))
        if events else []
    )
    attendance = (
        list(db.attendance.find({"event_id": id_filter}))
        if events else []
    )

    # ---- students' departments (one query) ----
    student_ids = set()
    for row in registrations + attendance:
        if row.get("student_id"):
            student_ids.add(str(row["student_id"]))

    object_ids = []
    for student_id in student_ids:
        try:
            object_ids.append(ObjectId(student_id))
        except Exception:
            continue

    student_department = {}
    if object_ids:
        for student in db.students.find(
            {"_id": {"$in": object_ids}}, {"department": 1}
        ):
            student_department[str(student["_id"])] = " ".join(
                str(student.get("department") or "").split()
            )

    # ---- per-event numbers ----
    active = {event_id: set() for event_id in event_ids}
    cancelled = {event_id: set() for event_id in event_ids}
    present = {event_id: set() for event_id in event_ids}

    for row in registrations:
        event_id = str(row.get("event_id"))
        student_id = str(row.get("student_id") or "")

        if event_id not in active or not student_id:
            continue

        if str(row.get("status") or "").lower() == "cancelled":
            cancelled[event_id].add(student_id)
        else:
            active[event_id].add(student_id)

    for row in attendance:
        event_id = str(row.get("event_id"))
        student_id = str(row.get("student_id") or "")

        if event_id not in present or not student_id:
            continue

        if str(row.get("status") or "present").lower() == "present":
            present[event_id].add(student_id)

    # ---- one display spelling per department (the most common one) ----
    spellings = {}

    def note(raw):
        raw = " ".join(str(raw or "").split())
        raw = canonical_department(raw) or raw
        if raw:
            spellings.setdefault(_report_key(raw), Counter())[raw] += 1

    for event in events:
        note(event.get("department"))
    for student_id in student_ids:
        note(student_department.get(student_id))

    # ---- older events have no Department of their own: use the creator's ----
    creator_department = {}
    creator_ids = {
        str(event.get("created_by"))
        for event in events
        if not " ".join(str(event.get("department") or "").split())
        and event.get("created_by")
        and str(event.get("created_by_role") or "").lower() != "admin"
    }

    creator_objects = []
    for creator_id in creator_ids:
        try:
            creator_objects.append(ObjectId(creator_id))
        except Exception:
            continue

    if creator_objects:
        for creator in db.students.find(
            {"_id": {"$in": creator_objects}}, {"department": 1}
        ):
            creator_department[str(creator["_id"])] = " ".join(
                str(creator.get("department") or "").split()
            )

    for department in creator_department.values():
        note(department)

    names = {key: counts.most_common(1)[0][0] for key, counts in spellings.items()}

    records = []
    for event in events:
        event_id = str(event["_id"])
        capacity = _report_int(event.get("capacity"))
        registered = len(active[event_id])

        own_department = " ".join(str(event.get("department") or "").split())
        inferred = False
        department_key = _report_key(own_department)

        if not department_key:
            fallback = creator_department.get(str(event.get("created_by")), "")
            if fallback:
                department_key = _report_key(fallback)
                inferred = True

        records.append({
            "id": event_id,
            "raw": event,
            "title": str(event.get("title") or "Untitled event"),
            "date": str(event.get("date") or "—"),
            "category": str(event.get("category") or "Other"),
            "mode": str(event.get("event_mode") or "—"),
            "organizer": str(event.get("organizer") or "—"),
            "dept_key": department_key,
            "dept_inferred": inferred,
            "capacity": capacity,
            "registered": registered,
            "cancelled": len(cancelled[event_id] - active[event_id]),
            "attended": len(present[event_id]),
            "reg_students": active[event_id],
            "att_students": present[event_id],
        })

    records.sort(key=lambda rec: (rec["date"], rec["title"]))

    return {
        "records": records,
        "names": names,
        "student_department": student_department,
    }


def _report_dept_label(data, key):
    return data["names"].get(key) or REPORT_NO_DEPARTMENT


def _report_student_key(data, student_id):
    return _report_key(data["student_department"].get(student_id, ""))


def _report_totals(records):
    capacity_total = sum(rec["capacity"] for rec in records if rec["capacity"] > 0)
    registered_for_capacity = sum(
        rec["registered"] for rec in records if rec["capacity"] > 0
    )

    return {
        "events": len(records),
        "registered": sum(rec["registered"] for rec in records),
        "cancelled": sum(rec["cancelled"] for rec in records),
        "attended": sum(rec["attended"] for rec in records),
        "fill": _report_pct(registered_for_capacity, capacity_total),
    }


def _report_categories(records):
    table = {}
    for rec in records:
        row = table.setdefault(rec["category"], [0, 0])
        row[0] += 1
        row[1] += rec["registered"]

    return sorted(
        ([name, events, regs] for name, (events, regs) in table.items()),
        key=lambda row: (-row[1], row[0]),
    )


def _report_department_stats(data, key):
    """Events a department ORGANISED, and how its STUDENTS took part."""
    organised = [rec for rec in data["records"] if rec["dept_key"] == key]

    per_event = []
    students = set()
    total_regs = 0
    total_att = 0

    for rec in data["records"]:
        regs = [s for s in rec["reg_students"] if _report_student_key(data, s) == key]
        atts = [s for s in rec["att_students"] if _report_student_key(data, s) == key]

        if regs or atts:
            per_event.append((rec, len(regs), len(atts)))

        total_regs += len(regs)
        total_att += len(atts)
        students.update(regs)

    return {
        "organised": organised,
        "per_event": per_event,
        "student_regs": total_regs,
        "student_att": total_att,
        "students": len(students),
    }


# ------------------------------------------------------------
# PDF building blocks (reportlab is imported only when needed)
# ------------------------------------------------------------

def _report_import_reportlab():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF reports need the 'reportlab' package. "
                "Run: pip install reportlab, then restart the backend."
            ),
        )


def _report_fonts():
    """Arial / DejaVu when the machine has them (more characters), else Helvetica."""
    if "fonts" in _report_font_cache:
        return _report_font_cache["fonts"]

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]

    fonts = ("Helvetica", "Helvetica-Bold")

    for regular, bold in candidates:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("CPReport", regular))
                pdfmetrics.registerFont(TTFont("CPReport-Bold", bold))
                fonts = ("CPReport", "CPReport-Bold")
                break
            except Exception:
                continue

    _report_font_cache["fonts"] = fonts
    return fonts


def _report_styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    font, bold = _report_fonts()
    ink = colors.HexColor("#1f2a5a")

    return {
        "title": ParagraphStyle("rt", fontName=bold, fontSize=21, leading=26, textColor=ink, spaceAfter=4),
        "h1": ParagraphStyle("rh1", fontName=bold, fontSize=15, leading=19, textColor=ink, spaceBefore=4, spaceAfter=4),
        "h2c": ParagraphStyle("rh2c", fontName=bold, fontSize=10.5, leading=14, textColor=ink, spaceAfter=3),
        "h2": ParagraphStyle("rh2", fontName=bold, fontSize=11, leading=14, textColor=ink, spaceBefore=12, spaceAfter=5),
        "body": ParagraphStyle("rb", fontName=font, fontSize=9, leading=12, textColor=colors.HexColor("#333333")),
        "note": ParagraphStyle("rn", fontName=font, fontSize=8, leading=11, spaceBefore=4, textColor=colors.HexColor("#6b7280")),
        "cell": ParagraphStyle("rc", fontName=font, fontSize=8, leading=10),
        "cellr": ParagraphStyle("rcr", fontName=font, fontSize=8, leading=10, alignment=TA_RIGHT),
        "cellb": ParagraphStyle("rcb", fontName=bold, fontSize=8, leading=10),
        "cellrb": ParagraphStyle("rcrb", fontName=bold, fontSize=8, leading=10, alignment=TA_RIGHT),
        "headc": ParagraphStyle("rhc", fontName=bold, fontSize=8.5, leading=11, textColor=colors.white, alignment=TA_CENTER),
        "head": ParagraphStyle("rhd", fontName=bold, fontSize=8, leading=10, textColor=colors.white),
        "headr": ParagraphStyle("rhdr", fontName=bold, fontSize=8, leading=10, textColor=colors.white, alignment=TA_RIGHT),
        "kpi_value": ParagraphStyle("rkv", fontName=bold, fontSize=17, leading=21, textColor=ink),
        "kpi_label": ParagraphStyle("rkl", fontName=font, fontSize=8, leading=10, textColor=colors.HexColor("#6b7280")),
    }


def _report_p(text, style):
    from reportlab.platypus import Paragraph
    return Paragraph(_report_escape(str(text)), style)


def _report_table(styles, header, rows, fractions, right=(), groups=None, total_row=False):
    """Header + rows table. `fractions` split the page width; `right` = numeric columns.
    `groups` = [(label, first_col, last_col)] adds a spanning header row above the header;
    `total_row` makes the last row bold."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    data = []
    group_rows = 1 if groups else 0

    if groups:
        group_row = [""] * len(header)
        for label, first, _last in groups:
            group_row[first] = _report_p(label, styles["headc"])
        data.append(group_row)

    data.append([
        _report_p(h, styles["headr" if i in right else "head"])
        for i, h in enumerate(header)
    ])

    for index, row in enumerate(rows):
        is_total = total_row and index == len(rows) - 1
        data.append([
            _report_p(
                c,
                styles[("cellrb" if is_total else "cellr") if i in right
                       else ("cellb" if is_total else "cell")],
            )
            for i, c in enumerate(row)
        ])

    widths = [_REPORT_PAGE_WIDTH * f for f in fractions]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, group_rows), (-1, group_rows), colors.HexColor("#3b4a9c")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, group_rows), (-1, -1), 0.25, colors.HexColor("#c9cfe6")),
        ("ROWBACKGROUNDS", (0, group_rows + 1), (-1, -1), [colors.white, colors.HexColor("#f3f5fc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]

    palette = ["#1f2a5a", "#0f6b5c"]
    for number, (_label, first, last) in enumerate(groups or []):
        commands += [
            ("SPAN", (first, 0), (last, 0)),
            ("BACKGROUND", (first, 0), (last, 0), colors.HexColor(palette[number % 2])),
            ("BOX", (first, 0), (last, 0), 0.25, colors.white),
        ]

    if total_row and rows:
        commands += [
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e3e7f7")),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#3b4a9c")),
        ]

    table.setStyle(TableStyle(commands))
    return table


def _report_kpis(styles, items):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    cells = [[
        [_report_p(value, styles["kpi_value"]), _report_p(label, styles["kpi_label"])]
        for label, value in items
    ]]

    table = Table(cells, colWidths=[_REPORT_PAGE_WIDTH / len(items)] * len(items))
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9cfe6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9cfe6")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8fd")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def _report_build(title, story_builder):
    """Run `story_builder(styles)` and return the finished PDF as bytes."""
    _report_import_reportlab()

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate

    styles = _report_styles()
    story = story_builder(styles)

    font, _bold = _report_fonts()
    buffer = _report_io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillGray(0.45)
        canvas.drawString(36, 20, f"CampusPath AI  |  {title}")
        canvas.drawRightString(842 - 36, 20, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36,
        title=title,
        author="CampusPath AI",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)

    return buffer.getvalue()


def _report_cover(styles, title, subtitle_lines):
    from reportlab.platypus import Spacer

    parts = [_report_p(title, styles["title"])]

    for line in subtitle_lines:
        parts.append(_report_p(line, styles["body"]))

    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    parts.append(_report_p(f"Generated on {generated}", styles["note"]))
    parts.append(Spacer(1, 10))
    return parts


INFERRED_NOTE = (
    "\u2020 This older event has no Department of its own, so it is credited to the "
    "department of the account that created it."
)


def _report_title(rec):
    return rec["title"] + (" \u2020" if rec["dept_inferred"] else "")


def _report_events_table(styles, rows, include_department):
    header = ["Event", "Date", "Category", "Mode"]
    fractions = [0.21, 0.085, 0.11, 0.075]

    if include_department:
        header.append("Department")
        fractions.append(0.13)
    else:
        header.append("Organiser")
        fractions.append(0.13)

    header += ["Capacity", "Registered", "Cancelled", "Attended", "Fill"]
    fractions += [0.08, 0.09, 0.085, 0.085, 0.05]

    return _report_table(
        styles, header, rows, fractions, right=range(5, len(header))
    )


# ------------------------------------------------------------
# department report
# ------------------------------------------------------------

def _report_legend(styles):
    """Plain-language 'how to read this' box shown on the department cover."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    lines = [
        _report_p("How to read this report", styles["h2c"]),
        _report_p(
            "Every registration is shown in TWO ways, so a department can appear "
            "in both parts of a page:",
            styles["body"],
        ),
        _report_p(
            "A.  Events HELD by the department: the events this department "
            "organised, and the people who registered for them (from any department).",
            styles["body"],
        ),
        _report_p(
            "B.  The department's OWN STUDENTS: how many of this department's "
            "students registered for events, whichever department or club held them.",
            styles["body"],
        ),
        _report_p(
            "Example: a CSE student registering for a robotics club event counts "
            "under B for CSE, and under A for the department that holds that event. "
            "A department with 0 events held can therefore still have students "
            "registered. Both parts add up to the same total number of registrations.",
            styles["body"],
        ),
    ]

    table = Table([[lines]], colWidths=[_REPORT_PAGE_WIDTH], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#3b4a9c")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f8fd")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def _report_department_section(styles, data, key, page_break=True):
    from reportlab.platypus import PageBreak, Spacer

    stats = _report_department_stats(data, key)
    totals = _report_totals(stats["organised"])
    label = _report_dept_label(data, key)

    story = [PageBreak()] if page_break else []
    story += [
        _report_p(f"Department: {label}", styles["h1"]),
        _report_p(
            "A.  Events held by this department "
            "(and everyone who registered for them)",
            styles["h2"],
        ),
        _report_kpis(styles, [
            ("Events held", totals["events"]),
            ("Registrations received", totals["registered"]),
            ("Attended these events", totals["attended"]),
            ("Seat fill rate", totals["fill"]),
        ]),
        Spacer(1, 6),
    ]

    if not stats["organised"]:
        story.append(_report_p(
            "No published events are credited to this department in the selected "
            "period, so there is nothing to show here.",
            styles["body"],
        ))
    else:
        story.append(_report_table(
            styles, ["Category", "Events", "Registrations"],
            _report_categories(stats["organised"]),
            [0.4, 0.15, 0.15], right=(1, 2),
        ))
        story.append(Spacer(1, 8))

        rows = [[
            _report_title(rec), rec["date"], rec["category"], rec["mode"],
            rec["organizer"], rec["capacity"] or "—", rec["registered"],
            rec["cancelled"], rec["attended"],
            _report_pct(rec["registered"], rec["capacity"]),
        ] for rec in stats["organised"]]

        story.append(_report_events_table(styles, rows, include_department=False))

        if any(rec["dept_inferred"] for rec in stats["organised"]):
            story.append(_report_p(INFERRED_NOTE, styles["note"]))

    story.append(_report_p(
        "B.  This department's own students "
        "(registered for events held by any department or club)",
        styles["h2"],
    ))
    story.append(_report_kpis(styles, [
        ("Students who registered", stats["students"]),
        ("Registrations made", stats["student_regs"]),
        ("Attended", stats["student_att"]),
    ]))
    story.append(Spacer(1, 6))

    if not stats["per_event"]:
        story.append(_report_p(
            "No students of this department registered for a published event in "
            "the selected period.",
            styles["body"],
        ))
    else:
        if not stats["organised"]:
            story.append(_report_p(
                "This department held no events in the selected period, but its "
                "students took part in the events below, which were held by others.",
                styles["body"],
            ))
            story.append(Spacer(1, 6))

        rows = [[
            _report_title(rec), rec["date"], rec["organizer"], regs, atts,
        ] for rec, regs, atts in stats["per_event"]]

        story.append(_report_table(
            styles,
            ["Event", "Date", "Held by", "Students registered", "Students attended"],
            rows, [0.38, 0.11, 0.21, 0.15, 0.15], right=(3, 4),
        ))

        if any(rec["dept_inferred"] for rec, _r, _a in stats["per_event"]):
            story.append(_report_p(INFERRED_NOTE, styles["note"]))

    return story


def _build_department_report(department, date_from, date_to):
    data = _report_load(date_from, date_to)
    wanted = _report_key(department)
    everything = wanted in ("", "all")

    keys = set()
    for rec in data["records"]:
        keys.add(rec["dept_key"])
    for rec in data["records"]:
        for student_id in rec["reg_students"] | rec["att_students"]:
            keys.add(_report_student_key(data, student_id))

    if everything:
        selected = sorted(keys, key=lambda k: (k == "", _report_dept_label(data, k).lower()))
        title = "Department-wise Report - All Departments"
        scope_line = "Scope: all departments"
        file_name = "All"
    else:
        selected = [wanted]
        label = _report_dept_label(data, wanted) if wanted in data["names"] else str(department).strip()
        title = f"Department Report - {label}"
        scope_line = f"Scope: {label}"
        file_name = label

    def story_builder(styles):
        from reportlab.platypus import Spacer

        story = _report_cover(styles, title, [
            scope_line,
            "Published events only.  " + _report_period_text(date_from, date_to),
            "Registration figures are counts of students; no personal details are included.",
        ])

        if everything:
            overall = _report_totals(data["records"])
            story.append(_report_kpis(styles, [
                ("Published events (all)", overall["events"]),
                ("Registrations (all)", overall["registered"]),
                ("Attended (all)", overall["attended"]),
                ("Seat fill rate", overall["fill"]),
            ]))
            story.append(Spacer(1, 10))
            story.append(_report_legend(styles))

            rows = []
            sums = [0] * 6
            for key in selected:
                stats = _report_department_stats(data, key)
                totals = _report_totals(stats["organised"])
                row = [
                    totals["events"], totals["registered"], totals["attended"],
                    stats["students"], stats["student_regs"], stats["student_att"],
                ]
                sums = [a + b for a, b in zip(sums, row)]
                rows.append([_report_dept_label(data, key)] + row)

            # students are counted per department, so the unique-student total
            # is the sum of each department's own unique students
            rows.append(["Total"] + sums)

            story.append(_report_p("Department overview", styles["h2"]))
            story.append(_report_table(
                styles,
                ["Department", "Events held", "Registrations received",
                 "Attended these events", "Students who registered",
                 "Registrations made", "Students attended"],
                rows, [0.25, 0.10, 0.15, 0.13, 0.14, 0.12, 0.11],
                right=range(1, 7),
                groups=[
                    ("A.  Events HELD by the department", 1, 3),
                    ("B.  The department's OWN STUDENTS (any event)", 4, 6),
                ],
                total_row=True,
            ))
            story.append(_report_p(
                "Departments with 0 events held but students registered simply did not "
                "organise a published event in this period; their students took part in "
                "events held by others.",
                styles["note"],
            ))

            if any(rec["dept_inferred"] for rec in data["records"]):
                story.append(_report_p(INFERRED_NOTE, styles["note"]))

        for key in selected:
            story.extend(
                _report_department_section(styles, data, key, page_break=everything)
            )

        return story

    return _report_build(title, story_builder), file_name


# ------------------------------------------------------------
# club report
# ------------------------------------------------------------

def _report_load_clubs():
    clubs = {}

    for user in db.students.find({"role": "club", "status": "active"}):
        club_id = user_club_id(user) or str(user["_id"])

        if club_id in clubs:
            continue

        clubs[club_id] = {
            "id": club_id,
            "name": str(user.get("club_name") or user.get("name") or "Unnamed club"),
            "college": str(user.get("college_name") or ""),
            "user": user,
        }

    return sorted(clubs.values(), key=lambda club: club["name"].lower())


def _report_club_section(styles, data, club, records, page_break=True):
    from reportlab.platypus import PageBreak, Spacer

    totals = _report_totals(records)

    story = [PageBreak()] if page_break else []
    story.append(_report_p(f"Club: {club['name']}", styles["h1"]))

    if club["college"]:
        story.append(_report_p(club["college"], styles["body"]))
        story.append(Spacer(1, 6))

    story.append(_report_kpis(styles, [
        ("Events held", totals["events"]),
        ("Registrations", totals["registered"]),
        ("Attended", totals["attended"]),
        ("Seat fill rate", totals["fill"]),
    ]))

    if not records:
        story.append(Spacer(1, 8))
        story.append(_report_p(
            "No published events for this club in the selected period.",
            styles["body"],
        ))
        return story

    story.append(_report_p("Events by category", styles["h2"]))
    story.append(_report_table(
        styles, ["Category", "Events", "Registrations"],
        _report_categories(records), [0.4, 0.15, 0.15], right=(1, 2),
    ))

    story.append(_report_p("Event details", styles["h2"]))
    rows = [[
        _report_title(rec), rec["date"], rec["category"], rec["mode"],
        _report_dept_label(data, rec["dept_key"]) if rec["dept_key"] else "—",
        rec["capacity"] or "—", rec["registered"], rec["cancelled"],
        rec["attended"], _report_pct(rec["registered"], rec["capacity"]),
    ] for rec in records]
    story.append(_report_events_table(styles, rows, include_department=True))
    if any(rec["dept_inferred"] for rec in records):
        story.append(_report_p(INFERRED_NOTE, styles["note"]))

    # registrations split by the students' own department (counts only)
    by_department = {}
    for rec in records:
        for student_id in rec["reg_students"]:
            row = by_department.setdefault(
                _report_student_key(data, student_id), [set(), 0, 0]
            )
            row[0].add(student_id)
            row[1] += 1
            if student_id in rec["att_students"]:
                row[2] += 1

    story.append(_report_p(
        "Who registered for this club's events (by the student's own department)",
        styles["h2"],
    ))
    if by_department:
        rows = sorted(
            (
                [_report_dept_label(data, key), len(students), regs, atts]
                for key, (students, regs, atts) in by_department.items()
            ),
            key=lambda row: (-row[2], row[0].lower()),
        )
        story.append(_report_table(
            styles,
            ["Department", "Students", "Registrations", "Attended"],
            rows, [0.4, 0.15, 0.15, 0.15], right=(1, 2, 3),
        ))
    else:
        story.append(_report_p("No registrations yet.", styles["body"]))

    return story


def _build_club_report(club_id, date_from, date_to):
    data = _report_load(date_from, date_to)
    clubs = _report_load_clubs()
    wanted = str(club_id or "").strip()
    everything = wanted.lower() in ("", "all")

    if everything:
        selected = clubs
        title = "Club-wise Report - All Clubs"
        scope_line = "Scope: all clubs"
        file_name = "All"
    else:
        selected = [club for club in clubs if club["id"] == wanted]

        if not selected:
            raise HTTPException(status_code=404, detail="Club not found.")

        title = f"Club Report - {selected[0]['name']}"
        scope_line = f"Scope: {selected[0]['name']}"
        file_name = selected[0]["name"]

    club_records = {
        club["id"]: [
            rec for rec in data["records"]
            if event_belongs_to_club(rec["raw"], club["user"])
        ]
        for club in selected
    }

    def story_builder(styles):
        story = _report_cover(styles, title, [
            scope_line,
            "Published events only.  " + _report_period_text(date_from, date_to),
            "Registration figures are counts of students; no personal details are included.",
        ])

        if everything:
            rows = []
            all_records = []

            for club in selected:
                records = club_records[club["id"]]
                all_records.extend(records)
                totals = _report_totals(records)
                rows.append([
                    club["name"], club["college"] or "—", totals["events"],
                    totals["registered"], totals["cancelled"],
                    totals["attended"], totals["fill"],
                ])

            overall = _report_totals(all_records)
            story.append(_report_kpis(styles, [
                ("Clubs", len(selected)),
                ("Events held", overall["events"]),
                ("Registrations", overall["registered"]),
                ("Attended", overall["attended"]),
            ]))

            story.append(_report_p("Club overview", styles["h2"]))
            if rows:
                story.append(_report_table(
                    styles,
                    ["Club", "College", "Events", "Registrations",
                     "Cancelled", "Attended", "Seat fill"],
                    rows, [0.26, 0.22, 0.08, 0.13, 0.10, 0.10, 0.11], right=range(2, 7),
                ))
            else:
                story.append(_report_p("No active clubs found.", styles["body"]))

            linked = {rec["id"] for rec in all_records}
            unlinked = sum(1 for rec in data["records"] if rec["id"] not in linked)
            if unlinked:
                story.append(_report_p(
                    f"{unlinked} published event(s) are not linked to a club "
                    "(for example events created directly by Admin) and are not "
                    "part of this report.",
                    styles["note"],
                ))

        for club in selected:
            story.extend(_report_club_section(
                styles, data, club, club_records[club["id"]],
                page_break=everything,
            ))

        return story

    return _report_build(title, story_builder), file_name


# ------------------------------------------------------------
# endpoints
# ------------------------------------------------------------

def _report_response(pdf_bytes, prefix, name):
    filename = _report_filename(prefix, name)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # lets the browser read the suggested file name across origins
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@app.get("/api/admin/reports/options")
def admin_report_options(authorization: str = Header(None)):
    """Departments and clubs the admin can pick in the Analytics page."""
    _report_require_admin(authorization)

    # every official department is always selectable, even before it has any data
    names = {_report_key(name): name for name in OFFICIAL_DEPARTMENTS}
    extra = {}

    def add(raw):
        raw = " ".join(str(raw or "").split())
        if raw and _report_key(raw) not in names:
            extra.setdefault(_report_key(raw), raw)

    for value in db.events.distinct("department", REPORT_PUBLISHED_FILTER):
        add(value)

    for value in db.students.distinct("department", {"role": "student"}):
        add(value)

    return {
        # official departments first (in their official order), then any
        # other names found in older data
        "departments": list(OFFICIAL_DEPARTMENTS)
        + sorted(extra.values(), key=str.lower),
        "clubs": [
            {"id": club["id"], "name": club["name"], "college": club["college"]}
            for club in _report_load_clubs()
        ],
    }


@app.get("/api/admin/reports/department")
def admin_department_report(
    department: str = "",
    date_from: str = "",
    date_to: str = "",
    authorization: str = Header(None),
):
    """PDF: one department, or every department when `department` is empty/"all"."""
    _report_require_admin(authorization)
    date_from, date_to = _report_period(date_from, date_to)

    pdf_bytes, name = _build_department_report(department, date_from, date_to)
    return _report_response(pdf_bytes, "Department_Report", name)


@app.get("/api/admin/reports/club")
def admin_club_report(
    club_id: str = "",
    date_from: str = "",
    date_to: str = "",
    authorization: str = Header(None),
):
    """PDF: one club, or every club when `club_id` is empty/"all"."""
    _report_require_admin(authorization)
    date_from, date_to = _report_period(date_from, date_to)

    pdf_bytes, name = _build_club_report(club_id, date_from, date_to)
    return _report_response(pdf_bytes, "Club_Report", name)


# ============================================================
# ADMIN: HOD ROUTING CHECK
# ------------------------------------------------------------
# Shows which HOD account handles which department and whether any
# HOD account or waiting event could not be matched to an official
# department. Read-only; changes nothing.
#   GET /api/admin/hod-routing-check
# ============================================================

@app.get("/api/admin/hod-routing-check")
def admin_hod_routing_check(authorization: str = Header(None)):
    _report_require_admin(authorization)

    hods = []
    by_department = {name: 0 for name in OFFICIAL_DEPARTMENTS}

    for hod in db.students.find({"role": "hod"}):
        resolved = canonical_department(hod.get("department"))
        active = str(hod.get("status") or "").lower() == "active"

        if resolved and active:
            by_department[resolved] += 1

        hods.append({
            "name": hod.get("name", ""),
            "email": hod.get("email", ""),
            "status": hod.get("status", ""),
            "department_on_account": hod.get("department", ""),
            "handles_department": resolved,
            "needs_fix": resolved is None,
        })

    waiting = []
    for event in db.events.find({"approval_status": "pending_hod"}):
        routed = canonical_department(event.get("department"))

        waiting.append({
            "event_id": str(event["_id"]),
            "title": event.get("title", ""),
            "department_on_event": event.get("department", ""),
            "goes_to": routed or "any HOD (older event, no official department)",
            "no_active_hod": bool(routed) and by_department.get(routed, 0) == 0,
        })

    return {
        "official_departments": OFFICIAL_DEPARTMENTS,
        "active_hods_per_department": by_department,
        "departments_without_hod": [
            name for name, count in by_department.items() if count == 0
        ],
        "hod_accounts": hods,
        "hod_accounts_needing_fix": [h for h in hods if h["needs_fix"]],
        "events_waiting_for_hod": waiting,
        "events_with_no_active_hod": [w for w in waiting if w["no_active_hod"]],
    }


# ============================================================
# ADMIN: BULK USER ACTIONS  (Users page checkboxes)
# ------------------------------------------------------------
#   POST /api/admin/users/bulk-status   {ids: [...], action: "activate"|"disable"}
#   POST /api/admin/users/bulk-delete   {ids: [...], confirm: "DELETE"}
#
# Rules enforced here (not just on the screen):
#   - Admin only. Admin accounts and the caller's own account are never changed.
#   - Disable: the user cannot sign in and their open session stops working at once.
#     The earlier status is remembered.
#   - Activate: only re-enables disabled/inactive accounts and restores the
#     remembered status. Pending / rejected organisation applications are NOT
#     activated here; they go through the Applications page.
#   - Delete: student accounts only, permanent, and removes their personal data.
#   - Every change is written to the activity log.
# ============================================================

USER_BULK_LIMIT = 200


class BulkUserStatusRequest(BaseModel):
    ids: List[str]
    action: str  # "activate" or "disable"


class BulkUserDeleteRequest(BaseModel):
    ids: List[str]
    confirm: str = ""


def _bulk_clean_ids(raw_ids):
    ids = []
    for value in raw_ids or []:
        value = str(value or "").strip()
        if value and value not in ids:
            ids.append(value)

    if not ids:
        raise HTTPException(status_code=422, detail="Select at least one user.")

    if len(ids) > USER_BULK_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"You can change at most {USER_BULK_LIMIT} users at once.",
        )

    for value in ids:
        try:
            ObjectId(value)
        except Exception:
            raise HTTPException(status_code=422, detail="One of the selected ids is not valid.")

    return ids


def _bulk_load_users(ids):
    return {
        str(user["_id"]): user
        for user in db.students.find({"_id": {"$in": [ObjectId(i) for i in ids]}})
    }


def _bulk_row(user_id, user, reason=None):
    row = {
        "id": user_id,
        "name": (user or {}).get("name", ""),
        "email": (user or {}).get("email", ""),
    }
    if reason:
        row["reason"] = reason
    return row


def _bulk_log(admin, action, user, description):
    create_activity_log(
        user_id=str(admin["_id"]),
        user_name=admin.get("name", "Admin"),
        user_email=admin.get("email", ""),
        role="admin",
        action=action,
        module="Users",
        description=description,
        related_id=str(user["_id"]),
    )


def _bulk_common_skip(admin, user_id, user):
    if user is None:
        return "Account not found."
    if user_id == str(admin["_id"]):
        return "You cannot change your own account."
    if user.get("role") == "admin":
        return "Admin accounts cannot be changed here."
    return None


@app.post("/api/admin/users/bulk-status")
def admin_bulk_user_status(
    body: BulkUserStatusRequest,
    authorization: str = Header(None),
):
    admin = _report_require_admin(authorization)

    action = str(body.action or "").strip().lower()
    if action not in ("activate", "disable"):
        raise HTTPException(status_code=422, detail="Action must be 'activate' or 'disable'.")

    ids = _bulk_clean_ids(body.ids)
    users = _bulk_load_users(ids)
    updated, skipped = [], []

    for user_id in ids:
        user = users.get(user_id)
        reason = _bulk_common_skip(admin, user_id, user)

        if reason:
            skipped.append(_bulk_row(user_id, user, reason))
            continue

        status = str(user.get("status") or "active").lower()
        role = user.get("role", "student")

        if action == "disable":
            if status == "disabled":
                skipped.append(_bulk_row(user_id, user, "Already disabled."))
                continue

            db.students.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "status": "disabled",
                    "status_before_disable": status,
                    "disabled_at": datetime.now(timezone.utc),
                    "disabled_by": str(admin["_id"]),
                }},
            )
            _bulk_log(admin, "USER_DISABLED", user, f"Disabled {role} account {user.get('email', '')}")
            updated.append({**_bulk_row(user_id, user), "status": "disabled"})
            continue

        # activate
        if status not in ("disabled", "inactive"):
            if status == "active":
                reason = "Already active."
            else:
                reason = (
                    f"This account is {status}. Organisation applications are "
                    "handled on the Applications page."
                )
            skipped.append(_bulk_row(user_id, user, reason))
            continue

        restored = str(user.get("status_before_disable") or "active").lower()
        if restored in ("disabled", "inactive"):
            restored = "active"

        db.students.update_one(
            {"_id": user["_id"]},
            {
                "$set": {"status": restored},
                "$unset": {"status_before_disable": "", "disabled_at": "", "disabled_by": ""},
            },
        )
        _bulk_log(admin, "USER_ACTIVATED", user, f"Re-enabled {role} account {user.get('email', '')}")
        updated.append({**_bulk_row(user_id, user), "status": restored})

    verb = "disabled" if action == "disable" else "activated"
    return {
        "status": "success",
        "action": action,
        "requested": len(ids),
        "updated": updated,
        "skipped": skipped,
        "message": f"{len(updated)} {verb}, {len(skipped)} skipped.",
    }


@app.post("/api/admin/users/bulk-delete")
def admin_bulk_user_delete(
    body: BulkUserDeleteRequest,
    authorization: str = Header(None),
):
    admin = _report_require_admin(authorization)

    if str(body.confirm or "").strip() != "DELETE":
        raise HTTPException(status_code=422, detail='Type DELETE to confirm this permanent action.')

    ids = _bulk_clean_ids(body.ids)
    users = _bulk_load_users(ids)
    deleted, skipped = [], []

    for user_id in ids:
        user = users.get(user_id)
        reason = _bulk_common_skip(admin, user_id, user)

        if not reason and user.get("role", "student") != "student":
            reason = "Only student accounts can be deleted here. You can disable this account instead."

        if reason:
            skipped.append(_bulk_row(user_id, user, reason))
            continue

        try:
            # the student's own data first, the account last
            removed = {
                "registrations": db.event_registrations.delete_many({"student_id": user_id}).deleted_count,
                "attendance": db.attendance.delete_many({"student_id": user_id}).deleted_count,
                "saved_events": db.saved_events.delete_many({"student_id": user_id}).deleted_count,
                "notifications": db.notifications.delete_many({"recipient": user_id}).deleted_count,
            }
            db.profiles.delete_many({"student_id": user_id})
            db.timetables.delete_many({"student_id": user_id})
            db.students.delete_one({"_id": user["_id"]})
        except Exception as error:
            print("BULK DELETE ERROR:", repr(error))
            skipped.append(_bulk_row(user_id, user, "Could not be deleted. Please try again."))
            continue

        _bulk_log(
            admin, "USER_DELETED", user,
            f"Deleted student account {user.get('email', '')} "
            f"({removed['registrations']} registrations removed)",
        )
        deleted.append({**_bulk_row(user_id, user), **removed})

    return {
        "status": "success",
        "requested": len(ids),
        "deleted": deleted,
        "skipped": skipped,
        "message": f"{len(deleted)} deleted, {len(skipped)} skipped.",
    }


# ============================================================
# QR SYSTEM (qr_system/)
# Phase 1: Student Identity QR.  See PHASE1_README.md.
# ============================================================
from qr_system import install_qr_system, legacy_scan

qr_system = install_qr_system(
    app,
    db,
    get_student_id_from_token,
    make_qr_svg=make_qr_svg,
    is_event_public=is_event_public,
    audience_gate=audience_gate,
    on_attendance_marked=lambda student_id, event_id, event: record_interaction(
        db, student_id, event_id, "attend", event=event
    ),
)


def qr_hook(name, *args):
    """Call a QR hook. A QR problem must never break the main feature."""
    try:
        return getattr(qr_system, name)(*args)
    except Exception as error:
        print("QR HOOK ERROR:", name, repr(error))


def qr_scan_for_scanner(qr_data, event_id, leader_id):
    """Used by the existing /attendance/scan route for CPQ- QR codes."""
    return legacy_scan(qr_system, qr_data, event_id, leader_id)


# ------------------------------------------------------------------
# Phase 3: keep attendance passes in step with eligibility changes.
# Both wrappers add one line and leave event_audience.py and
# academic_progression.py exactly as they are.
# ------------------------------------------------------------------
_set_audience_original = audience_gate.set_audience


def _set_audience_then_refresh_passes(admin, event_id, raw):
    """Admin changed the audience of a published event."""
    result = _set_audience_original(admin, event_id, raw)
    qr_hook("on_event_eligibility_changed", event_id)
    return result


audience_gate.set_audience = _set_audience_then_refresh_passes

import academic_progression as _academic_progression

_reconcile_original = _academic_progression.reconcile_eligibility


def _reconcile_then_refresh_passes(db_, changes, reason, actor_id=""):
    """Promotion / class correction / a student changing class."""
    outcome = _reconcile_original(db_, changes, reason, actor_id)
    changed = [c["student_id"] for c in changes if c["before"] != c["after"]]
    if changed:
        qr_hook("on_students_eligibility_changed", changed)
    return outcome


_academic_progression.reconcile_eligibility = _reconcile_then_refresh_passes