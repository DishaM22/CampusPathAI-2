// ============================================================
// MODIFICATION SERVICE (published-event changes, versions, student notifications)
// New file. Does not change adminService.js / clubService.js.
// ============================================================

const API_BASE = "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const token = localStorage.getItem("token");
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(
      (typeof detail === "string" && detail) ||
        (Array.isArray(detail) && detail.map((d) => d?.msg || JSON.stringify(d)).join("; ")) ||
        data?.message ||
        `Request failed (${response.status})`
    );
  }
  return data;
}

const json = (method, body) => ({ method, body: JSON.stringify(body ?? {}) });
const enc = encodeURIComponent;

// ---- the full event (budget included for the owning club) used to pre-fill the form
export const fetchEventForEdit = (eventId) => request(`/api/events/${enc(eventId)}`);

// ---- organiser (Club Coordinator / Faculty)
export const requestModification = (eventId, { fields, proposed, reason }) =>
  request(`/api/events/${enc(eventId)}/modification-requests`, json("POST", { fields, proposed, reason }));
export const fetchMyModifications = () => request("/api/modifications/mine");
export const fetchModification = (id) => request(`/api/modifications/${enc(id)}`);
export const saveModificationDraft = (id, proposed) =>
  request(`/api/modifications/${enc(id)}/draft`, json("PUT", { proposed }));
export const submitModification = (id) =>
  request(`/api/modifications/${enc(id)}/submit`, { method: "POST" });
export const withdrawModification = (id) =>
  request(`/api/modifications/${enc(id)}/withdraw`, { method: "POST" });

// ---- Admin
export const fetchAdminModifications = (status = "pending_admin_review") =>
  request(`/api/admin/modifications?status=${enc(status)}`);
export const initialReviewModification = (id, decision, remarks = "") =>
  request(`/api/admin/modifications/${enc(id)}/initial-review`, json("POST", { decision, remarks }));

// Final stage: the SAME review endpoint every reviewer already uses.
export const reviewModificationDecision = (id, decision, remarks = "", shareReason = false) =>
  request(
    `/api/approvals/events/${enc(id)}/review`,
    json("POST", { decision, remarks, share_reason_with_students: !!shareReason })
  );

// ---- history
export const fetchEventVersions = (eventId) => request(`/api/events/${enc(eventId)}/versions`);

// ---- student notifications
export const fetchStudentNotifications = (limit = 50) =>
  request(`/api/student/notifications?limit=${limit}`);
export const markStudentNotificationRead = (id) =>
  request(`/api/student/notifications/${enc(id)}/read`, { method: "POST" });
export const markAllStudentNotificationsRead = () =>
  request("/api/student/notifications/read-all", { method: "POST" });
