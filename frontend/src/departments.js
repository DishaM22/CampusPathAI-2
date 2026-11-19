// ============================================================
// OFFICIAL DEPARTMENTS
// ------------------------------------------------------------
// ONE list used by HOD registration and every event form. An event
// is sent to the HOD of the department chosen here.
//
// To add a department: add it here AND to OFFICIAL_DEPARTMENTS in the
// backend file departments.py (the backend rejects anything not on
// its own list). Put this file at src/departments.js.
// ============================================================

export const DEPARTMENTS = [
  "CSE",
  "AIML & Robotics",
  "Cyber Security",
  "EEE",
  "Mechanical",
];

const clean = (value) =>
  String(value || "").toLowerCase().replace(/\s+/g, " ").trim();

/* The official department that equals `value` (ignoring case/spaces),
   or "" for older free-text values, so a dropdown never shows a
   department that is not on the list. */
export function matchDepartment(value) {
  return DEPARTMENTS.find((name) => clean(name) === clean(value)) || "";
}