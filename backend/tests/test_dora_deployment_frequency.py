"""Unit tests for US-27 Task 27.2 rolling 14-day and 30-day Deployment Frequency calculator."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.dora.service import (
    calculate_rolling_deployment_frequency,
    compute_deployment_frequency,
)


def _make_deployment(
    *,
    environment: str = "production",
    status: str = "SUCCESS",
    deployed_at: datetime,
    authoritative_signal_source: str = "github_deployments_api",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        environment=environment,
        status=status,
        deployed_at=deployed_at,
        authoritative_signal_source=authoritative_signal_source,
    )


def test_acceptance_criteria_14_day_excludes_staging_and_failed():
    """6 successful prod, 3 staging, 1 failed prod over 14 days -> 0.43 deployments/day."""
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        # 6 successful production deployments
        _make_deployment(deployed_at=window_end - timedelta(days=1)),
        _make_deployment(deployed_at=window_end - timedelta(days=1, hours=2)),
        _make_deployment(deployed_at=window_end - timedelta(days=4)),
        _make_deployment(deployed_at=window_end - timedelta(days=7)),
        _make_deployment(deployed_at=window_end - timedelta(days=10)),
        _make_deployment(deployed_at=window_end - timedelta(days=13)),
        # 3 staging deployments (excluded)
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=2)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=5)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=8)),
        # 1 failed production deployment (excluded)
        _make_deployment(status="FAILED", deployed_at=window_end - timedelta(days=3)),
        # 1 rolled-back production deployment (excluded)
        _make_deployment(status="ROLLED_BACK", deployed_at=window_end - timedelta(days=6)),
    ]

    result = compute_deployment_frequency(deployments, window_days=14, window_end=window_end)

    assert result.window_days == 14
    assert result.successful_deployment_count == 6
    assert result.deployments_per_day == 0.43
    assert len(result.daily_series) == 14
    assert sum(bucket.count for bucket in result.daily_series) == 6
    counts_by_date = {bucket.date: bucket.count for bucket in result.daily_series}
    assert counts_by_date[(window_end - timedelta(days=1)).date()] == 2
    assert counts_by_date[(window_end - timedelta(days=2)).date()] == 0


def test_30_day_rolling_window_and_boundary_exclusions():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        _make_deployment(deployed_at=window_end - timedelta(days=i * 2))
        for i in range(15)
    ]
    # Out-of-window deployments (too old & future)
    deployments.append(_make_deployment(deployed_at=window_end - timedelta(days=31)))
    deployments.append(_make_deployment(deployed_at=window_end + timedelta(minutes=1)))

    result = compute_deployment_frequency(deployments, window_days=30, window_end=window_end)

    assert result.window_days == 30
    assert result.successful_deployment_count == 15
    assert result.deployments_per_day == 0.5
    assert len(result.daily_series) == 30


def test_zero_deployments_returns_zero_rate_and_zero_series():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    result = compute_deployment_frequency([], window_days=14, window_end=window_end)

    assert result.successful_deployment_count == 0
    assert result.deployments_per_day == 0.0
    assert len(result.daily_series) == 14
    assert all(bucket.count == 0 for bucket in result.daily_series)


def test_unsupported_window_days_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported window_days"):
        compute_deployment_frequency([], window_days=7)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_calculate_rolling_deployment_frequency_builds_authoritative_query():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    captured_statements = []
    prod_rows = [
        _make_deployment(deployed_at=window_end - timedelta(days=d))
        for d in (1, 2, 3, 4, 5, 6)
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
