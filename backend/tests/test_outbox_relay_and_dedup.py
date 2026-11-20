"""
SCRUM-372 / SCRUM-484: Automated pytest tests verifying restarted relay re-publishes
with identical UUIDv7 and consumer applies the event once (Task 33.5).

Objective:
Write automated pytest tests verifying Outbox Relay XADD publication,
mid-flight crash recovery with identical UUIDv7, and two-tier consumer deduplication.

Implementation Scope:
- Simulate relay termination immediately after XADD (before published_at update/commit).
- Run the relay worker a second time (restarted relay).
- Assertions:
  * Both stream messages carry the identical event_id (UUIDv7).
  * OutboxStreamConsumer invokes the downstream callback exactly once.
  * processed_outbox_events contains 1 record and outbox_events.published_at is set.
- Two-tier deduplication verification:
  * Tier-1: Redis fast path (outbox:dedup:{tenant_id}:{event_id} with 7-day TTL).
  * Tier-2: PostgreSQL durable ledger (processed_outbox_events with INSERT ON CONFLICT DO NOTHING).
- Edge cases covered:
  * Cache eviction fallback to Tier-2 ledger.
  * Batch publishing preserving monotonic UUIDv7 ordering across crash recovery.
  * Handler exception rollback atomicity & safe retry.
  * Multi-tenant RLS stream and ledger isolation.
  * End-to-end integration test with live PostgreSQL (executed in CI).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from typing import Any, Callable, Optional
import uuid

import pytest
from redis.exceptions import ResponseError
from sqlalchemy import text

from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import OutboxEvent, ProcessedOutboxEvent
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context
from app.outbox.consumer import (
    DEDUP_TTL_SECONDS,
    OutboxStreamConsumer,
    OutboxStreamMessage,
    build_dedup_key,
)
from app.outbox.relay import (
    OutboxRelayWorker,
    build_stream_key,
)
from app.outbox.service import record_outbox_event


# ---------------------------------------------------------------------------
# Test Doubles: In-Memory Redis Streams + In-Memory Outbox & Ledger Database
# ---------------------------------------------------------------------------


class _FakeRedisStreams:
    """In-memory async Redis Streams double supporting XADD, XREADGROUP, XACK, SET, EXISTS, DELETE."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[tuple[str, str], dict[str, Any]] = {}
        self.kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self._seq = 0
        self.ack_calls: list[tuple[str, str, str]] = []
        self.set_calls: list[dict[str, Any]] = []
        self.post_xadd_hook: Optional[Callable[[str, dict[str, str]], None]] = None

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: Optional[int] = None,
        approximate: bool = True,
    ) -> str:
        self._seq += 1
        entry_id = f"1700000000000-{self._seq}"
        entries = self.streams.setdefault(name, [])
        entries.append((entry_id, dict(fields)))
        if maxlen is not None and not approximate and len(entries) > maxlen:
            del entries[: len(entries) - maxlen]
        if self.post_xadd_hook is not None:
            self.post_xadd_hook(name, fields)
        return entry_id

    async def xgroup_create(
        self,
        *,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> None:
        if (name, groupname) in self.groups:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        if mkstream:
            self.streams.setdefault(name, [])
        self.groups[(name, groupname)] = {
            "cursor": 0 if id == "0" else len(self.streams[name]),
            "pel": set(),
        }

    async def xreadgroup(
        self,
        *,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: Optional[int] = None,
        block: Optional[int] = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del consumername, block
        out: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for skey in streams:
            grp = self.groups.get((skey, groupname))
            if grp is None:
                continue
            entries = self.streams.get(skey, [])
            cursor = int(grp["cursor"])
            slice_end = len(entries) if count is None else min(len(entries), cursor + count)
            batch = entries[cursor:slice_end]
            grp["cursor"] = slice_end
            for eid, _ in batch:
                grp["pel"].add(eid)
            if batch:
                out.append((skey, batch))
        return out

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        grp = self.groups.get((name, groupname))
        acked = 0
        for eid in ids:
            self.ack_calls.append((name, groupname, eid))
            if grp is not None and eid in grp["pel"]:
                grp["pel"].remove(eid)
                acked += 1
        return acked

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: Optional[int] = None,
    ) -> Optional[bool]:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.kv
        self.kv.pop(key, None)
        self.ttls.pop(key, None)
        return 1 if existed else 0


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None


class _ScalarOneResult:
    def __init__(self, val: Any) -> None:
        self._val = val

    def scalar_one_or_none(self) -> Any:
        return self._val

    def scalar_one(self) -> Any:
        if self._val is None:
            raise ValueError("No row found")
        return self._val


class _InMemoryOutboxStore:
    """In-memory database holding outbox_events and processed_outbox_events tables."""

    def __init__(self) -> None:
        self.outbox_events: dict[uuid.UUID, OutboxEvent] = {}
        self.committed_published_at: dict[uuid.UUID, Optional[datetime]] = {}
        self.processed_outbox_events: dict[uuid.UUID, dict[str, Any]] = {}

    def add_outbox_event(self, event: OutboxEvent) -> None:
        self.outbox_events[event.id] = event
        self.committed_published_at[event.id] = event.published_at

    def count_processed(self, event_id: uuid.UUID) -> int:
        return 1 if event_id in self.processed_outbox_events else 0


class _InMemoryRelayAndConsumerSession:
    """AsyncSession double handling OutboxEvent queries, ProcessedOutboxEvent inserts, and crash injection."""

    def __init__(
        self,
        store: _InMemoryOutboxStore,
        tenant_id: uuid.UUID,
        *,
        fail_on_commit: bool = False,
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.fail_on_commit = fail_on_commit
        self.pending_processed: dict[uuid.UUID, dict[str, Any]] = {}
        self.executed_sql: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> Any:
        sql = str(stmt)
        self.executed_sql.append(sql)
        params = params or {}

        # 1. Outbox relay claim query: SELECT ... FROM outbox_events WHERE published_at IS NULL ORDER BY id ASC ...
        if "outbox_events" in sql and (
            "published_at IS NULL" in sql or hasattr(stmt, "whereclause")
        ):
            pending = [
                ev
                for ev_id, ev in self.store.outbox_events.items()
                if ev.tenant_id == self.tenant_id
                and self.store.committed_published_at.get(ev_id) is None
            ]
            pending.sort(key=lambda r: r.id)
            return _ScalarResult(pending[:100])

        # 2. Consumer Tier-2 claim: INSERT INTO processed_outbox_events ... ON CONFLICT (event_id) DO NOTHING RETURNING event_id
        if "INSERT INTO processed_outbox_events" in sql:
            ev_id = uuid.UUID(str(params["event_id"]))
            t_id = uuid.UUID(str(params["tenant_id"]))
            if ev_id in self.store.processed_outbox_events or ev_id in self.pending_processed:
                return _ScalarOneResult(None)  # Conflict: DO NOTHING -> returns None
            self.pending_processed[ev_id] = {
                "event_id": ev_id,
                "tenant_id": t_id,
                "processed_at": params.get("processed_at", datetime.now(timezone.utc)),
            }
            return _ScalarOneResult(ev_id)

        # 3. ProcessedOutboxEvent queries
        if "processed_outbox_events" in sql:
            ev_id_val = params.get("event_id")
            if ev_id_val is not None:
                row = self.store.processed_outbox_events.get(uuid.UUID(str(ev_id_val)))
                return _ScalarOneResult(row)
            return _ScalarResult(list(self.store.processed_outbox_events.values()))

        raise AssertionError(f"Unexpected SQL in _InMemoryRelayAndConsumerSession: {sql}")

    async def commit(self) -> None:
        if self.fail_on_commit:
            raise RuntimeError(
                "Simulated relay crash immediately after XADD before published_at commit"
            )
        # Apply published_at updates
        for ev_id, ev in self.store.outbox_events.items():
            if ev.tenant_id == self.tenant_id and ev.published_at is not None:
                self.store.committed_published_at[ev_id] = ev.published_at
        # Apply processed_outbox_events inserts
        self.store.processed_outbox_events.update(self.pending_processed)
        self.pending_processed.clear()
        self.committed = True

    async def rollback(self) -> None:
        # Revert in-memory published_at on outbox events to last committed state
        for ev_id, ev in self.store.outbox_events.items():
            if ev.tenant_id == self.tenant_id:
                ev.published_at = self.store.committed_published_at.get(ev_id)
        self.pending_processed.clear()
        self.rolled_back = True


def _create_outbox_event(
    tenant_id: uuid.UUID,
    event_type: str = "deployment.failed",
    *,
    payload: Optional[dict[str, Any]] = None,
) -> OutboxEvent:
    ev_id = generate_uuidv7()
    now = datetime.now(timezone.utc)
    ev_payload = payload or {
        "event_id": str(ev_id),
        "event_type": event_type,
        "schema_version": "1.0",
        "tenant_id": str(tenant_id),
        "occurred_at": now.isoformat(),
        "data": {"status": "FAILED", "reason": "health check timeout"},
    }
    return OutboxEvent(
        id=ev_id,
        tenant_id=tenant_id,
        aggregate_type="deployment",
        aggregate_id=str(uuid.uuid4()),
        event_type=event_type,
        payload=ev_payload,
        created_at=now,
        published_at=None,
    )


# ---------------------------------------------------------------------------
# Unit Tests: Relay Mid-Flight Crash, Restart, and Two-Tier Consumer Dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relay_crash_after_xadd_restart_publishes_identical_uuidv7_and_consumer_dedups() -> (
    None
):
    """
    Task 33.5 Core Acceptance Scenario:
    1. Simulate relay termination immediately after XADD (before published_at commit).
    2. Run the relay a second time (restarted relay).
    3. Assert both stream messages carry identical event_id (UUIDv7).
    4. Assert OutboxStreamConsumer invokes downstream callback exactly once.
    5. Assert processed_outbox_events contains 1 record and outbox_events.published_at is set.
    """
    tenant_id = uuid.uuid4()
    store = _InMemoryOutboxStore()
    redis = _FakeRedisStreams()
    event = _create_outbox_event(tenant_id)
    store.add_outbox_event(event)
    stream_key = build_stream_key(tenant_id)

    # -----------------------------------------------------------------------
    # Step 1: First relay run crashes immediately after XADD (before published_at commit)
    # -----------------------------------------------------------------------
    crashed_session = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=True)
    relay_worker = OutboxRelayWorker(redis_client=redis)

    with pytest.raises(RuntimeError, match="Simulated relay crash immediately after XADD"):
        await relay_worker.poll_tenant_batch(crashed_session)  # type: ignore[arg-type]

    assert crashed_session.rolled_back is True
    assert event.published_at is None, "published_at must remain NULL after crash rollback"
    assert len(redis.streams[stream_key]) == 1, "Redis Stream must have received entry before crash"
    first_stream_entry = redis.streams[stream_key][0]
    assert first_stream_entry[1]["event_id"] == str(event.id)

    # -----------------------------------------------------------------------
    # Step 2: Restarted relay runs a second time and successfully commits
    # -----------------------------------------------------------------------
    recovered_session = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=False)
    published_count = await relay_worker.poll_tenant_batch(recovered_session)  # type: ignore[arg-type]

    assert published_count == 1
    assert recovered_session.committed is True
    assert event.published_at is not None, (
        "outbox_events.published_at must be set after successful relay"
    )
    assert event.published_at.tzinfo == timezone.utc

    # -----------------------------------------------------------------------
    # Step 3: Assert both stream messages carry the identical event_id (UUIDv7)
    # -----------------------------------------------------------------------
    assert len(redis.streams[stream_key]) == 2
    msg_1_fields = redis.streams[stream_key][0][1]
    msg_2_fields = redis.streams[stream_key][1][1]

    assert msg_1_fields["event_id"] == str(event.id)
    assert msg_2_fields["event_id"] == str(event.id)
    assert msg_1_fields["event_id"] == msg_2_fields["event_id"], (
        "Both stream messages must carry identical event_id"
    )

    # Verify event_id is a valid RFC 9562 UUIDv7
    parsed_uuid = uuid.UUID(msg_1_fields["event_id"])
    assert parsed_uuid.version == 7, "event_id must be RFC 9562 UUIDv7"

    # -----------------------------------------------------------------------
    # Step 4: OutboxStreamConsumer processes the stream with duplicate entries
    # -----------------------------------------------------------------------
    callback_invocations: list[OutboxStreamMessage] = []

    async def _downstream_callback(_session: Any, msg: OutboxStreamMessage) -> None:
        callback_invocations.append(msg)

    @asynccontextmanager
    async def _consumer_session_factory(_tid: uuid.UUID):
        session = _InMemoryRelayAndConsumerSession(store, _tid, fail_on_commit=False)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    consumer = OutboxStreamConsumer(
        redis_client=redis,
        tenant_session_factory=_consumer_session_factory,
    )

    consume_results = await consumer.consume_stream_once(stream_key, _downstream_callback)

    assert len(consume_results) == 2
    # First delivery: processed via Tier-2 ledger + post-commit Tier-1 warmup
    assert consume_results[0].status == "processed"
    assert consume_results[0].acked is True
    # Second delivery: suppressed via Tier-1 Redis fast path
    assert consume_results[1].status == "duplicate_redis"
    assert consume_results[1].acked is True

    # -----------------------------------------------------------------------
    # Step 5: Assert downstream callback invoked exactly once, processed_outbox_events
    # has 1 record, and outbox_events.published_at is set.
    # -----------------------------------------------------------------------
    assert len(callback_invocations) == 1, "Consumer must invoke downstream callback exactly once"
    assert callback_invocations[0].event_id == event.id
    assert store.count_processed(event.id) == 1, (
        "processed_outbox_events must contain exactly 1 record"
    )
    assert event.published_at is not None, "outbox_events.published_at must be set"

    # Verify Tier-1 Redis deduplication key populated with 7-day TTL
    dedup_key = build_dedup_key(tenant_id, event.id)
    assert redis.kv.get(dedup_key) == "1"
    assert redis.ttls.get(dedup_key) == DEDUP_TTL_SECONDS
    assert consumer.metrics.duplicates_skipped_redis == 1
    assert consumer.metrics.duplicates_skipped_postgres == 0


