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

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models.models import (
    Deployment,
    DeploymentIncident,
    Issue,
    Project,
    Tenant,
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
    Construct the canonical 10-deployment benchmark fixture (Task 28.2 Acceptance Criteria):
    - D1: FAILED deployment + 1 active CONFIRMED incident (overlapping signals -> counts as 1)
    - D2: ROLLED_BACK deployment + 2 active CONFIRMED incidents (overlapping + multi-incident -> counts as 1)
    - D3: SUCCESS deployment + 1 active CONFIRMED incident within 2h (incident failure -> counts as 1)
    - D4: SUCCESS deployment + 1 CANCELLED incident (is_active=False -> counts as 0 failed)
    - D5: SUCCESS deployment + 1 FALSE_POSITIVE incident (is_active=False -> counts as 0 failed)
    - D6..D10: 5 clean SUCCESS deployments without incidents (counts as 0 failed)
    Total production deployments = 10; Distinct failed deployments = 3 -> CFR = 0.30 (30.0%).
    """
    tid = tenant_id or uuid.uuid4()
    pid = project_id or uuid.uuid4()
    t0 = base_time or datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)

    d1 = _make_deployment(
        label="d1-failed-and-incident",
        deployed_at=t0 + timedelta(hours=1),
        status=DeploymentOutcome.FAILED.value,
        tenant_id=tid,
        project_id=pid,
    )
    d2 = _make_deployment(
        label="d2-rolled-back-and-two-incidents",
        deployed_at=t0 + timedelta(hours=2),
        status=DeploymentOutcome.ROLLED_BACK.value,
        tenant_id=tid,
        project_id=pid,
    )
    d3 = _make_deployment(
        label="d3-success-with-confirmed-incident",
        deployed_at=t0 + timedelta(hours=3),
        status=DeploymentOutcome.SUCCESS.value,
        tenant_id=tid,
        project_id=pid,
    )
    d4 = _make_deployment(
        label="d4-success-with-cancelled-incident",
        deployed_at=t0 + timedelta(hours=4),
        status=DeploymentOutcome.SUCCESS.value,
        tenant_id=tid,
        project_id=pid,
    )
    d5 = _make_deployment(
        label="d5-success-with-false-positive-incident",
        deployed_at=t0 + timedelta(hours=5),
        status=DeploymentOutcome.SUCCESS.value,
        tenant_id=tid,
        project_id=pid,
    )
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

    inc_d1 = _make_incident(deployment=d1, status="CONFIRMED", is_active=True)
    inc_d2_a = _make_incident(
        deployment=d2, status="CONFIRMED", is_active=True, detected_offset=timedelta(minutes=30)
    )
    inc_d2_b = _make_incident(
        deployment=d2, status="CONFIRMED", is_active=True, detected_offset=timedelta(hours=1)
    )
    inc_d3 = _make_incident(deployment=d3, status="CONFIRMED", is_active=True)
    inc_d4_cancelled = _make_incident(deployment=d4, status="CANCELLED", is_active=False)
    inc_d5_fp = _make_incident(deployment=d5, status="FALSE_POSITIVE", is_active=False)

    all_deployments = [d1, d2, d3, d4, d5, *clean_deployments]
    all_incidents = [inc_d1, inc_d2_a, inc_d2_b, inc_d3, inc_d4_cancelled, inc_d5_fp]

    # Simulated LEFT JOIN rows (note D2 appears twice because it has 2 active incidents)
    joined_rows: list[tuple[Deployment, Optional[DeploymentIncident]]] = [
        (d1, inc_d1),
        (d2, inc_d2_a),
        (d2, inc_d2_b),
        (d3, inc_d3),
        (d4, None),
        (d5, None),
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

    # 2. Verify via LEFT JOIN (Deployment, DeploymentIncident) rows where D2 is duplicated
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


def test_task_28_2_sql_queries_and_calculate_rolling_change_failure_rate_async():
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
    res = asyncio.run(
        calculate_rolling_change_failure_rate(
            session,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            project_id=project_id,
            window_days=14,
            window_end=window_end,
        )
    )
    assert res.total_deployment_count == 10
    assert res.failed_deployment_count == 3
    assert res.change_failure_rate == 0.30
    assert res.change_failure_rate_percentage == 30.0


# ===========================================================================
# Live PostgreSQL 16 End-to-End Verification (Task 28.2)
# ===========================================================================


def _is_postgres_available() -> bool:
    async def _probe() -> bool:
        try:
            async with async_engine.connect() as conn:
                await conn.exec_driver_sql("SELECT 1")
            await async_engine.dispose()
            return True
        except Exception:
            try:
                await async_engine.dispose()
            except Exception:
                pass
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


skip_without_live_db = pytest.mark.skipif(
    not _is_postgres_available(),
    reason="PostgreSQL test instance is not reachable locally; runs in CI.",
)


@skip_without_live_db
@pytest.mark.asyncio
async def test_task_28_2_live_postgres_10_deployment_benchmark_e2e():
    """
    Live PostgreSQL 16 E2E test verifying that the 10-deployment benchmark fixture with
    overlapping FAILED/ROLLED_BACK statuses and active CONFIRMED deployment_incidents yields
    total = 10, failed = 3, CFR = 0.30 (30.0%) in both calculate_rolling_change_failure_rate
    and native SQL build_change_failure_rate_sql_query.
    """
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    slug = f"us28-e2e-{tenant_id.hex[:8]}"
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    deployments, incidents, _ = _build_10_deployment_benchmark_fixture(
        tenant_id=tenant_id,
        project_id=project_id,
        base_time=window_end - timedelta(days=2),
    )

    async with get_tenant_db_context(tenant_id) as db:
        db.add(Tenant(id=tenant_id, slug=slug, name=f"US28 Tenant {slug}"))
        await db.flush()

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

        await db.rollback()
    await async_engine.dispose()
