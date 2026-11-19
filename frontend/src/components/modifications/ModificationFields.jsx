import React from "react";
import BudgetBreakupForm from "../budget/BudgetBreakupForm";
import {
  anySelected,
  breakupFromEvent,
  breakupToPayload,
  computeTotal,
  hasSavedBreakup,
  validateBreakup,
} from "../budget/budgetUtils";
import "./modifications.css";

// Keep in step with backend/departments.py
export const DEPARTMENTS = ["CSE", "AIML & Robotics", "Cyber Security", "EEE", "Mechanical"];

// Fields an organiser can ask to change.
export const FIELD_DEFS = [
  { key: "date", label: "Date", type: "date" },
  { key: "time", label: "Time", type: "text", hint: "start - end, e.g. 10:00 AM - 11:00 AM" },
  { key: "location", label: "Venue", type: "text" },
  { key: "event_mode", label: "Mode of event", type: "select", options: ["Online", "Offline", "Hybrid"] },
  { key: "resource_persons", label: "Resource persons", type: "persons" },
  { key: "budget_breakup", label: "Budget", type: "budget" },
  { key: "capacity", label: "Capacity", type: "number" },
  { key: "title", label: "Event title", type: "text" },
  { key: "description", label: "Description", type: "textarea" },
  { key: "category", label: "Category", type: "text" },
  { key: "skills", label: "Skills", type: "list", hint: "separate with commas" },
  { key: "poster_url", label: "Poster link", type: "text" },
  { key: "school_name", label: "School", type: "text" },
  { key: "department", label: "Department", type: "select", options: DEPARTMENTS },
  { key: "spoc_name", label: "SPOC name", type: "text" },
  { key: "spoc_designation", label: "SPOC designation", type: "text" },
  { key: "spoc_email", label: "SPOC email", type: "text" },
  { key: "spoc_contact", label: "SPOC contact", type: "text" },
];

export const defOf = (key) =>
  FIELD_DEFS.find((d) => d.key === key) || { key, label: key, type: "text" };

// Older events only have a single budget figure and no breakup.
export function legacyTotalOf(event) {
  return !hasSavedBreakup(event?.budget_breakup) && event?.budget != null
    ? Number(event.budget)
    : null;
}

// event/API value -> what the input shows
export function toInput(def, value) {
  if (def.type === "budget") return breakupFromEvent(value);
  if (def.type === "list") return Array.isArray(value) ? value.join(", ") : value || "";
  if (def.type === "persons")
    return Array.isArray(value) && value.length
      ? value.map((p) => ({ name: "", designation: "", organization: "", linkedin_url: "", ...p }))
      : [{ name: "", designation: "", organization: "", linkedin_url: "" }];
  return value === null || value === undefined ? "" : String(value);
}

// what the input holds -> value sent to the API
export function toApi(def, input) {
  if (def.type === "list") return String(input || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (def.type === "number") return input === "" ? null : Number(input);
  if (def.type === "persons")
    return (input || []).filter((p) => Object.values(p).some((v) => String(v || "").trim()));
  return typeof input === "string" ? input.trim() : input;
}

// { field: input } -> the "proposed" object the API expects
// (budget is sent the same way the event form sends it: breakup + its total)
export function buildProposed(fields, values) {
  const out = {};
  fields.forEach((k) => {
    const def = defOf(k);
    if (def.type === "budget") {
      out.budget_breakup = breakupToPayload(values[k]);
      out.budget = computeTotal(values[k]);
    } else {
      out[k] = toApi(def, values[k]);
    }
  });
  return out;
}

// Returns a message, or "" when the values look complete.
export function validateValues(fields, values, event) {
  for (const k of fields) {
    const def = defOf(k);
    if (def.type === "budget") {
      const problem = validateBreakup(values[k]);
      if (problem) return problem;
      if (legacyTotalOf(event) !== null && !anySelected(values[k]))
        return "This event only has an old single budget figure. Tick the applicable rows in the Budget Breakup Form and enter their costs.";
    }
  }
  return "";
}

export default function FieldEditor({ def, value, onChange, disabled, legacyTotal = null }) {
  const id = `mod-${def.key}`;

  if (def.type === "budget") {
    return (
      <div className="mod-field">
        <label>{def.label}</label>
        <div className="mod-bbf">
          <BudgetBreakupForm
            value={value}
            onChange={onChange}
            readOnly={!!disabled}
            legacyTotal={legacyTotal}
            idPrefix="mod-budget"
          />
        </div>
      </div>
    );
  }

  if (def.type === "persons") {
    const rows = value || [];
    const set = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    return (
      <div className="mod-field">
        <label>{def.label}</label>
        <div className="mod-persons">
          {rows.map((p, i) => (
            <div className="mod-person" key={i}>
              <input className="field" placeholder="Name" value={p.name} disabled={disabled} onChange={(e) => set(i, { name: e.target.value })} />
              <input className="field" placeholder="Designation" value={p.designation} disabled={disabled} onChange={(e) => set(i, { designation: e.target.value })} />
              <input className="field" placeholder="Organization" value={p.organization} disabled={disabled} onChange={(e) => set(i, { organization: e.target.value })} />
              <input className="field" placeholder="LinkedIn link (optional)" value={p.linkedin_url} disabled={disabled} onChange={(e) => set(i, { linkedin_url: e.target.value })} />
              <button type="button" className="btn-ghost small" disabled={disabled || rows.length === 1}
                onClick={() => onChange(rows.filter((_, j) => j !== i))}>Remove</button>
            </div>
          ))}
          <div>
            <button type="button" className="btn-ghost small" disabled={disabled || rows.length >= 20}
              onClick={() => onChange([...rows, { name: "", designation: "", organization: "", linkedin_url: "" }])}>
              + Add resource person
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mod-field">
      <label htmlFor={id}>{def.label}{def.hint ? ` (${def.hint})` : ""}</label>
      {def.type === "textarea" ? (
        <textarea id={id} className="field mod-textarea" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      ) : def.type === "select" ? (
        <select id={id} className="field" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          <option value="">Select…</option>
          {def.options.map((o) => <option key={o}>{o}</option>)}
        </select>
      ) : (
        <input id={id} className="field" type={def.type === "number" ? "number" : def.type === "date" ? "date" : "text"}
          min={def.type === "number" ? 1 : undefined} value={value} disabled={disabled}
          onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}