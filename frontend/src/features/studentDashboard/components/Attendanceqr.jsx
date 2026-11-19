import { useCallback, useEffect, useState } from "react";
import "./Attendanceqr.css";

// ============================================================
// ATTENDANCE QR (student side)
// ------------------------------------------------------------
// The student shows a QR code to the event organiser, who scans it to
// mark attendance. Everything comes from the QR system (/api/qr/...):
//
//   - events that need registration -> the Event Registration QR
//   - "No Registration Required" events -> the Event Attendance Pass
//
//   <AttendanceQRLauncher events={...} registrations={...} />
//        a floating "Attendance QR" button listing every QR / pass the
//        student has (already added to StudentDashboard.jsx)
//
//   <AttendanceQR eventId="..." eventTitle="..." onClose={...} />
//        the QR of one event in a pop-up
//
//   showAttendanceQR(eventId, title)
//        pops one event's QR up straight away (the launcher listens for it)
//
// The older plaintext-token QR (/api/events/{id}/attendance/qr) is no longer
// used here. The organiser scanner still accepts those old codes.
// ============================================================

const API_BASE = "http://127.0.0.1:8000";
const SHOW_EVENT_NAME = "campuspath:show-attendance-qr";

const KIND = {
  event_registration: "Registration QR",
  event_attendance_pass: "Attendance pass",
};

const STATUS = {
  active: "Ready to scan",
  used: "Attendance recorded",
  cancelled: "No longer valid",
  suspended: "On hold",
  revoked: "Revoked",
};

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/* The QR system answers {success:false, code, message}; FastAPI answers {detail}. */
async function readError(response) {
  let body = null;

  try {
    body = await response.json();
  } catch {
    body = null;
  }

  const detail = body?.message ?? body?.detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || JSON.stringify(item)).join("; ");
  }

  if (response.status === 401) {
    return "Please sign in again.";
  }

  return `Request failed (${response.status}).`;
}

async function getJson(path) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(),
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  return response.json();
}

/* The QR (registration QR or attendance pass) of ONE event. */
export function fetchAttendanceQR(eventId) {
  return getJson(`/api/qr/events/${encodeURIComponent(eventId)}`);
}

/* Pop the QR of an event up (used right after the student registers). */
export function showAttendanceQR(eventId, title = "") {
  window.dispatchEvent(
    new CustomEvent(SHOW_EVENT_NAME, { detail: { eventId, title } })
  );
}

const toDataUrl = (svg) =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

/* Scanners read a QR best when every square is a whole number of screen
   pixels, so size the picture as a whole multiple of the code's grid
   (the grid size is the SVG's viewBox), as close to `target` as possible. */
function qrPixelSize(svg, target = 260) {
  const match = /viewBox="0 0 (\d+) \d+"/.exec(svg || "");
  const units = match ? Number(match[1]) : 0;

  return units > 0
    ? Math.max(1, Math.floor(target / units)) * units
    : target;
}

const safeFileName = (title) =>
  String(title || "event")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40) || "event";

const whenText = (date, time) =>
  [date, time].filter(Boolean).join(" · ");

const errorText = (error, fallback) =>
  error instanceof TypeError
    ? "Unable to reach the server."
    : error.message || fallback;

// ============================================================
// THE QR CODE OF ONE EVENT
// ============================================================

/* Pass `item` (one entry of /api/qr/my-events) to show it without another
   request, or just an `eventId` to load it. */
