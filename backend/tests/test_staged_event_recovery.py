"""
SCRUM-391 (Task 19.6):
Automated Chaos & Concurrency Verification Suite for Staged Webhook Event Recovery.

Verifies that events stranded in 'staged' state (simulating an HTTP intake gateway
crashing after PostgreSQL commit but before Valkey / Redis push) recover within 15 seconds
without duplicate processing or duplicate domain side effects.

Test Matrix:
1. TC-CHAOS-001: Gateway crash recovery SLA (<15s) and strict single execution (execution_count == 1).
2. TC-CHAOS-002: High-concurrency sweepers + workers (3 sweepers + 4 workers over 20 stranded events)
   guarantee 100% completion within 15s with zero duplicate executions.
3. TC-CHAOS-003: Race condition between delayed gateway push and sweeper recovery (duplicate envelopes);
   atomic CAS serializes execution so exactly 1 worker executes domain logic and the duplicate is safely discarded.
4. TC-CHAOS-004: Transient Redis outage during sweep tick triggers atomic DB rollback reverting to 'staged';
   subsequent recovery and worker processing completes within 15s SLA with execution_count == 1.
5. TC-CHAOS-005: Multi-tenant RLS isolation: Stranded events across multiple tenants are discovered
   and recovered under strict tenant context without cross-tenant interference.
6. TC-CHAOS-006: 10-second grace threshold isolation: Fresh events (<10s) are untouched by sweeper,
   while stranded events (>10s) are recovered and completed within 15s.
7. TC-CHAOS-FAST: In-memory chaos matrix simulation (50 events, 5 sweepers, 8 workers, jitter & races)
   asserting 100% completion within 15s budget and execution_count == 1 per event in all environments.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import random
import time
from typing import Any, Optional
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
    sweep_once,
)
from workers.worker import QUEUE_DEFAULT

logger = logging.getLogger("tests.test_staged_event_recovery")


def is_live_db_available() -> bool:
    """
    Checks if a real PostgreSQL instance is reachable with configured credentials.
    In CI / GitHub Actions, this always returns True to enforce end-to-end live testing.
    """
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


class SideEffectTracker:
    """Thread-safe & async-safe tracker for domain side-effects per event."""

    def __init__(self) -> None:
        self.executions: dict[str, int] = defaultdict(int)
        self.lock = asyncio.Lock()

    async def record_execution(self, event_id: str | uuid.UUID) -> None:
        async with self.lock:
            self.executions[str(event_id)] += 1

    def count(self, event_id: str | uuid.UUID) -> int:
        return self.executions[str(event_id)]

    def all_single_executions(self, event_ids: list[uuid.UUID]) -> bool:
        return all(self.executions[str(eid)] == 1 for eid in event_ids)


class FailingAsyncRedis(InMemoryAsyncRedis):
    """Subclass of InMemoryAsyncRedis that simulates transient Redis outages."""

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
    if LIVE_DB_AVAILABLE:
        try:
            await async_engine.dispose()
        except Exception:
            pass
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


async def run_test_worker_consumer(
    redis: Any,
    worker_id: str,
    stop_event: asyncio.Event,
    tracker: SideEffectTracker,
    *,
    poll_interval: float = 0.01,
    max_jobs: Optional[int] = None,
) -> int:
    """
    Simulates a concurrent worker consumer executing atomic Compare-And-Set (CAS).

    1. Pops envelope from Valkey / Redis queue.
    2. Performs CAS: transitions 'staged'/'queued'/'recovering' -> 'processing'.
    3. If CAS succeeds: records domain side effect in tracker and transitions to 'completed'.
    4. If CAS fails (0 rows updated): safe duplicate discard, zero side effects recorded.
    """
    jobs_processed = 0
    while not stop_event.is_set():
        if max_jobs is not None and jobs_processed >= max_jobs:
            break

        raw_envelope = await redis.lpop(QUEUE_DEFAULT)
        if raw_envelope is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass
            continue

        envelope = json.loads(raw_envelope)
        event_id = uuid.UUID(envelope["event_id"])
        tenant_id = uuid.UUID(envelope["tenant_id"])

        async with get_tenant_db_context(tenant_id) as session:
            cas_ok = await cas_transition_to_processing(session, event_id, tenant_id=tenant_id)
            if not cas_ok:
                logger.warning(
                    f"[{worker_id}] Worker CAS rejected duplicate envelope for event {event_id}."
                )
                continue

            # Record domain side effect strictly once
            await tracker.record_execution(event_id)

            # Mark completed in DB
            await complete_webhook_event(session, event_id, tenant_id=tenant_id)
            jobs_processed += 1

    return jobs_processed


async def run_test_sweeper_ticks(
    redis: Any,
    stop_event: asyncio.Event,
    *,
    interval: float = 0.05,
    threshold_seconds: float = 10.0,
    batch_size: int = 100,
    tenant_id: Optional[uuid.UUID] = None,
) -> int:
    """Continuously runs sweeper ticks until stop_event is set."""
    ticks = 0
    while not stop_event.is_set():
        try:
            await sweep_once(
                redis,
                threshold_seconds=threshold_seconds,
                batch_size=batch_size,
                tenant_id=tenant_id,
            )
            ticks += 1
        except Exception as exc:
            logger.warning(f"Sweeper tick encountered error: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
    return ticks


# ==============================================================================
# 1. LIVE POSTGRESQL CHAOS & CONCURRENCY TESTS (CI / INTEGRATION)
# ==============================================================================


@pytest.mark.asyncio
@skip_without_live_db
async def test_staged_events_recover_within_15s_single_execution(redis_mock: InMemoryAsyncRedis):
    """
    TC-CHAOS-001 (Task 19.6 Core Requirement):
    Inserts 5 staged events backdated >10s (simulating gateway crash before Redis push),
    runs concurrent sweeper ticks + worker consumers, and asserts 100% completion
    within 15 seconds and execution count == 1 per event with zero duplicate side effects.
    """
    tenant_id, _ = await register_test_tenant("chaos-sla")
    now = datetime.now(timezone.utc)
    event_ids = [uuid.uuid4() for _ in range(5)]

    # 1. Ingest 5 stranded events backdated >10s (simulating gateway crash)
    async with get_tenant_db_context(tenant_id) as session:
        for idx, eid in enumerate(event_ids):
            session.add(
                WebhookEvent(
                    id=eid,
                    tenant_id=tenant_id,
                    provider="github",
                    provider_delivery_id=f"del-chaos-{eid.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=12 + idx),
                    payload={"commit": f"sha-{idx}", "ref": "refs/heads/main"},
                    status="staged",
                    created_at=now - timedelta(seconds=12 + idx),
                )
            )
        await session.flush()

    assert await redis_mock.llen(QUEUE_DEFAULT) == 0

    # 2. Start timer for 15-second recovery SLA
    start_time = time.monotonic()
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()

    # 3. Launch concurrent sweeper ticks + 2 worker consumers
    sweeper_task = asyncio.create_task(
        run_test_sweeper_ticks(
            redis_mock,
            stop_event,
            interval=0.05,
            threshold_seconds=10.0,
            tenant_id=tenant_id,
        )
    )
    worker1_task = asyncio.create_task(
        run_test_worker_consumer(redis_mock, "worker-1", stop_event, tracker)
    )
    worker2_task = asyncio.create_task(
        run_test_worker_consumer(redis_mock, "worker-2", stop_event, tracker)
    )

    # 4. Wait until all 5 events are completed or timeout (15s SLA)
    deadline = start_time + 15.0
    all_completed = False
    count = 0

    while time.monotonic() < deadline:
        async with get_tenant_db_context(tenant_id) as session:
            res = await session.execute(
                text(
                    "SELECT count(*) FROM webhook_events WHERE tenant_id = :tid AND status = 'completed'"
                ),
                {"tid": tenant_id},
            )
            count = res.scalar()
            if count == len(event_ids):
                all_completed = True
                break
        await asyncio.sleep(0.05)

    elapsed = time.monotonic() - start_time
    stop_event.set()
    await asyncio.gather(sweeper_task, worker1_task, worker2_task, return_exceptions=True)

    # 5. Assertions: SLA, 100% completion, and execution count == 1
    assert all_completed is True, (
        f"Not all events completed within 15s. Completed: {count}/{len(event_ids)}"
    )
    assert elapsed <= 15.0, f"Recovery SLA violated: completed in {elapsed:.3f}s (> 15.0s)"

    # Verify every event executed strictly once
    for eid in event_ids:
        assert tracker.count(eid) == 1, (
            f"Event {eid} had duplicate executions: {tracker.count(eid)} != 1"
        )

    # Verify database state
    async with get_tenant_db_context(tenant_id) as session:
        for eid in event_ids:
            ev = await session.get(WebhookEvent, eid)
            assert ev is not None
            assert ev.status == "completed"
            assert ev.attempts == 1


@pytest.mark.asyncio
@skip_without_live_db
async def test_high_concurrency_sweepers_and_workers_zero_duplicates(
    redis_mock: InMemoryAsyncRedis,
):
    """
    TC-CHAOS-002:
    High-concurrency stress test: 20 backdated staged events (>10s), 3 concurrent sweepers
    competing via FOR UPDATE SKIP LOCKED and 4 concurrent workers executing CAS simultaneously.
    Asserts 100% completion within 15 seconds and execution count == 1 per event.
    """
    tenant_id, _ = await register_test_tenant("chaos-highconcur")
    now = datetime.now(timezone.utc)
    num_events = 20
    event_ids = [uuid.uuid4() for _ in range(num_events)]

    # Ingest 20 stranded events
    async with get_tenant_db_context(tenant_id) as session:
        for idx, eid in enumerate(event_ids):
            session.add(
                WebhookEvent(
                    id=eid,
                    tenant_id=tenant_id,
                    provider="github",
                    provider_delivery_id=f"del-hc-{eid.hex[:8]}",
                    event_type="pull_request",
                    received_at=now - timedelta(seconds=15 + (idx % 10)),
                    payload={"pr_number": idx},
                    status="staged",
                    created_at=now - timedelta(seconds=15 + (idx % 10)),
                )
            )
        await session.flush()

    start_time = time.monotonic()
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()

    # Launch 3 concurrent sweepers
    sweeper_tasks = [
        asyncio.create_task(
            run_test_sweeper_ticks(
                redis_mock,
                stop_event,
                interval=0.03,
                threshold_seconds=10.0,
                batch_size=10,
                tenant_id=tenant_id,
            )
        )
        for _ in range(3)
    ]

    # Launch 4 concurrent workers
    worker_tasks = [
        asyncio.create_task(
            run_test_worker_consumer(redis_mock, f"worker-{i}", stop_event, tracker)
        )
        for i in range(4)
    ]

    # Wait until all 20 events are completed or timeout (15s SLA)
    deadline = start_time + 15.0
    all_completed = False

    while time.monotonic() < deadline:
        async with get_tenant_db_context(tenant_id) as session:
            res = await session.execute(
                text(
                    "SELECT count(*) FROM webhook_events WHERE tenant_id = :tid AND status = 'completed'"
                ),
                {"tid": tenant_id},
            )
            if res.scalar() == num_events:
                all_completed = True
                break
        await asyncio.sleep(0.05)

    elapsed = time.monotonic() - start_time
    stop_event.set()
    await asyncio.gather(*sweeper_tasks, *worker_tasks, return_exceptions=True)

    assert all_completed is True, "Failed to complete all 20 events within 15 seconds"
    assert elapsed <= 15.0, f"Recovery SLA violated: {elapsed:.3f}s"

    # Zero duplicate executions across all 20 events
    for eid in event_ids:
        assert tracker.count(eid) == 1, (
            f"Event {eid} had duplicate execution count: {tracker.count(eid)}"
        )

    # In DB, verify all are completed with attempts == 1
    async with get_tenant_db_context(tenant_id) as session:
        for eid in event_ids:
            ev = await session.get(WebhookEvent, eid)
            assert ev is not None and ev.status == "completed"
            assert ev.attempts == 1


@pytest.mark.asyncio
@skip_without_live_db
async def test_delayed_gateway_race_cas_deduplication(redis_mock: InMemoryAsyncRedis):
    """
    TC-CHAOS-003:
    Simulates race between delayed gateway push and sweeper recovery:
    Valkey queue holds duplicate envelopes (Copy A & Copy B).
    2 concurrent workers pop simultaneously; worker CAS grants processing to exactly
    one worker and discards the duplicate with zero side-effects.
    """
    tenant_id, _ = await register_test_tenant("chaos-race")
    now = datetime.now(timezone.utc)
    event_id = uuid.uuid4()

    # Staged event (>10s old)
    async with get_tenant_db_context(tenant_id) as session:
        ev = WebhookEvent(
            id=event_id,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-race-{event_id.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=16),
            payload={"action": "push", "ref": "refs/heads/main"},
            status="staged",
            created_at=now - timedelta(seconds=16),
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

    # Queue now holds 2 messages for the same event
    assert await redis_mock.llen(QUEUE_DEFAULT) == 2

    start_time = time.monotonic()
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()

    # 3. Two workers consume concurrently
    w1 = asyncio.create_task(run_test_worker_consumer(redis_mock, "w1", stop_event, tracker))
    w2 = asyncio.create_task(run_test_worker_consumer(redis_mock, "w2", stop_event, tracker))

    # Wait until queue is drained
    while await redis_mock.llen(QUEUE_DEFAULT) > 0:
        await asyncio.sleep(0.01)

    # Allow CAS and DB update to finish
    await asyncio.sleep(0.1)
    stop_event.set()
    await asyncio.gather(w1, w2, return_exceptions=True)

    elapsed = time.monotonic() - start_time
    assert elapsed <= 15.0

    # 4. Strict assertions: execution count must be exactly 1
    assert tracker.count(event_id) == 1, (
        f"Race condition caused duplicate execution! count={tracker.count(event_id)}"
    )

    async with get_tenant_db_context(tenant_id) as session:
        ev_final = await session.get(WebhookEvent, event_id)
        assert ev_final is not None
        assert ev_final.status == "completed"
        assert ev_final.attempts == 1


@pytest.mark.asyncio
@skip_without_live_db
async def test_sweeper_redis_outage_rollback_and_recovery_within_15s():
    """
    TC-CHAOS-004:
    Redis outage during sweep pass triggers atomic rollback (events revert to 'staged').
    Once Redis recovers, the sweeper + workers recover and complete all events within 15s.
    """
    tenant_id, _ = await register_test_tenant("chaos-outage")
    now = datetime.now(timezone.utc)
    event_ids = [uuid.uuid4() for _ in range(3)]

    async with get_tenant_db_context(tenant_id) as session:
        for eid in event_ids:
            session.add(
                WebhookEvent(
                    id=eid,
                    tenant_id=tenant_id,
                    provider="github",
                    provider_delivery_id=f"del-outage-{eid.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=18),
                    payload={"test": "outage"},
                    status="staged",
                    created_at=now - timedelta(seconds=18),
                )
            )
        await session.flush()

    # 1. Sweep with failing Redis
    failing_redis = FailingAsyncRedis(fail_on_rpush=True)
    res_fail = await sweep_once(failing_redis, threshold_seconds=10.0, tenant_id=tenant_id)
    assert res_fail.redis_failures == 1
    assert res_fail.queued == 0

    # Verify all events cleanly reverted to 'staged' (0 stuck in 'recovering')
    async with get_tenant_db_context(tenant_id) as session:
        for eid in event_ids:
            ev = await session.get(WebhookEvent, eid)
            assert ev is not None and ev.status == "staged"

    # 2. Recover Redis connection and start 15s recovery timer
    healthy_redis = InMemoryAsyncRedis()
    set_redis(healthy_redis)
    start_time = time.monotonic()
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()

    sweeper_task = asyncio.create_task(
        run_test_sweeper_ticks(
            healthy_redis,
            stop_event,
            interval=0.05,
            threshold_seconds=10.0,
            tenant_id=tenant_id,
        )
    )
    worker_task = asyncio.create_task(
        run_test_worker_consumer(healthy_redis, "worker-rec", stop_event, tracker)
    )

    deadline = start_time + 15.0
    all_completed = False
    while time.monotonic() < deadline:
        async with get_tenant_db_context(tenant_id) as session:
            res = await session.execute(
                text(
                    "SELECT count(*) FROM webhook_events WHERE tenant_id = :tid AND status = 'completed'"
                ),
                {"tid": tenant_id},
            )
            if res.scalar() == len(event_ids):
                all_completed = True
                break
        await asyncio.sleep(0.05)

    elapsed = time.monotonic() - start_time
    stop_event.set()
    await asyncio.gather(sweeper_task, worker_task, return_exceptions=True)

    assert all_completed is True, "Events failed to complete after Redis recovery"
    assert elapsed <= 15.0, f"Recovery exceeded 15s SLA: {elapsed:.3f}s"

    for eid in event_ids:
        assert tracker.count(eid) == 1


@pytest.mark.asyncio
@skip_without_live_db
async def test_multi_tenant_staged_recovery_rls_isolation(redis_mock: InMemoryAsyncRedis):
    """
    TC-CHAOS-005:
    Stranded events across multiple tenants (Tenant Alpha & Tenant Beta) are discovered
    via list_tenants_with_stranded_webhooks and recovered concurrently under PostgreSQL RLS.
    Asserts 100% completion and execution_count == 1 per event for all tenants.
    """
    tenant_a, _ = await register_test_tenant("chaos-ten-a")
    tenant_b, _ = await register_test_tenant("chaos-ten-b")
    now = datetime.now(timezone.utc)

    id_a1 = uuid.uuid4()
    id_a2 = uuid.uuid4()
    id_b1 = uuid.uuid4()
    id_b2 = uuid.uuid4()

    async with get_tenant_db_context(tenant_a) as session:
        session.add_all(
            [
                WebhookEvent(
                    id=id_a1,
                    tenant_id=tenant_a,
                    provider="github",
                    provider_delivery_id=f"del-a1-{id_a1.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=20),
                    payload={"org": "a", "idx": 1},
                    status="staged",
                    created_at=now - timedelta(seconds=20),
                ),
                WebhookEvent(
                    id=id_a2,
                    tenant_id=tenant_a,
                    provider="github",
                    provider_delivery_id=f"del-a2-{id_a2.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=15),
                    payload={"org": "a", "idx": 2},
                    status="staged",
                    created_at=now - timedelta(seconds=15),
                ),
            ]
        )
        await session.flush()

    async with get_tenant_db_context(tenant_b) as session:
        session.add_all(
            [
                WebhookEvent(
                    id=id_b1,
                    tenant_id=tenant_b,
                    provider="slack",
                    provider_delivery_id=f"del-b1-{id_b1.hex[:8]}",
                    event_type="message",
                    received_at=now - timedelta(seconds=22),
                    payload={"org": "b", "idx": 1},
                    status="staged",
                    created_at=now - timedelta(seconds=22),
                ),
                WebhookEvent(
                    id=id_b2,
                    tenant_id=tenant_b,
                    provider="jira",
                    provider_delivery_id=f"del-b2-{id_b2.hex[:8]}",
                    event_type="issue_created",
                    received_at=now - timedelta(seconds=14),
                    payload={"org": "b", "idx": 2},
                    status="staged",
                    created_at=now - timedelta(seconds=14),
                ),
            ]
        )
        await session.flush()

    # Discover tenants holding stranded events
    discovered = await discover_tenants_with_stranded_events(cutoff=now)
    assert tenant_a in discovered
    assert tenant_b in discovered

    start_time = time.monotonic()
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()

    # Run sweeper and workers across discovered tenants
    sweeper_task = asyncio.create_task(
        run_test_sweeper_ticks(redis_mock, stop_event, interval=0.05, threshold_seconds=10.0)
    )
    worker1_task = asyncio.create_task(
        run_test_worker_consumer(redis_mock, "worker-mt-1", stop_event, tracker)
    )
    worker2_task = asyncio.create_task(
        run_test_worker_consumer(redis_mock, "worker-mt-2", stop_event, tracker)
    )

    all_target_ids = [id_a1, id_a2, id_b1, id_b2]
    deadline = start_time + 15.0
    all_completed = False
    count_a, count_b = 0, 0

    while time.monotonic() < deadline:
        async with get_tenant_db_context(tenant_a) as session:
            res_a = await session.execute(
                text(
                    "SELECT count(*) FROM webhook_events WHERE tenant_id = :tid AND status = 'completed'"
                ),
                {"tid": tenant_a},
            )
            count_a = res_a.scalar()
        async with get_tenant_db_context(tenant_b) as session:
            res_b = await session.execute(
                text(
                    "SELECT count(*) FROM webhook_events WHERE tenant_id = :tid AND status = 'completed'"
                ),
                {"tid": tenant_b},
            )
            count_b = res_b.scalar()

        if count_a == 2 and count_b == 2:
            all_completed = True
            break
        await asyncio.sleep(0.05)

    elapsed = time.monotonic() - start_time
    stop_event.set()
    await asyncio.gather(sweeper_task, worker1_task, worker2_task, return_exceptions=True)

    assert all_completed is True, f"Multi-tenant recovery failed: A={count_a}/2, B={count_b}/2"
    assert elapsed <= 15.0, f"Recovery SLA violated: {elapsed:.3f}s"

    # Execution count strictly 1 for every event
    for eid in all_target_ids:
        assert tracker.count(eid) == 1


@pytest.mark.asyncio
@skip_without_live_db
async def test_grace_period_selective_recovery(redis_mock: InMemoryAsyncRedis):
    """
    TC-CHAOS-006:
    Mixed workload: Stranded events (>10s old) vs fresh events (<10s old).
    Sweeper recovers ONLY stranded events; fresh events remain untouched in 'staged'.
    Stranded events complete within 15s with execution_count == 1; fresh events have count == 0.
    """
    tenant_id, _ = await register_test_tenant("chaos-grace")
    now = datetime.now(timezone.utc)

    id_stranded = uuid.uuid4()
    id_fresh = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        session.add_all(
            [
                WebhookEvent(
                    id=id_stranded,
                    tenant_id=tenant_id,
                    provider="github",
                    provider_delivery_id=f"del-s-{id_stranded.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=18),
                    payload={"status": "stranded"},
                    status="staged",
                    created_at=now - timedelta(seconds=18),
                ),
                WebhookEvent(
                    id=id_fresh,
                    tenant_id=tenant_id,
                    provider="github",
                    provider_delivery_id=f"del-f-{id_fresh.hex[:8]}",
                    event_type="push",
                    received_at=now - timedelta(seconds=2),  # Well within 10s grace
                    payload={"status": "fresh"},
                    status="staged",
                    created_at=now - timedelta(seconds=2),
                ),
            ]
        )
        await session.flush()

    start_time = time.monotonic()
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()

    sweeper_task = asyncio.create_task(
        run_test_sweeper_ticks(
            redis_mock,
            stop_event,
            interval=0.05,
            threshold_seconds=10.0,
            tenant_id=tenant_id,
        )
    )
    worker_task = asyncio.create_task(
        run_test_worker_consumer(redis_mock, "worker-grace", stop_event, tracker)
    )

    deadline = start_time + 15.0
    while time.monotonic() < deadline:
        async with get_tenant_db_context(tenant_id) as session:
            ev_s = await session.get(WebhookEvent, id_stranded)
            if ev_s is not None and ev_s.status == "completed":
                break
        await asyncio.sleep(0.05)

    elapsed = time.monotonic() - start_time
    stop_event.set()
    await asyncio.gather(sweeper_task, worker_task, return_exceptions=True)

    assert elapsed <= 15.0

    # Stranded event is completed and executed exactly once
    assert tracker.count(id_stranded) == 1

    # Fresh event was NEVER touched by sweeper and has zero executions
    assert tracker.count(id_fresh) == 0

    async with get_tenant_db_context(tenant_id) as session:
        ev_s_db = await session.get(WebhookEvent, id_stranded)
        ev_f_db = await session.get(WebhookEvent, id_fresh)
        assert ev_s_db is not None and ev_s_db.status == "completed"
        assert ev_f_db is not None and ev_f_db.status == "staged"


# ==============================================================================
# 2. IN-MEMORY HIGH-DENSITY CHAOS MATRIX SIMULATION (ALL ENVIRONMENTS)
# ==============================================================================


@dataclass
class SimulatedEvent:
    id: uuid.UUID
    tenant_id: uuid.UUID
    status: str  # 'staged', 'recovering', 'queued', 'processing', 'completed'
    received_at: float  # simulated timestamp
    attempts: int = 0


class InMemoryEventStore:
    """
    High-fidelity, in-memory transactional event store simulating PostgreSQL
    FOR UPDATE SKIP LOCKED and atomic Compare-And-Set (CAS) transitions.
    """

    def __init__(self) -> None:
        self.events: dict[uuid.UUID, SimulatedEvent] = {}
        self.lock = asyncio.Lock()

    async def add_events(self, events: list[SimulatedEvent]) -> None:
        async with self.lock:
            for ev in events:
                self.events[ev.id] = ev

    async def claim_stranded(
        self,
        cutoff_time: float,
        batch_size: int = 10,
    ) -> list[SimulatedEvent]:
        """Simulates PostgreSQL: SELECT ... WHERE status='staged' AND received_at <= :cutoff FOR UPDATE SKIP LOCKED."""
        async with self.lock:
            candidates = [
                ev
                for ev in self.events.values()
                if ev.status == "staged" and ev.received_at <= cutoff_time
            ]
            candidates.sort(key=lambda x: x.received_at)
            claimed = candidates[:batch_size]
            for c in claimed:
                c.status = "recovering"
            return list(claimed)

    async def mark_queued(self, event_ids: list[uuid.UUID]) -> None:
        """Simulates: UPDATE webhook_events SET status='queued' WHERE id IN :ids AND status='recovering'."""
        async with self.lock:
            for eid in event_ids:
                if eid in self.events and self.events[eid].status == "recovering":
                    self.events[eid].status = "queued"

    async def cas_to_processing(self, event_id: uuid.UUID) -> bool:
        """
        Simulates atomic Compare-And-Set:
        UPDATE webhook_events SET status='processing', attempts=attempts+1
        WHERE id = :id AND status IN ('staged', 'queued', 'recovering')
        RETURNING id;
        """
        async with self.lock:
            ev = self.events.get(event_id)
            if ev is not None and ev.status in ("staged", "queued", "recovering"):
                ev.status = "processing"
                ev.attempts += 1
                return True
            return False

    async def complete_event(self, event_id: uuid.UUID) -> None:
        async with self.lock:
            ev = self.events.get(event_id)
            if ev is not None:
                ev.status = "completed"

    async def count_completed(self) -> int:
        async with self.lock:
            return sum(1 for ev in self.events.values() if ev.status == "completed")


@pytest.mark.asyncio
async def test_chaos_simulated_fast_concurrency_and_deduplication():
    """
    TC-CHAOS-FAST:
    High-density chaos matrix simulation: 50 stranded events backdated >10s,
    5 concurrent sweepers with random network jitter, 8 concurrent workers,
    and injected duplicate envelopes (simulating delayed gateway push races).
    Asserts 100% completion within 15 seconds budget and execution count == 1 per event.
    """
    store = InMemoryEventStore()
    redis = InMemoryAsyncRedis()
    tenant_id = uuid.uuid4()

    simulated_now = 1000.0  # Base simulated epoch seconds
    num_events = 50
    event_ids = [uuid.uuid4() for _ in range(num_events)]

    # 1. Populate store with 50 backdated stranded events (received_at <= simulated_now - 10s)
    events = [
        SimulatedEvent(
            id=eid,
            tenant_id=tenant_id,
            status="staged",
            received_at=simulated_now - random.uniform(11.0, 30.0),
        )
        for eid in event_ids
    ]
    await store.add_events(events)

    cutoff = simulated_now - 10.0
    tracker = SideEffectTracker()
    stop_event = asyncio.Event()
    start_real_time = time.monotonic()

    # 2. Sweeper coroutine: claims disjoint batches and pushes envelopes to Redis
    async def sweeper_worker(worker_idx: int):
        while not stop_event.is_set():
            claimed = await store.claim_stranded(cutoff, batch_size=10)
            if claimed:
                envelopes = [
                    build_webhook_envelope(
                        event_id=c.id,
                        tenant_id=c.tenant_id,
                        provider="github",
                        event_type="push",
                    )
                    for c in claimed
                ]
                await redis.rpush(QUEUE_DEFAULT, *envelopes)
                await store.mark_queued([c.id for c in claimed])

                # Chaos injection: randomly inject a delayed duplicate envelope (simulating gateway race)
                if random.random() < 0.2:
                    dup = random.choice(envelopes)
                    await redis.rpush(QUEUE_DEFAULT, dup)

            await asyncio.sleep(random.uniform(0.001, 0.005))

    # 3. Consumer worker coroutine: pops from Redis, executes atomic CAS, records side effects
    async def consumer_worker(worker_idx: int):
        while not stop_event.is_set():
            raw_envelope = await redis.lpop(QUEUE_DEFAULT)
            if raw_envelope is None:
                await asyncio.sleep(0.001)
                continue

            envelope = json.loads(raw_envelope)
            eid = uuid.UUID(envelope["event_id"])

            cas_ok = await store.cas_to_processing(eid)
            if not cas_ok:
                # Duplicate envelope caught by CAS!
                continue

            # CAS won: execute domain side effect
            await tracker.record_execution(eid)

            # Mark completed
            await store.complete_event(eid)
            await asyncio.sleep(random.uniform(0.0005, 0.002))

    # Launch 5 concurrent sweepers + 8 concurrent consumers
    sweeper_tasks = [asyncio.create_task(sweeper_worker(i)) for i in range(5)]
    consumer_tasks = [asyncio.create_task(consumer_worker(i)) for i in range(8)]

    # 4. Wait until 100% of events reach 'completed'
    while await store.count_completed() < num_events:
        if time.monotonic() - start_real_time > 15.0:
            break
        await asyncio.sleep(0.005)

    real_elapsed = time.monotonic() - start_real_time
    stop_event.set()
    await asyncio.gather(*sweeper_tasks, *consumer_tasks, return_exceptions=True)

    completed_count = await store.count_completed()
    assert completed_count == num_events, (
        f"Chaos test failed to complete all events: completed {completed_count}/{num_events}"
    )
    assert real_elapsed <= 15.0, f"Recovery exceeded 15s budget: {real_elapsed:.3f}s"

    # 5. Zero duplicate executions: assert execution_count == 1 for every event
    for eid in event_ids:
        assert tracker.count(eid) == 1, (
            f"Event {eid} had duplicate executions: count={tracker.count(eid)}"
        )
        assert store.events[eid].attempts == 1
        assert store.events[eid].status == "completed"
