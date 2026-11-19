// ============================================================
// ADMIN - CLASS & TIMETABLE MANAGEMENT
//
// Two pages in this file:
//   AdminClasses          (default)  /admin/classes
//        create / edit / deactivate classes
//   AdminTimetableEditor  (named)    /admin/classes/:id/timetable
//        weekly timetable editor with draft / preview / publish
//   AdminTimetableImport  (named)    /admin/classes/import
//        upload ONE PDF with many section timetables, review, then save
//
// Uses the existing admin shell (Sidebar, TopBar, ConfirmModal), the existing
// weekly grid (TimetableGrid) and timeUtils. New styles are all prefixed
// "ctm-" in ../class-timetable.css so nothing existing is restyled.
// ============================================================

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import Sidebar from "../admin/components/Sidebar";
import TopBar from "../admin/components/TopBar";
import ConfirmModal from "../admin/components/ConfirmModal";
import { EmptyState } from "../admin/components/States";

import TimetableGrid from "../features/onboarding/timetable/components/TimetableGrid";
import {
  DAYS,
  DAY_LABELS,
  formatTime,
  toMinutes,
  toHHMM,
} from "../features/onboarding/timetable/timeUtils";

import "../features/onboarding/timetable/timetable.css";
import "../admin-dashboard.css";
import "../class-timetable.css";

// ============================================================
// CONSTANTS
// ============================================================

const API_BASE = "http://127.0.0.1:8000";

// Same list as the student onboarding "Department" step, so the class a
// student has to pick always uses names they recognise.
const DEPARTMENTS = [
  "Artificial Intelligence",
  "Computer Science & Engineering",
  "Information Science",
  "Electronics & Communication",
  "Electrical & Electronics",
  "Mechanical Engineering",
  "Civil Engineering",
  "Other",
];

const YEARS = [1, 2, 3, 4];
const SEMESTERS = [1, 2, 3, 4, 5, 6, 7, 8];
const SLOT_OPTIONS = [15, 30, 45, 60, 90, 120];

const SESSION_TYPES = [
  { value: "lecture", label: "Lecture" },
  { value: "lab", label: "Laboratory" },
  { value: "tutorial", label: "Tutorial" },
  { value: "seminar", label: "Seminar" },
  { value: "other", label: "Other class" },
  { value: "lunch", label: "Lunch" },
  { value: "break", label: "Break" },
];

const CLASS_TYPES = ["lecture", "lab", "tutorial", "seminar", "other"];
const TYPE_LABEL = Object.fromEntries(
  SESSION_TYPES.map((type) => [type.value, type.label])
);

const DEFAULT_CONFIG = {
  collegeStartTime: "08:00",
  collegeEndTime: "17:00",
  slotDuration: 60,
};

const NOTIFICATIONS = [
  {
    id: 1,
    message: "Admin dashboard notifications will appear here.",
    time: "Now",
    read: true,
  },
];

// ============================================================
// API (same conventions as adminService.js: Bearer token, JSON)
// ============================================================

function readError(data, status) {
  const detail = data?.detail;

  if (typeof detail === "string") {
    return detail;
  }

  // FastAPI request-validation errors arrive as a list of {msg, loc, ...}
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || String(item))
      .join("; ");
  }

  return data?.message || `Request failed (${status})`;
}

async function api(path, options = {}) {
  const token = localStorage.getItem("token");

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });

  const text = await response.text();
  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    throw new Error(readError(data, response.status));
  }

  return data;
}