@pytest.mark.asyncio
async def test_relay_termination_via_xadd_hook_and_consumer_deduplication() -> None:
    """
    Simulate relay process abrupt termination directly after XADD completes
    via a post-XADD hook, verifying restart and consumer exactly-once processing.
    """
    tenant_id = uuid.uuid4()
    store = _InMemoryOutboxStore()
    redis = _FakeRedisStreams()
    event = _create_outbox_event(tenant_id)
    store.add_outbox_event(event)
    stream_key = build_stream_key(tenant_id)

    # Configure post-XADD hook simulating SIGKILL / network severance immediately after XADD
    hook_fired = False

    def _crash_hook(_name: str, _fields: dict[str, str]) -> None:
        nonlocal hook_fired
        hook_fired = True
        raise ConnectionResetError("Process killed immediately after XADD")

    redis.post_xadd_hook = _crash_hook
    relay_session_1 = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=False)
    worker = OutboxRelayWorker(redis_client=redis)

    with pytest.raises(ConnectionResetError, match="Process killed immediately after XADD"):
        await worker.poll_tenant_batch(relay_session_1)  # type: ignore[arg-type]

    assert hook_fired is True
    assert event.published_at is None
    assert len(redis.streams[stream_key]) == 1

    # Remove crash hook for restarted relay
    redis.post_xadd_hook = None
    relay_session_2 = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=False)
    published = await worker.poll_tenant_batch(relay_session_2)  # type: ignore[arg-type]

    assert published == 1
    assert event.published_at is not None
    assert len(redis.streams[stream_key]) == 2
    assert (
        redis.streams[stream_key][0][1]["event_id"]
        == redis.streams[stream_key][1][1]["event_id"]
        == str(event.id)
    )

    # Consume both messages
    handled: list[uuid.UUID] = []

    async def _handler(_session: Any, msg: OutboxStreamMessage) -> None:
        handled.append(msg.event_id)

    @asynccontextmanager
    async def _factory(_tid: uuid.UUID):
        s = _InMemoryRelayAndConsumerSession(store, _tid, fail_on_commit=False)
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

    consumer = OutboxStreamConsumer(redis_client=redis, tenant_session_factory=_factory)
    results = await consumer.consume_stream_once(stream_key, _handler)

    assert len(results) == 2
    assert [r.status for r in results] == ["processed", "duplicate_redis"]
    assert handled == [event.id]
    assert store.count_processed(event.id) == 1


