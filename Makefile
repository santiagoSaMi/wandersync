.PHONY: help env up down reset logs test audit security demo demo-slow package

help:
	@echo "make env       genera .env con secretos internos (luego pega las 3 claves de Supabase)"
	@echo "make up        construye y levanta TODO el stack (docker compose up --build)"
	@echo "make down      detiene el stack"
	@echo "make reset     detiene y BORRA volumenes (bases de datos, Prefect)"
	@echo "make logs      sigue los logs"
	@echo "make test      tests sin Docker: parsers, SAGA, orders, gateway, auth, participantes, frontend"
	@echo "               (los de integracion se omiten si faltan deps: ver requirements-test.txt)"
	@echo "make security  verificaciones de seguridad contra el stack en ejecucion"
	@echo "make audit     pip-audit + npm audit (requiere Docker)"
	@echo "make demo      demo de la SAGA por consola (camino feliz + compensacion)"
	@echo "make demo-slow la misma demo paso a paso, imprimiendo cada paso en vivo (presentacion)"
	@echo "make package   zip para entregar, SIN .env ni secretos (verificado)"

env:
	python3 scripts/init_env.py

up:
	docker compose up --build

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

test:
	cd services/ingestion && python3 -m unittest discover -s tests -v
	cd services/orders && python3 -m unittest discover -s tests -v
	cd services/gateway && python3 -m unittest discover -s tests -v
	cd services/auth && python3 -m unittest discover -s tests -v
	python3 -m unittest discover -s tests -v
	cd frontend && if [ -d node_modules ]; then npm test; else echo "(frontend: ejecuta 'npm install' en frontend/ para sus tests)"; fi

security:
	python3 scripts/security_checks.py

audit:
	./scripts/audit.sh

demo:
	python3 scripts/demo_saga.py

demo-slow:
	python3 scripts/demo_saga.py --slow

package:
	python3 scripts/package.py
