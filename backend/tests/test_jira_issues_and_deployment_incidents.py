"""
SCRUM-399 / US-67: Test Suite for Jira Issues Extensions & Deployment Incidents Link Table.

Acceptance Criteria Verified:
- Issue extensions: issue_links, changelog_id, active_fencing_token, acceptance_criteria, resolved_at.
- Sprint extensions: active_fencing_token, complete_date.
- DeploymentIncident creation linking deployment and issue with status and correlation_source.
- Unique constraint on (deployment_id, issue_id) prevents duplicate links.
- Check constraint on status IN ('CONFIRMED', 'CANCELLED', 'FALSE_POSITIVE').
- Tenant RLS isolation on deployment_incidents: Tenant A cannot view Tenant B's links.
"""

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.models import (
    Commit,
    Deployment,
    DeploymentIncident,
    Issue,
    Project,
    Repository,
    Sprint,
)
from app.db.session import async_engine, get_tenant_db_context
from app.main import app


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
        "name": f"Jira Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"admin-{unique}@jiratest.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


async def create_test_project_and_deployment(tenant_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Helper to insert project, repository, commit, and deployment under tenant context."""
    async with get_tenant_db_context(tenant_id) as session:
        proj_id = uuid.uuid4()
        project = Project(
            id=proj_id,
            tenant_id=tenant_id,
            name=f"Project {proj_id.hex[:6]}",
            slug=f"proj-{proj_id.hex[:6]}",
            tier_sla_hours=4.0,
        )
        session.add(project)
        await session.flush()

        repo_id = uuid.uuid4()
        repo = Repository(
            id=repo_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_repo_id=abs(hash(repo_id.hex)) % 10000000,
            full_name=f"org/repo-{repo_id.hex[:6]}",
            default_branch="main",
            is_private=True,
        )
        session.add(repo)
        await session.flush()

        sha = uuid.uuid4().hex[:40]
        now = datetime.now(timezone.utc)
        commit = Commit(
            sha=sha,
            tenant_id=tenant_id,
            repository_id=repo_id,
            author_pseudonym_id="0" * 64,
            lines_added=10,
            lines_deleted=5,
            authored_at=now,
            committed_at=now,
        )
        session.add(commit)
        await session.flush()

        dep_id = uuid.uuid4()
        deployment = Deployment(
            id=dep_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            repository_id=repo_id,
            commit_sha=sha,
            environment="production",
            status="SUCCESS",
            is_change_failure=False,
            deployed_at=now,
        )
        session.add(deployment)
        await session.flush()
        return proj_id, dep_id


@pytest.mark.asyncio
async def test_issue_and_sprint_schema_extensions():
    """TC-JIRA-001: Verifies issue and sprint extensions persistence."""
    tenant_id, _ = await register_test_tenant("jira-ext")
    proj_id, _ = await create_test_project_and_deployment(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        sprint_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        sprint = Sprint(
            id=sprint_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_sprint_id="sprint-101",
            name="Sprint 101",
            state="active",
            start_date=now - timedelta(days=7),
            end_date=now + timedelta(days=7),
            complete_date=now + timedelta(days=6),
            active_fencing_token=42,
        )
        session.add(sprint)
        await session.flush()

        issue_id = uuid.uuid4()
        links = [
            {"type": "Blocks", "direction": "outward", "target_key": "PROJ-200"},
            {"type": "Relates", "direction": "inward", "target_key": "INC-404"},
        ]
        issue = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            sprint_id=sprint_id,
            external_key="PROJ-101",
            external_issue_id=987654321,
            issue_type="Bug",
            title="Production outage triggered by schema change",
            status="Resolved",
            story_points=5.0,
            issue_links=links,
            changelog_id=1055,
            active_fencing_token=99,
            acceptance_criteria="System recovers within 5 minutes without manual restart.",
            acceptance_criteria_hash="a" * 64,
            resolved_at=now,
        )
        session.add(issue)
        await session.flush()

        # Read back and assert fields
        res_issue = await session.execute(
            text(
                "SELECT external_issue_id, issue_links, changelog_id, active_fencing_token, "
                "acceptance_criteria, acceptance_criteria_hash, resolved_at "
                "FROM issues WHERE id = :id;"
            ),
            {"id": issue_id},
        )
        row = res_issue.fetchone()
        assert row is not None
        ext_id, r_links, ch_id, f_tok, ac_text, ac_hash, res_at = row
        assert ext_id == 987654321
        assert len(r_links) == 2
        assert r_links[0]["type"] == "Blocks"
        assert ch_id == 1055
        assert f_tok == 99
        assert "System recovers" in ac_text
        assert ac_hash == "a" * 64
        assert res_at is not None

        # Verify sprint extensions
        res_sprint = await session.execute(
            text("SELECT complete_date, active_fencing_token FROM sprints WHERE id = :id;"),
            {"id": sprint_id},
        )
        sp_row = res_sprint.fetchone()
        assert sp_row is not None
        comp_date, sp_ftok = sp_row
        assert comp_date is not None
        assert sp_ftok == 42


@pytest.mark.asyncio
async def test_deployment_incidents_link_and_correlation():
    """TC-JIRA-002: Verifies creating deployment_incidents and bidirectional relationships."""
    tenant_id, _ = await register_test_tenant("jira-link")
    proj_id, dep_id = await create_test_project_and_deployment(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        issue_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        issue = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="INC-001",
            issue_type="Incident",
            title="Sev-1 payment gateway timeout",
            status="OPEN",
        )
        session.add(issue)
        await session.flush()

        link_id = uuid.uuid4()
        dep_incident = DeploymentIncident(
            id=link_id,
            tenant_id=tenant_id,
            deployment_id=dep_id,
            issue_id=issue_id,
            status="CONFIRMED",
            correlation_source="jira_issue_link",
            detected_at=now,
            resolved_at=now + timedelta(minutes=45),
            recovery_time_seconds=2700,
            is_active=True,
        )
        session.add(dep_incident)
        await session.flush()

        # Query and verify
        res = await session.execute(
            text(
                "SELECT status, correlation_source, recovery_time_seconds, is_active "
                "FROM deployment_incidents WHERE id = :id;"
            ),
            {"id": link_id},
        )
        row = res.fetchone()
        assert row is not None
        status, corr_src, rec_secs, is_act = row
        assert status == "CONFIRMED"
        assert corr_src == "jira_issue_link"
        assert rec_secs == 2700
        assert is_act is True


@pytest.mark.asyncio
async def test_deployment_incidents_unique_constraint():
    """TC-JIRA-003: Duplicate link on (deployment_id, issue_id) raises IntegrityError."""
    tenant_id, _ = await register_test_tenant("jira-uq")
    proj_id, dep_id = await create_test_project_and_deployment(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        issue_id = uuid.uuid4()
        issue = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="INC-002",
            issue_type="Incident",
            title="Database locking spike",
            status="Investigating",
        )
        session.add(issue)
        await session.flush()

        link1 = DeploymentIncident(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep_id,
            issue_id=issue_id,
            status="CONFIRMED",
            correlation_source="automated_probe",
        )
        session.add(link1)
        await session.flush()

        link2 = DeploymentIncident(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep_id,
            issue_id=issue_id,
            status="CONFIRMED",
            correlation_source="jira_webhook",
        )
        session.add(link2)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_deployment_incidents_status_check_constraint():
    """TC-JIRA-004: Status check constraint permits only CONFIRMED, CANCELLED, FALSE_POSITIVE."""
    tenant_id, _ = await register_test_tenant("jira-ck")
    proj_id, dep_id = await create_test_project_and_deployment(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        # Valid statuses each associated with a separate issue
        for i, valid_status in enumerate(["CONFIRMED", "CANCELLED", "FALSE_POSITIVE"], start=1):
            issue_id = uuid.uuid4()
            issue = Issue(
                id=issue_id,
                tenant_id=tenant_id,
                project_id=proj_id,
                external_key=f"INC-CK-{i}",
                issue_type="Incident",
                title=f"Check constraint ticket {i}",
                status="OPEN",
            )
            session.add(issue)
            await session.flush()

            test_link = DeploymentIncident(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                deployment_id=dep_id,
                issue_id=issue_id,
                status=valid_status,
                correlation_source="test",
            )
            session.add(test_link)
            await session.flush()

            res = await session.execute(
                text("SELECT status FROM deployment_incidents WHERE id = :id;"),
                {"id": test_link.id},
            )
            assert res.scalar_one() == valid_status

        # Invalid status should fail check constraint
        bogus_issue_id = uuid.uuid4()
        bogus_issue = Issue(
            id=bogus_issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="INC-CK-BOGUS",
            issue_type="Incident",
            title="Bogus status test ticket",
            status="OPEN",
        )
        session.add(bogus_issue)
        await session.flush()

        invalid_link = DeploymentIncident(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep_id,
            issue_id=bogus_issue_id,
            status="BOGUS_STATUS",
            correlation_source="test_ck",
        )
        session.add(invalid_link)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_deployment_incidents_rls_tenant_isolation():
    """TC-JIRA-005: Tenant B cannot view or manipulate Tenant A's deployment incident links."""
    tenant_a, _ = await register_test_tenant("jira-rls-a")
    tenant_b, _ = await register_test_tenant("jira-rls-b")

    proj_a, dep_a = await create_test_project_and_deployment(tenant_a)
    proj_b, dep_b = await create_test_project_and_deployment(tenant_b)

    link_a_id = uuid.uuid4()
    link_b_id = uuid.uuid4()

    async with get_tenant_db_context(tenant_a) as session_a:
        issue_a = Issue(
            id=uuid.uuid4(),
            tenant_id=tenant_a,
            project_id=proj_a,
            external_key="INCA-1",
            issue_type="Incident",
            title="Tenant A Incident",
            status="OPEN",
        )
        session_a.add(issue_a)
        await session_a.flush()

        link_a = DeploymentIncident(
            id=link_a_id,
            tenant_id=tenant_a,
            deployment_id=dep_a,
            issue_id=issue_a.id,
            status="CONFIRMED",
            correlation_source="test_a",
        )
        session_a.add(link_a)
        await session_a.flush()

    async with get_tenant_db_context(tenant_b) as session_b:
        issue_b = Issue(
            id=uuid.uuid4(),
            tenant_id=tenant_b,
            project_id=proj_b,
            external_key="INCB-1",
            issue_type="Incident",
            title="Tenant B Incident",
            status="OPEN",
        )
        session_b.add(issue_b)
        await session_b.flush()

        link_b = DeploymentIncident(
            id=link_b_id,
            tenant_id=tenant_b,
            deployment_id=dep_b,
            issue_id=issue_b.id,
            status="CONFIRMED",
            correlation_source="test_b",
        )
        session_b.add(link_b)
        await session_b.flush()

        # Under tenant B session, tenant A's link is completely invisible
        res_a = await session_b.execute(
            text("SELECT count(*) FROM deployment_incidents WHERE id = :id;"),
            {"id": link_a_id},
        )
        assert res_a.scalar_one() == 0

        # Tenant B can see their own link
        res_b = await session_b.execute(
            text("SELECT count(*) FROM deployment_incidents WHERE id = :id;"),
            {"id": link_b_id},
        )
        assert res_b.scalar_one() == 1
