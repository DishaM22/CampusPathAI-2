"""
patch_student_class_timetable.py - switches the student side to the official class timetable.

Usage (from any folder):

    python patch_student_class_timetable.py --frontend "D:\\path\\to\\frontend\\src" --backend "D:\\path\\to\\backend"

Add --check to only test that every edit can be applied (nothing is written).

What it changes
---------------
  src/pages/StudentOnboarding.jsx
      step 6 "Timetable" -> "Select Your Official Class" (no more timetable upload)
  src/pages/StudentDashboard.jsx
      "edit timetable" now opens the read-only official timetable page
      + a small notice for students who have not chosen a class yet
  src/features/studentDashboard/components/StudentDashboardUI.jsx
      3 text labels (the "Edit Timetable" button etc.)
  backend/main.py
      POST /api/student/timetable (student upload) is retired (returns 410)

Safety
------
  * every edit must match exactly ONE place, otherwise nothing is written
  * each changed file is first copied to <file>.bak_class_timetable
  * running it twice is harmless (edits already applied are skipped)
  * line endings (Windows / Unix) are preserved
"""

import argparse
import re
import shutil
import sys
from pathlib import Path


# ============================================================
# EDIT HELPERS
# ============================================================

class Edit:
    """One change. `apply(text, nl)` returns (new_text, status)."""

    def __init__(self, label, done_marker):
        self.label = label
        self.done_marker = done_marker

    def apply(self, text, nl):  # pragma: no cover - overridden
        raise NotImplementedError


class Replace(Edit):
    """Exact text replacement; `old` and `new` use \\n, converted to the file's newline."""

    def __init__(self, label, old, new, done_marker):
        super().__init__(label, done_marker)
        self.old, self.new = old, new

    def apply(self, text, nl):
        if self.done_marker.replace("\n", nl) in text:
            return text, "already applied"
        old, new = self.old.replace("\n", nl), self.new.replace("\n", nl)
        count = text.count(old)
        if count == 1:
            return text.replace(old, new), "applied"
        raise ValueError(f"expected 1 match, found {count}")


class RegexReplace(Edit):
    """Whitespace-tolerant replacement. `new` may be a function(match, nl) -> str."""

    def __init__(self, label, pattern, new, done_marker):
        super().__init__(label, done_marker)
        self.pattern, self.new = re.compile(pattern, re.S), new

    def apply(self, text, nl):
        if self.done_marker in text:
            return text, "already applied"
        matches = list(self.pattern.finditer(text))
        if len(matches) == 1:
            match = matches[0]
            new = self.new(match, nl) if callable(self.new) else self.new.replace("\n", nl)
            return text[: match.start()] + new + text[match.end():], "applied"
        raise ValueError(f"expected 1 match, found {len(matches)}")


class SliceReplace(Edit):
    """Replace everything from the line holding `start` to the end of `end`."""

    def __init__(self, label, start, end, new, done_marker):
        super().__init__(label, done_marker)
        self.start, self.end, self.new = start, end, new

    def apply(self, text, nl):
        start_text, end_text = self.start.replace("\n", nl), self.end.replace("\n", nl)
        if self.done_marker in text:
            return text, "already applied"
        starts = text.count(start_text)
        ends = text.count(end_text)

        if starts != 1 or ends != 1:
            raise ValueError(f"expected 1 start and 1 end, found {starts} and {ends}")

        a = text.index(start_text)
        # include the whole line the start text sits on, plus the banner line above it
        banner = text.rfind("  // ====", 0, a)
        if banner != -1 and text[banner:a].count(nl) <= 1:
            a = banner
        b = text.index(end_text, a) + len(end_text)
        if b <= a:
            raise ValueError("end appears before start")

        return text[:a] + self.new.replace("\n", nl) + text[b:], "applied"


# ============================================================
# THE EDITS
# ============================================================

