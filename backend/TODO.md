# Lumina Backend — TODO

Task list for the API (`backend/`). Written so a human or an AI agent can pick up one task at a time and finish it without extra context.

- **Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Alembic, PostgreSQL
- **Architecture:** modular monolith. `router → service → repository → database`. See `../README.md`.
- **Frontend counterpart:** `../frontend/TODO.md`

---

## How to use this file

**Task format**

```
- [ ] BE-EXAMPLE-00 · P0 · M — Title. Detail.
      Done when: observable acceptance criteria.
```

| Tag | Meaning |
| --- | --- |
| `P0` | Required for launch. Do first. |
| `P1` | Important, can follow launch. |
| `P2` | Nice to have / later. |
| `S` / `M` / `L` / `XL` | About half a day / 1–2 days / 3–5 days / more. Split any `L` or `XL` into smaller tasks before starting. |

**Rules for whoever picks up a task** (human or AI)

1. Take the first unchecked task in the lowest-numbered phase whose dependencies are done. One task per pull request.
2. Follow the existing module layout (`app/modules/<name>/` with `domain`, `models`, `schemas`, `repository`, `service`, `deps`, `router`). Routers never touch the session; repositories never commit; services own the transaction and never import FastAPI.
3. Every schema change ships with an Alembic migration. `alembic check` must report no drift, and the migration must downgrade cleanly.
4. Tick the checkbox in the same pull request that completes the task.
5. If a task turns out to be bigger than its size tag, split it and add the pieces here.

**Definition of done (applies to every task)**

- [ ] `make lint` passes (`ruff`, `ruff format --check`, `mypy --strict`)
- [ ] Tests added: service logic plus API tests for success, validation error, not-found, and forbidden cases
- [ ] Authorization is enforced and tested (once Phase 2 exists)
- [ ] New endpoints appear in OpenAPI with summaries, and error responses use the standard envelope
- [ ] No secrets, tokens, or personal data in logs
- [ ] Docs updated (`README.md`, or `docs/` for decisions)

---

## Current state (already built)

- [x] FastAPI app factory, typed settings (`LUMINA_*` env vars), CORS
- [x] Request ID middleware, structured logging, one error envelope
- [x] Async SQLAlchemy, UUID + timestamp mixins, constraint naming convention
- [x] Alembic wired to app settings, first migration (`subjects`, `courses`)
- [x] Catalog module: subjects and courses, search (grade, subject, title), pagination
- [x] Health probes: `/health/live`, `/health/ready`
- [x] Seed script (idempotent), Dockerfile, Docker Compose, Makefile
- [x] 18 tests (SQLite in memory), ruff and mypy clean

---

## Phase 0 — Gaps in the initial scaffold

Fix these before building on top. Most are small.

- [ ] BE-FIX-01 · P0 · S — Verify the Docker image builds and `docker compose up` works end to end against real PostgreSQL. The scaffold was tested on SQLite only.
      Done when: `docker compose up --build` starts all three services, migrations apply, and `/health/ready` reports `database: up`.
- [ ] BE-FIX-02 · P0 · M — Run the test suite against PostgreSQL (Testcontainers or a Compose service) instead of SQLite. Keep SQLite only if a test needs no dialect features; otherwise drop it.
      Done when: `make test` runs on PostgreSQL, and a test exists for the `grade_level` check constraint and the unique-slug race.
- [ ] BE-FIX-03 · P0 · S — Stop exposing drafts publicly. `GET /courses?status=draft` currently works for anyone, and `POST /courses` and `POST /subjects` have no authentication.
      Done when: the public list returns published courses only; write endpoints are blocked until Phase 2 lands (temporary admin API key via `LUMINA_ADMIN_API_KEY` is acceptable).
- [ ] BE-FIX-04 · P1 · S — Add `PATCH /courses/{id}` and `DELETE`/archive for courses and subjects (currently create and read only).
- [ ] BE-FIX-05 · P1 · S — Make the `updated_at` column update for bulk or raw SQL updates too (database trigger), not only through the ORM.
- [ ] BE-FIX-06 · P1 · S — Add trigram index (`pg_trgm`) for the title search, which currently does an unindexed `ILIKE '%…%'`.
- [ ] BE-FIX-07 · P1 · S — Stop running `alembic upgrade head` inside the API container command. Run migrations as a separate one-off job (Compose service and, later, a deploy step).
- [ ] BE-FIX-08 · P2 · S — Document the `LUMINA_CORS_ORIGINS` JSON format in `.env.example` with a second example, and fail startup if `environment=production` and CORS is `*` or empty.

