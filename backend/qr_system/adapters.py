"""
Integration adapters. Everything the QR module needs to know about the REST of
CampusPathAI goes through here, so the Admin-audience / eligibility /
Registration-Required work can be connected later without touching QR logic.
"""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

_NOT_REQUIRED = {
    "not_required", "no_registration", "no_registration_required",
    "none", "open", "false", "no",
}


def default_is_student_verified(student: dict) -> bool:
    """A student is OTP-verified when /api/register stamped `otp_verified_at`."""
    return bool(
        student.get("otp_verified_at")
        or student.get("otp_verified")
        or student.get("email_verified")
    )


def default_is_student_eligible(event: dict, student_id: str) -> bool:
    """Standalone default (tests). In CampusPathAI install_qr_system() swaps
    this for audience_gate.require_student_access."""
    return True


def default_event_requires_registration(event: dict) -> bool:
    """
    Future event model: `registration_required` (bool) or `registration_mode`
    ("Registration Required" / "No Registration Required").
    Today's events carry neither field and DO require registration.
    """
    if "registration_required" in event:
        value = event["registration_required"]
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1", "required")
        return bool(value)
    mode = event.get("registration_mode") or event.get("registration_type")
    if mode:
        key = str(mode).strip().lower().replace(" ", "_").replace("-", "_")
        return key not in _NOT_REQUIRED
    return True


def default_is_event_published(event: dict) -> bool:
    """Same rule as main.is_event_public (legacy events stay visible)."""
    return "approval_status" not in event or event.get("approval_status") == "published"


def default_is_event_cancelled(event: dict) -> bool:
    for field in ("status", "event_status", "approval_status"):
        if str(event.get(field) or "").strip().lower() in ("cancelled", "canceled"):
            return True
    return bool(event.get("is_cancelled") or event.get("cancelled_at"))


def default_can_scan_event(scanner: dict, event: dict) -> bool:
    role = str(scanner.get("role") or "").lower()
    if role == "admin":
        return True
    if str(event.get("created_by")) == str(scanner.get("_id")):
        return True
    if role in ("club", "faculty") and event.get("club_id") and (
        event.get("club_id") == scanner.get("club_id")
    ):
        return True
    return False


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")


def default_event_expiry(event: dict) -> Optional[datetime]:
    """End of the event day + grace (QR_EXPIRY_GRACE_HOURS, default 48 h).
    None = never expires (event date could not be parsed)."""
    raw = str(event.get("date") or "").strip()[:10]
    for fmt in _DATE_FORMATS:
        try:
            day = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        grace = float(os.getenv("QR_EXPIRY_GRACE_HOURS", "48"))
        return day.replace(tzinfo=timezone.utc) + timedelta(days=1, hours=grace)
    return None


@dataclass
class Adapters:
    is_student_verified: Callable[[dict], bool] = default_is_student_verified
    is_student_eligible: Callable[[dict, str], bool] = default_is_student_eligible
    event_requires_registration: Callable[[dict], bool] = default_event_requires_registration
    is_event_published: Callable[[dict], bool] = default_is_event_published
    is_event_cancelled: Callable[[dict], bool] = default_is_event_cancelled
    can_scan_event: Callable[[dict, dict], bool] = default_can_scan_event
    event_expiry: Callable[[dict], Optional[datetime]] = default_event_expiry
    # Optional fast path for batch jobs: event -> ids of ALL students eligible right now.
    # In CampusPathAI: audience_gate.eligible_ids(event["audience"]). When None, the
    # service checks every active student one by one with is_student_eligible.
    eligible_student_ids: Optional[Callable[[dict], Iterable[str]]] = None