ONBOARDING = [
    Replace(
        "import the new step",
        'import TimetablePage from "../features/onboarding/timetable/TimetablePage";',
        'import { ClassSelectStep } from "../features/studentClass/StudentClass";',
        "features/studentClass/StudentClass",
    ),
    Replace(
        'progress label "Timetable" -> "Class"',
        '  "Domains",\n  "Timetable",\n];',
        '  "Domains",\n  "Class",\n];',
        '  "Class",\n];',
    ),
    SliceReplace(
        "replace the timetable upload step with class selection",
        start="// TIMETABLE COMPLETED",
        end="""  if (currentStep === 6) {
    return (
      <TimetablePage
        onBack={() => setCurrentStep(5)}
        onContinue={handleTimetableContinue}
      />
    );
  }""",
        new="""  // ============================================================
  // CLASS CHOSEN
  // ClassSelectStep has already saved the class and the availability
  // preferences. Nothing else is uploaded here any more.
  // ============================================================

  const handleClassDone = () => {
    const studentEmail = (
      localStorage.getItem("userEmail") || ""
    )
      .trim()
      .toLowerCase();

    if (studentEmail) {
      localStorage.setItem(
        `onboardingCompleted_${studentEmail}`,
        "true"
      );
    }

    // Keep old flag for compatibility
    localStorage.setItem("profileCompleted", "true");

    navigate("/dashboard");
  };

  // ============================================================
  // STEP 6 - SELECT YOUR OFFICIAL CLASS
  //
  // Returned separately (like the old timetable step) so it has its
  // own header and progress bar.
  // ============================================================

  if (currentStep === 6) {
    return (
      <ClassSelectStep
        profile={profile}
        onBack={() => setCurrentStep(5)}
        onDone={handleClassDone}
      />
    );
  }""",
        done_marker="const handleClassDone",
    ),
    RegexReplace(
        "step 5 closing text",
        r"Next, we will collect your timetable\s+so CampusPath AI can recommend events\s+during your available periods\.",
        "Next, you will choose your official class so CampusPath AI can use your university timetable to recommend events during your free periods.",
        "you will choose your official class",
    ),
]

DASHBOARD = [
    Replace(
        "import the class page",
        'import TimetableEditPage from "../features/onboarding/timetable/TimetableEditPage";',
        'import {\n  ClassPrompt,\n  StudentClassTimetablePage,\n} from "../features/studentClass/StudentClass";',
        "StudentClassTimetablePage,",
    ),
    Replace(
        "state for the timetable details",
        "  const [timetable, setTimetable] = useState([]);",
        "  const [timetable, setTimetable] = useState([]);\n  const [timetableMeta, setTimetableMeta] = useState(null);",
        "const [timetableMeta, setTimetableMeta]",
    ),
    RegexReplace(
        "remember where the timetable came from",
        r"setTimetable\(\s*timetableData\?\.timetable\s*\|\|\s*timetableData\s*\|\|\s*\[\]\s*\);",
        lambda m, nl: m.group(0)
        + nl
        + "        setTimetableMeta("
        + nl
        + "          timetableData && timetableData.source ? timetableData : null"
        + nl
        + "        );",
        "setTimetableMeta(",
    ),
    RegexReplace(
        "open the read-only class page instead of the timetable editor",
        r"<TimetableEditPage\s+onCancel=\{\(\) => setEditingTimetable\(false\)\}\s+onSaved=\{\(\) => \{\s+setEditingTimetable\(false\);\s+setLoading\(true\);\s+setReloadKey\(\(key\) => key \+ 1\);\s+\}\}\s+/>",
        """<StudentClassTimetablePage
        onBack={(changed) => {
          setEditingTimetable(false);

          if (changed) {
            setLoading(true);
            setReloadKey((key) => key + 1);
          }
        }}
      />""",
        "<StudentClassTimetablePage",
    ),
    RegexReplace(
        "show the 'choose your class' notice",
        r"(?m)^([ \t]*)<CampusAIBot />",
        lambda m, nl: (
            f"{m.group(1)}<ClassPrompt{nl}"
            f"{m.group(1)}  meta={{timetableMeta}}{nl}"
            f"{m.group(1)}  onOpen={{() => setEditingTimetable(true)}}{nl}"
            f"{m.group(1)}/>{nl}{nl}"
            f"{m.group(1)}<CampusAIBot />"
        ),
        "<ClassPrompt",
    ),
]

