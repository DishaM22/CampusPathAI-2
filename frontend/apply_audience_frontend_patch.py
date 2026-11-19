"""
apply_audience_frontend_patch.py - wires "Publish Event To" into the Admin approvals screen.

Run from the frontend folder:   python apply_audience_frontend_patch.py
Target: src/admin/components/AdminEventApprovals.jsx  (backup is made first)
Same safety rules as the backend patch: anchored edits only, all-or-nothing,
safe to run twice, and it prints what to do by hand if an anchor is missing.
It does not touch any budget code.
"""
import shutil, sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "src/admin/components/AdminEventApprovals.jsx"
E = []
def edit(n, a, b): E.append((n, a, b))

edit("1. imports",
'''import { fetchEventApprovals, reviewEvent } from "../../adminService";
''',
'''import { fetchEventApprovals, reviewEvent } from "../../adminService";
import { reviewEventWithAudience } from "../../audienceService";
import PublishAudiencePicker, {
  EMPTY_AUDIENCE,
  audienceError,
} from "./PublishAudiencePicker";
import PublishedEventAudiences from "./PublishedEventAudiences";
''')

edit("2. audience state",
'''  const [modalError, setModalError] = useState("");
''',
'''  const [modalError, setModalError] = useState("");
  const [audience, setAudience] = useState({ ...EMPTY_AUDIENCE });
''')

edit("3. reset audience when a decision dialog opens",
'''    setModal({ event, decision });
    setRemarks("");
''',
'''    setModal({ event, decision });
    setAudience({ ...EMPTY_AUDIENCE });
    setRemarks("");
''')

edit("4. require + send the audience on final approval",
'''      await reviewEvent(eventId(event), decision, remarks.trim());
''',
'''      if (decision === "approved") {
        const problem = audienceError(audience);
        if (problem) {
          setModalError(problem);
          setBusy(false);
          return;
        }
        await reviewEventWithAudience(
          eventId(event),
          decision,
          remarks.trim(),
          audience
        );
      } else {
        await reviewEvent(eventId(event), decision, remarks.trim());
      }
''')

edit("5. 'Publish Event To' inside the approve dialog",
'''          {decision !== "approved" && (
            <>
              <label htmlFor="remarks">Remarks (required)</label>''',
'''          {decision === "approved" && (
            <>
              <label>Publish Event To</label>
              <PublishAudiencePicker
                value={audience}
                onChange={setAudience}
                disabled={busy}
              />
            </>
          )}

          {decision !== "approved" && (
            <>
              <label htmlFor="remarks">Remarks (required)</label>''')

edit("6. wider dialog for the approve step",
'''        <div className="modal-panel" onClick={(ev) => ev.stopPropagation()}>
          <h2>{title}</h2>''',
'''        <div
          className={`modal-panel ${decision === "approved" ? "pap-modal-wide" : ""}`}
          onClick={(ev) => ev.stopPropagation()}
        >
          <h2>{title}</h2>''')

edit("7. audience panel under the approvals list (embedded dashboard only)",
'''      {renderModal()}
    </Wrap>
  );

  // ---------------- decision modal ----------------''',
'''      {embedded && <PublishedEventAudiences />}

      {renderModal()}
    </Wrap>
  );

  // ---------------- decision modal ----------------''')

raw = open(PATH, "rb").read().decode("utf-8")
nl = "\r\n" if "\r\n" in raw else "\n"
text = raw.replace("\r\n", "\n")
if "PublishAudiencePicker" in text:
    print("Already applied - nothing to do."); sys.exit(0)
bad, work = [], text
for n, a, b in E:
    c = work.count(a)
    if c != 1: bad.append((n, c)); continue
    work = work.replace(a, b)
if bad:
    print("NOTHING WAS WRITTEN. Could not place:")
    for n, c in bad: print(f"- {n}: anchor found {c} time(s)")
    print("Send me the current AdminEventApprovals.jsx and I will give the exact manual edit.")
    sys.exit(1)
shutil.copyfile(PATH, PATH + ".bak_before_audience")
open(PATH, "wb").write(work.replace("\n", nl).encode("utf-8"))
print(f"Applied {len(E)} edits. Backup: {PATH}.bak_before_audience")