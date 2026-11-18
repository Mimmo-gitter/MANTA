"""
Task 23.4: Dead-letter queue API.

- GET  /api/v1/dead-letters              -> unresolved/all DLQ items + unresolved_count
- POST /api/v1/dead-letters/{id}/redrive -> reset webhook event, resolve DLQ item,
                                            push envelope to arq:queue:high

Both routes require MANAGE_WEBHOOK_INTEGRATIONS (TENANT_ADMIN only); DEVELOPER and
other roles receive HTTP 403. All DB access is tenant-scoped under RLS.
"""

import logging
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims
from app.db.session import get_tenant_db_context
from app.dead_letters import service
from app.dead_letters.schemas import DeadLetterItem, DeadLetterListResponse, RedriveResponse
from app.webhooks import enqueue as enqueue_mod
from workers.worker import QUEUE_HIGH, enqueue_job, serialize_job_payload

logger = logging.getLogger("app.dead_letters.router")

router = APIRouter(prefix="/api/v1/dead-letters", tags=["Dead Letters"])

require_dlq_admin = require_permission(Permission.MANAGE_WEBHOOK_INTEGRATIONS)


@router.get("", response_model=DeadLetterListResponse, summary="List quarantined DLQ items")
async def list_dead_letters(
    status_filter: Literal["unresolved", "all"] = Query("unresolved", alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenClaims = Depends(require_dlq_admin),
) -> DeadLetterListResponse:
    tenant_id = user.tenant_uuid
    async with get_tenant_db_context(tenant_id) as session:
        page = await service.list_dead_letters(
            session, tenant_id, status_filter=status_filter, limit=limit, offset=offset
        )
        items = [DeadLetterItem.model_validate(i) for i in page.items]
    return DeadLetterListResponse(
        items=items,
        unresolved_count=page.unresolved_count,
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{id}/redrive",
    response_model=RedriveResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-drive a DLQ item into the high-priority queue",
)
async def redrive_dead_letter(
    id: uuid.UUID,
    user: TokenClaims = Depends(require_dlq_admin),
) -> RedriveResponse:
    tenant_id = user.tenant_uuid
    try:
        # Transaction commits on context exit, strictly before the Redis push.
        async with get_tenant_db_context(tenant_id) as session:
            resolver_id = await service.resolve_user_id(session, tenant_id, user.sub, user.email)
            result = await service.redrive_dead_letter(session, tenant_id, id, resolver_id)
    except service.DeadLetterNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dead-letter event not found") from exc
    except service.DeadLetterAlreadyResolvedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dead-letter event already resolved") from exc

    envelope = serialize_job_payload(
        job_id=uuid.uuid4(),
        tenant_id=result.tenant_id,
        priority="high",
        job_type=enqueue_mod.WEBHOOK_JOB_TYPE,
        payload=result.payload,
        event_id=result.event_id,
        provider=result.provider,
        attempt=0,
    )
    enqueued = True
    try:
        await enqueue_job(enqueue_mod.get_redis(), envelope, priority="high")
    except enqueue_mod.REDIS_ERRORS as exc:
        enqueued = False
        logger.warning(
            "Redrive %s committed but Redis push failed (event %s stays 'queued'): %s",
            id,
            result.event_id,
            exc,
        )

    return RedriveResponse(
        id=result.dead_letter_id,
        event_id=result.event_id,
        queue=QUEUE_HIGH,
        enqueued=enqueued,
        resolved_at=result.resolved_at,
    )
