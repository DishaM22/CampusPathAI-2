import QrCard from "./QrCard";
import useQr from "./useQr";
import { fetchIdentityQr } from "./qrApi";

/** Phase 1: shows the student's permanent identity QR. */
export default function StudentIdentityQr() {
  const { data, error, loading, reload } = useQr(fetchIdentityQr);

  if (loading) return <p className="cpqr-state" aria-live="polite">Loading your student QR…</p>;
  if (error) {
    return (
      <div className="cpqr-state">
        <p className="cpqr-error" role="alert">{error.message}</p>
        <button type="button" className="cpqr-secondary" onClick={reload}>Try again</button>
      </div>
    );
  }
  return (
    <QrCard
      title="Student identity QR"
      subtitle={data.name}
      note="This code identifies you. It cannot be used to mark attendance at an event."
      svg={data.qr_svg}
      status={data.status}
      filename="campuspath-student-qr"
    />
  );
}