import React, { useEffect, useState } from "react";
import { fetchEventVersions } from "../../modificationService";
import { FIELD_LABELS, formatValue } from "./ModificationCompare";
import "./modifications.css";

const STAGES = [
  ["initial_admin", "Admin (request)"],
  ["faculty", "Faculty"],
  ["hod", "HOD"],
  ["dean", "Dean"],
  ["final_admin", "Admin (final)"],
];

const when = (v) => (v ? new Date(v).toLocaleString() : "—");

/** Modal: every approved version of one event (Version 1 = original). */
export default function EventVersionHistory({ eventId, title, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchEventVersions(eventId)
      .then(setData)
      .catch((e) => setError(e.message || "Could not load the history."));
  }, [eventId]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel mod-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Version history{title ? `: ${title}` : ""}</h2>
        {error && <p className="mod-error">{error}</p>}
        {!data && !error && <p className="mod-note">Loading…</p>}
        {data && (
          <>
            <p className="mod-note">
              Students currently see version {data.current_version}.
            </p>
            {[...data.versions].reverse().map((v) => (
              <div className="mod-card" key={v.version}>
                <div className="mod-row">
                  <b>Version {v.version}</b>
                  {v.version === data.current_version && <span className="mod-badge good">Current</span>}
                  <span className="mod-note">
                    {v.kind === "original" ? "Original event published" : `Approved ${when(v.approved_at)}`}
                  </span>
                </div>
                {v.kind !== "original" && (
                  <>
                    <table className="mod-compare">
                      <thead>
                        <tr><th>Changed</th><th>Before</th><th>After</th></tr>
                      </thead>
                      <tbody>
                        {(v.changed_fields || []).map((f) => (
                          <tr key={f}>
                            <td><b>{FIELD_LABELS[f] || f}</b></td>
                            <td className="mod-old">{formatValue(f, v.previous_data?.[f])}</td>
                            <td className="mod-new">{formatValue(f, v.new_data?.[f])}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="mod-note">
                      Requested by {v.requested_by?.name || "—"} ({v.requested_by?.role || "—"}) on {when(v.submitted_at)}.
                      Reason: {v.reason || "—"}
                    </p>
                    <ul className="mod-timeline">
                      {STAGES.map(([key, label]) => {
                        const d = v.decisions?.[key];
                        if (!d) return null;
                        return (
                          <li key={key}>
                            <b>{label}:</b> {d.decision} by {d.reviewer_name || "—"}
                            {d.remarks ? ` — “${d.remarks}”` : ""} ({when(d.reviewed_at)})
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </div>
            ))}
          </>
        )}
        <div className="modal-actions-right">
          <button className="btn-primary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
