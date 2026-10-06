import { useMemo, useRef, useState } from "react";
import { useQuery } from "@apollo/client";
import { AnimatePresence, motion } from "motion/react";
import { useLenis } from "lenis/react";
import {
  ArrowLeft, ArrowRight, BedDouble, CalendarDays, Car, Check, CircleAlert, ClipboardCheck, Clock, MapPin, Plane,
  PlaneLanding, PlaneTakeoff, SearchX, Star, Users,
} from "lucide-react";
import { TRIP_CARS, TRIP_FLIGHTS, TRIP_HOTELS } from "../graphql.js";
import { addMinutes, dayLabel, duration, errorText, money, stopsLabel } from "../format.js";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { resolveSearch } from "./SearchPanel.jsx";

// "Arma tu viaje": el usuario elige vuelo, hotel y auto por separado (fase a fase) y paga como siempre.
const PHASES = [
  { key: "flight", label: "Destino y vuelo", icon: Plane },
  { key: "hotel", label: "Hotel", icon: BedDouble },
  { key: "car", label: "Auto", icon: Car },
  { key: "review", label: "Revisión", icon: ClipboardCheck },
];

const SORTS = {
  flight: [["price", "Más barato", (a, b) => a.priceCents - b.priceCents],
           ["early", "Más temprano", (a, b) => a.departureTime.localeCompare(b.departureTime)],
           ["fast", "Más corto", (a, b) => a.durationMinutes - b.durationMinutes]],
  hotel: [["price", "Más barato", (a, b) => a.totalPriceCents - b.totalPriceCents],
          ["rating", "Mejor puntuación", (a, b) => (b.rating ?? 0) - (a.rating ?? 0)],
          ["stars", "Más estrellas", (a, b) => b.stars - a.stars]],
  car: [["price", "Más barato", (a, b) => a.totalPriceCents - b.totalPriceCents],
        ["seats", "Más asientos", (a, b) => b.seats - a.seats]],
};

// Opciones de hora de recogida del auto a partir de la llegada del vuelo elegido.
export function pickupOptions(flight) {
  if (!flight) return [];
  const options = [["Al aterrizar", flight.arrivalTime], ["+30 min", addMinutes(flight.arrivalTime, 30)],
                   ["+1 h", addMinutes(flight.arrivalTime, 60)], ["+2 h", addMinutes(flight.arrivalTime, 120)]];
  return options.filter(([, time]) => time);
}

