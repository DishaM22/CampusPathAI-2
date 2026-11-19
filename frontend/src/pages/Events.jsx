import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  isRegistrationClosed,
  isRegistrationRequired,
  NO_REGISTRATION_LABEL,
} from "../components/registration/RegistrationRequirement";

function Events() {

  const navigate = useNavigate();

  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);


  useEffect(() => {

    fetch("http://127.0.0.1:8000/api/events")
      .then((response) => response.json())
      .then((data) => {

        setEvents(data);
        setLoading(false);

      })
      .catch((error) => {

        console.error("Error loading events:", error);
        setLoading(false);

      });

  }, []);


  if (loading) {

    return (
      <div style={{ padding: "40px" }}>
        <h2>Loading campus events...</h2>
      </div>
    );

  }


  return (

    <div style={{ padding: "40px" }}>

      <button
        onClick={() => navigate("/dashboard")}
      >
        ← Back to Dashboard
      </button>


      <h1>Campus Events</h1>

      <p>
        Discover opportunities happening on campus.
      </p>


      {events.length === 0 ? (

        <p>
          No events available.
        </p>

      ) : (

        <div>

          {events.map((event) => (

            <div
              key={event._id}
              style={{
                border: "1px solid #ddd",
                borderRadius: "12px",
                padding: "20px",
                marginTop: "20px"
              }}
            >

              <h2>
                {event.title}
              </h2>


              <p>
                {event.description}
              </p>


              <p>
                <strong>Category:</strong>{" "}
                {event.category}
              </p>


              <p>
                <strong>Organizer:</strong>{" "}
                {event.organizer}
              </p>


              {event.school_name && (
                <p>
                  <strong>School:</strong>{" "}
                  {event.school_name}
                </p>
              )}


              {event.department && (
                <p>
                  <strong>Department:</strong>{" "}
                  {event.department}
                </p>
              )}


              {event.event_mode && (
                <p>
                  <strong>Mode:</strong>{" "}
                  {event.event_mode}
                </p>
              )}


              <p>
                <strong>Date:</strong>{" "}
                {event.date}
              </p>


              <p>
                <strong>Time:</strong>{" "}
                {event.time}
              </p>


              {(event.location || event.event_mode === "Online") && (
                <p>
                  <strong>Location:</strong>{" "}
                  {event.location || "Online"}
                </p>
              )}


              <p>
                <strong>Skills:</strong>{" "}
                {(event.skills || []).join(", ")}
              </p>


              {!isRegistrationRequired(event) && (
                <p>
                  <strong>{NO_REGISTRATION_LABEL}</strong>
                </p>
              )}

              {isRegistrationClosed(event) && (
                <p>
                  <strong>Registration Closed</strong>
                </p>
              )}


              <button>
                View Event
              </button>

            </div>

          ))}

        </div>

      )}

    </div>

  );
}

export default Events;