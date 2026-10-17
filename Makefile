.PHONY: help up down demo test logs build smoke clean

help:
	@echo "Aegis Exchange - Management Targets:"
	@echo "  make up      - Start API and Dashboard containers"
	@echo "  make down    - Stop all running containers"
	@echo "  make demo    - Start full stack with real-time demo traffic"
	@echo "  make test    - Run all pytest test suites"
	@echo "  make logs    - Follow container logs"
	@echo "  make build   - Build Docker container images"
	@echo "  make smoke   - Run smoke tests against active stack"

up:
	docker compose up -d api dashboard

down:
	docker compose down

demo:
	docker compose --profile demo up

test:
	pytest -v

logs:
	docker compose logs -f

build:
	docker compose build

smoke:
	python scripts/smoke.py

clean:
	docker compose down -v
