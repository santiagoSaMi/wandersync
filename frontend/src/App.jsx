import { useEffect, useRef, useState } from "react";
import { useLazyQuery, useMutation, useQuery } from "@apollo/client";
import { useLenis } from "lenis/react";
import { AnimatePresence, motion } from "motion/react";
import {
  CircleAlert, Compass, Database, ExternalLink, LogOut, Network, RefreshCw, SearchX, Sparkles, Terminal, Ticket, Wallet, Workflow,
} from "lucide-react";
import { CATALOG, LOGOUT, ME, PACKAGES, WALLET } from "./graphql.js";
import { dayLabel, errorText, timeAgo } from "./format.js";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import SearchPanel, { resolveSearch } from "./components/SearchPanel.jsx";
import Destinations from "./components/Destinations.jsx";
import PackageTicket from "./components/PackageTicket.jsx";
import CheckoutDialog from "./components/CheckoutDialog.jsx";
import AuthDialog from "./components/AuthDialog.jsx";
import OrdersView from "./components/OrdersView.jsx";
import WalletPanel from "./components/WalletPanel.jsx";
import AnimatedMoney from "./components/AnimatedMoney.jsx";
import FlightArc from "./components/FlightArc.jsx";

function useScrolled(threshold = 24) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > threshold);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return scrolled;
}

// Saldo de la billetera en la cabecera: se actualiza solo (Apollo normaliza Wallet por id) cuando la SAGA
// debita o reembolsa, y lleva a Mis reservas, donde está el detalle de movimientos.
function WalletChip({ overHero, onClick }) {
  const { data } = useQuery(WALLET, { fetchPolicy: "cache-and-network" });
  const wallet = data?.wallet;
  if (!wallet) return null;
  return (
    <button type="button" onClick={onClick} aria-label="Ver mi billetera"
      className={cn("inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors",
        overHero ? "border-white/15 bg-white/10 text-white hover:bg-white/20" : "bg-card hover:bg-secondary")}>
      <Wallet className="size-4 text-coral" aria-hidden="true" />
      <AnimatedMoney cents={wallet.balanceCents} currency={wallet.currency} className="font-mono text-[13px]" />
    </button>
  );
}

function initials(name = "") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
}

