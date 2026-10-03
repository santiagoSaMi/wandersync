import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MockedProvider } from "@apollo/client/testing";
import { createCache } from "../apollo.js";
import { DEMO_SET_WALLET, WALLET } from "../graphql.js";
import { money, signedMoney } from "../format.js";
import WalletPanel from "./WalletPanel.jsx";

afterEach(cleanup);

// Intl separa "US$" del número con un espacio duro (U+00A0); Testing Library normaliza el texto de la página
// a espacios simples pero no el texto esperado: se normaliza aquí igual.
const nb = (s) => s.replace(/\u00a0/g, " ");

beforeAll(() => { window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} }); });

const mv = (kind, amountCents, balanceAfterCents, orderId = null) => ({
  __typename: "WalletMovement", kind, amountCents, balanceAfterCents, orderId, createdAt: "2026-10-03T12:00:00Z",
});
const wallet = (balanceCents, movements) => ({ __typename: "Wallet", id: "u1", balanceCents, currency: "USD", movements });
const LEDGER = [mv("REFUND", 60000, 500000, "abcdef12-0000"), mv("DEBIT", -60000, 440000, "abcdef12-0000"), mv("OPENING", 500000, 500000)];

describe("WalletPanel", () => {
  it("muestra el saldo y el libro mayor de la billetera", async () => {
    render(
      <MockedProvider cache={createCache()} mocks={[{ request: { query: WALLET }, result: { data: { wallet: wallet(500000, LEDGER) } } }]}>
        <WalletPanel />
      </MockedProvider>
    );
    await screen.findByText("Saldo inicial de bienvenida");
    expect(screen.getByText("Pago de reserva (SAGA)")).toBeTruthy();
    expect(screen.getByText("Reembolso: la SAGA revirtió la reserva")).toBeTruthy();
    expect(screen.getByText(nb(signedMoney(-60000, "USD")))).toBeTruthy();
    expect(screen.getAllByText("abcdef12").length).toBe(2);              // orden corta en débito y reembolso
    expect(screen.getByLabelText(`Saldo: ${nb(money(500000, "USD", { exact: true }))}`)).toBeTruthy();
  });

  it("el botón de demostración deja el saldo en US$ 100", async () => {
    const after = wallet(10000, [mv("ADJUSTMENT", -490000, 10000), ...LEDGER]);
    render(
      <MockedProvider cache={createCache()} mocks={[
        { request: { query: WALLET }, result: { data: { wallet: wallet(500000, LEDGER) } } },
        { request: { query: DEMO_SET_WALLET, variables: { cents: 10000 } }, result: { data: { demoSetWalletBalance: after } } },
      ]}>
        <WalletPanel />
      </MockedProvider>
    );
    fireEvent.click(await screen.findByText("Dejar saldo en US$ 100"));
    await screen.findByText("Ajuste de demostración");
    await screen.findByLabelText(`Saldo: ${nb(money(10000, "USD", { exact: true }))}`);
  });
});
