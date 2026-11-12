"""Identity use cases. Services own the transaction: each public method commits once.

Rules that apply throughout:
- Responses never reveal whether an email address has an account (register, resend,
  forgot password): same response, similar timing, email sent only after the commit.
- Nothing secret is logged or audited: no passwords, tokens, codes, or email addresses.
- Emails are queued after the commit, so a rolled-back request never sends a link.
- Every way of signing in (email, username, picture, Google/Microsoft/Apple, 2FA) ends in
  ``_finish_sign_in``: age-band check, two-factor challenge, session lifetime, audit.
"""

import hashlib
import logging
import math
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.email import EmailMessage, Mailer
from app.core.exceptions import (
    ConflictError,
    DomainValidationError,
    EmailNotVerifiedError,
    ForbiddenError,
    InvalidCredentialsError,
    InvalidTokenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)
from app.db.mixins import utcnow
from app.db.transactions import conflict_on_integrity_error
from app.modules.audit.domain import AuditAction
from app.modules.audit.service import AuditLog
from app.modules.catalog.models import Subject
from app.modules.identity import emails, policies, totp
from app.modules.identity.domain import (
    EMAIL_RESEND_COOLDOWN,
    MFA_CHALLENGE_MAX_ATTEMPTS,
    MFA_CHALLENGE_TTL,
    OAUTH_HANDOFF_TTL,
    OAUTH_STATE_TTL,
    PASSWORD_MIN_LENGTH,
    PICTURE_HARD_LOCK_FAILURES,
    PICTURE_THROTTLE_THRESHOLD,
    RECENT_SIGN_IN,
    RECOVERY_CODE_COUNT,
    SELF_REGISTRATION_ROLES,
    SIGN_IN_POLICIES,
    THROTTLE_ACCOUNT_THRESHOLD,
    THROTTLE_BASE_LOCKOUT,
    THROTTLE_FAILURE_WINDOW,
    THROTTLE_IP_THRESHOLD,
    THROTTLE_MAX_LOCKOUT,
    TOKEN_TTL,
    Actor,
    AgeBand,
    ChallengeKind,
    LinkStatus,
    RequestInfo,
    Role,
    SessionEndReason,
    SignInMethod,
    TokenPurpose,
    age_band,
)
from app.modules.identity.models import (
    LoginChallenge,
    OAuthIdentity,
    OAuthState,
    ParentChildLink,
    RefreshToken,
    Session,
    StudentProfile,
    TeacherProfile,
    User,
    UserMfa,
    UserToken,
)
from app.modules.identity.oauth import OAuthError, OAuthProviders, ProviderIdentity, pkce_pair
from app.modules.identity.repository import (
    SessionRepository,
    SignInRepository,
    ThrottleRepository,
    TokenRepository,
    UserRepository,
)
from app.modules.identity.schemas import (
    ChildCreate,
    MeUpdate,
    MfaRequired,
    RegisterRequest,
    StudentSignInUpdate,
    TeacherProfileUpdate,
    TokenPair,
    UserAdminUpdate,
)
from app.modules.identity.security import (
    InvalidAccessToken,
    PasswordHasher,
    create_access_token,
    decode_access_token,
    hash_throttle_key,
    hash_token,
    new_link_code,
    new_opaque_token,
    normalize_link_code,
)

logger = logging.getLogger("lumina.identity")

DELETED_USER_NAME = "Deleted user"
# Far enough in the future to mean "until a teacher or parent sets a new picture password".
HARD_LOCK = timedelta(days=365 * 100)

# Shorter email names (sam@, ann@) occur inside ordinary passphrases by accident.
_MIN_EMAIL_NAME_TO_CHECK = 5


def normalize_email(email: str) -> str:
    """Emails are case-insensitive: store and compare them in lower case."""
    return email.strip().lower()


def check_password_policy(
    password: str, email: str | None, *, min_length: int = PASSWORD_MIN_LENGTH
) -> None:
    """Length over complexity. The minimum depends on the age band (8/10/12)."""
    if len(password) < min_length:
        raise DomainValidationError(f"The password must have at least {min_length} characters.")
    if email:
        local_part = email.split("@", 1)[0]
        if len(local_part) >= _MIN_EMAIL_NAME_TO_CHECK and local_part in password.lower():
            raise DomainValidationError("The password must not contain your email address.")
    if len(set(password)) < 4:
        raise DomainValidationError("The password is too repetitive.")


def picture_secret(pictures: Sequence[str]) -> str:
    """What is hashed for a picture password: the keys in order, e.g. "cat sun apple"."""
    return " ".join(pictures)


def sign_in_methods(user: User, linked_providers: Sequence[str] = ()) -> list[str]:
    """The ways this account can sign in right now (allowed for its age band AND set up)."""
    allowed = SIGN_IN_POLICIES[policies.band_of(user)].methods
    available: list[str] = []
    if user.email and user.password_hash and SignInMethod.EMAIL in allowed:
        available.append(SignInMethod.EMAIL.value)
    if user.username and user.password_hash and SignInMethod.USERNAME in allowed:
        available.append(SignInMethod.USERNAME.value)
    profile = user.student_profile if user.role == Role.STUDENT else None
    if profile and profile.picture_password_hash and SignInMethod.PICTURE in allowed:
        available.append(SignInMethod.PICTURE.value)
    available += [p for p in linked_providers if SignInMethod(p) in allowed]
    return available


@dataclass(frozen=True)
class MeView:
    user: User
    session: Session | None
    mfa_enabled: bool
    linked_providers: list[str]


LoginResult = TokenPair | MfaRequired