@pytest.mark.asyncio
async def test_consumer_tier2_fallback_when_tier1_redis_evicted() -> None:
    """
    Verify that if the Tier-1 Redis deduplication key is evicted or expired (>7d),
    the Tier-2 PostgreSQL durable ledger (INSERT ON CONFLICT DO NOTHING) catches the
    duplicate event, suppresses the downstream callback, and backfills the Tier-1 key.
    """
    tenant_id = uuid.uuid4()
    store = _InMemoryOutboxStore()
    redis = _FakeRedisStreams()
    event = _create_outbox_event(tenant_id)
    store.add_outbox_event(event)
    stream_key = build_stream_key(tenant_id)

    # 1. Simulate crash after XADD then restart to produce two identical stream messages
    worker = OutboxRelayWorker(redis_client=redis)
    session_crash = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=True)
    with pytest.raises(RuntimeError):
        await worker.poll_tenant_batch(session_crash)  # type: ignore[arg-type]

    session_restart = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=False)
    await worker.poll_tenant_batch(session_restart)  # type: ignore[arg-type]

    assert len(redis.streams[stream_key]) == 2
    entry_1_id, entry_1_fields = redis.streams[stream_key][0]
    entry_2_id, entry_2_fields = redis.streams[stream_key][1]
    assert entry_1_fields["event_id"] == entry_2_fields["event_id"] == str(event.id)

    handled_events: list[uuid.UUID] = []

    async def _callback(_session: Any, msg: OutboxStreamMessage) -> None:
        handled_events.append(msg.event_id)

    @asynccontextmanager
    async def _factory(_tid: uuid.UUID):
        s = _InMemoryRelayAndConsumerSession(store, _tid, fail_on_commit=False)
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

    consumer = OutboxStreamConsumer(redis_client=redis, tenant_session_factory=_factory)

    # 2. Process message 1 -> processed successfully
    res1 = await consumer.process_message(stream_key, entry_1_id, entry_1_fields, _callback)
    assert res1.status == "processed"
    assert len(handled_events) == 1

    # 3. Simulate Tier-1 Redis cache eviction / key expiry
    dedup_key = build_dedup_key(tenant_id, event.id)
    assert dedup_key in redis.kv
    await redis.delete(dedup_key)
    assert dedup_key not in redis.kv

    # 4. Process duplicate message 2 -> Tier-1 misses, Tier-2 PG ledger detects duplicate
    res2 = await consumer.process_message(stream_key, entry_2_id, entry_2_fields, _callback)
    assert res2.status == "duplicate_postgres"
    assert res2.acked is True

    # 5. Assertions: callback still ran only once, ledger has 1 row, Tier-1 key is re-warmed
    assert len(handled_events) == 1
    assert store.count_processed(event.id) == 1
    assert dedup_key in redis.kv, "Tier-1 key must be backfilled on Tier-2 duplicate hit"
    assert consumer.metrics.duplicates_skipped_postgres == 1
    assert consumer.metrics.duplicates_skipped_total == 1


