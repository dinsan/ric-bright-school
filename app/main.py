import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.email import EmailTransport, SmtpTransport
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.storage import ObjectStorage, S3Storage
from app.db.session import create_engine, create_session_factory
from app.modules.identity.oauth import OAuthProviders
from app.modules.identity.security import PasswordHasher
from app.modules.media.scanner import MediaScanner, NoScanner

logger = logging.getLogger("lumina.app")


def create_app(
    settings: Settings | None = None,
    *,
    email_transport: EmailTransport | None = None,
    oauth_transport: httpx.AsyncBaseTransport | None = None,
    storage: ObjectStorage | None = None,
    media_scanner: MediaScanner | None = None,
) -> FastAPI:
    """Application factory — tests and workers can build an app with their own settings
    (and, for tests, an email transport that records instead of sending, and a fake
    Google/Microsoft/Apple behind ``oauth_transport``)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "local")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        logger.info(
            "started %s v%s [%s]", settings.app_name, settings.app_version, settings.environment
        )
        try:
            yield
        finally:
            await oauth_http.aclose()
            await engine.dispose()
            logger.info("stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.password_hasher = PasswordHasher(settings)
    app.state.email_transport = email_transport or SmtpTransport(settings)
    # One HTTP client and one provider registry per app: keeps connections and the
    # providers' signing keys (JWKS) cached between sign-ins.
    oauth_http = httpx.AsyncClient(transport=oauth_transport)
    app.state.oauth_providers = OAuthProviders(settings, oauth_http)
    app.state.storage = storage or S3Storage(settings)
    app.state.media_scanner = media_scanner or NoScanner()

    # Starlette runs the last-added middleware first, so CORS wraps everything.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
