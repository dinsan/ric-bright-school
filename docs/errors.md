# Error codes

Every error response from the Lumina API has the same shape and a stable, machine-readable `code`. This page is the single reference for those codes. Clients (web, mobile, integrations) can rely on everything listed here.

The codes are defined once in code, in `ErrorCode` (`app/core/exceptions.py`). `tests/test_errors.py` fails if this page and the code disagree.

## The error envelope

```json
{
  "error": {
    "code": "not_found",
    "message": "Course not found.",
    "details": [],
    "request_id": "5f0c1a4e9b2d4c7e8a3f6b1d2e9c0a7b"
  }
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `code` | string | Stable machine-readable code from the table below. **Branch on this.** |
| `message` | string | Human-readable English explanation. May change at any time. Do not parse it or match on it. Safe to show to developers, not ideal for end users. |
| `details` | array | Extra structured information. Empty unless stated below. |
| `request_id` | string | Same value as the `X-Request-ID` response header. Include it in bug reports; it finds the request in the server logs. |

If the client sends an `X-Request-ID` header, the API reuses that value; otherwise it generates one.

## Codes

| Code | HTTP status | Meaning | When it happens | What the client should do |
| --- | --- | --- | --- | --- |
| `validation_error` | 422 | The request is malformed: wrong types, missing required fields, out-of-range values, invalid JSON, or unknown fields where they are rejected (`PATCH` bodies). | Any endpoint. Examples: `grade_level: 13`, `limit=0`, `"title": null` in a PATCH, a password shorter than 12 characters, an unknown time zone, a body that is not JSON. Lesson content with an unknown block type, an unknown field, a non-https or javascript link, or more than 200 blocks. | Fix the request. `details` lists every problem per field (see below). Show the messages next to the form fields. |
| `domain_validation_failed` | 422 | The request is well-formed but breaks a business rule. | No slug can be derived from a subject name or course title. A password that is too short for the account's age band (8, 10 or 12 characters), contains your email name, or is too repetitive. A password for a child without a username. A picture password for a child above Grade 3, or an email for a child in Grades 1–3. Changing your email to the address you already have. Verifying a user who is not a teacher. Publishing a course whose draft is not ready (`details` lists every problem: media not uploaded yet, image without description, video without captions or transcript, embed from a site not on the list). Lesson content using media you may not use or of the wrong kind. An upload of a type or size that is not accepted, or an image without alt text. A reorder that does not list every unit or lesson exactly once. Prerequisites that would make a circle. Importing a course whose subject does not exist here. | Show `message` to the user, or change the input. |
| `invalid_token` | 400 | An emailed link or a typed code is unknown, expired, or already used. | `POST /auth/verify-email`, `/auth/password/reset`, `/auth/email/confirm`, `/me/children/link`. Also `POST /auth/login/mfa` when the `mfa_token` expired (5 minutes), was used, or had 5 wrong codes, and `POST /auth/oauth/exchange` when the one-time code expired (2 minutes) or was used. | Tell the user the link or sign-in has expired and let them start again (resend verification, forgot password, new link code, sign in again). Never retry with the same token. |
| `unauthorized` | 401 | You are not signed in, or your access token is invalid or expired, or the session has ended (signed out, revoked, password reset, account deactivated). Also returned by `POST /auth/refresh` when the refresh token is unusable. Comes with `WWW-Authenticate: Bearer`. | Any endpoint that needs a signed-in user. Public reads when an *invalid* token is sent. | Call `POST /auth/refresh` once. If that also fails with `unauthorized`, send the user to the sign-in screen. |
| `invalid_credentials` | 401 | The email/password pair is wrong, or the current password given to confirm a change is wrong. Deliberately the same for unknown emails. | `POST /auth/login`, `/auth/login/username`, `/auth/login/picture` (wrong pictures), `/auth/login/mfa` (wrong code), `POST /me/password`, `POST /me/email`, `DELETE /me`, `DELETE /me/mfa/totp`, 2FA set-up. For accounts without a password (Google/Microsoft/Apple only), also when the last sign-in is older than 10 minutes for actions that need confirmation. | Show "incorrect" (or "sign in again" when the message says so). **Do not** refresh tokens or sign the user out: the session is fine. |
| `forbidden` | 403 | You are signed in, but your account may not do this. | Writes by the wrong role (for example a parent creating a course), editing a course you did not create, publishing as an unverified teacher, listing drafts as a parent, admin endpoints as a non-admin, account-security actions while impersonating, signing in to a deactivated account. A sign-in method the account's age band does not allow (for example email sign-in for a Grade 2 child, or Google for a Grade 6 child). Setting up sign-in for a student who has their own email. Two-factor set-up for a parent or student. Changing an archived class. | Do not retry. Hide or disable the action in the UI. |
| `email_not_verified` | 403 | The password is right, but the email address has not been confirmed yet. | `POST /auth/login`. | Ask the user to open the link in the verification email; offer `POST /auth/verify-email/resend`. |
| `not_found` | 404 | The resource does not exist, **or is not visible to you**. | Unknown ids or slugs. Draft and archived courses for anyone but their author and admins. Children not linked to you, sessions that are not yours, other teachers' classes, students not in the class (deliberately indistinguishable from missing ones). An unknown or disabled class code, an unknown social sign-in provider. An unknown URL. A course's draft or versions for anyone but its author and admins; the content of a course that is not published. Someone else's uploads. Units or lessons of another course. | Show a "not found" state. Do not retry. |
| `method_not_allowed` | 405 | The URL exists but not with this HTTP method. | For example `PUT /api/v1/courses/{id}` (use `PATCH`). | Client bug: use the method listed in the API reference (`/docs`). The `Allow` response header is included but may list only some of the accepted methods. |
| `conflict` | 409 | The request clashes with the current state of the data. | A subject or course with the same slug already exists, including when two requests race for the same slug. Deleting a subject that still has courses. Adding a child with an email that already has an account, or a username that is taken. Confirming an email change after someone else took the address. Starting 2FA set-up when it is already on. Unlinking the only remaining way to sign in. Saving a lesson that someone else changed since you loaded it (send `expected_revision`; reload and retry). Confirming an upload before the file has arrived. A course, subject, framework or learning-path slug/code that is taken. | Change the input, or resolve the blocking state first. Do not retry unchanged. |
| `rate_limited` | 429 | Too many failed sign-in attempts for this account or from this network. Comes with a `Retry-After` header (seconds). | Sign-in after repeated failures: `POST /auth/login` and `/auth/login/username` (5 per account, 50 per IP), `/auth/login/picture` (3 per child; after 10 the child is locked until a teacher or parent sets new pictures), `/auth/login/mfa` (wrong codes), and guessing class codes (`/auth/class-sign-in/{code}`, 20 per IP). The wait doubles with each further failure, up to 15 minutes. | Show the wait time from `Retry-After`. For adults offer "forgot password" (a reset lifts the lock); for a locked child, ask the teacher or parent to set new pictures or a new password. Do not retry before the time is up. |
| `http_error` | varies (4xx/5xx) | A protocol-level error with no more specific code. Fallback only; not produced by any current endpoint. | Reserved so the API never invents an undocumented code. | Handle by HTTP status class: 4xx is a client problem, 5xx a server problem. |
| `internal_error` | 500 | An unexpected server failure. The details are logged on the server, never sent to the client. | Bugs, or infrastructure failures such as the database being unreachable mid-request. | Safe to retry idempotent requests (`GET`, `DELETE`) with backoff. Report the `request_id`. |
| `service_unavailable` | 503 | A service the API needs for this request, other than the database, cannot be reached right now. | `POST /media/{id}/complete` when the file storage (MinIO, S3) is down or unreachable. | Retry later with backoff; nothing was changed. Report the `request_id` if it persists. |

### `details` for `validation_error`

One entry per problem:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request is invalid.",
    "details": [
      { "field": "body.grade_level", "message": "Input should be less than or equal to 12" },
      { "field": "query.limit", "message": "Input should be greater than or equal to 1" }
    ],
    "request_id": "..."
  }
}
```

