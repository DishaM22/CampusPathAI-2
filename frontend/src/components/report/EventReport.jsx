import React, { useEffect, useRef, useState } from "react";

const API_URL = "http://127.0.0.1:8000";

const token = () =>
  localStorage.getItem("token") ||
  localStorage.getItem("access_token") ||
  localStorage.getItem("jwt") ||
  "";

const auth = () => (token() ? { Authorization: `Bearer ${token()}` } : {});

async function readError(response) {
  const data = await response.json().catch(() => ({}));
  const detail = data && data.detail;
  return Array.isArray(detail)
    ? detail.map((d) => d.msg || String(d)).join(" ")
    : detail || "Request failed.";
}

async function call(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...auth(),
    },
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

// Files are private: fetch with the login token, then open/save as a blob.
async function fetchBlob(eventId, file) {
  const response = await fetch(
    `${API_URL}/api/events/${eventId}/report/files/${file.file_id}`,
    { headers: auth() }
  );
  if (!response.ok) throw new Error(await readError(response));
  return response.blob();
}

const STATUS = {
  not_started: ["Not started", "#64748b"],
  draft: ["Draft", "#64748b"],
  pending_faculty: ["Waiting for Faculty", "#b45309"],
  pending_hod: ["Waiting for HOD", "#b45309"],
  pending_dean: ["Waiting for Dean", "#b45309"],
  pending_admin: ["Waiting for Admin", "#b45309"],
  approved: ["Approved", "#15803d"],
  rejected: ["Returned for correction", "#b91c1c"],
};

const line = "1px solid rgba(148, 163, 184, 0.35)";
const btn = {
  padding: "6px 12px",
  borderRadius: 8,
  border: line,
  background: "transparent",
  color: "inherit",
  cursor: "pointer",
  fontSize: 13,
};
const primary = { ...btn, background: "#2563eb", borderColor: "#2563eb", color: "#fff" };
const danger = { ...btn, color: "#b91c1c", borderColor: "#b91c1c" };

function when(value) {
  const millis = Date.parse(String(value || ""));
  return Number.isNaN(millis)
    ? ""
    : new Date(millis).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      });
}

const size = (bytes) =>
  bytes >= 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;

function FileRow({ eventId, file, onRemove, onReplace, disabled, setError, onPreview }) {
  const pick = useRef(null);

  async function open(download) {
    try {
      const blob = await fetchBlob(eventId, file);
      const url = URL.createObjectURL(blob);
      if (download || !file.previewable) {
        const a = document.createElement("a");
        a.href = url;
        a.download = file.name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
      } else {
        onPreview({ url, name: file.name, image: /\.(jpe?g|png|webp)$/i.test(file.ext) });
      }
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "6px 0", borderBottom: line }}>
      <span style={{ flex: 1, minWidth: 160, wordBreak: "break-all" }}>
        {file.name} <small style={{ opacity: 0.65 }}>({size(file.size)})</small>
      </span>
      {file.previewable && <button type="button" style={btn} onClick={() => open(false)}>Preview</button>}
      <button type="button" style={btn} onClick={() => open(true)}>Download</button>
      {onReplace && (
        <>
          <button type="button" style={btn} disabled={disabled} onClick={() => pick.current.click()}>Replace</button>
          <input
            ref={pick}
            type="file"
            hidden
            onChange={(e) => {
              const f = e.target.files[0];
              e.target.value = "";
              if (f) onReplace(f);
            }}
          />
        </>
      )}
      {onRemove && (
        <button type="button" style={danger} disabled={disabled} onClick={onRemove}>Remove</button>
      )}
    </div>
  );
}

/**
 * Post-Event Report (separate from the checklist). Published events only.
 * Organizers (Club Coordinator / Faculty): draft, upload, preview, replace,
 * remove, submit, resubmit. Faculty / HOD / Dean / Admin: view, download,
 * approve or reject with remarks. The server decides every permission.
 */
