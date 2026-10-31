"""
SCRUM-335: Idempotent Webhook Staging Service.

Inserts incoming webhook events into `webhook_events` table using PostgreSQL
`ON CONFLICT (provider, provider_delivery_id) DO NOTHING`.

Guarantees:
- Duplicate redeliveries are recognized and ignored cleanly.
- No unhandled unique constraint exceptions are raised.
- Events are committed immediately before returning HTTP 202.
"""

from datetime import datetime, timezone
import logging
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import WebhookEvent

logger = logging.getLogger("app.webhooks.service")


class WebhookStagingResult:
    def __init__(
        self,
        event_id: uuid.UUID | None,
        is_duplicate: bool,
        provider: str,
        delivery_id: str,
    ) -> None:
        self.event_id = event_id
        self.is_duplicate = is_duplicate
        self.provider = provider
        self.delivery_id = delivery_id


async def stage_webhook_event_idempotent(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider: str,
    provider_delivery_id: str,
    payload: dict[str, Any],
    event_type: str = "unknown",
    integration_id: uuid.UUID | None = None,
) -> WebhookStagingResult:
    """
    Atomically stages a webhook event into the database with idempotency protection.

    Returns:
        WebhookStagingResult with event_id and is_duplicate boolean flag.
    """
    prov = provider.lower().strip()
    delivery_id = provider_delivery_id.strip()
    generated_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)

    # 1. Idempotent INSERT using PostgreSQL ON CONFLICT DO NOTHING
    stmt = (
        pg_insert(WebhookEvent)
        .values(
            id=generated_id,
            tenant_id=tenant_id,
            integration_id=integration_id,
            provider=prov,
            provider_delivery_id=delivery_id,
            event_type=event_type,
            received_at=now_utc,
            payload=payload,
            status="staged",
            attempts=0,
            created_at=now_utc,
        )
        .on_conflict_do_nothing(index_elements=["provider", "provider_delivery_id"])
        .returning(WebhookEvent.id)
    )

    result = await db.execute(stmt)
    inserted_id = result.scalar_one_or_none()

    # 2. Case A: Newly inserted event
    if inserted_id is not None:
        logger.info(
            f"Staged new webhook event {inserted_id} for provider '{prov}' (delivery: {delivery_id})"
        )
        return WebhookStagingResult(
            event_id=inserted_id,
            is_duplicate=False,
            provider=prov,
            delivery_id=delivery_id,
        )

    # 3. Case B: Redelivery detected (conflict handled gracefully)
    logger.warning(
        f"Duplicate webhook delivery detected for provider '{prov}' with delivery_id '{delivery_id}'. Ignoring."
    )
    existing_id = await db.scalar(
        select(WebhookEvent.id).where(
            WebhookEvent.provider == prov,
            WebhookEvent.provider_delivery_id == delivery_id,
        )
    )

    return WebhookStagingResult(
        event_id=existing_id,
        is_duplicate=True,
        provider=prov,
        delivery_id=delivery_id,
    )
