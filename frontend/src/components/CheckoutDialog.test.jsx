import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MockedProvider } from "@apollo/client/testing";
import { createCache } from "../apollo.js";
import { CREATE_BOOKING, ORDER } from "../graphql.js";
import CheckoutDialog from "./CheckoutDialog.jsx";

vi.mock("../format.js", async (original) => ({ ...(await original()), newIdempotencyKey: () => "key-12345678" }));

beforeAll(() => {
  // jsdom no implementa <dialog>.showModal()
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
});

// Sin `globals` en vitest, Testing Library no limpia el DOM sola: sin esto un test vería el diálogo del anterior.
afterEach(cleanup);

const PKG = {
  currency: "USD", nights: 3, totalPriceCents: 60000,
  flight: { id: "f1", airline: "Avianca", flightNumber: "AV 1", priceCents: 20000 },
  hotel: { id: "h1", name: "Hotel Bahía", totalPriceCents: 30000 },
  car: { id: "c1", company: "Hertz", model: "Kwid", totalPriceCents: 10000 },
};

const step = (name, status, phase = "EXECUTE") => ({
  __typename: "SagaStep", seq: 1, name, phase, status, attempts: 1, error: null,
  startedAt: "2026-10-03T00:00:00Z", finishedAt: null,
});

const order = (status, steps) => ({
  __typename: "Order", id: "o1", status, totalCents: 60000, currency: "USD", failureReason: null,
  invoiceNumber: status === "CONFIRMED" ? "WS-1" : null, createdAt: "2026-10-03T00:00:00Z", items: [], steps,
});

function bookingMock(result, simulateFailure = null, slowMotion = false) {
  return {
    request: {
      query: CREATE_BOOKING,
      variables: { input: { flightOfferId: "f1", hotelOfferId: "h1", carOfferId: "c1",
                            idempotencyKey: "key-12345678", simulateFailure, slowMotion } },
    },
    result: { data: { createBooking: result } },
  };
}

function pollMocks(counter, ...results) {
  return results.map((o) => ({
    request: { query: ORDER, variables: { id: "o1" } },
    result: () => { counter.calls += 1; return { data: { order: o } }; },
  }));
}

const renderDialog = (mocks) =>
  render(
    <MockedProvider mocks={mocks} cache={createCache()}>
      <CheckoutDialog pkg={PKG} onClose={() => {}} onViewOrders={() => {}} />
    </MockedProvider>
  );

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

describe("CheckoutDialog", () => {
  it("una SAGA lenta (PENDING) se consulta hasta el estado final y luego deja de consultar", async () => {
    const polls = { calls: 0 };
    const running = [step("PAYMENT", "COMPLETED"), step("FLIGHT", "STARTED")];
    renderDialog([
      bookingMock(order("PENDING", running)),
      ...pollMocks(polls, order("PENDING", running), order("CONFIRMED", [step("PAYMENT", "COMPLETED")]),
                   order("CONFIRMED", []), order("CONFIRMED", [])),   // sobrantes: no deben consumirse
    ]);

    fireEvent.click(screen.getByText(/Pagar/));
    await screen.findByText(/se está procesando/);
    expect(screen.getByText("Procesando tu reserva")).toBeTruthy();
    expect(screen.queryByText("Intentar de nuevo")).toBeNull();          // no se reintenta algo en curso

    await screen.findByText(/Reserva confirmada/, {}, { timeout: 6000 });
    const atFinal = polls.calls;
    await sleep(3500);                                                   // > 2 intervalos de sondeo
    expect(polls.calls).toBe(atFinal);                                   // el sondeo se detuvo
    expect(screen.getByText(/Reserva confirmada/)).toBeTruthy();         // y no volvió a PENDING
  }, 15000);

  it("modo paso a paso: envía slowMotion y muestra cada paso mientras ocurre", async () => {
    const polls = { calls: 0 };
    renderDialog([
      bookingMock(order("PENDING", []), null, true),             // el backend responde de inmediato
      ...pollMocks(polls,
        order("PENDING", [step("PAYMENT", "STARTED")]),
        order("PENDING", [step("PAYMENT", "COMPLETED"), step("FLIGHT", "STARTED")]),
        order("CONFIRMED", [step("PAYMENT", "COMPLETED"), step("FLIGHT", "COMPLETED")])),
    ]);

    fireEvent.click(screen.getByLabelText(/paso a paso/));
    fireEvent.click(screen.getByText(/Pagar/));
    await screen.findByText(/Modo paso a paso/);
    await screen.findByText("En curso", {}, { timeout: 3000 });  // el pago aparece "en curso"
    await screen.findByText(/Reserva confirmada/, {}, { timeout: 6000 });
    expect(screen.getAllByText("Listo")).toHaveLength(2);
  }, 15000);

  it("un resultado final inmediato no dispara ninguna consulta de seguimiento", async () => {
    const polls = { calls: 0 };
    renderDialog([
      bookingMock(order("CANCELLED", [step("CAR", "FAILED"), step("CAR", "COMPLETED", "COMPENSATE")]), "CAR"),
      ...pollMocks(polls, order("CANCELLED", [])),
    ]);

    fireEvent.click(screen.getByLabelText("La reserva del auto"));       // modo demo: forzar fallo en el auto
    fireEvent.click(screen.getByText(/Pagar/));
    await screen.findByText(/Revertimos todo/);
    expect(screen.getByText("Intentar de nuevo")).toBeTruthy();
    await sleep(2000);
    expect(polls.calls).toBe(0);
  }, 10000);
});
