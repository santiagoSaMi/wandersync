# Seguridad por diseño — WanderSync

## Requisitos del enunciado

| Requisito | Implementación | Dónde | Cómo se verifica |
|---|---|---|---|
| **Mitigación de session fixation** | Sesiones server-side con id opaco de 256 bits. Al autenticarse **siempre** se destruye la sesión previa (anónima o no) y se emite un id nuevo. Un id "plantado" por un atacante muere en el login. Logout y expiración (inactividad 30 min, absoluta 8 h) también invalidan en el servidor. | `services/auth/app/main.py` (`login`), `services/gateway/app/schema.py` (`_login`) | `make security` (id cambia, el anterior deja de autenticar, el nuevo sí, logout destruye la sesión) |
| **Hashing seguro** | **Argon2id** (`argon2-cffi`, parámetros RFC 9106 *low memory*), re-hash automático si se endurecen los parámetros, ejecutado en un hilo aparte. Política: 12–128 caracteres, no débil, sin el usuario del correo. | `services/auth/app/main.py` | revisión de código / `select password_hash from users` en `auth_db` (`$argon2id$…`) |
| **Rate limiting en auth, pago y checkout** | Ventana deslizante en Redis (compartida entre réplicas): login 10/min por IP, registro 10/h por IP, **checkout/pago 5/min por usuario** (el pago no tiene endpoint propio: es el primer paso de la SAGA dentro de `createBooking`, así que este límite cubre checkout y pago a la vez, y por usuario, porque solo usuarios autenticados reservan), tope global 240/min por IP. Capas extra: `limit_req` en nginx y bloqueo por cuenta en `auth` (5 fallos ⇒ 15 min). | `services/gateway/app/ratelimit.py`, `config.py`, `context.py`; `frontend/nginx.conf`; `services/auth` | `make security` (RATE_LIMITED tras >10 logins) |
| **Auditoría de cadena de suministro** | `pip-audit` por servicio y `npm audit` del frontend; reportes guardados como evidencia; también en CI (`.github/workflows/ci.yml`). | `scripts/audit.sh` | `make audit` → `docs/security/audit-reports/` |

## Controles adicionales

- **Cookie de sesión** `HttpOnly` (JS no puede leerla, mitiga robo por XSS), `SameSite=Lax` (mitiga CSRF), `Secure` con `COOKIE_SECURE=true` bajo HTTPS. En Redis se guarda `SHA-256(id)`: una filtración de Redis no entrega sesiones válidas.
- **Anti enumeración en el login y fuerza bruta:** contraseña incorrecta y correo inexistente producen **la misma respuesta**, con verificación *dummy* de Argon2id cuando el usuario no existe (mismo tiempo), y el bloqueo tras 5 fallos se aplica igual a correos inexistentes. El **registro** sí indica si el correo ya existe (ver riesgos residuales).
- **CSRF:** queries por GET deshabilitadas, mutaciones solo por `POST` JSON (requiere preflight CORS), cookies `SameSite=Lax`, CORS con lista blanca de orígenes.
- **Autorización (anti-IDOR):** `order` y `myOrders` filtran por el `user_id` de la sesión; el servicio de órdenes devuelve 404 si la orden es de otro usuario. El `user_id` jamás viene del cliente.
- **Integridad de precios:** el gateway relee las ofertas en Supabase; el cliente solo envía IDs.
- **Segmentación de red:** `backend` es una red `internal` (sin salida a internet); solo `gateway` y `frontend` están en `edge`; puertos publicados solo en `127.0.0.1`. Microservicios sin puertos publicados.
  `prefect-server` está además en `backend` para que `orders` refleje cada SAGA en Prefect; `backend` sigue sin salida a internet y la API de Prefect solo se publica en `127.0.0.1:4200`.
- **IP del cliente no falsificable:** el rate limiting por IP solo cree `X-Real-IP` si la conexión viene del nginx (`TRUSTED_PROXY_HOSTS=frontend`, resuelto por DNS de Docker y refrescado cada 30 s); por defecto no se confía en ningún proxy. Una petición directa al puerto 8000 con la cabecera falsificada se limita por su IP real.
- **Autenticación servicio-a-servicio:** `X-Internal-Token` (comparación en tiempo constante) en todos los servicios internos.
- **Mínimo privilegio en datos:** cada microservicio tiene su base y su rol de Postgres; en Supabase el rol `anon` solo tiene `SELECT` con RLS y la clave `secret` solo la usa la ingesta.
- **Contenedores:** usuario no-root, `cap_drop: ALL`, `no-new-privileges`, imágenes `-slim`/`alpine`.
- **GraphQL:** límite de tamaño (≤ 1000 tokens, se corta antes de parsear) y de alias (≤ 5: una petición no puede multiplicar llamadas a Supabase repitiendo `packages` con alias), límite de profundidad, batching deshabilitado, errores inesperados enmascarados (sin trazas), `DisableIntrospection` y GraphiQL apagados si `GRAPHIQL=false`, límites (`first ≤ 30`, `limit ≤ 12`), validación de IATA/IDs con expresiones regulares, y nombres de columnas hacia Supabase solo desde lista blanca.
- **Sin datos de tarjeta:** el pago es simulado; WanderSync no recibe ni almacena PAN/CVV (fuera de alcance PCI-DSS).
- **Secretos:** `.env` en `.gitignore`, generados aleatoriamente por `scripts/init_env.py`; el compose falla si falta alguno (`${VAR:?}`). La clave secreta de Supabase solo llega a `ingestion` y `dask-worker`, y `.env` queda fuera de todo contexto de build (ninguna imagen la contiene). Para entregar el proyecto se usa `make package`: genera el zip sin `.env` y verifica que ningún valor secreto quedó dentro.
- **Cabeceras** (nginx): CSP estricta, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`.

## Riesgos residuales (para discutir en la sustentación)

1. **Sin TLS** en el compose (localhost). En producción: terminación TLS + `COOKIE_SECURE=true` + HSTS.
2. **Bloqueo por cuenta** puede usarse para molestar a un usuario concreto (DoS de cuenta); se mitiga con el límite por IP y podría añadirse CAPTCHA/backoff progresivo.
3. **Dependencias Python sin lockfile:** el frontend ya tiene `package-lock.json` (`npm ci`), pero los servicios Python usan rangos, y `pip-audit` los resuelve al auditar. Ya causó un fallo real: Prefect 3 acepta SQLAlchemy `<3.0` pero su scheduler no funciona con la 2.1 (fijado a `<2.1` en `services/ingestion/requirements.txt`). Para builds reproducibles: `pip-compile` → `requirements.lock` por servicio.
7. **El registro revela si un correo ya existe** ("Ya existe una cuenta con ese correo"). Ocultarlo exige verificación por correo (responder siempre igual y avisar por email al dueño), porque el registro inicia sesión de inmediato. Mitigación: 10 registros/h por IP, así enumerar cuentas por esta vía es muy lento; el login no revela nada.
4. **Modo demostración** (`ALLOW_FAILURE_SIMULATION`, `GRAPHIQL`, mock "caos") debe estar apagado fuera de la sustentación.
5. **Catálogo legible por anónimos** (intencional: son datos públicos), por lo que el esquema de esas tablas es visible por introspección en Supabase.
6. **Una sola instancia de Postgres/Redis** (punto único de fallo); Redis sin persistencia (las sesiones se pierden al reiniciar: efecto aceptable y seguro).
