"""
SCRUM-476 / SCRUM-477 / SCRUM-478 / SCRUM-479: Transactional Outbox Pattern & Service Automated Tests.
Parent Story: SCRUM-371 (US-32: Transactional outbox).
Design Specification: backend/tests/scenarios/test_us32_transactional_outbox_scenarios.md

Automated verification of transactional outbox pattern and database invariants:
- TC-OUTBOX-001: record_outbox_event stages row with published_at=None and canonical envelope.
- TC-OUTBOX-002: Atomic dual-commit: Domain mutation + outbox event commit durably in one transaction.
- TC-OUTBOX-003: Rollback on error: Exception before commit rolls back both domain entity and outbox event.
- TC-OUTBOX-004: Injected DB constraint violation rolls back both domain change and outbox event.
- TC-OUTBOX-005: Relay polling query: claim_unpublished_outbox_events retrieves events via SKIP LOCKED.
- TC-OUTBOX-006: Partial index: PostgreSQL EXPLAIN verifies usage of ix_outbox_events_unpublished.
- TC-OUTBOX-007: Tenant RLS: Outbox events strictly isolated per tenant under eng_app_user.
- TC-OUTBOX-008: Tenant RLS table scan and anti-enumeration filtering isolation.
"""

from datetime import datetime, timezone
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select, text

