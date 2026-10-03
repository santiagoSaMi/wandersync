# WanderSync — plataforma de paquetes de viaje

Vuelos + hoteles + autos en un solo paquete. Proyecto del parcial de **Patrones Arquitectónicos Avanzados**.
Stack obligatorio cubierto: **Docker Compose · GraphQL · SAGA · Dask · Prefect** + seguridad por diseño.

```
Navegador ─► nginx (frontend React) ─► Gateway GraphQL ─┬─► Supabase Cloud  (catálogo, GraphQL nativo, solo lectura)
                                                       ├─► auth     (Argon2id + sesiones en Redis)
                                                       └─► orders ──► flights · hotels · cars   (SAGA orquestada)
Prefect ─► Dask (scheduler + 3 workers) ─► scrapea "MockTravel" ─► limpia ─► upsert en Supabase
```

Documentación técnica con diagramas de secuencia de la SAGA: [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) · Seguridad: [`docs/SEGURIDAD.md`](docs/SEGURIDAD.md)

---

## Estado de verificación (léelo antes de la sustentación)

| Qué | Estado |
|---|---|
| Tests sin Docker: parsers, motor SAGA y su runner, OrderActions (reintentos), API de orders (idempotencia, anti-IDOR, compensación), auth (Argon2id, bloqueo, session fixation), contrato de flights/hotels/cars, gateway (`createBooking`, límites de consulta, IP del cliente) y frontend (sondeo del checkout) | **153 tests pasan** (`make test`; los de integración requieren `pip install -r requirements-test.txt` + requirements de los servicios y `npm install` en `frontend/`; sin ellos se omiten, y en CI, con `REQUIRE_TEST_DEPS=1`, fallarían) |
| `docker compose up` completo (14 servicios sanos) | **Verificado** (2026-10-03) |
| Ingesta Prefect + Dask contra Supabase real: grafo con flechas, reintentos, artefactos, cron cada 30 min | **Verificado**: 120 consultas, 3 workers, ~2.600 ofertas cargadas |
| GraphQL de punta a punta (nginx → gateway → Supabase), sin over-fetching visible en `extensions.supabase` | **Verificado** con datos reales |
| SAGA: camino feliz, compensación, modo paso a paso, servicio colgado + recuperación automática | **Verificado** (`make demo`, `make demo-slow`), consistente en las 4 bases |
| Seguridad contra el stack en ejecución (`make security`) | **12/12** verificaciones |
| Dashboards de Prefect (:4200) y Dask (:8787) | **Verificados** (responden y muestran las ejecuciones) |
| App en el navegador (:3000) | Servida y probada por HTTP y con tests de componentes; **haz una pasada visual antes de presentar** |

Antes de presentar: deja el stack arriba unos minutos (la primera ingesta tarda ~2 min) y recorre el guion de abajo una vez. Si algo falla, mira **Solución de problemas** al final.

---

## Puesta en marcha (5 pasos)

Requisitos: Docker Desktop (Compose v2), Python 3 y una cuenta gratuita en [supabase.com](https://supabase.com).

### 1. Crea el proyecto en Supabase Cloud
1. *New project* (región sugerida: South America – São Paulo). La contraseña de la base no la usa la app.
2. **SQL Editor → New query**, pega el contenido de [`supabase/schema.sql`](supabase/schema.sql) y pulsa **Run**.
3. **Project Settings → API Keys** y copia:
   - *Project URL* → `SUPABASE_URL`
   - *Publishable key* (`sb_publishable_…`) → `SUPABASE_ANON_KEY` (solo lectura, la usa el gateway)
   - *Secret key* (`sb_secret_…`) → `SUPABASE_SERVICE_KEY` (**solo la ingesta**; jamás al frontend ni a git)
   - (Sirven también las claves *legacy* `anon` / `service_role`.)
4. Comprueba el GraphQL nativo (debe responder `totalCount: 0`):
   ```bash
   curl -s "$SUPABASE_URL/graphql/v1" -H "apikey: $SUPABASE_ANON_KEY" \
        -H "Content-Type: application/json" \
        -d '{"query":"{ flight_offersCollection(first:1){ totalCount } }"}'
   ```
   (Con claves legacy añade `-H "Authorization: Bearer $SUPABASE_ANON_KEY"`.)

### 2. Genera el `.env`
```bash
python3 scripts/init_env.py      # crea .env con contraseñas/tokens internos aleatorios
```
Abre `.env` y pega **solo** las tres variables de Supabase.

### 3. Levanta todo con un comando
```bash
docker compose up --build        # (o: make up)
```
La primera vez tarda unos minutos (construye 9 imágenes). Al final el contenedor `ingestion` lanza la
primera ingesta: en ~1–2 min el catálogo tendrá unas 2.500–3.000 ofertas en Supabase.

### 4. Abre las herramientas

| URL | Qué es |
|---|---|
| http://localhost:3000 | **La aplicación** (React + Apollo Client) |
| http://localhost:4200 | **Prefect** (dashboard de orquestación) |
| http://localhost:8787 | **Dask** (dashboard del cluster) |
| http://localhost:8000/graphql | GraphiQL del gateway (solo si `GRAPHIQL=true`) |
| http://localhost:8090 | Sitio simulado MockTravel + "modo caos" |

Todo se publica solo en `127.0.0.1`.

### 5. Verifica
```bash
make demo        # camino feliz + compensación de la SAGA por consola
make security    # session fixation, cookies, autenticación, rate limiting
make audit       # pip-audit + npm audit -> docs/security/audit-reports/
```

---

## Guion de la demostración en vivo

**a) Prefect** — http://localhost:4200 → *Deployments* → `catalog-ingestion` → **Run → Quick run**. Abre el *flow run*
(se llama `ingesta <fecha> UTC`) y muestra:
- **El grafo con flechas** (pipeline ETL): `Extraer …` (×120, en paralelo) → `Transformar …` → `Consolidar con Dask` →
  `Cargar flight_offers / hotel_offers / car_offers` → `Purgar fechas pasadas` → `Registrar ingesta`. Haz clic en
  cualquier nodo para ver sus logs (worker de Dask, intento, filas).
