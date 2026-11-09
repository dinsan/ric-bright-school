# Lumina

A learning platform for Grades 1 to 12. This repository is the first stage: a clean architecture for the backend and frontend, with a working catalog (subjects and courses) running end to end.

| Layer | Stack |
| --- | --- |
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Alembic, PostgreSQL |
| Frontend | Next.js 16 (App Router), React 19, TypeScript (strict), Tailwind CSS 4, TanStack Query |
| Tooling | Docker Compose, pytest, ruff, mypy, ESLint, Makefile |

## Quick start

**Everything in Docker**

```bash
docker compose up --build
docker compose exec api python -m app.scripts.seed   # demo data
```

Web: http://localhost:3000 · API docs: http://localhost:8000/docs

**Local development** (hot reload)

```bash
make install     # venv + npm install
make db          # PostgreSQL in Docker
make migrate
make seed
make api         # terminal 1: http://localhost:8000
make web         # terminal 2: http://localhost:3000
```

Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to `frontend/.env.local` to change settings.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health/live` | Liveness probe (never touches the database) |
| GET | `/health/ready` | Readiness probe (checks the database) |
| GET | `/api/v1/subjects` | List subjects |
| POST | `/api/v1/subjects` | Create a subject |
| GET | `/api/v1/courses` | Search courses: `grade_level`, `subject`, `q`, `status`, `limit`, `offset` |
| POST | `/api/v1/courses` | Create a course |
| GET | `/api/v1/courses/{id}` | Get a course by id |
| GET | `/api/v1/courses/slug/{slug}` | Get a course by slug |

Every error uses one envelope: `{"error": {"code", "message", "details", "request_id"}}`. Every response carries an `X-Request-ID` that also appears in the logs.

## Architecture

### Backend: modular monolith with layers

```
backend/
├── app/
│   ├── main.py              application factory (create_app)
│   ├── core/                config, logging, errors, middleware, pagination
│   ├── db/                  declarative base, mixins, engine/session, transactions
│   ├── api/                 shared HTTP concerns: deps, error handlers, health, v1 router
│   └── modules/
│       └── catalog/         one folder per business area
│           ├── domain.py        enums and constants, no framework imports
│           ├── models.py        SQLAlchemy tables
│           ├── schemas.py       Pydantic request/response models
│           ├── repository.py    queries only
│           ├── service.py       business rules and the transaction boundary
│           ├── deps.py          wiring
│           └── router.py        HTTP endpoints
├── migrations/              Alembic
└── tests/
```

Request flow: `router → service → repository → database`. Routers never touch the session, repositories never commit, and services never import FastAPI. New areas (users, enrolments, lessons, assessments) are new folders under `modules/` with the same shape, so they can be split into services later if needed.

### Frontend: feature-based

```
frontend/src/
├── app/                     routes only (thin): layout, pages, error and not-found
├── features/
│   └── catalog/             api, hooks, types, URL state, components
├── components/              shared UI (ui/) and layout
├── lib/                     API client, env, helpers
└── hooks/ · config/
```

- The landing page and course list shell are static. The course detail page is rendered on the server (revalidated every 60 seconds) for SEO and fast first paint.
- Course search state lives in the URL, so filtered views are shareable and the back button works.
- One API client turns the backend's error envelope into a typed `ApiError`.

## Decisions worth knowing

- **Async everywhere**: `asyncpg` with `AsyncSession`. Relationships use `lazy="raise"` so an accidental lazy load fails loudly instead of causing hidden queries. Queries load what they need explicitly.
- **Transactions**: services commit; a unique-constraint race is translated into a 409 instead of a 500.
- **Migrations are hand-reviewed**: constraints follow a naming convention, and `alembic check` confirms the models and migration agree.
- **Probes**: liveness and readiness are separate so a database outage does not cause restart loops.
- **Tests run on in-memory SQLite** for speed. Add a PostgreSQL job in CI for dialect-specific behaviour before relying on it.

## Not built yet (suggested order)

1. Authentication and roles (student, parent, teacher, admin), then protect the write endpoints and the `status` filter
2. Lessons, content and enrolments inside `modules/`
3. Progress tracking and assessments
4. CI pipeline (lint, type-check, tests against PostgreSQL, build images)
5. Generated TypeScript types from `/openapi.json`, frontend tests, observability (metrics and tracing)
6. Mobile app, which can reuse the same API
