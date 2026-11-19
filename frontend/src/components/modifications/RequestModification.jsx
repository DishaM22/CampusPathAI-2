import React, { useEffect, useState } from "react";
import FieldEditor, {
  FIELD_DEFS,
  buildProposed,
  defOf,
  legacyTotalOf,
  toInput,
  validateValues,
} from "./ModificationFields";
import { fetchEventForEdit, requestModification } from "../../modificationService";
import "./modifications.css";

/** True for events students can already see (published, or older events with no status). */
export const isLiveEvent = (event) =>
  !!event && (!event.approval_status || event.approval_status === "published");

/**
 * The "Request Modification" dialog for an ALREADY PUBLISHED event.
 * Nothing changes on the live event: the request goes to the Admin first.
 *   <RequestModificationModal event={event} onClose={...} onDone={...} />
 */
export function RequestModificationModal({ event, onClose, onDone }) {
  const eventId = event?.id || event?._id;
  const [full, setFull] = useState(null); // the complete event (all fields, budget for the owner)
  const [step, setStep] = useState(1);
  const [picked, setPicked] = useState([]);
  const [values, setValues] = useState({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  useEffect(() => {
    let live = true;
    fetchEventForEdit(eventId)
      .then((e) => live && setFull(e))
      .catch(() => live && setFull(event)); // fall back to what the card knows
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  const toggle = (key) =>
    setPicked((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));

  function goToValues() {
    if (picked.length === 0) { setError("Select at least one field."); return; }
    setError("");
    setValues((cur) => {
      const next = { ...cur };
      picked.forEach((k) => { if (!(k in next)) next[k] = toInput(defOf(k), full[k]); });
      return next;
    });
    setStep(2);
  }

  async function submit() {
    if (reason.trim().length < 10) { setError("Please give a reason (at least 10 characters)."); return; }
    const problem = validateValues(picked, values, full);
    if (problem) { setError(problem); return; }
    setBusy(true); setError("");
    try {
      await requestModification(eventId, {
        fields: picked,
        proposed: buildProposed(picked, values),
        reason: reason.trim(),
      });
      setDone("Request sent to the Admin. The published event stays unchanged until a revised version is fully approved.");
      if (onDone) onDone();
    } catch (e) {
      setError(e.message || "Could not send the request.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-panel mod-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Request modification: {event.title}</h2>

        {!full ? (
          <p className="mod-note">Loading the event…</p>
        ) : done ? (
          <>
            <p className="mod-ok">{done}</p>
            <div className="modal-actions-right">
              <button className="btn-primary" onClick={onClose}>Close</button>
            </div>
          </>
        ) : step === 1 ? (
          <>
            <p className="mod-note">
              This event is already published, so it cannot be edited directly. Choose what needs to change.
              The Admin reviews your request first; students keep seeing the current details until the
              revised event is approved by the HOD, Dean and Admin.
            </p>
            <div className="mod-grid">
              {FIELD_DEFS.map((d) => (
                <label className="mod-check" key={d.key}>
                  <input type="checkbox" checked={picked.includes(d.key)} onChange={() => toggle(d.key)} />
                  {d.label}
                </label>
              ))}
            </div>
            {error && <p className="mod-error">{error}</p>}
            <div className="modal-actions-right">
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={goToValues}>Next</button>
            </div>
          </>
        ) : (
          <>
            {picked.map((k) => (
              <FieldEditor key={k} def={defOf(k)} value={values[k]} disabled={busy}
                legacyTotal={defOf(k).type === "budget" ? legacyTotalOf(full) : null}
                onChange={(v) => setValues((cur) => ({ ...cur, [k]: v }))} />
            ))}
            <div className="mod-field">
              <label htmlFor="mod-reason">Reason for the modification (required)</label>
              <textarea id="mod-reason" className="field mod-textarea" maxLength={1000} value={reason}
                disabled={busy} onChange={(e) => setReason(e.target.value)} />
            </div>
            {error && <p className="mod-error">{error}</p>}
            <div className="modal-actions-right">
              <button className="btn-ghost" disabled={busy} onClick={() => setStep(1)}>Back</button>
              <button className="btn-primary" disabled={busy} onClick={submit}>
                {busy ? "Sending…" : "Send to Admin"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** A button that opens the dialog (for places that don't manage their own state). */
export default function RequestModificationButton({ event, onDone, label = "Request Modification" }) {
  const [open, setOpen] = useState(false);
  if (!event || !(event.id || event._id) || !isLiveEvent(event)) return null;
  return (
    <>
      <button type="button" className="btn-ghost small" onClick={() => setOpen(true)}>{label}</button>
      {open && <RequestModificationModal event={event} onClose={() => setOpen(false)} onDone={onDone} />}
    </>
  );
}