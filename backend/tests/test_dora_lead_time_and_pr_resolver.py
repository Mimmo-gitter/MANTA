"""
US-26 (Task 26.2 & Task 26.3): Automated Unit & Integration Tests for:
- Task 26.2: Commit-range resolver mapping deployed commits to pull requests across
  merge, squash, and rebase strategies while excluding trunk-only commits and already-shipped PRs.
- Task 26.3: Per-PR Lead Time (T_prod_deployed - T_first_commit) and project median Lead Time
  (percentile_cont(0.5)) calculator over 14d and 30d windows using contributor pseudonyms.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from typing import Optional
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from app.contributors.service import resolve_or_create_contributor_pseudonym
from app.db.models.models import (
    Commit,
    Deployment,
    DeploymentPullRequest,
    Project,
    PullRequest,
    Repository,
)
from app.db.session import async_engine, get_tenant_db_context
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.deployments.service import (
    compute_commit_range_shas,
    match_unclaimed_pull_requests_in_range,
    persist_deployment_idempotent,
    resolve_deployment_pull_requests,
)
from app.dora.schemas import DailyLeadTimeMetric, LeadTimeResult, PullRequestLeadTimeItem
from app.dora.service import (
    build_project_median_lead_time_sql_query,
    build_shipped_pull_requests_lead_time_query,
    calculate_rolling_lead_time,
    compute_continuous_median,
    compute_lead_time_metrics,
    compute_pr_lead_time_hours,
    compute_pr_lead_time_seconds,
)
from app.main import app


def is_live_db_available() -> bool:
    """Check if a real PostgreSQL instance is reachable with configured credentials."""
    try:

        async def _ping() -> None:
            await async_engine.dispose()
            try:
                async with async_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await async_engine.dispose()

        asyncio.run(_ping())
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL test instance is not reachable locally; runs in CI.",
)


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test."""
    from app.tenants.rate_limiter import registration_rate_limiter

    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


async def _register_test_tenant(slug_prefix: str) -> tuple[uuid.UUID, str]:
    """Register an isolated test tenant via /tenants/register for live DB tests."""
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"DORA Lead Time Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"lt-{unique}@doratemp.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


