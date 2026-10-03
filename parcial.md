Evaluación - Segundo Corte: Patrones Arquitectónicos Avanzados

**WanderSync Travel Solutions: Plataforma de Empaquetamiento Turístico Dinámico**

# 1. Encabezado y Datos Generales

**Asignatura: Patrones Arquitectónicos Avanzados**

**Evaluación: Parcial Práctico del Segundo Corte (Taller Extenso - 2 Semanas de Duración)**

**Proyecto**: WanderSync Travel Solutions: Plataforma de Empaquetamiento Turístico Dinámico

Libertad de Stack Tecnológico: Los estudiantes disponen de libertad absoluta para seleccionar los lenguajes de programación, librerías y frameworks de su preferencia tanto para el desarrollo del frontend como de los microservicios del backend. Sin embargo, es de cumplimiento ESTRICTAMENTE OBLIGATORIO incorporar las siguientes tecnologías nucleares en la solución: Docker (mediante Docker Compose), GraphQL, el Patrón SAGA, Dask para computación distribuida y Prefect para la orquestación y observabilidad del sistema.

# 2. Contexto de Negocio y Justificación del Proyecto

## 2.1 Historia y Modelo de Negocio

**WanderSync Travel Solutions** es una empresa emergente del sector Travel-Tech especializada en la comercialización de paquetes turísticos dinámicos e integrados, combinando reservas instantáneas de vuelos, alojamientos hoteleros y alquiler de vehículos. Con el fin de romper la dependencia de APIs comerciales cerradas, costosas y restrictivas, WanderSync ha decidido migrar hacia un esquema basado en la extracción automatizada de datos en tiempo real, junto con una capa de alta resiliencia transaccional y procesamiento asíncrono masivo.

## 2.2 Justificación del Problema y Necesidad de Rediseño

En su arquitectura previa, la plataforma sufría frecuentemente del fenómeno conocido como "reservas huérfanas": escenarios de inconsistencia transaccional donde el pago del cliente era procesado y el vuelo reservado, pero la confirmación del hotel o del automóvil fallaba en la red, dejando la orden en un estado indeterminado sin ejecutar mecanismos automáticos de reversión (rollback). Asimismo, la sincronización masiva de tarifas causaba cuellos de botella críticos en la capa de persistencia y bloqueos de red.

El presente proyecto busca rediseñar integralmente el núcleo del sistema adoptando patrones arquitectónicos distribuidos de vanguardia, esquemas de observabilidad moderna y directrices strictly de ciberseguridad por diseño.

# 3. Módulo de Web Scraping e Ingesta de Datos Distribuida (Dask + Fuentes Reales/Mockeadas)

## 3.1 Objetivo del Scraping y Fuentes de Datos

El sistema debe capturar continuamente información actualizada sobre itinerarios de vuelo, precios de habitaciones y disponibilidad de vehículos desde plataformas públicas (ej. Google Flights, Kayak, Booking.com) o servicios mockeados que simulen la complejidad de dichas fuentes. Cada grupo elegirá libremente la fuente objetivo para su implementación.

## 3.2 Procesamiento Distribuido e Ingesta con Dask

Para evitar el bloqueo de los servicios principales de la aplicación, la recolección de datos debe ejecutarse de forma asíncrona y distribuida. Se implementará un clúster de workers de Dask encargados de paralelizar las tareas de scraping, estructuración y limpieza de datos en segundo plano.

## 3.3 Destino de Persistencia e Integración GraphQL

Los datos procesados por los workers de Dask deben ser persistidos directamente en una base de datos moderna que ofrezca soporte nativo o integración transparente con GraphQL (por ejemplo, Supabase utilizando pg\_graphql / PostgREST, o cualquier motor equivalente). Esta persistencia servirá como la fuente unificada de datos consumida por la capa de API Gateway.

# 4. Requisitos Arquitectónicos Obligatorios

1. **Dockerización & Microservicios:**
   El sistema debe dividirse de forma modular en microservicios independientes, incluyendo al menos los servicios de Vuelos, Hoteles, Autos, Órdenes/Facturación y el Gateway de entrada. Todo el ecosistema debe ser desplegable mediante un único comando (docker compose up).
2. **GraphQL como API Gateway Unificado:**
   Toda interacción entre el frontend y el backend debe encauzarse exclusivamente a través del API Gateway basado en GraphQL. Debe soportar consultas complejas para la consolidación de disponibilidad de paquetes y mutaciones para la creación de reservas, garantizando la eliminación de sobre-obtención de datos (over-fetching).
3. **Patrón SAGA (Coreografía u Orquestación):**
   Implementación obligatoria del patrón SAGA para gestionar el flujo transaccional distribuido al reservar un paquete. Es indispensable demostrar tanto el camino exitoso (happy path) como la ejecución automática de transacciones de compensación ante fallos simulados (ej. si la reserva del vehículo falla, se deben cancelar las reservas confirmadas de vuelo y hotel de manera consistente).
