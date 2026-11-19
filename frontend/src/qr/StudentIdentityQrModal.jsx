import { useEffect, useRef } from "react";
import StudentIdentityQr from "./StudentIdentityQr";
import "./qr.css";

/** Pop-up that shows the student's permanent identity QR. */
export default function StudentIdentityQrModal({ open, onClose }) {
  const closeButton = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

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
      <div className="cpqr-dialog" role="dialog" aria-modal="true" aria-labelledby="cpqr-dialog-title">
        <div className="cpqr-dialog-head">
          <h2 id="cpqr-dialog-title">My student QR</h2>
          <button type="button" ref={closeButton} className="cpqr-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <StudentIdentityQr />
      </div>
    </div>
  );
}