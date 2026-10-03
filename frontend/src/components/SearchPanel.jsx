import { useMemo, useState } from "react";
import { dayLabel } from "../format.js";

export default function SearchPanel({ catalog, busy, onSearch }) {
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [date, setDate] = useState("");

  const routes = useMemo(() => catalog?.routes ?? [], [catalog]);
  const origins = useMemo(() => {
    const seen = new Map();
    routes.forEach((r) => seen.set(r.origin, r.originCity));
    return [...seen].map(([code, name]) => ({ code, name }));
  }, [routes]);

  const originValue = origins.some((o) => o.code === origin) ? origin : origins[0]?.code ?? "";
  const destinations = useMemo(
    () => routes.filter((r) => r.origin === originValue).map((r) => ({ code: r.destination, name: r.destinationCity })),
    [routes, originValue]
  );
  const destinationValue = destinations.some((d) => d.code === destination) ? destination : destinations[0]?.code ?? "";
  const dates = catalog?.dates ?? [];
  const dateValue = dates.includes(date) ? date : dates[0] ?? "";

  const disabled = busy || !catalog?.ready || !originValue || !destinationValue || !dateValue;

  function submit(event) {
    event.preventDefault();
    onSearch({ origin: originValue, destination: destinationValue, date: dateValue });
  }

  return (
    <form className="search" onSubmit={submit} aria-label="Buscar paquetes">
      <div className="field">
        <label htmlFor="origin">Sales desde</label>
        <select id="origin" value={originValue} onChange={(e) => setOrigin(e.target.value)}>
          {origins.map((o) => <option key={o.code} value={o.code}>{o.name} ({o.code})</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor="destination">Viajas a</label>
        <select id="destination" value={destinationValue} onChange={(e) => setDestination(e.target.value)}>
          {destinations.map((d) => <option key={d.code} value={d.code}>{d.name} ({d.code})</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor="date">Fecha de salida</label>
        <select id="date" value={dateValue} onChange={(e) => setDate(e.target.value)}>
          {dates.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
        </select>
      </div>
      <button type="submit" className="btn btn-primary" disabled={disabled}>
        {busy ? "Buscando…" : "Buscar paquetes"}
      </button>
    </form>
  );
}
