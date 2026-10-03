import { useEffect } from "react";
import { useQuery } from "@apollo/client";
import { motion } from "motion/react";
import { BedDouble, Car, ChevronDown, CircleAlert, Luggage, Plane, Receipt } from "lucide-react";
import { FINAL_ORDER_STATUSES, MY_ORDERS } from "../graphql.js";
import { errorText, money } from "../format.js";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import SagaTimeline from "./SagaTimeline.jsx";

const STATUS = {
  CONFIRMED: ["Confirmada", "success"],
  CANCELLED: ["Cancelada y revertida", "warning"],
  PENDING: ["En proceso", "info"],
  COMPENSATING: ["Revirtiendo", "info"],
  COMPENSATION_FAILED: ["Reversión pendiente", "destructive"],
};
const ITEM_ICON = { FLIGHT: Plane, HOTEL: BedDouble, CAR: Car };

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

  if (loading) {
    return (
      <div className="space-y-4" aria-busy="true">
        <p className="sr-only">Cargando tus reservas…</p>
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
      </div>
    );
  }
  if (error) {
    return (
      <p className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive" role="alert">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{errorText(error)}
      </p>
    );
  }
  if (orders.length === 0) {
    return (
      <Card className="grid place-items-center gap-3 px-6 py-14 text-center">
        <span className="grid size-14 place-items-center rounded-2xl bg-secondary"><Luggage className="size-6 text-muted-foreground" aria-hidden="true" /></span>
        <p className="text-muted-foreground">Todavía no tienes reservas. Busca un paquete y reserva el primero.</p>
      </Card>
    );
  }
  return (
    <ul className="space-y-4">
      {orders.map((o, i) => {
        const [label, variant] = STATUS[o.status] ?? [o.status, "info"];
        return (
          <motion.li key={o.id} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i, 8) * 0.05, type: "spring", duration: 0.5, bounce: 0.15 }}>
            <Card className="overflow-hidden">
              <div className="flex flex-wrap items-start justify-between gap-4 p-5 sm:p-6">
                <div className="min-w-0">
                  <p className="font-medium">{o.items.find((it) => it.kind === "FLIGHT")?.title}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                    <span>{new Date(o.createdAt).toLocaleString("es-CO")}</span>
                    {o.invoiceNumber && (
                      <span className="inline-flex items-center gap-1 font-mono text-xs">
                        <Receipt className="size-3.5" aria-hidden="true" />factura {o.invoiceNumber}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <Badge variant={variant}>{label}</Badge>
                  <span className="font-serif text-3xl leading-none">{money(o.totalCents, o.currency)}</span>
                </div>
              </div>
              <details className="group border-t border-dashed">
                <summary className="flex list-none items-center justify-between px-5 py-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground sm:px-6 [&::-webkit-details-marker]:hidden">
                  Ver detalle y pasos de la SAGA
                  <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden="true" />
                </summary>
                <div className="space-y-5 px-5 pb-6 sm:px-6">
                  <ul className="divide-y divide-dashed rounded-xl border bg-secondary/40">
                    {o.items.map((it) => {
                      const Icon = ITEM_ICON[it.kind] ?? Receipt;
                      return (
                        <li key={it.kind} className="flex items-center gap-3 px-4 py-3 text-sm">
                          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                          <span className="min-w-0 flex-1">{it.title}<span className="block text-xs text-muted-foreground">{it.detail}</span></span>
                          <span className="font-mono tabular-nums">{money(it.priceCents, o.currency)}</span>
                        </li>
                      );
                    })}
                  </ul>
                  <SagaTimeline steps={o.steps} payment={{ amountCents: o.totalCents, currency: o.currency }} />
                </div>
              </details>
            </Card>
          </motion.li>
        );
      })}
    </ul>
  );
}
