import React from "react";
import BudgetBreakupForm from "../budget/BudgetBreakupForm";
import { breakupFromEvent, computeTotal, formatRupees } from "../budget/budgetUtils";
import "./modifications.css";

export const FIELD_LABELS = {
  title: "Event title",
  description: "Description",
  category: "Category",
  date: "Date",
  time: "Time",
  location: "Venue",
  event_mode: "Mode of event",
  school_name: "School",
  department: "Department",
  capacity: "Capacity",
  skills: "Skills",
  poster_url: "Poster",
  spoc_name: "SPOC name",
  spoc_designation: "SPOC designation",
  spoc_email: "SPOC email",
  spoc_contact: "SPOC contact",
  resource_persons: "Resource persons",
  budget_breakup: "Budget",
};

export const STATUS_LABELS = {
  pending_admin_review: ["Waiting for Admin review", "warn"],
  request_rejected: ["Request rejected", "bad"],
  editing: ["Approved: prepare your changes", "good"],
  changes_requested: ["Changes requested: edit and resubmit", "warn"],
  pending_faculty: ["With Faculty", ""],
  pending_hod: ["With HOD", ""],
  pending_dean: ["With Dean", ""],
  pending_admin: ["Awaiting final Admin approval", ""],
  rejected: ["Rejected", "bad"],
  published: ["Published (live)", "good"],
  cancelled: ["Withdrawn", ""],
};

export function StatusBadge({ status }) {
  const [text, tone] = STATUS_LABELS[status] || [status, ""];
  return <span className={`mod-badge ${tone}`}>{text}</span>;
}

// Plain-text view of any field value.
export function formatValue(field, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "resource_persons" && Array.isArray(value)) {
    const names = value.map((p) => [p.name, p.designation].filter(Boolean).join(", ")).filter(Boolean);
    return names.length ? names.join(" · ") : "—";
  }
  if (field === "skills" && Array.isArray(value)) return value.join(", ") || "—";
  if (field === "budget_breakup" && typeof value === "object") {
    try {
      return `Total ${formatRupees(computeTotal(breakupFromEvent(value)))}`;
    } catch {
      return "Updated budget breakup";
    }
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * rows: [{ field, original, proposed }]
 * Shows the approved (live) value next to the proposed one.
 */
export default function ModificationCompare({ rows }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="table-wrap" style={{ overflowX: "auto" }}>
      <table className="mod-compare">
        <thead>
          <tr>
            <th>Field</th>
            <th>Original (live)</th>
            <th>Proposed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const changed = formatValue(r.field, r.original) !== formatValue(r.field, r.proposed);
            return (
              <React.Fragment key={r.field}>
                <tr>
                  <td><b>{FIELD_LABELS[r.field] || r.field}</b></td>
                  <td className={changed ? "mod-old" : ""}>{formatValue(r.field, r.original)}</td>
                  <td className={changed ? "mod-new" : ""}>{formatValue(r.field, r.proposed)}</td>
                </tr>
                {r.field === "budget_breakup" && (
                  <tr>
                    <td colSpan={3}>
                      <details className="mod-details">
                        <summary>View budget breakup (original and proposed)</summary>
                        <p className="mod-note">Original</p>
                        <div className="mod-bbf">
                          <BudgetBreakupForm value={breakupFromEvent(r.original)} readOnly idPrefix={`mod-orig`} />
                        </div>
                        <p className="mod-note">Proposed</p>
                        <div className="mod-bbf">
                          <BudgetBreakupForm value={breakupFromEvent(r.proposed)} readOnly idPrefix={`mod-new`} />
                        </div>
                      </details>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Rows from an approvals-queue item (item.modification)
export function rowsFromQueueItem(modification) {
  if (!modification) return [];
  return (modification.changed_fields || []).map((f) => ({
    field: f,
    original: modification.original_values ? modification.original_values[f] : undefined,
    proposed: modification.proposed_values ? modification.proposed_values[f] : undefined,
  }));
}
