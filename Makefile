.DEFAULT_GOAL := help
API := cd backend &&
VENV := .venv/bin

.PHONY: help install db migrate seed api web test lint up down

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	$(API) python3 -m venv .venv && $(VENV)/pip install -e ".[dev]"
	cd frontend && npm install

db: ## Start PostgreSQL in Docker
	docker compose up -d db

migrate: ## Apply database migrations
	$(API) $(VENV)/alembic upgrade head

seed: ## Load demo subjects and courses (idempotent)
	$(API) $(VENV)/python -m app.scripts.seed

api: ## Run the API with auto-reload on :8000
	$(API) $(VENV)/uvicorn app.main:app --reload

web: ## Run the web app on :3000
	cd frontend && npm run dev

test: ## Run backend tests
	$(API) $(VENV)/pytest

lint: ## Lint and type-check both apps
	$(API) $(VENV)/ruff check . && $(VENV)/mypy
	cd frontend && npm run lint && npm run typecheck

up: ## Build and run the whole stack in Docker
	docker compose up --build

down: ## Stop the Docker stack
	docker compose down
