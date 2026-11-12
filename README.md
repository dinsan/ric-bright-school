# Lumina API

Backend for Lumina, a learning platform for Grades 1 to 12. It serves a catalog of subjects and courses, the courses' units and lessons (text, images, video, files, embeds, interactive activities, quizzes), media uploads, curriculum standards, search, classes, and accounts for parents, teachers, students and administrators. Teachers build a course as a **draft** and **publish** it as an immutable version; learners only ever see published versions. Adults sign in with their **email** (and a password, or Google / Microsoft / Apple); **children sign in in a way that fits their age** (pictures in Grades 1–3, a username or email from Grade 4). The web frontend lives in a separate project and talks to this API over HTTP.

| Layer | Stack |
| --- | --- |
| API | Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Alembic, PostgreSQL 17 |
| Content | Lesson blocks as validated JSON (Pydantic), immutable published versions, PostgreSQL full-text search, S3-compatible storage for uploads (MinIO locally, via boto3) |
| Accounts | Argon2id password hashing, JWT access tokens + rotating refresh tokens, OpenID Connect (Google, Microsoft, Apple), TOTP two-factor, email via SMTP |
| Tooling | Docker Compose, pytest, ruff, mypy, Makefile, GitHub Actions |

## Prerequisites

- **Docker** with Compose v2 (`docker compose version`). Needed for the database, the email catcher, the upload storage and the tests.
- **Python 3.11+** and GNU Make. Only needed for local development outside Docker.

## Quick start: everything in Docker

```bash
cp .env.example .env                 # optional; defaults work for local use
docker compose up --build
```

This starts these services:

