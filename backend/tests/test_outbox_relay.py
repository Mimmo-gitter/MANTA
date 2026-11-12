"""
Task 33.2: Automated unit & PostgreSQL integration tests for OutboxRelayWorker.

Covers:
- Stream key & XADD payload field formatting (`event_id`, `event_type`, `tenant_id`, `payload`, `occurred_at`).
- Monotonic UUIDv7 (`ORDER BY id ASC LIMIT 100 FOR UPDATE SKIP LOCKED`) batch relay and `published_at` commit.
- Crash recovery between `XADD` and `published_at` commit: preserves exact same `event_id` (UUIDv7).
- Live PostgreSQL + `eng_app_user` FORCE RLS multi-tenant isolation & partial index plan check.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import OutboxEvent
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context
from app.outbox.relay import OutboxRelayWorker, build_stream_fields, build_stream_key
from app.outbox.service import record_outbox_event


class _RecordingStreamRedis:
    """Minimal async Redis Streams stub recording XADD calls in order."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._seq = 0

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._seq += 1
        stream_id = f"1700000000000-{self._seq}"
        self.calls.append(
            {
                "name": name,
                "fields": dict(fields),
                "maxlen": maxlen,
                "approximate": approximate,
                "stream_id": stream_id,
            }
        )
        return stream_id


class _ScalarResult:
    def __init__(self, rows: list[OutboxEvent]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[OutboxEvent]:
        return self._rows


class _FakeRelaySession:
    """In-memory async session supporting SELECT FOR UPDATE, commit crash injection, and rollback."""

    def __init__(self, rows: list[OutboxEvent], *, fail_on_commit: bool = False) -> None:
        self.rows = rows
        self.fail_on_commit = fail_on_commit
        self.executed_sql: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt: object) -> _ScalarResult:
        compiled = (
            stmt.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
            if hasattr(stmt, "compile")
            else stmt
        )
        self.executed_sql.append(str(compiled))
        pending = sorted(
            (r for r in self.rows if r.published_at is None),
            key=lambda r: r.id,
        )
        return _ScalarResult(pending[:100])

    async def commit(self) -> None:
        if self.fail_on_commit:
            raise RuntimeError("Simulated crash before published_at commit")
        self.committed = True

    async def rollback(self) -> None:
        for r in self.rows:
            r.published_at = None
        self.rolled_back = True


def _make_outbox_row(tenant_id: uuid.UUID, event_type: str = "deployment.failed") -> OutboxEvent:
    ev_id = generate_uuidv7()
    now = datetime.now(timezone.utc)
    return OutboxEvent(
        id=ev_id,
        tenant_id=tenant_id,
        aggregate_type="deployment",
        aggregate_id=str(uuid.uuid4()),
        event_type=event_type,
        payload={
            "event_id": str(ev_id),
            "event_type": event_type,
            "tenant_id": str(tenant_id),
            "occurred_at": now.isoformat(),
            "data": {"status": "FAILED"},
        },
        created_at=now,
        published_at=None,
    )


def test_build_stream_key_and_fields() -> None:
    """Verify Redis Stream naming and XADD payload fields follow Task 33.1 spec."""
    tenant_id = uuid.uuid4()
    row = _make_outbox_row(tenant_id)

    assert build_stream_key(tenant_id) == f"tenant_stream:{tenant_id}"

    fields = build_stream_fields(row)
    assert set(fields.keys()) == {"event_id", "event_type", "tenant_id", "payload", "occurred_at"}
    assert fields["event_id"] == str(row.id)
    assert fields["event_type"] == "deployment.failed"
    assert fields["tenant_id"] == str(tenant_id)
    assert fields["occurred_at"] == row.payload["occurred_at"]
    assert json.loads(fields["payload"]) == row.payload


async def test_relay_worker_unit_monotonic_xadd_and_commit() -> None:
    """Verify relay claims with ORDER BY id ASC LIMIT 100 SKIP LOCKED and XADD MAXLEN ~ 10000."""
    tenant_id = uuid.uuid4()
    rows = [_make_outbox_row(tenant_id) for _ in range(3)]
    session = _FakeRelaySession(list(reversed(rows)))
    redis = _RecordingStreamRedis()
    worker = OutboxRelayWorker(redis_client=redis)

    count = await worker.poll_tenant_batch(session)  # type: ignore[arg-type]

    assert count == 3
    assert session.committed is True
    assert session.rolled_back is False
    assert all(r.published_at is not None and r.published_at.tzinfo == timezone.utc for r in rows)

    sql_text = session.executed_sql[0]
    assert "outbox_events.published_at IS NULL" in sql_text
    assert "ORDER BY outbox_events.id ASC" in sql_text
    assert "FOR UPDATE SKIP LOCKED" in sql_text

    published_ids = [c["fields"]["event_id"] for c in redis.calls]  # type: ignore[index]
    assert published_ids == [str(r.id) for r in rows]
    assert all(c["name"] == f"tenant_stream:{tenant_id}" for c in redis.calls)
    assert all(c["maxlen"] == 10_000 and c["approximate"] is True for c in redis.calls)