@pytest.mark.asyncio
async def test_batch_relay_crash_recovery_preserves_monotonic_order_and_dedup() -> None:
    """
    Verify batch crash recovery:
    - 3 events staged in monotonic UUIDv7 order.
    - Relay publishes all 3 to Redis Streams via XADD but crashes before commit.
    - Restarted relay claims all 3 events in order and re-publishes with identical UUIDv7 IDs.
    - Consumer processes all 6 messages: callback runs exactly 3 times (once per unique event).
    - processed_outbox_events contains 3 records and all 3 events have published_at set.
    """
    tenant_id = uuid.uuid4()
    store = _InMemoryOutboxStore()
    redis = _FakeRedisStreams()
    events = [_create_outbox_event(tenant_id, payload={"batch_idx": idx}) for idx in range(3)]
    for ev in events:
        store.add_outbox_event(ev)
    stream_key = build_stream_key(tenant_id)

    # Strict monotonic ordering check
    assert events[0].id < events[1].id < events[2].id

    # First relay batch crashes on commit
    worker = OutboxRelayWorker(redis_client=redis)
    session_crash = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=True)
    with pytest.raises(RuntimeError):
        await worker.poll_tenant_batch(session_crash)  # type: ignore[arg-type]

    assert all(ev.published_at is None for ev in events)
    assert len(redis.streams[stream_key]) == 3

    # Restarted relay drains batch cleanly
    session_restart = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=False)
    published = await worker.poll_tenant_batch(session_restart)  # type: ignore[arg-type]

    assert published == 3
    assert all(ev.published_at is not None for ev in events)
    assert len(redis.streams[stream_key]) == 6

    # Verify pairwise identical event_ids
    original_ids = [entry[1]["event_id"] for entry in redis.streams[stream_key][:3]]
    duplicate_ids = [entry[1]["event_id"] for entry in redis.streams[stream_key][3:]]
    assert original_ids == duplicate_ids == [str(ev.id) for ev in events]

    # Consumer processes all 6 entries
    processed_event_ids: list[uuid.UUID] = []

    async def _batch_callback(_session: Any, msg: OutboxStreamMessage) -> None:
        processed_event_ids.append(msg.event_id)

    @asynccontextmanager
    async def _factory(_tid: uuid.UUID):
        s = _InMemoryRelayAndConsumerSession(store, _tid, fail_on_commit=False)
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

    consumer = OutboxStreamConsumer(redis_client=redis, tenant_session_factory=_factory)
    results = await consumer.consume_stream_once(stream_key, _batch_callback, count=10)

    assert len(results) == 6
    assert [r.status for r in results] == [
        "processed",
        "processed",
        "processed",
        "duplicate_redis",
        "duplicate_redis",
        "duplicate_redis",
    ]
    assert processed_event_ids == [ev.id for ev in events]
    assert len(processed_event_ids) == 3
    for ev in events:
        assert store.count_processed(ev.id) == 1
        assert ev.published_at is not None


