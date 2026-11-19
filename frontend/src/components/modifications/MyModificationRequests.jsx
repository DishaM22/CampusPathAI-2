import React, { useEffect, useState } from "react";
import FieldEditor, { defOf, toApi, toInput } from "./ModificationFields";
import ModificationCompare, { StatusBadge } from "./ModificationCompare";
import EventVersionHistory from "./EventVersionHistory";
import {
  fetchMyModifications,
  fetchModification,
  saveModificationDraft,
  submitModification,
  withdrawModification,
} from "../../modificationService";
import "./modifications.css";

const when = (v) => (v ? new Date(v).toLocaleString() : "—");

/**
 * Club Coordinator / Faculty: all modification requests of their club, with
 * editing of the proposed version (after Admin approval) and submit/withdraw.
 *   <MyModificationRequests />
 */
export default function MyModificationRequests() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState(null); // { data, values, busy, error }
  const [history, setHistory] = useState(null);

  async function load() {
    setLoading(true); setError("");
    try {
      setRows((await fetchMyModifications()).modifications || []);
    } catch (e) {
      setError(e.message || "Could not load your requests.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function open(row) {
    setNotice("");
    try {
      const data = await fetchModification(row.id);
      const values = {};
      (data.comparison || []).forEach((c) => { values[c.field] = toInput(defOf(c.field) || { type: "text" }, c.proposed); });
      setView({ data, values, busy: false, error: "" });
    } catch (e) {
      setError(e.message || "Could not open this request.");
    }
  }

  const proposedPayload = () =>
    Object.fromEntries(view.data.comparison.map((c) => [c.field, toApi(defOf(c.field) || { type: "text" }, view.values[c.field])]));

  async function act(fn, okMessage) {
    setView((v) => ({ ...v, busy: true, error: "" }));
    try {
      await fn();
      setNotice(okMessage);
      setView(null);
      await load();
    } catch (e) {
      setView((v) => ({ ...v, busy: false, error: e.message || "Something went wrong." }));
    }
  }

  const lastReturn = (d) =>
    [...(d.approval_history || [])].reverse().find((h) => h.decision === "changes" || h.decision === "rejected");

  return (
    <>
      <div className="section-head"><h2>✏️ My modification requests</h2></div>
      {notice && <div className="sample-banner">{notice}</div>}
      {loading ? (
        <div className="empty-state"><h3>Loading…</h3></div>
      ) : error ? (
        <div className="empty-state"><h3>Unable to load</h3><p>{error}</p></div>
      ) : rows.length === 0 ? (
        <div className="empty-state"><h3>No modification requests yet</h3>
          <p>Use “Request Modification” on a published event.</p></div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Event</th><th>Changes</th><th>Status</th><th>Updated</th><th></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><b>{r.event_title}</b></td>
                  <td style={{ whiteSpace: "normal" }}>{(r.fields || []).join(", ")}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td>{when(r.updated_at)}</td>
                  <td>
                    <button className="btn-ghost small" onClick={() => open(r)}>
                      {r.status === "editing" || r.status === "changes_requested" ? "Edit" : "View"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {view && (
        <div className="modal-overlay" onClick={() => !view.busy && setView(null)}>
          <div className="modal-panel mod-modal" onClick={(e) => e.stopPropagation()}>
            <h2>{view.data.event_title}</h2>
            <div className="mod-row"><StatusBadge status={view.data.status} />
              <span className="mod-note">Your reason: {view.data.reason}</span></div>

            {view.data.status === "pending_admin_review" && (
              <p className="mod-note">Waiting for the Admin to decide whether you may prepare this change.</p>
            )}
            {["pending_faculty", "pending_hod", "pending_dean", "pending_admin"].includes(view.data.status) && (
              <p className="mod-note">Your revised event is in review. It can't be edited now. Students still see the current details.</p>
            )}
            {view.data.status === "published" && (
              <p className="mod-ok">Applied as version {view.data.applied_version}. The updated details are live.</p>
            )}
            {(view.data.status === "request_rejected" || view.data.status === "rejected" || view.data.status === "changes_requested") && (
              <p className="mod-error">
                {view.data.status === "request_rejected"
                  ? `Admin: ${view.data.initial_review?.remarks || "—"}`
                  : (lastReturn(view.data) ? `${lastReturn(view.data).reviewer_role.toUpperCase()}: ${lastReturn(view.data).remarks}` : "")}
              </p>
            )}

            {view.data.editable ? (
              <>
                <p className="mod-note">
                  Current published value is shown in the box above each field. Only the approved fields can be edited.
                  Nothing goes live until the HOD, Dean and Admin approve.
                </p>
                {view.data.comparison.map((c) => (
                  <div key={c.field}>
                    <FieldEditor def={defOf(c.field) || { key: c.field, label: c.field, type: "text" }}
                      value={view.values[c.field]} disabled={view.busy}
                      onChange={(v) => setView((cur) => ({ ...cur, values: { ...cur.values, [c.field]: v } }))} />
                  </div>
                ))}
                <ModificationCompare rows={view.data.comparison} />
              </>
            ) : (
              <ModificationCompare rows={view.data.comparison} />
            )}

            {(view.data.approval_history || []).length > 0 && (
              <ul className="mod-timeline">
                {view.data.approval_history.map((h, i) => (
                  <li key={i}><b>{h.reviewer_role}:</b> {h.decision}{h.remarks ? ` — “${h.remarks}”` : ""}</li>
                ))}
              </ul>
            )}
            {view.error && <p className="mod-error">{view.error}</p>}

            <div className="modal-actions-right">
              <button className="btn-ghost" onClick={() => setHistory({ id: view.data.event_id, title: view.data.event_title })}>Version history</button>
              <button className="btn-ghost" disabled={view.busy} onClick={() => setView(null)}>Close</button>
              {["pending_admin_review", "editing", "changes_requested"].includes(view.data.status) && (
                <button className="btn-ghost" disabled={view.busy}
                  onClick={() => act(() => withdrawModification(view.data.id), "Request withdrawn.")}>Withdraw</button>
              )}
              {view.data.editable && (
                <>
                  <button className="btn-ghost" disabled={view.busy}
                    onClick={() => act(() => saveModificationDraft(view.data.id, proposedPayload()), "Draft saved.")}>Save draft</button>
                  <button className="btn-primary" disabled={view.busy}
                    onClick={() => act(async () => {
                      await saveModificationDraft(view.data.id, proposedPayload());
                      await submitModification(view.data.id);
                    }, "Submitted for approval. It follows the normal approval stages.")}>
                    {view.busy ? "Working…" : "Submit for approval"}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
      {history && <EventVersionHistory eventId={history.id} title={history.title} onClose={() => setHistory(null)} />}
    </>
  );
}