4. **Computación Distribuida con Dask:**
   Uso de Dask para la coordinación y ejecución de tareas distribuidas orientadas a la recolección, transformación e ingesta masiva de información turística.
5. **Orquestación y Observabilidad con Prefect:**
   Creación de un Flow en Prefect que orqueste los trabajos ejecutados en Dask. Debe incorporar políticas explícitas de reintentos (retries) ante fallos en la red o en la extracción de datos, permitiendo además el seguimiento y monitoreo visual del estado de los flujos desde la interfaz gráfica de Prefect.

# 5. Requisitos de Ciberseguridad por Diseño (Semana 9)

* **Gestión Segura de Identidad y Sesiones:** Implementación estricta de mecanismos para mitigar ataques de *Session Fixation* (regenerando de forma obligatoria los identificadores de sesión tras una autenticación exitosa). El almacenamiento de contraseñas debe realizarse empleando algoritmos de hashing orientados a la seguridad con elevado factor de costo (Argon2id o bcrypt).
* **Protección de Superficie y Endpoints:** Configuración de políticas de Rate Limiting en rutas sensibles (autenticación de usuarios, endpoints de pago y checkout de reservas) para prevenir ataques de denegación de servicio y fuerza bruta.
* **Seguridad en la Cadena de Suministro (Supply Chain Security):** Presentación de evidencia formal de auditorías de seguridad sobre las dependencias del proyecto (ej. usando npm audit, pip-audit o la herramienta correspondiente al stack seleccionado). Esta medida previene riesgos asociados a la ejecución remota de código (RCE) y librerías vulnerables.

# 6. Entregables del Proyecto

* **Repositorio de Código Fuente:** Repositorio Git limpio y estructurado que contenga el código completo de los microservicios, frontend, archivos Dockerfile por servicio y el archivo docker-compose.yml ejecutable.
* **Documento Técnico de Arquitectura:** Informe detallado que incluya diagramas de arquitectura, diagramas de secuencia del patrón SAGA (tanto para el flujo exitoso como para la ejecución de transacciones de compensación) y la justificación técnica de las decisiones adoptadas.
* **Demostración en Vivo:** Grabación o sustentación en directo donde se evidencie: (a) el panel de control de Prefect monitoreando los flujos, (b) la ejecución de tareas distribuidas en Dask, (c) el consumo de la API GraphQL desde el frontend y (d) la simulación de un fallo transaccional comprobando el correcto funcionamiento de las compensaciones SAGA.

# 7. Rúbrica de Evaluación Detallada

| Criterio | Peso | Excelente (100%) | Aceptable (60%) | Insuficiente (0-30%) |
| --- | --- | --- | --- | --- |
| **Arquitectura y Dockerización** | 15% | Despliegue automatizado completo con docker compose up. Microservicios aislados, eficientes y correctamente configurados. | El entorno Docker levanta con intervenciones manuales o presenta inconsistencias menores en la comunicación inter-servicio. | Fallo al levantar contenedores o ausencia de estructuración en microservicios. |
| **API Gateway con GraphQL y Persistencia** | 15% | Gateway GraphQL completamente funcional, consultas/mutaciones optimizadas sin over-fetching e integración fluida con la base de datos (ej. Supabase). | API GraphQL funcional pero con esquemas ineficientes o acoplamiento innecesario entre servicios. | Ausencia de API Gateway o no integración de GraphQL para la comunicación del frontend. |
| **Patrón SAGA y Consistencia Transaccional** | 25% | Demostración impecable del happy path y de la ejecución de transacciones de compensación automáticas ante fallos simulados. | Implementación parcial del patrón SAGA; las compensaciones requieren intervención manual o fallan en escenarios específicos. | No se implementa el patrón SAGA o persistencia inconsistente ante errores en reservas. |
| **Computación Distribuida y Observabilidad (Dask + Prefect)** | 25% | Workers de Dask ejecutando scraping/ingesta asíncrona integrados con Prefect Flows, retries configurados y monitoreo en dashboard. | Dask o Prefect integrados de forma limitada, sin manejo claro de retries o con observabilidad deficiente. | Omisión de Dask o Prefect dentro del flujo de la arquitectura. |
| **Ciberseguridad y Resiliencia** | 20% | Implementación completa de mitigación de Session Fixation, hashing robusto (Argon2id/bcrypt), Rate Limiting y reporte formal de Supply Chain audit. | Implementación parcial de requisitos de seguridad o ausencia de evidencias en la auditoría de dependencias. | Incumplimiento de las directrices de ciberseguridad por diseño especificadas. |