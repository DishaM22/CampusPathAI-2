"""
apply_modifications_patch.py - Phases 6-8 hooks for main.py (modification requests,
re-approval through the EXISTING HOD/Dean/Admin queues, versions, student notifications).

Run from the backend folder (AFTER apply_progression_patch.py):
    python apply_modifications_patch.py

Edits (all small, anchored insertions; each is skipped if already present):
  1. set-up block        registers the new API and passes in your existing validation
                         and ownership functions (nothing is copied or changed)
  2. approvals queue     modifications appear in the SAME queue HOD/Dean/Faculty/Admin
                         already use (so their pages need no change)
  3. review endpoint     a decision on a modification is handled by the new module
  5-7. Faculty endpoints  modifications also appear in Faculty's own review list, list and counts
  4. update_event        a published event can no longer be edited directly by a
                         Club Coordinator / Faculty (the old behaviour re-started the
                         approval chain and HID the event from students)
Backups are made first; if any anchor is not found NOTHING is written.
Budget code is not changed: the existing budget validation is simply re-used.
"""
import shutil, sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "main.py"

EDITS = [
 ("1. set-up block", "install_event_modifications(",
'''install_academic_progression(app, db, get_student_id_from_token)
''',
'''install_academic_progression(app, db, get_student_id_from_token)

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
'''),

 ("2. approvals queue shows modifications", "event_modifications.approval_items(",
'''    return {
        "status": "success",
        "role": role,
        "queue": queue,
        "reviewed": reviewed,
    }
''',
'''    # Modifications of published events use the same queue (see event_modifications.py).
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
'''),

 ("3. review endpoint hands modifications to the new module", "event_modifications.is_modification(",
'''    event = db.events.find_one({"_id": oid})

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if role == "faculty" and not event_belongs_to_club(event, user):
        raise HTTPException(
            status_code=403,
            detail="You can only review events of your own club.",
        )
''',
'''    # A decision on a modification of a published event is handled by
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
'''),

 ("5. Faculty 'Pending Event Reviews' list shows modifications", "event_modifications.faculty_review_events(user):",
'''    rows = [faculty_event_row(e, names) for e in events if keep(e)]
''',
'''    rows = [faculty_event_row(e, names) for e in events if keep(e)]

    # Modifications of published events waiting for this faculty's review.
    if "pending_faculty" in wanted_statuses:
        try:
            for modification_event in event_modifications.faculty_review_events(user):
                if not text or text in normalize_text(modification_event.get("title")):
                    rows.append(faculty_event_row(modification_event, names))
        except Exception as error:
            print("FACULTY MODIFICATION LIST WARNING:", repr(error))
'''),

 ("6. Faculty dashboard lists them", "faculty_modification_events = event_modifications",
'''    pending = [
        faculty_event_row(e, names)
        for e in events
        if e.get("approval_status") == "pending_faculty"
    ]
''',
'''    pending = [
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
'''),

 ("7. Faculty dashboard pending-review count includes modifications", "len(faculty_modification_events)",
'''        "kpis": {
            **faculty_status_counts(events),
''',
'''        "kpis": {
            **faculty_status_counts(events),
            "pendingFacultyReviews": faculty_status_counts(events)[
                "pendingFacultyReviews"
            ] + len(faculty_modification_events),
'''),

 ("4. published events are changed through a modification request", "Use Request Modification",
'''    faculty_correcting = (
        user_role == "faculty"
        and existing_event.get("approval_status") == "pending_faculty"
    )
''',
'''    # A published event is live for students. Editing it here used to restart
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
'''),
]

raw = open(PATH, "rb").read().decode("utf-8")
nl = "\r\n" if "\r\n" in raw else "\n"
work = raw.replace("\r\n", "\n")
if "install_academic_progression(app" not in work:
    print("Run apply_progression_patch.py first (this patch builds on it)."); sys.exit(1)

todo, bad = 0, []
for name, marker, anchor, repl in EDITS:
    if marker in work:
        print(f"  - already applied: {name}"); continue
    n = work.count(anchor)
    if n != 1:
        bad.append((name, n)); continue
    work = work.replace(anchor, repl); todo += 1
    print(f"  + applied: {name}")
if bad:
    print(f"NOTHING WRITTEN. Could not place:")
    for name, n in bad: print(f"   - {name}: anchor found {n} time(s)")
    print("Send me that part of main.py and I will give the exact manual edit.")
    sys.exit(1)
if todo:
    shutil.copyfile(PATH, PATH + ".bak_before_modifications")
    open(PATH, "wb").write(work.replace("\n", nl).encode("utf-8"))
    print(f"{PATH}: done. Backup: {PATH}.bak_before_modifications")
else:
    print(f"{PATH}: nothing to do.")
