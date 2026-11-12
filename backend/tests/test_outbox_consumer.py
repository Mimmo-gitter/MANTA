"""
Task 33.3: Automated unit & PostgreSQL integration tests for OutboxStreamConsumer,
two-tier deduplication (Redis 7d SET NX + PostgreSQL 30d ledger), RLS isolation,
and 30-day retention cleanup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
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
    cleanup_expired_processed_outbox_events,
    purge_expired_processed_outbox_events,
)
from app.outbox.relay import OutboxRelayWorker, build_stream_key


class _FakeRedisStreams:
    """In-memory async Redis Streams + key-value double matching Redis/Valkey 7 semantics."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[tuple[str, str], dict[str, object]] = {}
        self.kv: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self._seq = 0
        self.ack_calls: list[tuple[str, str, str]] = []
        self.set_calls: list[dict[str, object]] = []

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._seq += 1
        entry_id = f"1700000000000-{self._seq}"
        entries = self.streams.setdefault(name, [])
        entries.append((entry_id, dict(fields)))
        if maxlen is not None and not approximate and len(entries) > maxlen:
            del entries[: len(entries) - maxlen]
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
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del consumername, block
        out: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for skey in streams:
            grp = self.groups[(skey, groupname)]
            entries = self.streams.get(skey, [])
            cursor = int(grp["cursor"])  # type: ignore[arg-type]
            slice_end = len(entries) if count is None else min(len(entries), cursor + count)
            batch = entries[cursor:slice_end]
            grp["cursor"] = slice_end
            for eid, _ in batch:
                grp["pel"].add(eid)  # type: ignore[attr-defined]
            if batch:
                out.append((skey, batch))
        return out

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        grp = self.groups.get((name, groupname))
        acked = 0
        for eid in ids:
            self.ack_calls.append((name, groupname, eid))
            if grp is not None and eid in grp["pel"]:  # type: ignore[operator]
                grp["pel"].remove(eid)  # type: ignore[attr-defined]
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
        ex: int | None = None,
    ) -> bool | None:
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


class _FakeScalarOneResult:
    def __init__(self, val: object) -> None:
        self._val = val

    def scalar_one_or_none(self) -> object:
        return self._val


class _FakeDeleteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeLedgerSession:
    """In-memory async session simulating PostgreSQL INSERT ON CONFLICT (event_id) DO NOTHING."""

    def __init__(self) -> None:
        self.processed: dict[uuid.UUID, dict[str, object]] = {}
        self.side_effects: list[uuid.UUID] = []
        self._pending_processed: dict[uuid.UUID, dict[str, object]] = {}
        self._pending_side_effects: list[uuid.UUID] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt: object, params: dict[str, object] | None = None):
        sql = str(stmt)
        params = params or {}
        if "INSERT INTO processed_outbox_events" in sql:
            ev_id = uuid.UUID(str(params["event_id"]))
            if ev_id in self.processed or ev_id in self._pending_processed:
                return _FakeScalarOneResult(None)
            self._pending_processed[ev_id] = {
                "tenant_id": uuid.UUID(str(params["tenant_id"])),
                "processed_at": params["processed_at"],
            }
            return _FakeScalarOneResult(ev_id)
        if "DELETE FROM processed_outbox_events" in sql:
            cutoff = params["cutoff"]
            batch_size = int(params["batch_size"])  # type: ignore[arg-type]
            expired_ids = [
                eid
                for eid, row in sorted(
                    self.processed.items(),
                    key=lambda kv: kv[1]["processed_at"],  # type: ignore[arg-type]
                )
                if row["processed_at"] < cutoff  # type: ignore[operator]
            ][:batch_size]
            for eid in expired_ids:
                self.processed.pop(eid)
            return _FakeDeleteResult(len(expired_ids))
        raise AssertionError(f"Unexpected SQL in FakeLedgerSession: {sql}")

    async def commit(self) -> None:
        self.processed.update(self._pending_processed)
        self.side_effects.extend(self._pending_side_effects)
        self._pending_processed.clear()
        self._pending_side_effects.clear()
        self.commits += 1

    async def rollback(self) -> None:
        self._pending_processed.clear()
        self._pending_side_effects.clear()
        self.rollbacks += 1


