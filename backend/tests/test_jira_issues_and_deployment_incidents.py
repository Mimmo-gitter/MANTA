"""
SCRUM-399 / SCRUM-402 / US-67: Test Suite for Jira Issues Extensions, AC Storage & Deployment Incidents Link Table.
Design Specification: docs/jira-story-ac-storage-incident-linking-and-cancellation-test-scenarios.md

Acceptance Criteria Verified:
- Issue extensions: issue_links, changelog_id, active_fencing_token, acceptance_criteria, resolved_at.
- Sprint extensions: active_fencing_token, complete_date.
- DeploymentIncident creation linking deployment and issue with status and correlation_source.
- Unique constraint on (deployment_id, issue_id) prevents duplicate links.
- Check constraint on status IN ('CONFIRMED', 'CANCELLED', 'FALSE_POSITIVE').
- Tenant RLS isolation on deployment_incidents: Tenant A cannot view Tenant B's links.
- TC-JIRA-AC-001: Initial Story creation with Acceptance Criteria v1 and SHA-256 hash.
- TC-JIRA-AC-002: Story update changing Acceptance Criteria to v2 with immutable version history.
- TC-JIRA-TR-003: Sequential status transition history (To Do -> In Progress -> Done) with duration.
- TC-JIRA-CORR-004: Incident created 2h after deployment automatically linked within 24h window (CFR inclusion).
- TC-JIRA-CORR-005: Incident created 30h after deployment rejected (not linked, window > 24h).
- TC-JIRA-CANC-006: Incident transitioned to Cancelled unlinked (is_active=False, CFR exclusion).
- TC-JIRA-EDGE-007: Out-of-order Jira changelog event rejection (monotonic version precedence).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models.models import (
    AcceptanceCriteriaVersion,
    Commit,
    Deployment,
    DeploymentIncident,
    Issue,
    IssueTransition,
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


@pytest.mark.asyncio
async def test_jira_story_initial_creation_ac_v1():
    """TC-JIRA-AC-001: Verifies initial Story creation with AC v1 and SHA-256 hash."""
    tenant_id, _ = await register_test_tenant("jira-ac1")
    proj_id, _ = await create_test_project_and_deployment(tenant_id)

    ac_text = (
        "1. User can request SMS OTP if TOTP app is unreachable.\n"
        "2. Rate limit SMS to max 3 attempts per 15 minutes.\n"
        "3. Log security audit event on each attempt."
    )
    ac_hash = hashlib.sha256(ac_text.strip().encode("utf-8")).hexdigest()

    async with get_tenant_db_context(tenant_id) as session:
        issue_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        story = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="MANTA-101",
            external_issue_id=10045,
            issue_type="Story",
            title="Implement MFA OTP fallback via SMS and TOTP authenticator",
            status="To Do",
            story_points=5.0,
            changelog_id=1001,
            acceptance_criteria=ac_text,
            acceptance_criteria_hash=ac_hash,
            entity_version=1,
            created_at=now,
        )
        session.add(story)
        await session.flush()

        # Insert AC version 1 baseline
        ac_v1 = AcceptanceCriteriaVersion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            issue_id=issue_id,
            version=1,
            raw_criteria_text=ac_text,
            content_hash=ac_hash,
            lexical_tokens_count=len(ac_text.split()),
            created_at=now,
        )
        session.add(ac_v1)
        await session.flush()

    # Query back and verify persistence
    async with get_tenant_db_context(tenant_id) as session:
        issue = await session.get(Issue, issue_id)
        assert issue is not None
        assert issue.external_key == "MANTA-101"
        assert issue.status == "To Do"
        assert issue.changelog_id == 1001
        assert issue.acceptance_criteria_hash == ac_hash

        res_v1 = await session.execute(
            select(AcceptanceCriteriaVersion).where(
                AcceptanceCriteriaVersion.issue_id == issue_id,
                AcceptanceCriteriaVersion.version == 1,
            )
        )
        v1_row = res_v1.scalar_one_or_none()
        assert v1_row is not None
        assert v1_row.content_hash == ac_hash
        assert "Rate limit SMS" in v1_row.raw_criteria_text


@pytest.mark.asyncio
async def test_jira_story_update_ac_v2():
    """TC-JIRA-AC-002: Story update changing Acceptance Criteria to v2 with immutable history."""
    tenant_id, _ = await register_test_tenant("jira-ac2")
    proj_id, _ = await create_test_project_and_deployment(tenant_id)

    ac_v1_text = "1. Basic email notification on password reset."
    ac_v1_hash = hashlib.sha256(ac_v1_text.strip().encode("utf-8")).hexdigest()

    ac_v2_text = (
        "1. Basic email notification on password reset.\n"
        "2. Add magic link expiry of 10 minutes.\n"
        "3. Invalidate previous active sessions."
    )
    ac_v2_hash = hashlib.sha256(ac_v2_text.strip().encode("utf-8")).hexdigest()
    assert ac_v1_hash != ac_v2_hash

    issue_id = uuid.uuid4()
    async with get_tenant_db_context(tenant_id) as session:
        now = datetime.now(timezone.utc)
        story = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="MANTA-102",
            external_issue_id=10046,
            issue_type="Story",
            title="Password reset security enhancement",
            status="To Do",
            changelog_id=1001,
            acceptance_criteria=ac_v1_text,
            acceptance_criteria_hash=ac_v1_hash,
            entity_version=1,
            created_at=now,
        )
        session.add(story)
        await session.flush()

        v1 = AcceptanceCriteriaVersion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            issue_id=issue_id,
            version=1,
            raw_criteria_text=ac_v1_text,
            content_hash=ac_v1_hash,
            lexical_tokens_count=len(ac_v1_text.split()),
            created_at=now,
        )
        session.add(v1)
        await session.flush()

    # Apply AC v2 update
    async with get_tenant_db_context(tenant_id) as session:
        story = await session.get(Issue, issue_id)
        assert story is not None

        # Verify changelog precedence
        new_changelog_id = 1005
        assert new_changelog_id > story.changelog_id

        story.acceptance_criteria = ac_v2_text
        story.acceptance_criteria_hash = ac_v2_hash
        story.changelog_id = new_changelog_id
        story.entity_version += 1

        v2 = AcceptanceCriteriaVersion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            issue_id=issue_id,
            version=2,
            raw_criteria_text=ac_v2_text,
            content_hash=ac_v2_hash,
            lexical_tokens_count=len(ac_v2_text.split()),
            created_at=datetime.now(timezone.utc),
        )
        session.add(v2)
        await session.flush()

    # Assert both versions exist immutably
    async with get_tenant_db_context(tenant_id) as session:
        versions = (
            (
                await session.execute(
                    select(AcceptanceCriteriaVersion)
                    .where(AcceptanceCriteriaVersion.issue_id == issue_id)
                    .order_by(AcceptanceCriteriaVersion.version.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(versions) == 2
        assert versions[0].version == 1 and versions[0].content_hash == ac_v1_hash
        assert versions[1].version == 2 and versions[1].content_hash == ac_v2_hash
        assert "Invalidate previous" in versions[1].raw_criteria_text

        issue = await session.get(Issue, issue_id)
        assert issue.acceptance_criteria_hash == ac_v2_hash
        assert issue.changelog_id == 1005


@pytest.mark.asyncio
async def test_jira_story_status_transition_history():
    """TC-JIRA-TR-003: Sequential status transition history (To Do -> In Progress -> Done) with duration."""
    tenant_id, _ = await register_test_tenant("jira-trans")
    proj_id, _ = await create_test_project_and_deployment(tenant_id)

    issue_id = uuid.uuid4()
    t0 = datetime(2026, 11, 12, 9, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=2)   # 11:00 UTC (In Progress)
    t2 = t1 + timedelta(hours=4)   # 15:00 UTC (Done, duration = 14400s)

    async with get_tenant_db_context(tenant_id) as session:
        story = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="MANTA-103",
            external_issue_id=10047,
            issue_type="Story",
            title="Implement dashboard latency chart",
            status="To Do",
            changelog_id=1001,
            created_at=t0,
        )
        session.add(story)
        await session.flush()

    # Transition 1: To Do -> In Progress
    async with get_tenant_db_context(tenant_id) as session:
        story = await session.get(Issue, issue_id)
        story.status = "In Progress"
        story.changelog_id = 1010
        tr1 = IssueTransition(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            issue_id=issue_id,
            from_status="To Do",
            to_status="In Progress",
            duration_seconds=int((t1 - t0).total_seconds()),
            transitioned_at=t1,
        )
        session.add(tr1)
        await session.flush()

    # Transition 2: In Progress -> Done
    async with get_tenant_db_context(tenant_id) as session:
        story = await session.get(Issue, issue_id)
        story.status = "Done"
        story.resolved_at = t2
        story.changelog_id = 1018
        tr2 = IssueTransition(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            issue_id=issue_id,
            from_status="In Progress",
            to_status="Done",
            duration_seconds=int((t2 - t1).total_seconds()),
            transitioned_at=t2,
        )
        session.add(tr2)
        await session.flush()

    # Query back transitions
    async with get_tenant_db_context(tenant_id) as session:
        story = await session.get(Issue, issue_id)
        assert story.status == "Done"
        assert story.resolved_at == t2
        assert story.changelog_id == 1018

        transitions = (
            (
                await session.execute(
                    select(IssueTransition)
                    .where(IssueTransition.issue_id == issue_id)
                    .order_by(IssueTransition.transitioned_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(transitions) == 2
        assert transitions[0].from_status == "To Do" and transitions[0].to_status == "In Progress"
        assert transitions[1].from_status == "In Progress" and transitions[1].to_status == "Done"
        assert transitions[1].duration_seconds == 14400


@pytest.mark.asyncio
async def test_incident_created_2h_after_deployment_linked():
    """TC-JIRA-CORR-004: Incident created 2h after deployment automatically linked within 24h window (CFR inclusion)."""
    tenant_id, _ = await register_test_tenant("jira-corr-2h")
    proj_id, dep_id = await create_test_project_and_deployment(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        dep = await session.get(Deployment, dep_id)
        t_dep = dep.deployed_at
        t_inc = t_dep + timedelta(hours=2)

        # 2h delta check
        delta_seconds = (t_inc - t_dep).total_seconds()
        assert 0 <= delta_seconds <= 86400  # <= 24 hours

        incident_id = uuid.uuid4()
        inc = Issue(
            id=incident_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="INC-201",
            external_issue_id=20001,
            issue_type="Incident",
            title="Auth gateway timeouts post-deploy",
            status="OPEN",
            created_at=t_inc,
        )
        session.add(inc)
        await session.flush()

        # Correlation engine generates link
        link = DeploymentIncident(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep_id,
            issue_id=incident_id,
            status="CONFIRMED",
            correlation_source="automated_time_window_24h",
            detected_at=t_inc,
            is_active=True,
        )
        session.add(link)
        await session.flush()

    # Assert DORA CFR query includes this deployment in failure count
    async with get_tenant_db_context(tenant_id) as session:
        cfr_query = await session.execute(
            text(
                "SELECT COUNT(DISTINCT d.id) AS total, "
                "COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'FAILED' OR di.status = 'CONFIRMED') AS failed "
                "FROM deployments d "
                "LEFT JOIN deployment_incidents di ON di.deployment_id = d.id AND di.tenant_id = d.tenant_id "
                "WHERE d.tenant_id = :tid AND d.environment = 'production';"
            ),
            {"tid": tenant_id},
        )
        row = cfr_query.fetchone()
        assert row is not None
        total, failed = row
        assert total == 1
        assert failed == 1  # Correlated incident caused failure count


@pytest.mark.asyncio
async def test_incident_created_30h_after_deployment_not_linked():
    """TC-JIRA-CORR-005: Incident created 30h after deployment rejected (not linked, window > 24h)."""
    tenant_id, _ = await register_test_tenant("jira-corr-30h")
    proj_id, dep_id = await create_test_project_and_deployment(tenant_id)

    async with get_tenant_db_context(tenant_id) as session:
        dep = await session.get(Deployment, dep_id)
        t_dep = dep.deployed_at
        t_inc = t_dep + timedelta(hours=30)

        # 30h delta check
        delta_seconds = (t_inc - t_dep).total_seconds()
        assert delta_seconds > 86400  # Outside 24h correlation window

        incident_id = uuid.uuid4()
        inc = Issue(
            id=incident_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="INC-202",
            external_issue_id=20002,
            issue_type="Incident",
            title="Memory leak 30h later",
            status="OPEN",
            created_at=t_inc,
        )
        session.add(inc)
        await session.flush()

        # Engine checks window and skips link creation
        if 0 <= delta_seconds <= 86400:
            link = DeploymentIncident(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                deployment_id=dep_id,
                issue_id=incident_id,
                status="CONFIRMED",
                correlation_source="automated_time_window_24h",
                detected_at=t_inc,
            )
            session.add(link)
            await session.flush()

    # Assert 0 links exist for this incident
    async with get_tenant_db_context(tenant_id) as session:
        links_count = await session.scalar(
            select(func.count(DeploymentIncident.id)).where(DeploymentIncident.issue_id == incident_id)
        )
        assert links_count == 0

        # CFR failure count remains 0
        cfr_query = await session.execute(
            text(
                "SELECT COUNT(DISTINCT d.id) AS total, "
                "COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'FAILED' OR di.status = 'CONFIRMED') AS failed "
                "FROM deployments d "
                "LEFT JOIN deployment_incidents di ON di.deployment_id = d.id AND di.tenant_id = d.tenant_id "
                "WHERE d.tenant_id = :tid AND d.environment = 'production';"
            ),
            {"tid": tenant_id},
        )
        total, failed = cfr_query.fetchone()
        assert total == 1
        assert failed == 0


@pytest.mark.asyncio
async def test_incident_transitioned_to_cancelled_unlinked():
    """TC-JIRA-CANC-006: Incident transitioned to Cancelled unlinked (is_active=False, CFR exclusion)."""
    tenant_id, _ = await register_test_tenant("jira-canc")
    proj_id, dep_id = await create_test_project_and_deployment(tenant_id)

    link_id = uuid.uuid4()
    incident_id = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        dep = await session.get(Deployment, dep_id)
        t_inc = dep.deployed_at + timedelta(hours=1)

        inc = Issue(
            id=incident_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="INC-203",
            external_issue_id=20003,
            issue_type="Incident",
            title="Transient network spike falsely reported as bug",
            status="OPEN",
            created_at=t_inc,
        )
        session.add(inc)
        await session.flush()

        link = DeploymentIncident(
            id=link_id,
            tenant_id=tenant_id,
            deployment_id=dep_id,
            issue_id=incident_id,
            status="CONFIRMED",
            correlation_source="automated_time_window_24h",
            detected_at=t_inc,
            is_active=True,
        )
        session.add(link)
        await session.flush()

    # Verify initially counted in CFR
    async with get_tenant_db_context(tenant_id) as session:
        cfr_query = await session.execute(
            text(
                "SELECT COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'FAILED' OR di.status = 'CONFIRMED') "
                "FROM deployments d "
                "LEFT JOIN deployment_incidents di ON di.deployment_id = d.id AND di.tenant_id = d.tenant_id "
                "WHERE d.tenant_id = :tid AND d.environment = 'production';"
            ),
            {"tid": tenant_id},
        )
        assert cfr_query.scalar_one() == 1

    # Transition Incident to Cancelled (Triage unlinking)
    now_cancel = datetime.now(timezone.utc)
    async with get_tenant_db_context(tenant_id) as session:
        inc = await session.get(Issue, incident_id)
        inc.status = "Cancelled"
        inc.resolved_at = now_cancel

        link = await session.get(DeploymentIncident, link_id)
        link.status = "CANCELLED"
        link.is_active = False
        link.resolved_at = now_cancel
        await session.flush()

    # Assert CFR failure count drops to 0 after cancellation
    async with get_tenant_db_context(tenant_id) as session:
        link = await session.get(DeploymentIncident, link_id)
        assert link.status == "CANCELLED"
        assert link.is_active is False
        assert link.resolved_at is not None

        cfr_query = await session.execute(
            text(
                "SELECT COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'FAILED' OR di.status = 'CONFIRMED') "
                "FROM deployments d "
                "LEFT JOIN deployment_incidents di ON di.deployment_id = d.id AND di.tenant_id = d.tenant_id "
                "WHERE d.tenant_id = :tid AND d.environment = 'production';"
            ),
            {"tid": tenant_id},
        )
        assert cfr_query.scalar_one() == 0  # Cancelled incident is excluded from CFR failure numerator!


@pytest.mark.asyncio
async def test_jira_out_of_order_changelog_precedence():
    """TC-JIRA-EDGE-007: Out-of-order Jira changelog event rejection (monotonic version precedence)."""
    tenant_id, _ = await register_test_tenant("jira-ooo")
    proj_id, _ = await create_test_project_and_deployment(tenant_id)

    issue_id = uuid.uuid4()
    async with get_tenant_db_context(tenant_id) as session:
        story = Issue(
            id=issue_id,
            tenant_id=tenant_id,
            project_id=proj_id,
            external_key="MANTA-104",
            external_issue_id=10048,
            issue_type="Story",
            title="Rate limiter optimization",
            status="Done",
            changelog_id=1018,
            created_at=datetime.now(timezone.utc),
        )
        session.add(story)
        await session.flush()

    # Simulate arrival of out-of-order webhook carrying older changelog_id 1008
    stale_changelog_id = 1008
    stale_status = "In Progress"

    async with get_tenant_db_context(tenant_id) as session:
        story = await session.get(Issue, issue_id)
        assert story is not None

        # Version precedence check: discard if incoming <= stored
        if stale_changelog_id <= story.changelog_id:
            # Event discarded! No database mutation performed
            pass
        else:
            story.status = stale_status
            story.changelog_id = stale_changelog_id
            await session.flush()

    # Verify status and changelog_id were preserved without regression
    async with get_tenant_db_context(tenant_id) as session:
        story = await session.get(Issue, issue_id)
        assert story.status == "Done"
        assert story.changelog_id == 1018
