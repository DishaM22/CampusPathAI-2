import { useState } from "react";
import { DEPARTMENTS } from "../departments";

/* FastAPI sends validation errors as a LIST of objects. This turns any
   error detail into readable text (instead of "[object Object]"). */
function formatApiDetail(detail) {
  if (!detail) return "";

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item?.loc)
          ? item.loc.filter((part) => part !== "body").join(".")
          : "";

        const text = item?.msg || JSON.stringify(item);

        return field ? field + ": " + text : text;
      })
      .join("; ");
  }

  return detail.msg || JSON.stringify(detail);
}

// Existing categories are kept; "Other" is now "Others" like the other forms.
const EVENT_CATEGORIES = [
  "Technical",
  "Workshop",
  "Hackathon",
  "Seminar",
  "Sports",
  "Cultural",
  "Career",
  "Conference",
  "Masterclass",
  "Webinar",
  "Industry Visit",
  "Camp",
  "FDP",
  "SDP",
  "MDP",
  "Guest Lecturers",
  "Others",
];

const EVENT_MODES = ["Online", "Offline", "Hybrid"];

const EMAIL_REGEX = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;

/* 10-digit Indian mobile; +91 / 91 / 0 prefixes, spaces and dashes allowed. */
function normalizeIndianMobile(value) {
  let digits = String(value || "").replace(/[\s\-().]/g, "");

  if (digits.startsWith("+91")) digits = digits.slice(3);
  else if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);

  return digits;
}

/* Returns the first problem as readable text, or "" if all is well.
   The backend repeats these checks. */
function validateEventDetails(data) {
  if (!data.school_name.trim()) return "School name is required.";
  if (!DEPARTMENTS.includes(data.department)) {
    return "Please select the department from the list.";
  }

  if (!EVENT_MODES.includes(data.event_mode)) {
    return "Please select the mode of the event (Online, Offline or Hybrid).";
  }

  if (data.event_mode !== "Online" && !data.location.trim()) {
    return "Location is required for Offline and Hybrid events.";
  }

  if (!data.spoc_name.trim()) return "SPOC name is required.";
  if (!data.spoc_designation.trim()) return "SPOC designation is required.";

  if (!EMAIL_REGEX.test(data.spoc_email.trim())) {
    return "Enter a valid SPOC email address.";
  }

  if (!/^[6-9][0-9]{9}$/.test(normalizeIndianMobile(data.spoc_contact))) {
    return "Enter a valid 10-digit SPOC mobile number (starting with 6, 7, 8 or 9).";
  }

  return "";
}

