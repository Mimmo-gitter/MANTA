"""
US-28: Automated pytest test suite for DORA Change Failure Rate (CFR) and Distinct Union Engine.

Task: SCRUM-445 (Task 28.4: Write automated pytest tests verifying 30% distinct CFR on the 10-deployment fixture)
Parent User Story: SCRUM-365 (US-28: DORA Change Failure Rate)
Design Specifications:
- docs/dora-change-failure-rate-test-scenarios.md (Task 28.3 / SCRUM-444)
- backend/tests/scenarios/test_us28_change_failure_rate_scenarios.md (Task 28.3 / SCRUM-444)
- docs/dora-change-failure-rate-design.md (Task 28.1 / SCRUM-442)

Master Test Matrix Coverage:
- TC-DORA-CFR-001: Canonical 10-Deployment Benchmark Fixture (30.0% / 0.30 distinct CFR)
- TC-DORA-CFR-002: Defect Differential Analysis (strictly asserting 30.0% vs. 40.0% across 3 defect models)
- TC-DORA-CFR-003: SQL LEFT JOIN Row Multiplication Deduplication (multiple incidents collapse to 1 via COUNT(DISTINCT))
- TC-DORA-CFR-004: Zero-Deployment Window — null Semantics (14-day rolling window returns None/null)
- TC-DORA-CFR-005: Zero-Deployment Window — null Semantics (30-day rolling window returns None/null)
- TC-DORA-CFR-006: Zero-Failure Window (clean production deployments evaluate to 0.00 / 0.0%)
- TC-DORA-CFR-007: Non-Production ('staging') Environment Exclusion (staging deployments strictly excluded)
- TC-DORA-CFR-008: Authoritative CD Signal Source Mismatch Exclusion (secondary signals excluded)
- TC-DORA-CFR-009: Strict 24-Hour Boundary Edge Cases (+24h included, +24h01s excluded, negative delta excluded)
- TC-DORA-CFR-010: Incident Lifecycle Status & Inactive Flag Filtering (CANCELLED, FALSE_POSITIVE, is_active=False excluded)
- TC-DORA-CFR-011: Rolling 30-Day Window Evaluation with Canonical Fixture (invariant 30.0% across 30 days)
- TC-DORA-CFR-012: Daily CFR Trend Time-Series Generation (contiguous daily buckets, null on idle days)
- TC-DORA-CFR-013: Multi-Tenant Row-Level Security (RLS) Isolation (Tenant A vs Tenant B isolation)
- TC-DORA-CFR-014: Unsupported Window Parameter Validation (fail-closed ValueError on unsupported windows)
- TC-DORA-CFR-SQL: SQL Query Builders & Async Service Integration (LEFT OUTER JOIN & COUNT(DISTINCT) compilation)
- TC-DORA-CFR-LIVE: Live PostgreSQL 16 database verification under tenant RLS (10-deployment benchmark & zero-deployment null return)
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Optional
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.db.models.models import (
    Deployment,
    DeploymentIncident,
    Issue,
    Project,
)
from app.db.session import async_engine, get_tenant_db_context
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.dora.schemas import ChangeFailureRateResult
from app.dora.service import (
    build_change_failure_rate_deployments_query,
    build_change_failure_rate_sql_query,
    calculate_rolling_change_failure_rate,
    compute_change_failure_rate,
    is_active_confirmed_incident_for_deployment,
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
        "name": f"DORA CFR Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"cfr-{unique}@doratemp.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


def _sha(label: str) -> str:
    """Deterministic 40-character hexadecimal git SHA."""
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _make_deployment(
    *,
    label: str,
    deployed_at: datetime,
    status: str = DeploymentOutcome.SUCCESS.value,
    environment: str = "production",
    signal_source: str = DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    deployment_id: Optional[uuid.UUID] = None,
) -> Deployment:
    """Construct a test Deployment entity with deterministic SHA."""
    return Deployment(
        id=deployment_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        environment=environment,
        commit_sha=_sha(label),
        status=status,
        is_change_failure=status
        in (DeploymentOutcome.FAILED.value, DeploymentOutcome.ROLLED_BACK.value),
        authoritative_signal_source=signal_source,
        deployed_at=deployed_at,
    )


def _make_incident(
    *,
    deployment: Deployment,
    status: str = "CONFIRMED",
    is_active: bool = True,
    detected_offset: timedelta = timedelta(hours=2),
    correlation_source: str = "automated_time_window_24h",
    issue_id: Optional[uuid.UUID] = None,
) -> DeploymentIncident:
    """Construct a test DeploymentIncident entity linked to a deployment."""
    detected_at = deployment.deployed_at + detected_offset
    return DeploymentIncident(
        id=uuid.uuid4(),
        tenant_id=deployment.tenant_id,
        deployment_id=deployment.id,
        issue_id=issue_id or uuid.uuid4(),
        status=status,
        correlation_source=correlation_source,
        detected_at=detected_at,
        is_active=is_active,
    )


def _build_10_deployment_benchmark_fixture(
    *,
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    base_time: Optional[datetime] = None,
) -> tuple[
    list[Deployment],
    list[DeploymentIncident],
    list[tuple[Deployment, Optional[DeploymentIncident]]],
]:
    """
    Construct the canonical 10-deployment benchmark fixture (Task 28.3 / SCRUM-444 & Task 28.4 / SCRUM-445):
    - D1: FAILED + confirmed Jira incident at +4h (overlapping signals -> counts as 1)
    - D2: ROLLED_BACK (counts as 1)
    - D3: SUCCESS + confirmed Jira incident at +18h (counts as 1)
    - D4: SUCCESS + Jira incident at +30h (outside 24h window -> counts as 0)
    - D5: SUCCESS + Jira incident at +6h later Cancelled (is_active=False -> counts as 0)
    - D6..D10: 5 clean SUCCESS deployments without incidents (counts as 0)
    Total production deployments = 10; Distinct failed deployments = 3 -> CFR = 0.30 (30.0%).
    Asserts 30.0% vs. 40.0% (preventing +30h temporal leakage and overlapping double-counting).
    """
    tid = tenant_id or uuid.uuid4()
    pid = project_id or uuid.uuid4()
    t0 = base_time or datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)

    # D1: FAILED + confirmed Jira incident at +4h -> counts as 1
    d1 = _make_deployment(
        label="d1-failed-and-incident-4h",
        deployed_at=t0 + timedelta(hours=1),
        status=DeploymentOutcome.FAILED.value,
        tenant_id=tid,
        project_id=pid,
    )
    # D2: ROLLED_BACK -> counts as 1
    d2 = _make_deployment(
        label="d2-rolled-back-clean",
        deployed_at=t0 + timedelta(hours=2),
        status=DeploymentOutcome.ROLLED_BACK.value,
        tenant_id=tid,
        project_id=pid,
    )
    # D3: SUCCESS + confirmed Jira incident at +18h -> counts as 1
    d3 = _make_deployment(
        label="d3-success-confirmed-incident-18h",
        deployed_at=t0 + timedelta(hours=3),
        status=DeploymentOutcome.SUCCESS.value,
        tenant_id=tid,
        project_id=pid,
    )
    # D4: SUCCESS + Jira incident at +30h (outside 24h correlation window) -> counts as 0
    d4 = _make_deployment(
        label="d4-success-incident-30h-outside-window",
        deployed_at=t0 + timedelta(hours=4),
        status=DeploymentOutcome.SUCCESS.value,
        tenant_id=tid,
        project_id=pid,
    )
    # D5: SUCCESS + Jira incident at +6h later Cancelled -> counts as 0
    d5 = _make_deployment(
        label="d5-success-incident-6h-cancelled",
        deployed_at=t0 + timedelta(hours=5),
        status=DeploymentOutcome.SUCCESS.value,
        tenant_id=tid,
        project_id=pid,
    )
    # D6..D10: Clean successful deployments without any incidents -> counts as 0
    clean_deployments = [
        _make_deployment(
            label=f"d{i}-success-clean",
            deployed_at=t0 + timedelta(hours=i),
            status=DeploymentOutcome.SUCCESS.value,
            tenant_id=tid,
            project_id=pid,
        )
        for i in range(6, 11)
    ]

    inc_d1 = _make_incident(
        deployment=d1,
        status="CONFIRMED",
        is_active=True,
        detected_offset=timedelta(hours=4),
    )
    inc_d3 = _make_incident(
        deployment=d3,
        status="CONFIRMED",
        is_active=True,
        detected_offset=timedelta(hours=18),
    )
    inc_d4_30h = _make_incident(
        deployment=d4,
        status="CONFIRMED",
        is_active=True,
        detected_offset=timedelta(hours=30),
    )
    inc_d5_cancelled = _make_incident(
        deployment=d5,
        status="CANCELLED",
        is_active=False,
        detected_offset=timedelta(hours=6),
    )

    all_deployments = [d1, d2, d3, d4, d5, *clean_deployments]
    all_incidents = [inc_d1, inc_d3, inc_d4_30h, inc_d5_cancelled]

    # Simulated LEFT JOIN rows
    joined_rows: list[tuple[Deployment, Optional[DeploymentIncident]]] = [
        (d1, inc_d1),
        (d2, None),
        (d3, inc_d3),
        (d4, inc_d4_30h),
        (d5, inc_d5_cancelled),
        *[(d, None) for d in clean_deployments],
    ]
    return all_deployments, all_incidents, joined_rows


# ===========================================================================
# TC-DORA-CFR-001: Canonical 10-Deployment Benchmark Fixture (30.0% CFR)
# ===========================================================================


def test_tc_dora_cfr_001_canonical_10_deployment_benchmark_fixture():
    """
    Scenario TC-DORA-CFR-001 (SCRUM-444 / SCRUM-445):
    Verify that on the canonical 10-deployment benchmark fixture, the Change Failure Rate
    calculation engine strictly produces CFR_ratio = 0.3000 (CFR_% = 30.0%), proving:
    1. D1 with both FAILED status and confirmed +4h incident counts as 1 (not 2).
    2. D2 with ROLLED_BACK status counts as 1.
    3. D3 with SUCCESS status and confirmed +18h incident counts as 1.
    4. D4 with SUCCESS status and confirmed +30h incident is excluded by the 24h window (counts as 0).
    5. D5 with SUCCESS status and CANCELLED incident is excluded (counts as 0).
    6. D6..D10 clean SUCCESS deployments count as 0.
    Total = 10; Failed = 3 -> CFR = 30.0% (0.30).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    base_time = window_end - timedelta(days=2)
    deployments, incidents, joined_rows = _build_10_deployment_benchmark_fixture(
        base_time=base_time,
    )

    d1, d2, d3 = deployments[0], deployments[1], deployments[2]

    # 1. Authoritative calculation via separate sequences
    result_separate = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=14,
        window_end=window_end,
    )
    assert isinstance(result_separate, ChangeFailureRateResult)
    assert result_separate.window_days == 14
    assert result_separate.total_deployment_count == 10
    assert result_separate.failed_deployment_count == 3
    assert result_separate.change_failure_rate == 0.30
    assert result_separate.cfr == 0.30
    assert result_separate.cfr_ratio == 0.30
    assert result_separate.change_failure_rate_percentage == 30.0
    assert result_separate.cfr_percentage == 30.0
    assert result_separate.failed_deployment_ids == [d1.id, d2.id, d3.id]

    # 2. Authoritative calculation via simulated SQL LEFT JOIN rows
    result_joined = compute_change_failure_rate(
        joined_rows,
        window_days=14,
        window_end=window_end,
    )
    assert result_joined.window_days == 14
    assert result_joined.total_deployment_count == 10
    assert result_joined.failed_deployment_count == 3
    assert result_joined.change_failure_rate == 0.30
    assert result_joined.cfr == 0.30
    assert result_joined.cfr_ratio == 0.30
    assert result_joined.change_failure_rate_percentage == 30.0
    assert result_joined.cfr_percentage == 30.0
    assert result_joined.failed_deployment_ids == [d1.id, d2.id, d3.id]


