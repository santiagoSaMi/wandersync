import { useEffect, useRef, useState } from "react";
import { animate, useReducedMotion } from "motion/react";
import { money } from "../format.js";
import { cn } from "@/lib/utils";

// Muestra un monto que "rueda" hasta su nuevo valor cuando cambia (p. ej. el saldo al debitar o reembolsar).
// Con prefers-reduced-motion salta directo al valor final.
export default function AnimatedMoney({ cents, currency = "USD", className }) {
  const [shown, setShown] = useState(cents);
  const from = useRef(cents);
  const reduce = useReducedMotion();

  useEffect(() => {
    if (reduce || from.current === cents) {
      from.current = cents;
      setShown(cents);
      return undefined;
    }
    const controls = animate(from.current, cents, {
      duration: 0.9, ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => { from.current = v; setShown(Math.round(v)); },
    });
    return () => controls.stop();
  }, [cents, reduce]);

  return <span className={cn("tabular-nums", className)}>{money(shown, currency, { exact: true })}</span>;
}
