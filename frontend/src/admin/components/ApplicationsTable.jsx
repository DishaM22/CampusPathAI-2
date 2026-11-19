import React from "react";
import StatusBadge from "./StatusBadge";
import { getKind, getRole, ROLE_LABEL } from "../applicationUtils";

export default function ApplicationsTable({ applications, onView, onApprove, onReject }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Applicant</th>
            <th>Organization / Department</th>
            <th>Type</th>
            <th>College / University</th>
            <th>Position / Designation</th>
            <th>Date</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {applications.map((app) => {
            const role = getRole(app);
            const isFaculty = getKind(app) === "faculty";
            return (
              <tr key={app.id}>
                <td>{app.applicantName}</td>
                <td>{app.organizationName || "Not provided"}</td>
                <td>
                  <span className={`type-pill ${isFaculty ? "faculty" : "org"}`}>
                    {ROLE_LABEL[role] || "Club"}
                  </span>
                </td>
                <td>{app.college || "Not provided"}</td>
                <td>{app.position || (isFaculty ? ROLE_LABEL[role] : "Not provided")}</td>
                <td>{app.date}</td>
                <td><StatusBadge status={app.status} /></td>
                <td>
                  <div className="row-actions">
                    <button className="btn-ghost small" onClick={() => onView(app)}>View</button>
                    {app.status === "pending" && (
                      <>
                        <button className="btn-primary small" onClick={() => onApprove(app)}>Approve</button>
                        <button className="btn-danger small" onClick={() => onReject(app)}>Reject</button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}