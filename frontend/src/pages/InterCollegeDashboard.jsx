import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

import {
  clearSession,
  createEvent,
  fetchFacultyAnalytics,
  fetchFacultyDashboard,
  fetchFacultyEvents,
  fetchFacultyNotifications,
  fetchFacultyReviewHistory,
  fileUrl,
  markAllFacultyNotificationsRead,
  markFacultyNotificationRead,
  reviewEvent,
  updateEvent,
  uploadPoster
} from "../facultyService";
import { DEPARTMENTS, matchDepartment } from "../departments";
import BudgetBreakupForm from "../components/budget/BudgetBreakupForm";
import {
  anySelected,
  breakupFromEvent,
  breakupToPayload,
  computeTotal,
  emptyBreakup,
  hasSavedBreakup,
  validateBreakup,
} from "../components/budget/budgetUtils";

import RegistrationRequirement, {
  DeadlineChangeRequest,
  deadlineProblem,
  formatDeadline,
  isRegistrationClosed,
  isRegistrationRequired,
  registrationLabel,
  toDateTimeLocal,
} from "../components/registration/RegistrationRequirement";

import EventChecklist from "../components/checklist/EventChecklist";
import EventReport from "../components/report/EventReport";
import PendingReports from "../components/report/PendingReports";

import {
  RequestModificationModal,
  isLiveEvent,
} from "../components/modifications/RequestModification";
import MyModificationRequests from "../components/modifications/MyModificationRequests";
import ModificationCompare, {
  rowsFromQueueItem,
} from "../components/modifications/ModificationCompare";

import "../club-dashboard.css";
import "../faculty-dashboard.css";

// ============================================================
// FACULTY DASHBOARD  (replaces the Inter-College Leader dashboard)
// ------------------------------------------------------------
// Everything for the Faculty dashboard lives in this one file:
//   1. Config & helpers      (navigation, labels, approval stages)
//   2. Reusable components   (sidebar, top bar, stepper, modal, forms)
//   3. Pages                 (events, reviews, corrections, tracking,
//                             notifications)
//   4. The FacultyDashboard component itself (data loading + routing
//      between sections) at the very bottom.
// API calls are in ../facultyService.js, styles in ../faculty-dashboard.css
// ============================================================


// ============================================================
// NAVIGATION & PAGE TEXT
// ============================================================

// Faculty navigation. `ready` = the page works today;
// otherwise the page shows a clearly-marked "not yet connected" card.

const FACULTY_NAV = [
  { key: "dashboard",     label: "Dashboard",              icon: "🏠", ready: true },
  { key: "club",          label: "Club Overview",          icon: "🏛️", ready: true },
  { key: "events",        label: "All Club Events",        icon: "📅", ready: true },
  { key: "create",        label: "Create Event",           icon: "➕", ready: true },
  { key: "reviews",       label: "Pending Event Reviews",  icon: "📥", ready: true, badge: "pendingFacultyReviews" },
  { key: "corrections",   label: "Events Awaiting Corrections", icon: "↩️", ready: true, badge: "returnedForCorrections" },
  { key: "modifications", label: "Modification Requests",  icon: "✏️", ready: true },
  { key: "tracking",      label: "Approval Tracking",      icon: "🧭", ready: true },
  { key: "analytics",     label: "Event Analytics",        icon: "📊", ready: true },
  { key: "history",       label: "Review History",         icon: "🕘", ready: true },
  { key: "notifications", label: "Notifications",          icon: "🔔", ready: true, badge: "unreadNotifications" },
  { key: "profile",       label: "Profile & Settings",     icon: "👤", ready: true },
];

const PAGE_TITLES = Object.fromEntries(
  FACULTY_NAV.map((item) => [item.key, item.label])
);

// What each not-yet-built page will do, and what backend it already has.
const COMING_SOON = {
  events: {
    text: "Search, filter, sort and page through every event of your club, including those created by Club Coordinators.",
    api: "Backend ready: GET /api/faculty/events",
  },
  create: {
    text: "Create an event for your club using the existing event form. Faculty events go straight to the HOD.",
    api: "Backend ready: POST /api/events (creates with club_id)",
  },
};


// ============================================================
// SHARED CONSTANTS & HELPERS
// ============================================================

const STATUS_LABELS = {
  pending_faculty: ["Awaiting Faculty", "status-pending"],
  pending_hod: ["Awaiting HOD", "status-pending"],
  pending_dean: ["Awaiting Dean", "status-pending"],
  pending_admin: ["Awaiting Admin", "status-pending"],
  changes_requested: ["Changes requested", "fac-status-returned"],
  rejected: ["Rejected", "status-cancelled"],
  published: ["Published", "status-published"],
};

const ROLE_LABELS = {
  faculty: "Faculty",
  club: "Club Coordinator",
  hod: "HOD",
  dean: "Dean",
  admin: "Admin",
};

const CATEGORIES = [
  "Technical",
  "Cultural",
  "Sports",
  "Workshop",
  "Competition",
  "Conference",
  "Masterclass",
  "Webinar",
  "Industry Visit",
  "Seminar",
  "Camp",
  "FDP",
  "SDP",
  "MDP",
  "Guest Lecturers",
  "Others",
];

// Events in these states are under review and cannot be edited.
const LOCKED_STATUSES = [
  "pending_faculty",
  "pending_hod",
  "pending_dean",
  "pending_admin",
];

function StatusBadge({ status }) {
  const [label, cls] = STATUS_LABELS[status] || [
    status ? String(status).replace(/_/g, " ") : "No status",
    "status-draft",
  ];
  return <span className={`status-badge ${cls}`}>{label}</span>;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

function canEdit(event) {
  return !LOCKED_STATUSES.includes(event?.approval_status);
}


// ============================================================
// APPROVAL STAGE LOGIC
// ============================================================

// Computes the approval tracker for ONE submission version.
// Earlier versions are never reused: only decisions recorded for the
// event's current submission_version count toward its progress.

const AFTER_FACULTY = ["pending_hod", "pending_dean", "pending_admin", "published"];

function computeStages(event) {
  const version = event.submission_version || 1;
  const status = event.approval_status;
  const history = (event.approval_history || []).filter(
    (item) => (item.submission_version || 1) === version
  );
  const latest = (role) =>
    [...history].reverse().find((item) => item.reviewer_role === role);

  function fromEntry(entry, role) {
    if (entry) {
      return {
        state:
          entry.decision === "approved"
            ? "done"
            : entry.decision === "changes"
            ? "returned"
            : "rejected",
        by: entry.reviewer_name,
        at: entry.reviewed_at,
        remarks: entry.remarks,
        note:
          entry.decision === "approved"
            ? "Approved"
            : entry.decision === "changes"
            ? "Changes requested"
            : "Rejected",
      };
    }
    if (status === `pending_${role}`) {
      return { state: "current", note: "Awaiting decision" };
    }
    return { state: "upcoming", note: "Not reached yet" };
  }

  // ---- Faculty stage ----
  let faculty;
  if (event.created_by_role === "faculty") {
    faculty = { state: "done", note: "Submitted by Faculty", by: event.created_by_name, at: event.created_at };
  } else {
    const entry = latest("faculty");
    if (entry) {
      faculty = fromEntry(entry, "faculty");
    } else if (status === "pending_faculty") {
      faculty = { state: "current", note: "Awaiting Faculty review" };
    } else if (AFTER_FACULTY.includes(status)) {
      faculty = { state: "skipped", note: "Not required (no faculty was assigned)" };
    } else {
      faculty = { state: "upcoming", note: "Not reached yet" };
    }
  }

  const stages = [
    { key: "faculty", label: "Faculty Review", ...faculty },
    { key: "hod", label: "HOD", ...fromEntry(latest("hod"), "hod") },
    { key: "dean", label: "Dean", ...fromEntry(latest("dean"), "dean") },
    { key: "admin", label: "Admin", ...fromEntry(latest("admin"), "admin") },
    {
      key: "published",
      label: "Published",
      state: status === "published" ? "done" : "upcoming",
      note: status === "published" ? "Visible to students" : "Not published yet",
    },
  ];

  return { stages, version };
}

function currentStageLabel(event) {
  switch (event.approval_status) {
    case "pending_faculty": return "Faculty review";
    case "pending_hod": return "HOD";
    case "pending_dean": return "Dean";
    case "pending_admin": return "Admin";
    case "published": return "Published";
    case "changes_requested": return "Returned for corrections";
    case "rejected": return "Rejected";
    default: return "No approval status";
  }
}


// ============================================================
// SIDEBAR
// ============================================================

function initialsOf(name) {
  return (
    String(name || "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join("") || "F"
  );
}

function FacultySidebar({
  activeKey,
  collapsed,
  mobileOpen,
  facultyName,
  clubName,
  counts,
  onToggleCollapsed,
  onCloseMobile,
  onNavigate,
  onLogout,
}) {
  return (
    <>
      {mobileOpen && (
        <div className="sidebar-backdrop" onClick={onCloseMobile} />
      )}

      <aside
        className={`sidebar ${collapsed ? "collapsed" : ""} ${
          mobileOpen ? "mobile-open" : ""
        }`}
        aria-label="Faculty navigation"
      >
        <div className="sidebar-head">
          <div className="brand">
            <span className="brand-mark">C</span>
            {!collapsed && (
              <span>
                CampusPath <span className="brand-ai">AI</span>
              </span>
            )}
          </div>
          <button
            type="button"
            className="collapse-btn"
            onClick={onToggleCollapsed}
            aria-label="Collapse sidebar"
          >
            {collapsed ? "»" : "«"}
          </button>
        </div>

        <div className="sidebar-role fac-user">
          <span className="fac-avatar-sm">{initialsOf(facultyName)}</span>
          {!collapsed && (
            <div className="fac-user-text">
              <div className="org-name">{facultyName || "Faculty"}</div>
              <div className="role-tag">Faculty</div>
              {clubName && <div className="fac-club-name">{clubName}</div>}
            </div>
          )}
        </div>

        <nav className="nav">
          {FACULTY_NAV.map((item) => {
            const badgeCount = item.badge ? counts?.[item.badge] || 0 : 0;
            return (
              <button
                key={item.key}
                type="button"
                className={`nav-item ${
                  activeKey === item.key ? "active" : ""
                }`}
                onClick={() => {
                  onNavigate(item.key);
                  onCloseMobile();
                }}
                title={item.label}
              >
                <span className="nav-icon">{item.icon}</span>
                {!collapsed && (
                  <>
                    <span className="fac-nav-label">{item.label}</span>
                    {badgeCount > 0 && (
                      <span className="fac-count">{badgeCount}</span>
                    )}
                    {!item.ready && (
                      <span className="fac-soon">Soon</span>
                    )}
                  </>
                )}
              </button>
            );
          })}

          <button
            type="button"
            className="nav-item logout"
            onClick={onLogout}
          >
            <span className="nav-icon">🚪</span>
            {!collapsed && <span>Logout</span>}
          </button>
        </nav>
      </aside>
    </>
  );
}


// ============================================================
// TOP BAR
// ============================================================

function FacultyTopBar({
  title,
  search,
  facultyName,
  facultyEmail,
  attention,        // [{ id, text, sub }]  real items needing the user's attention
  onSearch,
  onMenuToggle,
  onCreate,
  onNavigate,
  onLogout,
}) {
  const [openMenu, setOpenMenu] = useState(null); // "bell" | "profile" | null
  const wrapRef = useRef(null);

  useEffect(() => {
    function close(event) {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const count = attention?.length || 0;

  return (
    <header className="topbar" ref={wrapRef}>
      <button
        type="button"
        className="hamburger-btn"
        onClick={onMenuToggle}
        aria-label="Open menu"
      >
        ☰
      </button>

      <h1 className="fac-page-title">{title}</h1>

      <label className="search">
        <span aria-hidden="true">🔍</span>
        <input
          type="search"
          placeholder="Search events by title…"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>

      <div className="top-actions">
        <button
          type="button"
          className="btn-primary small fac-create-btn"
          onClick={onCreate}
        >
          + Create Event
        </button>

        <div className="dropdown-wrap">
          <button
            type="button"
            className="icon-btn"
            aria-label={`Notifications${count ? `, ${count} need attention` : ""}`}
            onClick={() =>
              setOpenMenu((menu) => (menu === "bell" ? null : "bell"))
            }
          >
            🔔
            {count > 0 && <span className="fac-bell-count">{count}</span>}
          </button>

          {openMenu === "bell" && (
            <div className="dropdown-panel">
              <div className="dropdown-title">Needs your attention</div>
              {count === 0 ? (
                <div className="dropdown-empty">Nothing needs attention.</div>
              ) : (
                attention.slice(0, 6).map((item) => (
                  <div className="notif-row unread" key={item.id}>
                    <p>{item.text}</p>
                    <span>{item.sub}</span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>

        <div className="dropdown-wrap">
          <button
            type="button"
            className="avatar"
            aria-label="Profile menu"
            onClick={() =>
              setOpenMenu((menu) => (menu === "profile" ? null : "profile"))
            }
          >
            {initialsOf(facultyName)}
          </button>

          {openMenu === "profile" && (
            <div className="dropdown-panel fac-profile-panel">
              <div className="fac-profile-head">
                <strong>{facultyName || "Faculty"}</strong>
                <span className="role-tag">Faculty</span>
                {facultyEmail && <small>{facultyEmail}</small>}
              </div>
              <button
                type="button"
                className="fac-menu-btn"
                onClick={() => {
                  setOpenMenu(null);
                  onNavigate("profile");
                }}
              >
                Profile &amp; Settings
              </button>
              <button
                type="button"
                className="fac-menu-btn danger"
                onClick={onLogout}
              >
                Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}


// ============================================================
// APPROVAL STEPPER
// ============================================================

const STEP_ICONS = {
  done: "✓",
  current: "●",
  returned: "↩",
  rejected: "✕",
  skipped: "–",
  upcoming: "",
};

function ApprovalStepper({ event }) {
  const { stages, version } = computeStages(event);

  return (
    <div className="fac-card">
      <div className="fac-stepper-head">
        <h3 style={{ margin: 0 }}>Approval progress</h3>
        <span className="fac-sub">Submission version v{version}</span>
      </div>

      <ol className="fac-stepper">
        {stages.map((stage, index) => (
          <li key={stage.key} className={`fac-step ${stage.state}`}>
            <div className="fac-step-line" aria-hidden="true">
              <span className="fac-step-dot">{STEP_ICONS[stage.state]}</span>
              {index < stages.length - 1 && <span className="fac-step-bar" />}
            </div>
            <div className="fac-step-body">
              <div className="fac-step-title">{stage.label}</div>
              <div className="fac-step-note">{stage.note}</div>
              {stage.by && (
                <div className="fac-sub">
                  {stage.by}
                  {stage.at ? ` · ${formatDateTime(stage.at)}` : ""}
                </div>
              )}
              {stage.remarks && (
                <div className="fac-step-remarks">“{stage.remarks}”</div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}


// ============================================================
// REVIEW HISTORY (remarks + revisions)
// ============================================================

const DECISION = {
  approved: ["Approved", "status-published"],
  changes: ["Changes requested", "fac-status-returned"],
  rejected: ["Rejected", "status-cancelled"],
};

/**
 * Reusable remarks + revision history.
 * Groups every review decision by submission version (newest first) so
 * earlier rounds stay visible and are never mixed with the current one.
 */
function ReviewHistory({ event }) {
  const history = event.approval_history || [];
  const edits = event.edit_history || [];
  const current = event.submission_version || 1;

  const versions = [...new Set(history.map((h) => h.submission_version || 1))]
    .concat(edits.map((e) => e.submission_version || 1))
    .filter((value, index, all) => all.indexOf(value) === index)
    .sort((a, b) => b - a);

  if (versions.length === 0) {
    return (
      <div className="fac-card">
        <h3>Review history &amp; remarks</h3>
        <div className="dropdown-empty">
          No review decisions or edits recorded for this event yet.
        </div>
      </div>
    );
  }

  return (
    <div className="fac-card">
      <h3>Review history &amp; remarks</h3>
      {versions.map((version) => {
        const decisions = history
          .filter((h) => (h.submission_version || 1) === version)
          .reverse();
        const versionEdits = edits
          .filter((e) => (e.submission_version || 1) === version)
          .reverse();
        return (
          <div className="fac-version" key={version}>
            <div className="fac-version-head">
              Version {version}
              {version === current && (
                <span className="status-badge status-completed">Current</span>
              )}
            </div>

            <ul className="fac-list">
              {decisions.map((item, index) => {
                const [label, cls] = DECISION[item.decision] || [
                  item.decision,
                  "status-draft",
                ];
                return (
                  <li key={`d${index}`}>
                    <div>
                      <div>
                        <b style={{ color: "var(--text)" }}>
                          {ROLE_LABELS[item.reviewer_role] || item.reviewer_role}
                        </b>{" "}
                        <span className={`status-badge ${cls}`}>{label}</span>
                      </div>
                      {item.remarks && (
                        <div className="remarks">“{item.remarks}”</div>
                      )}
                      <div className="fac-sub">{item.reviewer_name}</div>
                    </div>
                    <div className="fac-sub" style={{ whiteSpace: "nowrap" }}>
                      {formatDateTime(item.reviewed_at)}
                    </div>
                  </li>
                );
              })}

              {versionEdits.map((item, index) => (
                <li key={`e${index}`}>
                  <div>
                    <div>
                      <b style={{ color: "var(--text)" }}>
                        {ROLE_LABELS[item.editor_role] || item.editor_role}
                      </b>{" "}
                      <span className="status-badge status-draft">Edited</span>
                    </div>
                    <div className="fac-sub">
                      {item.editor_name}
                      {item.changed_fields?.length
                        ? ` changed: ${item.changed_fields.join(", ")}`
                        : " saved without changing details"}
                    </div>
                  </div>
                  <div className="fac-sub" style={{ whiteSpace: "nowrap" }}>
                    {formatDateTime(item.edited_at)}
                  </div>
                </li>
              ))}

              {decisions.length === 0 && versionEdits.length === 0 && (
                <li>
                  <div className="fac-sub">No activity recorded.</div>
                </li>
              )}
            </ul>
          </div>
        );
      })}
    </div>
  );
}


// ============================================================
// REVIEW / REMARKS MODAL
// ============================================================

/**
 * mode "forward"  -> confirmation only
 * mode "changes"  -> remarks required
 * mode "rejected" -> reason required
 * `onSubmit(remarks)` must return a promise; the modal only closes after
 * the backend confirms, and shows the error otherwise.
 */
function ReviewModal({ mode, event, onSubmit, onClose }) {
  const [remarks, setRemarks] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const needsRemarks = mode !== "forward";
  const copy = {
    forward: {
      title: "Approve and forward to HOD?",
      body: `“${event.title}” will be sent to the HOD for approval. You can no longer edit it after forwarding.`,
      confirm: "Approve & forward",
      cls: "btn-primary",
    },
    changes: {
      title: "Request changes",
      body: "Explain what the coordinator needs to correct. The event returns to its creator for editing, and your remarks appear on both dashboards.",
      confirm: "Send back for changes",
      cls: "btn-primary",
    },
    rejected: {
      title: "Reject event",
      body: "Give the reason for rejecting this event. It returns to its creator, who can edit and resubmit it.",
      confirm: "Reject event",
      cls: "btn-danger",
    },
  }[mode];

  async function confirm() {
    if (needsRemarks && remarks.trim().length < 10) {
      setError("Please enter at least 10 characters.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit(remarks.trim());
    } catch (err) {
      setError(err.message || "The decision could not be saved.");
      setBusy(false);
    }
  }

  return (
    <div className="fac-modal-overlay" onClick={() => !busy && onClose()}>
      <div
        className="fac-modal"
        role="dialog"
        aria-modal="true"
        aria-label={copy.title}
        onClick={(e) => e.stopPropagation()}
      >
        <h2>{copy.title}</h2>
        <p>{copy.body}</p>

        {needsRemarks && (
          <div className="fac-field">
            <label htmlFor="review-remarks">
              {mode === "rejected" ? "Rejection reason" : "Remarks"} (required)
            </label>
            <textarea
              id="review-remarks"
              rows={4}
              maxLength={500}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              autoFocus
            />
            <small>{remarks.trim().length}/500</small>
          </div>
        )}

        {error && <div className="sample-banner error-banner">{error}</div>}

        <div className="fac-form-actions" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn-ghost" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={copy.cls} disabled={busy} onClick={confirm}>
            {busy ? "Saving…" : copy.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}


// ============================================================
// EVENT FORM (create + edit)
// ============================================================

const MAX_POSTER_BYTES = 3 * 1024 * 1024;
const POSTER_TYPES = ["image/png", "image/jpeg", "image/webp"];
const TIME_PATTERN = "^(0?[1-9]|1[0-2]):[0-5][0-9]\\s?(AM|PM|am|pm)$";
const TIME_REGEX = new RegExp(TIME_PATTERN);

const EVENT_MODES = ["Online", "Offline", "Hybrid"];
const EMAIL_REGEX = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;

/* 10-digit Indian mobile; +91 / 91 / 0 prefixes and spaces/dashes allowed. */
function normalizeIndianMobile(value) {
  let digits = String(value || "").replace(/[\s\-().]/g, "");
  if (digits.startsWith("+91")) digits = digits.slice(3);
  else if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
  return digits;
}

/* Indian rupee format, e.g. 25000 -> "₹ 25,000". */
const formatRupees = (value) =>
  `₹ ${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

/* Returns the first problem as a readable message, or "" if all is well.
   The backend repeats these checks, so this only gives faster feedback. */
function validateEventDetails(form) {
  if (!form.schoolName.trim()) return "School name is required.";
  if (!DEPARTMENTS.includes(form.department)) {
    return "Please select the department from the list.";
  }
  if (!EVENT_MODES.includes(form.eventMode)) {
    return "Please select the mode of the event (Online, Offline or Hybrid).";
  }
  if (form.eventMode !== "Online" && !form.venue.trim()) {
    return "Venue is required for Offline and Hybrid events.";
  }
  if (!form.spocName.trim()) return "SPOC name is required.";
  if (!form.spocDesignation.trim()) return "SPOC designation is required.";
  if (!EMAIL_REGEX.test(form.spocEmail.trim())) {
    return "Enter a valid SPOC email address.";
  }
  if (!/^[6-9][0-9]{9}$/.test(normalizeIndianMobile(form.spocContact))) {
    return "Enter a valid 10-digit SPOC mobile number (starting with 6, 7, 8 or 9).";
  }
  // The budget is checked separately by validateBreakup().
  return "";
}

const emptyPerson = () => ({
  name: "",
  designation: "",
  organization: "",
  linkedin_url: "",
});

function toForm(event) {
  if (!event) {
    return {
      title: "",
      description: "",
      category: "",
      date: "",
      startTime: "",
      endTime: "",
      venue: "",
      capacity: "",
      skills: "",
      schoolName: "",
      department: "",
      eventMode: "",
      spocName: "",
      spocDesignation: "",
      spocEmail: "",
      spocContact: "",
    };
  }
  const [startTime = "", endTime = ""] = String(event.time || "").split(
    /\s+-\s+/
  );
  const rawDate = String(event.date || "");
  return {
    title: event.title || "",
    description: event.description || "",
    category: event.category || "",
    date: /^\d{4}-\d{2}-\d{2}/.test(rawDate) ? rawDate.slice(0, 10) : "",
    startTime: startTime.trim(),
    endTime: endTime.trim(),
    venue: event.location || event.venue || "",
    capacity:
      event.capacity !== undefined && event.capacity !== null
        ? String(event.capacity)
        : "",
    skills: Array.isArray(event.skills)
      ? event.skills.join(", ")
      : String(event.skills || ""),
    // Older events have none of these yet; they stay blank until filled in.
    schoolName: event.school_name || "",
    department: matchDepartment(event.department),
    eventMode: event.event_mode || "",
    spocName: event.spoc_name || "",
    spocDesignation: event.spoc_designation || "",
    spocEmail: event.spoc_email || "",
    spocContact: event.spoc_contact || "",
  };
}

function toPersons(event) {
  return Array.isArray(event?.resource_persons) &&
    event.resource_persons.length > 0
    ? event.resource_persons.map((person) => ({
        name: person.name || "",
        designation: person.designation || "",
        organization: person.organization || "",
        linkedin_url: person.linkedin_url || "",
      }))
    : [emptyPerson()];
}

/**
 * One form for both creating and editing a club event.
 * It writes to the same /api/events documents the Club Coordinator uses.
 */
function EventForm({
  mode, // "create" | "edit"
  event, // existing event when editing
  clubName,
  onSaved,
  onCancel,
}) {
  const editing = mode === "edit";
  const correcting = editing && event?.approval_status === "pending_faculty";

  const [form, setForm] = useState(() => toForm(event));
  const [persons, setPersons] = useState(() => toPersons(event));

  // Budget breakup. legacyTotal is set only for older events saved with a
  // single budget figure and no breakup.
  const [breakup, setBreakup] = useState(() =>
    breakupFromEvent(event?.budget_breakup)
  );
  const legacyTotal =
    editing && !hasSavedBreakup(event?.budget_breakup) && event?.budget != null
      ? Number(event.budget)
      : null;
  // Registration requirement (older events and new events: required)
  const [registrationRequired, setRegistrationRequired] = useState(() =>
    isRegistrationRequired(event)
  );
  const [registrationDeadline, setRegistrationDeadline] = useState(() =>
    toDateTimeLocal(event?.registration_deadline)
  );
  // Published events keep their deadline; changes go through the Admin.
  const deadlineLocked = editing && event?.approval_status === "published";
  const [posterFile, setPosterFile] = useState(null);
  const [posterPreview, setPosterPreview] = useState(
    event?.poster_url ? fileUrl(event.poster_url) : ""
  );
  const [saving, setSaving] = useState(false);
  const savingLock = useRef(false);
  const [error, setError] = useState("");

  // Release the temporary preview address.
  useEffect(
    () => () => {
      if (posterPreview.startsWith("blob:")) URL.revokeObjectURL(posterPreview);
    },
    [posterPreview]
  );

  function update(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  function pickPoster(changeEvent) {
    const file = changeEvent.target.files?.[0];
    if (!file) return;
    if (!POSTER_TYPES.includes(file.type)) {
      setError("Poster must be a PNG, JPG or WEBP image.");
      changeEvent.target.value = "";
      return;
    }
    if (file.size > MAX_POSTER_BYTES) {
      setError("Poster is too large (maximum 3 MB).");
      changeEvent.target.value = "";
      return;
    }
    setError("");
    setPosterFile(file);
    setPosterPreview(URL.createObjectURL(file));
  }

  function updatePerson(index, name, value) {
    setPersons((rows) =>
      rows.map((row, i) => (i === index ? { ...row, [name]: value } : row))
    );
  }

  async function submit(submitEvent) {
    submitEvent.preventDefault();
    setError("");

    if (!TIME_REGEX.test(form.startTime) || !TIME_REGEX.test(form.endTime)) {
      setError("Enter times like 10:00 AM.");
      return;
    }

    const capacity = Number(form.capacity);
    if (!Number.isInteger(capacity) || capacity < 1) {
      setError("Capacity must be a whole number of at least 1.");
      return;
    }

    let detailsProblem =
      validateEventDetails(form) ||
      validateBreakup(breakup) ||
      (registrationRequired && !deadlineLocked && (!editing || registrationDeadline)
        ? deadlineProblem(registrationDeadline, form.date, form.startTime)
        : "");

    // An older event only has a single figure. Do not silently replace it
    // with 0: the breakup must be filled in first.
    if (!detailsProblem && legacyTotal !== null && !anySelected(breakup)) {
      detailsProblem =
        "This event only has an old single budget figure. Tick the applicable rows in the Budget Breakup Form and enter their costs.";
    }

    if (detailsProblem) {
      setError(detailsProblem);
      return;
    }

    if (
      editing &&
      event?.approval_status === "published" &&
      !window.confirm(
        "This event is published. Saving your changes sends it through HOD, Dean and Admin approval again, and students will not see it until it is re-approved. Continue?"
      )
    ) {
      return;
    }

    if (savingLock.current) return; // no duplicate submissions
    savingLock.current = true;
    setSaving(true);
    try {
      const posterUrl = posterFile ? await uploadPoster(posterFile) : null;

      const payload = {
        title: form.title.trim(),
        description: form.description.trim(),
        category: form.category,
        organizer: clubName || event?.organizer || "",
        date: form.date,
        time: `${form.startTime.trim()} - ${form.endTime.trim()}`,
        location: form.venue.trim(),
        school_name: form.schoolName.trim(),
        department: form.department.trim(),
        event_mode: form.eventMode,
        spoc_name: form.spocName.trim(),
        spoc_designation: form.spocDesignation.trim(),
        spoc_email: form.spocEmail.trim(),
        spoc_contact: normalizeIndianMobile(form.spocContact),
        // Total of the ticked rows (the server recalculates and checks it)
        budget: computeTotal(breakup),
        budget_breakup: breakupToPayload(breakup),
        registration_required: registrationRequired,
        // Only events with registration have a deadline (IST); published
        // events change it through "Request deadline change" instead.
        ...(deadlineLocked
          ? {}
          : { registration_deadline: registrationRequired ? registrationDeadline : null }),
        capacity,
        skills: form.skills
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        // Always sent, so removing every person clears the list.
        resource_persons: persons
          .map((person) => ({
            name: person.name.trim(),
            designation: person.designation.trim(),
            organization: person.organization.trim(),
            linkedin_url: person.linkedin_url.trim(),
          }))
          .filter(
            (person) =>
              person.name ||
              person.designation ||
              person.organization ||
              person.linkedin_url
          ),
        // Keep the existing poster unless a new file was chosen.
        ...(posterUrl ? { poster_url: posterUrl } : {}),
      };

      const result = editing
        ? await updateEvent(event.id, payload)
        : await createEvent(payload);

      onSaved(result, mode);
    } catch (err) {
      setError(err.message || "Could not save the event.");
    } finally {
      setSaving(false);
      savingLock.current = false;
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>{editing ? "Edit Event" : "Create Event"}</h1>
        <p>
          {correcting
            ? "You are correcting a coordinator's submission. It stays in your review queue until you forward it to the HOD."
            : editing
            ? "Saving sends the event back for approval, starting with the HOD."
            : "Events you create go to the HOD, then the Dean, then the Admin. You do not approve your own event."}
        </p>
      </div>

      {editing && event?.approval_history?.length > 0 && (
        <div className="fac-notice">
          Last review:{" "}
          <b>
            {event.approval_history[event.approval_history.length - 1]
              .reviewer_name || "Reviewer"}
          </b>
          {" — "}
          {event.approval_history[event.approval_history.length - 1].remarks ||
            "no remarks"}
        </div>
      )}

      {error && <div className="sample-banner error-banner">{error}</div>}

      <form className="fac-card fac-form" onSubmit={submit}>
        <div className="fac-field">
          <label htmlFor="ev-title">Event title</label>
          <input
            id="ev-title"
            value={form.title}
            onChange={(e) => update("title", e.target.value)}
            placeholder="Enter event title"
            required
          />
        </div>

        <div className="fac-field">
          <label htmlFor="ev-desc">Description</label>
          <textarea
            id="ev-desc"
            rows={5}
            value={form.description}
            onChange={(e) => update("description", e.target.value)}
            placeholder="Describe the event"
            required
          />
        </div>

        <div className="fac-grid-form">
          <div className="fac-field">
            <label htmlFor="ev-cat">Category</label>
            <select
              id="ev-cat"
              value={form.category}
              onChange={(e) => update("category", e.target.value)}
              required
            >
              <option value="">Select category</option>
              {CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
              {form.category && !CATEGORIES.includes(form.category) && (
                <option value={form.category}>{form.category}</option>
              )}
            </select>
          </div>

          <div className="fac-field">
            <label htmlFor="ev-cap">Capacity</label>
            <input
              id="ev-cap"
              type="number"
              min="1"
              value={form.capacity}
              onChange={(e) => update("capacity", e.target.value)}
              placeholder="Maximum participants"
              required
            />
          </div>
        </div>

        <div className="fac-grid-form three">
          <div className="fac-field">
            <label htmlFor="ev-date">Date</label>
            <input
              id="ev-date"
              type="date"
              value={form.date}
              onChange={(e) => update("date", e.target.value)}
              required
            />
          </div>
          <div className="fac-field">
            <label htmlFor="ev-start">Start time</label>
            <input
              id="ev-start"
              value={form.startTime}
              onChange={(e) => update("startTime", e.target.value)}
              placeholder="10:00 AM"
              pattern={TIME_PATTERN}
              title="Enter time like 10:00 AM"
              required
            />
          </div>
          <div className="fac-field">
            <label htmlFor="ev-end">Finish time</label>
            <input
              id="ev-end"
              value={form.endTime}
              onChange={(e) => update("endTime", e.target.value)}
              placeholder="12:00 PM"
              pattern={TIME_PATTERN}
              title="Enter time like 12:00 PM"
              required
            />
          </div>
        </div>

        <div className="fac-field">
          <label htmlFor="ev-venue">
            Venue{form.eventMode === "Online" ? " (optional for Online events)" : ""}
          </label>
          <input
            id="ev-venue"
            value={form.venue}
            onChange={(e) => update("venue", e.target.value)}
            placeholder={
              form.eventMode === "Online" ? "Meeting platform or link (optional)" : "Enter venue"
            }
            required={form.eventMode !== "Online"}
          />
        </div>

        <h3 style={{ margin: "8px 0 0", fontSize: "15px" }}>Organizing details</h3>

        <div className="fac-grid-form">
          <div className="fac-field">
            <label htmlFor="ev-school">School name</label>
            <input
              id="ev-school"
              value={form.schoolName}
              onChange={(e) => update("schoolName", e.target.value)}
              placeholder="e.g. School of Computer Science"
              maxLength={150}
              required
            />
          </div>
          <div className="fac-field">
            <label htmlFor="ev-dept">Department</label>
            <select
              id="ev-dept"
              value={form.department}
              onChange={(e) => update("department", e.target.value)}
              required
            >
              <option value="">Select department</option>
              {DEPARTMENTS.map((departmentName) => (
                <option key={departmentName} value={departmentName}>
                  {departmentName}
                </option>
              ))}
            </select>
            <p style={{ fontSize: "12px", opacity: 0.7, margin: "4px 0 0" }}>
            The event is sent to the HOD of this department for approval.
          </p>
          </div>
        </div>

        <div className="fac-field">
          <label htmlFor="ev-mode">Mode of event</label>
          <select
            id="ev-mode"
            value={form.eventMode}
            onChange={(e) => update("eventMode", e.target.value)}
            required
          >
            <option value="">Select mode</option>
            {EVENT_MODES.map((modeName) => (
              <option key={modeName} value={modeName}>
                {modeName}
              </option>
            ))}
          </select>
        </div>

        <h3 style={{ margin: "8px 0 0", fontSize: "15px" }}>
          Single Point of Contact (SPOC)
        </h3>

        <div className="fac-grid-form">
          <div className="fac-field">
            <label htmlFor="ev-spoc-name">SPOC name</label>
            <input
              id="ev-spoc-name"
              value={form.spocName}
              onChange={(e) => update("spocName", e.target.value)}
              placeholder="Full name"
              maxLength={150}
              required
            />
          </div>
          <div className="fac-field">
            <label htmlFor="ev-spoc-desig">SPOC designation</label>
            <input
              id="ev-spoc-desig"
              value={form.spocDesignation}
              onChange={(e) => update("spocDesignation", e.target.value)}
              placeholder="e.g. Assistant Professor"
              maxLength={150}
              required
            />
          </div>
        </div>

        <div className="fac-grid-form">
          <div className="fac-field">
            <label htmlFor="ev-spoc-email">SPOC email address</label>
            <input
              id="ev-spoc-email"
              type="email"
              value={form.spocEmail}
              onChange={(e) => update("spocEmail", e.target.value)}
              placeholder="name@university.edu"
              required
            />
          </div>
          <div className="fac-field">
            <label htmlFor="ev-spoc-phone">SPOC contact number</label>
            <input
              id="ev-spoc-phone"
              type="tel"
              value={form.spocContact}
              onChange={(e) => update("spocContact", e.target.value)}
              placeholder="10-digit mobile number"
              required
            />
          </div>
        </div>

        <RegistrationRequirement
          value={registrationRequired}
          onChange={setRegistrationRequired}
          idPrefix="fac-regreq"
          deadline={registrationDeadline}
          onDeadlineChange={setRegistrationDeadline}
          deadlineLocked={deadlineLocked}
        />

        <BudgetBreakupForm
          value={breakup}
          onChange={setBreakup}
          legacyTotal={legacyTotal}
          idPrefix="fac-budget"
        />

        <div className="fac-field">
          <label htmlFor="ev-skills">Skills / tags</label>
          <input
            id="ev-skills"
            value={form.skills}
            onChange={(e) => update("skills", e.target.value)}
            placeholder="Python, Machine Learning, Data Preprocessing"
          />
          <small>Separate skills with commas.</small>
        </div>

        <div className="fac-field">
          <label htmlFor="ev-poster">Event poster (optional)</label>
          <input
            id="ev-poster"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={pickPoster}
          />
          <small>PNG, JPG or WEBP, up to 3 MB.</small>
          {posterPreview && (
            <img
              className="fac-poster-preview"
              src={posterPreview}
              alt="Poster preview"
            />
          )}
        </div>

        <div className="fac-field">
          <label>Resource persons</label>
          {persons.map((person, index) => (
            <div className="fac-person-row" key={index}>
              <input
                placeholder="Name"
                value={person.name}
                onChange={(e) => updatePerson(index, "name", e.target.value)}
              />
              <input
                placeholder="Designation"
                value={person.designation}
                onChange={(e) =>
                  updatePerson(index, "designation", e.target.value)
                }
              />
              <input
                placeholder="Organization"
                value={person.organization}
                onChange={(e) =>
                  updatePerson(index, "organization", e.target.value)
                }
              />
              <input
                type="url"
                placeholder="LinkedIn URL (https://…)"
                value={person.linkedin_url}
                onChange={(e) =>
                  updatePerson(index, "linkedin_url", e.target.value)
                }
              />
              <button
                type="button"
                className="btn-ghost small"
                onClick={() =>
                  setPersons((rows) =>
                    rows.length > 1
                      ? rows.filter((_, i) => i !== index)
                      : [emptyPerson()]
                  )
                }
                aria-label="Remove resource person"
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn-ghost small"
            style={{ alignSelf: "flex-start" }}
            onClick={() => setPersons((rows) => [...rows, emptyPerson()])}
          >
            + Add resource person
          </button>
        </div>

        <div className="fac-form-actions">
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving
              ? "Saving…"
              : correcting
              ? "Save corrections"
              : editing
              ? "Save & resubmit for approval"
              : "Submit for HOD approval"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={saving}
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </form>
    </>
  );
}


// ============================================================
// EVENT DETAIL VIEW
// ============================================================

function skillsOf(event) {
  return Array.isArray(event.skills)
    ? event.skills.filter(Boolean)
    : String(event.skills || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
}

function EventDetailView({
  event,
  onEdit,
  onBack,
  onTrack,
  backLabel = "Back to events",
  actionBar,
  canEditOverride,
}) {
  const persons = event.resource_persons || [];
  const skills = skillsOf(event);
  const editable =
    canEditOverride !== undefined ? canEditOverride : canEdit(event);

  const details = [
    ["Category", event.category],
    ["Created by", event.created_by_name],
    ["Creator role", ROLE_LABELS[event.created_by_role] || event.created_by_role],
    ["Date", event.date],
    ["Time", event.time],
    ["Venue", event.location || event.venue],
    ["Capacity", event.capacity],
    ["Registration", registrationLabel(event)],
    [
      "Registration deadline",
      isRegistrationRequired(event) && event.registration_deadline
        ? formatDeadline(event.registration_deadline) +
          (isRegistrationClosed(event) ? " (closed)" : "")
        : null,
    ],
    ["Budget (private)", event.budget != null ? formatRupees(event.budget) : null],
    ["Submitted version", `v${event.submission_version || 1}`],
    ["Submitted", formatDateTime(event.created_at)],
    ["Last updated", formatDateTime(event.last_updated)],
  ];

  return (
    <>
      <div className="fac-detail-head">
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>{event.title}</h1>
          <p className="fac-sub" style={{ marginTop: 6 }}>
            {event.organizer || "—"}
          </p>
        </div>
        <div className="fac-detail-actions">
          <StatusBadge status={event.approval_status} />
          <button
            type="button"
            className="btn-primary small"
            disabled={!editable || event.is_modification}
            title={
              event.is_modification
                ? "This is a requested change; it cannot be edited here"
                : isLiveEvent(event)
                ? "This event is published. Ask the Admin to approve a change."
                : editable
                ? "Edit this event"
                : "This event is under review and cannot be edited"
            }
            onClick={() => onEdit(event)}
          >
            {isLiveEvent(event) ? "Request Modification" : "Edit"}
          </button>
          <button type="button" className="btn-ghost small" onClick={onBack}>
            {backLabel}
          </button>
          {onTrack && (
            <button type="button" className="btn-ghost small" onClick={() => onTrack(event)}>
              Track approval
            </button>
          )}
        </div>
      </div>

      {actionBar}

      {event.is_modification && (
        <div className="fac-card" style={{ marginTop: 18 }}>
          <h3>Requested modification</h3>
          <p style={{ margin: "0 0 10px", color: "var(--text-dim)", fontSize: 13.5 }}>
            <b>Reason:</b> {event.modification?.reason || "—"}
          </p>
          <ModificationCompare rows={rowsFromQueueItem(event.modification)} />
          <p className="fac-sub" style={{ marginTop: 10 }}>
            The published event stays unchanged until the HOD, Dean and Admin approve this revised version.
          </p>
        </div>
      )}

      {!editable && !actionBar && (
        <div className="fac-notice">
          This event is under review, so it cannot be edited until a reviewer
          returns it.
        </div>
      )}

      {event.poster_url && (
        <a href={fileUrl(event.poster_url)} target="_blank" rel="noreferrer">
          <img
            className="fac-poster-preview"
            src={fileUrl(event.poster_url)}
            alt={`${event.title} poster`}
          />
        </a>
      )}

      <div className="fac-card" style={{ marginTop: 18 }}>
        <h3>Event details</h3>
        <dl className="fac-info-grid three">
          {details.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value || value === 0 ? value : "—"}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="fac-card">
        <h3>Description</h3>
        <p style={{ margin: 0, whiteSpace: "pre-wrap", color: "var(--text-dim)", fontSize: 13.5, lineHeight: 1.6 }}>
          {event.description || "No description."}
        </p>
      </div>

      <div className="fac-card">
        <h3>Skills / domains</h3>
        {skills.length === 0 ? (
          <div className="dropdown-empty">No skills listed.</div>
        ) : (
          <div className="fac-tags">
            {skills.map((skill) => (
              <span key={skill} className="status-badge status-completed">
                {skill}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="fac-card">
        <h3>Resource persons</h3>
        {persons.length === 0 ? (
          <div className="dropdown-empty">No resource persons added.</div>
        ) : (
          <ul className="fac-list">
            {persons.map((person, index) => (
              <li key={index}>
                <div>
                  <b style={{ color: "var(--text)" }}>{person.name || "—"}</b>
                  <div className="fac-sub">
                    {[person.designation, person.organization]
                      .filter(Boolean)
                      .join(", ")}
                  </div>
                </div>
                {/^https?:\/\//i.test(person.linkedin_url || "") && (
                  <a
                    className="link"
                    href={person.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    LinkedIn ↗
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Published event: ask the Admin to change the registration deadline */}
      <DeadlineChangeRequest event={event} />

      {/* Event Checklist (published events only; the server enforces it) */}
      <div className="fac-card">
        <EventChecklist event={event} />
        <EventReport event={event} />
      </div>

      {/* Budget breakup: read-only here. Edit it from the event form. */}
      {hasSavedBreakup(event.budget_breakup) && (
        <div className="fac-card">
          <h3>Budget breakup</h3>
          <BudgetBreakupForm
            value={breakupFromEvent(event.budget_breakup)}
            readOnly
            idPrefix="fac-view-budget"
          />
        </div>
      )}

      {event.report_pdf_url && (
        <div className="fac-card">
          <h3>Event report</h3>
          <a
            className="link"
            href={fileUrl(event.report_pdf_url)}
            target="_blank"
            rel="noopener noreferrer"
          >
            📄 Open report (PDF) ↗
          </a>
        </div>
      )}

      <ReviewHistory event={event} />
    </>
  );
}


// ============================================================
// PAGE: ALL CLUB EVENTS
// ============================================================

const PAGE_SIZE = 10;

const EMPTY_FILTERS = {
  creator: "",
  category: "",
  status: "",
  date_from: "",
  date_to: "",
  sort: "created",
  order: "desc",
};

function ClubEventsPage({
  search,
  refreshKey,
  onView,
  onEdit,
  onCreate,
}) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [debouncedSearch, setDebouncedSearch] = useState(search);

  const [state, setState] = useState("loading"); // loading | ready | error
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  // Wait for the user to stop typing before querying.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Any filter change goes back to the first page.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, filters]);

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      const data = await fetchFacultyEvents({
        q: debouncedSearch,
        ...filters,
        page,
        page_size: PAGE_SIZE,
      });
      setResult(data);
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, [debouncedSearch, filters, page, refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  function setFilter(name, value) {
    setFilters((current) => ({ ...current, [name]: value }));
  }

  const hasFilters =
    debouncedSearch ||
    Object.keys(EMPTY_FILTERS).some(
      (key) => filters[key] !== EMPTY_FILTERS[key]
    );

  return (
    <>
      <div className="page-head">
        <h1>All Club Events</h1>
        <p>
          Every event of your club, whether created by you or by a Club
          Coordinator. These are the same records the coordinators see.
        </p>
      </div>

      <div className="fac-filters">
        <select
          value={filters.creator}
          onChange={(e) => setFilter("creator", e.target.value)}
          aria-label="Filter by creator"
        >
          <option value="">All creators</option>
          <option value="faculty">Created by Faculty</option>
          <option value="coordinator">Created by Coordinators</option>
        </select>

        <select
          value={filters.category}
          onChange={(e) => setFilter("category", e.target.value)}
          aria-label="Filter by category"
        >
          <option value="">All categories</option>
          {CATEGORIES.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>

        <select
          value={filters.status}
          onChange={(e) => setFilter("status", e.target.value)}
          aria-label="Filter by approval status"
        >
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).map(([value, [label]]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>

        <label className="fac-date-filter">
          From
          <input
            type="date"
            value={filters.date_from}
            onChange={(e) => setFilter("date_from", e.target.value)}
          />
        </label>

        <label className="fac-date-filter">
          To
          <input
            type="date"
            value={filters.date_to}
            onChange={(e) => setFilter("date_to", e.target.value)}
          />
        </label>

        <select
          value={`${filters.sort}:${filters.order}`}
          onChange={(e) => {
            const [sort, order] = e.target.value.split(":");
            setFilters((current) => ({ ...current, sort, order }));
          }}
          aria-label="Sort events"
        >
          <option value="created:desc">Newest submitted</option>
          <option value="created:asc">Oldest submitted</option>
          <option value="date:asc">Event date (earliest)</option>
          <option value="date:desc">Event date (latest)</option>
          <option value="updated:desc">Recently updated</option>
          <option value="title:asc">Title A–Z</option>
          <option value="title:desc">Title Z–A</option>
        </select>

        {hasFilters && (
          <button
            type="button"
            className="btn-ghost small"
            onClick={() => setFilters(EMPTY_FILTERS)}
          >
            Clear filters
          </button>
        )}
      </div>

      {debouncedSearch && (
        <p className="fac-search-note">
          Searching titles for “{debouncedSearch}” (use the search box at the
          top to change it).
        </p>
      )}

      {state === "loading" && !result && (
        <div className="fac-skeleton" style={{ height: 220 }} />
      )}

      {state === "error" && (
        <div className="empty-state">
          <h3>Unable to load events</h3>
          <p>{error}</p>
          <button type="button" className="btn-primary" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {result && state !== "error" && (
        <>
          {result.events.length === 0 ? (
            <div className="empty-state">
              <h3>{hasFilters ? "No matching events" : "No events yet"}</h3>
              <p>
                {hasFilters
                  ? "Try changing or clearing the filters."
                  : "Create the first event for your club."}
              </p>
              {!hasFilters && (
                <button type="button" className="btn-primary" onClick={onCreate}>
                  + Create Event
                </button>
              )}
            </div>
          ) : (
            <div
              className="table-wrap"
              style={{ opacity: state === "loading" ? 0.6 : 1 }}
            >
              <table className="data-table" style={{ minWidth: 1000 }}>
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Created by</th>
                    <th>Category</th>
                    <th>Event date</th>
                    <th>Submitted</th>
                    <th>Status</th>
                    <th>Last updated</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {result.events.map((event) => {
                    const editable = canEdit(event);
                    return (
                      <tr key={event.id}>
                        <td>
                          <b style={{ color: "var(--text)" }}>{event.title}</b>
                          <div className="fac-sub">
                            v{event.submission_version || 1}
                          </div>
                        </td>
                        <td>
                          {event.created_by_name || "—"}
                          <div className="fac-sub">
                            {event.created_by_role === "faculty"
                              ? "Faculty"
                              : event.created_by_role === "club"
                              ? "Coordinator"
                              : event.created_by_role || ""}
                          </div>
                        </td>
                        <td>{event.category || "—"}</td>
                        <td>{event.date || "—"}</td>
                        <td>{formatDate(event.created_at)}</td>
                        <td>
                          <StatusBadge status={event.approval_status} />
                        </td>
                        <td>{formatDateTime(event.last_updated)}</td>
                        <td>
                          <div className="row-actions">
                            <button
                              type="button"
                              className="btn-ghost small"
                              onClick={() => onView(event)}
                            >
                              View
                            </button>
                            <button
                              type="button"
                              className="btn-primary small"
                              disabled={!editable}
                              title={
                                editable
                                  ? "Edit this event"
                                  : "This event is under review and cannot be edited"
                              }
                              onClick={() => onEdit(event)}
                            >
                              Edit
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {result.total > 0 && (
            <div className="pagination">
              <button
                type="button"
                className="btn-ghost small"
                disabled={page <= 1}
                onClick={() => setPage((value) => value - 1)}
              >
                ← Previous
              </button>
              <span>
                Page {result.page} of {result.pages} · {result.total} event
                {result.total === 1 ? "" : "s"}
              </span>
              <button
                type="button"
                className="btn-ghost small"
                disabled={page >= result.pages}
                onClick={() => setPage((value) => value + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}


// ============================================================
// PAGE: PENDING EVENT REVIEWS
// ============================================================

function PendingReviewsPage({
  search,
  refreshKey,
  onEdit,        // (event) => open the edit form for corrections
  onDecision,    // (message) => parent refreshes KPIs and shows a notice
  onTrack,
  flash,
}) {
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [events, setEvents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [modal, setModal] = useState(null); // { mode, event }

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      const result = await fetchFacultyEvents({
        status: "pending_faculty",
        sort: "created",
        order: "asc",
        page_size: 100,
      });
      setEvents(result.events);
      // Keep the open detail in sync with fresh data (e.g. after corrections).
      setSelected((current) =>
        current ? result.events.find((e) => e.id === current.id) || null : null
      );
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, [refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  async function submitDecision(remarks) {
    const { mode, event } = modal;
    const decision = mode === "forward" ? "approved" : mode;
    await reviewEvent(event.id, decision, remarks); // throws on failure
    // Only reached after the backend confirmed the decision.
    setModal(null);
    setSelected(null);
    onDecision(
      mode === "forward"
        ? `“${event.title}” was approved and forwarded to the HOD.`
        : mode === "changes"
        ? `Changes requested for “${event.title}”. It was returned to its creator.`
        : `“${event.title}” was rejected and returned to its creator.`
    );
  }

  const q = search.trim().toLowerCase();
  const rows = events.filter(
    (event) => !q || String(event.title || "").toLowerCase().includes(q)
  );

  // ------------------- detail + decision -------------------
  if (selected) {
    const bar = (
      <div className="fac-review-bar">
        <div className="fac-review-bar-text">
          <b>Faculty review</b>
          <span>
            Inspect the details, correct them if needed, then decide. You
            cannot publish directly; approval goes to the HOD.
          </span>
        </div>
        <div className="fac-review-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => setModal({ mode: "forward", event: selected })}
          >
            ✓ Approve &amp; forward to HOD
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setModal({ mode: "changes", event: selected })}
          >
            ↩ Request changes
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={() => setModal({ mode: "rejected", event: selected })}
          >
            ✕ Reject
          </button>
        </div>
      </div>
    );

    return (
      <>
        <EventDetailView
          event={selected}
          actionBar={bar}
          canEditOverride={true}
          onEdit={(event) => onEdit(event)}
          onTrack={onTrack}
          onBack={() => setSelected(null)}
          backLabel="Back to review queue"
        />
        {modal && (
          <ReviewModal
            mode={modal.mode}
            event={modal.event}
            onSubmit={submitDecision}
            onClose={() => setModal(null)}
          />
        )}
      </>
    );
  }

  // ------------------- queue -------------------
  return (
    <>
      <div className="page-head">
        <h1>Pending Event Reviews</h1>
        <p>
          Events submitted by Club Coordinators. Review each one, then forward
          it to the HOD or send it back.
        </p>
      </div>

      {flash && <div className="fac-flash">{flash}</div>}

      <PendingReports />

      {state === "loading" && <div className="fac-skeleton" style={{ height: 180 }} />}

      {state === "error" && (
        <div className="empty-state">
          <h3>Unable to load the review queue</h3>
          <p>{error}</p>
          <button type="button" className="btn-primary" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {state === "ready" &&
        (rows.length === 0 ? (
          <div className="empty-state">
            <h3>{q ? "No matching events" : "No pending reviews"}</h3>
            <p>
              {q
                ? "Try a different search."
                : "Events submitted by Club Coordinators will appear here."}
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table" style={{ minWidth: 820 }}>
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Club Coordinator</th>
                  <th>Submitted</th>
                  <th>Event date</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <b style={{ color: "var(--text)" }}>{event.title}</b>
                    </td>
                    <td>{event.created_by_name || "—"}</td>
                    <td>{formatDate(event.created_at)}</td>
                    <td>{event.date || "—"}</td>
                    <td>v{event.submission_version || 1}</td>
                    <td>
                      <StatusBadge status={event.approval_status} />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-primary small"
                        onClick={() => setSelected(event)}
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </>
  );
}


// ============================================================
// PAGE: EVENTS AWAITING CORRECTIONS
// ============================================================

function CorrectionsPage({ search, refreshKey, onView, onEdit, flash }) {
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [events, setEvents] = useState([]);

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      const result = await fetchFacultyEvents({
        status: "changes_requested,rejected",
        sort: "updated",
        order: "desc",
        page_size: 100,
      });
      setEvents(result.events);
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, [refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  const q = search.trim().toLowerCase();
  const rows = events.filter(
    (event) => !q || String(event.title || "").toLowerCase().includes(q)
  );

  return (
    <>
      <div className="page-head">
        <h1>Events Awaiting Corrections</h1>
        <p>
          Events returned by a reviewer. You or the Club Coordinator can edit
          them; an edit sends the event back into review as a new version.
        </p>
      </div>

      {flash && <div className="fac-flash">{flash}</div>}

      {state === "loading" && <div className="fac-skeleton" style={{ height: 180 }} />}

      {state === "error" && (
        <div className="empty-state">
          <h3>Unable to load events</h3>
          <p>{error}</p>
          <button type="button" className="btn-primary" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {state === "ready" &&
        (rows.length === 0 ? (
          <div className="empty-state">
            <h3>{q ? "No matching events" : "Nothing awaiting corrections"}</h3>
            <p>
              {q
                ? "Try a different search."
                : "Events returned by the Faculty, HOD, Dean or Admin appear here with their remarks."}
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table" style={{ minWidth: 900 }}>
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Created by</th>
                  <th>Returned by</th>
                  <th>Remarks</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => {
                  const history = event.approval_history || [];
                  const last = history[history.length - 1] || {};
                  return (
                    <tr key={event.id}>
                      <td>
                        <b style={{ color: "var(--text)" }}>{event.title}</b>
                      </td>
                      <td>{event.created_by_name || "—"}</td>
                      <td>
                        {ROLE_LABELS[last.reviewer_role] || last.reviewer_role || "—"}
                        <div className="fac-sub">{formatDateTime(last.reviewed_at)}</div>
                      </td>
                      <td style={{ whiteSpace: "normal", minWidth: 220 }}>
                        {last.remarks || "—"}
                      </td>
                      <td>v{event.submission_version || 1}</td>
                      <td>
                        <StatusBadge status={event.approval_status} />
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            type="button"
                            className="btn-ghost small"
                            onClick={() => onView(event)}
                          >
                            View
                          </button>
                          <button
                            type="button"
                            className="btn-primary small"
                            onClick={() => onEdit(event)}
                          >
                            Edit
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
    </>
  );
}


// ============================================================
// PAGE: APPROVAL TRACKING
// ============================================================

function ApprovalTrackingPage({ search, refreshKey, focusEventId }) {
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [events, setEvents] = useState([]);
  const [selectedId, setSelectedId] = useState(focusEventId || "");

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      const result = await fetchFacultyEvents({
        sort: "updated",
        order: "desc",
        page_size: 100,
      });
      setEvents(result.events);
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, [refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (focusEventId) setSelectedId(focusEventId);
  }, [focusEventId]);

  const q = search.trim().toLowerCase();
  const options = events.filter(
    (event) => !q || String(event.title || "").toLowerCase().includes(q)
  );
  const selected =
    events.find((event) => event.id === selectedId) || options[0] || null;

  const history = selected?.approval_history || [];
  const lastReturn = [...history]
    .reverse()
    .find((h) => h.decision === "changes" || h.decision === "rejected");
  const needsCorrections =
    selected &&
    (selected.approval_status === "changes_requested" ||
      selected.approval_status === "rejected");

  return (
    <>
      <div className="page-head">
        <h1>Approval Tracking</h1>
        <p>
          Follow an event through Faculty review → HOD → Dean → Admin →
          Published, with every decision and remark.
        </p>
      </div>

      {state === "loading" && <div className="fac-skeleton" style={{ height: 180 }} />}

      {state === "error" && (
        <div className="empty-state">
          <h3>Unable to load events</h3>
          <p>{error}</p>
          <button type="button" className="btn-primary" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {state === "ready" && events.length === 0 && (
        <div className="empty-state">
          <h3>No events to track</h3>
          <p>Create an event or wait for a coordinator to submit one.</p>
        </div>
      )}

      {state === "ready" && events.length > 0 && (
        <>
          <div className="fac-field" style={{ maxWidth: 520, marginBottom: 16 }}>
            <label htmlFor="track-event">Event</label>
            <select
              id="track-event"
              value={selected?.id || ""}
              onChange={(e) => setSelectedId(e.target.value)}
            >
              {options.length === 0 && <option value="">No matching events</option>}
              {options.map((event) => (
                <option key={event.id} value={event.id}>
                  {event.title} — {currentStageLabel(event)}
                </option>
              ))}
            </select>
          </div>

          {selected && (
            <>
              <div className="fac-detail-head">
                <div>
                  <h2 style={{ margin: 0, fontSize: 18 }}>{selected.title}</h2>
                  <p className="fac-sub" style={{ marginTop: 4 }}>
                    Created by {selected.created_by_name || "—"} (
                    {ROLE_LABELS[selected.created_by_role] || selected.created_by_role || "—"})
                  </p>
                </div>
                <StatusBadge status={selected.approval_status} />
              </div>

              {!selected.approval_status && (
                <div className="fac-notice">
                  This event has no approval status (it was created before the
                  approval workflow). It will enter the workflow when edited.
                </div>
              )}

              {needsCorrections && (
                <div className="fac-notice fac-notice-danger">
                  <b>Requires corrections.</b>{" "}
                  {lastReturn
                    ? `${ROLE_LABELS[lastReturn.reviewer_role] || lastReturn.reviewer_role}: ${
                        lastReturn.remarks || "no remarks"
                      }`
                    : "A reviewer returned this event."}
                </div>
              )}

              <ApprovalStepper event={selected} />
              <ReviewHistory event={selected} />
            </>
          )}
        </>
      )}
    </>
  );
}


// ============================================================
// PAGE: NOTIFICATIONS
// ============================================================

const NOTIFICATION_ICONS = {
  review_requested: "📥",
  event_returned: "↩️",
  event_approved: "✅",
  event_published: "🎉",
};

function NotificationsPage({ onChanged, onTrack }) {
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [items, setItems] = useState([]);

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      const result = await fetchFacultyNotifications();
      setItems(result.notifications);
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function markRead(item) {
    if (item.is_read) return;
    try {
      await markFacultyNotificationRead(item.id);
      setItems((rows) =>
        rows.map((row) => (row.id === item.id ? { ...row, is_read: true } : row))
      );
      onChanged();
    } catch (err) {
      setError(err.message);
    }
  }

  async function markAll() {
    try {
      await markAllFacultyNotificationsRead();
      setItems((rows) => rows.map((row) => ({ ...row, is_read: true })));
      onChanged();
    } catch (err) {
      setError(err.message);
    }
  }

  const unread = items.filter((item) => !item.is_read).length;

  return (
    <>
      <div className="page-head">
        <h1>Notifications</h1>
        <p>Coordinator submissions and decisions from the HOD, Dean and Admin.</p>
      </div>

      {error && <div className="sample-banner error-banner">{error}</div>}

      {state === "loading" && <div className="fac-skeleton" style={{ height: 160 }} />}

      {state === "ready" &&
        (items.length === 0 ? (
          <div className="empty-state">
            <h3>No notifications yet</h3>
            <p>You will be notified when a coordinator submits an event or a reviewer decides on one.</p>
          </div>
        ) : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <span className="fac-sub">{unread} unread</span>
              <button
                type="button"
                className="btn-ghost small"
                disabled={unread === 0}
                onClick={markAll}
              >
                Mark all as read
              </button>
            </div>

            <div className="fac-card">
              <ul className="fac-list">
                {items.map((item) => (
                  <li
                    key={item.id}
                    className={item.is_read ? "" : "fac-unread"}
                    onClick={() => markRead(item)}
                  >
                    <div>
                      <div>
                        <span style={{ marginRight: 6 }}>{NOTIFICATION_ICONS[item.type] || "🔔"}</span>
                        <b style={{ color: "var(--text)" }}>{item.title}</b>
                        {!item.is_read && <span className="fac-dot" />}
                      </div>
                      <div className="fac-sub" style={{ fontSize: 13 }}>{item.message}</div>
                      {item.remarks && (
                        <div className="remarks">
                          “{item.remarks}”
                          {item.reviewer_role
                            ? ` — ${ROLE_LABELS[item.reviewer_role] || item.reviewer_role}`
                            : ""}
                        </div>
                      )}
                      {item.event_id && (
                        <button
                          type="button"
                          className="link"
                          style={{ marginTop: 6 }}
                          onClick={(e) => {
                            e.stopPropagation();
                            markRead(item);
                            onTrack(item.event_id);
                          }}
                        >
                          Track approval →
                        </button>
                      )}
                    </div>
                    <div className="fac-sub" style={{ whiteSpace: "nowrap" }}>
                      {formatDateTime(item.created_at)}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </>
        ))}
    </>
  );
}


// ============================================================
// CHARTS  (plain SVG / CSS — no charting package needed)
// ============================================================

const CHART_COLORS = [
  "#7c6cff", "#22c55e", "#f59e0b", "#ef4444",
  "#38bdf8", "#a855f7", "#14b8a6", "#94a3b8",
];

function DonutChart({ data }) {
  const total = data.reduce((sum, item) => sum + item.value, 0);
  if (total === 0) {
    return <div className="dropdown-empty">No data to chart yet.</div>;
  }
  const radius = 44;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="fac-donut-wrap">
      <svg viewBox="0 0 120 120" className="fac-donut" role="img" aria-label="Distribution chart">
        <circle cx="60" cy="60" r={radius} fill="none" stroke="var(--surface-2)" strokeWidth="16" />
        {data.map((item, index) => {
          const length = (item.value / total) * circumference;
          const circle = (
            <circle
              key={item.label}
              cx="60"
              cy="60"
              r={radius}
              fill="none"
              stroke={CHART_COLORS[index % CHART_COLORS.length]}
              strokeWidth="16"
              strokeDasharray={`${length} ${circumference - length}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 60 60)"
            />
          );
          offset += length;
          return circle;
        })}
        <text x="60" y="58" textAnchor="middle" className="fac-donut-total">{total}</text>
        <text x="60" y="73" textAnchor="middle" className="fac-donut-caption">total</text>
      </svg>

      <ul className="fac-legend">
        {data.map((item, index) => (
          <li key={item.label}>
            <span className="fac-swatch" style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} />
            <span className="fac-legend-label">{item.label}</span>
            <b>{item.value}</b>
            <span className="fac-sub">{Math.round((item.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BarList({ data }) {
  const max = Math.max(...data.map((item) => item.value), 0);
  if (!data.length || max === 0) {
    return <div className="dropdown-empty">No data to chart yet.</div>;
  }
  return (
    <ul className="fac-bars">
      {data.map((item, index) => (
        <li key={item.label}>
          <span className="fac-bar-label" title={item.label}>{item.label}</span>
          <span className="fac-bar-track">
            <span
              className="fac-bar-fill"
              style={{
                width: `${(item.value / max) * 100}%`,
                background: CHART_COLORS[index % CHART_COLORS.length],
              }}
            />
          </span>
          <b>{item.value}</b>
        </li>
      ))}
    </ul>
  );
}

function ColumnChart({ data }) {
  const max = Math.max(...data.map((item) => item.value), 0);
  if (!data.length || max === 0) {
    return <div className="dropdown-empty">No data to chart yet.</div>;
  }
  return (
    <div className="fac-columns" role="img" aria-label="Activity over time">
      {data.map((item) => (
        <div className="fac-column" key={item.label}>
          <span className="fac-column-value">{item.value}</span>
          <span
            className="fac-column-bar"
            style={{ height: `${Math.max((item.value / max) * 100, 4)}%` }}
          />
          <span className="fac-column-label">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

function monthLabel(value) {
  const [year, month] = String(value).split("-");
  const date = new Date(Number(year), Number(month) - 1, 1);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

function StatTile({ label, value, hint }) {
  return (
    <div className="fac-kpi" style={{ cursor: "default" }}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="fac-sub">{hint}</div>}
    </div>
  );
}

// ============================================================
// PAGE: EVENT ANALYTICS
// ============================================================

function AnalyticsPage({ refreshKey }) {
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [data, setData] = useState(null);

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      setData(await fetchFacultyAnalytics());
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, [refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  if (state === "loading") {
    return (
      <>
        <div className="page-head"><h1>Event Analytics</h1></div>
        <div className="fac-kpi-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <div className="fac-skeleton" key={i} />
          ))}
        </div>
      </>
    );
  }

  if (state === "error") {
    return (
      <div className="empty-state">
        <h3>Unable to load analytics</h3>
        <p>{error}</p>
        <button type="button" className="btn-primary" onClick={load}>Try again</button>
      </div>
    );
  }

  const totals = data.totals;
  const statusData = data.byStatus.map((row) => ({
    label: (STATUS_LABELS[row.label] || [String(row.label).replace(/_/g, " ")])[0],
    value: row.count,
  }));
  const categoryData = data.byCategory.map((row) => ({ label: row.label, value: row.count }));
  const monthData = data.activityByMonth.map((row) => ({ label: monthLabel(row.month), value: row.count }));
  const upcomingData = [
    { label: "Upcoming", value: totals.upcoming },
    { label: "Completed", value: totals.completed },
  ].filter((row) => row.value > 0);

  return (
    <>
      <div className="page-head">
        <h1>Event Analytics</h1>
        <p>Figures for your club, calculated from the events and registrations in the database.</p>
      </div>

      <div className="fac-kpi-grid">
        <StatTile label="Total events" value={totals.events} />
        <StatTile label="Registrations" value={totals.registrations} />
        <StatTile label="Attendance marked" value={totals.attendance} />
        <StatTile
          label="Attendance rate"
          value={totals.attendanceRate === null ? "—" : `${totals.attendanceRate}%`}
          hint={totals.attendanceRate === null ? "No registrations yet" : "of registrations"}
        />
        <StatTile label="Upcoming (published)" value={totals.upcoming} />
        <StatTile label="Completed (published)" value={totals.completed} />
      </div>

      <div className="fac-grid-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="fac-card">
          <h3>Event status distribution</h3>
          <DonutChart data={statusData} />
        </div>
        <div className="fac-card">
          <h3>Upcoming vs completed</h3>
          {upcomingData.length === 0 ? (
            <div className="dropdown-empty">
              No published events yet. Only published events are counted here.
            </div>
          ) : (
            <DonutChart data={upcomingData} />
          )}
        </div>
      </div>

      <div className="fac-grid-2" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 18 }}>
        <div className="fac-card">
          <h3>Events by category</h3>
          <BarList data={categoryData} />
        </div>
        <div className="fac-card">
          <h3>Events created per month</h3>
          <ColumnChart data={monthData} />
        </div>
      </div>

      <div className="section-head">
        <h2>Registrations &amp; attendance by event</h2>
      </div>
      {data.byEvent.length === 0 ? (
        <div className="empty-state">
          <h3>No registrations yet</h3>
          <p>Registration and attendance figures appear once students register for your events.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data-table" style={{ minWidth: 700 }}>
            <thead>
              <tr>
                <th>Event</th>
                <th>Date</th>
                <th>Capacity</th>
                <th>Registrations</th>
                <th>Seats filled</th>
                <th>Attended</th>
                <th>Attendance rate</th>
              </tr>
            </thead>
            <tbody>
              {data.byEvent.map((row) => (
                <tr key={row.id}>
                  <td><b style={{ color: "var(--text)" }}>{row.title}</b></td>
                  <td>{row.date || "—"}</td>
                  <td>{row.capacity ?? "—"}</td>
                  <td>{row.registrations}</td>
                  <td>{row.fillRate === null ? "—" : `${row.fillRate}%`}</td>
                  <td>{row.attendance}</td>
                  <td>{row.attendanceRate === null ? "—" : `${row.attendanceRate}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ============================================================
// PAGE: REVIEW HISTORY
// ============================================================

const HISTORY_PAGE_SIZE = 15;

const DECISION_LABELS = {
  approved: ["Approved", "status-published"],
  changes: ["Changes requested", "fac-status-returned"],
  rejected: ["Rejected", "status-cancelled"],
};

function ReviewHistoryPage({ search, refreshKey, onTrack }) {
  const [filters, setFilters] = useState({ kind: "", role: "", decision: "" });
  const [page, setPage] = useState(1);
  const [debounced, setDebounced] = useState(search);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [debounced, filters]);

  const load = useCallback(async () => {
    setState("loading");
    setError("");
    try {
      setResult(
        await fetchFacultyReviewHistory({
          q: debounced,
          ...filters,
          page,
          page_size: HISTORY_PAGE_SIZE,
        })
      );
      setState("ready");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }, [debounced, filters, page, refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  function setFilter(name, value) {
    setFilters((current) => {
      const next = { ...current, [name]: value };
      // Decisions only exist for reviews, so an edits-only view has none.
      if (name === "kind" && value === "edit") next.decision = "";
      return next;
    });
  }

  const hasFilters = debounced || filters.kind || filters.role || filters.decision;

  return (
    <>
      <div className="page-head">
        <h1>Review History</h1>
        <p>Every review decision and edit across your club's events, newest first.</p>
      </div>

      <div className="fac-filters">
        <select value={filters.kind} onChange={(e) => setFilter("kind", e.target.value)} aria-label="Type">
          <option value="">Reviews &amp; edits</option>
          <option value="review">Review decisions only</option>
          <option value="edit">Edits only</option>
        </select>

        <select value={filters.role} onChange={(e) => setFilter("role", e.target.value)} aria-label="Role">
          <option value="">All roles</option>
          <option value="faculty">Faculty</option>
          <option value="club">Club Coordinator</option>
          <option value="hod">HOD</option>
          <option value="dean">Dean</option>
          <option value="admin">Admin</option>
        </select>

        <select
          value={filters.decision}
          disabled={filters.kind === "edit"}
          onChange={(e) => setFilter("decision", e.target.value)}
          aria-label="Decision"
        >
          <option value="">All decisions</option>
          <option value="approved">Approved</option>
          <option value="changes">Changes requested</option>
          <option value="rejected">Rejected</option>
        </select>

        {hasFilters && (
          <button
            type="button"
            className="btn-ghost small"
            onClick={() => setFilters({ kind: "", role: "", decision: "" })}
          >
            Clear filters
          </button>
        )}
      </div>

      {debounced && (
        <p className="fac-search-note">Showing events with “{debounced}” in the title.</p>
      )}

      {state === "loading" && !result && <div className="fac-skeleton" style={{ height: 200 }} />}

      {state === "error" && (
        <div className="empty-state">
          <h3>Unable to load review history</h3>
          <p>{error}</p>
          <button type="button" className="btn-primary" onClick={load}>Try again</button>
        </div>
      )}

      {result && state !== "error" && (
        <>
          {result.items.length === 0 ? (
            <div className="empty-state">
              <h3>{hasFilters ? "No matching records" : "No history yet"}</h3>
              <p>
                {hasFilters
                  ? "Try changing or clearing the filters."
                  : "Review decisions and edits will be recorded here as events move through approval."}
              </p>
            </div>
          ) : (
            <div className="table-wrap" style={{ opacity: state === "loading" ? 0.6 : 1 }}>
              <table className="data-table" style={{ minWidth: 900 }}>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Event</th>
                    <th>By</th>
                    <th>Action</th>
                    <th>Remarks / changes</th>
                    <th>Version</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((item, index) => {
                    const [label, cls] =
                      item.type === "review"
                        ? DECISION_LABELS[item.decision] || [item.decision, "status-draft"]
                        : ["Edited", "status-draft"];
                    return (
                      <tr key={`${item.event_id}-${item.at}-${index}`}>
                        <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(item.at)}</td>
                        <td>
                          <button
                            type="button"
                            className="link"
                            style={{ textAlign: "left" }}
                            onClick={() => onTrack(item.event_id)}
                            title="Track this event's approval"
                          >
                            {item.event_title}
                          </button>
                        </td>
                        <td>
                          {item.name || "—"}
                          <div className="fac-sub">{ROLE_LABELS[item.role] || item.role || ""}</div>
                        </td>
                        <td><span className={`status-badge ${cls}`}>{label}</span></td>
                        <td style={{ whiteSpace: "normal", minWidth: 220 }}>
                          {item.type === "review"
                            ? item.remarks || "—"
                            : item.changed_fields?.length
                            ? `Changed: ${item.changed_fields.join(", ")}`
                            : "Saved without changing details"}
                        </td>
                        <td>v{item.submission_version || 1}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {result.total > 0 && (
            <div className="pagination">
              <button type="button" className="btn-ghost small" disabled={page <= 1} onClick={() => setPage((v) => v - 1)}>
                ← Previous
              </button>
              <span>
                Page {result.page} of {result.pages} · {result.total} record{result.total === 1 ? "" : "s"}
              </span>
              <button type="button" className="btn-ghost small" disabled={page >= result.pages} onClick={() => setPage((v) => v + 1)}>
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}

// ============================================================
// FACULTY DASHBOARD (main component)
// ============================================================

// ============================================================
// FACULTY DASHBOARD
// (replaces the Inter-College Leader dashboard; same file/route)
// ============================================================

function ComingSoon({ pageKey }) {
  const info = COMING_SOON[pageKey];
  const item = FACULTY_NAV.find((entry) => entry.key === pageKey);
  return (
    <div className="fac-soon-card">
      <span className="fac-soon-tag">
        Not yet connected · planned for Step {item?.step || "—"}
      </span>
      <h2>{item?.label}</h2>
      <p>{info?.text}</p>
      <p className="fac-api-note">{info?.api}</p>
    </div>
  );
}

function KpiCard({ icon, label, value, tone, onClick }) {
  return (
    <button
      type="button"
      className={`fac-kpi ${tone || ""}`}
      onClick={onClick}
    >
      <div className="stat-top">
        <span className="stat-icon">{icon}</span>
      </div>
      <div className="stat-value">{value ?? 0}</div>
      <div className="stat-label">{label}</div>
    </button>
  );
}

// ------------------------------------------------------------
// MAIN COMPONENT
// ------------------------------------------------------------

export default function FacultyDashboard() {
  const navigate = useNavigate();

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [section, setSection] = useState("dashboard");
  const [search, setSearch] = useState("");
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [justCreated, setJustCreated] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [flash, setFlash] = useState("");
  const [origin, setOrigin] = useState("events"); // where Back/Cancel/Save return to
  const [focusEventId, setFocusEventId] = useState("");
  const [modEvent, setModEvent] = useState(null);

  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null); // { message, status }
  const [data, setData] = useState(null);

  // Frontend guard only. The backend enforces the real rules (403).
  useEffect(() => {
    const role = localStorage.getItem("userRole");
    if (role && role !== "faculty") {
      navigate("/", { replace: true });
    }
  }, [navigate]);

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const result = await fetchFacultyDashboard();
      setData(result);
      setStatus("ready");
    } catch (err) {
      setError({ message: err.message, status: err.status });
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function handleLogout() {
    clearSession();
    navigate("/");
  }

  const kpis = data?.kpis;
  const faculty = data?.faculty;
  const club = data?.club;

  // Real items needing attention (no fake notifications)
  const attention = useMemo(() => {
    if (!data) return [];
    return [
      ...(data.pendingReviews || []).map((event) => ({
        id: `p-${event.id}`,
        text: `Review needed: ${event.title}`,
        sub: `Submitted by ${event.created_by_name || "a coordinator"}`,
      })),
      ...(data.returnedEvents || []).map((event) => ({
        id: `r-${event.id}`,
        text: `Returned for corrections: ${event.title}`,
        sub: `Status: ${String(event.approval_status).replace(/_/g, " ")}`,
      })),
    ];
  }, [data]);

  const q = search.trim().toLowerCase();
  const matches = (event) =>
    !q || String(event.title || "").toLowerCase().includes(q);

  function goTo(key) {
    setFlash("");
    setSection(key);
  }

  function viewEvent(event, from = "events") {
    setSelectedEvent(event);
    setOrigin(from);
    setFlash("");
    setSection("event-view");
  }

  function editEvent(event, from = "events") {
    // A modification under review is not an event: nothing to edit here.
    if (event?.is_modification) return;

    // A published event is live for students, so changes go through a
    // modification request (the Admin reviews it first).
    if (isLiveEvent(event)) {
      setModEvent(event);
      return;
    }

    setSelectedEvent(event);
    setOrigin(from);
    setFlash("");
    setSection("event-edit");
  }

  function trackEvent(eventOrId) {
    setFocusEventId(
      typeof eventOrId === "string" ? eventOrId : eventOrId?.id || ""
    );
    goTo("tracking");
  }

  function afterChange(message, target) {
    setSelectedEvent(null);
    setRefreshKey((value) => value + 1);
    load(); // refresh KPI counts and sidebar badges
    setSection(target);
    setFlash(message);
  }

  // After a successful create: open the saved event's details page.
  async function openCreatedEvent(result) {
    const newId = result?.event_id || result?.id;
    const message = "Event created and submitted to the HOD for approval.";
    try {
      if (!newId) throw new Error("no id");
      const data = await fetchFacultyEvents({ page_size: 100, sort: "created", order: "desc" });
      const saved = (data.events || []).find(
        (item) => String(item.id || item._id) === String(newId)
      );
      if (!saved) throw new Error("not found");
      setJustCreated(true);
      setRefreshKey((value) => value + 1);
      load();
      viewEvent(saved, "events");
      setFlash(message);
    } catch {
      afterChange(message, "events"); // could not load it: show the list instead
    }
  }

  function handleSaved(result, mode) {
    if (mode !== "edit" && origin !== "reviews") {
      openCreatedEvent(result);
      return;
    }
    setJustCreated(false);
    if (origin === "reviews") {
      afterChange(
        "Corrections saved. The event is still awaiting your review.",
        "reviews"
      );
      return;
    }
    afterChange(
      mode === "edit"
        ? "Event updated and sent back for approval."
        : "Event created and submitted to the HOD for approval.",
      origin === "corrections" ? "corrections" : "events"
    );
  }

  // ----------------------------------------------------------
  // PAGES
  // ----------------------------------------------------------

  function eventTable(events, emptyTitle, emptyText, showRemarks) {
    if (!events.length) {
      return (
        <div className="empty-state">
          <h3>{emptyTitle}</h3>
          <p>{emptyText}</p>
        </div>
      );
    }
    return (
      <div className="table-wrap">
        <table className="data-table" style={{ minWidth: 720 }}>
          <thead>
            <tr>
              <th>Event</th>
              <th>Created by</th>
              <th>Event date</th>
              <th>Version</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id}>
                <td>
                  <b style={{ color: "var(--text)" }}>{event.title}</b>
                  {showRemarks && (event.approval_history || []).length > 0 && (
                    <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>
                      {(() => {
                        const last = event.approval_history.slice(-1)[0];
                        return `${ROLE_LABELS[last.reviewer_role] || last.reviewer_role}: ${
                          last.remarks || "no remarks"
                        }`;
                      })()}
                    </div>
                  )}
                </td>
                <td>{event.created_by_name || "—"}</td>
                <td>{event.date || "—"}</td>
                <td>v{event.submission_version || 1}</td>
                <td>
                  <StatusBadge status={event.approval_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  function renderDashboard() {
    const pending = (data.pendingReviews || []).filter(matches);
    const returned = (data.returnedEvents || []).filter(matches);
    const activity = data.recentActivity || [];

    return (
      <>
        <div className="page-head">
          <h1>Welcome, {faculty?.name || "Faculty"}.</h1>
          <p>
            Supervise {club?.name ? `“${club.name}”` : "your club"} events,
            review coordinator submissions and track approvals.
          </p>
        </div>

        <div className="fac-kpi-grid">
          <KpiCard icon="📅" label="Total Club Events" value={kpis.total} onClick={() => setSection("events")} />
          <KpiCard icon="📥" label="Pending Faculty Reviews" value={kpis.pendingFacultyReviews} tone={kpis.pendingFacultyReviews ? "warn" : ""} onClick={() => setSection("reviews")} />
          <KpiCard icon="🏛️" label="Awaiting HOD Approval" value={kpis.awaitingHod} onClick={() => setSection("tracking")} />
          <KpiCard icon="↩️" label="Returned for Corrections" value={kpis.returnedForCorrections} tone={kpis.returnedForCorrections ? "danger" : ""} onClick={() => setSection("corrections")} />
          <KpiCard icon="✅" label="Published Events" value={kpis.published} onClick={() => setSection("events")} />
          <KpiCard icon="🗓️" label="Upcoming Events" value={kpis.upcoming} onClick={() => setSection("events")} />
        </div>

        <div className="section-head">
          <h2>📥 Awaiting your review</h2>
          <button type="button" className="link" onClick={() => setSection("reviews")}>
            Open review queue
          </button>
        </div>
        {eventTable(
          pending,
          q ? "No matching events" : "No pending reviews",
          q
            ? "Try a different search."
            : "Events submitted by Club Coordinators will appear here."
        )}

        <div className="section-head">
          <h2>↩️ Returned for corrections</h2>
          <button type="button" className="link" onClick={() => setSection("corrections")}>
            View all
          </button>
        </div>
        {eventTable(
          returned,
          q ? "No matching events" : "Nothing returned",
          q
            ? "Try a different search."
            : "Events the HOD, Dean or Admin send back will appear here with their remarks.",
          true
        )}

        <div className="section-head">
          <h2>🕘 Recent approval activity</h2>
        </div>
        <div className="fac-card">{renderActivity(activity)}</div>
      </>
    );
  }

  function renderActivity(activity) {
    if (!activity.length) {
      return (
        <div className="dropdown-empty">
          No review decisions have been recorded for your club yet.
        </div>
      );
    }
    return (
      <ul className="fac-list">
        {activity.map((item, index) => (
          <li key={`${item.event_id}-${index}`}>
            <div>
              <div>
                <b style={{ color: "var(--text)" }}>
                  {ROLE_LABELS[item.reviewer_role] || item.reviewer_role}
                </b>{" "}
                {item.decision === "approved"
                  ? "approved"
                  : item.decision === "changes"
                  ? "requested changes to"
                  : "rejected"}{" "}
                “{item.event_title}”
              </div>
              {item.remarks && <div className="remarks">“{item.remarks}”</div>}
              <div className="sub">
                {item.reviewer_name} · v{item.submission_version}
              </div>
            </div>
            <div className="sub" style={{ whiteSpace: "nowrap" }}>
              {formatDateTime(item.reviewed_at)}
            </div>
          </li>
        ))}
      </ul>
    );
  }

  function renderClubOverview() {
    const coordinators = data.coordinators || [];
    return (
      <>
        <div className="page-head">
          <h1>{club?.name || "Club Overview"}</h1>
          <p>The club you supervise as Faculty.</p>
        </div>

        <div className="fac-grid-2">
          <div>
            <div className="fac-card">
              <h3>Club details</h3>
              <dl className="fac-info-grid">
                <div>
                  <dt>Club name</dt>
                  <dd>{club?.name || "—"}</dd>
                </div>
                <div>
                  <dt>Category / domain</dt>
                  <dd>{club?.category || "—"}</dd>
                </div>
                <div>
                  <dt>College</dt>
                  <dd>{club?.college || "—"}</dd>
                </div>
                <div>
                  <dt>Department</dt>
                  <dd>{faculty?.department || "—"}</dd>
                </div>
                <div className="wide">
                  <dt>Description</dt>
                  <dd>
                    {club?.description || "No description has been added for this club yet."}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="fac-card">
              <h3>Event summary</h3>
              <dl className="fac-info-grid">
                <div>
                  <dt>Total events</dt>
                  <dd>{kpis.total}</dd>
                </div>
                <div>
                  <dt>Upcoming events</dt>
                  <dd>{kpis.upcoming}</dd>
                </div>
                <div>
                  <dt>Published</dt>
                  <dd>{kpis.published}</dd>
                </div>
                <div>
                  <dt>In approval</dt>
                  <dd>
                    {kpis.pendingFacultyReviews +
                      kpis.awaitingHod +
                      kpis.awaitingDean +
                      kpis.awaitingAdmin}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="fac-card">
              <h3>Recent activity</h3>
              {renderActivity(data.recentActivity || [])}
            </div>
          </div>

          <div>
            <div className="fac-card">
              <h3>Assigned Faculty</h3>
              <dl className="fac-info-grid">
                <div className="wide">
                  <dt>Name</dt>
                  <dd>{faculty?.name || "—"}</dd>
                </div>
                <div className="wide">
                  <dt>Email</dt>
                  <dd>{faculty?.email || "—"}</dd>
                </div>
              </dl>
            </div>

            <div className="fac-card">
              <h3>Club Coordinators</h3>
              {coordinators.length === 0 ? (
                <div className="dropdown-empty">
                  No active Club Coordinator is linked to this club yet.
                </div>
              ) : (
                <ul className="fac-list">
                  {coordinators.map((person) => (
                    <li key={person.id}>
                      <div>
                        <b style={{ color: "var(--text)" }}>{person.name}</b>
                        <div className="sub">{person.email}</div>
                      </div>
                      <div className="sub">{person.position}</div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      </>
    );
  }

 function renderProfile() {
  return (
    <>
      <div className="page-head">
        <h1>Profile &amp; Settings</h1>
        <p>Your faculty account.</p>
      </div>

      <div className="fac-card" style={{ maxWidth: 560 }}>
        <dl className="fac-info-grid">
          <div className="wide">
            <dt>Name</dt>
            <dd>{faculty?.name || "—"}</dd>
          </div>
          <div className="wide">
            <dt>Email</dt>
            <dd>{faculty?.email || "—"}</dd>
          </div>
          <div>
            <dt>Role</dt>
            <dd>Faculty</dd>
          </div>
          <div>
            <dt>Department</dt>
            <dd>{faculty?.department || "—"}</dd>
          </div>
          <div className="wide">
            <dt>Assigned club</dt>
            <dd>{club?.name || "—"}</dd>
          </div>
        </dl>

        <p className="sub" style={{ marginTop: 16, fontSize: 12.5, color: "var(--text-faint)" }}>
          Editing profile details and changing the password are not yet connected.
        </p>
      </div>

      <div className="fac-card" style={{ maxWidth: 560, marginTop: 20 }}>
        <h3>Appearance</h3>
        <p className="sub">Customize your dashboard appearance.</p>

        <div className="fac-info-grid">
          <div className="wide" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <b>Theme</b>
              <p className="sub" style={{ fontSize: 12.5 }}>Choose dark or light mode.</p>
            </div>

            <select
              value={document.documentElement.getAttribute("data-theme") || "dark"}
              onChange={(e) => {
                const selectedTheme = e.target.value;
                document.documentElement.setAttribute("data-theme", selectedTheme);
                document.body.setAttribute("data-theme", selectedTheme);
                window.dispatchEvent(
                  new CustomEvent("campuspath-theme-change", { detail: selectedTheme })
                );
              }}
              style={{
                padding: "10px 14px",
                borderRadius: 8,
                border: "1px solid var(--border-color)",
                background: "var(--card-bg)",
                color: "var(--text-primary)",
                cursor: "pointer"
              }}
            >
              <option value="dark">Dark Mode</option>
              <option value="light">Light Mode</option>
            </select>
          </div>
        </div>
      </div>
    </>
  );
}

  function renderBody() {
    if (status === "loading") {
      return (
        <div className="fac-kpi-grid">
          {Array.from({ length: 6 }).map((_, index) => (
            <div className="fac-skeleton" key={index} />
          ))}
        </div>
      );
    }

    if (status === "error") {
      const denied = error?.status === 401 || error?.status === 403;
      return (
        <div className="empty-state">
          <h3>
            {denied ? "Access denied" : "Unable to load the Faculty Dashboard"}
          </h3>
          <p>
            {error?.message ||
              "Something went wrong. Please try again."}
          </p>
          {denied ? (
            <button type="button" className="btn-primary" onClick={handleLogout}>
              Back to login
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={load}>
              Try again
            </button>
          )}
        </div>
      );
    }

    switch (section) {
      case "dashboard":
        return renderDashboard();
      case "club":
        return renderClubOverview();
      case "profile":
        return renderProfile();
      case "events":
        return (
          <>
            {flash && <div className="fac-flash">{flash}</div>}
            <ClubEventsPage
              search={search}
              refreshKey={refreshKey}
              onView={(event) => viewEvent(event, "events")}
              onEdit={(event) => editEvent(event, "events")}
              onCreate={() => goTo("create")}
            />
          </>
        );
      case "create":
        return (
          <EventForm
            mode="create"
            clubName={club?.name}
            onSaved={handleSaved}
            onCancel={() => goTo("events")}
          />
        );
      case "event-view":
        return selectedEvent ? (
          <>
            {justCreated && (
              <div className="fac-notice" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <strong>Event created.</strong>
                <span>Status: {(STATUS_LABELS[selectedEvent.approval_status] || [selectedEvent.approval_status])[0]}</span>
                <button type="button" className="btn-secondary" onClick={() => { setJustCreated(false); goTo("dashboard"); }}>
                  Back to Dashboard
                </button>
                <button type="button" className="btn-secondary" onClick={() => { setJustCreated(false); goTo("events"); }}>
                  View My Events
                </button>
              </div>
            )}
            <EventDetailView
              event={selectedEvent}
              onEdit={(event) => editEvent(event, origin)}
              onTrack={trackEvent}
              onBack={() => { setJustCreated(false); goTo(origin); }}
              backLabel={
                origin === "corrections" ? "Back to corrections" : "Back to events"
              }
            />
          </>
        ) : null;
      case "event-edit":
        return selectedEvent ? (
          <EventForm
            key={selectedEvent.id}
            mode="edit"
            event={selectedEvent}
            clubName={club?.name}
            onSaved={handleSaved}
            onCancel={() =>
              origin === "reviews"
                ? goTo("reviews")
                : viewEvent(selectedEvent, origin)
            }
          />
        ) : null;
      case "reviews":
        return (
          <PendingReviewsPage
            search={search}
            refreshKey={refreshKey}
            flash={flash}
            onEdit={(event) => editEvent(event, "reviews")}
            onTrack={trackEvent}
            onDecision={(message) => afterChange(message, "reviews")}
          />
        );
      case "modifications":
        return <MyModificationRequests />;
      case "corrections":
        return (
          <CorrectionsPage
            search={search}
            refreshKey={refreshKey}
            flash={flash}
            onView={(event) => viewEvent(event, "corrections")}
            onEdit={(event) => editEvent(event, "corrections")}
          />
        );
      case "tracking":
        return (
          <ApprovalTrackingPage
            search={search}
            refreshKey={refreshKey}
            focusEventId={focusEventId}
          />
        );
      case "notifications":
        return <NotificationsPage onChanged={load} onTrack={trackEvent} />;
      case "analytics":
        return <AnalyticsPage refreshKey={refreshKey} />;
      case "history":
        return (
          <ReviewHistoryPage
            search={search}
            refreshKey={refreshKey}
            onTrack={trackEvent}
          />
        );
      default:
        return <ComingSoon pageKey={section} />;
    }
  }

  return (
    <div className="dashboard-shell">
      <FacultySidebar
        activeKey={section === "event-view" || section === "event-edit" ? "events" : section}
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        facultyName={faculty?.name}
        clubName={club?.name}
        counts={kpis}
        onToggleCollapsed={() => setCollapsed((value) => !value)}
        onCloseMobile={() => setMobileOpen(false)}
        onNavigate={goTo}
        onLogout={handleLogout}
      />

      <div className="dashboard-main">
        <FacultyTopBar
          title={
            section === "event-view"
              ? "Event Details"
              : section === "event-edit"
              ? "Edit Event"
              : PAGE_TITLES[section] || "Dashboard"
          }
          search={search}
          facultyName={faculty?.name}
          facultyEmail={faculty?.email}
          attention={attention}
          onSearch={setSearch}
          onMenuToggle={() => setMobileOpen(true)}
          onCreate={() => goTo("create")}
          onNavigate={goTo}
          onLogout={handleLogout}
        />

        <main className="content">
          {![
            "dashboard",
            "events",
            "create",
            "event-view",
            "event-edit",
            "reviews",
            "modifications",
            "corrections",
            "tracking",
            "notifications",
            "analytics",
            "history",
          ].includes(
            section
          ) &&
            status === "ready" && (
            <button
              type="button"
              className="btn-ghost small"
              style={{ marginBottom: 16 }}
              onClick={() => setSection("dashboard")}
            >
              ← Back to dashboard
            </button>
          )}
          {renderBody()}
        </main>
      </div>

      {modEvent && (
        <RequestModificationModal
          event={modEvent}
          onClose={() => setModEvent(null)}
          onDone={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </div>
  );
}