def _sha(label: str) -> str:
    """Generate a deterministic 40-char lowercase hex Git commit SHA from a label."""
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _pseudonym(label: str) -> str:
    """Generate a deterministic 64-char HMAC-SHA256 hex contributor pseudonym."""
    return hmac.new(b"test-salt-us26", label.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_commit(
    sha: str,
    *,
    parent_shas: list[str] | None = None,
    pull_request_id: Optional[uuid.UUID] = None,
    tenant_id: Optional[uuid.UUID] = None,
    repository_id: Optional[uuid.UUID] = None,
    author_pseudonym_id: Optional[str] = None,
    authored_at: Optional[datetime] = None,
) -> Commit:
    ts = authored_at or datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    return Commit(
        sha=sha,
        tenant_id=tenant_id or uuid.uuid4(),
        repository_id=repository_id or uuid.uuid4(),
        author_pseudonym_id=author_pseudonym_id or _pseudonym("default-author"),
        committer_pseudonym_id=author_pseudonym_id or _pseudonym("default-author"),
        lines_added=10,
        lines_deleted=2,
        authored_at=ts,
        committed_at=ts,
        parent_shas=parent_shas or [],
        is_root_of_branch=False,
        pull_request_id=pull_request_id,
    )


def _make_pr(
    *,
    pr_number: int,
    head_sha: str,
    merge_commit_sha: Optional[str] = None,
    first_commit_at: Optional[datetime] = None,
    merged_at: Optional[datetime] = None,
    state: str = "merged",
    base_branch: str = "main",
    author_pseudonym_id: Optional[str] = None,
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    repository_id: Optional[uuid.UUID] = None,
    pr_id: Optional[uuid.UUID] = None,
) -> PullRequest:
    created_ts = first_commit_at or datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    merged_ts = merged_at or (created_ts + timedelta(hours=4))
    return PullRequest(
        id=pr_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        repository_id=repository_id or uuid.uuid4(),
        author_pseudonym_id=author_pseudonym_id or _pseudonym(f"author-pr-{pr_number}"),
        external_pr_number=pr_number,
        title=f"PR #{pr_number}",
        state=state,
        head_branch=f"feat/pr-{pr_number}",
        base_branch=base_branch,
        head_sha=head_sha,
        merge_commit_sha=merge_commit_sha,
        first_commit_at=first_commit_at,
        created_in_git_at=created_ts,
        merged_at=merged_ts if state.lower() == "merged" else None,
    )


def _make_deployment(
    *,
    commit_sha: str,
    deployed_at: datetime,
    status: str = DeploymentOutcome.SUCCESS.value,
    environment: str = "production",
    signal_source: str = DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
    actor_pseudonym_id: Optional[str] = None,
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    repository_id: Optional[uuid.UUID] = None,
    deployment_id: Optional[uuid.UUID] = None,
) -> Deployment:
    return Deployment(
        id=deployment_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        repository_id=repository_id,
        actor_pseudonym_id=actor_pseudonym_id or _pseudonym("release-bot"),
        environment=environment,
        commit_sha=commit_sha,
        status=status,
        is_change_failure=status
        in (DeploymentOutcome.FAILED.value, DeploymentOutcome.ROLLED_BACK.value),
        authoritative_signal_source=signal_source,
        deployed_at=deployed_at,
    )


# ===========================================================================
# PART 1: Task 26.2 — Commit Range Resolver & PR Attribution Tests
# ===========================================================================


def test_task_26_2_single_pr_standard_merge_strategy():
    """
    Task 26.2: Single-PR deployment using standard merge (--no-ff) matches PR via
    merge_commit_sha and head_sha without duplicating the PR in the returned list.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sha_d0 = _sha("d0-prev-prod")
    sha_feat_1 = _sha("pr101-feat-1")
    sha_feat_2 = _sha("pr101-feat-2-head")
    sha_merge = _sha("pr101-merge-commit")

    commits = {
        sha_d0: _make_commit(sha_d0, parent_shas=[]),
        sha_feat_1: _make_commit(sha_feat_1, parent_shas=[sha_d0]),
        sha_feat_2: _make_commit(sha_feat_2, parent_shas=[sha_feat_1]),
        sha_merge: _make_commit(sha_merge, parent_shas=[sha_d0, sha_feat_2]),
    }

    range_shas = compute_commit_range_shas(
        sha_merge,
        previous_deployment_shas=[sha_d0],
        commits_by_sha=commits,
    )
    assert range_shas == {sha_merge, sha_feat_2, sha_feat_1}
    assert sha_d0 not in range_shas

    pr_101 = _make_pr(
        pr_number=101,
        head_sha=sha_feat_2,
        merge_commit_sha=sha_merge,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    dep = _make_deployment(
        commit_sha=sha_merge,
        deployed_at=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_101],
        commits_in_range=list(commits.values()),
    )
    assert len(matched) == 1
    assert matched[0].id == pr_101.id


def test_task_26_2_single_pr_squash_and_merge_strategy():
    """
    Task 26.2: Single-PR deployment using squash-and-merge matches PR via merge_commit_sha
    even though original feature branch commits (head_sha) are not parents of the squash commit.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sha_d0 = _sha("d0-prev")
    sha_dangling_head = _sha("pr102-dangling-feature-head")
    sha_squash = _sha("pr102-squash-on-main")

    commits = {
        sha_d0: _make_commit(sha_d0, parent_shas=[]),
        sha_squash: _make_commit(sha_squash, parent_shas=[sha_d0]),
    }

    range_shas = compute_commit_range_shas(
        sha_squash,
        previous_deployment_shas=[sha_d0],
        commits_by_sha=commits,
    )
    assert range_shas == {sha_squash}

    pr_102 = _make_pr(
        pr_number=102,
        head_sha=sha_dangling_head,
        merge_commit_sha=sha_squash,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    dep = _make_deployment(
        commit_sha=sha_squash,
        deployed_at=datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_102],
        commits_in_range=list(commits.values()),
    )
    assert [p.external_pr_number for p in matched] == [102]


def test_task_26_2_single_pr_rebase_and_merge_strategy():
    """
    Task 26.2: Single-PR deployment using rebase-and-merge matches PR via rebased commits
    linked through commits.pull_request_id (or merge_commit_sha / head_sha).
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pr_103_id = uuid.uuid4()
    sha_d0 = _sha("d0-base")
    sha_orig_head = _sha("pr103-pre-rebase-head")
    sha_rebased_1 = _sha("pr103-rebased-1")
    sha_rebased_2 = _sha("pr103-rebased-2-tip")

    commits = {
        sha_d0: _make_commit(sha_d0, parent_shas=[]),
        sha_rebased_1: _make_commit(sha_rebased_1, parent_shas=[sha_d0], pull_request_id=pr_103_id),
        sha_rebased_2: _make_commit(
            sha_rebased_2, parent_shas=[sha_rebased_1], pull_request_id=pr_103_id
        ),
    }

    range_shas = compute_commit_range_shas(
        sha_rebased_2,
        previous_deployment_shas=[sha_d0],
        commits_by_sha=commits,
    )
    assert range_shas == {sha_rebased_1, sha_rebased_2}

    # Even if merge_commit_sha was NULL on the PR and head_sha was the pre-rebase SHA,
    # commits.pull_request_id links the rebased commits in S_i back to PR #103.
    pr_103 = _make_pr(
        pr_id=pr_103_id,
        pr_number=103,
        head_sha=sha_orig_head,
        merge_commit_sha=None,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    dep = _make_deployment(
        commit_sha=sha_rebased_2,
        deployed_at=datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_103],
        commits_in_range=list(commits.values()),
    )
    assert [p.external_pr_number for p in matched] == [103]


def test_task_26_2_batched_multi_pr_deployment_across_all_three_strategies_and_trunk_exclusion():
    """
    Task 26.2 Acceptance Criteria:
    Batched multi-PR deployment containing:
    - PR #1 (already shipped in D_{i-1}) -> MUST NOT be duplicated
    - PR #2 (standard --no-ff merge in S_i) -> matched
    - Direct trunk-only hotfix commit on main (pull_request_id=None) -> traversed & ignored
    - PR #3 (squash-and-merge in S_i) -> matched
    - PR #4 (rebase-and-merge in S_i) -> matched
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # D_{i-1} shipped PR #1 at sha_m1
    sha_root = _sha("root")
    sha_pr1_head = _sha("pr1-head")
    sha_m1 = _sha("m1-prev-prod-deploy")

    # Commits in S_i = (D_{i-1}, D_i]:
    # 1. PR #2 (--no-ff merge)
    sha_pr2_c1 = _sha("pr2-c1")
    sha_pr2_head = _sha("pr2-head")
    sha_m2_merge = _sha("m2-merge-pr2")
    # 2. Direct trunk commit on main (no PR)
    sha_trunk_direct = _sha("trunk-direct-bump-version")
    # 3. PR #3 (squash merge)
    sha_pr3_dangling_head = _sha("pr3-dangling-head")
    sha_m3_squash = _sha("m3-squash-pr3")
    # 4. PR #4 (rebase merge of 2 commits onto sha_m3_squash)
    pr4_id = uuid.uuid4()
    sha_pr4_orig_head = _sha("pr4-orig-head")
    sha_r4_1 = _sha("r4-1")
    sha_r4_2_tip = _sha("r4-2-deployed-tip")

    commits = {
        sha_root: _make_commit(sha_root, parent_shas=[]),
        sha_pr1_head: _make_commit(sha_pr1_head, parent_shas=[sha_root]),
        sha_m1: _make_commit(sha_m1, parent_shas=[sha_root, sha_pr1_head]),
        sha_pr2_c1: _make_commit(sha_pr2_c1, parent_shas=[sha_m1]),
        sha_pr2_head: _make_commit(sha_pr2_head, parent_shas=[sha_pr2_c1]),
        sha_m2_merge: _make_commit(sha_m2_merge, parent_shas=[sha_m1, sha_pr2_head]),
        sha_trunk_direct: _make_commit(
            sha_trunk_direct, parent_shas=[sha_m2_merge], pull_request_id=None
        ),
        sha_m3_squash: _make_commit(sha_m3_squash, parent_shas=[sha_trunk_direct]),
        sha_r4_1: _make_commit(sha_r4_1, parent_shas=[sha_m3_squash], pull_request_id=pr4_id),
        sha_r4_2_tip: _make_commit(sha_r4_2_tip, parent_shas=[sha_r4_1], pull_request_id=pr4_id),
    }

    range_shas = compute_commit_range_shas(
        sha_r4_2_tip,
        previous_deployment_shas=[sha_m1],
        commits_by_sha=commits,
    )
    # S_i contains PR2 commits, the trunk commit, PR3 squash commit, and PR4 rebased commits,
    # but strictly excludes sha_m1, sha_pr1_head, and sha_root!
    assert range_shas == {
        sha_pr2_c1,
        sha_pr2_head,
        sha_m2_merge,
        sha_trunk_direct,
        sha_m3_squash,
        sha_r4_1,
        sha_r4_2_tip,
    }
    assert sha_m1 not in range_shas
    assert sha_pr1_head not in range_shas

    pr1_shipped = _make_pr(
        pr_number=1,
        head_sha=sha_pr1_head,
        merge_commit_sha=sha_m1,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr2_merge = _make_pr(
        pr_number=2,
        head_sha=sha_pr2_head,
        merge_commit_sha=sha_m2_merge,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr3_squash = _make_pr(
        pr_number=3,
        head_sha=sha_pr3_dangling_head,
        merge_commit_sha=sha_m3_squash,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr4_rebase = _make_pr(
        pr_id=pr4_id,
        pr_number=4,
        head_sha=sha_pr4_orig_head,
        merge_commit_sha=sha_r4_2_tip,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    dep_i = _make_deployment(
        commit_sha=sha_r4_2_tip,
        deployed_at=datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep_i,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr1_shipped, pr2_merge, pr3_squash, pr4_rebase],
        commits_in_range=[commits[s] for s in range_shas],
        already_claimed_pr_ids={pr1_shipped.id},
        default_branch="main",
    )
    assert [p.external_pr_number for p in matched] == [2, 3, 4]


def test_task_26_2_redeploy_and_already_claimed_pr_prevention():
    """
    Task 26.2:
    1. Re-deploying the exact same commit SHA (or an ancestor SHA) yields an empty range.
    2. Even if a PR's SHA is in range_shas, if its ID is in already_claimed_pr_ids, it is skipped.
    3. Non-production ('staging') or FAILED deployments never claim PRs.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sha_1 = _sha("sha-1")
    sha_2 = _sha("sha-2")
    commits = {
        sha_1: _make_commit(sha_1, parent_shas=[]),
        sha_2: _make_commit(sha_2, parent_shas=[sha_1]),
    }

    # Re-deploy of sha_2 when sha_2 was already deployed
    assert (
        compute_commit_range_shas(sha_2, previous_deployment_shas=[sha_2], commits_by_sha=commits)
        == set()
    )
    # Rollback to ancestor sha_1 when sha_2 was already deployed
    assert (
        compute_commit_range_shas(sha_1, previous_deployment_shas=[sha_2], commits_by_sha=commits)
        == set()
    )

    pr = _make_pr(
        pr_number=50,
        head_sha=sha_2,
        merge_commit_sha=sha_2,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    dep_prod = _make_deployment(
        commit_sha=sha_2,
        deployed_at=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    # Already claimed PR is excluded
    assert (
        match_unclaimed_pull_requests_in_range(
            dep_prod,
            commit_range_shas={sha_2},
            candidate_pull_requests=[pr],
            already_claimed_pr_ids={pr.id},
        )
        == []
    )

    # FAILED deployment does not claim PRs
    dep_failed = _make_deployment(
        commit_sha=sha_2,
        deployed_at=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc),
        status=DeploymentOutcome.FAILED.value,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    assert (
        match_unclaimed_pull_requests_in_range(
            dep_failed,
            commit_range_shas={sha_2},
            candidate_pull_requests=[pr],
        )
        == []
    )


@pytest.mark.asyncio
async def test_task_26_2_resolve_deployment_pull_requests_async_session():
    """
    Task 26.2: Verify resolve_deployment_pull_requests queries prior SUCCESS boundary,
    walks commit DAG, filters unclaimed merged PRs, and executes idempotent insert into
    deployment_pull_requests.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sha_d0 = _sha("async-d0")
    sha_m1 = _sha("async-m1")
    sha_m2 = _sha("async-m2")

    commit_d0 = _make_commit(sha_d0, parent_shas=[], tenant_id=tenant_id, repository_id=repo_id)
    commit_m1 = _make_commit(
        sha_m1, parent_shas=[sha_d0], tenant_id=tenant_id, repository_id=repo_id
    )
    commit_m2 = _make_commit(
        sha_m2, parent_shas=[sha_m1], tenant_id=tenant_id, repository_id=repo_id
    )

    pr_1 = _make_pr(
        pr_number=11,
        head_sha=sha_m1,
        merge_commit_sha=sha_m1,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_2 = _make_pr(
        pr_number=12,
        head_sha=sha_m2,
        merge_commit_sha=sha_m2,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    dep = _make_deployment(
        commit_sha=sha_m2,
        deployed_at=datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    expected_links = [
        DeploymentPullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep.id,
            pull_request_id=pr_1.id,
        ),
        DeploymentPullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep.id,
            pull_request_id=pr_2.id,
        ),
    ]

    class _MockResult:
        def __init__(self, items, scalar_single=None):
            self._items = items
            self._scalar_single = scalar_single

        def scalars(self):
            return self

        def all(self):
            return self._items

        def scalar_one_or_none(self):
            return self._scalar_single

    class _MockAsyncSession:
        def __init__(self):
            self.executed_statements = []
            self._step = 0

        async def execute(self, stmt):
            self.executed_statements.append(stmt)
            self._step += 1
            if self._step == 1:
                # 1. Previous SUCCESS production deployment SHAs
                return _MockResult([sha_d0])
            if self._step == 2:
                # 2. Repository.default_branch
                return _MockResult([], scalar_single="main")
            if self._step == 3:
                # 3. Commits in repository
                return _MockResult([commit_d0, commit_m1, commit_m2])
            if self._step == 4:
                # 4. Already-claimed pull_request_ids
                return _MockResult([])
            if self._step == 5:
                # 5. Candidate merged pull requests
                return _MockResult([pr_1, pr_2])
            if self._step == 6:
                # 6. INSERT INTO deployment_pull_requests ... ON CONFLICT DO NOTHING
                return _MockResult(expected_links)
            raise AssertionError(f"Unexpected query step {self._step}")

    session = _MockAsyncSession()
    inserted = await resolve_deployment_pull_requests(session, deployment=dep)  # type: ignore[arg-type]
    assert len(inserted) == 2
    assert {link.pull_request_id for link in inserted} == {pr_1.id, pr_2.id}

    compiled_insert = str(session.executed_statements[-1].compile(dialect=postgresql.dialect()))
    assert "INSERT INTO deployment_pull_requests" in compiled_insert
    assert "ON CONFLICT (deployment_id, pull_request_id) DO NOTHING" in compiled_insert


# ===========================================================================
# PART 2: Task 26.3 — Per-PR Lead Time & Project Median Lead Time Calculator
# ===========================================================================


def test_task_26_3_single_pr_26_5_hours_acceptance_criterion():
    """
    Task 26.3 Acceptance Criterion 1:
    Single PR with first_commit_at 26.5h before deployed_at returns 26.5 hours
    (and 95,400.0 seconds) for both per-PR Lead Time and project median Lead Time.
    """
    deployed_at = datetime(2026, 11, 14, 14, 30, 0, tzinfo=timezone.utc)
    first_commit_at = deployed_at - timedelta(hours=26, minutes=30)  # Exactly 26.5h earlier

    assert compute_pr_lead_time_seconds(first_commit_at, deployed_at) == 95400.0
    assert compute_pr_lead_time_hours(first_commit_at, deployed_at) == 26.5

    author_pseudo = _pseudonym("dev-alice")
    actor_pseudo = _pseudonym("ci-deployer")
    pr = _make_pr(
        pr_number=201,
        head_sha=_sha("pr201-head"),
        merge_commit_sha=_sha("pr201-merge"),
        first_commit_at=first_commit_at,
        merged_at=deployed_at - timedelta(hours=1),
        author_pseudonym_id=author_pseudo,
    )
    dep = _make_deployment(
        commit_sha=_sha("pr201-merge"),
        deployed_at=deployed_at,
        actor_pseudonym_id=actor_pseudo,
        tenant_id=pr.tenant_id,
        project_id=pr.project_id,
        repository_id=pr.repository_id,
    )

    result = compute_lead_time_metrics(
        [(pr, dep)],
        window_days=14,
        window_end=deployed_at + timedelta(hours=1),
    )

    assert isinstance(result, LeadTimeResult)
    assert result.window_days == 14
    assert result.deployed_pr_count == 1
    assert result.median_lead_time_hours == 26.5
    assert result.median_lead_time_seconds == 95400.0

    assert len(result.pull_requests) == 1
    pr_item = result.pull_requests[0]
    assert isinstance(pr_item, PullRequestLeadTimeItem)
    assert pr_item.pull_request_id == pr.id
    assert pr_item.deployment_id == dep.id
    assert pr_item.external_pr_number == 201
    assert pr_item.author_pseudonym_id == author_pseudo
    assert pr_item.actor_pseudonym_id == actor_pseudo
    assert pr_item.lead_time_hours == 26.5
    assert pr_item.lead_time_seconds == 95400.0


def test_task_26_3_batched_prs_exact_median_23_5_hours_acceptance_criterion():
    """
    Task 26.3 Acceptance Criterion 2:
    Batched PRs return exact percentile_cont(0.5) median (23.5 hours on test fixture).
    Verifies both even-numbered PR batch (linear interpolation of middle pair) and
    odd-numbered PR batch.
    """
    deployed_at = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    dep = _make_deployment(
        commit_sha=_sha("batch-release"),
        deployed_at=deployed_at,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    # Fixture A: 4 batched PRs with lead times [16.0h, 22.0h, 25.0h, 31.0h]
    # percentile_cont(0.5) interpolates 0.5 * (22.0 + 25.0) = 23.5 hours (84,600.0 seconds)
    lead_hours_even = [16.0, 22.0, 25.0, 31.0]
    shipped_even = [
        (
            _make_pr(
                pr_number=300 + idx,
                head_sha=_sha(f"even-head-{idx}"),
                merge_commit_sha=dep.commit_sha,
                first_commit_at=deployed_at - timedelta(hours=hrs),
                merged_at=deployed_at - timedelta(minutes=30),
                author_pseudonym_id=_pseudonym(f"contrib-{idx}"),
                tenant_id=tenant_id,
                project_id=project_id,
                repository_id=repo_id,
            ),
            dep,
        )
        for idx, hrs in enumerate(lead_hours_even, start=1)
    ]

    res_even = compute_lead_time_metrics(
        shipped_even,
        window_days=14,
        window_end=deployed_at,
    )
    assert res_even.deployed_pr_count == 4
    assert res_even.median_lead_time_hours == 23.5
    assert res_even.median_lead_time_seconds == 84600.0
    assert [item.lead_time_hours for item in res_even.pull_requests] == [16.0, 22.0, 25.0, 31.0]

    # Fixture B: 3 batched PRs with lead times [19.0h, 23.5h, 29.0h] -> middle element is 23.5h
    lead_hours_odd = [29.0, 19.0, 23.5]
    shipped_odd = [
        (
            _make_pr(
                pr_number=400 + idx,
                head_sha=_sha(f"odd-head-{idx}"),
                merge_commit_sha=dep.commit_sha,
                first_commit_at=deployed_at - timedelta(hours=hrs),
                merged_at=deployed_at - timedelta(minutes=20),
                author_pseudonym_id=_pseudonym(f"contrib-odd-{idx}"),
                tenant_id=tenant_id,
                project_id=project_id,
                repository_id=repo_id,
            ),
            dep,
        )
        for idx, hrs in enumerate(lead_hours_odd, start=1)
    ]

    res_odd = compute_lead_time_metrics(
        shipped_odd,
        window_days=30,
        window_end=deployed_at,
    )
    assert res_odd.window_days == 30
    assert res_odd.deployed_pr_count == 3
    assert res_odd.median_lead_time_hours == 23.5
    assert res_odd.median_lead_time_seconds == 84600.0


def test_task_26_3_empty_window_returns_none_acceptance_criterion():
    """
    Task 26.3 Acceptance Criterion 3:
    When 0 PRs were deployed in the window, median_lead_time_hours and
    median_lead_time_seconds return None (null), and every daily bucket has None median.
    """
    window_end = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone.utc)

    for window_days in (14, 30):
        res = compute_lead_time_metrics(
            [],
            window_days=window_days,  # type: ignore[arg-type]
            window_end=window_end,
        )
        assert res.window_days == window_days
        assert res.deployed_pr_count == 0
        assert res.median_lead_time_hours is None
        assert res.median_lead_time_seconds is None
        assert res.pull_requests == []
        assert len(res.daily_series) == window_days
        assert all(
            day.deployed_pr_count == 0
            and day.median_lead_time_hours is None
            and day.median_lead_time_seconds is None
            for day in res.daily_series
        )


def test_task_26_3_daily_trend_series_and_contributor_pseudonym_isolation():
    """
    Task 26.3:
    - Daily trend series computes per-day percentile_cont(0.5) median on active days and None on idle days.
    - Contributor pseudonyms (author_pseudonym_id, actor_pseudonym_id) are preserved without raw PII,
      and filtering by author_pseudonym_id isolates that contributor's PRs.
    """
    window_end = datetime(2026, 11, 14, 20, 0, 0, tzinfo=timezone.utc)
    day_1_deploy_time = datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc)
    day_2_deploy_time = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)

    pseudo_dev_a = _pseudonym("developer-a")
    pseudo_dev_b = _pseudonym("developer-b")

    dep_day1 = _make_deployment(commit_sha=_sha("d-day1"), deployed_at=day_1_deploy_time)
    dep_day2 = _make_deployment(commit_sha=_sha("d-day2"), deployed_at=day_2_deploy_time)

    # Day 1 (2026-11-12): 2 PRs with lead times 10.0h and 14.0h -> Day 1 median = 12.0h
    pr_1 = _make_pr(
        pr_number=1,
        head_sha=_sha("h1"),
        first_commit_at=day_1_deploy_time - timedelta(hours=10),
        author_pseudonym_id=pseudo_dev_a,
    )
    pr_2 = _make_pr(
        pr_number=2,
        head_sha=_sha("h2"),
        first_commit_at=day_1_deploy_time - timedelta(hours=14),
        author_pseudonym_id=pseudo_dev_b,
    )

    # Day 2 (2026-11-14): 1 PR with lead time 26.5h -> Day 2 median = 26.5h
    pr_3 = _make_pr(
        pr_number=3,
        head_sha=_sha("h3"),
        first_commit_at=day_2_deploy_time - timedelta(hours=26, minutes=30),
        author_pseudonym_id=pseudo_dev_a,
    )

    # Window median across [10.0h, 14.0h, 26.5h] -> 14.0h
    res_all = compute_lead_time_metrics(
        [(pr_1, dep_day1), (pr_2, dep_day1), (pr_3, dep_day2)],
        window_days=14,
        window_end=window_end,
    )
    assert res_all.deployed_pr_count == 3
    assert res_all.median_lead_time_hours == 14.0

    by_date: dict[date, DailyLeadTimeMetric] = {d.date: d for d in res_all.daily_series}
    assert by_date[date(2026, 11, 12)].deployed_pr_count == 2
    assert by_date[date(2026, 11, 12)].median_lead_time_hours == 12.0
    assert by_date[date(2026, 11, 13)].deployed_pr_count == 0
    assert by_date[date(2026, 11, 13)].median_lead_time_hours is None
    assert by_date[date(2026, 11, 14)].deployed_pr_count == 1
    assert by_date[date(2026, 11, 14)].median_lead_time_hours == 26.5

    # Filter by contributor pseudonym pseudo_dev_a -> [10.0h, 26.5h] -> median 18.25h
    res_dev_a = compute_lead_time_metrics(
        [(pr_1, dep_day1), (pr_2, dep_day1), (pr_3, dep_day2)],
        window_days=14,
        window_end=window_end,
        author_pseudonym_id=pseudo_dev_a,
    )
    assert res_dev_a.deployed_pr_count == 2
    assert res_dev_a.median_lead_time_hours == 18.25

    # Verify serialized payload contains only pseudonyms and zero raw PII fields
    serialized = res_all.model_dump()
    for item_dict in serialized["pull_requests"]:
        assert "author_pseudonym_id" in item_dict
        assert len(item_dict["author_pseudonym_id"]) == 64
        for forbidden_pii_key in (
            "raw_email",
            "raw_name",
            "raw_handle",
            "author_email",
            "author_id",
        ):
            assert forbidden_pii_key not in item_dict


def test_task_26_3_invalid_inputs_and_sql_query_builders():
    """
    Task 26.3:
    - Unsupported window_days raises ValueError.
    - Negative lead time (deployed_at < first_commit_at) raises ValueError in direct helper
      and is ignored in batch aggregation.
    - SQL query builders compile valid PostgreSQL SELECT statements with percentile_cont(0.5).
    """
    t0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="cannot be earlier"):
        compute_pr_lead_time_seconds(t0, t0 - timedelta(minutes=5))
    with pytest.raises(ValueError, match="cannot be earlier"):
        compute_pr_lead_time_hours(t0, t0 - timedelta(minutes=5))
    with pytest.raises(ValueError, match="Unsupported window_days"):
        compute_lead_time_metrics([], window_days=7)  # type: ignore[arg-type]

    assert compute_continuous_median([]) is None

    tenant_id, project_id = uuid.uuid4(), uuid.uuid4()
    pseudo = _pseudonym("filter-author")

    shipped_stmt = build_shipped_pull_requests_lead_time_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=t0 - timedelta(days=14),
        end_at=t0,
        author_pseudonym_id=pseudo,
    )
    compiled_shipped = str(shipped_stmt.compile(dialect=postgresql.dialect()))
    assert "JOIN deployment_pull_requests" in compiled_shipped
    assert (
        "deployments.authoritative_signal_source = projects.authoritative_cd_signal"
        in compiled_shipped
    )
    assert "pull_requests.author_pseudonym_id =" in compiled_shipped

    median_stmt = build_project_median_lead_time_sql_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=t0 - timedelta(days=14),
        end_at=t0,
        author_pseudonym_id=pseudo,
    )
    compiled_median = str(median_stmt.compile(dialect=postgresql.dialect()))
    assert "percentile_cont" in compiled_median.lower()
    assert "within group" in compiled_median.lower()