function EventManagement() {
  const [formData, setFormData] = useState({
    title: "",
    description: "",
    category: "",
    organizer: "",
    date: "",
    time: "",
    location: "",
    capacity: "",
    skills: "",
    school_name: "",
    department: "",
    event_mode: "",
    spoc_name: "",
    spoc_designation: "",
    spoc_email: "",
    spoc_contact: ""
  });

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  /* ---------------- EVENT POSTER (optional) ---------------- */
  const [posterFile, setPosterFile] = useState(null);
  const [posterPreview, setPosterPreview] = useState("");
  const [posterInputKey, setPosterInputKey] = useState(0);

  const MAX_POSTER_BYTES = 3 * 1024 * 1024;
  const POSTER_TYPES = ["image/png", "image/jpeg", "image/webp"];

  function clearPoster() {
    if (posterPreview) {
      URL.revokeObjectURL(posterPreview);
    }

    setPosterFile(null);
    setPosterPreview("");
    setPosterInputKey((key) => key + 1);
  }

  function choosePoster(changeEvent) {
    const file =
      changeEvent.target.files && changeEvent.target.files[0];

    if (!file) {
      clearPoster();
      return;
    }

    if (!POSTER_TYPES.includes(file.type)) {
      window.alert("Poster must be a PNG, JPG or WEBP image.");
      clearPoster();
      return;
    }

    if (file.size > MAX_POSTER_BYTES) {
      window.alert("Poster is too large (maximum 3 MB).");
      clearPoster();
      return;
    }

    if (posterPreview) {
      URL.revokeObjectURL(posterPreview);
    }

    setPosterFile(file);
    setPosterPreview(URL.createObjectURL(file));
  }

  /* Uploads the chosen poster and returns its address (or null if none). */
  async function uploadPoster(apiBase, token) {
    if (!posterFile) {
      return null;
    }

    const posterData = new FormData();
    posterData.append("file", posterFile);

    const posterResponse = await fetch(`${apiBase}/api/events/poster`, {
      method: "POST",
      // no Content-Type here: the browser adds the multipart one itself
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: posterData
    });

    const posterResult = await posterResponse.json().catch(() => ({}));

    if (!posterResponse.ok) {
      throw new Error(
        "Poster upload failed: " +
          (formatApiDetail(posterResult.detail) || posterResponse.status)
      );
    }

    return posterResult.poster_url || null;
  }


  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    setLoading(true);
    setMessage("");

    const capacity = Number(formData.capacity);

    if (!capacity || capacity <= 0) {
      setMessage("Please enter a valid number of seats.");
      setLoading(false);
      return;
    }

    const detailsProblem = validateEventDetails(formData);

    if (detailsProblem) {
      setMessage(detailsProblem);
      setLoading(false);
      return;
    }

    const eventData = {
      title: formData.title.trim(),
      description: formData.description.trim(),
      category: formData.category,
      organizer: formData.organizer.trim(),
      date: formData.date,
      time: formData.time.trim(),
      location: formData.location.trim(),

      school_name: formData.school_name.trim(),
      department: formData.department.trim(),
      event_mode: formData.event_mode,
      spoc_name: formData.spoc_name.trim(),
      spoc_designation: formData.spoc_designation.trim(),
      spoc_email: formData.spoc_email.trim(),
      spoc_contact: normalizeIndianMobile(formData.spoc_contact),

      // IMPORTANT
      capacity: capacity,

      skills: formData.skills
        .split(",")
        .map((skill) => skill.trim())
        .filter((skill) => skill !== "")
    };

    try {
      const token = localStorage.getItem("token");

      // upload the poster first (if one was chosen)
      eventData.poster_url = await uploadPoster(
        "http://127.0.0.1:8000",
        token
      );

      const response = await fetch(
        "http://127.0.0.1:8000/api/events",
        {
          method: "POST",

          headers: {
            "Content-Type": "application/json",
            ...(token
              ? {
                  Authorization: `Bearer ${token}`
                }
              : {})
          },

          body: JSON.stringify(eventData)
        }
      );

      const data = await response.json();

      if (response.ok) {
        setMessage("Event created successfully!");

        clearPoster();

        setFormData({
          title: "",
          description: "",
          category: "",
          organizer: "",
          date: "",
          time: "",
          location: "",
          capacity: "",
          skills: "",
          school_name: "",
          department: "",
          event_mode: "",
          spoc_name: "",
          spoc_designation: "",
          spoc_email: "",
          spoc_contact: ""
        });
      } else {
        setMessage(
          formatApiDetail(data.detail) ||
            data.message ||
            "Failed to create event"
        );
      }
    } catch (error) {
      console.error("Event creation error:", error);

      setMessage(
        error.message && error.message.startsWith("Poster upload failed")
          ? error.message
          : "Unable to connect to the server."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-container">
      <div className="auth-card">

        <h1>Create Campus Event</h1>

        <p>
          Add a new opportunity for students.
        </p>

        <form onSubmit={handleSubmit}>

          {/* EVENT TITLE */}
          <input
            type="text"
            name="title"
            placeholder="Event Title"
            value={formData.title}
            onChange={handleChange}
            required
          />

          {/* DESCRIPTION */}
          <textarea
            name="description"
            placeholder="Event Description"
            value={formData.description}
            onChange={handleChange}
            rows="4"
            required
          />

          {/* CATEGORY */}
          <select
            name="category"
            value={formData.category}
            onChange={handleChange}
            required
          >
            <option value="">
              Select Category
            </option>

            {EVENT_CATEGORIES.map((categoryName) => (
              <option key={categoryName} value={categoryName}>
                {categoryName}
              </option>
            ))}
          </select>

          {/* ORGANIZER */}
          <input
            type="text"
            name="organizer"
            placeholder="Organizer / Club"
            value={formData.organizer}
            onChange={handleChange}
            required
          />

          {/* DATE */}
          <input
            type="date"
            name="date"
            value={formData.date}
            onChange={handleChange}
            required
          />

          {/* TIME */}
          <input
            type="text"
            name="time"
            placeholder="Time (e.g. 10:00 AM - 12:00 PM)"
            value={formData.time}
            onChange={handleChange}
            required
          />

          {/* LOCATION (optional for Online events) */}
          <input
            type="text"
            name="location"
            placeholder={
              formData.event_mode === "Online"
                ? "Location (optional for Online events)"
                : "Location"
            }
            value={formData.location}
            onChange={handleChange}
            required={formData.event_mode !== "Online"}
          />

          {/* SCHOOL NAME */}
          <input
            type="text"
            name="school_name"
            placeholder="School Name"
            value={formData.school_name}
            onChange={handleChange}
            maxLength={150}
            required
          />

          {/* DEPARTMENT (the event goes to the HOD of this department) */}
          <select
            name="department"
            value={formData.department}
            onChange={handleChange}
            required
          >
            <option value="">
              Select Department
            </option>

            {DEPARTMENTS.map((departmentName) => (
              <option key={departmentName} value={departmentName}>
                {departmentName}
              </option>
            ))}
          </select>

          {/* MODE OF EVENT */}
          <select
            name="event_mode"
            value={formData.event_mode}
            onChange={handleChange}
            required
          >
            <option value="">
              Select Mode of Event
            </option>

            {EVENT_MODES.map((modeName) => (
              <option key={modeName} value={modeName}>
                {modeName}
              </option>
            ))}
          </select>

          {/* SPOC NAME */}
          <input
            type="text"
            name="spoc_name"
            placeholder="SPOC Name"
            value={formData.spoc_name}
            onChange={handleChange}
            maxLength={150}
            required
          />

          {/* SPOC DESIGNATION */}
          <input
            type="text"
            name="spoc_designation"
            placeholder="SPOC Designation"
            value={formData.spoc_designation}
            onChange={handleChange}
            maxLength={150}
            required
          />

          {/* SPOC EMAIL */}
          <input
            type="email"
            name="spoc_email"
            placeholder="SPOC Email Address"
            value={formData.spoc_email}
            onChange={handleChange}
            required
          />

          {/* SPOC CONTACT NUMBER */}
          <input
            type="tel"
            name="spoc_contact"
            placeholder="SPOC Contact Number (10 digits)"
            value={formData.spoc_contact}
            onChange={handleChange}
            required
          />

          {/* The budget is entered by the Club Coordinator / Faculty (Budget
              Breakup Form). Admin accounts view it only, so it is not here. */}

          {/* TOTAL SEATS */}
          <input
            type="number"
            name="capacity"
            placeholder="Total Seats"
            value={formData.capacity}
            onChange={handleChange}
            min="1"
            required
          />

          {/* SKILLS */}
          <input
            type="text"
            name="skills"
            placeholder="Skills (e.g. Python, AI, ML)"
            value={formData.skills}
            onChange={handleChange}
            required
          />
          {/* EVENT POSTER */}
          <label style={{ display: "block", textAlign: "left", margin: "10px 0 4px" }}>
            Event Poster (optional)
          </label>

          <input
            key={posterInputKey}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={choosePoster}
          />

          <p style={{ fontSize: "12px", opacity: 0.7, margin: "4px 0 8px", textAlign: "left" }}>
            PNG, JPG or WEBP, up to 3 MB. A wide image (about 1200 x 630) looks best.
          </p>

          {posterPreview && (
            <div style={{ marginBottom: "12px", textAlign: "left" }}>
              <img
                src={posterPreview}
                alt="Poster preview"
                style={{
                  width: "100%",
                  maxHeight: "180px",
                  objectFit: "cover",
                  borderRadius: "12px",
                  display: "block",
                  marginBottom: "8px"
                }}
              />
              <button type="button" onClick={clearPoster}>
                Remove poster
              </button>
            </div>
          )}


          {/* SUBMIT */}
          <button
            type="submit"
            disabled={loading}
          >
            {loading
              ? "Creating..."
              : "Create Event"}
          </button>

        </form>

        {message && (
          <p>{message}</p>
        )}

      </div>
    </div>
  );
}

export default EventManagement;