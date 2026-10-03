const LABEL = { PAYMENT: "Pago", FLIGHT: "Reserva del vuelo", HOTEL: "Reserva del hotel", CAR: "Reserva del auto", INVOICE: "Factura" };
const UNDO = {
  PAYMENT: "Reembolso del pago",
  FLIGHT: "Cancelación del vuelo",
  HOTEL: "Cancelación del hotel",
  CAR: "Cancelación del auto",
  INVOICE: "Anulación de la factura",
};

function Icon({ kind }) {
  const paths = {
    ok: <path d="M4 10.5l4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />,
    fail: <path d="M5 5l10 10M15 5L5 15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />,
    undo: <path d="M7 4L3 8l4 4M3 8h8a5 5 0 010 10H8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />,
    run: <circle cx="10" cy="10" r="3" fill="currentColor" />,
  };
  return <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">{paths[kind]}</svg>;
}

function Step({ step, undo }) {
  const done = step.status === "COMPLETED";
  const failed = step.status === "FAILED";
  const kind = failed ? "fail" : done ? (undo ? "undo" : "ok") : "run";
  const tone = failed ? "bad" : undo ? "undo" : done ? "good" : "run";
  const status = undo
    ? (done ? "Revertido" : failed ? "No se pudo revertir" : "Revirtiendo")
    : (done ? "Listo" : failed ? "Falló" : "En curso");
  return (
    <li className={`saga-step saga-${tone}`}>
      <span className="saga-icon"><Icon kind={kind} /></span>
      <div>
        <div className="saga-title">
          <strong>{(undo ? UNDO : LABEL)[step.name]}</strong>
          <span className="saga-status">{status}</span>
        </div>
        {step.attempts > 1 && <div className="saga-meta">{step.attempts} intentos</div>}
        {step.error && <div className="saga-meta saga-error">{step.error}</div>}
      </div>
    </li>
  );
}

export default function SagaTimeline({ steps }) {
  const forward = steps.filter((s) => s.phase === "EXECUTE");
  const undo = steps.filter((s) => s.phase === "COMPENSATE");
  return (
    <div className="saga">
      <ol className="saga-list" aria-label="Pasos de la reserva">
        {forward.map((s) => <Step key={`${s.name}-${s.phase}`} step={s} />)}
      </ol>
      {undo.length > 0 && (
        <>
          <p className="saga-divider">
            Un paso falló: se revierte lo que ya estaba hecho, en orden inverso:
          </p>
          <ol className="saga-list" aria-label="Pasos de reversión">
            {undo.map((s) => <Step key={`${s.name}-${s.phase}`} step={s} undo />)}
          </ol>
        </>
      )}
    </div>
  );
}
