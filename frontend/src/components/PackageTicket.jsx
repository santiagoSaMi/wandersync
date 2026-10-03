import { duration, money, stopsLabel } from "../format.js";

function Segment({ label, price, children }) {
  return (
    <div className="segment">
      <span className="segment-label">{label}</span>
      <div className="segment-main">{children}</div>
      <span className="segment-price">{price}</span>
    </div>
  );
}

export default function PackageTicket({ pkg, onBook }) {
  const { flight: f, hotel: h, car: c, currency } = pkg;
  return (
    <article className="ticket">
      <div className="ticket-body">
        <Segment label="Vuelo" price={money(f.priceCents, currency)}>
          <strong>{f.airline} {f.flightNumber}</strong>
          <span>
            {f.departureTime} a {f.arrivalTime}, {stopsLabel(f.stops)}, {duration(f.durationMinutes)}
          </span>
        </Segment>
        <Segment label="Hotel" price={money(h.totalPriceCents, currency)}>
          <strong>
            {h.name}{" "}
            <span className="stars" role="img" aria-label={`${h.stars} estrellas`}>{"★".repeat(h.stars)}</span>
          </strong>
          <span>
            {pkg.nights} noches{h.rating ? `, puntuación ${String(h.rating).replace(".", ",")}` : ""}
            {h.amenities?.length ? `, ${h.amenities.slice(0, 3).join(", ")}` : ""}
          </span>
        </Segment>
        <Segment label="Auto" price={money(c.totalPriceCents, currency)}>
          <strong>{c.company} {c.model}</strong>
          <span>
            {c.category}, {c.transmission === "AUTOMATIC" ? "automático" : "manual"}, {c.seats} asientos
          </span>
        </Segment>
      </div>
      <div className="ticket-stub">
        <div>
          <span className="stub-label">Total del paquete</span>
          <span className="stub-total">{money(pkg.totalPriceCents, currency)}</span>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => onBook(pkg)}>
          Reservar
        </button>
      </div>
    </article>
  );
}