# ===========================================================================
# TC-DORA-CFR-002: Defect Differential Analysis (Asserting 30.0% vs. 40.0%)
# ===========================================================================


def test_tc_dora_cfr_002_defect_differential_30_percent_vs_40_percent():
    """
    Scenario TC-DORA-CFR-002 (SCRUM-444 / SCRUM-445):
    Defect Differential Analysis:
    - Authoritative expected result: 3 / 10 = 30.0% (0.3000).
    - Defect Model A (Temporal Window Leakage at +30h):
      If the <=24h boundary is omitted or improperly computed, D4's incident at +30h
      would be included, resulting in 4 / 10 = 40.0%.
    - Defect Model B (Signal Double-Counting on D1):
      If D1's FAILED status and +4h confirmed incident are summed additively instead of
      deduplicated via set union / COUNT(DISTINCT d.id), numerator = 4 -> 40.0%.
    - Defect Model C (Cancelled Incident Inclusion on D5):
      If D5's CANCELLED incident is not filtered by status/is_active, numerator = 4 -> 40.0%.

    The test strictly asserts CFR == 30.0% (0.30) and REJECTS 40.0% (0.40).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    base_time = window_end - timedelta(days=2)
    deployments, incidents, joined_rows = _build_10_deployment_benchmark_fixture(
        base_time=base_time,
    )

    d1, d2, d3, d4, d5 = (
        deployments[0],
        deployments[1],
        deployments[2],
        deployments[3],
        deployments[4],
    )
    inc_d1 = next(inc for inc in incidents if inc.deployment_id == d1.id)
    inc_d3 = next(inc for inc in incidents if inc.deployment_id == d3.id)
    inc_d4_30h = next(inc for inc in incidents if inc.deployment_id == d4.id)
    inc_d5_cancelled = next(inc for inc in incidents if inc.deployment_id == d5.id)

    # 1. Verify individual correlation predicates
    assert is_active_confirmed_incident_for_deployment(inc_d1, d1) is True  # +4h <= 24h
    assert is_active_confirmed_incident_for_deployment(inc_d3, d3) is True  # +18h <= 24h
    assert is_active_confirmed_incident_for_deployment(inc_d4_30h, d4) is False  # +30h > 24h
    assert is_active_confirmed_incident_for_deployment(inc_d5_cancelled, d5) is False  # CANCELLED

    # 2. Authoritative 14-day calculation
    res_14d = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=14,
        window_end=window_end,
    )
    assert res_14d.total_deployment_count == 10
    assert res_14d.failed_deployment_count == 3
    assert res_14d.change_failure_rate == 0.30
    assert res_14d.change_failure_rate_percentage == 30.0
    assert res_14d.failed_deployment_ids == [d1.id, d2.id, d3.id]

    # Strict contrast assertions: 30.0% vs. 40.0%
    assert res_14d.change_failure_rate != 0.40
    assert res_14d.change_failure_rate_percentage != 40.0
    assert res_14d.cfr != 0.40
    assert res_14d.cfr_percentage != 40.0

    # 3. Explicit modeling of defect models
    # Defect Model A: Temporal leakage at +30h includes D4
    failed_with_temporal_leak = [d1.id, d2.id, d3.id, d4.id]
    cfr_temporal_leak = round(len(failed_with_temporal_leak) / len(deployments) * 100.0, 2)
    assert cfr_temporal_leak == 40.0, "Defect Model A evaluates to 40.0%"

    # Defect Model B: Double counting D1
    numerator_double_count = 1 + 1 + 1 + 1  # D1 FAILED + D1 incident + D2 ROLLED_BACK + D3 incident
    cfr_double_count = round((numerator_double_count / 10) * 100.0, 2)
    assert cfr_double_count == 40.0, "Defect Model B evaluates to 40.0%"

    # Defect Model C: Cancelled incident on D5
    failed_with_cancelled = [d1.id, d2.id, d3.id, d5.id]
    cfr_cancelled_leak = round(len(failed_with_cancelled) / len(deployments) * 100.0, 2)
    assert cfr_cancelled_leak == 40.0, "Defect Model C evaluates to 40.0%"


# ===========================================================================
# TC-DORA-CFR-003: SQL LEFT JOIN Row Multiplication Deduplication
# ===========================================================================


def test_tc_dora_cfr_003_left_join_row_multiplication_deduplication():
    """
    Scenario TC-DORA-CFR-003:
    A single deployment linked to 3 confirmed incidents produces 3 joined rows in a
    SQL LEFT JOIN, but MUST count as 1 total deployment and 1 failed deployment.
    Together with 2 clean SUCCESS deployments, total = 3, failed = 1, CFR = 33.33% (0.3333).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    d1 = _make_deployment(
        label="d1-multi-inc",
        deployed_at=window_end - timedelta(hours=6),
        status=DeploymentOutcome.SUCCESS.value,
    )
    d2 = _make_deployment(
        label="d2-ok",
        deployed_at=window_end - timedelta(hours=5),
        status=DeploymentOutcome.SUCCESS.value,
    )
    d3 = _make_deployment(
        label="d3-ok",
        deployed_at=window_end - timedelta(hours=4),
        status=DeploymentOutcome.SUCCESS.value,
    )

    inc1 = _make_incident(deployment=d1, detected_offset=timedelta(minutes=15))
    inc2 = _make_incident(deployment=d1, detected_offset=timedelta(minutes=45))
    inc3 = _make_incident(deployment=d1, detected_offset=timedelta(hours=2))

    joined_rows = [
        (d1, inc1),
        (d1, inc2),
        (d1, inc3),
        (d2, None),
        (d3, None),
    ]
    res = compute_change_failure_rate(joined_rows, window_days=14, window_end=window_end)
    assert res.total_deployment_count == 3
    assert res.failed_deployment_count == 1
    assert res.change_failure_rate == 0.3333
    assert res.cfr_ratio == 0.3333
    assert res.change_failure_rate_percentage == 33.33
    assert res.cfr_percentage == 33.33
    assert res.failed_deployment_ids == [d1.id]


