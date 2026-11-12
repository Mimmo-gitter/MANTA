"""
SCRUM-371 / SCRUM-479: Automated Pytest Suite for Transactional Outbox Pattern.
Task 32.5: Write automated pytest tests verifying both rows commit together
or roll back together when an error occurs before commit.

Acceptance Criteria & Scope Verified:
- Successful atomic commit of deployment failure + outbox_events row (published_at IS NULL).
- Simulated exception prior to session.commit() asserting SELECT count() FROM deployments
  and SELECT count(*) FROM outbox_events reflect full rollback of both operations.
- Simulated database constraint error prior to commit triggering full rollback.
- Tenant Row-Level Security (RLS) enforcement on outbox_events:
  * Cross-tenant read isolation (Tenant B cannot read Tenant A rows by ID or scan).
  * Anti-enumeration and cross-tenant WHERE filtering returns 0 rows.
  * Cross-tenant INSERT prevention via RLS WITH CHECK policy.
- Canonical event envelope contract verification (schema_version = "1.0", published_at IS NULL).
- Outbox Relay polling (FOR UPDATE SKIP LOCKED) and published status transition.
- Partial index ix_outbox_events_unpublished catalog verification.
"""

from datetime import datetime, timezone
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

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
    initial_status: str = "PENDING",
) -> uuid.UUID:
    """Creates a complete repo, commit, and deployment hierarchy in specified status."""
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
            status=initial_status,
            is_change_failure=False,
            authoritative_signal_source="github_deployments_api",
            deployed_at=now,
            created_at=now,
        )
        session.add(dep)
        await session.commit()

    return deployment_id


# ==============================================================================
# 1. ATOMIC DUAL-COMMIT TESTS (Scope #1)
# ==============================================================================


@pytest.mark.asyncio
async def test_atomic_dual_commit_deployment_failure():
    """
    Scope #1: Successful atomic commit of deployment failure + outbox_events row.
    Verifies that updating deployments to status='FAILED' (is_change_failure=TRUE)
    and inserting outbox_events (published_at IS NULL) commit together atomically
    in a single database transaction.
    """
    tenant_id, _ = await register_test_tenant("tx-outbox-dual")
    project_id = await create_test_project(tenant_id)
    deployment_id = await create_test_deployment(tenant_id, project_id, initial_status="PENDING")

    # 1. Execute atomic deployment failure mutation + outbox event staging in active session
    async with get_tenant_db_context(tenant_id) as session:
        dep, outbox = await record_deployment_failure_atomic(
            session,
            tenant_id=tenant_id,
            deployment_id=deployment_id,
            failure_reason="health_check_timeout",
            error_details={"http_status": 503, "endpoint": "/healthz"},
        )

        # In-memory session state assertions before commit
        assert dep.status == "FAILED"
        assert dep.is_change_failure is True
        assert outbox.id is not None
        assert outbox.tenant_id == tenant_id
        assert outbox.event_type == "deployment.failed"
        assert outbox.aggregate_type == "deployment"
        assert outbox.aggregate_id == str(deployment_id)
        assert outbox.published_at is None

        # Persist both entities in a single atomic commit
        await session.commit()

    # 2. In a fresh session, verify durable persistence of both rows via ORM and SQL count queries
    async with get_tenant_db_context(tenant_id) as session:
        refreshed_dep = await session.get(Deployment, deployment_id)
        assert refreshed_dep is not None
        assert refreshed_dep.status == "FAILED"
        assert refreshed_dep.is_change_failure is True

        refreshed_outbox = await session.get(OutboxEvent, outbox.id)
        assert refreshed_outbox is not None
        assert refreshed_outbox.event_type == "deployment.failed"
        assert refreshed_outbox.published_at is None
        assert refreshed_outbox.payload["schema_version"] == "1.0"
        assert refreshed_outbox.payload["data"]["failure_reason"] == "health_check_timeout"
        assert refreshed_outbox.payload["data"]["error_details"]["http_status"] == 503

        # Assert SQL count queries reflect atomic persistence of both rows
        dep_count_res = await session.execute(
            text(
                "SELECT count(*) FROM deployments "
                "WHERE id = :dep_id AND status = 'FAILED' AND is_change_failure = true;"
            ),
            {"dep_id": deployment_id},
        )
        assert dep_count_res.scalar() == 1

        outbox_count_res = await session.execute(
            text(
                "SELECT count(*) FROM outbox_events WHERE id = :outbox_id AND published_at IS NULL;"
            ),
            {"outbox_id": outbox.id},
        )
        assert outbox_count_res.scalar() == 1


