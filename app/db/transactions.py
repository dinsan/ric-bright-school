from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError


@asynccontextmanager
async def conflict_on_integrity_error(session: AsyncSession, message: str) -> AsyncIterator[None]:
    """Translate a unique/FK violation raised inside the block into a 409 domain error.

    Wrap the whole write (flush *and* commit): PostgreSQL reports a unique violation when the
    ``INSERT`` is flushed, so guarding only the commit lets a lost race escape as a 500.
    """
    try:
        yield
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(message) from exc
