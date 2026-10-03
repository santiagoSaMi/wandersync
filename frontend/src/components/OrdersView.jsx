import { useEffect } from "react";
import { useQuery } from "@apollo/client";
import { FINAL_ORDER_STATUSES, MY_ORDERS } from "../graphql.js";
import { errorText, money } from "../format.js";
import SagaTimeline from "./SagaTimeline.jsx";

const STATUS = {
  CONFIRMED: ["Confirmada", "pill-ok"],
  CANCELLED: ["Cancelada y revertida", "pill-warn"],
  PENDING: ["En proceso", "pill-run"],
  COMPENSATING: ["Revirtiendo", "pill-run"],
  COMPENSATION_FAILED: ["Reversión pendiente", "pill-bad"],
};

export default function OrdersView() {
  const { data, loading, error, startPolling, stopPolling } = useQuery(MY_ORDERS, { fetchPolicy: "network-only" });
  const orders = data?.myOrders ?? [];
  // Mientras alguna reserva siga en proceso, se refresca sola hasta llegar a un estado final.
  const inProgress = orders.some((o) => !FINAL_ORDER_STATUSES.has(o.status));
  useEffect(() => {
    if (!inProgress) return undefined;
    startPolling(2000);
    return stopPolling;
  }, [inProgress, startPolling, stopPolling]);

  if (loading) return <p className="empty">Cargando tus reservas…</p>;
  if (error) return <p className="alert alert-error" role="alert">{errorText(error)}</p>;
  if (orders.length === 0) {
    return <p className="empty">Todavía no tienes reservas. Busca un paquete y reserva el primero.</p>;
  }
  return (
    <ul className="orders">
      {orders.map((o) => {
        const [label, klass] = STATUS[o.status] ?? [o.status, "pill-run"];
        return (
          <li key={o.id} className="order">
            <div className="order-head">
              <div>
                <strong>{o.items.find((i) => i.kind === "FLIGHT")?.title}</strong>
                <div className="hint">{new Date(o.createdAt).toLocaleString("es-CO")}{o.invoiceNumber ? `, factura ${o.invoiceNumber}` : ""}</div>
              </div>
              <div className="order-side">
                <span className={`pill ${klass}`}>{label}</span>
                <span className="order-total">{money(o.totalCents, o.currency)}</span>
              </div>
            </div>
            <details>
              <summary>Ver detalle y pasos de la SAGA</summary>
              <ul className="summary">
                {o.items.map((i) => (
                  <li key={i.kind}><span>{i.title}<br /><span className="hint">{i.detail}</span></span><span>{money(i.priceCents, o.currency)}</span></li>
                ))}
              </ul>
              <SagaTimeline steps={o.steps} />
            </details>
          </li>
        );
      })}
    </ul>
  );
}
