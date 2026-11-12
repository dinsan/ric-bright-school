from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.pagination import Page
from app.modules.audit.schemas import AuditEventQuery, AuditEventRead
from app.modules.identity.deps import AuditLogDep, require_permission
from app.modules.identity.domain import Actor, Permission

router = APIRouter(prefix="/admin/audit-events", tags=["admin"])

AuditReader = Annotated[Actor, Depends(require_permission(Permission.AUDIT_READ))]


@router.get("", response_model=Page[AuditEventRead], summary="Search the audit log")
async def search_audit_events(
    query: Annotated[AuditEventQuery, Query()], actor: AuditReader, audit: AuditLogDep
) -> Page[AuditEventRead]:
    events, total = await audit.search(
        actor_id=query.actor_id,
        action=query.action,
        entity_type=query.entity_type,
        entity_id=query.entity_id,
        limit=query.limit,
        offset=query.offset,
    )
    return Page[AuditEventRead](
        items=[AuditEventRead.model_validate(e) for e in events],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )
