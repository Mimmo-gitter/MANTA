"""
Task 33.3: Two-Tier Outbox Stream Consumer Deduplication & 30-Day Ledger Retention.

Implements `OutboxStreamConsumer` with:
- Tier-1 Redis fast path (`outbox:dedup:{tenant_id}:{event_id}` with 7-day TTL `EX 604800`)
- Tier-2 PostgreSQL durable ledger (`processed_outbox_events` with
  `INSERT ... ON CONFLICT (event_id) DO NOTHING`, 30-day retention, and tenant RLS)
- Crash-safe execution order (Tier-1 read check -> Tier-2 claim + handler in single DB tx ->
  commit -> post-commit Tier-1 `SET ... NX EX 604800` warmup + `XACK`)
- Duplicate skip metrics (Prometheus counter + structured log + in-instance counters)
- Bounded 30-day retention cleanup helpers (`purge_expired_processed_outbox_events` and
  `cleanup_expired_processed_outbox_events`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import json
import logging
from typing import Any, Literal, Optional
import uuid

from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_tenant_db_context
from app.outbox.relay import STREAM_KEY_PREFIX, get_redis

try:
    from prometheus_client import Counter
except ImportError:  # pragma: no cover - optional fallback in minimal test envs
    Counter = None  # type: ignore[assignment,misc]

logger = logging.getLogger("app.outbox.consumer")

DEDUP_KEY_PREFIX = "outbox:dedup"
DEDUP_TTL_SECONDS = 604_800  # 7 days
LEDGER_RETENTION_DAYS = 30
DEFAULT_GROUP_NAME = "outbox-consumers"
DEFAULT_CONSUMER_NAME = "consumer-1"

if Counter is not None:
    OUTBOX_DUPLICATE_SKIP_COUNTER = Counter(
        "manta_outbox_consumer_duplicates_skipped_total",
        "Total duplicate outbox stream events suppressed by consumer idempotency filters.",
        labelnames=("tier", "event_type"),
    )
else:  # pragma: no cover
    OUTBOX_DUPLICATE_SKIP_COUNTER = None

_INSERT_PROCESSED_SQL = text(
    """
    INSERT INTO processed_outbox_events (event_id, tenant_id, processed_at)
    VALUES (:event_id, :tenant_id, :processed_at)
    ON CONFLICT (event_id) DO NOTHING
    RETURNING event_id;
    """
)

_PURGE_EXPIRED_SQL = text(
    """
    DELETE FROM processed_outbox_events
    WHERE event_id IN (
        SELECT event_id
        FROM processed_outbox_events
        WHERE processed_at < :cutoff
        ORDER BY processed_at ASC
        LIMIT :batch_size
    );
    """
)

_DISCOVER_EXPIRED_TENANTS_SQL = text(
    """
    SELECT tenant_id
    FROM list_tenants_with_expired_processed_outbox_events(
        CAST(:cutoff AS timestamptz),
        CAST(:limit AS integer)
    );
    """
)


def build_dedup_key(tenant_id: uuid.UUID | str, event_id: uuid.UUID | str) -> str:
    """Return Tier-1 Redis deduplication key `outbox:dedup:{tenant_id}:{event_id}`."""
    return f"{DEDUP_KEY_PREFIX}:{tenant_id}:{event_id}"


def _decode_str(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


def _decode_stream_fields(fields: Mapping[Any, Any]) -> dict[str, str]:
    return {_decode_str(k): _decode_str(v) for k, v in fields.items()}


@dataclass(frozen=True)
class OutboxStreamMessage:
    """Parsed outbox event read from `tenant_stream:{tenant_id}`."""

    stream_key: str
    stream_entry_id: str
    event_id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: str

    @classmethod
    def from_stream_entry(
        cls,
        stream_key: Any,
        stream_entry_id: Any,
        raw_fields: Mapping[Any, Any],
    ) -> OutboxStreamMessage:
        """Decode and validate Redis Stream entry fields into an OutboxStreamMessage."""
        skey = _decode_str(stream_key)
        eid = _decode_str(stream_entry_id)
        decoded = _decode_stream_fields(raw_fields)

        if "event_id" not in decoded or "tenant_id" not in decoded:
            raise ValueError(
                f"Stream entry {eid} on {skey} missing required 'event_id' or 'tenant_id' fields."
            )

        event_uuid = uuid.UUID(decoded["event_id"])
        tenant_uuid = uuid.UUID(decoded["tenant_id"])

        stream_prefix = f"{STREAM_KEY_PREFIX}:"
        if skey.startswith(stream_prefix):
            stream_tenant = uuid.UUID(skey[len(stream_prefix) :])
            if stream_tenant != tenant_uuid:
                raise ValueError(
                    f"Tenant mismatch on stream {skey}: payload tenant_id={tenant_uuid}"
                )

        raw_payload = decoded.get("payload", "{}")
        parsed_payload = json.loads(raw_payload) if raw_payload else {}
        if not isinstance(parsed_payload, dict):
            parsed_payload = {"value": parsed_payload}

        return cls(
            stream_key=skey,
            stream_entry_id=eid,
            event_id=event_uuid,
            tenant_id=tenant_uuid,
            event_type=decoded.get("event_type", "unknown"),
            payload=parsed_payload,
            occurred_at=decoded.get("occurred_at", ""),
        )


@dataclass
class OutboxConsumerMetrics:
    """In-process counters tracking processed events and duplicate suppressions."""

    processed_total: int = 0
    duplicates_skipped_redis: int = 0
    duplicates_skipped_postgres: int = 0
    handler_errors_total: int = 0

    @property
    def duplicates_skipped_total(self) -> int:
        return self.duplicates_skipped_redis + self.duplicates_skipped_postgres


@dataclass(frozen=True)
class ConsumeResult:
    """Outcome for consuming a single Redis Stream outbox entry."""

    status: Literal["processed", "duplicate_redis", "duplicate_postgres"]
    event_id: uuid.UUID
    tenant_id: uuid.UUID
    stream_entry_id: str
    acked: bool


StreamHandler = Callable[[AsyncSession, OutboxStreamMessage], Awaitable[Any] | Any]


class OutboxStreamConsumer:
    """Two-tier idempotent consumer for `tenant_stream:{tenant_id}` Redis Streams."""

    def __init__(
        self,
        *,
        redis_client: Optional[Any] = None,
        tenant_session_factory: Optional[Callable[[uuid.UUID], Any]] = None,
        group_name: str = DEFAULT_GROUP_NAME,
        consumer_name: str = DEFAULT_CONSUMER_NAME,
        dedup_ttl_seconds: int = DEDUP_TTL_SECONDS,
    ) -> None:
        if dedup_ttl_seconds <= 0:
            raise ValueError("dedup_ttl_seconds must be a positive integer.")
        self.redis_client = redis_client
        self.tenant_session_factory = tenant_session_factory or get_tenant_db_context
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.dedup_ttl_seconds = dedup_ttl_seconds
        self.metrics = OutboxConsumerMetrics()
        self._initialized_groups: set[str] = set()

    def _get_redis(self, redis_client: Optional[Any] = None) -> Any:
        return redis_client or self.redis_client or get_redis()

    def _record_duplicate_metric(
        self,
        *,
        tier: Literal["redis", "postgres"],
        msg: OutboxStreamMessage,
    ) -> None:
        if tier == "redis":
            self.metrics.duplicates_skipped_redis += 1
        else:
            self.metrics.duplicates_skipped_postgres += 1

        if OUTBOX_DUPLICATE_SKIP_COUNTER is not None:
            OUTBOX_DUPLICATE_SKIP_COUNTER.labels(tier=tier, event_type=msg.event_type).inc()

        logger.info(
            "outbox.consumer.duplicate_skipped tier=%s event_id=%s tenant_id=%s "
            "event_type=%s stream=%s entry_id=%s total_skipped=%d",
            tier,
            msg.event_id,
            msg.tenant_id,
            msg.event_type,
            msg.stream_key,
            msg.stream_entry_id,
            self.metrics.duplicates_skipped_total,
            extra={
                "metric": "outbox_duplicate_skipped",
                "dedup_tier": tier,
                "event_id": str(msg.event_id),
                "tenant_id": str(msg.tenant_id),
                "event_type": msg.event_type,
                "stream_key": msg.stream_key,
                "stream_entry_id": msg.stream_entry_id,
            },
        )

    async def _ack_entry(self, client: Any, stream_key: str, entry_id: str) -> bool:
        if not entry_id:
            return False
        await client.xack(stream_key, self.group_name, entry_id)
        return True

    async def _warm_tier1_dedup(self, client: Any, key: str) -> None:
        await client.set(key, "1", nx=True, ex=self.dedup_ttl_seconds)

    async def ensure_consumer_group(
        self,
        stream_key: str,
        *,
        redis_client: Optional[Any] = None,
        start_id: str = "0",
    ) -> None:
        """Idempotently create the Redis Stream consumer group (`MKSTREAM`)."""
        cache_key = f"{stream_key}:{self.group_name}"
        if cache_key in self._initialized_groups:
            return

        client = self._get_redis(redis_client)
        try:
            await client.xgroup_create(
                name=stream_key,
                groupname=self.group_name,
                id=start_id,
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._initialized_groups.add(cache_key)

    async def _try_claim_tier2(
        self,
        session: AsyncSession,
        msg: OutboxStreamMessage,
    ) -> bool:
        """Attempt durable Tier-2 insert; returns True if claimed, False if duplicate."""
        res = await session.execute(
            _INSERT_PROCESSED_SQL,
            {
                "event_id": msg.event_id,
                "tenant_id": msg.tenant_id,
                "processed_at": datetime.now(timezone.utc),
            },
        )
        return res.scalar_one_or_none() is not None

    async def _execute_with_tier2_session(
        self,
        session: AsyncSession,
        msg: OutboxStreamMessage,
        handler: StreamHandler,
        *,
        commit_session: bool,
    ) -> bool:
        """Execute Tier-2 claim + downstream handler inside one transaction."""
        claimed = await self._try_claim_tier2(session, msg)
        if not claimed:
            return False

        try:
            outcome = handler(session, msg)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            self.metrics.handler_errors_total += 1
            if commit_session:
                await session.rollback()
            raise

        if commit_session:
            await session.commit()
        return True

    async def process_message(
        self,
        stream_key: Any,
        stream_entry_id: Any,
        raw_fields: Mapping[Any, Any],
        handler: StreamHandler,
        *,
        session: Optional[AsyncSession] = None,
        redis_client: Optional[Any] = None,
    ) -> ConsumeResult:
        """Process one Redis Stream entry through Tier-1 Redis + Tier-2 PG deduplication."""
        msg = OutboxStreamMessage.from_stream_entry(stream_key, stream_entry_id, raw_fields)
        client = self._get_redis(redis_client)
        dedup_key = build_dedup_key(msg.tenant_id, msg.event_id)

        # Tier-1 Redis fast path check (read-only so SIGKILL cannot drop uncommitted events)
        if await client.exists(dedup_key):
            self._record_duplicate_metric(tier="redis", msg=msg)
            acked = await self._ack_entry(client, msg.stream_key, msg.stream_entry_id)
            return ConsumeResult(
                status="duplicate_redis",
                event_id=msg.event_id,
                tenant_id=msg.tenant_id,
                stream_entry_id=msg.stream_entry_id,
                acked=acked,
            )

        # Tier-2 PostgreSQL durable ledger claim + handler inside one tenant RLS transaction
        if session is not None:
            claimed = await self._execute_with_tier2_session(
                session,
                msg,
                handler,
                commit_session=True,
            )
        else:
            async with self.tenant_session_factory(msg.tenant_id) as managed_session:
                claimed = await self._execute_with_tier2_session(
                    managed_session,
                    msg,
                    handler,
                    commit_session=False,
                )

        if not claimed:
            try:
                await self._warm_tier1_dedup(client, dedup_key)
            except Exception as exc:
                logger.warning("Failed to backfill Tier-1 key %s: %s", dedup_key, exc)
            self._record_duplicate_metric(tier="postgres", msg=msg)
            acked = await self._ack_entry(client, msg.stream_key, msg.stream_entry_id)
            return ConsumeResult(
                status="duplicate_postgres",
                event_id=msg.event_id,
                tenant_id=msg.tenant_id,
                stream_entry_id=msg.stream_entry_id,
                acked=acked,
            )

        # Post-commit: populate Tier-1 7-day key and XACK the Redis Stream entry
        await self._warm_tier1_dedup(client, dedup_key)
        acked = await self._ack_entry(client, msg.stream_key, msg.stream_entry_id)
        self.metrics.processed_total += 1
        return ConsumeResult(
            status="processed",
            event_id=msg.event_id,
            tenant_id=msg.tenant_id,
            stream_entry_id=msg.stream_entry_id,
            acked=acked,
        )

    async def poll_streams_once(
        self,
        stream_keys: Sequence[str],
        handler: StreamHandler,
        *,
        count: int = 100,
        block_ms: Optional[int] = None,
        redis_client: Optional[Any] = None,
    ) -> list[ConsumeResult]:
        """Ensure consumer groups exist, XREADGROUP pending entries, and deduplicate each."""
        if not stream_keys:
            return []

        client = self._get_redis(redis_client)
        for skey in stream_keys:
            await self.ensure_consumer_group(skey, redis_client=client)

        streams_arg = {skey: ">" for skey in stream_keys}
        raw_batches = await client.xreadgroup(
            groupname=self.group_name,
            consumername=self.consumer_name,
            streams=streams_arg,
            count=count,
            block=block_ms,
        )
        if not raw_batches:
            return []

        results: list[ConsumeResult] = []
        for raw_stream_key, entries in raw_batches:
            for entry_id, raw_fields in entries:
                result = await self.process_message(
                    raw_stream_key,
                    entry_id,
                    raw_fields,
                    handler,
                    redis_client=client,
                )
                results.append(result)
        return results

    async def consume_stream_once(
        self,
        stream_key: str,
        handler: StreamHandler,
        *,
        count: int = 100,
        block_ms: Optional[int] = None,
        redis_client: Optional[Any] = None,
    ) -> list[ConsumeResult]:
        """Consume up to `count` entries from a single `tenant_stream:{tenant_id}` stream."""
        return await self.poll_streams_once(
            [stream_key],
            handler,
            count=count,
            block_ms=block_ms,
            redis_client=redis_client,
        )


async def purge_expired_processed_outbox_events(
    session: AsyncSession,
    *,
    retention_days: int = LEDGER_RETENTION_DAYS,
    batch_size: int = 5000,
    now: Optional[datetime] = None,
    commit: bool = False,
) -> int:
    """Delete up to `batch_size` processed_outbox_events rows older than `retention_days`.

    Must execute inside an active tenant-scoped RLS session (`get_tenant_db_context`).
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive.")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")

    ref_time = now or datetime.now(timezone.utc)
    cutoff = ref_time - timedelta(days=retention_days)
    res = await session.execute(
        _PURGE_EXPIRED_SQL,
        {"cutoff": cutoff, "batch_size": batch_size},
    )
    if commit:
        await session.commit()
    return int(res.rowcount or 0)


