"""
One-off: let EXISTING students (registered before this module) have an identity QR.
    cd backend
    python backfill_otp_verified.py            # dry run: only counts
    python backfill_otp_verified.py --apply    # does it
"""
import sys
from datetime import datetime, timezone

from database import db

APPLY = "--apply" in sys.argv
query = {"role": "student", "otp_verified_at": {"$exists": False}}
count = db.students.count_documents(query)
print(f"{count} existing student account(s) without an OTP stamp.")
if APPLY and count:
    result = db.students.update_many(
        query, {"$set": {"otp_verified_at": datetime.now(timezone.utc), "otp_verified_source": "legacy_backfill"}})
    print(f"Stamped {result.modified_count} account(s).")
elif not APPLY:
    print("Dry run - add --apply to stamp them.")