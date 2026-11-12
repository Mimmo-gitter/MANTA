"""
SCRUM-388 / SCRUM-389 / SCRUM-390 (Tasks 19.3, 19.4 & 19.5):
Comprehensive Automated Verification Suite for Staged Webhook Recovery Sweeper,
Immediate Post-Commit Intake Enqueue, Failure-Injection Scenarios & Worker CAS Deduplication.

Test Scenarios (Design Reference: docs/staged-webhook-failure-injection-test-scenarios.md):
1. TC-SWEEP-001 / TC-STAGED-001: Stranded staged events older than 10s are claimed, pushed to Valkey/Redis
   via RPUSH, and transitioned to 'queued' within one sweep cycle (<= 15s total).
2. TC-SWEEP-002: Events younger than 10s grace threshold or in other states ('processing',
   'completed', 'failed') are untouched by the sweeper.
3. TC-SWEEP-003 / TC-STAGED-003: Redis outage during sweep triggers atomic database rollback, cleanly
   reverting events to 'staged' with no stuck 'recovering' rows.
4. TC-SWEEP-004: Subsequent sweep cycle after Redis recovery successfully claims and queues
   the previously stranded events without data loss.
5. TC-SWEEP-005 / TC-STAGED-002: Concurrency guarantee: Multiple sweeper workers running concurrently claim
   disjoint sets of events with zero duplicate enqueues (FOR UPDATE SKIP LOCKED).
6. TC-SWEEP-006 / TC-STAGED-005: Tenant isolation under PostgreSQL Row-Level Security: Multi-tenant batches
   are discovered via SECURITY DEFINER function and processed strictly within their tenant RLS context.
7. TC-SWEEP-007: Immediate intake enqueue: Normal POST /api/v1/webhooks/... traffic enqueues
   immediately to Valkey/Redis post-commit and marks rows 'queued' without waiting for the sweeper.
8. TC-SWEEP-008: Intake resilience: Redis outage during POST intake still returns HTTP 202 Accepted,
   leaves the event safely 'staged' in PostgreSQL, and the sweeper recovers it when Redis recovers.
9. TC-STAGED-004: Race condition between delayed gateway push and sweeper recovery: Valkey receives
   duplicate envelopes; Worker CAS executes exactly once and discards duplicate (zero double-processing).
10. TC-SWEEP-009: Sweeper heartbeat file generation and container healthcheck script verification.
11. TC-SWEEP-010: Sweeper background loop graceful shutdown on stop event.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text

from app.db.models.models import WebhookEvent
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.webhooks.enqueue import (
    build_webhook_envelope,
    set_redis,
)
from app.webhooks.sweeper import (
    cas_transition_to_processing,
    complete_webhook_event,
)
from tests.test_reliable_job_queue import InMemoryAsyncRedis
from workers.recovery_sweeper import (
    discover_tenants_with_stranded_events,
    run_sweeper_loop,
    sweep_once,
    write_heartbeat,
)
from workers.worker import QUEUE_DEFAULT


class FailingAsyncRedis(InMemoryAsyncRedis):
    """InMemoryAsyncRedis subclass that simulates transient Redis connection failures."""

    def __init__(self, fail_on_rpush: bool = True) -> None:
        super().__init__()
        self.fail_on_rpush = fail_on_rpush

    async def rpush(self, key: str, *values: str) -> int:
        if self.fail_on_rpush:
            raise ConnectionError("Valkey / Redis cluster unreachable (simulated outage)")
        return await super().rpush(key, *values)


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test."""
    from app.tenants.rate_limiter import registration_rate_limiter

    registration_rate_limiter.reset()
    yield
    set_redis(None)
    await async_engine.dispose()
    registration_rate_limiter.reset()


@pytest.fixture
def redis_mock() -> InMemoryAsyncRedis:
    """Provides a fresh in-memory mock Redis client."""
    client = InMemoryAsyncRedis()
    set_redis(client)
    return client