@pytest.mark.asyncio
async def test_consumer_downstream_handler_exception_rolls_back_and_allows_safe_retry() -> None:
    """
    Verify atomicity: if downstream handler raises an exception, the transaction rolls back,
    Tier-1 key is NOT set, and stream message is NOT acked, enabling safe redelivery.
    """
    tenant_id = uuid.uuid4()
    store = _InMemoryOutboxStore()
    redis = _FakeRedisStreams()
    event = _create_outbox_event(tenant_id)
    store.add_outbox_event(event)
    stream_key = build_stream_key(tenant_id)

    worker = OutboxRelayWorker(redis_client=redis)
    relay_session = _InMemoryRelayAndConsumerSession(store, tenant_id, fail_on_commit=False)
    await worker.poll_tenant_batch(relay_session)  # type: ignore[arg-type]

    stream_entry_id, stream_fields = redis.streams[stream_key][0]
    dedup_key = build_dedup_key(tenant_id, event.id)

    attempts = 0

    async def _failing_handler(_session: Any, _msg: OutboxStreamMessage) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Transient handler error")

    @asynccontextmanager
    async def _factory(_tid: uuid.UUID):
        s = _InMemoryRelayAndConsumerSession(store, _tid, fail_on_commit=False)
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

    consumer = OutboxStreamConsumer(redis_client=redis, tenant_session_factory=_factory)

    # First attempt fails -> rolls back
    with pytest.raises(RuntimeError, match="Transient handler error"):
        await consumer.process_message(stream_key, stream_entry_id, stream_fields, _failing_handler)

    assert attempts == 1
    assert store.count_processed(event.id) == 0
    assert dedup_key not in redis.kv
    assert redis.ack_calls == []
    assert consumer.metrics.handler_errors_total == 1

    # Retry succeeds -> commits and warms Tier-1
    result = await consumer.process_message(
        stream_key, stream_entry_id, stream_fields, _failing_handler
    )
    assert result.status == "processed"
    assert attempts == 2
    assert store.count_processed(event.id) == 1
    assert dedup_key in redis.kv
    assert (stream_key, consumer.group_name, stream_entry_id) in redis.ack_calls


