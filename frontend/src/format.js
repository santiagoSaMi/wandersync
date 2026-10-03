const moneyFmt = {};
// Siempre con centavos: con la billetera, el precio mostrado debe coincidir al centavo con lo que se debita.
// ({ exact: false } redondea a unidades, por si algún resumen compacto lo necesitara.)
export function money(cents, currency = "USD", { exact = true } = {}) {
  const key = `${currency}:${exact}`;
  moneyFmt[key] ??= new Intl.NumberFormat("es-CO", exact
    ? { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }
    : { style: "currency", currency, maximumFractionDigits: 0 });
  return moneyFmt[key].format(cents / 100);
}

// Monto con signo explícito para movimientos de la billetera: "−US$ 342,00" / "+US$ 342,00".
export function signedMoney(cents, currency = "USD") {
  return `${cents < 0 ? "−" : "+"}${money(Math.abs(cents), currency, { exact: true })}`;
}

export function dayLabel(iso) {
  return new Intl.DateTimeFormat("es-CO", { weekday: "short", day: "numeric", month: "short" })
    .format(new Date(`${iso}T12:00:00`));
}

export function duration(minutes) {
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? `${h} h ${m ? `${m} min` : ""}`.trim() : `${m} min`;
}

export function stopsLabel(n) {
  return n === 0 ? "directo" : `${n} escala${n > 1 ? "s" : ""}`;
}

export function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}

export function errorText(error) {
  if (!error) return "";
  const fromGraphQL = error.graphQLErrors?.[0]?.message;
  const fromNetwork = error.networkError?.result?.errors?.[0]?.message;
  if (fromGraphQL) return fromGraphQL;
  if (fromNetwork) return fromNetwork;
  if (error.networkError) return "No pudimos conectar con el servidor. Revisa tu conexión e intenta de nuevo.";
  return error.message;
}

export function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `k-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
