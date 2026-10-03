#!/bin/bash
# Se ejecuta UNA sola vez (al crear el volumen). Crea una base y un rol por microservicio:
# cada servicio solo puede tocar SU base ("database per service").
set -euo pipefail

create_db() {
  local db="$1" user="$2" pass="$3"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE ROLE ${user} LOGIN PASSWORD '${pass}';
    CREATE DATABASE ${db} OWNER ${user};
    REVOKE ALL ON DATABASE ${db} FROM PUBLIC;
EOSQL
  echo "[init] base ${db} lista (rol ${user})"
}

create_db flights_db flights_svc "${FLIGHTS_DB_PASSWORD}"
create_db hotels_db  hotels_svc  "${HOTELS_DB_PASSWORD}"
create_db cars_db    cars_svc    "${CARS_DB_PASSWORD}"
create_db orders_db  orders_svc  "${ORDERS_DB_PASSWORD}"
create_db auth_db    auth_svc    "${AUTH_DB_PASSWORD}"