export function AttendanceQRPass({ eventId, eventTitle, item = null }) {
  const [state, setState] = useState(
    item
      ? { status: "ready", data: item, error: "" }
      : { status: "loading", data: null, error: "" }
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (item) {
      setState({ status: "ready", data: item, error: "" });
      return undefined;
    }

    let cancelled = false;

    setState({ status: "loading", data: null, error: "" });

    fetchAttendanceQR(eventId)
      .then((data) => {
        if (!cancelled) {
          setState({ status: "ready", data, error: "" });
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setState({
            status: "error",
            data: null,
            error: errorText(error, "Could not load your QR code."),
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [eventId, item, attempt]);

  if (state.status === "loading") {
    return <p className="aqr-note">Preparing your QR code…</p>;
  }

  if (state.status === "error") {
    return (
      <div className="aqr-error" role="alert">
        <p>{state.error}</p>

        <button
          type="button"
          className="aqr-btn"
          onClick={() => setAttempt((count) => count + 1)}
        >
          Try again
        </button>
      </div>
    );
  }

  const { data } = state;
  const title = data.title || eventTitle || "Event";
  const picture = data.qr_svg ? toDataUrl(data.qr_svg) : "";
  const pictureSize = qrPixelSize(data.qr_svg);
  const usable = data.status === "active";
  const isPass = data.qr_type === "event_attendance_pass";

  return (
    <div className="aqr-pass">
      <h2 className="aqr-title">{title}</h2>

      {whenText(data.date, data.time) && (
        <p className="aqr-when">{whenText(data.date, data.time)}</p>
      )}

      <p className="aqr-note">
        {KIND[data.qr_type] || "QR"} · {STATUS[data.status] || data.status}
      </p>

      {picture ? (
        <>
          <div className="aqr-frame" style={usable ? undefined : { opacity: 0.35 }}>
            <img
              className="aqr-img"
              src={picture}
              alt={`${KIND[data.qr_type] || "QR"} for ${title}`}
              width={pictureSize}
              height={pictureSize}
            />
          </div>

          <a
            className="aqr-btn"
            href={picture}
            download={`attendance-qr-${safeFileName(title)}.svg`}
          >
            ⬇ Download QR
          </a>
        </>
      ) : (
        <p className="aqr-error">
          The QR picture could not be drawn. Close this and try again.
        </p>
      )}

      <p className="aqr-note">
        {isPass
          ? "No registration is needed for this event. Show this pass to the event organiser to mark your attendance. It is personal to you and works once."
          : "Show this QR code to the event organiser to mark your attendance. It is personal to you and works once."}
      </p>
    </div>
  );
}

// ============================================================
// POP-UP FOR ONE EVENT
// ============================================================

function useEscape(active, onClose) {
  useEffect(() => {
    if (!active) return undefined;

    const onKey = (event) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, onClose]);
}

export default function AttendanceQR({ eventId, eventTitle, onClose }) {
  useEscape(true, onClose);

  return (
    <div className="aqr-overlay" onClick={onClose}>
      <div
        className="aqr-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Attendance QR code"
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          className="aqr-close"
          aria-label="Close"
          onClick={onClose}
        >
          ×
        </button>

        <AttendanceQRPass eventId={eventId} eventTitle={eventTitle} />
      </div>
    </div>
  );
}

// ============================================================
// FLOATING BUTTON + LIST OF THE STUDENT'S QR CODES AND PASSES
// ============================================================

// `events` and `registrations` are still accepted so StudentDashboard.jsx
// needs no change, but the list now comes straight from the QR system.
export function AttendanceQRLauncher() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(null); // { id, title, item }
  const [list, setList] = useState({ status: "idle", items: [], error: "" });

  /* Always read fresh, so an event the student has just registered for (or a
     pass that has just been created) is there without reloading the page. */
  const loadList = useCallback(async () => {
    setList({ status: "loading", items: [], error: "" });

    try {
      const data = await getJson("/api/qr/my-events");

      setList({ status: "ready", items: data.items || [], error: "" });
    } catch (error) {
      setList({
        status: "error",
        items: [],
        error: errorText(error, "Could not load your QR codes."),
      });
    }
  }, []);

  const openPanel = () => {
    setActive(null);
    setOpen(true);
    loadList();
  };

  const closePanel = useCallback(() => {
    setOpen(false);
    setActive(null);
  }, []);

  // showAttendanceQR(eventId, title) from anywhere opens that event's QR
  useEffect(() => {
    const onShow = (event) => {
      const { eventId, title } = event.detail || {};

      if (eventId) {
        setActive({ id: String(eventId), title: title || "Event", item: null });
        setOpen(true);
      }
    };

    window.addEventListener(SHOW_EVENT_NAME, onShow);
    return () => window.removeEventListener(SHOW_EVENT_NAME, onShow);
  }, []);

  useEscape(open, closePanel);

  return (
    <>
      <button
        type="button"
        className="aqr-fab"
        onClick={openPanel}
        aria-label="Open my attendance QR codes"
      >
        🎟️ Attendance QR
      </button>

      {open && (
        <div className="aqr-overlay" onClick={closePanel}>
          <div
            className="aqr-modal"
            role="dialog"
            aria-modal="true"
            aria-label="My attendance QR codes"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className="aqr-close"
              aria-label="Close"
              onClick={closePanel}
            >
              ×
            </button>

            {active ? (
              <>
                <button
                  type="button"
                  className="aqr-back"
                  onClick={() => {
                    setActive(null);
                    loadList();
                  }}
                >
                  ← All my events
                </button>

                <AttendanceQRPass
                  eventId={active.id}
                  eventTitle={active.title}
                  item={active.item}
                />
              </>
            ) : (
              <>
                <h2 className="aqr-title">My attendance QR codes</h2>

                <p className="aqr-note">
                  Events you registered for have a registration QR. Events
                  that need no registration have an attendance pass. Show
                  the one you need to the organiser.
                </p>

                {list.status === "loading" && (
                  <p className="aqr-note">Loading your QR codes…</p>
                )}

                {list.status === "error" && (
                  <div className="aqr-error" role="alert">
                    <p>{list.error}</p>

                    <button
                      type="button"
                      className="aqr-btn"
                      onClick={loadList}
                    >
                      Try again
                    </button>
                  </div>
                )}

                {list.status === "ready" && list.items.length === 0 && (
                  <p className="aqr-empty">
                    You have no event QR codes yet. When you register for an
                    event, or an event that needs no registration is
                    published to you, its QR code appears here.
                  </p>
                )}

                {list.status === "ready" && list.items.length > 0 && (
                  <ul className="aqr-list">
                    {list.items.map((item) => (
                      <li
                        key={`${item.qr_type}-${item.event_id}`}
                        className="aqr-item"
                      >
                        <div className="aqr-item-text">
                          <strong>{item.title || "Event"}</strong>

                          <span>
                            {[
                              KIND[item.qr_type],
                              whenText(item.date, item.time),
                              item.status === "used" ? "Attended" : "",
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        </div>

                        <button
                          type="button"
                          className="aqr-btn"
                          onClick={() =>
                            setActive({
                              id: item.event_id,
                              title: item.title,
                              item,
                            })
                          }
                        >
                          Show QR
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}