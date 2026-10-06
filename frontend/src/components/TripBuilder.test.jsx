import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MockedProvider } from "@apollo/client/testing";
import { createCache } from "../apollo.js";
import { TRIP_CARS, TRIP_FLIGHTS, TRIP_HOTELS } from "../graphql.js";
import { money } from "../format.js";
import TripBuilder, { pickupOptions } from "./TripBuilder.jsx";

afterEach(cleanup);

// Intl separa "US$" del número con un espacio duro; Testing Library normaliza solo el texto de la página.
const nb = (s) => s.replace(/ /g, " ");

const CATALOG = {
  ready: true, dates: ["2026-10-10", "2026-10-17"],
  routes: [{ origin: "BOG", destination: "CTG", originCity: "Bogotá", destinationCity: "Cartagena" },
           { origin: "BOG", destination: "MDE", originCity: "Bogotá", destinationCity: "Medellín" }],
};
const flight = (id, airline, flightNumber, departureTime, arrivalTime, priceCents) => ({
  __typename: "FlightOffer", id, airline, flightNumber, departureTime, arrivalTime, durationMinutes: 75, stops: 0,
  cabin: "ECONOMY", priceCents, currency: "USD", destinationCity: "Cartagena",
});
const hotel = (id, name, totalPriceCents, rating) => ({
  __typename: "HotelOffer", id, name, stars: 4, rating, amenities: ["WiFi", "Piscina"], nights: 3,
  pricePerNightCents: totalPriceCents / 3, totalPriceCents, currency: "USD",
});
const car = (id, company, model, totalPriceCents, seats = 5) => ({
  __typename: "CarOffer", id, company, model, category: "Compacto", transmission: "MANUAL", seats, days: 3,
  pricePerDayCents: totalPriceCents / 3, totalPriceCents, currency: "USD",
});

const FLIGHTS = [flight("f1", "Avianca", "AV 100", "06:00", "07:20", 9000), flight("f2", "LATAM", "LA 200", "13:20", "14:35", 7500)];
const HOTELS = [hotel("h1", "Casa Bahía", 45000, 8.9), hotel("h2", "Hotel Faro", 30000, 7.2)];
const CARS = [car("c1", "Hertz", "Kia Picanto", 15000, 4), car("c2", "Avis", "Renault Duster", 24000, 5)];

const mock = (query, variables, field, list) => ({ request: { query, variables }, result: { data: { [field]: list } } });
const baseMocks = () => [
  mock(TRIP_FLIGHTS, { origin: "BOG", destination: "CTG", date: "2026-10-10" }, "flightOffers", FLIGHTS),
  mock(TRIP_HOTELS, { city: "CTG", date: "2026-10-10" }, "hotelOffers", HOTELS),
  mock(TRIP_CARS, { city: "CTG", date: "2026-10-10" }, "carOffers", CARS),
];

function renderBuilder(mocks = baseMocks(), onCheckout = vi.fn()) {
  render(<MockedProvider mocks={mocks} cache={createCache()}><TripBuilder catalog={CATALOG} onCheckout={onCheckout} /></MockedProvider>);
  return onCheckout;
}
const next = (label) => screen.getByRole("button", { name: new RegExp(`Siguiente: ${label}`) });

