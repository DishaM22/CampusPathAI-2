import React, { useEffect, useState } from "react";
import EventReport from "./EventReport";

const API_URL = "http://127.0.0.1:8000";
const token = () =>
  localStorage.getItem("token") || localStorage.getItem("access_token") || localStorage.getItem("jwt") || "";

/** Reports waiting for the logged-in Faculty / Admin; click one to review it. */
export default function PendingReports() {
  const [rows, setRows] = useState([]);
  const [open, setOpen] = useState(null);

  async function load() {
    try {
      const r = await fetch(`${API_URL}/api/reports/pending`, {
        headers: token() ? { Authorization: `Bearer ${token()}` } : {},
      });
      const d = await r.json().catch(() => ({}));
      setRows(r.ok ? d.reports || [] : []);
    } catch {
      setRows([]);
    }
  }
  useEffect(() => { load(); }, []);

  if (!rows.length) return null;
  return (
    <section style={{ margin: "16px 0", padding: 16, border: "1px solid rgba(148,163,184,0.35)", borderRadius: 12 }}>
      <h3 style={{ marginTop: 0 }}>Post-event reports waiting for you ({rows.length})</h3>
      {rows.map((r) => (
        <div key={r.event_id} style={{ padding: "6px 0" }}>
          <button
            type="button"
            onClick={() => setOpen(open === r.event_id ? null : r.event_id)}
            style={{ background: "none", border: 0, color: "inherit", cursor: "pointer", textDecoration: "underline", fontSize: 14 }}
          >
            {r.title} — v{r.version}, {r.submitted_by_name}
          </button>
          {open === r.event_id && (
            <div key={r.event_id} onClickCapture={() => setTimeout(load, 800)}>
              <EventReport event={{ _id: r.event_id }} />
            </div>
          )}
        </div>
      ))}
    </section>
  );
}