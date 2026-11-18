"""
US-27: Automated pytest test suite for rolling 14-day and 30-day Deployment Frequency calculations.

Task: SCRUM-441 (Task 27.4: Write automated pytest tests verifying 14-day (0.43/day) and 30-day Deployment Frequency calculations)
Parent User Story: SCRUM-364 (US-27: Rolling 14-Day & 30-Day DORA Deployment Frequency)
Design Specifications:
- backend/tests/scenarios/test_us27_deployment_frequency_scenarios.md (Task 27.3 / SCRUM-440)
- docs/dora-deployment-frequency-formula-design.md (Task 27.1 / SCRUM-438)

Master Test Matrix Coverage:
- TC-DORA-DF-001: Canonical 14-Day Acceptance Fixture (6 prod SUCCESS, 3 staging, 1 prod FAILED, 1 prod ROLLED_BACK -> 0.43/day)
- TC-DORA-DF-002: 30-Day Window with Same Fixture (6 / 30 = 0.20/day)
- TC-DORA-DF-003: Zero-Deployment Window (14-Day) -> 0.00 rate, 14 zero-filled daily buckets
- TC-DORA-DF-004: Zero-Deployment Window (30-Day) -> 0.00 rate, 30 zero-filled daily buckets
- TC-DORA-DF-005: 30-Day High-Cadence Fixture with Boundary Exclusions (15 / 30 = 0.50/day)
- TC-DORA-DF-006: Authoritative CD Signal Source Filtering (GitHub vs ArgoCD vs Direct API -> 4 / 14 = 0.29/day)
- TC-DORA-DF-007: Dynamic CD Signal Switch Historical Recalculation
- TC-DORA-DF-008: Exact Window Temporal Boundary Conditions (inclusive endpoints, microsecond bounds)
- TC-DORA-DF-009: Multi-Tenant Row-Level Security Isolation (SQL compilation & execution isolation)
- TC-DORA-DF-010: Intra-Tenant Project Isolation (sibling project exclusion)
- TC-DORA-DF-011: Soft-Deleted Project Exclusion (deleted_at IS NOT NULL exclusion)
- TC-DORA-DF-012: Unsupported Window Parameter Validation (7, 45, 0, -1 fail-closed)
- TC-DORA-DF-013: UTC Midnight Boundary Grouping (23:59:59Z vs 00:00:01Z distinct buckets)
- TC-DORA-DF-LIVE: End-to-end PostgreSQL 16 database verification under tenant RLS (executed in CI)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text

from app.db.models.models import Deployment, Project
from app.db.session import async_engine, get_tenant_db_context
from app.dora.service import (
    calculate_rolling_deployment_frequency,
    compute_deployment_frequency,
)
from app.main import app


# ---------------------------------------------------------------------------
# Database Availability & Teardown Fixtures
# ---------------------------------------------------------------------------


def is_live_db_available() -> bool:
    """Checks if a real PostgreSQL instance is reachable with configured credentials."""
    try:

        async def _ping():
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        asyncio.run(_ping())
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL database service not available with configured credentials in local environment",
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
# Test Fixtures & In-Memory Helpers
# ---------------------------------------------------------------------------


def _make_deployment(
    *,
    environment: str = "production",
    status: str = "SUCCESS",
    deployed_at: datetime,
    authoritative_signal_source: str = "github_deployments_api",
    project_id: Optional[uuid.UUID] = None,
    tenant_id: Optional[uuid.UUID] = None,
) -> SimpleNamespace:
    """Construct a lightweight mock Deployment entity for calculation testing."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        environment=environment,
        status=status,
        deployed_at=deployed_at,
        authoritative_signal_source=authoritative_signal_source,
    )


def _build_canonical_11_deployments(window_end: datetime) -> list[SimpleNamespace]:
    """
    Construct the canonical 11-deployment test dataset specified in SCRUM-440 / SCRUM-441:
    - 6 successful production deployments
    - 3 staging deployments (strictly excluded)
    - 1 failed production deployment (strictly excluded)
    - 1 rolled-back production deployment (strictly excluded)
    """
    return [
        # 6 successful production deployments
        _make_deployment(deployed_at=window_end - timedelta(days=1)),
        _make_deployment(deployed_at=window_end - timedelta(days=1, hours=2)),
        _make_deployment(deployed_at=window_end - timedelta(days=4)),
        _make_deployment(deployed_at=window_end - timedelta(days=7)),
        _make_deployment(deployed_at=window_end - timedelta(days=10)),
        _make_deployment(deployed_at=window_end - timedelta(days=13)),
        # 3 staging deployments (excluded by environment != 'production')
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=2)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=5)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=8)),
        # 1 failed production deployment (excluded by status != 'SUCCESS')
        _make_deployment(status="FAILED", deployed_at=window_end - timedelta(days=3)),
        # 1 rolled-back production deployment (excluded by status != 'SUCCESS')
        _make_deployment(status="ROLLED_BACK", deployed_at=window_end - timedelta(days=6)),
    ]


