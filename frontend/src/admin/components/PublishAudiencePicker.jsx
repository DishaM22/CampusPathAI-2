import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAudienceOptions,
  fetchAudienceClasses,
  searchAudienceStudents,
  previewAudience,
} from "../../audienceService";
import "./PublishAudiencePicker.css";

export const EMPTY_AUDIENCE = {
  type: "",
  departments: [],
  class_ids: [],
  student_ids: [],
};

// Returns "" when the choice is complete, otherwise a message to show.
export function audienceError(v) {
  if (!v || !v.type) return "Choose who this event is published to.";
  if (v.type === "departments" && v.departments.length === 0)
    return "Select at least one department.";
  if (v.type === "classes" && v.class_ids.length === 0)
    return "Select at least one class.";
  if (v.type === "students" && v.student_ids.length === 0)
    return "Select at least one student.";
  return "";
}

const TYPES = [
  { key: "all", title: "All students", hint: "Every active student in the university." },
  { key: "departments", title: "Particular department", hint: "One or more departments." },
  { key: "classes", title: "Particular class", hint: "Pick by department, year, semester and section." },
  { key: "students", title: "Particular students", hint: "Search and pick individual students." },
];

const toggle = (list, id) =>
  list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

/**
 * value:    { type, departments, class_ids, student_ids }
 * onChange: (newValue) => void
 * initialClasses / initialStudents: names for an audience that already exists
 */
