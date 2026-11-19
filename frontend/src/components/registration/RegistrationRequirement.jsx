import React, { useState } from "react";

const API_URL = "http://127.0.0.1:8000";

/**
 * Event Registration Requirement.
 *   true  = students register through the existing flow
 *   false = no registration, students simply attend
 * Older events have no field and count as "registration required".
 */
export function isRegistrationRequired(event) {
  return !(event && event.registration_required === false);
}

export const REGISTRATION_LABEL = "Registration Required";
export const NO_REGISTRATION_LABEL = "No Registration Required";

export function registrationLabel(event) {
  return isRegistrationRequired(event)
    ? REGISTRATION_LABEL
    : NO_REGISTRATION_LABEL;
}

// ------------------------------------------------------------
// Registration deadline helpers (all times are Asia/Kolkata, IST)
// ------------------------------------------------------------
const IST_OFFSET = "+05:30";

/** "2026-12-01T17:00:00+05:30" -> "2026-12-01T17:00" (datetime-local value). */
export function toDateTimeLocal(value) {
  const text = String(value || "");
  if (!text) return "";
  if (text.endsWith(IST_OFFSET)) return text.slice(0, 16);
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return "";
  const ist = new Date(parsed.getTime() + 5.5 * 3600 * 1000);
  return ist.toISOString().slice(0, 16);
}

const istMillis = (local) => {
  const text = String(local || "");
  if (!text) return NaN;
  return Date.parse(`${text.length === 16 ? text + ":00" : text}${IST_OFFSET}`);
};

/** Readable IST text, e.g. "01 Dec 2026, 05:00 PM IST". */
export function formatDeadline(value) {
  const millis = Date.parse(String(value || ""));
  if (Number.isNaN(millis)) return "";
  return (
    new Date(millis).toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
    }) + " IST"
  );
}

/** True when registration is closed (server flag, or the deadline passed). */
export function isRegistrationClosed(event) {
  if (!event || event.registration_required === false) return false;
  if (event.registration_closed === true) return true;
  const millis = Date.parse(String(event.registration_deadline || ""));
  return !Number.isNaN(millis) && Date.now() > millis;
}

/** First problem with a deadline, or "" (same rules as the server). */
export function deadlineProblem(deadline, date, time, required = true) {
  if (!deadline) {
    return required
      ? "Registration deadline is required for events with registration."
      : "";
  }
  const when = istMillis(deadline);
  if (Number.isNaN(when)) return "Enter a valid registration deadline.";

  const match = String(time || "").match(/(\d{1,2}):(\d{2})/);
  if (date && match) {
    const start = istMillis(
      `${String(date).slice(0, 10)}T${match[1].padStart(2, "0")}:${match[2]}`
    );
    if (!Number.isNaN(start) && when >= start) {
      return "The registration deadline must be before the event starts.";
    }
  }
  if (when <= Date.now()) return "The registration deadline must be in the future.";
  return "";
}

const optionStyle = (active) => ({
  display: "flex",
  alignItems: "center",
  gap: "8px",
  padding: "10px 14px",
  borderRadius: "10px",
  cursor: "pointer",
  border: active
    ? "2px solid var(--accent-color, var(--primary, #6757f5))"
    : "1px solid rgba(148, 163, 184, 0.4)",
  background: active ? "rgba(103, 87, 245, 0.10)" : "transparent",
});