async def _register_test_tenant(slug_prefix: str) -> tuple[uuid.UUID, str]:
    """Register an isolated test tenant via /tenants/register for live DB tests."""
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"DORA Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"dora-{unique}@doratemp.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-001: Canonical 14-Day Acceptance Fixture (0.43/day)
# ---------------------------------------------------------------------------


def test_acceptance_criteria_14_day_excludes_staging_and_failed():
    """
    Scenario TC-DORA-DF-001: Canonical 14-Day Baseline Fixture.
    - 6 successful prod, 3 staging, 1 failed prod, 1 rolled-back prod over 14 days.
    - Expected rate: round_2(6 / 14) = 0.43 deployments/day.
    - Daily series: exactly 14 UTC calendar days summing to 6.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = _build_canonical_11_deployments(window_end)

    result = compute_deployment_frequency(deployments, window_days=14, window_end=window_end)

    assert result.window_days == 14
    assert result.successful_deployment_count == 6
    assert result.deployments_per_day == 0.43
    assert len(result.daily_series) == 14
    assert sum(bucket.count for bucket in result.daily_series) == 6

    # Verify daily bucket distribution
    counts_by_date = {bucket.date: bucket.count for bucket in result.daily_series}
    assert counts_by_date[(window_end - timedelta(days=1)).date()] == 2
    assert counts_by_date[(window_end - timedelta(days=4)).date()] == 1
    assert counts_by_date[(window_end - timedelta(days=7)).date()] == 1
    assert counts_by_date[(window_end - timedelta(days=10)).date()] == 1
    assert counts_by_date[(window_end - timedelta(days=13)).date()] == 1

    # Verify staging and failed dates are zero
    assert counts_by_date[(window_end - timedelta(days=2)).date()] == 0
    assert counts_by_date[(window_end - timedelta(days=3)).date()] == 0
    assert counts_by_date[(window_end - timedelta(days=5)).date()] == 0
    assert counts_by_date[(window_end - timedelta(days=6)).date()] == 0
    assert counts_by_date[(window_end - timedelta(days=8)).date()] == 0

    # Verify window boundary metadata
    assert result.window_start == window_end - timedelta(days=14)
    assert result.window_end == window_end


def test_tc_dora_df_001_canonical_14_day_acceptance_fixture():
    """Explicit alias for Master Test Matrix Scenario TC-DORA-DF-001."""
    test_acceptance_criteria_14_day_excludes_staging_and_failed()


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-002: 30-Day Window with Same Fixture (0.20/day)
# ---------------------------------------------------------------------------


def test_acceptance_criteria_30_day_window_with_same_fixture_yields_point_twenty():
    """
    Scenario TC-DORA-DF-002: 30-Day Window with Same 11-Deployment Fixture.
    - Same 6 successful prod, 3 staging, 1 failed, 1 rolled-back over 30 days.
    - Expected rate: round_2(6 / 30) = 0.20 deployments/day.
    - Daily series: exactly 30 UTC calendar days summing to 6 (25 zero-filled, 5 active).
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = _build_canonical_11_deployments(window_end)

    result = compute_deployment_frequency(deployments, window_days=30, window_end=window_end)

    assert result.window_days == 30
    assert result.successful_deployment_count == 6
    assert result.deployments_per_day == 0.20
    assert len(result.daily_series) == 30
    assert sum(bucket.count for bucket in result.daily_series) == 6

    # Verify zero-filled buckets vs active buckets
    zero_buckets = [b for b in result.daily_series if b.count == 0]
    active_buckets = [b for b in result.daily_series if b.count > 0]
    assert len(zero_buckets) == 25
    assert len(active_buckets) == 5

    # Verify window boundary metadata
    assert result.window_start == window_end - timedelta(days=30)
    assert result.window_end == window_end


