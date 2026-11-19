// ============================================================
// AUDIENCE SERVICE  ("Publish Event To")
// New file: it does not change adminService.js.
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
    const message =
      (typeof detail === "string" && detail) ||
      (Array.isArray(detail) && detail.map((d) => d?.msg || JSON.stringify(d)).join("; ")) ||
      data?.message ||
      `Request failed (${response.status})`;
    throw new Error(message);
  }
  return data;
}

const qs = (params) => {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.append(k, v);
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
};

export const fetchAudienceOptions = () => request("/api/admin/audience/options");
export const fetchAudienceClasses = (filters = {}) =>
  request(`/api/admin/audience/classes${qs(filters)}`);
export const searchAudienceStudents = (q, extra = {}) =>
  request(`/api/admin/audience/students${qs({ q, ...extra })}`);
export const previewAudience = (audience) =>
  request("/api/admin/audience/preview", { method: "POST", body: JSON.stringify(audience) });
export const fetchAudienceEvents = () => request("/api/admin/audience/events");
export const fetchEventAudience = (id) =>
  request(`/api/admin/events/${encodeURIComponent(id)}/audience`);
export const saveEventAudience = (id, audience) =>
  request(`/api/admin/events/${encodeURIComponent(id)}/audience`, {
    method: "PUT",
    body: JSON.stringify(audience),
  });

// Final Admin approval: the same review endpoint, now carrying the audience.
export const reviewEventWithAudience = (id, decision, remarks = "", audience = null) =>
  request(`/api/approvals/events/${encodeURIComponent(id)}/review`, {
    method: "POST",
    body: JSON.stringify({ decision, remarks, ...(audience ? { audience } : {}) }),
  });