async def register_test_tenant(slug_prefix: str) -> tuple[uuid.UUID, str]:
    """Helper to register a clean tenant via /tenants/register."""
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"Org {slug_prefix} {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"admin@{slug_prefix}-{unique}.io",
        "admin_name": f"Admin {unique}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/tenants/register", json=payload)
        assert res.status_code == 201, f"Failed to register test tenant: {res.text}"
        data = res.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


# ==============================================================================
# 1. DATABASE SCHEMA & SECURITY DEFINER VERIFICATION
# ==============================================================================


@pytest.mark.asyncio
async def test_security_definer_tenant_discovery_function_exists():
    """Verify that list_tenants_with_stranded_webhooks exists in PostgreSQL pg_proc."""
    async with async_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT proname, prosecdef FROM pg_proc "
                "WHERE proname = 'list_tenants_with_stranded_webhooks';"
            )
        )
        row = res.fetchone()
        assert row is not None, "Function list_tenants_with_stranded_webhooks not found!"
        assert row[1] is True, "Function must be configured as SECURITY DEFINER!"


# ==============================================================================
# 2. SWEEPER BATCH CLAIM & VALKEY ENQUEUE VERIFICATION
# ==============================================================================


@pytest.mark.asyncio
async def test_sweeper_claims_and_queues_stranded_events(redis_mock: InMemoryAsyncRedis):
    """
    TC-SWEEP-001: Stranded staged events older than 10s are claimed, pushed to Valkey/Redis
    via RPUSH, and transitioned to 'queued' within one sweep pass.
    """
    tenant_id, _ = await register_test_tenant("sweep-queue")
    now = datetime.now(timezone.utc)

    id_old1 = uuid.uuid4()
    id_old2 = uuid.uuid4()
    id_fresh = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        ev1 = WebhookEvent(
            id=id_old1,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{id_old1.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=20),
            payload={"action": "commit", "ref": "main"},
            status="staged",
            created_at=now - timedelta(seconds=20),
        )
        ev2 = WebhookEvent(
            id=id_old2,
            tenant_id=tenant_id,
            provider="jira",
            provider_delivery_id=f"del-{id_old2.hex[:8]}",
            event_type="issue_created",
            received_at=now - timedelta(seconds=12),
            payload={"issue": "PROJ-123"},
            status="staged",
            created_at=now - timedelta(seconds=12),
        )
        ev3 = WebhookEvent(
            id=id_fresh,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{id_fresh.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=2),
            payload={"action": "fresh"},
            status="staged",
            created_at=now - timedelta(seconds=2),
        )
        session.add_all([ev1, ev2, ev3])
        await session.flush()

    # Execute sweeper pass
    result = await sweep_once(redis_mock, threshold_seconds=10.0, tenant_id=tenant_id)
    assert result.queued == 2
    assert result.redis_failures == 0

    # Verify queue contains exactly 2 messages in arq:queue:default
    assert await redis_mock.llen(QUEUE_DEFAULT) == 2
    items = await redis_mock.lrange(QUEUE_DEFAULT, 0, -1)

    parsed_envelopes = [json.loads(item) for item in items]
    enqueued_event_ids = {e["event_id"] for e in parsed_envelopes}
    assert str(id_old1) in enqueued_event_ids
    assert str(id_old2) in enqueued_event_ids
    assert str(id_fresh) not in enqueued_event_ids

    # Verify canonical envelope structure
    for env in parsed_envelopes:
        assert env["priority"] == "default"
        assert env["job_type"] == "webhook.process"
        assert env["tenant_id"] == str(tenant_id)
        assert env["version"] == 1
        assert "enqueued_at" in env

    # Verify PostgreSQL row statuses
    async with get_tenant_db_context(tenant_id) as session:
        ev1_db = await session.get(WebhookEvent, id_old1)
        ev2_db = await session.get(WebhookEvent, id_old2)
        ev3_db = await session.get(WebhookEvent, id_fresh)

        assert ev1_db is not None and ev1_db.status == "queued"
        assert ev2_db is not None and ev2_db.status == "queued"
        assert ev3_db is not None and ev3_db.status == "staged"