# ==============================================================================
# 2. MID-TRANSACTION ERROR ROLLBACK TESTS (Scope #2)
# ==============================================================================


@pytest.mark.asyncio
async def test_rollback_on_simulated_exception_before_commit():
    """
    Scope #2: Simulated exception prior to session.commit() asserting:
      SELECT count() FROM deployments
      and
      SELECT count(*) FROM outbox_events
    reflect full rollback of both operations (zero partial writes).
    """
    tenant_id, _ = await register_test_tenant("tx-outbox-sim-err")
    project_id = await create_test_project(tenant_id)
    deployment_id = await create_test_deployment(tenant_id, project_id, initial_status="PENDING")

    # 1. Capture baseline counts before initiating the mutation
    async with get_tenant_db_context(tenant_id) as session:
        baseline_dep_count = (
            await session.execute(
                text("SELECT count(*) FROM deployments WHERE tenant_id = :tid;"),
                {"tid": tenant_id},
            )
        ).scalar()
        baseline_failed_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM deployments WHERE tenant_id = :tid AND status = 'FAILED';"
                ),
                {"tid": tenant_id},
            )
        ).scalar()
        baseline_outbox_count = (
            await session.execute(
                text("SELECT count(*) FROM outbox_events WHERE tenant_id = :tid;"),
                {"tid": tenant_id},
            )
        ).scalar()

        assert baseline_dep_count >= 1
        assert baseline_failed_count == 0
        assert baseline_outbox_count == 0

    # 2. Mutate deployment entity and stage outbox event, then inject unhandled exception before commit
    staged_outbox_id = None
    with pytest.raises(RuntimeError, match="Simulated mid-transaction failure prior to commit"):
        async with get_tenant_db_context(tenant_id) as session:
            dep, outbox = await record_deployment_failure_atomic(
                session,
                tenant_id=tenant_id,
                deployment_id=deployment_id,
                failure_reason="simulated_worker_crash",
            )
            staged_outbox_id = outbox.id

            # Verify in-memory staged changes exist within uncommitted identity map
            assert dep.status == "FAILED"
            assert dep.is_change_failure is True
            assert outbox.published_at is None

            # Inject simulated unhandled application exception before commit
            raise RuntimeError("Simulated mid-transaction failure prior to commit")

    # 3. Open a clean session and assert full rollback via SELECT count() queries
    async with get_tenant_db_context(tenant_id) as session:
        # SELECT count(*) FROM deployments must match baseline exactly
        post_err_dep_count = (
            await session.execute(
                text("SELECT count(*) FROM deployments WHERE tenant_id = :tid;"),
                {"tid": tenant_id},
            )
        ).scalar()
        assert post_err_dep_count == baseline_dep_count

        # SELECT count(*) FROM deployments WHERE status = 'FAILED' must be ZERO
        post_err_failed_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM deployments WHERE tenant_id = :tid AND status = 'FAILED';"
                ),
                {"tid": tenant_id},
            )
        ).scalar()
        assert post_err_failed_count == 0

        # SELECT count(*) FROM outbox_events must match baseline exactly (ZERO)
        post_err_outbox_count = (
            await session.execute(
                text("SELECT count(*) FROM outbox_events WHERE tenant_id = :tid;"),
                {"tid": tenant_id},
            )
        ).scalar()
        assert post_err_outbox_count == baseline_outbox_count

        # Target outbox event row must not exist
        staged_row_count = (
            await session.execute(
                text("SELECT count(*) FROM outbox_events WHERE id = :oid;"),
                {"oid": staged_outbox_id},
            )
        ).scalar()
        assert staged_row_count == 0

        # Verify deployment domain record status remained PENDING (not FAILED)
        refreshed_dep = await session.get(Deployment, deployment_id)
        assert refreshed_dep is not None
        assert refreshed_dep.status == "PENDING"
        assert refreshed_dep.is_change_failure is False

        # Verify outbox event record is None
        persisted_outbox = await session.get(OutboxEvent, staged_outbox_id)
        assert persisted_outbox is None


