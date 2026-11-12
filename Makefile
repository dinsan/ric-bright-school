.DEFAULT_GOAL := help
PYTHON ?= python3
VENV := .venv/bin
COMPOSE := docker compose

.PHONY: help bootstrap install db mail storage migrate seed reindex admin api test-db test lint requirements up down protect-branch

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

bootstrap: ## First-time setup in one command: checks, .env, .venv, database, migrations, demo data, admin
	@PYTHON="$(PYTHON)" MAKE="$(MAKE)" bash scripts/bootstrap.sh

install: ## Create .venv and install the pinned dependencies (requirements-dev.txt)
	$(PYTHON) -m venv .venv
	$(VENV)/pip install -r requirements-dev.txt
	$(VENV)/pip install --no-deps -e .

db: ## Start PostgreSQL in Docker (port 5432)
	$(COMPOSE) up -d --wait db

mail: ## Start Mailpit in Docker: catches every email the API sends (http://localhost:8025)
	$(COMPOSE) up -d --wait mailpit

storage: ## Start MinIO in Docker (local S3 for uploads) and create the bucket
	$(COMPOSE) up -d minio
	$(COMPOSE) run --rm minio-init

migrate: ## Apply database migrations
	$(VENV)/alembic upgrade head

seed: ## Load demo subjects and courses (idempotent)
	$(VENV)/python -m app.scripts.seed

reindex: ## Rebuild the full-text search index from published courses
	$(VENV)/python -m app.scripts.reindex_search

admin: ## Create a platform admin: make admin EMAIL=you@example.com (asks for a password)
	@test -n "$(EMAIL)" || { echo "Usage: make admin EMAIL=you@example.com"; exit 1; }
	$(VENV)/python -m app.scripts.create_admin --email "$(EMAIL)"

api: ## Run the API with auto-reload on :8000
	$(VENV)/uvicorn app.main:app --reload

test-db: ## Start the throwaway test PostgreSQL in Docker (port 5433)
	$(COMPOSE) up -d --wait test-db

# Set LUMINA_TEST_DATABASE_URL to use your own PostgreSQL instead of the Docker one.
# Pass pytest options with ARGS, e.g. make test ARGS="tests/test_auth.py -k refresh -v"
test: ## Run the tests against PostgreSQL (ARGS="..." for pytest options)
	@if [ -z "$$LUMINA_TEST_DATABASE_URL" ]; then $(MAKE) --no-print-directory test-db; fi
	$(VENV)/pytest $(ARGS)

lint: ## Lint, check formatting and type-check
	$(VENV)/ruff check .
	$(VENV)/ruff format --check .
	$(VENV)/mypy

requirements: ## Regenerate requirements.txt and requirements-dev.txt from pyproject.toml
	bash scripts/requirements.sh

up: ## Build and run the whole stack in Docker (db, mailpit, minio, migrate, api)
	$(COMPOSE) up --build

down: ## Stop the Docker stack, including the test database
	$(COMPOSE) --profile test down

protect-branch: ## (GitHub admin, once) Block merging into main unless CI passed
	bash scripts/protect-branch.sh