1. `db`: PostgreSQL on port 5432.
2. `mailpit`: catches every email the API sends. Read them at http://localhost:8025.
3. `minio`: S3-compatible storage for uploaded images, videos and files, on port 9000 (web console at http://localhost:9001, user `lumina`, password `lumina-dev-storage-secret`).
4. `minio-init`: creates the `lumina-media` bucket once, then exits.
5. `migrate`: runs `alembic upgrade head` once, then exits.
6. `api`: starts only after the bucket exists and the migrations succeeded. Serves http://localhost:8000.

In a second terminal:

```bash
curl http://localhost:8000/health/ready                         # {"status":"ok",...,"checks":{"database":"up"}}
docker compose exec api python -m app.scripts.seed              # demo subjects, courses and lessons (idempotent)
docker compose exec api python -m app.scripts.create_admin --email you@example.com   # asks for a password
```

API docs: http://localhost:8000/docs. Stop with `Ctrl+C`, then `docker compose down` (add `-v` to also delete the database).

## Local development (hot reload)

The database, the email catcher and the upload storage run in Docker; the API runs on your machine. One command sets everything up:

```bash
make bootstrap             # checks, .env, .venv, PostgreSQL, Mailpit, MinIO, migrations, demo data, admin account
make api                   # http://localhost:8000 with auto-reload
```

`make bootstrap` first checks the prerequisites (Python 3.11+ with `venv`, Docker running, Compose v2) and stops with a fix-it message if one is missing. If `python3` is older than 3.11, point it at a newer one: `make bootstrap PYTHON=python3.12`.

On the first run it creates `.env` with a random `LUMINA_SECRET_KEY`, and a platform admin `admin@example.com` whose generated password is **printed once** at the end. It is safe to run again: an existing `.env` and existing accounts are never changed.

The same steps one at a time:

```bash
make install               # .venv with the pinned dependencies (requirements-dev.txt)
cp .env.example .env       # settings; defaults work as is
make db                    # PostgreSQL in Docker on :5432
make mail                  # Mailpit in Docker: SMTP on :1025, inbox on http://localhost:8025
make storage               # MinIO in Docker on :9000 (console :9001) and the media bucket
make migrate               # apply migrations
make seed                  # demo data (also fills the search index)
make admin EMAIL=you@example.com   # a platform admin (asks for a password)
```

Run `make` with no target to list every command. `make reindex` rebuilds the search index from the published courses (after restoring a backup, for example).

### Without make (plain pip)

The pinned dependency lists are [`requirements.txt`](requirements.txt) (what the API needs to run) and [`requirements-dev.txt`](requirements-dev.txt) (plus tests, lint and type-check). They work on Linux, macOS and Windows.

```bash
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt        # or requirements.txt to only run the API
pip install --no-deps -e .
alembic upgrade head
uvicorn app.main:app --reload
```

After changing dependencies in `pyproject.toml`, regenerate both files with `make requirements` (a test fails if they drift apart).

## Configuration

Settings come from environment variables prefixed with `LUMINA_`, or from a `.env` file in the project root. Real environment variables win. [`.env.example`](.env.example) documents every variable.

| Variable | Default | Notes |
| --- | --- | --- |
| `LUMINA_ENVIRONMENT` | `local` | `local`, `test`, `staging` or `production`. Non-local uses JSON logs; production hides `/docs` and enforces the checks below. |
| `LUMINA_DATABASE_URL` | `postgresql+asyncpg://lumina:lumina@localhost:5432/lumina` | Must use the `postgresql+asyncpg` driver. |
| `LUMINA_SECRET_KEY` | development key | Signs access tokens. 32+ random characters. **Production refuses to start with the default.** |
| `LUMINA_ENCRYPTION_KEY` | derived from the secret key | Encrypts two-factor secrets. Set it to its own random value and **never change it**. |
| `LUMINA_GOOGLE_*`, `LUMINA_MICROSOFT_*`, `LUMINA_APPLE_*` | unset (off) | Social sign-in; a provider is on once its values are set. Also `LUMINA_API_PUBLIC_URL` for the redirect URI. See `.env.example`. |
| `LUMINA_CORS_ORIGINS` | `["http://localhost:3000"]` | **JSON array**. See below. |
| `LUMINA_FRONTEND_URL` | `http://localhost:3000` | Web app address, used in the links inside emails. |
| `LUMINA_SMTP_HOST` / `_PORT` | `localhost` / `1025` | Mailpit locally. `_USERNAME`, `_PASSWORD`, `_STARTTLS` for a real provider. |
| `LUMINA_STORAGE_ENDPOINT_URL` | `http://localhost:9000` | S3-compatible storage the API talks to (MinIO locally; AWS S3, Cloudflare R2, … in production). Also `_BUCKET` (`lumina-media`), `_REGION`, `_ACCESS_KEY`, `_SECRET_KEY` (**production refuses the MinIO default**). |
| `LUMINA_STORAGE_PUBLIC_ENDPOINT_URL` | same as above | The address browsers use for upload/download links, when it differs (Docker Compose sets `http://localhost:9000` while the API uses `http://minio:9000`). |
| `LUMINA_MEDIA_CDN_BASE_URL` | unset | A CDN in front of the bucket: download links become CDN URLs instead of signed storage links. |
| `LUMINA_EMBED_ALLOWED_HOSTS` | YouTube, Vimeo, GeoGebra, PhET, Desmos, Scratch, Google Docs | **JSON array** of sites lessons may embed (https only). |
| `LUMINA_ACCESS_TOKEN_TTL_MINUTES` | `15` | Also `LUMINA_REFRESH_TOKEN_TTL_DAYS` (30). |
| `LUMINA_LOG_LEVEL` | `INFO` | |

Startup errors about settings never print the values themselves, so secrets do not end up in logs.

### `LUMINA_CORS_ORIGINS` format

The value must be a JSON array of origins (scheme + host + optional port, no path, no trailing slash). Wrap it in single quotes so the inner double quotes survive:

```bash
LUMINA_CORS_ORIGINS='["http://localhost:3000"]'
LUMINA_CORS_ORIGINS='["https://app.lumina.example","https://staging.lumina.example"]'
```

A bare URL such as `LUMINA_CORS_ORIGINS=http://localhost:3000` is not valid JSON, and the app refuses to start (`error parsing value for field "cors_origins"`). With `LUMINA_ENVIRONMENT=production`, startup also fails if the list is empty or contains `"*"`.

## Accounts and sign-in

The full reference (roles, sign-in by age, permissions, every flow, security properties) is in [**docs/auth.md**](docs/auth.md). In short:

- **Parents and teachers sign up** themselves with their email (password, or Google / Microsoft / Apple) and confirm it. **Students are created by a parent or a teacher.** **Admins** are created with `make admin` or appointed by another admin.
- **Sign-in depends on age**:

  | Grades | Sign in with | Session |
  | --- | --- | --- |
  | 1–3 | Class code + 3 pictures on a classroom device, or username + password | 8 hours, not extended |
  | 4–8 | Username or email + password | 7 days |
  | 9–12 | Username or email + password, Google, Microsoft, Apple | 14 days |
  | Adults | Email + password, Google, Microsoft, Apple; two-factor for teachers and admins | 30 days |

  Parents (and teachers, for children in their classes) set up usernames, passwords and picture passwords for children who have no email of their own.
- **Reading** published courses and subjects needs no account. **Writing** needs the right role: teachers create courses and edit their own (only **verified** teachers can publish), teachers see only their own classes, parents only their own children, platform admins everything. These ownership rules live in each module's `policies.py`.
- Sign-in returns a **15-minute access token** (send it as `Authorization: Bearer …`) and a **single-use refresh token** (exchange it at `POST /auth/refresh`), or, with two-factor on, an `mfa_token` to finish at `POST /auth/login/mfa`.

Try it with curl (after `make bootstrap` or with an admin you created):

```bash
# 1. sign in (use the password make bootstrap printed)
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "THE-PRINTED-PASSWORD"}'
# -> {"access_token": "eyJ...", "refresh_token": "...", "token_type": "bearer", "expires_in": 900}

# 2. use the access token
TOKEN=eyJ...
curl -s http://localhost:8000/api/v1/me -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://localhost:8000/api/v1/subjects \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": "Music"}'
```

To try sign-up: `POST /api/v1/auth/register`, open the email at http://localhost:8025, and post the `token` from its link to `POST /api/v1/auth/verify-email`. In `/docs`, click **Authorize** and paste an access token to try any endpoint.

## API

All paths except the health probes start with `/api/v1`. "Sign-in" means a valid `Authorization: Bearer` access token.

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/health/live`, `/health/ready` | public | Liveness (no database) and readiness (checks the database) probes |
| POST | `/auth/register` | public | Sign up as a parent or teacher (always 202) |
| POST | `/auth/verify-email`, `/auth/verify-email/resend` | public | Confirm the email address / send the link again |
| POST | `/auth/login` | public | Sign in with email + password: tokens, or a two-factor challenge |
| POST | `/auth/login/username`, `/auth/login/picture`, `/auth/login/mfa` | public | Sign in with a username (students), class code + pictures (Grades 1–3), or finish with a 2FA code |
| GET | `/auth/class-sign-in/{code}` | public | Picture sign-in page of a class: children and pictures to show |
| GET, POST | `/auth/oauth/providers`, `/auth/oauth/{provider}/start`, `/auth/oauth/exchange` | public | Sign in or up with Google, Microsoft, Apple |
| POST | `/auth/refresh` | public (refresh token) | New tokens; the old refresh token stops working |
| POST | `/auth/logout` | sign-in | End this session |
| POST | `/auth/password/forgot`, `/auth/password/reset` | public | Reset a forgotten password (also activates child accounts) |
| POST | `/auth/email/confirm` | public (emailed token) | Confirm a new email address |
| GET, PATCH, DELETE | `/me` | sign-in | Own profile; update name, locale, time zone; delete the account |
| POST | `/me/password`, `/me/email` | sign-in | Change password / start an email change |
| GET, DELETE, POST | `/me/sessions`, `/me/sessions/{id}`, `/me/sessions/revoke-others` | sign-in | See and end sessions (devices) |
| GET, POST, DELETE | `/me/oauth`, `/me/oauth/{provider}/link`, `/me/oauth/{provider}` | sign-in | Link and unlink Google, Microsoft, Apple |
| POST, DELETE | `/me/mfa/totp`, `/me/mfa/totp/confirm`, `/me/mfa/recovery-codes` | teacher, admin | Two-factor authentication |
| PATCH | `/me/teacher-profile` | teacher | Bio and subjects |
| GET, POST | `/me/children`, `/me/children/link` | parent | List, add, or link children |
| PUT | `/me/children/{id}/sign-in` | parent | Set up the child's username, password or picture password |
| PUT | `/me/active-child` | parent | Switch the active child for this device |
| POST | `/me/link-code` | student | Code a parent can use to link to this account |
| GET | `/subjects` | public | List subjects |
| POST | `/subjects` | teacher, admin | Create a subject |
| DELETE | `/subjects/{id}` | admin | Delete a subject (409 while any course uses it) |
| GET | `/courses` | public | Search published courses: `grade_level`, `subject`, `q`, `tag`, `difficulty`, `language`, `limit`, `offset`. `status=draft`/`archived`: teachers (own) and admins |
| POST | `/courses` | teacher, admin | Create a course (publishing needs a verified teacher) |
| GET | `/courses/{id}`, `/courses/slug/{slug}` | public | A published course (drafts: author and admins only) |
| PATCH | `/courses/{id}` | author, admin | Partial update (`"status": "published"` goes through the same checks as `/publish`) |
| DELETE | `/courses/{id}` | author, admin | Archive a course (soft delete) |
| GET | `/courses/recommended` | public | Published courses for a grade, those in learning paths first |
| GET, PUT | `/courses/{id}/prerequisites` | public / author, admin | Courses to take first (no circles) |
| GET | `/courses/{id}/draft`, `/courses/{id}/draft/lessons/{lesson_id}` | author, admin | The draft outline / a draft lesson with its blocks |
| POST, PATCH, DELETE, PUT | `/courses/{id}/units…`, `/courses/{id}/units/{unit_id}/lessons…`, `/courses/{id}/units/order` | author, admin | Add, rename, delete and reorder units and lessons |
| PATCH, DELETE, PUT, POST | `/courses/{id}/lessons/{lesson_id}`, `…/content`, `…/move`, `…/standards`, `…/prerequisites` | author, admin | Edit a lesson, save its blocks (with a revision check), move it, link standards and prerequisites |
| POST | `/courses/{id}/publish`, `/courses/{id}/unpublish` | author (verified teacher), admin | Freeze the draft into a new immutable version / take the course offline |
| GET | `/courses/{id}/versions` | author, admin | Published versions |
| GET | `/courses/{id}/content`, `/courses/{id}/content/lessons/{lesson_id}` | public (published) | What learners read: the current version, or `?version=N` |
| POST | `/courses/{id}/duplicate` | teacher, admin | Copy a course into a new draft |
| GET | `/courses/{id}/export` | public (published), author and admins (`?source=draft`) | A course as one JSON document |
| POST | `/courses/import` | teacher, admin | A new draft course from such a document |
| GET | `/search` | public | Full-text search of published courses and lessons |
| GET, POST, PATCH, DELETE | `/learning-paths`, `/learning-paths/{slug}` | public / admin | Ordered course sequences per grade |
| POST, GET, PATCH | `/media`, `/media/{id}`, `/media/{id}/complete` | teacher, admin | Upload files straight to storage with a signed form, then confirm |
| GET, POST, PUT | `/standards`, `/standards/frameworks`, `/standards/frameworks/{code}/standards` | public / admin | Curriculum standards (Common Core, NGSS, …): search, add, import |
| POST, GET | `/classes`, `/classes/join` | teacher, student | Create a class / my classes / join with a code |
| GET, PATCH, DELETE | `/classes/{id}`, `/classes/{id}/join-code` | teacher of the class | Read, change, archive a class; replace its code |
| GET, POST, DELETE, PUT | `/classes/{id}/students`, `…/{student_id}`, `…/{student_id}/sign-in` | teacher of the class | Roster, create students, remove, set up their sign-in |
| GET, PATCH | `/admin/users`, `/admin/users/{id}` | admin | Search users, change role, (de)activate |
| DELETE | `/admin/users/{id}/mfa` | admin | Turn off a user's two-factor (lost phone) |
| PUT | `/admin/users/{id}/teacher-verification` | admin | Verify a teacher |
| POST | `/admin/users/{id}/impersonate` | admin | Act as a user for support (15 minutes, audited) |
| GET | `/admin/audit-events` | admin | Search the audit log |

Every error uses one envelope: `{"error": {"code", "message", "details", "request_id"}}`. Every response carries an `X-Request-ID` that also appears in the logs. The `code` values are stable and machine-readable. All of them, with when they happen and how clients should react, are listed in [`docs/errors.md`](docs/errors.md).

Building courses (drafts and versions, the lesson block types, uploads, publishing checks, export/import, search) is described in [**docs/content.md**](docs/content.md).

## Running the tests

The tests run against a **real PostgreSQL database** (not SQLite). Before the first test, the test database is wiped and rebuilt with the real migrations, so the tests also cover the constraints, triggers and indexes.

### The quick way (Docker)

```bash
make install      # once: creates .venv with the test tools
make test         # starts the test database in Docker (port 5433), then runs every test
```

`make test` starts a separate, throwaway PostgreSQL (`test-db` in `docker-compose.yml`, data kept in memory), so your development database is never touched. Stop it with `make down`.

### Run only some tests

Pass any pytest options through `ARGS`:

```bash
make test ARGS="tests/test_auth.py"                      # one file
make test ARGS="tests/test_auth.py -k refresh"           # tests whose name contains "refresh"
make test ARGS="tests/test_courses.py::test_draft_visible_only_to_author_and_admin"   # one test
make test ARGS="-x -v"                                   # stop at the first failure, verbose
make test ARGS="--lf"                                    # only the tests that failed last time
```

### With your own PostgreSQL (no Docker)

Create an empty database whose name contains `test`, owned by the user in the URL, then point `LUMINA_TEST_DATABASE_URL` at it:

```bash
createdb -O lumina lumina_test        # or in psql: CREATE DATABASE lumina_test OWNER lumina;
LUMINA_TEST_DATABASE_URL=postgresql+asyncpg://lumina:lumina@localhost:5432/lumina_test make test
```

The name must contain `test` because **every run drops and rebuilds the database's `public` schema**; the tests refuse to run against any other database. Use PostgreSQL 15 or newer (the project targets 17); the database owner needs no superuser rights. On PostgreSQL 13 or 14, also run `ALTER SCHEMA public OWNER TO lumina;` in that database once, because there the `public` schema belongs to the superuser.

### Without make

```bash
source .venv/bin/activate                                    # Windows: .venv\Scripts\activate
docker compose up -d --wait test-db                          # or use your own PostgreSQL (above)
pytest                                                       # everything
pytest tests/test_auth.py -k refresh -v                      # a subset
```

If you use your own database, set the variable first: `export LUMINA_TEST_DATABASE_URL=postgresql+asyncpg://...` (Windows PowerShell: `$env:LUMINA_TEST_DATABASE_URL="postgresql+asyncpg://..."`).

### What the tests cover

| File | Covers |
| --- | --- |
| `test_auth.py` | Sign-up, email verification, sign-in, throttling and lockout, access-token expiry and tampering, refresh rotation and reuse detection, logout, password reset |
| `test_child_sign_in.py` | Age bands, usernames and passwords for children, who may set them up, methods each age may use |
| `test_classes.py` | Teachers see only their own classes, roster, join codes, picture sign-in with its lockouts |
| `test_oauth.py` | Google, Microsoft, Apple against a fake provider: sign-up, linking, forged tokens, the "nOAuth" protection |
| `test_mfa.py` | Two-factor set-up, sign-in, replay protection, recovery codes, admin reset |
| `test_me.py` | Own profile, password change, email change, sessions, account deletion, teacher profile |
| `test_family.py` | Adding and linking children, child account activation, active child, parent-only visibility |
| `test_admin.py` | User administration, teacher verification, impersonation, audit log, role/permission helpers |
| `test_courses.py`, `test_subjects.py` | Catalog endpoints with every role: success, validation errors, not found, unauthenticated, forbidden |
| `test_structure.py` | Units and lessons: adding, reordering, moving between units, positions without gaps, who may edit |
| `test_content.py` | Every block type, validation (unsafe links, unknown embed sites, someone else's media), revision conflicts, lesson standards and prerequisites |
| `test_publishing.py` | Drafts stay private, publishing checks (captions, alt text, empty lessons), immutable versions, reading older versions, unpublishing |
| `test_transfer.py` | Duplicating, exporting and importing courses |
| `test_media.py` | Signed uploads, size and type checks on completion, the virus-scan hook, owners only |
| `test_search.py` | Full-text search: only published content, stemming per language, ranking, filters |
| `test_standards.py`, `test_catalog_extras.py` | Standards import and search; tags, difficulty, course prerequisites, learning paths, recommendations |
| `test_health.py` | Liveness and readiness probes, request ids |
| `test_database.py`, `test_timestamps.py`, `test_migrations.py` | PostgreSQL behaviour: constraints, slug races, trigram index, `updated_at` triggers, migrations up/down with no drift |
| `test_security.py`, `test_config.py`, `test_errors.py`, `test_text.py` | Hashing, tokens, password policy, settings validation, error codes in sync with `docs/errors.md` |
| `test_scripts.py`, `test_requirements.py`, `test_ci_config.py` | The seed and create-admin commands, pinned requirements in sync, CI merge gate in sync |

Email is never really sent in tests: it is recorded in memory, and tests follow the links in it like a user would. Uploads go to an in-memory fake storage, so the tests need neither MinIO nor S3 (the smoke test below uploads a real file).

### Lint and type-check

```bash
make lint     # ruff check, ruff format --check, mypy --strict
```

CI runs exactly `make lint` and `make test` (see below), so a green run locally means the same checks pass on GitHub.

### When the tests do not start

- **`Cannot reach the test database`**: run `make test-db` (is Docker running?), or check `LUMINA_TEST_DATABASE_URL`.
- **`Refusing to run: database ... does not look like a test database`**: the database name must contain `test`.
- **`port is already allocated` on 5433**: another program uses the port. Stop it, or use your own PostgreSQL as above.
- **`ModuleNotFoundError`**: activate the virtual environment, or run `make install` again after pulling changes.

## Database migrations

Migrations are a separate step and are never run by the API process. In Docker Compose the `migrate` service does it before `api` starts. Locally, run `make migrate`. To re-run only the migrations in Docker (after the image has been built once by `docker compose up --build`): `docker compose run --rm migrate`.

Creating a migration:

```bash
.venv/bin/alembic revision --autogenerate -m "describe the change"   # then review the file by hand
make migrate
.venv/bin/alembic check              # must print "No new upgrade operations detected."
.venv/bin/alembic downgrade -1 && make migrate   # must downgrade cleanly
```

Tables that use `TimestampMixin` must also attach the `set_updated_at()` trigger in their migration (see `migrations/versions/20261111_0002_add_updated_at_triggers.py`). A test fails if one is missing.

## Continuous integration

GitHub Actions runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on every pull request, on every push to `main`, and on demand:

| Job | What it checks |
| --- | --- |
| Lint and type-check | `make install` + `make lint` (ruff, ruff format, mypy --strict). Problems show as annotations on the diff. |
| Tests | `make test` on a PostgreSQL 17 service, on Python 3.11 (minimum) and 3.13 (Docker image). |
| Docker image and Compose stack | `docker compose up --build`, checks the `migrate` job succeeded, seeds, creates an admin, and runs [`scripts/smoke-test.sh`](scripts/smoke-test.sh) (public reads, sign-in, a write, a real file upload to MinIO, sign-out) against the running stack. |
| **CI passed** | Succeeds only if every job above succeeded. This is the single required check. |

pip downloads are cached between runs.

### Blocking merges when CI fails

The workflow alone only reports results. To make GitHub **refuse to merge** a pull request whose checks fail, a repository admin applies the ruleset in [`.github/rulesets/protect-main.json`](.github/rulesets/protect-main.json) once:

```bash
gh auth login              # GitHub CLI, as a repository admin
make protect-branch        # creates the ruleset, or updates it if it already exists
```

The ruleset applies to the default branch:

- Changes go through a pull request.
- The **CI passed** check must succeed, and the branch must be up to date with `main` before merging.
- Force-pushes and deleting the branch are blocked.
- Nobody can bypass it, admins included.

Without the CLI: **Settings → Rules → Rulesets → New branch ruleset**, target the default branch, enable *Require a pull request before merging* and *Require status checks to pass* (add `CI passed`, select *GitHub Actions* as the source, and tick *Require branches to be up to date*), and block force pushes.

Notes:

- Rulesets are available on public repositories, and on private repositories with GitHub Pro, Team or Enterprise.
- Add new CI jobs to the `needs:` list of the `ci-passed` job. `tests/test_ci_config.py` fails if a job is missing there, or if the check name and the ruleset drift apart.
- Required approvals are set to 0. Raise `required_approving_review_count` in the ruleset and re-run `make protect-branch` once there is a second reviewer.

To smoke-test a running stack yourself: `docker compose up --build -d`, then `scripts/smoke-test.sh` (set `LUMINA_SMOKE_EMAIL` and `LUMINA_SMOKE_PASSWORD` of a platform admin to include the signed-in checks).

## Architecture: modular monolith with layers

```
.
├── app/
│   ├── main.py              application factory (create_app)
│   ├── core/                config, logging, errors, middleware, pagination, email
│   ├── db/                  declarative base, mixins, engine/session, transactions
│   ├── api/                 shared HTTP concerns: deps, error handlers, health, v1 router
│   ├── modules/
│   │   ├── catalog/         subjects, courses, course prerequisites, learning paths
│   │   ├── curriculum/      units, lessons and their blocks, publishing and versions, export/import, search
│   │   ├── media/           uploads to S3-compatible storage (signed forms, checks, scan hook)
│   │   ├── standards/       curriculum standards frameworks and their standards
│   │   ├── classes/         classes, rosters, join codes, class-code picture sign-in
│   │   ├── identity/        users, roles, sign-in, sessions, parents and children, admin
│   │   │   ├── domain.py        roles, permissions, rules; no framework imports
│   │   │   ├── models.py        SQLAlchemy tables
│   │   │   ├── schemas.py       Pydantic request/response models
│   │   │   ├── repository.py    queries only
│   │   │   ├── security.py      password hashing, tokens
│   │   │   ├── oauth.py         Google / Microsoft / Apple (OpenID Connect)
│   │   │   ├── totp.py          two-factor codes, encryption of their secrets
│   │   │   ├── policies.py      ownership and safety rules, in one place
│   │   │   ├── service.py       use cases and the transaction boundary
│   │   │   ├── deps.py          wiring, current user, require_role / require_permission
│   │   │   └── router.py        HTTP endpoints
│   │   └── audit/           append-only audit log
│   └── scripts/             seed.py (demo data), create_admin.py, reindex_search.py
├── migrations/              Alembic
├── tests/                   pytest, against PostgreSQL
├── docs/                    auth.md (accounts, permissions), content.md (courses, lessons, media), errors.md
├── scripts/                 bootstrap, smoke test, branch protection, requirements
├── requirements*.txt        pinned dependencies (generated from pyproject.toml)
└── .github/                 CI workflow and the branch ruleset
```

Request flow: `router → service → repository → database`. Routers never touch the session, repositories never commit, and services never import FastAPI. Authorization rules that depend on ownership live in each module's `policies.py`. New areas (enrolments, progress, assessments) are new folders under `modules/` with the same shape, so they can be split into services later if needed.

## Decisions worth knowing

- **Async everywhere**: `asyncpg` with `AsyncSession`. Relationships use `lazy="raise"`, so an accidental lazy load fails loudly instead of causing hidden queries. Queries load what they need explicitly.
- **Transactions**: services commit once per use case, together with the audit entries describing it. A unique-constraint race becomes a 409 instead of a 500 (the translation covers the flush as well as the commit). Emails are sent only after the commit.
- **Every sign-in method ends in one place** (`_finish_sign_in`): age-band check, two-factor challenge, session length, audit. A new method cannot skip them.
- **Sessions are checked on every request**: access tokens are short JWTs, but each request also confirms the session is open and the account active (one query). Sign-out, revocation and deactivation are therefore immediate, and the role always comes from the database.
- **Users are anonymised, never deleted**, so audit entries and authored content keep valid references.
- **`updated_at` is set by a database trigger**, so bulk and raw SQL updates move it too.
- **Drafts and versions are separate**: authors edit units and lessons in place; publishing copies them into an immutable JSON snapshot (a trigger refuses updates and deletes), and learners read only snapshots. Half-finished edits can never leak, and an older version stays readable.
- **Lesson content is JSON validated by Pydantic**, a list of typed blocks with a `schema_version`, so new block types need no migration. Saving sends the `revision` it started from; a stale one gets `409 edit_conflict` instead of silently overwriting a colleague's work.
- **Uploads never pass through the API**: it signs a form limited to the declared type and size, the browser posts the file to the storage, and the API checks what arrived before the file can be used.
- **Search** (`/search`) is PostgreSQL full-text search over published versions, stemmed in each course's language; the catalog's title filter (`q` on `/courses`) uses `ILIKE '%term%'`, backed by a `pg_trgm` GIN index.
- **Migrations are hand-reviewed**: constraints follow a naming convention, and `alembic check` (also run by the tests) confirms the models and migrations agree.
- **Probes**: liveness and readiness are separate, so a database outage does not cause restart loops.

## Troubleshooting

- **`port is already allocated` on 5432, 1025, 8025, 9000 or 9001**: another program (a local PostgreSQL, another mail catcher, another MinIO) uses the port. Stop it, or change the host port in `docker-compose.yml` (for the database, also in `LUMINA_DATABASE_URL`).
- **`error parsing value for field "cors_origins"`**: `LUMINA_CORS_ORIGINS` is not a JSON array. See [the format](#lumina_cors_origins-format).
- **`LUMINA_SECRET_KEY must be ...` at startup**: set a random key of 32+ characters (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`).
- **Sign-in says `email_not_verified`**: open the verification email (locally at http://localhost:8025) or call `POST /auth/verify-email/resend`.
- **No emails arrive locally**: start Mailpit (`make mail`); the API logs `email delivery failed` when it cannot reach the SMTP server.
- **`429 rate_limited` on sign-in**: too many wrong passwords; wait for the `Retry-After` seconds, or reset the password.
- **The browser cannot reach the upload link, or `POST /media/{id}/complete` returns `503 service_unavailable`**: the storage is not running; start MinIO (`make storage`). Links must point at an address the browser can reach: set `LUMINA_STORAGE_PUBLIC_ENDPOINT_URL` when the API reaches the storage under another name (as inside Docker).
- **Uploads from the web app are blocked by CORS**: the storage must allow the web app's origin. MinIO allows every origin by default; on AWS S3 or R2, add a CORS rule for `POST` from your frontend.
- **Search finds nothing for a course**: only published courses are indexed. After restoring a database, run `make reindex`.
- **Lost the admin password**: create another admin with `make admin EMAIL=...`, or use "forgot password" and read the email in Mailpit.

## Roadmap

The task-by-task plan (priorities, sizes, acceptance criteria) is in [`TODO.md`](TODO.md).