@pytest.mark.asyncio
async def test_rollback_on_database_constraint_error():
    """
    Scope #2: Relational database constraint violation (foreign key failure)
    prior to commit triggers PostgreSQL transaction abort and full rollback.
    """
    tenant_id, _ = await register_test_tenant("tx-outbox-dberr")
    project_id = await create_test_project(tenant_id)
    deployment_id = await create_test_deployment(tenant_id, project_id, initial_status="PENDING")

    staged_outbox_id = None
    try:
        async with get_tenant_db_context(tenant_id) as session:
            dep, outbox = await record_deployment_failure_atomic(
                session,
                tenant_id=tenant_id,
                deployment_id=deployment_id,
                failure_reason="db_error_simulation",
            )
            staged_outbox_id = outbox.id

            # Inject relational constraint violation: insert Commit with non-existent repository_id
            non_existent_repo_id = uuid.uuid4()
            invalid_commit = Commit(
                sha=uuid.uuid4().hex + uuid.uuid4().hex[:8],
                tenant_id=tenant_id,
                repository_id=non_existent_repo_id,  # Violates FK to repositories(id)
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
        # Expected database integrity error on commit flush
        pass

    # Verify that neither the deployment status nor the outbox row was committed
    async with get_tenant_db_context(tenant_id) as session:
        dep_res = await session.execute(
            text(
                "SELECT count(*) FROM deployments "
                "WHERE id = :dep_id AND status = 'FAILED' AND is_change_failure = true;"
            ),
            {"dep_id": deployment_id},
        )
        assert dep_res.scalar() == 0

        outbox_res = await session.execute(
            text("SELECT count(*) FROM outbox_events WHERE id = :oid;"),
            {"oid": staged_outbox_id},
        )
        assert outbox_res.scalar() == 0

        dep = await session.get(Deployment, deployment_id)
        assert dep is not None
        assert dep.status == "PENDING"
        assert dep.is_change_failure is False


# ==============================================================================
# 3. TENANT ROW-LEVEL SECURITY (RLS) ENFORCEMENT TESTS (Scope #3)
# ==============================================================================


@pytest.mark.asyncio
async def test_tenant_rls_direct_read_isolation():
    """
    Scope #3: Tenant RLS enforcement on outbox_events.
    Proves that Tenant B cannot read or lookup Tenant A's outbox_events by ID
    under unprivileged eng_app_user session context.
    """
    tenant_a, _ = await register_test_tenant("rls-ten-a")
    tenant_b, _ = await register_test_tenant("rls-ten-b")
    proj_a = await create_test_project(tenant_a)
    proj_b = await create_test_project(tenant_b)

    # 1. Tenant A stages and commits an outbox event
    async with get_tenant_db_context(tenant_a) as session_a:
        ev_a = record_outbox_event(
            session_a,
            tenant_id=tenant_a,
            project_id=proj_a,
            event_type="tenant.a.confidential",
            payload={"secret_metric": "alpha_123"},
        )
        ev_a_id = ev_a.id
        await session_a.commit()

    # 2. Tenant B stages and commits an outbox event
    async with get_tenant_db_context(tenant_b) as session_b:
        ev_b = record_outbox_event(
            session_b,
            tenant_id=tenant_b,
            project_id=proj_b,
            event_type="tenant.b.confidential",
            payload={"secret_metric": "bravo_456"},
        )
        ev_b_id = ev_b.id
        await session_b.commit()

    # 3. Under Tenant A context, direct lookup of Tenant B's event MUST return None
    async with get_tenant_db_context(tenant_a) as session:
        # Cross-tenant direct primary key lookup returns None due to RLS policy
        res_cross = await session.get(OutboxEvent, ev_b_id)
        assert res_cross is None, (
            "RLS Violation: Tenant A was able to read Tenant B's outbox event!"
        )

        # Tenant A can read its own outbox event
        res_own = await session.get(OutboxEvent, ev_a_id)
        assert res_own is not None
        assert res_own.payload["data"]["secret_metric"] == "alpha_123"

        # Direct SQL count check: SELECT count(*) FROM outbox_events WHERE id = :b_id returns 0
        sql_cross = await session.execute(
            text("SELECT count(*) FROM outbox_events WHERE id = :bid;"),
            {"bid": ev_b_id},
        )
        assert sql_cross.scalar() == 0

    # 4. Under Tenant B context, direct lookup of Tenant A's event MUST return None
    async with get_tenant_db_context(tenant_b) as session:
        res_cross = await session.get(OutboxEvent, ev_a_id)
        assert res_cross is None, (
            "RLS Violation: Tenant B was able to read Tenant A's outbox event!"
        )

        res_own = await session.get(OutboxEvent, ev_b_id)
        assert res_own is not None
        assert res_own.payload["data"]["secret_metric"] == "bravo_456"

        sql_cross = await session.execute(
            text("SELECT count(*) FROM outbox_events WHERE id = :aid;"),
            {"aid": ev_a_id},
        )
        assert sql_cross.scalar() == 0


@pytest.mark.asyncio
async def test_tenant_rls_table_scan_and_query_filtering():
    """
    Scope #3: Tenant RLS enforcement against table scans and adversarial cross-tenant WHERE filters.
    Tenant B queries return strictly Tenant B rows; cross-tenant queries return 0 rows.
    """
    tenant_a, _ = await register_test_tenant("rls-scan-a")
    tenant_b, _ = await register_test_tenant("rls-scan-b")
    proj_a = await create_test_project(tenant_a)
    proj_b = await create_test_project(tenant_b)

    # Tenant A inserts 3 events
    async with get_tenant_db_context(tenant_a) as session_a:
        for i in range(3):
            record_outbox_event(
                session_a,
                tenant_id=tenant_a,
                project_id=proj_a,
                event_type=f"scan.a.{i}",
                payload={"index": i},
            )
        await session_a.commit()

    # Tenant B inserts 2 events
    async with get_tenant_db_context(tenant_b) as session_b:
        for i in range(2):
            record_outbox_event(
                session_b,
                tenant_id=tenant_b,
                project_id=proj_b,
                event_type=f"scan.b.{i}",
                payload={"index": i},
            )
        await session_b.commit()

    # In Tenant B's context:
    async with get_tenant_db_context(tenant_b) as session:
        # 1. Unfiltered SELECT count(*) FROM outbox_events must return exactly 2 (Tenant B only)
        count_res = await session.execute(text("SELECT count(*) FROM outbox_events;"))
        assert count_res.scalar() == 2

        # 2. Adversarial query filtering by tenant_id = tenant_a must return 0 rows
        cross_count_res = await session.execute(
            text("SELECT count(*) FROM outbox_events WHERE tenant_id = :ta;"),
            {"ta": tenant_a},
        )
        assert cross_count_res.scalar() == 0

        # 3. ORM select(OutboxEvent) scan returns strictly Tenant B instances
        orm_res = (await session.execute(select(OutboxEvent))).scalars().all()
        assert len(orm_res) == 2
        for ev in orm_res:
            assert ev.tenant_id == tenant_b

        # 4. Explicit cross-tenant ORM query returns empty list
        cross_orm_res = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_a)))
            .scalars()
            .all()
        )
        assert len(cross_orm_res) == 0