- **Reintentos por etapa**: `Extraer` reintenta 4 veces (2, 5, 10, 20 s + jitter) ante fallos de red/fuente;
  `Transformar` no reintenta (es determinista); `Cargar` reintenta 3 veces ante fallos de Supabase. Para provocarlos:
  en http://localhost:8090 sube la probabilidad de fallo a 0.5 y relanza; verás `Extraer …` en `Retrying` y luego `Completed`.
- **Artifacts** (pestaña del flow run y menú *Artifacts*): `resumen-ingesta` (reparto del trabajo por worker de Dask,
  qué consultas necesitaron reintentos y cuántos, filas cargadas por tabla y en cuántos lotes) y `precios-por-ruta`
  (tabla calculada con Dask: mínimo/promedio/máximo por ruta y ciudad).

**b) Dask** — http://localhost:8787 (*Status* / *Workers*) mientras corre el flow: 3 workers × 2 hilos ejecutando las
extracciones y transformaciones en paralelo, y después la consolidación con `dask.bag` (filtrar, deduplicar y agregar
por particiones, estilo MapReduce) repartida en el mismo cluster.

**c) GraphQL** — en la app: busca *Bogotá → Cartagena*. Después, en GraphiQL (http://localhost:8000/graphql), ejecuta:
```graphql
query { packages(origin: "BOG", destination: "CTG", departureDate: "2026-10-24", limit: 3) {
  totalPriceCents  flight { airline flightNumber }  hotel { name stars } } }
```
(Usa una fecha de `catalogStatus { dates }`.) Debajo de `data`, la respuesta trae **`extensions.supabase`**: la consulta
**literal** que el gateway envió a Supabase, con sus columnas. Muestra que (1) vuelos, hoteles y autos van en **una sola**
petición, y (2) al **quitar** `flightNumber` o `stars` y volver a ejecutar, esa columna **desaparece** de la consulta a
Supabase: sin over-fetching hasta la base de datos. Las columnas `id`, precios, `currency` y `nights` siempre van porque
se necesitan para calcular el total del paquete. La traza solo existe con `GRAPHIQL=true` (o `SUPABASE_QUERY_TRACE=true`).

**d) Fallo transaccional + compensación** — en la app: *Reservar* → *Modo demostración* → **"La reserva del auto"** → *Pagar*.
La línea de tiempo muestra Pago ✔, Vuelo ✔, Hotel ✔, Auto ✖ (3 intentos) y luego las reversiones en orden inverso.

**Paso a paso (recomendado para presentar):** marca además *"Mostrar la SAGA paso a paso"*. Cada paso aparece "En curso"
unos 2,5 s y luego ✔/✖, y las compensaciones se ven igual, una a una (≈ 12 s el camino feliz, ≈ 20 s con el fallo del auto).
Ajusta la pausa con `DEMO_STEP_DELAY_MS` en `.env` (máx. 5000; requiere `ALLOW_FAILURE_SIMULATION=true`).
Respaldo por consola con el mismo efecto: `make demo-slow`.

