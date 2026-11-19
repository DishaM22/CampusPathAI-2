import { DEPARTMENTS } from "../../departments";

// Edit the endpoints here to match your FastAPI routes.
export const HOD = {
  role: "hod", label: "HOD", icon: "🏫", title: "HOD Portal",
  blurb: "First-stage review of events submitted by Club Coordinators and Inter-College Leaders.",
  points: ["Approve events and send them to the Dean", "Request changes with clear remarks", "Track every decision in your review history"],
  unitLabel: "Department", unitPh: "Select your department", unitOptions: DEPARTMENTS,
  base: "/hod-dashboard", loginRoute: "/hod/login", registerRoute: "/hod/register", registerPath: "/api/hod/register",
  other: { label: "Dean", path: "/dean/login" },
};
export const DEAN = {
  role: "dean", label: "Dean", icon: "🏛️", title: "Dean Portal",
  blurb: "Second-stage institutional review of events already recommended by the HOD.",
  points: ["Review HOD-approved events with their remarks", "Approve to the Admin's final queue", "Return events for corrections with comments"],
  unitLabel: "Faculty / School", unitPh: "e.g. Faculty of Engineering",
  base: "/dean-dashboard", loginRoute: "/dean/login", registerRoute: "/dean/register", registerPath: "/api/dean/register",
  other: { label: "HOD", path: "/hod/login" },
};