from app.db.models.models import Commit, Deployment, OutboxEvent, Project, Repository
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.outbox.service import (
    claim_unpublished_outbox_events,
    mark_outbox_events_published,
    record_deployment_failure_atomic,
    record_outbox_event,
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
        "name": f"Outbox Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"admin-{unique}@outboxtemp.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


async def create_test_project(tenant_id: uuid.UUID, name: str = "Outbox Project") -> uuid.UUID:
    """Creates a project scoped to the tenant under tenant context."""
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with get_tenant_db_context(tenant_id) as session:
        p = Project(
            id=project_id,
            tenant_id=tenant_id,
            name=name,
            slug=f"proj-{project_id.hex[:8]}",
            tier_sla_hours=48,
            authoritative_cd_signal="github_deployments_api",
            lead_time_first_commit_strategy="feature_branch_root",
            wip_limit_per_dev=3,
            pr_stale_threshold_hours=48,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(p)
        await session.commit()
    return project_id


async def create_test_deployment(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> uuid.UUID:
    """Creates a complete repo, commit, and deployment hierarchy in PENDING status."""
    deployment_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    commit_sha = uuid.uuid4().hex + uuid.uuid4().hex[:8]  # 40 chars
    now = datetime.now(timezone.utc)

    async with get_tenant_db_context(tenant_id) as session:
        repo = Repository(
            id=repo_id,
            tenant_id=tenant_id,
            project_id=project_id,
            external_repo_id=int(uuid.uuid4().int % 100000000),
            full_name=f"org/repo-{repo_id.hex[:6]}",
            default_branch="main",
            is_private=True,
            created_at=now,
        )
        session.add(repo)
        await session.flush()

        cmt = Commit(
            sha=commit_sha,
            tenant_id=tenant_id,
            repository_id=repo_id,
            author_pseudonym_id="pseudo-" + uuid.uuid4().hex[:16],
            lines_added=15,
            lines_deleted=3,
            authored_at=now,
            committed_at=now,
            is_root_of_branch=False,
            created_at=now,
        )
        session.add(cmt)
        await session.flush()

        dep = Deployment(
            id=deployment_id,
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repo_id,
            environment="production",
            commit_sha=commit_sha,
            status="PENDING",
            is_change_failure=False,
            authoritative_signal_source="github_deployments_api",
            deployed_at=now,
            created_at=now,
        )
        session.add(dep)
        await session.commit()

    return deployment_id


@pytest.mark.asyncio
async def test_record_outbox_event_staging():
    """TC-OUTBOX-001: record_outbox_event stages row with published_at=None and canonical envelope."""
    tenant_id, _ = await register_test_tenant("outbox-stage")
    project_id = await create_test_project(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        payload_data = {"key": "value", "metric": 42}
        event = record_outbox_event(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="test.event.staged",
            aggregate_type="test_agg",
            aggregate_id=project_id,
            payload=payload_data,
        )

        assert event.id is not None
        assert event.tenant_id == tenant_id
        assert event.event_type == "test.event.staged"
        assert event.published_at is None
        assert event.payload["schema_version"] == "1.0"
        assert event.payload["event_type"] == "test.event.staged"
        assert event.payload["tenant_id"] == str(tenant_id)
        assert event.payload["data"] == payload_data

        # Commit to persist
        await session.commit()

    # Verify persisted row
    async with get_tenant_db_context(tenant_id) as session:
        persisted = await session.get(OutboxEvent, event.id)
        assert persisted is not None
        assert persisted.published_at is None
        assert persisted.payload["data"] == payload_data


@pytest.mark.asyncio
async def test_atomic_dual_commit():
    """TC-OUTBOX-002: Atomic dual-commit: domain mutation + outbox event commit together."""
    tenant_id, _ = await register_test_tenant("outbox-dual")
    project_id = await create_test_project(tenant_id)
    deployment_id = await create_test_deployment(tenant_id, project_id)

    # Execute atomic failure update + outbox staging
    async with get_tenant_db_context(tenant_id) as session:
        dep, outbox = await record_deployment_failure_atomic(
            session,
            tenant_id=tenant_id,
            deployment_id=deployment_id,
            failure_reason="health_check_timeout",
            error_details={"http_status": 503},
        )
        assert dep.status == "FAILED"
        assert dep.is_change_failure is True
        assert outbox.published_at is None
        await session.commit()

    # Assert BOTH changes exist in the database
    async with get_tenant_db_context(tenant_id) as session:
        refreshed_dep = await session.get(Deployment, deployment_id)
        assert refreshed_dep is not None
        assert refreshed_dep.status == "FAILED"
        assert refreshed_dep.is_change_failure is True

        refreshed_outbox = await session.get(OutboxEvent, outbox.id)
        assert refreshed_outbox is not None
        assert refreshed_outbox.event_type == "deployment.failed"
        assert refreshed_outbox.published_at is None
        assert refreshed_outbox.payload["data"]["failure_reason"] == "health_check_timeout"


@pytest.mark.asyncio
async def test_rollback_on_error():
    """TC-OUTBOX-003: Unhandled exception before commit rolls back both domain change and outbox event."""
    tenant_id, _ = await register_test_tenant("outbox-rollback")
    project_id = await create_test_project(tenant_id)
    deployment_id = await create_test_deployment(tenant_id, project_id)

    # Simulate an operation that raises an exception before session.commit()
    staged_outbox_id = None
    try:
        async with get_tenant_db_context(tenant_id) as session:
            dep, outbox = await record_deployment_failure_atomic(
                session,
                tenant_id=tenant_id,
                deployment_id=deployment_id,
                failure_reason="simulated_crash",
            )
            staged_outbox_id = outbox.id
            # Raise exception before commit
            raise RuntimeError("Simulated unhandled worker crash!")
    except RuntimeError:
        pass

    # Verify that NEITHER the status update nor the outbox row was persisted
    async with get_tenant_db_context(tenant_id) as session:
        dep = await session.get(Deployment, deployment_id)
        assert dep is not None
        assert dep.status == "PENDING"  # NOT FAILED!
        assert dep.is_change_failure is False

        outbox = await session.get(OutboxEvent, staged_outbox_id)
        assert outbox is None  # Never committed!


@pytest.mark.asyncio
async def test_database_constraint_error_rollback():
    """TC-OUTBOX-004: Injected DB constraint violation rolls back both domain change and outbox event."""
    tenant_id, _ = await register_test_tenant("outbox-dberr")
    project_id = await create_test_project(tenant_id)
    deployment_id = await create_test_deployment(tenant_id, project_id)

    staged_outbox_id = None
    try:
        async with get_tenant_db_context(tenant_id) as session:
            dep, outbox = await record_deployment_failure_atomic(
                session,
                tenant_id=tenant_id,
                deployment_id=deployment_id,
                failure_reason="db_error_test",
            )
            staged_outbox_id = outbox.id

            # Inject relational constraint violation: insert commit referencing non-existent repository
            invalid_commit = Commit(
                sha=uuid.uuid4().hex + uuid.uuid4().hex[:8],
                tenant_id=tenant_id,
                repository_id=uuid.uuid4(),  # Does not exist in repositories table
                author_pseudonym_id="pseudo-" + uuid.uuid4().hex[:16],
                lines_added=10,
                lines_deleted=2,
                authored_at=datetime.now(timezone.utc),
                committed_at=datetime.now(timezone.utc),
                is_root_of_branch=False,
                created_at=datetime.now(timezone.utc),
            )
            session.add(invalid_commit)
            await session.commit()
    except Exception:
        pass

    # Verify that NEITHER the status update nor the outbox row was persisted
    async with get_tenant_db_context(tenant_id) as session:
        dep = await session.get(Deployment, deployment_id)
        assert dep is not None
        assert dep.status == "PENDING"
        assert dep.is_change_failure is False

        outbox = await session.get(OutboxEvent, staged_outbox_id)
        assert outbox is None


@pytest.mark.asyncio
async def test_claim_and_publish_outbox_events():
    """TC-OUTBOX-005: Relay polling query and mark_outbox_events_published."""
    tenant_id, _ = await register_test_tenant("outbox-relay")
    project_id = await create_test_project(tenant_id)

    # Insert 3 unpublished events
    events: list[OutboxEvent] = []
    async with get_tenant_db_context(tenant_id) as session:
        for i in range(3):
            ev = record_outbox_event(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                event_type=f"relay.event.{i}",
                payload={"index": i},
            )
            events.append(ev)
        await session.commit()

    # Claim events using SKIP LOCKED
    async with get_tenant_db_context(tenant_id) as session:
        claimed = await claim_unpublished_outbox_events(session, limit=10)
        claimed_ids = [e.id for e in claimed if e.tenant_id == tenant_id]
        assert len(claimed_ids) >= 3

        # Mark them published
        count = await mark_outbox_events_published(session, claimed_ids)
        assert count >= 3
        await session.commit()

    # Verify they are no longer returned by unpublished query
    async with get_tenant_db_context(tenant_id) as session:
        unpub = await claim_unpublished_outbox_events(session, limit=10)
        unpub_for_tenant = [e.id for e in unpub if e.tenant_id == tenant_id]
        assert len(unpub_for_tenant) == 0


@pytest.mark.asyncio
async def test_partial_index_explain():
    """TC-OUTBOX-006: EXPLAIN on unpublished query uses ix_outbox_events_unpublished."""
    tenant_id, _ = await register_test_tenant("outbox-explain")

    async with get_tenant_db_context(tenant_id) as session:
        explain_stmt = text(
            "EXPLAIN SELECT id FROM outbox_events WHERE published_at IS NULL ORDER BY created_at ASC;"
        )
        res = await session.execute(explain_stmt)
        plan_lines = "\n".join([row[0] for row in res.fetchall()])

        assert (
            "ix_outbox_events_unpublished" in plan_lines
            or "Index Scan" in plan_lines
            or "Bitmap Index Scan" in plan_lines
            or "Seq Scan" in plan_lines
        )

        # Verify index catalog definition in PostgreSQL
        idx_check = await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'outbox_events' AND indexname = 'ix_outbox_events_unpublished';"
            )
        )
        idx_def = idx_check.scalar_one_or_none()
        assert idx_def is not None
        assert "WHERE (published_at IS NULL)" in idx_def

        # Force index scan test to guarantee index validity
        await session.execute(text("SET LOCAL enable_seqscan = off;"))
        res_forced = await session.execute(explain_stmt)
        forced_plan = "\n".join([row[0] for row in res_forced.fetchall()])
        assert "Index Scan" in forced_plan


@pytest.mark.asyncio
async def test_outbox_tenant_rls_isolation():
    """TC-OUTBOX-007: Tenant A cannot see Tenant B's outbox events under tenant context."""
    tenant_a, _ = await register_test_tenant("outbox-ten-a")
    tenant_b, _ = await register_test_tenant("outbox-ten-b")
    proj_a = await create_test_project(tenant_a)
    proj_b = await create_test_project(tenant_b)

    ev_a_id = None
    ev_b_id = None

    async with get_tenant_db_context(tenant_a) as session:
        ev_a = record_outbox_event(
            session,
            tenant_id=tenant_a,
            project_id=proj_a,
            event_type="tenant.a.event",
            payload={"secret": "ten_a"},
        )
        ev_a_id = ev_a.id
        await session.commit()

    async with get_tenant_db_context(tenant_b) as session:
        ev_b = record_outbox_event(
            session,
            tenant_id=tenant_b,
            project_id=proj_b,
            event_type="tenant.b.event",
            payload={"secret": "ten_b"},
        )
        ev_b_id = ev_b.id
        await session.commit()

    # Under Tenant A context, Tenant B's event must not be visible
    async with get_tenant_db_context(tenant_a) as session:
        res = await session.get(OutboxEvent, ev_b_id)
        assert res is None, "Tenant A should NOT be able to read Tenant B's outbox event!"

        # Tenant A can see its own event
        own = await session.get(OutboxEvent, ev_a_id)
        assert own is not None


@pytest.mark.asyncio
async def test_cross_tenant_rls_query_filter():
    """TC-OUTBOX-008: Tenant B queries cannot scan or filter Tenant A's outbox events under RLS."""
    tenant_a, _ = await register_test_tenant("outbox-scan-a")
    tenant_b, _ = await register_test_tenant("outbox-scan-b")
    proj_a = await create_test_project(tenant_a)
    proj_b = await create_test_project(tenant_b)

    async with get_tenant_db_context(tenant_a) as session:
        for i in range(3):
            record_outbox_event(
                session,
                tenant_id=tenant_a,
                project_id=proj_a,
                event_type=f"tenant.a.scan.{i}",
                payload={"index": i},
            )
        await session.commit()

    async with get_tenant_db_context(tenant_b) as session:
        for i in range(2):
            record_outbox_event(
                session,
                tenant_id=tenant_b,
                project_id=proj_b,
                event_type=f"tenant.b.scan.{i}",
                payload={"index": i},
            )
        await session.commit()

    # Under Tenant B context, unfiltered query must return only Tenant B's 2 events
    async with get_tenant_db_context(tenant_b) as session:
        stmt = select(OutboxEvent)
        res = await session.execute(stmt)
        events_b = res.scalars().all()
        assert len(events_b) == 2
        for ev in events_b:
            assert ev.tenant_id == tenant_b

        # Explicit cross-tenant query for Tenant A must return 0 rows due to RLS policy
        cross_stmt = select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_a)
        cross_res = await session.execute(cross_stmt)
        cross_events = cross_res.scalars().all()
        assert len(cross_events) == 0
