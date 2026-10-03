-- =====================================================================
--  WanderSync - esquema del CATALOGO en Supabase (plano de lectura)
--  Ejecutar UNA vez en: Supabase Dashboard > SQL Editor > New query > Run
--  Es idempotente: puedes volver a ejecutarlo sin perder datos.
--
--  Quien escribe:  la ingesta (Dask + Prefect) con la clave SECRET / service_role
--  Quien lee:      el API Gateway GraphQL con la clave PUBLISHABLE / anon (solo SELECT)
--  Las reservas reales NO viven aqui: cada microservicio tiene su propia base.
-- =====================================================================

-- GraphQL nativo (pg_graphql). En Supabase ya viene habilitada; se deja por si acaso.
create extension if not exists pg_graphql;

-- Nombres de GraphQL = nombres SQL tal cual (flight_offersCollection, price_cents, ...)
comment on schema public is e'@graphql({"inflect_names": false})';

-- ---------------------------------------------------------------------
--  Vuelos
-- ---------------------------------------------------------------------
create table if not exists public.flight_offers (
  id               text primary key,               -- hash estable: permite upsert idempotente
  source           text        not null,
  origin           text        not null,           -- IATA
  destination      text        not null,           -- IATA
  origin_city      text        not null,
  destination_city text        not null,
  airline          text        not null,
  flight_number    text        not null,
  departure_date   date        not null,
  departure_time   text        not null,           -- HH:MM hora local
  arrival_time     text        not null,
  duration_minutes integer     not null check (duration_minutes > 0),
  stops            integer     not null default 0 check (stops >= 0),
  cabin            text        not null default 'ECONOMY',
  price_cents      integer     not null check (price_cents > 0),
  currency         text        not null default 'USD' check (char_length(currency) = 3),
  scraped_at       timestamptz not null default now()
);
create index if not exists flight_offers_search_idx
  on public.flight_offers (origin, destination, departure_date, price_cents);

-- ---------------------------------------------------------------------
--  Hoteles
-- ---------------------------------------------------------------------
create table if not exists public.hotel_offers (
  id                    text primary key,
  source                text        not null,
  city_code             text        not null,      -- IATA de la ciudad
  city_name             text        not null,
  name                  text        not null,
  stars                 integer     not null check (stars between 1 and 5),
  rating                double precision,          -- 0..10
  amenities             text        not null default '',   -- separadas por coma
  checkin               date        not null,
  checkout              date        not null,
  nights                integer     not null check (nights > 0),
  price_per_night_cents integer     not null check (price_per_night_cents > 0),
  total_price_cents     integer     not null check (total_price_cents > 0),
  currency              text        not null default 'USD' check (char_length(currency) = 3),
  scraped_at            timestamptz not null default now()
);
create index if not exists hotel_offers_search_idx
  on public.hotel_offers (city_code, checkin, total_price_cents);

-- ---------------------------------------------------------------------
--  Autos
-- ---------------------------------------------------------------------
create table if not exists public.car_offers (
  id                  text primary key,
  source              text        not null,
  city_code           text        not null,
  city_name           text        not null,
  company             text        not null,
  model               text        not null,
  category            text        not null,
  transmission        text        not null,        -- MANUAL | AUTOMATIC
  seats               integer     not null check (seats > 0),
  pickup_date         date        not null,
  dropoff_date        date        not null,
  days                integer     not null check (days > 0),
  price_per_day_cents integer     not null check (price_per_day_cents > 0),
  total_price_cents   integer     not null check (total_price_cents > 0),
  currency            text        not null default 'USD' check (char_length(currency) = 3),
  scraped_at          timestamptz not null default now()
);
create index if not exists car_offers_search_idx
  on public.car_offers (city_code, pickup_date, total_price_cents);

-- ---------------------------------------------------------------------
--  Bitacora de ingestas (la escribe el flow de Prefect al terminar)
--  meta = {"routes": [["BOG","CTG"],...], "dates": ["2026-10-04",...],
--          "cities": {"BOG": "Bogota", ...}, "nights": 3}
-- ---------------------------------------------------------------------
create table if not exists public.ingestion_runs (
  id             text primary key,                 -- id del flow run de Prefect
  started_at     timestamptz not null,
  finished_at    timestamptz not null,
  status         text        not null,             -- SUCCESS | PARTIAL | FAILED
  flights_count  integer     not null default 0,
  hotels_count   integer     not null default 0,
  cars_count     integer     not null default 0,
  failed_queries integer     not null default 0,
  meta           jsonb       not null default '{}'::jsonb
);

-- ---------------------------------------------------------------------
--  pg_graphql: `totalCount` es OPT-IN por tabla. Sin esto, la consulta de conteos del
--  gateway (catalogStatus) falla con "Unknown field totalCount" y la app nunca queda lista.
-- ---------------------------------------------------------------------
comment on table public.flight_offers is e'@graphql({"totalCount": {"enabled": true}})';
comment on table public.hotel_offers  is e'@graphql({"totalCount": {"enabled": true}})';
comment on table public.car_offers    is e'@graphql({"totalCount": {"enabled": true}})';

-- ---------------------------------------------------------------------
--  Seguridad: RLS + permisos minimos
--   - anon / authenticated: SOLO lectura (los datos de catalogo son publicos)
--   - service_role: escritura (ademas se salta RLS por diseno)
-- ---------------------------------------------------------------------
alter table public.flight_offers  enable row level security;
alter table public.hotel_offers   enable row level security;
alter table public.car_offers     enable row level security;
alter table public.ingestion_runs enable row level security;

do $$
declare t text;
begin
  foreach t in array array['flight_offers','hotel_offers','car_offers','ingestion_runs'] loop
    execute format('drop policy if exists "catalog_read_all" on public.%I', t);
    execute format(
      'create policy "catalog_read_all" on public.%I for select to anon, authenticated using (true)', t);
  end loop;
end $$;

grant usage on schema public to anon, authenticated, service_role;
revoke all on public.flight_offers, public.hotel_offers, public.car_offers, public.ingestion_runs
  from anon, authenticated;
grant select on public.flight_offers, public.hotel_offers, public.car_offers, public.ingestion_runs
  to anon, authenticated;
grant all on public.flight_offers, public.hotel_offers, public.car_offers, public.ingestion_runs
  to service_role;

-- Verificacion rapida (debe devolver 4 filas):
--   select table_name from information_schema.tables
--   where table_schema = 'public' and table_name in
--   ('flight_offers','hotel_offers','car_offers','ingestion_runs');
