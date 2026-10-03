import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import { useLenis } from "lenis/react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

// <dialog> nativo (foco atrapado, Escape, fondo inerte) con estilo shadcn y entrada animada con Motion.
// Se evita el Dialog de Radix: su bloqueo de scroll inyecta <style> y la CSP de nginx lo bloquearía.
// Mientras está abierto se pausa Lenis (si no, la rueda del ratón no desplazaría el contenido del diálogo).
export default function Dialog({ title, description, onClose, children, wide = false }) {
  const ref = useRef(null);
  const lenis = useLenis();

  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
  }, []);

  useEffect(() => {
    lenis?.stop();
    return () => lenis?.start();
  }, [lenis]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="dialog-title"
      data-lenis-prevent
      className={cn(
        "m-auto max-h-[min(92dvh,900px)] w-[calc(100%-1.5rem)] overflow-y-auto overscroll-contain rounded-2xl border-0 bg-card p-0 text-card-foreground shadow-2xl",
        "backdrop:bg-midnight/60 backdrop:backdrop-blur-[3px]",
        wide ? "max-w-2xl" : "max-w-md"
      )}
    >
      <motion.div
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", duration: 0.45, bounce: 0.18 }}
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b bg-card/90 px-6 py-4 backdrop-blur">
          <div>
            <h2 id="dialog-title" className="font-serif text-[1.75rem] leading-tight">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
          </div>
          <Button variant="ghost" size="icon-sm" className="-mr-2 mt-1" aria-label="Cerrar" onClick={() => ref.current?.close()}>
            <X />
          </Button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </motion.div>
    </dialog>
  );
}
