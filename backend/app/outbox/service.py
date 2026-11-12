"""
SCRUM-477 / Task 32.3: Transactional Outbox Service.

Implements the atomic session helper record_outbox_event and domain mutation helpers
operating inside a single PostgreSQL AsyncSession transaction boundary.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import Deployment, OutboxEvent

logger = logging.getLogger("app.outbox.service")


def record_outbox_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: Dict[str, Any],
    aggregate_type: str = "general",
    aggregate_id: str | uuid.UUID | None = None,
    event_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    occurred_at: Optional[datetime] = None,
) -> OutboxEvent:
    """
    Stages an OutboxEvent row inside the caller's active database transaction.

    CRITICAL INVARIANT: DOES NOT COMMIT THE TRANSACTION.
    The caller owns the transaction boundary and must await db.commit().
    If an exception occurs or db.rollback() is invoked, both the domain mutation
    and this outbox event roll back atomically.
    """
    now = occurred_at or datetime.now(timezone.utc)
    ev_id = event_id or generate_uuidv7()
    agg_id = str(aggregate_id) if aggregate_id is not None else str(ev_id)

    # Construct canonical event envelope payload as specified in SRS §3.2.3 and architecture design
    canonical_payload: Dict[str, Any] = {
        "event_id": str(ev_id),
        "event_type": event_type,
        "schema_version": "1.0",
        "tenant_id": str(tenant_id),
        "project_id": str(project_id) if project_id else None,
        "occurred_at": now.isoformat(),
        "data": payload,
    }

    outbox_event = OutboxEvent(
        id=ev_id,
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=agg_id,
        event_type=event_type,
        payload=canonical_payload,
        created_at=now,
        published_at=None,
    )

    db.add(outbox_event)
    logger.debug(
        "Staged outbox event %s (%s) for tenant %s within active transaction.",
        ev_id,
        event_type,
        tenant_id,
    )
    return outbox_event


async def record_deployment_failure_atomic(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    deployment_id: uuid.UUID,
    failure_reason: str,
    error_details: Optional[Dict[str, Any]] = None,
) -> tuple[Deployment, OutboxEvent]:
    """
    Atomically updates a production deployment to FAILED status and stages a
    'deployment.failed' outbox event within the SAME session.

    DOES NOT COMMIT: The caller retains control of the transaction boundary.
    """
    stmt = select(Deployment).where(
        Deployment.id == deployment_id,
        Deployment.tenant_id == tenant_id,
    )
    res = await db.execute(stmt)
    deployment = res.scalars().first()
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment '{deployment_id}' not found.",
        )

    # 1. Mutate domain entity
    deployment.status = "FAILED"
    deployment.is_change_failure = True

    # 2. Stage outbox event in the same transaction
    outbox_event = record_outbox_event(
        db,
        tenant_id=tenant_id,
        project_id=deployment.project_id,
        event_type="deployment.failed",
        aggregate_type="deployment",
        aggregate_id=deployment.id,
        payload={
            "deployment_id": str(deployment.id),
            "project_id": str(deployment.project_id) if deployment.project_id else None,
            "environment": deployment.environment,
            "commit_sha": deployment.commit_sha,
            "status": "FAILED",
            "is_change_failure": True,
            "failure_reason": failure_reason,
            "error_details": error_details or {},
        },
    )

    return deployment, outbox_event


async def claim_unpublished_outbox_events(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> list[OutboxEvent]:
    """
    Selects pending unpublished outbox events using FOR UPDATE SKIP LOCKED
    for safe, non-blocking concurrent Outbox Relay polling in monotonic UUIDv7 order.
    """
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def mark_outbox_events_published(
    db: AsyncSession,
    event_ids: list[uuid.UUID],
    published_at: Optional[datetime] = None,
) -> int:
    """
    Marks confirmed outbox events as published.
    """
    if not event_ids:
        return 0
    now = published_at or datetime.now(timezone.utc)
    stmt = update(OutboxEvent).where(OutboxEvent.id.in_(event_ids)).values(published_at=now)
    res = await db.execute(stmt)
    return res.rowcount
