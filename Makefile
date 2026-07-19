# Tiqora Makefile (mirrors justfile for environments without just)

.PHONY: help dev-up dev-down sync api worker mcp test lint fmt fe-install fe-dev fe-build build compose-check

help:
	@echo "Targets: dev-up dev-down sync api worker mcp test lint fmt fe-install fe-dev fe-build build compose-check"

dev-up:
	docker compose -f docker-compose.dev.yml up -d

dev-down:
	docker compose -f docker-compose.dev.yml down

sync:
	cd backend && uv sync

api:
	cd backend && uv run uvicorn tiqora.api.app:create_app --factory --reload --host 0.0.0.0 --port 8000

worker:
	cd backend && uv run python -m tiqora.worker

mcp:
	cd backend && uv run python -m tiqora.mcp_server

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check src tests
	cd backend && uv run ruff format --check src tests
	cd backend && uv run mypy src/tiqora

fmt:
	cd backend && uv run ruff check --fix src tests
	cd backend && uv run ruff format src tests

fe-install:
	cd frontend && pnpm install

fe-dev:
	cd frontend && pnpm dev

fe-build:
	cd frontend && pnpm build

build:
	docker build -t tiqora:local .

compose-check:
	docker compose -f docker-compose.dev.yml config -q
	docker compose -f docker-compose.example.yml config -q