---

## Phase 1 — Engineering foundation

- [ ] BE-OPS-01 · P0 · M — CI pipeline (GitHub Actions): install, `ruff`, `ruff format --check`, `mypy`, tests on PostgreSQL service, build Docker image. Cache pip.
      Done when: a pull request cannot merge with a failing check.
- [ ] BE-OPS-02 · P0 · S — Pre-commit hooks (`ruff`, `ruff format`, trailing whitespace, secret scan such as `gitleaks`).
- [ ] BE-OPS-03 · P0 · S — Pin dependencies with a lock file (`uv` or `pip-tools`) and add Dependabot or Renovate.
- [ ] BE-OPS-04 · P1 · S — Test coverage report with a floor (start at 85%) enforced in CI.
- [ ] BE-OPS-05 · P1 · S — Split Docker image into build and runtime stages; run as non-root (already done), add a `HEALTHCHECK`, and scan with Trivy in CI.
- [ ] BE-CORE-01 · P0 · M — Settings validation per environment: production requires a non-default `database_url`, a secret key, and explicit CORS origins. Fail fast with a clear message.
- [ ] BE-CORE-02 · P0 · M — Shared `SoftDeleteMixin` (`deleted_at`) and repository helpers that exclude deleted rows by default.
- [ ] BE-CORE-03 · P1 · M — Cursor (keyset) pagination helper for large or fast-changing lists, alongside the existing offset pagination.
- [ ] BE-CORE-04 · P1 · M — Rate limiting middleware (Redis-backed) with per-route limits; stricter on auth endpoints. Returns `429` in the standard envelope with `Retry-After`.
- [ ] BE-CORE-05 · P1 · S — Request body size limit and security response headers (`X-Content-Type-Options`, `Strict-Transport-Security`, etc.).
- [ ] BE-CORE-06 · P1 · M — Idempotency keys for create endpoints that clients may retry (`Idempotency-Key` header, stored for 24 hours).
- [ ] BE-CORE-07 · P1 · S — ETag / `If-None-Match` support on read endpoints that are cacheable (courses, subjects).
- [ ] BE-CORE-08 · P2 · S — Stable machine-readable error codes documented in one place (`docs/errors.md`) so clients can rely on them.
- [ ] BE-DX-01 · P1 · S — `docs/` folder with architecture decision records (ADR): why modular monolith, why async, why offset then cursor pagination.
- [ ] BE-DX-02 · P1 · M — Test factories (`factory_boy` or simple builders) and shared fixtures so new modules do not copy helper code.
- [ ] BE-DX-03 · P2 · S — Dev container or `make bootstrap` that sets up everything in one command.

---

## Phase 2 — Identity, authentication, authorization

This is the biggest dependency: almost every later feature needs it. Children use the platform, so account rules matter (see Phase 13).

**Data model**

- [ ] BE-AUTH-01 · P0 · M — `users` table: id, email (unique, case-insensitive, nullable for child accounts), password hash, display name, role, locale, timezone, `is_active`, `email_verified_at`, timestamps.
- [ ] BE-AUTH-02 · P0 · M — Roles: `student`, `parent`, `teacher`, `school_admin`, `platform_admin`. Store as enum; permissions derived in code, not in the database, until a real need appears.
- [ ] BE-AUTH-03 · P0 · M — `student_profiles` (grade level, date of birth, school) and `parent_child_links` (parent ↔ student, with status). A student may have no email (parent-managed) or a school-issued username.
- [ ] BE-AUTH-04 · P1 · S — `teacher_profiles` (subjects, verified flag, bio).

**Credentials and sessions**

- [ ] BE-AUTH-05 · P0 · M — Password hashing with Argon2id. Password policy (length over complexity), breached-password check optional.
- [ ] BE-AUTH-06 · P0 · L — Register, login, logout, refresh. Short-lived access token (JWT, 15 min) plus rotating refresh token stored hashed in `sessions`; reuse of an old refresh token revokes the whole family.
      Done when: tests cover expiry, rotation, reuse detection, and logout invalidation.