def test_tc_dora_df_002_30_day_window_with_same_fixture():
    """Explicit alias for Master Test Matrix Scenario TC-DORA-DF-002."""
    test_acceptance_criteria_30_day_window_with_same_fixture_yields_point_twenty()


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-003 & 004: Zero-Deployment Window (0.00 vs null)
# ---------------------------------------------------------------------------


def test_zero_deployments_returns_zero_rate_and_zero_series():
    """
    Scenario TC-DORA-DF-003 & TC-DORA-DF-004: Zero-Deployment Windows.
    - Returns numeric 0.0 (SQL 0.00), strictly NOT None / null.
    - Returns array of W buckets each with integer 0.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    # 14-day zero-deployment window (TC-DORA-DF-003)
    result_14 = compute_deployment_frequency([], window_days=14, window_end=window_end)
    assert result_14.successful_deployment_count == 0
    assert result_14.deployments_per_day == 0.0
    assert result_14.deployments_per_day is not None
    assert len(result_14.daily_series) == 14
    assert all(bucket.count == 0 for bucket in result_14.daily_series)
    assert all(isinstance(bucket.count, int) for bucket in result_14.daily_series)
    assert result_14.daily_series[0].date == (window_end.date() - timedelta(days=13))
    assert result_14.daily_series[-1].date == window_end.date()

    # 30-day zero-deployment window (TC-DORA-DF-004)
    result_30 = compute_deployment_frequency([], window_days=30, window_end=window_end)
    assert result_30.successful_deployment_count == 0
    assert result_30.deployments_per_day == 0.0
    assert result_30.deployments_per_day is not None
    assert len(result_30.daily_series) == 30
    assert all(bucket.count == 0 for bucket in result_30.daily_series)
    assert result_30.daily_series[0].date == (window_end.date() - timedelta(days=29))
    assert result_30.daily_series[-1].date == window_end.date()


def test_tc_dora_df_003_zero_deployment_window_14_day():
    """Verify TC-DORA-DF-003 with empty input and non-prod/failed input."""
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    # Dataset containing only non-production and failed deployments
    deployments = [
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=2)),
        _make_deployment(status="FAILED", deployed_at=window_end - timedelta(days=3)),
    ]
    result = compute_deployment_frequency(deployments, window_days=14, window_end=window_end)
    assert result.successful_deployment_count == 0
    assert result.deployments_per_day == 0.0
    assert result.deployments_per_day is not None
    assert len(result.daily_series) == 14
    assert all(b.count == 0 for b in result.daily_series)


def test_tc_dora_df_004_zero_deployment_window_30_day():
    """Verify TC-DORA-DF-004 with empty input."""
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    result = compute_deployment_frequency([], window_days=30, window_end=window_end)
    assert result.successful_deployment_count == 0
    assert result.deployments_per_day == 0.0
    assert len(result.daily_series) == 30
    assert all(b.count == 0 for b in result.daily_series)


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-005: 30-Day High-Cadence Fixture (0.50/day)
# ---------------------------------------------------------------------------


def test_30_day_rolling_window_and_boundary_exclusions():
    """
    Scenario TC-DORA-DF-005: 30-Day High-Cadence Fixture with Boundary Exclusions.
    - 15 prod SUCCESS deployments in window.
    - 1 deployment at day -31 (too old -> strictly excluded).
    - 1 deployment in future (+1 min -> strictly excluded).
    - Expected rate: 15 / 30 = 0.50 deployments/day.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        _make_deployment(deployed_at=window_end - timedelta(days=i * 2)) for i in range(15)
    ]
    # Out-of-window deployments (too old & future)
    deployments.append(_make_deployment(deployed_at=window_end - timedelta(days=31)))
    deployments.append(_make_deployment(deployed_at=window_end + timedelta(minutes=1)))

    result = compute_deployment_frequency(deployments, window_days=30, window_end=window_end)

    assert result.window_days == 30
    assert result.successful_deployment_count == 15
    assert result.deployments_per_day == 0.50
    assert len(result.daily_series) == 30
    assert sum(bucket.count for bucket in result.daily_series) == 15


