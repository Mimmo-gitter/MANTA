"""
US-28 (Task 28.2): Automated Unit & Integration Tests for DORA Change Failure Rate (CFR) Calculator.

Covers:
- Task 28.2 Acceptance Criterion: Overlapping failure signals on the same deployment
  (e.g. d.status IN ('FAILED', 'ROLLED_BACK') AND active CONFIRMED deployment_incidents)
  increment the failed deployment count by 1 (COUNT(DISTINCT d.id)), yielding 30.0% (0.30)
  on the 10-deployment benchmark fixture.
- SQL LEFT JOIN row multiplication deduplication (multiple incidents on one deployment).
- Exclusion of CANCELLED / FALSE_POSITIVE / inactive (is_active=False) incidents and >24h incidents.
- Exclusion of non-production ('staging') deployments and outside-window deployments.
- Empty window (0 production deployments) returning None (null) vs 0-failure window returning 0.0.
- Daily CFR trend series and 14d / 30d rolling window calculations.
- Native PostgreSQL 16 SQL query compilation and live database E2E verification.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
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
from app.dora.schemas import ChangeFailureRateResult, DailyChangeFailureRateMetric
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
    Construct the canonical 10-deployment benchmark fixture (Task 28.3 / SCRUM-444):
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
    # D6..D10: 5 clean SUCCESS deployments without incidents -> count as 0
    clean_deployments = [
        _make_deployment(
            label=f"d{idx}-clean-success",
            deployed_at=t0 + timedelta(hours=idx),
            status=DeploymentOutcome.SUCCESS.value,
            tenant_id=tid,
            project_id=pid,
        )
        for idx in range(6, 11)
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


def test_task_28_2_ten_deployment_benchmark_fixture_overlapping_signals_yields_30_percent():
    """
    Task 28.2 Acceptance Criterion:
    Overlapping failure signals on the same deployment increment the failed deployment count
    by 1, yielding 30.0% (0.30) on the 10-deployment benchmark fixture.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    deployments, incidents, joined_rows = _build_10_deployment_benchmark_fixture(
        base_time=window_end - timedelta(days=2),
    )

    # 1. Verify via separate deployments + deployment_incidents sequences
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
    assert result_separate.failed_deployment_ids == [
        deployments[0].id,
        deployments[1].id,
        deployments[2].id,
    ]

    # 2. Verify via LEFT JOIN (Deployment, DeploymentIncident) rows
    result_joined = compute_change_failure_rate(
        joined_rows,
        window_days=30,
        window_end=window_end,
    )
    assert result_joined.window_days == 30
    assert result_joined.total_deployment_count == 10
    assert result_joined.failed_deployment_count == 3
    assert result_joined.change_failure_rate == 0.30
    assert result_joined.change_failure_rate_percentage == 30.0


