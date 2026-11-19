"""
Read-only audit of the retired Inter-College Leader role.

Run from your backend folder (the one containing main.py):

    python audit_intercollege.py

It only READS the database and prints counts. It never changes or
deletes anything.
"""
from main import db

accounts = list(db.students.find({"role": "intercollege"}))
print(f"Inter-College accounts: {len(accounts)}")
by_status = {}
for account in accounts:
    status = account.get("status", "active")
    by_status[status] = by_status.get(status, 0) + 1
for status, count in sorted(by_status.items()):
    print(f"  {status}: {count}")

for account in accounts:
    print(
        f"  - {account.get('name', '?')} <{account.get('email', '?')}> "
        f"club='{account.get('club_name', '')}' "
        f"college='{account.get('college_name', '')}' "
        f"status={account.get('status', 'active')}"
    )

events = db.events.count_documents({"created_by_role": "intercollege"})
print(f"\nEvents created by Inter-College accounts: {events}")
print("\nNothing was changed. Those accounts can no longer log in.")
print("To move one to Faculty, register a Faculty account with the same")
print("email domain/club details and have the admin approve it.")