"""Email texts. Links point at the web app, which posts the token to the API."""

from urllib.parse import quote

from app.core.email import EmailMessage

_SIGNATURE = "\n\n— The Lumina team"
_NOT_YOU = "If you did not ask for this, you can ignore this email."


def _link(frontend_url: str, path: str, token: str) -> str:
    return f"{frontend_url.rstrip('/')}/{path}?token={quote(token)}"


def verify_email(to: str, name: str, token: str, frontend_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind="verify_email",
        subject="Confirm your email address",
        body=(
            f"Hi {name},\n\nConfirm your email address to finish setting up your Lumina "
            f"account:\n\n{_link(frontend_url, 'verify-email', token)}\n\n"
            f"The link expires in 48 hours. {_NOT_YOU}{_SIGNATURE}"
        ),
    )


def account_exists(to: str, frontend_url: str) -> EmailMessage:
    # Sent instead of an error when someone registers with an address that already has an
    # account, so the registration response never reveals which addresses are in use.
    return EmailMessage(
        to=to,
        kind="account_exists",
        subject="You already have a Lumina account",
        body=(
            "Someone (probably you) tried to create a Lumina account with this email "
            "address, but it already has one.\n\nForgot your password? Reset it here:\n\n"
            f"{frontend_url.rstrip('/')}/forgot-password\n\n{_NOT_YOU}{_SIGNATURE}"
        ),
    )


def reset_password(to: str, name: str, token: str, frontend_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind="reset_password",
        subject="Reset your Lumina password",
        body=(
            f"Hi {name},\n\nUse this link to choose a new password:\n\n"
            f"{_link(frontend_url, 'reset-password', token)}\n\n"
            f"The link expires in 1 hour and works once. {_NOT_YOU}{_SIGNATURE}"
        ),
    )


def activate_account(
    to: str, name: str, parent_name: str, token: str, frontend_url: str
) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind="activate_account",
        subject="Your Lumina account is ready",
        body=(
            f"Hi {name},\n\n{parent_name} created a Lumina account for you. Choose a "
            f"password to start:\n\n{_link(frontend_url, 'activate', token)}\n\n"
            f"The link expires in 7 days.{_SIGNATURE}"
        ),
    )


def confirm_new_email(to: str, name: str, token: str, frontend_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind="change_email",
        subject="Confirm your new email address",
        body=(
            f"Hi {name},\n\nConfirm this address as the new email for your Lumina "
            f"account:\n\n{_link(frontend_url, 'confirm-email', token)}\n\n"
            f"The link expires in 24 hours. {_NOT_YOU}{_SIGNATURE}"
        ),
    )


def email_changed_notice(to: str, name: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind="email_changed",
        subject="Your Lumina email address was changed",
        body=(
            f"Hi {name},\n\nThe email address of your Lumina account was just changed, "
            "and this address will no longer be used.\n\nIf this was not you, contact "
            f"support right away.{_SIGNATURE}"
        ),
    )


def password_changed_notice(to: str, name: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind="password_changed",
        subject="Your Lumina password was changed",
        body=(
            f"Hi {name},\n\nThe password of your Lumina account was just changed and you "
            "were signed out on your other devices.\n\nIf this was not you, reset your "
            f"password right away and contact support.{_SIGNATURE}"
        ),
    )


def _security_notice(to: str, name: str, kind: str, subject: str, what: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        kind=kind,
        subject=subject,
        body=(
            f"Hi {name},\n\n{what}\n\nIf this was not you, reset your password right away "
            f"and contact support.{_SIGNATURE}"
        ),
    )


def two_factor_enabled(to: str, name: str) -> EmailMessage:
    return _security_notice(
        to, name, "mfa_enabled", "Two-step verification is on",
        "Two-step verification was turned on for your Lumina account. Keep your recovery "
        "codes somewhere safe.",
    )  # fmt: skip


def two_factor_disabled(to: str, name: str) -> EmailMessage:
    return _security_notice(
        to, name, "mfa_disabled", "Two-step verification is off",
        "Two-step verification was turned off for your Lumina account.",
    )  # fmt: skip


def recovery_code_used(to: str, name: str, remaining: int) -> EmailMessage:
    return _security_notice(
        to, name, "recovery_code_used", "A recovery code was used",
        f"A recovery code was used to sign in to your Lumina account. You have {remaining} "
        "left; you can create new ones in your security settings.",
    )  # fmt: skip


def identity_linked(to: str, name: str, provider: str) -> EmailMessage:
    return _security_notice(
        to, name, "identity_linked", f"{provider.title()} sign-in was added",
        f"You can now sign in to your Lumina account with {provider.title()}.",
    )  # fmt: skip
