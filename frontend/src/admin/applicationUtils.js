// src/admin/applicationUtils.js
// One place that decides whether an application is a faculty-type
// (Faculty / HOD / Dean) account request or a club / organization request.

export const FACULTY_ROLES = ["faculty", "hod", "dean"];

export const ROLE_LABEL = {
  faculty: "Faculty",
  hod: "HOD",
  dean: "Dean",
  intercollege: "Inter-College",
  club: "Club",
};

export function getRole(app) {
  return String(app?.role || app?.organizationType || "club").toLowerCase();
}

export function getKind(app) {
  return FACULTY_ROLES.includes(getRole(app)) ? "faculty" : "organization";
}

export function approveText(app) {
  const role = getRole(app);
  if (getKind(app) === "faculty") {
    return {
      title: `Approve ${ROLE_LABEL[role]}?`,
      message: `Approving ${app.applicantName || "this applicant"} will activate their ${ROLE_LABEL[role]} account so they can sign in and review events.`,
    };
  }
  return {
    title: "Approve Organization?",
    message: `Approving "${app.organizationName}" will activate the organization account.`,
  };
}