@pytest.mark.asyncio
async def test_task_26_3_calculate_rolling_lead_time_async_service():
    """
    Task 26.3: Verify calculate_rolling_lead_time executes query and computes
    LeadTimeResult with mock AsyncSession.
    """
    tenant_id, project_id = uuid.uuid4(), uuid.uuid4()
    deployed_at = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
    pr = _make_pr(
        pr_number=77,
        head_sha=_sha("pr77"),
        first_commit_at=deployed_at - timedelta(hours=26, minutes=30),
        tenant_id=tenant_id,
        project_id=project_id,
    )
    dep = _make_deployment(
        commit_sha=_sha("pr77"),
        deployed_at=deployed_at,
        tenant_id=tenant_id,
        project_id=project_id,
    )

    class _MockResult:
        def all(self):
            return [(pr, dep)]

    class _MockSession:
        async def execute(self, stmt):
            self.last_stmt = stmt
            return _MockResult()

    session = _MockSession()
    res = await calculate_rolling_lead_time(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
        window_end=deployed_at,
    )
    assert res.deployed_pr_count == 1
    assert res.median_lead_time_hours == 26.5
    assert res.median_lead_time_seconds == 95400.0


# ===========================================================================
# PART 3: Live PostgreSQL 16 End-to-End Verification (Task 26.2 + Task 26.3)
# ===========================================================================


