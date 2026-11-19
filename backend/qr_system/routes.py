"""HTTP layer. Thin on purpose - all rules live in service.py. Prefix: /api/qr"""
import functools

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from .service import QrError


def build_router(service, get_user_id):
    router = APIRouter(prefix="/api/qr", tags=["qr"])

    def guarded(fn):
        """QrError -> JSON {success:false, code, message} with a proper status."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except QrError as err:
                return JSONResponse(
                    status_code=err.status,
                    content={"success": False, "code": err.code, "message": err.message,
                             "detail": err.message, **err.extra},
                )
        return wrapper

    def current_user(authorization, roles):
        user_id = get_user_id(authorization)  # raises 401 / disabled accounts
        user = service.get_user(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Account not found")
        if str(user.get("role") or "student") not in roles:
            raise HTTPException(status_code=403, detail="You do not have access to this QR feature.")
        return str(user["_id"])

    # ---- Phase 1 ----------------------------------------------------
    @router.get("/identity")
    @guarded
    def my_identity_qr(authorization: str = Header(None)):
        sid = current_user(authorization, ("student",))
        return {"success": True, **service.get_or_create_identity_qr(sid)}

    # ---- Phase 2 (student side) ------------------------------------
    @router.get("/my-events")
    @guarded
    def my_event_qrs(authorization: str = Header(None)):
        sid = current_user(authorization, ("student",))
        return {"success": True, "items": service.list_student_event_qrs(sid)}

    @router.get("/events/{event_id}")
    @guarded
    def my_event_qr(event_id: str, authorization: str = Header(None)):
        sid = current_user(authorization, ("student",))
        return {"success": True, **service.get_event_qr_for_student(sid, event_id)}

    # ---- scanner side ------------------------------------------------
    @router.post("/events/{event_id}/scan")
    @guarded
    def scan_qr(event_id: str, payload: dict, authorization: str = Header(None)):
        uid = current_user(authorization, ("club", "faculty", "admin"))
        return service.scan(payload.get("qr_data"), event_id, uid, mark=True)

    @router.post("/events/{event_id}/validate")
    @guarded
    def validate_qr(event_id: str, payload: dict, authorization: str = Header(None)):
        uid = current_user(authorization, ("club", "faculty", "admin"))
        return service.scan(payload.get("qr_data"), event_id, uid, mark=False)

    # ---- Phase 3: admin / operations --------------------------------
    @router.post("/admin/events/{event_id}/generate-passes")
    @guarded
    def admin_generate_passes(event_id: str, authorization: str = Header(None)):
        """Re-run the (idempotent) pass job, e.g. for events published before the QR module existed."""
        current_user(authorization, ("admin",))
        service.load_event(event_id)
        return {"success": True, "job_id": service.enqueue_pass_job(event_id, reason="admin_resync")}

    @router.get("/admin/jobs/{job_id}")
    @guarded
    def admin_job_status(job_id: str, authorization: str = Header(None)):
        current_user(authorization, ("admin",))
        return {"success": True, **service.get_job(job_id)}

    return router


def legacy_scan(service, token, event_id, scanner_id):
    """
    Used by the EXISTING /api/events/{id}/attendance/scan route (the organiser's
    scanner screen already calls it). It answers in that route's own format, so
    the scanner screen needs no change:
        success         -> {"status": "success", ...}
        already scanned -> {"status": "already_marked", ...}
        anything else   -> HTTP error with a readable `detail`
    """
    try:
        return service.scan(token, event_id, scanner_id, mark=True)
    except QrError as err:
        if err.code == "ALREADY_ATTENDED":
            return {"status": "already_marked", "message": err.message,
                    "student_id": err.extra.get("student_id"), "event_id": event_id}
        raise HTTPException(status_code=err.status, detail=err.message)