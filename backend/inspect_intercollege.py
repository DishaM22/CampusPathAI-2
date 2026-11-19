"""
READ-ONLY inspection for the Inter-College -> Faculty change.
Run from the backend folder:   python inspect_intercollege.py
It only uses find / count / aggregate. It never writes, updates or deletes.
"""
from collections import Counter
from database import db

S, E = db.students, db.events
line = lambda t="": print(t)
head = lambda t: (line(), print("=" * 64), print(t), print("=" * 64))

head("1. INTER-COLLEGE ACCOUNTS  (students: role == intercollege)")
ic = list(S.find({"role": "intercollege"}))
print("total:", len(ic), "| by status:", dict(Counter(u.get("status", "(none)") for u in ic)))
for u in ic:
    print(f"  - {u.get('name','?')} <{u.get('email','?')}> status={u.get('status','(none)')} org={u.get('club_name') or u.get('organization_name') or '-'}")

head("2. INTER-COLLEGE EVENTS  (events: created_by_role == intercollege)")
ev = list(E.find({"created_by_role": "intercollege"}))
print("total:", len(ev), "| by approval_status:", dict(Counter(e.get("approval_status", "(legacy, none)") for e in ev)))
ids = {str(u["_id"]) for u in ic}
by_owner = list(E.find({"created_by": {"$in": list(ids)}, "created_by_role": {"$ne": "intercollege"}})) if ids else []
print("events owned by an inter-college account but with another/missing created_by_role:", len(by_owner))
for e in ev:
    n = db.event_registrations.count_documents({"event_id": str(e["_id"])}) if "event_registrations" in db.list_collection_names() else 0
    print(f"  - {e.get('title','?')} | {e.get('date','?')} | status={e.get('approval_status','(legacy)')} v{e.get('submission_version',1)} | registrations={n}")

head("3. ORPHAN RISK  (retired creator, event not finished)")
orph = [e for e in ev if e.get("approval_status") in ("changes_requested", "rejected")]
live = [e for e in ev if str(e.get("approval_status", "")).startswith("pending_")]
print("returned/rejected (creator can no longer resubmit):", len(orph))
print("still in review queues (HOD/Dean/Admin can finish these):", len(live))

head("4. EVENT SCHEMA READINESS")
total = E.count_documents({})
print("events total:", total)
print("without approval_status (legacy, treated as published):", E.count_documents({"approval_status": {"$exists": False}}))
print("without club_id (expected: all, until backfill):", E.count_documents({"club_id": {"$exists": False}}))
print("by approval_status:", {r["_id"] if r["_id"] else "(none)": r["n"] for r in E.aggregate([{"$group": {"_id": "$approval_status", "n": {"$sum": 1}}}])})
print("by created_by_role:", {r["_id"] if r["_id"] else "(none)": r["n"] for r in E.aggregate([{"$group": {"_id": "$created_by_role", "n": {"$sum": 1}}}])})

head("5. CLUB ACCOUNTS  (needed for Faculty assignment)")
clubs = list(S.find({"role": "club"}))
print("club accounts:", len(clubs), "| by status:", dict(Counter(c.get("status", "(none)") for c in clubs)))
names = Counter((c.get("club_name") or "").strip().lower() for c in clubs)
dups = {n: k for n, k in names.items() if n and k > 1}
print("duplicate club names (would make name-based matching ambiguous):", dups or "none")
print("clubs without coordinator_email:", sum(1 for c in clubs if not c.get("coordinator_email")))
from bson import ObjectId
unmatched = 0
for e in E.find({"created_by_role": "club"}):
    cid = str(e.get("created_by", ""))
    if not (ObjectId.is_valid(cid) and S.find_one({"_id": ObjectId(cid)})):
        unmatched += 1
print("club events whose creator account cannot be found:", unmatched)

head("6. FACULTY / REVIEWER ACCOUNTS ALREADY PRESENT")
print({r["_id"]: r["n"] for r in S.aggregate([{"$match": {"role": {"$in": ["faculty", "hod", "dean"]}}}, {"$group": {"_id": "$role", "n": {"$sum": 1}}}])} or "none")
line("\nDone. Nothing was modified.")