@skip_without_live_db
@pytest.mark.asyncio
async def test_tasks_26_2_and_26_3_live_postgres_e2e():
    """
    Live PostgreSQL 16 E2E test:
    1. Creates tenant, project, repository, contributor pseudonyms, commit DAG, and 2 merged PRs
       (with lead times 20.0h and 27.0h -> median 23.5h) plus a trunk-only commit.
    2. Persists a SUCCESS production deployment via persist_deployment_idempotent.
    3. Verifies deployment_pull_requests automatically links both PRs and ignores the trunk commit.
    4. Verifies calculate_rolling_lead_time and build_project_median_lead_time_sql_query
       both return 23.5h (84,600.0s).
    """
    tenant_id, _ = await _register_test_tenant("us26-e2e")
    project_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    deployed_at = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Project(
                id=project_id,
                tenant_id=tenant_id,
                name="US26 DORA Project",
                slug=f"proj-{tenant_id.hex[:6]}",
                authoritative_cd_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            )
        )
        await db.flush()

        db.add(
            Repository(
                id=repo_id,
                tenant_id=tenant_id,
                project_id=project_id,
                external_repo_id=int(tenant_id.int % 1_000_000_000),
                full_name="acme/us26-service",
                default_branch="main",
            )
        )
        await db.flush()

        pseudo_a = await resolve_or_create_contributor_pseudonym(
            db, tenant_id=tenant_id, provider="github", external_actor_id="dev-a"
        )
        pseudo_b = await resolve_or_create_contributor_pseudonym(
            db, tenant_id=tenant_id, provider="github", external_actor_id="dev-b"
        )

        sha_m1 = _sha(f"e2e-m1-{tenant_id.hex[:6]}")
        sha_trunk = _sha(f"e2e-trunk-{tenant_id.hex[:6]}")
        sha_m2 = _sha(f"e2e-m2-{tenant_id.hex[:6]}")

        pr1 = _make_pr(
            pr_number=501,
            head_sha=sha_m1,
            merge_commit_sha=sha_m1,
            first_commit_at=deployed_at - timedelta(hours=20),
            merged_at=deployed_at - timedelta(hours=2),
            author_pseudonym_id=pseudo_a,
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repo_id,
        )
        pr2 = _make_pr(
            pr_number=502,
            head_sha=sha_m2,
            merge_commit_sha=sha_m2,
            first_commit_at=deployed_at - timedelta(hours=27),
            merged_at=deployed_at - timedelta(hours=1),
            author_pseudonym_id=pseudo_b,
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repo_id,
        )
        db.add_all([pr1, pr2])
        await db.flush()

        db.add_all(
            [
                _make_commit(
                    sha_m1,
                    parent_shas=[],
                    pull_request_id=pr1.id,
                    tenant_id=tenant_id,
                    repository_id=repo_id,
                    author_pseudonym_id=pseudo_a,
                ),
                _make_commit(
                    sha_trunk,
                    parent_shas=[sha_m1],
                    pull_request_id=None,
                    tenant_id=tenant_id,
                    repository_id=repo_id,
                    author_pseudonym_id=pseudo_a,
                ),
                _make_commit(
                    sha_m2,
                    parent_shas=[sha_trunk],
                    pull_request_id=pr2.id,
                    tenant_id=tenant_id,
                    repository_id=repo_id,
                    author_pseudonym_id=pseudo_b,
                ),
            ]
        )
        await db.flush()

        dep, dedup = await persist_deployment_idempotent(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repo_id,
            integration_id=None,
            commit_sha=sha_m2,
            environment="production",
            outcome=DeploymentOutcome.SUCCESS,
            deployed_at=deployed_at,
            authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
            external_deployment_id=f"gh-e2e-{tenant_id.hex[:8]}",
            actor=None,
            actor_default_provider="github",
        )
        assert dedup is False

        links = list(
            (
                await db.execute(
                    select(DeploymentPullRequest).where(
                        DeploymentPullRequest.deployment_id == dep.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {lnk.pull_request_id for lnk in links} == {pr1.id, pr2.id}

        lead_res = await calculate_rolling_lead_time(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            window_days=14,
            window_end=deployed_at,
        )
        assert lead_res.deployed_pr_count == 2
        assert lead_res.median_lead_time_hours == 23.5
        assert lead_res.median_lead_time_seconds == 84600.0

        sql_row = (
            await db.execute(
                build_project_median_lead_time_sql_query(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    start_at=deployed_at - timedelta(days=14),
                    end_at=deployed_at,
                )
            )
        ).first()
        assert sql_row is not None
        assert float(sql_row[0]) == 84600.0
        assert int(sql_row[1]) == 2
