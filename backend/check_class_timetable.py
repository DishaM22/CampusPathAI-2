"""
check_class_timetable.py - READ-ONLY check of why a student's official timetable
does (or does not) show up.

Run from the backend folder with the virtual environment active:

    python check_class_timetable.py student.email@example.com

It prints, for that student:
  * whether a class is saved on the account
  * whether that class is active and has a PUBLISHED timetable (students only
    ever see published timetables, never drafts)
  * exactly what the dashboard is sent (the same function the API uses)
  * a plain-language verdict and the next step

It never changes the database and never prints passwords.
"""

import sys

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def per_day(timetable):
    counts = {}
    for day in DAYS:
        blocks = (timetable or {}).get(day)
        counts[day] = len(blocks) if isinstance(blocks, list) else 0
    return counts


def summarize(db, email, resolver=None):
    """Returns a list of text lines. `db` is the pymongo database."""
    from bson import ObjectId

    lines = []
    student = db.students.find_one({"email": email.strip().lower()}) or db.students.find_one({"email": email.strip()})

    if not student:
        return [f"No account found with email {email!r}."]

    student_id = str(student["_id"])
    lines.append(f"Account: {student.get('name', '')} ({student.get('role', 'no role')})")
    lines.append(f"Student id: {student_id}")
    lines.append("")

    if student.get("role") != "student":
        lines.append("VERDICT: this account is not a student account, so it has no class.")
        return lines

    class_id = student.get("class_id")
    lines.append(f"1) Class saved on the account: {class_id or 'NONE'}")

    if not class_id:
        lines.append("")
        lines.append("VERDICT: no class is saved on this account.")
        lines.append("  -> Log in as this student and choose a class: the dashboard notice")
        lines.append("     'Choose class', or the 'My class and timetable' button.")
        lines.append("     (If you pressed 'Finish without a class' in onboarding, that is why.)")
        _append_resolved(db, student_id, lines, resolver)
        return lines

    try:
        cls = db.classes.find_one({"_id": ObjectId(str(class_id))})
    except Exception:
        cls = None

    if not cls:
        lines.append("   The saved class no longer exists in the database.")
        lines.append("")
        lines.append("VERDICT: the class this account points to was deleted or the id is wrong.")
        lines.append("  -> Choose the class again from 'My class and timetable'.")
        _append_resolved(db, student_id, lines, resolver)
        return lines

    lines.append(f"2) Class: {cls.get('name')}  | active: {cls.get('is_active', True)}")

    published_id = cls.get("published_timetable_id")
    drafts = list(db.class_timetables.find({"class_id": str(cls["_id"]), "status": "draft"}))
    others = list(db.class_timetables.find({"class_id": str(cls["_id"])}))
    lines.append(f"3) Published timetable: {published_id or 'NONE'}"
                 f"  | drafts: {len(drafts)}  | versions in total: {len(others)}")

    published = None
    if published_id:
        try:
            published = db.class_timetables.find_one({"_id": ObjectId(str(published_id))})
        except Exception:
            published = None

    if published:
        counts = per_day(published.get("timetable"))
        total = sum(counts.values())
        lines.append(f"   Published v{published.get('version')} has {total} session(s): "
                     + ", ".join(f"{d[:3]} {n}" for d, n in counts.items()))

    _append_resolved(db, student_id, lines, resolver)
    lines.append("")

    if not published_id:
        if drafts:
            lines.append("VERDICT: this class has a DRAFT timetable but nothing is PUBLISHED.")
            lines.append("  Students only see published timetables.")
            lines.append("  -> Admin: Class & Timetable > open this class > Publish.")
        else:
            lines.append("VERDICT: this class has no timetable at all yet.")
            lines.append("  -> Admin: open the class and build one, or import the PDF, then Publish.")
    elif not published:
        lines.append("VERDICT: the class points to a published timetable that cannot be found.")
        lines.append("  -> Re-publish the class's timetable from the Admin page.")
    elif sum(per_day(published.get("timetable")).values()) == 0:
        lines.append("VERDICT: the published timetable is EMPTY.")
        lines.append("  -> Admin: edit the class, add sessions and publish again.")
    elif not cls.get("is_active", True):
        lines.append("VERDICT: the class is INACTIVE. The student still keeps it, but check why.")
    else:
        lines.append("VERDICT: the database and the API are fine: this student SHOULD see the timetable.")
        lines.append("  If the dashboard still shows nothing, the problem is in the browser:")
        lines.append("  open the dashboard, press F12 > Console, reload, and look for lines starting")
        lines.append("  'TIMETABLE GET RESPONSE' and 'FINAL DASHBOARD CLASS MAP'. Send me those.")

    return lines


def _append_resolved(db, student_id, lines, resolver):
    if resolver is None:
        try:
            from class_timetable import resolve_student_timetable as resolver
        except Exception as error:  # pragma: no cover
            lines.append(f"(could not load the resolver: {error})")
            return

    resolved = resolver(db, student_id)
    counts = per_day(resolved.get("timetable"))
    lines.append("")
    lines.append("What GET /api/student/timetable sends to the dashboard:")
    lines.append(f"   source: {resolved.get('source')}   published: {resolved.get('published')}"
                 f"   version: {resolved.get('version')}")
    lines.append(f"   sessions per day: " + ", ".join(f"{d[:3]} {n}" for d, n in counts.items()))
    if resolved.get("message"):
        lines.append(f"   message shown to the student: {resolved['message']}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_class_timetable.py student.email@example.com")
        return

    from database import db

    print("\n".join(summarize(db, sys.argv[1])))


if __name__ == "__main__":
    main()