- [ ] BE-AUTH-07 · P0 · M — Email verification and password reset (single-use, expiring tokens; no user enumeration in responses).
- [ ] BE-AUTH-08 · P0 · S — Login throttling and lockout backoff per account and per IP.
- [ ] BE-AUTH-09 · P1 · M — Session list and revoke ("log out other devices").
- [ ] BE-AUTH-10 · P1 · M — Child sign-in methods: username + password, or a class join code / picture password for Grades 1–3 (no email required).
- [ ] BE-AUTH-11 · P1 · L — Social login (Google, Microsoft, Apple). School SSO (Google Workspace, Microsoft Entra, Clever, ClassLink) tracked separately in Phase 6.
- [ ] BE-AUTH-12 · P2 · M — Optional TOTP two-factor for teachers and admins.

**Authorization**

- [ ] BE-AUTHZ-01 · P0 · M — `current_user` dependency and `require_role(...)` / `require_permission(...)` helpers. Unauthenticated → `401`, forbidden → `403`, both in the standard envelope.
- [ ] BE-AUTHZ-02 · P0 · M — Apply to existing endpoints: course and subject writes need `teacher` (own content) or `platform_admin`; drafts visible only to their author and admins. Remove the temporary key from BE-FIX-03.
- [ ] BE-AUTHZ-03 · P0 · M — Ownership checks as service-layer policies (a parent sees only linked children; a teacher sees only own classes). Keep them in one `policies.py` per module so they are easy to audit.
- [ ] BE-AUTHZ-04 · P1 · M — Audit log table and service (who did what, to which entity, when, from which IP). Log logins, role changes, deletes, grade changes.
- [ ] BE-USERS-01 · P0 · S — `GET/PATCH /me`, change password, change email (re-verify), delete account (see Phase 13).
- [ ] BE-USERS-02 · P1 · M — Parent endpoints: add child, link existing child with a code, list children, switch active child.
- [ ] BE-USERS-03 · P1 · M — Admin endpoints: list/search users, deactivate, change role, impersonate (audited, time-limited).

---

## Phase 3 — Curriculum and content

Turn the catalog from a list of courses into a real curriculum.

- [ ] BE-CAT-01 · P0 · M — Course structure: `course → unit → lesson`, with ordering (`position`) and move/reorder endpoints. Published state per course, with `draft` and `published` content kept separate.
- [ ] BE-CAT-02 · P0 · L — Lesson content model: ordered blocks (text/markdown, video, image, file, embed, interactive, quiz reference). Store as validated JSON with a schema version.
- [ ] BE-CAT-03 · P0 · M — Authoring endpoints for teachers: create, edit, reorder, publish, unpublish, duplicate course.
- [ ] BE-CAT-04 · P1 · M — Content versioning: publishing creates an immutable version; learners keep seeing the version they started until they choose to update.
- [ ] BE-CAT-05 · P1 · M — Curriculum standards: `standards` (framework, code, description, grade) and links from lessons and questions to standards (Common Core, NGSS, national equivalents). Make the framework configurable per country.
- [ ] BE-CAT-06 · P1 · M — Tags, difficulty, estimated duration, prerequisites between courses and lessons.
- [ ] BE-CAT-07 · P1 · M — Full-text search across courses and lessons using PostgreSQL (`tsvector` + GIN), with ranking and language config. Plan the move to a search service only if this proves insufficient.
- [ ] BE-CAT-08 · P1 · M — Learning paths (ordered course sets per grade and subject) and "recommended for grade" endpoint.
- [ ] BE-CAT-09 · P2 · M — Localization of content (translation tables per entity, fallback language).
- [ ] BE-CAT-10 · P2 · M — Import/export courses (JSON or a common format such as IMS Common Cartridge).

**Media**

- [ ] BE-MEDIA-01 · P0 · M — Object storage abstraction (S3-compatible; MinIO locally). Pre-signed upload URLs, size and type limits, virus scan hook.
- [ ] BE-MEDIA-02 · P0 · M — `media_assets` table (owner, kind, size, checksum, status, alt text required for images).
- [ ] BE-MEDIA-03 · P1 · L — Video pipeline: upload → transcode to HLS → thumbnails → captions (use a managed service such as Mux or Cloudflare Stream rather than building one). Signed playback URLs.
- [ ] BE-MEDIA-04 · P1 · M — Image variants and CDN URLs; captions/transcripts required for video before publish (accessibility).

---

## Phase 4 — Learning experience

