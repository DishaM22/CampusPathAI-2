"""
HOD / Dean registration for CampusPath AI.

Uses the same collection (db.students), the same password hasher (pwdlib)
and the same pending -> active approval flow as your club / inter-college
accounts, so the existing Admin approval screen can approve these accounts.

main.py must contain (AFTER `app = FastAPI()`):
    from reviewer_auth import router as reviewer_auth_router
    app.include_router(reviewer_auth_router)
"""
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pwdlib import PasswordHash
from pydantic import BaseModel

from database import db
from departments import OFFICIAL_DEPARTMENTS, canonical_department

password_hash = PasswordHash.recommended()   # same hasher as main.py
router = APIRouter(prefix="/api", tags=["reviewer-auth"])
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^[+\d][\d\s-]{8,14}$")

# Throw-away mailbox providers are never accepted for faculty accounts.
DISPOSABLE_DOMAINS = {
    "yopmail.com", "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "tempmail.com", "temp-mail.org", "trashmail.com", "sharklasers.com",
    "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com",
}

# Optional: restrict sign-ups to your college domain(s), e.g. in .env:
#   ALLOWED_EMAIL_DOMAINS=abccollege.edu.in,abc.ac.in
# Leave it unset to allow any non-disposable address.
ALLOWED_EMAIL_DOMAINS = {
    d.strip().lower()
    for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",")
    if d.strip()
}


class ReviewerRegister(BaseModel):
    name: str
    email: str
    department: str          # for Dean this holds the Faculty / School
    employee_id: str
    password: str
    role: str                # "hod" or "dean"
    college: str = ""        # college / university name
    designation: str = ""    # e.g. "Professor & HOD"
    phone: str | None = None # optional


def _create_reviewer(expected_role: str, data: ReviewerRegister):
    if data.role != expected_role:
        raise HTTPException(status_code=400, detail="Role does not match this registration form.")

    email = data.email.strip().lower()
    emp_id = data.employee_id.strip()
    if len(data.name.strip()) < 3:
        raise HTTPException(status_code=400, detail="Please enter your full name.")
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    domain = email.rsplit("@", 1)[-1]
    if domain in DISPOSABLE_DOMAINS:
        raise HTTPException(status_code=400, detail="Please use your official college email address.")
    if ALLOWED_EMAIL_DOMAINS and domain not in ALLOWED_EMAIL_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail="Use your official college email (" + ", ".join(sorted(ALLOWED_EMAIL_DOMAINS)) + ").",
        )
    if not data.department.strip() or not emp_id:
        raise HTTPException(status_code=400, detail="Department/faculty and employee ID are required.")

    college = data.college.strip()
    designation = data.designation.strip()
    phone = (data.phone or "").strip()
    if len(college) < 3:
        raise HTTPException(status_code=400, detail="Please enter your college / university name.")
    if not designation:
        raise HTTPException(status_code=400, detail="Please enter your designation.")
    if phone and not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number.")

    # A HOD's department decides which events reach them, so it must be an
    # official one and is stored in its official spelling. (A Dean's
    # "department" is a faculty / school and stays free text.)
    department = data.department.strip()
    if expected_role == "hod":
        official = canonical_department(department)
        if not official:
            raise HTTPException(
                status_code=400,
                detail="Select your department from the list: "
                       + ", ".join(OFFICIAL_DEPARTMENTS) + ".",
            )
        department = official
    p = data.password
    if len(p) < 8 or not re.search(r"[a-z]", p) or not re.search(r"[A-Z]", p) or not re.search(r"\d", p):
        raise HTTPException(status_code=400, detail="Password needs 8+ characters with upper-case, lower-case and a number.")

    if db.students.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    if db.students.find_one({"role": expected_role, "employee_id": emp_id}):
        raise HTTPException(status_code=409, detail="This employee ID is already registered.")

    db.students.insert_one({
        "name": data.name.strip(),
        "email": email,
        "password": password_hash.hash(p),
        "department": department,
        "employee_id": emp_id,
        "college_name": college,
        "designation": designation,
        "phone": phone,
        "role": expected_role,
        "status": "pending",                      # Admin sets this to "active"
        "submitted_at": datetime.now(timezone.utc),
    })
    return {"success": True, "message": "Request submitted. An Admin must approve your account before you can sign in."}


@router.post("/hod/register")
def register_hod(data: ReviewerRegister):
    return _create_reviewer("hod", data)


@router.post("/dean/register")
def register_dean(data: ReviewerRegister):
    return _create_reviewer("dean", data)