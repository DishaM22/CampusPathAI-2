import React, { useEffect, useState } from "react";
import {
  fetchAdminModifications,
  fetchModification,
  initialReviewModification,
} from "../../modificationService";
import ModificationCompare, { StatusBadge } from "../../components/modifications/ModificationCompare";
import EventVersionHistory from "../../components/modifications/EventVersionHistory";
import "../../components/modifications/modifications.css";

const FILTERS = [
  ["pending_admin_review", "Waiting for my review"],
  ["editing", "Approved, being edited"],
  ["pending_admin", "Awaiting final approval"],
  ["published", "Published"],
  ["request_rejected", "Rejected requests"],
  ["all", "All"],
];

const when = (v) => (v ? new Date(v).toLocaleString() : "—");

/**
 * Admin section: Published Event Modification Requests.
 * Step 1 only: the Admin decides whether the organiser may PREPARE a change.
 * Nothing is published here. The revised event still goes HOD -> Dean -> Admin,
 * and the final approval appears in the normal event approvals list above.
 */
export default function AdminModificationRequests() {
  const [filter, setFilter] = useState("pending_admin_review");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState(null); // { data, decision, remarks, busy, error }
  const [history, setHistory] = useState(null);
  const [notice, setNotice] = useState("");

  async function load(f = filter) {
    setLoading(true);
    setError("");
    try {
      const d = await fetchAdminModifications(f);
      setRows(d.modifications || []);
    } catch (e) {
      setError(e.message || "Could not load requests.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  async function open(row) {
    setNotice("");
    try {
      const data = await fetchModification(row.id);
      setDetail({ data, decision: "", remarks: "", busy: false, error: "" });
    } catch (e) {
      setError(e.message || "Could not open this request.");
    }
  }

  async function decide() {
    const { data, decision, remarks } = detail;
    if (decision === "rejected" && remarks.trim().length < 10) {
      setDetail((d) => ({ ...d, error: "Please enter at least 10 characters of remarks." }));
      return;
    }
    setDetail((d) => ({ ...d, busy: true, error: "" }));
    try {
      await initialReviewModification(data.id, decision, remarks.trim());
      setNotice(
        decision === "approved"
          ? "Approved. The organiser can now prepare the revised event. The live event is unchanged until it is fully approved."
          : "Rejected. The published event is unchanged and the organiser was notified."
      );
      setDetail(null);
      await load();
    } catch (e) {
      setDetail((d) => ({ ...d, busy: false, error: e.message || "Could not save the decision." }));
    }
  }

  return (
    <>
      <div className="section-head">
        <h2>✏️ Published event modification requests</h2>
      </div>

      {notice && <div className="sample-banner">{notice}</div>}

      <div className="filter-bar">
        <select className="field" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {FILTERS.map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <button className="btn-ghost" onClick={() => load()}>Refresh</button>
      </div>

      {loading ? (
        <div className="empty-state"><h3>Loading…</h3></div>
      ) : error ? (
        <div className="empty-state"><h3>Unable to load</h3><p>{error}</p></div>
      ) : rows.length === 0 ? (
        <div className="empty-state"><h3>Nothing here</h3></div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Event</th><th>Requested by</th><th>Changes</th><th>Status</th><th>Date</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><b>{r.event_title}</b></td>
                  <td>{r.requested_by_name || "—"}</td>
                  <td style={{ whiteSpace: "normal" }}>{(r.fields || []).join(", ")}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td>{when(r.created_at)}</td>
                  <td><button className="btn-ghost small" onClick={() => open(r)}>View</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="modal-overlay" onClick={() => !detail.busy && setDetail(null)}>
          <div className="modal-panel mod-modal" onClick={(e) => e.stopPropagation()}>
            <h2>{detail.data.event_title}</h2>
            <p className="mod-note">
              Requested by {detail.data.requested_by_name} ({detail.data.requested_by_role}) on {when(detail.data.created_at)}.{" "}
              {detail.data.registered_students} student(s) are currently registered.
            </p>
            <div className="mod-row"><StatusBadge status={detail.data.status} />
              <span className="mod-note">Live version {detail.data.live_version}</span></div>

            <p><b>Reason:</b> {detail.data.reason}</p>
            <ModificationCompare rows={detail.data.comparison} />

            {(detail.data.initial_review || (detail.data.approval_history || []).length > 0) && (
              <ul className="mod-timeline">
                {detail.data.initial_review && (
                  <li>
                    <b>Admin (request):</b> {detail.data.initial_review.decision}
                    {detail.data.initial_review.remarks ? ` — “${detail.data.initial_review.remarks}”` : ""} (
                    {when(detail.data.initial_review.reviewed_at)})
                  </li>
                )}
                {(detail.data.approval_history || []).map((h, i) => (
                  <li key={i}>
                    <b>{h.reviewer_role}:</b> {h.decision} by {h.reviewer_name}
                    {h.remarks ? ` — “${h.remarks}”` : ""} (round {h.submission_version})
                  </li>
                ))}
              </ul>
            )}

            {detail.data.status === "pending_admin_review" && (
              <>
                <p className="mod-note">
                  Approving only lets the organiser prepare a revised event. It is NOT the final
                  publication: the revised event still goes through HOD → Dean → Admin, and students keep
                  seeing the current details until then.
                </p>
                <div className="mod-row">
                  <label className="mod-check">
                    <input type="radio" name="dec" checked={detail.decision === "approved"}
                      onChange={() => setDetail({ ...detail, decision: "approved", error: "" })} /> Approve request
                  </label>
                  <label className="mod-check">
                    <input type="radio" name="dec" checked={detail.decision === "rejected"}
                      onChange={() => setDetail({ ...detail, decision: "rejected", error: "" })} /> Reject request
                  </label>
                </div>
                {detail.decision && (
                  <textarea className="field mod-textarea" maxLength={500}
                    placeholder={detail.decision === "rejected" ? "Remarks (required, min. 10 characters)" : "Remarks (optional)"}
                    value={detail.remarks} onChange={(e) => setDetail({ ...detail, remarks: e.target.value })} />
                )}
              </>
            )}
            {detail.error && <p className="mod-error">{detail.error}</p>}

            <div className="modal-actions-right">
              <button className="btn-ghost" onClick={() => setHistory({ id: detail.data.event_id, title: detail.data.event_title })}>
                Version history
              </button>
              <button className="btn-ghost" disabled={detail.busy} onClick={() => setDetail(null)}>Close</button>
              {detail.data.status === "pending_admin_review" && (
                <button className="btn-primary" disabled={detail.busy || !detail.decision} onClick={decide}>
                  {detail.busy ? "Saving…" : "Save decision"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {history && <EventVersionHistory eventId={history.id} title={history.title} onClose={() => setHistory(null)} />}
    </>
  );
}