class _IdentityBase:
    """Shared plumbing: repositories, tokens, sessions, throttling, emails after commit."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        hasher: PasswordHasher,
        mailer: Mailer,
        audit: AuditLog,
    ) -> None:
        self._session = session
        self._settings = settings
        self._hasher = hasher
        self._mailer = mailer
        self._audit = audit
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._tokens = TokenRepository(session)
        self._throttles = ThrottleRepository(session)
        self._signin = SignInRepository(session)
        self._outbox: list[EmailMessage] = []

    # --- transaction and email ------------------------------------------------------------

    def _queue_email(self, message: EmailMessage) -> None:
        self._outbox.append(message)

    async def _commit(self) -> None:
        await self._session.commit()
        for message in self._outbox:
            self._mailer.send(message)
        self._outbox.clear()

    # --- single-use tokens ----------------------------------------------------------------

    async def _issue_token(
        self,
        user: User,
        purpose: TokenPurpose,
        now: datetime,
        *,
        new_email: str | None = None,
    ) -> tuple[str, UserToken]:
        await self._tokens.invalidate_unused(user.id, [purpose], now)
        raw = new_link_code() if purpose == TokenPurpose.PARENT_LINK else new_opaque_token()
        token = UserToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=now + TOKEN_TTL[purpose],
            new_email=new_email,
        )
        await self._tokens.add(token)
        return raw, token

    async def _consume_token(
        self, raw: str, purposes: set[TokenPurpose], now: datetime
    ) -> UserToken:
        token = await self._tokens.get_for_update(hash_token(raw))
        if (
            token is None
            or token.purpose not in purposes
            or token.used_at is not None
            or token.expires_at <= now
            or not token.user.is_active
            or token.user.deleted_at is not None
        ):
            raise InvalidTokenError()
        token.used_at = now
        return token

    async def _recently_sent(self, user: User, purpose: TokenPurpose, now: datetime) -> bool:
        latest = await self._tokens.latest_created_at(user.id, purpose)
        return latest is not None and now - latest < EMAIL_RESEND_COOLDOWN

    # --- throttling (BE-AUTH-08) ----------------------------------------------------------

    async def _ensure_not_locked(
        self, keys: Sequence[str], now: datetime, request: RequestInfo
    ) -> None:
        lock_ends = [
            t.locked_until
            for t in await self._throttles.get_many(keys)
            if t.locked_until is not None and t.locked_until > now
        ]
        if lock_ends:
            await self._audit.record(AuditAction.LOGIN_LOCKED, request=request)
            await self._commit()
            raise RateLimitedError(math.ceil((max(lock_ends) - now).total_seconds()))

    async def _record_failure(
        self, key: str, threshold: int, now: datetime, *, hard_lock_after: int | None = None
    ) -> None:
        throttle = await self._throttles.lock(key, now)
        window_passed = now - throttle.last_failure_at > THROTTLE_FAILURE_WINDOW
        hard_locked = hard_lock_after is not None and throttle.failures >= hard_lock_after
        if throttle.failures and window_passed and not hard_locked:
            throttle.failures = 0  # old failures no longer count (a hard lock stays)
        throttle.failures += 1
        throttle.last_failure_at = now
        if hard_lock_after is not None and throttle.failures >= hard_lock_after:
            throttle.locked_until = now + HARD_LOCK
            return
        extra = throttle.failures - threshold
        if extra >= 0:
            # Exponential backoff: 30 s, 1 min, 2 min, ... capped at 15 min.
            throttle.locked_until = now + min(
                THROTTLE_BASE_LOCKOUT * 2**extra, THROTTLE_MAX_LOCKOUT
            )

    async def _record_sign_in_failure(
        self, keys: Sequence[tuple[str, int]], user: User | None, request: RequestInfo
    ) -> None:
        now = utcnow()
        for key, threshold in keys:
            await self._record_failure(key, threshold, now)
        await self._audit.record(
            AuditAction.LOGIN_FAILED,
            actor_id=user.id if user else None,
            entity_type="user" if user else None,
            entity_id=user.id if user else None,
            request=request,
        )
        await self._commit()

    # --- sessions and tokens --------------------------------------------------------------

    def _session_lifetime(self, user: User, method: SignInMethod) -> timedelta:
        if method == SignInMethod.IMPERSONATION:
            return timedelta(minutes=self._settings.impersonation_ttl_minutes)
        band_lifetime = SIGN_IN_POLICIES[policies.band_of(user)].session_lifetime
        return min(band_lifetime, timedelta(days=self._settings.refresh_token_ttl_days))

    async def _start_session(
        self,
        user: User,
        request: RequestInfo,
        now: datetime,
        *,
        method: SignInMethod,
        impersonator_id: uuid.UUID | None = None,
    ) -> Session:
        session = Session(
            user_id=user.id,
            expires_at=now + self._session_lifetime(user, method),
            last_used_at=now,
            ip=request.ip,
            user_agent=(request.user_agent or "")[:255] or None,
            impersonator_id=impersonator_id,
            sign_in_method=method.value,
        )
        await self._sessions.add(session)
        return session

    async def _issue_token_pair(self, user: User, session: Session, now: datetime) -> TokenPair:
        refresh = new_opaque_token()
        await self._sessions.add(
            RefreshToken(
                session_id=session.id,
                token_hash=hash_token(refresh),
                # Never outlives its session (fixed-length sessions for young children).
                expires_at=min(
                    now + timedelta(days=self._settings.refresh_token_ttl_days),
                    session.expires_at,
                ),
            )
        )
        ttl = timedelta(minutes=self._settings.access_token_ttl_minutes)
        access = create_access_token(
            self._settings,
            user_id=user.id,
            session_id=session.id,
            role=user.role,
            ttl=ttl,
            now=now,
        )
        return TokenPair(
            access_token=access, expires_in=int(ttl.total_seconds()), refresh_token=refresh
        )

    async def _finish_sign_in(
        self,
        user: User,
        method: SignInMethod,
        request: RequestInfo,
        *,
        second_factor_done: bool = False,
    ) -> LoginResult:
        """The end of every sign-in: tokens, or a two-factor challenge first."""
        policies.ensure_method_allowed(user, method)
        now = utcnow()
        if not second_factor_done:
            mfa = await self._signin.get_mfa(user.id)
            if mfa is not None and mfa.is_enabled:
                raw = new_opaque_token()
                await self._signin.add(
                    LoginChallenge(
                        user_id=user.id,
                        kind=ChallengeKind.MFA,
                        token_hash=hash_token(raw),
                        sign_in_method=method.value,
                        expires_at=now + MFA_CHALLENGE_TTL,
                    )
                )
                await self._commit()
                return MfaRequired(mfa_token=raw, expires_in=int(MFA_CHALLENGE_TTL.total_seconds()))

        session = await self._start_session(user, request, now, method=method)
        pair = await self._issue_token_pair(user, session, now)
        await self._audit.record(
            AuditAction.LOGIN_SUCCEEDED,
            actor_id=user.id,
            entity_type="session",
            entity_id=session.id,
            request=request,
            details={"method": method.value, "second_factor": second_factor_done},
        )
        await self._commit()
        return pair

    # --- confirming it is really the account owner ----------------------------------------

    async def _confirm_owner(self, actor: Actor, password: str | None) -> User:
        """Password if the account has one; otherwise a sign-in in the last 10 minutes
        (accounts that only use Google/Microsoft/Apple have no password)."""
        policies.ensure_not_impersonated(actor)
        user = await self._users.get_with_profiles(actor.user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if user.password_hash is not None:
            if password is None or not self._hasher.verify(user.password_hash, password):
                raise InvalidCredentialsError("The password is incorrect.")
            return user
        session = await self._sessions.get(actor.session_id)
        if session is None or utcnow() - session.created_at > RECENT_SIGN_IN:
            raise InvalidCredentialsError("Sign in again to confirm it is you, then retry.")
        return user

    def _anonymize(self, user: User, now: datetime) -> None:
        user.email = None
        user.username = None
        user.password_hash = None
        user.email_verified_at = None
        user.display_name = DELETED_USER_NAME
        user.is_active = False
        user.deleted_at = now
        if user.student_profile is not None:
            user.student_profile.date_of_birth = None
            user.student_profile.school_name = None
            user.student_profile.picture_password_hash = None
        if user.teacher_profile is not None:
            user.teacher_profile.bio = None

    # --- student sign-in set-up (shared by parents and teachers) --------------------------

    async def _apply_student_sign_in(
        self, student: User, data: StudentSignInUpdate, now: datetime
    ) -> list[str]:
        """Set a child's username, password and/or picture password. The caller has
        already checked that the actor may do this. Returns the changed field names."""
        policies.ensure_guardian_manages_sign_in(student)
        band = policies.band_of(student)
        policy = SIGN_IN_POLICIES[band]
        changed: list[str] = []

        if data.picture_password is not None:
            if band != AgeBand.EARLY:
                raise DomainValidationError("Picture passwords are for Grades 1 to 3.")
            assert student.student_profile is not None
            student.student_profile.picture_password_hash = self._hasher.hash(
                picture_secret(data.picture_password)
            )
            # A new picture password also lifts a lockout.
            await self._throttles.delete(hash_throttle_key("picture", str(student.id)))
            changed.append("picture_password")

        if data.username is not None and data.username != student.username:
            if await self._users.username_taken(data.username, excluding=student.id):
                raise ConflictError("This username is taken. Try adding a number.")
            if student.username:
                await self._throttles.delete(hash_throttle_key("username", student.username))
            student.username = data.username
            changed.append("username")

        if data.password is not None:
            if not student.username:
                raise DomainValidationError("Set a username so the child can use this password.")
            check_password_policy(data.password, None, min_length=policy.password_min_length)
            student.password_hash = self._hasher.hash(data.password)
            await self._throttles.delete(hash_throttle_key("username", student.username))
            # A new password set by a guardian ends the child's other sessions.
            await self._sessions.revoke_all(
                student.id, reason=SessionEndReason.PASSWORD_RESET, now=now
            )
            changed.append("password")
        return changed


class AuthService(_IdentityBase):
    """Register, verify, sign in (email, username, picture, 2FA), refresh, sign out, reset."""

    # --- authenticating requests ----------------------------------------------------------

    async def authenticate(self, access_token: str) -> Actor:
        """Turn a bearer token into an Actor. Checks the session on every request, so
        logout, revocation and deactivation take effect immediately."""
        try:
            claims = decode_access_token(self._settings, access_token)
        except InvalidAccessToken as exc:
            raise UnauthorizedError("The access token is invalid or has expired.") from exc

        session = await self._sessions.get_for_auth(claims.session_id)
        now = utcnow()
        if (
            session is None
            or session.user_id != claims.user_id
            or session.revoked_at is not None
            or session.expires_at <= now
            or not session.user.is_active
            or session.user.deleted_at is not None
        ):
            raise UnauthorizedError("The session has ended. Sign in again.")
        user = session.user
        return Actor(
            user_id=user.id,
            role=user.role,  # from the database, not the token: role changes apply at once
            session_id=session.id,
            is_verified_teacher=bool(user.teacher_profile and user.teacher_profile.is_verified),
            impersonator_id=session.impersonator_id,
        )

    # --- registration and verification ----------------------------------------------------

    async def register(self, data: RegisterRequest, request: RequestInfo) -> None:
        email = normalize_email(data.email)
        check_password_policy(data.password, email)
        # Hash before looking the address up, so both paths cost the same time.
        password_hash = self._hasher.hash(data.password)
        now = utcnow()

        existing = await self._users.get_by_email(email)
        if existing is not None:
            await self._notify_existing_account(existing, now)
            await self._commit()
            return

        user = User(
            email=email,
            password_hash=password_hash,
            display_name=data.display_name.strip(),
            role=Role(data.role),
            locale=data.locale,
            timezone=data.timezone,
        )
        try:
            async with conflict_on_integrity_error(self._session, "email taken"):
                await self._users.add(user)
                if user.role == Role.TEACHER:
                    await self._users.add(TeacherProfile(user_id=user.id))
                raw, _ = await self._issue_token(user, TokenPurpose.VERIFY_EMAIL, now)
                await self._audit.record(
                    AuditAction.USER_REGISTERED,
                    actor_id=user.id,
                    entity_type="user",
                    entity_id=user.id,
                    request=request,
                    details={"role": user.role.value, "method": SignInMethod.EMAIL.value},
                )
                self._queue_email(
                    emails.verify_email(email, user.display_name, raw, self._settings.frontend_url)
                )
                await self._commit()
        except ConflictError:
            # Lost a race with a concurrent registration of the same address: answer as if
            # it had existed already (no enumeration), and send nothing.
            self._outbox.clear()

    async def _notify_existing_account(self, user: User, now: datetime) -> None:
        if not user.is_active or user.deleted_at is not None or user.email is None:
            return
        if not user.is_email_verified:
            # Same person signing up again before verifying: send a fresh link.
            if not await self._recently_sent(user, TokenPurpose.VERIFY_EMAIL, now):
                raw, _ = await self._issue_token(user, TokenPurpose.VERIFY_EMAIL, now)
                self._queue_email(
                    emails.verify_email(
                        user.email, user.display_name, raw, self._settings.frontend_url
                    )
                )
            return
        self._queue_email(emails.account_exists(user.email, self._settings.frontend_url))

    async def verify_email(self, raw_token: str, request: RequestInfo) -> None:
        now = utcnow()
        token = await self._consume_token(raw_token, {TokenPurpose.VERIFY_EMAIL}, now)
        if token.user.email_verified_at is None:
            token.user.email_verified_at = now
            await self._audit.record(
                AuditAction.EMAIL_VERIFIED,
                actor_id=token.user_id,
                entity_type="user",
                entity_id=token.user_id,
                request=request,
            )
        await self._commit()

    async def resend_verification(self, email: str) -> None:
        now = utcnow()
        user = await self._users.get_by_email(normalize_email(email))
        if (
            user is not None
            and user.email is not None
            and user.is_active
            and user.deleted_at is None
            and not user.is_email_verified
            and user.password_hash is not None
            and not await self._recently_sent(user, TokenPurpose.VERIFY_EMAIL, now)
        ):
            raw, _ = await self._issue_token(user, TokenPurpose.VERIFY_EMAIL, now)
            self._queue_email(
                emails.verify_email(user.email, user.display_name, raw, self._settings.frontend_url)
            )
        await self._commit()

    # --- sign in ----------------------------------------------------------------------------

    def _ip_key(self, request: RequestInfo) -> list[tuple[str, int]]:
        return [(hash_throttle_key("ip", request.ip), THROTTLE_IP_THRESHOLD)] if request.ip else []

    async def login(self, email: str, password: str, request: RequestInfo) -> LoginResult:
        """Email + password (adults; students from Grade 4 who have an email)."""
        email = normalize_email(email)
        keys = [(hash_throttle_key("account", email), THROTTLE_ACCOUNT_THRESHOLD)]
        keys += self._ip_key(request)
        # Locked out: refuse before checking the password (no Argon2 work for attackers).
        await self._ensure_not_locked([k for k, _ in keys], utcnow(), request)

        user = await self._users.get_by_email(email)
        valid = self._hasher.verify(user.password_hash if user else None, password)
        if user is None or not valid or user.deleted_at is not None:
            await self._record_sign_in_failure(keys, user, request)
            raise InvalidCredentialsError()

        # The password is right from here on, so these answers reveal nothing new.
        if not user.is_active:
            raise ForbiddenError("This account has been deactivated.")
        if not user.is_email_verified:
            raise EmailNotVerifiedError()
        await self._throttles.delete(keys[0][0])
        self._upgrade_hash(user, password)
        return await self._finish_sign_in(user, SignInMethod.EMAIL, request)

    async def login_with_username(
        self, username: str, password: str, request: RequestInfo
    ) -> LoginResult:
        """Username + password: children without an email (BE-AUTH-10)."""
        keys = [(hash_throttle_key("username", username), THROTTLE_ACCOUNT_THRESHOLD)]
        keys += self._ip_key(request)
        await self._ensure_not_locked([k for k, _ in keys], utcnow(), request)

        user = await self._users.get_by_username(username)
        valid = self._hasher.verify(user.password_hash if user else None, password)
        if user is None or not valid or user.deleted_at is not None:
            await self._record_sign_in_failure(keys, user, request)
            raise InvalidCredentialsError("The username or password is incorrect.")
        if not user.is_active:
            raise ForbiddenError("This account has been deactivated.")
        await self._throttles.delete(keys[0][0])
        self._upgrade_hash(user, password)
        return await self._finish_sign_in(user, SignInMethod.USERNAME, request)

    async def login_with_picture(
        self, student: User, pictures: Sequence[str], request: RequestInfo
    ) -> LoginResult:
        """Picture password (Grades 1-3). The classes module has already checked the class
        code and that the child is in that class."""
        key = hash_throttle_key("picture", str(student.id))
        await self._ensure_not_locked([key], utcnow(), request)
        profile = student.student_profile
        stored = profile.picture_password_hash if profile else None
        if not self._hasher.verify(stored, picture_secret(pictures)) or not student.is_active:
            await self._record_failure(
                key,
                PICTURE_THROTTLE_THRESHOLD,
                utcnow(),
                hard_lock_after=PICTURE_HARD_LOCK_FAILURES,
            )
            await self._audit.record(
                AuditAction.LOGIN_FAILED,
                actor_id=student.id,
                entity_type="user",
                entity_id=student.id,
                request=request,
                details={"method": SignInMethod.PICTURE.value},
            )
            await self._commit()
            raise InvalidCredentialsError("Those pictures are not right. Try again.")
        await self._throttles.delete(key)
        return await self._finish_sign_in(student, SignInMethod.PICTURE, request)

    # --- guessing class codes (the picture sign-in page is public) ------------------------

    def _code_guess_key(self, request: RequestInfo) -> str | None:
        return hash_throttle_key("classcode-ip", request.ip) if request.ip else None

    async def ensure_code_lookup_allowed(self, request: RequestInfo) -> None:
        key = self._code_guess_key(request)
        if key:
            await self._ensure_not_locked([key], utcnow(), request)

    async def record_code_lookup_failure(self, request: RequestInfo, threshold: int) -> None:
        key = self._code_guess_key(request)
        if key:
            await self._record_failure(key, threshold, utcnow())
            await self._commit()

    def _upgrade_hash(self, user: User, password: str) -> None:
        assert user.password_hash is not None
        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(password)

    async def complete_mfa(
        self,
        mfa_token: str,
        *,
        code: str | None,
        recovery_code: str | None,
        request: RequestInfo,
    ) -> TokenPair:
        """Second step of a sign-in with two-factor authentication (BE-AUTH-12)."""
        now = utcnow()
        challenge = await self._signin.get_challenge_for_update(hash_token(mfa_token))
        if (
            challenge is None
            or challenge.kind != ChallengeKind.MFA
            or challenge.used_at is not None
            or challenge.expires_at <= now
            or challenge.attempts >= MFA_CHALLENGE_MAX_ATTEMPTS
            or not challenge.user.is_active
        ):
            raise InvalidTokenError("This sign-in has expired. Start again.")
        user = challenge.user
        key = hash_throttle_key("mfa", str(user.id))
        await self._ensure_not_locked([key], now, request)

        ok = False
        if code is not None:
            mfa = await self._signin.get_mfa_for_update(user.id)
            secret = (
                SecretBoxCache.get(self._settings).decrypt(mfa.secret_encrypted) if mfa else None
            )
            step = (
                totp.matching_step(secret, code, last_used_step=mfa.last_used_step)
                if mfa and mfa.is_enabled and secret
                else None
            )
            if mfa is not None and step is not None:
                mfa.last_used_step = step
                ok = True
        elif recovery_code is not None:
            stored = await self._signin.get_recovery_code_for_update(
                user.id, hash_token(totp.normalize_recovery_code(recovery_code))
            )
            if stored is not None and stored.used_at is None:
                stored.used_at = now
                ok = True
                await self._audit.record(
                    AuditAction.RECOVERY_CODE_USED, actor_id=user.id, request=request
                )
                if user.email:
                    remaining = await self._signin.unused_recovery_codes(user.id)
                    self._queue_email(
                        emails.recovery_code_used(user.email, user.display_name, remaining)
                    )

        if not ok:
            challenge.attempts += 1
            await self._record_failure(key, THROTTLE_ACCOUNT_THRESHOLD, now)
            await self._audit.record(AuditAction.MFA_FAILED, actor_id=user.id, request=request)
            await self._commit()
            raise InvalidCredentialsError("The code is incorrect.")

        challenge.used_at = now
        await self._throttles.delete(key)
        result = await self._finish_sign_in(
            user, SignInMethod(challenge.sign_in_method), request, second_factor_done=True
        )
        assert isinstance(result, TokenPair)
        return result

    # --- refresh / sign out -----------------------------------------------------------------

    async def refresh(self, raw_refresh_token: str, request: RequestInfo) -> TokenPair:
        now = utcnow()
        token = await self._sessions.get_refresh_token_for_update(hash_token(raw_refresh_token))
        ended = UnauthorizedError("The session has ended. Sign in again.")
        if token is None:
            raise ended
        session = token.session
        if session.revoked_at is not None:
            raise ended

        if token.used_at is not None:
            # An old refresh token came back: it was copied. Revoke the whole family so
            # neither the thief nor the victim can keep using this session.
            session.revoked_at = now
            session.revoke_reason = SessionEndReason.REFRESH_TOKEN_REUSE
            await self._audit.record(
                AuditAction.REFRESH_TOKEN_REUSED,
                actor_id=session.user_id,
                entity_type="session",
                entity_id=session.id,
                request=request,
            )
            await self._commit()
            raise ended

        user = session.user
        if (
            token.expires_at <= now
            or session.expires_at <= now
            or not user.is_active
            or user.deleted_at is not None
        ):
            raise ended

        token.used_at = now
        session.last_used_at = now
        method = SignInMethod(session.sign_in_method or SignInMethod.EMAIL)
        if SIGN_IN_POLICIES[policies.band_of(user)].sliding:
            session.expires_at = now + self._session_lifetime(user, method)
        # Fixed sessions (Grades 1-3) keep their original end: refreshing never extends them.
        pair = await self._issue_token_pair(user, session, now)
        await self._commit()
        return pair

    async def logout(self, actor: Actor, request: RequestInfo) -> None:
        session = await self._sessions.get(actor.session_id)
        if session is not None and session.revoked_at is None:
            session.revoked_at = utcnow()
            session.revoke_reason = SessionEndReason.LOGOUT
            await self._audit.record(
                AuditAction.LOGOUT,
                actor=actor,
                entity_type="session",
                entity_id=session.id,
                request=request,
            )
        await self._commit()

    # --- password reset, account activation, email change ---------------------------------

    async def forgot_password(self, email: str) -> None:
        now = utcnow()
        user = await self._users.get_by_email(normalize_email(email))
        if (
            user is not None
            and user.email is not None
            and user.is_active
            and user.deleted_at is None
            and not await self._recently_sent(user, TokenPurpose.RESET_PASSWORD, now)
        ):
            raw, _ = await self._issue_token(user, TokenPurpose.RESET_PASSWORD, now)
            self._queue_email(
                emails.reset_password(
                    user.email, user.display_name, raw, self._settings.frontend_url
                )
            )
        await self._commit()

    async def reset_password(self, raw_token: str, new_password: str, request: RequestInfo) -> None:
        """Completes both "forgot password" and a child's account activation."""
        now = utcnow()
        token = await self._consume_token(
            raw_token, {TokenPurpose.RESET_PASSWORD, TokenPurpose.ACTIVATE_ACCOUNT}, now
        )
        user = await self._users.get_with_profiles(token.user_id)
        assert user is not None
        check_password_policy(
            new_password,
            user.email,
            min_length=SIGN_IN_POLICIES[policies.band_of(user)].password_min_length,
        )
        user.password_hash = self._hasher.hash(new_password)
        # The token arrived by email, so the address is proven.
        user.email_verified_at = user.email_verified_at or now
        await self._tokens.invalidate_unused(
            user.id, [TokenPurpose.RESET_PASSWORD, TokenPurpose.ACTIVATE_ACCOUNT], now
        )
        await self._sessions.revoke_all(user.id, reason=SessionEndReason.PASSWORD_RESET, now=now)
        if user.email:
            await self._throttles.delete(hash_throttle_key("account", user.email))
        await self._audit.record(
            AuditAction.PASSWORD_RESET,
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request=request,
        )
        if token.purpose == TokenPurpose.RESET_PASSWORD and user.email:
            self._queue_email(emails.password_changed_notice(user.email, user.display_name))
        await self._commit()

    async def confirm_email_change(self, raw_token: str, request: RequestInfo) -> None:
        now = utcnow()
        token = await self._consume_token(raw_token, {TokenPurpose.CHANGE_EMAIL}, now)
        user = token.user
        new_email = token.new_email
        assert new_email is not None
        if await self._users.email_taken(new_email):
            raise ConflictError("This email address is already in use.")
        old_email = user.email
        user.email = new_email
        user.email_verified_at = now
        await self._audit.record(
            AuditAction.EMAIL_CHANGED,
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request=request,
        )
        if old_email:
            self._queue_email(emails.email_changed_notice(old_email, user.display_name))
        async with conflict_on_integrity_error(
            self._session, "This email address is already in use."
        ):
            await self._commit()


