// ============================================================
// STUDENT - OFFICIAL CLASS + OFFICIAL TIMETABLE
//
// Exports
//   ClassSelectStep            onboarding step 6 ("Select Your Official Class")
//   StudentClassTimetablePage  dashboard page: my class, official timetable,
//                              change class, availability preferences
//   ClassPrompt                small dashboard notice for students with no
//                              official class yet
//   OfficialTimetableView      read-only weekly timetable (day tabs + week)
//
// The timetable itself is NEVER editable here: it is the one the Admin
// published for the class. Students only choose their class (and their own
// availability preferences, which are personal).
// ============================================================

import React, { useCallback, useEffect, useMemo, useState } from "react";

import AvailabilityPreferences from "../onboarding/timetable/components/AvailabilityPreferences";
import {
  DAYS,
  DAY_LABELS,
  formatTime,
  toMinutes,
} from "../onboarding/timetable/timeUtils";

import "../onboarding/timetable/timetable.css";
import "../../student-class.css";

// ============================================================
// CONSTANTS + API
// ============================================================

const API_BASE = "http://127.0.0.1:8000";

const STEP_LABELS = [
  "Academic",
  "Interests",
  "Skills",
  "Goals",
  "Domains",
  "Class",
];

const DEFAULT_PREFS = {
  preferredEventDuration: [],
  bufferTime: 15,
  preferredEventTimes: [],
  notificationPrefs: [],
};

const TYPE_LABEL = {
  lecture: "Lecture",
  lab: "Laboratory",
  tutorial: "Tutorial",
  seminar: "Seminar",
  other: "Class",
  lunch: "Lunch",
  break: "Break",
};

function readError(data, status) {
  const detail = data?.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || String(item)).join("; ");
  }

  return data?.message || `Request failed (${status})`;
}