- `field` is a dot-separated path. It starts with where the value came from (`body`, `query`, `path` or `header`), followed by the field name and, for nested values, list indexes or nested keys (for example `body.tags.0`). A body that is not valid JSON is reported with the field `body.<position>`.
- `message` is human-readable and may change; use `field` to attach it to a form input.

All other codes currently return an empty `details` array.

## Responses that are not errors in this format

- `GET /health/ready` returns `503` with `{"status": "unavailable", "version": "...", "checks": {"database": "down"}}` when the database is unreachable. It is a probe for orchestrators, not an API error.
- Successful `DELETE` calls return `204 No Content` with an empty body.

## Rules for clients

1. Branch on `code` first. If you get a code your client does not know, fall back to the HTTP status class (4xx or 5xx). New codes can be added without notice.
2. Never parse or compare `message`.
3. Log or display `request_id` with every unexpected error.

## Stability policy

- A code, once published here, is **never renamed, removed, or reused** for a different meaning within API version `v1`.
- **Adding** a new code is not a breaking change. It is listed here in the same pull request that introduces it.
- The HTTP status for a code does not change (except `http_error`, which mirrors whatever status caused it).
- `message` texts and the exact `details[].message` wording are not part of the contract.

## Adding a new code (for backend developers)

1. Add a member to `ErrorCode` in `app/core/exceptions.py` (lowercase `snake_case`).
2. Raise it through an `AppError` subclass with a fixed `status_code`. Services raise domain errors; they never build HTTP responses.
3. Add a row to the table above (code, status, meaning, when, client action).
4. Add a test that triggers it through the API. `tests/test_errors.py` fails until steps 1 and 3 agree.
