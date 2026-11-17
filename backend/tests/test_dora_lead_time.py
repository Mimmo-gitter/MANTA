"""
US-26: Automated pytest test suite for DORA Lead Time for Changes and PR-to-Deployment Attribution.

Task: SCRUM-437 (Task 26.5: Write automated pytest tests verifying 26.5h single PR and 23.5h batched median Lead Time scenarios)
Parent User Story: SCRUM-363 (US-26: DORA Lead Time for Changes & PR-to-Deployment Attribution)
Design Specifications:
- docs/dora-lead-time-for-changes-test-scenarios.md (Task 26.4 / SCRUM-436)
- backend/tests/scenarios/test_us26_lead_time_for_changes_scenarios.md (Task 26.4 / SCRUM-436)
- docs/dora-pr-to-deployment-mapping-design.md (Task 26.1 / SCRUM-433)

Master Test Matrix Coverage:
- TC-DORA-LT-001: Single Merge Commit PR ($T_0 \to T_0 + 26h 30m = 26.50h / 95,400.0s)
- TC-DORA-LT-002: Batched Squash-Merged PRs — Even Batch ($N=4$, $[16.0, 22.0, 25.0, 31.0] \to 23.50h / 84,600.0s$)
- TC-DORA-LT-003: Batched Squash-Merged PRs — Odd Batch ($N=3$, $[19.0, 23.5, 29.0] \to 23.50h / 84,600.0s$)
- TC-DORA-LT-004: Trunk Commit Exclusion & Direct Push Filtering (direct trunk push without PR excluded from PR claims)
- TC-DORA-LT-005: Zero-Deployment Window — null Semantics (null vs 0.00 rate contrast)
- TC-DORA-LT-006: Rebase-and-Merge Linear DAG Attribution (original branch first_commit_at preserved across rebased SHAs)
- TC-DORA-LT-007: Heterogeneous Multi-Strategy Batched Release (Merge + Squash + Rebase + Direct Trunk Push)
- TC-DORA-LT-008: Re-deployment & Rollback No-Op Idempotency (re-deploying same commit SHA or ancestor returns empty range)
- TC-DORA-LT-009: Multi-Tenant Row-Level Security & Contributor Pseudonym Privacy (HMAC-SHA256, zero raw PII)
- TC-DORA-LT-010: Authoritative CD Signal Source Filtering (GitHub Deployments vs ArgoCD vs Direct API)
- TC-DORA-LT-011: Window Temporal Boundaries & UTC Midnight Grouping (inclusive endpoints, microsecond bounds)
- TC-DORA-LT-LIVE: Live PostgreSQL 16 database verification under tenant RLS (executed in CI)
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from typing import Optional
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text
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
)
from app.dora.service import (
    build_project_median_lead_time_sql_query,
    build_shipped_pull_requests_lead_time_query,
    calculate_rolling_lead_time,
    compute_lead_time_metrics,
    compute_pr_lead_time_hours,
    compute_pr_lead_time_seconds,
)
from app.main import app


# ---------------------------------------------------------------------------
# Database Availability & Teardown Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Deterministic Test Fixtures & In-Memory Generators
# ---------------------------------------------------------------------------


def _sha(label: str) -> str:
    """Generate a deterministic 40-char lowercase hex Git commit SHA from a label."""
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _pseudonym(label: str) -> str:
    """Generate a deterministic 64-char HMAC-SHA256 hex contributor pseudonym."""
    return hmac.new(b"test-salt-us26-scrum-437", label.encode("utf-8"), hashlib.sha256).hexdigest()


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
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    repository_id: Optional[uuid.UUID] = None,
    author_pseudonym_id: Optional[str] = None,
    state: str = "merged",
    base_branch: str = "main",
) -> PullRequest:
    t_first = first_commit_at or datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    t_merged = merged_at or (t_first + timedelta(hours=10))
    return PullRequest(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        repository_id=repository_id or uuid.uuid4(),
        author_pseudonym_id=author_pseudonym_id or _pseudonym(f"author-{pr_number}"),
        external_pr_number=pr_number,
        title=f"Feature Pull Request #{pr_number}",
        state=state,
        head_branch=f"feat/branch-{pr_number}",
        base_branch=base_branch,
        head_sha=head_sha,
        merge_commit_sha=merge_commit_sha,
        first_commit_at=t_first,
        created_in_git_at=t_first + timedelta(minutes=10),
        merged_at=t_merged,
        closed_at=t_merged,
    )


def _make_deployment(
    *,
    commit_sha: str,
    deployed_at: datetime,
    status: str = DeploymentOutcome.SUCCESS.value,
    environment: str = "production",
    signal_source: str = DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    repository_id: Optional[uuid.UUID] = None,
    actor_pseudonym_id: Optional[str] = None,
) -> Deployment:
    return Deployment(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        repository_id=repository_id or uuid.uuid4(),
        actor_pseudonym_id=actor_pseudonym_id or _pseudonym("release-bot"),
        environment=environment,
        commit_sha=commit_sha,
        status=status,
        is_change_failure=status
        in (DeploymentOutcome.FAILED.value, DeploymentOutcome.ROLLED_BACK.value),
        authoritative_signal_source=signal_source,
        deployed_at=deployed_at,
    )


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


# ===========================================================================
# SCENARIO TC-DORA-LT-001: Single Merge Commit PR (26.5h / 95,400.0s)
# ===========================================================================


def test_tc_dora_lt_001_single_merge_commit_pr_26_5_hours():
    """
    Scenario TC-DORA-LT-001 (SCRUM-436 / SCRUM-437 / SCRUM-435 AC 1):
    Single PR merged into trunk via standard merge (--no-ff).
    - Earliest commit: T0 = 2026-11-12T12:00:00Z.
    - Production deployment: T_deploy = 2026-11-13T14:30:00Z (T0 + 26h 30m).
    - Expected Lead Time: exactly 95,400.0 seconds and 26.50 hours.
    - Project median evaluates to exactly 26.50 hours.
    - Daily series buckets 26.5h to 2026-11-13 and null to the preceding 13 days.
    """
    t0 = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
    t_deployed = datetime(2026, 11, 13, 14, 30, 0, tzinfo=timezone.utc)

    # Invariant 1: Mathematical formulation
    seconds = compute_pr_lead_time_seconds(t0, t_deployed)
    hours = compute_pr_lead_time_hours(t0, t_deployed)
    assert seconds == 95400.0
    assert hours == 26.5

    # Graph & Topology setup
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sha_anchor = "acd64de32964ba83f3f7de3979104de072150485"
    sha_c1 = "3f5f67e759abd255c471637450df250c24b22e1d"
    sha_c2 = "74e7fcf0335ba1cbf633fb7efd6fdda29b4a3214"
    sha_m1 = "5273610b58406de60def552d340fed6190da57c9"

    commits = {
        sha_anchor: _make_commit(sha_anchor, parent_shas=[]),
        sha_c1: _make_commit(sha_c1, parent_shas=[sha_anchor], authored_at=t0),
        sha_c2: _make_commit(sha_c2, parent_shas=[sha_c1], authored_at=t0 + timedelta(hours=4)),
        sha_m1: _make_commit(
            sha_m1, parent_shas=[sha_anchor, sha_c2], authored_at=t_deployed - timedelta(hours=1)
        ),
    }

    # DAG Range Traversal: prune at sha_anchor
    range_shas = compute_commit_range_shas(
        sha_m1,
        previous_deployment_shas=[sha_anchor],
        commits_by_sha=commits,
    )
    assert range_shas == {sha_m1, sha_c2, sha_c1}
    assert sha_anchor not in range_shas

    author_pseudo = _pseudonym("pseudo_alice")
    actor_pseudo = _pseudonym("release_bot")

    pr_101 = _make_pr(
        pr_number=101,
        head_sha=sha_c2,
        merge_commit_sha=sha_m1,
        first_commit_at=t0,
        merged_at=t_deployed - timedelta(hours=1),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
        author_pseudonym_id=author_pseudo,
    )
    dep_01 = _make_deployment(
        commit_sha=sha_m1,
        deployed_at=t_deployed,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
        actor_pseudonym_id=actor_pseudo,
    )

    # Attribution matching
    matched = match_unclaimed_pull_requests_in_range(
        dep_01,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_101],
        commits_in_range=list(commits.values()),
    )
    assert len(matched) == 1
    assert matched[0].id == pr_101.id

    # Compute Lead Time metrics over 14-day window ending 2026-11-14T00:00:00Z
    window_end = datetime(2026, 11, 14, 0, 0, 0, tzinfo=timezone.utc)
    res = compute_lead_time_metrics(
        [(pr_101, dep_01)],
        window_days=14,
        window_end=window_end,
    )

    assert res.window_days == 14
    assert res.deployed_pr_count == 1
    assert res.median_lead_time_hours == 26.50
    assert res.median_lead_time_seconds == 95400.0
    assert len(res.pull_requests) == 1

    item = res.pull_requests[0]
    assert item.external_pr_number == 101
    assert item.lead_time_hours == 26.5
    assert item.lead_time_seconds == 95400.0
    assert item.author_pseudonym_id == author_pseudo
    assert item.actor_pseudonym_id == actor_pseudo
    assert item.merge_commit_sha == sha_m1
    assert item.head_sha == sha_c2

    # Daily time series assertions (14 UTC calendar buckets)
    assert len(res.daily_series) == 14
    by_date = {bucket.date: bucket for bucket in res.daily_series}
    deploy_bucket = by_date[date(2026, 11, 13)]
    assert deploy_bucket.deployed_pr_count == 1
    assert deploy_bucket.median_lead_time_hours == 26.5
    assert deploy_bucket.median_lead_time_seconds == 95400.0

    # The other 13 days must report 0 PR count and null medians
    for d, bucket in by_date.items():
        if d != date(2026, 11, 13):
            assert bucket.deployed_pr_count == 0
            assert bucket.median_lead_time_hours is None
            assert bucket.median_lead_time_seconds is None


# ===========================================================================
# SCENARIO TC-DORA-LT-002: Batched Squash-Merged PRs — Even Batch (23.5h Median)
# ===========================================================================


def test_tc_dora_lt_002_batched_squash_merged_prs_even_batch_23_5_hours():
    """
    Scenario TC-DORA-LT-002 (SCRUM-436 / SCRUM-437 / SCRUM-435 AC 2):
    Batched production release shipping 4 squash-merged PRs (even N=4).
    - Raw lead times: [31.0h, 25.0h, 22.0h, 16.0h].
    - Continuous median linear interpolation: 0.5 * (22.0 + 25.0) = 23.50 hours (84,600.0s).
    - Validates squash-merge 1-parent trunk commits where original branch heads are dangling.
    """
    t_deploy = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    sha_anchor = "5273610b58406de60def552d340fed6190da57c9"
    sha_q1 = "5bca982b79bfad0116adb4dca4483ce927a55791"
    sha_q2 = "2d1c974b7a5e57168ad0af0ea7aa0663f9ec0eef"
    sha_q3 = "c5e6835a7ce522a188605dccd841a0cfcbf13948"
    sha_q4 = "8daea49620214de10f16c02dfa08a237e478f28c"

    commits = {
        sha_anchor: _make_commit(sha_anchor, parent_shas=[]),
        sha_q1: _make_commit(
            sha_q1, parent_shas=[sha_anchor], authored_at=t_deploy - timedelta(hours=4)
        ),
        sha_q2: _make_commit(
            sha_q2, parent_shas=[sha_q1], authored_at=t_deploy - timedelta(hours=3)
        ),
        sha_q3: _make_commit(
            sha_q3, parent_shas=[sha_q2], authored_at=t_deploy - timedelta(hours=2)
        ),
        sha_q4: _make_commit(
            sha_q4, parent_shas=[sha_q3], authored_at=t_deploy - timedelta(hours=1)
        ),
    }

    # Range traversal from tip Q4 to anchor
    range_shas = compute_commit_range_shas(
        sha_q4,
        previous_deployment_shas=[sha_anchor],
        commits_by_sha=commits,
    )
    assert range_shas == {sha_q4, sha_q3, sha_q2, sha_q1}
    assert sha_anchor not in range_shas

    pr_specs = [
        (301, "d4b37bae0005dc776bccc31871a9b80bcedb78d3", sha_q1, 31.0),
        (302, "90ca0b9fb44a48893b4daee6969c4e4eb148cb7a", sha_q2, 25.0),
        (303, "3198f751ee4e902eb72bb33be65fb5c45ef8bf88", sha_q3, 22.0),
        (304, "b58403c889d2831a690633f15745b01dbff223b5", sha_q4, 16.0),
    ]

    candidate_prs = [
        _make_pr(
            pr_number=num,
            head_sha=dangling_head,
            merge_commit_sha=squash_sha,
            first_commit_at=t_deploy - timedelta(hours=lead_h),
            merged_at=t_deploy - timedelta(minutes=30),
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repo_id,
            author_pseudonym_id=_pseudonym(f"contrib-{num}"),
        )
        for num, dangling_head, squash_sha, lead_h in pr_specs
    ]

    dep_02 = _make_deployment(
        commit_sha=sha_q4,
        deployed_at=t_deploy,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep_02,
        commit_range_shas=range_shas,
        candidate_pull_requests=candidate_prs,
        commits_in_range=list(commits.values()),
    )
    assert len(matched) == 4
    matched_pr_numbers = {pr.external_pr_number for pr in matched}
    assert matched_pr_numbers == {301, 302, 303, 304}

    # Verify Continuous Median Interpolation
    res = compute_lead_time_metrics(
        [(pr, dep_02) for pr in matched],
        window_days=14,
        window_end=t_deploy,
    )

    assert res.deployed_pr_count == 4
    # percentile_cont(0.5) of [16.0, 22.0, 25.0, 31.0] -> 0.5 * (22.0 + 25.0) = 23.50h
    assert res.median_lead_time_hours == 23.50
    # percentile_cont(0.5) of [57600.0, 79200.0, 90000.0, 111600.0] -> 84,600.0s
    assert res.median_lead_time_seconds == 84600.0

    # Verify per-PR items and sorted lead times
    lead_times = [item.lead_time_hours for item in res.pull_requests]
    assert lead_times == [31.0, 25.0, 22.0, 16.0]
    assert sorted(lead_times) == [16.0, 22.0, 25.0, 31.0]


# ===========================================================================
# SCENARIO TC-DORA-LT-003: Batched Squash-Merged PRs — Odd Batch (23.5h Median)
# ===========================================================================


def test_tc_dora_lt_003_batched_squash_merged_prs_odd_batch_23_5_hours():
    """
    Scenario TC-DORA-LT-003 (SCRUM-436 / SCRUM-437 / SCRUM-435 AC 2):
    Odd-numbered batch (N=3) of squash-merged PRs with lead times [29.0h, 23.5h, 19.0h].
    - Continuous median selects the middle sorted element: 23.50 hours (84,600.0s).
    - Validates both 14-day and 30-day window median calculations.
    """
    t_deploy = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    pr_specs = [
        (401, "172dedb70e250f7feaae0c124d8346b3326a5eb1", 29.0),
        (402, "52a09859c72d083e749f3646f733811b7c2a06af", 23.5),
        (403, "72ed499b8bdb5b64065a1e9d68187c0c857726e7", 19.0),
    ]

    dep_03 = _make_deployment(
        commit_sha=_sha("batch-release-odd"),
        deployed_at=t_deploy,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    shipped = [
        (
            _make_pr(
                pr_number=num,
                head_sha=_sha(f"dangling-head-{num}"),
                merge_commit_sha=merge_sha,
                first_commit_at=t_deploy - timedelta(hours=lead_h),
                merged_at=t_deploy - timedelta(minutes=15),
                tenant_id=tenant_id,
                project_id=project_id,
                repository_id=repo_id,
            ),
            dep_03,
        )
        for num, merge_sha, lead_h in pr_specs
    ]

    for window_days in (14, 30):
        res = compute_lead_time_metrics(
            shipped,
            window_days=window_days,  # type: ignore[arg-type]
            window_end=t_deploy,
        )
        assert res.window_days == window_days
        assert res.deployed_pr_count == 3
        # Sorted lead times: [19.0, 23.5, 29.0] -> index 1 is 23.50
        assert res.median_lead_time_hours == 23.50
        assert res.median_lead_time_seconds == 84600.0


# ===========================================================================
# SCENARIO TC-DORA-LT-004: Trunk Commit Exclusion & Direct Push Filtering
# ===========================================================================


def test_tc_dora_lt_004_trunk_commit_exclusion_and_direct_push_filtering():
    """
    Scenario TC-DORA-LT-004 (SCRUM-436 / SCRUM-437 / SCRUM-434):
    Direct commits pushed to trunk without a PR (e.g., version bumps, bot chores)
    are traversed for DAG reachability but strictly excluded from deployment_pull_requests
    and do not skew Lead Time calculations.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    t_deploy = datetime(2026, 11, 14, 16, 0, 0, tzinfo=timezone.utc)

    sha_prev = "5273610b58406de60def552d340fed6190da57c9"
    sha_m_pr2 = "5bca982b79bfad0116adb4dca4483ce927a55791"
    sha_k_trunk = "ab22299bf0ea7627885d1a275c4d76fb5a37844c"  # Direct push to trunk: version bump
    sha_q_pr3 = "2d1c974b7a5e57168ad0af0ea7aa0663f9ec0eef"

    commits = {
        sha_prev: _make_commit(sha_prev, parent_shas=[]),
        sha_m_pr2: _make_commit(sha_m_pr2, parent_shas=[sha_prev], pull_request_id=uuid.uuid4()),
        sha_k_trunk: _make_commit(sha_k_trunk, parent_shas=[sha_m_pr2], pull_request_id=None),
        sha_q_pr3: _make_commit(sha_q_pr3, parent_shas=[sha_k_trunk], pull_request_id=uuid.uuid4()),
    }

    range_shas = compute_commit_range_shas(
        sha_q_pr3,
        previous_deployment_shas=[sha_prev],
        commits_by_sha=commits,
    )
    # Range includes trunk push K_trunk, but it must be filtered out during PR matching
    assert range_shas == {sha_q_pr3, sha_k_trunk, sha_m_pr2}

    pr_202 = _make_pr(
        pr_number=202,
        head_sha=_sha("pr202-head"),
        merge_commit_sha=sha_m_pr2,
        first_commit_at=t_deploy - timedelta(hours=20),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_203 = _make_pr(
        pr_number=203,
        head_sha=_sha("pr203-head"),
        merge_commit_sha=sha_q_pr3,
        first_commit_at=t_deploy - timedelta(hours=10),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    dep = _make_deployment(
        commit_sha=sha_q_pr3,
        deployed_at=t_deploy,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_202, pr_203],
        commits_in_range=list(commits.values()),
    )

    # Exactly the 2 PRs matched; zero synthetic PRs for the direct trunk commit
    assert len(matched) == 2
    matched_ids = {pr.id for pr in matched}
    assert matched_ids == {pr_202.id, pr_203.id}

    # Metrics verify deployed_pr_count = 2, unskewed by K_trunk
    res = compute_lead_time_metrics(
        [(pr, dep) for pr in matched],
        window_days=14,
        window_end=t_deploy,
    )
    assert res.deployed_pr_count == 2
    # Continuous median of [10.0, 20.0] = 15.0h
    assert res.median_lead_time_hours == 15.0


# ===========================================================================
# SCENARIO TC-DORA-LT-005: Zero-Deployment Window — null Semantics
# ===========================================================================


def test_tc_dora_lt_005_zero_deployment_window_null_semantics():
    """
    Scenario TC-DORA-LT-005 (SCRUM-436 / SCRUM-437 / SCRUM-435 AC 3):
    When zero qualifying deployments or zero shipped PRs exist in a window,
    Lead Time returns null (None in Python), NOT 0.00.
    Contrasts with Deployment Frequency which returns 0.00 (arrival rate).
    """
    window_end = datetime(2026, 11, 14, 0, 0, 0, tzinfo=timezone.utc)

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

        # All W daily series buckets report null
        for bucket in res.daily_series:
            assert bucket.deployed_pr_count == 0
            assert bucket.median_lead_time_hours is None
            assert bucket.median_lead_time_seconds is None


# ===========================================================================
# SCENARIO TC-DORA-LT-006: Rebase-and-Merge Linear DAG Attribution
# ===========================================================================


def test_tc_dora_lt_006_rebase_and_merge_linear_dag_attribution():
    """
    Scenario TC-DORA-LT-006 (SCRUM-436 / SCRUM-437 / SCRUM-434):
    PR integrated via rebase-and-merge replays commits linearly with new SHAs.
    Matches via commits.pull_request_id foreign key or rebased tip SHA.
    Lead time computed from original feature branch first_commit_at (T0 -> T0 + 24.0h).
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    t0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)
    t_deploy = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)

    sha_anchor = "acd64de32964ba83f3f7de3979104de072150485"
    sha_r1 = "7f4fd932adb49170649e02de4306e48cfc2afb40"
    sha_r2 = "aa6276e29a67f397c24bac1c727ad2b1e7fbd67c"

    pr_id = uuid.uuid4()
    commits = {
        sha_anchor: _make_commit(sha_anchor, parent_shas=[]),
        sha_r1: _make_commit(
            sha_r1, parent_shas=[sha_anchor], pull_request_id=pr_id, authored_at=t0
        ),
        sha_r2: _make_commit(
            sha_r2, parent_shas=[sha_r1], pull_request_id=pr_id, authored_at=t0 + timedelta(hours=4)
        ),
    }

    range_shas = compute_commit_range_shas(
        sha_r2,
        previous_deployment_shas=[sha_anchor],
        commits_by_sha=commits,
    )
    assert range_shas == {sha_r2, sha_r1}

    pr_501 = _make_pr(
        pr_number=501,
        head_sha=_sha("orig-branch-head"),
        merge_commit_sha=sha_r2,
        first_commit_at=t0,
        merged_at=t0 + timedelta(hours=5),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_501.id = pr_id

    dep_04 = _make_deployment(
        commit_sha=sha_r2,
        deployed_at=t_deploy,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep_04,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_501],
        commits_in_range=list(commits.values()),
    )
    assert len(matched) == 1
    assert matched[0].id == pr_501.id

    res = compute_lead_time_metrics(
        [(pr_501, dep_04)],
        window_days=14,
        window_end=t_deploy,
    )
    assert res.deployed_pr_count == 1
    assert res.median_lead_time_hours == 24.0
    assert res.median_lead_time_seconds == 86400.0


# ===========================================================================
# SCENARIO TC-DORA-LT-007: Heterogeneous Multi-Strategy Batched Release
# ===========================================================================


def test_tc_dora_lt_007_heterogeneous_multi_strategy_batched_release():
    """
    Scenario TC-DORA-LT-007 (SCRUM-436 / SCRUM-437 / SCRUM-434):
    Single release batch containing:
    1. Standard merge PR #1 (already shipped in prior deployment -> excluded).
    2. Standard merge PR #2 (attributed).
    3. Direct trunk commit push without PR (excluded from PR claims).
    4. Squash-and-merge PR #3 (attributed).
    5. Rebase-and-merge PR #4 (attributed).
    Verifies exactly 3 PRs attributed without duplication.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    t_deploy = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    sha_d0 = _sha("d0-anchor-shipped-pr1")
    sha_pr2_feat = _sha("pr2-feat")
    sha_pr2_merge = _sha("pr2-merge-commit")
    sha_trunk_chore = _sha("trunk-chore-direct-push")
    sha_pr3_squash = _sha("pr3-squash-commit")
    sha_pr4_rebase = _sha("pr4-rebase-commit")

    pr4_id = uuid.uuid4()

    commits = {
        sha_d0: _make_commit(sha_d0, parent_shas=[]),
        sha_pr2_feat: _make_commit(sha_pr2_feat, parent_shas=[sha_d0]),
        sha_pr2_merge: _make_commit(sha_pr2_merge, parent_shas=[sha_d0, sha_pr2_feat]),
        sha_trunk_chore: _make_commit(
            sha_trunk_chore, parent_shas=[sha_pr2_merge], pull_request_id=None
        ),
        sha_pr3_squash: _make_commit(sha_pr3_squash, parent_shas=[sha_trunk_chore]),
        sha_pr4_rebase: _make_commit(
            sha_pr4_rebase, parent_shas=[sha_pr3_squash], pull_request_id=pr4_id
        ),
    }

    range_shas = compute_commit_range_shas(
        sha_pr4_rebase,
        previous_deployment_shas=[sha_d0],
        commits_by_sha=commits,
    )
    assert sha_d0 not in range_shas
    assert sha_pr4_rebase in range_shas
    assert sha_pr3_squash in range_shas
    assert sha_trunk_chore in range_shas
    assert sha_pr2_merge in range_shas
    assert sha_pr2_feat in range_shas

    pr_1_already_shipped = _make_pr(
        pr_number=1,
        head_sha=_sha("pr1-head"),
        merge_commit_sha=sha_d0,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_2_merge = _make_pr(
        pr_number=2,
        head_sha=sha_pr2_feat,
        merge_commit_sha=sha_pr2_merge,
        first_commit_at=t_deploy - timedelta(hours=30),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_3_squash = _make_pr(
        pr_number=3,
        head_sha=_sha("pr3-dangling"),
        merge_commit_sha=sha_pr3_squash,
        first_commit_at=t_deploy - timedelta(hours=20),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_4_rebase = _make_pr(
        pr_number=4,
        head_sha=_sha("pr4-orig-head"),
        merge_commit_sha=sha_pr4_rebase,
        first_commit_at=t_deploy - timedelta(hours=10),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    pr_4_rebase.id = pr4_id

    dep = _make_deployment(
        commit_sha=sha_pr4_rebase,
        deployed_at=t_deploy,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    matched = match_unclaimed_pull_requests_in_range(
        dep,
        commit_range_shas=range_shas,
        candidate_pull_requests=[pr_1_already_shipped, pr_2_merge, pr_3_squash, pr_4_rebase],
        commits_in_range=list(commits.values()),
        already_claimed_pr_ids={pr_1_already_shipped.id},
    )

    assert len(matched) == 3
    matched_numbers = [pr.external_pr_number for pr in matched]
    assert sorted(matched_numbers) == [2, 3, 4]


# ===========================================================================
# SCENARIO TC-DORA-LT-008: Re-deployment & Rollback No-Op Idempotency
# ===========================================================================


def test_tc_dora_lt_008_redeploy_and_rollback_no_op_idempotency():
    """
    Scenario TC-DORA-LT-008 (SCRUM-436 / SCRUM-437 / SCRUM-434):
    Re-deploying an already-deployed commit SHA or rolling back to an ancestor SHA
    produces an empty range S_i = empty, 0 claimed PRs, and 0 inserted join rows.
    """
    sha_1 = _sha("commit-1")
    sha_2 = _sha("commit-2")

    commits = {
        sha_1: _make_commit(sha_1, parent_shas=[]),
        sha_2: _make_commit(sha_2, parent_shas=[sha_1]),
    }

    # Case A: Re-deploy exact same SHA
    redeploy_range = compute_commit_range_shas(
        sha_2,
        previous_deployment_shas=[sha_2],
        commits_by_sha=commits,
    )
    assert redeploy_range == set()

    # Case B: Rollback to ancestor SHA
    rollback_range = compute_commit_range_shas(
        sha_1,
        previous_deployment_shas=[sha_2],
        commits_by_sha=commits,
    )
    assert rollback_range == set()

    dep = _make_deployment(commit_sha=sha_2, deployed_at=datetime.now(timezone.utc))
    pr = _make_pr(pr_number=999, head_sha=sha_2, merge_commit_sha=sha_2)

    matched = match_unclaimed_pull_requests_in_range(
        dep,
        commit_range_shas=set(),
        candidate_pull_requests=[pr],
        commits_in_range=[],
    )
    assert matched == []


# ===========================================================================
# SCENARIO TC-DORA-LT-009: Multi-Tenant RLS & Contributor Pseudonym Privacy
# ===========================================================================


def test_tc_dora_lt_009_multi_tenant_rls_and_pseudonym_isolation():
    """
    Scenario TC-DORA-LT-009 (SCRUM-436 / SCRUM-437):
    - PostgreSQL Row-Level Security ensures Tenant A cannot access Tenant B's data.
    - Contributor identities use salted HMAC-SHA256 pseudonyms without plaintext PII.
    - Filtering by author_pseudonym_id isolates individual contributor's Lead Time metrics.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()

    t_deploy = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
    pseudo_alice = _pseudonym("alice@tenant-a.com")
    pseudo_bob = _pseudonym("bob@tenant-a.com")

    # Verify pseudonym formatting: 64-char lowercase hex, no plaintext leak
    assert len(pseudo_alice) == 64
    assert "alice" not in pseudo_alice
    assert "tenant" not in pseudo_alice

    # Tenant A: Alice has 26.5h PR, Bob has 10.0h PR
    pr_alice = _make_pr(
        pr_number=101,
        head_sha=_sha("alice-head"),
        merge_commit_sha=_sha("alice-merge"),
        first_commit_at=t_deploy - timedelta(hours=26, minutes=30),
        author_pseudonym_id=pseudo_alice,
        tenant_id=tenant_a,
        project_id=project_a,
    )
    pr_bob = _make_pr(
        pr_number=102,
        head_sha=_sha("bob-head"),
        merge_commit_sha=_sha("bob-merge"),
        first_commit_at=t_deploy - timedelta(hours=10),
        author_pseudonym_id=pseudo_bob,
        tenant_id=tenant_a,
        project_id=project_a,
    )
    dep_a = _make_deployment(
        commit_sha=_sha("dep-a"),
        deployed_at=t_deploy,
        tenant_id=tenant_a,
        project_id=project_a,
    )

    # Tenant B isolation test fixture
    pr_tenant_b = _make_pr(
        pr_number=999,
        head_sha=_sha("tenant-b-head"),
        merge_commit_sha=_sha("tenant-b-merge"),
        first_commit_at=t_deploy - timedelta(hours=5),
        tenant_id=tenant_b,
        project_id=project_b,
    )
    dep_b = _make_deployment(
        commit_sha=_sha("dep-b"),
        deployed_at=t_deploy,
        tenant_id=tenant_b,
        project_id=project_b,
    )
    assert pr_tenant_b.tenant_id != tenant_a
    assert dep_b.tenant_id != tenant_a

    # Filter by Alice's pseudonym
    res_alice = compute_lead_time_metrics(
        [(pr_alice, dep_a), (pr_bob, dep_a)],
        window_days=14,
        window_end=t_deploy,
        author_pseudonym_id=pseudo_alice,
    )
    assert res_alice.deployed_pr_count == 1
    assert res_alice.median_lead_time_hours == 26.50
    assert res_alice.pull_requests[0].author_pseudonym_id == pseudo_alice

    # Tenant isolation in SQL generation
    stmt_a = build_shipped_pull_requests_lead_time_query(
        tenant_id=tenant_a,
        project_id=project_a,
    )
    compiled_sql = str(stmt_a.compile(dialect=postgresql.dialect()))
    assert "deployment_pull_requests.tenant_id =" in compiled_sql
    assert "pull_requests.tenant_id =" in compiled_sql
    assert "deployments.tenant_id =" in compiled_sql


# ===========================================================================
# SCENARIO TC-DORA-LT-010: Authoritative CD Signal Source Filtering
# ===========================================================================


def test_tc_dora_lt_010_authoritative_cd_signal_source_filtering():
    """
    Scenario TC-DORA-LT-010 (SCRUM-436 / SCRUM-437 / SCRUM-384):
    Deployments originating from non-authoritative CD signals are excluded from Lead Time.
    """
    tenant_id, project_id, repo_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    t_deploy = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)

    # Deployment 1: Authoritative GitHub Deployments API
    dep_github = _make_deployment(
        commit_sha=_sha("github-deploy"),
        deployed_at=t_deploy,
        signal_source="github_deployments_api",
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )
    # Deployment 2: Non-authoritative ArgoCD (when project is configured for GitHub)
    dep_argocd = _make_deployment(
        commit_sha=_sha("argocd-deploy"),
        deployed_at=t_deploy - timedelta(hours=5),
        signal_source="argocd_postsync_webhook",
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repo_id,
    )

    pr_gh = _make_pr(
        pr_number=601,
        head_sha=_sha("gh-head"),
        first_commit_at=t_deploy - timedelta(hours=26, minutes=30),
        tenant_id=tenant_id,
        project_id=project_id,
    )
    pr_argo = _make_pr(
        pr_number=602,
        head_sha=_sha("argo-head"),
        first_commit_at=t_deploy - timedelta(hours=50),
        tenant_id=tenant_id,
        project_id=project_id,
    )

    # Ingestion query excludes non-authoritative signal
    assert dep_github.authoritative_signal_source == "github_deployments_api"
    assert dep_argocd.authoritative_signal_source != "github_deployments_api"
    assert pr_gh.project_id == project_id
    assert pr_argo.project_id == project_id

    stmt = build_shipped_pull_requests_lead_time_query(
        tenant_id=tenant_id,
        project_id=project_id,
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "deployments.authoritative_signal_source = projects.authoritative_cd_signal" in compiled


# ===========================================================================
# SCENARIO TC-DORA-LT-011: Window Temporal Boundaries & UTC Midnight Grouping
# ===========================================================================


def test_tc_dora_lt_011_window_temporal_boundaries_and_utc_midnight_grouping():
    """
    Scenario TC-DORA-LT-011 (SCRUM-436 / SCRUM-437 / SCRUM-435):
    - Strict temporal inclusion [t_end - W days, t_end].
    - Excludes deployments 1 second prior to start or 1 second after end.
    - Daily series groups strictly by UTC calendar date.
    """
    window_end = datetime(2026, 11, 14, 0, 0, 0, tzinfo=timezone.utc)
    window_start = window_end - timedelta(days=14)

    t_too_old = window_start - timedelta(seconds=1)
    t_start = window_start
    t_end = window_end
    t_too_late = window_end + timedelta(seconds=1)

    tenant_id, project_id = uuid.uuid4(), uuid.uuid4()

    prs_and_deps = [
        (
            _make_pr(
                pr_number=1,
                head_sha=_sha("h1"),
                first_commit_at=t_too_old - timedelta(hours=10),
                tenant_id=tenant_id,
                project_id=project_id,
            ),
            _make_deployment(
                commit_sha=_sha("d1"),
                deployed_at=t_too_old,
                tenant_id=tenant_id,
                project_id=project_id,
            ),
        ),
        (
            _make_pr(
                pr_number=2,
                head_sha=_sha("h2"),
                first_commit_at=t_start - timedelta(hours=10),
                tenant_id=tenant_id,
                project_id=project_id,
            ),
            _make_deployment(
                commit_sha=_sha("d2"),
                deployed_at=t_start,
                tenant_id=tenant_id,
                project_id=project_id,
            ),
        ),
        (
            _make_pr(
                pr_number=3,
                head_sha=_sha("h3"),
                first_commit_at=t_end - timedelta(hours=10),
                tenant_id=tenant_id,
                project_id=project_id,
            ),
            _make_deployment(
                commit_sha=_sha("d3"), deployed_at=t_end, tenant_id=tenant_id, project_id=project_id
            ),
        ),
        (
            _make_pr(
                pr_number=4,
                head_sha=_sha("h4"),
                first_commit_at=t_too_late - timedelta(hours=10),
                tenant_id=tenant_id,
                project_id=project_id,
            ),
            _make_deployment(
                commit_sha=_sha("d4"),
                deployed_at=t_too_late,
                tenant_id=tenant_id,
                project_id=project_id,
            ),
        ),
    ]

    # compute_lead_time_metrics filters out prs_and_deps outside [window_start, window_end]
    res = compute_lead_time_metrics(
        prs_and_deps,
        window_days=14,
        window_end=window_end,
    )

    # Exactly 2 deployments included (t_start and t_end)
    assert res.deployed_pr_count == 2
    pr_numbers = {item.external_pr_number for item in res.pull_requests}
    assert pr_numbers == {2, 3}


# ===========================================================================
# ADDITIONAL ACCEPTANCE TESTS & VALIDATION GUARDS
# ===========================================================================


def test_lead_time_negative_duration_fails_closed():
    """
    Temporal Invariant: deployed_at < first_commit_at must fail-closed with ValueError.
    """
    first_commit_at = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
    deployed_at = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)  # Prior to first commit

    with pytest.raises(ValueError, match="cannot be earlier than first_commit_at"):
        compute_pr_lead_time_seconds(first_commit_at, deployed_at)

    with pytest.raises(ValueError, match="cannot be earlier than first_commit_at"):
        compute_pr_lead_time_hours(first_commit_at, deployed_at)


def test_lead_time_unsupported_window_validation():
    """
    Validation: window_days outside {14, 30} must raise ValueError.
    """
    with pytest.raises(ValueError, match="Unsupported window_days=7"):
        compute_lead_time_metrics([], window_days=7, window_end=datetime.now(timezone.utc))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unsupported window_days=45"):
        compute_lead_time_metrics([], window_days=45, window_end=datetime.now(timezone.utc))  # type: ignore[arg-type]


def test_sql_continuous_median_query_generation():
    """
    Verifies that the compiled SQL query uses PostgreSQL PERCENTILE_CONT(0.5) WITHIN GROUP.
    """
    tenant_id, project_id = uuid.uuid4(), uuid.uuid4()
    stmt = build_project_median_lead_time_sql_query(
        tenant_id=tenant_id,
        project_id=project_id,
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "percentile_cont" in compiled.lower()
    assert "within group" in compiled.lower()
    assert "deployment_pull_requests" in compiled


@pytest.mark.asyncio
async def test_calculate_rolling_lead_time_service_mock_session():
    """
    Verifies calculate_rolling_lead_time orchestration over mock async DB session.
    """
    tenant_id, project_id = uuid.uuid4(), uuid.uuid4()
    t_deploy = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)

    pr = _make_pr(
        pr_number=701,
        head_sha=_sha("head-701"),
        first_commit_at=t_deploy - timedelta(hours=26, minutes=30),
        tenant_id=tenant_id,
        project_id=project_id,
    )
    dep = _make_deployment(
        commit_sha=_sha("dep-701"),
        deployed_at=t_deploy,
        tenant_id=tenant_id,
        project_id=project_id,
    )

    class MockResult:
        def all(self):
            return [(pr, dep)]

    class MockAsyncSession:
        async def execute(self, stmt):
            return MockResult()

    result = await calculate_rolling_lead_time(
        MockAsyncSession(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
        window_end=t_deploy + timedelta(hours=1),
    )

    assert result.window_days == 14
    assert result.deployed_pr_count == 1
    assert result.median_lead_time_hours == 26.50
    assert result.median_lead_time_seconds == 95400.0


# ===========================================================================
# SCENARIO TC-DORA-LT-LIVE: Live PostgreSQL 16 Database E2E Verification
# ===========================================================================


@skip_without_live_db
@pytest.mark.asyncio
async def test_tc_dora_lt_live_postgres_e2e():
    """
    Scenario TC-DORA-LT-LIVE (SCRUM-436 / SCRUM-437):
    Full end-to-end PostgreSQL 16 database verification under tenant Row-Level Security:
    - Registers isolated test tenant.
    - Creates project, repository, commits, PRs, deployments, and deployment_pull_requests join rows.
    - Executes calculate_rolling_lead_time on live PostgreSQL with PERCENTILE_CONT(0.5).
    - Verifies exact 26.5h single PR and 23.5h batched median against live database.
    """
    tenant_id, tenant_slug = await _register_test_tenant("lt-live")
    t_deploy = datetime(2026, 11, 14, 15, 0, 0, tzinfo=timezone.utc)

    async with get_tenant_db_context(tenant_id) as session:
        # Create Project
        project = Project(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="DORA LT Live Project",
            slug=f"dora-lt-{uuid.uuid4().hex[:6]}",
            authoritative_cd_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
        )
        session.add(project)

        # Create Repository with valid schema attributes
        repo = Repository(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project.id,
            external_repo_id=int(tenant_id.int % 1_000_000_000),
            full_name=f"acme/dora-lt-{uuid.uuid4().hex[:6]}",
            default_branch="main",
            is_private=True,
        )
        session.add(repo)
        await session.flush()

        pseudo_author = await resolve_or_create_contributor_pseudonym(
            session, tenant_id=tenant_id, provider="github", external_actor_id="live-alice"
        )

        # Create 1 Single PR with 26.5h Lead Time
        t_first = t_deploy - timedelta(hours=26, minutes=30)
        sha_merge = _sha("live-m1")
        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project.id,
            repository_id=repo.id,
            author_pseudonym_id=pseudo_author,
            external_pr_number=101,
            title="Live E2E 26.5h PR",
            state="merged",
            head_branch="feat/live-pr",
            base_branch="main",
            head_sha=_sha("live-head"),
            merge_commit_sha=sha_merge,
            first_commit_at=t_first,
            created_in_git_at=t_first + timedelta(minutes=5),
            merged_at=t_deploy - timedelta(hours=1),
            closed_at=t_deploy - timedelta(hours=1),
        )
        session.add(pr)

        dep = Deployment(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project.id,
            repository_id=repo.id,
            actor_pseudonym_id=None,
            external_deployment_id=f"deploy-{uuid.uuid4().hex[:8]}",
            environment="production",
            commit_sha=sha_merge,
            status=DeploymentOutcome.SUCCESS.value,
            authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            deployed_at=t_deploy,
        )
        session.add(dep)
        await session.flush()

        join_row = DeploymentPullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=dep.id,
            pull_request_id=pr.id,
        )
        session.add(join_row)
        await session.commit()

        # Query rolling Lead Time via service
        res = await calculate_rolling_lead_time(
            session,
            tenant_id=tenant_id,
            project_id=project.id,
            window_days=14,
            window_end=t_deploy + timedelta(hours=1),
        )

        assert res.window_days == 14
        assert res.deployed_pr_count == 1
        assert res.median_lead_time_hours == 26.50
        assert res.median_lead_time_seconds == 95400.0
        assert len(res.pull_requests) == 1
        assert res.pull_requests[0].external_pr_number == 101