- [ ] BE-LRN-01 · P0 · M — Enrolment: `enrollments` (student, course, status, enrolled_at). Enrol, unenrol, list "my courses".
- [ ] BE-LRN-02 · P0 · L — Progress tracking: per-lesson state (not started / in progress / completed), time spent, last position (for video resume), course completion percentage. Idempotent updates; safe against duplicate and out-of-order requests from flaky connections.
- [ ] BE-LRN-03 · P0 · M — "Continue learning" endpoint: next unfinished lesson per enrolled course.
- [ ] BE-LRN-04 · P1 · M — Bookmarks and student notes on lessons.
- [ ] BE-LRN-05 · P1 · M — Gamification: XP, streaks (timezone-aware), badges, levels. Configurable per grade band; keep it optional for older students. Store as an append-only event ledger so totals can be recomputed.
- [ ] BE-LRN-06 · P1 · M — Certificates of completion (PDF generation, verification URL).
- [ ] BE-LRN-07 · P2 · L — Adaptive learning: mastery per standard, next-best-lesson recommendation.
- [ ] BE-LRN-08 · P2 · M — Spaced repetition and review queue.

---

## Phase 5 — Assessments

- [ ] BE-ASM-01 · P0 · L — Question bank: types (multiple choice, multi-select, true/false, short answer, numeric, fill-in-the-blank, matching, ordering, open response). Validated JSON per type, linked to standards and difficulty.
- [ ] BE-ASM-02 · P0 · M — Quizzes and tests: compose from fixed questions or random pools; settings for time limit, attempts, shuffling, show-answers policy, passing score.
- [ ] BE-ASM-03 · P0 · L — Attempts: start, autosave answers, submit, server-side timer (never trust the client clock), resume after disconnect.
- [ ] BE-ASM-04 · P0 · M — Auto-grading for objective types (including numeric tolerance and accepted-answer lists). Return per-question feedback.
- [ ] BE-ASM-05 · P1 · M — Manual grading queue for open responses with rubrics; teacher feedback and regrade.
- [ ] BE-ASM-06 · P1 · M — Do not leak answers: correct answers and explanations are only returned after the policy allows it. Add tests that assert this.
- [ ] BE-ASM-07 · P1 · M — Integrity basics: attempt locking, one active attempt, event log of focus loss (informational only).
- [ ] BE-ASM-08 · P2 · M — Practice mode with unlimited attempts and instant feedback; item analytics (difficulty, discrimination).

---

## Phase 6 — Schools and classrooms

- [ ] BE-SCH-01 · P0 · M — Organizations: `schools` (and optionally districts), membership with roles. Introduce `organization_id` scoping in queries; decide row-level security vs application-level scoping in an ADR first.
- [ ] BE-SCH-02 · P0 · M — Classes: teacher(s), students, grade, subject, school year, join code.
- [ ] BE-SCH-03 · P0 · L — Assignments: assign course/lesson/quiz to a class or students, with due dates, late policy, status per student.
- [ ] BE-SCH-04 · P0 · M — Gradebook: scores per student per assignment, weighting, term grades, export to CSV.
- [ ] BE-SCH-05 · P1 · M — Roster import (CSV) with validation report; bulk create child accounts with generated credentials.
- [ ] BE-SCH-06 · P1 · L — School SSO and roster sync (Google Classroom, Clever, ClassLink, OneRoster).
- [ ] BE-SCH-07 · P1 · M — Parent-visible reports: progress, grades, time on task, per child. Report card PDF.
- [ ] BE-SCH-08 · P2 · M — Attendance and learning goals per class.

---

## Phase 7 — Communication

- [ ] BE-NOTIF-01 · P0 · M — Notification model and preferences (channel × event type). In-app notifications with read state.
- [ ] BE-NOTIF-02 · P0 · M — Transactional email via a provider (Postmark, SES, or Resend) with templates, bounce handling, unsubscribe where required.
- [ ] BE-NOTIF-03 · P1 · M — Announcements per class or school.
- [ ] BE-NOTIF-04 · P1 · L — Messaging between teachers and parents. Students under 13 get no direct messaging with other students. Include reporting, blocking, and moderation hooks.
- [ ] BE-NOTIF-05 · P1 · M — Discussion threads on lessons and classes, with moderation tools and profanity filtering.
- [ ] BE-NOTIF-06 · P2 · M — Push notification tokens and delivery (needed for the mobile app).
- [ ] BE-NOTIF-07 · P2 · M — Realtime channel (WebSocket or SSE) for live notifications.

---

## Phase 8 — Live classes (optional for first launch)

