"""
apply_dashboards_patch.py - wires Phases 6-8 into the Club, Faculty and Student dashboards.

Run from the frontend folder:   python apply_dashboards_patch.py
  src/pages/ClubDashboard.jsx         Edit on a PUBLISHED event opens "Request Modification";
                                      "My modification requests" list on the home page
  src/pages/InterCollegeDashboard.jsx (Faculty) same, plus a "Modification Requests" menu item,
                                      and Faculty can review a coordinator's modification
                                      (original vs proposed) in "Pending Event Reviews"
  src/pages/StudentDashboard.jsx      notification bell (event changes, newly opened events)
Backups first, anchored insertions only, all-or-nothing per file, safe to run twice.
"""
import shutil, sys

JOBS = {
"src/pages/ClubDashboard.jsx": [
 ("imports", "RequestModificationModal",
'''import EventCard from "../club/components/EventCard";
''',
'''import EventCard from "../club/components/EventCard";
import {
  RequestModificationModal,
  isLiveEvent,
} from "../components/modifications/RequestModification";
import MyModificationRequests from "../components/modifications/MyModificationRequests";
'''),
 ("state", "const [modEvent, setModEvent]",
'''  const [search, setSearch] = useState("");
''',
'''  const [search, setSearch] = useState("");
  const [modEvent, setModEvent] = useState(null);
'''),
 ("Edit on a published event = request a modification", "isLiveEvent(selectedEvent)",
'''                            onEdit={(selectedEvent) =>
                              handleNavigate(
                                `/club-dashboard/events/${selectedEvent.id}/edit`
                              )
                            }
''',
'''                            onEdit={(selectedEvent) =>
                              isLiveEvent(selectedEvent)
                                ? setModEvent(selectedEvent)
                                : handleNavigate(
                                    `/club-dashboard/events/${selectedEvent.id}/edit`
                                  )
                            }
'''),
 ("dialog + my requests list", "<MyModificationRequests />",
'''                    <div className="section-head">

                      <h2>
                        Recent registrations
                      </h2>
''',
'''                    {modEvent && (
                      <RequestModificationModal
                        event={modEvent}
                        onClose={() => setModEvent(null)}
                      />
                    )}

                    <MyModificationRequests />

                    <div className="section-head">

                      <h2>
                        Recent registrations
                      </h2>
'''),
],
"src/pages/InterCollegeDashboard.jsx": [
 ("imports", "RequestModificationModal",
'''import "../club-dashboard.css";
''',
'''import {
  RequestModificationModal,
  isLiveEvent,
} from "../components/modifications/RequestModification";
import MyModificationRequests from "../components/modifications/MyModificationRequests";
import ModificationCompare, {
  rowsFromQueueItem,
} from "../components/modifications/ModificationCompare";

import "../club-dashboard.css";
'''),
 ("menu item", 'key: "modifications"',
'''  { key: "corrections",   label: "Events Awaiting Corrections", icon: "↩️", ready: true, badge: "returnedForCorrections" },
''',
'''  { key: "corrections",   label: "Events Awaiting Corrections", icon: "↩️", ready: true, badge: "returnedForCorrections" },
  { key: "modifications", label: "Modification Requests",  icon: "✏️", ready: true },
'''),
 ("state", "const [modEvent, setModEvent]",
'''  const [focusEventId, setFocusEventId] = useState("");
''',
'''  const [focusEventId, setFocusEventId] = useState("");
  const [modEvent, setModEvent] = useState(null);
'''),
 ("Edit on a published event = request a modification", "setModEvent(event);",
'''  function editEvent(event, from = "events") {
    setSelectedEvent(event);
''',
'''  function editEvent(event, from = "events") {
    // A modification under review is not an event: nothing to edit here.
    if (event?.is_modification) return;

    // A published event is live for students, so changes go through a
    // modification request (the Admin reviews it first).
    if (isLiveEvent(event)) {
      setModEvent(event);
      return;
    }

    setSelectedEvent(event);
'''),
 ("modifications page", 'case "modifications":',
'''      case "corrections":
        return (
''',
'''      case "modifications":
        return <MyModificationRequests />;
      case "corrections":
        return (
'''),
 ("dialog", "RequestModificationModal\n          event={modEvent}",
'''          {renderBody()}
        </main>
      </div>
    </div>
  );
''',
'''          {renderBody()}
        </main>
      </div>

      {modEvent && (
        <RequestModificationModal
          event={modEvent}
          onClose={() => setModEvent(null)}
          onDone={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </div>
  );
'''),
 ("back button on the new page", '"modifications",\n            "corrections"',
'''            "event-edit",
            "reviews",
            "corrections",
''',
'''            "event-edit",
            "reviews",
            "modifications",
            "corrections",
'''),
 ("review a modification: original vs proposed", "Requested modification",
'''      {actionBar}

      {!editable && !actionBar && (''',
'''      {actionBar}

      {event.is_modification && (
        <div className="fac-card" style={{ marginTop: 18 }}>
          <h3>Requested modification</h3>
          <p style={{ margin: "0 0 10px", color: "var(--text-dim)", fontSize: 13.5 }}>
            <b>Reason:</b> {event.modification?.reason || "—"}
          </p>
          <ModificationCompare rows={rowsFromQueueItem(event.modification)} />
          <p className="fac-sub" style={{ marginTop: 10 }}>
            The published event stays unchanged until the HOD, Dean and Admin approve this revised version.
          </p>
        </div>
      )}

      {!editable && !actionBar && ('''),
 ("detail view: Edit becomes Request Modification for published events", "Request Modification",
'''            disabled={!editable}
            title={
              editable
                ? "Edit this event"
                : "This event is under review and cannot be edited"
            }
            onClick={() => onEdit(event)}
          >
            Edit
          </button>
          <button type="button" className="btn-ghost small" onClick={onBack}>''',
'''            disabled={!editable || event.is_modification}
            title={
              event.is_modification
                ? "This is a requested change; it cannot be edited here"
                : isLiveEvent(event)
                ? "This event is published. Ask the Admin to approve a change."
                : editable
                ? "Edit this event"
                : "This event is under review and cannot be edited"
            }
            onClick={() => onEdit(event)}
          >
            {isLiveEvent(event) ? "Request Modification" : "Edit"}
          </button>
          <button type="button" className="btn-ghost small" onClick={onBack}>'''),
],
"src/pages/StudentDashboard.jsx": [
 ("import", "StudentNotificationsBell",
'''import { logImpressions } from "../features/studentDashboard/services/interactionService";
''',
'''import { logImpressions } from "../features/studentDashboard/services/interactionService";
import StudentNotificationsBell from "../components/StudentNotificationsBell";
'''),
 ("bell", '<div className="mod-bell-fixed">',
'''    <CampusAIBot />
  </>
''',
'''    <div className="mod-bell-fixed">
      <StudentNotificationsBell />
    </div>

    <CampusAIBot />
  </>
'''),
],
}

def patch(path, edits):
    raw = open(path, "rb").read().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    todo, bad = 0, []
    for name, marker, anchor, repl in edits:
        if marker in work:
            print(f"  - already applied: {name}"); continue
        n = work.count(anchor)
        if n != 1:
            bad.append((name, n)); continue
        work = work.replace(anchor, repl); todo += 1
        print(f"  + applied: {name}")
    if bad:
        print(f"{path}: NOTHING WRITTEN. Could not place:")
        for name, n in bad: print(f"   - {name}: anchor found {n} time(s)")
        print("   Send me the current file and I will give the exact manual edit.")
        return False
    if todo:
        shutil.copyfile(path, path + ".bak_before_modifications")
        open(path, "wb").write(work.replace("\n", nl).encode("utf-8"))
        print(f"{path}: done. Backup: {path}.bak_before_modifications")
    else:
        print(f"{path}: nothing to do.")
    return True

if __name__ == "__main__":
    ok = True
    for f, edits in JOBS.items():
        print(f"{f}:")
        try: ok = patch(f, edits) and ok
        except FileNotFoundError: print(f"  not found: {f}"); ok = False
    sys.exit(0 if ok else 1)