async def test_relay_crash_after_xadd_before_commit_preserves_event_id() -> None:
    """Task 33.2 AC: Crash after XADD before commit re-publishes exact same UUIDv7 event_id."""
    tenant_id = uuid.uuid4()
    row = _make_outbox_row(tenant_id)
    original_event_id = str(row.id)

    crashed_session = _FakeRelaySession([row], fail_on_commit=True)
    redis = _RecordingStreamRedis()
    worker = OutboxRelayWorker(redis_client=redis)

    with pytest.raises(RuntimeError, match="Simulated crash"):
        await worker.poll_tenant_batch(crashed_session)  # type: ignore[arg-type]

    assert crashed_session.rolled_back is True
    assert row.published_at is None
    assert len(redis.calls) == 1
    assert redis.calls[0]["fields"]["event_id"] == original_event_id  # type: ignore[index]

    # Restarted relay polls again and re-publishes with identical event_id
    recovered_session = _FakeRelaySession([row], fail_on_commit=False)
    published_count = await worker.poll_tenant_batch(recovered_session)  # type: ignore[arg-type]

    assert published_count == 1
    assert recovered_session.committed is True
    assert row.published_at is not None
    assert len(redis.calls) == 2
    assert redis.calls[1]["fields"]["event_id"] == original_event_id  # type: ignore[index]


async def test_relay_run_once_discovers_and_drains_multiple_tenants() -> None:
    """Verify run_once polls each discovered tenant under its isolated session factory."""
    t_a = uuid.uuid4()
    t_b = uuid.uuid4()
    sessions_by_tenant = {
        t_a: _FakeRelaySession([_make_outbox_row(t_a), _make_outbox_row(t_a)]),
        t_b: _FakeRelaySession([_make_outbox_row(t_b)]),
    }
    redis = _RecordingStreamRedis()

    class _FakeDiscoveryResult:
        def fetchall(self) -> list[tuple[uuid.UUID]]:
            return [(t_a,), (t_b,)]

    class _FakeDiscoverySession:
        @asynccontextmanager
        async def begin(self):
            yield

        async def execute(self, *_args: object, **_kwargs: object) -> _FakeDiscoveryResult:
            return _FakeDiscoveryResult()

    @asynccontextmanager
    async def _discovery_factory():
        yield _FakeDiscoverySession()

    @asynccontextmanager
    async def _tenant_factory(tid: uuid.UUID):
        yield sessions_by_tenant[tid]

    worker = OutboxRelayWorker(
        redis_client=redis,
        tenant_session_factory=_tenant_factory,
        discovery_session_factory=_discovery_factory,
    )
    stats = await worker.run_once()

    assert stats.tenants_polled == 2
    assert stats.batches_processed == 2
    assert stats.events_published == 3
    assert stats.errors == 0
    assert {c["name"] for c in redis.calls} == {f"tenant_stream:{t_a}", f"tenant_stream:{t_b}"}


async def _provision_test_tenant(slug_prefix: str) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        async with session.begin():
            res = await session.execute(
                text("SELECT tenant_id FROM provision_tenant(:name, :slug, :email, :admin_name)"),
                {
                    "name": f"Relay Tenant {unique}",
                    "slug": f"{slug_prefix}-{unique}",
                    "email": f"admin-{unique}@relaytest.org",
                    "admin_name": f"Admin {unique}",
                },
            )
            return uuid.UUID(str(res.scalar_one()))


@pytest.mark.asyncio
async def test_relay_postgres_monotonic_rls_and_crash_recovery() -> None:
    """Integration: real PostgreSQL + eng_app_user FORCE RLS + partial index + crash replay."""
    try:
        tenant_a = await _provision_test_tenant("relay-a")
        tenant_b = await _provision_test_tenant("relay-b")
        staged_a: list[uuid.UUID] = []

        async with get_tenant_db_context(tenant_a) as session:
            for idx in range(3):
                ev = record_outbox_event(
                    session,
                    tenant_id=tenant_a,
                    event_type="deployment.failed",
                    payload={"seq": idx},
                )
                assert ev.id.version == 7
                staged_a.append(ev.id)

        async with get_tenant_db_context(tenant_b) as session:
            ev_b = record_outbox_event(
                session,
                tenant_id=tenant_b,
                event_type="dora.updated",
                payload={"metric": "mttr"},
            )
            staged_b_id = ev_b.id

        redis = _RecordingStreamRedis()
        worker = OutboxRelayWorker(redis_client=redis)

        # Simulate crash between XADD and commit for Tenant A
        async with get_tenant_db_context(tenant_a) as session:
            orig_commit = session.commit

            async def _crash_commit() -> None:
                raise RuntimeError("Injected post-XADD crash")

            session.commit = _crash_commit  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="Injected post-XADD crash"):
                await worker.poll_tenant_batch(session)
            session.commit = orig_commit  # type: ignore[method-assign]

        crashed_event_ids = [c["fields"]["event_id"] for c in redis.calls]  # type: ignore[index]
        assert crashed_event_ids == [str(eid) for eid in staged_a]

        # Normal full relay run discovers both tenants under RLS and commits published_at
        stats = await worker.run_once()
        assert stats.errors == 0
        assert stats.events_published >= 4

        republished_a = [
            c["fields"]["event_id"]  # type: ignore[index]
            for c in redis.calls[len(staged_a) :]
            if c["name"] == f"tenant_stream:{tenant_a}"
        ]
        assert republished_a == [str(eid) for eid in staged_a]
        assert any(
            c["name"] == f"tenant_stream:{tenant_b}" and c["fields"]["event_id"] == str(staged_b_id)  # type: ignore[index]
            for c in redis.calls
        )

        # Verify published_at IS NOT NULL and partial index ix_outbox_events_unpublished_id exists
        async with get_tenant_db_context(tenant_a) as session:
            for eid in staged_a:
                row = await session.get(OutboxEvent, eid)
                assert row is not None and row.published_at is not None

            idx_res = await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'outbox_events' "
                    "AND indexname = 'ix_outbox_events_unpublished_id';"
                )
            )
            indexdef = idx_res.scalar_one_or_none()
            assert indexdef is not None
            assert "WHERE (published_at IS NULL)" in indexdef
    finally:
        await async_engine.dispose()