def _build_stream_fields(event_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    return {
        "event_id": str(event_id),
        "event_type": "deployment.failed",
        "tenant_id": str(tenant_id),
        "payload": json.dumps({"event_id": str(event_id), "data": {"ok": True}}),
        "occurred_at": now.isoformat(),
    }


async def test_consumer_unit_deduplicates_tier1_and_tier2_exactly_once() -> None:
    """Task 33.3 AC: Delivering same UUIDv7 twice runs handler once and deduplicates across tiers."""
    tenant_id = uuid.uuid4()
    event_id = generate_uuidv7()
    stream_key = build_stream_key(tenant_id)
    redis = _FakeRedisStreams()
    ledger = _FakeLedgerSession()
    executed_messages: list[OutboxStreamMessage] = []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_factory(_tid: uuid.UUID):
        try:
            yield ledger
            await ledger.commit()
        except Exception:
            await ledger.rollback()
            raise

    async def _handler(_session: object, msg: OutboxStreamMessage) -> None:
        ledger._pending_side_effects.append(msg.event_id)
        executed_messages.append(msg)

    fields = _build_stream_fields(event_id, tenant_id)
    # Publish same event_id twice (e.g., after relay crash recovery)
    entry_1 = await redis.xadd(stream_key, fields)
    entry_2 = await redis.xadd(stream_key, fields)

    consumer = OutboxStreamConsumer(
        redis_client=redis,
        tenant_session_factory=_tenant_factory,
    )
    results = await consumer.consume_stream_once(stream_key, _handler)

    assert len(results) == 2
    assert results[0].status == "processed" and results[0].acked is True
    assert results[1].status == "duplicate_redis" and results[1].acked is True

    # Downstream handler ran exactly once; single row persisted in ledger; Redis key TTL=604800
    assert len(executed_messages) == 1
    assert ledger.side_effects == [event_id]
    assert list(ledger.processed.keys()) == [event_id]
    dedup_key = build_dedup_key(tenant_id, event_id)
    assert redis.ttls[dedup_key] == DEDUP_TTL_SECONDS
    assert consumer.metrics.duplicates_skipped_redis == 1
    assert consumer.metrics.duplicates_skipped_postgres == 0
    assert redis.groups[(stream_key, consumer.group_name)]["pel"] == set()
    assert (stream_key, consumer.group_name, entry_1) in redis.ack_calls
    assert (stream_key, consumer.group_name, entry_2) in redis.ack_calls

    # Now simulate Tier-1 Redis key expiry/eviction (>7d) and 3rd duplicate delivery -> Tier-2 PG hit
    await redis.delete(dedup_key)
    entry_3 = await redis.xadd(stream_key, fields)
    results_2 = await consumer.consume_stream_once(stream_key, _handler)

    assert len(results_2) == 1
    assert results_2[0].status == "duplicate_postgres" and results_2[0].acked is True
    assert len(executed_messages) == 1  # Still 1!
    assert consumer.metrics.duplicates_skipped_postgres == 1
    assert consumer.metrics.duplicates_skipped_total == 2
    assert dedup_key in redis.kv  # Backfilled Tier-1 key
    assert (stream_key, consumer.group_name, entry_3) in redis.ack_calls


async def test_consumer_handler_failure_rolls_back_and_allows_retry() -> None:
    """Verify handler exception rolls back ledger, skips Tier-1 key & XACK, allowing safe retry."""
    tenant_id = uuid.uuid4()
    event_id = generate_uuidv7()
    stream_key = build_stream_key(tenant_id)
    redis = _FakeRedisStreams()
    ledger = _FakeLedgerSession()
    attempts = 0

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_factory(_tid: uuid.UUID):
        try:
            yield ledger
            await ledger.commit()
        except Exception:
            await ledger.rollback()
            raise

    async def _flaky_handler(_session: object, msg: OutboxStreamMessage) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Downstream handler failure")
        ledger._pending_side_effects.append(msg.event_id)

    fields = _build_stream_fields(event_id, tenant_id)
    consumer = OutboxStreamConsumer(redis_client=redis, tenant_session_factory=_tenant_factory)

    with pytest.raises(RuntimeError, match="Downstream handler failure"):
        await consumer.process_message(stream_key, "1-0", fields, _flaky_handler)

    assert ledger.rollbacks == 1
    assert event_id not in ledger.processed
    assert build_dedup_key(tenant_id, event_id) not in redis.kv
    assert redis.ack_calls == []
    assert consumer.metrics.handler_errors_total == 1

    # Retry succeeds and records once
    retry_result = await consumer.process_message(stream_key, "1-0", fields, _flaky_handler)
    assert retry_result.status == "processed"
    assert retry_result.acked is True
    assert attempts == 2
    assert list(ledger.processed.keys()) == [event_id]


async def test_consumer_rejects_cross_tenant_stream_spoofing() -> None:
    """Reject stream entry whose payload tenant_id mismatches tenant_stream:{tenant_id}."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    consumer = OutboxStreamConsumer(redis_client=_FakeRedisStreams())

    with pytest.raises(ValueError, match="Tenant mismatch"):
        await consumer.process_message(
            build_stream_key(tenant_a),
            "1-0",
            _build_stream_fields(generate_uuidv7(), tenant_b),
            lambda _s, _m: None,
        )


async def test_purge_expired_processed_outbox_events_unit() -> None:
    """Verify 30-day retention purge helper removes only expired rows."""
    now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
    tenant_id = uuid.uuid4()
    ledger = _FakeLedgerSession()
    old_id = generate_uuidv7()
    recent_id = generate_uuidv7()
    ledger.processed[old_id] = {"tenant_id": tenant_id, "processed_at": now - timedelta(days=31)}
    ledger.processed[recent_id] = {
        "tenant_id": tenant_id,
        "processed_at": now - timedelta(days=29),
    }

    deleted = await purge_expired_processed_outbox_events(
        ledger,  # type: ignore[arg-type]
        retention_days=30,
        batch_size=5000,
        now=now,
    )
    assert deleted == 1
    assert old_id not in ledger.processed
    assert recent_id in ledger.processed


async def _provision_test_tenant(slug_prefix: str) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        async with session.begin():
            res = await session.execute(
                text("SELECT tenant_id FROM provision_tenant(:name, :slug, :email, :admin_name)"),
                {
                    "name": f"Consumer Tenant {unique}",
                    "slug": f"{slug_prefix}-{unique}",
                    "email": f"admin-{unique}@consumertest.org",
                    "admin_name": f"Admin {unique}",
                },
            )
            return uuid.UUID(str(res.scalar_one()))


@pytest.mark.asyncio
async def test_relay_to_consumer_postgres_e2e_duplicate_rls_and_30d_purge() -> None:
    """Integration: Relay crash -> duplicate XADD -> Consumer dedup -> RLS isolation -> 30d purge."""
    try:
        tenant_a = await _provision_test_tenant("cons-a")
        tenant_b = await _provision_test_tenant("cons-b")
        event_id = generate_uuidv7()

        async with get_tenant_db_context(tenant_a) as session:
            session.add(
                OutboxEvent(
                    id=event_id,
                    tenant_id=tenant_a,
                    aggregate_type="deployment",
                    aggregate_id=str(uuid.uuid4()),
                    event_type="deployment.failed",
                    payload={"event_id": str(event_id), "data": {"env": "prod"}},
                    created_at=datetime.now(timezone.utc),
                    published_at=None,
                )
            )

        redis = _FakeRedisStreams()
        worker = OutboxRelayWorker(redis_client=redis)

        # 1. Relay crash after XADD & before commit
        async with get_tenant_db_context(tenant_a) as session:
            orig_commit = session.commit

            async def _crash_commit() -> None:
                raise RuntimeError("Simulated crash")

            session.commit = _crash_commit  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="Simulated crash"):
                await worker.poll_tenant_batch(session)
            session.commit = orig_commit  # type: ignore[method-assign]

        # 2. Relay restart publishes same event_id a second time onto tenant_stream:{tenant_a}
        async with get_tenant_db_context(tenant_a) as session:
            published = await worker.poll_tenant_batch(session)
            assert published == 1

        stream_key = build_stream_key(tenant_a)
        assert len(redis.streams[stream_key]) == 2
        assert (
            redis.streams[stream_key][0][1]["event_id"]
            == redis.streams[stream_key][1][1]["event_id"]
            == str(event_id)
        )

        # 3. Consumer processes both stream entries -> handler runs ONCE, 1 PG row recorded
        handled_ids: list[uuid.UUID] = []

        async def _handler(_session: object, msg: OutboxStreamMessage) -> None:
            handled_ids.append(msg.event_id)

        consumer = OutboxStreamConsumer(redis_client=redis)
        batch_1 = await consumer.consume_stream_once(stream_key, _handler)
        assert [r.status for r in batch_1] == ["processed", "duplicate_redis"]
        assert handled_ids == [event_id]

        # 4. Evict Redis Tier-1 and re-deliver -> Tier-2 PG INSERT ON CONFLICT suppresses execution
        await redis.delete(build_dedup_key(tenant_a, event_id))
        await redis.xadd(stream_key, redis.streams[stream_key][0][1])
        batch_2 = await consumer.consume_stream_once(stream_key, _handler)
        assert [r.status for r in batch_2] == ["duplicate_postgres"]
        assert handled_ids == [event_id]

        # 5. Verify tenant RLS isolation & 30-day purge on processed_outbox_events
        async with get_tenant_db_context(tenant_b) as session:
            cross = await session.get(ProcessedOutboxEvent, event_id)
            assert cross is None, "Tenant B must not read Tenant A processed_outbox_events row"

        now = datetime.now(timezone.utc)
        old_id = generate_uuidv7()
        async with get_tenant_db_context(tenant_a) as session:
            session.add(
                ProcessedOutboxEvent(
                    event_id=old_id,
                    tenant_id=tenant_a,
                    processed_at=now - timedelta(days=31),
                )
            )

        deleted = await cleanup_expired_processed_outbox_events(retention_days=30, now=now)
        assert deleted >= 1

        async with get_tenant_db_context(tenant_a) as session:
            assert await session.get(ProcessedOutboxEvent, old_id) is None
            assert await session.get(ProcessedOutboxEvent, event_id) is not None
    finally:
        await async_engine.dispose()