describe("TripBuilder (Arma tu viaje)", () => {
  it("recorre las 4 fases y entrega el viaje armado al pago", async () => {
    const onCheckout = renderBuilder();
    fireEvent.click(await screen.findByRole("button", { name: "Vuelo LATAM LA 200" }));
    fireEvent.click(next("Hotel"));

    await screen.findByText("Elige tu hotel en Cartagena");
    fireEvent.click(await screen.findByRole("button", { name: "Hotel Hotel Faro" }));
    fireEvent.click(next("Auto"));

    await screen.findByText("Elige tu auto");
    const times = within(screen.getByRole("group", { name: "Hora de recogida del auto" }));
    expect(times.getByRole("button", { name: /Al aterrizar · 14:35/ }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(times.getByRole("button", { name: /\+30 min · 15:05/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Auto Hertz Kia Picanto" }));
    fireEvent.click(next("Revisión"));

    await screen.findByText("Revisa tu viaje");
    expect(screen.getByText(/recogida .* a las 15:05/)).toBeTruthy();
    const total = 7500 + 30000 + 15000;
    // el nombre accesible conserva el espacio duro de Intl: \s lo acepta
    const amount = money(total).replace("$", "\\$").replace(/\u00a0/g, "\\s");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`Continuar al pago · ${amount}`) }));
    expect(onCheckout).toHaveBeenCalledTimes(1);
    const pkg = onCheckout.mock.calls[0][0];
    expect(pkg.id).toBe("f2~h2~c1");
    expect([pkg.flight.id, pkg.hotel.id, pkg.car.id]).toEqual(["f2", "h2", "c1"]);
    expect(pkg.carPickupTime).toBe("15:05");
    expect(pkg.totalPriceCents).toBe(total);
    expect(pkg.nights).toBe(3);
  });

  it("cada fase se desbloquea solo cuando la anterior tiene una elección", async () => {
    renderBuilder();
    await screen.findByRole("button", { name: "Vuelo Avianca AV 100" });
    expect(next("Hotel").disabled).toBe(true);
    const phases = within(screen.getByRole("list", { name: "Fases del viaje" })).getAllByRole("button");
    expect(phases.map((b) => b.disabled)).toEqual([false, true, true, true]);
    fireEvent.click(screen.getByRole("button", { name: "Vuelo Avianca AV 100" }));
    await waitFor(() => expect(next("Hotel").disabled).toBe(false));
  });

  it("ordena los vuelos por precio y por hora de salida", async () => {
    renderBuilder();
    await screen.findByRole("button", { name: "Vuelo Avianca AV 100" });
    const order = () => within(screen.getByRole("group", { name: "Vuelos disponibles" })).getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(order()).toEqual(["Vuelo LATAM LA 200", "Vuelo Avianca AV 100"]);          // más barato primero
    fireEvent.click(screen.getByRole("button", { name: "Más temprano" }));
    await waitFor(() => expect(order()).toEqual(["Vuelo Avianca AV 100", "Vuelo LATAM LA 200"]));
  });

  it("cambiar la fecha borra lo elegido después (otra fecha = otros vuelos y hoteles)", async () => {
    renderBuilder([...baseMocks(),
      mock(TRIP_FLIGHTS, { origin: "BOG", destination: "CTG", date: "2026-10-17" }, "flightOffers", [FLIGHTS[0]])]);
    fireEvent.click(await screen.findByRole("button", { name: "Vuelo LATAM LA 200" }));
    fireEvent.click(next("Hotel"));
    fireEvent.click(await screen.findByRole("button", { name: "Hotel Casa Bahía" }));
    fireEvent.click(screen.getByRole("button", { name: /Atrás/ }));

    fireEvent.change(await screen.findByLabelText("Fecha de salida"), { target: { value: "2026-10-17" } });
    const summary = within(screen.getByRole("list", { name: "Resumen del viaje" }));
    await waitFor(() => expect(summary.getAllByText("Por elegir")).toHaveLength(3));
    expect(next("Hotel").disabled).toBe(true);
    await screen.findByRole("button", { name: "Vuelo Avianca AV 100" });                 // vuelos de la nueva fecha
  });
});

describe("pickupOptions", () => {
  it("propone recoger al aterrizar y algo después, sin pasar de medianoche", () => {
    expect(pickupOptions({ arrivalTime: "14:35" }).map(([, t]) => t)).toEqual(["14:35", "15:05", "15:35", "16:35"]);
    expect(pickupOptions({ arrivalTime: "23:10" }).map(([, t]) => t)).toEqual(["23:10", "23:40"]);
    expect(pickupOptions(null)).toEqual([]);
  });
});
