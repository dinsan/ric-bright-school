"""Outgoing email: message type, transports, and the interface services use to send.

Services call ``Mailer.send()``, which only *queues* the message; the HTTP layer delivers it
after the response (and therefore after the database commit). That keeps response times
independent of whether an email is sent, so responses cannot reveal which addresses exist.

Never log message bodies or recipients: bodies carry single-use tokens, addresses are
personal data.
"""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger("lumina.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str
    kind: str  # short label for logs and tests, e.g. "verify_email"; never personal data


class EmailTransport(Protocol):
    """Actually delivers a message (SMTP in real use, a recorder in tests)."""

    async def deliver(self, message: EmailMessage) -> None: ...


class Mailer(Protocol):
    """What services depend on: queue a message for delivery after the response."""

    def send(self, message: EmailMessage) -> None: ...


class SmtpTransport:
    """Plain SMTP via the standard library, run in a worker thread (smtplib blocks)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def deliver(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._deliver_sync, message)

    def _deliver_sync(self, message: EmailMessage) -> None:
        s = self._settings
        mime = MimeMessage()
        mime["From"] = s.email_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username and s.smtp_password:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(mime)


async def deliver_safely(transport: EmailTransport, message: EmailMessage) -> None:
    """Background-task wrapper: a failed delivery is logged, never raised to the client."""
    try:
        await transport.deliver(message)
    except Exception as exc:
        # Only the exception type: SMTP errors can contain the recipient address.
        logger.error("email delivery failed (kind=%s, error=%s)", message.kind, type(exc).__name__)
    else:
        logger.info("email sent (kind=%s)", message.kind)
