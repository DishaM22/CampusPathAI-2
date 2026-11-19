import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";

function EventDetails() {

  const { id } = useParams();

  const navigate = useNavigate();

  const [event, setEvent] = useState(null);

  const [loading, setLoading] = useState(true);


  useEffect(() => {

    fetch(
      `http://127.0.0.1:8000/api/events/${id}`
    )

      .then(response => response.json())

      .then(data => {

        setEvent(data);

        setLoading(false);

      })

      .catch(error => {

        console.error(error);

        setLoading(false);

      });

  }, [id]);


  if (loading) {

    return (
      <p>Loading event...</p>
    );

  }


  if (!event) {

    return (
      <p>Event not found.</p>
    );

  }


  return (

    <div style={{
      padding: "40px",
      maxWidth: "800px",
      margin: "auto"
    }}>

      <button
        onClick={() =>
          navigate("/dashboard")
        }
      >
        ← Back to Dashboard
      </button>


      <h1>
        {event.title}
      </h1>


      <p>
        {event.description}
      </p>


      <hr />


      <p>
        <strong>Category:</strong>{" "}
        {event.category}
      </p>


      {event.event_mode && (
        <p>
          <strong>Mode:</strong>{" "}
          {event.event_mode}
        </p>
      )}


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


      {/* Older events do not have contact details, so show them only if present */}
      {(event.spoc_name ||
        event.spoc_designation ||
        event.spoc_email ||
        event.spoc_contact) && (
        <>
          <hr />

          <h3>Contact (SPOC)</h3>

          {event.spoc_name && (
            <p>
              <strong>Name:</strong>{" "}
              {event.spoc_name}
            </p>
          )}

          {event.spoc_designation && (
            <p>
              <strong>Designation:</strong>{" "}
              {event.spoc_designation}
            </p>
          )}

          {event.spoc_email && (
            <p>
              <strong>Email:</strong>{" "}
              <a href={`mailto:${event.spoc_email}`}>
                {event.spoc_email}
              </a>
            </p>
          )}

          {event.spoc_contact && (
            <p>
              <strong>Contact Number:</strong>{" "}
              {event.spoc_contact}
            </p>
          )}
        </>
      )}


      {Array.isArray(event.resource_persons) &&
        event.resource_persons.length > 0 && (
          <>
            <hr />

            <h3>Resource Person(s)</h3>

            {event.resource_persons.map((person, index) => (
              <p key={index}>
                <strong>{person.name}</strong>
                {person.designation && `, ${person.designation}`}
                {person.organization && ` — ${person.organization}`}
                {/^https?:\/\//i.test(person.linkedin_url || "") && (
                  <>
                    {" "}
                    <a
                      href={person.linkedin_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      LinkedIn
                    </a>
                  </>
                )}
              </p>
            ))}
          </>
        )}


      <button>
        Register for Event
      </button>

    </div>

  );

}

export default EventDetails;