@pytest.mark.asyncio
async def test_sweeper_redis_outage_reverts_to_staged():
    """
    TC-SWEEP-003 & TC-SWEEP-004:
    If Valkey/Redis is unreachable, the transaction rolls back cleanly,
    reverting all claimed events to 'staged' (no stuck 'recovering' rows).
    Once Redis recovers, the next sweep cycle successfully recovers them.
    """
    tenant_id, _ = await register_test_tenant("sweep-outage")
    now = datetime.now(timezone.utc)
    id_event = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        ev = WebhookEvent(
            id=id_event,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{id_event.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=25),
            payload={"commit": "abc"},
            status="staged",
            created_at=now - timedelta(seconds=25),
        )
        session.add(ev)
        await session.flush()

    # 1. Sweep with failing Redis client
    failing_redis = FailingAsyncRedis(fail_on_rpush=True)
    res_fail = await sweep_once(failing_redis, threshold_seconds=10.0, tenant_id=tenant_id)
    assert res_fail.redis_failures == 1
    assert res_fail.queued == 0

    # Verify event remains in 'staged' status (NOT 'recovering')
    async with get_tenant_db_context(tenant_id) as session:
        ev_after_fail = await session.get(WebhookEvent, id_event)
        assert ev_after_fail is not None
        assert ev_after_fail.status == "staged"

    # 2. Recover Redis connection and re-run sweeper
    healthy_redis = InMemoryAsyncRedis()
    res_ok = await sweep_once(healthy_redis, threshold_seconds=10.0, tenant_id=tenant_id)
    assert res_ok.queued == 1
    assert res_ok.redis_failures == 0
    assert await healthy_redis.llen(QUEUE_DEFAULT) == 1

    # Verify event transitioned to 'queued'
    async with get_tenant_db_context(tenant_id) as session:
        ev_after_recovery = await session.get(WebhookEvent, id_event)
        assert ev_after_recovery is not None
        assert ev_after_recovery.status == "queued"


@pytest.mark.asyncio
async def test_concurrent_sweepers_claim_disjoint_sets(redis_mock: InMemoryAsyncRedis):
    """
    TC-SWEEP-005: Two concurrent sweepers claiming the same queue simultaneously
    process disjoint subsets with zero duplicate messages pushed to Valkey.
    """
    tenant_id, _ = await register_test_tenant("sweep-concur")
    now = datetime.now(timezone.utc)

    # Insert 20 stranded staged events
    event_ids = [uuid.uuid4() for _ in range(20)]
    async with get_tenant_db_context(tenant_id) as session:
        for eid in event_ids:
            session.add(
                WebhookEvent(
                    id=eid,
                    tenant_id=tenant_id,
                    provider="github",
                    provider_delivery_id=f"del-{eid.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=30),
                    payload={"idx": eid.hex[:4]},
                    status="staged",
                    created_at=now - timedelta(seconds=30),
                )
            )
        await session.flush()

    # Run two sweep passes concurrently
    r1, r2 = await asyncio.gather(
        sweep_once(redis_mock, threshold_seconds=10.0, batch_size=10, tenant_id=tenant_id),
        sweep_once(redis_mock, threshold_seconds=10.0, batch_size=10, tenant_id=tenant_id),
    )

    total_queued = r1.queued + r2.queued
    assert total_queued == 20
    assert await redis_mock.llen(QUEUE_DEFAULT) == 20

    # Verify all 20 unique event IDs were enqueued with zero duplicates
    items = await redis_mock.lrange(QUEUE_DEFAULT, 0, -1)
    enqueued_ids = [json.loads(it)["event_id"] for it in items]
    assert len(enqueued_ids) == 20
    assert len(set(enqueued_ids)) == 20


