import { AnimatePresence, motion } from "motion/react";
import { BedDouble, Car, Check, CreditCard, LoaderCircle, Plane, Receipt, Undo2, X } from "lucide-react";
import { cn } from "@/lib/utils";

const LABEL = { PAYMENT: "Pago", FLIGHT: "Reserva del vuelo", HOTEL: "Reserva del hotel", CAR: "Reserva del auto", INVOICE: "Factura" };
const UNDO = {
  PAYMENT: "Reembolso del pago",
  FLIGHT: "Cancelación del vuelo",
  HOTEL: "Cancelación del hotel",
  CAR: "Cancelación del auto",
  INVOICE: "Anulación de la factura",
};
const SERVICE_ICON = { PAYMENT: CreditCard, FLIGHT: Plane, HOTEL: BedDouble, CAR: Car, INVOICE: Receipt };
const TOTAL_STEPS = 5;

const TONE = {
  good: { node: "bg-success text-white ring-success/20", text: "text-success" },
  bad: { node: "bg-destructive text-white ring-destructive/20", text: "text-destructive" },
  undo: { node: "bg-warning text-white ring-warning/25", text: "text-[oklch(0.52_0.12_60)]" },
  run: { node: "bg-coral text-white ring-coral/25", text: "text-coral" },
};

function Step({ step, undo, last }) {
  const done = step.status === "COMPLETED";
  const failed = step.status === "FAILED";
  const tone = failed ? "bad" : done ? (undo ? "undo" : "good") : "run";
  const status = undo
    ? (done ? "Revertido" : failed ? "No se pudo revertir" : "Revirtiendo")
    : (done ? "Listo" : failed ? "Falló" : "En curso");
  const NodeIcon = failed ? X : done ? (undo ? Undo2 : Check) : LoaderCircle;
  const ServiceIcon = SERVICE_ICON[step.name] ?? Check;

  return (
    <motion.li
      layout
      initial={{ opacity: 0, x: -14 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ type: "spring", duration: 0.5, bounce: 0.2 }}
      className="relative flex gap-4 pb-5 last:pb-0"
    >
      {!last && <span aria-hidden="true" className="absolute left-[15px] top-9 bottom-0 w-px bg-border" />}
      <span className={cn("relative z-[1] mt-0.5 grid size-8 shrink-0 place-items-center rounded-full ring-4", TONE[tone].node)}>
        <NodeIcon className={cn("size-4", tone === "run" && "animate-spin")} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <ServiceIcon className="size-4 text-muted-foreground" aria-hidden="true" />
          <strong className="font-medium">{(undo ? UNDO : LABEL)[step.name]}</strong>
          <span className={cn("text-xs font-semibold uppercase tracking-wider", TONE[tone].text)}>{status}</span>
        </div>
        {step.attempts > 1 && <div className="mt-1 text-xs text-muted-foreground">{step.attempts} intentos</div>}
        {step.error && (
          <div className="mt-1.5 rounded-md bg-destructive/8 px-2.5 py-1.5 font-mono text-xs text-destructive">{step.error}</div>
        )}
      </div>
    </motion.li>
  );
}

export default function SagaTimeline({ steps }) {
  const forward = steps.filter((s) => s.phase === "EXECUTE");
  const undo = steps.filter((s) => s.phase === "COMPENSATE");
  const completed = forward.filter((s) => s.status === "COMPLETED").length;
  const failed = forward.some((s) => s.status === "FAILED");
  const reverted = undo.filter((s) => s.status === "COMPLETED").length;
  const pct = failed ? (undo.length ? (reverted / undo.length) * 100 : 0) : (completed / TOTAL_STEPS) * 100;

  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 flex items-baseline justify-between text-xs text-muted-foreground">
          <span className="font-medium uppercase tracking-wider">{failed ? "Compensación" : "Avance de la SAGA"}</span>
          <span className="font-mono">
            {failed ? `${reverted}/${undo.length} revertidos` : `${completed}/${TOTAL_STEPS} pasos`}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
          <motion.div
            className={cn("h-full rounded-full", failed ? "bg-warning" : "bg-success")}
            initial={false}
            animate={{ width: `${pct}%` }}
            transition={{ type: "spring", duration: 0.8, bounce: 0 }}
          />
        </div>
      </div>

      <ol className="rounded-xl border bg-secondary/40 p-4" aria-label="Pasos de la reserva">
        <AnimatePresence initial={false}>
          {forward.map((s, i) => <Step key={`${s.name}-${s.phase}`} step={s} last={i === forward.length - 1} />)}
        </AnimatePresence>
      </ol>

      <AnimatePresence>
        {undo.length > 0 && (
          <motion.div key="undo" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
            <p className="flex items-center gap-2 text-sm font-medium text-[oklch(0.52_0.12_60)]">
              <Undo2 className="size-4" aria-hidden="true" />
              Un paso falló: se revierte lo que ya estaba hecho, en orden inverso:
            </p>
            <ol className="rounded-xl border border-warning/30 bg-warning/6 p-4" aria-label="Pasos de reversión">
              <AnimatePresence initial={false}>
                {undo.map((s, i) => <Step key={`${s.name}-${s.phase}`} step={s} undo last={i === undo.length - 1} />)}
              </AnimatePresence>
            </ol>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
