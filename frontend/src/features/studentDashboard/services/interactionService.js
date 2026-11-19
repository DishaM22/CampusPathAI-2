// interactionService.js
//
// PHASE A: tells the backend what the student SEES and DOES, so the
// recommender can learn from it later.
//
//   logView(eventId)            student opened an event's details
//   logNotInterested(eventId)   student dismissed an event
//   logImpressions(eventIds)    events that were shown on the dashboard
//
// Rules:
//  * Fire-and-forget. These functions NEVER throw and never block the UI.
//  * Registrations, saves and attendance are NOT sent from here. The server
//    records those itself, right after the real action succeeds.
//  * The student is identified by the login token, never by an id we send.

const API_ORIGIN = "http://127.0.0.1:8000";
const MAX_IDS_PER_REQUEST = 30;

// Remembers what was already sent during this page visit so quick
// re-renders do not create repeat requests. (The server also de-duplicates.)
const alreadySent = new Set();

async function post(path, body) {
  try {
    const token = localStorage.getItem("token");

    if (!token) return false;

    const response = await fetch(`${API_ORIGIN}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
      keepalive: true,
    });

    return response.ok;
  } catch {
    return false;
  }
}

export function logView(eventId, source = "event_details") {
  const id = String(eventId || "");
  const key = `view:${id}`;

  if (!id || alreadySent.has(key)) return;

  alreadySent.add(key);
  post("/api/interactions", { event_id: id, action: "view", source });
}

export function logNotInterested(eventId, source = "other") {
  const id = String(eventId || "");
  const key = `not_interested:${id}`;

  if (!id || alreadySent.has(key)) return;

  alreadySent.add(key);
  post("/api/interactions", { event_id: id, action: "not_interested", source });
}

export function logImpressions(eventIds, source = "dashboard_recommended") {
  const fresh = [...new Set((eventIds || []).map(String))].filter(
    (id) => id && !alreadySent.has(`impression:${source}:${id}`)
  );

  for (let i = 0; i < fresh.length; i += MAX_IDS_PER_REQUEST) {
    const chunk = fresh.slice(i, i + MAX_IDS_PER_REQUEST);

    chunk.forEach((id) => alreadySent.add(`impression:${source}:${id}`));
    post("/api/interactions/impressions", { event_ids: chunk, source });
  }
}