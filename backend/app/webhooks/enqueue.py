"""
SCRUM-388 / Task 19.3:
Webhook -> Valkey FIFO Queue Enqueue Helpers.

Provides deterministic envelope creation and post-commit best-effort enqueue
into Valkey/Redis `arq:queue:default` via RPUSH, followed by transitioning
`webhook_events.status` from 'staged' to 'queued'.
"""

import asyncio
import logging
from typing import Any, Optional
import uuid

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import settings
from app.db.session import get_tenant_db_context
from app.webhooks.sweeper import mark_event_queued_if_staged
from workers.worker import QUEUE_DEFAULT, serialize_job_payload

logger = logging.getLogger("app.webhooks.enqueue")

REDIS_ERRORS = (RedisError, OSError, asyncio.TimeoutError, ConnectionError)
WEBHOOK_JOB_TYPE = "webhook.process"

_redis_client: Optional[aioredis.Redis] = None


def get_redis(redis_url: Optional[str] = None) -> aioredis.Redis:
    """
    Returns a lazily-initialized shared async Redis client with short timeouts
    so intake request handlers never hang on a slow or unreachable Redis broker.
    """
    global _redis_client
    if _redis_client is None:
        url = redis_url or settings.REDIS_URL
        _redis_client = aioredis.from_url(
            url,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            health_check_interval=15,
        )
    return _redis_client


def set_redis(client: Optional[Any]) -> None:
    """Allows tests or dependency injection to override or reset the redis client."""
    global _redis_client
    _redis_client = client


def build_webhook_envelope(
    *,
    event_id: uuid.UUID | str,
    tenant_id: uuid.UUID | str,
    provider: str,
    event_type: str = "unknown",
    priority: str = "default",
    payload: Optional[dict[str, Any]] = None,
) -> str:
    """
    Serializes canonical MantaJobEnvelope into deterministic JSON using
    the workers.worker.serialize_job_payload helper.
    """
    return serialize_job_payload(
        job_id=str(event_id),
        event_id=str(event_id),
        tenant_id=str(tenant_id),
        priority=priority,
        job_type=WEBHOOK_JOB_TYPE,
        provider=provider,
        payload=payload if payload is not None else {"event_type": event_type},
    )


async def enqueue_staged_event_best_effort(
    *,
    event_id: Optional[uuid.UUID],
    tenant_id: uuid.UUID,
    provider: str,
    event_type: str = "unknown",
    redis_client: Optional[Any] = None,
    priority: str = "default",
) -> bool:
    """
    Immediate post-commit enqueue into Valkey/Redis via RPUSH.
    After enqueuing, transitions status from 'staged' to 'queued'.

    If Redis is down or any transient error occurs, this function logs
    a warning and returns False without raising an exception, leaving
    the event in 'staged' status so the 5-second recovery sweeper will
    recover it.
    """
    if event_id is None:
        return False

    try:
        client = redis_client or get_redis()
        raw_envelope = build_webhook_envelope(
            event_id=event_id,
            tenant_id=tenant_id,
            provider=provider,
            event_type=event_type,
            priority=priority,
        )
        await client.rpush(QUEUE_DEFAULT, raw_envelope)

        async with get_tenant_db_context(tenant_id) as db:
            await mark_event_queued_if_staged(db, event_id, tenant_id=tenant_id)

        return True
    except Exception as exc:  # Intentionally catch-all so intake endpoint never fails on queue outage
        logger.warning(
            f"Immediate enqueue failed for webhook event {event_id} (sweeper will recover): {exc}"
        )
        return False
