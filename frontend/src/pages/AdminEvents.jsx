import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import Sidebar from "../admin/components/Sidebar";
import BudgetBreakupForm from "../components/budget/BudgetBreakupForm";
import {
  breakupFromEvent,
  hasSavedBreakup,
} from "../components/budget/budgetUtils";
import {
  formatDeadline,
  isRegistrationRequired,
  registrationLabel,
} from "../components/registration/RegistrationRequirement";
import AdminDeadlineRequests from "../components/registration/AdminDeadlineRequests";
import EventChecklist from "../components/checklist/EventChecklist";
import EventReport from "../components/report/EventReport";
import PendingReports from "../components/report/PendingReports";

const API_BASE = "http://127.0.0.1:8000";

export default function AdminEvents() {
  const navigate = useNavigate();

  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [selectedEvent, setSelectedEvent] = useState(null);

  // Budget is private: the public list never carries it, so it is fetched
  // for the opened event with the admin's token (view only).
  const [budgetInfo, setBudgetInfo] = useState(null);
  const selectedId = selectedEvent?.id || selectedEvent?._id || null;

  useEffect(() => {
    loadEvents();
  }, []);

  useEffect(() => {
    setBudgetInfo(null);
    if (!selectedId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const token = localStorage.getItem("token");
        const res = await fetch(`${API_BASE}/api/events/${selectedId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) {
          setBudgetInfo({
            breakup: data.budget_breakup || null,
            total: data.budget ?? null,
          });
        }
      } catch (err) {
        console.error("BUDGET LOAD ERROR:", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function loadEvents() {
    try {
      setLoading(true);
      setError("");

      const response = await fetch(
        `${API_BASE}/api/events`
      );

      if (!response.ok) {
        throw new Error(
          `Failed to load events (${response.status})`
        );
      }

      const data = await response.json();

      console.log("EVENTS FROM DATABASE:", data);

      if (Array.isArray(data)) {
        setEvents(data);
      } else if (Array.isArray(data.events)) {
        setEvents(data.events);
      } else {
        setEvents([]);
      }
    } catch (err) {
      console.error("EVENT LOAD ERROR:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const filteredEvents = events.filter((event) => {
    const searchText = search.toLowerCase();

    return (
      String(event.title || "")
        .toLowerCase()
        .includes(searchText) ||
      String(event.category || "")
        .toLowerCase()
        .includes(searchText) ||
      String(event.organizer || "")
        .toLowerCase()
        .includes(searchText) ||
      String(event.location || "")
        .toLowerCase()
        .includes(searchText)
    );
  });

  const publishedCount = events.filter(
    (event) =>
      String(
        event.status || "published"
      ).toLowerCase() === "published"
  ).length;

  const registrationCount = events.reduce(
    (total, event) =>
      total +
      Number(
        event.registered ||
          event.registration_count ||
          0
      ),
    0
  );

  function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    localStorage.removeItem("userEmail");
    localStorage.removeItem("userRole");

    navigate("/");
  }

  return (
    <div className="admin-page">
      {/* SIDEBAR */}
      <Sidebar
        role="admin"
        orgName="Admin"
        activeKey="events"
        onNavigate={navigate}
        onLogout={logout}
      />

      {/* =====================================================
          EVENTS CONTENT
          ===================================================== */}

      <div
        style={{
          position: "fixed",
          left: "195px",
          top: "0",
          right: "0",
          bottom: "0",
          overflowY: "auto",
          background: "var(--ap-page)",
          color: "var(--ap-text)",
          padding: "35px",
          boxSizing: "border-box",
          zIndex: 100,
        }}
      >

        {/* HEADER */}

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "28px",
          }}
        >
          <div>
            <h1
              style={{
                margin: "0 0 7px 0",
                fontSize: "28px",
                fontWeight: "700",
                color: "var(--ap-text)",
              }}
            >
              Events
            </h1>

            <p
              style={{
                margin: 0,
                color: "var(--ap-muted)",
                fontSize: "14px",
              }}
            >
              Manage and review all
              CampusPath AI events.
            </p>
          </div>

          <button
            onClick={loadEvents}
            style={{
              background: "var(--ap-card)",
              border: "1px solid var(--ap-border)",
              color: "var(--ap-text)",
              borderRadius: "8px",
              padding: "10px 16px",
              cursor: "pointer",
            }}
          >
            ↻ Refresh
          </button>
        </div>

        {/* =====================================================
            STATS
            ===================================================== */}

        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "repeat(3, minmax(0, 1fr))",
            gap: "15px",
            marginBottom: "22px",
          }}
        >

          <StatCard
            title="Total Events"
            value={events.length}
          />

          <StatCard
            title="Published Events"
            value={publishedCount}
          />

          <StatCard
            title="Registrations"
            value={registrationCount}
          />

        </div>

        {/* Pending registration-deadline change requests (Admin approval) */}
        <AdminDeadlineRequests onChanged={loadEvents} />
        <PendingReports />

        {/* =====================================================
            MAIN EVENTS CARD
            ===================================================== */}

        <div
          style={{
            background: "var(--ap-card)",
            border: "1px solid var(--ap-border)",
            borderRadius: "12px",
            overflow: "hidden",
            width: "100%",
            boxSizing: "border-box",
          }}
        >

          {/* SEARCH */}

          <div
            style={{
              padding: "16px",
              borderBottom:
                "1px solid var(--ap-border)",
            }}
          >
            <input
              value={search}
              onChange={(e) =>
                setSearch(e.target.value)
              }
              placeholder="Search by event, organizer, category, location..."
              style={{
                width: "100%",
                height: "42px",
                boxSizing: "border-box",
                padding: "0 13px",
                borderRadius: "7px",
                border:
                  "1px solid var(--ap-border)",
                background: "var(--ap-card2)",
                color: "var(--ap-text)",
                outline: "none",
                fontSize: "13px",
              }}
            />
          </div>

          {/* LOADING */}

          {loading && (
            <div
              style={{
                padding: "60px",
                textAlign: "center",
                color: "var(--ap-muted)",
              }}
            >
              Loading events...
            </div>
          )}

          {/* ERROR */}

          {!loading && error && (
            <div
              style={{
                padding: "60px",
                textAlign: "center",
                color: "var(--ap-bad-fg)",
              }}
            >
              {error}
            </div>
          )}

          {/* EMPTY */}

          {!loading &&
            !error &&
            filteredEvents.length === 0 && (
              <div
                style={{
                  padding: "60px",
                  textAlign: "center",
                  color: "var(--ap-muted)",
                }}
              >
                No events found.
              </div>
            )}

          {/* EVENTS TABLE */}

          {!loading &&
            !error &&
            filteredEvents.length > 0 && (
              <div
                style={{
                  width: "100%",
                  overflowX: "auto",
                }}
              >
                <table
                  style={{
                    width: "100%",
                    minWidth: "900px",
                    borderCollapse:
                      "collapse",
                  }}
                >

                  <thead>
                    <tr>
                      <th style={headerStyle}>
                        EVENT
                      </th>

                      <th style={headerStyle}>
                        CATEGORY
                      </th>

                      <th style={headerStyle}>
                        ORGANIZER
                      </th>

                      <th style={headerStyle}>
                        DATE
                      </th>

                      <th style={headerStyle}>
                        LOCATION
                      </th>

                      <th style={headerStyle}>
                        STATUS
                      </th>

                      <th style={headerStyle}>
                        ACTION
                      </th>
                    </tr>
                  </thead>

                  <tbody>
                    {filteredEvents.map(
                      (event, index) => (
                        <tr
                          key={
                            event._id ||
                            event.id ||
                            index
                          }
                        >

                          <td style={cellStyle}>
                            <div
                              style={{
                                color:
                                  "var(--ap-text)",
                                fontWeight:
                                  "600",
                              }}
                            >
                              {event.title ||
                                "Untitled Event"}
                            </div>

                            {event.description && (
                              <div
                                style={{
                                  color:
                                    "var(--ap-faint)",
                                  fontSize:
                                    "11px",
                                  marginTop:
                                    "5px",
                                  maxWidth:
                                    "280px",
                                  overflow:
                                    "hidden",
                                  textOverflow:
                                    "ellipsis",
                                  whiteSpace:
                                    "nowrap",
                                }}
                              >
                                {
                                  event.description
                                }
                              </div>
                            )}
                          </td>

                          <td style={cellStyle}>
                            {event.category ||
                              "Other"}
                          </td>

                          <td style={cellStyle}>
                            {event.organizer ||
                              "—"}
                          </td>

                          <td style={cellStyle}>
                            {event.date ||
                              "—"}

                            {event.time && (
                              <div
                                style={{
                                  marginTop:
                                    "4px",
                                  color:
                                    "var(--ap-faint)",
                                  fontSize:
                                    "11px",
                                }}
                              >
                                {event.time}
                              </div>
                            )}
                          </td>

                          <td style={cellStyle}>
                            {event.location ||
                              "—"}
                          </td>

                          <td style={cellStyle}>
                            <span
                              style={{
                                display:
                                  "inline-block",
                                padding:
                                  "5px 9px",
                                borderRadius:
                                  "20px",
                                background:
                                  "var(--ap-ok-bg)",
                                color:
                                  "var(--ap-ok-fg)",
                                fontSize:
                                  "10px",
                                fontWeight:
                                  "700",
                              }}
                            >
                              {String(
                                event.status ||
                                  "PUBLISHED"
                              ).toUpperCase()}
                            </span>
                          </td>

                          <td style={cellStyle}>
                            <button
                              onClick={() =>
                                setSelectedEvent(
                                  event
                                )
                              }
                              style={{
                                padding:
                                  "7px 12px",
                                borderRadius:
                                  "7px",
                                border:
                                  "1px solid var(--ap-border)",
                                background:
                                  "transparent",
                                color:
                                  "var(--ap-acc-fg)",
                                cursor:
                                  "pointer",
                              }}
                            >
                              View
                            </button>
                          </td>

                        </tr>
                      )
                    )}
                  </tbody>

                </table>
              </div>
            )}

        </div>

      </div>

      {/* =====================================================
          EVENT DETAILS MODAL
          ===================================================== */}

      {selectedEvent && (
        <div
          onClick={() =>
            setSelectedEvent(null)
          }
          style={{
            position: "fixed",
            inset: 0,
            background:
              "rgba(0,0,0,0.7)",
            zIndex: 10000,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            padding: "20px",
          }}
        >

          <div
            onClick={(e) =>
              e.stopPropagation()
            }
            style={{
              width: "650px",
              maxWidth: "100%",
              maxHeight: "85vh",
              overflowY: "auto",
              background: "var(--ap-card)",
              border:
                "1px solid var(--ap-border)",
              borderRadius: "12px",
              padding: "25px",
              boxSizing: "border-box",
            }}
          >

            <div
              style={{
                display: "flex",
                justifyContent:
                  "space-between",
                alignItems: "flex-start",
                marginBottom: "25px",
              }}
            >
              <div>
                <h2
                  style={{
                    margin: 0,
                    color: "var(--ap-text)",
                  }}
                >
                  {selectedEvent.title ||
                    "Event Details"}
                </h2>

                <p
                  style={{
                    color: "var(--ap-muted)",
                    marginTop: "6px",
                  }}
                >
                  Event Details
                </p>
              </div>

              <button
                onClick={() =>
                  setSelectedEvent(
                    null
                  )
                }
                style={{
                  border: 0,
                  background:
                    "transparent",
                  color: "var(--ap-muted)",
                  fontSize: "26px",
                  cursor: "pointer",
                }}
              >
                ×
              </button>
            </div>

            <Detail
              label="Category"
              value={
                selectedEvent.category
              }
            />

            <Detail
              label="Organizer"
              value={
                selectedEvent.organizer
              }
            />

            <Detail
              label="Date"
              value={selectedEvent.date}
            />

            <Detail
              label="Time"
              value={selectedEvent.time}
            />

            <Detail
              label="Location"
              value={
                selectedEvent.location
              }
            />

            <Detail
              label="Registration"
              value={registrationLabel(
                selectedEvent
              )}
            />

            {isRegistrationRequired(selectedEvent) &&
              selectedEvent.registration_deadline && (
                <Detail
                  label="Registration deadline"
                  value={formatDeadline(
                    selectedEvent.registration_deadline
                  )}
                />
              )}

            <Detail
              label="Description"
              value={
                selectedEvent.description
              }
            />

            {/* Event Checklist: view only for Admin (the server enforces it) */}
            {selectedEvent.approval_status === "published" && (
              <div style={{ marginTop: "20px" }}>
                <EventChecklist event={selectedEvent} />
                <EventReport event={selectedEvent} />
              </div>
            )}

            {budgetInfo && hasSavedBreakup(budgetInfo.breakup) && (
              <div
                style={{
                  marginTop: "20px",
                  "--bbf-text": "var(--ap-text)",
                  "--bbf-border": "var(--ap-border)",
                }}
              >
                <BudgetBreakupForm
                  value={breakupFromEvent(budgetInfo.breakup)}
                  readOnly
                  idPrefix="admin-budget"
                />
              </div>
            )}

            {budgetInfo &&
              !hasSavedBreakup(budgetInfo.breakup) &&
              budgetInfo.total != null && (
                <Detail
                  label="Budget (older event, single figure)"
                  value={`₹ ${Number(budgetInfo.total).toLocaleString("en-IN")}`}
                />
              )}

          </div>

        </div>
      )}
    </div>
  );
}

function StatCard({ title, value }) {
  return (
    <div
      style={{
        background: "var(--ap-card)",
        border:
          "1px solid var(--ap-border)",
        borderRadius: "10px",
        padding: "20px",
      }}
    >
      <div
        style={{
          color: "var(--ap-muted)",
          fontSize: "13px",
        }}
      >
        {title}
      </div>

      <div
        style={{
          marginTop: "8px",
          fontSize: "27px",
          fontWeight: "700",
          color: "var(--ap-text)",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <div
      style={{
        padding: "13px",
        marginBottom: "10px",
        border:
          "1px solid var(--ap-border)",
        borderRadius: "8px",
        background: "var(--ap-card2)",
      }}
    >
      <div
        style={{
          color: "var(--ap-faint)",
          fontSize: "10px",
          textTransform: "uppercase",
          marginBottom: "5px",
        }}
      >
        {label}
      </div>

      <div
        style={{
          color: "var(--ap-text)",
          fontSize: "13px",
        }}
      >
        {value || "—"}
      </div>
    </div>
  );
}

const headerStyle = {
  padding: "13px 16px",
  textAlign: "left",
  background: "var(--ap-card)",
  borderBottom:
    "1px solid var(--ap-border)",
  color: "var(--ap-muted)",
  fontSize: "11px",
};

const cellStyle = {
  padding: "16px",
  borderBottom:
    "1px solid var(--ap-border)",
  color: "var(--ap-text)",
  fontSize: "13px",
};