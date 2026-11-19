import React, { useEffect, useState } from "react";
import { formatDeadline } from "./RegistrationRequirement";

const API_URL = "http://127.0.0.1:8000";

const token = () => localStorage.getItem("token") || "";

async function call(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail.map((d) => d.msg || String(d)).join(" ")
        : detail || "Request failed."
    );
  }
  return data;
}

/**
 * Admin: pending registration-deadline change requests (published events).
 * Approve -> new deadline applies, registered students are notified.
 * Reject  -> the original deadline stays, remarks are required.
 * Renders nothing when there is nothing waiting.
 */
export default function AdminDeadlineRequests({ onChanged }) {
  const [rows, setRows] = useState([]);
  const [remarks, setRemarks] = useState({});
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    try {
      const data = await call("/api/admin/deadline-requests");
      setRows(data.requests || []);
    } catch (err) {
      setRows([]);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function decide(row, decision) {
    if (busy) return;
    setBusy(row.event_id);
    setError("");
    setMessage("");
    try {
      const data = await call(
        `/api/admin/deadline-requests/${row.event_id}/review`,
        {
          method: "POST",
          body: JSON.stringify({
            decision,
            remarks: remarks[row.event_id] || "",
          }),
        }
      );
      setMessage(
        decision === "approved"
          ? `Deadline updated. ${data.students_notified} registered student(s) notified.`
          : "Request rejected."
      );
      await load();
      if (onChanged) onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (!rows.length && !message && !error) return null;

  const box = {
    background: "var(--ap-card)",
    border: "1px solid var(--ap-border)",
    borderRadius: "12px",
    padding: "16px",
    marginBottom: "22px",
    color: "var(--ap-text)",
  };
  const small = { color: "var(--ap-muted)", fontSize: "13px" };
  const button = {
    padding: "7px 14px",
    borderRadius: "7px",
    border: "1px solid var(--ap-border)",
    background: "transparent",
    color: "var(--ap-text)",
    cursor: "pointer",
  };

  return (
    <div style={box}>
      <h3 style={{ margin: "0 0 10px" }}>
        Registration deadline change requests ({rows.length})
      </h3>

      {message && <div style={{ marginBottom: "8px" }}>✓ {message}</div>}
      {error && (
        <div style={{ marginBottom: "8px", color: "var(--ap-bad-fg)" }}>{error}</div>
      )}

      {rows.map((row) => (
        <div
          key={row.event_id}
          style={{ borderTop: "1px solid var(--ap-border)", padding: "12px 0" }}
        >
          <strong>{row.title}</strong>
          <div style={small}>
            {row.organizer} · {row.date} {row.time} · {row.registered} registered
          </div>
          <div style={{ marginTop: "6px" }}>
            Current: <strong>{formatDeadline(row.current_deadline) || "not set"}</strong>
            {"  →  "}
            Requested: <strong>{formatDeadline(row.new_deadline)}</strong>
          </div>
          <div style={{ marginTop: "4px" }}>
            Reason: {row.reason}
          </div>
          <div style={small}>
            Requested by {row.requested_by_name} ({row.requested_by_role})
          </div>

          <textarea
            rows={2}
            placeholder="Remarks (required to reject, at least 10 characters)"
            value={remarks[row.event_id] || ""}
            onChange={(e) =>
              setRemarks({ ...remarks, [row.event_id]: e.target.value })
            }
            style={{ width: "100%", boxSizing: "border-box", margin: "8px 0" }}
          />

          <div style={{ display: "flex", gap: "8px" }}>
            <button
              type="button"
              style={button}
              disabled={busy === row.event_id}
              onClick={() => decide(row, "approved")}
            >
              Approve
            </button>
            <button
              type="button"
              style={button}
              disabled={busy === row.event_id}
              onClick={() => decide(row, "rejected")}
            >
              Reject
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}