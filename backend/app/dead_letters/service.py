"""
Task 23.4: Dead-letter queue (DLQ) listing and re-drive service.

All queries run on a tenant-scoped session (RLS via app.current_tenant_id) and are
additionally filtered by tenant_id as defence in depth.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeadLetterEvent, User, WebhookEvent

StatusFilter = Literal["unresolved", "all"]


class DeadLetterNotFoundError(Exception):
    """DLQ item does not exist or is not visible to the tenant."""


class DeadLetterAlreadyResolvedError(Exception):
    """DLQ item was already re-driven/resolved."""


@dataclass(frozen=True)
class DeadLetterPage:
    items: list[DeadLetterEvent]
    unresolved_count: int
    total: int


@dataclass(frozen=True)
class RedriveResult:
    dead_letter_id: uuid.UUID
    event_id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    event_type: str
    payload: dict[str, Any]
    resolved_at: datetime


async def list_dead_letters(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status_filter: StatusFilter = "unresolved",
    limit: int = 50,
    offset: int = 0,
) -> DeadLetterPage:
    """Lists DLQ items newest first (served by idx_dead_letter_events_unresolved)."""
    base = select(DeadLetterEvent).where(DeadLetterEvent.tenant_id == tenant_id)
    count_base = (
        select(func.count())
        .select_from(DeadLetterEvent)
        .where(DeadLetterEvent.tenant_id == tenant_id)
    )
    if status_filter == "unresolved":
        base = base.where(DeadLetterEvent.is_resolved.is_(False))
        count_base = count_base.where(DeadLetterEvent.is_resolved.is_(False))

    rows = await session.execute(
        base.order_by(DeadLetterEvent.quarantined_at.desc()).limit(limit).offset(offset)
    )
    total = (await session.execute(count_base)).scalar_one()
    unresolved = (
        await session.execute(
            select(func.count())
            .select_from(DeadLetterEvent)
            .where(
                DeadLetterEvent.tenant_id == tenant_id,
                DeadLetterEvent.is_resolved.is_(False),
            )
        )
    ).scalar_one()
    return DeadLetterPage(
        items=list(rows.scalars().all()), unresolved_count=int(unresolved), total=int(total)
    )


async def resolve_user_id(
    session: AsyncSession, tenant_id: uuid.UUID, sub: str, email: Optional[str]
) -> Optional[uuid.UUID]:
    """Maps JWT identity (sub/email) to users.id; None when the user is not provisioned."""
    conditions = [User.google_sub == sub]
    if email:
        conditions.append(User.email == email)
    try:
        sub_uuid = uuid.UUID(sub)
        conditions.append(User.id == sub_uuid)
    except ValueError:
        pass
    result = await session.execute(
        select(User.id).where(User.tenant_id == tenant_id, or_(*conditions)).limit(1)
    )
    return result.scalar_one_or_none()


async def redrive_dead_letter(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    dead_letter_id: uuid.UUID,
    resolver_user_id: Optional[uuid.UUID],
) -> RedriveResult:
    """
    Resets webhook_events (attempts=0, status='queued') and marks the DLQ item resolved.
    Caller commits and then pushes the envelope to arq:queue:high.
    """
    dl = (
        await session.execute(
            select(DeadLetterEvent)
            .where(DeadLetterEvent.id == dead_letter_id, DeadLetterEvent.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if dl is None:
        raise DeadLetterNotFoundError(str(dead_letter_id))
    if dl.is_resolved:
        raise DeadLetterAlreadyResolvedError(str(dead_letter_id))

    event_row = (
        await session.execute(
            update(WebhookEvent)
            .where(WebhookEvent.id == dl.original_event_id, WebhookEvent.tenant_id == tenant_id)
            .values(attempts=0, status="queued", error_message=None)
            .returning(WebhookEvent.event_type)
        )
    ).first()
    if event_row is None:
        raise DeadLetterNotFoundError(str(dead_letter_id))

    resolved_at = datetime.now(timezone.utc)
    await session.execute(
        update(DeadLetterEvent)
        .where(DeadLetterEvent.id == dl.id)
        .values(is_resolved=True, resolved_by=resolver_user_id, resolved_at=resolved_at)
    )
    return RedriveResult(
        dead_letter_id=dl.id,
        event_id=dl.original_event_id,
        tenant_id=tenant_id,
        provider=dl.provider,
        event_type=event_row[0],
        payload=dict(dl.payload or {}),
        resolved_at=resolved_at,
    )
