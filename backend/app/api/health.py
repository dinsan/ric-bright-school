import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionDep

logger = logging.getLogger("lumina.health")
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    """The process is up. Never touches dependencies, so it cannot cascade restarts."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready(request: Request, session: SessionDep) -> JSONResponse:
    """The service can take traffic: verifies database connectivity."""
    version = request.app.version
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "version": version, "checks": {"database": "down"}},
        )
    return JSONResponse(
        content={"status": "ok", "version": version, "checks": {"database": "up"}},
    )