@pytest.mark.asyncio
async def test_multi_tenant_discovery_and_rls_isolation(redis_mock: InMemoryAsyncRedis):
    """
    TC-SWEEP-006: Events across multiple tenants are correctly discovered
    via list_tenants_with_stranded_webhooks and processed under their respective RLS contexts.
    """
    tenant_a, _ = await register_test_tenant("sweep-ten-a")
    tenant_b, _ = await register_test_tenant("sweep-ten-b")
    now = datetime.now(timezone.utc)

    id_a = uuid.uuid4()
    id_b = uuid.uuid4()

    async with get_tenant_db_context(tenant_a) as session:
        session.add(
            WebhookEvent(
                id=id_a,
                tenant_id=tenant_a,
                provider="github",
                provider_delivery_id=f"del-{id_a.hex[:8]}",
                event_type="push",
                received_at=now - timedelta(seconds=20),
                payload={"org": "a"},
                status="staged",
                created_at=now - timedelta(seconds=20),
            )
        )
        await session.flush()

    async with get_tenant_db_context(tenant_b) as session:
        session.add(
            WebhookEvent(
                id=id_b,
                tenant_id=tenant_b,
                provider="slack",
                provider_delivery_id=f"del-{id_b.hex[:8]}",
                event_type="message",
                received_at=now - timedelta(seconds=20),
                payload={"org": "b"},
                status="staged",
                created_at=now - timedelta(seconds=20),
            )
        )
        await session.flush()

    # Discover tenants
    discovered = await discover_tenants_with_stranded_events(cutoff=now)
    assert tenant_a in discovered
    assert tenant_b in discovered

    # Sweep all discovered tenants
    result = await sweep_once(redis_mock, threshold_seconds=10.0)
    assert result.queued >= 2
    assert result.redis_failures == 0

    # Verify both events transitioned to 'queued'
    async with get_tenant_db_context(tenant_a) as session:
        ev_a = await session.get(WebhookEvent, id_a)
        assert ev_a is not None and ev_a.status == "queued"

    async with get_tenant_db_context(tenant_b) as session:
        ev_b = await session.get(WebhookEvent, id_b)
        assert ev_b is not None and ev_b.status == "queued"


# ==============================================================================
# 3. IMMEDIATE INTAKE ENQUEUE & RESILIENCE
# ==============================================================================


@pytest.mark.asyncio
async def test_intake_route_enqueues_immediately_to_valkey(redis_mock: InMemoryAsyncRedis):
    """
    TC-SWEEP-007: Normal POST webhook intake immediately enqueues the event to
    Valkey/Redis and marks the status 'queued' post-commit without sweeper intervention.
    """
    tenant_id, _ = await register_test_tenant("intake-immediate")
    payload = {"repository": {"name": "test-repo"}, "action": "opened"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/webhooks/generic",
            headers={"X-Tenant-ID": str(tenant_id), "Content-Type": "application/json"},
            json=payload,
        )
        assert res.status_code == 202
        body = res.json()
        assert body["status"] == "accepted"
        assert body["duplicate"] is False
        event_id = uuid.UUID(body["event_id"])

    # Verify immediately pushed to Redis
    assert await redis_mock.llen(QUEUE_DEFAULT) == 1
    raw_item = (await redis_mock.lrange(QUEUE_DEFAULT, 0, -1))[0]
    envelope = json.loads(raw_item)
    assert envelope["event_id"] == str(event_id)
    assert envelope["tenant_id"] == str(tenant_id)
    assert envelope["priority"] == "default"

    # Verify PostgreSQL row status is already 'queued'
    async with get_tenant_db_context(tenant_id) as session:
        ev = await session.get(WebhookEvent, event_id)
        assert ev is not None
        assert ev.status == "queued"


