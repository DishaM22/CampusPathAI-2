import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAcademicFilters,
  fetchAcademicStudents,
  previewPromotion,
  applyPromotion,
  fetchPromotionHistory,
  correctPromotion,
  syncProfileYears,
} from "../../academicService";
import "./AcademicProgression.css";

const EMPTY_FILTERS = {
  department: "",
  academic_year: "",
  year_of_study: "",
  semester: "",
  class_id: "",
  q: "",
};

// Suggest the next semester from the semester the Admin filtered on.
function suggestTarget(sem, academicYear) {
  const s = Number(sem);
  if (!s || s >= 8) return null;
  const next = s + 1;
  let ay = academicYear || "";
  if (s % 2 === 0 && /^\d{4}-\d{4}$/.test(ay)) {
    const [a, b] = ay.split("-").map(Number);
    ay = `${a + 1}-${b + 1}`; // even -> odd semester starts a new academic year
  }
  return { academic_year: ay, year_of_study: String(Math.ceil(next / 2)), semester: String(next) };
}

const fmtDate = (v) => (v ? new Date(v).toLocaleString() : "—");

export default function AcademicProgression() {
  const [tab, setTab] = useState("promote");
  const [options, setOptions] = useState({ departments: [], academic_years: [], classes: [] });

  // ---- promote tab
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [students, setStudents] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState([]);
  const [target, setTarget] = useState({ academic_year: "", year_of_study: "", semester: "" });
  const [targetClassId, setTargetClassId] = useState("");

  const [confirm, setConfirm] = useState(null); // { preview, busy, error, result }
  const requestId = useRef("");

  // ---- history tab
  const [batches, setBatches] = useState([]);
  const [batchRows, setBatchRows] = useState(null); // { batch, rows }
  const [histError, setHistError] = useState("");
  const [histLoading, setHistLoading] = useState(false);
  const [correcting, setCorrecting] = useState(null);

  // ---- profile year repair
  const [sync, setSync] = useState(null); // { busy, error, report, done }

  async function checkProfileYears() {
    setSync({ busy: true, error: "", report: null, done: false });
    try {
      const report = await syncProfileYears({ dry_run: true, include_class_derived: true });
      setSync({ busy: false, error: "", report, done: false });
    } catch (e) {
      setSync({ busy: false, error: e.message || "Could not check.", report: null, done: false });
    }
  }

  async function applyProfileYears() {
    setSync((c) => ({ ...c, busy: true, error: "" }));
    try {
      const report = await syncProfileYears({ dry_run: false, include_class_derived: true });
      setSync({ busy: false, error: "", report, done: true });
    } catch (e) {
      setSync((c) => ({ ...c, busy: false, error: e.message || "Could not update." }));
    }
  }

  const classById = useMemo(
    () => Object.fromEntries((options.classes || []).map((c) => [c.id, c.name])),
    [options.classes]
  );

  useEffect(() => {
    fetchAcademicFilters()
      .then(setOptions)
      .catch((e) => setError(e.message || "Could not load filters."));
    loadStudents(EMPTY_FILTERS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadStudents(f = filters) {
    setLoading(true);
    setError("");
    try {
      const data = await fetchAcademicStudents({
        ...f,
        year_of_study: f.year_of_study || undefined,
        semester: f.semester || undefined,
      });
      setStudents(data.students || []);
      setTotal(data.total_matching || 0);
      setSelected([]);
    } catch (e) {
      setError(e.message || "Could not load students.");
    } finally {
      setLoading(false);
    }
  }

  function changeFilter(patch) {
    const next = { ...filters, ...patch };
    setFilters(next);
    if ("semester" in patch || "academic_year" in patch) {
      const t = suggestTarget(next.semester, next.academic_year);
      if (t) setTarget(t);
    }
  }

  const eligibleRows = students; // the server already applied the filters
  const allSelected = eligibleRows.length > 0 && eligibleRows.every((s) => selected.includes(s.id));
  const toggleOne = (id) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const selectedRows = students.filter((s) => selected.includes(s.id));
  const sourceSemesters = [...new Set(selectedRows.map((s) => s.semester).filter(Boolean))];
  const targetBody = {
    academic_year: target.academic_year.trim(),
    year_of_study: Number(target.year_of_study) || null,
    semester: Number(target.semester) || null,
  };
  const targetReady = targetBody.academic_year && targetBody.year_of_study && targetBody.semester;

  const summaryText = selected.length
    ? `${selected.length} student${selected.length === 1 ? "" : "s"} selected for promotion ` +
      (sourceSemesters.length === 1
        ? `from Semester ${sourceSemesters[0]} to Semester ${target.semester || "?"}.`
        : `to Semester ${target.semester || "?"} (from semesters: ${
            sourceSemesters.join(", ") || "unknown"
          }).`)
    : "No students selected.";

  const matchingTargetClasses = (options.classes || []).filter(
    (c) =>
      c.academic_year === targetBody.academic_year &&
      c.year_of_study === targetBody.year_of_study &&
      c.semester === targetBody.semester
  );

  function requestBody() {
    return {
      student_ids: selected,
      target: targetBody,
      target_class_id: targetClassId || null,
      expected_semester: sourceSemesters.length === 1 ? sourceSemesters[0] : null,
    };
  }

  async function openConfirm() {
    requestId.current =
      (typeof crypto !== "undefined" && crypto.randomUUID && crypto.randomUUID()) ||
      `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setConfirm({ preview: null, busy: true, error: "", result: null });
    try {
      const preview = await previewPromotion(requestBody());
      setConfirm({ preview, busy: false, error: "", result: null });
    } catch (e) {
      setConfirm({ preview: null, busy: false, error: e.message || "Preview failed.", result: null });
    }
  }

  async function runPromotion() {
    setConfirm((c) => ({ ...c, busy: true, error: "" }));
    try {
      // The same request id is reused if this is retried, so it can never promote twice.
      const result = await applyPromotion({ ...requestBody(), request_id: requestId.current });
      setConfirm((c) => ({ ...c, busy: false, result }));
      loadStudents(filters);
    } catch (e) {
      setConfirm((c) => ({ ...c, busy: false, error: e.message || "Promotion failed." }));
    }
  }

  // ---------------- history
  async function loadBatches() {
    setHistLoading(true);
    setHistError("");
    try {
      const data = await fetchPromotionHistory();
      setBatches(data.batches || []);
    } catch (e) {
      setHistError(e.message || "Could not load history.");
    } finally {
      setHistLoading(false);
    }
  }

  async function openBatch(batch) {
    setHistError("");
    try {
      const data = await fetchPromotionHistory({ batch_id: batch.batch_id, limit: 500 });
      setBatchRows({ batch, rows: data.rows || [] });
    } catch (e) {
      setHistError(e.message || "Could not load this batch.");
    }
  }

  useEffect(() => {
    if (tab === "history") loadBatches();
  }, [tab]);

  const describeState = (s) =>
    s
      ? `Sem ${s.semester ?? "—"} · ${s.academic_year || "—"}${
          s.class_id ? ` · ${classById[s.class_id] || "class"}` : ""
        }`
      : "—";

  return (
    <>
      <div className="section-head">
        <h2>🎓 Academic Progression Management</h2>
      </div>

      <div className="acp-tabs">
        <button className={`acp-tab ${tab === "promote" ? "is-active" : ""}`} onClick={() => setTab("promote")}>
          Promote students
        </button>
        <button className={`acp-tab ${tab === "history" ? "is-active" : ""}`} onClick={() => setTab("history")}>
          Promotion history
        </button>
      </div>

      {tab === "promote" && (
        <>
          <div className="acp-card">
            <h3>1. Find students</h3>
            <div className="acp-grid">
              <select className="field" value={filters.department}
                onChange={(e) => changeFilter({ department: e.target.value })}>
                <option value="">All departments</option>
                {(options.departments || []).map((d) => <option key={d}>{d}</option>)}
              </select>
              <select className="field" value={filters.academic_year}
                onChange={(e) => changeFilter({ academic_year: e.target.value })}>
                <option value="">All academic years</option>
                {(options.academic_years || []).map((y) => <option key={y}>{y}</option>)}
              </select>
              <select className="field" value={filters.year_of_study}
                onChange={(e) => changeFilter({ year_of_study: e.target.value })}>
                <option value="">All years</option>
                {[1, 2, 3, 4].map((y) => <option key={y} value={y}>Year {y}</option>)}
              </select>
              <select className="field" value={filters.semester}
                onChange={(e) => changeFilter({ semester: e.target.value })}>
                <option value="">All semesters</option>
                {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => <option key={s} value={s}>Semester {s}</option>)}
              </select>
              <select className="field" value={filters.class_id}
                onChange={(e) => changeFilter({ class_id: e.target.value })}>
                <option value="">All classes / sections</option>
                {(options.classes || [])
                  .filter((c) => !filters.department || c.department === filters.department)
                  .map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <input className="field" placeholder="Search name or email…" value={filters.q}
                onChange={(e) => changeFilter({ q: e.target.value })} />
            </div>
            <div className="acp-row">
              <button className="btn-primary" onClick={() => loadStudents(filters)} disabled={loading}>
                {loading ? "Loading…" : "Show students"}
              </button>
              <button className="btn-ghost" onClick={() => { setFilters(EMPTY_FILTERS); loadStudents(EMPTY_FILTERS); }}>
                Reset
              </button>
              <span className="acp-note">
                {total} matching{total > students.length ? ` (showing first ${students.length}; narrow the filters)` : ""}
              </span>
            </div>
            {error && <p className="acp-error">{error}</p>}
          </div>

          <div className="acp-card">
            <h3>2. Choose who to promote</h3>
            <div className="acp-row">
              <button className="btn-ghost small" onClick={() => setSelected(students.filter((s) => s.has_academic && !s.disabled).map((s) => s.id))}>
                Select all matching
              </button>
              <button className="btn-ghost small" onClick={() => setSelected([])}>Deselect all</button>
              <span className="acp-note">Untick students with backlogs or academic delays.</span>
            </div>
            {students.length === 0 ? (
              <div className="empty-state"><h3>No students match</h3></div>
            ) : (
              <div className="table-wrap acp-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>
                        <input type="checkbox" checked={allSelected} aria-label="Select all"
                          onChange={(e) => setSelected(e.target.checked ? students.filter((s) => s.has_academic && !s.disabled).map((s) => s.id) : [])} />
                      </th>
                      <th>Student</th><th>Department</th><th>Class</th><th>Academic year</th><th>Year</th><th>Sem</th>
                    </tr>
                  </thead>
                  <tbody>
                    {students.map((s) => (
                      <tr key={s.id}>
                        <td>
                          <input type="checkbox" checked={selected.includes(s.id)}
                            disabled={!s.has_academic || s.disabled} onChange={() => toggleOne(s.id)}
                            title={!s.has_academic ? "No class yet" : s.disabled ? "Account disabled" : ""} />
                        </td>
                        <td><b>{s.name}</b><br /><small style={{ color: "var(--text-faint)" }}>{s.email_masked}</small></td>
                        <td>{s.department}</td>
                        <td>{s.class_name || <em>none</em>}</td>
                        <td>{s.academic_year || "—"}</td>
                        <td>{s.year_of_study ?? "—"}</td>
                        <td>{s.semester ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="acp-card">
            <h3>3. Promote to</h3>
            <div className="acp-grid">
              <input className="field" placeholder="Academic year (2027-2028)" value={target.academic_year}
                onChange={(e) => setTarget({ ...target, academic_year: e.target.value })} />
              <select className="field" value={target.year_of_study}
                onChange={(e) => setTarget({ ...target, year_of_study: e.target.value })}>
                <option value="">Target year</option>
                {[1, 2, 3, 4].map((y) => <option key={y} value={y}>Year {y}</option>)}
              </select>
              <select className="field" value={target.semester}
                onChange={(e) => setTarget({ ...target, semester: e.target.value })}>
                <option value="">Target semester</option>
                {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => <option key={s} value={s}>Semester {s}</option>)}
              </select>
            </div>
            <select className="field" value={targetClassId} onChange={(e) => setTargetClassId(e.target.value)}>
              <option value="">Keep each student's current class and timetable</option>
              {matchingTargetClasses.map((c) => (
                <option key={c.id} value={c.id}>Move to: {c.name}</option>
              ))}
            </select>
            <p className="acp-note">
              The class (and so the timetable) only changes if you pick a target class here. A class
              belongs to one department, so promote one department at a time when you choose a class.
            </p>
            <div className="acp-summary">{summaryText}</div>
            <div>
              <button className="btn-primary" disabled={!selected.length || !targetReady} onClick={openConfirm}>
                Promote Selected Students
              </button>
            </div>
          </div>

          <div className="acp-card">
            <h3>Profile years</h3>
            <p className="acp-note">
              A student's profile “Year” now follows the official record automatically after every
              promotion. Use this once to repair students who were promoted before that, so their
              profile shows the right year.
            </p>
            <div className="acp-row">
              <button className="btn-ghost" onClick={checkProfileYears} disabled={sync?.busy}>
                {sync?.busy && !sync.report ? "Checking…" : "Check profile years"}
              </button>
              {sync?.report && !sync.done && sync.report.to_change > 0 && (
                <button className="btn-primary" onClick={applyProfileYears} disabled={sync.busy}>
                  {sync.busy ? "Updating…" : `Fix ${sync.report.to_change} profile(s)`}
                </button>
              )}
            </div>
            {sync?.report && (
              <p className="acp-note">
                {sync.done
                  ? `Updated ${sync.report.changed} profile(s).`
                  : sync.report.to_change === 0
                  ? "All checked profiles already show the correct year."
                  : `${sync.report.to_change} profile(s) show a different year than the official record:`}
              </p>
            )}
            {sync?.report && !sync.done && sync.report.sample.length > 0 && (
              <ul className="acp-list">
                {sync.report.sample.map((c) => (
                  <li key={c.student_id}>{c.name}: {c.old || "(empty)"} → {c.new}</li>
                ))}
              </ul>
            )}
            {sync?.error && <p className="acp-error">{sync.error}</p>}
          </div>
        </>
      )}

      {tab === "history" && (
        <>
          {histError && <div className="sample-banner error-banner">{histError}</div>}
          {histLoading ? (
            <div className="empty-state"><h3>Loading…</h3></div>
          ) : batches.length === 0 ? (
            <div className="empty-state"><h3>No promotions yet</h3></div>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr><th>Date</th><th>By</th><th>Target</th><th>Promoted</th><th>Skipped</th><th></th></tr>
                </thead>
                <tbody>
                  {batches.map((b) => (
                    <tr key={b.batch_id}>
                      <td>{fmtDate(b.created_at)}</td>
                      <td>{b.admin_name || "Admin"}</td>
                      <td>Sem {b.target?.semester} · {b.target?.academic_year}</td>
                      <td>{b.promoted}</td>
                      <td>{b.failed}</td>
                      <td><button className="btn-ghost small" onClick={() => openBatch(b)}>View</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* ---------- confirm / result dialog ---------- */}
      {confirm && (
        <div className="modal-overlay" onClick={() => !confirm.busy && setConfirm(null)}>
          <div className="modal-panel acp-modal-wide" onClick={(e) => e.stopPropagation()}>
            {confirm.result ? (
              <>
                <h2>{confirm.result.duplicate ? "Already processed" : "Promotion complete"}</h2>
                <p className="modal-message">
                  {confirm.result.promoted} promoted, {confirm.result.failed} skipped.{" "}
                  {confirm.result.eligibility_gained} event access gained and{" "}
                  {confirm.result.eligibility_lost} lost because of class changes.
                </p>
                {confirm.result.failures?.length > 0 && (
                  <ul className="acp-list">
                    {confirm.result.failures.map((f, i) => (
                      <li key={i}>{f.name || f.student_id}: {f.reason}</li>
                    ))}
                  </ul>
                )}
                <div className="modal-actions-right">
                  <button className="btn-primary" onClick={() => setConfirm(null)}>Close</button>
                </div>
              </>
            ) : (
              <>
                <h2>Confirm promotion</h2>
                <p className="modal-message">{summaryText}</p>
                {confirm.busy && !confirm.preview && <p className="acp-note">Checking students…</p>}
                {confirm.preview && (
                  <>
                    <p className="modal-message">
                      <b>{confirm.preview.will_promote}</b> will be promoted to Semester {targetBody.semester}
                      {targetClassId ? ` and moved to ${classById[targetClassId] || "the chosen class"}` : " (class unchanged)"}.
                      {confirm.preview.will_skip > 0 && <> <b>{confirm.preview.will_skip}</b> will be skipped:</>}
                    </p>
                    {confirm.preview.skipped.length > 0 && (
                      <ul className="acp-list">
                        {confirm.preview.skipped.map((f, i) => (
                          <li key={i}>{f.name || f.student_id}: {f.reason}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
                {confirm.error && <p className="acp-error">{confirm.error}</p>}
                <div className="modal-actions-right">
                  <button className="btn-ghost" disabled={confirm.busy} onClick={() => setConfirm(null)}>Cancel</button>
                  <button className="btn-primary" disabled={confirm.busy || !confirm.preview || confirm.preview.will_promote === 0}
                    onClick={runPromotion}>
                    {confirm.busy ? "Working…" : "Confirm promotion"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ---------- batch detail ---------- */}
      {batchRows && (
        <div className="modal-overlay" onClick={() => setBatchRows(null)}>
          <div className="modal-panel acp-modal-wide" onClick={(e) => e.stopPropagation()}>
            <h2>Promotion batch · {fmtDate(batchRows.batch.created_at)}</h2>
            <p className="acp-note">Batch reference: {batchRows.batch.batch_id}</p>
            <div className="table-wrap acp-scroll">
              <table className="data-table">
                <thead><tr><th>Student</th><th>Before</th><th>After</th><th>Type</th><th></th></tr></thead>
                <tbody>
                  {batchRows.rows.map((r) => (
                    <tr key={r.id}>
                      <td>{r.student_name || r.student_id}</td>
                      <td>{describeState(r.previous)}</td>
                      <td>{describeState(r.new)}{r.reason ? <><br /><small>{r.reason}</small></> : null}</td>
                      <td>{r.kind === "correction" ? "Correction" : r.corrected_by ? "Corrected" : "Promotion"}</td>
                      <td>
                        {r.kind === "promotion" && !r.corrected_by && (
                          <button className="btn-ghost small" onClick={() => setCorrecting({ row: r, reason: "", busy: false, error: "" })}>
                            Correct
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="modal-actions-right">
              <button className="btn-primary" onClick={() => setBatchRows(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* ---------- correction dialog ---------- */}
      {correcting && (
        <div className="modal-overlay" onClick={() => !correcting.busy && setCorrecting(null)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <h2>Correct promotion</h2>
            <p className="modal-message">
              {correcting.row.student_name}: {describeState(correcting.row.previous)} → {describeState(correcting.row.new)}.
              This puts the student back to the “before” state. The original record is kept.
            </p>
            <label htmlFor="acp-reason">Reason (required)</label>
            <textarea id="acp-reason" className="field acp-textarea" value={correcting.reason}
              onChange={(e) => setCorrecting({ ...correcting, reason: e.target.value })} />
            {correcting.error && <p className="acp-error">{correcting.error}</p>}
            <div className="modal-actions-right">
              <button className="btn-ghost" disabled={correcting.busy} onClick={() => setCorrecting(null)}>Cancel</button>
              <button className="btn-primary" disabled={correcting.busy || correcting.reason.trim().length < 5}
                onClick={async () => {
                  setCorrecting((c) => ({ ...c, busy: true, error: "" }));
                  try {
                    await correctPromotion({ history_id: correcting.row.id, reason: correcting.reason.trim() });
                    const batch = batchRows.batch;
                    setCorrecting(null);
                    await openBatch(batch);
                  } catch (e) {
                    setCorrecting((c) => ({ ...c, busy: false, error: e.message || "Correction failed." }));
                  }
                }}>
                {correcting.busy ? "Saving…" : "Apply correction"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}