def test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent():
    """
    Task 28.3 / SCRUM-444 Acceptance Criteria:
    Create 10-deployment test scenario:
    - D1: FAILED + confirmed Jira incident at +4h -> counts as 1 (distinct union, overlapping signals)
    - D2: ROLLED_BACK -> counts as 1 (terminal failure outcome)
    - D3: SUCCESS + confirmed Jira incident at +18h -> counts as 1 (incident <=24h)
    - D4: SUCCESS + Jira incident at +30h -> outside 24h window, counts as 0
    - D5: SUCCESS + Jira incident at +6h later Cancelled -> cancelled, counts as 0
    - D6..D10: SUCCESS -> clean, count as 0
    Total = 3 / 10 = 30.0% (0.30).

    Asserting 30% vs 40%:
    - Expected Authoritative Result: 3 / 10 = 30.0% (0.3000).
    - Contrast Defect A (Temporal Leakage): If D4's incident at +30h were improperly included,
      numerator would be 4, yielding 40.0% (4 / 10).
    - Contrast Defect B (Signal Double-Counting): If D1's FAILED status and +4h incident were
      double-counted, numerator would be 4, yielding 40.0% (4 / 10).
    - Contrast Defect C (Cancelled Incident Inclusion): If D5's cancelled incident were included,
      numerator would be 4, yielding 40.0% (4 / 10).
    The test strictly asserts CFR == 30.0% and CFR != 40.0%.
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

    # Verify individual incident correlation predicates
    assert is_active_confirmed_incident_for_deployment(inc_d1, d1) is True  # +4h <= 24h
    assert is_active_confirmed_incident_for_deployment(inc_d3, d3) is True  # +18h <= 24h
    assert is_active_confirmed_incident_for_deployment(inc_d4_30h, d4) is False  # +30h > 24h
    assert is_active_confirmed_incident_for_deployment(inc_d5_cancelled, d5) is False  # CANCELLED

    # 1. Authoritative 14-day window evaluation
    res_14d = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=14,
        window_end=window_end,
    )
    assert res_14d.total_deployment_count == 10
    assert res_14d.failed_deployment_count == 3
    assert res_14d.cfr == 0.30
    assert res_14d.cfr_ratio == 0.30
    assert res_14d.cfr_percentage == 30.0
    assert res_14d.failed_deployment_ids == [d1.id, d2.id, d3.id]

    # Strict assertion: 30.0% vs 40.0%
    assert res_14d.cfr_percentage != 40.0
    assert res_14d.cfr_ratio != 0.40

    # 2. Authoritative 30-day window evaluation
    res_30d = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=30,
        window_end=window_end,
    )
    assert res_30d.total_deployment_count == 10
    assert res_30d.failed_deployment_count == 3
    assert res_30d.cfr == 0.30
    assert res_30d.cfr_percentage == 30.0
    assert res_30d.cfr_percentage != 40.0

    # 3. Simulated LEFT JOIN row deduplication evaluation
    res_joined = compute_change_failure_rate(
        joined_rows,
        window_days=14,
        window_end=window_end,
    )
    assert res_joined.total_deployment_count == 10
    assert res_joined.failed_deployment_count == 3
    assert res_joined.cfr_percentage == 30.0
    assert res_joined.cfr_percentage != 40.0

    # 4. Explicit contrast with Naive / Buggy implementations:
    # Defect A: Naive calculation without the 24h window filter would include D4 (+30h)
    naive_failed_ids_temporal_leak = [d1.id, d2.id, d3.id, d4.id]
    naive_cfr_temporal_leak = round(
        len(naive_failed_ids_temporal_leak) / len(deployments) * 100.0, 2
    )
    assert naive_cfr_temporal_leak == 40.0, "Naive temporal leakage would evaluate to 40.0%"

    # Defect B: Naive double-counting of D1 (FAILED + incident counted separately)
    naive_numerator_double_count = (
        1 + 1 + 1 + 1
    )  # 1 (D1 status) + 1 (D1 incident) + 1 (D2 status) + 1 (D3 incident)
    # 2 + 1 + 1 = 4
    assert naive_numerator_double_count == 4
    assert (naive_numerator_double_count / 10) * 100.0 == 40.0, (
        "Double-counting would evaluate to 40.0%"
    )


def test_task_28_2_left_join_row_multiplication_deduplicated_by_distinct_id():
    """
    Task 28.2: A single deployment linked to 3 confirmed incidents in a SQL LEFT JOIN
    produces 3 joined rows but MUST count as 1 total deployment and 1 failed deployment.
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
    assert res.change_failure_rate_percentage == 33.33