@pytest.mark.asyncio
async def test_intake_route_resilient_to_redis_failure():
    """
    TC-SWEEP-008: If Valkey/Redis is unreachable during POST webhook intake,
    HTTP 202 is STILL returned, the event is safely 'staged' in PostgreSQL,
    and the recovery sweeper recovers it when Redis recovers.
    """
    tenant_id, _ = await register_test_tenant("intake-resilient")
    payload = {"repository": {"name": "test-repo"}, "action": "closed"}

    # Mock Redis to fail
    failing_redis = FailingAsyncRedis(fail_on_rpush=True)
    set_redis(failing_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/webhooks/generic",
            headers={"X-Tenant-ID": str(tenant_id), "Content-Type": "application/json"},
            json=payload,
        )
        # Endpoint MUST NOT return 500/503 on Redis failure; event is durable in Postgres!
        assert res.status_code == 202
        event_id = uuid.UUID(res.json()["event_id"])

    # Event remains in 'staged' status
    async with get_tenant_db_context(tenant_id) as session:
        ev = await session.get(WebhookEvent, event_id)
        assert ev is not None
        assert ev.status == "staged"

        # Simulate time aging to >10s
        ev.received_at = datetime.now(timezone.utc) - timedelta(seconds=15)
        await session.flush()

    # Now recover Redis and run sweeper
    healthy_redis = InMemoryAsyncRedis()
    set_redis(healthy_redis)

    res_sweep = await sweep_once(healthy_redis, threshold_seconds=10.0, tenant_id=tenant_id)
    assert res_sweep.queued == 1
    assert await healthy_redis.llen(QUEUE_DEFAULT) == 1

    # Verify event transitioned to 'queued'
    async with get_tenant_db_context(tenant_id) as session:
        ev_recovered = await session.get(WebhookEvent, event_id)
        assert ev_recovered is not None
        assert ev_recovered.status == "queued"


@pytest.mark.asyncio
async def test_gateway_crash_after_db_commit_recovers_via_sweeper(redis_mock: InMemoryAsyncRedis):
    """
    TC-STAGED-001: Gateway inserts 'staged' row into PostgreSQL and crashes before
    pushing to Valkey/Redis. The stranded row lingers in 'staged', is claimed by
    the recovery sweeper (>10s threshold), pushed to Redis, and claimed via atomic CAS.
    """
    tenant_id, _ = await register_test_tenant("crash-rec")
    now = datetime.now(timezone.utc)
    event_id = uuid.uuid4()

    # 1. Gateway commits 'staged' row, then crashes immediately (never calls redis.rpush)
    async with get_tenant_db_context(tenant_id) as session:
        ev = WebhookEvent(
            id=event_id,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{event_id.hex[:8]}",
            event_type="pull_request",
            received_at=now - timedelta(seconds=12),  # > 10s grace threshold
            payload={"action": "opened", "number": 42},
            status="staged",
            created_at=now - timedelta(seconds=12),
        )
        session.add(ev)
        await session.flush()

    # Redis queue remains completely empty
    assert await redis_mock.llen(QUEUE_DEFAULT) == 0

    # 2. Sweeper runs tick and claims stranded event
    result = await sweep_once(redis_mock, threshold_seconds=10.0, tenant_id=tenant_id)
    assert result.queued == 1
    assert await redis_mock.llen(QUEUE_DEFAULT) == 1

    # 3. Worker pops envelope from Redis and executes atomic CAS
    raw_envelope = await redis_mock.lpop(QUEUE_DEFAULT)
    assert raw_envelope is not None
    envelope = json.loads(raw_envelope)
    assert envelope["event_id"] == str(event_id)

    async with get_tenant_db_context(tenant_id) as session:
        cas_ok = await cas_transition_to_processing(session, event_id, tenant_id=tenant_id)
        assert cas_ok is True

        # Transition to completed
        await complete_webhook_event(session, event_id, tenant_id=tenant_id)

    # 4. Assert final terminal state and 0 duplicates
    async with get_tenant_db_context(tenant_id) as session:
        res = await session.execute(
            text("SELECT status, attempts FROM webhook_events WHERE id = :id"),
            {"id": event_id},
        )
        row = res.fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert row[1] == 1