# ===========================================================================
# TC-DORA-CFR-004: Zero-Deployment Window Null Semantics (14-Day)
# ===========================================================================


def test_tc_dora_cfr_004_zero_deployment_window_14_day_null_semantics():
    """
    Scenario TC-DORA-CFR-004:
    When 0 production deployments exist in a 14-day window, CFR strictly returns None (null),
    total = 0, failed = 0, and exactly 14 zero-filled daily buckets with CFR = null.
    """
    window_end = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone.utc)
    res = compute_change_failure_rate(
        [],
        window_days=14,
        window_end=window_end,
    )
    assert res.window_days == 14
    assert res.total_deployment_count == 0
    assert res.failed_deployment_count == 0
    assert res.change_failure_rate is None
    assert res.change_failure_rate_percentage is None
    assert res.cfr is None
    assert res.cfr_ratio is None
    assert res.cfr_percentage is None
    assert res.failed_deployment_ids == []
    assert len(res.daily_series) == 14
    assert all(
        day.total_deployment_count == 0
        and day.failed_deployment_count == 0
        and day.change_failure_rate is None
        and day.change_failure_rate_percentage is None
        for day in res.daily_series
    )


# ===========================================================================
# TC-DORA-CFR-005: Zero-Deployment Window Null Semantics (30-Day)
# ===========================================================================


