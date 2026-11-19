import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  useLocation,
  useNavigate,
  useParams
} from "react-router-dom";
import "../club-modules.css";
import { DEPARTMENTS, matchDepartment } from "../departments";
import { ScanAttendance } from "./ClubAttendanceScanner";
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

import { useTheme } from "../context/ThemeContext";
const API_BASE = "http://127.0.0.1:8000";


// ============================================================
// HELPERS
// ============================================================

function getToken() {
  return (
    localStorage.getItem("token") ||
    localStorage.getItem("access_token") ||
    localStorage.getItem("jwt") ||
    ""
  );
}

async function apiFetch(url, options = {}) {
  const token = getToken();

  const response = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token
        ? {
            Authorization: `Bearer ${token}`,
          }
        : {}),
      ...(options.headers || {}),
    },
  });

  const text = await response.text();

  let data;

  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {
      message: text,
    };
  }

if (!response.ok) {
  let errorMessage = `Request failed with status ${response.status}`;

  if (Array.isArray(data?.detail)) {
    errorMessage = data.detail
      .map((item) => {
        const field = Array.isArray(item?.loc)
          ? item.loc.filter((x) => x !== "body").join(".")
          : "";

        const message = item?.msg || "Invalid value";

        return field
          ? `${field}: ${message}`
          : message;
      })
      .join(", ");
  } else if (data?.detail) {
    errorMessage = data.detail;
  } else if (data?.message) {
    errorMessage = data.message;
  }

  throw new Error(errorMessage);
}
  return data;
}


// ============================================================
// POSTER + RESOURCE PERSON HELPERS (shared by create / edit / view)
// ============================================================

const MAX_POSTER_BYTES = 3 * 1024 * 1024;
const POSTER_TYPES = ["image/png", "image/jpeg", "image/webp"];

// The backend stores posters as "/uploads/posters/<name>.png".
// The browser needs the full address (or a local blob: preview).
function posterSrc(url) {
  if (!url) return "";

  const text = String(url);

  if (/^(https?:|blob:)/i.test(text)) return text;

  return `${API_BASE.replace(/\/$/, "")}/${text.replace(/^\//, "")}`;
}

// Uploads a poster and returns its stored address.
// (apiFetch can't be used: it forces a JSON Content-Type.)
async function uploadPosterFile(file) {
  const body = new FormData();
  body.append("file", file);

  const token = getToken();

  const response = await fetch(`${API_BASE}/api/events/poster`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  });

  const result = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail =
      typeof result?.detail === "string"
        ? result.detail
        : response.status;

    throw new Error(`Poster upload failed: ${detail}`);
  }

  if (!result.poster_url) {
    throw new Error("Poster upload did not return a URL.");
  }

  return result.poster_url;
}

function usePoster() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [inputKey, setInputKey] = useState(0);

  // Free the temporary blob: address when the preview changes / unmounts
  useEffect(() => {
    return () => {
      if (preview && preview.startsWith("blob:")) {
        URL.revokeObjectURL(preview);
      }
    };
  }, [preview]);

  // Returns true when a valid file was picked
  function choose(changeEvent) {
    const picked =
      changeEvent.target.files && changeEvent.target.files[0];

    // File picker cancelled: keep the current selection
    if (!picked) return false;

    if (!POSTER_TYPES.includes(picked.type)) {
      window.alert("Poster must be a PNG, JPG or WEBP image.");
      changeEvent.target.value = "";
      return false;
    }

    if (picked.size > MAX_POSTER_BYTES) {
      window.alert("Poster is too large (maximum 3 MB).");
      changeEvent.target.value = "";
      return false;
    }

    setFile(picked);
    setPreview(URL.createObjectURL(picked));
    return true;
  }

  // Drop any chosen file and show `fallbackUrl` (the saved poster) instead
  function clear(fallbackUrl = "") {
    setFile(null);
    setPreview(posterSrc(fallbackUrl));
    setInputKey((key) => key + 1);
  }

  return { file, preview, inputKey, choose, clear };
}

function PosterField({ poster, onPick, children }) {
  return (
    <div className="form-group">
      <label>Event Poster (optional)</label>

      <input
        key={poster.inputKey}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        onChange={(e) => {
          if (poster.choose(e) && onPick) onPick();
        }}
      />

      <small className="form-help">
        PNG, JPG or WEBP, up to 3 MB. A wide image (about 1200 x 630)
        looks best.
      </small>

      {poster.preview && (
        <div style={{ marginTop: "10px" }}>
          <img
            src={poster.preview}
            alt="Event poster preview"
            style={{
              display: "block",
              width: "100%",
              maxWidth: "420px",
              maxHeight: "260px",
              objectFit: "contain",
              borderRadius: "12px",
            }}
          />
        </div>
      )}

      {children}
    </div>
  );
}


const emptyResourcePerson = () => ({
  name: "",
  designation: "",
  organization: "",
  linkedin_url: "",
});

// Saved event -> editable rows
function resourcePersonsFromEvent(event) {
  return Array.isArray(event?.resource_persons) &&
    event.resource_persons.length > 0
    ? event.resource_persons.map((person) => ({
        name: person.name || "",
        designation: person.designation || "",
        organization: person.organization || "",
        linkedin_url: person.linkedin_url || "",
      }))
    : [emptyResourcePerson()];
}

// Editable rows -> payload (empty rows dropped)
function cleanResourcePersons(persons) {
  return persons
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
    );
}

