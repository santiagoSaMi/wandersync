import { motion } from "motion/react";
import { ArrowUpRight, Plane } from "lucide-react";
import { cn } from "@/lib/utils";
import { Carousel, CarouselContent, CarouselItem, CarouselNext, CarouselPrevious } from "@/components/ui/carousel";

// Paleta por destino (clases completas para que Tailwind las detecte al compilar).
const PALETTE = {
  CTG: "from-[oklch(0.72_0.17_45)] to-[oklch(0.55_0.2_15)]",
  MDE: "from-[oklch(0.68_0.14_150)] to-[oklch(0.45_0.11_175)]",
  SMR: "from-[oklch(0.78_0.15_75)] to-[oklch(0.6_0.18_35)]",
  ADZ: "from-[oklch(0.75_0.12_200)] to-[oklch(0.5_0.13_230)]",
  CLO: "from-[oklch(0.7_0.16_120)] to-[oklch(0.5_0.13_150)]",
  MIA: "from-[oklch(0.72_0.15_330)] to-[oklch(0.55_0.18_290)]",
  MAD: "from-[oklch(0.62_0.17_20)] to-[oklch(0.38_0.12_330)]",
  CUN: "from-[oklch(0.78_0.12_185)] to-[oklch(0.55_0.12_215)]",
  PTY: "from-[oklch(0.6_0.13_265)] to-[oklch(0.35_0.1_280)]",
};
const FALLBACK = "from-[oklch(0.55_0.08_262)] to-[oklch(0.3_0.06_262)]";

export default function Destinations({ routes, selected, onPick }) {
  if (!routes?.length) return null;
  return (
    <section aria-labelledby="destinos" className="space-y-6">
      <Carousel opts={{ align: "start", dragFree: true }}>
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-coral">Rutas del catálogo</p>
            <h2 id="destinos" className="mt-1 font-serif text-4xl leading-tight sm:text-5xl">¿A dónde vamos?</h2>
            <p className="mt-1 text-sm text-muted-foreground">Elige una ruta y armamos los paquetes al instante.</p>
          </div>
          <div className="hidden gap-2 sm:flex">
            <CarouselPrevious />
            <CarouselNext />
          </div>
        </div>
        <CarouselContent className="mt-6 py-2">
          {routes.map((r, i) => {
            const active = selected?.origin === r.origin && selected?.destination === r.destination;
            return (
              <CarouselItem key={`${r.origin}-${r.destination}`} className="basis-[78%] sm:basis-1/2 lg:basis-1/4">
                <motion.button
                  type="button"
                  onClick={() => onPick(r)}
                  aria-label={`Ver paquetes ${r.originCity} a ${r.destinationCity}`}
                  aria-pressed={active}
                  initial={{ opacity: 0, y: 16 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }}
                  transition={{ delay: Math.min(i, 6) * 0.05, type: "spring", duration: 0.6, bounce: 0.15 }}
                  whileHover={{ y: -4 }}
                  className={cn(
                    "group relative flex h-56 w-full flex-col justify-between overflow-hidden rounded-2xl bg-linear-to-br p-5 text-left text-white shadow-lg outline-none",
                    "focus-visible:ring-4 focus-visible:ring-ring/40",
                    PALETTE[r.destination] ?? FALLBACK,
                    active && "ring-4 ring-coral/70 ring-offset-2 ring-offset-background"
                  )}
                >
                  <span aria-hidden="true" className="absolute -right-10 -top-10 size-40 rounded-full bg-white/10 blur-xl transition-transform duration-500 group-hover:scale-125" />
                  <span aria-hidden="true" className="absolute inset-x-5 top-1/2 border-t border-dashed border-white/30" />
                  <div className="relative flex items-center justify-between font-mono text-sm">
                    <span className="rounded-full bg-black/15 px-2.5 py-1 backdrop-blur-sm">{r.origin}</span>
                    <Plane className="size-4 opacity-90" aria-hidden="true" />
                    <span className="rounded-full bg-black/15 px-2.5 py-1 backdrop-blur-sm">{r.destination}</span>
                  </div>
                  <div className="relative">
                    <p className="text-xs uppercase tracking-[0.16em] text-white/75">desde {r.originCity}</p>
                    <p className="font-serif text-[2.1rem] leading-[1.05]">{r.destinationCity}</p>
                    <span className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-white/90">
                      Ver paquetes <ArrowUpRight className="size-4 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden="true" />
                    </span>
                  </div>
                </motion.button>
              </CarouselItem>
            );
          })}
        </CarouselContent>
      </Carousel>
    </section>
  );
}
