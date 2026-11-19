import QrCard from "./QrCard";
import useQr from "./useQr";
import { fetchEventQr } from "./qrApi";

/** Phase 2: the student's Event Registration QR for one event. */
export default function EventQr({ eventId }) {
  const { data, error, loading, reload } = useQr(() => fetchEventQr(eventId));

  if (loading) return <p className="cpqr-state" aria-live="polite">Loading your QR…</p>;
  if (error) {
    return (
      <div className="cpqr-state">
        <p className="cpqr-error" role="alert">{error.message}</p>
        {error.code === "NETWORK" && (
          <button type="button" className="cpqr-secondary" onClick={reload}>Try again</button>
        )}
      </div>
    );
  }
  return (
    <QrCard
      title="Event registration QR"
      note="Show this at the event entrance. It works for this event only and can be scanned once."
      svg={data.qr_svg}
      status={data.status}
      filename={`campuspath-event-${eventId}`}
    />
  );
}