export default function App() {
  const [view, setView] = useState("search");
  const [authOpen, setAuthOpen] = useState(false);
  const [booking, setBooking] = useState(null);      // paquete en checkout
  const [pending, setPending] = useState(null);      // paquete elegido antes de iniciar sesión
  const [lastSearch, setLastSearch] = useState(null);
  const [selection, setSelection] = useState({ origin: "", destination: "", date: "" });
  const resultsRef = useRef(null);
  const lenis = useLenis();
  const scrolled = useScrolled();

  const { data: meData } = useQuery(ME);
  const { data: catData, refetch: refetchCatalog } = useQuery(CATALOG, { pollInterval: 60000 });
  const [runSearch, { data: pkgData, loading: searching, error: searchError }] =
    useLazyQuery(PACKAGES, { fetchPolicy: "network-only" });
  const [logout] = useMutation(LOGOUT, { refetchQueries: [{ query: ME }], awaitRefetchQueries: true });

  const me = meData?.me;
  const catalog = catData?.catalogStatus;
  const packages = pkgData?.packages;
  const overHero = view === "search" && !scrolled;

  function scrollTo(target) {
    if (lenis) lenis.scrollTo(target, { offset: -84, duration: 1.1 });
    else if (target === 0) window.scrollTo({ top: 0 });
    else target?.scrollIntoView?.({ block: "start" });
  }

  function search(params) {
    setLastSearch(params);
    runSearch({ variables: { ...params, limit: 6 } });
    requestAnimationFrame(() => scrollTo(resultsRef.current));
  }

  function pickRoute(route) {
    const next = { ...selection, origin: route.origin, destination: route.destination };
    setSelection(next);
    const { origin, destination, date } = resolveSearch(catalog, next);
    if (catalog?.ready && origin && destination && date) search({ origin, destination, date });
  }

  function book(pkg) {
    if (me) setBooking(pkg);
    else { setPending(pkg); setAuthOpen(true); }
  }

  function authDone() {
    setAuthOpen(false);
    if (pending) { setBooking(pending); setPending(null); }
  }

  function go(next) {
    setView(next);
    scrollTo(0);
  }

  const navLink = (active) => cn(
    "rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors",
    overHero
      ? active ? "bg-white/15 text-white" : "text-white/75 hover:text-white"
      : active ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"
  );

  return (
    <div className="flex min-h-dvh flex-col">
      {/* ------------------------------------------------------------------ cabecera */}
      <header className={cn("fixed inset-x-0 top-0 z-40 transition-[background-color,box-shadow,border-color] duration-300",
        overHero ? "border-b border-transparent" : "border-b bg-background/80 shadow-[0_8px_30px_-20px_rgb(20_33_61/0.35)] backdrop-blur-xl")}>
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
          <button type="button" onClick={() => go("search")} className="flex items-center gap-2.5" aria-label="WanderSync, inicio">
            <span className="grid size-9 place-items-center rounded-xl bg-coral text-coral-foreground shadow-[0_6px_18px_-6px] shadow-coral/70">
              <Compass className="size-5" aria-hidden="true" />
            </span>
            <span className={cn("font-serif text-2xl tracking-tight", overHero ? "text-white" : "text-foreground")}>WanderSync</span>
          </button>
          <nav className="flex items-center gap-1 sm:gap-2" aria-label="Principal">
            <button type="button" className={cn(navLink(view === "search"), "hidden sm:inline-flex")} onClick={() => go("search")}>Buscar</button>
            {me && <button type="button" className={navLink(view === "orders")} onClick={() => go("orders")}>Mis reservas</button>}
            {me && <WalletChip overHero={overHero} onClick={() => go("orders")} />}
            {me ? (
              <span className="ml-1 flex items-center gap-2">
                <span className={cn("hidden items-center gap-2 text-sm md:flex", overHero ? "text-white/90" : "text-foreground")}>
                  <span className="grid size-8 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground ring-2 ring-white/40">
                    {initials(me.fullName)}
                  </span>
                  {me.fullName}
                </span>
                <Button variant={overHero ? "glass" : "ghost"} size="sm" onClick={() => { logout(); go("search"); }}>
                  <LogOut aria-hidden="true" />Salir
                </Button>
              </span>
            ) : (
              <Button variant={overHero ? "glass" : "outline"} size="sm" className="ml-1" onClick={() => setAuthOpen(true)}>Ingresar</Button>
            )}
          </nav>
        </div>
      </header>

      <main className="flex flex-1 flex-col">
        {view === "search" && (
          <>
            {/* ------------------------------------------------------------------ hero */}
            <section className="relative isolate overflow-hidden bg-midnight pb-28 pt-32 text-white sm:pb-32 sm:pt-36">
              <div aria-hidden="true" className="hero-grid absolute inset-0 -z-10" />
              <div aria-hidden="true" className="absolute -left-32 -top-24 -z-10 size-[34rem] rounded-full bg-coral/30 blur-[110px] motion-safe:animate-drift" />
              <div aria-hidden="true" className="absolute -bottom-40 right-[-10rem] -z-10 size-[38rem] rounded-full bg-sky/25 blur-[120px] motion-safe:animate-drift [animation-delay:-9s]" />
              {/* A la derecha del texto: solo en pantallas anchas, donde no se cruza con el titular ni el párrafo */}
              <FlightArc className="pointer-events-none absolute right-4 top-24 -z-10 hidden w-[38rem] opacity-90 xl:block 2xl:right-[4%]" />

              <div className="mx-auto max-w-6xl px-4 sm:px-6">
                <motion.div initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", duration: 0.9, bounce: 0.1 }}
                  className="max-w-2xl">
                  <Badge variant="glass" className="mb-5 gap-1.5 px-3 py-1">
                    <Sparkles aria-hidden="true" /> Paquetes dinámicos con precios del catálogo en vivo
                  </Badge>
                  <h1 className="font-serif text-[3.4rem] leading-[0.98] tracking-tight sm:text-7xl lg:text-[5.4rem]">
                    Vuelo, hotel y auto.<br />
                    <em className="text-coral">En un solo viaje.</em>
                  </h1>
                  <p className="mt-6 max-w-xl text-lg leading-relaxed text-white/70">
                    Combinamos las mejores tarifas en paquetes listos para reservar. Si una reserva falla,
                    todo lo demás se revierte solo: sin cobros a medias.
                  </p>
                </motion.div>

                <motion.div initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.15, type: "spring", duration: 0.9, bounce: 0.1 }} className="mt-12">
                  <SearchPanel catalog={catalog} busy={searching} value={selection} onChange={setSelection} onSearch={search} />
                </motion.div>

                <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-white/70" aria-live="polite">
                  {!catalog && <span>Conectando con el catálogo…</span>}
                  {catalog && !catalog.ready && <span>El catálogo se está cargando. La primera ingesta en Prefect tarda cerca de un minuto.</span>}
                  {catalog?.ready && (
                    <>
                      <span className="inline-flex items-center gap-2">
                        <span className="relative flex size-2">
                          <span className="absolute inline-flex size-full rounded-full bg-success opacity-70 motion-safe:animate-ping" />
                          <span className="relative inline-flex size-2 rounded-full bg-success" />
                        </span>
                        Catálogo en Supabase: {(catalog.flightOffers + catalog.hotelOffers + catalog.carOffers).toLocaleString("es-CO")} ofertas,
                        actualizado {timeAgo(catalog.lastRunAt)}.
                      </span>
                      <button type="button" className="inline-flex items-center gap-1.5 text-white underline-offset-4 hover:underline" onClick={() => refetchCatalog()}>
                        <RefreshCw className="size-3.5" aria-hidden="true" />Actualizar
                      </button>
                    </>
                  )}
                </div>
              </div>
            </section>

            <div className="paper-dots">
              <div className="mx-auto max-w-6xl space-y-16 px-4 py-16 sm:px-6 sm:py-20">
                <Destinations routes={catalog?.routes} selected={lastSearch} onPick={pickRoute} />

                {/* ------------------------------------------------------------------ resultados */}
                <section ref={resultsRef} aria-label="Resultados" className="scroll-mt-24 space-y-6">
                  {searchError && (
                    <p className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/8 px-4 py-3 text-sm text-destructive" role="alert">
                      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{errorText(searchError)}
                    </p>
                  )}

                  {searching && (
                    <div className="space-y-5" aria-busy="true">
                      <Skeleton className="h-10 w-80" />
                      {[0, 1, 2].map((i) => <Skeleton key={i} className="h-64 rounded-2xl" />)}
                    </div>
                  )}

                  {!searching && packages && packages.length === 0 && (
                    <div className="grid place-items-center gap-3 rounded-2xl border border-dashed bg-card px-6 py-14 text-center">
                      <SearchX className="size-8 text-muted-foreground" aria-hidden="true" />
                      <p className="text-muted-foreground">No hay paquetes para esa combinación. Prueba con otra fecha o destino.</p>
                    </div>
                  )}

                  {!searching && packages && packages.length > 0 && (
                    <>
                      <div className="flex flex-wrap items-end justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-coral">Resultados</p>
                          <h2 className="mt-1 font-serif text-4xl leading-tight sm:text-5xl">
                            {packages.length} paquetes para el {lastSearch ? dayLabel(lastSearch.date) : ""}
                          </h2>
                        </div>
                        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                          <Ticket className="size-4" aria-hidden="true" />ordenados por precio
                        </p>
                      </div>
                      <div className="space-y-5">
                        <AnimatePresence initial>
                          {packages.map((p, i) => (
                            <motion.div key={p.id} initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }}
                              transition={{ delay: i * 0.07, type: "spring", duration: 0.6, bounce: 0.15 }}>
                              <PackageTicket pkg={p} onBook={book} />
                            </motion.div>
                          ))}
                        </AnimatePresence>
                      </div>
                    </>
                  )}

                  {!packages && !searching && catalog?.ready && (
                    <p className="rounded-2xl border border-dashed bg-card/60 px-6 py-10 text-center text-muted-foreground">
                      Elige origen, destino y fecha para ver paquetes armados con precios reales del catálogo.
                    </p>
                  )}
                </section>
              </div>
            </div>
          </>
        )}

        {view === "orders" && me && (
          <section aria-label="Mis reservas" className="paper-dots flex-1">
            <div className="mx-auto max-w-4xl px-4 pb-20 pt-32 sm:px-6">
              <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-coral">Tu cuenta</p>
                <h2 className="mt-1 font-serif text-5xl leading-tight">Mis reservas</h2>
                <p className="mt-1 text-sm text-muted-foreground">Tu billetera y cada reserva, con los pasos de la SAGA que la confirmaron o la revirtieron.</p>
              </motion.div>
              <div className="space-y-8">
                <WalletPanel />
                <OrdersView />
              </div>
            </div>
          </section>
        )}
      </main>

      {/* ------------------------------------------------------------------ pie */}
      <footer className="border-t bg-card/60">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-8 text-sm text-muted-foreground sm:px-6">
          <span className="flex items-center gap-2"><Compass className="size-4 text-coral" aria-hidden="true" />WanderSync · Patrones Arquitectónicos Avanzados</span>
          <span className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span>Herramientas de la demo:</span>
            {[
              ["http://localhost:4200", "Prefect", Workflow],
              ["http://localhost:8787", "Dask", Network],
              ["http://localhost:8000/graphql", "GraphiQL", Terminal],
              ["http://localhost:8090", "fuente simulada", Database],
            ].map(([href, label, Icon]) => (
              <a key={label} href={href} target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-foreground/80 underline-offset-4 hover:text-foreground hover:underline">
                <Icon className="size-3.5" aria-hidden="true" />{label}<ExternalLink className="size-3 opacity-50" aria-hidden="true" />
              </a>
            ))}
          </span>
        </div>
      </footer>

      {authOpen && (
        <AuthDialog
          onClose={() => { setAuthOpen(false); setPending(null); }}
          onSuccess={authDone}
          reason={pending ? "Ingresa para terminar tu reserva." : ""}
        />
      )}
      {booking && (
        <CheckoutDialog
          pkg={booking}
          onClose={() => setBooking(null)}
          onViewOrders={() => { setBooking(null); go("orders"); }}
        />
      )}
    </div>
  );
}