/** Radio choice used on the Club Coordinator and Faculty event forms. */
export default function RegistrationRequirement({
  value,
  onChange,
  idPrefix = "regreq",
  deadline,
  onDeadlineChange,
  deadlineLocked = false,
}) {
  return (
    <fieldset
      style={{ border: 0, margin: "8px 0 4px", padding: 0, textAlign: "left" }}
    >
      <legend style={{ fontSize: "15px", fontWeight: 700, padding: 0 }}>
        Event Registration Requirement
      </legend>

      <div
        style={{
          display: "flex",
          gap: "12px",
          flexWrap: "wrap",
          marginTop: "8px",
        }}
      >
        {[
          [true, REGISTRATION_LABEL],
          [false, NO_REGISTRATION_LABEL],
        ].map(([optionValue, text]) => (
          <label
            key={text}
            htmlFor={`${idPrefix}-${optionValue}`}
            style={optionStyle(value === optionValue)}
          >
            <input
              id={`${idPrefix}-${optionValue}`}
              type="radio"
              name={`${idPrefix}-group`}
              checked={value === optionValue}
              onChange={() => onChange(optionValue)}
              style={{ width: 18, height: 18, margin: 0, padding: 0 }}
            />
            <span>{text}</span>
          </label>
        ))}
      </div>

      <p style={{ fontSize: "12px", opacity: 0.7, margin: "6px 0 0" }}>
        {value
          ? "Students must register for this event through the Register button."
          : "Students can attend without registering. No Register button is shown."}
      </p>

      {value && onDeadlineChange && (
        <div style={{ marginTop: "12px" }}>
          <label
            htmlFor={`${idPrefix}-deadline`}
            style={{ display: "block", fontWeight: 600, marginBottom: "4px" }}
          >
            Registration Deadline (IST)
          </label>
          <input
            id={`${idPrefix}-deadline`}
            type="datetime-local"
            value={deadline || ""}
            disabled={deadlineLocked}
            onChange={(e) => onDeadlineChange(e.target.value)}
            style={{ maxWidth: "260px" }}
          />
          <p style={{ fontSize: "12px", opacity: 0.7, margin: "4px 0 0" }}>
            {deadlineLocked
              ? "This event is published. To change the deadline, use “Request deadline change” on the event page; the Admin must approve it."
              : "Registration closes automatically at this time. It must be before the event starts."}
          </p>
        </div>
      )}
    </fieldset>
  );
}


function authToken() {
  return (
    localStorage.getItem("token") ||
    localStorage.getItem("access_token") ||
    localStorage.getItem("jwt") ||
    ""
  );
}

function errorText(data, fallback) {
  const detail = data && data.detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || String(item)).join(" ");
  }
  return (typeof detail === "string" && detail) || fallback;
}

/**
 * Shown on the event page of a PUBLISHED Registration Required event.
 * Club Coordinator / Faculty ask for a new deadline (with a reason); the
 * Admin approves or rejects. The current deadline stays until approval.
 */
export function DeadlineChangeRequest({ event }) {
  const [pending, setPending] = useState(event?.deadline_change_request || null);
  const [open, setOpen] = useState(false);
  const [deadline, setDeadline] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const id = event?._id || event?.id;
  const published =
    !event?.approval_status || event.approval_status === "published";

  if (!id || !published || !isRegistrationRequired(event)) return null;

  async function send() {
    if (busy) return;
    const problem =
      deadlineProblem(deadline, event.date, event.time) ||
      (reason.trim().length < 10 ? "Please give a reason (at least 10 characters)." : "");
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/events/${id}/deadline-change`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authToken() ? { Authorization: `Bearer ${authToken()}` } : {}),
        },
        body: JSON.stringify({
          registration_deadline: deadline,
          reason: reason.trim(),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(data, "Request failed."));
      setPending(data.deadline_change_request || { status: "pending", new_deadline: deadline, reason });
      setOpen(false);
      setDeadline("");
      setReason("");
    } catch (err) {
      setError(err.message || "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ margin: "14px 0", textAlign: "left" }}>
      <div>
        <strong>Registration deadline:</strong>{" "}
        {formatDeadline(event.registration_deadline) || "Not set"}
        {isRegistrationClosed(event) && " (closed)"}
      </div>

      {pending && pending.status === "pending" ? (
        <p style={{ margin: "6px 0 0", fontSize: "13px" }}>
          ⏳ Change to <strong>{formatDeadline(pending.new_deadline)}</strong>{" "}
          is waiting for Admin approval. The current deadline stays active
          until it is approved.
        </p>
      ) : open ? (
        <div style={{ marginTop: "8px", display: "grid", gap: "8px", maxWidth: "420px" }}>
          <label>
            New deadline (IST)
            <input
              type="datetime-local"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
          </label>
          <label>
            Reason (required)
            <textarea
              rows={3}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why does the deadline need to change?"
            />
          </label>
          {error && <div style={{ color: "#ef4444", fontSize: "13px" }}>{error}</div>}
          <div style={{ display: "flex", gap: "8px" }}>
            <button type="button" disabled={busy} onClick={send}>
              {busy ? "Sending..." : "Send to Admin"}
            </button>
            <button type="button" disabled={busy} onClick={() => { setOpen(false); setError(""); }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" style={{ marginTop: "6px" }} onClick={() => setOpen(true)}>
          Request deadline change
        </button>
      )}
    </div>
  );
}