"""Wiring, plus the authentication dependencies other modules use (BE-AUTHZ-01):

actor: CurrentActor                         401 unless signed in
actor: OptionalActor                        None when anonymous
actor: Annotated[Actor, Depends(require_permission(Permission.X))]   403 without it
actor: Annotated[Actor, Depends(require_role(Role.X, ...))]          403 otherwise
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import MailerDep, SessionDep, SettingsDep
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditLog
from app.modules.identity.domain import Actor, Permission, RequestInfo, Role
from app.modules.identity.oauth import OAuthProviders
from app.modules.identity.security import PasswordHasher
from app.modules.identity.service import (
    AccountService,
    AdminService,
    AuthService,
    FamilyService,
    OAuthService,
    StudentAccountService,
)

_bearer = HTTPBearer(
    auto_error=False,
    description="Access token from `POST /api/v1/auth/login` (expires after 15 minutes).",
)
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def get_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


def get_audit_log(session: SessionDep) -> AuditLog:
    return AuditLog(AuditRepository(session))


def get_request_info(request: Request) -> RequestInfo:
    # Behind a reverse proxy, run uvicorn with --proxy-headers (and --forwarded-allow-ips)
    # so this is the client's address rather than the proxy's.
    return RequestInfo(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def get_oauth_providers(request: Request) -> OAuthProviders:
    providers: OAuthProviders = request.app.state.oauth_providers
    return providers


HasherDep = Annotated[PasswordHasher, Depends(get_hasher)]
OAuthProvidersDep = Annotated[OAuthProviders, Depends(get_oauth_providers)]
AuditLogDep = Annotated[AuditLog, Depends(get_audit_log)]
RequestInfoDep = Annotated[RequestInfo, Depends(get_request_info)]


def get_auth_service(
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
    mailer: MailerDep,
    audit: AuditLogDep,
) -> AuthService:
    return AuthService(session, settings, hasher, mailer, audit)


def get_account_service(
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
    mailer: MailerDep,
    audit: AuditLogDep,
) -> AccountService:
    return AccountService(session, settings, hasher, mailer, audit)


def get_family_service(
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
    mailer: MailerDep,
    audit: AuditLogDep,
) -> FamilyService:
    return FamilyService(session, settings, hasher, mailer, audit)


def get_admin_service(
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
    mailer: MailerDep,
    audit: AuditLogDep,
) -> AdminService:
    return AdminService(session, settings, hasher, mailer, audit)


def get_oauth_service(
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
    mailer: MailerDep,
    audit: AuditLogDep,
    providers: OAuthProvidersDep,
) -> OAuthService:
    return OAuthService(session, settings, hasher, mailer, audit, providers)


def get_student_account_service(
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
    mailer: MailerDep,
    audit: AuditLogDep,
) -> StudentAccountService:
    return StudentAccountService(session, settings, hasher, mailer, audit)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
OAuthServiceDep = Annotated[OAuthService, Depends(get_oauth_service)]
StudentAccountServiceDep = Annotated[StudentAccountService, Depends(get_student_account_service)]
AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]
FamilyServiceDep = Annotated[FamilyService, Depends(get_family_service)]
AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


# --- authentication -----------------------------------------------------------------------


async def get_optional_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    auth: AuthServiceDep,
) -> Actor | None:
    """None for anonymous requests. A token that is present but invalid or expired is a
    401 even on public endpoints, so clients learn they must refresh it."""
    if credentials is None:
        return None
    try:
        return await auth.authenticate(credentials.credentials)
    except UnauthorizedError as exc:
        raise UnauthorizedError(exc.message, headers=_WWW_AUTHENTICATE) from exc


async def get_current_actor(
    actor: Annotated[Actor | None, Depends(get_optional_actor)],
) -> Actor:
    if actor is None:
        raise UnauthorizedError("Sign in to continue.", headers=_WWW_AUTHENTICATE)
    return actor


OptionalActor = Annotated[Actor | None, Depends(get_optional_actor)]
CurrentActor = Annotated[Actor, Depends(get_current_actor)]


def require_permission(*permissions: Permission) -> Callable[[Actor], Awaitable[Actor]]:
    """Dependency factory: the signed-in user must hold every listed permission."""

    async def dependency(actor: CurrentActor) -> Actor:
        if not all(actor.can(p) for p in permissions):
            raise ForbiddenError("You do not have permission to do this.")
        return actor

    return dependency


def require_role(*roles: Role) -> Callable[[Actor], Awaitable[Actor]]:
    """Dependency factory: the signed-in user must have one of the listed roles."""

    async def dependency(actor: CurrentActor) -> Actor:
        if actor.role not in roles:
            raise ForbiddenError("Your account type cannot do this.")
        return actor

    return dependency
