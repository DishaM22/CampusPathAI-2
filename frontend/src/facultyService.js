// ============================================================
// FACULTY SERVICE  (all Faculty Dashboard API calls live here)
// ============================================================
// Real endpoints only. Nothing in this file returns mock data.
// Backend routes (main.py):
//   GET /api/faculty/dashboard
//   GET /api/faculty/events      (search / filter / sort / paging)
//   GET /api/faculty/analytics
// ============================================================

export const API_BASE = "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function getToken() {
  return (
    localStorage.getItem("token") ||
    localStorage.getItem("access_token") ||
    ""
  );
}

async function request(path, options = {}) {
  const token = getToken();

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiError(
      "Cannot reach the server. Check that the backend is running.",
      0
    );
  }

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail = data?.detail || data?.message;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : `Request failed (${response.status})`,
      response.status
    );
  }

  return data;
}

export function fetchFacultyDashboard() {
  return request("/api/faculty/dashboard");
}

export function fetchFacultyEvents(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.append(key, value);
    }
  });
  const qs = query.toString();
  return request(`/api/faculty/events${qs ? `?${qs}` : ""}`);
}

export function fetchFacultyAnalytics() {
  return request("/api/faculty/analytics");
}

export function clearSession() {
  [
    "token",
    "access_token",
    "user",
    "userEmail",
    "userRole",
    "student_id",
  ].forEach((key) => localStorage.removeItem(key));
}

// ------------------------------------------------------------
// EVENTS (shared club events: same documents coordinators use)
// ------------------------------------------------------------

export function createEvent(payload) {
  return request("/api/events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateEvent(eventId, payload) {
  return request(`/api/events/${encodeURIComponent(eventId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// Multipart uploads: no Content-Type header, the browser adds it.
async function upload(path, file, fieldName, label) {
  const token = getToken();
  const body = new FormData();
  body.append("file", file);

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body,
    });
  } catch {
    throw new ApiError("Cannot reach the server.", 0);
  }

  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(
      `${label} upload failed: ${
        typeof result?.detail === "string" ? result.detail : response.status
      }`,
      response.status
    );
  }
  if (!result[fieldName]) {
    throw new ApiError(`${label} upload did not return a file address.`, 500);
  }
  return result[fieldName];
}

export const uploadPoster = (file) =>
  upload("/api/events/poster", file, "poster_url", "Poster");

export function fileUrl(url) {
  if (!url) return "";
  const text = String(url);
  if (/^(https?:|blob:)/i.test(text)) return text;
  return `${API_BASE}/${text.replace(/^\//, "")}`;
}

// ------------------------------------------------------------
// REVIEWS (Faculty decision on coordinator submissions)
// decision: "approved" (forward to HOD) | "changes" | "rejected"
// ------------------------------------------------------------

export function reviewEvent(eventId, decision, remarks = "") {
  return request(
    `/api/approvals/events/${encodeURIComponent(eventId)}/review`,
    {
      method: "POST",
      body: JSON.stringify({ decision, remarks }),
    }
  );
}

// ------------------------------------------------------------
// NOTIFICATIONS
// ------------------------------------------------------------

export function fetchFacultyNotifications() {
  return request("/api/faculty/notifications");
}

export function markFacultyNotificationRead(id) {
  return request(`/api/faculty/notifications/${encodeURIComponent(id)}/read`, {
    method: "POST",
  });
}

export function markAllFacultyNotificationsRead() {
  return request("/api/faculty/notifications/read-all", { method: "POST" });
}

// ------------------------------------------------------------
// REVIEW HISTORY  (decisions + edits across all club events)
// ------------------------------------------------------------

export function fetchFacultyReviewHistory(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.append(key, value);
    }
  });
  const qs = query.toString();
  return request(`/api/faculty/review-history${qs ? `?${qs}` : ""}`);
}