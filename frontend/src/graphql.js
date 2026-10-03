import { gql } from "@apollo/client";

export const ME = gql`
  query Me {
    me { id email fullName }
  }
`;

export const CATALOG = gql`
  query Catalog {
    catalogStatus {
      ready
      flightOffers
      hotelOffers
      carOffers
      lastRunAt
      failedQueries
      nights
      dates
      routes { origin destination originCity destinationCity }
    }
  }
`;

// El cliente pide SOLO los campos que muestra: el gateway solo trae esas columnas de Supabase.
export const PACKAGES = gql`
  query Packages($origin: String!, $destination: String!, $date: Date!, $limit: Int) {
    packages(origin: $origin, destination: $destination, departureDate: $date, limit: $limit) {
      id
      totalPriceCents
      currency
      nights
      flight { id airline flightNumber departureTime arrivalTime durationMinutes stops priceCents }
      hotel { id name stars rating amenities totalPriceCents }
      car { id company model category transmission seats totalPriceCents }
    }
  }
`;

const ORDER_FIELDS = gql`
  fragment OrderFields on Order {
    id status totalCents currency failureReason invoiceNumber createdAt
    items { kind title detail priceCents }
    steps { seq name phase status attempts error startedAt finishedAt }
  }
`;

export const MY_ORDERS = gql`
  ${ORDER_FIELDS}
  query MyOrders {
    myOrders { ...OrderFields }
  }
`;

// Una orden puede volver PENDING si la SAGA tarda: el cliente la consulta hasta un estado final.
export const ORDER = gql`
  ${ORDER_FIELDS}
  query Order($id: ID!) {
    order(id: $id) { ...OrderFields }
  }
`;

// COMPENSATION_FAILED es final para la UI: la recuperación del backend sigue reintentando por su cuenta.
export const FINAL_ORDER_STATUSES = new Set(["CONFIRMED", "CANCELLED", "COMPENSATION_FAILED"]);

export const LOGIN = gql`
  mutation Login($input: LoginInput!) {
    login(input: $input) { id email fullName }
  }
`;

export const REGISTER = gql`
  mutation Register($input: RegisterInput!) {
    register(input: $input) { id email fullName }
  }
`;

export const LOGOUT = gql`
  mutation Logout { logout }
`;

export const CREATE_BOOKING = gql`
  ${ORDER_FIELDS}
  mutation CreateBooking($input: BookingInput!) {
    createBooking(input: $input) { ...OrderFields }
  }
`;

// Billetera: el PAGO de la SAGA la debita y la compensación la reembolsa. `id` = usuario (Apollo la normaliza).
export const WALLET = gql`
  query Wallet {
    wallet { id balanceCents currency movements { kind amountCents balanceAfterCents orderId createdAt } }
  }
`;

export const DEMO_SET_WALLET = gql`
  mutation DemoSetWallet($cents: Int!) {
    demoSetWalletBalance(balanceCents: $cents) { id balanceCents currency movements { kind amountCents balanceAfterCents orderId createdAt } }
  }
`;
