from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.email import EmailMessage, EmailTransport, Mailer, deliver_safely


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Leaving without a commit rolls the transaction back."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


class BackgroundMailer:
    """Queues emails as background tasks: delivered after the response has been sent."""

    def __init__(self, tasks: BackgroundTasks, transport: EmailTransport) -> None:
        self._tasks = tasks
        self._transport = transport

    def send(self, message: EmailMessage) -> None:
        self._tasks.add_task(deliver_safely, self._transport, message)


def get_mailer(request: Request, tasks: BackgroundTasks) -> Mailer:
    return BackgroundMailer(tasks, request.app.state.email_transport)


MailerDep = Annotated[Mailer, Depends(get_mailer)]
