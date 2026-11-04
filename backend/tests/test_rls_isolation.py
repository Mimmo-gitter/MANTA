"""
SCRUM-294 / SCRUM-295: Automated pytest suite verifying multi-tenant Row-Level Security (RLS)
isolation scenarios (see docs/rls-test-scenarios.md).

Verifies:
1. Tenant A reads & writes own data; cross-tenant READ isolation (TC-RLS-001, TC-RLS-002).
2. Cross-tenant UPDATE isolation (Tenant A cannot modify Tenant B's records; 0 rows affected) (TC-RLS-003).
3. Cross-tenant DELETE isolation (Tenant A cannot delete Tenant B's records; 0 rows affected) (TC-RLS-003).
4. Cross-tenant INSERT prevention (WITH CHECK policy raises DBAPIError on mismatched tenant_id) (TC-RLS-003).
5. Cross-tenant ownership reassignment prevention (Tenant A cannot update own row to Tenant B's tenant_id) (TC-RLS-004).
6. Unset or explicitly cleared tenant setting returns 0 rows and blocks writes (fail-safe closed) (TC-RLS-005).
7. Mid-session tenant setting change switches isolation boundary and resets across transactions (TC-RLS-006).
"""

import uuid
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models.models import Project, Tenant, User
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context

TENANT_A_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

PROJECT_A_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROJECT_B_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

USER_A_ID = uuid.UUID("a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1")
USER_B_ID = uuid.UUID("b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2")


async def setup_test_tenants():
    """Ensure clean test tenants exist under their respective tenant contexts."""
    async with get_tenant_db_context(TENANT_A_ID) as session:
        existing_a = await session.get(Tenant, TENANT_A_ID)
        if not existing_a:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Alpha Corp',
                        p_slug => 'tenant-alpha-rls',
                        p_admin_email => 'admin@alpha-rls.com',
                        p_admin_name => 'Admin Alpha',
                        p_tier => 'enterprise',
                        p_ai_processing_mode => 'cloud_zdr',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_A_ID},
            )

    async with get_tenant_db_context(TENANT_B_ID) as session:
        existing_b = await session.get(Tenant, TENANT_B_ID)
        if not existing_b:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Beta Ltd',
                        p_slug => 'tenant-beta-rls',
                        p_admin_email => 'admin@beta-rls.com',
                        p_admin_name => 'Admin Beta',
                        p_tier => 'starter',
                        p_ai_processing_mode => 'local',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_B_ID},
            )