export default function PublishAudiencePicker({
  value,
  onChange,
  disabled = false,
  initialClasses = [],
  initialStudents = [],
}) {
  const [options, setOptions] = useState({ departments: [] });
  const [loadError, setLoadError] = useState("");

  // class picker
  const [dept, setDept] = useState("");
  const [year, setYear] = useState("");
  const [acYear, setAcYear] = useState("");
  const [sem, setSem] = useState("");
  const [deptClasses, setDeptClasses] = useState([]);
  const [classNames, setClassNames] = useState(() =>
    Object.fromEntries(initialClasses.map((c) => [c.id, c.name]))
  );

  // student picker
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [studentInfo, setStudentInfo] = useState(() =>
    Object.fromEntries(initialStudents.map((s) => [s.id, s]))
  );
  const searchSeq = useRef(0);

  // preview
  const [preview, setPreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const set = (patch) => {
    setPreview(null);
    onChange({ ...value, ...patch });
  };

  useEffect(() => {
    fetchAudienceOptions()
      .then(setOptions)
      .catch((e) => setLoadError(e.message || "Could not load departments."));
  }, []);

  // classes for the chosen department (the other filters are applied below)
  useEffect(() => {
    if (value.type !== "classes") return;
    let live = true;
    fetchAudienceClasses({ department: dept })
      .then((d) => {
        if (!live) return;
        setDeptClasses(d.classes || []);
        setClassNames((m) => ({
          ...m,
          ...Object.fromEntries((d.classes || []).map((c) => [c.id, c.name])),
        }));
      })
      .catch((e) => live && setLoadError(e.message));
    return () => {
      live = false;
    };
  }, [value.type, dept]);

  const acYears = useMemo(
    () => [...new Set(deptClasses.map((c) => c.academic_year).filter(Boolean))].sort(),
    [deptClasses]
  );
  const afterAc = deptClasses.filter((c) => !acYear || c.academic_year === acYear);
  const years = [...new Set(afterAc.map((c) => c.year_of_study).filter(Boolean))].sort();
  const afterYear = afterAc.filter((c) => !year || String(c.year_of_study) === year);
  const sems = [...new Set(afterYear.map((c) => c.semester).filter(Boolean))].sort();
  const matching = afterYear.filter((c) => !sem || String(c.semester) === sem);

  // debounced student search
  useEffect(() => {
    if (value.type !== "students") return;
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const seq = ++searchSeq.current;
    setSearching(true);
    const t = setTimeout(() => {
      searchAudienceStudents(q)
        .then((d) => {
          if (seq !== searchSeq.current) return;
          setResults(d.students || []);
          setStudentInfo((m) => ({
            ...m,
            ...Object.fromEntries((d.students || []).map((s) => [s.id, s])),
          }));
        })
        .catch((e) => seq === searchSeq.current && setLoadError(e.message))
        .finally(() => seq === searchSeq.current && setSearching(false));
    }, 300);
    return () => clearTimeout(t);
  }, [query, value.type]);

  async function runPreview() {
    const problem = audienceError(value);
    if (problem) {
      setPreviewError(problem);
      return;
    }
    setPreviewBusy(true);
    setPreviewError("");
    try {
      setPreview(await previewAudience(value));
    } catch (e) {
      setPreviewError(e.message || "Could not preview.");
    } finally {
      setPreviewBusy(false);
    }
  }

  const resultIds = results.map((s) => s.id);
  const allResultsSelected =
    resultIds.length > 0 && resultIds.every((id) => value.student_ids.includes(id));

  return (
    <div className="pap" aria-disabled={disabled}>
      <div className="pap-types" role="radiogroup" aria-label="Publish event to">
        {TYPES.map((t) => (
          <label
            key={t.key}
            className={`pap-type ${value.type === t.key ? "is-active" : ""}`}
          >
            <input
              type="radio"
              name="pap-type"
              checked={value.type === t.key}
              disabled={disabled}
              onChange={() => set({ ...EMPTY_AUDIENCE, type: t.key })}
            />
            <span>
              <b>{t.title}</b>
              <small>{t.hint}</small>
            </span>
          </label>
        ))}
      </div>

      {loadError && <p className="pap-error">{loadError}</p>}

      {value.type === "all" && (
        <p className="pap-note">
          Every active student can see, register for and attend this event. No department
          or class selection is needed.
        </p>
      )}

      {value.type === "departments" && (
        <div className="pap-box">
          {(options.departments || []).map((d) => (
            <label key={d} className="pap-check">
              <input
                type="checkbox"
                disabled={disabled}
                checked={value.departments.includes(d)}
                onChange={() => set({ departments: toggle(value.departments, d) })}
              />
              {d}
            </label>
          ))}
        </div>
      )}

      {value.type === "classes" && (
        <div className="pap-box">
          <div className="pap-filters">
            <select
              className="field"
              value={dept}
              disabled={disabled}
              onChange={(e) => {
                setDept(e.target.value);
                setAcYear("");
                setYear("");
                setSem("");
              }}
            >
              <option value="">1. Department</option>
              {(options.departments || []).map((d) => (
                <option key={d}>{d}</option>
              ))}
            </select>
            <select
              className="field"
              value={acYear}
              disabled={disabled || !dept}
              onChange={(e) => {
                setAcYear(e.target.value);
                setYear("");
                setSem("");
              }}
            >
              <option value="">2. Academic year</option>
              {acYears.map((y) => (
                <option key={y}>{y}</option>
              ))}
            </select>
            <select
              className="field"
              value={year}
              disabled={disabled || !dept}
              onChange={(e) => {
                setYear(e.target.value);
                setSem("");
              }}
            >
              <option value="">Year of study</option>
              {years.map((y) => (
                <option key={y} value={String(y)}>
                  Year {y}
                </option>
              ))}
            </select>
            <select
              className="field"
              value={sem}
              disabled={disabled || !dept}
              onChange={(e) => setSem(e.target.value)}
            >
              <option value="">3. Semester</option>
              {sems.map((s) => (
                <option key={s} value={String(s)}>
                  Semester {s}
                </option>
              ))}
            </select>
          </div>

          {!dept ? (
            <p className="pap-note">Select a department to see its classes.</p>
          ) : matching.length === 0 ? (
            <p className="pap-note">No active classes match these filters.</p>
          ) : (
            <>
              <div className="pap-row">
                <b>{matching.length} matching class(es)</b>
                <span>
                  <button
                    type="button"
                    className="btn-ghost small"
                    disabled={disabled}
                    onClick={() =>
                      set({
                        class_ids: [
                          ...new Set([...value.class_ids, ...matching.map((c) => c.id)]),
                        ],
                      })
                    }
                  >
                    Select all
                  </button>{" "}
                  <button
                    type="button"
                    className="btn-ghost small"
                    disabled={disabled}
                    onClick={() =>
                      set({
                        class_ids: value.class_ids.filter(
                          (id) => !matching.some((c) => c.id === id)
                        ),
                      })
                    }
                  >
                    Deselect
                  </button>
                </span>
              </div>
              <div className="pap-list">
                {matching.map((c) => (
                  <label key={c.id} className="pap-check">
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={value.class_ids.includes(c.id)}
                      onChange={() => set({ class_ids: toggle(value.class_ids, c.id) })}
                    />
                    {c.name}
                  </label>
                ))}
              </div>
            </>
          )}

          {value.class_ids.length > 0 && (
            <div className="pap-chips">
              {value.class_ids.map((id) => (
                <span key={id} className="pap-chip">
                  {classNames[id] || "Class"}
                  <button
                    type="button"
                    aria-label="Remove class"
                    disabled={disabled}
                    onClick={() => set({ class_ids: value.class_ids.filter((x) => x !== id) })}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {value.type === "students" && (
        <div className="pap-box">
          <input
            className="field"
            placeholder="Search by student name or email (min. 2 letters)…"
            value={query}
            disabled={disabled}
            onChange={(e) => setQuery(e.target.value)}
          />
          {searching && <p className="pap-note">Searching…</p>}
          {!searching && query.trim().length >= 2 && results.length === 0 && (
            <p className="pap-note">No students found.</p>
          )}
          {results.length > 0 && (
            <>
              <div className="pap-row">
                <b>{results.length} result(s)</b>
                <span>
                  <button
                    type="button"
                    className="btn-ghost small"
                    disabled={disabled || allResultsSelected}
                    onClick={() =>
                      set({ student_ids: [...new Set([...value.student_ids, ...resultIds])] })
                    }
                  >
                    Select all results
                  </button>{" "}
                  <button
                    type="button"
                    className="btn-ghost small"
                    disabled={disabled}
                    onClick={() =>
                      set({ student_ids: value.student_ids.filter((id) => !resultIds.includes(id)) })
                    }
                  >
                    Deselect results
                  </button>
                </span>
              </div>
              <div className="pap-list">
                {results.map((s) => (
                  <label key={s.id} className="pap-check">
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={value.student_ids.includes(s.id)}
                      onChange={() => set({ student_ids: toggle(value.student_ids, s.id) })}
                    />
                    <span>
                      {s.name}
                      <small>
                        {" "}
                        {s.department}
                        {s.class_name ? ` · ${s.class_name}` : ""} · {s.email_masked}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
            </>
          )}
          {value.student_ids.length > 0 && (
            <div className="pap-chips">
              <b>{value.student_ids.length} selected:</b>
              {value.student_ids.slice(0, 40).map((id) => (
                <span key={id} className="pap-chip">
                  {studentInfo[id]?.name || "Student"}
                  <button
                    type="button"
                    aria-label="Remove student"
                    disabled={disabled}
                    onClick={() =>
                      set({ student_ids: value.student_ids.filter((x) => x !== id) })
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
              {value.student_ids.length > 40 && (
                <span className="pap-note">+{value.student_ids.length - 40} more</span>
              )}
            </div>
          )}
        </div>
      )}

      {value.type && (
        <div className="pap-preview">
          <button
            type="button"
            className="btn-ghost small"
            disabled={disabled || previewBusy}
            onClick={runPreview}
          >
            {previewBusy ? "Checking…" : "Check how many students this reaches"}
          </button>
          {preview && (
            <span className="pap-note">
              {preview.eligible_students} of {preview.total_students} active students will
              have access.
            </span>
          )}
          {previewError && <span className="pap-error">{previewError}</span>}
        </div>
      )}
    </div>
  );
}