class SecretBoxCache:
    """One SecretBox per key (key derivation is not free). Keyed by a hash of the key
    material, never by object identity: ids are reused after garbage collection."""

    _boxes: ClassVar[dict[str, totp.SecretBox]] = {}

    @classmethod
    def get(cls, settings: Settings) -> totp.SecretBox:
        material = (settings.encryption_key or settings.secret_key).get_secret_value()
        cache_key = hashlib.sha256(material.encode()).hexdigest()
        box = cls._boxes.get(cache_key)
        if box is None:
            box = cls._boxes[cache_key] = totp.SecretBox(settings)
        return box


class OAuthService(_IdentityBase):
    """Sign in with Google, Microsoft or Apple (BE-AUTH-11).

    1. ``start`` returns the provider URL (the web app sends the browser there).
    2. The provider redirects the browser to ``callback`` (on this API). It checks
       everything, then redirects to ``{frontend_url}/auth/complete?code=...`` (or
       ``?error=...``). No tokens ever travel in a URL.
    3. The web app posts that one-time code to ``exchange`` and receives the tokens (or a
       two-factor challenge), like any other sign-in.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        hasher: PasswordHasher,
        mailer: Mailer,
        audit: AuditLog,
        providers: OAuthProviders,
    ) -> None:
        super().__init__(session, settings, hasher, mailer, audit)
        self._providers = providers

    def enabled_providers(self) -> list[str]:
        return self._providers.enabled()

    async def start(
        self,
        provider_name: str,
        *,
        intent: str,
        role: str | None = None,
        actor: Actor | None = None,
    ) -> str:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise NotFoundError("This sign-in provider is not available.")
        if intent == "link":
            if actor is None:
                raise UnauthorizedError("Sign in first to link an account.")
            policies.ensure_not_impersonated(actor)
        if intent == "signup" and (role is None or Role(role) not in SELF_REGISTRATION_ROLES):
            raise DomainValidationError("Choose `parent` or `teacher` to sign up.")

        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        await self._signin.add(
            OAuthState(
                state_hash=hash_token(state),
                provider=provider_name,
                intent=intent,
                signup_role=role if intent == "signup" else None,
                user_id=actor.user_id if intent == "link" and actor else None,
                code_verifier=verifier,
                nonce=nonce,
                expires_at=utcnow() + OAUTH_STATE_TTL,
            )
        )
        await self._commit()
        return provider.authorization_url(state=state, nonce=nonce, code_challenge=challenge)

    def _complete_url(self, **params: str) -> str:
        return f"{self._settings.frontend_url.rstrip('/')}/auth/complete?{urlencode(params)}"

    async def callback(
        self,
        provider_name: str,
        *,
        code: str | None,
        state: str | None,
        error: str | None,
        extra: dict[str, Any],
        request: RequestInfo,
    ) -> str:
        """Returns where to send the browser next. Never raises for provider problems:
        the web app shows ``error`` (a short machine-readable reason)."""
        provider = self._providers.get(provider_name)
        if provider is None:
            return self._complete_url(error="provider_disabled")
        now = utcnow()
        attempt = await self._signin.get_state_for_update(hash_token(state)) if state else None
        if (
            attempt is None
            or attempt.provider != provider_name
            or attempt.used_at is not None
            or attempt.expires_at <= now
        ):
            return self._complete_url(error="invalid_state")
        attempt.used_at = now  # single use, whatever happens next
        if error or not code:
            await self._commit()
            return self._complete_url(error="cancelled" if error else "invalid_request")

        try:
            id_token = await provider.exchange_code(code, attempt.code_verifier)
            ident = await provider.verify_id_token(id_token, nonce=attempt.nonce, extra=extra)
        except OAuthError as exc:
            logger.warning("social sign-in failed (provider=%s): %s", provider_name, exc)
            await self._commit()
            return self._complete_url(error="provider_error")

        method = SignInMethod(provider_name)
        outcome = await self._resolve_account(attempt, method, ident, request, now)
        if isinstance(outcome, str):  # an error reason, or the "linked" redirect
            await self._commit()
            return outcome
        user = outcome

        raw = new_opaque_token()
        await self._signin.add(
            LoginChallenge(
                user_id=user.id,
                kind=ChallengeKind.OAUTH_HANDOFF,
                token_hash=hash_token(raw),
                sign_in_method=method.value,
                expires_at=now + OAUTH_HANDOFF_TTL,
            )
        )
        await self._commit()
        return self._complete_url(code=raw)

    async def _resolve_account(
        self,
        attempt: OAuthState,
        method: SignInMethod,
        ident: ProviderIdentity,
        request: RequestInfo,
        now: datetime,
    ) -> User | str:
        provider = method.value
        existing = await self._signin.get_identity(provider, ident.subject)

        if attempt.intent == "link":
            assert attempt.user_id is not None
            user = await self._users.get_with_profiles(attempt.user_id)
            if user is None or not user.is_active:
                return self._complete_url(error="account_inactive")
            if existing is not None and existing.user_id != user.id:
                return self._complete_url(error="identity_in_use")
            if method not in SIGN_IN_POLICIES[policies.band_of(user)].methods:
                return self._complete_url(error="method_not_allowed")
            if existing is None:
                await self._link(user, method, ident.subject, request, auto=False)
            return self._complete_url(linked=provider)

        if existing is not None:
            existing.last_used_at = now
            user = existing.user
        elif ident.email and ident.email_trusted:
            email = normalize_email(ident.email)
            found = await self._users.get_by_email(email)
            if found is not None:
                user = found
                if user.is_active and method in SIGN_IN_POLICIES[policies.band_of(user)].methods:
                    # The provider proved the address, so this is the account's owner.
                    await self._link(user, method, ident.subject, request, auto=True)
                    user.email_verified_at = user.email_verified_at or now
            elif attempt.intent == "signup" and attempt.signup_role:
                user = await self._create_social_account(attempt, method, ident, email, request)
            else:
                return self._complete_url(error="no_account")
        else:
            # Unverified (or no) email: never matched to an account. Sign up with email and
            # password first, then link this provider from the account settings.
            return self._complete_url(error="email_not_verified")

        if not user.is_active or user.deleted_at is not None:
            return self._complete_url(error="account_inactive")
        if method not in SIGN_IN_POLICIES[policies.band_of(user)].methods:
            return self._complete_url(error="method_not_allowed")
        return user

    async def _link(
        self, user: User, method: SignInMethod, subject: str, request: RequestInfo, *, auto: bool
    ) -> None:
        await self._signin.add(
            OAuthIdentity(
                user_id=user.id, provider=method.value, subject=subject, last_used_at=utcnow()
            )
        )
        await self._audit.record(
            AuditAction.OAUTH_LINKED,
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request=request,
            details={"provider": method.value, "automatic": auto},
        )
        if user.email:
            self._queue_email(emails.identity_linked(user.email, user.display_name, method.value))

    async def _create_social_account(
        self,
        attempt: OAuthState,
        method: SignInMethod,
        ident: ProviderIdentity,
        email: str,
        request: RequestInfo,
    ) -> User:
        assert attempt.signup_role is not None
        user = User(
            email=email,
            password_hash=None,  # can set one later with "forgot password"
            display_name=(ident.name or email.split("@", 1)[0])[:100],
            role=Role(attempt.signup_role),
            email_verified_at=utcnow(),  # the provider verified it
        )
        await self._users.add(user)
        if user.role == Role.TEACHER:
            await self._users.add(TeacherProfile(user_id=user.id))
        await self._signin.add(
            OAuthIdentity(
                user_id=user.id, provider=method.value, subject=ident.subject, last_used_at=utcnow()
            )
        )
        await self._audit.record(
            AuditAction.USER_REGISTERED,
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            request=request,
            details={"role": user.role.value, "method": method.value},
        )
        loaded = await self._users.get_with_profiles(user.id)
        assert loaded is not None
        return loaded

    async def exchange(self, code: str, request: RequestInfo) -> LoginResult:
        now = utcnow()
        handoff = await self._signin.get_challenge_for_update(hash_token(code))
        if (
            handoff is None
            or handoff.kind != ChallengeKind.OAUTH_HANDOFF
            or handoff.used_at is not None
            or handoff.expires_at <= now
        ):
            raise InvalidTokenError("This sign-in has expired. Start again.")
        handoff.used_at = now
        user = handoff.user
        if not user.is_active or user.deleted_at is not None:
            await self._commit()
            raise ForbiddenError("This account has been deactivated.")
        return await self._finish_sign_in(user, SignInMethod(handoff.sign_in_method), request)

    async def list_linked(self, actor: Actor) -> Sequence[OAuthIdentity]:
        return await self._signin.identities_of(actor.user_id)

    async def unlink(self, actor: Actor, provider: str, request: RequestInfo) -> None:
        policies.ensure_not_impersonated(actor)
        user = await self._users.get_with_profiles(actor.user_id)
        assert user is not None
        linked = [i.provider for i in await self._signin.identities_of(user.id)]
        policies.ensure_can_unlink(user, provider, linked)
        await self._signin.delete_identity(user.id, provider)
        await self._audit.record(
            AuditAction.OAUTH_UNLINKED,
            actor=actor,
            entity_type="user",
            entity_id=user.id,
            request=request,
            details={"provider": provider},
        )
        await self._commit()


class AccountService(_IdentityBase):
    """The signed-in user's own account (BE-USERS-01), sessions (BE-AUTH-09), 2FA."""

    async def get_me(self, actor: Actor) -> MeView:
        user = await self._users.get_with_profiles(actor.user_id)
        if user is None:
            raise NotFoundError("User not found.")
        mfa = await self._signin.get_mfa(user.id)
        linked = [i.provider for i in await self._signin.identities_of(user.id)]
        return MeView(
            user=user,
            session=await self._sessions.get(actor.session_id),
            mfa_enabled=bool(mfa and mfa.is_enabled),
            linked_providers=linked,
        )

    async def update_me(self, actor: Actor, data: MeUpdate) -> MeView:
        user = await self._users.get_with_profiles(actor.user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if data.display_name is not None:
            user.display_name = data.display_name.strip()
        if data.locale is not None:
            user.locale = data.locale
        if data.timezone is not None:
            user.timezone = data.timezone
        await self._session.flush()
        await self._commit()
        return await self.get_me(actor)

    async def change_password(
        self, actor: Actor, current_password: str, new_password: str, request: RequestInfo
    ) -> None:
        policies.ensure_not_impersonated(actor)
        user = await self._users.get_with_profiles(actor.user_id)
        if user is None or not self._hasher.verify(user.password_hash, current_password):
            raise InvalidCredentialsError("The password is incorrect.")
        check_password_policy(
            new_password,
            user.email,
            min_length=SIGN_IN_POLICIES[policies.band_of(user)].password_min_length,
        )
        now = utcnow()
        user.password_hash = self._hasher.hash(new_password)
        # Other devices are signed out; this one stays signed in.
        await self._sessions.revoke_all(
            user.id,
            reason=SessionEndReason.PASSWORD_CHANGED,
            now=now,
            except_session_id=actor.session_id,
        )
        await self._audit.record(
            AuditAction.PASSWORD_CHANGED,
            actor=actor,
            entity_type="user",
            entity_id=user.id,
            request=request,
        )
        if user.email:
            self._queue_email(emails.password_changed_notice(user.email, user.display_name))
        await self._commit()

    async def request_email_change(
        self, actor: Actor, new_email: str, password: str | None
    ) -> None:
        user = await self._confirm_owner(actor, password)
        new_email = normalize_email(new_email)
        if new_email == user.email:
            raise DomainValidationError("This is already your email address.")
        if policies.band_of(user) == AgeBand.EARLY:
            raise ForbiddenError("Children in Grades 1 to 3 do not sign in with an email.")
        # A taken address gets the same response and no email (no enumeration).
        if not await self._users.email_taken(new_email):
            raw, _ = await self._issue_token(
                user, TokenPurpose.CHANGE_EMAIL, utcnow(), new_email=new_email
            )
            self._queue_email(
                emails.confirm_new_email(
                    new_email, user.display_name, raw, self._settings.frontend_url
                )
            )
        await self._commit()

    async def delete_account(
        self, actor: Actor, password: str | None, request: RequestInfo
    ) -> None:
        """Anonymise rather than delete (Phase 13): personal data is scrubbed, the row stays
        for audit references. Children this parent manages alone are anonymised too."""
        user = await self._confirm_owner(actor, password)
        now = utcnow()
        for link in await self._users.links_of(user.id):
            if (
                link.parent_id == user.id
                and link.is_managed
                and link.status == LinkStatus.ACTIVE
                and await self._users.count_active_parents(link.child_id, excluding=user.id) == 0
            ):
                child = await self._users.get_with_profiles(link.child_id)
                if child is not None and child.email is None:
                    self._anonymize(child, now)
                    await self._sessions.revoke_all(
                        child.id, reason=SessionEndReason.ACCOUNT_DELETED, now=now
                    )
            link.status = LinkStatus.REVOKED

        self._anonymize(user, now)
        await self._signin.delete_mfa(user.id)
        for identity in await self._signin.identities_of(user.id):
            await self._signin.delete_identity(user.id, identity.provider)
        await self._tokens.invalidate_unused(user.id, list(TokenPurpose), now)
        await self._sessions.revoke_all(user.id, reason=SessionEndReason.ACCOUNT_DELETED, now=now)
        await self._audit.record(
            AuditAction.ACCOUNT_DELETED,
            actor=actor,
            entity_type="user",
            entity_id=user.id,
            request=request,
        )
        await self._commit()

    async def list_sessions(self, actor: Actor) -> Sequence[Session]:
        return await self._sessions.list_active(actor.user_id, utcnow())

    async def revoke_session(
        self, actor: Actor, session_id: uuid.UUID, request: RequestInfo
    ) -> None:
        policies.ensure_not_impersonated(actor)
        session = await self._sessions.get(session_id)
        if session is None or session.user_id != actor.user_id:
            raise NotFoundError("Session not found.")
        if session.revoked_at is None:
            session.revoked_at = utcnow()
            session.revoke_reason = SessionEndReason.REVOKED_BY_USER
            await self._audit.record(
                AuditAction.SESSION_REVOKED,
                actor=actor,
                entity_type="session",
                entity_id=session.id,
                request=request,
            )
        await self._commit()

    async def revoke_other_sessions(self, actor: Actor, request: RequestInfo) -> int:
        """Sign out on every other device."""
        policies.ensure_not_impersonated(actor)
        count = await self._sessions.revoke_all(
            actor.user_id,
            reason=SessionEndReason.REVOKED_BY_USER,
            now=utcnow(),
            except_session_id=actor.session_id,
        )
        await self._audit.record(
            AuditAction.OTHER_SESSIONS_REVOKED,
            actor=actor,
            entity_type="user",
            entity_id=actor.user_id,
            request=request,
            details={"sessions": count},
        )
        await self._commit()
        return count

    async def update_teacher_profile(
        self, actor: Actor, data: TeacherProfileUpdate
    ) -> TeacherProfile:
        policies.ensure_teacher(actor)
        profile = await self._users.get_teacher_profile(actor.user_id)
        if profile is None:
            profile = TeacherProfile(user_id=actor.user_id, subjects=[])
            await self._users.add(profile)
        if data.bio is not None:
            profile.bio = data.bio.strip() or None
        if data.subject_ids is not None:
            subjects = [await self._session.get(Subject, sid) for sid in set(data.subject_ids)]
            if any(s is None for s in subjects):
                raise NotFoundError("Subject not found.")
            # Same order as when the profile is loaded (relationship order_by=name); the
            # de-duplicating set above would otherwise make the response order random.
            profile.subjects = sorted((s for s in subjects if s is not None), key=lambda s: s.name)
        await self._session.flush()
        await self._commit()
        return profile

    # --- two-factor authentication (BE-AUTH-12) -------------------------------------------

    async def start_totp_setup(self, actor: Actor) -> tuple[str, str]:
        """A new secret for the authenticator app. 2FA stays off until confirmed."""
        policies.ensure_can_use_totp(actor)
        user = await self._users.get(actor.user_id)
        assert user is not None
        existing = await self._signin.get_mfa_for_update(user.id)
        if existing is not None and existing.is_enabled:
            raise ConflictError("Two-factor authentication is already on.")
        secret = totp.new_secret()
        encrypted = SecretBoxCache.get(self._settings).encrypt(secret)
        if existing is None:
            await self._signin.add(UserMfa(user_id=user.id, secret_encrypted=encrypted))
        else:
            existing.secret_encrypted = encrypted
            existing.last_used_step = None
        await self._commit()
        return secret, totp.provisioning_uri(secret, user.email or user.display_name)

    async def confirm_totp(self, actor: Actor, code: str, request: RequestInfo) -> list[str]:
        """Turns 2FA on with a first valid code; returns the recovery codes (shown once)."""
        policies.ensure_can_use_totp(actor)
        mfa = await self._signin.get_mfa_for_update(actor.user_id)
        if mfa is None or mfa.is_enabled:
            raise ConflictError("Start the two-factor set-up first.")
        secret = SecretBoxCache.get(self._settings).decrypt(mfa.secret_encrypted)
        step = totp.matching_step(secret, code, last_used_step=None) if secret else None
        if step is None:
            raise InvalidCredentialsError("The code is incorrect. Check the time on your phone.")
        mfa.confirmed_at = utcnow()
        mfa.last_used_step = step
        codes = await self._new_recovery_codes(actor.user_id)
        await self._audit.record(
            AuditAction.MFA_ENABLED, actor=actor, entity_type="user", entity_id=actor.user_id,
            request=request,
        )  # fmt: skip
        user = await self._users.get(actor.user_id)
        if user is not None and user.email:
            self._queue_email(emails.two_factor_enabled(user.email, user.display_name))
        await self._commit()
        return codes

    async def _check_second_factor(self, user_id: uuid.UUID, code: str) -> bool:
        """A current app code or an unused recovery code (which is then used up)."""
        mfa = await self._signin.get_mfa_for_update(user_id)
        if mfa is None or not mfa.is_enabled:
            return False
        secret = SecretBoxCache.get(self._settings).decrypt(mfa.secret_encrypted)
        step = (
            totp.matching_step(secret, code, last_used_step=mfa.last_used_step) if secret else None
        )
        if step is not None:
            mfa.last_used_step = step
            return True
        stored = await self._signin.get_recovery_code_for_update(
            user_id, hash_token(totp.normalize_recovery_code(code))
        )
        if stored is not None and stored.used_at is None:
            stored.used_at = utcnow()
            return True
        return False

    async def disable_totp(
        self, actor: Actor, code: str, password: str | None, request: RequestInfo
    ) -> None:
        user = await self._confirm_owner(actor, password)
        if not await self._check_second_factor(user.id, code):
            raise InvalidCredentialsError("The code is incorrect.")
        await self._signin.delete_mfa(user.id)
        await self._audit.record(
            AuditAction.MFA_DISABLED, actor=actor, entity_type="user", entity_id=user.id,
            request=request,
        )  # fmt: skip
        if user.email:
            self._queue_email(emails.two_factor_disabled(user.email, user.display_name))
        await self._commit()

    async def regenerate_recovery_codes(
        self, actor: Actor, code: str, request: RequestInfo
    ) -> list[str]:
        policies.ensure_not_impersonated(actor)
        if not await self._check_second_factor(actor.user_id, code):
            raise InvalidCredentialsError("The code is incorrect.")
        codes = await self._new_recovery_codes(actor.user_id)
        await self._audit.record(
            AuditAction.RECOVERY_CODES_REGENERATED, actor=actor, request=request
        )
        await self._commit()
        return codes

    async def _new_recovery_codes(self, user_id: uuid.UUID) -> list[str]:
        codes = [totp.new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        await self._signin.replace_recovery_codes(user_id, [hash_token(c) for c in codes])
        return codes


class StudentAccountService(_IdentityBase):
    """Creating child accounts and setting up how they sign in (BE-AUTH-10). Used by
    parents (FamilyService) and teachers (classes module); each caller checks its own
    ownership policy first."""

    async def create_student(
        self,
        creator: Actor,
        *,
        display_name: str,
        grade_level: int | None,
        date_of_birth: Any = None,
        school_name: str | None = None,
        email: str | None = None,
        sign_in: StudentSignInUpdate | None = None,
        request: RequestInfo,
    ) -> tuple[User, str | None]:
        """Returns the student and, if an email was given, the raw activation token."""
        creator_user = await self._users.get(creator.user_id)
        assert creator_user is not None
        email = normalize_email(email) if email else None
        band = age_band(Role.STUDENT, grade_level)
        if email and sign_in is not None:
            raise DomainValidationError(
                "With an email address, the child chooses their own password from the "
                "invitation email: leave username, password and pictures empty."
            )
        if email and band == AgeBand.EARLY:
            raise DomainValidationError(
                "Children in Grades 1 to 3 sign in with pictures or a username, not an email."
            )
        if email and await self._users.email_taken(email):
            raise ConflictError("This email address cannot be used for a new account.")
        now = utcnow()
        child = User(
            email=email,
            password_hash=None,
            display_name=display_name.strip(),
            role=Role.STUDENT,
            locale=creator_user.locale,
            timezone=creator_user.timezone,
        )
        await self._users.add(child)
        profile = StudentProfile(
            user_id=child.id,
            grade_level=grade_level,
            date_of_birth=date_of_birth,
            school_name=school_name,
        )
        await self._users.add(profile)
        loaded = await self._users.get_with_profiles(child.id)
        assert loaded is not None
        if sign_in is not None:
            await self._apply_student_sign_in(loaded, sign_in, now)
        raw = None
        if email:
            raw, _ = await self._issue_token(loaded, TokenPurpose.ACTIVATE_ACCOUNT, now)
            self._queue_email(
                emails.activate_account(
                    email,
                    loaded.display_name,
                    creator_user.display_name,
                    raw,
                    self._settings.frontend_url,
                )
            )
        await self._audit.record(
            AuditAction.CHILD_ADDED,
            actor=creator,
            entity_type="user",
            entity_id=child.id,
            request=request,
            details={"with_email": bool(email), "band": band.value},
        )
        return loaded, raw

    async def update_sign_in(
        self, actor: Actor, student: User, data: StudentSignInUpdate, request: RequestInfo
    ) -> User:
        changed = await self._apply_student_sign_in(student, data, utcnow())
        await self._audit.record(
            AuditAction.STUDENT_SIGN_IN_UPDATED,
            actor=actor,
            entity_type="user",
            entity_id=student.id,
            request=request,
            details={"changed": changed},  # which fields, never the values
        )
        async with conflict_on_integrity_error(
            self._session, "This username is taken. Try adding a number."
        ):
            await self._commit()
        loaded = await self._users.get_with_profiles(student.id)
        assert loaded is not None
        return loaded

    async def get_student(self, student_id: uuid.UUID) -> User | None:
        return await self._users.get_with_profiles(student_id)

    async def commit(self) -> None:
        await self._commit()


class FamilyService(StudentAccountService):
    """Parents and their children (BE-USERS-02). Ownership rules live in policies.py."""

    async def list_children(self, actor: Actor) -> Sequence[ParentChildLink]:
        return await self._users.active_children(actor.user_id)

    async def add_child(
        self, actor: Actor, data: ChildCreate, request: RequestInfo
    ) -> ParentChildLink:
        sign_in = StudentSignInUpdate.model_construct(
            username=data.username, password=data.password, picture_password=data.picture_password
        )
        has_sign_in = any(
            v is not None for v in (data.username, data.password, data.picture_password)
        )
        async with conflict_on_integrity_error(
            self._session, "This email address or username cannot be used for a new account."
        ):
            child, _ = await self.create_student(
                actor,
                display_name=data.display_name,
                grade_level=data.grade_level,
                date_of_birth=data.date_of_birth,
                school_name=data.school_name,
                email=data.email,
                sign_in=sign_in if has_sign_in else None,
                request=request,
            )
            await self._users.add(
                ParentChildLink(parent_id=actor.user_id, child_id=child.id, is_managed=True)
            )
            await self._commit()
        return await self._link_with_child(actor.user_id, child.id)

    async def update_child_sign_in(
        self,
        actor: Actor,
        child_id: uuid.UUID,
        data: StudentSignInUpdate,
        request: RequestInfo,
    ) -> ParentChildLink:
        policies.ensure_parent_of(actor, await self._users.get_link(actor.user_id, child_id))
        student = await self._users.get_with_profiles(child_id)
        assert student is not None
        await self.update_sign_in(actor, student, data, request)
        return await self._link_with_child(actor.user_id, child_id)

    async def create_link_code(self, actor: Actor) -> tuple[str, datetime]:
        """A student creates a short code and gives it to a parent (the student's consent)."""
        policies.ensure_student(actor)
        student = await self._users.get(actor.user_id)
        assert student is not None
        code, token = await self._issue_token(student, TokenPurpose.PARENT_LINK, utcnow())
        await self._commit()
        return code, token.expires_at

    async def link_child(self, actor: Actor, code: str, request: RequestInfo) -> ParentChildLink:
        now = utcnow()
        token = await self._consume_token(
            normalize_link_code(code), {TokenPurpose.PARENT_LINK}, now
        )
        child = token.user
        if child.role != Role.STUDENT or child.id == actor.user_id:
            raise InvalidTokenError()
        link = await self._users.get_link(actor.user_id, child.id)
        if link is None:
            link = ParentChildLink(parent_id=actor.user_id, child_id=child.id, is_managed=False)
            await self._users.add(link)
        link.status = LinkStatus.ACTIVE
        await self._audit.record(
            AuditAction.CHILD_LINKED,
            actor=actor,
            entity_type="user",
            entity_id=child.id,
            request=request,
        )
        await self._commit()
        return await self._link_with_child(actor.user_id, child.id)

    async def set_active_child(self, actor: Actor, child_id: uuid.UUID | None) -> None:
        if child_id is not None:
            policies.ensure_parent_of(actor, await self._users.get_link(actor.user_id, child_id))
        session = await self._sessions.get(actor.session_id)
        assert session is not None
        session.active_child_id = child_id
        await self._commit()

    async def _link_with_child(self, parent_id: uuid.UUID, child_id: uuid.UUID) -> ParentChildLink:
        for link in await self._users.active_children(parent_id):
            if link.child_id == child_id:
                return link
        raise NotFoundError("Child not found.")


class AdminService(_IdentityBase):
    """Platform administration of users (BE-USERS-03). Every change is audited."""

    async def search_users(
        self,
        *,
        text: str | None,
        role: Role | None,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]:
        return await self._users.search(
            text=text.strip() if text else None,
            role=role,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get_with_profiles(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def update_user(
        self, actor: Actor, user_id: uuid.UUID, data: UserAdminUpdate, request: RequestInfo
    ) -> User:
        user = await self.get_user(user_id)
        policies.ensure_can_change_user(actor, user)
        now = utcnow()

        if data.role is not None and data.role != user.role:
            policies.ensure_assignable_role(user, data.role)
            previous = user.role
            user.role = data.role
            if data.role == Role.TEACHER and user.teacher_profile is None:
                user.teacher_profile = TeacherProfile(user_id=user.id)
            await self._audit.record(
                AuditAction.ROLE_CHANGED,
                actor=actor,
                entity_type="user",
                entity_id=user.id,
                request=request,
                details={"from": previous.value, "to": data.role.value},
            )

        if data.is_active is not None and data.is_active != user.is_active:
            user.is_active = data.is_active
            if not data.is_active:
                await self._sessions.revoke_all(
                    user.id, reason=SessionEndReason.ACCOUNT_DEACTIVATED, now=now
                )
            await self._audit.record(
                AuditAction.USER_REACTIVATED if data.is_active else AuditAction.USER_DEACTIVATED,
                actor=actor,
                entity_type="user",
                entity_id=user.id,
                request=request,
            )
        await self._commit()
        return await self.get_user(user_id)

    async def set_teacher_verification(
        self, actor: Actor, user_id: uuid.UUID, verified: bool, request: RequestInfo
    ) -> User:
        user = await self.get_user(user_id)
        if user.role != Role.TEACHER or user.teacher_profile is None:
            raise DomainValidationError("Only teacher accounts can be verified.")
        profile = user.teacher_profile
        if verified != profile.is_verified:
            profile.verified_at = utcnow() if verified else None
            profile.verified_by_id = actor.user_id if verified else None
            await self._audit.record(
                AuditAction.TEACHER_VERIFIED if verified else AuditAction.TEACHER_UNVERIFIED,
                actor=actor,
                entity_type="user",
                entity_id=user.id,
                request=request,
            )
        await self._commit()
        return await self.get_user(user_id)

    async def reset_mfa(self, actor: Actor, user_id: uuid.UUID, request: RequestInfo) -> None:
        """For a user who lost both their phone and their recovery codes."""
        user = await self.get_user(user_id)
        if user.id == actor.user_id:
            raise ForbiddenError("Use your own security settings to change your 2FA.")
        mfa = await self._signin.get_mfa(user.id)
        if mfa is None:
            raise NotFoundError("This user has no two-factor authentication.")
        await self._signin.delete_mfa(user.id)
        await self._sessions.revoke_all(
            user.id, reason=SessionEndReason.PASSWORD_RESET, now=utcnow()
        )
        await self._audit.record(
            AuditAction.MFA_RESET_BY_ADMIN,
            actor=actor,
            entity_type="user",
            entity_id=user.id,
            request=request,
        )
        if user.email:
            self._queue_email(emails.two_factor_disabled(user.email, user.display_name))
        await self._commit()

    async def impersonate(
        self, actor: Actor, user_id: uuid.UUID, request: RequestInfo
    ) -> tuple[str, int]:
        """A short-lived access token for support, with no refresh token. The token carries
        the admin's id (``act`` claim) and everything done with it is audited as such."""
        user = await self.get_user(user_id)
        policies.ensure_can_impersonate(actor, user)
        now = utcnow()
        session = await self._start_session(
            user, request, now, method=SignInMethod.IMPERSONATION, impersonator_id=actor.user_id
        )
        ttl = session.expires_at - now
        token = create_access_token(
            self._settings,
            user_id=user.id,
            session_id=session.id,
            role=user.role,
            ttl=ttl,
            impersonator_id=actor.user_id,
            now=now,
        )
        await self._audit.record(
            AuditAction.IMPERSONATION_STARTED,
            actor=actor,
            entity_type="user",
            entity_id=user.id,
            request=request,
            details={"session_id": str(session.id), "minutes": ttl.total_seconds() // 60},
        )
        await self._commit()
        return token, int(ttl.total_seconds())