function ResourcePersonsField({ persons, setPersons }) {
  function change(index, field, value) {
    setPersons((previous) =>
      previous.map((person, i) =>
        i === index ? { ...person, [field]: value } : person
      )
    );
  }

  const boxStyle = {
    marginTop: "6px",
    padding: "16px",
    border: "1px solid rgba(148, 163, 184, 0.25)",
    borderRadius: "12px",
  };

  return (
    <div className="form-group">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <div>
          <label style={{ marginBottom: "2px" }}>
            Resource Person(s)
          </label>
          <small className="form-help">
            Add speakers, mentors, judges, or other event experts.
          </small>
        </div>

        <button
          type="button"
          className="btn-ghost"
          onClick={() =>
            setPersons((previous) => [
              ...previous,
              emptyResourcePerson(),
            ])
          }
        >
          + Add Resource Person
        </button>
      </div>

      {persons.map((person, index) => {
        const otherFilled = Boolean(
          person.organization.trim() || person.linkedin_url.trim()
        );
        const nameRequired = Boolean(
          person.designation.trim() || otherFilled
        );
        const designationRequired = Boolean(
          person.name.trim() || otherFilled
        );

        return (
          <div key={index} style={boxStyle}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "10px",
              }}
            >
              <strong>Resource Person {index + 1}</strong>

              {persons.length > 1 && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() =>
                    setPersons((previous) =>
                      previous.filter((_, i) => i !== index)
                    )
                  }
                >
                  Remove
                </button>
              )}
            </div>

            <div className="form-grid">
              <div className="form-group">
                <label>Full Name</label>
                <input
                  type="text"
                  value={person.name}
                  onChange={(e) =>
                    change(index, "name", e.target.value)
                  }
                  placeholder="Enter full name"
                  required={nameRequired}
                />
              </div>

              <div className="form-group">
                <label>Designation</label>
                <input
                  type="text"
                  value={person.designation}
                  onChange={(e) =>
                    change(index, "designation", e.target.value)
                  }
                  placeholder="e.g. AI Research Engineer"
                  required={designationRequired}
                />
              </div>

              <div className="form-group">
                <label>Organization / Company</label>
                <input
                  type="text"
                  value={person.organization}
                  onChange={(e) =>
                    change(index, "organization", e.target.value)
                  }
                  placeholder="Enter organization"
                />
              </div>

              <div className="form-group">
                <label>LinkedIn Profile URL</label>
                <input
                  type="url"
                  value={person.linkedin_url}
                  onChange={(e) =>
                    change(index, "linkedin_url", e.target.value)
                  }
                  placeholder="https://www.linkedin.com/in/username"
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ResourcePersonsView({ persons }) {
  if (!Array.isArray(persons) || persons.length === 0) return null;

  return (
    <div className="event-skills">
      <h3>Resource Person(s)</h3>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
          gap: "12px",
          marginTop: "10px",
        }}
      >
        {persons.map((person, index) => (
          <div
            key={index}
            style={{
              padding: "14px 16px",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              borderRadius: "12px",
              background: "rgba(148, 163, 184, 0.06)",
            }}
          >
            <strong style={{ display: "block" }}>{person.name}</strong>

            {person.designation && (
              <div style={{ fontSize: "14px", opacity: 0.85 }}>
                {person.designation}
              </div>
            )}

            {person.organization && (
              <div style={{ fontSize: "13px", opacity: 0.7 }}>
                {person.organization}
              </div>
            )}

            {/^https?:\/\//i.test(person.linkedin_url || "") && (
              <a
                href={person.linkedin_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-block",
                  marginTop: "8px",
                  fontSize: "13px",
                }}
              >
                LinkedIn profile ↗
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}



// ============================================================
// COMMON PAGE WRAPPER
// ============================================================

function Page({ title, subtitle, children }) {
  return (
    <div className="club-module-page">
      <div className="club-module-header">
        <div>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
      </div>

      {children}
    </div>
  );
}


// ============================================================
// COMMON CARD
// ============================================================

function Card({ children, className = "" }) {
  return (
    <div className={`club-card ${className}`}>
      {children}
    </div>
  );
}


// ============================================================
// LOADING
// ============================================================

function Loading() {
  return (
    <div className="club-loading">
      Loading...
    </div>
  );
}


// ============================================================
// ERROR
// ============================================================

function ErrorBox({ message }) {
  if (!message) return null;

  return (
    <div className="club-error">
      {message}
    </div>
  );
}


// ============================================================
// MY CLUB
// ============================================================

export function MyClub() {
  const [club, setClub] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadClub();
  }, []);

  async function loadClub() {
    try {
      setLoading(true);
      setError("");

      const data = await apiFetch("/api/club/dashboard");

      setClub(data.club || {});
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <Page
        title="My Club"
        subtitle="View and manage your club information."
      >
        <Loading />
      </Page>
    );
  }

  return (
    <Page
      title="My Club"
      subtitle="View and manage your club information."
    >
      <ErrorBox message={error} />

      <div className="club-profile-grid">

        <Card>
          <div className="club-big-avatar">
            {(club?.name || "C").charAt(0).toUpperCase()}
          </div>

          <h2>{club?.name || "Beta Club"}</h2>

          <p className="muted">
            Club Leader
          </p>
        </Card>


        <Card>
          <h3>Club Information</h3>

          <div className="info-row">
            <span>Club Name</span>
            <strong>{club?.name || "—"}</strong>
          </div>

          <div className="info-row">
            <span>Category</span>
            <strong>{club?.category || "—"}</strong>
          </div>

          <div className="info-row">
            <span>College</span>
            <strong>{club?.college || "—"}</strong>
          </div>

          <div className="info-row">
            <span>Status</span>
            <strong className="status-active">
              {club?.status || "Active"}
            </strong>
          </div>
        </Card>

      </div>
    </Page>
  );
}


// ============================================================
// CREATE EVENT
// ============================================================

// ============================================================
// CREATE EVENT
// ============================================================

// ============================================================
// ORGANIZING DETAILS + SPOC (shared by create / edit)
// ============================================================

// Category choices for the event forms. Stored as plain text, so adding
// one here needs no backend change.
const EVENT_CATEGORIES = [
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

const EVENT_MODES = ["Online", "Offline", "Hybrid"];

const EMAIL_REGEX = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;

/* 10-digit Indian mobile; +91 / 91 / 0 prefixes, spaces and dashes allowed. */
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
   The backend repeats these checks; this only gives faster feedback. */
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

function EventOrganizingFields({
  form,
  updateField,
  breakup,
  setBreakup,
  legacyTotal,
  registrationRequired,
  setRegistrationRequired,
  registrationDeadline,
  setRegistrationDeadline,
  deadlineLocked,
}) {
  const headingStyle = { margin: "8px 0 0", fontSize: "16px" };

  return (
    <>
      <h3 style={headingStyle}>Organizing Details</h3>

      <div className="form-grid">
        <div className="form-group">
          <label>School Name</label>
          <input
            name="schoolName"
            value={form.schoolName}
            onChange={updateField}
            placeholder="e.g. School of Computer Science"
            maxLength={150}
            required
          />
        </div>

        <div className="form-group">
          <label>Department</label>
          <select
            name="department"
            value={form.department}
            onChange={updateField}
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

      <div className="form-group">
        <label>Mode of Event</label>
        <select
          name="eventMode"
          value={form.eventMode}
          onChange={updateField}
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

      <h3 style={headingStyle}>Single Point of Contact (SPOC)</h3>

      <div className="form-grid">
        <div className="form-group">
          <label>SPOC Name</label>
          <input
            name="spocName"
            value={form.spocName}
            onChange={updateField}
            placeholder="Full name"
            maxLength={150}
            required
          />
        </div>

        <div className="form-group">
          <label>SPOC Designation</label>
          <input
            name="spocDesignation"
            value={form.spocDesignation}
            onChange={updateField}
            placeholder="e.g. Assistant Professor"
            maxLength={150}
            required
          />
        </div>
      </div>

      <div className="form-grid">
        <div className="form-group">
          <label>SPOC Email Address</label>
          <input
            type="email"
            name="spocEmail"
            value={form.spocEmail}
            onChange={updateField}
            placeholder="name@university.edu"
            required
          />
        </div>

        <div className="form-group">
          <label>SPOC Contact Number</label>
          <input
            type="tel"
            name="spocContact"
            value={form.spocContact}
            onChange={updateField}
            placeholder="10-digit mobile number"
            required
          />
        </div>
      </div>

      <RegistrationRequirement
        value={registrationRequired}
        onChange={setRegistrationRequired}
        idPrefix="club-regreq"
        deadline={registrationDeadline}
        onDeadlineChange={setRegistrationDeadline}
        deadlineLocked={deadlineLocked}
      />

      <BudgetBreakupForm
        value={breakup}
        onChange={setBreakup}
        legacyTotal={legacyTotal}
        idPrefix="club-budget"
      />
    </>
  );
}


export function CreateEvent() {
  const navigate = useNavigate();
  const submitting = useRef(false); // blocks double clicks / double submits
  const [form, setForm] = useState({
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
  });

  const [breakup, setBreakup] = useState(emptyBreakup);

  // Default: students register (existing behaviour)
  const [registrationRequired, setRegistrationRequired] = useState(true);
  const [registrationDeadline, setRegistrationDeadline] = useState("");

  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const poster = usePoster();
  const [resourcePersons, setResourcePersons] = useState([
    emptyResourcePerson(),
  ]);
  function updateField(e) {
    setForm({
      ...form,
      [e.target.name]: e.target.value,
    });
  }

  async function submitEvent(e) {
    e.preventDefault();
    if (submitting.current) return;
    submitting.current = true;

    setLoading(true);
    setError("");
    setMessage("");

    const detailsProblem =
      validateEventDetails(form) ||
      validateBreakup(breakup) ||
      (registrationRequired
        ? deadlineProblem(registrationDeadline, form.date, form.startTime)
        : "");
    if (detailsProblem) {
      setError(detailsProblem);
      setLoading(false);
      submitting.current = false;
      return;
    }

    try {
      // Backend expects "location", not "venue".
      // The backend identifies the logged-in club through JWT.

      // Upload the poster first (if one was chosen)
      const posterUrl = poster.file
        ? await uploadPosterFile(poster.file)
        : null;

      const payload = {
  title: form.title.trim(),
  description: form.description.trim(),
  category: form.category,

  // Required by the backend
  organizer: "Beta club",

  date: form.date,
  time: `${form.startTime} - ${form.endTime}`,
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
  // Only events with registration have a deadline (IST); published events
  // change it through "Request deadline change" instead.
  ...(deadlineLocked
    ? {}
    : { registration_deadline: registrationRequired ? registrationDeadline : null }),

  capacity: Number(form.capacity),

  skills: form.skills
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean),

  // Always sent, so removing every person clears the list
resource_persons: cleanResourcePersons(resourcePersons),

  ...(posterUrl ? { poster_url: posterUrl } : {}),
};

      console.log("CREATE EVENT PAYLOAD:", payload);

      const result = await apiFetch("/api/events", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      console.log("CREATE EVENT RESPONSE:", result);

      setMessage("Event created successfully!");

      // Go to the saved event's details page (status, Back / View My Events).
      const newId = result?.event_id || result?.id;
      if (newId) {
        navigate(`/club-dashboard/events/${newId}`, {
          state: { justCreated: true },
        });
        return; // the form unmounts; keep the submit lock until then
      }

      poster.clear("");
      setResourcePersons([emptyResourcePerson()]);

      setForm({
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
      });
      setBreakup(emptyBreakup());
      setRegistrationRequired(true);
      setRegistrationDeadline("");

    } catch (err) {
      console.error("CREATE EVENT ERROR:", err);

      let errorMessage = "Failed to create event.";

      if (Array.isArray(err)) {
        errorMessage = err
          .map((item) => {
            if (typeof item === "string") {
              return item;
            }

            return item?.msg || item?.message || JSON.stringify(item);
          })
          .join(", ");
      } else if (err?.message) {
        errorMessage = err.message;
      } else if (typeof err === "string") {
        errorMessage = err;
      }

      setError(errorMessage);
    } finally {
      setLoading(false);
      submitting.current = false;
    }
  }

  return (
    <Page
      title="Create Event"
      subtitle="Create and publish an event for students."
    >

      <Card>

        <ErrorBox message={error} />

        {message && (
          <div className="club-success">
            {message}
          </div>
        )}

        <form
          className="club-event-form"
          onSubmit={submitEvent}
        >

          {/* EVENT TITLE */}
          <div className="form-group">
            <label>Event Title</label>

            <input
              name="title"
              value={form.title}
              onChange={updateField}
              placeholder="Enter event title"
              required
            />
          </div>


          {/* DESCRIPTION */}
          <div className="form-group">
            <label>Description</label>

            <textarea
              name="description"
              value={form.description}
              onChange={updateField}
              placeholder="Describe your event"
              rows="5"
              required
            />
          </div>


          {/* CATEGORY + CAPACITY */}
          <div className="form-grid">

            <div className="form-group">
              <label>Category</label>

              <select
                name="category"
                value={form.category}
                onChange={updateField}
                required
              >
                <option value="">
                  Select category
                </option>

                {EVENT_CATEGORIES.map((categoryName) => (
                  <option key={categoryName} value={categoryName}>
                    {categoryName}
                  </option>
                ))}
              </select>
            </div>


            <div className="form-group">
              <label>Capacity</label>

              <input
                type="number"
                name="capacity"
                value={form.capacity}
                onChange={updateField}
                placeholder="Maximum participants"
                min="1"
                required
              />
            </div>

          </div>


          {/* DATE */}
          <div className="form-group">
            <label>Date</label>

            <input
              type="date"
              name="date"
              value={form.date}
              onChange={updateField}
              required
            />
          </div>


          {/* START + FINISH TIME */}
          <div className="form-grid">

            <div className="form-group">
              <label>Start Time</label>

              <input
                type="text"
                name="startTime"
                value={form.startTime}
                onChange={updateField}
                placeholder="10:00 AM"
                pattern="^(0?[1-9]|1[0-2]):[0-5][0-9]\s?(AM|PM|am|pm)$"
                title="Enter time like 10:00 AM"
                required
              />
            </div>

            <div className="form-group">
              <label>Finish Time</label>

              <input
                type="text"
                name="endTime"
                value={form.endTime}
                onChange={updateField}
                placeholder="12:00 PM"
                pattern="^(0?[1-9]|1[0-2]):[0-5][0-9]\s?(AM|PM|am|pm)$"
                title="Enter time like 12:00 PM"
                required
              />
            </div>

          </div>


          {/* VENUE */}
          <div className="form-group">
            <label>
              Venue{form.eventMode === "Online" ? " (optional for Online events)" : ""}
            </label>

            <input
              name="venue"
              value={form.venue}
              onChange={updateField}
              placeholder={
                form.eventMode === "Online"
                  ? "Meeting platform or link (optional)"
                  : "Enter venue"
              }
              required={form.eventMode !== "Online"}
            />
          </div>


          {/* ORGANIZING DETAILS + SPOC + BUDGET BREAKUP */}
          <EventOrganizingFields
            form={form}
            updateField={updateField}
            breakup={breakup}
            setBreakup={setBreakup}
            legacyTotal={null}
            registrationRequired={registrationRequired}
            setRegistrationRequired={setRegistrationRequired}
            registrationDeadline={registrationDeadline}
            setRegistrationDeadline={setRegistrationDeadline}
            deadlineLocked={false}
          />


          {/* SKILLS */}
          <div className="form-group">
            <label>Skills / Tags</label>

            <input
              name="skills"
              value={form.skills}
              onChange={updateField}
              placeholder="Cybersecurity, Networking, Linux"
            />

            <small className="form-help">
              Separate skills using commas.
            </small>
          </div>


          {/* POSTER */}
          <PosterField poster={poster} />


          {/* RESOURCE PERSONS */}
          <ResourcePersonsField
            persons={resourcePersons}
            setPersons={setResourcePersons}
          />



          {/* SUBMIT */}
          <button
            type="submit"
            className="btn-primary"
            disabled={loading}
          >
            {loading
              ? "Creating Event..."
              : "+ Create Event"}
          </button>

        </form>

      </Card>

    </Page>
  );
}

// ============================================================
// MY EVENTS
// ============================================================

// ============================================================
// MY EVENTS
// ============================================================

// ============================================================
// APPROVAL STATUS HELPERS (Faculty -> HOD -> Dean -> Admin)
// ============================================================

const REVIEW_ROLE_LABELS = {
  faculty: "Faculty",
  hod: "HOD",
  dean: "Dean",
  admin: "Admin",
};

const REVIEW_DECISION_LABELS = {
  approved: "Approved",
  changes: "Requested changes",
  rejected: "Rejected",
};

function ReturnedNotice({ review }) {
  if (!review) return null;
  const role = REVIEW_ROLE_LABELS[review.role] || review.role || "Reviewer";
  return (
    <div
      style={{
        margin: "12px 0",
        padding: "10px 12px",
        borderRadius: "10px",
        background: "rgba(245, 158, 11, 0.12)",
        border: "1px solid rgba(245, 158, 11, 0.35)",
        fontSize: "13px",
        lineHeight: 1.5,
      }}
    >
      <strong>
        {role} {review.decision === "rejected" ? "rejected this event" : "requested changes"}
      </strong>
      {review.remarks ? `: “${review.remarks}”` : "."}
      <div style={{ opacity: 0.8, marginTop: 4 }}>
        Edit the event and save to resubmit it for approval.
      </div>
    </div>
  );
}

function ApprovalHistory({ event }) {
  const history = Array.isArray(event?.approval_history)
    ? [...event.approval_history].reverse()
    : [];
  if (!event?.approval_status && history.length === 0) return null;

  return (
    <div style={{ margin: "16px 0" }}>
      <h3 style={{ margin: "0 0 8px", fontSize: "15px" }}>
        Approval status
      </h3>
      {history.length === 0 ? (
        <p style={{ margin: 0, opacity: 0.8, fontSize: "13px" }}>
          No review decisions yet. Current stage:{" "}
          {String(event.approval_status || "—").replace(/_/g, " ")}.
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {history.map((item, index) => (
            <li
              key={index}
              style={{
                padding: "8px 0",
                borderBottom: "1px solid rgba(148, 163, 184, 0.2)",
                fontSize: "13px",
              }}
            >
              <strong>
                {REVIEW_ROLE_LABELS[item.reviewer_role] || item.reviewer_role}
              </strong>{" "}
              — {REVIEW_DECISION_LABELS[item.decision] || item.decision}{" "}
              <span style={{ opacity: 0.7 }}>
                (v{item.submission_version || 1})
              </span>
              {item.remarks && (
                <div style={{ opacity: 0.85, fontStyle: "italic" }}>
                  “{item.remarks}”
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function MyEvents() {
  const navigate = useNavigate();

  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadEvents();
  }, []);

  async function loadEvents() {
    try {
      setLoading(true);
      setError("");

      const data = await apiFetch("/api/club/dashboard");

      setEvents(data.upcomingEvents || []);
    } catch (err) {
      setError(err.message || "Failed to load events.");
    } finally {
      setLoading(false);
    }
  }

  function handleView(event) {
    navigate(`/club-dashboard/events/${event.id}`);
  }

  function handleEdit(event) {
    navigate(`/club-dashboard/events/${event.id}/edit`);
  }

  function handleRegistrations(event) {
    navigate(
      `/club-dashboard/registrations?event=${encodeURIComponent(event.id)}`
    );
  }

  return (
    <Page
      title="My Events"
      subtitle="View and manage all events created by your club."
    >
      <ErrorBox message={error} />

      {loading ? (
        <Loading />
      ) : events.length === 0 ? (
        <Card className="empty-card">
          <div className="empty-icon">📭</div>

          <h3>No events found</h3>

          <p>
            Create your first event to get started.
          </p>

          <button
            className="btn-primary"
            onClick={() => navigate("/club-dashboard/events/new")}
          >
            + Create Event
          </button>
        </Card>
      ) : (
        <div className="event-grid">
          {events.map((event) => (
            <Card
              key={event.id}
              className="club-event-card"
            >
              {/* EVENT HEADER */}
              <div className="event-card-header">
                <span className="event-category">
                  {(event.category || "Event").toUpperCase()}
                </span>

                <span className="event-status">
                  {(event.status || "Published").toUpperCase()}
                </span>
              </div>

              {/* EVENT TITLE */}
              <h3 className="event-title">
                {event.title}
              </h3>

              {/* DATE */}
              <div className="event-detail">
                📅 {event.date || "Date not available"}
              </div>

              {/* TIME */}
              <div className="event-detail">
                🕐 {event.time || "Time not available"}
              </div>

              {/* VENUE */}
              <div className="event-detail">
                📍{" "}
                {event.venue ||
                  event.location ||
                  "Venue not available"}
              </div>

              {/* REGISTRATION PROGRESS */}
              <div className="event-registration">
                <span>
                  {event.registered || 0} /{" "}
                  {event.capacity || 0} registered
                </span>
              </div>

              {/* PROGRESS BAR */}
              <div className="registration-progress">
                <div
                  className="registration-progress-fill"
                  style={{
                    width: `${
                      event.capacity
                        ? Math.min(
                            ((event.registered || 0) /
                              event.capacity) *
                              100,
                            100
                          )
                        : 0
                    }%`,
                  }}
                />
              </div>

              {/* REVIEWER REMARKS (changes requested / rejected) */}
              <ReturnedNotice review={event.latest_review} />

              {event.can_edit === false && (
                <p style={{ margin: "8px 0", fontSize: "12px", opacity: 0.8 }}>
                  Under review — editing is locked until a reviewer responds.
                </p>
              )}

              {/* ACTION BUTTONS */}
              <div className="event-actions">

                <button
                  type="button"
                  className="event-action-btn"
                  onClick={() => handleView(event)}
                >
                  View
                </button>

                <button
                  type="button"
                  className="event-action-btn"
                  disabled={event.can_edit === false}
                  title={
                    event.can_edit === false
                      ? "This event is under review and cannot be edited"
                      : "Edit this event"
                  }
                  onClick={() => handleEdit(event)}
                >
                  Edit
                </button>

                <button
                  type="button"
                  className="event-action-btn"
                  onClick={() =>
                    handleRegistrations(event)
                  }
                >
                  Registrations
                </button>

              </div>
            </Card>
          ))}
        </div>
      )}
    </Page>
  );
}

// ============================================================
// EDIT REQUESTS
// Events a reviewer (Faculty / HOD / Dean / Admin) sent back to the
// club, so the organizer can edit and resubmit them.
// ============================================================

export function EditRequests() {
  const navigate = useNavigate();

  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadReturnedEvents();
  }, []);

  async function loadReturnedEvents() {
    try {
      setLoading(true);
      setError("");

      const data = await apiFetch("/api/club/dashboard");

      const returned = (data.upcomingEvents || []).filter(
        (event) =>
          event.approval_status === "changes_requested" ||
          event.approval_status === "rejected"
      );

      setEvents(returned);
    } catch (err) {
      setError(err.message || "Failed to load events that need changes.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Page
      title="Edit Requests"
      subtitle="Events sent back by a reviewer. Edit them and save to resubmit for approval."
    >
      <ErrorBox message={error} />

      {loading ? (
        <Loading />
      ) : events.length === 0 ? (
        <Card className="empty-card">
          <div className="empty-icon">✅</div>

          <h3>Nothing to edit</h3>

          <p>
            No reviewer has asked for changes. Events that are sent back will appear here.
          </p>
        </Card>
      ) : (
        <div className="event-grid">
          {events.map((event) => (
            <Card key={event.id} className="club-event-card">
              <div className="event-card-header">
                <span className="event-category">
                  {(event.category || "Event").toUpperCase()}
                </span>

                <span className="event-status">
                  {(event.status || "").toUpperCase()}
                </span>
              </div>

              <h3 className="event-title">{event.title}</h3>

              <div className="event-detail">
                📅 {event.date || "Date not available"}
              </div>

              <div className="event-detail">
                🕐 {event.time || "Time not available"}
              </div>

              <div className="event-detail">
                📍 {event.venue || "Venue not available"}
              </div>

              {/* WHO SENT IT BACK AND WHY */}
              <ReturnedNotice review={event.latest_review} />

              <div className="event-actions">
                <button
                  type="button"
                  className="event-action-btn"
                  onClick={() =>
                    navigate(`/club-dashboard/events/${event.id}`)
                  }
                >
                  View
                </button>

                <button
                  type="button"
                  className="event-action-btn"
                  onClick={() =>
                    navigate(`/club-dashboard/events/${event.id}/edit`)
                  }
                >
                  Edit &amp; Resubmit
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </Page>
  );
}

// ============================================================
// REGISTRATIONS
// ============================================================

export function Registrations() {

  const [registrations, setRegistrations] = useState([]);
  const [events, setEvents] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attendance, setAttendance] = useState({});
  useEffect(() => {
    loadData();
  }, []);

  async function loadAttendance(eventId) {
  try {
    const response = await apiFetch(
      `/api/events/${eventId}/attendance`
    );

    setAttendance((prev) => ({
      ...prev,
      [eventId]: response
    }));

  } catch (error) {
    console.error(
      "Attendance loading error:",
      error
    );
  }
}

  async function loadData() {

    try {

      setLoading(true);

      /*
       * Club dashboard already returns:
       *
       * recentRegistrations
       * upcomingEvents
       *
       * We use those existing backend values.
       */

      const data =
        await apiFetch(
          "/api/club/dashboard"
        );

      setRegistrations(
        data.recentRegistrations || []
      );

      setEvents(
        data.upcomingEvents || []
      );

    } catch (err) {

      setError(err.message);

    } finally {

      setLoading(false);

    }
  }


  const filtered =
    registrations.filter((item) => {

      const text =
        JSON.stringify(item)
          .toLowerCase();

      return text.includes(
        search.toLowerCase()
      );

    });


  return (
    <Page
      title="Registrations"
      subtitle="Students registered for your club events."
    >

      <ErrorBox message={error} />


      <Card>

        <div className="table-toolbar">

          <input
            value={search}
            onChange={(e) =>
              setSearch(e.target.value)
            }
            placeholder="Search registrations..."
          />

          <span>
            {registrations.length} registrations
          </span>

        </div>


        {loading ? (
          <Loading />
        ) : filtered.length === 0 ? (

          <div className="empty-table">
            No registrations found.
          </div>

        ) : (

          <div className="club-table-wrapper">

            <table className="club-table">

              <thead>

                <tr>
                  <th>Student</th>
                  <th>Email</th>
                  <th>Event</th>
                  <th>Date</th>
                  <th>Status</th>
                </tr>

              </thead>


              <tbody>

                {filtered.map(
                  (registration, index) => (

                    <tr
                      key={
                        registration.id ||
                        index
                      }
                    >

                      <td>
                        {registration.name ||
                          registration.student_name ||
                          registration.studentName ||
                          "—"}
                      </td>

                      <td>
                        {registration.email ||
                          "—"}
                      </td>

                      <td>
                        {registration.event_title ||
                          registration.eventTitle ||
                          registration.event ||
                          "—"}
                      </td>

                      <td>
                        {registration.date ||
                          registration.registered_at ||
                          registration.created_at ||
                          "—"}
                      </td>

                      <td>
                        <span className="status-badge">
                          {registration.status ||
                            "Registered"}
                        </span>
                      </td>

                    </tr>

                  )
                )}

              </tbody>

            </table>

          </div>

        )}

      </Card>

    </Page>
  );
}


// ============================================================
// CALENDAR
// ============================================================

export function CalendarPage() {

  const [events, setEvents] = useState([]);
  const [selectedDate, setSelectedDate] =
    useState("");

  useEffect(() => {

    apiFetch("/api/club/dashboard")
      .then((data) => {
        setEvents(
          data.upcomingEvents || []
        );
      })
      .catch(console.error);

  }, []);


  const dates = useMemo(() => {

    return events.reduce(
      (acc, event) => {

        const date =
          event.date || "";

        if (!acc[date]) {
          acc[date] = [];
        }

        acc[date].push(event);

        return acc;

      },
      {}
    );

  }, [events]);


  return (
    <Page
      title="Calendar"
      subtitle="View your club's upcoming events."
    >

      <Card>

        <div className="calendar-top">

          <input
            type="date"
            value={selectedDate}
            onChange={(e) =>
              setSelectedDate(
                e.target.value
              )
            }
          />

          <span>
            {events.length} events
          </span>

        </div>


        {selectedDate ? (

          <div>

            <h3>
              Events on {selectedDate}
            </h3>

            {(dates[selectedDate] || [])
              .map((event) => (

                <div
                  className="calendar-event"
                  key={event.id}
                >

                  <strong>
                    {event.title}
                  </strong>

                  <span>
                    {event.time}
                  </span>

                  <span>
                    {event.venue}
                  </span>

                </div>

              ))}

            {(!dates[selectedDate] ||
              dates[selectedDate].length === 0) && (

              <p className="muted">
                No events on this date.
              </p>

            )}

          </div>

        ) : (

          <div className="calendar-list">

            {events.map((event) => (

              <div
                className="calendar-event"
                key={event.id}
              >

                <div>
                  <strong>
                    {event.title}
                  </strong>

                  <span>
                    {event.date}
                  </span>
                </div>

                <span>
                  {event.time}
                </span>

              </div>

            ))}

          </div>

        )}

      </Card>

    </Page>
  );
}


// ============================================================
// ANALYTICS
// ============================================================

// ============================================================
// ANALYTICS
// ============================================================

export function Analytics() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [attendanceTotal, setAttendanceTotal] = useState(0);
  const [attendanceRecords, setAttendanceRecords] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    loadAnalytics();
  }, []);

  async function loadAnalytics() {
    try {
      setLoading(true);
      setError("");

      // --------------------------------------------------------
      // LOAD CLUB ANALYTICS
      // --------------------------------------------------------
      let analyticsData = null;

      try {
        analyticsData = await apiFetch("/api/club/analytics");
        setData(analyticsData);
      } catch (err) {
        console.warn(
          "Club analytics API unavailable:",
          err.message
        );

        // Keep analytics page working even if this endpoint
        // is not available.
        setData({});
      }

      // --------------------------------------------------------
      // LOAD CLUB EVENTS
      // --------------------------------------------------------
      let events = [];

      try {
        const eventsResponse = await apiFetch("/api/events");

        if (Array.isArray(eventsResponse)) {
          events = eventsResponse;
        } else if (Array.isArray(eventsResponse?.events)) {
          events = eventsResponse.events;
        } else if (Array.isArray(eventsResponse?.data)) {
          events = eventsResponse.data;
        }
      } catch (err) {
        console.warn(
          "Unable to load events:",
          err.message
        );
      }

      // --------------------------------------------------------
      // GET ATTENDANCE FOR CLUB EVENTS
      // --------------------------------------------------------
      let allAttendance = [];

      for (const event of events) {
        const eventId =
          event?._id ||
          event?.id ||
          event?.event_id;

        if (!eventId) continue;

        try {
          const attendanceResponse =
            await apiFetch(
              `/api/events/${eventId}/attendance`
            );

          const attendance =
            Array.isArray(attendanceResponse?.attendance)
              ? attendanceResponse.attendance
              : [];

          allAttendance = [
            ...allAttendance,
            ...attendance.map((item) => ({
              ...item,
              event_id: eventId,
              event_title:
                event?.title ||
                event?.name ||
                "Event",
            })),
          ];
        } catch (err) {
          console.warn(
            `Unable to load attendance for event ${eventId}:`,
            err.message
          );
        }
      }

      // --------------------------------------------------------
      // UPDATE ATTENDANCE STATE
      // --------------------------------------------------------
      setAttendanceRecords(allAttendance);
      setAttendanceTotal(allAttendance.length);

    } catch (err) {
      console.error(
        "ANALYTICS ERROR:",
        err
      );

      setError(
        err.message ||
        "Unable to load analytics."
      );
    } finally {
      setLoading(false);
    }
  }

  // ----------------------------------------------------------
  // CALCULATIONS
  // ----------------------------------------------------------

  const totalEvents =
    data?.total_events ??
    data?.events_count ??
    data?.event_count ??
    0;

  const totalRegistrations =
    data?.total_registrations ??
    data?.registrations_count ??
    data?.registration_count ??
    0;

  const totalStudents =
    data?.total_students ??
    data?.students_count ??
    0;

  const calculatedEvents =
    data?.events?.length ??
    0;

  const calculatedRegistrations =
    data?.registrations?.length ??
    0;

  const displayEvents =
    Number(totalEvents) > 0
      ? totalEvents
      : calculatedEvents;

  const displayRegistrations =
    Number(totalRegistrations) > 0
      ? totalRegistrations
      : calculatedRegistrations;

  // ----------------------------------------------------------
  // ATTENDANCE BY EVENT
  // ----------------------------------------------------------

  const attendanceByEvent = useMemo(() => {
    const grouped = {};

    attendanceRecords.forEach((record) => {
      const eventId =
        record?.event_id || "unknown";

      if (!grouped[eventId]) {
        grouped[eventId] = {
          event_id: eventId,
          event_title:
            record?.event_title || "Event",
          count: 0,
        };
      }

      grouped[eventId].count += 1;
    });

    return Object.values(grouped);
  }, [attendanceRecords]);

  // ----------------------------------------------------------
  // LOADING
  // ----------------------------------------------------------

  if (loading) {
    return (
      <Page
        title="Analytics"
        subtitle="View event, registration and attendance analytics."
      >
        <Loading />
      </Page>
    );
  }

  // ----------------------------------------------------------
  // PAGE
  // ----------------------------------------------------------

  return (
    <Page
      title="Analytics"
      subtitle="View event, registration and attendance analytics."
    >
      <ErrorBox message={error} />

      {/* ======================================================
          SUMMARY CARDS
      ====================================================== */}

      <div className="club-profile-grid">

        <Card>
          <h3>Total Events</h3>

          <div
            style={{
              fontSize: "36px",
              fontWeight: "700",
              marginTop: "10px",
            }}
          >
            {displayEvents}
          </div>

          <p className="muted">
            Events organized by the club
          </p>
        </Card>

        <Card>
          <h3>Total Registrations</h3>

          <div
            style={{
              fontSize: "36px",
              fontWeight: "700",
              marginTop: "10px",
            }}
          >
            {displayRegistrations}
          </div>

          <p className="muted">
            Student registrations
          </p>
        </Card>

        <Card>
          <h3>Total Attendance</h3>

          <div
            style={{
              fontSize: "36px",
              fontWeight: "700",
              marginTop: "10px",
            }}
          >
            {attendanceTotal}
          </div>

          <p className="muted">
            Students marked present
          </p>
        </Card>

        <Card>
          <h3>Total Students</h3>

          <div
            style={{
              fontSize: "36px",
              fontWeight: "700",
              marginTop: "10px",
            }}
          >
            {totalStudents}
          </div>

          <p className="muted">
            Students participating
          </p>
        </Card>

      </div>

      {/* ======================================================
          ATTENDANCE DETAILS
      ====================================================== */}

      <div style={{ marginTop: "25px" }}>

        <Card>

          <h2>
            Attendance Overview
          </h2>

          <p className="muted">
            Attendance recorded through QR scanning.
          </p>

          {attendanceRecords.length === 0 ? (
            <div
              style={{
                padding: "30px 10px",
                textAlign: "center",
              }}
            >
              <div
                style={{
                  fontSize: "40px",
                  marginBottom: "10px",
                }}
              >
                📷
              </div>

              <h3>
                No attendance recorded yet
              </h3>

              <p className="muted">
                Attendance will appear here after
                students scan the event QR code.
              </p>
            </div>
          ) : (
            <div
              style={{
                overflowX: "auto",
                marginTop: "20px",
              }}
            >
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                }}
              >
                <thead>
                  <tr>
                    <th
                      style={{
                        textAlign: "left",
                        padding: "12px",
                      }}
                    >
                      Event
                    </th>

                    <th
                      style={{
                        textAlign: "left",
                        padding: "12px",
                      }}
                    >
                      Attendance
                    </th>

                    <th
                      style={{
                        textAlign: "left",
                        padding: "12px",
                      }}
                    >
                      Status
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {attendanceByEvent.map(
                    (event) => (
                      <tr
                        key={event.event_id}
                      >
                        <td
                          style={{
                            padding: "12px",
                          }}
                        >
                          {event.event_title}
                        </td>

                        <td
                          style={{
                            padding: "12px",
                            fontWeight: "600",
                          }}
                        >
                          {event.count}
                        </td>

                        <td
                          style={{
                            padding: "12px",
                          }}
                        >
                          <span
                            style={{
                              padding:
                                "5px 10px",
                              borderRadius:
                                "15px",
                            }}
                          >
                            Present
                          </span>
                        </td>
                      </tr>
                    )
                  )}
                </tbody>
              </table>
            </div>
          )}

        </Card>

      </div>

      {/* ======================================================
          QR ATTENDANCE INFO
      ====================================================== */}

      <div style={{ marginTop: "25px" }}>

        <Card>

          <h2>
            📷 QR Attendance
          </h2>

          <p className="muted">
            Use the Scan Attendance section from
            the sidebar to mark student attendance.
          </p>

          <div
            style={{
              marginTop: "15px",
              padding: "15px",
              borderRadius: "10px",
            }}
          >
            <strong>
              {attendanceTotal}
            </strong>{" "}
            attendance record
            {attendanceTotal === 1
              ? ""
              : "s"} recorded.
          </div>

        </Card>

      </div>

    </Page>
  );
}

// ============================================================
// NOTIFICATIONS
// ============================================================

export function Notifications() {
  const navigate = useNavigate();
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadNotifications();
  }, []);

  async function loadNotifications() {
    try {
      setLoading(true);
      setError("");

      const data = await apiFetch("/api/club/dashboard");

      // Event edit / report notifications (non-fatal if this fails)
      let editNotifications = [];

      try {
        const editData = await apiFetch("/api/club/notifications");

        editNotifications = (editData.notifications || []).map((n) => ({
          id: `event-update-${n.id}`,
          title: n.title || "Event Updated",
          message: n.message,
          time: n.created_at
            ? new Date(n.created_at).toLocaleString()
            : "",
          read: n.is_read,
          // Dean-requested changes: backend should send these fields
          type: n.type || "",
          eventId: n.event_id || n.related_id || null,
          remarks: n.remarks || n.comment || "",
        }));
      } catch (notifErr) {
        console.error("Event update notifications error:", notifErr);
      }

      const registrations =
        data.recentRegistrations || [];

      const generatedNotifications =
        registrations.map((registration, index) => ({
          id:
            registration.id ||
            registration._id ||
            `registration-${index}`,

          title: "New Event Registration",

          message:
            `${registration.name || "A student"} registered for ` +
            `"${registration.event_title || registration.event || "your event"}".`,

          time:
            registration.registered_at ||
            registration.date ||
            "",

          read: false,
        }));

      setNotifications([
        ...editNotifications,
        ...generatedNotifications,
      ]);

    } catch (err) {
      console.error(
        "Notification loading error:",
        err
      );

      setError(
        err.message ||
        "Failed to load notifications."
      );

      setNotifications([]);

    } finally {
      setLoading(false);
    }
  }

  return (
    <Page
      title="Notifications"
      subtitle="Stay updated with your club activity."
    >

      <Card>

        {loading ? (

          <div className="empty-card">
            <div className="empty-icon">
              🔄
            </div>

            <h3>
              Loading notifications...
            </h3>
          </div>

        ) : error ? (

          <div className="empty-card">

            <div className="empty-icon">
              ⚠️
            </div>

            <h3>
              Unable to load notifications
            </h3>

            <p>
              {error}
            </p>

          </div>

        ) : notifications.length === 0 ? (

          <div className="empty-card">

            <div className="empty-icon">
              🔔
            </div>

            <h3>
              No notifications
            </h3>

            <p>
              New registrations and updates
              will appear here.
            </p>

          </div>

        ) : (

          <div className="notifications-list">

            {notifications.map(
              (notification) => (

                <div
                  className="notification-item"
                  key={notification.id}
                >

                  <div className="notification-icon">
                    {notification.type === "changes_requested" ? "📝" : "👤"}
                  </div>

                  <div className="notification-content">

                    <strong>
                      {notification.title}
                    </strong>

                    <p>
                      {notification.message}
                    </p>

                    {notification.remarks && (
                      <p>
                        <em>Reviewer's remarks: {notification.remarks}</em>
                      </p>
                    )}

                    {notification.time && (
                      <small>
                        {notification.time}
                      </small>
                    )}

                    {notification.type === "changes_requested" &&
                      notification.eventId && (
                        <div style={{ marginTop: 8 }}>
                          <button
                            type="button"
                            className="btn-primary"
                            onClick={() =>
                              navigate(
                                `/club-dashboard/events/${notification.eventId}/edit`
                              )
                            }
                          >
                            Edit Event
                          </button>
                        </div>
                      )}

                  </div>

                </div>

              )
            )}

          </div>

        )}

      </Card>

    </Page>
  );
}

// ============================================================
// PROFILE
// ============================================================

export function Profile() {

  const [profile, setProfile] =
    useState(null);

  useEffect(() => {

    apiFetch("/api/club/dashboard")
      .then((data) => {
        setProfile(data.club || {});
      })
      .catch(console.error);

  }, []);


  return (
    <Page
      title="Profile"
      subtitle="Manage your club leader profile."
    >

      <Card>

        <div className="profile-header">

          <div className="club-big-avatar">
            {(profile?.name || "C")
              .charAt(0)
              .toUpperCase()}
          </div>

          <div>

            <h2>
              {profile?.name ||
                "Beta Club"}
            </h2>

            <p className="muted">
              Club Leader
            </p>

          </div>

        </div>


        <div className="profile-fields">

          <div>
            <label>
              Club Name
            </label>

            <input
              value={
                profile?.name || ""
              }
              readOnly
            />
          </div>


          <div>
            <label>
              Role
            </label>

            <input
              value="Club Leader"
              readOnly
            />
          </div>

        </div>

      </Card>

    </Page>
  );
}


// ============================================================
// SETTINGS
// ============================================================

export function Settings() {

  const [emailNotifications, setEmailNotifications] =
    useState(true);

  const [eventNotifications, setEventNotifications] =
    useState(true);

  const { theme, setTheme } = useTheme();
  return (
    <Page
      title="Settings"
      subtitle="Manage your club account preferences."
    >

      <Card>

        <h3>
          Notification Settings
        </h3>


        <div className="setting-row">

          <div>

            <strong>
              Email Notifications
            </strong>

            <p>
              Receive important updates through email.
            </p>

          </div>

          <input
            type="checkbox"
            checked={emailNotifications}
            onChange={(e) =>
              setEmailNotifications(
                e.target.checked
              )
            }
          />

        </div>
        
        

        <div className="setting-row">

          <div>

            <strong>
              Event Notifications
            </strong>

            <p>
              Receive notifications about registrations.
            </p>

          </div>
          

          <input
            type="checkbox"
            checked={eventNotifications}
            onChange={(e) =>
              setEventNotifications(
                e.target.checked
              )
            }
          />

        </div>
        <div className="appearance-settings">
  <h3>Appearance</h3>
  <p>Choose your preferred dashboard theme.</p>

  <button
    onClick={() => setTheme("light")}
    className={theme === "light" ? "active" : ""}
  >
    ☀️ Light Mode
  </button>

  <button
    onClick={() => setTheme("dark")}
    className={theme === "dark" ? "active" : ""}
  >
    🌙 Dark Mode
  </button>
</div>

      </Card>


      <Card>

        <h3>
          Account
        </h3>

        <button
          className="danger-btn"
          onClick={() => {

            localStorage.removeItem("token");
            localStorage.removeItem("access_token");
            localStorage.removeItem("jwt");

            window.location.href =
              "/login";

          }}
        >
          Logout
        </button>

      </Card>

    </Page>
  );
}

// ============================================================
// VIEW EVENT
// ============================================================

const APPROVAL_LABELS = {
  pending_faculty: "Waiting for Faculty review",
  pending_hod: "Waiting for HOD review",
  pending_dean: "Waiting for Dean review",
  pending_admin: "Waiting for Admin review",
  published: "Published",
  changes_requested: "Changes requested",
  rejected: "Rejected",
};
const approvalStatusLabel = (status) =>
  APPROVAL_LABELS[status] || (status ? String(status) : "Published");
// Same rule as the server: events under active review cannot be edited.
const canEditEvent = (event) =>
  !String(event?.approval_status || "").startsWith("pending_");

export function ClubEventView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const justCreated = Boolean(useLocation().state?.justCreated);

  const [event, setEvent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadEvent();
  }, [id]);

  async function loadEvent() {
    try {
      setLoading(true);
      setError("");

      const data = await apiFetch(`/api/events/${id}`);

      setEvent(data);
    } catch (err) {
      console.error("VIEW EVENT ERROR:", err);
      setError(err.message || "Failed to load event.");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <Page
        title="Event Details"
        subtitle="Loading event information..."
      >
        <Loading />
      </Page>
    );
  }

  if (error) {
    return (
      <Page
        title="Event Details"
        subtitle="Unable to load event."
      >
        <ErrorBox message={error} />
      </Page>
    );
  }

  if (!event) {
    return (
      <Page
        title="Event Details"
        subtitle="Event not found."
      />
    );
  }

  return (
    <Page
      title={event.title || "Event Details"}
      subtitle="View event information."
    >
      <Card>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}>
          {justCreated && <strong>Event created successfully.</strong>}
          <span>
            Approval status:{" "}
            <strong>{approvalStatusLabel(event.approval_status)}</strong>
          </span>
          <button type="button" className="event-action-btn" onClick={() => navigate("/club-dashboard")}>
            Back to Dashboard
          </button>
          <button type="button" className="event-action-btn" onClick={() => navigate("/club-dashboard/events")}>
            View My Events
          </button>
          {canEditEvent(event) && (
            <button
              type="button"
              className="event-action-btn"
              onClick={() => navigate(`/club-dashboard/events/${event._id || event.id || id}/edit`)}
            >
              Edit Event
            </button>
          )}
        </div>

        {event.poster_url && (
          <img
            src={posterSrc(event.poster_url)}
            alt={`${event.title || "Event"} poster`}
            style={{
              display: "block",
              width: "100%",
              maxWidth: "500px",
              maxHeight: "320px",
              objectFit: "contain",
              borderRadius: "16px",
              marginBottom: "20px",
            }}
          />
        )}

        <div className="event-view-header">

          <div>
            <span className="event-category">
              {(event.category || "Event").toUpperCase()}
            </span>

            <h2>
              {event.title}
            </h2>
          </div>

          <span className="event-status">
            {(event.status || "PUBLISHED").toUpperCase()}
          </span>

        </div>


        <ApprovalHistory event={event} />

        <div className="event-view-details">

         <div className="event-view-item">
  <span>📝 Description</span>
  <strong>
    {" "}
    {event.description || "No description available."}
  </strong>
</div>


          <div className="event-view-item">
            <span>📅 Date</span>
            <strong>
              {event.date || "Not specified"}
            </strong>
          </div>


          <div className="event-view-item">
            <span>🕐 Time</span>
            <strong>
              {event.time || "Not specified"}
            </strong>
          </div>


          <div className="event-view-item">
            <span>📍 Venue</span>
            <strong>
              {event.location ||
                event.venue ||
                (event.event_mode === "Online" ? "Online" : "Not specified")}
            </strong>
          </div>


          {event.event_mode && (
            <div className="event-view-item">
              <span>💻 Mode</span>
              <strong>{event.event_mode}</strong>
            </div>
          )}


          {event.school_name && (
            <div className="event-view-item">
              <span>🏫 School</span>
              <strong>{event.school_name}</strong>
            </div>
          )}


          {event.department && (
            <div className="event-view-item">
              <span>🎓 Department</span>
              <strong>{event.department}</strong>
            </div>
          )}


          {(event.spoc_name || event.spoc_designation) && (
            <div className="event-view-item">
              <span>👤 SPOC</span>
              <strong>
                {[event.spoc_name, event.spoc_designation]
                  .filter(Boolean)
                  .join(", ")}
              </strong>
            </div>
          )}


          {event.spoc_email && (
            <div className="event-view-item">
              <span>✉️ SPOC Email</span>
              <strong>
                <a href={`mailto:${event.spoc_email}`}>
                  {event.spoc_email}
                </a>
              </strong>
            </div>
          )}


          {event.spoc_contact && (
            <div className="event-view-item">
              <span>📞 SPOC Contact</span>
              <strong>{event.spoc_contact}</strong>
            </div>
          )}


          {event.budget != null && (
            <div className="event-view-item">
              <span>💰 Budget (private)</span>
              <strong>{formatRupees(event.budget)}</strong>
            </div>
          )}


          <div className="event-view-item">
            <span>📝 Registration</span>
            <strong>{registrationLabel(event)}</strong>
          </div>


          {isRegistrationRequired(event) && event.registration_deadline && (
            <div className="event-view-item">
              <span>⏰ Registration deadline</span>
              <strong>
                {formatDeadline(event.registration_deadline)}
                {isRegistrationClosed(event) ? " (closed)" : ""}
              </strong>
            </div>
          )}


          <div className="event-view-item">
            <span>👥 Registrations</span>
            <strong>
              {event.registered || 0}
              {event.capacity
                ? ` / ${event.capacity}`
                : ""}
            </strong>
          </div>


          <div className="event-view-item">
            <span>🏷️ Organizer</span>
            <strong>
              {event.organizer || "Beta club"}
            </strong>
          </div>

        </div>


        {event.skills &&
          event.skills.length > 0 && (
            <div className="event-skills">

              <h3>Skills / Tags</h3>

              <p className="skill-list">
                {event.skills
                  .map((skill) => String(skill).trim())
                  .filter(Boolean)
                  .join(", ")}
              </p>

            </div>
          )}

        <ResourcePersonsView persons={event.resource_persons} />

        {/* Published event: ask the Admin to change the deadline */}
        <DeadlineChangeRequest event={event} />

        {/* Event Checklist (published events only; the server enforces it) */}
        <EventChecklist event={event} />
        <EventReport event={event} />

        {/* BUDGET BREAKUP (read-only here; edit it from the Edit page) */}
        {hasSavedBreakup(event.budget_breakup) && (
          <div className="event-skills">
            <h3>Budget Breakup</h3>
            <BudgetBreakupForm
              value={breakupFromEvent(event.budget_breakup)}
              readOnly
              idPrefix="club-view-budget"
            />
          </div>
        )}

        {/* LEGACY EVENT REPORT: only older events can have one */}
        {event.report_pdf_url && (
        <div className="event-skills">
          <h3>Event Report</h3>

          <p>
            <a
              href={posterSrc(event.report_pdf_url)}
              target="_blank"
              rel="noopener noreferrer"
            >
              📄 View / Download Event Report (PDF)
            </a>
          </p>
        </div>
        )}

      </Card>
    </Page>
  );
}


// ============================================================
// EDIT EVENT
// ============================================================

export function EditEvent() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const poster = usePoster();
  const [existingPoster, setExistingPoster] = useState("");
  const [removePoster, setRemovePoster] = useState(false);
  const [resourcePersons, setResourcePersons] = useState([
    emptyResourcePerson(),
  ]);

  // Budget breakup. legacyTotal is set only for older events that were
  // saved with a single budget figure and have no breakup yet.
  const [breakup, setBreakup] = useState(emptyBreakup);
  const [legacyTotal, setLegacyTotal] = useState(null);

  // Registration requirement of the event being edited (older events: true)
  const [registrationRequired, setRegistrationRequired] = useState(true);
  const [registrationDeadline, setRegistrationDeadline] = useState("");
  // Published events keep their deadline; changes go through the Admin.
  const [deadlineLocked, setDeadlineLocked] = useState(false);

  const [form, setForm] = useState({
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
  });


  useEffect(() => {
    loadEvent();
  }, [id]);


  async function loadEvent() {
    try {
      setLoading(true);
      setError("");

      const data = await apiFetch(
        `/api/events/${id}`
      );

      let startTime = "";
      let endTime = "";

      if (data.time) {
        const parts = data.time
          .split("-")
          .map((item) => item.trim());

        startTime = parts[0] || "";
        endTime = parts[1] || "";
      }

      setForm({
        title: data.title || "",
        description: data.description || "",
        category: data.category || "",
        date: data.date || "",
        startTime,
        endTime,
        venue:
          data.location ||
          data.venue ||
          "",
        capacity: data.capacity || "",
        skills: Array.isArray(data.skills)
          ? data.skills.join(", ")
          : data.skills || "",
        // Older events have none of these yet; they stay blank until filled in.
        schoolName: data.school_name || "",
        department: matchDepartment(data.department),
        eventMode: data.event_mode || "",
        spocName: data.spoc_name || "",
        spocDesignation: data.spoc_designation || "",
        spocEmail: data.spoc_email || "",
        spocContact: data.spoc_contact || "",
      });

      // Budget breakup (the server only sends it to accounts allowed to see it)
      const savedBreakup = hasSavedBreakup(data.budget_breakup);
      setBreakup(breakupFromEvent(data.budget_breakup));
      setRegistrationRequired(isRegistrationRequired(data));
      setRegistrationDeadline(toDateTimeLocal(data.registration_deadline));
      setDeadlineLocked(data.approval_status === "published");
      setLegacyTotal(
        !savedBreakup && data.budget != null ? Number(data.budget) : null
      );

      setResourcePersons(resourcePersonsFromEvent(data));
      setExistingPoster(data.poster_url || "");
      setRemovePoster(false);
      poster.clear(data.poster_url || "");

    } catch (err) {
      console.error("LOAD EVENT ERROR:", err);
      setError(
        err.message ||
        "Failed to load event."
      );
    } finally {
      setLoading(false);
    }
  }


  function updateField(e) {
    setForm({
      ...form,
      [e.target.name]: e.target.value,
    });
  }


  async function updateEvent(e) {
    e.preventDefault();

    setSaving(true);
    setError("");
    setMessage("");

    let detailsProblem =
      validateEventDetails(form) ||
      validateBreakup(breakup) ||
      (registrationRequired && !deadlineLocked && registrationDeadline
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
      setSaving(false);
      return;
    }

    try {

      // Upload a replacement poster first (if one was chosen)
      const newPosterUrl = poster.file
        ? await uploadPosterFile(poster.file)
        : null;
      const payload = {
  title: form.title.trim(),
  description: form.description.trim(),
  category: form.category,

  organizer: "Beta club",

  date: form.date,
  time: `${form.startTime} - ${form.endTime}`,
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
  // Only events with registration have a deadline (IST); published events
  // change it through "Request deadline change" instead.
  ...(deadlineLocked
    ? {}
    : { registration_deadline: registrationRequired ? registrationDeadline : null }),

  // IMPORTANT
  capacity: Number(form.capacity),

  skills: form.skills
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean),

  // Always sent, so removing every person clears the list
  resource_persons: cleanResourcePersons(resourcePersons),
  // new file -> replace; "" -> remove; omitted -> keep current poster
  ...(newPosterUrl
    ? { poster_url: newPosterUrl }
    : removePoster
      ? { poster_url: "" }
      : {}),
};


      console.log(
        "UPDATE EVENT PAYLOAD:",
        payload
      );


      const result = await apiFetch(
        `/api/events/${id}`,
        {
          method: "PUT",
          body: JSON.stringify(payload),
        }
      );


      console.log(
        "UPDATE EVENT RESPONSE:",
        result
      );


      setMessage(
        "Event updated successfully."
      );

      setTimeout(() => {
        navigate(
          `/club-dashboard/events/${id}`
        );
      }, 700);

    } catch (err) {

      console.error(
        "UPDATE EVENT ERROR:",
        err
      );

      setError(
        err.message ||
        "Failed to update event."
      );

    } finally {
      setSaving(false);
    }
  }


  if (loading) {
    return (
      <Page
        title="Edit Event"
        subtitle="Loading event..."
      >
        <Loading />
      </Page>
    );
  }


  return (
    <Page
      title="Edit Event"
      subtitle="Update your event details."
    >

      <Card>

        <ErrorBox message={error} />

        {message && (
          <div className="club-success">
            {message}
          </div>
        )}


        <form
          className="club-event-form"
          onSubmit={updateEvent}
        >

          <div className="form-group">
            <label>Event Title</label>

            <input
              name="title"
              value={form.title}
              onChange={updateField}
              required
            />
          </div>


          <div className="form-group">
            <label>Description</label>

            <textarea
              name="description"
              value={form.description}
              onChange={updateField}
              rows="5"
              required
            />
          </div>


          <div className="form-grid">

            <div className="form-group">
              <label>Category</label>

              <select
                name="category"
                value={form.category}
                onChange={updateField}
                required
              >
                <option value="">
                  Select category
                </option>

                {EVENT_CATEGORIES.map((categoryName) => (
                  <option key={categoryName} value={categoryName}>
                    {categoryName}
                  </option>
                ))}

                {/* keep an older event's existing category selectable */}
                {form.category &&
                  !EVENT_CATEGORIES.includes(form.category) && (
                    <option value={form.category}>
                      {form.category}
                    </option>
                  )}
              </select>
            </div>


            <div className="form-group">
              <label>Capacity</label>

              <input
                type="number"
                name="capacity"
                value={form.capacity}
                onChange={updateField}
                min="1"
              />
            </div>

          </div>


          <div className="form-grid">

            <div className="form-group">
              <label>Start Time</label>

              <input
                type="text"
                name="startTime"
                value={form.startTime}
                onChange={updateField}
                placeholder="10:00 AM"
                required
              />
            </div>


            <div className="form-group">
              <label>Finish Time</label>

              <input
                type="text"
                name="endTime"
                value={form.endTime}
                onChange={updateField}
                placeholder="12:00 PM"
                required
              />
            </div>

          </div>


          <div className="form-group">
            <label>Date</label>

            <input
              type="date"
              name="date"
              value={form.date}
              onChange={updateField}
              required
            />
          </div>


          <div className="form-group">
            <label>
              Venue{form.eventMode === "Online" ? " (optional for Online events)" : ""}
            </label>

            <input
              name="venue"
              value={form.venue}
              onChange={updateField}
              required={form.eventMode !== "Online"}
            />
          </div>


          {/* ORGANIZING DETAILS + SPOC + BUDGET BREAKUP */}
          <EventOrganizingFields
            form={form}
            updateField={updateField}
            breakup={breakup}
            setBreakup={setBreakup}
            legacyTotal={legacyTotal}
            registrationRequired={registrationRequired}
            setRegistrationRequired={setRegistrationRequired}
            registrationDeadline={registrationDeadline}
            setRegistrationDeadline={setRegistrationDeadline}
            deadlineLocked={deadlineLocked}
          />


          <div className="form-group">
            <label>Skills / Tags</label>

            <input
              name="skills"
              value={form.skills}
              onChange={updateField}
              placeholder="Cybersecurity, Networking, Linux"
            />
          </div>


          <PosterField
            poster={poster}
            onPick={() => setRemovePoster(false)}
          >
            <div
              style={{
                display: "flex",
                gap: "10px",
                marginTop: "10px",
                flexWrap: "wrap",
              }}
            >
              {poster.file && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() =>
                    poster.clear(removePoster ? "" : existingPoster)
                  }
                >
                  Discard new poster
                </button>
              )}

              {!poster.file && existingPoster && !removePoster && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    poster.clear("");
                    setRemovePoster(true);
                  }}
                >
                  Remove poster
                </button>
              )}

              {!poster.file && removePoster && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setRemovePoster(false);
                    poster.clear(existingPoster);
                  }}
                >
                  Undo remove
                </button>
              )}
            </div>
          </PosterField>


          <ResourcePersonsField
            persons={resourcePersons}
            setPersons={setResourcePersons}
          />




          <div className="event-actions">

            <button
              type="submit"
              className="btn-primary"
              disabled={saving}
            >
              {saving
                ? "Saving..."
                : "Save Changes"}
            </button>


            <button
              type="button"
              className="btn-ghost"
              onClick={() =>
                navigate(
                  `/club-dashboard/events/${id}`
                )
              }
            >
              Cancel
            </button>

          </div>

        </form>

      </Card>

    </Page>
  );
}
// ============================================================
// CLUB MODULE ROUTER
// ============================================================
//
// Use this if you don't want to create 10 separate files.
// The existing sidebar can navigate to these URLs.
//
// ============================================================

export default function ClubModules() {

  const path =
    window.location.pathname;


  if (
    path === "/club/my-club"
  ) {
    return <MyClub />;
  }


  if (
    path === "/club/create-event"
  ) {
    return <CreateEvent />;
  }


  if (
    path === "/club/my-events"
  ) {
    return <MyEvents />;
  }


  if (
    path === "/club/edit-requests"
  ) {
    return <EditRequests />;
  }


  if (
    path === "/club/registrations"
  ) {
    return <Registrations />;
  }


  if (
    path === "/club/calendar"
  ) {
    return <CalendarPage />;
  }


  if (
    path === "/club/analytics"
  ) {
    return <Analytics />;
  }


  if (
    path === "/club/notifications"
  ) {
    return <Notifications />;
  }


  if (
    path === "/club/profile"
  ) {
    return <Profile />;
  }


  if (
    path === "/club/settings"
  ) {
    return <Settings />;
  }
  if (
    path === "/club/attendance"
  ) {
    return <ScanAttendance />;
  }

  return null;
}
export { ScanAttendance };