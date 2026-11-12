"""
SCRUM-387: Staged-Event Recovery Sweeper Claim Query & Worker CAS Transition Service.

Features:
- Non-blocking batch claim of stranded 'staged' events (>10s grace threshold) using PostgreSQL
  'FOR UPDATE SKIP LOCKED', transitioning them to 'recovering'.
- Concurrency guarantee: Multiple sweeper workers claim disjoint sets of events with zero lock contention.
- Atomic Compare-And-Swap (CAS) worker transition:
  UPDATE webhook_events SET status = 'processing', attempts = attempts + 1, claimed_at = now()
  WHERE id = :id AND status IN ('staged', 'queued', 'recovering') RETURNING id
  Guarantees duplicate queue messages cannot trigger double processing.
- Tenant-isolated under PostgreSQL RLS or explicit tenant_id scoping.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, List, Optional
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.webhooks.sweeper")


@dataclass(frozen=True)
class ClaimedEventRecord:
    """Represents a webhook event claimed by the recovery sweeper."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    integration_id: Optional[uuid.UUID]
    provider: str
    provider_delivery_id: str
    event_type: str
    received_at: datetime
    payload: dict[str, Any]
    status: str
    attempts: int
    claimed_at: datetime


_CLAIM_STRANDED_EVENTS_SQL = text(
    """
    WITH candidates AS (
        SELECT id
        FROM webhook_events
        WHERE status = 'staged'
          AND received_at <= :cutoff
          AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
        ORDER BY received_at ASC
        LIMIT :batch_size
        FOR UPDATE SKIP LOCKED
    )
    UPDATE webhook_events AS w
    SET status = 'recovering',
        claimed_at = :now
    FROM candidates
    WHERE w.id = candidates.id
    RETURNING w.id, w.tenant_id, w.integration_id, w.provider, w.provider_delivery_id,
              w.event_type, w.received_at, w.payload, w.status, w.attempts, w.claimed_at;
    """
)

_CAS_WORKER_TRANSITION_SQL = text(
    """
    UPDATE webhook_events
    SET status = 'processing',
        attempts = attempts + 1,
        claimed_at = :now
    WHERE id = :event_id
      AND status IN ('staged', 'queued', 'recovering')
      AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
    RETURNING id;
    """
)

_COMPLETE_EVENT_SQL = text(
    """
    UPDATE webhook_events
    SET status = 'completed'
    WHERE id = :event_id
      AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
    RETURNING id;
    """
)

_FAIL_EVENT_SQL = text(
    """
    UPDATE webhook_events
    SET status = 'failed',
        error_message = :error_message
    WHERE id = :event_id
      AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
    RETURNING id;
    """
)

_MARK_EVENTS_QUEUED_SQL = text(
    """
    UPDATE webhook_events
    SET status = 'queued'
    WHERE id IN :ids
      AND status = 'recovering'
      AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
    RETURNING id;
    """
).bindparams(bindparam("ids", expanding=True))

_MARK_QUEUED_FROM_STAGED_SQL = text(
    """
    UPDATE webhook_events
    SET status = 'queued'
    WHERE id = :event_id
      AND status = 'staged'
      AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = CAST(:tenant_id AS uuid))
    RETURNING id;
    """
)


async def claim_stranded_staged_events(
    db: AsyncSession,
    *,
    threshold_seconds: float = 10.0,
    batch_size: int = 100,
    tenant_id: Optional[uuid.UUID] = None,
) -> List[ClaimedEventRecord]:
    """
    Claims stranded 'staged' events older than `threshold_seconds` (default 10s)
    using non-blocking FOR UPDATE SKIP LOCKED, transitioning them to 'recovering'.

    Returns:
        List[ClaimedEventRecord] claimed in this transaction. Disjoint across concurrent calls.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=threshold_seconds)

    result = await db.execute(
        _CLAIM_STRANDED_EVENTS_SQL,
        {
            "cutoff": cutoff,
            "tenant_id": tenant_id,
            "batch_size": batch_size,
            "now": now,
        },
    )
    rows = result.fetchall()

    claimed: List[ClaimedEventRecord] = []
    for r in rows:
        claimed.append(
            ClaimedEventRecord(
                id=r.id,
                tenant_id=r.tenant_id,
                integration_id=r.integration_id,
                provider=r.provider,
                provider_delivery_id=r.provider_delivery_id,
                event_type=r.event_type,
                received_at=r.received_at,
                payload=r.payload,
                status=r.status,
                attempts=r.attempts,
                claimed_at=r.claimed_at,
            )
        )

    if claimed:
        logger.info(
            f"Sweeper claimed {len(claimed)} stranded webhook events older than {threshold_seconds}s."
        )
    return claimed


async def cas_transition_to_processing(
    db: AsyncSession,
    event_id: uuid.UUID,
    *,
    tenant_id: Optional[uuid.UUID] = None,
) -> bool:
    """
    Atomically transitions an event to 'processing' and increments 'attempts'.
    Only succeeds if current status is in ('staged', 'queued', 'recovering').

    Returns:
        True if the event was successfully transitioned to 'processing'.
        False if the event was already in 'processing', 'completed', or terminal state (skips duplicate processing).
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        _CAS_WORKER_TRANSITION_SQL,
        {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "now": now,
        },
    )
    row = result.fetchone()
    if row is not None:
        logger.debug(f"Worker CAS successfully transitioned event {event_id} to processing.")
        return True

    logger.warning(
        f"Worker CAS rejected transition for event {event_id}. Event is already processing or completed."
    )
    return False


async def complete_webhook_event(
    db: AsyncSession,
    event_id: uuid.UUID,
    *,
    tenant_id: Optional[uuid.UUID] = None,
) -> bool:
    """Marks an event as 'completed'."""
    result = await db.execute(
        _COMPLETE_EVENT_SQL,
        {"event_id": event_id, "tenant_id": tenant_id},
    )
    return result.fetchone() is not None


async def fail_webhook_event(
    db: AsyncSession,
    event_id: uuid.UUID,
    error_message: str,
    *,
    tenant_id: Optional[uuid.UUID] = None,
) -> bool:
    """Marks an event as 'failed' with error_message."""
    result = await db.execute(
        _FAIL_EVENT_SQL,
        {"event_id": event_id, "error_message": error_message, "tenant_id": tenant_id},
    )
    return result.fetchone() is not None


async def mark_events_queued(
    db: AsyncSession,
    event_ids: List[uuid.UUID],
    *,
    tenant_id: Optional[uuid.UUID] = None,
) -> int:
    """
    Transitions events from 'recovering' to 'queued' (used by recovery sweeper).
    Returns the number of events updated.
    """
    if not event_ids:
        return 0
    result = await db.execute(
        _MARK_EVENTS_QUEUED_SQL,
        {"ids": list(event_ids), "tenant_id": tenant_id},
    )
    rows = result.fetchall()
    return len(rows)


async def mark_event_queued_if_staged(
    db: AsyncSession,
    event_id: uuid.UUID,
    *,
    tenant_id: Optional[uuid.UUID] = None,
) -> bool:
    """
    Transitions an event from 'staged' to 'queued' if still in 'staged' status
    (used by immediate post-commit webhook intake).
    Returns True if successfully transitioned, False if already claimed or processed.
    """
    result = await db.execute(
        _MARK_QUEUED_FROM_STAGED_SQL,
        {"event_id": event_id, "tenant_id": tenant_id},
    )
    return result.fetchone() is not None
