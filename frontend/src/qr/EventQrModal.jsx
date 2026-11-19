import { useEffect, useRef } from "react";
import EventQr from "./EventQr";
import "./qr.css";

/** Pop-up with the Event Registration QR. Render it with the event id to open it. */
export default function EventQrModal({ eventId, onClose }) {
  const closeButton = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const open = Boolean(eventId);

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement;
    closeButton.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") onCloseRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="cpqr-overlay" onClick={(event) => event.target === event.currentTarget && onClose?.()}>
      <div className="cpqr-dialog" role="dialog" aria-modal="true" aria-labelledby="cpqr-event-dialog-title">
        <div className="cpqr-dialog-head">
          <h2 id="cpqr-event-dialog-title">My event QR</h2>
          <button type="button" ref={closeButton} className="cpqr-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <EventQr key={eventId} eventId={eventId} />
      </div>
    </div>
  );
}