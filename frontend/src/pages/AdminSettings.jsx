import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import Sidebar from "../admin/components/Sidebar";

export default function AdminSettings() {
  const navigate = useNavigate();

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const [adminName, setAdminName] = useState("Administrator");
  const [adminEmail, setAdminEmail] = useState("");

  const [applicationNotifications, setApplicationNotifications] =
    useState(true);

  const [eventNotifications, setEventNotifications] =
    useState(true);

  const [registrationNotifications, setRegistrationNotifications] =
    useState(true);

  const [saved, setSaved] = useState(false);
  // Start from the theme saved last time, so coming back to this page
  // does not reset it to dark.
  const [theme, setTheme] = useState(() => {
    try {
      const saved = JSON.parse(
        localStorage.getItem("campusPathAdminSettings") || "{}"
      );
      return saved.theme === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });

  // ============================================================
  // LOAD ADMIN INFORMATION
  // ============================================================

  useEffect(() => {
    try {
      const storedUser = localStorage.getItem("user");

      if (storedUser) {
        const user = JSON.parse(storedUser);

        setAdminName(
          user?.name ||
            user?.full_name ||
            user?.fullName ||
            "Administrator"
        );

        setAdminEmail(
          user?.email || ""
        );
      }

      const storedSettings =
        localStorage.getItem(
          "campusPathAdminSettings"
        );

      if (storedSettings) {
        const settings =
          JSON.parse(storedSettings);

        setApplicationNotifications(
          settings.applicationNotifications ?? true
        );

        setEventNotifications(
          settings.eventNotifications ?? true
        );

        setRegistrationNotifications(
          settings.registrationNotifications ?? true
        );

        setTheme(settings.theme || "dark");
      }
    } catch (error) {
      console.error(
        "Failed to load admin settings:",
        error
      );
    }
  }, []);

  // Apply the theme to the whole admin area and remember it right away
  // (no need to press Save for the theme to stick).
  useEffect(() => {
    document.documentElement.setAttribute("data-admin-theme", theme);

    try {
      const stored = JSON.parse(
        localStorage.getItem("campusPathAdminSettings") || "{}"
      );

      localStorage.setItem(
        "campusPathAdminSettings",
        JSON.stringify({ ...stored, theme })
      );
    } catch (error) {
      console.error("Failed to save theme:", error);
    }
  }, [theme]);

  // ============================================================
  // SAVE SETTINGS
  // ============================================================

  const handleSaveSettings = () => {
    const settings = {
      applicationNotifications,
      eventNotifications,
      registrationNotifications,
      theme,
    };

    localStorage.setItem(
      "campusPathAdminSettings",
      JSON.stringify(settings)
    );

    setSaved(true);

    setTimeout(() => {
      setSaved(false);
    }, 2500);
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
  // RESET NOTIFICATION SETTINGS
  // ============================================================

  const handleResetNotifications = () => {
    setApplicationNotifications(true);
    setEventNotifications(true);
    setRegistrationNotifications(true);
  };

  // ============================================================
  // PAGE
  // ============================================================

  return (
    <div className="admin-page"
      style={{
        minHeight: "100vh",
        background: theme === "dark" ? "#080d1c" : "#f3f6fc",
        color: theme === "dark" ? "#ffffff" : "#17243b",
      }}
    >
      {/* ======================================================
          SIDEBAR
      ====================================================== */}

      <Sidebar
        role="admin"
        orgName="Admin"
        activeKey="settings"
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

      <style>{`
        @media (max-width: 700px) {
          .admin-settings-grid {
            grid-template-columns: minmax(0, 1fr) !important;
          }
        }
      `}</style>

      <main
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          left: collapsed ? "80px" : "200px",
          overflowY: "auto",
          background: theme === "dark" ? "#080d1c" : "#f3f6fc",
          color: theme === "dark" ? "#ffffff" : "#17243b",
          padding: "32px",
          boxSizing: "border-box",
        }}
      >
        {/* ====================================================
            HEADER
        ==================================================== */}

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "30px",
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: "32px",
                fontWeight: "700",
                color: theme === "dark" ? "#ffffff" : "#17243b",
              }}
            >
              Settings
            </h1>

            <p
              style={{
                marginTop: "8px",
                marginBottom: 0,
                color: theme === "dark" ? "#94a3b8" : "#64748b",
                fontSize: "14px",
              }}
            >
              Manage your CampusPath AI administrator
              settings.
            </p>
          </div>

          {saved && (
            <div
              style={{
                background: "#123c2b",
                border: "1px solid #166534",
                color: "#86efac",
                padding: "10px 16px",
                borderRadius: "8px",
                fontSize: "14px",
                fontWeight: "600",
              }}
            >
              ✓ Settings saved
            </div>
          )}
        </div>

        {/* ====================================================
            PROFILE
        ==================================================== */}

        <section
          style={{
            background: theme === "dark" ? "#111827" : "#ffffff",
            border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
            borderRadius: "12px",
            padding: "24px",
            marginBottom: "20px",
          }}
        >
          <SectionTitle
            title="Admin Profile"
            description="Your administrator account information."
          />

          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(2, minmax(0, 1fr))",
              gap: "20px",
            }}
          >
            <InfoField
              label="Full Name"
              value={adminName}
              theme={theme}
            />

            <InfoField
              label="Email Address"
              value={adminEmail || "Not available"}
              theme={theme}
            />

            <InfoField
              label="Role"
              value="Administrator"
              theme={theme}
            />

            <InfoField
              label="Account Status"
              value="Active"
              status
              theme={theme}
            />
          </div>
        </section>

        {/* ====================================================
            PLATFORM
        ==================================================== */}

        <section
          style={{
            background: theme === "dark" ? "#111827" : "#ffffff",
            border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
            borderRadius: "12px",
            padding: "24px",
            marginBottom: "20px",
          }}
        >
          <SectionTitle
            title="Platform Information"
            description="Basic information about the CampusPath AI platform."
          />

          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(2, minmax(0, 1fr))",
              gap: "20px",
            }}
          >
            <InfoField
              label="Application Name"
              value="CampusPath AI"
            />

            <InfoField
              label="Application Type"
              value="University Event Recommendation Platform"
              theme={theme}
            />

            <InfoField
              label="Administrator Panel"
              value="Admin Dashboard"
              theme={theme}
            />

            <InfoField
              label="Authentication"
              value="JWT Authentication"
              theme={theme}
            />
          </div>
        </section>

        {/* ====================================================
            NOTIFICATION SETTINGS
        ==================================================== */}

        <section
          style={{
            background: theme === "dark" ? "#111827" : "#ffffff",
            border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
            borderRadius: "12px",
            padding: "24px",
            marginBottom: "20px",
          }}
        >
          <SectionTitle
            title="Notification Preferences"
            description="Choose which administrative notifications you want to receive."
          />

          <SettingRow
            title="Application Notifications"
            description="Receive notifications when clubs or intercollege organizations submit applications."
            checked={applicationNotifications}
            onChange={setApplicationNotifications}
            theme={theme}
          />

          <SettingRow
            title="Event Notifications"
            description="Receive notifications related to newly created or published events."
            checked={eventNotifications}
            onChange={setEventNotifications}
            theme={theme}
          />

          <SettingRow
            title="Registration Notifications"
            description="Receive notifications when students register for events."
            checked={registrationNotifications}
            onChange={setRegistrationNotifications}
            theme={theme}
          />

          <div
            style={{
              marginTop: "20px",
            }}
          >
            <button
              onClick={handleResetNotifications}
              style={{
                padding: "9px 15px",
                borderRadius: "7px",
                border: `1px solid ${theme === "dark" ? "#334155" : "#cbd5e1"}`,
                background: theme === "dark" ? "#1e293b" : "#edf1f8",
                color: theme === "dark" ? "#cbd5e1" : "#17243b",
                cursor: "pointer",
              }}
            >
              Reset Preferences
            </button>
          </div>
        </section>

        {/* ====================================================
            SECURITY
        ==================================================== */}

        <section
          style={{
            background: theme === "dark" ? "#111827" : "#ffffff",
            border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
            borderRadius: "12px",
            padding: "24px",
            marginBottom: "20px",
          }}
        >
          <SectionTitle
            title="Security"
            description="Information about your current administrator session."
          />

          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(2, minmax(0, 1fr))",
              gap: "20px",
            }}
          >
            <InfoField
              label="Authentication Method"
              value="JWT"
              theme={theme}
            />

            <InfoField
              label="Session"
              value="Authenticated"
              status
              theme={theme}
            />

            <InfoField
              label="Administrator Access"
              value="Enabled"
              status
              theme={theme}
            />

            <InfoField
              label="Account Role"
              value="admin"
              theme={theme}
            />
          </div>
        </section>

        {/* ====================================================
            APPEARANCE
        ==================================================== */}

        <section
          style={{
            background: theme === "dark" ? "#111827" : "#ffffff",
            border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
            borderRadius: "12px",
            padding: "24px",
            marginBottom: "20px",
            color: theme === "dark" ? "#ffffff" : "#17243b",
          }}
        >
          <SectionTitle
            title="Appearance"
            description="Customize your CampusPath AI administrator dashboard."
            theme={theme}
          />

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: "20px",
              padding: "16px",
              background: theme === "dark" ? "#0b1220" : "#f3f6fc",
              border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
              borderRadius: "9px",
            }}
          >
            <div>
              <div
                style={{
                  fontWeight: "600",
                  marginBottom: "5px",
                  color: theme === "dark" ? "#ffffff" : "#17243b",
                }}
              >
                Theme
              </div>

              <div
                style={{
                  color: theme === "dark" ? "#94a3b8" : "#64748b",
                  fontSize: "13px",
                }}
              >
                Choose dark or light mode for your dashboard.
              </div>
            </div>

            <select
              value={theme}
              onChange={(event) => setTheme(event.target.value)}
              aria-label="Choose dashboard theme"
              style={{
                padding: "10px 14px",
                borderRadius: "8px",
                border: `1px solid ${theme === "dark" ? "#475569" : "#cbd5e1"}`,
                background: theme === "dark" ? "#111827" : "#ffffff",
                color: theme === "dark" ? "#ffffff" : "#17243b",
                cursor: "pointer",
                minWidth: "145px",
                fontSize: "14px",
              }}
            >
              <option value="dark">Dark Mode</option>
              <option value="light">Light Mode</option>
            </select>
          </div>
        </section>

        {/* ====================================================
            SAVE
        ==================================================== */}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "12px",
            paddingBottom: "40px",
          }}
        >
          <button
            onClick={() =>
              navigate("/admin/dashboard")
            }
            style={{
              padding: "11px 20px",
              borderRadius: "8px",
              border: "1px solid #334155",
              background: theme === "dark" ? "#111827" : "#ffffff",
              color: theme === "dark" ? "#ffffff" : "#17243b",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>

          <button
            onClick={handleSaveSettings}
            style={{
              padding: "11px 22px",
              borderRadius: "8px",
              border: "none",
              background: "#4f46e5",
              color: "#ffffff",
              fontWeight: "600",
              cursor: "pointer",
            }}
          >
            Save Settings
          </button>
        </div>
      </main>
    </div>
  );
}

