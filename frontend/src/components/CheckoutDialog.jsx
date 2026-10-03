import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@apollo/client";
import { AnimatePresence, motion } from "motion/react";
import { ArrowDownRight, ArrowRight, BedDouble, Car, CircleAlert, CircleCheck, ExternalLink, FlaskConical, LoaderCircle, Lock, Plane, ShieldCheck, Undo2, Wallet, Workflow } from "lucide-react";
import { CREATE_BOOKING, FINAL_ORDER_STATUSES, ORDER, WALLET } from "../graphql.js";
import { errorText, money, newIdempotencyKey, signedMoney } from "../format.js";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import AnimatedMoney from "./AnimatedMoney.jsx";
import Dialog from "./Dialog.jsx";
import SagaTimeline from "./SagaTimeline.jsx";

// Barra de acciones fija al pie del diálogo: los botones siguen visibles aunque la línea de tiempo crezca.
const ACTION_BAR =
  "sticky bottom-0 z-10 -mx-6 -mb-5 flex flex-col-reverse gap-2 border-t bg-card/95 px-6 py-4 backdrop-blur sm:flex-row sm:justify-end";

const FAILURES = [
  ["NONE", "Ninguno"],
  ["PAYMENT", "El pago"],
  ["FLIGHT", "La reserva del vuelo"],
  ["HOTEL", "La reserva del hotel"],
  ["CAR", "La reserva del auto"],
];

function Alert({ tone, icon: Icon, children }) {
  const tones = {
    ok: "border-success/30 bg-success/8 text-[oklch(0.38_0.09_160)]",
    warn: "border-warning/35 bg-warning/10 text-[oklch(0.42_0.1_60)]",
    run: "border-coral/30 bg-coral/8 text-[oklch(0.45_0.14_38)]",
    error: "border-destructive/30 bg-destructive/8 text-destructive",
  };
  return (
    <div className={cn("flex items-start gap-3 rounded-xl border px-4 py-3 text-sm", tones[tone])} role={tone === "error" ? "alert" : "status"}>
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone === "run" && "animate-spin")} aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}