@pytest.mark.asyncio
async def test_tenant_rls_cross_tenant_insert_denied():
    """
    Scope #3: PostgreSQL RLS WITH CHECK policy prevents Tenant B from inserting
    an outbox event row with Tenant A's tenant_id.
    """
    tenant_a, _ = await register_test_tenant("rls-spoof-a")
    tenant_b, _ = await register_test_tenant("rls-spoof-b")
    await create_test_project(tenant_b)

    with pytest.raises(DBAPIError) as exc_info:
        async with get_tenant_db_context(tenant_b) as session:
            # Tenant B attempts to spoof tenant_id = tenant_a on outbox_events insert
            spoofed_event = OutboxEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_a,  # Mismatched tenant_id!
                aggregate_type="spoof",
                aggregate_id="spoof-id",
                event_type="spoofed.event",
                payload={"attack": True},
                created_at=datetime.now(timezone.utc),
                published_at=None,
            )
            session.add(spoofed_event)
            await session.commit()

    # RLS WITH CHECK policy violation or permission error
    err_msg = str(exc_info.value).lower()
    assert (
        "violates row-level security policy" in err_msg
        or "check constraint" in err_msg
        or "permission denied" in err_msg
        or "rls" in err_msg
    )


# ==============================================================================
# 4. CANONICAL ENVELOPE, RELAY POLLING & PARTIAL INDEX TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_canonical_outbox_envelope_and_uncommitted_staging():
    """
    Verifies record_outbox_event formats the canonical JSON envelope
    and leaves transaction ownership to the caller (does not commit prematurely).
    """
    tenant_id, _ = await register_test_tenant("tx-envelope")
    project_id = await create_test_project(tenant_id)
    payload_data = {"deployment_id": uuid.uuid4().hex, "status": "FAILED"}

    async with get_tenant_db_context(tenant_id) as session:
        event = record_outbox_event(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="deployment.failed",
            aggregate_type="deployment",
            aggregate_id=project_id,
            payload=payload_data,
        )

        # Assert envelope specification (SRS §3.2.3)
        assert event.id is not None
        assert event.tenant_id == tenant_id
        assert event.published_at is None
        assert event.payload["schema_version"] == "1.0"
        assert event.payload["event_type"] == "deployment.failed"
        assert event.payload["tenant_id"] == str(tenant_id)
        assert event.payload["project_id"] == str(project_id)
        assert event.payload["data"] == payload_data
        assert "occurred_at" in event.payload

        await session.commit()

    # Verify durability in clean session
    async with get_tenant_db_context(tenant_id) as session:
        persisted = await session.get(OutboxEvent, event.id)
        assert persisted is not None
        assert persisted.published_at is None
        assert persisted.payload["data"] == payload_data