DASHBOARD_UI = [
    Replace(
        'button label "Edit Timetable"',
        '"Edit Timetable"',
        '"My class and timetable"',
        '"My class and timetable"',
    ),
    Replace(
        "profile card hint",
        '"Collected during onboarding. Used for availability matching."',
        '"Your official class timetable from your university. Used for availability matching."',
        "Your official class timetable from your university",
    ),
    Replace(
        "calendar subtitle",
        "Your timetable from onboarding, with registered",
        "Your official class timetable, with registered",
        "Your official class timetable, with registered",
    ),
]

BACKEND_MAIN = [
    SliceReplace(
        "retire the student timetable upload (POST /api/student/timetable)",
        start='@app.post("/api/student/timetable")',
        end="""        "message":
            "Timetable saved successfully!"

    }""",
        new='''@app.post("/api/student/timetable")
def save_student_timetable(
    timetable_data: dict,
    authorization: str = Header(None)
):
    """
    RETIRED. Students no longer enter their own timetable: it comes from the
    official timetable the Admin publishes for the class the student chose
    (PATCH /api/student/class). Availability preferences are saved with
    PUT /api/student/availability-preferences.

    Timetables students saved earlier are kept (read only) as a fallback for
    students who have not chosen a class yet.
    """

    # Still require a valid login.
    get_student_id_from_token(
        authorization
    )

    raise HTTPException(
        status_code=410,
        detail=(
            "Students can no longer upload their own timetable. "
            "Choose your official class instead."
        )
    )''',
        done_marker="RETIRED. Students no longer enter their own timetable",
    ),
]

TARGETS = [
    ("frontend", "pages/StudentOnboarding.jsx", ONBOARDING),
    ("frontend", "pages/StudentDashboard.jsx", DASHBOARD),
    ("frontend", "features/studentDashboard/components/StudentDashboardUI.jsx", DASHBOARD_UI),
    ("backend", "main.py", BACKEND_MAIN),
]


# ============================================================
# RUNNER
# ============================================================

def process(root, relative, edits, check_only):
    path = Path(root) / relative
    if not path.is_file():
        print(f"  MISSING  {path}")
        return None

    raw = path.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw
    ok = True

    print(f"\n{relative}")
    for edit in edits:
        try:
            text, status = edit.apply(text, nl)
            print(f"  {status:<16} {edit.label}")
        except ValueError as error:
            ok = False
            print(f"  NOT APPLIED      {edit.label}  ({error})")

    return (path, raw, text) if ok else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", required=True, help="the frontend 'src' folder")
    parser.add_argument("--backend", required=True, help="the backend folder (has main.py)")
    parser.add_argument("--check", action="store_true", help="test only, write nothing")
    args = parser.parse_args()

    roots = {"frontend": args.frontend, "backend": args.backend}
    results = []

    for kind, relative, edits in TARGETS:
        results.append(process(roots[kind], relative, edits, args.check))

    if any(item is None for item in results):
        print("\nNothing was written: fix the lines marked above first.")
        print("(An edit fails when the file differs from the version I was given.)")
        sys.exit(1)

    if args.check:
        print("\nAll edits can be applied. Run again without --check to apply them.")
        return

    for path, raw, text in results:
        if text == raw:
            continue
        backup = path.with_name(path.name + ".bak_class_timetable")
        if not backup.exists():
            shutil.copyfile(path, backup)
        path.write_bytes(text.encode("utf-8"))
        print(f"\nupdated {path}  (backup: {backup.name})")

    print("\nDone.")


if __name__ == "__main__":
    main()
