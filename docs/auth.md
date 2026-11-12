# Authentication, accounts and permissions

How people sign in to Lumina, what each account type may do, and how the API protects accounts. Error codes are described in [errors.md](errors.md). All paths below are under `/api/v1`.

**Adults sign in with their own email address** (and a password, or Google / Microsoft / Apple). **Children sign in in a way that fits their age**: pictures in Grades 1–3, a username or email from Grade 4, and Google / Microsoft / Apple from Grade 9. Teachers and admins can add two-factor authentication.

## Account types (roles)

| Role | How you get it | What it can do |
| --- | --- | --- |
| `parent` | Sign up (email + password, or Google / Microsoft / Apple) | Add child accounts and set up how they sign in, link an existing student account with a code, switch the active child |
| `teacher` | Sign up | Create classes (with a join code), create student accounts in them and set up their sign-in; create subjects; create courses and edit/archive **their own**. Publishing needs a **verified** teacher (a platform admin verifies) |
| `student` | Added by a parent (`POST /me/children`) or a teacher (`POST /classes/{id}/students`) | Sign in the way their age band allows; join classes with a code; create a link code for a second parent |
| `school_admin` | Assigned by a platform admin | Nothing extra yet (arrives with schools, Phase 6) |
| `platform_admin` | `make admin` / `python -m app.scripts.create_admin`, or assigned by another platform admin | Everything: all courses, drafts and classes, delete subjects, manage users, reset 2FA, impersonate, read the audit log |

Students cannot sign up on their own: a parent or a teacher creates their account, which keeps a responsible adult in the loop (Phase 13 in `TODO.md` covers the full parental-consent work).

## Sign-in by age

The rules are in one table in code (`SIGN_IN_POLICIES` in `app/modules/identity/domain.py`) and are checked at the end of **every** sign-in, whatever the method:

| Age band | Who | Ways to sign in | Minimum password | Session |
| --- | --- | --- | --- | --- |
| `early` | Grades 1–3 | **Class code + picture password** (on a classroom device), or username + password | 8 characters | 8 hours (one school day), **never extended** by refreshing |
| `middle` | Grades 4–8, and students whose grade is not known | Username + password, or email + password | 8 | 7 days, extended while in use |
| `teen` | Grades 9–12 | Username or email + password, Google, Microsoft, Apple | 10 | 14 days, extended while in use |
| `adult` | Parents, teachers, admins | Email + password, Google, Microsoft, Apple; 2FA for teachers and admins | 12 | 30 days, extended while in use |

- The band comes from the student's `grade_level`; when the grade changes, the allowed methods change with it (a child moving up to Grade 4 loses picture sign-in and needs a username and password).
- Children in Grades 1–3 cannot have an email sign-in at all. From Grade 4 a parent or teacher may give the child an email: the child then gets an invitation to choose their own password, and manages it themselves from then on.
- `GET /me` shows the account's `age_band` and the `sign_in_methods` that are set up and allowed right now.

### Who sets up a child's sign-in

Children **without their own email** are managed by their grown-ups:

- **Parents** of the child: `PUT /me/children/{id}/sign-in`.
- **Teachers** of a class the child is in: `PUT /classes/{class_id}/students/{id}/sign-in` (for example, picture passwords for a whole Grade 1 class).

The body sets any of `username`, `password` and `picture_password` (see below). A new password signs the child out everywhere, and a new password or picture password also lifts a lockout. Children **with their own email** manage their own password (and can use "forgot password"); their parents and teachers cannot change it.

Usernames are 3–30 characters (letters, digits, `.`, `-`, `_`), case-insensitive, unique across Lumina, and never contain `@`. A password always needs a username (or an email) to go with it.

### Picture sign-in (Grades 1–3)

For classroom devices. The child never types anything:

1. The teacher's class has **picture sign-in** switched on (the default for Grades 1–3) and shares its **class code** (for example `K7QM-9XDA`) with the device once.
2. `GET /auth/class-sign-in/{code}` returns the class name, the children who can sign in with pictures (first names to tap), and the 16 pictures to show: `apple ball bird boat cake car cat dog fish flower hat house moon star sun tree`.
3. The child taps their name, then their **three pictures in order**.
4. `POST /auth/login/picture` with `{class_code, student_id, pictures: ["cat", "sun", "apple"]}` returns tokens for an 8-hour session.

Three pictures out of 16 is only 4,096 combinations, so picture sign-in is guarded more tightly than passwords:

- It only works through the code of a class the child is currently in, and only while picture sign-in is on for that class.
- After 3 wrong tries the child waits (30 s, 1 min, 2 min …). After **10**, picture sign-in is locked until a teacher or parent sets new pictures.
- Guessing class codes is blocked per network address (20 wrong codes), and the teacher can replace a leaked code at any time (`POST /classes/{id}/join-code`).

### Username sign-in

`POST /auth/login/username` with `{username, password}`. Same protection as email sign-in (5 failures per username, 50 per network address, growing waits).

## Permissions

Derived from the role in code (`ROLE_PERMISSIONS` in `app/modules/identity/domain.py`), not stored in the database:

| Permission | student | parent | teacher | school_admin | platform_admin |
| --- | :-: | :-: | :-: | :-: | :-: |
| `children:manage` | | ✓ | | | ✓ |
| `classes:teach` | | | ✓ | | ✓ |
| `courses:author` | | | ✓ | | ✓ |
| `subjects:create` | | | ✓ | | ✓ |
| `classes:manage` | | | | | ✓ |
| `courses:manage` | | | | | ✓ |
| `standards:manage` | | | | | ✓ |
| `paths:manage` | | | | | ✓ |
| `subjects:delete` | | | | | ✓ |
| `users:manage` | | | | | ✓ |
| `users:impersonate` | | | | | ✓ |
| `audit:read` | | | | | ✓ |

### Ownership rules (service-layer policies)

Permissions say what a *role* may do; ownership says *which records*. Ownership rules live in one `policies.py` per module and are called by the services (never only by the routers), so every path to the data is covered:

| Rule | Where |
| --- | --- |
| A parent sees and manages only children linked to them (anything else is "not found") | `identity/policies.py`: `ensure_parent_of` |
| Parents and teachers set up sign-in only for children without their own email | `identity/policies.py`: `ensure_guardian_manages_sign_in` |
| A sign-in method must be allowed for the account's age band | `identity/policies.py`: `ensure_method_allowed` |
| Sessions, 2FA, password and email can only be changed by the account owner (not while impersonating) | `identity/policies.py`: `ensure_not_impersonated`, `ensure_can_use_totp` |
| Admins cannot demote or deactivate themselves, or impersonate other admins | `identity/policies.py` |
| A teacher sees and manages only their own classes; students see the classes they are in; other teachers' classes are "not found" | `classes/policies.py`: `ensure_can_view`, `ensure_can_manage` |
| A teacher manages a child's sign-in only while the child is in one of their classes | `classes/policies.py`: `ensure_enrolled` (+ the identity rule above) |
| Teachers edit only their own courses; drafts are visible only to their author and admins | `catalog/policies.py` |
| Course content: the author or an admin edits the draft and publishes; anyone reads published versions; lessons use only media their author may use | `curriculum/policies.py` |
| Uploads are private to their owner (and admins) | `media/policies.py` |

## Signing in

### Sign up and confirm the email (adults)

1. `POST /auth/register` with `email`, `password`, `display_name`, `role` (`parent` or `teacher`), optional `locale` and `timezone`. The answer is always **202** with the same message.
2. The user receives an email with a link to `{LUMINA_FRONTEND_URL}/verify-email?token=…`. The web app posts the token to `POST /auth/verify-email` (**204**).
3. Only then does `POST /auth/login` work. Before that it answers **403 `email_not_verified`**; the app can offer `POST /auth/verify-email/resend`.

If the address already has an account, no second account is created: its owner gets a "you already have an account" email instead. The response is identical either way, so the sign-up form cannot be used to find out who has an account.

### Tokens, refresh and sign-out (every method)

