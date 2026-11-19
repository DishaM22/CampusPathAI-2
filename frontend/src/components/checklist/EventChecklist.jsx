import React, { useEffect, useState } from "react";

const API_URL = "http://127.0.0.1:8000";

const token = () =>
  localStorage.getItem("token") ||
  localStorage.getItem("access_token") ||
  localStorage.getItem("jwt") ||
  "";

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

const line = "1px solid rgba(148, 163, 184, 0.35)";
const cell = { padding: "8px 10px", borderBottom: line, textAlign: "left", verticalAlign: "top" };

function when(value) {
  const millis = Date.parse(String(value || ""));
  return Number.isNaN(millis)
    ? ""
    : new Date(millis).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      });
}

/**
 * Event Checklist (the 17 activities). Shown only for PUBLISHED events.
 * Club Coordinator / Faculty edit it; HOD, Dean and Admin see it read-only
 * (the server decides: `can_edit` comes from the API, never from the page).
 */
export default function EventChecklist({ event }) {
  const id = event?._id || event?.id;
  const published =
    !event?.approval_status || event.approval_status === "published";

  const [data, setData] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  async function load() {
    try {
      setError("");
      const result = await call(`/api/events/${id}/checklist`);
      setData(result);
      setDrafts({});
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    if (id && published) load();
  }, [id, published]);

  if (!id || !published) return null;

  async function save(row) {
    const draft = drafts[row.key] || {};
    setBusy(row.key);
    setError("");
    setNote("");
    try {
      await call(`/api/events/${id}/checklist/${row.key}`, {
        method: "PUT",
        body: JSON.stringify({
          status: draft.status ?? row.status,
          challenges: draft.challenges ?? row.challenges,
        }),
      });
      setNote(`Saved: ${row.label}`);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  const change = (key, patch) =>
    setDrafts((current) => ({ ...current, [key]: { ...current[key], ...patch } }));

  return (
    <section style={{ margin: "18px 0", textAlign: "left" }}>
      <h3 style={{ margin: "0 0 6px" }}>Event Checklist</h3>

      {error && <div style={{ color: "#ef4444", margin: "6px 0" }}>{error}</div>}
      {note && <div style={{ margin: "6px 0" }}>✓ {note}</div>}
      {!data && !error && <div>Loading checklist...</div>}

      {data && (
        <>
          {!data.can_edit && (
            <p style={{ fontSize: "13px", opacity: 0.75, margin: "0 0 8px" }}>
              View only. Club Coordinators and Faculty update this checklist.
            </p>
          )}

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: "620px", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={cell}>#</th>
                  <th style={cell}>Activity</th>
                  <th style={cell}>Status</th>
                  <th style={cell}>Challenges (if any)</th>
                  {data.can_edit && <th style={cell}></th>}
                </tr>
              </thead>
              <tbody>
                {data.activities.map((row, index) => {
                  const draft = drafts[row.key] || {};
                  const status = draft.status ?? row.status;
                  const challenges = draft.challenges ?? row.challenges;
                  const dirty = status !== row.status || challenges !== row.challenges;

                  return (
                    <tr key={row.key}>
                      <td style={cell}>{index + 1}</td>
                      <td style={cell}>
                        {row.label}
                        {row.updated_at && (
                          <div style={{ fontSize: "11px", opacity: 0.65 }}>
                            Updated {when(row.updated_at)}
                            {row.updated_by_name ? ` by ${row.updated_by_name}` : ""}
                          </div>
                        )}
                      </td>
                      <td style={cell}>
                        {data.can_edit ? (
                          <select
                            aria-label={`${row.label} status`}
                            value={status}
                            onChange={(e) => change(row.key, { status: e.target.value })}
                          >
                            {row.options.map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        ) : (
                          row.status
                        )}
                      </td>
                      <td style={cell}>
                        {data.can_edit ? (
                          <input
                            type="text"
                            aria-label={`${row.label} challenges`}
                            maxLength={500}
                            value={challenges}
                            placeholder="Challenges, if any"
                            onChange={(e) => change(row.key, { challenges: e.target.value })}
                          />
                        ) : (
                          row.challenges || "—"
                        )}
                      </td>
                      {data.can_edit && (
                        <td style={cell}>
                          <button
                            type="button"
                            disabled={!dirty || busy === row.key}
                            onClick={() => save(row)}
                          >
                            {busy === row.key ? "Saving..." : "Save"}
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <button
            type="button"
            style={{ marginTop: "10px" }}
            onClick={() => setShowHistory((value) => !value)}
          >
            {showHistory ? "Hide" : "Show"} status history ({data.history.length})
          </button>

          {showHistory && (
            <ul style={{ margin: "8px 0 0", paddingLeft: "18px", fontSize: "13px" }}>
              {data.history.length === 0 && <li>No status changes yet.</li>}
              {data.history.map((entry, index) => (
                <li key={index}>
                  {entry.label}: {entry.from} → <strong>{entry.to}</strong>{" "}
                  ({entry.by_name || entry.role}, {when(entry.at)})
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}