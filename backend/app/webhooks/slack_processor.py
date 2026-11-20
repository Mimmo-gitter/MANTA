"""
SCRUM-422 & SCRUM-423: Slack Webhook Ingestion for Communication Events with
Monotonic Microsecond `entity_version` and Conditional PostgreSQL Upsert Guard.

Implements:
- Microsecond-precision `event_ts` extraction via `extract_slack_event_version`.
- Atomic PostgreSQL upsert on `(project_id, channel_id, thread_ts)` guarded by
  `WHERE communication_events.entity_version < EXCLUDED.entity_version`.
- Out-of-order delayed Slack deliveries update 0 rows and return `stale_rejected`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Mapping, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import CommunicationEvent, Integration, Project
from app.webhooks.metrics import record_dropped_stale_event
from app.webhooks.sweeper import complete_webhook_event
from app.webhooks.versioning import (
    VersionedMutationResult,
    extract_entity_version,
    parse_slack_ts_to_micros,
)

logger = logging.getLogger("app.webhooks.slack_processor")


def _slack_ts_to_datetime(raw_ts: Any) -> Optional[datetime]:
    micros = parse_slack_ts_to_micros(raw_ts)
    if micros is None:
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc)


async def _resolve_slack_project_id(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: Mapping[str, Any],
    integration_id: Optional[uuid.UUID],
) -> Optional[uuid.UUID]:
    raw_pid = payload.get("project_id")
    if raw_pid:
        try:
            pid = uuid.UUID(str(raw_pid))
            exists = (
                await db.execute(
                    select(Project.id).where(
                        Project.id == pid,
                        Project.tenant_id == tenant_id,
                        Project.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                return exists
        except ValueError:
            pass

    if integration_id is not None:
        integ_pid = (
            await db.execute(
                select(Integration.project_id).where(
                    Integration.id == integration_id,
                    Integration.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if integ_pid is not None:
            return integ_pid

    return None


async def upsert_communication_event_versioned(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    integration_id: Optional[uuid.UUID],
    channel_id: str,
    thread_ts: str,
    incoming_version: int,
    kind: str = "blocker_thread",
    asked_at: Optional[datetime] = None,
    first_response_at: Optional[datetime] = None,
    response_delay_seconds: Optional[int] = None,
    is_resolved: bool = False,
) -> VersionedMutationResult:
    """
    Atomically inserts or conditionally updates a `communication_events` record
    asserting `communication_events.entity_version < EXCLUDED.entity_version`.
    """
    now = datetime.now(timezone.utc)
    asked = asked_at or _slack_ts_to_datetime(thread_ts) or now

    stmt = pg_insert(CommunicationEvent).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project_id,
        integration_id=integration_id,
        kind=kind,
        channel_id=channel_id,
        thread_ts=thread_ts,
        asked_at=asked,
        first_response_at=first_response_at,
        response_delay_seconds=response_delay_seconds,
        is_resolved=is_resolved,
        entity_version=incoming_version,
        created_at=now,
    )

    update_set: dict[str, Any] = {
        "kind": kind,
        "is_resolved": is_resolved,
        "entity_version": stmt.excluded.entity_version,
    }
    if first_response_at is not None:
        update_set["first_response_at"] = first_response_at
    if response_delay_seconds is not None:
        update_set["response_delay_seconds"] = response_delay_seconds

    stmt = stmt.on_conflict_do_update(
        constraint="uq_communication_thread",
        set_=update_set,
        where=CommunicationEvent.entity_version < stmt.excluded.entity_version,
    ).returning(CommunicationEvent.id)

    res = await db.execute(stmt)
    affected_id = res.scalar_one_or_none()

    if affected_id is not None:
        return VersionedMutationResult(
            applied=True,
            stale_rejected=False,
            entity_id=affected_id,
            incoming_version=incoming_version,
        )

    record_dropped_stale_event("slack")
    logger.warning(
        "STALE_OUT_OF_ORDER_REJECTED provider=slack table=communication_events "
        "channel=%s thread_ts=%s incoming_version=%s",
        channel_id,
        thread_ts,
        incoming_version,
    )
    return VersionedMutationResult(
        applied=False,
        stale_rejected=True,
        entity_id=None,
        incoming_version=incoming_version,
    )


async def ingest_slack_event_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: Mapping[str, Any],
    event_id: uuid.UUID,
    integration_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Processes a staged Slack communication webhook event in session with monotonic
    microsecond `event_ts` ordering protection.
    """
    event_dict = payload.get("event")
    ev: Mapping[str, Any] = event_dict if isinstance(event_dict, Mapping) else payload
    nested_msg = ev.get("message")
    msg_map: Mapping[str, Any] = nested_msg if isinstance(nested_msg, Mapping) else {}

    channel_id = str(ev.get("channel") or payload.get("channel_id") or "").strip()
    thread_ts = str(
        ev.get("thread_ts")
        or msg_map.get("thread_ts")
        or msg_map.get("ts")
        or ev.get("ts")
        or payload.get("thread_ts")
        or ""
    ).strip()

    if not channel_id or not thread_ts:
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:missing_slack_channel_or_thread"

    project_id = await _resolve_slack_project_id(
        db,
        tenant_id=tenant_id,
        payload=payload,
        integration_id=integration_id,
    )
    if project_id is None:
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:unlinked_slack_integration"

    incoming_version = extract_entity_version("slack", payload)
    if incoming_version is None:
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:missing_slack_event_ts"

    asked_at = _slack_ts_to_datetime(thread_ts)
    first_response_at: Optional[datetime] = None
    response_delay: Optional[int] = None
    msg_ts = _slack_ts_to_datetime(ev.get("ts") or ev.get("event_ts"))
    is_reply = ev.get("thread_ts") and ev.get("ts") and ev.get("thread_ts") != ev.get("ts")
    if is_reply and asked_at is not None and msg_ts is not None:
        first_response_at = msg_ts
        response_delay = max(0, int((msg_ts - asked_at).total_seconds()))

    is_resolved = bool(ev.get("is_resolved", False) or payload.get("is_resolved", False))
    kind = str(payload.get("kind") or "blocker_thread").strip()[:32]

    mut_res = await upsert_communication_event_versioned(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        integration_id=integration_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        incoming_version=incoming_version,
        kind=kind,
        asked_at=asked_at,
        first_response_at=first_response_at,
        response_delay_seconds=response_delay,
        is_resolved=is_resolved,
    )
    await complete_webhook_event(db, event_id, tenant_id=tenant_id)
    if not mut_res.applied:
        return f"noop:stale_slack_event:{channel_id}:{thread_ts}"
    return f"persisted_communication_event:{mut_res.entity_id}"
