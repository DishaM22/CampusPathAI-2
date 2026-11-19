"""
apply_audience_patch.py  -  adds the Publish-Event-To audience hooks to main.py.

Run from the backend folder:      python apply_audience_patch.py
 * makes a backup first (main.py.bak_before_audience)
 * every edit is a tiny, anchored insertion; nothing else in main.py is touched
 * safe to run twice (already-applied edits are skipped)
 * if an anchor is missing or appears more than once (for example because a
   teammate edited that exact spot) it does NOT guess: it prints the edit so
   you can place it by hand, and writes nothing unless ALL edits can be placed.
Budget code is not touched by any edit below.
"""
import shutil, sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "main.py"
EDITS = []
def edit(name, anchor, replacement): EDITS.append((name, anchor, replacement))

edit("1. install audience + interactions hook",
'''app.include_router(
    build_interactions_router(
        db,
        get_student_id_from_token,
        is_event_public,
    )
)''',
'''# Admin-controlled publishing audience (see event_audience.py)
from event_audience import (
    install as install_event_audience,
    viewer_can_view as _audience_viewer_can_view,
)
audience_gate = install_event_audience(app, db, get_student_id_from_token)

app.include_router(
    build_interactions_router(
        db,
        get_student_id_from_token,
        is_event_public,
        student_can_view=_audience_viewer_can_view,
    )
)''')

edit("2. GET /api/events list",
'''        db.events.find(PUBLIC_EVENT_FILTER).sort("_id", -1)
    )
''',
'''        db.events.find(PUBLIC_EVENT_FILTER).sort("_id", -1)
    )
    events = audience_gate.filter_events(events, authorization)
''')

edit("3. dashboard events (also feeds recommendations + AI chat)",
'''    events = list(
        db.events.find(PUBLIC_EVENT_FILTER)
    )
''',
'''    events = list(
        db.events.find(PUBLIC_EVENT_FILTER)
    )
    events = audience_gate.filter_events(events, authorization)
''')

edit("4. GET /api/events/{id}",
'''    if not is_event_public(event) and not viewer_can_see_budget(authorization):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )
''',
'''    if not is_event_public(event) and not viewer_can_see_budget(authorization):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # Publishing audience: students outside it cannot open the event by URL.
    if not audience_gate.can_view(event, authorization):
        raise HTTPException(status_code=404, detail="Event not found")
''')

edit("5. register for event",
'''    # Students may only register for published events.
    if not is_event_public(event):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )
''',
'''    # Students may only register for published events.
    if not is_event_public(event):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    # ...and only if they are in the event's publishing audience.
    audience_gate.require_student_access(event, student_id)
''')

edit("6. save event",
'''    # Students may only save published events.
    if not is_event_public(event):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )
''',
'''    # Students may only save published events.
    if not is_event_public(event):
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )
    audience_gate.require_student_access(event, student_id)
''')

edit("7. not interested",
'''    if not event or not is_event_public(event):
        raise HTTPException(status_code=404, detail="Event not found")

    now = datetime.now(timezone.utc)
''',
'''    if not event or not is_event_public(event):
        raise HTTPException(status_code=404, detail="Event not found")
    audience_gate.require_student_access(event, student_id)

    now = datetime.now(timezone.utc)
''')

edit("8. attendance QR (student)",
'''    # Generate attendance token if it doesn't exist''',
'''    # Students who left the audience can no longer get a QR (records are kept).
    audience_gate.require_student_access(
        event, student_id, 403, "You are no longer eligible for this event."
    )

    # Generate attendance token if it doesn't exist''')

edit("9. attendance scan (organiser)",
'''    # Check if attendance already exists''',
'''    # The scanned student must still be in the event's audience.
    audience_gate.require_student_access(
        event, registration["student_id"], 403,
        "This student is no longer in the audience for this event."
    )

    # Check if attendance already exists''')

edit("10. final Admin approval needs an audience",
'''    result = db.events.update_one(
        {
            "_id": oid,
            "approval_status": expected_status,
            "submission_version": version,
        },
        {
            "$set": {"approval_status": next_status},
            "$push": {"approval_history": entry},
        },
    )''',
'''    set_fields = {"approval_status": next_status}

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
    )''')

raw = open(PATH, "rb").read().decode("utf-8")
nl = "\r\n" if "\r\n" in raw else "\n"
text = raw.replace("\r\n", "\n")
if "audience_gate" in text and "install_event_audience" in text:
    print("Hooks already present - nothing to do."); sys.exit(0)

problems, work = [], text
for name, anchor, repl in EDITS:
    n = work.count(anchor)
    if n != 1:
        problems.append((name, n, repl))
        continue
    work = work.replace(anchor, repl)

if problems:
    print("NOTHING WAS WRITTEN. These edits could not be placed automatically:\n")
    for name, n, repl in problems:
        print(f"- {name}: anchor found {n} time(s)")
    print("\nSend me that part of main.py and I will give you the exact manual edit.")
    sys.exit(1)

shutil.copyfile(PATH, PATH + ".bak_before_audience")
open(PATH, "wb").write(work.replace("\n", nl).encode("utf-8"))
print(f"Applied {len(EDITS)} edits. Backup: {PATH}.bak_before_audience")