- [ ] BE-LIVE-01 · P2 · M — Scheduling: sessions, recurrence, capacity, time zones, reminders.
- [ ] BE-LIVE-02 · P2 · L — Video provider integration (Zoom, Daily, LiveKit, or similar): create room, signed join token per user, attendance capture.
- [ ] BE-LIVE-03 · P2 · M — Recordings linked to lessons after processing, with captions.

---

## Phase 9 — Billing and subscriptions

- [ ] BE-BILL-01 · P1 · M — Plans, prices, and entitlements model (what a plan unlocks). Check entitlements in the service layer through one `entitlements` helper.
- [ ] BE-BILL-02 · P1 · L — Stripe integration: checkout, customer portal, webhooks (verified signatures, idempotent handling, replay safe).
- [ ] BE-BILL-03 · P1 · M — Family plans (parent pays for several children); free tier rules.
- [ ] BE-BILL-04 · P1 · M — School and district licensing (seat counts, invoices, quotes).
- [ ] BE-BILL-05 · P2 · M — Promo codes, trials, refunds, tax handling (use Stripe Tax), invoices in PDF.

---

## Phase 10 — Background jobs and infrastructure

- [ ] BE-INFRA-01 · P0 · M — Redis in Compose and settings. Use it for rate limits, cache, and the job queue.
- [ ] BE-INFRA-02 · P0 · L — Background worker (`arq`, Celery, or Dramatiq): retries with backoff, dead-letter handling, scheduled jobs, a job status table where users need feedback.
- [ ] BE-INFRA-03 · P0 · M — Transactional outbox so events (email, notifications, analytics) are only sent after the database commit succeeds.
- [ ] BE-INFRA-04 · P1 · M — Caching layer for hot reads (course catalog, standards) with explicit invalidation on publish.
- [ ] BE-INFRA-05 · P1 · M — Feature flags (database or a service such as Unleash/GrowthBook).
- [ ] BE-INFRA-06 · P1 · M — Domain events inside the monolith (`CoursePublished`, `LessonCompleted`, `AttemptSubmitted`) so modules stay decoupled (progress → gamification → notifications).
- [ ] BE-INFRA-07 · P2 · M — Read replicas and connection pooling (PgBouncer) configuration for production.

---

## Phase 11 — Admin, analytics, reporting

- [ ] BE-ADM-01 · P1 · M — Admin API for content moderation (review queue, approve/reject courses, takedown).
- [ ] BE-ADM-02 · P1 · M — Product analytics events (server-side event stream to a warehouse or PostHog). No personal data about children in third-party tools without a legal basis (see Phase 13).
- [ ] BE-ADM-03 · P1 · M — Teacher analytics: class progress, struggling students, question-level results.
- [ ] BE-ADM-04 · P2 · M — Scheduled data exports and a reporting schema (materialized views) for school administrators.
- [ ] BE-ADM-05 · P2 · M — Public API with API keys and scopes for school integrations.

---

## Phase 12 — Observability and operations

- [ ] BE-OBS-01 · P0 · M — Error tracking (Sentry) with request ID, user ID (hashed), and release tags. Scrub personal data.
- [ ] BE-OBS-02 · P0 · M — Metrics (Prometheus or OpenTelemetry): request rate, latency percentiles, error rate, DB pool usage, queue depth.
- [ ] BE-OBS-03 · P1 · M — Distributed tracing (OpenTelemetry) across API, worker, and database.
- [ ] BE-OBS-04 · P1 · M — Dashboards and alerts (SLO: for example 99.9% availability, p95 under 300 ms on read endpoints). Runbooks for each alert.
- [ ] BE-OPS-10 · P0 · L — Production deployment (choose one: managed containers, Kubernetes, or a PaaS). Infrastructure as code (Terraform). Separate staging and production.
- [ ] BE-OPS-11 · P0 · M — Database: managed PostgreSQL, automated backups, point-in-time recovery, and a tested restore procedure.
- [ ] BE-OPS-12 · P1 · M — Zero-downtime migrations policy (expand → migrate → contract) and a migration lint in CI.
- [ ] BE-OPS-13 · P1 · M — Load tests (k6 or Locust) for catalog reads, login, and quiz submission. Establish baselines, find the first bottleneck.
- [ ] BE-OPS-14 · P1 · S — Secrets management (no secrets in the repository or images). Rotation procedure.
- [ ] BE-OPS-15 · P2 · M — Disaster recovery plan, and a quarterly restore drill.

