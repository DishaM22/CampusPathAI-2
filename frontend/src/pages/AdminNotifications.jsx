import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import Sidebar from "../admin/components/Sidebar";

const API_BASE = "http://127.0.0.1:8000";

export default function AdminNotifications() {
  const navigate = useNavigate();

  const [notifications, setNotifications] = useState([]);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // ============================================================
  // LOAD NOTIFICATIONS
  // ============================================================

  const loadNotifications = async () => {
    try {
      setLoading(true);
      setError("");

      const token = localStorage.getItem("token");

      const response = await fetch(
        `${API_BASE}/api/admin/notifications`,
        {
          method: "GET",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const data = await response.json();

      console.log("NOTIFICATIONS RESPONSE:", data);

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            `Request failed (${response.status})`
        );
      }

      setNotifications(
        Array.isArray(data?.notifications)
          ? data.notifications
          : []
      );
    } catch (err) {
      console.error("NOTIFICATIONS ERROR:", err);

      setError(
        err?.message ||
          "Failed to load notifications."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadNotifications();
  }, []);

  // ============================================================
  // FILTER
  // ============================================================

  const filteredNotifications = notifications.filter(
    (notification) => {
      if (filter === "unread") {
        return !notification.is_read;
      }

      if (filter === "application") {
        return notification.type === "application";
      }

      if (filter === "event") {
        return notification.type === "event";
      }

      if (filter === "registration") {
        return notification.type === "registration";
      }

      return true;
    }
  );

  // ============================================================
  // COUNTS
  // ============================================================

  const unreadCount = notifications.filter(
    (notification) => !notification.is_read
  ).length;

  const applicationCount = notifications.filter(
    (notification) =>
      notification.type === "application"
  ).length;

  const eventCount = notifications.filter(
    (notification) =>
      notification.type === "event"
  ).length;

  // ============================================================
  // MARK ONE READ
  // ============================================================

  const markAsRead = async (id) => {
    try {
      const token = localStorage.getItem("token");

      const response = await fetch(
        `${API_BASE}/api/admin/notifications/${id}/read`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "Failed to mark notification as read."
        );
      }

      setNotifications((current) =>
        current.map((notification) =>
          notification.id === id
            ? {
                ...notification,
                is_read: true,
              }
            : notification
        )
      );
    } catch (err) {
      console.error("MARK READ ERROR:", err);

      alert(
        err?.message ||
          "Failed to mark notification as read."
      );
    }
  };

  // ============================================================
  // MARK ALL READ
  // ============================================================

  const markAllAsRead = async () => {
    if (unreadCount === 0) {
      return;
    }

    try {
      const token = localStorage.getItem("token");

      const response = await fetch(
        `${API_BASE}/api/admin/notifications/read-all`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "Failed to mark all notifications as read."
        );
      }

      setNotifications((current) =>
        current.map((notification) => ({
          ...notification,
          is_read: true,
        }))
      );
    } catch (err) {
      console.error("MARK ALL READ ERROR:", err);

      alert(
        err?.message ||
          "Failed to mark all notifications as read."
      );
    }
  };

  // ============================================================
  // DELETE
  // ============================================================

  const deleteNotification = async (id) => {
    const confirmed = window.confirm(
      "Delete this notification?"
    );

    if (!confirmed) {
      return;
    }

    try {
      const token = localStorage.getItem("token");

      const response = await fetch(
        `${API_BASE}/api/admin/notifications/${id}`,
        {
          method: "DELETE",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            data?.message ||
            "Failed to delete notification."
        );
      }

      setNotifications((current) =>
        current.filter(
          (notification) =>
            notification.id !== id
        )
      );
    } catch (err) {
      console.error("DELETE ERROR:", err);

      alert(
        err?.message ||
          "Failed to delete notification."
      );
    }
  };

  // ============================================================
  // OPEN NOTIFICATION
  // ============================================================

  const openNotification = async (notification) => {
    if (!notification.is_read) {
      await markAsRead(notification.id);
    }

    if (
      notification.type === "application" &&
      notification.related_id
    ) {
      navigate(
        `/admin/applications/${notification.related_id}`
      );
    }
  };

  // ============================================================
  // LOGOUT
  // ============================================================

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    localStorage.removeItem("userEmail");
    localStorage.removeItem("userRole");
    localStorage.removeItem("student_id");
    localStorage.removeItem("studentId");

    navigate("/");
  };

  // ============================================================
  // NAVIGATION
  // ============================================================

  const handleNavigate = (path) => {
    navigate(path);
  };

  // ============================================================
  // ICON
  // ============================================================

  const getIcon = (type) => {
    if (type === "application") {
      return "📥";
    }

    if (type === "event") {
      return "📅";
    }

    if (type === "registration") {
      return "📋";
    }

    return "🔔";
  };

  // ============================================================
  // DATE
  // ============================================================

  const formatDate = (value) => {
    if (!value) {
      return "";
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return String(value);
    }

    return date.toLocaleString();
  };

  // ============================================================
  // PAGE
  // ============================================================

  return (
    <div className="admin-page"
      style={{
        minHeight: "100vh",
        background: "var(--ap-page)",
        color: "var(--ap-text)",
      }}
    >
      {/* ======================================================
          SIDEBAR
      ====================================================== */}

      <Sidebar
        role="admin"
        orgName="Admin"
        activeKey="notifications"
        collapsed={collapsed}
        onToggleCollapsed={() =>
          setCollapsed((value) => !value)
        }
        onNavigate={handleNavigate}
        onLogout={handleLogout}
        mobileOpen={mobileOpen}
        onCloseMobile={() =>
          setMobileOpen(false)
        }
      />

      {/* ======================================================
          MAIN CONTENT
      ====================================================== */}

      <div
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          left: collapsed ? "80px" : "200px",
          overflowY: "auto",
          background: "var(--ap-page)",
          padding: "32px",
          boxSizing: "border-box",
        }}
      >
        {/* HEADER */}

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "28px",
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: "32px",
                color: "var(--ap-text)",
              }}
            >
              Notifications
            </h1>

            <p
              style={{
                margin: "8px 0 0",
                color: "var(--ap-muted)",
              }}
            >
              Stay updated with applications,
              events and registrations.
            </p>
          </div>

          <button
            onClick={loadNotifications}
            disabled={loading}
            style={{
              padding: "10px 18px",
              borderRadius: "8px",
              border: "1px solid var(--ap-border)",
              background: "var(--ap-card)",
              color: "var(--ap-text)",
              cursor: "pointer",
            }}
          >
            Refresh
          </button>
        </div>

        {/* SUMMARY CARDS */}

        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "repeat(4, minmax(0, 1fr))",
            gap: "16px",
            marginBottom: "24px",
          }}
        >
          <SummaryCard
            title="Total Notifications"
            value={notifications.length}
          />

          <SummaryCard
            title="Unread"
            value={unreadCount}
          />

          <SummaryCard
            title="Applications"
            value={applicationCount}
          />

          <SummaryCard
            title="Events"
            value={eventCount}
          />
        </div>

        {/* FILTER BAR */}

        <div
          style={{
            background: "var(--ap-card)",
            border: "1px solid var(--ap-border)",
            borderRadius: "10px",
            padding: "14px",
            marginBottom: "18px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "12px",
          }}
        >
          <div
            style={{
              display: "flex",
              gap: "8px",
              flexWrap: "wrap",
            }}
          >
            <FilterButton
              active={filter === "all"}
              onClick={() => setFilter("all")}
            >
              All
            </FilterButton>

            <FilterButton
              active={filter === "unread"}
              onClick={() => setFilter("unread")}
            >
              Unread
            </FilterButton>

            <FilterButton
              active={filter === "application"}
              onClick={() =>
                setFilter("application")
              }
            >
              Applications
            </FilterButton>

            <FilterButton
              active={filter === "event"}
              onClick={() => setFilter("event")}
            >
              Events
            </FilterButton>

            <FilterButton
              active={filter === "registration"}
              onClick={() =>
                setFilter("registration")
              }
            >
              Registrations
            </FilterButton>
          </div>

          <button
            onClick={markAllAsRead}
            disabled={unreadCount === 0}
            style={{
              padding: "9px 15px",
              borderRadius: "7px",
              border: "none",
              background:
                unreadCount === 0
                  ? "var(--ap-border)"
                  : "#2563eb",
              color: "#ffffff",
              cursor:
                unreadCount === 0
                  ? "not-allowed"
                  : "pointer",
            }}
          >
            Mark all as read
          </button>
        </div>

        {/* ======================================================
            NOTIFICATION CONTENT
        ====================================================== */}

        <div
          style={{
            background: "var(--ap-card)",
            border: "1px solid var(--ap-border)",
            borderRadius: "12px",
            overflow: "hidden",
            minHeight: "250px",
          }}
        >
          {/* LOADING */}

          {loading && (
            <div
              style={{
                padding: "80px 20px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: "40px" }}>
                ⏳
              </div>

              <h3>Loading notifications...</h3>

              <p
                style={{
                  color: "var(--ap-muted)",
                }}
              >
                Please wait.
              </p>
            </div>
          )}

          {/* ERROR */}

          {!loading && error && (
            <div
              style={{
                padding: "80px 20px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: "40px" }}>
                ⚠️
              </div>

              <h3
                style={{
                  color: "var(--ap-bad-fg)",
                }}
              >
                Unable to load notifications
              </h3>

              <p
                style={{
                  color: "var(--ap-muted)",
                }}
              >
                {error}
              </p>

              <button
                onClick={loadNotifications}
                style={{
                  padding: "10px 18px",
                  border: "none",
                  borderRadius: "7px",
                  background: "#2563eb",
                  color: "#ffffff",
                  cursor: "pointer",
                }}
              >
                Try Again
              </button>
            </div>
          )}

          {/* EMPTY */}

          {!loading &&
            !error &&
            filteredNotifications.length === 0 && (
              <div
                style={{
                  padding: "90px 20px",
                  textAlign: "center",
                }}
              >
                <div
                  style={{
                    fontSize: "48px",
                  }}
                >
                  🔔
                </div>

                <h3
                  style={{
                    color: "var(--ap-text)",
                  }}
                >
                  No notifications
                </h3>

                <p
                  style={{
                    color: "var(--ap-muted)",
                  }}
                >
                  There are no notifications
                  to display.
                </p>
              </div>
            )}

          {/* NOTIFICATIONS */}

          {!loading &&
            !error &&
            filteredNotifications.length > 0 && (
              <div>
                {filteredNotifications.map(
                  (notification) => (
                    <div
                      key={notification.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        borderBottom:
                          "1px solid var(--ap-border)",
                        background:
                          notification.is_read
                            ? "var(--ap-card)"
                            : "var(--ap-card)",
                      }}
                    >
                      {/* NOTIFICATION */}

                      <button
                        onClick={() =>
                          openNotification(
                            notification
                          )
                        }
                        style={{
                          flex: 1,
                          display: "flex",
                          alignItems:
                            "flex-start",
                          gap: "15px",
                          padding: "20px",
                          border: "none",
                          background:
                            "transparent",
                          color: "var(--ap-text)",
                          textAlign: "left",
                          cursor: "pointer",
                        }}
                      >
                        <div
                          style={{
                            width: "44px",
                            height: "44px",
                            minWidth: "44px",
                            borderRadius: "10px",
                            background: "var(--ap-card2)",
                            display: "flex",
                            alignItems:
                              "center",
                            justifyContent:
                              "center",
                            fontSize: "21px",
                          }}
                        >
                          {getIcon(
                            notification.type
                          )}
                        </div>

                        <div
                          style={{
                            flex: 1,
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              alignItems:
                                "center",
                              gap: "8px",
                              marginBottom: "6px",
                            }}
                          >
                            <strong
                              style={{
                                color: "var(--ap-text)",
                              }}
                            >
                              {notification.title ||
                                "Notification"}
                            </strong>

                            {!notification.is_read && (
                              <span
                                style={{
                                  background:
                                    "#2563eb",
                                  color:
                                    "#ffffff",
                                  padding:
                                    "3px 7px",
                                  borderRadius:
                                    "999px",
                                  fontSize:
                                    "10px",
                                  fontWeight:
                                    "700",
                                }}
                              >
                                NEW
                              </span>
                            )}
                          </div>

                          <div
                            style={{
                              color: "var(--ap-text)",
                              marginBottom: "7px",
                            }}
                          >
                            {notification.message ||
                              "No message available."}
                          </div>

                          <div
                            style={{
                              color: "var(--ap-faint)",
                              fontSize: "12px",
                            }}
                          >
                            {formatDate(
                              notification.created_at
                            )}
                          </div>
                        </div>
                      </button>

                      {/* ACTIONS */}

                      <div
                        style={{
                          display: "flex",
                          gap: "7px",
                          padding: "0 15px",
                        }}
                      >
                        {!notification.is_read && (
                          <button
                            onClick={() =>
                              markAsRead(
                                notification.id
                              )
                            }
                            style={{
                              width: "35px",
                              height: "35px",
                              border:
                                "1px solid var(--ap-border)",
                              borderRadius:
                                "7px",
                              background:
                                "var(--ap-card2)",
                              color:
                                "var(--ap-text)",
                              cursor:
                                "pointer",
                            }}
                            title="Mark as read"
                          >
                            ✓
                          </button>
                        )}

                        <button
                          onClick={() =>
                            deleteNotification(
                              notification.id
                            )
                          }
                          style={{
                            width: "35px",
                            height: "35px",
                            border:
                              "1px solid var(--ap-border)",
                            borderRadius:
                              "7px",
                            background:
                              "var(--ap-card2)",
                            color:
                              "var(--ap-text)",
                            cursor:
                              "pointer",
                          }}
                          title="Delete"
                        >
                          🗑
                        </button>
                      </div>
                    </div>
                  )
                )}
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SUMMARY CARD
// ============================================================

function SummaryCard({ title, value }) {
  return (
    <div
      style={{
        background: "var(--ap-card)",
        border: "1px solid var(--ap-border)",
        borderRadius: "10px",
        padding: "20px",
      }}
    >
      <div
        style={{
          color: "var(--ap-muted)",
          fontSize: "13px",
          marginBottom: "8px",
        }}
      >
        {title}
      </div>

      <div
        style={{
          color: "var(--ap-text)",
          fontSize: "28px",
          fontWeight: "700",
        }}
      >
        {value}
      </div>
    </div>
  );
}

// ============================================================
// FILTER BUTTON
// ============================================================

function FilterButton({
  active,
  onClick,
  children,
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "8px 14px",
        borderRadius: "7px",
        border: active
          ? "1px solid #6366f1"
          : "1px solid var(--ap-border)",
        background: active
          ? "var(--ap-acc-bg)"
          : "var(--ap-card)",
        color: "#ffffff",
        cursor: "pointer",
        fontWeight: "600",
      }}
    >
      {children}
    </button>
  );
}