**La SAGA en Prefect (con flechas):** cada reserva aparece en http://localhost:4200 → *Runs* como el flow run
**`SAGA reserva <orden>`** (el diálogo de resultado muestra el id y un enlace). Ábrelo mientras corre en modo paso a paso:
el grafo crece en vivo. Camino feliz: `1 · Pago → 2 · Vuelo → 3 · Hotel → 4 · Auto → 5 · Factura` (estado *Confirmada*).
Con el fallo del auto se dibuja una "V": los pasos bajan, `4 · Reserva de auto` queda en rojo y las reversiones suben en
orden inverso, cada una con una flecha al paso que deshace (estado *Revertida*). Los logs del run explican cada paso.
Evidencia en las bases de datos:
```bash
docker compose exec postgres psql -U postgres -d orders_db  -c "select left(id,8) id,status,failure_reason from orders order by created_at desc limit 3;"
docker compose exec postgres psql -U postgres -d flights_db -c "select left(order_id,8) o,status from reservations order by created_at desc limit 3;"
docker compose exec postgres psql -U postgres -d orders_db  -c "select name,phase,status,attempts from saga_steps order by id desc limit 10;"
```
Prueba de resiliencia: `docker compose kill orders` durante una reserva y `docker compose up -d orders`: al reiniciar, el orquestador compensa las SAGAs interrumpidas (ver `recover_once`). No es instantáneo: una SAGA se considera interrumpida tras 60 s sin avanzar (`SAGA_STUCK_AFTER_SECONDS`) y el bucle revisa cada 30 s, así que la compensación aparece en 1–1,5 min. Esa espera es la que hace seguro correr varias réplicas de `orders`.

---

## Estructura

```
docker-compose.yml        14 servicios, redes edge/backend(interna)/data, healthchecks
supabase/schema.sql       catálogo + RLS + permisos (se ejecuta en Supabase Cloud)
services/
  mock-source/            sitio simulado (HTML real, paginación, latencia, fallos inyectables)
  ingestion/              flow Prefect + tareas Dask (scrape -> limpieza -> upsert) + tests
  flights|hotels|cars/    participantes de la SAGA (una base cada uno)
  orders/                 ORQUESTADOR de la SAGA + pagos/facturas + recuperación   (app/saga.py = motor)
  auth/                   usuarios, Argon2id, sesiones Redis, anti session-fixation
  gateway/                GraphQL (Strawberry): Supabase + auth + orders, rate limiting
frontend/                 React + Vite + Apollo Client; UI con Tailwind CSS v4, shadcn/ui, Motion, Lucide, Embla y Lenis (nginx + CSP estricta)
scripts/                  init_env, audit.sh, security_checks.py, demo_saga.py
docs/                     ARQUITECTURA.md, SEGURIDAD.md, evidencias de auditoría
```

## Decisiones y límites conocidos
- **Fuente de datos:** sitio de viajes *propio* (MockTravel) que se scrapea de verdad (HTML + BeautifulSoup). Las APIs reales (Booking, Amadeus self-service) no están disponibles para un proyecto académico, y el enunciado permite servicios mockeados.
- **SAGA por orquestación con HTTP** (no coreografía con broker): el flujo se lee en un solo lugar, es determinista para la demo y todo es idempotente. Añadir RabbitMQ sería una evolución natural.
- **Un servidor Postgres, cinco bases y cinco roles** (base por servicio, credenciales distintas). En producción serían instancias separadas.
- **Sin lockfiles:** las dependencias usan rangos. Para reproducibilidad: `npm install` una vez y commitea `package-lock.json`; para Python, genera `requirements.lock` con `pip-compile`.
- **Sin TLS:** el compose sirve HTTP en localhost. Detrás de HTTPS pon `COOKIE_SECURE=true`.
- **Modo demostración:** `ALLOW_FAILURE_SIMULATION` y `GRAPHIQL` deben ser `false` fuera de la sustentación.

## Solución de problemas
| Síntoma | Causa probable y arreglo |
|---|---|
| `docker compose` dice "Falta SUPABASE_URL…" | No pegaste las claves en `.env`. |
| La app dice "El catálogo se está cargando" y no avanza | `docker compose logs ingestion dask-worker`. Si ves 401/404 de Supabase: revisa URL/claves y que ejecutaste `schema.sql`. |
| Error `PGRST204` / columna inexistente al hacer upsert | `schema.sql` desactualizado en tu proyecto: vuelve a ejecutarlo. |
| `CATALOG_ERROR` y en `docker compose logs gateway` aparece `Unknown field "totalCount"` | Tu proyecto tiene un `schema.sql` anterior (sin `totalCount` habilitado): vuelve a ejecutarlo; es idempotente. |
| El gateway responde `CATALOG_ERROR` | `docker compose logs gateway` muestra el error real de pg_graphql (nombres de tabla/columna, permisos `anon`). |
| Prefect UI en blanco / "can't connect to API" | Usa `http://localhost:4200` (no `127.0.0.1` con otro puerto); la UI necesita `PREFECT_UI_API_URL`, ya configurado en el compose. |
| Dask: errores de versión o de deserialización | Todo el cluster usa la misma imagen; reconstruye con `docker compose build --no-cache dask-scheduler`. |
| `RATE_LIMITED` al probar mucho | Es el control funcionando. Reinicia contadores: `docker compose exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" -n 1 FLUSHDB'`. |
| Quieres partir de cero | `make reset` (borra volúmenes y bases). |