async def cleanup_test_data():
    """Clean up test entities and dispose of async engine connections."""
    try:
        async with get_tenant_db_context(TENANT_A_ID) as session:
            await session.execute(delete(Project).where(Project.tenant_id == TENANT_A_ID))
            await session.execute(delete(User).where(User.tenant_id == TENANT_A_ID))
            await session.execute(delete(Tenant).where(Tenant.id == TENANT_A_ID))

        async with get_tenant_db_context(TENANT_B_ID) as session:
            await session.execute(delete(Project).where(Project.tenant_id == TENANT_B_ID))
            await session.execute(delete(User).where(User.tenant_id == TENANT_B_ID))
            await session.execute(delete(Tenant).where(Tenant.id == TENANT_B_ID))
    except Exception:
        pass
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_tenant_read_isolation():
    """Verify Tenant A sees ONLY Tenant A, and Tenant B sees ONLY Tenant B."""
    await setup_test_tenants()
    try:
        async with get_tenant_db_context(TENANT_A_ID) as session:
            result = await session.scalars(select(Tenant))
            tenants = result.all()
            assert len(tenants) == 1
            assert tenants[0].id == TENANT_A_ID
            assert tenants[0].name == "Tenant Alpha Corp"

        async with get_tenant_db_context(TENANT_B_ID) as session:
            result = await session.scalars(select(Tenant))
            tenants = result.all()
            assert len(tenants) == 1
            assert tenants[0].id == TENANT_B_ID
            assert tenants[0].name == "Tenant Beta Ltd"
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_project_cross_tenant_read_isolation():
    """Verify Project records created by Tenant B are completely invisible to Tenant A."""
    await setup_test_tenants()
    try:
        # Tenant A creates Project A
        async with get_tenant_db_context(TENANT_A_ID) as session:
            session.add(
                Project(
                    id=PROJECT_A_ID,
                    tenant_id=TENANT_A_ID,
                    name="Project Alpha",
                    slug="project-alpha",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )

        # Tenant B creates Project B
        async with get_tenant_db_context(TENANT_B_ID) as session:
            session.add(
                Project(
                    id=PROJECT_B_ID,
                    tenant_id=TENANT_B_ID,
                    name="Project Beta",
                    slug="project-beta",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )

        # Tenant A queries all projects: MUST only see Project A
        async with get_tenant_db_context(TENANT_A_ID) as session:
            result = await session.scalars(select(Project))
            projects = result.all()
            project_ids = [p.id for p in projects]
            assert PROJECT_A_ID in project_ids
            assert PROJECT_B_ID not in project_ids
            assert len(projects) == 1

        # Tenant B queries all projects: MUST only see Project B
        async with get_tenant_db_context(TENANT_B_ID) as session:
            result = await session.scalars(select(Project))
            projects = result.all()
            project_ids = [p.id for p in projects]
            assert PROJECT_B_ID in project_ids
            assert PROJECT_A_ID not in project_ids
            assert len(projects) == 1
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_project_cross_tenant_update_isolation():
    """Verify Tenant A cannot update Tenant B's projects (0 rows affected)."""
    await setup_test_tenants()
    try:
        # Tenant B creates Project B
        async with get_tenant_db_context(TENANT_B_ID) as session:
            session.add(
                Project(
                    id=PROJECT_B_ID,
                    tenant_id=TENANT_B_ID,
                    name="Original Beta Name",
                    slug="project-beta-update",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )

        # Tenant A attempts to hijack/rename Project B
        async with get_tenant_db_context(TENANT_A_ID) as session:
            update_result = await session.execute(
                update(Project).where(Project.id == PROJECT_B_ID).values(name="Hacked by Tenant A")
            )
            assert update_result.rowcount == 0, "Tenant A updated Tenant B's project!"

        # Tenant B confirms Project B name is intact
        async with get_tenant_db_context(TENANT_B_ID) as session:
            proj_b = await session.get(Project, PROJECT_B_ID)
            assert proj_b is not None
            assert proj_b.name == "Original Beta Name"
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_project_cross_tenant_delete_isolation():
    """Verify Tenant A cannot delete Tenant B's projects (0 rows affected)."""
    await setup_test_tenants()
    try:
        # Tenant B creates Project B
        async with get_tenant_db_context(TENANT_B_ID) as session:
            session.add(
                Project(
                    id=PROJECT_B_ID,
                    tenant_id=TENANT_B_ID,
                    name="Beta to be Deleted",
                    slug="project-beta-del",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )

        # Tenant A attempts to delete Project B
        async with get_tenant_db_context(TENANT_A_ID) as session:
            delete_result = await session.execute(delete(Project).where(Project.id == PROJECT_B_ID))
            assert delete_result.rowcount == 0, "Tenant A deleted Tenant B's project!"

        # Tenant B confirms Project B still exists
        async with get_tenant_db_context(TENANT_B_ID) as session:
            proj_b = await session.get(Project, PROJECT_B_ID)
            assert proj_b is not None
            assert proj_b.name == "Beta to be Deleted"
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_cross_tenant_insert_violates_with_check():
    """Verify Tenant A cannot insert a record with tenant_id = Tenant B (WITH CHECK violation)."""
    await setup_test_tenants()
    try:
        with pytest.raises(DBAPIError) as exc_info:
            async with get_tenant_db_context(TENANT_A_ID) as session:
                session.add(
                    Project(
                        id=PROJECT_B_ID,
                        tenant_id=TENANT_B_ID,  # Mismatched tenant_id!
                        name="Illegal Cross-Tenant Project",
                        slug="illegal-proj",
                        tier_sla_hours=24,
                        authoritative_cd_signal="github_deployments_api",
                        lead_time_first_commit_strategy="feature_branch_root",
                        wip_limit_per_dev=3,
                        pr_stale_threshold_hours=48,
                        is_active=True,
                    )
                )
                await session.flush()

        assert "row-level security policy" in str(exc_info.value).lower()
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_user_cross_tenant_isolation():
    """Verify user records are strictly isolated across tenants."""
    await setup_test_tenants()
    try:
        # Tenant A creates User A
        async with get_tenant_db_context(TENANT_A_ID) as session:
            session.add(
                User(
                    id=USER_A_ID,
                    tenant_id=TENANT_A_ID,
                    email="alpha_dev@tenanta.com",
                    full_name="Alpha Developer",
                    google_sub="sub-alpha-123",
                    role="DEVELOPER",
                    is_active=True,
                )
            )

        # Tenant B creates User B
        async with get_tenant_db_context(TENANT_B_ID) as session:
            session.add(
                User(
                    id=USER_B_ID,
                    tenant_id=TENANT_B_ID,
                    email="beta_dev@tenantb.com",
                    full_name="Beta Developer",
                    google_sub="sub-beta-456",
                    role="DEVELOPER",
                    is_active=True,
                )
            )

        # Tenant A reads users -> sees only User A
        async with get_tenant_db_context(TENANT_A_ID) as session:
            users_a = (await session.scalars(select(User))).all()
            user_ids_a = [u.id for u in users_a]
            assert USER_A_ID in user_ids_a
            assert USER_B_ID not in user_ids_a

        # Tenant B reads users -> sees only User B
        async with get_tenant_db_context(TENANT_B_ID) as session:
            users_b = (await session.scalars(select(User))).all()
            user_ids_b = [u.id for u in users_b]
            assert USER_B_ID in user_ids_b
            assert USER_A_ID not in user_ids_b
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_unauthenticated_session_returns_zero_rows():
    """Verify that a session without app.current_tenant_id set cannot see any tenant data."""
    await setup_test_tenants()
    try:
        # Pre-seed Project A
        async with get_tenant_db_context(TENANT_A_ID) as session:
            session.add(
                Project(
                    id=PROJECT_A_ID,
                    tenant_id=TENANT_A_ID,
                    name="Protected Project",
                    slug="protected-proj",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )

        # Raw AsyncSession without tenant context: fail-safe closed
        async with AsyncSessionLocal() as raw_session:
            current_tid = await raw_session.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert current_tid in (None, "")

            tenants = (await raw_session.scalars(select(Tenant))).all()
            assert len(tenants) == 0, "Unauthenticated session saw tenants!"

            projects = (await raw_session.scalars(select(Project))).all()
            assert len(projects) == 0, "Unauthenticated session saw projects!"
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_transaction_local_scoping_prevents_pool_pollution():
    """Verify set_config(..., true) is purged after transaction ends, preventing pool leakage."""
    try:
        async with AsyncSessionLocal() as session:
            # 1. First transaction: set tenant context
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(TENANT_A_ID)},
                )
                tid_in_txn = await session.scalar(
                    text("SELECT current_setting('app.current_tenant_id', true)")
                )
                assert tid_in_txn == str(TENANT_A_ID)

            # 2. Second transaction on same session/connection: setting must be reset automatically
            async with session.begin():
                tid_after_txn = await session.scalar(
                    text("SELECT current_setting('app.current_tenant_id', true)")
                )
                assert tid_after_txn in (None, ""), (
                    f"Tenant context leaked across transactions: {tid_after_txn}"
                )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_cannot_update_own_row_to_another_tenants_id():
    """TC-RLS-004: Verify Tenant A cannot update its own row's tenant_id to Tenant B (WITH CHECK violation)."""
    await setup_test_tenants()
    try:
        # Tenant A creates Project A
        async with get_tenant_db_context(TENANT_A_ID) as session:
            session.add(
                Project(
                    id=PROJECT_A_ID,
                    tenant_id=TENANT_A_ID,
                    name="Project Alpha Owned by A",
                    slug="project-alpha-reassign",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )

        # Tenant A attempts to update Project A's tenant_id to Tenant B
        with pytest.raises(DBAPIError) as exc_info:
            async with get_tenant_db_context(TENANT_A_ID) as session:
                await session.execute(
                    update(Project).where(Project.id == PROJECT_A_ID).values(tenant_id=TENANT_B_ID)
                )

        assert "row-level security policy" in str(exc_info.value).lower()

        # Verify Project A still belongs to Tenant A and is invisible to Tenant B
        async with get_tenant_db_context(TENANT_A_ID) as session:
            proj_a = await session.get(Project, PROJECT_A_ID)
            assert proj_a is not None
            assert proj_a.tenant_id == TENANT_A_ID

        async with get_tenant_db_context(TENANT_B_ID) as session:
            proj_in_b = await session.get(Project, PROJECT_A_ID)
            assert proj_in_b is None
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_cleared_tenant_setting_returns_zero_rows_and_blocks_writes():
    """TC-RLS-005: Verify clearing app.current_tenant_id mid-transaction returns 0 rows and blocks writes."""
    await setup_test_tenants()
    try:
        # Seed Project A under Tenant A and Project B under Tenant B
        async with get_tenant_db_context(TENANT_A_ID) as session:
            session.add(
                Project(
                    id=PROJECT_A_ID,
                    tenant_id=TENANT_A_ID,
                    name="Project Alpha",
                    slug="project-alpha-clear",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )
        async with get_tenant_db_context(TENANT_B_ID) as session:
            session.add(
                Project(
                    id=PROJECT_B_ID,
                    tenant_id=TENANT_B_ID,
                    name="Project Beta",
                    slug="project-beta-clear",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )

        # 1. Start as Tenant A (1 row visible), then clear via set_config(..., '', true) and RESET
        async with get_tenant_db_context(TENANT_A_ID) as session:
            initial_projects = (await session.scalars(select(Project))).all()
            assert len(initial_projects) == 1
            assert initial_projects[0].id == PROJECT_A_ID

            # Clear setting to empty string
            await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
            session.expire_all()

            assert (await session.scalars(select(Tenant))).all() == []
            assert (await session.scalars(select(Project))).all() == []
            assert (await session.scalars(select(User))).all() == []

            # Writes while cleared must affect 0 rows
            upd_res = await session.execute(
                update(Project)
                .where(Project.id == PROJECT_A_ID)
                .values(name="Mutated While Cleared")
            )
            assert upd_res.rowcount == 0

            del_res = await session.execute(delete(Project).where(Project.id == PROJECT_A_ID))
            assert del_res.rowcount == 0

            # Re-set to Tenant A and then clear via RESET app.current_tenant_id
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(TENANT_A_ID)},
            )
            await session.execute(text("RESET app.current_tenant_id"))
            session.expire_all()
            assert (await session.scalars(select(Project))).all() == []

        # 2. Attempting INSERT while tenant setting is cleared violates WITH CHECK
        with pytest.raises(DBAPIError) as exc_info:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await session.execute(
                        text("SELECT set_config('app.current_tenant_id', '', true)")
                    )
                    session.add(
                        Project(
                            id=uuid.uuid4(),
                            tenant_id=TENANT_A_ID,
                            name="Unset Context Insert",
                            slug="unset-insert",
                            tier_sla_hours=24,
                            authoritative_cd_signal="github_deployments_api",
                            lead_time_first_commit_strategy="feature_branch_root",
                            wip_limit_per_dev=3,
                            pr_stale_threshold_hours=48,
                            is_active=True,
                        )
                    )
                    await session.flush()

        assert "row-level security policy" in str(exc_info.value).lower()
    finally:
        await cleanup_test_data()


@pytest.mark.asyncio
async def test_tenant_setting_changed_mid_session_switches_isolation_boundary():
    """TC-RLS-006: Verify changing app.current_tenant_id mid-session switches read and write boundaries."""
    await setup_test_tenants()
    try:
        # Seed Project A under Tenant A and Project B under Tenant B
        async with get_tenant_db_context(TENANT_A_ID) as session:
            session.add(
                Project(
                    id=PROJECT_A_ID,
                    tenant_id=TENANT_A_ID,
                    name="Project Alpha",
                    slug="project-alpha-switch",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )
        async with get_tenant_db_context(TENANT_B_ID) as session:
            session.add(
                Project(
                    id=PROJECT_B_ID,
                    tenant_id=TENANT_B_ID,
                    name="Project Beta",
                    slug="project-beta-switch",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Phase 1: Set context to Tenant A
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(TENANT_A_ID)},
                )
                tenants_a = (await session.scalars(select(Tenant))).all()
                assert [t.id for t in tenants_a] == [TENANT_A_ID]

                projects_a = (await session.scalars(select(Project))).all()
                assert [p.id for p in projects_a] == [PROJECT_A_ID]

                upd_a = await session.execute(
                    update(Project)
                    .where(Project.id == PROJECT_A_ID)
                    .values(name="Project Alpha Updated")
                )
                assert upd_a.rowcount == 1

                # Phase 2: Change setting mid-session on the SAME connection to Tenant B
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(TENANT_B_ID)},
                )
                session.expire_all()

                tenants_b = (await session.scalars(select(Tenant))).all()
                assert [t.id for t in tenants_b] == [TENANT_B_ID]

                projects_b = (await session.scalars(select(Project))).all()
                assert [p.id for p in projects_b] == [PROJECT_B_ID]

                # Writes against Tenant A's row must now affect 0 rows
                stale_upd_a = await session.execute(
                    update(Project)
                    .where(Project.id == PROJECT_A_ID)
                    .values(name="Illegal Post-Switch Mutation")
                )
                assert stale_upd_a.rowcount == 0

                stale_del_a = await session.execute(
                    delete(Project).where(Project.id == PROJECT_A_ID)
                )
                assert stale_del_a.rowcount == 0

                # Writes against Tenant B's row must succeed (1 row affected)
                upd_b = await session.execute(
                    update(Project)
                    .where(Project.id == PROJECT_B_ID)
                    .values(name="Project Beta Updated")
                )
                assert upd_b.rowcount == 1
    finally:
        await cleanup_test_data()