---

## Phase 13 — Security, privacy, compliance

Children's data is regulated. Get legal advice for each market before launching there.

- [ ] BE-SEC-01 · P0 · M — Threat model (STRIDE) for auth, uploads, messaging, and payments. Store in `docs/security/`.
- [ ] BE-SEC-02 · P0 · M — Review against OWASP API Top 10. Add automated tests for broken object-level authorization on every endpoint that takes an id.
- [ ] BE-SEC-03 · P0 · M — Dependency and container scanning in CI (`pip-audit`, Trivy), plus static analysis (`bandit` or CodeQL).
- [ ] BE-SEC-04 · P0 · L — Children's privacy: verifiable parental consent flow for users under 13 (COPPA) and under the local digital-consent age elsewhere (GDPR-K), age gate, data minimization for child profiles.
- [ ] BE-SEC-05 · P0 · M — Data subject rights: export all data, delete account, with retention rules; anonymize rather than delete where records must be kept (grades, invoices).
- [ ] BE-SEC-06 · P1 · M — Education records rules (FERPA in the US): access logging, school-as-controller model, data processing agreements.
- [ ] BE-SEC-07 · P1 · M — Encrypt sensitive columns at rest where needed (date of birth, parent contact), and key management.
- [ ] BE-SEC-08 · P1 · M — Content safety: upload scanning, text moderation for discussions, reporting flow, and a trust-and-safety runbook.
- [ ] BE-SEC-09 · P1 · S — Cookie and consent handling for web (coordinate with the frontend).
- [ ] BE-SEC-10 · P2 · M — External penetration test before public launch; track findings here.
- [ ] BE-SEC-11 · P2 · S — Accessibility of generated documents (PDF certificates, reports).

---

## Phase 14 — Mobile readiness (API side)

- [ ] BE-MOB-01 · P1 · M — API versioning and deprecation policy. Minimum app version header and a "force update" response.
- [ ] BE-MOB-02 · P1 · L — Offline-friendly sync: delta endpoints (`updated_since`), batched progress upload, conflict rules (last-write-wins for progress with monotonic counters).
- [ ] BE-MOB-03 · P1 · M — Device registration and push tokens (links to BE-NOTIF-06).
- [ ] BE-MOB-04 · P2 · M — Downloadable lesson packages for offline use, with expiring licenses.

---

## Phase 15 — AI features (later, after the core is stable)

Treat these as separate products with their own safety review.

- [ ] BE-AI-01 · P2 · L — AI tutor: grade-appropriate answers grounded in the course content, strict safety filters, no personal data sent to the model, parent/teacher visibility into transcripts.
- [ ] BE-AI-02 · P2 · L — Teacher tools: generate quiz questions and lesson drafts from a standard, always as drafts needing human review.
- [ ] BE-AI-03 · P2 · M — Automatic feedback on short written answers with rubric alignment, teacher can override.
- [ ] BE-AI-04 · P2 · M — Content recommendations from learning history (start rule-based, then model-based).
- [ ] BE-AI-05 · P2 · M — Evaluation harness and cost/rate controls for every AI feature.

---

## Cross-project contracts (keep in sync with `../frontend/TODO.md`)

- [ ] X-01 · P0 · S — Publish OpenAPI at `/openapi.json` in non-production and export it to `docs/openapi.json` in CI. The frontend generates its types from this file.
- [ ] X-02 · P0 · M — Decide the browser auth transport: httpOnly cookies set by the API (recommended for the web app) vs bearer tokens. Record in an ADR; the mobile app will use bearer tokens.
- [ ] X-03 · P0 · S — Agree on the error envelope codes and validation `details` shape (already implemented; document it).
- [ ] X-04 · P1 · S — CORS and cookie settings per environment (`SameSite`, `Secure`, domains).
- [ ] X-05 · P1 · M — Contract tests (Schemathesis against OpenAPI) in CI.

---

## Suggested order for a first public launch

1. Phase 0 → Phase 1 (foundation)
2. Phase 2 (accounts and permissions)
3. Phase 3 (curriculum and content) → Phase 4 (learning experience)
4. Phase 5 (assessments)
5. Phase 10 (jobs) + Phase 12 (observability, deployment) + Phase 13 (privacy and security)
6. Soft launch with a few schools → then Phases 6, 7, 9 → later Phases 8, 11, 14, 15