export default function EventReport({ event }) {
  const id = event?._id || event?.id;
  const published = !event?.approval_status || event.approval_status === "published";

  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [remarks, setRemarks] = useState("");
  const [preview, setPreview] = useState(null);
  const [showHistory, setShowHistory] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const mainPick = useRef(null);
  const attPick = useRef(null);

  async function load() {
    try {
      setError("");
      setData(await call(`/api/events/${id}/report`));
    } catch (err) {
      // 404 = nothing submitted yet and the viewer is a reviewer: just stay quiet
      if (!/No report has been submitted/.test(err.message)) setError(err.message);
      setData(null);
    }
  }

  useEffect(() => {
    if (id && published) load();
    return () => preview && URL.revokeObjectURL(preview.url);
  }, [id, published]);

  if (!id || !published) return null;
  if (!data && !error) return null;

  async function run(job) {
    setBusy(true);
    setError("");
    try {
      setData(await job());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const form = (file) => {
    const body = new FormData();
    body.append("file", file);
    return body;
  };

  const uploadMain = (file) =>
    run(() => call(`/api/events/${id}/report/main`, { method: "PUT", body: form(file) }));
  const addAttachment = (file) =>
    run(() => call(`/api/events/${id}/report/attachments`, { method: "POST", body: form(file) }));
  const removeFile = (fileId) =>
    run(() => call(`/api/events/${id}/report/files/${fileId}`, { method: "DELETE" }));
  const submit = () => run(() => call(`/api/events/${id}/report/submit`, { method: "POST" }));
  const review = (decision) =>
    run(async () => {
      const result = await call(`/api/events/${id}/report/review`, {
        method: "POST",
        body: JSON.stringify({ decision, remarks }),
      });
      setRemarks("");
      return result;
    });

  const [label, color] = STATUS[data?.report_status] || STATUS.not_started;
  const editable = data?.can_edit;
  const lastRejection =
    data?.report_status === "rejected"
      ? [...(data.submissions || [])].reverse().flatMap((s) => s.decisions || []).find((d) => d.decision === "rejected")
      : null;

  return (
    <section style={{ marginTop: 20, padding: 16, border: line, borderRadius: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: 0 }}>Post-Event Report</h3>
        {data && <strong style={{ color }}>{label}</strong>}
      </div>

      {error && <p style={{ color: "#b91c1c", margin: "8px 0" }}>{error}</p>}

      {data && (
        <>
          {lastRejection && (
            <p style={{ background: "rgba(185,28,28,0.1)", padding: 10, borderRadius: 8 }}>
              <strong>{String(lastRejection.role).toUpperCase()} remarks:</strong> {lastRejection.remarks}
            </p>
          )}

          <h4 style={{ marginBottom: 4 }}>Main report (required — PDF, DOC or DOCX)</h4>
          {data.main_report ? (
            <FileRow
              eventId={id}
              file={data.main_report}
              setError={setError}
              onPreview={setPreview}
              disabled={busy}
              onReplace={editable ? uploadMain : null}
              onRemove={editable ? () => removeFile(data.main_report.file_id) : null}
            />
          ) : editable ? (
            <>
              <button type="button" style={btn} disabled={busy} onClick={() => mainPick.current.click()}>
                Upload main report
              </button>
              <input
                ref={mainPick}
                type="file"
                accept=".pdf,.doc,.docx"
                hidden
                onChange={(e) => {
                  const f = e.target.files[0];
                  e.target.value = "";
                  if (f) uploadMain(f);
                }}
              />
            </>
          ) : (
            <p style={{ opacity: 0.7 }}>No main report.</p>
          )}

          <h4 style={{ marginBottom: 4 }}>Attachments (optional — photos and supporting documents)</h4>
          {data.attachments.length === 0 && <p style={{ opacity: 0.7, margin: "4px 0" }}>None.</p>}
          {data.attachments.map((file) => (
            <FileRow
              key={file.file_id}
              eventId={id}
              file={file}
              setError={setError}
              onPreview={setPreview}
              disabled={busy}
              onRemove={editable ? () => removeFile(file.file_id) : null}
            />
          ))}
          {editable && (
            <div style={{ marginTop: 8 }}>
              <button type="button" style={btn} disabled={busy} onClick={() => attPick.current.click()}>
                Add attachment
              </button>
              <input
                ref={attPick}
                type="file"
                hidden
                accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.jpg,.jpeg,.png,.webp"
                onChange={(e) => {
                  const f = e.target.files[0];
                  e.target.value = "";
                  if (f) addAttachment(f);
                }}
              />
              <small style={{ marginLeft: 8, opacity: 0.7 }}>
                Max {data.limits.attachment_mb} MB each, {data.limits.max_attachments} files, main report {data.limits.main_mb} MB.
              </small>
            </div>
          )}

          {editable && (
            <div style={{ marginTop: 14 }}>
              <button
                type="button"
                style={primary}
                disabled={busy || !data.main_report}
                onClick={() => setConfirming(true)}
              >
                {data.report_status === "rejected" ? "Resubmit for review" : "Submit for review"}
              </button>
              {!data.main_report && <small style={{ marginLeft: 8 }}>Upload the main report first.</small>}
            </div>
          )}

          {data.can_review && (
            <div style={{ marginTop: 14 }}>
              <textarea
                value={remarks}
                onChange={(e) => setRemarks(e.target.value)}
                placeholder="Remarks (required, at least 10 characters, when rejecting)"
                rows={3}
                maxLength={500}
                style={{ width: "100%", boxSizing: "border-box", padding: 8, borderRadius: 8, border: line }}
              />
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button type="button" style={primary} disabled={busy} onClick={() => review("approved")}>Approve</button>
                <button type="button" style={danger} disabled={busy} onClick={() => review("rejected")}>Reject</button>
              </div>
            </div>
          )}

          {data.submissions.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <button type="button" style={btn} onClick={() => setShowHistory(!showHistory)}>
                {showHistory ? "Hide" : "Show"} submissions & history
              </button>
              {showHistory && (
                <div style={{ marginTop: 8, fontSize: 13 }}>
                  {[...data.submissions].reverse().map((s) => (
                    <div key={s.version} style={{ padding: "8px 0", borderBottom: line }}>
                      <strong>Version {s.version}</strong> — {when(s.submitted_at)} by {s.submitted_by_name}
                      <div>
                        {[s.main_report, ...s.attachments].filter(Boolean).map((f) => (
                          <FileRow key={f.file_id} eventId={id} file={f} setError={setError} onPreview={setPreview} />
                        ))}
                      </div>
                      {(s.decisions || []).map((d, i) => (
                        <div key={i}>
                          {String(d.role).toUpperCase()} {d.decision} ({when(d.at)}, {d.by_name})
                          {d.remarks ? `: ${d.remarks}` : ""}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {confirming && (
        <div
          onClick={() => setConfirming(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: "#0f172a", color: "#e2e8f0", border: line, borderRadius: 14, padding: 22, width: "min(440px,100%)" }}
          >
            <h3 style={{ marginTop: 0 }}>Submit report for review?</h3>
            <p style={{ opacity: 0.85 }}>
              Once submitted, you cannot change the report until it is reviewed. If it is
              returned, you can correct it and resubmit.
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button type="button" style={btn} onClick={() => setConfirming(false)}>Cancel</button>
              <button
                type="button"
                style={primary}
                disabled={busy}
                onClick={() => { setConfirming(false); submit(); }}
              >
                Yes, submit
              </button>
            </div>
          </div>
        </div>
      )}

      {preview && (
        <div
          onClick={() => { URL.revokeObjectURL(preview.url); setPreview(null); }}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#fff", borderRadius: 12, width: "min(900px,100%)", height: "85vh", display: "flex", flexDirection: "column" }}>
            <div style={{ padding: 10, display: "flex", justifyContent: "space-between", color: "#111" }}>
              <strong style={{ wordBreak: "break-all" }}>{preview.name}</strong>
              <button type="button" style={{ ...btn, color: "#111" }} onClick={() => { URL.revokeObjectURL(preview.url); setPreview(null); }}>Close</button>
            </div>
            {preview.image ? (
              <img src={preview.url} alt={preview.name} style={{ flex: 1, minHeight: 0, objectFit: "contain" }} />
            ) : (
              <iframe title={preview.name} src={preview.url} style={{ flex: 1, border: 0 }} />
            )}
          </div>
        </div>
      )}
    </section>
  );
}