Every sign-in endpoint (`/auth/login`, `/auth/login/username`, `/auth/login/picture`, `/auth/oauth/exchange`) answers with **either** tokens:

```json
{"access_token": "eyJ…", "refresh_token": "…", "token_type": "bearer", "expires_in": 900}
```

**or**, when the account has two-factor authentication, a challenge (see [Two-factor authentication](#two-factor-authentication-teachers-and-admins)):

```json
{"mfa_required": true, "mfa_token": "…", "expires_in": 300, "methods": ["totp", "recovery_code"]}
```

- **Access token**: a signed JWT, valid for **15 minutes**. Send it as `Authorization: Bearer …`.
- **Refresh token**: opaque, **single-use**: `POST /auth/refresh {refresh_token}` returns new tokens and the old refresh token stops working. The session length depends on the age band (table above).
- **Reuse detection**: if an already-used refresh token is presented again, someone has a copy of it. The API then ends that whole sign-in (the *session*), so both the thief and the real user must sign in again (audited as `auth.refresh_token_reused`).
- **Immediate effect**: every request checks that its session is still open and the account still active. Signing out (`POST /auth/logout`), revoking a session, resetting the password or deactivating the account takes effect on the very next request.

**For frontend developers**: when any request returns **401 `unauthorized`**, call `/auth/refresh` once and retry; if the refresh also returns 401, show the sign-in screen. Make sure only one refresh runs at a time (a second refresh with the same token triggers reuse detection). **401 `invalid_credentials`** means a wrong password, picture or code was entered; it is not an expired session.

### Forgot password

1. `POST /auth/password/forgot {email}`: always **202**. If the account exists, it gets a link `{LUMINA_FRONTEND_URL}/reset-password?token=…` (valid **1 hour**, single use).
2. `POST /auth/password/reset {token, new_password}`: **204**. Every session of that account is ended, any lockout is lifted, and a "your password was changed" email is sent. The same endpoint completes a child's account invitation (`/activate?token=…`, valid 7 days).

### Sign in with Google, Microsoft or Apple

Available to adults and to students from Grade 9. Providers are switched on by configuration (see [Operating it](#operating-it)); `GET /auth/oauth/providers` lists the ones that are set up.

```
web app ── GET /auth/oauth/{provider}/start?intent=login ─────────────▶ API   → {authorization_url}
browser ── authorization_url ─────────────────────────────────────────▶ Google / Microsoft / Apple
provider ── redirect to /auth/oauth/{provider}/callback?code&state ───▶ API   (checks everything)
API ── 303 to {LUMINA_FRONTEND_URL}/auth/complete?code=… (or ?error=…) ▶ web app
web app ── POST /auth/oauth/exchange {code} ──────────────────────────▶ API   → tokens (or 2FA challenge)
```

- `intent=login` signs in an existing account. `intent=signup&role=parent|teacher` also creates the account if there is none.
- No token ever travels in a URL: the `code` on `/auth/complete` is a one-time handle, valid 2 minutes.
- **Finding the account**: by the provider's own user id once linked. The first time, an existing Lumina account is linked **only if the provider vouches for the email address**:
  - Google and Apple: when they mark the email as verified.
  - Microsoft: personal accounts (outlook.com, hotmail.com, …), and work or school accounts only when Microsoft confirms the organisation owns the email domain (the `xms_edov` claim). Other work accounts can carry an email typed in by their administrator; trusting it would let anyone take over an account with that address (the "nOAuth" attack). Such users sign up with email and password first, then link Microsoft from their settings.
- **Linking from the settings** (`POST /me/oauth/{provider}/link` → `authorization_url`): the user proves both sides, so no email check is needed. `GET /me/oauth` lists the linked providers; `DELETE /me/oauth/{provider}` unlinks one (refused when it is the only way left to sign in).
- Accounts created with a provider have no password; they can set one with "forgot password". For actions that need the password (deleting the account, changing the email, turning 2FA off), they must have signed in within the last 10 minutes instead.

Values of `error` on `/auth/complete`:

| `error` | Meaning | What to show |
| --- | --- | --- |
| `cancelled` | The user cancelled at the provider | Back to the sign-in screen |
| `no_account` | Verified email, but no Lumina account and `intent=login` | Offer to sign up (start again with `intent=signup&role=…`) |
| `email_not_verified` | The provider does not vouch for the email (or gives none) | Sign up with email and password, then link the provider in settings |
| `method_not_allowed` | The account's age band does not allow this provider | Sign in another way |
| `identity_in_use` | (Linking) this provider account is linked to another Lumina account | Unlink it there first |
| `account_inactive` | The account is deactivated or deleted | Contact support |
| `invalid_state`, `invalid_request`, `provider_error`, `provider_disabled` | Expired or tampered attempt, or the provider answered something invalid | "Something went wrong, try again" |

After a successful link the web app lands on `/auth/complete?linked={provider}`.

### Two-factor authentication (teachers and admins)

Optional, with any authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, …):

1. `POST /me/mfa/totp` returns a `secret` and an `otpauth_uri` (show it as a QR code).
2. `POST /me/mfa/totp/confirm {code}` turns it on and returns **10 recovery codes**, shown only this once.

From then on, every sign-in (password or Google/Microsoft/Apple) returns `mfa_required` instead of tokens. Finish with `POST /auth/login/mfa {mfa_token, code}` or `{mfa_token, recovery_code}`:

- Codes are 6 digits and change every 30 seconds; one step of clock drift is accepted. **A code can be used only once**: right after setting up 2FA, wait for the next code before signing in.
- Each challenge allows 5 attempts within 5 minutes; wrong codes also count towards the account's lockout.
- Recovery codes work once each; using one sends an email with the number left. `POST /me/mfa/recovery-codes {code}` replaces them all.
- `DELETE /me/mfa/totp {code, password}` turns 2FA off (a recovery code also works as `code`).
- Lost both phone and recovery codes: a platform admin can turn it off (`DELETE /admin/users/{id}/mfa`). That ends all the user's sessions, is audited, and emails the user.
- Secrets are stored encrypted (with `LUMINA_ENCRYPTION_KEY`); recovery codes only as hashes.

## The signed-in user: `/me`

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/me` | Profile, role, `age_band`, permissions, `sign_in_methods`, `mfa_enabled`, student/teacher profile, active child |
| PATCH | `/me` | Change `display_name`, `locale`, `timezone` |
| POST | `/me/password` | `{current_password, new_password}`. Signs out every *other* session |
| POST | `/me/email` | `{new_email, password}`. Confirmation link to the new address (valid 24 hours), applied at `POST /auth/email/confirm {token}`. The old address is told. Not for Grades 1–3 |
| DELETE | `/me` | `{password}`. Deletes the account (see below) |
| GET | `/me/sessions` | Open sessions (devices) with their `sign_in_method`, and `current: true` for this one |
| DELETE | `/me/sessions/{id}` | End one session |
| POST | `/me/sessions/revoke-others` | "Sign out everywhere else" |
| GET / POST / DELETE | `/me/oauth`, `/me/oauth/{provider}/link`, `/me/oauth/{provider}` | Linked Google/Microsoft/Apple accounts |
| POST / DELETE | `/me/mfa/totp`, `/me/mfa/totp/confirm`, `/me/mfa/recovery-codes` | Two-factor authentication |
| PATCH | `/me/teacher-profile` | Teachers: `bio`, `subject_ids` |

**Deleting an account** erases the personal data (email, username, name, passwords, date of birth, school, bio, 2FA, linked providers), ends every session and marks the account deleted. The row itself stays so that the audit log and authored courses keep a valid reference. Child accounts that this parent alone managed, and that have no email of their own, are deleted too. The same email address can sign up again afterwards.

## Parents and children

| Method | Path | Who | What it does |
| --- | --- | --- | --- |
| GET | `/me/children` | parent | The parent's children, with their `age_band` and `sign_in_methods` |
| POST | `/me/children` | parent | Add a child: `display_name`, optional `grade_level`, `date_of_birth`, `school_name`, and either sign-in set-up (`username` + `password`, `picture_password`) or, from Grade 4, an `email` (the child is invited to choose a password) |
| PUT | `/me/children/{id}/sign-in` | parent | Set up or change the child's username, password or pictures |
| POST | `/me/link-code` | student | Create a code like `K7QM-9XDA` (valid 24 hours, single use) |
| POST | `/me/children/link` | parent | `{code}`: link that student to this parent (for example a second parent) |
| PUT | `/me/active-child` | parent | `{child_id}` (or `null`): which child this device is currently looking at; remembered per session |

## Classes

A minimal version of Phase 6's classes (BE-SCH-02): a teacher, students, grade, subject, school year, join code.

| Method | Path | Who | What it does |
| --- | --- | --- | --- |
| POST | `/classes` | teacher | Create a class: `name`, optional `grade_level`, `subject_id`, `school_year` (`2026-2027`), `picture_sign_in` (on by default for Grades 1–3). Gets a join code |
| GET | `/classes` | teacher, student, admin | Teachers: their classes. Students: classes they are in. Admins: all |
| GET / PATCH / DELETE | `/classes/{id}` | teacher of the class, admin (students: read) | Read, change, archive. An archived class's code stops working |
| POST | `/classes/{id}/join-code` | teacher of the class | Replace the code; the old one stops working at once |
| GET | `/classes/{id}/students` | teacher of the class | The roster (not visible to students) |
| POST | `/classes/{id}/students` | teacher of the class | Create a student account directly in the class, with a username/password or picture password (or an email from Grade 4) |
| DELETE | `/classes/{id}/students/{student_id}` | teacher of the class | Remove a student from the class |
| PUT | `/classes/{id}/students/{student_id}/sign-in` | teacher of the class | Set up the student's sign-in (no own email only) |
| POST | `/classes/join` | student | `{code}`: join a class |

## Administration (platform admins)

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/admin/users` | Search by `q` (email or name), `role`, `is_active`; paginated |
| GET | `/admin/users/{id}` | One user |
| PATCH | `/admin/users/{id}` | `{role}` and/or `{is_active}`. Deactivating ends all their sessions at once. You cannot change your own role or deactivate yourself; students keep the student role |
| PUT | `/admin/users/{id}/teacher-verification` | `{verified: true/false}`. Only verified teachers can publish |
| DELETE | `/admin/users/{id}/mfa` | Turn off a user's 2FA (lost phone and recovery codes) |
| POST | `/admin/users/{id}/impersonate` | Act as the user for support (see below) |
| GET | `/admin/audit-events` | Search the audit log by `actor_id`, `action`, `entity_type`, `entity_id` |

**Impersonation** returns a 15-minute access token with no refresh token. It cannot be used on other platform admins or inactive accounts. Everything done with it is recorded with both the user's id and the admin's (`impersonator_id`), and `GET /me` shows `impersonated_by`. Account-security actions are blocked while impersonating: password, email, 2FA, linked providers, sessions, deleting the account.

## Catalog rules (who may change courses)

- Anyone can read **published** courses and subjects.
- **Drafts and archived courses** are visible only to their author and platform admins; to everyone else they answer 404. `GET /courses?status=draft` lists all drafts for admins and only their own for teachers.
- Teachers create courses (they become the `author_id`) and edit or archive their own. Platform admins can change any course, including platform content without an author (such as the seeded catalog).
- **Publishing** needs a verified teacher or a platform admin. Unverified teachers can work on drafts in the meantime.
- Teachers and platform admins create subjects; only platform admins delete them.

## Security properties

| Property | How |
| --- | --- |
| Passwords and picture passwords stored safely | Argon2id (OWASP parameters: 19 MiB, 2 iterations), upgraded automatically at sign-in when the parameters change |
| Password policy | Length over complexity: 12+ characters for adults, 10+ for Grades 9–12, 8+ for younger students, up to 128. Rejected: passwords containing your email name (5+ characters) and very repetitive ones |
| Age-appropriate sign-in | One policy table per age band, checked at the end of every sign-in; young children get supervised methods and short, non-extendable sessions |
| No account enumeration | Register, resend verification, forgot password and email change answer the same for known and unknown addresses; emails are sent after the response; sign-in checks a dummy hash for unknown accounts so timing matches |
| Brute-force protection | Growing lockouts (30 s doubling to 15 min) per email, username, child (pictures: hard lock after 10), 2FA account, and per network address; class-code guessing is blocked per address. Answer: **429** with `Retry-After` |
| Stolen refresh tokens | Single-use with reuse detection; stored only as SHA-256 hashes |
| Social sign-in | Authorization code flow with state (CSRF), nonce, and PKCE where the provider supports it; all kept on the server and single-use. ID tokens checked against the provider's published keys (signature, issuer, audience, expiry, nonce). Emails trusted only when the provider vouches for them (no "nOAuth" takeover). No tokens in URLs |
| Two-factor | RFC 6238 TOTP with replay protection, attempt limits and lockout; encrypted secrets; single-use hashed recovery codes; email notices when 2FA changes or a recovery code is used |
| Emailed links and codes | Single-use, expiring, stored as hashes; a new one cancels the previous one. Verification and password-reset emails go out at most once a minute per account |
| Tokens cannot be forged or downgraded | HS256 with `LUMINA_SECRET_KEY`; the algorithm is fixed server-side; issuer, audience, type and expiry are checked. The role is always read from the database |
| Audit trail | `audit_events` records sign-ins (method, second factor, failures, lockouts), sign-outs, password/email/2FA changes, recovery-code use, provider linking, role changes, (de)activations, teacher verification, impersonation, child accounts and their sign-in set-up, classes and enrolments, course archiving and subject deletion. A database trigger makes the table append-only |
| Nothing sensitive in logs | No passwords, pictures, tokens, codes or email addresses in logs or audit details; the throttle table stores only hashes |

## Operating it

- **First admin**: `make admin EMAIL=you@example.com` (asks for a password) or, for scripts, `LUMINA_ADMIN_PASSWORD=… python -m app.scripts.create_admin --email you@example.com`. In Docker: `docker compose exec api python -m app.scripts.create_admin --email you@example.com`. `make bootstrap` creates `admin@example.com` with a generated password for local development.
- **Keys**: set `LUMINA_SECRET_KEY` (signs access tokens) and `LUMINA_ENCRYPTION_KEY` (encrypts 2FA secrets) to long random values in every deployed environment; production refuses to start with the development signing key. Changing the signing key signs everybody out (clients recover with one refresh). **Never change the encryption key**: 2FA set-ups encrypted with the old one stop working, and those users must sign in with a recovery code or be reset by an admin. If `LUMINA_ENCRYPTION_KEY` is empty, the signing key is used for both, which then must never change either.
- **Social sign-in**: register the app with each provider and set the `LUMINA_GOOGLE_*`, `LUMINA_MICROSOFT_*` and `LUMINA_APPLE_*` values (see `.env.example`). The redirect URI to register is `{LUMINA_API_PUBLIC_URL}/api/v1/auth/oauth/{provider}/callback`, exactly. The web app needs a page at `{LUMINA_FRONTEND_URL}/auth/complete`.
- **Email**: locally, Mailpit catches everything at http://localhost:8025. In production set the `LUMINA_SMTP_*` variables for your provider, and `LUMINA_FRONTEND_URL` to the web app's address.
- **Behind a reverse proxy**, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy address>` so per-address limits and the audit log see the real client address.

## Not built yet

| Item | Where it is tracked |
| --- | --- |
| Schools, co-teachers, assignments (classes are minimal for now) | Phase 6 (BE-SCH-01..04) |
| School SSO and roster sync (Google Workspace for Education, Clever, ClassLink, OneRoster) | Phase 6 (BE-SCH-06) |
| Verifiable parental consent for children under 13, age gate | Phase 13 (BE-SEC-04) |
| httpOnly cookies instead of bearer tokens for the web app | Cross-project decision X-02 |