// ============================================================
// SECTION TITLE
// ============================================================

function SectionTitle({
  title,
  description,
  theme = "dark",
}) {
  return (
    <div
      style={{
        marginBottom: "20px",
      }}
    >
      <h2
        style={{
          margin: 0,
          fontSize: "19px",
          color: theme === "dark" ? "#ffffff" : "#17243b",
        }}
      >
        {title}
      </h2>

      <p
        style={{
          margin: "6px 0 0",
          color: theme === "dark" ? "#64748b" : "#64748b",
          fontSize: "13px",
        }}
      >
        {description}
      </p>
    </div>
  );
}

// ============================================================
// INFO FIELD
// ============================================================

function InfoField({
  label,
  value,
  status = false,
  theme = "dark",
}) {
  return (
    <div
      style={{
        padding: "15px",
        background: theme === "dark" ? "#0b1220" : "#f8fafc",
        border: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
        borderRadius: "9px",
      }}
    >
      <div
        style={{
          color: "#64748b",
          fontSize: "12px",
          marginBottom: "7px",
        }}
      >
        {label}
      </div>

      <div
        style={{
          color: status
            ? "#16a34a"
            : theme === "dark" ? "#e2e8f0" : "#17243b",
          fontSize: "14px",
          fontWeight: "600",
        }}
      >
        {value}
      </div>
    </div>
  );
}

