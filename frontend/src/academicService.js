// ============================================================
// ACADEMIC SERVICE  (promotion + history). New file.
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

const qs = (params) => {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.append(k, v);
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
};

const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body) });

export const fetchAcademicFilters = () => request("/api/admin/academic/filters");
export const fetchAcademicStudents = (filters = {}) =>
  request(`/api/admin/academic/students${qs(filters)}`);
export const previewPromotion = (body) => post("/api/admin/academic/promotion/preview", body);
export const applyPromotion = (body) => post("/api/admin/academic/promotion/apply", body);
export const fetchPromotionHistory = (params = {}) =>
  request(`/api/admin/academic/promotion/history${qs(params)}`);
export const correctPromotion = (body) => post("/api/admin/academic/promotion/correct", body);
export const changeStudentAcademic = (studentId, body) =>
  request(`/api/admin/academic/students/${encodeURIComponent(studentId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

// Make profiles show the official year. dry_run=true only counts what would change.
export const syncProfileYears = (body) => post("/api/admin/academic/sync-profile-years", body);