async function api(path, options = {}) {
  const token = localStorage.getItem("token");

  if (!token) {
    throw new Error("You are signed out. Please log in again.");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
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

const studentApi = {
  classes: () => api("/api/classes"),
  myClass: () => api("/api/student/class"),
  timetable: () => api("/api/student/timetable"),
  classTimetable: (id) =>
    api(`/api/classes/${encodeURIComponent(id)}/timetable`),
  setClass: (classId) =>
    api("/api/student/class", {
      method: "PATCH",
      body: JSON.stringify({ class_id: classId }),
    }),
  savePreferences: (preferences) =>
    api("/api/student/availability-preferences", {
      method: "PUT",
      body: JSON.stringify(preferences),
    }),
};

// ============================================================
// HELPERS
// ============================================================

function todayKey() {
  const index = new Date().getDay(); // 0 = Sunday
  return index >= 1 && index <= 5 ? DAYS[index - 1] : null;
}

function blocksFor(timetable, day) {
  return [...(Array.isArray(timetable?.[day]) ? timetable[day] : [])].sort(
    (a, b) => toMinutes(a.startTime) - toMinutes(b.startTime)
  );
}

function countBlocks(timetable) {
  return DAYS.reduce((sum, day) => sum + blocksFor(timetable, day).length, 0);
}

function formatWhen(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
}

function describeClass(cls) {
  return [
    cls.department,
    `Year ${cls.year_of_study}`,
    `Sem ${cls.semester}`,
    `Section ${cls.section}`,
    cls.batch,
    cls.academic_year,
  ]
    .filter(Boolean)
    .join(" · ");
}

function withPreferenceDefaults(raw) {
  const prefs = { ...DEFAULT_PREFS, ...(raw || {}) };

  ["preferredEventDuration", "preferredEventTimes", "notificationPrefs"].forEach(
    (key) => {
      if (!Array.isArray(prefs[key])) prefs[key] = [];
    }
  );

  prefs.bufferTime = Number.isFinite(Number(prefs.bufferTime))
    ? Number(prefs.bufferTime)
    : DEFAULT_PREFS.bufferTime;

  return prefs;
}

// ============================================================
// READ-ONLY TIMETABLE
// ============================================================

function SessionCard({ block }) {
  return (
    <article className={`scl-session scl-type-${block.type}`}>
      <div className="scl-session-time">
        {formatTime(block.startTime)} – {formatTime(block.endTime)}
      </div>

      <div className="scl-session-subject">
        {block.subject}
        {block.courseCode ? (
          <span className="scl-session-code"> · {block.courseCode}</span>
        ) : null}
      </div>

      <div className="scl-session-meta">
        <span className="scl-type-tag">
          {TYPE_LABEL[block.type] || block.type}
        </span>
        {block.faculty ? <span>{block.faculty}</span> : null}
        {block.room ? <span>{block.room}</span> : null}
      </div>
    </article>
  );
}

export function OfficialTimetableView({
  timetable,
  emptyMessage = "There are no sessions in this timetable yet.",
}) {
  const today = todayKey();
  const [view, setView] = useState(today || "week");

  if (countBlocks(timetable) === 0) {
    return (
      <div className="scl-empty" role="status">
        <div className="scl-empty-icon" aria-hidden="true">
          🗓️
        </div>
        <p>{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="scl-timetable">
      <div className="scl-tabs" role="tablist" aria-label="Choose a day">
        {DAYS.map((day) => (
          <button
            key={day}
            type="button"
            role="tab"
            aria-selected={view === day}
            className={`scl-tab ${view === day ? "active" : ""}`}
            onClick={() => setView(day)}
          >
            {DAY_LABELS[day].slice(0, 3)}
            {day === today ? <span className="scl-today">Today</span> : null}
          </button>
        ))}

        <button
          type="button"
          role="tab"
          aria-selected={view === "week"}
          className={`scl-tab ${view === "week" ? "active" : ""}`}
          onClick={() => setView("week")}
        >
          Week
        </button>
      </div>

      {view === "week" ? (
        <div className="scl-week">
          {DAYS.map((day) => {
            const blocks = blocksFor(timetable, day);

            return (
              <section
                key={day}
                className={`scl-week-day ${day === today ? "is-today" : ""}`}
              >
                <h3>
                  {DAY_LABELS[day]}
                  {day === today ? <span className="scl-today">Today</span> : null}
                </h3>

                {blocks.length === 0 ? (
                  <p className="scl-muted">No sessions</p>
                ) : (
                  blocks.map((block) => (
                    <SessionCard key={block.id || `${day}-${block.startTime}`} block={block} />
                  ))
                )}
              </section>
            );
          })}
        </div>
      ) : (
        <div className="scl-day" role="tabpanel">
          {blocksFor(timetable, view).length === 0 ? (
            <p className="scl-muted scl-day-empty">
              No sessions on {DAY_LABELS[view]}.
            </p>
          ) : (
            blocksFor(timetable, view).map((block) => (
              <SessionCard key={block.id || `${view}-${block.startTime}`} block={block} />
            ))
          )}
        </div>
      )}

      <p className="scl-legend">
        <span className="scl-dot scl-type-lecture" /> Lecture
        <span className="scl-dot scl-type-lab" /> Laboratory
        <span className="scl-dot scl-type-tutorial" /> Tutorial
        <span className="scl-dot scl-type-seminar" /> Seminar
        <span className="scl-dot scl-type-lunch" /> Lunch / break
      </p>
    </div>
  );
}

// ============================================================
// CLASS PICKER (search + list of ACTIVE classes the Admin created)
// ============================================================

function ClassPicker({ classes, selectedId, onSelect, profile }) {
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [showAll, setShowAll] = useState(false);

  const wantDepartment = profile?.department || "";
  const wantYear = parseInt(profile?.year, 10) || null;
  const wantSemester = parseInt(profile?.semester, 10) || null;

  const profileMatches = useMemo(
    () =>
      classes.filter(
        (cls) =>
          (!wantDepartment || cls.department === wantDepartment) &&
          (!wantYear || cls.year_of_study === wantYear) &&
          (!wantSemester || cls.semester === wantSemester)
      ),
    [classes, wantDepartment, wantYear, wantSemester]
  );

  const hasProfileDetails = !!(wantDepartment || wantYear || wantSemester);
  const usingProfile =
    !showAll &&
    hasProfileDetails &&
    profileMatches.length > 0 &&
    profileMatches.length < classes.length;

  const pool = usingProfile ? profileMatches : classes;

  const departments = useMemo(
    () => [...new Set(classes.map((cls) => cls.department))].sort(),
    [classes]
  );

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();

    return pool.filter((cls) => {
      if (department && cls.department !== department) return false;
      if (!needle) return true;
      return `${cls.name} ${describeClass(cls)}`.toLowerCase().includes(needle);
    });
  }, [pool, search, department]);

  return (
    <div className="scl-picker">
      <div className="scl-picker-filters">
        <input
          className="scl-input"
          type="search"
          placeholder="Search your class…"
          aria-label="Search classes"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />

        {departments.length > 1 && (
          <select
            className="scl-input"
            aria-label="Filter by department"
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
          >
            <option value="">All departments</option>
            {departments.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}
      </div>

      {usingProfile && (
        <p className="scl-note">
          Showing classes that match your department, year and semester.{" "}
          <button type="button" className="scl-link" onClick={() => setShowAll(true)}>
            Show all classes
          </button>
        </p>
      )}

      {visible.length === 0 ? (
        <p className="scl-muted scl-picker-empty">
          No classes match your search.
        </p>
      ) : (
        <div className="scl-options" role="radiogroup" aria-label="Official class">
          {visible.map((cls) => {
            const checked = selectedId === cls.id;

            return (
              <label
                key={cls.id}
                className={`scl-option ${checked ? "selected" : ""}`}
              >
                <input
                  type="radio"
                  name="official-class"
                  value={cls.id}
                  checked={checked}
                  onChange={() => onSelect(cls.id)}
                />

                <span className="scl-option-body">
                  <span className="scl-option-name">{cls.name}</span>
                  <span className="scl-option-detail">{describeClass(cls)}</span>
                </span>

                <span
                  className={`scl-badge ${
                    cls.has_published_timetable ? "ok" : "wait"
                  }`}
                >
                  {cls.has_published_timetable
                    ? "Timetable available"
                    : "Timetable not published yet"}
                </span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ClassPreview({ classId }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    studentApi
      .classTimetable(classId)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", message: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, [classId]);

  if (state.status === "loading") {
    return <p className="scl-muted" role="status">Loading the timetable…</p>;
  }

  if (state.status === "error") {
    return <p className="scl-error" role="alert">{state.message}</p>;
  }

  return (
    <OfficialTimetableView
      timetable={state.data.timetable}
      emptyMessage={
        state.data.message ||
        "The official timetable for this class has not been published yet."
      }
    />
  );
}

// ============================================================
// ONBOARDING STEP 6
// ============================================================

export function ClassSelectStep({ profile, onBack, onDone }) {
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [classes, setClasses] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [prefs, setPrefs] = useState(DEFAULT_PREFS);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");

    try {
      const [list, mine] = await Promise.all([
        studentApi.classes(),
        studentApi.myClass().catch(() => null),
      ]);

      const active = Array.isArray(list?.classes) ? list.classes : [];
      setClasses(active);

      if (mine?.class && active.some((cls) => cls.id === mine.class.id)) {
        setSelectedId(mine.class.id);
      }

      setStatus("ready");
    } catch (err) {
      setError(err.message || "Could not load the classes.");
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const noClassesYet = status === "ready" && classes.length === 0;
  const canFinish =
    status === "ready" && !saving && (noClassesYet || !!selectedId);

  async function finish() {
    setSaveError("");
    setSaving(true);

    try {
      // Nothing moves on unless every request succeeded.
      if (selectedId) {
        await studentApi.setClass(selectedId);
      }

      await studentApi.savePreferences(prefs);
      onDone();
    } catch (err) {
      setSaveError(err.message || "Could not save. Please try again.");
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <header className="page-top">
        <div className="brand">
          <span className="brand-mark">CP</span>
          CampusPath <span className="brand-ai">AI</span>
        </div>
      </header>

      <div className="progress-track" aria-label="Onboarding progress">
        {STEP_LABELS.map((label, index) => {
          const state = index < 5 ? "done" : "active";

          return (
            <div className={`step-node ${state}`} key={label}>
              {index > 0 && <div className="step-line" />}
              <div className="step-circle">{index < 5 ? "✓" : index + 1}</div>
              <div className="step-label">{label}</div>
            </div>
          );
        })}
      </div>

      <main className="stage">
        <div className="stage-head">
          <div className="eyebrow">STEP 6 OF 6</div>
          <h1>Select Your Official Class</h1>
          <p>
            Pick the class you belong to. Your university sets the official
            timetable for each class, so you never have to type it in.
          </p>
        </div>

        <div className="why-card">
          <strong>Why we ask.</strong> CampusPath AI uses your class timetable
          to avoid recommending events that clash with your lectures and labs.
        </div>

        {status === "loading" && (
          <p className="scl-muted" role="status">
            Loading classes…
          </p>
        )}

        {status === "error" && (
          <div className="scl-error-box" role="alert">
            <p>{error}</p>
            <button type="button" className="btn-ghost" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {noClassesYet && (
          <div className="scl-empty" role="status">
            <div className="scl-empty-icon" aria-hidden="true">
              🏫
            </div>
            <p>
              No classes have been set up yet. You can finish now and choose
              your class later from your dashboard.
            </p>
          </div>
        )}

        {status === "ready" && classes.length > 0 && (
          <>
            <ClassPicker
              classes={classes}
              selectedId={selectedId}
              onSelect={setSelectedId}
              profile={profile}
            />

            {selectedId && (
              <section className="scl-card" aria-label="Timetable preview">
                <h2>Your timetable</h2>
                <ClassPreview classId={selectedId} />
              </section>
            )}
          </>
        )}

        {status === "ready" && (
          <section className="scl-card scl-prefs" aria-label="Availability preferences">
            <h2>Your availability preferences</h2>
            <p className="scl-muted">
              These are personal. They help us suggest events at times that suit
              you.
            </p>
            <AvailabilityPreferences
              preferences={prefs}
              onChange={setPrefs}
              onImportClick={() => {}}
            />
          </section>
        )}

        {saveError && (
          <p className="save-error" role="alert">
            {saveError}
          </p>
        )}

        <div className="nav-btns">
          <button type="button" className="btn-back" onClick={onBack} disabled={saving}>
            ← Back
          </button>

          <button
            type="button"
            className="btn-primary"
            disabled={!canFinish}
            onClick={finish}
          >
            {saving
              ? "Saving…"
              : noClassesYet
              ? "Finish without a class"
              : "Finish"}
          </button>
        </div>
      </main>
    </div>
  );
}

// ============================================================
// DASHBOARD PAGE: MY CLASS + OFFICIAL TIMETABLE
// ============================================================

export function StudentClassTimetablePage({ onBack }) {
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [data, setData] = useState(null);

  const [changing, setChanging] = useState(false);
  const [classes, setClasses] = useState(null);
  const [classesError, setClassesError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [savingClass, setSavingClass] = useState(false);

  const [prefs, setPrefs] = useState(DEFAULT_PREFS);
  const [savingPrefs, setSavingPrefs] = useState(false);

  const [notice, setNotice] = useState(null);
  const [changed, setChanged] = useState(false);

  const load = useCallback(async () => {
    setStatus("loading");
    setError("");

    try {
      const result = await studentApi.timetable();
      setData(result);
      setPrefs(withPreferenceDefaults(result.preferences));
      setStatus("ready");
    } catch (err) {
      setError(err.message || "Could not load your timetable.");
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function openChange() {
    setChanging(true);
    setSelectedId(data?.class?.id || "");
    setClassesError("");

    if (classes === null) {
      try {
        const list = await studentApi.classes();
        setClasses(Array.isArray(list?.classes) ? list.classes : []);
      } catch (err) {
        setClassesError(err.message || "Could not load the classes.");
      }
    }
  }

  async function saveClass() {
    setSavingClass(true);
    setNotice(null);

    try {
      await studentApi.setClass(selectedId);
      await load();
      setChanging(false);
      setChanged(true);
      setNotice({ kind: "success", text: "Your class has been updated." });
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
    } finally {
      setSavingClass(false);
    }
  }

  async function savePrefs() {
    setSavingPrefs(true);
    setNotice(null);

    try {
      await studentApi.savePreferences(prefs);
      setChanged(true);
      setNotice({ kind: "success", text: "Availability preferences saved." });
    } catch (err) {
      setNotice({ kind: "error", text: err.message });
    } finally {
      setSavingPrefs(false);
    }
  }

  const cls = data?.class;
  const source = data?.source;

  return (
    <div className="page scl-page">
      <header className="page-top scl-topbar">
        <div className="brand">
          <span className="brand-mark">CP</span>
          CampusPath <span className="brand-ai">AI</span>
        </div>

        <button
          type="button"
          className="btn-ghost small"
          onClick={() => onBack(changed)}
        >
          ← Back to dashboard
        </button>
      </header>

      <main className="stage scl-stage">
        <div className="stage-head">
          <div className="eyebrow">MY CLASS</div>
          <h1>Class &amp; timetable</h1>
          <p>
            Your timetable is set by your university and updates automatically
            when it changes. It cannot be edited here.
          </p>
        </div>

        {notice && (
          <div
            className={`scl-banner ${notice.kind}`}
            role={notice.kind === "error" ? "alert" : "status"}
          >
            {notice.text}
          </div>
        )}

        {status === "loading" && (
          <p className="scl-muted" role="status">
            Loading your timetable…
          </p>
        )}

        {status === "error" && (
          <div className="scl-error-box" role="alert">
            <p>{error}</p>
            <button type="button" className="btn-ghost" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {status === "ready" && (
          <>
            <section className="scl-card" aria-label="My class">
              <div className="scl-card-head">
                <h2>My class</h2>
                {!changing && (
                  <button type="button" className="btn-ghost small" onClick={openChange}>
                    {cls ? "Change class" : "Choose class"}
                  </button>
                )}
              </div>

              {cls ? (
                <>
                  <p className="scl-class-name">{cls.name}</p>
                  <p className="scl-muted">{describeClass(cls)}</p>

                  {source === "official" && data.published && (
                    <p className="scl-muted">
                      Official timetable v{data.version}
                      {data.published_at
                        ? ` · updated ${formatWhen(data.published_at)}`
                        : ""}
                    </p>
                  )}
                </>
              ) : (
                <p className="scl-muted">{data.message}</p>
              )}

              {cls && data.class_active === false && (
                <p className="scl-warn" role="status">
                  {data.message}
                </p>
              )}

              {source === "legacy" && (
                <p className="scl-warn" role="status">
                  {data.message}
                </p>
              )}
            </section>

            {changing && (
              <section className="scl-card" aria-label="Change class">
                <h2>Choose your class</h2>

                {classes === null && !classesError && (
                  <p className="scl-muted" role="status">
                    Loading classes…
                  </p>
                )}

                {classesError && (
                  <p className="scl-error" role="alert">
                    {classesError}
                  </p>
                )}

                {classes && classes.length === 0 && (
                  <p className="scl-muted">No classes are available yet.</p>
                )}

                {classes && classes.length > 0 && (
                  <ClassPicker
                    classes={classes}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    profile={null}
                  />
                )}

                <div className="scl-actions">
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={savingClass}
                    onClick={() => setChanging(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={
                      savingClass || !selectedId || selectedId === cls?.id
                    }
                    onClick={saveClass}
                  >
                    {savingClass ? "Saving…" : "Save class"}
                  </button>
                </div>
              </section>
            )}

            <section className="scl-card" aria-label="Timetable">
              <h2>
                {source === "legacy" ? "Your earlier timetable" : "Weekly timetable"}
              </h2>

              <OfficialTimetableView
                timetable={data.timetable}
                emptyMessage={
                  source === "official"
                    ? data.message ||
                      "The official timetable for your class has not been published yet."
                    : "Choose your class to see your official timetable."
                }
              />
            </section>

            <section className="scl-card scl-prefs" aria-label="Availability preferences">
              <h2>Availability preferences</h2>
              <p className="scl-muted">
                Personal settings that help us pick events at times that suit
                you.
              </p>

              <AvailabilityPreferences
                preferences={prefs}
                onChange={setPrefs}
                onImportClick={() => {}}
              />

              <div className="scl-actions">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={savingPrefs}
                  onClick={savePrefs}
                >
                  {savingPrefs ? "Saving…" : "Save preferences"}
                </button>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

// ============================================================
// DASHBOARD NOTICE (only for students without an official class)
// ============================================================

const PROMPT_KEY = "campusPathClassPromptHidden";

export function ClassPrompt({ meta, onOpen }) {
  const [hidden, setHidden] = useState(
    () => sessionStorage.getItem(PROMPT_KEY) === "1"
  );

  if (!meta || hidden) return null;

  const missing = meta.source === "legacy" || meta.source === "none";
  const inactive = meta.source === "official" && meta.class_active === false;

  if (!missing && !inactive) return null;

  function dismiss() {
    sessionStorage.setItem(PROMPT_KEY, "1");
    setHidden(true);
  }

  return (
    <div className="scl-prompt" role="status">
      <span>
        {inactive
          ? "Your class was deactivated by the Admin. Your timetable is unchanged, but please choose your current class."
          : "Choose your official class to get your university timetable and better event recommendations."}
      </span>

      <button type="button" className="scl-prompt-btn" onClick={onOpen}>
        {inactive ? "Review class" : "Choose class"}
      </button>

      <button
        type="button"
        className="scl-prompt-close"
        aria-label="Dismiss"
        onClick={dismiss}
      >
        ✕
      </button>
    </div>
  );
}