"""Rebuild the full-text search index from the published versions.

    python -m app.scripts.reindex_search

Safe to run at any time (e.g. after changing language dictionaries or restoring a backup).
"""

import asyncio

from app.core.config import get_settings
from app.core.storage import S3Storage
from app.db import registry  # noqa: F401  (all models)
from app.db.session import create_engine, create_session_factory
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditLog
from app.modules.curriculum.service import PublishingService
from app.modules.media.scanner import NoScanner
from app.modules.media.service import MediaService
from app.modules.standards.service import StandardsService


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    async with create_session_factory(engine)() as session:
        publishing = PublishingService(
            session,
            settings,
            AuditLog(AuditRepository(session)),
            MediaService(session, settings, S3Storage(settings), NoScanner()),
            StandardsService(session),
        )
        count = await publishing.reindex_all()
    await engine.dispose()
    print(f"Search index rebuilt for {count} published course(s).")


if __name__ == "__main__":
    asyncio.run(main())
