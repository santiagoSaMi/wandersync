import { useMutation, useQuery } from "@apollo/client";
import { AnimatePresence, motion } from "motion/react";
import { ArrowDownRight, CircleAlert, FlaskConical, Gift, RotateCcw, SlidersHorizontal, Undo2, Wallet } from "lucide-react";
import { DEMO_SET_WALLET, WALLET } from "../graphql.js";
import { errorText, money, signedMoney } from "../format.js";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import AnimatedMoney from "./AnimatedMoney.jsx";

const INITIAL_CENTS = 500000;
export const MOVEMENT = {
  OPENING: { label: "Saldo inicial de bienvenida", icon: Gift, tone: "text-sky-700 bg-sky/15" },
  DEBIT: { label: "Pago de reserva (SAGA)", icon: ArrowDownRight, tone: "text-destructive bg-destructive/10" },
  REFUND: { label: "Reembolso: la SAGA revirtió la reserva", icon: Undo2, tone: "text-success bg-success/12" },
  ADJUSTMENT: { label: "Ajuste de demostración", icon: SlidersHorizontal, tone: "text-[oklch(0.5_0.12_60)] bg-warning/15" },
};

export default function WalletPanel() {
  const { data, loading, error } = useQuery(WALLET, { fetchPolicy: "cache-and-network" });
  const [setDemo, { loading: saving, error: demoError }] = useMutation(DEMO_SET_WALLET);
  const wallet = data?.wallet;

  if (loading && !wallet) return <Skeleton className="h-64 rounded-2xl" />;
  if (error && !wallet) {
    return (
      <p className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive" role="alert">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{errorText(error)}
      </p>
    );
  }
  if (!wallet) return null;
  const spentPct = Math.min(100, Math.max(0, ((INITIAL_CENTS - wallet.balanceCents) / INITIAL_CENTS) * 100));

  return (
    <Card className="overflow-hidden">
      <div className="relative isolate overflow-hidden bg-midnight p-6 text-white sm:p-7">
        <div aria-hidden="true" className="hero-grid absolute inset-0 -z-10 opacity-60" />
        <div aria-hidden="true" className="absolute -right-16 -top-20 -z-10 size-64 rounded-full bg-coral/30 blur-3xl" />
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-white/70">
              <Wallet className="size-4" aria-hidden="true" />Tu billetera
            </p>
            <p className="mt-2 font-serif text-5xl leading-none sm:text-6xl" aria-label={`Saldo: ${money(wallet.balanceCents, wallet.currency, { exact: true })}`}>
              <AnimatedMoney cents={wallet.balanceCents} currency={wallet.currency} />
            </p>
            <p className="mt-2 text-sm text-white/65">
              Cada reserva se paga desde aquí. Si un paso de la SAGA falla, el dinero vuelve solo.
            </p>
          </div>
          <div className="w-full max-w-56">
            <div className="mb-1.5 flex justify-between text-xs text-white/65">
              <span>Usado</span><span className="font-mono">{spentPct.toFixed(0)}% de {money(INITIAL_CENTS, wallet.currency)}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/15">
              <motion.div className="h-full rounded-full bg-coral" initial={false} animate={{ width: `${spentPct}%` }}
                transition={{ type: "spring", duration: 0.8, bounce: 0 }} />
            </div>
          </div>
        </div>
      </div>

      <div className="p-5 sm:p-6">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">Movimientos</h3>
        <ol className="divide-y divide-dashed" aria-label="Movimientos de la billetera">
          <AnimatePresence initial={false}>
            {wallet.movements.map((m) => {
              const meta = MOVEMENT[m.kind] ?? MOVEMENT.ADJUSTMENT;
              const Icon = meta.icon;
              return (
                <motion.li key={`${m.kind}-${m.orderId}-${m.createdAt}`} layout
                  initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
                  className="flex items-center gap-3 py-3 text-sm">
                  <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl", meta.tone)}><Icon className="size-4" aria-hidden="true" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{meta.label}</span>
                    <span className="block text-xs text-muted-foreground">
                      {new Date(m.createdAt).toLocaleString("es-CO")}
                      {m.orderId && <> · orden <span className="font-mono">{m.orderId.slice(0, 8)}</span></>}
                    </span>
                  </span>
                  <span className="text-right">
                    <span className={cn("block font-mono font-medium tabular-nums", m.amountCents < 0 ? "text-destructive" : "text-success")}>
                      {signedMoney(m.amountCents, wallet.currency)}
                    </span>
                    <span className="block font-mono text-xs text-muted-foreground tabular-nums">
                      saldo {money(m.balanceAfterCents, wallet.currency, { exact: true })}
                    </span>
                  </span>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ol>

        <div className="mt-5 flex flex-wrap items-center gap-2 rounded-xl border border-dashed p-3 text-[13px]">
          <span className="mr-auto flex items-center gap-1.5 font-medium"><FlaskConical className="size-4 text-coral" aria-hidden="true" />Modo demostración</span>
          <Button size="sm" variant="outline" disabled={saving}
            onClick={() => setDemo({ variables: { cents: 10000 } }).catch(() => {})}>
            Dejar saldo en US$ 100
          </Button>
          <Button size="sm" variant="outline" disabled={saving}
            onClick={() => setDemo({ variables: { cents: INITIAL_CENTS } }).catch(() => {})}>
            <RotateCcw aria-hidden="true" />Restaurar US$ 5.000
          </Button>
          {demoError && <p className="w-full text-destructive" role="alert">{errorText(demoError)}</p>}
        </div>
      </div>
    </Card>
  );
}