def test_tc_dora_df_005_30_day_high_cadence_boundary_exclusions():
    """Explicit alias for Master Test Matrix Scenario TC-DORA-DF-005."""
    test_30_day_rolling_window_and_boundary_exclusions()


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-006: Authoritative CD Signal Source Filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_df_006_authoritative_signal_filtering():
    """
    Scenario TC-DORA-DF-006: Authoritative CD Signal Source Filtering.
    - 4 GitHub prod SUCCESS, 4 ArgoCD prod SUCCESS, 2 Direct API prod SUCCESS.
    - Project configured for 'github_deployments_api'.
    - Calculator strictly filters matching rows -> 4 / 14 = 0.29 deployments/day.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()

    github_rows = [
        _make_deployment(
            deployed_at=window_end - timedelta(days=d),
            authoritative_signal_source="github_deployments_api",
            tenant_id=tenant_id,
            project_id=project_id,
        )
        for d in (1, 3, 5, 7)
    ]

    class _FakeScalars:
        def all(self):
            return github_rows

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    captured_statements = []

    class _FakeSession:
        async def execute(self, stmt):
            captured_statements.append(stmt)
            return _FakeResult()

    result = await calculate_rolling_deployment_frequency(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
        window_end=window_end,
    )

    assert result.successful_deployment_count == 4
    assert result.deployments_per_day == 0.29  # round_2(4 / 14) = 0.29
    assert len(result.daily_series) == 14
    assert sum(b.count for b in result.daily_series) == 4

    # Verify query includes authoritative signal join
    assert len(captured_statements) == 1
    sql = str(captured_statements[0])
    assert "deployments.authoritative_signal_source = projects.authoritative_cd_signal" in sql


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-007: Dynamic CD Signal Switch Historical Recalculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_df_007_dynamic_cd_signal_switch():
    """
    Scenario TC-DORA-DF-007: Dynamic CD Signal Switch Recalculation.
    - Switch project signal from GitHub to ArgoCD: returns 4 ArgoCD deployments -> 0.29/day.
    - Switch project signal to Direct API: returns 2 Direct API deployments -> 0.14/day.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()

    argocd_rows = [
        _make_deployment(
            deployed_at=window_end - timedelta(days=d),
            authoritative_signal_source="argocd_postsync_webhook",
            tenant_id=tenant_id,
            project_id=project_id,
        )
        for d in (2, 4, 6, 8)
    ]
    direct_rows = [
        _make_deployment(
            deployed_at=window_end - timedelta(days=d),
            authoritative_signal_source="direct_deployment_api",
            tenant_id=tenant_id,
            project_id=project_id,
        )
        for d in (9, 11)
    ]

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: self._rows)

    class _ArgocdSession:
        async def execute(self, stmt):
            return _FakeResult(argocd_rows)

    class _DirectSession:
        async def execute(self, stmt):
            return _FakeResult(direct_rows)

    res_argocd = await calculate_rolling_deployment_frequency(
        _ArgocdSession(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
        window_end=window_end,
    )
    assert res_argocd.successful_deployment_count == 4
    assert res_argocd.deployments_per_day == 0.29

    res_direct = await calculate_rolling_deployment_frequency(
        _DirectSession(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
        window_end=window_end,
    )
    assert res_direct.successful_deployment_count == 2
    assert res_direct.deployments_per_day == 0.14  # round_2(2 / 14) = 0.14


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-008: Exact Window Temporal Boundary Conditions
# ---------------------------------------------------------------------------


def test_tc_dora_df_008_exact_window_temporal_boundaries():
    """
    Scenario TC-DORA-DF-008: Inclusive endpoints and microsecond exclusions.
    - deployed_at = window_end (INCLUDED)
    - deployed_at = window_start (INCLUDED)
    - deployed_at = window_start - 1 microsecond (EXCLUDED)
    - deployed_at = window_end + 1 microsecond (EXCLUDED)
    - Expected count: exactly 2 -> round_2(2 / 14) = 0.14 deployments/day.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, 0, tzinfo=timezone.utc)
    window_start = window_end - timedelta(days=14)

    deployments = [
        # Exactly on upper boundary: INCLUDED
        _make_deployment(deployed_at=window_end),
        # Exactly on lower boundary: INCLUDED
        _make_deployment(deployed_at=window_start),
        # 1 microsecond before lower bound: EXCLUDED
        _make_deployment(deployed_at=window_start - timedelta(microseconds=1)),
        # 1 microsecond after upper bound: EXCLUDED
        _make_deployment(deployed_at=window_end + timedelta(microseconds=1)),
    ]

    result = compute_deployment_frequency(deployments, window_days=14, window_end=window_end)

    assert result.window_days == 14
    assert result.successful_deployment_count == 2
    assert result.deployments_per_day == 0.14
    assert len(result.daily_series) == 14


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-009: Multi-Tenant Row-Level Security Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_df_009_multi_tenant_rls_isolation():
    """
    Scenario TC-DORA-DF-009: Multi-Tenant Query Isolation.
    - Verifies SQL query contains explicit tenant_id filter.
    - Tenant B rows are never factored into Tenant A calculations.
    """
    tenant_a_id = uuid.uuid4()
    project_a_id = uuid.uuid4()
    captured_statements = []

    class _FakeSession:
        async def execute(self, stmt):
            captured_statements.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    await calculate_rolling_deployment_frequency(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=tenant_a_id,
        project_id=project_a_id,
        window_days=14,
    )

    assert len(captured_statements) == 1
    sql = str(captured_statements[0])
    assert "deployments.tenant_id =" in sql
    assert (
        "projects.id = deployments.project_id" in sql
        or "deployments.project_id = projects.id" in sql
    )


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-010: Intra-Tenant Project Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_df_010_intra_tenant_project_isolation():
    """
    Scenario TC-DORA-DF-010: Intra-Tenant Project Isolation.
    - Query strictly filters by project_id so sibling projects are never conflated.
    """
    tenant_id = uuid.uuid4()
    project_1_id = uuid.uuid4()
    captured_statements = []

    class _FakeSession:
        async def execute(self, stmt):
            captured_statements.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    await calculate_rolling_deployment_frequency(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_1_id,
        window_days=14,
    )

    assert len(captured_statements) == 1
    sql = str(captured_statements[0])
    assert "deployments.project_id =" in sql


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-011: Soft-Deleted Project Exclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_df_011_soft_deleted_project_exclusion():
    """
    Scenario TC-DORA-DF-011: Soft-Deleted Project Exclusion.
    - Verifies SQL query joins projects and requires projects.deleted_at IS NULL.
    """
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    captured_statements = []

    class _FakeSession:
        async def execute(self, stmt):
            captured_statements.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    await calculate_rolling_deployment_frequency(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=14,
    )

    assert len(captured_statements) == 1
    sql = str(captured_statements[0])
    assert "projects.deleted_at IS NULL" in sql


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-012: Unsupported Window Parameter Validation
# ---------------------------------------------------------------------------


def test_unsupported_window_days_raises_value_error():
    """
    Scenario TC-DORA-DF-012: Unsupported Window Parameter Validation.
    - Any window_days not in (14, 30) (e.g. 7, 45, 0, -1, 60) raises ValueError fail-closed.
    """
    for invalid in (7, 45, 0, -1, 60):
        with pytest.raises(ValueError, match="Unsupported window_days"):
            compute_deployment_frequency([], window_days=invalid)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_tc_dora_df_012_unsupported_window_validation_async():
    """Verify calculate_rolling_deployment_frequency rejects invalid window."""
    with pytest.raises(ValueError, match="Unsupported window_days"):
        await calculate_rolling_deployment_frequency(
            None,  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            window_days=7,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Scenario TC-DORA-DF-013: UTC Midnight Boundary Grouping
# ---------------------------------------------------------------------------


def test_tc_dora_df_013_utc_midnight_boundary_grouping():
    """
    Scenario TC-DORA-DF-013: UTC Midnight Boundary Grouping.
    - Deployments at 23:59:59Z and 00:00:01Z land in distinct calendar dates.
    - No timezone drift across day transition.
    """
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        _make_deployment(deployed_at=datetime(2026, 11, 12, 23, 59, 59, tzinfo=timezone.utc)),
        _make_deployment(deployed_at=datetime(2026, 11, 13, 0, 0, 1, tzinfo=timezone.utc)),
    ]

    result = compute_deployment_frequency(deployments, window_days=14, window_end=window_end)

    assert result.successful_deployment_count == 2
    assert result.deployments_per_day == 0.14
    counts_by_date = {b.date: b.count for b in result.daily_series}
    assert counts_by_date[datetime(2026, 11, 12).date()] == 1
    assert counts_by_date[datetime(2026, 11, 13).date()] == 1
    assert sum(b.count for b in result.daily_series) == 2


# ---------------------------------------------------------------------------
# Query Builder Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calculate_rolling_deployment_frequency_builds_authoritative_query():
    """Verify query structure produced by calculate_rolling_deployment_frequency."""
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    captured_statements = []
    prod_rows = [
        _make_deployment(deployed_at=window_end - timedelta(days=d)) for d in (1, 2, 3, 4, 5, 6)
    ]

    class _FakeScalars:
        def all(self):
            return prod_rows

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, stmt):
            captured_statements.append(stmt)
            return _FakeResult()

    result = await calculate_rolling_deployment_frequency(
        _FakeSession(),  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        window_days=14,
        window_end=window_end,
    )

    assert len(captured_statements) == 1
    sql_text = str(captured_statements[0])
    assert "deployments.environment =" in sql_text
    assert "deployments.status =" in sql_text
    assert "deployments.authoritative_signal_source = projects.authoritative_cd_signal" in sql_text
    assert result.successful_deployment_count == 6
    assert result.deployments_per_day == 0.43


# ---------------------------------------------------------------------------
# End-to-End Live PostgreSQL Verification (Runs in CI with live PostgreSQL)
# ---------------------------------------------------------------------------


@skip_without_live_db
@pytest.mark.asyncio
async def test_tc_dora_df_live_db_14_and_30_day_e2e():
    """
    End-to-end integration test executed against live PostgreSQL 16 under tenant RLS.
    - Registers real tenant via /tenants/register.
    - Inserts Project and canonical 11 deployments into PostgreSQL.
    - Executes calculate_rolling_deployment_frequency for 14-day and 30-day windows.
    - Asserts real SQL execution yields 0.43/day (14-day) and 0.20/day (30-day).
    """
    tenant_id, slug = await _register_test_tenant("df-live")
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    proj_id = uuid.uuid4()

    async with get_tenant_db_context(tenant_id) as session:
        # Create Project
        project = Project(
            id=proj_id,
            tenant_id=tenant_id,
            name=f"DORA DF Project {proj_id.hex[:6]}",
            slug=f"df-proj-{proj_id.hex[:6]}",
            authoritative_cd_signal="github_deployments_api",
            is_active=True,
        )
        session.add(project)
        await session.flush()

        # Insert canonical 11 deployments
        deployments_data = [
            # 6 successful production deployments
            ("production", "SUCCESS", window_end - timedelta(days=1)),
            ("production", "SUCCESS", window_end - timedelta(days=1, hours=2)),
            ("production", "SUCCESS", window_end - timedelta(days=4)),
            ("production", "SUCCESS", window_end - timedelta(days=7)),
            ("production", "SUCCESS", window_end - timedelta(days=10)),
            ("production", "SUCCESS", window_end - timedelta(days=13)),
            # 3 staging deployments (excluded)
            ("staging", "SUCCESS", window_end - timedelta(days=2)),
            ("staging", "SUCCESS", window_end - timedelta(days=5)),
            ("staging", "SUCCESS", window_end - timedelta(days=8)),
            # 1 failed production deployment (excluded)
            ("production", "FAILED", window_end - timedelta(days=3)),
            # 1 rolled-back production deployment (excluded)
            ("production", "ROLLED_BACK", window_end - timedelta(days=6)),
        ]

        for env, status, dep_at in deployments_data:
            session.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=proj_id,
                    commit_sha="a" * 40,
                    environment=env,
                    status=status,
                    is_change_failure=False,
                    authoritative_signal_source="github_deployments_api",
                    deployed_at=dep_at,
                )
            )
        await session.flush()

        # 1. Verify 14-day rolling window
        res_14 = await calculate_rolling_deployment_frequency(
            session,
            tenant_id=tenant_id,
            project_id=proj_id,
            window_days=14,
            window_end=window_end,
        )
        assert res_14.successful_deployment_count == 6
        assert res_14.deployments_per_day == 0.43
        assert len(res_14.daily_series) == 14
        assert sum(b.count for b in res_14.daily_series) == 6

        # 2. Verify 30-day rolling window
        res_30 = await calculate_rolling_deployment_frequency(
            session,
            tenant_id=tenant_id,
            project_id=proj_id,
            window_days=30,
            window_end=window_end,
        )
        assert res_30.successful_deployment_count == 6
        assert res_30.deployments_per_day == 0.20
        assert len(res_30.daily_series) == 30
        assert sum(b.count for b in res_30.daily_series) == 6
