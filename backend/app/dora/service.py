"""Backend DORA metrics calculation services (US-27 / Task 27.2)."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional, Sequence
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Deployment
from app.deployments.schemas import DeploymentOutcome
from app.deployments.service import build_authoritative_production_deployments_query
from app.dora.schemas import DailyDeploymentCount, DeploymentFrequencyResult

SUPPORTED_DEPLOYMENT_FREQUENCY_WINDOWS: tuple[int, ...] = (14, 30)


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _round_rate(count: int, window_days: int) -> float:
    """Compute round(count / window_days, 2) using deterministic half-up rounding."""
    if count == 0:
        return 0.0
    rate = (Decimal(count) / Decimal(window_days)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(rate)


def compute_deployment_frequency(
    deployments: Sequence[Deployment],
    *,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> DeploymentFrequencyResult:
    """Compute rolling 14-day or 30-day Deployment Frequency and zero-filled daily series."""
    if window_days not in SUPPORTED_DEPLOYMENT_FREQUENCY_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_DEPLOYMENT_FREQUENCY_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    daily_counts: Counter = Counter()
    total_successful = 0

    for deployment in deployments:
        if deployment.environment != "production":
            continue
        if deployment.status != DeploymentOutcome.SUCCESS.value:
            continue

        deployed_utc = _ensure_utc(deployment.deployed_at)
        if deployed_utc < start_utc or deployed_utc > end_utc:
            continue

        total_successful += 1
        daily_counts[deployed_utc.date()] += 1

    end_date = end_utc.date()
    start_date = end_date - timedelta(days=window_days - 1)
    daily_series = [
        DailyDeploymentCount(
            date=start_date + timedelta(days=offset),
            count=daily_counts.get(start_date + timedelta(days=offset), 0),
        )
        for offset in range(window_days)
    ]

    return DeploymentFrequencyResult(
        window_days=window_days,
        successful_deployment_count=total_successful,
        deployments_per_day=_round_rate(total_successful, window_days),
        daily_series=daily_series,
        window_start=start_utc,
        window_end=end_utc,
    )


async def calculate_rolling_deployment_frequency(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> DeploymentFrequencyResult:
    """Query authoritative SUCCESS production deployments and compute rolling DF."""
    if window_days not in SUPPORTED_DEPLOYMENT_FREQUENCY_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_DEPLOYMENT_FREQUENCY_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    stmt = build_authoritative_production_deployments_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_utc,
        end_at=end_utc,
    ).where(Deployment.status == DeploymentOutcome.SUCCESS.value)

    result = await db.execute(stmt)
    deployments = list(result.scalars().all())

    return compute_deployment_frequency(
        deployments,
        window_days=window_days,
        window_end=end_utc,
    )
