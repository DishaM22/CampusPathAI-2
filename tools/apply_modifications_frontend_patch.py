"""
apply_modifications_frontend_patch.py - Phases 6-8 for the Admin screens.

Run from the frontend folder (after the earlier two frontend patches):
    python apply_modifications_frontend_patch.py
  src/admin/components/AdminEventApprovals.jsx : the Approve dialog understands a
        MODIFICATION (shows original vs proposed, no audience picker because the
        event already has one, optional "share the reason with students")
  src/pages/AdminDashboard.jsx : shows "Published event modification requests"
Backups first, anchored edits only, all-or-nothing per file, safe to run twice.
"""
import shutil, sys

JOBS = {
 "src/admin/components/AdminEventApprovals.jsx": [
  ("imports", "reviewModificationDecision",
'''import PublishedEventAudiences from "./PublishedEventAudiences";
''',
'''import PublishedEventAudiences from "./PublishedEventAudiences";
import { reviewModificationDecision } from "../../modificationService";
import ModificationCompare, {
  rowsFromQueueItem,
} from "../../components/modifications/ModificationCompare";
'''),
  ("state", "const [shareReason, setShareReason]",
'''  const [audience, setAudience] = useState({ ...EMPTY_AUDIENCE });
''',
'''  const [audience, setAudience] = useState({ ...EMPTY_AUDIENCE });
  const [shareReason, setShareReason] = useState(false);
'''),
  ("reset when a dialog opens", "setShareReason(false);",
'''    setModal({ event, decision });
    setAudience({ ...EMPTY_AUDIENCE });
''',
'''    setModal({ event, decision });
    setAudience({ ...EMPTY_AUDIENCE });
    setShareReason(false);
'''),
  ("final approval of a modification", "event.is_modification) {",
'''      if (decision === "approved") {
        const problem = audienceError(audience);
''',
'''      if (decision === "approved" && event.is_modification) {
        // A modification keeps the event's existing audience; this approval
        // applies the approved changes to the live event.
        await reviewModificationDecision(
          eventId(event),
          decision,
          remarks.trim(),
          shareReason
        );
      } else if (decision === "approved") {
        const problem = audienceError(audience);
'''),
  ("dialog title", '"Approve modification & publish changes"',
'''        ? "Approve & publish event"
''',
'''        ? event.is_modification
          ? "Approve modification & publish changes"
          : "Approve & publish event"
'''),
  ("dialog message", "The approved changes will replace",
'''            {decision === "approved"
              ? `"${event.title}" will be published and visible to students.`
''',
'''            {decision === "approved"
              ? event.is_modification
                ? "The approved changes will replace the current details of this event. Registered students are notified of important changes."
                : `"${event.title}" will be published and visible to students.`
'''),
  ("modification details + audience picker only for new events", "decision === \"approved\" && event.is_modification && (",
'''          {decision === "approved" && (
            <>
              <label>Publish Event To</label>''',
'''          {decision === "approved" && event.is_modification && (
            <>
              <p className="modal-message">
                <b>Reason:</b> {event.modification?.reason || "—"}
              </p>
              <ModificationCompare rows={rowsFromQueueItem(event.modification)} />
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={shareReason}
                  disabled={busy}
                  onChange={(ev) => setShareReason(ev.target.checked)}
                />
                Share the organiser's reason with the notified students
              </label>
            </>
          )}

          {decision === "approved" && !event.is_modification && (
            <>
              <label>Publish Event To</label>'''),
 ],
 "src/pages/AdminDashboard.jsx": [
  ("import", "AdminModificationRequests",
'''import AcademicProgression from "../admin/components/AcademicProgression";
''',
'''import AcademicProgression from "../admin/components/AcademicProgression";
import AdminModificationRequests from "../admin/components/AdminModificationRequests";
'''),
  ("render after the event approvals", "<AdminModificationRequests />",
'''              {/* =================================================
                  ACADEMIC PROGRESSION (Admin only)
              ================================================= */}
''',
'''              {/* =================================================
                  PUBLISHED EVENT MODIFICATION REQUESTS (Admin only)
              ================================================= */}

              <AdminModificationRequests />

              {/* =================================================
                  ACADEMIC PROGRESSION (Admin only)
              ================================================= */}
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
        print("   Send me the current file and I will give the exact manual edit.")
        return False
    if todo:
        shutil.copyfile(path, path + ".bak_before_modifications")
        open(path, "wb").write(work.replace("\n", nl).encode("utf-8"))
        print(f"{path}: done. Backup: {path}.bak_before_modifications")
    else:
        print(f"{path}: nothing to do.")
    return True

if __name__ == "__main__":
    ok = True
    for f, edits in JOBS.items():
        print(f"{f}:")
        try: ok = patch(f, edits) and ok
        except FileNotFoundError: print(f"  not found: {f}"); ok = False
    sys.exit(0 if ok else 1)
