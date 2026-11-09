from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError


async def commit_or_conflict(session: AsyncSession, message: str) -> None:
    """Commit; translate a unique/FK violation (e.g. a lost race) into a 409 domain error."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(message) from exc