@pytest.mark.asyncio
async def test_relay_claim_and_publish_cycle():
    """
    Verifies claim_unpublished_outbox_events selects unpublished events via SKIP LOCKED
    and mark_outbox_events_published sets published_at to exclude them from future sweeps.
    """
    tenant_id, _ = await register_test_tenant("tx-relay-cycle")
    project_id = await create_test_project(tenant_id)

    # Insert 3 unpublished events
    staged_ids = []
    async with get_tenant_db_context(tenant_id) as session:
        for i in range(3):
            ev = record_outbox_event(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                event_type=f"relay.cycle.{i}",
                payload={"i": i},
            )
            staged_ids.append(ev.id)
        await session.commit()

    # Claim events with SKIP LOCKED
    async with get_tenant_db_context(tenant_id) as session:
        claimed = await claim_unpublished_outbox_events(session, limit=10)
        claimed_ids = [e.id for e in claimed if e.tenant_id == tenant_id]
        for sid in staged_ids:
            assert sid in claimed_ids

        # Mark claimed events as published
        published_count = await mark_outbox_events_published(session, claimed_ids)
        assert published_count >= len(staged_ids)
        await session.commit()

    # Re-polling should find 0 unpublished events for this tenant
    async with get_tenant_db_context(tenant_id) as session:
        remaining = await claim_unpublished_outbox_events(session, limit=10)
        remaining_for_tenant = [e.id for e in remaining if e.tenant_id == tenant_id]
        assert len(remaining_for_tenant) == 0


@pytest.mark.asyncio
async def test_unpublished_partial_index_catalog():
    """
    Verifies partial index ix_outbox_events_unpublished exists with predicate WHERE (published_at IS NULL).
    """
    tenant_id, _ = await register_test_tenant("tx-partial-idx")

    async with get_tenant_db_context(tenant_id) as session:
        idx_check = await session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'outbox_events' AND indexname = 'ix_outbox_events_unpublished';"
            )
        )
        row = idx_check.fetchone()
        assert row is not None, (
            "Partial index ix_outbox_events_unpublished not found in pg_indexes!"
        )
        idx_def = row[1]
        assert (
            "WHERE (published_at IS NULL)" in idx_def
            or "where (published_at is null)" in idx_def.lower()
        )