@pytest.mark.asyncio
async def test_race_delayed_gateway_push_and_sweeper_recovery_cas_deduplication(
    redis_mock: InMemoryAsyncRedis,
):
    """
    TC-STAGED-004: Race condition between delayed gateway push and sweeper recovery.
    Valkey queue holds duplicate envelopes (Copy A & Copy B); Worker CAS serializes
    execution such that exactly one worker acquires processing claim while the second
    worker detects duplicate and safely discards it (zero double-processing).
    """
    tenant_id, _ = await register_test_tenant("race-cas")
    now = datetime.now(timezone.utc)
    event_id = uuid.uuid4()

    # Staged event (>10s old)
    async with get_tenant_db_context(tenant_id) as session:
        ev = WebhookEvent(
            id=event_id,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{event_id.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=15),
            payload={"ref": "main"},
            status="staged",
            created_at=now - timedelta(seconds=15),
        )
        session.add(ev)
        await session.flush()

    # 1. Sweeper recovers event -> pushes Copy A, marks queued
    res = await sweep_once(redis_mock, threshold_seconds=10.0, tenant_id=tenant_id)
    assert res.queued == 1

    # 2. Delayed gateway wakes up after 12s stall -> pushes Copy B
    envelope_b = build_webhook_envelope(
        event_id=event_id,
        tenant_id=tenant_id,
        provider="github",
        event_type="push",
    )
    await redis_mock.rpush(QUEUE_DEFAULT, envelope_b)

    # Queue now holds 2 envelopes for the same event
    assert await redis_mock.llen(QUEUE_DEFAULT) == 2

    # 3. Worker 1 pops Copy A and executes CAS -> WINS
    async with get_tenant_db_context(tenant_id) as session:
        cas_w1 = await cas_transition_to_processing(session, event_id, tenant_id=tenant_id)
        assert cas_w1 is True

    # 4. Worker 2 pops Copy B and executes CAS -> LOSES (0 rows updated)
    async with get_tenant_db_context(tenant_id) as session:
        cas_w2 = await cas_transition_to_processing(session, event_id, tenant_id=tenant_id)
        assert cas_w2 is False  # DUPLICATE REJECTED AT DB LEVEL!

    # 5. Worker 1 completes the job
    async with get_tenant_db_context(tenant_id) as session:
        await complete_webhook_event(session, event_id, tenant_id=tenant_id)
        res = await session.execute(
            text("SELECT status, attempts FROM webhook_events WHERE id = :id"),
            {"id": event_id},
        )
        row = res.fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert row[1] == 1


# ==============================================================================
# 4. SWEEPER LOOP, HEARTBEAT & HEALTHCHECK MONITORING
# ==============================================================================


@pytest.mark.asyncio
async def test_sweeper_heartbeat_and_healthcheck(tmp_path: Path):
    """
    TC-SWEEP-009: Sweeper heartbeat file writing and docker-compose healthcheck script logic.
    """
    hb_file = tmp_path / "sweeper.heartbeat"

    # 1. Before heartbeat is written: healthcheck command fails
    assert not hb_file.exists()
    # Check logic: time.time() - os.path.getmtime(hb_file) < 30
    is_healthy = hb_file.exists() and (time.time() - os.path.getmtime(hb_file)) < 30
    assert is_healthy is False

    # 2. Write heartbeat
    write_heartbeat(hb_file)
    assert hb_file.exists()
    is_healthy = hb_file.exists() and (time.time() - os.path.getmtime(hb_file)) < 30
    assert is_healthy is True

    # 3. Simulate stale heartbeat file (written 40 seconds ago)
    stale_time = time.time() - 40
    os.utime(hb_file, (stale_time, stale_time))
    is_healthy = hb_file.exists() and (time.time() - os.path.getmtime(hb_file)) < 30
    assert is_healthy is False


@pytest.mark.asyncio
async def test_sweeper_loop_execution_and_stop_event(
    redis_mock: InMemoryAsyncRedis,
    tmp_path: Path,
):
    """
    TC-SWEEP-010: Sweeper background loop executes iterations, updates heartbeat,
    and terminates cleanly on stop_event.
    """
    hb_file = tmp_path / "sweeper.heartbeat"
    stop_event = asyncio.Event()

    # Run for 2 iterations
    iterations = await run_sweeper_loop(
        redis_mock,
        stop_event,
        interval=0.01,
        heartbeat_path=hb_file,
        max_iterations=2,
    )
    assert iterations == 2
    assert hb_file.exists()
    assert (time.time() - os.path.getmtime(hb_file)) < 5
