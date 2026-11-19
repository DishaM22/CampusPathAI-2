import React, { useEffect, useMemo, useState } from "react";
import PublishAudiencePicker, {
  EMPTY_AUDIENCE,
  audienceError,
} from "./PublishAudiencePicker";
import {
  fetchAudienceEvents,
  fetchEventAudience,
  saveEventAudience,
} from "../../audienceService";
import "./PublishAudiencePicker.css";

/**
 * Admin-only panel: who can see each PUBLISHED event, and a way to change it.
 * Changing the audience never deletes registrations or attendance records.
 */
export default function PublishedEventAudiences() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [onlyLegacy, setOnlyLegacy] = useState(false);
  const [notice, setNotice] = useState("");

  const [editing, setEditing] = useState(null); // { event, value, classes, students }
  const [opening, setOpening] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [modalError, setModalError] = useState("");

  async function load() {
    try {
      setLoading(true);
      setError("");
      const data = await fetchAudienceEvents();
      setEvents(data.events || []);
    } catch (e) {
      setError(e.message || "Could not load published events.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return events.filter(
      (e) =>
        (!onlyLegacy || e.is_legacy) &&
        (!q || `${e.title} ${e.organizer}`.toLowerCase().includes(q))
    );
  }, [events, search, onlyLegacy]);

  async function openEditor(event) {
    setOpening(event.id);
    setNotice("");
    setModalError("");
    try {
      const data = await fetchEventAudience(event.id);
      const a = data.audience;
      setEditing({
        event,
        value: a
          ? {
              type: a.type,
              departments: a.departments || [],
              class_ids: a.class_ids || [],
              student_ids: a.student_ids || [],
            }
          : { ...EMPTY_AUDIENCE },
        classes: (a && a.class_snapshot) || [],
        students: data.selected_students || [],
        history: data.history || [],
      });
      setConfirming(false);
    } catch (e) {
      setError(e.message || "Could not open this event's audience.");
    } finally {
      setOpening("");
    }
  }

  async function save() {
    setSaving(true);
    setModalError("");
    try {
      const r = await saveEventAudience(editing.event.id, editing.value);
      setNotice(
        `Audience updated for "${editing.event.title}": ${r.newly_eligible} student(s) gained access, ` +
          `${r.no_longer_eligible} lost access. ${r.eligible_students} student(s) can now see it. ` +
          `Existing registrations and attendance were kept.`
      );
      setEditing(null);
      await load();
    } catch (e) {
      setModalError(e.message || "Could not save the audience.");
      setConfirming(false);
    } finally {
      setSaving(false);
    }
  }

  const problem = editing ? audienceError(editing.value) : "";

  return (
    <>
      <div className="section-head">
        <h2>🎯 Published events — who can see them</h2>
      </div>

      {notice && <div className="sample-banner">{notice}</div>}

      <div className="filter-bar">
        <input
          className="field"
          placeholder="Search by event or organizer…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="pap-check" style={{ alignItems: "center" }}>
          <input
            type="checkbox"
            checked={onlyLegacy}
            onChange={(e) => setOnlyLegacy(e.target.checked)}
          />
          Only events with no audience set
        </label>
        <button className="btn-ghost" onClick={load}>
          Refresh
        </button>
      </div>

      {loading ? (
        <div className="empty-state">
          <h3>Loading published events…</h3>
        </div>
      ) : error ? (
        <div className="empty-state">
          <h3>Unable to load</h3>
          <p>{error}</p>
          <button className="btn-primary" onClick={load}>
            Try again
          </button>
        </div>
      ) : shown.length === 0 ? (
        <div className="empty-state">
          <h3>No published events found</h3>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Organizer</th>
                <th>Date</th>
                <th>Published to</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e) => (
                <tr key={e.id}>
                  <td>
                    <b>{e.title}</b>
                  </td>
                  <td>{e.organizer || "—"}</td>
                  <td>{e.date || "—"}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 280 }}>
                    {e.is_legacy ? (
                      <span className="status-badge status-pending">
                        Not set · visible to everyone
                      </span>
                    ) : (
                      <>
                        {e.summary}{" "}
                        <small style={{ color: "var(--text-faint)" }}>v{e.version}</small>
                      </>
                    )}
                  </td>
                  <td>
                    <button
                      className="btn-ghost small"
                      disabled={opening === e.id}
                      onClick={() => openEditor(e)}
                    >
                      {opening === e.id ? "Opening…" : e.is_legacy ? "Set audience" : "Change audience"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <div className="modal-overlay" onClick={() => !saving && setEditing(null)}>
          <div
            className="modal-panel pap-modal-wide"
            onClick={(ev) => ev.stopPropagation()}
          >
            <h2>Publish “{editing.event.title}” to</h2>

            <PublishAudiencePicker
              key={editing.event.id}
              value={editing.value}
              onChange={(value) => {
                setConfirming(false);
                setEditing((cur) => ({ ...cur, value }));
              }}
              disabled={saving}
              initialClasses={editing.classes}
              initialStudents={editing.students}
            />

            <p className="modal-message">
              Students who are no longer in the audience lose access to this event, its
              registration and its attendance QR. Past registrations and attendance records
              are kept.
            </p>

            {editing.history.length > 0 && (
              <details>
                <summary>Audience history ({editing.history.length})</summary>
                <ul style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
                  {[...editing.history].reverse().map((h, i) => (
                    <li key={i}>
                      v{h.version}: {h.previous} → {h.new}
                      {h.changed_by_name ? ` · ${h.changed_by_name}` : ""}
                      {h.changed_at ? ` · ${new Date(h.changed_at).toLocaleString()}` : ""}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {modalError && (
              <p className="modal-message" style={{ color: "var(--danger)" }}>
                {modalError}
              </p>
            )}

            <div className="modal-actions-right">
              <button
                className="btn-ghost"
                disabled={saving}
                onClick={() => setEditing(null)}
              >
                Cancel
              </button>
              {!confirming ? (
                <button
                  className="btn-primary"
                  disabled={!!problem}
                  title={problem}
                  onClick={() => setConfirming(true)}
                >
                  Review change
                </button>
              ) : (
                <button className="btn-primary" disabled={saving} onClick={save}>
                  {saving ? "Saving…" : "Confirm & apply"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}