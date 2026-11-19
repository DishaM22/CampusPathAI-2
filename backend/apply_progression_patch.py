"""
apply_progression_patch.py - Phase 4/5 hooks (safe to run any number of times,
also on a main.py that already has the earlier version of this patch).

Run from the backend folder:     python apply_progression_patch.py
  main.py             : registers the Academic Progression API, and makes
                        POST /api/profile keep the OFFICIAL year (a student can
                        no longer type a different year than the university's record)
  class_timetable.py  : students cannot re-pick their class after the first
                        choice; changes are logged with eligibility history

Each edit has its own check, so already-applied edits are skipped.
Backups are made first, edits are anchored insertions only, and if any
anchor cannot be found NOTHING is written to that file.
Budget code is never touched.
"""
import shutil, sys

# (name, marker that proves it is already applied, anchor, replacement)
JOBS = {
 "main.py": [
  ("register Academic Progression API", "install_academic_progression(app",
'''audience_gate = install_event_audience(app, db, get_student_id_from_token)
''',
'''audience_gate = install_event_audience(app, db, get_student_id_from_token)

# Admin-controlled promotion + dynamic eligibility (see academic_progression.py)
from academic_progression import (
    install as install_academic_progression,
    official_profile_year,
)
install_academic_progression(app, db, get_student_id_from_token)
'''),
  ("import official_profile_year (upgrade from the first version of this patch)", "official_profile_year,",
'''from academic_progression import install as install_academic_progression
''',
'''from academic_progression import (
    install as install_academic_progression,
    official_profile_year,
)
'''),
  ("GET /api/profile shows the official year (student has no profile yet)", 'official_profile_year(db, student_id, "")',
'''            "preferred_domains": [],
            "year": ""
        }
''',
'''            "preferred_domains": [],
            "year": official_profile_year(db, student_id, "")
        }
'''),
  ("GET /api/profile shows the official year (normal case)", 'db, student_id, profile.get("year", "")',
'''        "year": profile.get(
            "year",
            ""
        )
    }
''',
'''        "year": official_profile_year(
            db, student_id, profile.get("year", "")
        )
    }
'''),
  ("saving a profile keeps the official year", "official_profile_year(db, student_id, profile.year)",
'''        "year": profile.year,
''',
'''        "year": official_profile_year(db, student_id, profile.year),
'''),
 ],
 "class_timetable.py": [
  ("lock + log the student's own class choice", "guard_student_class_change(db, student)",
'''        student = require_student(authorization)
        data = run(
            assign_student_class, db, str(student["_id"]), body.class_id
        )
''',
'''        student = require_student(authorization)

        # Official academic data: after the first choice, only the Admin can
        # change a student's class (so audience restrictions cannot be dodged).
        from academic_progression import (
            guard_student_class_change,
            record_student_class_change,
        )
        from event_audience import student_snapshot

        guard_student_class_change(db, student)
        before_snapshot = student_snapshot(student)

        data = run(
            assign_student_class, db, str(student["_id"]), body.class_id
        )

        record_student_class_change(
            db, str(student["_id"]), before_snapshot, "student_selected_class"
        )
'''),
 ],
}

def patch(path, edits):
    raw = open(path, "rb").read().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    todo, bad = 0, []
    for name, marker, anchor, repl in edits:
        if marker in work:
            print(f"  - already applied: {name}"); continue
        n = work.count(anchor)
        if n != 1:
            bad.append((name, n)); continue
        work = work.replace(anchor, repl); todo += 1
        print(f"  + applied: {name}")
    if bad:
        print(f"{path}: NOTHING WRITTEN. Could not place:")
        for name, n in bad: print(f"   - {name}: anchor found {n} time(s)")
        print("   Send me that part of the file and I will give the exact manual edit.")
        return False
    if todo:
        shutil.copyfile(path, path + ".bak_before_progression")
        open(path, "wb").write(work.replace("\n", nl).encode("utf-8"))
        print(f"{path}: done. Backup: {path}.bak_before_progression")
    else:
        print(f"{path}: nothing to do.")
    return True

if __name__ == "__main__":
    ok = True
    for f, edits in JOBS.items():
        if len(sys.argv) > 1 and f not in sys.argv[1:]: continue
        print(f"{f}:")
        try: ok = patch(f, edits) and ok
        except FileNotFoundError: print(f"  {f}: not found in this folder."); ok = False
    sys.exit(0 if ok else 1)