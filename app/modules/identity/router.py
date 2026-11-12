import json
import uuid
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.pagination import Page
from app.modules.identity.deps import (
    AccountServiceDep,
    AdminServiceDep,
    AuthServiceDep,
    CurrentActor,
    FamilyServiceDep,
    OAuthServiceDep,
    RequestInfoDep,
    require_permission,
)
from app.modules.identity.domain import ROLE_PERMISSIONS, Actor, Permission
from app.modules.identity.models import ParentChildLink, User
from app.modules.identity.policies import band_of
from app.modules.identity.schemas import (
    AcceptedResponse,
    ActiveChildRequest,
    ChangeEmailRequest,
    ChangePasswordRequest,
    ChildCreate,
    ChildRead,
    DeleteAccountRequest,
    EmailRequest,
    ImpersonationRead,
    LinkChildRequest,
    LinkCodeRead,
    LinkedIdentityRead,
    LoginRequest,
    LoginResponse,
    MeRead,
    MeUpdate,
    MfaLoginRequest,
    OAuthExchangeRequest,
    OAuthProvidersRead,
    OAuthStartRead,
    RecoveryCodesRead,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionRead,
    StudentProfileRead,
    StudentSignInUpdate,
    TeacherProfileRead,
    TeacherProfileUpdate,
    TeacherVerificationRequest,
    TokenPair,
    TokenRequest,
    TotpConfirmRequest,
    TotpDisableRequest,
    TotpSetupRead,
    UserAdminQuery,
    UserAdminRead,
    UserAdminUpdate,
    UsernameLoginRequest,
)
from app.modules.identity.service import MeView, sign_in_methods

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])

NO_CONTENT = status.HTTP_204_NO_CONTENT
# Same text whatever happened, so the response never reveals whether an address is known.
CHECK_YOUR_EMAIL = AcceptedResponse(
    message="If the address can receive it, we have sent an email with the next step."
)

ChildrenManager = Annotated[Actor, Depends(require_permission(Permission.CHILDREN_MANAGE))]
UsersManager = Annotated[Actor, Depends(require_permission(Permission.USERS_MANAGE))]
Impersonator = Annotated[Actor, Depends(require_permission(Permission.USERS_IMPERSONATE))]


# --- response builders --------------------------------------------------------------------


def _me(view: MeView, actor: Actor) -> MeRead:
    user, session = view.user, view.session
    return MeRead(
        id=user.id,
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        age_band=band_of(user),
        permissions=sorted(ROLE_PERMISSIONS[user.role]),
        sign_in_methods=sign_in_methods(user, view.linked_providers),
        mfa_enabled=view.mfa_enabled,
        locale=user.locale,
        timezone=user.timezone,
        email_verified=user.is_email_verified,
        created_at=user.created_at,
        student_profile=(
            StudentProfileRead.model_validate(user.student_profile)
            if user.student_profile
            else None
        ),
        teacher_profile=(
            TeacherProfileRead.model_validate(user.teacher_profile)
            if user.teacher_profile
            else None
        ),
        active_child_id=session.active_child_id if session else None,
        impersonated_by=actor.impersonator_id,
    )


def _child(link: ParentChildLink) -> ChildRead:
    child = link.child
    profile = child.student_profile
    return ChildRead(
        id=child.id,
        display_name=child.display_name,
        email=child.email,
        grade_level=profile.grade_level if profile else None,
        date_of_birth=profile.date_of_birth if profile else None,
        school_name=profile.school_name if profile else None,
        is_managed=link.is_managed,
        username=child.username,
        age_band=band_of(child),
        sign_in_methods=(methods := sign_in_methods(child)),
        can_sign_in=bool(methods) and child.is_active,
    )


def _admin_user(user: User) -> UserAdminRead:
    return UserAdminRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        email_verified=user.is_email_verified,
        teacher_verified=user.teacher_profile.is_verified if user.teacher_profile else None,
        deleted=user.deleted_at is not None,
        created_at=user.created_at,
    )


# --- /auth: public ------------------------------------------------------------------------


