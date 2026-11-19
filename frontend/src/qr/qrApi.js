// Calls to the QR backend (/api/qr/...). Same origin and token key as the rest of the app.
const API_BASE = "http://127.0.0.1:8000";
const TOKEN_KEY = "token"; // the JWT saved by Login.jsx / Register.jsx

async function request(path) {
  const token = localStorage.getItem(TOKEN_KEY);
  let response;
  try {
    response = await fetch(`${API_BASE}/api/qr${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw Object.assign(new Error("Cannot reach the server. Check your connection."), { code: "NETWORK" });
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body.message || (typeof body.detail === "string" ? body.detail : "Something went wrong.");
    throw Object.assign(new Error(message), { code: body.code || String(response.status) });
  }
  return body;
}

export const fetchIdentityQr = () => request("/identity");
export const fetchMyEventQrs = () => request("/my-events");
export const fetchEventQr = (eventId) => request(`/events/${encodeURIComponent(eventId)}`);