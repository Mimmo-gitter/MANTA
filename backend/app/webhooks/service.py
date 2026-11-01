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
import json
import logging
from typing import Any
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.webhooks.service")

_STAGE_WEBHOOK_SQL = text(
    """
    INSERT INTO webhook_events (
        id, tenant_id, integration_id, provider, provider_delivery_id,
        event_type, received_at, payload, status, attempts, created_at
    ) VALUES (
        :id, :tenant_id, :integration_id, :provider, :provider_delivery_id,
        :event_type, :received_at, CAST(:payload_json AS jsonb), 'staged', 0, :created_at
    )
    ON CONFLICT (provider, provider_delivery_id) DO NOTHING
    RETURNING id
    """
)

_SELECT_EXISTING_WEBHOOK_ID_SQL = text(
    """
    SELECT id
    FROM webhook_events
    WHERE provider = :provider
      AND provider_delivery_id = :provider_delivery_id
    LIMIT 1
    """
)


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
    raw_payload_json: str | None = None,
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
    payload_json = (
        raw_payload_json
        if raw_payload_json is not None
        else json.dumps(payload, separators=(",", ":"))
    )

    # 1. Idempotent INSERT using pre-defined parameterized SQL (ON CONFLICT DO NOTHING)
    result = await db.execute(
        _STAGE_WEBHOOK_SQL,
        {
            "id": generated_id,
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "provider": prov,
            "provider_delivery_id": delivery_id,
            "event_type": event_type,
            "received_at": now_utc,
            "payload_json": payload_json,
            "created_at": now_utc,
        },
    )
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
    existing_res = await db.execute(
        _SELECT_EXISTING_WEBHOOK_ID_SQL,
        {
            "provider": prov,
            "provider_delivery_id": delivery_id,
        },
    )
    existing_id = existing_res.scalar_one_or_none()

    return WebhookStagingResult(
        event_id=existing_id,
        is_duplicate=True,
        provider=prov,
        delivery_id=delivery_id,
    )