// ============================================================
// SETTING ROW
// ============================================================

function SettingRow({
  title,
  description,
  checked,
  onChange,
  theme = "dark",
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: "20px",
        padding: "18px 0",
        borderBottom: `1px solid ${theme === "dark" ? "#26344d" : "#dce3ef"}`,
      }}
    >
      <div>
        <div
          style={{
            color: theme === "dark" ? "#ffffff" : "#17243b",
            fontWeight: "600",
            fontSize: "14px",
            marginBottom: "5px",
          }}
        >
          {title}
        </div>

        <div
          style={{
            color: "#64748b",
            fontSize: "13px",
            lineHeight: "1.5",
          }}
        >
          {description}
        </div>
      </div>

      <button
        onClick={() => onChange(!checked)}
        aria-label={title}
        style={{
          width: "50px",
          height: "28px",
          borderRadius: "999px",
          border: "none",
          background: checked
            ? "#4f46e5"
            : "#374151",
          padding: "3px",
          cursor: "pointer",
          position: "relative",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            display: "block",
            width: "22px",
            height: "22px",
            borderRadius: "50%",
            background: "#ffffff",
            transform: checked
              ? "translateX(22px)"
              : "translateX(0)",
            transition: "transform 0.2s",
          }}
        />
      </button>
    </div>
  );
}