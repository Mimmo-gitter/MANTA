"""
Task 33.2: Outbox Relay Background Worker & Redis Stream Publisher.

Polls unpublished `outbox_events` rows strictly in monotonic RFC 9562 UUIDv7 order
(`WHERE published_at IS NULL ORDER BY id ASC LIMIT 100 FOR UPDATE SKIP LOCKED`),
publishes each row to per-tenant Redis / Valkey Streams
(`tenant_stream:{row.tenant_id}` via `XADD ... MAXLEN ~ 10000`), and updates
`published_at` atomically on commit.

Multi-tenant RLS compliance:
- Application database sessions connect as `eng_app_user` (`NOBYPASSRLS`) and
  `outbox_events` enforces `FORCE ROW LEVEL SECURITY`.
- Cross-tenant polling first calls `list_tenants_with_unpublished_outbox_events`
  (`SECURITY DEFINER`, returning only `tenant_id` UUIDs) and then enters
  `get_tenant_db_context(tenant_id)` per tenant to claim and publish rows under
  full `app.current_tenant_id` isolation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import signal
from typing import Any, Optional
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.models import OutboxEvent
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context

logger = logging.getLogger("app.outbox.relay")

DEFAULT_BATCH_SIZE = 100
DEFAULT_STREAM_MAXLEN = 10_000
DEFAULT_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_TENANT_DISCOVERY_LIMIT = 100
STREAM_KEY_PREFIX = "tenant_stream"

_DISCOVER_UNPUBLISHED_TENANTS_SQL = text(
    "SELECT tenant_id FROM list_tenants_with_unpublished_outbox_events(CAST(:limit AS integer))"
)

_redis_client: Optional[Any] = None


def get_redis(redis_url: Optional[str] = None) -> aioredis.Redis:
    """Return a lazily initialized shared async Redis/Valkey client."""
    global _redis_client
    if _redis_client is None:
        url = redis_url or settings.REDIS_URL
        _redis_client = aioredis.from_url(
            url,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=15,
        )
    return _redis_client


def set_redis(client: Optional[Any]) -> None:
    """Override or reset the cached Redis/Valkey client (primarily for tests)."""
    global _redis_client
    _redis_client = client


def build_stream_key(tenant_id: uuid.UUID | str) -> str:
    """Return the per-tenant Redis Stream key `tenant_stream:{tenant_id}`."""
    return f"{STREAM_KEY_PREFIX}:{tenant_id}"


def build_stream_fields(row: OutboxEvent) -> dict[str, str]:
    """Serialize an OutboxEvent row into Redis Stream XADD string fields."""
    payload_dict = row.payload if isinstance(row.payload, dict) else {"raw": row.payload}
    occurred_at = payload_dict.get("occurred_at") if isinstance(payload_dict, dict) else None
    if not isinstance(occurred_at, str) or not occurred_at:
        created_at = row.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        occurred_at = created_at.astimezone(timezone.utc).isoformat()

    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "tenant_id": str(row.tenant_id),
        "payload": json.dumps(row.payload, separators=(",", ":"), sort_keys=True),
        "occurred_at": occurred_at,
    }


@dataclass
class OutboxRelayStats:
    """Summary metrics for a single relay polling cycle."""

    tenants_polled: int = 0
    batches_processed: int = 0
    events_published: int = 0
    errors: int = 0


class OutboxRelayWorker:
    """Asynchronous Outbox Relay polling PostgreSQL and publishing to Redis Streams."""

    def __init__(
        self,
        *,
        redis_client: Optional[Any] = None,
        tenant_session_factory: Optional[Callable[[uuid.UUID], Any]] = None,
        discovery_session_factory: Optional[Callable[[], Any]] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        stream_maxlen: int = DEFAULT_STREAM_MAXLEN,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        tenant_discovery_limit: int = DEFAULT_TENANT_DISCOVERY_LIMIT,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be a positive integer.")
        if stream_maxlen <= 0:
            raise ValueError("stream_maxlen must be a positive integer.")
        self.redis_client = redis_client
        self.tenant_session_factory = tenant_session_factory or get_tenant_db_context
        self.discovery_session_factory = discovery_session_factory or AsyncSessionLocal
        self.batch_size = batch_size
        self.stream_maxlen = stream_maxlen
        self.poll_interval = poll_interval
        self.tenant_discovery_limit = tenant_discovery_limit

    def _get_redis(self, redis_client: Optional[Any] = None) -> Any:
        return redis_client or self.redis_client or get_redis()

    async def _claim_unpublished_rows(self, session: AsyncSession) -> list[OutboxEvent]:
        """Claim up to batch_size unpublished rows ordered monotonically by UUIDv7 id."""
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.id.asc())
            .limit(self.batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def poll_tenant_batch(
        self,
        session: AsyncSession,
        redis_client: Optional[Any] = None,
    ) -> int:
        """Poll one batch on a tenant-scoped AsyncSession, XADD to Redis, and commit.

        On any failure (including a simulated crash between `XADD` and `commit`), the
        transaction rolls back so `published_at` remains `NULL` and the restarted relay
        re-publishes the row preserving the exact same `event_id` (UUIDv7).
        """
        rows = await self._claim_unpublished_rows(session)
        if not rows:
            return 0

        client = self._get_redis(redis_client)
        try:
            for row in rows:
                stream_key = build_stream_key(row.tenant_id)
                fields = build_stream_fields(row)
                await client.xadd(
                    stream_key,
                    fields,
                    maxlen=self.stream_maxlen,
                    approximate=True,
                )

            published_at = datetime.now(timezone.utc)
            for row in rows:
                row.published_at = published_at

            await session.commit()
            logger.debug("Relay published %d outbox event(s) to Redis Streams.", len(rows))
            return len(rows)
        except Exception:
            await session.rollback()
            raise

    async def discover_tenants_with_pending(self) -> list[uuid.UUID]:
        """Return tenant UUIDs that currently have unpublished outbox rows."""
        async with self.discovery_session_factory() as session:
            async with session.begin():
                res = await session.execute(
                    _DISCOVER_UNPUBLISHED_TENANTS_SQL,
                    {"limit": self.tenant_discovery_limit},
                )
                return [uuid.UUID(str(row[0])) for row in res.fetchall()]

    async def run_once(self, *, max_batches_per_tenant: int = 10) -> OutboxRelayStats:
        """Execute one cross-tenant discovery + per-tenant relay drain pass."""
        stats = OutboxRelayStats()
        try:
            tenant_ids = await self.discover_tenants_with_pending()
        except Exception as exc:
            stats.errors += 1
            logger.error("Failed to discover tenants with unpublished outbox events: %s", exc)
            return stats

        for tenant_id in tenant_ids:
            stats.tenants_polled += 1
            for _ in range(max_batches_per_tenant):
                try:
                    async with self.tenant_session_factory(tenant_id) as session:
                        published = await self.poll_tenant_batch(session)
                except Exception as exc:
                    stats.errors += 1
                    logger.warning(
                        "Outbox relay failed while processing tenant %s: %s",
                        tenant_id,
                        exc,
                    )
                    break

                if published == 0:
                    break
                stats.batches_processed += 1
                stats.events_published += published
                if published < self.batch_size:
                    break

        return stats

    async def run_forever(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Continuously poll for unpublished outbox events until stop_event is set."""
        stopper = stop_event or asyncio.Event()
        logger.info(
            "OutboxRelayWorker started (batch_size=%d, maxlen~%d, interval=%.2fs).",
            self.batch_size,
            self.stream_maxlen,
            self.poll_interval,
        )
        while not stopper.is_set():
            stats = await self.run_once()
            if stats.events_published > 0 and stats.errors == 0:
                continue
            try:
                await asyncio.wait_for(stopper.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue


async def main() -> None:
    """Standalone CLI entrypoint for running the OutboxRelayWorker daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, ValueError):
            signal.signal(sig, lambda _s, _f: stop_event.set())

    worker = OutboxRelayWorker()
    try:
        await worker.run_forever(stop_event=stop_event)
    finally:
        client = get_redis()
        aclose = getattr(client, "aclose", None) or getattr(client, "close", None)
        if aclose is not None:
            res = aclose()
            if asyncio.iscoroutine(res):
                await res
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