@auth_router.post(
    "/register",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an account (parent or teacher)",
    description="Always answers 202 with the same message. A new account gets a "
    "verification email; an address that already has an account gets a notice instead.",
)
async def register(
    payload: RegisterRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> AcceptedResponse:
    await auth.register(payload, request)
    return CHECK_YOUR_EMAIL


@auth_router.post("/verify-email", status_code=NO_CONTENT, summary="Confirm an email address")
async def verify_email(
    payload: TokenRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> Response:
    await auth.verify_email(payload.token, request)
    return Response(status_code=NO_CONTENT)


@auth_router.post(
    "/verify-email/resend",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the verification email again",
)
async def resend_verification(payload: EmailRequest, auth: AuthServiceDep) -> AcceptedResponse:
    await auth.resend_verification(payload.email)
    return CHECK_YOUR_EMAIL


@auth_router.post(
    "/login",
    response_model=LoginResponse,
    summary="Sign in with email and password",
    description="Returns tokens, or `{mfa_required: true, mfa_token}` when the account uses "
    "two-factor authentication: then call `POST /auth/login/mfa`.",
)
async def login(
    payload: LoginRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> LoginResponse:
    return await auth.login(payload.email, payload.password, request)


@auth_router.post(
    "/login/username",
    response_model=LoginResponse,
    summary="Sign in with a username and password (students)",
    description="For children without an email address. Usernames are set by a parent or teacher.",
)
async def login_with_username(
    payload: UsernameLoginRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> LoginResponse:
    return await auth.login_with_username(payload.username, payload.password, request)


@auth_router.post(
    "/login/mfa",
    response_model=TokenPair,
    summary="Finish signing in with a two-factor code",
    description="Send the `mfa_token` from the sign-in response with either `code` (from the "
    "authenticator app) or `recovery_code`. At most 5 attempts per sign-in.",
)
async def login_mfa(
    payload: MfaLoginRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> TokenPair:
    return await auth.complete_mfa(
        payload.mfa_token, code=payload.code, recovery_code=payload.recovery_code, request=request
    )


# --- /auth/oauth: Google, Microsoft, Apple ------------------------------------------------


@auth_router.get(
    "/oauth/providers",
    response_model=OAuthProvidersRead,
    summary="Social sign-in providers that are set up",
)
async def oauth_providers(oauth: OAuthServiceDep) -> OAuthProvidersRead:
    return OAuthProvidersRead(providers=oauth.enabled_providers())


@auth_router.get(
    "/oauth/{provider}/start",
    response_model=OAuthStartRead,
    summary="Start signing in (or up) with a provider",
    description="Send the browser to `authorization_url`. `intent=signup` needs `role` "
    "(`parent` or `teacher`). When it is done, the browser lands on "
    "`{LUMINA_FRONTEND_URL}/auth/complete?code=...` (post it to `/auth/oauth/exchange`) or "
    "`?error=...`.",
)
async def oauth_start(
    provider: str,
    oauth: OAuthServiceDep,
    intent: Literal["login", "signup"] = "login",
    role: Literal["parent", "teacher"] | None = None,
) -> OAuthStartRead:
    url = await oauth.start(provider, intent=intent, role=role)
    return OAuthStartRead(authorization_url=url)


def _redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@auth_router.get("/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback_get(
    provider: str,
    oauth: OAuthServiceDep,
    request: RequestInfoDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Called by Google and Microsoft (not by clients)."""
    return _redirect(
        await oauth.callback(
            provider, code=code, state=state, error=error, extra={}, request=request
        )
    )


@auth_router.post("/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback_post(
    provider: str, raw: Request, oauth: OAuthServiceDep, request: RequestInfoDep
) -> RedirectResponse:
    """Apple posts the result as a form (response_mode=form_post)."""
    body = (await raw.body()).decode(errors="replace")
    fields = {k: v[0] for k, v in parse_qs(body).items()}
    extra: Any = {}
    if "user" in fields:  # Apple sends the name only on the first sign-in
        try:
            extra = json.loads(fields["user"])
        except ValueError:
            extra = {}
    return _redirect(
        await oauth.callback(
            provider,
            code=fields.get("code"),
            state=fields.get("state"),
            error=fields.get("error"),
            extra=extra if isinstance(extra, dict) else {},
            request=request,
        )
    )


@auth_router.post(
    "/oauth/exchange",
    response_model=LoginResponse,
    summary="Collect the tokens after a provider sign-in",
    description="The one-time `code` from `/auth/complete?code=...` (valid 2 minutes).",
)
async def oauth_exchange(
    payload: OAuthExchangeRequest, oauth: OAuthServiceDep, request: RequestInfoDep
) -> LoginResponse:
    return await oauth.exchange(payload.code, request)


@auth_router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for new tokens",
    description="The refresh token is single-use: the response contains a new one. "
    "Presenting an already-used refresh token ends the whole session.",
)
async def refresh(
    payload: RefreshRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> TokenPair:
    return await auth.refresh(payload.refresh_token, request)


@auth_router.post("/logout", status_code=NO_CONTENT, summary="Sign out of this session")
async def logout(actor: CurrentActor, auth: AuthServiceDep, request: RequestInfoDep) -> Response:
    await auth.logout(actor, request)
    return Response(status_code=NO_CONTENT)


@auth_router.post(
    "/password/forgot",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password reset link",
)
async def forgot_password(payload: EmailRequest, auth: AuthServiceDep) -> AcceptedResponse:
    await auth.forgot_password(payload.email)
    return CHECK_YOUR_EMAIL


@auth_router.post(
    "/password/reset",
    status_code=NO_CONTENT,
    summary="Set a new password with an emailed token",
    description="Also used to activate a child account created by a parent. "
    "Signs the account out everywhere.",
)
async def reset_password(
    payload: ResetPasswordRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> Response:
    await auth.reset_password(payload.token, payload.new_password, request)
    return Response(status_code=NO_CONTENT)


@auth_router.post("/email/confirm", status_code=NO_CONTENT, summary="Confirm a new email address")
async def confirm_email_change(
    payload: TokenRequest, auth: AuthServiceDep, request: RequestInfoDep
) -> Response:
    await auth.confirm_email_change(payload.token, request)
    return Response(status_code=NO_CONTENT)


# --- /me: the signed-in user --------------------------------------------------------------


@me_router.get("", response_model=MeRead, summary="The signed-in user")
async def get_me(actor: CurrentActor, accounts: AccountServiceDep) -> MeRead:
    return _me(await accounts.get_me(actor), actor)


@me_router.patch("", response_model=MeRead, summary="Update name, locale or time zone")
async def update_me(payload: MeUpdate, actor: CurrentActor, accounts: AccountServiceDep) -> MeRead:
    return _me(await accounts.update_me(actor, payload), actor)


@me_router.delete(
    "",
    status_code=NO_CONTENT,
    summary="Delete my account",
    description="Requires the password. Personal data is erased and every session ends. "
    "Children managed only by this parent are deleted too.",
)
async def delete_me(
    payload: DeleteAccountRequest,
    actor: CurrentActor,
    accounts: AccountServiceDep,
    request: RequestInfoDep,
) -> Response:
    await accounts.delete_account(actor, payload.password, request)
    return Response(status_code=NO_CONTENT)


@me_router.post(
    "/password",
    status_code=NO_CONTENT,
    summary="Change my password",
    description="Signs out every other session.",
)
async def change_password(
    payload: ChangePasswordRequest,
    actor: CurrentActor,
    accounts: AccountServiceDep,
    request: RequestInfoDep,
) -> Response:
    await accounts.change_password(actor, payload.current_password, payload.new_password, request)
    return Response(status_code=NO_CONTENT)


@me_router.post(
    "/email",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Change my email address",
    description="Sends a confirmation link to the new address. The change applies when "
    "the link is used (`POST /auth/email/confirm`).",
)
async def change_email(
    payload: ChangeEmailRequest, actor: CurrentActor, accounts: AccountServiceDep
) -> AcceptedResponse:
    await accounts.request_email_change(actor, payload.new_email, payload.password)
    return CHECK_YOUR_EMAIL


@me_router.get("/sessions", response_model=list[SessionRead], summary="My active sessions")
async def list_sessions(actor: CurrentActor, accounts: AccountServiceDep) -> list[SessionRead]:
    return [
        SessionRead.model_validate(s).model_copy(update={"current": s.id == actor.session_id})
        for s in await accounts.list_sessions(actor)
    ]


@me_router.delete("/sessions/{session_id}", status_code=NO_CONTENT, summary="End one session")
async def revoke_session(
    session_id: uuid.UUID,
    actor: CurrentActor,
    accounts: AccountServiceDep,
    request: RequestInfoDep,
) -> Response:
    await accounts.revoke_session(actor, session_id, request)
    return Response(status_code=NO_CONTENT)


@me_router.post(
    "/sessions/revoke-others",
    summary="Sign out on every other device",
)
async def revoke_other_sessions(
    actor: CurrentActor, accounts: AccountServiceDep, request: RequestInfoDep
) -> dict[str, int]:
    return {"revoked": await accounts.revoke_other_sessions(actor, request)}


# --- /me: linked providers and two-factor authentication ----------------------------------


@me_router.get(
    "/oauth", response_model=list[LinkedIdentityRead], summary="My linked Google/Microsoft/Apple"
)
async def linked_identities(
    actor: CurrentActor, oauth: OAuthServiceDep
) -> list[LinkedIdentityRead]:
    return [LinkedIdentityRead.model_validate(i) for i in await oauth.list_linked(actor)]


@me_router.post(
    "/oauth/{provider}/link",
    response_model=OAuthStartRead,
    summary="Link a Google/Microsoft/Apple account to my account",
    description="Send the browser to `authorization_url`; it comes back to "
    "`{LUMINA_FRONTEND_URL}/auth/complete?linked={provider}` (or `?error=...`).",
)
async def link_identity(
    provider: str, actor: CurrentActor, oauth: OAuthServiceDep
) -> OAuthStartRead:
    url = await oauth.start(provider, intent="link", actor=actor)
    return OAuthStartRead(authorization_url=url)


@me_router.delete(
    "/oauth/{provider}",
    status_code=NO_CONTENT,
    summary="Unlink a provider",
    description="Refused (409) if it is the only way left to sign in.",
)
async def unlink_identity(
    provider: str, actor: CurrentActor, oauth: OAuthServiceDep, request: RequestInfoDep
) -> Response:
    await oauth.unlink(actor, provider, request)
    return Response(status_code=NO_CONTENT)


@me_router.post(
    "/mfa/totp",
    response_model=TotpSetupRead,
    summary="Start setting up two-factor authentication (teachers, admins)",
    description="Add the secret to an authenticator app (show `otpauth_uri` as a QR code), "
    "then confirm with a code. Two-factor stays off until confirmed.",
)
async def start_totp(actor: CurrentActor, accounts: AccountServiceDep) -> TotpSetupRead:
    secret, uri = await accounts.start_totp_setup(actor)
    return TotpSetupRead(secret=secret, otpauth_uri=uri)


@me_router.post(
    "/mfa/totp/confirm",
    response_model=RecoveryCodesRead,
    summary="Turn two-factor authentication on",
    description="Returns 10 single-use recovery codes. They are shown only this once.",
)
async def confirm_totp(
    payload: TotpConfirmRequest,
    actor: CurrentActor,
    accounts: AccountServiceDep,
    request: RequestInfoDep,
) -> RecoveryCodesRead:
    codes = await accounts.confirm_totp(actor, payload.code, request)
    return RecoveryCodesRead(recovery_codes=codes)


@me_router.delete("/mfa/totp", status_code=NO_CONTENT, summary="Turn two-factor off")
async def disable_totp(
    payload: TotpDisableRequest,
    actor: CurrentActor,
    accounts: AccountServiceDep,
    request: RequestInfoDep,
) -> Response:
    await accounts.disable_totp(actor, payload.code, payload.password, request)
    return Response(status_code=NO_CONTENT)


@me_router.post(
    "/mfa/recovery-codes",
    response_model=RecoveryCodesRead,
    summary="Replace my recovery codes",
    description="Needs a current code. The old recovery codes stop working.",
)
async def regenerate_recovery_codes(
    payload: TotpConfirmRequest,
    actor: CurrentActor,
    accounts: AccountServiceDep,
    request: RequestInfoDep,
) -> RecoveryCodesRead:
    codes = await accounts.regenerate_recovery_codes(actor, payload.code, request)
    return RecoveryCodesRead(recovery_codes=codes)


@me_router.patch(
    "/teacher-profile", response_model=TeacherProfileRead, summary="Update my teacher profile"
)
async def update_teacher_profile(
    payload: TeacherProfileUpdate, actor: CurrentActor, accounts: AccountServiceDep
) -> TeacherProfileRead:
    return TeacherProfileRead.model_validate(await accounts.update_teacher_profile(actor, payload))


# --- /me: parents and children ------------------------------------------------------------


@me_router.get("/children", response_model=list[ChildRead], summary="My children")
async def list_children(actor: ChildrenManager, family: FamilyServiceDep) -> list[ChildRead]:
    return [_child(link) for link in await family.list_children(actor)]


@me_router.post(
    "/children",
    response_model=ChildRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a child account",
    description="Creates a student account managed by this parent. With an email, the "
    "child is invited to set a password and can then sign in.",
)
async def add_child(
    payload: ChildCreate,
    actor: ChildrenManager,
    family: FamilyServiceDep,
    request: RequestInfoDep,
) -> ChildRead:
    return _child(await family.add_child(actor, payload, request))


@me_router.put(
    "/children/{child_id}/sign-in",
    response_model=ChildRead,
    summary="Set up how my child signs in",
    description="Username and password, and/or a picture password (Grades 1-3). Only for "
    "children without their own email address. A new password signs the child out "
    "everywhere.",
)
async def update_child_sign_in(
    child_id: uuid.UUID,
    payload: StudentSignInUpdate,
    actor: ChildrenManager,
    family: FamilyServiceDep,
    request: RequestInfoDep,
) -> ChildRead:
    return _child(await family.update_child_sign_in(actor, child_id, payload, request))


@me_router.post(
    "/children/link",
    response_model=ChildRead,
    summary="Link an existing student account with its code",
)
async def link_child(
    payload: LinkChildRequest,
    actor: ChildrenManager,
    family: FamilyServiceDep,
    request: RequestInfoDep,
) -> ChildRead:
    return _child(await family.link_child(actor, payload.code, request))


@me_router.put(
    "/active-child",
    status_code=NO_CONTENT,
    summary="Switch the active child for this session",
)
async def set_active_child(
    payload: ActiveChildRequest, actor: ChildrenManager, family: FamilyServiceDep
) -> Response:
    await family.set_active_child(actor, payload.child_id)
    return Response(status_code=NO_CONTENT)


@me_router.post(
    "/link-code",
    response_model=LinkCodeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a code a parent can use to link to my student account",
)
async def create_link_code(actor: CurrentActor, family: FamilyServiceDep) -> LinkCodeRead:
    code, expires_at = await family.create_link_code(actor)
    return LinkCodeRead(code=code, expires_at=expires_at)


# --- /admin: platform administration ------------------------------------------------------


@admin_router.get("/users", response_model=Page[UserAdminRead], summary="Search users")
async def search_users(
    query: Annotated[UserAdminQuery, Query()], actor: UsersManager, admin: AdminServiceDep
) -> Page[UserAdminRead]:
    users, total = await admin.search_users(
        text=query.q,
        role=query.role,
        is_active=query.is_active,
        limit=query.limit,
        offset=query.offset,
    )
    return Page[UserAdminRead](
        items=[_admin_user(u) for u in users], total=total, limit=query.limit, offset=query.offset
    )


@admin_router.get("/users/{user_id}", response_model=UserAdminRead, summary="Get a user")
async def get_user(
    user_id: uuid.UUID, actor: UsersManager, admin: AdminServiceDep
) -> UserAdminRead:
    return _admin_user(await admin.get_user(user_id))


@admin_router.patch(
    "/users/{user_id}",
    response_model=UserAdminRead,
    summary="Change a user's role or deactivate/reactivate them",
    description="Deactivating ends all the user's sessions immediately.",
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserAdminUpdate,
    actor: UsersManager,
    admin: AdminServiceDep,
    request: RequestInfoDep,
) -> UserAdminRead:
    return _admin_user(await admin.update_user(actor, user_id, payload, request))


@admin_router.put(
    "/users/{user_id}/teacher-verification",
    response_model=UserAdminRead,
    summary="Verify (or unverify) a teacher",
    description="Only verified teachers can publish courses.",
)
async def set_teacher_verification(
    user_id: uuid.UUID,
    payload: TeacherVerificationRequest,
    actor: UsersManager,
    admin: AdminServiceDep,
    request: RequestInfoDep,
) -> UserAdminRead:
    return _admin_user(
        await admin.set_teacher_verification(actor, user_id, payload.verified, request)
    )


@admin_router.delete(
    "/users/{user_id}/mfa",
    status_code=NO_CONTENT,
    summary="Turn off a user's two-factor authentication",
    description="For someone who lost both their phone and their recovery codes. Ends all "
    "their sessions and emails them.",
)
async def reset_user_mfa(
    user_id: uuid.UUID, actor: UsersManager, admin: AdminServiceDep, request: RequestInfoDep
) -> Response:
    await admin.reset_mfa(actor, user_id, request)
    return Response(status_code=NO_CONTENT)


@admin_router.post(
    "/users/{user_id}/impersonate",
    response_model=ImpersonationRead,
    summary="Act as a user for support (audited, time-limited)",
    description="Returns a 15-minute access token with no refresh token. Account-security "
    "actions (password, email, sessions, deletion) are blocked while impersonating.",
)
async def impersonate(
    user_id: uuid.UUID, actor: Impersonator, admin: AdminServiceDep, request: RequestInfoDep
) -> ImpersonationRead:
    token, expires_in = await admin.impersonate(actor, user_id, request)
    return ImpersonationRead(access_token=token, expires_in=expires_in, user_id=user_id)
