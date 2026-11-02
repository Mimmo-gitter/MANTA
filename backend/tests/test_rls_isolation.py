"""
SCRUM-295: Automated pytest suite verifying multi-tenant Row-Level Security (RLS) isolation.

Verifies:
1. Cross-tenant READ isolation (Tenant A cannot see Tenant B's tenants, projects, users).
2. Cross-tenant UPDATE isolation (Tenant A cannot modify Tenant B's records; 0 rows affected).
3. Cross-tenant DELETE isolation (Tenant A cannot delete Tenant B's records; 0 rows affected).
4. Cross-tenant INSERT prevention (WITH CHECK policy raises DBAPIError on mismatched tenant_id).
5. Unauthenticated / empty context isolation (returns 0 rows by default; fail-safe closed).
6. Transaction pooling safety (set_config local=true is purged upon transaction completion).
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
