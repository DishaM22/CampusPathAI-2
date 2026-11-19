import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Sidebar from "../admin/components/Sidebar";
import { bulkUpdateUserStatus, bulkDeleteUsers } from "../adminService";

const API_BASE = "http://127.0.0.1:8000";

// Admin accounts are never changed from this page, so they get no checkbox.
const isSelectable = (user) =>
  !!user.id && String(user.role).toLowerCase() !== "admin";

export default function AdminUsers() {
  const navigate = useNavigate();

  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const [selectedUser, setSelectedUser] = useState(null);

  // bulk actions (checkboxes)
  const [selectedIds, setSelectedIds] = useState([]);
  const [bulkDialog, setBulkDialog] = useState(null); // "activate" | "disable" | "delete"
  const [deleteText, setDeleteText] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNotice, setBulkNotice] = useState(null); // { type, message, skipped }

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    fetchUsers();
  }, []);

  const fetchUsers = async () => {
    try {
      setLoading(true);
      setError("");

      const token = localStorage.getItem("token");

      const response = await fetch(
        `${API_BASE}/api/admin/users`,
        {
          method: "GET",
          headers: {
            "Content-Type": "application/json",
            ...(token
              ? {
                  Authorization: `Bearer ${token}`,
                }
              : {}),
          },
        }
      );

      const text = await response.text();

      let data = {};

      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        throw new Error("Invalid response received from server.");
      }

      if (!response.ok) {
        throw new Error(
          data.detail ||
            data.message ||
            `Failed to load users (${response.status})`
        );
      }

      const userList =
        data.users ||
        data.data ||
        data.results ||
        [];

      setUsers(Array.isArray(userList) ? userList : []);
    } catch (err) {
      console.error("FETCH USERS ERROR:", err);
      setError(err.message || "Unable to load users.");
    } finally {
      setLoading(false);
    }
  };

  const normalizedUsers = useMemo(() => {
    return users.map((user) => ({
      id:
        user.id ||
        user._id ||
        user.user_id ||
        "",

      name:
        user.name ||
        user.fullName ||
        user.full_name ||
        "Unknown User",

      email:
        user.email ||
        "",

      role:
        user.role ||
        "student",

      department:
        user.department ||
        "—",

      status:
        user.status ||
        "active",

      studentId:
        user.student_id ||
        user.studentId ||
        "—",

      college:
        user.college_name ||
        user.college ||
        "—",

      clubName:
        user.club_name ||
        user.organization_name ||
        "",

      clubCategory:
        user.club_category ||
        user.category ||
        "",

      position:
        user.position ||
        "",

      submittedAt:
        user.submitted_at ||
        user.created_at ||
        user.createdAt ||
        null,

      phone:
        user.phone ||
        user.mobile ||
        "",

      year:
        user.year ||
        "",
    }));
  }, [users]);

  const filteredUsers = useMemo(() => {
    const searchValue = search.trim().toLowerCase();

    return normalizedUsers.filter((user) => {
      const matchesSearch =
        !searchValue ||
        user.name.toLowerCase().includes(searchValue) ||
        user.email.toLowerCase().includes(searchValue) ||
        user.department.toLowerCase().includes(searchValue) ||
        user.studentId.toLowerCase().includes(searchValue) ||
        user.college.toLowerCase().includes(searchValue) ||
        user.clubName.toLowerCase().includes(searchValue);

      const matchesRole =
        roleFilter === "all" ||
        user.role.toLowerCase() === roleFilter;

      const matchesStatus =
        statusFilter === "all" ||
        user.status.toLowerCase() === statusFilter;

      return (
        matchesSearch &&
        matchesRole &&
        matchesStatus
      );
    });
  }, [
    normalizedUsers,
    search,
    roleFilter,
    statusFilter,
  ]);

  // A selection only makes sense for the rows being shown, so changing the
  // search or a filter clears it (nothing hidden can be acted on by mistake).
  useEffect(() => {
    setSelectedIds([]);
  }, [search, roleFilter, statusFilter]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const selectableShown = useMemo(
    () => filteredUsers.filter(isSelectable),
    [filteredUsers]
  );

  const allShownSelected =
    selectableShown.length > 0 &&
    selectableShown.every((user) => selectedSet.has(user.id));

  const someShownSelected = selectableShown.some((user) =>
    selectedSet.has(user.id)
  );

  // What the selected users are, so only the actions that still apply to
  // them are offered (no "Disable" for people who are already disabled).
  const selection = useMemo(() => {
    const picked = normalizedUsers.filter((user) => selectedSet.has(user.id));
    const statusOf = (user) => String(user.status).toLowerCase();

    return {
      students: picked.filter(
        (user) => String(user.role).toLowerCase() === "student"
      ),
      disableIds: picked
        .filter((user) => statusOf(user) !== "disabled")
        .map((user) => user.id),
      activateIds: picked
        .filter((user) => ["disabled", "inactive"].includes(statusOf(user)))
        .map((user) => user.id),
    };
  }, [normalizedUsers, selectedSet]);

  const selectedStudentCount = selection.students.length;

  // How many of the selected users the open dialog's action will really change.
  const dialogCount =
    bulkDialog === "disable"
      ? selection.disableIds.length
      : bulkDialog === "activate"
      ? selection.activateIds.length
      : selectedStudentCount;

  const toggleUser = (id) => {
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id]
    );
  };

  const toggleAllShown = () => {
    setSelectedIds(
      allShownSelected ? [] : selectableShown.map((user) => user.id)
    );
  };

  const openBulkDialog = (action) => {
    setDeleteText("");
    setBulkNotice(null);
    setBulkDialog(action);
  };

  const closeBulkDialog = () => {
    if (!bulkBusy) {
      setBulkDialog(null);
      setDeleteText("");
    }
  };

  const runBulkAction = async () => {
    const ids =
      bulkDialog === "delete"
        ? selectedIds
        : bulkDialog === "disable"
        ? selection.disableIds
        : selection.activateIds;

    if (!bulkDialog || ids.length === 0) {
      return;
    }

    setBulkBusy(true);

    try {
      const result =
        bulkDialog === "delete"
          ? await bulkDeleteUsers(ids)
          : await bulkUpdateUserStatus(ids, bulkDialog);

      setBulkNotice({
        type: "success",
        message: result?.message || "Done.",
        skipped: Array.isArray(result?.skipped) ? result.skipped : [],
      });

      setSelectedIds([]);
      setBulkDialog(null);
      setDeleteText("");

      await fetchUsers();
    } catch (err) {
      setBulkNotice({
        type: "error",
        message: err.message || "The action could not be completed.",
        skipped: [],
      });

      setBulkDialog(null);
    } finally {
      setBulkBusy(false);
    }
  };

  const getInitials = (name) => {
    if (!name) return "?";

    return name
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
  };

  const formatRole = (role) => {
    if (!role) return "Student";

    if (role === "intercollege") {
      return "Intercollege";
    }

    if (role === "hod") {
      return "HOD";
    }

    return (
      role.charAt(0).toUpperCase() +
      role.slice(1)
    );
  };

  const formatStatus = (status) => {
    if (!status) return "Active";

    return (
      status.charAt(0).toUpperCase() +
      status.slice(1)
    );
  };

  const formatDate = (value) => {
    if (!value) return "—";

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return String(value);
    }

    return date.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  };

  const getStatusClass = (status) => {
    switch (String(status).toLowerCase()) {
      case "active":
        return "status-active";

      case "pending":
        return "status-pending";

      case "rejected":
        return "status-rejected";

      case "inactive":
        return "status-inactive";

      case "disabled":
        return "status-disabled";

      default:
        return "status-default";
    }
  };

  const getRoleClass = (role) => {
    switch (String(role).toLowerCase()) {
      case "admin":
        return "role-admin";

      case "club":
        return "role-club";

      case "intercollege":
        return "role-intercollege";

      default:
        return "role-student";
    }
  };

  return (
    <div className="dashboard-shell">
      <Sidebar
        role="admin"
        orgName="Admin"
        activeKey="users"
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((value) => !value)}
        onNavigate={navigate}
        onLogout={() => {
          localStorage.removeItem("token");
          localStorage.removeItem("user");
          localStorage.removeItem("userEmail");
          localStorage.removeItem("userRole");
          navigate("/");
        }}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />

      <div className="dashboard-main">
        <div className="admin-users-page">
      <style>{`
        .admin-users-page {
          min-height: 100vh;
          background: #0b1220;
          color: #f8fafc;
          padding: 32px;
          box-sizing: border-box;
          margin-left: 0;
        }

        .admin-users-container {
          max-width: 1400px;
          margin: 0 auto;
        }

        .users-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 20px;
          margin-bottom: 28px;
        }

        .users-header-left h1 {
          margin: 0 0 8px;
          font-size: 28px;
          font-weight: 700;
          color: #f8fafc;
        }

        .users-header-left p {
          margin: 0;
          color: #94a3b8;
          font-size: 14px;
        }

        .refresh-button {
          border: 1px solid #334155;
          background: #111827;
          color: #e2e8f0;
          padding: 10px 16px;
          border-radius: 8px;
          cursor: pointer;
          font-size: 14px;
        }

        .refresh-button:hover {
          background: #1e293b;
        }

        .users-stats {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 16px;
          margin-bottom: 24px;
        }

        .user-stat-card {
          background: #111827;
          border: 1px solid #263247;
          border-radius: 12px;
          padding: 18px;
        }

        .user-stat-label {
          color: #94a3b8;
          font-size: 13px;
          margin-bottom: 8px;
        }

        .user-stat-value {
          color: #f8fafc;
          font-size: 25px;
          font-weight: 700;
        }

        .users-panel {
          background: #111827;
          border: 1px solid #263247;
          border-radius: 14px;
          overflow: hidden;
        }

        .users-toolbar {
          padding: 18px;
          border-bottom: 1px solid #263247;
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
        }

        .users-search {
          flex: 1;
          min-width: 260px;
          height: 42px;
          border: 1px solid #334155;
          background: #0f172a;
          color: #f8fafc;
          border-radius: 8px;
          padding: 0 14px;
          outline: none;
          box-sizing: border-box;
        }

        .users-search:focus {
          border-color: #6366f1;
        }

        .users-select {
          height: 42px;
          min-width: 150px;
          border: 1px solid #334155;
          background: #0f172a;
          color: #e2e8f0;
          border-radius: 8px;
          padding: 0 12px;
          outline: none;
        }

        .users-table-wrapper {
          width: 100%;
          overflow-x: auto;
        }

        .users-table {
          width: 100%;
          border-collapse: collapse;
          min-width: 900px;
        }

        .users-table th {
          text-align: left;
          padding: 14px 18px;
          color: #94a3b8;
          font-size: 12px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          border-bottom: 1px solid #263247;
          white-space: nowrap;
        }

        .users-table td {
          padding: 15px 18px;
          border-bottom: 1px solid #1f2937;
          color: #cbd5e1;
          font-size: 14px;
          vertical-align: middle;
        }

        .users-table tbody tr:hover {
          background: #151e2e;
        }

        .user-info {
          display: flex;
          align-items: center;
          gap: 11px;
          min-width: 200px;
        }

        .user-avatar {
          width: 36px;
          height: 36px;
          min-width: 36px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #272050;
          color: #a78bfa;
          font-size: 12px;
          font-weight: 700;
        }

        .user-name {
          color: #f8fafc;
          font-weight: 600;
          margin-bottom: 3px;
        }

        .user-email {
          color: #64748b;
          font-size: 12px;
        }

        .role-badge,
        .status-badge {
          display: inline-flex;
          align-items: center;
          padding: 5px 9px;
          border-radius: 999px;
          font-size: 11px;
          font-weight: 600;
          white-space: nowrap;
        }

        .role-student {
          background: #172554;
          color: #93c5fd;
        }

        .role-club {
          background: #2e1065;
          color: #c4b5fd;
        }

        .role-intercollege {
          background: #083344;
          color: #67e8f9;
        }

        .role-admin {
          background: #422006;
          color: #fcd34d;
        }

        .status-active {
          background: #052e1b;
          color: #4ade80;
        }

        .status-pending {
          background: #422006;
          color: #fbbf24;
        }

        .status-rejected {
          background: #450a0a;
          color: #f87171;
        }

        .status-inactive {
          background: #1e293b;
          color: #94a3b8;
        }

        .status-default {
          background: #1e293b;
          color: #cbd5e1;
        }

        .view-user-button {
          border: 1px solid #334155;
          background: transparent;
          color: #a78bfa;
          padding: 7px 11px;
          border-radius: 7px;
          cursor: pointer;
          font-size: 12px;
          font-weight: 600;
        }

        .view-user-button:hover {
          background: #211c46;
          border-color: #51459a;
        }

        .users-empty {
          padding: 70px 20px;
          text-align: center;
          color: #94a3b8;
        }

        .users-empty-icon {
          font-size: 38px;
          margin-bottom: 12px;
        }

        .users-empty-title {
          color: #f8fafc;
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 6px;
        }

        .users-empty-text {
          font-size: 13px;
        }

        .users-error {
          margin: 18px;
          padding: 14px 16px;
          border: 1px solid #7f1d1d;
          background: #2a1111;
          color: #fca5a5;
          border-radius: 8px;
          font-size: 14px;
        }

        .users-loading {
          padding: 70px 20px;
          text-align: center;
          color: #94a3b8;
        }

        .status-disabled {
          background: #3b1d1d;
          color: #fca5a5;
        }

        .check-col {
          width: 44px;
          padding-right: 0 !important;
        }

        .row-check {
          width: 17px;
          height: 17px;
          accent-color: #7c5cff;
          cursor: pointer;
        }

        .row-check:disabled {
          cursor: not-allowed;
          opacity: 0.35;
        }

        .users-table tbody tr.row-selected {
          background: #1a1f3d;
        }

        .bulk-bar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
          margin: 14px 18px 0;
          padding: 10px 14px;
          border: 1px solid #3b3f8f;
          background: #181c3a;
          border-radius: 10px;
        }

        .bulk-count {
          font-weight: 700;
          color: #c7d2fe;
        }

        .bulk-actions,
        .bulk-dialog-actions {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
        }

        .bulk-dialog-actions {
          justify-content: flex-end;
          margin-top: 22px;
        }

        .bulk-btn {
          border: 1px solid #334155;
          background: transparent;
          color: #e2e8f0;
          padding: 8px 14px;
          border-radius: 8px;
          cursor: pointer;
          font-size: 13px;
          font-weight: 600;
        }

        .bulk-btn:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }

        .bulk-activate {
          border-color: #166534;
          color: #4ade80;
        }

        .bulk-activate:hover:not(:disabled) {
          background: #052e1b;
        }

        .bulk-disable {
          border-color: #92400e;
          color: #fbbf24;
        }

        .bulk-disable:hover:not(:disabled) {
          background: #422006;
        }

        .bulk-delete {
          border-color: #991b1b;
          color: #f87171;
        }

        .bulk-delete:hover:not(:disabled) {
          background: #450a0a;
        }

        .bulk-delete-solid {
          background: #b91c1c;
          border-color: #b91c1c;
          color: #fff;
        }

        .bulk-clear:hover:not(:disabled) {
          background: #1e293b;
        }

        .bulk-notice {
          margin: 14px 18px 0;
          padding: 12px 14px;
          border-radius: 10px;
          font-size: 14px;
        }

        .bulk-notice.success {
          border: 1px solid #166534;
          background: #052e1b;
          color: #86efac;
        }

        .bulk-notice.error {
          border: 1px solid #7f1d1d;
          background: #2a1111;
          color: #fca5a5;
        }

        .bulk-notice-main {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
        }

        .bulk-skipped {
          margin: 10px 0 0;
          padding-left: 18px;
          color: #cbd5e1;
          font-size: 13px;
        }

        .bulk-skipped li {
          margin-bottom: 3px;
        }

        .bulk-modal {
          max-width: 520px;
        }

        .bulk-text {
          margin: 0 0 12px;
          color: #cbd5e1;
          font-size: 14px;
          line-height: 1.55;
        }

        .bulk-danger {
          color: #fca5a5;
        }

        .bulk-label {
          display: block;
          margin: 14px 0 6px;
          color: #94a3b8;
          font-size: 13px;
        }

        .bulk-input {
          width: 100%;
          flex: none;
        }

        .modal-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.65);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 20px;
          z-index: 3000;
        }

        .user-modal {
          width: 100%;
          max-width: 650px;
          max-height: 85vh;
          overflow-y: auto;
          background: #111827;
          border: 1px solid #334155;
          border-radius: 14px;
          box-shadow: 0 25px 70px rgba(0, 0, 0, 0.45);
        }

        .user-modal-header {
          padding: 20px;
          border-bottom: 1px solid #263247;
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 20px;
        }

        .user-modal-title {
          margin: 0 0 5px;
          font-size: 20px;
          color: #f8fafc;
        }

        .user-modal-subtitle {
          margin: 0;
          color: #64748b;
          font-size: 13px;
        }

        .modal-close {
          border: none;
          background: transparent;
          color: #94a3b8;
          font-size: 22px;
          cursor: pointer;
        }

        .modal-close:hover {
          color: #ffffff;
        }

        .user-modal-body {
          padding: 20px;
        }

        .modal-user-header {
          display: flex;
          align-items: center;
          gap: 14px;
          margin-bottom: 24px;
        }

        .modal-avatar {
          width: 54px;
          height: 54px;
          border-radius: 50%;
          background: #272050;
          color: #a78bfa;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 700;
          font-size: 17px;
        }

        .modal-user-name {
          color: #f8fafc;
          font-size: 18px;
          font-weight: 700;
          margin-bottom: 5px;
        }

        .modal-user-email {
          color: #94a3b8;
          font-size: 13px;
        }

        .details-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 14px;
        }

        .detail-box {
          background: #0f172a;
          border: 1px solid #263247;
          border-radius: 8px;
          padding: 12px;
        }

        .detail-label {
          color: #64748b;
          font-size: 11px;
          margin-bottom: 5px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .detail-value {
          color: #e2e8f0;
          font-size: 13px;
          word-break: break-word;
        }

        @media (max-width: 1000px) {
          .users-stats {
            grid-template-columns: repeat(2, 1fr);
          }
        }

        @media (max-width: 768px) {
          .admin-users-page {
            margin-left: 0;
            padding: 20px;
          }

          .users-header {
            flex-direction: column;
          }

          .users-stats {
            grid-template-columns: 1fr 1fr;
          }

          .details-grid {
            grid-template-columns: 1fr;
          }
        }
        

/* =====================================
   ADMIN USERS - LIGHT MODE
===================================== */

:root[data-admin-theme="light"] .admin-users-page {
  background: #f3f6fc !important;
  color: #17243b !important;
}

:root[data-admin-theme="light"] .users-header-left h1,
:root[data-admin-theme="light"] .user-stat-value,
:root[data-admin-theme="light"] .user-name,
:root[data-admin-theme="light"] .users-empty-title,
:root[data-admin-theme="light"] .user-modal-title,
:root[data-admin-theme="light"] .modal-user-name {
  color: #17243b !important;
}

:root[data-admin-theme="light"] .users-header-left p,
:root[data-admin-theme="light"] .user-stat-label,
:root[data-admin-theme="light"] .users-empty,
:root[data-admin-theme="light"] .users-loading,
:root[data-admin-theme="light"] .user-modal-subtitle,
:root[data-admin-theme="light"] .modal-user-email,
:root[data-admin-theme="light"] .bulk-label {
  color: #64748b !important;
}

/* Cards and main panel */
:root[data-admin-theme="light"] .user-stat-card,
:root[data-admin-theme="light"] .users-panel,
:root[data-admin-theme="light"] .user-modal {
  background: #ffffff !important;
  border-color: #dce3ef !important;
}

/* Search and dropdowns */
:root[data-admin-theme="light"] .users-search,
:root[data-admin-theme="light"] .users-select,
:root[data-admin-theme="light"] .detail-box {
  background: #f8fafc !important;
  color: #17243b !important;
  border-color: #dce3ef !important;
}

:root[data-admin-theme="light"] .users-search::placeholder {
  color: #94a3b8 !important;
}

:root[data-admin-theme="light"] .users-select option {
  background: #ffffff;
  color: #17243b;
}

/* Table */
:root[data-admin-theme="light"] .users-table th {
  color: #64748b !important;
  border-bottom-color: #dce3ef !important;
}

:root[data-admin-theme="light"] .users-table td {
  color: #465670 !important;
  border-bottom-color: #e5eaf2 !important;
}

:root[data-admin-theme="light"] .users-table tbody tr:hover {
  background: #f1f5fb !important;
}

:root[data-admin-theme="light"] .users-table tbody tr.row-selected {
  background: #e8eaff !important;
}

:root[data-admin-theme="light"] .user-email,
:root[data-admin-theme="light"] .detail-label {
  color: #687991 !important;
}

/* Buttons */
:root[data-admin-theme="light"] .refresh-button,
:root[data-admin-theme="light"] .view-user-button,
:root[data-admin-theme="light"] .bulk-btn {
  background: #ffffff !important;
  color: #465670 !important;
  border-color: #dce3ef !important;
}

:root[data-admin-theme="light"] .refresh-button:hover,
:root[data-admin-theme="light"] .bulk-clear:hover:not(:disabled) {
  background: #edf1f8 !important;
}

:root[data-admin-theme="light"] .view-user-button {
  color: #635bdb !important;
}

:root[data-admin-theme="light"] .view-user-button:hover {
  background: #eeedff !important;
}

/* Modal */
:root[data-admin-theme="light"] .user-modal-header {
  border-bottom-color: #dce3ef !important;
}

:root[data-admin-theme="light"] .detail-value,
:root[data-admin-theme="light"] .bulk-text {
  color: #465670 !important;
}

:root[data-admin-theme="light"] .modal-close {
  color: #64748b !important;
}

:root[data-admin-theme="light"] .modal-close:hover {
  color: #17243b !important;
}

/* Bulk action panel */
:root[data-admin-theme="light"] .bulk-bar {
  background: #eeefff !important;
  border-color: #c7c9f5 !important;
}

:root[data-admin-theme="light"] .bulk-count {
  color: #4338a8 !important;
}

:root[data-admin-theme="light"] .bulk-text,
:root[data-admin-theme="light"] .bulk-skipped {
  color: #465670 !important;
}

/* Keep modal overlay readable */
:root[data-admin-theme="light"] .modal-overlay {
  background: rgba(15, 23, 42, 0.40) !important;
}

/* User avatar */
:root[data-admin-theme="light"] .user-avatar,
:root[data-admin-theme="light"] .modal-avatar {
  background: #eeedff !important;
  color: #635bdb !important;
}

      `}</style>

      <div className="admin-users-container">

        {/* HEADER */}
        <div className="users-header">
          <div className="users-header-left">
            <h1>Users</h1>

            <p>
              Manage and review all registered CampusPath AI users.
            </p>
          </div>

          <button
            className="refresh-button"
            onClick={fetchUsers}
            disabled={loading}
          >
            ↻ Refresh
          </button>
        </div>

        {/* STATS */}
        <div className="users-stats">

          <div className="user-stat-card">
            <div className="user-stat-label">
              Total Users
            </div>

            <div className="user-stat-value">
              {normalizedUsers.length}
            </div>
          </div>

          <div className="user-stat-card">
            <div className="user-stat-label">
              Students
            </div>

            <div className="user-stat-value">
              {
                normalizedUsers.filter(
                  (user) => user.role === "student"
                ).length
              }
            </div>
          </div>

          <div className="user-stat-card">
            <div className="user-stat-label">
              Organizations
            </div>

            <div className="user-stat-value">
              {
                normalizedUsers.filter(
                  (user) =>
                    user.role === "club" ||
                    user.role === "intercollege"
                ).length
              }
            </div>
          </div>

          <div className="user-stat-card">
            <div className="user-stat-label">
              Active Users
            </div>

            <div className="user-stat-value">
              {
                normalizedUsers.filter(
                  (user) => user.status === "active"
                ).length
              }
            </div>
          </div>

        </div>

        {/* MAIN PANEL */}
        <div className="users-panel">

          {/* TOOLBAR */}
          <div className="users-toolbar">

            <input
              type="text"
              className="users-search"
              placeholder="Search by name, email, department, college..."
              value={search}
              onChange={(event) =>
                setSearch(event.target.value)
              }
            />

            <select
              className="users-select"
              value={roleFilter}
              onChange={(event) =>
                setRoleFilter(event.target.value)
              }
            >
              <option value="all">
                All Roles
              </option>

              <option value="student">
                Students
              </option>

              <option value="club">
                Clubs
              </option>

              <option value="intercollege">
                Intercollege
              </option>

              <option value="faculty">
                Faculty
              </option>

              <option value="hod">
                HODs
              </option>

              <option value="dean">
                Deans
              </option>

              <option value="admin">
                Admins
              </option>
            </select>

            <select
              className="users-select"
              value={statusFilter}
              onChange={(event) =>
                setStatusFilter(event.target.value)
              }
            >
              <option value="all">
                All Status
              </option>

              <option value="active">
                Active
              </option>

              <option value="pending">
                Pending
              </option>

              <option value="rejected">
                Rejected
              </option>

              <option value="inactive">
                Inactive
              </option>

              <option value="disabled">
                Disabled
              </option>
            </select>

          </div>

          {/* RESULT OF THE LAST BULK ACTION */}
          {bulkNotice && (
            <div className={`bulk-notice ${bulkNotice.type}`}>
              <div className="bulk-notice-main">
                <span>{bulkNotice.message}</span>

                <button
                  type="button"
                  className="modal-close"
                  aria-label="Dismiss"
                  onClick={() => setBulkNotice(null)}
                >
                  ×
                </button>
              </div>

              {bulkNotice.skipped.length > 0 && (
                <ul className="bulk-skipped">
                  {bulkNotice.skipped.slice(0, 10).map((item) => (
                    <li key={item.id}>
                      <strong>
                        {item.name || item.email || item.id}
                      </strong>
                      : {item.reason}
                    </li>
                  ))}

                  {bulkNotice.skipped.length > 10 && (
                    <li>
                      …and {bulkNotice.skipped.length - 10} more
                    </li>
                  )}
                </ul>
              )}
            </div>
          )}

          {/* SELECTION BAR */}
          {selectedIds.length > 0 && (
            <div className="bulk-bar">
              <span className="bulk-count">
                {selectedIds.length} selected
              </span>

              <div className="bulk-actions">
                {selection.activateIds.length > 0 && (
                  <button
                    type="button"
                    className="bulk-btn bulk-activate"
                    onClick={() => openBulkDialog("activate")}
                  >
                    Activate
                  </button>
                )}

                {selection.disableIds.length > 0 && (
                  <button
                    type="button"
                    className="bulk-btn bulk-disable"
                    onClick={() => openBulkDialog("disable")}
                  >
                    Disable
                  </button>
                )}

                <button
                  type="button"
                  className="bulk-btn bulk-delete"
                  disabled={selectedStudentCount === 0}
                  title={
                    selectedStudentCount === 0
                      ? "Only student accounts can be deleted. You can disable other accounts."
                      : "Permanently delete the selected students"
                  }
                  onClick={() => openBulkDialog("delete")}
                >
                  Delete
                </button>

                <button
                  type="button"
                  className="bulk-btn bulk-clear"
                  onClick={() => setSelectedIds([])}
                >
                  Clear
                </button>
              </div>
            </div>
          )}

          {/* ERROR */}
          {error && (
            <div className="users-error">
              {error}
            </div>
          )}

          {/* LOADING */}
          {loading ? (
            <div className="users-loading">
              Loading users...
            </div>
          ) : filteredUsers.length === 0 ? (

            <div className="users-empty">

              <div className="users-empty-icon">
                👥
              </div>

              <div className="users-empty-title">
                No users found
              </div>

              <div className="users-empty-text">
                {error
                  ? "The users could not be loaded from the server."
                  : "No users match your current search or filters."}
              </div>

            </div>

          ) : (

            <div className="users-table-wrapper">

              <table className="users-table">

                <thead>
                  <tr>
                    <th className="check-col">
                      <input
                        type="checkbox"
                        className="row-check"
                        aria-label="Select all users shown"
                        checked={allShownSelected}
                        disabled={selectableShown.length === 0}
                        ref={(element) => {
                          if (element) {
                            element.indeterminate =
                              someShownSelected && !allShownSelected;
                          }
                        }}
                        onChange={toggleAllShown}
                      />
                    </th>
                    <th>User</th>
                    <th>Role</th>
                    <th>Department</th>
                    <th>Status</th>
                    <th>Registered</th>
                    <th>Action</th>
                  </tr>
                </thead>

                <tbody>

                  {filteredUsers.map((user) => (

                    <tr
                      key={user.id || user.email}
                      className={selectedSet.has(user.id) ? "row-selected" : ""}
                    >

                      <td className="check-col">
                        <input
                          type="checkbox"
                          className="row-check"
                          aria-label={`Select ${user.name}`}
                          checked={selectedSet.has(user.id)}
                          disabled={!isSelectable(user)}
                          title={
                            isSelectable(user)
                              ? undefined
                              : "Admin accounts cannot be changed here"
                          }
                          onChange={() => toggleUser(user.id)}
                        />
                      </td>

                      <td>

                        <div className="user-info">

                          <div className="user-avatar">
                            {getInitials(user.name)}
                          </div>

                          <div>
                            <div className="user-name">
                              {user.name}
                            </div>

                            <div className="user-email">
                              {user.email}
                            </div>
                          </div>

                        </div>

                      </td>

                      <td>
                        <span
                          className={`role-badge ${getRoleClass(
                            user.role
                          )}`}
                        >
                          {formatRole(user.role)}
                        </span>
                      </td>

                      <td>
                        {user.department}
                      </td>

                      <td>
                        <span
                          className={`status-badge ${getStatusClass(
                            user.status
                          )}`}
                        >
                          {formatStatus(user.status)}
                        </span>
                      </td>

                      <td>
                        {formatDate(user.submittedAt)}
                      </td>

                      <td>

                        <button
                          className="view-user-button"
                          onClick={() =>
                            setSelectedUser(user)
                          }
                        >
                          View
                        </button>

                      </td>

                    </tr>

                  ))}

                </tbody>

              </table>

            </div>

          )}

        </div>
      </div>

      {/* BULK ACTION CONFIRMATION */}
      {bulkDialog && (
        <div
          className="modal-overlay"
          onClick={closeBulkDialog}
        >
          <div
            className="user-modal bulk-modal"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="user-modal-header">
              <div>
                <h2 className="user-modal-title">
                  {bulkDialog === "activate" &&
                    `Activate ${dialogCount} account${dialogCount === 1 ? "" : "s"}?`}
                  {bulkDialog === "disable" &&
                    `Disable ${dialogCount} account${dialogCount === 1 ? "" : "s"}?`}
                  {bulkDialog === "delete" &&
                    `Permanently delete ${selectedStudentCount} student account${selectedStudentCount === 1 ? "" : "s"}?`}
                </h2>
              </div>

              <button
                type="button"
                className="modal-close"
                aria-label="Close"
                onClick={closeBulkDialog}
              >
                ×
              </button>
            </div>

            <div className="user-modal-body">
              {bulkDialog === "activate" && (
                <>
                  <p className="bulk-text">
                    These disabled accounts can sign in again and get back
                    the status they had before. Pending or rejected
                    organisation applications are handled on the
                    Applications page.
                  </p>

                  {selectedIds.length > dialogCount && (
                    <p className="bulk-text">
                      {selectedIds.length - dialogCount} other selected
                      account(s) are not disabled and are left unchanged.
                    </p>
                  )}
                </>
              )}

              {bulkDialog === "disable" && (
                <>
                  <p className="bulk-text">
                    These users are signed out immediately and cannot sign
                    in until you activate them again. Their data is kept.
                    Admin accounts and your own account are never changed.
                  </p>

                  {selectedIds.length > dialogCount && (
                    <p className="bulk-text">
                      {selectedIds.length - dialogCount} other selected
                      account(s) are already disabled and are left
                      unchanged.
                    </p>
                  )}
                </>
              )}

              {bulkDialog === "delete" && (
                <>
                  <p className="bulk-text bulk-danger">
                    This cannot be undone. Each account is removed together
                    with the student's registrations, attendance, saved
                    events and notifications.
                  </p>

                  {selectedIds.length > selectedStudentCount && (
                    <p className="bulk-text">
                      {selectedIds.length - selectedStudentCount} selected
                      account(s) are not students and will be skipped. You
                      can disable those instead.
                    </p>
                  )}

                  <label className="bulk-label" htmlFor="bulk-delete-text">
                    Type <strong>DELETE</strong> to confirm
                  </label>

                  <input
                    id="bulk-delete-text"
                    className="users-search bulk-input"
                    value={deleteText}
                    onChange={(event) => setDeleteText(event.target.value)}
                    autoComplete="off"
                    autoFocus
                  />
                </>
              )}

              <div className="bulk-dialog-actions">
                <button
                  type="button"
                  className="bulk-btn bulk-clear"
                  onClick={closeBulkDialog}
                  disabled={bulkBusy}
                >
                  Cancel
                </button>

                <button
                  type="button"
                  className={`bulk-btn ${
                    bulkDialog === "delete"
                      ? "bulk-delete-solid"
                      : bulkDialog === "disable"
                      ? "bulk-disable"
                      : "bulk-activate"
                  }`}
                  disabled={
                    bulkBusy ||
                    (bulkDialog === "delete" && deleteText !== "DELETE")
                  }
                  onClick={runBulkAction}
                >
                  {bulkBusy
                    ? "Working…"
                    : bulkDialog === "delete"
                    ? "Delete permanently"
                    : bulkDialog === "disable"
                    ? "Disable"
                    : "Activate"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* USER DETAILS MODAL */}
      {selectedUser && (

        <div
          className="modal-overlay"
          onClick={() =>
            setSelectedUser(null)
          }
        >

          <div
            className="user-modal"
            onClick={(event) =>
              event.stopPropagation()
            }
          >

            <div className="user-modal-header">

              <div>
                <h2 className="user-modal-title">
                  User Details
                </h2>

                <p className="user-modal-subtitle">
                  Registered CampusPath AI account
                </p>
              </div>

              <button
                className="modal-close"
                onClick={() =>
                  setSelectedUser(null)
                }
              >
                ×
              </button>

            </div>

            <div className="user-modal-body">

              <div className="modal-user-header">

                <div className="modal-avatar">
                  {getInitials(selectedUser.name)}
                </div>

                <div>
                  <div className="modal-user-name">
                    {selectedUser.name}
                  </div>

                  <div className="modal-user-email">
                    {selectedUser.email}
                  </div>
                </div>

              </div>

              <div className="details-grid">

                <div className="detail-box">
                  <div className="detail-label">
                    Role
                  </div>

                  <div className="detail-value">
                    {formatRole(selectedUser.role)}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    Status
                  </div>

                  <div className="detail-value">
                    {formatStatus(selectedUser.status)}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    Student ID
                  </div>

                  <div className="detail-value">
                    {selectedUser.studentId}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    Department
                  </div>

                  <div className="detail-value">
                    {selectedUser.department}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    College
                  </div>

                  <div className="detail-value">
                    {selectedUser.college}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    Year
                  </div>

                  <div className="detail-value">
                    {selectedUser.year || "—"}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    Phone
                  </div>

                  <div className="detail-value">
                    {selectedUser.phone || "—"}
                  </div>
                </div>

                <div className="detail-box">
                  <div className="detail-label">
                    Registered
                  </div>

                  <div className="detail-value">
                    {formatDate(
                      selectedUser.submittedAt
                    )}
                  </div>
                </div>

                {(selectedUser.clubName ||
                  selectedUser.clubCategory ||
                  selectedUser.position) && (

                  <>
                    <div className="detail-box">
                      <div className="detail-label">
                        Organization
                      </div>

                      <div className="detail-value">
                        {selectedUser.clubName || "—"}
                      </div>
                    </div>

                    <div className="detail-box">
                      <div className="detail-label">
                        Category
                      </div>

                      <div className="detail-value">
                        {selectedUser.clubCategory || "—"}
                      </div>
                    </div>

                    <div className="detail-box">
                      <div className="detail-label">
                        Position
                      </div>

                      <div className="detail-value">
                        {selectedUser.position || "—"}
                      </div>
                    </div>
                  </>

                )}

              </div>

            </div>

          </div>

        </div>

      )}

        </div>
      </div>
    </div>
  );
}