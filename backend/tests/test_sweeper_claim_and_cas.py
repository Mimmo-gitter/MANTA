"""
SCRUM-387 / US-19: Test Suite for Staged-Event Recovery Sweeper Claim & CAS Transition.

Acceptance Criteria Verified:
- Partial index 'idx_webhook_events_staged_received' exists on webhook_events (received_at) WHERE status = 'staged'.
- Batch claim query transitions stranded 'staged' rows older than threshold (10s) to 'recovering' with claimed_at = now().
- Events younger than threshold or in other statuses ('processing', 'completed') are NOT claimed.
- Concurrent calls to the claim query return disjoint sets of rows with zero lock contention (FOR UPDATE SKIP LOCKED).
- Worker CAS update transitions status IN ('staged', 'queued', 'recovering') to 'processing' and increments attempts.
- If worker receives a job already in 'processing' or 'completed' state, CAS returns False (zero rows updated, prevents double processing).
"""

import asyncio
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db.models.models import WebhookEvent
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.webhooks.sweeper import (
    cas_transition_to_processing,
    claim_stranded_staged_events,
    complete_webhook_event,
)


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test."""
    from app.tenants.rate_limiter import registration_rate_limiter

    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


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
# 1. PARTIAL INDEX VERIFICATION
# ==============================================================================


@pytest.mark.asyncio
async def test_partial_index_idx_webhook_events_staged_received_exists():
    """Verify that idx_webhook_events_staged_received exists in PostgreSQL pg_indexes."""
    async with async_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'webhook_events' AND indexname = 'idx_webhook_events_staged_received';"
            )
        )
        row = res.fetchone()
        assert row is not None, "Partial index idx_webhook_events_staged_received not found!"
        index_def = row[1].lower()
        assert "received_at" in index_def
        assert "where" in index_def and "status" in index_def and "'staged'" in index_def


# ==============================================================================
# 2. BATCH CLAIM QUERY VERIFICATION
# ==============================================================================


@pytest.mark.asyncio
async def test_sweeper_claims_stranded_staged_events_older_than_threshold():
    """TC-SWEEP-001: Stranded events > 10s are claimed and moved to 'recovering'."""
    tenant_id, _ = await register_test_tenant("sweep-claim")
    now = datetime.now(timezone.utc)

    # Insert 3 events:
    # Event 1: staged, received 25 seconds ago (should be claimed)
    # Event 2: staged, received 5 seconds ago (too young, should NOT be claimed)
    # Event 3: processing, received 30 seconds ago (already processing, should NOT be claimed)
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    id3 = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        ev1 = WebhookEvent(
            id=id1,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{id1.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=25),
            payload={"action": "test_stranded"},
            status="staged",
            created_at=now - timedelta(seconds=25),
        )
        ev2 = WebhookEvent(
            id=id2,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{id2.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=5),
            payload={"action": "test_fresh"},
            status="staged",
            created_at=now - timedelta(seconds=5),
        )
        ev3 = WebhookEvent(
            id=id3,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-{id3.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=30),
            payload={"action": "test_processing"},
            status="processing",
            created_at=now - timedelta(seconds=30),
        )
        session.add_all([ev1, ev2, ev3])
        await session.flush()

        # Run sweeper claim with 10-second threshold
        claimed = await claim_stranded_staged_events(
            session, threshold_seconds=10.0, batch_size=50, tenant_id=tenant_id
        )

        assert len(claimed) == 1
        assert claimed[0].id == id1
        assert claimed[0].status == "recovering"
        assert claimed[0].claimed_at is not None

        # Verify DB state of all 3 events
        res1 = await session.execute(
            text("SELECT status, claimed_at FROM webhook_events WHERE id = :id;"), {"id": id1}
        )
        row1 = res1.fetchone()
        assert row1[0] == "recovering"
        assert row1[1] is not None

        res2 = await session.execute(
            text("SELECT status, claimed_at FROM webhook_events WHERE id = :id;"), {"id": id2}
        )
        row2 = res2.fetchone()
        assert row2[0] == "staged"
        assert row2[1] is None

        res3 = await session.execute(
            text("SELECT status FROM webhook_events WHERE id = :id;"), {"id": id3}
        )
        assert res3.scalar_one() == "processing"


# ==============================================================================
# 3. CONCURRENT CLAIMS (SKIP LOCKED DISJOINT SETS)
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_sweepers_return_disjoint_sets_skip_locked():
    """TC-SWEEP-002: Concurrent workers claim disjoint rows with zero lock contention."""
    tenant_id, _ = await register_test_tenant("sweep-concur")
    now = datetime.now(timezone.utc)

    # Insert 6 stranded events
    event_ids = [uuid.uuid4() for _ in range(6)]
    async with get_tenant_db_context(tenant_id) as session:
        for idx, eid in enumerate(event_ids):
            ev = WebhookEvent(
                id=eid,
                tenant_id=tenant_id,
                provider="github",
                provider_delivery_id=f"del-concur-{idx}-{eid.hex[:6]}",
                event_type="push",
                received_at=now - timedelta(seconds=20 + idx),
                payload={"index": idx},
                status="staged",
                created_at=now - timedelta(seconds=20 + idx),
            )
            session.add(ev)
        await session.flush()

    # Simulate 2 concurrent workers claiming 3 events each
    async def worker_claim(limit: int):
        async with get_tenant_db_context(tenant_id) as s:
            batch = await claim_stranded_staged_events(
                s, threshold_seconds=10.0, batch_size=limit, tenant_id=tenant_id
            )
            await s.commit()
            return [b.id for b in batch]

    # Run simultaneously
    res_worker1, res_worker2 = await asyncio.gather(worker_claim(3), worker_claim(3))

    set1 = set(res_worker1)
    set2 = set(res_worker2)

    # Assert sets are strictly disjoint
    assert len(set1.intersection(set2)) == 0, f"Overlapping claimed rows: {set1.intersection(set2)}"
    assert len(set1) + len(set2) == 6, f"Expected 6 claimed rows, got {len(set1) + len(set2)}"


# ==============================================================================
# 4. WORKER ATOMIC CAS TRANSITION TO PROCESSING
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_cas_transition_and_duplicate_prevention():
    """TC-SWEEP-003: CAS transitions to processing; duplicate worker call returns False."""
    tenant_id, _ = await register_test_tenant("sweep-cas")
    now = datetime.now(timezone.utc)
    eid = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        ev = WebhookEvent(
            id=eid,
            tenant_id=tenant_id,
            provider="github",
            provider_delivery_id=f"del-cas-{eid.hex[:8]}",
            event_type="push",
            received_at=now - timedelta(seconds=15),
            payload={"foo": "bar"},
            status="recovering",
            attempts=0,
            created_at=now - timedelta(seconds=15),
        )
        session.add(ev)
        await session.flush()

        # 1. First worker CAS call succeeds
        success1 = await cas_transition_to_processing(session, eid, tenant_id=tenant_id)
        assert success1 is True

        # Check DB state: status is 'processing', attempts incremented to 1
        res = await session.execute(
            text("SELECT status, attempts FROM webhook_events WHERE id = :id;"), {"id": eid}
        )
        row = res.fetchone()
        assert row[0] == "processing"
        assert row[1] == 1

        # 2. Second concurrent/delayed worker CAS call fails (returns False)
        # Prevents duplicate processing of the same job!
        success2 = await cas_transition_to_processing(session, eid, tenant_id=tenant_id)
        assert success2 is False

        # Status and attempts remain untouched
        res_after = await session.execute(
            text("SELECT status, attempts FROM webhook_events WHERE id = :id;"), {"id": eid}
        )
        row_after = res_after.fetchone()
        assert row_after[0] == "processing"
        assert row_after[1] == 1

        # 3. Complete the event
        completed = await complete_webhook_event(session, eid, tenant_id=tenant_id)
        assert completed is True

        # 4. CAS on completed event also returns False
        success3 = await cas_transition_to_processing(session, eid, tenant_id=tenant_id)
        assert success3 is False
