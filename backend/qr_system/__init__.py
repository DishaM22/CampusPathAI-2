"""CampusPathAI QR module - Phase 1 (Student Identity QR), Phase 2 (Event Registration QR), Phase 3 (Attendance Pass)."""
import logging
import threading

from .adapters import Adapters
from .routes import build_router, legacy_scan
from .service import QrError, QrService
from .tokens import TYPE_IDENTITY, TYPE_PASS, TYPE_REGISTRATION, TokenVault

log = logging.getLogger("campuspath.qr")
__all__ = ["install_qr_system", "legacy_scan", "QrService", "QrError", "Adapters", "TokenVault",
           "TYPE_IDENTITY", "TYPE_REGISTRATION", "TYPE_PASS"]


def install_qr_system(app, db, get_user_id, *, make_qr_svg=None, is_event_public=None,
                      audience_gate=None, eligibility=None, on_attendance_marked=None,
                      adapters=None, secret=None, **_future_options) -> QrService:
    """
    app          FastAPI app
    db           the same MongoDB `db` as database.py
    get_user_id  main.get_student_id_from_token
    make_qr_svg  main.make_qr_svg  (text -> SVG string)
    is_event_public      main.is_event_public (what counts as "published")
    audience_gate        main.audience_gate - the eligibility rule (per student) AND the fast
                         "who is eligible for this event" list used by the pass batch job
    eligibility          OR a plain function(event, student_id) -> bool
    on_attendance_marked function(student_id, event_id, event) called after a scan marks attendance
    """
    adapters = adapters or Adapters()
    if is_event_public:
        adapters.is_event_published = is_event_public
    if eligibility:
        adapters.is_student_eligible = eligibility
    elif audience_gate is not None:
        def _gate(event, student_id):
            try:
                audience_gate.require_student_access(event, student_id)
                return True
            except Exception as err:  # HTTPException = "not in the audience"
                if getattr(err, "status_code", None) is None:
                    log.exception("audience gate crashed")
                return False
        adapters.is_student_eligible = _gate
        if hasattr(audience_gate, "eligible_ids") and adapters.eligible_student_ids is None:
            adapters.eligible_student_ids = lambda event: audience_gate.eligible_ids(event.get("audience"))

    service = QrService(db, TokenVault(secret), adapters, make_qr_svg, on_attendance_marked)
    app.include_router(build_router(service, get_user_id))

    def _ensure_indexes():
        try:
            service.ensure_indexes()
            service.resume_pending_jobs()   # passes jobs interrupted by a restart
        except Exception:
            log.exception("QR index creation failed")

    # Newer FastAPI versions removed app.add_event_handler(); fall back to a thread.
    register = getattr(app, "add_event_handler", None)
    if callable(register):
        register("startup", _ensure_indexes)
    else:
        threading.Thread(target=_ensure_indexes, name="qr-indexes", daemon=True).start()
    return service