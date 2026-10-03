import { useState } from "react";
import { useLazyQuery, useMutation, useQuery } from "@apollo/client";
import { CATALOG, LOGOUT, ME, PACKAGES } from "./graphql.js";
import { dayLabel, errorText, timeAgo } from "./format.js";
import SearchPanel from "./components/SearchPanel.jsx";
import PackageTicket from "./components/PackageTicket.jsx";
import CheckoutDialog from "./components/CheckoutDialog.jsx";
import AuthDialog from "./components/AuthDialog.jsx";
import OrdersView from "./components/OrdersView.jsx";

export default function App() {
  const [view, setView] = useState("search");
  const [authOpen, setAuthOpen] = useState(false);
  const [booking, setBooking] = useState(null);      // paquete en checkout
  const [pending, setPending] = useState(null);      // paquete elegido antes de iniciar sesión
  const [lastSearch, setLastSearch] = useState(null);

  const { data: meData } = useQuery(ME);
  const { data: catData, refetch: refetchCatalog } = useQuery(CATALOG, { pollInterval: 60000 });
  const [runSearch, { data: pkgData, loading: searching, error: searchError }] =
    useLazyQuery(PACKAGES, { fetchPolicy: "network-only" });
  const [logout] = useMutation(LOGOUT, { refetchQueries: [{ query: ME }], awaitRefetchQueries: true });

  const me = meData?.me;
  const catalog = catData?.catalogStatus;
  const packages = pkgData?.packages;

  function search(params) {
    setLastSearch(params);
    runSearch({ variables: { ...params, limit: 6 } });
  }

  function book(pkg) {
    if (me) setBooking(pkg);
    else { setPending(pkg); setAuthOpen(true); }
  }

  function authDone() {
    setAuthOpen(false);
    if (pending) { setBooking(pending); setPending(null); }
  }

  return (
    <>
      <div className="airmail" aria-hidden="true" />
      <header className="masthead">
        <div className="masthead-inner">
          <div>
            <h1 className="brand">WanderSync</h1>
            <p className="tagline">Vuelo, hotel y auto en un solo paquete.</p>
          </div>
          <nav className="nav" aria-label="Principal">
            <button type="button" className={view === "search" ? "nav-link nav-on" : "nav-link"} onClick={() => setView("search")}>Buscar</button>
            {me && <button type="button" className={view === "orders" ? "nav-link nav-on" : "nav-link"} onClick={() => setView("orders")}>Mis reservas</button>}
            {me ? (
              <span className="who">
                {me.fullName}
                <button type="button" className="nav-link" onClick={() => { logout(); setView("search"); }}>Salir</button>
              </span>
            ) : (
              <button type="button" className="btn btn-ghost" onClick={() => setAuthOpen(true)}>Ingresar</button>
            )}
          </nav>
        </div>
      </header>

      <main className="page">
        {view === "search" && (
          <>
            <SearchPanel catalog={catalog} busy={searching} onSearch={search} />

            <p className="catalog-note" aria-live="polite">
              {!catalog && "Conectando con el catálogo…"}
              {catalog && !catalog.ready && "El catálogo se está cargando. La primera ingesta en Prefect tarda cerca de un minuto."}
              {catalog?.ready && (
                <>
                  Catálogo en Supabase: {(catalog.flightOffers + catalog.hotelOffers + catalog.carOffers).toLocaleString("es-CO")} ofertas,
                  actualizado {timeAgo(catalog.lastRunAt)}.{" "}
                  <button type="button" className="link" onClick={() => refetchCatalog()}>Actualizar</button>
                </>
              )}
            </p>

            {searchError && <p className="alert alert-error" role="alert">{errorText(searchError)}</p>}

            {packages && packages.length === 0 && (
              <p className="empty">No hay paquetes para esa combinación. Prueba con otra fecha o destino.</p>
            )}
            {packages && packages.length > 0 && (
              <section aria-label="Resultados">
                <h2 className="results-title">
                  {packages.length} paquetes para el {lastSearch ? dayLabel(lastSearch.date) : ""}, ordenados por precio
                </h2>
                <div className="tickets">
                  {packages.map((p) => <PackageTicket key={p.id} pkg={p} onBook={book} />)}
                </div>
              </section>
            )}
            {!packages && !searching && catalog?.ready && (
              <p className="empty">Elige origen, destino y fecha para ver paquetes armados con precios reales del catálogo.</p>
            )}
          </>
        )}

        {view === "orders" && me && (
          <section aria-label="Mis reservas">
            <h2 className="results-title">Mis reservas</h2>
            <OrdersView />
          </section>
        )}
      </main>

      <footer className="footer">
        Herramientas de la demo:{" "}
        <a href="http://localhost:4200" target="_blank" rel="noreferrer">Prefect</a>,{" "}
        <a href="http://localhost:8787" target="_blank" rel="noreferrer">Dask</a>,{" "}
        <a href="http://localhost:8000/graphql" target="_blank" rel="noreferrer">GraphiQL</a>,{" "}
        <a href="http://localhost:8090" target="_blank" rel="noreferrer">fuente simulada</a>.
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
          onViewOrders={() => { setBooking(null); setView("orders"); }}
        />
      )}
    </>
  );
}

