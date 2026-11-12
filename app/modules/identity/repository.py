import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.modules.identity.domain import LinkStatus, Role, TokenPurpose
from app.modules.identity.models import (
    LoginChallenge,
    LoginThrottle,
    MfaRecoveryCode,
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


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class UserRepository:
    """Data access only: no business rules, no commits (the service owns the transaction)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, obj: object) -> None:
        self._session.add(obj)
        await self._session.flush()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_with_profiles(self, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .options(
                joinedload(User.student_profile),
                joinedload(User.teacher_profile).selectinload(TeacherProfile.subjects),
            )
            .where(User.id == user_id)
            # Refresh objects already in the session (e.g. a profile added in this request).
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def get_by_email(self, email: str) -> User | None:
        # The student profile decides the age band (which sign-in methods are allowed).
        stmt = select(User).options(joinedload(User.student_profile)).where(User.email == email)
        return await self._session.scalar(stmt)

    async def get_by_username(self, username: str) -> User | None:
        stmt = (
            select(User).options(joinedload(User.student_profile)).where(User.username == username)
        )
        return await self._session.scalar(stmt)

    async def email_taken(self, email: str) -> bool:
        stmt = select(select(User.id).where(User.email == email).exists())
        return bool(await self._session.scalar(stmt))

    async def username_taken(self, username: str, *, excluding: uuid.UUID | None = None) -> bool:
        query = select(User.id).where(User.username == username)
        if excluding is not None:
            query = query.where(User.id != excluding)
        return bool(await self._session.scalar(select(query.exists())))

    async def get_student_profile(self, user_id: uuid.UUID) -> StudentProfile | None:
        return await self._session.get(StudentProfile, user_id)

    async def get_teacher_profile(self, user_id: uuid.UUID) -> TeacherProfile | None:
        stmt = (
            select(TeacherProfile)
            .options(selectinload(TeacherProfile.subjects))
            .where(TeacherProfile.user_id == user_id)
        )
        return await self._session.scalar(stmt)

    async def search(
        self,
        *,
        text: str | None,
        role: Role | None,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]:
        conditions = []
        if text:
            pattern = f"%{_escape_like(text)}%"
            conditions.append(
                or_(
                    User.email.ilike(pattern, escape="\\"),
                    User.display_name.ilike(pattern, escape="\\"),
                )
            )
        if role is not None:
            conditions.append(User.role == role)
        if is_active is not None:
            conditions.append(User.is_active.is_(is_active))

        total = await self._session.scalar(select(func.count(User.id)).where(*conditions))
        stmt = (
            select(User)
            .options(joinedload(User.teacher_profile))
            .where(*conditions)
            .order_by(User.created_at.desc(), User.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(stmt)).unique().all(), total or 0

    # --- parent/child links ---------------------------------------------------------------

    async def get_link(self, parent_id: uuid.UUID, child_id: uuid.UUID) -> ParentChildLink | None:
        stmt = select(ParentChildLink).where(
            ParentChildLink.parent_id == parent_id, ParentChildLink.child_id == child_id
        )
        return await self._session.scalar(stmt)

    async def active_children(self, parent_id: uuid.UUID) -> Sequence[ParentChildLink]:
        stmt = (
            select(ParentChildLink)
            .options(joinedload(ParentChildLink.child).joinedload(User.student_profile))
            .where(
                ParentChildLink.parent_id == parent_id,
                ParentChildLink.status == LinkStatus.ACTIVE,
            )
            .order_by(ParentChildLink.created_at)
        )
        return (await self._session.scalars(stmt)).unique().all()

    async def links_of(self, user_id: uuid.UUID) -> Sequence[ParentChildLink]:
        """Every link where the user is the parent or the child."""
        stmt = select(ParentChildLink).where(
            or_(ParentChildLink.parent_id == user_id, ParentChildLink.child_id == user_id)
        )
        return (await self._session.scalars(stmt)).all()

    async def count_active_parents(
        self, child_id: uuid.UUID, *, excluding: uuid.UUID | None = None
    ) -> int:
        stmt = select(func.count(ParentChildLink.id)).where(
            ParentChildLink.child_id == child_id, ParentChildLink.status == LinkStatus.ACTIVE
        )
        if excluding is not None:
            stmt = stmt.where(ParentChildLink.parent_id != excluding)
        return int(await self._session.scalar(stmt) or 0)


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, obj: object) -> None:
        self._session.add(obj)
        await self._session.flush()

    async def get(self, session_id: uuid.UUID) -> Session | None:
        return await self._session.get(Session, session_id)

    async def get_for_auth(self, session_id: uuid.UUID) -> Session | None:
        """One query per authenticated request: session, user and teacher profile."""
        stmt = (
            select(Session)
            .options(joinedload(Session.user).joinedload(User.teacher_profile))
            .where(Session.id == session_id)
        )
        return await self._session.scalar(stmt)

    async def list_active(self, user_id: uuid.UUID, now: datetime) -> Sequence[Session]:
        stmt = (
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
            .order_by(Session.last_used_at.desc())
        )
        return (await self._session.scalars(stmt)).all()

    async def revoke_all(
        self,
        user_id: uuid.UUID,
        *,
        reason: str,
        now: datetime,
        except_session_id: uuid.UUID | None = None,
    ) -> int:
        stmt = (
            update(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=reason)
        )
        if except_session_id is not None:
            stmt = stmt.where(Session.id != except_session_id)
        result = await self._session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def get_refresh_token_for_update(self, token_hash: str) -> RefreshToken | None:
        # Row lock: two concurrent refreshes with the same token are serialised, so the
        # second one sees `used_at` and is treated as reuse.
        stmt = (
            select(RefreshToken)
            .options(
                joinedload(RefreshToken.session)
                .joinedload(Session.user)
                .joinedload(User.student_profile)
            )
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update(of=RefreshToken)
        )
        return await self._session.scalar(stmt)


class TokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: UserToken) -> None:
        self._session.add(token)
        await self._session.flush()

    async def get_for_update(self, token_hash: str) -> UserToken | None:
        stmt = (
            select(UserToken)
            .options(joinedload(UserToken.user))
            .where(UserToken.token_hash == token_hash)
            .with_for_update(of=UserToken)
        )
        return await self._session.scalar(stmt)

    async def latest_created_at(self, user_id: uuid.UUID, purpose: TokenPurpose) -> datetime | None:
        stmt = select(func.max(UserToken.created_at)).where(
            UserToken.user_id == user_id, UserToken.purpose == purpose
        )
        return await self._session.scalar(stmt)

    async def invalidate_unused(
        self, user_id: uuid.UUID, purposes: Sequence[TokenPurpose], now: datetime
    ) -> None:
        """Older unused tokens stop working as soon as a new one is issued."""
        await self._session.execute(
            update(UserToken)
            .where(
                UserToken.user_id == user_id,
                UserToken.purpose.in_(purposes),
                UserToken.used_at.is_(None),
            )
            .values(used_at=now)
        )


class ThrottleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock(self, key: str, now: datetime) -> LoginThrottle:
        """Return the counter row for ``key``, created if needed, locked for this transaction.

        INSERT ... ON CONFLICT makes concurrent first failures for the same key safe.
        """
        await self._session.execute(
            pg_insert(LoginThrottle)
            .values(key=key, failures=0, last_failure_at=now)
            .on_conflict_do_nothing(index_elements=[LoginThrottle.key])
        )
        stmt = (
            select(LoginThrottle)
            .where(LoginThrottle.key == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        throttle = await self._session.scalar(stmt)
        assert throttle is not None
        return throttle

    async def get_many(self, keys: Sequence[str]) -> Sequence[LoginThrottle]:
        return (
            await self._session.scalars(select(LoginThrottle).where(LoginThrottle.key.in_(keys)))
        ).all()

    async def delete(self, key: str) -> None:
        throttle = await self._session.get(LoginThrottle, key)
        if throttle is not None:
            await self._session.delete(throttle)


class SignInRepository:
    """Social identities, OAuth attempts, pending sign-ins (challenges), and 2FA data."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, obj: object) -> None:
        self._session.add(obj)
        await self._session.flush()

    # --- social identities ----------------------------------------------------------------

    async def get_identity(self, provider: str, subject: str) -> OAuthIdentity | None:
        stmt = (
            select(OAuthIdentity)
            .options(joinedload(OAuthIdentity.user).joinedload(User.student_profile))
            .where(OAuthIdentity.provider == provider, OAuthIdentity.subject == subject)
        )
        return await self._session.scalar(stmt)

    async def identities_of(self, user_id: uuid.UUID) -> Sequence[OAuthIdentity]:
        stmt = (
            select(OAuthIdentity)
            .where(OAuthIdentity.user_id == user_id)
            .order_by(OAuthIdentity.provider)
        )
        return (await self._session.scalars(stmt)).all()

    async def delete_identity(self, user_id: uuid.UUID, provider: str) -> int:
        result = await self._session.execute(
            delete(OAuthIdentity).where(
                OAuthIdentity.user_id == user_id, OAuthIdentity.provider == provider
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def get_state_for_update(self, state_hash: str) -> OAuthState | None:
        stmt = select(OAuthState).where(OAuthState.state_hash == state_hash).with_for_update()
        return await self._session.scalar(stmt)

    # --- pending sign-ins -----------------------------------------------------------------

    async def get_challenge_for_update(self, token_hash: str) -> LoginChallenge | None:
        stmt = (
            select(LoginChallenge)
            .options(joinedload(LoginChallenge.user).joinedload(User.student_profile))
            .where(LoginChallenge.token_hash == token_hash)
            .with_for_update(of=LoginChallenge)
        )
        return await self._session.scalar(stmt)

    # --- two-factor -----------------------------------------------------------------------

    async def get_mfa(self, user_id: uuid.UUID) -> UserMfa | None:
        return await self._session.get(UserMfa, user_id)

    async def get_mfa_for_update(self, user_id: uuid.UUID) -> UserMfa | None:
        stmt = select(UserMfa).where(UserMfa.user_id == user_id).with_for_update()
        return await self._session.scalar(stmt)

    async def delete_mfa(self, user_id: uuid.UUID) -> None:
        await self._session.execute(delete(UserMfa).where(UserMfa.user_id == user_id))
        await self._session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )

    async def replace_recovery_codes(self, user_id: uuid.UUID, hashes: Sequence[str]) -> None:
        await self._session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )
        self._session.add_all(MfaRecoveryCode(user_id=user_id, code_hash=h) for h in hashes)
        await self._session.flush()

    async def get_recovery_code_for_update(
        self, user_id: uuid.UUID, code_hash: str
    ) -> MfaRecoveryCode | None:
        stmt = (
            select(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.code_hash == code_hash)
            .with_for_update()
        )
        return await self._session.scalar(stmt)

    async def unused_recovery_codes(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count(MfaRecoveryCode.id)).where(
            MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
        )
        return int(await self._session.scalar(stmt) or 0)