@pytest.mark.asyncio
async def test_multi_tenant_crash_recovery_isolation() -> None:
    """
    Verify multi-tenant isolation:
    - Tenant A encounters relay crash and restart (2 identical stream messages on tenant_stream:{tenant_a}).
    - Tenant B has a single normal event on tenant_stream:{tenant_b}.
    - Consumer processes both streams; Tenant A callback called once, Tenant B callback called once.
    - Tenant streams and dedup keys are completely isolated.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    store = _InMemoryOutboxStore()
    redis = _FakeRedisStreams()

    ev_a = _create_outbox_event(tenant_a, "deployment.failed")
    ev_b = _create_outbox_event(tenant_b, "deployment.succeeded")
    store.add_outbox_event(ev_a)
    store.add_outbox_event(ev_b)

    worker = OutboxRelayWorker(redis_client=redis)

    # Tenant A crashes on first run
    session_a_crash = _InMemoryRelayAndConsumerSession(store, tenant_a, fail_on_commit=True)
    with pytest.raises(RuntimeError):
        await worker.poll_tenant_batch(session_a_crash)  # type: ignore[arg-type]

    # Tenant A restarted relay succeeds
    session_a_restart = _InMemoryRelayAndConsumerSession(store, tenant_a, fail_on_commit=False)
    await worker.poll_tenant_batch(session_a_restart)  # type: ignore[arg-type]

    # Tenant B runs normally
    session_b = _InMemoryRelayAndConsumerSession(store, tenant_b, fail_on_commit=False)
    await worker.poll_tenant_batch(session_b)  # type: ignore[arg-type]

    stream_a = build_stream_key(tenant_a)
    stream_b = build_stream_key(tenant_b)

    assert len(redis.streams[stream_a]) == 2
    assert len(redis.streams[stream_b]) == 1
    assert (
        redis.streams[stream_a][0][1]["event_id"]
        == redis.streams[stream_a][1][1]["event_id"]
        == str(ev_a.id)
    )
    assert redis.streams[stream_b][0][1]["event_id"] == str(ev_b.id)

    callbacks_called: dict[uuid.UUID, int] = {tenant_a: 0, tenant_b: 0}

    async def _tenant_callback(_session: Any, msg: OutboxStreamMessage) -> None:
        callbacks_called[msg.tenant_id] += 1

    @asynccontextmanager
    async def _factory(tid: uuid.UUID):
        s = _InMemoryRelayAndConsumerSession(store, tid, fail_on_commit=False)
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

    consumer = OutboxStreamConsumer(redis_client=redis, tenant_session_factory=_factory)
    results_a = await consumer.consume_stream_once(stream_a, _tenant_callback)
    results_b = await consumer.consume_stream_once(stream_b, _tenant_callback)

    assert [r.status for r in results_a] == ["processed", "duplicate_redis"]
    assert [r.status for r in results_b] == ["processed"]
    assert callbacks_called[tenant_a] == 1
    assert callbacks_called[tenant_b] == 1
    assert store.count_processed(ev_a.id) == 1
    assert store.count_processed(ev_b.id) == 1
    assert ev_a.published_at is not None
    assert ev_b.published_at is not None


# ---------------------------------------------------------------------------
# Live PostgreSQL + Redis Integration Test (Executed in CI)
# ---------------------------------------------------------------------------


def is_live_db_available() -> bool:
    """Check if real PostgreSQL instance is reachable with configured credentials."""
    if os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true":
        return True
    try:

        async def _ping():
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        asyncio.run(_ping())
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL database service not available with configured credentials in local environment",
)


async def _provision_test_tenant(slug_prefix: str) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        async with session.begin():
            res = await session.execute(
                text("SELECT tenant_id FROM provision_tenant(:name, :slug, :email, :admin_name)"),
                {
                    "name": f"RelayDedup Tenant {unique}",
                    "slug": f"{slug_prefix}-{unique}",
                    "email": f"admin-{unique}@relaydeduptest.org",
                    "admin_name": f"Admin {unique}",
                },
            )
            return uuid.UUID(str(res.scalar_one()))


@pytest.mark.asyncio
@skip_without_live_db
async def test_live_postgres_restarted_relay_publishes_identical_uuidv7_and_consumer_dedups() -> (
    None
):
    """
    Live PostgreSQL + FORCE RLS integration test verifying:
    1. Relay crash immediately after XADD before published_at commit rolls back database.
    2. Restarted relay claims the same OutboxEvent and publishes identical UUIDv7 event_id.
    3. Both stream messages carry identical event_id (UUIDv7).
    4. OutboxStreamConsumer invokes downstream callback exactly once.
    5. processed_outbox_events contains 1 record and outbox_events.published_at is set.
    """
    try:
        tenant_id = await _provision_test_tenant("relay-dedup")
        event_id = generate_uuidv7()
        stream_key = build_stream_key(tenant_id)

        # 1. Stage an OutboxEvent row under tenant RLS isolation
        async with get_tenant_db_context(tenant_id) as session:
            ev = record_outbox_event(
                session,
                tenant_id=tenant_id,
                event_type="deployment.failed",
                payload={"service": "payment-api", "status": "FAILED"},
                event_id=event_id,
            )
            assert ev.id == event_id
            assert ev.id.version == 7

        redis = _FakeRedisStreams()
        relay_worker = OutboxRelayWorker(redis_client=redis)

        # 2. Simulate relay crash immediately after XADD before published_at commit
        async with get_tenant_db_context(tenant_id) as session:
            orig_commit = session.commit

            async def _injected_crash_commit() -> None:
                raise RuntimeError(
                    "Simulated crash immediately after XADD before published_at commit"
                )

            session.commit = _injected_crash_commit  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="Simulated crash immediately after XADD"):
                await relay_worker.poll_tenant_batch(session)
            session.commit = orig_commit  # type: ignore[method-assign]

        # Verify stream received entry, but DB rolled back published_at
        assert len(redis.streams[stream_key]) == 1
        assert redis.streams[stream_key][0][1]["event_id"] == str(event_id)

        async with get_tenant_db_context(tenant_id) as session:
            row_after_crash = await session.get(OutboxEvent, event_id)
            assert row_after_crash is not None
            assert row_after_crash.published_at is None, (
                "published_at must remain NULL after crash rollback"
            )

        # 3. Restarted relay runs a second time and successfully commits published_at
        async with get_tenant_db_context(tenant_id) as session:
            published_count = await relay_worker.poll_tenant_batch(session)
            assert published_count == 1

        # 4. Assert both stream messages carry identical event_id (UUIDv7)
        assert len(redis.streams[stream_key]) == 2
        entry_1 = redis.streams[stream_key][0]
        entry_2 = redis.streams[stream_key][1]
        assert entry_1[1]["event_id"] == str(event_id)
        assert entry_2[1]["event_id"] == str(event_id)
        assert entry_1[1]["event_id"] == entry_2[1]["event_id"]
        assert uuid.UUID(entry_1[1]["event_id"]).version == 7

        # 5. OutboxStreamConsumer processes both entries
        callback_executions: list[OutboxStreamMessage] = []

        async def _live_callback(_session: Any, msg: OutboxStreamMessage) -> None:
            callback_executions.append(msg)

        consumer = OutboxStreamConsumer(redis_client=redis)
        results = await consumer.consume_stream_once(stream_key, _live_callback)

        assert len(results) == 2
        assert results[0].status == "processed"
        assert results[1].status == "duplicate_redis"

        # 6. Assert downstream callback invoked exactly once, processed_outbox_events has 1 row,
        # and outbox_events.published_at is set.
        assert len(callback_executions) == 1
        assert callback_executions[0].event_id == event_id

        async with get_tenant_db_context(tenant_id) as session:
            # Verify outbox_events.published_at is set
            outbox_row = await session.get(OutboxEvent, event_id)
            assert outbox_row is not None
            assert outbox_row.published_at is not None

            # Verify processed_outbox_events contains 1 record
            processed_row = await session.get(ProcessedOutboxEvent, event_id)
            assert processed_row is not None
            assert processed_row.event_id == event_id
            assert processed_row.tenant_id == tenant_id

            count_res = await session.execute(
                text("SELECT count(*) FROM processed_outbox_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )
            assert count_res.scalar_one() == 1

    finally:
        await async_engine.dispose()