def test_task_28_2_incident_correlation_24h_boundary_and_staging_exclusion():
    """
    Task 28.2 & Task 28.1:
    - Incident detected at exactly +24h (86,400s) is included; incident at +30h is excluded.
    - Non-production ('staging') deployments are excluded even if FAILED or incident-linked.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    deploy_time = window_end - timedelta(days=3)

    d_24h = _make_deployment(label="d-exact-24h", deployed_at=deploy_time)
    d_30h = _make_deployment(label="d-late-30h", deployed_at=deploy_time + timedelta(hours=1))
    d_staging_failed = _make_deployment(
        label="d-staging-failed",
        deployed_at=deploy_time + timedelta(hours=2),
        status=DeploymentOutcome.FAILED.value,
        environment="staging",
    )

    inc_24h = _make_incident(deployment=d_24h, detected_offset=timedelta(hours=24))
    inc_30h = _make_incident(deployment=d_30h, detected_offset=timedelta(hours=30))
    inc_before = _make_incident(deployment=d_30h, detected_offset=timedelta(minutes=-10))

    assert is_active_confirmed_incident_for_deployment(inc_24h, d_24h) is True
    assert is_active_confirmed_incident_for_deployment(inc_30h, d_30h) is False
    assert is_active_confirmed_incident_for_deployment(inc_before, d_30h) is False

    res = compute_change_failure_rate(
        [d_24h, d_30h, d_staging_failed],
        deployment_incidents=[inc_24h, inc_30h, inc_before],
        window_days=14,
        window_end=window_end,
    )
    assert res.total_deployment_count == 2
    assert res.failed_deployment_count == 1
    assert res.change_failure_rate == 0.5
    assert res.change_failure_rate_percentage == 50.0


def test_task_28_2_empty_window_returns_none_vs_zero_failure_window_returns_zero():
    """
    Task 28.1 & Task 28.2:
    - When 0 production deployments exist in the window, CFR returns None (null).
    - When >=1 production deployments exist and 0 failed, CFR returns 0.0 (0.0%).
    """
    window_end = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone.utc)

    for window_days in (14, 30):
        empty_res = compute_change_failure_rate(
            [],
            window_days=window_days,  # type: ignore[arg-type]
            window_end=window_end,
        )
        assert empty_res.total_deployment_count == 0
        assert empty_res.failed_deployment_count == 0
        assert empty_res.change_failure_rate is None
        assert empty_res.change_failure_rate_percentage is None
        assert len(empty_res.daily_series) == window_days
        assert all(
            day.total_deployment_count == 0
            and day.failed_deployment_count == 0
            and day.change_failure_rate is None
            and day.change_failure_rate_percentage is None
            for day in empty_res.daily_series
        )

    clean_deps = [
        _make_deployment(
            label=f"clean-{i}",
            deployed_at=window_end - timedelta(days=i),
            status=DeploymentOutcome.SUCCESS.value,
        )
        for i in range(1, 6)
    ]
    zero_fail_res = compute_change_failure_rate(
        clean_deps,
        window_days=14,
        window_end=window_end,
    )
    assert zero_fail_res.total_deployment_count == 5
    assert zero_fail_res.failed_deployment_count == 0
    assert zero_fail_res.change_failure_rate == 0.0
    assert zero_fail_res.change_failure_rate_percentage == 0.0


def test_task_28_2_daily_trend_series_and_window_validation():
    """
    Task 28.2:
    - Daily CFR trend series computes per-day ratio/percentage on active days and None on idle days.
    - Unsupported window_days raises ValueError.
    """
    window_end = datetime(2026, 11, 14, 20, 0, 0, tzinfo=timezone.utc)
    day_a = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
    day_b = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)

    d_a1 = _make_deployment(label="da1", deployed_at=day_a, status=DeploymentOutcome.FAILED.value)
    d_a2 = _make_deployment(
        label="da2", deployed_at=day_a + timedelta(hours=1), status=DeploymentOutcome.SUCCESS.value
    )
    d_b1 = _make_deployment(label="db1", deployed_at=day_b, status=DeploymentOutcome.SUCCESS.value)

    res = compute_change_failure_rate(
        [d_a1, d_a2, d_b1],
        window_days=14,
        window_end=window_end,
    )
    by_date: dict[date, DailyChangeFailureRateMetric] = {d.date: d for d in res.daily_series}

    # 2026-11-12: 1 failed out of 2 -> 0.5 (50.0%)
    assert by_date[date(2026, 11, 12)].total_deployment_count == 2
    assert by_date[date(2026, 11, 12)].failed_deployment_count == 1
    assert by_date[date(2026, 11, 12)].cfr_ratio == 0.5
    assert by_date[date(2026, 11, 12)].cfr_percentage == 50.0

    # 2026-11-13: 0 deployments -> None
    assert by_date[date(2026, 11, 13)].total_deployment_count == 0
    assert by_date[date(2026, 11, 13)].cfr_ratio is None

    # 2026-11-14: 0 failed out of 1 -> 0.0 (0.0%)
    assert by_date[date(2026, 11, 14)].total_deployment_count == 1
    assert by_date[date(2026, 11, 14)].failed_deployment_count == 0
    assert by_date[date(2026, 11, 14)].cfr_ratio == 0.0
    assert by_date[date(2026, 11, 14)].cfr_percentage == 0.0

    with pytest.raises(ValueError, match="Unsupported window_days"):
        compute_change_failure_rate([], window_days=7)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_task_28_2_sql_queries_and_calculate_rolling_change_failure_rate_async():
    """
    Task 28.2:
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
# Live PostgreSQL 16 End-to-End Verification (Task 28.2)
# ===========================================================================


@skip_without_live_db
@pytest.mark.asyncio
async def test_task_28_2_live_postgres_10_deployment_benchmark_e2e():
    """
    Live PostgreSQL 16 E2E test verifying that the 10-deployment benchmark fixture with
    overlapping FAILED/ROLLED_BACK statuses and active CONFIRMED deployment_incidents yields
    total = 10, failed = 3, CFR = 0.30 (30.0%) in both calculate_rolling_change_failure_rate
    and native SQL build_change_failure_rate_sql_query.
    """
    tenant_id, _ = await _register_test_tenant("us28-e2e")
    project_id = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    deployments, incidents, _ = _build_10_deployment_benchmark_fixture(
        tenant_id=tenant_id,
        project_id=project_id,
        base_time=window_end - timedelta(days=2),
    )

    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Project(
                id=project_id,
                tenant_id=tenant_id,
                name="US28 CFR Project",
                slug=f"proj-{tenant_id.hex[:6]}",
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
                external_key=f"INC-US28-{idx}",
                issue_type="Incident",
                title=f"Incident {idx}",
                status="OPEN",
            )
            db.add(issue)
        await db.flush()

        db.add_all(incidents)
        await db.flush()

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
