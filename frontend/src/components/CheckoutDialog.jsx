import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@apollo/client";
import { CREATE_BOOKING, FINAL_ORDER_STATUSES, ORDER } from "../graphql.js";
import { errorText, money, newIdempotencyKey } from "../format.js";
import Dialog from "./Dialog.jsx";
import SagaTimeline from "./SagaTimeline.jsx";

const FAILURES = [
  ["NONE", "Ninguno"],
  ["PAYMENT", "El pago"],
  ["FLIGHT", "La reserva del vuelo"],
  ["HOTEL", "La reserva del hotel"],
  ["CAR", "La reserva del auto"],
];

export default function CheckoutDialog({ pkg, onClose, onViewOrders }) {
  const [failure, setFailure] = useState("NONE");
  const [slowMotion, setSlowMotion] = useState(false);   // modo presentación: la SAGA avanza paso a paso
  const keyRef = useRef(newIdempotencyKey());
  const [book, { loading, error, reset }] = useMutation(CREATE_BOOKING);
  // Última versión conocida de la orden: la deja la mutación y la actualiza el sondeo mientras no sea final.
  const [order, setOrder] = useState(null);
  const processing = order != null && !FINAL_ORDER_STATUSES.has(order.status);

  const { data: polled } = useQuery(ORDER, {
    variables: { id: order?.id },
    skip: !processing,
    pollInterval: slowMotion ? 1000 : 1500,
    fetchPolicy: "network-only",
  });
  useEffect(() => {
    if (polled?.order) setOrder(polled.order);
  }, [polled]);

  async function submit() {
    try {
      const result = await book({
        variables: {
          input: {
            flightOfferId: pkg.flight.id,
            hotelOfferId: pkg.hotel.id,
            carOfferId: pkg.car.id,
            idempotencyKey: keyRef.current,
            simulateFailure: failure === "NONE" ? null : failure,
            slowMotion,
          },
        },
      });
      setOrder(result.data.createBooking);
    } catch {
      /* el error se muestra desde `error` */
    }
  }

  function retry() {
    keyRef.current = newIdempotencyKey(); // una reserva nueva necesita una clave nueva
    setOrder(null);
    reset();
  }

  const title = !order ? "Confirmar reserva" : processing ? "Procesando tu reserva" : "Resultado de tu reserva";

  return (
    <Dialog title={title} onClose={onClose} wide>
      {!order && (
        <>
          <ul className="summary">
            <li><span>Vuelo {pkg.flight.airline} {pkg.flight.flightNumber}</span><span>{money(pkg.flight.priceCents, pkg.currency)}</span></li>
            <li><span>{pkg.hotel.name}, {pkg.nights} noches</span><span>{money(pkg.hotel.totalPriceCents, pkg.currency)}</span></li>
            <li><span>Auto {pkg.car.company} {pkg.car.model}</span><span>{money(pkg.car.totalPriceCents, pkg.currency)}</span></li>
            <li className="summary-total"><span>Total</span><span>{money(pkg.totalPriceCents, pkg.currency)}</span></li>
          </ul>

          <fieldset className="demo">
            <legend>Modo demostración: forzar un fallo en</legend>
            <p className="hint">
              Sirve para ver cómo la reserva se revierte sola cuando un paso falla (patrón SAGA).
            </p>
            <div className="demo-options">
              {FAILURES.map(([value, label]) => (
                <label key={value} className="radio">
                  <input type="radio" name="failure" value={value} checked={failure === value} onChange={() => setFailure(value)} />
                  {label}
                </label>
              ))}
            </div>
            <label className="radio">
              <input type="checkbox" checked={slowMotion} onChange={(e) => setSlowMotion(e.target.checked)} />
              Mostrar la SAGA paso a paso (lento, para la presentación)
            </label>
          </fieldset>

          {error && <p className="alert alert-error" role="alert">{errorText(error)}</p>}
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancelar</button>
            <button type="button" className="btn btn-primary" onClick={submit} disabled={loading}>
              {loading ? "Procesando reserva…" : `Pagar ${money(pkg.totalPriceCents, pkg.currency)} y reservar`}
            </button>
          </div>
          <p className="hint">Es una simulación: no se ingresan ni se guardan datos de tarjeta.</p>
        </>
      )}

      {order && (
        <>
          {processing && slowMotion ? (
            <p className="alert alert-warn" role="status">
              Modo paso a paso: cada paso de la SAGA aparece aquí mientras ocurre.
            </p>
          ) : processing ? (
            <p className="alert alert-warn" role="status">
              Tu reserva se está procesando; uno de los servicios está tardando más de lo normal.
              Esta pantalla se actualiza sola, y también la verás en Mis reservas.
            </p>
          ) : order.status === "CONFIRMED" ? (
            <p className="alert alert-ok" role="status">
              Reserva confirmada. Factura {order.invoiceNumber}. Total {money(order.totalCents, order.currency)}.
            </p>
          ) : (
            <p className="alert alert-warn" role="status">
              {order.status === "CANCELLED"
                ? "No pudimos completar tu reserva. Revertimos todo lo que se había hecho y no se te cobra nada."
                : "La reserva no se completó y alguna reversión sigue pendiente. El sistema la reintentará automáticamente."}
            </p>
          )}
          <SagaTimeline steps={order.steps} />
          <div className="actions">
            {!processing && order.status !== "CONFIRMED" && <button type="button" className="btn btn-ghost" onClick={retry}>Intentar de nuevo</button>}
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cerrar</button>
            <button type="button" className="btn btn-primary" onClick={onViewOrders}>Ver mis reservas</button>
          </div>
        </>
      )}
    </Dialog>
  );
}