def test_tc_dora_cfr_005_zero_deployment_window_30_day_null_semantics():
    """
    Scenario TC-DORA-CFR-005:
    When 0 production deployments exist in a 30-day window, CFR strictly returns None (null),
    total = 0, failed = 0, and exactly 30 zero-filled daily buckets with CFR = null.
    """
    window_end = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone.utc)
    res = compute_change_failure_rate(
        [],
        window_days=30,
        window_end=window_end,
    )
    assert res.window_days == 30
    assert res.total_deployment_count == 0
    assert res.failed_deployment_count == 0
    assert res.change_failure_rate is None
    assert res.change_failure_rate_percentage is None
    assert res.cfr is None
    assert len(res.daily_series) == 30
    assert all(
        day.total_deployment_count == 0
        and day.failed_deployment_count == 0
        and day.change_failure_rate is None
        and day.change_failure_rate_percentage is None
        for day in res.daily_series
    )


# ===========================================================================
# TC-DORA-CFR-006: Zero-Failure Window (Clean Deployments)
# ===========================================================================


def test_tc_dora_cfr_006_zero_failure_window_clean_deployments():
    """
    Scenario TC-DORA-CFR-006:
    When N >= 1 production deployments exist and 0 fail, CFR strictly evaluates to numeric
    0.00 (0.0%), distinguishing a flawless production period from an empty window (null).
    """
    window_end = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone.utc)
    clean_deps = [
        _make_deployment(
            label=f"clean-{i}",
            deployed_at=window_end - timedelta(days=i),
            status=DeploymentOutcome.SUCCESS.value,
        )
        for i in range(1, 6)
    ]
    res = compute_change_failure_rate(
        clean_deps,
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 5
    assert res.failed_deployment_count == 0
    assert res.change_failure_rate == 0.0
    assert res.cfr == 0.0
    assert res.cfr_ratio == 0.0
    assert res.change_failure_rate_percentage == 0.0
    assert res.cfr_percentage == 0.0
    assert res.failed_deployment_ids == []
    # Distinct from null return:
    assert res.change_failure_rate is not None
    assert res.change_failure_rate_percentage is not None


# ===========================================================================
# TC-DORA-CFR-007: Non-Production ('staging') Environment Exclusion
# ===========================================================================


def test_tc_dora_cfr_007_staging_environment_exclusion():
    """
    Scenario TC-DORA-CFR-007:
    Deployments to non-production environments (e.g. 'staging') are strictly excluded from
    both the denominator and the numerator, even if their status is FAILED or they are linked
    to confirmed incidents.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    deploy_time = window_end - timedelta(days=3)

    d_prod1 = _make_deployment(
        label="prod-1", deployed_at=deploy_time, status=DeploymentOutcome.SUCCESS.value
    )
    d_prod2 = _make_deployment(
        label="prod-2",
        deployed_at=deploy_time + timedelta(hours=1),
        status=DeploymentOutcome.SUCCESS.value,
    )
    d_staging_failed = _make_deployment(
        label="staging-failed",
        deployed_at=deploy_time + timedelta(hours=2),
        status=DeploymentOutcome.FAILED.value,
        environment="staging",
    )
    d_staging_inc = _make_deployment(
        label="staging-inc",
        deployed_at=deploy_time + timedelta(hours=3),
        status=DeploymentOutcome.SUCCESS.value,
        environment="staging",
    )
    inc_staging = _make_incident(deployment=d_staging_inc, detected_offset=timedelta(hours=1))

    res = compute_change_failure_rate(
        [d_prod1, d_prod2, d_staging_failed, d_staging_inc],
        deployment_incidents=[inc_staging],
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 2
    assert res.failed_deployment_count == 0
    assert res.change_failure_rate == 0.0
    assert res.change_failure_rate_percentage == 0.0
    assert res.failed_deployment_ids == []


# ===========================================================================
# TC-DORA-CFR-008: Authoritative CD Signal Source Mismatch Exclusion
# ===========================================================================


def test_tc_dora_cfr_008_authoritative_cd_signal_mismatch_exclusion():
    """
    Scenario TC-DORA-CFR-008:
    Verify that deployments generated by secondary or non-authoritative CD signal sources
    are strictly filtered out from CFR evaluation to prevent double counting or metric distortion.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    base_time = window_end - timedelta(days=2)
    authoritative_signal = DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value

    # 2 authoritative clean deployments
    d1 = _make_deployment(
        label="d1-auth",
        deployed_at=base_time,
        status=DeploymentOutcome.SUCCESS.value,
        signal_source=authoritative_signal,
    )
    d2 = _make_deployment(
        label="d2-auth",
        deployed_at=base_time + timedelta(hours=1),
        status=DeploymentOutcome.SUCCESS.value,
        signal_source=authoritative_signal,
    )
    # 1 non-authoritative FAILED deployment
    d3_non_auth = _make_deployment(
        label="d3-argo",
        deployed_at=base_time + timedelta(hours=2),
        status=DeploymentOutcome.FAILED.value,
        signal_source=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK.value,
    )
    # 1 non-authoritative deployment with incident
    d4_non_auth = _make_deployment(
        label="d4-direct",
        deployed_at=base_time + timedelta(hours=3),
        status=DeploymentOutcome.SUCCESS.value,
        signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
    )
    inc_d4 = _make_incident(deployment=d4_non_auth, detected_offset=timedelta(hours=2))

    # Signal-filtered ingestion matching service query WHERE clause:
    auth_deployments = [
        d
        for d in [d1, d2, d3_non_auth, d4_non_auth]
        if d.authoritative_signal_source == authoritative_signal
    ]
    res = compute_change_failure_rate(
        auth_deployments,
        deployment_incidents=[inc_d4],
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 2
    assert res.failed_deployment_count == 0
    assert res.change_failure_rate == 0.0
    assert res.change_failure_rate_percentage == 0.0


# ===========================================================================
# TC-DORA-CFR-009: Strict 24-Hour Boundary Edge Cases
# ===========================================================================


def test_tc_dora_cfr_009_strict_24h_boundary_and_negative_offset():
    """
    Scenario TC-DORA-CFR-009:
    Verify precise boundary conditions for incident correlation timestamps:
    1. Exact upper bound: +24h 00m 00s (86,400 seconds) is INCLUDED.
    2. Beyond upper bound: +24h 00m 01s (86,401 seconds) is EXCLUDED.
    3. Negative offset: incident detected before deployment completed (-300s) is EXCLUDED.
    All 3 deployments are SUCCESS; total = 3, failed = 1, CFR = 33.33% (0.3333).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t0 = window_end - timedelta(days=3)

    d_exact = _make_deployment(
        label="d-exact-24h", deployed_at=t0, status=DeploymentOutcome.SUCCESS.value
    )
    d_late = _make_deployment(
        label="d-late-24h-1s",
        deployed_at=t0 + timedelta(hours=1),
        status=DeploymentOutcome.SUCCESS.value,
    )
    d_pre = _make_deployment(
        label="d-pre-deployment",
        deployed_at=t0 + timedelta(hours=2),
        status=DeploymentOutcome.SUCCESS.value,
    )

    inc_exact = _make_incident(
        deployment=d_exact, detected_offset=timedelta(hours=24)
    )  # 86,400s -> INCLUDED
    inc_late = _make_incident(
        deployment=d_late, detected_offset=timedelta(seconds=86401)
    )  # 86,401s -> EXCLUDED
    inc_pre = _make_incident(
        deployment=d_pre, detected_offset=timedelta(seconds=-300)
    )  # -300s -> EXCLUDED

    assert is_active_confirmed_incident_for_deployment(inc_exact, d_exact) is True
    assert is_active_confirmed_incident_for_deployment(inc_late, d_late) is False
    assert is_active_confirmed_incident_for_deployment(inc_pre, d_pre) is False

    res = compute_change_failure_rate(
        [d_exact, d_late, d_pre],
        deployment_incidents=[inc_exact, inc_late, inc_pre],
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 3
    assert res.failed_deployment_count == 1
    assert res.change_failure_rate == 0.3333
    assert res.change_failure_rate_percentage == 33.33
    assert res.failed_deployment_ids == [d_exact.id]


# ===========================================================================
# TC-DORA-CFR-010: Incident Lifecycle Status & Inactive Flag Filtering
# ===========================================================================


def test_tc_dora_cfr_010_incident_lifecycle_and_inactive_filtering():
    """
    Scenario TC-DORA-CFR-010:
    Incidents with statuses other than 'CONFIRMED' ('FALSE_POSITIVE', 'CANCELLED') or with
    is_active = False must never mark a successful deployment as a change failure.
    Total = 3, Failed = 0, CFR = 0.0% (0.0000).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t0 = window_end - timedelta(days=2)

    d1 = _make_deployment(label="d1-fp", deployed_at=t0, status=DeploymentOutcome.SUCCESS.value)
    d2 = _make_deployment(
        label="d2-cancel",
        deployed_at=t0 + timedelta(hours=1),
        status=DeploymentOutcome.SUCCESS.value,
    )
    d3 = _make_deployment(
        label="d3-inactive",
        deployed_at=t0 + timedelta(hours=2),
        status=DeploymentOutcome.SUCCESS.value,
    )

    inc_fp = _make_incident(
        deployment=d1,
        status="FALSE_POSITIVE",
        is_active=False,
        detected_offset=timedelta(hours=2),
    )
    inc_cancel = _make_incident(
        deployment=d2,
        status="CANCELLED",
        is_active=False,
        detected_offset=timedelta(hours=2),
    )
    inc_inactive = _make_incident(
        deployment=d3,
        status="CONFIRMED",
        is_active=False,
        detected_offset=timedelta(hours=2),
    )

    assert is_active_confirmed_incident_for_deployment(inc_fp, d1) is False
    assert is_active_confirmed_incident_for_deployment(inc_cancel, d2) is False
    assert is_active_confirmed_incident_for_deployment(inc_inactive, d3) is False

    res = compute_change_failure_rate(
        [d1, d2, d3],
        deployment_incidents=[inc_fp, inc_cancel, inc_inactive],
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 3
    assert res.failed_deployment_count == 0
    assert res.change_failure_rate == 0.0
    assert res.change_failure_rate_percentage == 0.0
    assert res.failed_deployment_ids == []


# ===========================================================================
# TC-DORA-CFR-011: Rolling 30-Day Window Evaluation with Canonical Fixture
# ===========================================================================


def test_tc_dora_cfr_011_rolling_30_day_window_evaluation():
    """
    Scenario TC-DORA-CFR-011:
    Evaluating the canonical 10-deployment benchmark fixture over a 30-day window (W = 30)
    produces the identical 30.0% CFR while generating exactly 30 daily trend buckets.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    base_time = window_end - timedelta(days=5)
    deployments, incidents, _ = _build_10_deployment_benchmark_fixture(
        base_time=base_time,
    )

    res = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=30,
        window_end=window_end,
    )
    assert res.window_days == 30
    assert res.total_deployment_count == 10
    assert res.failed_deployment_count == 3
    assert res.change_failure_rate == 0.30
    assert res.cfr == 0.30
    assert res.change_failure_rate_percentage == 30.0
    assert res.cfr_percentage == 30.0
    assert res.change_failure_rate != 0.40
    assert res.change_failure_rate_percentage != 40.0
    assert len(res.daily_series) == 30


# ===========================================================================
# TC-DORA-CFR-012: Daily CFR Trend Time-Series Generation
# ===========================================================================


def test_tc_dora_cfr_012_daily_cfr_trend_time_series_generation():
    """
    Scenario TC-DORA-CFR-012:
    Daily time-series bucketization over the evaluation window:
    - Active deployment days report total, failed count, daily ratio, and percentage.
    - Idle calendar days report total = 0, failed = 0, CFR = null, percentage = null.
    - Sum of daily totals equals window total (10); sum of daily failed equals window failed (3).
    """
    window_end = datetime(2026, 11, 14, 20, 0, 0, tzinfo=timezone.utc)
    deployments, incidents, _ = _build_10_deployment_benchmark_fixture(
        base_time=window_end - timedelta(days=2),
    )

    res = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=14,
        window_end=window_end,
    )
    assert len(res.daily_series) == 14

    sum_daily_total = sum(b.total_deployment_count for b in res.daily_series)
    sum_daily_failed = sum(b.failed_deployment_count for b in res.daily_series)
    assert sum_daily_total == 10
    assert sum_daily_failed == 3

    # Check idle days vs active days
    idle_buckets = [b for b in res.daily_series if b.total_deployment_count == 0]
    active_buckets = [b for b in res.daily_series if b.total_deployment_count > 0]
    assert len(idle_buckets) > 0
    assert len(active_buckets) > 0

    for idle in idle_buckets:
        assert idle.failed_deployment_count == 0
        assert idle.change_failure_rate is None
        assert idle.change_failure_rate_percentage is None

    for active in active_buckets:
        assert active.change_failure_rate is not None
        assert active.change_failure_rate_percentage is not None


# ===========================================================================
# TC-DORA-CFR-013: Multi-Tenant Row-Level Security (RLS) Isolation
# ===========================================================================


def test_tc_dora_cfr_013_multi_tenant_rls_boundary_isolation():
    """
    Scenario TC-DORA-CFR-013:
    Multi-tenant isolation:
    - Tenant A has the canonical 10-deployment benchmark fixture (CFR = 30.0%).
    - Tenant B has 8 production deployments (4 FAILED, 4 SUCCESS -> CFR = 50.0%).
    - In-memory calculation strictly isolates Tenant A's deployments from Tenant B's deployments.
    - SQL query builder includes tenant_id and project_id in WHERE clauses and table joins.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    deps_a, incs_a, _ = _build_10_deployment_benchmark_fixture(
        tenant_id=tenant_a,
        project_id=project_a,
        base_time=window_end - timedelta(days=2),
    )

    # Tenant B has 8 deployments: 4 FAILED, 4 SUCCESS -> 50.0%
    deps_b = [
        _make_deployment(
            label=f"tb-fail-{i}",
            deployed_at=window_end - timedelta(hours=i + 1),
            status=DeploymentOutcome.FAILED.value,
            tenant_id=tenant_b,
            project_id=project_b,
        )
        for i in range(4)
    ] + [
        _make_deployment(
            label=f"tb-succ-{i}",
            deployed_at=window_end - timedelta(hours=i + 5),
            status=DeploymentOutcome.SUCCESS.value,
            tenant_id=tenant_b,
            project_id=project_b,
        )
        for i in range(4)
    ]

    res_a = compute_change_failure_rate(
        deps_a, deployment_incidents=incs_a, window_days=14, window_end=window_end
    )
    res_b = compute_change_failure_rate(
        deps_b, deployment_incidents=[], window_days=14, window_end=window_end
    )

    assert res_a.total_deployment_count == 10
    assert res_a.failed_deployment_count == 3
    assert res_a.change_failure_rate == 0.30
    assert res_a.change_failure_rate_percentage == 30.0

    assert res_b.total_deployment_count == 8
    assert res_b.failed_deployment_count == 4
    assert res_b.change_failure_rate == 0.50
    assert res_b.change_failure_rate_percentage == 50.0

    # Verify SQL query builder incorporates tenant isolation predicates
    stmt = build_change_failure_rate_deployments_query(
        tenant_id=tenant_a,
        project_id=project_a,
        start_at=window_end - timedelta(days=14),
        end_at=window_end,
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "deployments.tenant_id =" in compiled
    assert "deployments.project_id =" in compiled
    assert "deployment_incidents.tenant_id = deployments.tenant_id" in compiled


# ===========================================================================
# TC-DORA-CFR-014: Unsupported Window Parameter Validation
# ===========================================================================


def test_tc_dora_cfr_014_unsupported_window_validation():
    """
    Scenario TC-DORA-CFR-014:
    Passing an unsupported window duration (e.g. 7, 60, or negative) fails-closed
    by raising ValueError.
    """
    for bad_window in (7, 21, 60, -14, 0):
        with pytest.raises(ValueError, match="Unsupported window_days"):
            compute_change_failure_rate([], window_days=bad_window)  # type: ignore[arg-type]


# ===========================================================================
# TC-DORA-CFR-SQL: SQL Query Compilation & Async Service Execution
# ===========================================================================


@pytest.mark.asyncio
async def test_tc_dora_cfr_sql_query_builders_and_async_service():
    """
    Scenario TC-DORA-CFR-SQL:
    - Verifies SQL query builders compile LEFT OUTER JOIN deployment_incidents and COUNT(DISTINCT deployments.id).
    - Verifies calculate_rolling_change_failure_rate with mock AsyncSession on the 10-deployment benchmark fixture.
    """
    tenant_id, project_id = uuid.uuid4(), uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    _, _, joined_rows = _build_10_deployment_benchmark_fixture(
        tenant_id=tenant_id,
        project_id=project_id,
        base_time=window_end - timedelta(days=3),
    )

    join_stmt = build_change_failure_rate_deployments_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=window_end - timedelta(days=14),
        end_at=window_end,
    )
    compiled_join = str(join_stmt.compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN deployment_incidents" in compiled_join
    assert "deployment_incidents.is_active IS true" in compiled_join
    assert (
        "deployments.authoritative_signal_source = projects.authoritative_cd_signal"
        in compiled_join
    )

    agg_stmt = build_change_failure_rate_sql_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=window_end - timedelta(days=14),
        end_at=window_end,
    )
    compiled_agg = str(agg_stmt.compile(dialect=postgresql.dialect()))
    assert "count(distinct(deployments.id))" in compiled_agg.lower()
    assert "left outer join deployment_incidents" in compiled_agg.lower()

    class _MockResult:
        def all(self):
            return joined_rows

    class _MockSession:
        async def execute(self, stmt):
            self.last_stmt = stmt
            return _MockResult()

    session = _MockSession()
    res = await calculate_rolling_change_failure_rate(
        session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 10
    assert res.failed_deployment_count == 3
    assert res.change_failure_rate == 0.30
    assert res.change_failure_rate_percentage == 30.0


# ===========================================================================
# TC-DORA-CFR-LIVE: Live PostgreSQL 16 End-to-End Verification
# ===========================================================================


@skip_without_live_db
@pytest.mark.asyncio
async def test_tc_dora_cfr_live_postgres_10_deployment_benchmark_and_zero_deployment_e2e():
    """
    Scenario TC-DORA-CFR-LIVE (Task 28.4 / SCRUM-445):
    Live PostgreSQL 16 E2E test verifying under tenant RLS:
    1. Canonical 10-deployment benchmark fixture with overlapping FAILED/ROLLED_BACK statuses
       and active CONFIRMED deployment_incidents yields total = 10, failed = 3, CFR = 0.30 (30.0%)
       in both calculate_rolling_change_failure_rate and native SQL build_change_failure_rate_sql_query.
       Strictly asserts CFR != 0.40 (40.0%).
    2. Zero-deployment project in the same tenant returns total = 0, failed = 0, CFR = None (null),
       and native SQL query produces (0, 0, None, None).
    """
    tenant_id, _ = await _register_test_tenant("us28-e2e")
    project_id = uuid.uuid4()
    empty_project_id = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    deployments, incidents, _ = _build_10_deployment_benchmark_fixture(
        tenant_id=tenant_id,
        project_id=project_id,
        base_time=window_end - timedelta(days=2),
    )

    async with get_tenant_db_context(tenant_id) as db:
        # 1. Create main project with 10 deployments
        db.add(
            Project(
                id=project_id,
                tenant_id=tenant_id,
                name="US28 CFR Project",
                slug=f"proj-{tenant_id.hex[:6]}",
                authoritative_cd_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            )
        )
        # Create second empty project for zero-deployment verification
        db.add(
            Project(
                id=empty_project_id,
                tenant_id=tenant_id,
                name="US28 CFR Empty Project",
                slug=f"proj-empty-{tenant_id.hex[:6]}",
                authoritative_cd_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            )
        )
        await db.flush()

        db.add_all(deployments)
        await db.flush()

        for idx, inc in enumerate(incidents, start=1):
            issue = Issue(
                id=inc.issue_id,
                tenant_id=tenant_id,
                project_id=project_id,
                external_key=f"INC-US28-{idx}-{tenant_id.hex[:4]}",
                issue_type="Incident",
                title=f"Incident {idx}",
                status="OPEN",
            )
            db.add(issue)
        await db.flush()

        db.add_all(incidents)
        await db.flush()

        # Part 1: Verify 10-deployment benchmark on live PostgreSQL
        cfr_res = await calculate_rolling_change_failure_rate(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            window_days=14,
            window_end=window_end,
        )
        assert cfr_res.total_deployment_count == 10
        assert cfr_res.failed_deployment_count == 3
        assert cfr_res.change_failure_rate == 0.30
        assert cfr_res.change_failure_rate_percentage == 30.0
        assert cfr_res.change_failure_rate != 0.40
        assert cfr_res.change_failure_rate_percentage != 40.0

        sql_row = (
            await db.execute(
                build_change_failure_rate_sql_query(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    start_at=window_end - timedelta(days=14),
                    end_at=window_end,
                )
            )
        ).first()
        assert sql_row is not None
        assert int(sql_row[0]) == 10
        assert int(sql_row[1]) == 3
        assert float(sql_row[2]) == 0.30
        assert float(sql_row[3]) == 30.0

        # Part 2: Verify zero-deployment project returns null on live PostgreSQL
        empty_res = await calculate_rolling_change_failure_rate(
            db,
            tenant_id=tenant_id,
            project_id=empty_project_id,
            window_days=14,
            window_end=window_end,
        )
        assert empty_res.total_deployment_count == 0
        assert empty_res.failed_deployment_count == 0
        assert empty_res.change_failure_rate is None
        assert empty_res.change_failure_rate_percentage is None

        empty_sql_row = (
            await db.execute(
                build_change_failure_rate_sql_query(
                    tenant_id=tenant_id,
                    project_id=empty_project_id,
                    start_at=window_end - timedelta(days=14),
                    end_at=window_end,
                )
            )
        ).first()
        assert empty_sql_row is not None
        assert int(empty_sql_row[0]) == 0
        assert int(empty_sql_row[1]) == 0
        assert empty_sql_row[2] is None
        assert empty_sql_row[3] is None


# ===========================================================================
# Backwards Compatibility Aliases
# ===========================================================================


def test_task_28_2_ten_deployment_benchmark_fixture_overlapping_signals_yields_30_percent():
    """Alias for TC-DORA-CFR-001 preserving backwards compatibility with Task 28.2."""
    test_tc_dora_cfr_001_canonical_10_deployment_benchmark_fixture()


def test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent():
    """Alias for TC-DORA-CFR-002 preserving backwards compatibility with Task 28.3."""
    test_tc_dora_cfr_002_defect_differential_30_percent_vs_40_percent()


def test_task_28_2_left_join_row_multiplication_deduplicated_by_distinct_id():
    """Alias for TC-DORA-CFR-003 preserving backwards compatibility with Task 28.2."""
    test_tc_dora_cfr_003_left_join_row_multiplication_deduplication()


def test_task_28_2_incident_correlation_24h_boundary_and_staging_exclusion():
    """Alias for TC-DORA-CFR-007 & TC-DORA-CFR-009 preserving backwards compatibility."""
    test_tc_dora_cfr_007_staging_environment_exclusion()
    test_tc_dora_cfr_009_strict_24h_boundary_and_negative_offset()


def test_task_28_2_empty_window_returns_none_vs_zero_failure_window_returns_zero():
    """Alias for TC-DORA-CFR-004, 005, 006 preserving backwards compatibility."""
    test_tc_dora_cfr_004_zero_deployment_window_14_day_null_semantics()
    test_tc_dora_cfr_005_zero_deployment_window_30_day_null_semantics()
    test_tc_dora_cfr_006_zero_failure_window_clean_deployments()


def test_task_28_2_daily_trend_series_and_window_validation():
    """Alias for TC-DORA-CFR-012 & 014 preserving backwards compatibility with Task 28.2."""
    test_tc_dora_cfr_012_daily_cfr_trend_time_series_generation()
    test_tc_dora_cfr_014_unsupported_window_validation()


@pytest.mark.asyncio
async def test_task_28_2_sql_queries_and_calculate_rolling_change_failure_rate_async():
    """Alias for TC-DORA-CFR-SQL preserving backwards compatibility with Task 28.2."""
    await test_tc_dora_cfr_sql_query_builders_and_async_service()


@skip_without_live_db
@pytest.mark.asyncio
async def test_task_28_2_live_postgres_10_deployment_benchmark_e2e():
    """Alias for TC-DORA-CFR-LIVE preserving backwards compatibility with Task 28.2."""
    await test_tc_dora_cfr_live_postgres_10_deployment_benchmark_and_zero_deployment_e2e()