async def cleanup_expired_processed_outbox_events(
    *,
    retention_days: int = LEDGER_RETENTION_DAYS,
    batch_size: int = 5000,
    max_batches_per_tenant: int = 100,
    tenant_limit: int = 100,
    now: Optional[datetime] = None,
    tenant_session_factory: Callable[[uuid.UUID], Any] = get_tenant_db_context,
    discovery_session_factory: Callable[[], Any] = AsyncSessionLocal,
) -> int:
    """Discover tenants with expired ledger rows (>30d) and prune under RLS isolation."""
    ref_time = now or datetime.now(timezone.utc)
    cutoff = ref_time - timedelta(days=retention_days)

    async with discovery_session_factory() as discovery_session:
        async with discovery_session.begin():
            res = await discovery_session.execute(
                _DISCOVER_EXPIRED_TENANTS_SQL,
                {"cutoff": cutoff, "limit": tenant_limit},
            )
            tenant_ids = [uuid.UUID(str(row[0])) for row in res.fetchall()]

    total_deleted = 0
    for tenant_id in tenant_ids:
        for _ in range(max_batches_per_tenant):
            async with tenant_session_factory(tenant_id) as session:
                deleted = await purge_expired_processed_outbox_events(
                    session,
                    retention_days=retention_days,
                    batch_size=batch_size,
                    now=ref_time,
                    commit=False,
                )
            total_deleted += deleted
            if deleted < batch_size:
                break

    if total_deleted > 0:
        logger.info(
            "Purged %d processed_outbox_events row(s) older than %d days.",
            total_deleted,
            retention_days,
        )
    return total_deleted
