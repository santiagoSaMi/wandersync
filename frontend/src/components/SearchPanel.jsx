import { useMemo } from "react";
import { CalendarDays, LoaderCircle, MapPin, PlaneTakeoff, Search } from "lucide-react";
import { dayLabel } from "../format.js";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// Valores efectivos de la búsqueda: lo elegido si es válido en el catálogo; si no, la primera opción.
export function resolveSearch(catalog, value) {
  const routes = catalog?.routes ?? [];
  const origins = [...new Map(routes.map((r) => [r.origin, r.originCity]))].map(([code, name]) => ({ code, name }));
  const origin = origins.some((o) => o.code === value.origin) ? value.origin : origins[0]?.code ?? "";
  const destinations = routes.filter((r) => r.origin === origin).map((r) => ({ code: r.destination, name: r.destinationCity }));
  const destination = destinations.some((d) => d.code === value.destination) ? value.destination : destinations[0]?.code ?? "";
  const dates = catalog?.dates ?? [];
  const date = dates.includes(value.date) ? value.date : dates[0] ?? "";
  return { origins, destinations, dates, origin, destination, date };
}

export default function SearchPanel({ catalog, busy, value, onChange, onSearch }) {
  const { origins, destinations, dates, origin, destination, date } = useMemo(
    () => resolveSearch(catalog, value), [catalog, value]);
  const disabled = busy || !catalog?.ready || !origin || !destination || !date;

  function submit(event) {
    event.preventDefault();
    onSearch({ origin, destination, date });
  }

  return (
    <form onSubmit={submit} aria-label="Buscar paquetes"
      className="grid gap-4 rounded-2xl border bg-card p-4 text-card-foreground shadow-[0_30px_80px_-30px_rgb(10_20_45/0.55)] sm:p-5 md:grid-cols-[1fr_1fr_1fr_auto] md:items-end">
      <div className="space-y-2">
        <Label htmlFor="origin">Sales desde</Label>
        <NativeSelect id="origin" icon={PlaneTakeoff} value={origin}
          onChange={(e) => onChange({ ...value, origin: e.target.value })}>
          {origins.map((o) => <option key={o.code} value={o.code}>{o.name} ({o.code})</option>)}
        </NativeSelect>
      </div>
      <div className="space-y-2">
        <Label htmlFor="destination">Viajas a</Label>
        <NativeSelect id="destination" icon={MapPin} value={destination}
          onChange={(e) => onChange({ ...value, origin, destination: e.target.value })}>
          {destinations.map((d) => <option key={d.code} value={d.code}>{d.name} ({d.code})</option>)}
        </NativeSelect>
      </div>
      <div className="space-y-2">
        <Label htmlFor="date">Fecha de salida</Label>
        <NativeSelect id="date" icon={CalendarDays} value={date}
          onChange={(e) => onChange({ ...value, date: e.target.value })}>
          {dates.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
        </NativeSelect>
      </div>
      <Button type="submit" variant="coral" size="lg" className="h-11" disabled={disabled}>
        {busy ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Search aria-hidden="true" />}
        {busy ? "Buscando…" : "Buscar paquetes"}
      </Button>
    </form>
  );
}