const classApi = {
  list: () => api("/api/admin/classes"),

  create: (body) =>
    api("/api/classes", { method: "POST", body: JSON.stringify(body) }),

  update: (id, body) =>
    api(`/api/classes/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  timetable: (id) =>
    api(`/api/admin/classes/${encodeURIComponent(id)}/timetable`),

  saveDraft: (id, body) =>
    api(`/api/admin/classes/${encodeURIComponent(id)}/timetable`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  publish: (timetableId) =>
    api(
      `/api/admin/timetables/${encodeURIComponent(timetableId)}/publish`,
      { method: "POST" }
    ),

  importPreview: (fileName, base64) =>
    api("/api/admin/timetable-import/preview", {
      method: "POST",
      body: JSON.stringify({ file_name: fileName, data: base64 }),
    }),

  importCommit: (body) =>
    api("/api/admin/timetable-import/commit", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ============================================================
// SMALL HELPERS
// ============================================================

function currentAcademicYear() {
  const now = new Date();
  const year = now.getFullYear();
  // The academic year starts around June.
  const start = now.getMonth() >= 5 ? year : year - 1;
  return `${start}-${start + 1}`;
}

function newBlockId() {
  return `blk_${Math.random().toString(36).slice(2, 10)}`;
}

function formatWhen(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function normalizeTimetable(raw) {
  const result = {};

  DAYS.forEach((day) => {
    const blocks = Array.isArray(raw?.[day]) ? raw[day] : [];
    result[day] = blocks
      .map((block) => ({ ...block, id: block.id || newBlockId() }))
      .sort((a, b) => toMinutes(a.startTime) - toMinutes(b.startTime));
  });

  return result;
}

function overlaps(aStart, aEnd, bStart, bEnd) {
  return (
    toMinutes(aStart) < toMinutes(bEnd) &&
    toMinutes(aEnd) > toMinutes(bStart)
  );
}

function countClasses(timetable) {
  return DAYS.reduce(
    (sum, day) =>
      sum +
      (timetable[day] || []).filter((block) =>
        CLASS_TYPES.includes(block.type)
      ).length,
    0
  );
}

// Mirrors the server rules so problems show up before saving. The server
// still re-checks everything and is the authority.
function validateWeek(timetable, config) {
  const issues = [];
  const open = toMinutes(config.collegeStartTime);
  const close = toMinutes(config.collegeEndTime);

  DAYS.forEach((day) => {
    const blocks = [...(timetable[day] || [])].sort(
      (a, b) => toMinutes(a.startTime) - toMinutes(b.startTime)
    );

    blocks.forEach((block, index) => {
      if (
        toMinutes(block.startTime) < open ||
        toMinutes(block.endTime) > close
      ) {
        issues.push(
          `${DAY_LABELS[day]}: "${block.subject}" is outside college hours (${config.collegeStartTime}–${config.collegeEndTime}).`
        );
      }

      if (CLASS_TYPES.includes(block.type) && !block.courseCode) {
        issues.push(
          `${DAY_LABELS[day]}: "${block.subject}" needs a subject code.`
        );
      }

      const next = blocks[index + 1];
      if (
        next &&
        overlaps(block.startTime, block.endTime, next.startTime, next.endTime)
      ) {
        issues.push(
          `${DAY_LABELS[day]}: "${block.subject}" overlaps "${next.subject}".`
        );
      }
    });
  });

  return issues;
}

function useEscape(onClose) {
  useEffect(() => {
    function handle(event) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [onClose]);
}

// ============================================================
// SHARED PIECES
// ============================================================

function AdminShell({ children }) {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="dashboard-shell">
      <Sidebar
        role="admin"
        activeKey="classes"
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((current) => !current)}
        onLogout={() => {
          localStorage.removeItem("token");
          localStorage.removeItem("user");
          localStorage.removeItem("userEmail");
          localStorage.removeItem("userRole");
          localStorage.removeItem("student_id");
          navigate("/");
        }}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />

      <div className="dashboard-main">
        <TopBar
          orgInitial="A"
          notifications={NOTIFICATIONS}
          onNavigate={navigate}
          onMenuToggle={() => setMobileOpen(true)}
          basePath="/admin"
        />

        <main className="content ctm-content">{children}</main>
      </div>
    </div>
  );
}

function Banner({ kind, children, onDismiss }) {
  if (!children) return null;

  return (
    <div
      className={`ctm-banner ctm-banner-${kind}`}
      role={kind === "error" ? "alert" : "status"}
    >
      <span>{children}</span>
      {onDismiss && (
        <button
          type="button"
          className="ctm-banner-close"
          aria-label="Dismiss message"
          onClick={onDismiss}
        >
          ✕
        </button>
      )}
    </div>
  );
}

function LoadingBlock({ label }) {
  return (
    <div className="ctm-loading" role="status" aria-live="polite">
      <div className="ctm-skel" />
      <div className="ctm-skel" />
      <div className="ctm-skel short" />
      <span className="ctm-sr">{label}</span>
    </div>
  );
}

// ============================================================
// PAGE 1 - CLASS LIST
// ============================================================

function ClassFormModal({ editing, onClose, onSubmit }) {
  useEscape(onClose);

  const [form, setForm] = useState(() => ({
    department: editing?.department || "",
    academic_year: editing?.academic_year || currentAcademicYear(),
    year_of_study: editing?.year_of_study || 4,
    semester: editing?.semester || 7,
    section: editing?.section || "",
    batch: editing?.batch || "",
    name: editing?.name || "",
  }));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const departmentOptions = DEPARTMENTS.includes(form.department) || !form.department
    ? DEPARTMENTS
    : [form.department, ...DEPARTMENTS];

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (!form.department) return setError("Choose a department.");
    if (!/^\d{4}-\d{4}$/.test(form.academic_year.trim())) {
      return setError("Academic year must look like 2026-2027.");
    }
    if (!form.section.trim()) return setError("Enter the section, e.g. A.");

    setSaving(true);

    try {
      await onSubmit({
        department: form.department,
        academic_year: form.academic_year.trim(),
        year_of_study: Number(form.year_of_study),
        semester: Number(form.semester),
        section: form.section.trim(),
        batch: form.batch.trim(),
        name: form.name.trim(),
      });
    } catch (err) {
      setError(err.message || "Could not save the class.");
      setSaving(false);
    }
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={editing ? "Edit class" : "New class"}
    >
      <form className="modal-panel ctm-modal" onSubmit={handleSubmit} noValidate>
        <h2>{editing ? "Edit class" : "New class"}</h2>

        <label htmlFor="ctm-department">Department</label>
        <select
          id="ctm-department"
          className="field"
          value={form.department}
          onChange={(e) => update("department", e.target.value)}
        >
          <option value="">Select department</option>
          {departmentOptions.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <div className="ctm-row">
          <div>
            <label htmlFor="ctm-year-of-study">Year of study</label>
            <select
              id="ctm-year-of-study"
              className="field"
              value={form.year_of_study}
              onChange={(e) => update("year_of_study", e.target.value)}
            >
              {YEARS.map((year) => (
                <option key={year} value={year}>
                  Year {year}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="ctm-semester">Semester</label>
            <select
              id="ctm-semester"
              className="field"
              value={form.semester}
              onChange={(e) => update("semester", e.target.value)}
            >
              {SEMESTERS.map((sem) => (
                <option key={sem} value={sem}>
                  Semester {sem}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="ctm-row">
          <div>
            <label htmlFor="ctm-academic-year">Academic year</label>
            <input
              id="ctm-academic-year"
              className="field"
              value={form.academic_year}
              placeholder="2026-2027"
              onChange={(e) => update("academic_year", e.target.value)}
            />
          </div>

          <div>
            <label htmlFor="ctm-section">Section</label>
            <input
              id="ctm-section"
              className="field"
              value={form.section}
              placeholder="A"
              maxLength={10}
              onChange={(e) => update("section", e.target.value)}
            />
          </div>
        </div>

        <label htmlFor="ctm-batch">Batch / specialization (optional)</label>
        <input
          id="ctm-batch"
          className="field"
          value={form.batch}
          placeholder="e.g. Batch 1 or Data Science"
          onChange={(e) => update("batch", e.target.value)}
        />

        <label htmlFor="ctm-name">Display name (optional)</label>
        <input
          id="ctm-name"
          className="field"
          value={form.name}
          placeholder="Created automatically if left empty"
          onChange={(e) => update("name", e.target.value)}
        />

        {error && (
          <p className="ctm-form-error" role="alert">
            {error}
          </p>
        )}

        <div className="ctm-actions">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : editing ? "Save changes" : "Create class"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function AdminClasses() {
  const navigate = useNavigate();

  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [classes, setClasses] = useState([]);

  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [state, setState] = useState("all");

  const [notice, setNotice] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [toggle, setToggle] = useState(null);
  const [toggling, setToggling] = useState(false);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");

    try {
      const data = await classApi.list();
      setClasses(Array.isArray(data?.classes) ? data.classes : []);
      setStatus("ready");
    } catch (err) {
      setError(err.message || "Could not load classes.");
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();

    return classes.filter((cls) => {
      if (department && cls.department !== department) return false;
      if (state === "active" && !cls.is_active) return false;
      if (state === "inactive" && cls.is_active) return false;

      if (needle) {
        const text = [
          cls.name,
          cls.department,
          cls.academic_year,
          cls.section,
          cls.batch,
        ]
          .join(" ")
          .toLowerCase();
        if (!text.includes(needle)) return false;
      }

      return true;
    });
  }, [classes, search, department, state]);

  const departmentsInUse = useMemo(
    () => [...new Set(classes.map((cls) => cls.department))].sort(),
    [classes]
  );

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(cls) {
    setEditing(cls);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditing(null);
  }

  async function submitForm(body) {
    if (editing) {
      await classApi.update(editing.id, body);
      setNotice({ kind: "success", text: "Class updated." });
    } else {
      await classApi.create(body);
      setNotice({ kind: "success", text: "Class created." });
    }

    closeForm();
    await load();
  }

  async function confirmToggle() {
    if (!toggle) return;
    setToggling(true);

    try {
      await classApi.update(toggle.cls.id, { is_active: toggle.activate });
      setNotice({
        kind: "success",
        text: toggle.activate ? "Class activated." : "Class deactivated.",
      });
      setToggle(null);
      await load();
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
      setToggle(null);
    } finally {
      setToggling(false);
    }
  }

  return (
    <AdminShell>
      <div className="page-head ctm-head">
        <div>
          <h1>Class &amp; Timetable Management</h1>
          <p>
            Create the official classes and build the timetable each class
            follows. Students choose their class during onboarding.
          </p>
        </div>

        <div className="ctm-head-actions">
          <button
            type="button"
            className="btn-ghost"
            onClick={() => navigate("/admin/classes/import")}
          >
            Import from PDF
          </button>
          <button type="button" className="btn-primary" onClick={openCreate}>
            + New class
          </button>
        </div>
      </div>

      <Banner kind={notice?.kind} onDismiss={() => setNotice(null)}>
        {notice?.text}
      </Banner>

      <div className="filter-bar">
        <input
          className="field"
          type="search"
          placeholder="Search by name, department, section…"
          aria-label="Search classes"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        <select
          className="field small"
          aria-label="Filter by department"
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
        >
          <option value="">All departments</option>
          {departmentsInUse.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <select
          className="field small"
          aria-label="Filter by status"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
      </div>

      {status === "loading" && <LoadingBlock label="Loading classes" />}

      {status === "error" && (
        <div className="ctm-error-box" role="alert">
          <p>{error}</p>
          <button type="button" className="btn-ghost" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {status === "ready" && classes.length === 0 && (
        <div>
          <EmptyState
            title="No classes yet"
            message="Create the first class, then build its timetable."
          />
        </div>
      )}

      {status === "ready" && classes.length > 0 && visible.length === 0 && (
        <EmptyState
          title="No classes match"
          message="Try a different search or clear the filters."
        />
      )}

      {status === "ready" && visible.length > 0 && (
        <div className="table-wrap ctm-table-wrap">
          <table className="data-table ctm-class-table">
            <thead>
              <tr>
                <th>Class</th>
                <th>Year · Sem · Section</th>
                <th>Students</th>
                <th>Timetable</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>

            <tbody>
              {visible.map((cls) => (
                <tr key={cls.id}>
                  <td>
                    <strong className="ctm-strong">{cls.name}</strong>
                    <div className="ctm-muted">
                      {cls.department} · {cls.academic_year}
                    </div>
                  </td>
                  <td>
                    Year {cls.year_of_study} · Sem {cls.semester} · Sec{" "}
                    {cls.section}
                    {cls.batch ? <div className="ctm-muted">{cls.batch}</div> : null}
                  </td>
                  <td>{cls.student_count ?? "–"}</td>
                  <td>
                    {cls.has_published_timetable ? (
                      <span className="status-badge status-published">
                        Published v{cls.published_version}
                      </span>
                    ) : (
                      <span className="status-badge status-draft">
                        Not published
                      </span>
                    )}
                  </td>
                  <td>
                    <span
                      className={`status-badge ${
                        cls.is_active ? "status-published" : "status-cancelled"
                      }`}
                    >
                      {cls.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    <div className="ctm-row-actions">
                      <button
                        type="button"
                        className="btn-primary small"
                        aria-label={`Open timetable for ${cls.name}`}
                        onClick={() =>
                          navigate(`/admin/classes/${cls.id}/timetable`)
                        }
                      >
                        Timetable
                      </button>
                      <button
                        type="button"
                        className="btn-ghost small"
                        aria-label={`Edit ${cls.name}`}
                        onClick={() => openEdit(cls)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn-ghost small"
                        aria-label={`${
                          cls.is_active ? "Deactivate" : "Activate"
                        } ${cls.name}`}
                        onClick={() =>
                          setToggle({ cls, activate: !cls.is_active })
                        }
                      >
                        {cls.is_active ? "Deactivate" : "Activate"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {formOpen && (
        <ClassFormModal
          editing={editing}
          onClose={closeForm}
          onSubmit={submitForm}
        />
      )}

      <ConfirmModal
        open={!!toggle}
        title={toggle?.activate ? "Activate class?" : "Deactivate class?"}
        message={
          toggle
            ? toggle.activate
              ? `"${toggle.cls.name}" will appear in the class list students choose from.`
              : `"${toggle.cls.name}" will no longer be offered to students. Students already in this class keep it and its timetable, and no timetable data is deleted.`
            : ""
        }
        confirmLabel={toggle?.activate ? "Activate" : "Deactivate"}
        isSubmitting={toggling}
        onCancel={() => setToggle(null)}
        onConfirm={confirmToggle}
      />
    </AdminShell>
  );
}

// ============================================================
// PAGE 2 - TIMETABLE EDITOR
// ============================================================

function SessionModal({ state, timetable, config, onClose, onSave, onDelete }) {
  useEscape(onClose);

  const editing = state.mode === "edit";

  const [form, setForm] = useState(() => ({
    type: state.block?.type || "lecture",
    subject: state.block?.subject || "",
    courseCode: state.block?.courseCode || "",
    faculty: state.block?.faculty || "",
    room: state.block?.room || "",
    startTime: state.block?.startTime || state.start || "09:00",
    endTime: state.block?.endTime || state.end || "10:00",
    notes: state.block?.notes || "",
  }));
  const [days, setDays] = useState([state.day]);
  const [errors, setErrors] = useState([]);

  const isBreak = !CLASS_TYPES.includes(form.type);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleDay(day) {
    setDays((current) =>
      current.includes(day)
        ? current.filter((item) => item !== day)
        : [...current, day]
    );
  }

  function handleSubmit(event) {
    event.preventDefault();
    const problems = [];

    const subject = form.subject.trim();
    const code = form.courseCode.trim();

    if (!subject) problems.push(isBreak ? "Enter a label." : "Enter the subject name.");
    if (subject.length > 80) problems.push("Subject must be 80 characters or fewer.");
    if (!isBreak && !code) problems.push("Enter the subject code.");
    if (code.length > 40) problems.push("Subject code must be 40 characters or fewer.");
    if (days.length === 0) problems.push("Choose at least one day.");

    const startOk = /^\d{2}:\d{2}$/.test(form.startTime);
    const endOk = /^\d{2}:\d{2}$/.test(form.endTime);

    if (!startOk || !endOk) {
      problems.push("Enter a start and end time.");
    } else if (toMinutes(form.startTime) >= toMinutes(form.endTime)) {
      problems.push("Start time must be earlier than end time.");
    } else {
      if (
        toMinutes(form.startTime) < toMinutes(config.collegeStartTime) ||
        toMinutes(form.endTime) > toMinutes(config.collegeEndTime)
      ) {
        problems.push(
          `Times must be inside college hours (${config.collegeStartTime}–${config.collegeEndTime}). Change the hours above the grid if needed.`
        );
      }

      days.forEach((day) => {
        const clash = (timetable[day] || []).find(
          (other) =>
            other.id !== state.block?.id &&
            overlaps(form.startTime, form.endTime, other.startTime, other.endTime)
        );

        if (clash) {
          problems.push(
            `${DAY_LABELS[day]}: overlaps "${clash.subject}" (${clash.startTime}–${clash.endTime}).`
          );
        }
      });
    }

    if (problems.length > 0) {
      setErrors(problems);
      return;
    }

    onSave(days, {
      id: state.block?.id,
      type: form.type,
      subject,
      courseCode: isBreak ? "" : code,
      faculty: isBreak ? "" : form.faculty.trim(),
      room: isBreak ? "" : form.room.trim(),
      startTime: form.startTime,
      endTime: form.endTime,
      notes: form.notes.trim(),
    });
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={editing ? "Edit session" : "Add session"}
    >
      <form className="modal-panel ctm-modal" onSubmit={handleSubmit} noValidate>
        <h2>{editing ? "Edit session" : "Add session"}</h2>

        <label htmlFor="ctm-type">Session type</label>
        <select
          id="ctm-type"
          className="field"
          value={form.type}
          onChange={(e) => update("type", e.target.value)}
        >
          {SESSION_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </select>

        <label htmlFor="ctm-subject">
          {isBreak ? "Label" : "Subject name"}
        </label>
        <input
          id="ctm-subject"
          className="field"
          value={form.subject}
          placeholder={isBreak ? "Lunch" : "Machine Learning"}
          onChange={(e) => update("subject", e.target.value)}
        />

        {!isBreak && (
          <>
            <label htmlFor="ctm-code">Subject code</label>
            <input
              id="ctm-code"
              className="field"
              value={form.courseCode}
              placeholder="ML701"
              onChange={(e) => update("courseCode", e.target.value)}
            />
          </>
        )}

        <div className="ctm-row">
          <div>
            <label htmlFor="ctm-start">Start time</label>
            <input
              id="ctm-start"
              className="field"
              type="time"
              value={form.startTime}
              onChange={(e) => update("startTime", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="ctm-end">End time</label>
            <input
              id="ctm-end"
              className="field"
              type="time"
              value={form.endTime}
              onChange={(e) => update("endTime", e.target.value)}
            />
          </div>
        </div>

        {!isBreak && (
          <div className="ctm-row">
            <div>
              <label htmlFor="ctm-faculty">Faculty (optional)</label>
              <input
                id="ctm-faculty"
                className="field"
                value={form.faculty}
                onChange={(e) => update("faculty", e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="ctm-room">Classroom / lab (optional)</label>
              <input
                id="ctm-room"
                className="field"
                value={form.room}
                placeholder="Room 201"
                onChange={(e) => update("room", e.target.value)}
              />
            </div>
          </div>
        )}

        <fieldset className="ctm-days">
          <legend>{editing ? "Day" : "Repeat on"}</legend>
          <div className="ctm-day-row">
            {DAYS.map((day) => (
              <button
                type="button"
                key={day}
                className={`ctm-day-pill ${days.includes(day) ? "active" : ""}`}
                aria-pressed={days.includes(day)}
                disabled={editing}
                onClick={() => toggleDay(day)}
              >
                {DAY_LABELS[day].slice(0, 3)}
              </button>
            ))}
          </div>
        </fieldset>

        {errors.length > 0 && (
          <ul className="ctm-form-error" role="alert">
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        )}

        <div className="ctm-actions">
          {editing ? (
            <button
              type="button"
              className="btn-ghost danger"
              onClick={() => onDelete(state.day, state.block.id)}
            >
              Delete
            </button>
          ) : (
            <span />
          )}

          <div className="ctm-actions-right">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              {editing ? "Update session" : "Add session"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function CopyDayModal({ day, onClose, onCopy }) {
  useEscape(onClose);
  const [targets, setTargets] = useState([]);

  function toggle(target) {
    setTargets((current) =>
      current.includes(target)
        ? current.filter((item) => item !== target)
        : [...current, target]
    );
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`Copy ${DAY_LABELS[day]}`}
    >
      <div className="modal-panel ctm-modal">
        <h2>Copy {DAY_LABELS[day]} to…</h2>
        <p className="modal-message">
          The sessions on the days you choose are replaced by a copy of{" "}
          {DAY_LABELS[day]}&apos;s sessions. Nothing is saved until you press
          Save draft.
        </p>

        <div className="ctm-day-row">
          {DAYS.filter((item) => item !== day).map((item) => (
            <button
              type="button"
              key={item}
              className={`ctm-day-pill ${targets.includes(item) ? "active" : ""}`}
              aria-pressed={targets.includes(item)}
              onClick={() => toggle(item)}
            >
              {DAY_LABELS[item].slice(0, 3)}
            </button>
          ))}
        </div>

        <div className="ctm-actions">
          <span />
          <div className="ctm-actions-right">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={targets.length === 0}
              onClick={() => onCopy(day, targets)}
            >
              Copy
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function WeekPreview({ timetable }) {
  return (
    <div className="ctm-preview" aria-label="Weekly timetable preview">
      {DAYS.map((day) => {
        const blocks = timetable[day] || [];

        return (
          <section className="ctm-preview-day" key={day}>
            <h3>{DAY_LABELS[day]}</h3>

            {blocks.length === 0 && (
              <p className="ctm-muted">No sessions</p>
            )}

            {blocks.map((block) => (
              <article
                key={block.id}
                className={`ctm-card ctm-type-${block.type}`}
              >
                <div className="ctm-card-time">
                  {formatTime(block.startTime)} – {formatTime(block.endTime)}
                </div>
                <div className="ctm-card-subject">
                  {block.subject}
                  {block.courseCode && block.courseCode !== block.subject ? (
                    <span className="ctm-card-code"> · {block.courseCode}</span>
                  ) : null}
                </div>
                <div className="ctm-card-meta">
                  <span className="ctm-type-tag">
                    {TYPE_LABEL[block.type] || block.type}
                  </span>
                  {block.faculty ? <span>{block.faculty}</span> : null}
                  {block.room ? <span>{block.room}</span> : null}
                </div>
              </article>
            ))}
          </section>
        );
      })}
    </div>
  );
}

export function AdminTimetableEditor() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [view, setView] = useState(null);

  const [timetable, setTimetable] = useState(() => normalizeTimetable({}));
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [draftId, setDraftId] = useState(null);
  const [source, setSource] = useState("empty");
  const [dirty, setDirty] = useState(false);

  const [tab, setTab] = useState("edit");
  const [modal, setModal] = useState(null);
  const [copyFrom, setCopyFrom] = useState(null);
  const [clearDay, setClearDay] = useState(null);
  const [publishOpen, setPublishOpen] = useState(false);

  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");

    try {
      const data = await classApi.timetable(id);
      const base = data.draft || data.published;

      setView(data);
      setTimetable(normalizeTimetable(base?.timetable));
      setConfig({ ...DEFAULT_CONFIG, ...(base?.config || {}) });
      setDraftId(data.draft ? data.draft.id : null);
      setSource(data.draft ? "draft" : data.published ? "published" : "empty");
      setDirty(false);
      setStatus("ready");
    } catch (err) {
      setError(err.message || "Could not load this timetable.");
      setStatus("error");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  // Warn before closing the tab with unsaved changes.
  useEffect(() => {
    if (!dirty) return undefined;

    function warn(event) {
      event.preventDefault();
      event.returnValue = "";
    }

    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const issues = useMemo(
    () => validateWeek(timetable, config),
    [timetable, config]
  );
  const classCount = useMemo(() => countClasses(timetable), [timetable]);

  const cls = view?.class;
  const published = view?.published;
  const classActive = cls ? cls.is_active : true;
  const hasChanges = dirty || !!draftId;

  const publishBlocker = !classActive
    ? "Activate this class before publishing."
    : classCount === 0
    ? "Add at least one class session before publishing."
    : issues.length > 0
    ? "Fix the problems listed below before publishing."
    : !hasChanges
    ? "There are no changes to publish."
    : "";

  function edit(nextTimetable) {
    setTimetable(nextTimetable);
    setDirty(true);
  }

  function goBack() {
    if (dirty && !window.confirm("You have unsaved changes. Leave without saving?")) {
      return;
    }
    navigate("/admin/classes");
  }

  // ---------- session editing (all local until "Save draft") ----------

  function openAdd(day, range) {
    const start = range?.start;
    const end =
      range && start
        ? toHHMM(Math.min(toMinutes(range.end), toMinutes(start) + 60))
        : undefined;

    setModal({ mode: "add", day, start, end });
  }

  function handleBlockClick(day, block) {
    if (block) {
      setModal({ mode: "edit", day, block });
    } else {
      openAdd(day);
    }
  }

  function saveSession(days, data) {
    const next = { ...timetable };

    if (modal.mode === "edit") {
      next[modal.day] = next[modal.day].map((block) =>
        block.id === data.id ? { ...data } : block
      );
    } else {
      days.forEach((day) => {
        next[day] = [...next[day], { ...data, id: newBlockId() }];
      });
    }

    DAYS.forEach((day) => {
      next[day] = [...next[day]].sort(
        (a, b) => toMinutes(a.startTime) - toMinutes(b.startTime)
      );
    });

    edit(next);
    setModal(null);
  }

  function deleteSession(day, blockId) {
    edit({
      ...timetable,
      [day]: timetable[day].filter((block) => block.id !== blockId),
    });
    setModal(null);
  }

  function copyDay(from, targets) {
    const next = { ...timetable };

    targets.forEach((day) => {
      next[day] = (timetable[from] || []).map((block) => ({
        ...block,
        id: newBlockId(),
      }));
    });

    edit(next);
    setCopyFrom(null);
  }

  function confirmClearDay() {
    edit({ ...timetable, [clearDay]: [] });
    setClearDay(null);
  }

  function updateConfig(field, value) {
    setConfig((current) => ({ ...current, [field]: value }));
    setDirty(true);
  }

  // ---------- server actions ----------

  async function saveDraft() {
    setSaving(true);
    setNotice(null);

    try {
      const data = await classApi.saveDraft(id, { timetable, config });
      const draft = data.timetable;

      setDraftId(draft.id);
      setTimetable(normalizeTimetable(draft.timetable));
      setConfig({ ...DEFAULT_CONFIG, ...(draft.config || {}) });
      setSource("draft");
      setDirty(false);
      setNotice({
        kind: "success",
        text: "Draft saved. Students still see the published timetable.",
      });

      return draft.id;
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function publishNow() {
    setPublishing(true);
    setNotice(null);

    try {
      let target = draftId;

      if (dirty || !target) {
        target = await saveDraft();
        if (!target) {
          setPublishOpen(false);
          return;
        }
      }

      await classApi.publish(target);
      setPublishOpen(false);
      await load();
      setNotice({
        kind: "success",
        text: "Timetable published. Students in this class now see it.",
      });
    } catch (err) {
      setPublishOpen(false);
      setNotice({ kind: "error", text: err.message });
    } finally {
      setPublishing(false);
    }
  }

  // ---------- render ----------

  if (status === "loading") {
    return (
      <AdminShell>
        <LoadingBlock label="Loading timetable" />
      </AdminShell>
    );
  }

  if (status === "error") {
    return (
      <AdminShell>
        <button type="button" className="btn-ghost small" onClick={() => navigate("/admin/classes")}>
          ← Back to classes
        </button>
        <div className="ctm-error-box" role="alert">
          <p>{error}</p>
          <button type="button" className="btn-ghost" onClick={load}>
            Try again
          </button>
        </div>
      </AdminShell>
    );
  }

  const nextVersion = (published?.version || 0) + 1;

  return (
    <AdminShell>
      <button type="button" className="btn-ghost small" onClick={goBack}>
        ← Back to classes
      </button>

      <div className="page-head ctm-head ctm-head-spaced">
        <div>
          <h1>{cls.name}</h1>
          <p className="ctm-chips">
            <span className="ctm-chip">{cls.department}</span>
            <span className="ctm-chip">
              Year {cls.year_of_study} · Sem {cls.semester}
            </span>
            <span className="ctm-chip">Section {cls.section}</span>
            <span className="ctm-chip">{cls.academic_year}</span>
            {cls.batch ? <span className="ctm-chip">{cls.batch}</span> : null}
          </p>
        </div>

        <div className="ctm-badges">
          {published ? (
            <span className="status-badge status-published">
              Published v{published.version}
            </span>
          ) : (
            <span className="status-badge status-draft">Not published</span>
          )}
          {hasChanges && (
            <span className="status-badge status-pending">
              {dirty ? "Unsaved changes" : "Draft saved"}
            </span>
          )}
          {!classActive && (
            <span className="status-badge status-cancelled">Class inactive</span>
          )}
        </div>
      </div>

      <Banner kind={notice?.kind} onDismiss={() => setNotice(null)}>
        {notice?.text}
      </Banner>

      {source === "published" && !dirty && (
        <Banner kind="info">
          You are editing a copy of the published timetable (v{published.version}
          ). Students keep seeing v{published.version} until you publish your
          changes.
        </Banner>
      )}

      {source === "empty" && !dirty && (
        <Banner kind="info">
          This class has no timetable yet. Add sessions, save a draft, then
          publish when it is ready. Students can pick the class before a
          timetable exists and will see a &quot;not published yet&quot; message.
        </Banner>
      )}

      <div className="ctm-toolbar">
        <div className="ctm-tabs" role="tablist" aria-label="Timetable view">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "edit"}
            className={tab === "edit" ? "active" : ""}
            onClick={() => setTab("edit")}
          >
            Editor
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "preview"}
            className={tab === "preview" ? "active" : ""}
            onClick={() => setTab("preview")}
          >
            Student preview
          </button>
        </div>

        <div className="ctm-toolbar-actions">
          <button
            type="button"
            className="btn-ghost"
            onClick={() => openAdd("monday")}
          >
            + Add session
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={saving || publishing || !dirty}
            onClick={saveDraft}
          >
            {saving ? "Saving…" : "Save draft"}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!!publishBlocker || saving || publishing}
            title={publishBlocker || "Publish this timetable to students"}
            onClick={() => setPublishOpen(true)}
          >
            Publish
          </button>
        </div>
      </div>

      {publishBlocker && (
        <p className="ctm-muted ctm-blocker">{publishBlocker}</p>
      )}

      {tab === "edit" && (
        <>
          <div className="ctm-config">
            <label>
              College starts
              <input
                type="time"
                className="field"
                value={config.collegeStartTime}
                onChange={(e) => updateConfig("collegeStartTime", e.target.value)}
              />
            </label>
            <label>
              College ends
              <input
                type="time"
                className="field"
                value={config.collegeEndTime}
                onChange={(e) => updateConfig("collegeEndTime", e.target.value)}
              />
            </label>
            <label>
              Grid slot
              <select
                className="field"
                value={config.slotDuration}
                onChange={(e) =>
                  updateConfig("slotDuration", Number(e.target.value))
                }
              >
                {SLOT_OPTIONS.map((minutes) => (
                  <option key={minutes} value={minutes}>
                    {minutes} min
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p className="ctm-muted">
            Click an empty area or a green FREE slot to add a session, or click
            a session to edit it.
          </p>

          <div className="ctm-grid-wrap">
            <TimetableGrid
              config={config}
              timetable={timetable}
              onBlockClick={handleBlockClick}
              onFreeClick={(day, range) => openAdd(day, range)}
              onCopyDay={(day) => setCopyFrom(day)}
              onClearDay={(day) => setClearDay(day)}
            />
          </div>
        </>
      )}

      {tab === "preview" && <WeekPreview timetable={timetable} />}

      <section className="ctm-checks" aria-label="Publish checklist">
        <h2>Before you publish</h2>
        <ul>
          <li className={classCount > 0 ? "ok" : "bad"}>
            {classCount > 0
              ? `${classCount} class session${classCount === 1 ? "" : "s"} added`
              : "No class sessions added yet"}
          </li>
          <li className={issues.length === 0 ? "ok" : "bad"}>
            {issues.length === 0 ? "No overlaps or time problems" : `${issues.length} problem${issues.length === 1 ? "" : "s"} to fix`}
          </li>
          <li className={!dirty ? "ok" : "bad"}>
            {!dirty ? "All changes are saved" : "You have unsaved changes (they are saved automatically when you publish)"}
          </li>
        </ul>

        {issues.length > 0 && (
          <ul className="ctm-issue-list">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
      </section>

      {view.history.length > 0 && (
        <section className="ctm-history" aria-label="Version history">
          <h2>Version history</h2>
          <div className="table-wrap">
            <table className="data-table ctm-history-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Published</th>
                </tr>
              </thead>
              <tbody>
                {view.history.map((row) => (
                  <tr key={row.id}>
                    <td>v{row.version}</td>
                    <td>
                      <span
                        className={`status-badge ${
                          row.status === "published"
                            ? "status-published"
                            : "status-draft"
                        }`}
                      >
                        {row.status === "published" ? "Live" : "Archived"}
                      </span>
                    </td>
                    <td>{formatWhen(row.published_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {modal && (
        <SessionModal
          state={modal}
          timetable={timetable}
          config={config}
          onClose={() => setModal(null)}
          onSave={saveSession}
          onDelete={deleteSession}
        />
      )}

      {copyFrom && (
        <CopyDayModal
          day={copyFrom}
          onClose={() => setCopyFrom(null)}
          onCopy={copyDay}
        />
      )}

      <ConfirmModal
        open={!!clearDay}
        title={clearDay ? `Clear ${DAY_LABELS[clearDay]}?` : "Clear day?"}
        message={
          clearDay
            ? `This removes all ${(timetable[clearDay] || []).length} session(s) from ${DAY_LABELS[clearDay]} in this draft. Nothing changes for students until you publish.`
            : ""
        }
        confirmLabel="Clear day"
        isSubmitting={false}
        onCancel={() => setClearDay(null)}
        onConfirm={confirmClearDay}
      />

      <ConfirmModal
        open={publishOpen}
        title={`Publish timetable v${nextVersion}?`}
        message={
          published
            ? `Students in "${cls.name}" will see this timetable immediately. It replaces v${published.version}, which stays in the version history.`
            : `Students in "${cls.name}" will see this timetable immediately.`
        }
        confirmLabel="Publish"
        isSubmitting={publishing}
        onCancel={() => setPublishOpen(false)}
        onConfirm={publishNow}
      />
    </AdminShell>
  );
}


// ============================================================
// PAGE 3 - IMPORT MANY TIMETABLES FROM ONE PDF
// ============================================================

const MAX_PDF_MB = 25;

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = () => {
      const text = String(reader.result || "");
      resolve(text.includes(",") ? text.split(",")[1] : text);
    };
    reader.onerror = () => reject(new Error("Could not read that file."));
    reader.readAsDataURL(file);
  });
}

function withoutLunch(timetable) {
  const result = {};
  DAYS.forEach((day) => {
    result[day] = (timetable?.[day] || []).filter((b) => b.type !== "lunch");
  });
  return result;
}

function needsAttention(item) {
  return (
    !item.valid ||
    item.semester == null ||
    (item.warnings || []).some((w) => !/semester number/i.test(w))
  );
}

export function AdminTimetableImport() {
  const navigate = useNavigate();

  const [stage, setStage] = useState("upload"); // upload | reading | review | saving | done
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);

  const [meta, setMeta] = useState(null);
  const [classes, setClasses] = useState([]);
  const [deptByProgram, setDeptByProgram] = useState({});

  const [academicYear, setAcademicYear] = useState("");
  const [includeLunch, setIncludeLunch] = useState(true);
  const [publish, setPublish] = useState(false);

  const [search, setSearch] = useState("");
  const [onlyAttention, setOnlyAttention] = useState(false);
  const [openCode, setOpenCode] = useState("");

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [result, setResult] = useState(null);

  function chooseFile(event) {
    const chosen = event.target.files?.[0] || null;
    setError("");

    if (!chosen) {
      setFile(null);
      return;
    }

    const isPdf =
      chosen.type === "application/pdf" || /\.pdf$/i.test(chosen.name);

    if (!isPdf) {
      setFile(null);
      setError("Please choose a PDF file.");
      return;
    }

    if (chosen.size > MAX_PDF_MB * 1024 * 1024) {
      setFile(null);
      setError(`That file is larger than ${MAX_PDF_MB} MB.`);
      return;
    }

    setFile(chosen);
  }

  async function readPdf() {
    if (!file) return;

    setError("");
    setStage("reading");

    try {
      const base64 = await readFileAsBase64(file);
      const data = await classApi.importPreview(file.name, base64);

      setMeta(data);
      setAcademicYear(data.academic_year || currentAcademicYear());
      setClasses(
        (data.classes || []).map((item) => ({
          ...item,
          selected: item.valid && item.semester != null,
        }))
      );
      setDeptByProgram(
        Object.fromEntries((data.programs || []).map((p) => [p.code, "Other"]))
      );
      setStage("review");
    } catch (err) {
      setError(err.message || "Could not read the PDF.");
      setStage("upload");
    }
  }

  function updateClass(code, patch) {
    setClasses((current) =>
      current.map((item) => (item.code === code ? { ...item, ...patch } : item))
    );
  }

  function setSemester(code, value) {
    const semester = value === "" ? null : Number(value);
    updateClass(code, {
      semester,
      year_of_study: semester ? Math.ceil(semester / 2) : null,
      selected: semester ? true : false,
    });
  }

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return classes.filter((item) => {
      if (onlyAttention && !needsAttention(item)) return false;
      if (!needle) return true;
      return `${item.code} ${item.program}`.toLowerCase().includes(needle);
    });
  }, [classes, search, onlyAttention]);

  const selected = classes.filter((item) => item.selected);
  const attentionCount = classes.filter(needsAttention).length;

  function selectVisible(value) {
    const codes = new Set(visible.map((item) => item.code));
    setClasses((current) =>
      current.map((item) =>
        codes.has(item.code) && item.valid && item.semester != null
          ? { ...item, selected: value }
          : item
      )
    );
  }

  function startImport() {
    setError("");

    if (!/^\d{4}-\d{4}$/.test(academicYear.trim())) {
      setError("Academic year must look like 2026-2027.");
      return;
    }
    if (selected.length === 0) {
      setError("Choose at least one class to import.");
      return;
    }
    if (selected.some((item) => item.semester == null)) {
      setError("Choose a semester for every selected class.");
      return;
    }

    if (publish) {
      setConfirmOpen(true);
    } else {
      saveAll();
    }
  }

  async function saveAll() {
    setConfirmOpen(false);
    setStage("saving");
    setError("");

    try {
      const response = await classApi.importCommit({
        academic_year: academicYear.trim(),
        publish,
        classes: selected.map((item) => ({
          code: item.code,
          name: item.name,
          department: deptByProgram[item.program] || "Other",
          year_of_study: item.year_of_study,
          semester: item.semester,
          section: item.section,
          batch: item.batch,
          timetable: includeLunch ? item.timetable : withoutLunch(item.timetable),
          config: item.config,
        })),
      });

      setResult(response);
      setStage("done");
    } catch (err) {
      setError(err.message || "Could not save the timetables.");
      setStage("review");
    }
  }

  function backToReview() {
    const done = new Set(
      (result?.results || [])
        .filter((row) => row.status !== "failed")
        .map((row) => row.code)
    );
    const failed = new Set(
      (result?.results || [])
        .filter((row) => row.status === "failed")
        .map((row) => row.code)
    );

    setClasses((current) =>
      current.map((item) => ({
        ...item,
        selected: failed.has(item.code) && !done.has(item.code),
      }))
    );
    setResult(null);
    setStage("review");
  }

  function startOver() {
    setFile(null);
    setMeta(null);
    setClasses([]);
    setResult(null);
    setError("");
    setPublish(false); // never carry "publish right away" over to the next import
    setStage("upload");
  }

  // ---------- render ----------

  return (
    <AdminShell>
      <button
        type="button"
        className="btn-ghost small"
        onClick={() => navigate("/admin/classes")}
      >
        ← Back to classes
      </button>

      <div className="page-head ctm-head ctm-head-spaced">
        <div>
          <h1>Import timetables from a PDF</h1>
          <p>
            Upload one PDF with a timetable per section. Each page becomes a
            class, so students only choose their section.
          </p>
        </div>
      </div>

      {error && <Banner kind="error" onDismiss={() => setError("")}>{error}</Banner>}

      {stage === "upload" && (
        <section className="ctm-card-box" aria-label="Upload a PDF">
          <h2>1. Choose the PDF</h2>
          <p className="ctm-muted">
            Works with timetables exported as one page per section (a weekly
            grid of periods). Nothing is saved until you review and confirm.
          </p>

          <label className="ctm-file" htmlFor="ctm-pdf">
            <span className="ctm-file-label">
              {file ? file.name : "Select a PDF file"}
            </span>
            <input
              id="ctm-pdf"
              type="file"
              accept="application/pdf,.pdf"
              onChange={chooseFile}
            />
          </label>

          <div className="ctm-actions">
            <span />
            <button
              type="button"
              className="btn-primary"
              disabled={!file}
              onClick={readPdf}
            >
              Read PDF
            </button>
          </div>
        </section>
      )}

      {stage === "reading" && (
        <LoadingBlock label="Reading the PDF. Large files can take up to a minute." />
      )}

      {stage === "saving" && (
        <LoadingBlock label={`Saving ${selected.length} timetables…`} />
      )}

      {stage === "review" && meta && (
        <>
          <Banner kind="info">
            Found {classes.length} timetable{classes.length === 1 ? "" : "s"} in{" "}
            {meta.programs.length} programme code
            {meta.programs.length === 1 ? "" : "s"}.{" "}
            {attentionCount > 0
              ? `${attentionCount} need${attentionCount === 1 ? "s" : ""} a look (marked below).`
              : "All look ready."}{" "}
            Nothing is saved until you press the button at the bottom.
          </Banner>

          <section className="ctm-card-box" aria-label="Import settings">
            <h2>2. Settings</h2>

            <div className="ctm-settings">
              <label htmlFor="ctm-ay">
                Academic year
                <input
                  id="ctm-ay"
                  className="field"
                  value={academicYear}
                  onChange={(e) => setAcademicYear(e.target.value)}
                  placeholder="2026-2027"
                />
              </label>

              <label className="ctm-check">
                <input
                  type="checkbox"
                  checked={includeLunch}
                  onChange={(e) => setIncludeLunch(e.target.checked)}
                />
                Include the lunch break in each timetable
              </label>

              <label className="ctm-check">
                <input
                  type="checkbox"
                  checked={publish}
                  onChange={(e) => setPublish(e.target.checked)}
                />
                Publish right away (students see them immediately)
              </label>
            </div>

            <p className="ctm-muted">
              Leave &quot;Publish&quot; off to save everything as drafts first and
              publish each class after checking it. Classes that already exist
              are updated, not duplicated.
            </p>
          </section>

          <section className="ctm-card-box" aria-label="Programmes">
            <h2>3. Departments</h2>
            <p className="ctm-muted">
              The PDF names each section by a programme code (for example CAI in
              3CAI01). Choose the department for each code so students see their
              own classes first.
            </p>

            <div className="table-wrap ctm-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Programme code</th>
                    <th>Sections</th>
                    <th>Department</th>
                  </tr>
                </thead>
                <tbody>
                  {meta.programs.map((program) => (
                    <tr key={program.code || "none"}>
                      <td>
                        <strong className="ctm-strong">{program.code || "(none)"}</strong>
                      </td>
                      <td>{program.classes}</td>
                      <td>
                        <select
                          className="field small"
                          aria-label={`Department for ${program.code}`}
                          value={deptByProgram[program.code] || "Other"}
                          onChange={(e) =>
                            setDeptByProgram((current) => ({
                              ...current,
                              [program.code]: e.target.value,
                            }))
                          }
                        >
                          {DEPARTMENTS.map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ctm-card-box" aria-label="Classes found">
            <h2>4. Check the timetables</h2>

            <div className="filter-bar">
              <input
                className="field"
                type="search"
                placeholder="Search by section name, e.g. 3CAI01"
                aria-label="Search sections"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <label className="ctm-check">
                <input
                  type="checkbox"
                  checked={onlyAttention}
                  onChange={(e) => setOnlyAttention(e.target.checked)}
                />
                Only those needing a look
              </label>
              <button type="button" className="btn-ghost small" onClick={() => selectVisible(true)}>
                Select shown
              </button>
              <button type="button" className="btn-ghost small" onClick={() => selectVisible(false)}>
                Clear shown
              </button>
            </div>

            {visible.length === 0 ? (
              <p className="ctm-muted">No timetables match.</p>
            ) : (
              <div className="table-wrap ctm-table-wrap">
                <table className="data-table ctm-import-table">
                  <thead>
                    <tr>
                      <th>Import</th>
                      <th>Section</th>
                      <th>Semester</th>
                      <th>Year</th>
                      <th>Sessions</th>
                      <th>Status</th>
                      <th>Timetable</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((item) => {
                      const open = openCode === item.code;
                      const attention = needsAttention(item);
                      const issues = [...(item.errors || []), ...(item.warnings || [])];

                      return (
                        <React.Fragment key={item.code}>
                          <tr>
                            <td>
                              <input
                                type="checkbox"
                                aria-label={`Import ${item.code}`}
                                checked={item.selected}
                                disabled={!item.valid || item.semester == null}
                                onChange={(e) =>
                                  updateClass(item.code, { selected: e.target.checked })
                                }
                              />
                            </td>
                            <td>
                              <strong className="ctm-strong">{item.code}</strong>
                              <div className="ctm-muted">page {item.page}</div>
                            </td>
                            <td>
                              <select
                                className={`field small ${item.semester == null ? "ctm-need" : ""}`}
                                aria-label={`Semester for ${item.code}`}
                                value={item.semester ?? ""}
                                onChange={(e) => setSemester(item.code, e.target.value)}
                              >
                                <option value="">Choose…</option>
                                {SEMESTERS.map((sem) => (
                                  <option key={sem} value={sem}>
                                    Semester {sem}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td>{item.year_of_study ? `Year ${item.year_of_study}` : "–"}</td>
                            <td>{item.class_sessions}</td>
                            <td>
                              {attention ? (
                                <span className="status-badge status-pending" title={issues.join(" ")}>
                                  Check
                                </span>
                              ) : (
                                <span className="status-badge status-published">Ready</span>
                              )}
                            </td>
                            <td>
                              <button
                                type="button"
                                className="btn-ghost small"
                                aria-expanded={open}
                                aria-label={`${open ? "Hide" : "Show"} timetable ${item.code}`}
                                onClick={() => setOpenCode(open ? "" : item.code)}
                              >
                                {open ? "Hide" : "View"}
                              </button>
                            </td>
                          </tr>

                          {open && (
                            <tr className="ctm-import-open">
                              <td colSpan={7}>
                                {issues.length > 0 && (
                                  <ul className="ctm-form-error">
                                    {issues.map((message) => (
                                      <li key={message}>{message}</li>
                                    ))}
                                  </ul>
                                )}
                                <WeekPreview
                                  timetable={
                                    includeLunch ? item.timetable : withoutLunch(item.timetable)
                                  }
                                />
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="ctm-actionbar">
            <span>
              {selected.length} of {classes.length} selected
            </span>
            <div className="ctm-actions-right">
              <button type="button" className="btn-ghost" onClick={startOver}>
                Start over
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={selected.length === 0}
                onClick={startImport}
              >
                {publish
                  ? `Import and publish ${selected.length}`
                  : `Import ${selected.length} as drafts`}
              </button>
            </div>
          </div>
        </>
      )}

      {stage === "done" && result && (
        <section className="ctm-card-box" aria-label="Import result">
          <h2>Import finished</h2>

          <ul className="ctm-result-list">
            <li>
              <strong>{result.created}</strong> new class
              {result.created === 1 ? "" : "es"} created
            </li>
            <li>
              <strong>{result.updated}</strong> existing class
              {result.updated === 1 ? "" : "es"} updated
            </li>
            <li>
              <strong>{result.published}</strong> published
              {result.published === 0 && result.failed === 0
                ? " (saved as drafts: open a class to review and publish)"
                : ""}
            </li>
            <li className={result.failed ? "bad" : ""}>
              <strong>{result.failed}</strong> could not be saved
            </li>
          </ul>

          {result.results.some((row) => row.status !== "failed" && row.publish_error) && (
            <p className="ctm-form-error">
              Some timetables were saved but could not be published. Open them
              from the class list to publish.
            </p>
          )}

          {result.failed > 0 && (
            <div className="table-wrap ctm-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Section</th>
                    <th>Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results
                    .filter((row) => row.status === "failed")
                    .map((row) => (
                      <tr key={row.code}>
                        <td>
                          <strong className="ctm-strong">{row.code}</strong>
                        </td>
                        <td>{row.error}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="ctm-actions">
            {result.failed > 0 ? (
              <button type="button" className="btn-ghost" onClick={backToReview}>
                Fix the ones that failed
              </button>
            ) : (
              <button type="button" className="btn-ghost" onClick={startOver}>
                Import another PDF
              </button>
            )}
            <button
              type="button"
              className="btn-primary"
              onClick={() => navigate("/admin/classes")}
            >
              Go to classes
            </button>
          </div>
        </section>
      )}

      <ConfirmModal
        open={confirmOpen}
        title={`Publish ${selected.length} timetables now?`}
        message="Students in these classes will see them immediately. Classes that already have a published timetable move to a new version; the old one stays in the history."
        confirmLabel="Import and publish"
        isSubmitting={false}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={saveAll}
      />
    </AdminShell>
  );
}