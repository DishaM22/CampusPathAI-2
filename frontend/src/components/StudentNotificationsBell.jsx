import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchStudentNotifications,
  markStudentNotificationRead,
  markAllStudentNotificationsRead,
} from "../modificationService";
import "./modifications/modifications.css";

/**
 * Student notification bell: event changes (new date/time/venue/resource person)
 * and events newly opened to the student. Only the student's OWN notifications.
 *   <StudentNotificationsBell />
 */
export default function StudentNotificationsBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState("");
  const box = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await fetchStudentNotifications(30);
      setItems(d.notifications || []);
      setUnread(d.unread || 0);
      setError("");
    } catch (e) {
      setError(e.message || "Could not load notifications.");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  async function read(n) {
    if (n.is_read) return;
    try {
      await markStudentNotificationRead(n.id);
      setItems((cur) => cur.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)));
      setUnread((u) => Math.max(0, u - 1));
    } catch { /* ignore */ }
  }

  async function readAll() {
    try {
      await markAllStudentNotificationsRead();
      setItems((cur) => cur.map((x) => ({ ...x, is_read: true })));
      setUnread(0);
    } catch { /* ignore */ }
  }

  return (
    <div className="mod-bell" ref={box}>
      <button type="button" className="mod-bell-btn" aria-label="Notifications"
        onClick={() => { setOpen((o) => !o); if (!open) load(); }}>
        🔔{unread > 0 && <span className="mod-bell-count">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="mod-bell-panel">
          <div className="mod-row" style={{ justifyContent: "space-between", padding: "4px 6px" }}>
            <b>Notifications</b>
            <button type="button" className="btn-ghost small" disabled={unread === 0} onClick={readAll}>Mark all read</button>
          </div>
          {error && <p className="mod-error" style={{ padding: 6 }}>{error}</p>}
          {!error && items.length === 0 && <p className="mod-note" style={{ padding: 8 }}>No notifications yet.</p>}
          {items.map((n) => (
            <div key={n.id} className={`mod-bell-item ${n.is_read ? "" : "unread"}`} onClick={() => read(n)}>
              <b>{n.title}</b>
              <span>{n.message}</span>
              <small>{n.created_at ? new Date(n.created_at).toLocaleString() : ""}</small>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}