// ------------------------------------------------------------------ piezas de interfaz
function Stepper({ phase, unlocked, onGo }) {
  return (
    <ol className="grid grid-cols-4 gap-2" aria-label="Fases del viaje">
      {PHASES.map((p, i) => {
        const Icon = p.icon;
        const state = i === phase ? "current" : i < unlocked ? "done" : "locked";
        return (
          <li key={p.key}>
            <button type="button" onClick={() => onGo(i)} disabled={i > unlocked}
              aria-current={i === phase ? "step" : undefined}
              aria-label={`Fase ${i + 1}: ${p.label}${state === "done" && i !== phase ? " (completada)" : ""}`}
              className={cn("group flex w-full items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors",
                state === "current" && "border-coral bg-coral/8",
                state === "done" && "bg-card hover:bg-secondary",
                state === "locked" && "cursor-not-allowed bg-secondary/40 opacity-60")}>
              <span className={cn("grid size-8 shrink-0 place-items-center rounded-full text-sm font-semibold",
                state === "current" ? "bg-coral text-coral-foreground" : state === "done" ? "bg-success text-white" : "bg-secondary text-muted-foreground")}>
                {state === "done" && i !== phase ? <Check className="size-4" aria-hidden="true" /> : <Icon className="size-4" aria-hidden="true" />}
              </span>
              <span className="hidden min-w-0 sm:block">
                <span className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Fase {i + 1}</span>
                <span className="block truncate text-sm font-medium">{p.label}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function SortChips({ options, value, onChange }) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Ordenar">
      {options.map(([key, label]) => (
        <button key={key} type="button" onClick={() => onChange(key)} aria-pressed={value === key}
          className={cn("rounded-full border px-3 py-1 text-[13px] transition-colors",
            value === key ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-secondary")}>
          {label}
        </button>
      ))}
    </div>
  );
}

function OptionCard({ selected, onSelect, label, children, price, priceNote }) {
  return (
    <motion.button type="button" layout onClick={onSelect} aria-pressed={selected} aria-label={label}
      whileTap={{ scale: 0.99 }}
      className={cn("relative grid w-full grid-cols-[1fr_auto] items-center gap-4 rounded-2xl border bg-card p-4 text-left transition-shadow sm:p-5",
        selected ? "border-coral shadow-[0_0_0_3px] shadow-coral/25" : "hover:shadow-md")}>
      <div className="min-w-0">{children}</div>
      <div className="text-right">
        <p className="font-serif text-2xl leading-none">{price}</p>
        {priceNote && <p className="mt-1 text-xs text-muted-foreground">{priceNote}</p>}
        <span className={cn("mt-3 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium",
          selected ? "bg-coral text-coral-foreground" : "bg-secondary text-muted-foreground")}>
          {selected ? <><Check className="size-3.5" aria-hidden="true" />Elegido</> : "Elegir"}
        </span>
      </div>
    </motion.button>
  );
}

function ListState({ loading, error, empty, emptyText }) {
  if (loading) return <div className="space-y-3" aria-busy="true">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}</div>;
  if (error) {
    return (
      <p className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive" role="alert">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{errorText(error)}
      </p>
    );
  }
  if (empty) {
    return (
      <div className="grid place-items-center gap-2 rounded-2xl border border-dashed bg-card px-6 py-12 text-center text-muted-foreground">
        <SearchX className="size-7" aria-hidden="true" />{emptyText}
      </div>
    );
  }
  return null;
}

function Summary({ selection, flight, hotel, car, pickupTime, total, currency }) {
  const rows = [
    [Plane, "Vuelo", flight && `${flight.airline} ${flight.flightNumber}`, flight && `${flight.departureTime} → ${flight.arrivalTime}`, flight?.priceCents],
    [BedDouble, "Hotel", hotel?.name, hotel && `${hotel.nights} noches`, hotel?.totalPriceCents],
    [Car, "Auto", car && `${car.company} ${car.model}`, car && pickupTime && `Recogida ${pickupTime}`, car?.totalPriceCents],
  ];
  return (
    <Card className="p-5 lg:sticky lg:top-24">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-coral">Tu viaje</p>
      <p className="mt-1 font-serif text-2xl leading-tight">
        {selection.destinationCity ? `${selection.originCity} → ${selection.destinationCity}` : "Elige tu destino"}
      </p>
      {selection.date && <p className="text-sm text-muted-foreground">{dayLabel(selection.date)}</p>}
      <ul className="mt-4 divide-y divide-dashed text-sm" aria-label="Resumen del viaje">
        {rows.map(([Icon, kind, title, sub, cents]) => (
          <li key={kind} className="flex items-start gap-3 py-3">
            <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{kind}</span>
              <AnimatePresence mode="wait" initial={false}>
                <motion.span key={title ?? "none"} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                  className={cn("block truncate", title ? "font-medium" : "italic text-muted-foreground")}>
                  {title ?? "Por elegir"}
                </motion.span>
              </AnimatePresence>
              {sub && <span className="block text-xs text-muted-foreground">{sub}</span>}
            </span>
            {cents != null && <span className="font-mono tabular-nums">{money(cents, currency)}</span>}
          </li>
        ))}
      </ul>
      <div className="mt-2 flex items-baseline justify-between border-t pt-4">
        <span className="text-sm font-medium">Total</span>
        <span className="font-serif text-3xl leading-none">{money(total, currency)}</span>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ asistente
export default function TripBuilder({ catalog, onCheckout }) {
  const [phase, setPhase] = useState(0);
  const [selection, setSelection] = useState({ origin: "", destination: "", date: "" });
  const [flight, setFlight] = useState(null);
  const [hotel, setHotel] = useState(null);
  const [car, setCar] = useState(null);
  const [pickupTime, setPickupTime] = useState(null);
  const [sort, setSort] = useState({ flight: "price", hotel: "price", car: "price" });
  const topRef = useRef(null);
  const lenis = useLenis();

  const search = useMemo(() => resolveSearch(catalog, selection), [catalog, selection]);
  const { origin, destination, date } = search;
  const routeNames = (catalog?.routes ?? []).find((r) => r.origin === origin && r.destination === destination);
  const where = { origin, destination, date, originCity: routeNames?.originCity, destinationCity: routeNames?.destinationCity };

  const flights = useQuery(TRIP_FLIGHTS, { variables: { origin, destination, date }, skip: !(origin && destination && date) });
  const hotels = useQuery(TRIP_HOTELS, { variables: { city: destination, date }, skip: phase < 1 || !flight });
  const cars = useQuery(TRIP_CARS, { variables: { city: destination, date }, skip: phase < 2 || !hotel });

  // Cambiar la ruta o la fecha invalida todo lo elegido después (otra ciudad u otro día).
  function changeSelection(next) {
    setSelection(next);
    setFlight(null); setHotel(null); setCar(null); setPickupTime(null);
  }
  function chooseFlight(f) {
    setFlight(f);
    setPickupTime(f.arrivalTime);             // por defecto, recoger el auto al aterrizar
  }

  const unlocked = !flight ? 0 : !hotel ? 1 : !car ? 2 : 3;
  const currency = flight?.currency ?? hotel?.currency ?? car?.currency ?? "USD";
  const total = (flight?.priceCents ?? 0) + (hotel?.totalPriceCents ?? 0) + (car?.totalPriceCents ?? 0);

  function go(next) {
    if (next > unlocked) return;
    setPhase(next);
    if (topRef.current) {
      if (lenis) lenis.scrollTo(topRef.current, { offset: -96, duration: 0.8 });
      else topRef.current.scrollIntoView?.({ block: "start" });
    }
  }

  function checkout() {
    onCheckout({
      id: `${flight.id}~${hotel.id}~${car.id}`,
      flight, hotel, car, currency, nights: hotel.nights, totalPriceCents: total, carPickupTime: pickupTime,
    });
  }

  const sorted = (list, kind) => {
    const cmp = SORTS[kind].find(([key]) => key === sort[kind])?.[2];
    return [...(list ?? [])].sort(cmp);
  };
  const overnight = flight && flight.arrivalTime < flight.departureTime;

  return (
    <div ref={topRef} className="space-y-8">
      <Stepper phase={phase} unlocked={unlocked} onGo={go} />

      <div className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
        <AnimatePresence mode="wait" initial={false}>
          <motion.section key={phase} aria-label={PHASES[phase].label}
            initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -24 }}
            transition={{ type: "spring", duration: 0.45, bounce: 0.1 }} className="min-w-0 space-y-5">

            {phase === 0 && (
              <>
                <Card className="grid gap-4 p-5 sm:grid-cols-3">
                  <div className="space-y-2">
                    <Label htmlFor="trip-origin">Sales desde</Label>
                    <NativeSelect id="trip-origin" icon={PlaneTakeoff} value={origin}
                      onChange={(e) => changeSelection({ ...selection, origin: e.target.value })}>
                      {search.origins.map((o) => <option key={o.code} value={o.code}>{o.name} ({o.code})</option>)}
                    </NativeSelect>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="trip-destination">Destino</Label>
                    <NativeSelect id="trip-destination" icon={MapPin} value={destination}
                      onChange={(e) => changeSelection({ ...selection, origin, destination: e.target.value })}>
                      {search.destinations.map((d) => <option key={d.code} value={d.code}>{d.name} ({d.code})</option>)}
                    </NativeSelect>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="trip-date">Fecha de salida</Label>
                    <NativeSelect id="trip-date" icon={CalendarDays} value={date}
                      onChange={(e) => changeSelection({ ...selection, origin, destination, date: e.target.value })}>
                      {search.dates.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
                    </NativeSelect>
                  </div>
                </Card>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="font-serif text-3xl">Elige tu vuelo</h3>
                  <SortChips options={SORTS.flight} value={sort.flight} onChange={(v) => setSort({ ...sort, flight: v })} />
                </div>
                <ListState loading={flights.loading} error={flights.error} empty={flights.data && !flights.data.flightOffers.length}
                  emptyText="No hay vuelos para esa ruta y fecha." />
                {!flights.loading && (
                  <div className="space-y-3" role="group" aria-label="Vuelos disponibles">
                    {sorted(flights.data?.flightOffers, "flight").map((f) => (
                      <OptionCard key={f.id} selected={flight?.id === f.id} onSelect={() => chooseFlight(f)}
                        label={`Vuelo ${f.airline} ${f.flightNumber}`} price={money(f.priceCents, f.currency)}
                        priceNote={f.cabin.replace("_", " ").toLowerCase()}>
                        <p className="font-medium">{f.airline} <span className="font-mono text-[13px] text-muted-foreground">{f.flightNumber}</span></p>
                        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
                          <span className="font-mono text-base text-foreground">{f.departureTime}</span>
                          <span className="inline-flex items-center gap-1" aria-hidden="true">
                            <span className="h-px w-8 bg-border" /><Plane className="size-3.5 rotate-45" /><span className="h-px w-8 bg-border" />
                          </span>
                          <span className="font-mono text-base text-foreground">{f.arrivalTime}</span>
                          <span>· {stopsLabel(f.stops)} · {duration(f.durationMinutes)}</span>
                        </p>
                      </OptionCard>
                    ))}
                  </div>
                )}
              </>
            )}

            {phase === 1 && (
              <>
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h3 className="font-serif text-3xl">Elige tu hotel en {where.destinationCity}</h3>
                    <p className="text-sm text-muted-foreground">Entrada el {dayLabel(date)}, el día que llega tu vuelo.</p>
                  </div>
                  <SortChips options={SORTS.hotel} value={sort.hotel} onChange={(v) => setSort({ ...sort, hotel: v })} />
                </div>
                <ListState loading={hotels.loading} error={hotels.error} empty={hotels.data && !hotels.data.hotelOffers.length}
                  emptyText="No hay hoteles para ese destino y fecha." />
                {!hotels.loading && (
                  <div className="space-y-3" role="group" aria-label="Hoteles disponibles">
                    {sorted(hotels.data?.hotelOffers, "hotel").map((h) => (
                      <OptionCard key={h.id} selected={hotel?.id === h.id} onSelect={() => setHotel(h)} label={`Hotel ${h.name}`}
                        price={money(h.totalPriceCents, h.currency)} priceNote={`${money(h.pricePerNightCents, h.currency)} / noche`}>
                        <p className="flex flex-wrap items-center gap-2 font-medium">
                          {h.name}
                          <span className="inline-flex text-warning" role="img" aria-label={`${h.stars} estrellas`}>
                            {Array.from({ length: h.stars }, (_, i) => <Star key={i} className="size-3.5 fill-current" aria-hidden="true" />)}
                          </span>
                          {h.rating != null && <Badge variant="secondary">{String(h.rating).replace(".", ",")}</Badge>}
                        </p>
                        <p className="mt-1 text-sm text-muted-foreground">{h.nights} noches</p>
                        {h.amenities?.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {h.amenities.slice(0, 4).map((a) => <Badge key={a} variant="outline" className="font-normal">{a}</Badge>)}
                          </div>
                        )}
                      </OptionCard>
                    ))}
                  </div>
                )}
              </>
            )}

            {phase === 2 && (
              <>
                <div>
                  <h3 className="font-serif text-3xl">Elige tu auto</h3>
                  <p className="text-sm text-muted-foreground">Recogida en {where.destinationCity} el {dayLabel(date)}.</p>
                </div>
                <Card className="p-4 sm:p-5">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    <PlaneLanding className="size-4 text-coral" aria-hidden="true" />
                    Tu vuelo {flight.airline} {flight.flightNumber} aterriza a las <span className="font-mono">{flight.arrivalTime}</span>
                    {overnight && <span className="text-muted-foreground">(del día siguiente)</span>}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Hora de recogida del auto">
                    <Clock className="size-4 text-muted-foreground" aria-hidden="true" />
                    {pickupOptions(flight).map(([label, time]) => (
                      <button key={time} type="button" onClick={() => setPickupTime(time)} aria-pressed={pickupTime === time}
                        className={cn("rounded-full border px-3 py-1 text-[13px] transition-colors",
                          pickupTime === time ? "border-coral bg-coral text-coral-foreground" : "bg-card hover:bg-secondary")}>
                        {label} · <span className="font-mono">{time}</span>
                      </button>
                    ))}
                  </div>
                </Card>
                <div className="flex justify-end">
                  <SortChips options={SORTS.car} value={sort.car} onChange={(v) => setSort({ ...sort, car: v })} />
                </div>
                <ListState loading={cars.loading} error={cars.error} empty={cars.data && !cars.data.carOffers.length}
                  emptyText="No hay autos para ese destino y fecha." />
                {!cars.loading && (
                  <div className="space-y-3" role="group" aria-label="Autos disponibles">
                    {sorted(cars.data?.carOffers, "car").map((c) => (
                      <OptionCard key={c.id} selected={car?.id === c.id} onSelect={() => setCar(c)} label={`Auto ${c.company} ${c.model}`}
                        price={money(c.totalPriceCents, c.currency)} priceNote={`${money(c.pricePerDayCents, c.currency)} / día · ${c.days} días`}>
                        <p className="font-medium">{c.company} {c.model}</p>
                        <p className="mt-1 flex flex-wrap items-center gap-x-3 text-sm text-muted-foreground">
                          <span>{c.category}</span>
                          <span>{c.transmission === "AUTOMATIC" ? "Automático" : "Manual"}</span>
                          <span className="inline-flex items-center gap-1"><Users className="size-3.5" aria-hidden="true" />{c.seats} asientos</span>
                        </p>
                      </OptionCard>
                    ))}
                  </div>
                )}
              </>
            )}

            {phase === 3 && (
              <>
                <div>
                  <h3 className="font-serif text-3xl">Revisa tu viaje</h3>
                  <p className="text-sm text-muted-foreground">Si todo está bien, continúa al pago con tu billetera. Puedes cambiar cualquier elección.</p>
                </div>
                {[
                  [Plane, "Vuelo", `${flight.airline} ${flight.flightNumber}`,
                   `${dayLabel(date)} · ${flight.departureTime} → ${flight.arrivalTime} · ${stopsLabel(flight.stops)}`, flight.priceCents, 0],
                  [BedDouble, "Hotel", hotel.name, `${hotel.nights} noches desde el ${dayLabel(date)} · ${hotel.stars} estrellas`, hotel.totalPriceCents, 1],
                  [Car, "Auto", `${car.company} ${car.model}`,
                   `${car.category} · recogida ${dayLabel(date)} a las ${pickupTime} · ${car.days} días`, car.totalPriceCents, 2],
                ].map(([Icon, kind, title, sub, cents, back]) => (
                  <Card key={kind} className="flex items-center gap-4 p-4 sm:p-5">
                    <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-secondary"><Icon className="size-5" aria-hidden="true" /></span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{kind}</p>
                      <p className="font-medium">{title}</p>
                      <p className="text-sm text-muted-foreground">{sub}</p>
                    </div>
                    <div className="text-right">
                      <p className="font-mono tabular-nums">{money(cents, currency)}</p>
                      <Button variant="link" size="sm" className="h-auto text-coral" onClick={() => go(back)}>Cambiar</Button>
                    </div>
                  </Card>
                ))}
              </>
            )}

            <div className="flex items-center justify-between gap-3 border-t pt-5">
              {phase > 0 ? (
                <Button variant="ghost" onClick={() => go(phase - 1)}><ArrowLeft aria-hidden="true" />Atrás</Button>
              ) : <span />}
              {phase < 3 ? (
                <Button variant="coral" size="lg" disabled={unlocked <= phase} onClick={() => go(phase + 1)}>
                  Siguiente: {PHASES[phase + 1].label}<ArrowRight aria-hidden="true" />
                </Button>
              ) : (
                <Button variant="coral" size="lg" onClick={checkout}>
                  Continuar al pago · {money(total, currency)}<ArrowRight aria-hidden="true" />
                </Button>
              )}
            </div>
          </motion.section>
        </AnimatePresence>

        <Summary selection={where} flight={flight} hotel={hotel} car={car} pickupTime={pickupTime} total={total} currency={currency} />
      </div>
    </div>
  );
}
