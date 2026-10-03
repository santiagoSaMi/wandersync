import { ArrowRight, BedDouble, Car, Plane, Star } from "lucide-react";
import { duration, money, stopsLabel } from "../format.js";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

function Segment({ icon: Icon, label, price, children }) {
  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-start gap-x-4 py-4 first:pt-0 last:pb-0">
      <span className="grid size-10 place-items-center rounded-xl bg-secondary text-foreground/80">
        <Icon className="size-[18px]" aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
        <div className="mt-0.5 space-y-1">{children}</div>
      </div>
      <span className="pt-4 font-mono text-sm tabular-nums text-foreground/80">{price}</span>
    </div>
  );
}

export default function PackageTicket({ pkg, onBook }) {
  const { flight: f, hotel: h, car: c, currency } = pkg;
  return (
    <article className="group grid overflow-hidden rounded-2xl border bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_16px_40px_-24px_rgb(20_33_61/0.35)] transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_1px_2px_rgb(0_0_0/0.04),0_24px_48px_-20px_rgb(20_33_61/0.4)] md:grid-cols-[1fr_240px]">
      <div className="divide-y divide-dashed p-5 sm:p-6">
        <Segment icon={Plane} label="Vuelo" price={money(f.priceCents, currency)}>
          <p className="font-medium">
            {f.airline} <span className="font-mono text-[13px] text-muted-foreground">{f.flightNumber}</span>
          </p>
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
            <span className="font-mono text-foreground">{f.departureTime}</span>
            <span className="inline-flex items-center gap-1" aria-hidden="true">
              <span className="h-px w-6 bg-border" /><Plane className="size-3 rotate-45" /><span className="h-px w-6 bg-border" />
            </span>
            <span className="sr-only">a</span>
            <span className="font-mono text-foreground">{f.arrivalTime}</span>
            <span>· {stopsLabel(f.stops)} · {duration(f.durationMinutes)}</span>
          </p>
        </Segment>
        <Segment icon={BedDouble} label="Hotel" price={money(h.totalPriceCents, currency)}>
          <p className="flex flex-wrap items-center gap-x-2 font-medium">
            {h.name}
            <span className="inline-flex text-warning" role="img" aria-label={`${h.stars} estrellas`}>
              {Array.from({ length: h.stars }, (_, i) => <Star key={i} className="size-3.5 fill-current" aria-hidden="true" />)}
            </span>
          </p>
          <p className="text-sm text-muted-foreground">
            {pkg.nights} noches{h.rating ? `, puntuación ${String(h.rating).replace(".", ",")}` : ""}
          </p>
          {h.amenities?.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {h.amenities.slice(0, 3).map((a) => <Badge key={a} variant="secondary" className="font-normal">{a}</Badge>)}
            </div>
          )}
        </Segment>
        <Segment icon={Car} label="Auto" price={money(c.totalPriceCents, currency)}>
          <p className="font-medium">{c.company} {c.model}</p>
          <p className="text-sm text-muted-foreground">
            {c.category}, {c.transmission === "AUTOMATIC" ? "automático" : "manual"}, {c.seats} asientos
          </p>
        </Segment>
      </div>

      {/* Talón de la tarjeta de embarque: línea perforada con muescas */}
      <div className="relative flex items-center justify-between gap-4 border-t border-dashed bg-secondary/50 p-5 sm:p-6 md:flex-col md:items-stretch md:justify-center md:border-t-0 md:border-l">
        <span aria-hidden="true" className="absolute -top-3 -left-3 hidden size-6 rounded-full bg-background md:block" />
        <span aria-hidden="true" className="absolute -bottom-3 -left-3 hidden size-6 rounded-full bg-background md:block" />
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Total del paquete</span>
          <p className="mt-1 font-serif text-[2.6rem] leading-none tracking-tight">{money(pkg.totalPriceCents, currency)}</p>
          <p className="mt-1.5 text-xs text-muted-foreground">Vuelo + {pkg.nights} noches + auto</p>
        </div>
        <Button variant="coral" size="lg" className="md:mt-5 md:w-full" onClick={() => onBook(pkg)}>
          Reservar <ArrowRight className="transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
        </Button>
      </div>
    </article>
  );
}