// Billetera EN VIVO durante la SAGA: el saldo baja cuando el PAGO se completa y vuelve si la compensación
// lo reembolsa. Se deriva de los pasos de la orden (los mismos que la línea de tiempo), sin consultas extra.
function LiveWallet({ startCents, totalCents, currency, steps }) {
  const done = (phase) => steps.some((s) => s.name === "PAYMENT" && s.phase === phase && s.status === "COMPLETED");
  const debited = done("EXECUTE");
  const refunded = debited && done("COMPENSATE");
  const balance = startCents - (debited ? totalCents : 0) + (refunded ? totalCents : 0);
  const lines = [];
  if (debited) lines.push(["debit", ArrowDownRight, "Pago de la reserva", -totalCents]);
  if (refunded) lines.push(["refund", Undo2, "Reembolso por compensación", totalCents]);
  return (
    <section aria-label="Tu billetera" className="rounded-xl border bg-secondary/40 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-medium"><Wallet className="size-4 text-coral" aria-hidden="true" />Tu billetera</span>
        <span className="font-serif text-2xl leading-none"><AnimatedMoney cents={balance} currency={currency} /></span>
      </div>
      <ol className="mt-3 space-y-1.5 text-sm">
        <li className="flex justify-between text-muted-foreground">
          <span>Saldo antes de reservar</span><span className="font-mono tabular-nums">{money(startCents, currency, { exact: true })}</span>
        </li>
        <AnimatePresence initial={false}>
          {lines.map(([key, Icon, label, cents]) => (
            <motion.li key={key} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}
              className={cn("flex items-center justify-between font-medium", cents < 0 ? "text-destructive" : "text-success")}>
              <span className="flex items-center gap-1.5"><Icon className="size-4" aria-hidden="true" />{label}</span>
              <span className="font-mono tabular-nums">{signedMoney(cents, currency)}</span>
            </motion.li>
          ))}
        </AnimatePresence>
        {!debited && <li className="text-xs text-muted-foreground">Esperando el paso de pago…</li>}
      </ol>
    </section>
  );
}

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

  // Billetera: saldo antes de pagar (validación en pantalla) y refresco al terminar la SAGA (o si se rechazó).
  const { data: walletData, refetch: refetchWallet } = useQuery(WALLET, { fetchPolicy: "cache-and-network" });
  const wallet = walletData?.wallet;
  const startRef = useRef(null);
  if (!order && wallet) startRef.current = wallet.balanceCents;
  const insufficient = !order && wallet != null && wallet.balanceCents < pkg.totalPriceCents;
  const finished = order != null && FINAL_ORDER_STATUSES.has(order.status);
  useEffect(() => {
    if (finished || error) refetchWallet().catch(() => {});
  }, [finished, error, refetchWallet]);

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
  const lines = [
    [Plane, `Vuelo ${pkg.flight.airline} ${pkg.flight.flightNumber}`, pkg.flight.priceCents],
    [BedDouble, `${pkg.hotel.name}, ${pkg.nights} noches`, pkg.hotel.totalPriceCents],
    [Car, `Auto ${pkg.car.company} ${pkg.car.model}`, pkg.car.totalPriceCents],
  ];

  return (
    <Dialog title={title} onClose={onClose} wide
      description={!order ? "Revisa tu paquete. El precio se valida de nuevo contra el catálogo al pagar." : undefined}>
      <AnimatePresence mode="wait" initial={false}>
        {!order ? (
          <motion.div key="confirm" exit={{ opacity: 0, y: -8 }} className="space-y-5">
            <ul className="divide-y divide-dashed rounded-xl border bg-secondary/40">
              {lines.map(([Icon, text, cents]) => (
                <li key={text} className="flex items-center gap-3 px-4 py-3 text-sm">
                  <span className="grid size-8 place-items-center rounded-lg bg-card shadow-xs"><Icon className="size-4 text-muted-foreground" aria-hidden="true" /></span>
                  <span className="min-w-0 flex-1 truncate">{text}</span>
                  <span className="font-mono tabular-nums">{money(cents, pkg.currency)}</span>
                </li>
              ))}
              <li className="flex items-baseline justify-between px-4 py-3.5">
                <span className="text-sm font-medium">Total</span>
                <span className="font-serif text-3xl leading-none">{money(pkg.totalPriceCents, pkg.currency)}</span>
              </li>
            </ul>

            {wallet && (
              <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border px-4 py-3 text-sm",
                insufficient ? "border-destructive/30 bg-destructive/8" : "bg-card")}>
                <span className="flex items-center gap-2 font-medium"><Wallet className="size-4 text-coral" aria-hidden="true" />Pagas con tu billetera</span>
                <span className="ml-auto flex items-center gap-2 font-mono tabular-nums">
                  {money(wallet.balanceCents, wallet.currency, { exact: true })}
                  {!insufficient && (
                    <>
                      <ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />
                      <span className="font-semibold">{money(wallet.balanceCents - pkg.totalPriceCents, wallet.currency, { exact: true })}</span>
                    </>
                  )}
                </span>
                {insufficient && (
                  <p className="flex w-full items-start gap-2 text-destructive" role="alert">
                    <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                    Saldo insuficiente: tu billetera no alcanza para este paquete. Elige uno más económico o ajusta el saldo en Mis reservas (modo demostración).
                  </p>
                )}
              </div>
            )}

            <fieldset className="rounded-xl border border-dashed p-4">
              <legend className="flex items-center gap-1.5 px-1.5 text-sm font-medium">
                <FlaskConical className="size-4 text-coral" aria-hidden="true" />
                Modo demostración: forzar un fallo en
              </legend>
              <p className="mb-3 text-[13px] text-muted-foreground">
                Sirve para ver cómo la reserva se revierte sola cuando un paso falla (patrón SAGA).
              </p>
              <div className="flex flex-wrap gap-2">
                {FAILURES.map(([value, label]) => (
                  <label key={value}
                    className={cn("inline-flex items-center rounded-full border px-3.5 py-1.5 text-[13px] transition-colors",
                      "has-[:focus-visible]:ring-4 has-[:focus-visible]:ring-ring/25",
                      failure === value ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-secondary")}>
                    <input type="radio" name="failure" value={value} className="sr-only"
                      checked={failure === value} onChange={() => setFailure(value)} />
                    {label}
                  </label>
                ))}
              </div>
              <label className="mt-4 flex items-center gap-3 border-t border-dashed pt-4 text-[13px]">
                <input type="checkbox" checked={slowMotion} onChange={(e) => setSlowMotion(e.target.checked)}
                  className="peer sr-only" />
                <span aria-hidden="true"
                  className="relative inline-flex h-5 w-9 shrink-0 rounded-full bg-input transition-colors peer-checked:bg-coral peer-focus-visible:ring-4 peer-focus-visible:ring-ring/25 after:absolute after:left-0.5 after:top-0.5 after:size-4 after:rounded-full after:bg-white after:shadow after:transition-transform peer-checked:after:translate-x-4" />
                Mostrar la SAGA paso a paso (lento, para la presentación)
              </label>
            </fieldset>

            {error && <Alert tone="error" icon={CircleAlert}>{errorText(error)}</Alert>}
            <p className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5" aria-hidden="true" />
              Es una simulación: no se ingresan ni se guardan datos de tarjeta.
            </p>
            <div className={ACTION_BAR}>
              <Button variant="ghost" onClick={onClose}>Cancelar</Button>
              <Button variant="coral" size="lg" onClick={submit} disabled={loading || insufficient}>
                {loading ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Lock aria-hidden="true" />}
                {loading ? "Procesando reserva…" : `Pagar ${money(pkg.totalPriceCents, pkg.currency)} y reservar`}
              </Button>
            </div>
          </motion.div>
        ) : (
          <motion.div key="result" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5">
            {processing && slowMotion ? (
              <Alert tone="run" icon={LoaderCircle}>Modo paso a paso: cada paso de la SAGA aparece aquí mientras ocurre.</Alert>
            ) : processing ? (
              <Alert tone="run" icon={LoaderCircle}>
                Tu reserva se está procesando; uno de los servicios está tardando más de lo normal.
                Esta pantalla se actualiza sola, y también la verás en Mis reservas.
              </Alert>
            ) : order.status === "CONFIRMED" ? (
              <Alert tone="ok" icon={CircleCheck}>
                Reserva confirmada. Factura {order.invoiceNumber}. Total {money(order.totalCents, order.currency)}.
              </Alert>
            ) : (
              <Alert tone="warn" icon={Undo2}>
                {order.status === "CANCELLED"
                  ? "No pudimos completar tu reserva. Revertimos todo lo que se había hecho y el dinero volvió a tu billetera."
                  : "La reserva no se completó y alguna reversión sigue pendiente. El sistema la reintentará automáticamente."}
              </Alert>
            )}
            {startRef.current != null && (
              <LiveWallet startCents={startRef.current} totalCents={order.totalCents} currency={order.currency} steps={order.steps} />
            )}
            {/* Cada SAGA se refleja en Prefect como el flow run "SAGA reserva <orden>" (grafo con flechas, en vivo) */}
            <a href="http://localhost:4200/runs" target="_blank" rel="noreferrer"
              className="flex items-center justify-between gap-3 rounded-xl border border-dashed px-4 py-2.5 text-sm transition-colors hover:bg-secondary">
              <span className="flex flex-wrap items-center gap-x-2 text-muted-foreground">
                <Workflow className="size-4" aria-hidden="true" />
                Orden <span className="font-mono text-foreground">{order.id.slice(0, 8)}</span>
                · en Prefect: <span className="font-medium text-foreground">SAGA reserva {order.id.slice(0, 8)}</span>
              </span>
              <ExternalLink className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            </a>
            <SagaTimeline steps={order.steps} payment={{ amountCents: order.totalCents, currency: order.currency }} />
            <div className={ACTION_BAR}>
              {!processing && order.status !== "CONFIRMED" && <Button variant="outline" onClick={retry}>Intentar de nuevo</Button>}
              <Button variant="ghost" onClick={onClose}>Cerrar</Button>
              <Button onClick={onViewOrders}>Ver mis reservas</Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Dialog>
  );
}
