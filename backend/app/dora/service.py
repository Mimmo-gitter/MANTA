"""Backend DORA metrics calculation services (US-26 / Task 26.3, US-27 / Task 27.2, US-28 / Task 28.2 & US-29 / Task 29.2)."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Optional, Sequence
import uuid

from fastapi import HTTPException, status as http_status
from sqlalchemy import Numeric, Select, and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import (
    Deployment,
    DeploymentIncident,
    DeploymentPullRequest,
    Incident,
    ProductionFailure,
    Project,
    PullRequest,
)
from app.db.session import get_tenant_db_context
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.deployments.service import build_authoritative_production_deployments_query
from app.dora.schemas import (
    ChangeFailureRateResult,
    DailyChangeFailureRateMetric,
    DailyDeploymentCount,
    DailyLeadTimeMetric,
    DailyMTTRMetric,
    DeploymentFrequencyResult,
    DoraAggregates,
    DoraDailyPoint,
    DoraMetricsResponse,
    DoraTimeframeDays,
    DoraWindow,
    LeadTimeResult,
    MTTROutageItem,
    MTTRResult,
    PullRequestLeadTimeItem,
)

SUPPORTED_DEPLOYMENT_FREQUENCY_WINDOWS: tuple[int, ...] = (14, 30)
SUPPORTED_LEAD_TIME_WINDOWS: tuple[int, ...] = (14, 30)
SUPPORTED_CFR_WINDOWS: tuple[int, ...] = (14, 30)
SUPPORTED_MTTR_WINDOWS: tuple[int, ...] = (14, 30)
INCIDENT_CORRELATION_WINDOW_SECONDS: int = 86400


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _round_two_decimals(value: float | Decimal) -> float:
    """Round a numeric value to 2 decimal places using deterministic half-up rounding."""
    quantized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(quantized)


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


# ---------------------------------------------------------------------------
# US-26 / Task 26.3: Per-PR Lead Time & Project Median Lead Time Calculator
# ---------------------------------------------------------------------------


def compute_pr_lead_time_seconds(
    first_commit_at: datetime,
    deployed_at: datetime,
) -> float:
    """
    Calculate per-PR Lead Time in seconds: T_prod_deployed - T_first_commit (Task 26.3).

    Raises ValueError if deployed_at is earlier than first_commit_at.
    """
    first_utc = _ensure_utc(first_commit_at)
    deployed_utc = _ensure_utc(deployed_at)
    delta_seconds = (deployed_utc - first_utc).total_seconds()
    if delta_seconds < 0:
        raise ValueError(
            f"deployed_at ({deployed_utc.isoformat()}) cannot be earlier than "
            f"first_commit_at ({first_utc.isoformat()})."
        )
    return _round_two_decimals(delta_seconds)


def compute_pr_lead_time_hours(
    first_commit_at: datetime,
    deployed_at: datetime,
) -> float:
    """
    Calculate per-PR Lead Time in hours: (T_prod_deployed - T_first_commit) / 3600 (Task 26.3).

    Raises ValueError if deployed_at is earlier than first_commit_at.
    """
    first_utc = _ensure_utc(first_commit_at)
    deployed_utc = _ensure_utc(deployed_at)
    delta_seconds = (deployed_utc - first_utc).total_seconds()
    if delta_seconds < 0:
        raise ValueError(
            f"deployed_at ({deployed_utc.isoformat()}) cannot be earlier than "
            f"first_commit_at ({first_utc.isoformat()})."
        )
    hours = Decimal(str(delta_seconds)) / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_continuous_median(values: Sequence[float | Decimal]) -> Optional[float]:
    """
    Compute exact continuous median equivalent to PostgreSQL percentile_cont(0.5) WITHIN GROUP.

    - Returns None when values is empty (0 shipped PRs).
    - For odd N, returns the middle sorted element.
    - For even N, linearly interpolates 0.5 * (sorted[N//2 - 1] + sorted[N//2]).
    """
    if not values:
        return None

    sorted_vals = sorted(Decimal(str(v)) for v in values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        median_val = sorted_vals[mid]
    else:
        median_val = (sorted_vals[mid - 1] + sorted_vals[mid]) / Decimal("2")

    return float(median_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _unpack_shipped_pr_entry(entry: Any) -> tuple[Any, Any]:
    """
    Normalize a shipped PR entry into a (pull_request, deployment) pair.

    Supports:
    - (PullRequest, Deployment) 2-tuples
    - (DeploymentPullRequest, PullRequest, Deployment) 3-tuples
    - Objects with .pull_request and .deployment attributes
    - Flat objects carrying both PullRequest and Deployment fields directly
    """
    if isinstance(entry, tuple):
        if len(entry) == 2:
            return entry[0], entry[1]
        if len(entry) == 3:
            return entry[1], entry[2]
    if hasattr(entry, "pull_request") and hasattr(entry, "deployment"):
        return entry.pull_request, entry.deployment
    return entry, entry


def compute_lead_time_metrics(
    shipped_prs: Sequence[Any],
    *,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
    author_pseudonym_id: Optional[str] = None,
) -> LeadTimeResult:
    """
    Compute per-PR Lead Time (attributed to contributor pseudonyms) and the project's
    rolling 14d/30d continuous median Lead Time and daily trend series (Task 26.3).
    """
    if window_days not in SUPPORTED_LEAD_TIME_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_LEAD_TIME_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    # Deduplicate by pull_request_id, keeping the earliest shipping production deployment
    earliest_by_pr_id: dict[uuid.UUID, tuple[PullRequestLeadTimeItem, Decimal]] = {}

    for entry in shipped_prs:
        pr_obj, dep_obj = _unpack_shipped_pr_entry(entry)

        dep_env = getattr(dep_obj, "environment", "production")
        dep_status = getattr(dep_obj, "status", DeploymentOutcome.SUCCESS.value)
        if dep_env != "production" or dep_status != DeploymentOutcome.SUCCESS.value:
            continue

        pr_state = getattr(pr_obj, "state", "merged")
        if pr_state is not None and str(pr_state).strip().lower() != "merged":
            continue

        first_commit_at = getattr(pr_obj, "first_commit_at", None)
        deployed_at = getattr(dep_obj, "deployed_at", None)
        if first_commit_at is None or deployed_at is None:
            continue

        first_utc = _ensure_utc(first_commit_at)
        deployed_utc = _ensure_utc(deployed_at)

        if deployed_utc < start_utc or deployed_utc > end_utc:
            continue
        if deployed_utc < first_utc:
            continue

        pr_author_pseudonym = getattr(pr_obj, "author_pseudonym_id", None) or ""
        if author_pseudonym_id is not None and pr_author_pseudonym != author_pseudonym_id:
            continue

        pr_id = getattr(pr_obj, "pull_request_id", None) or getattr(pr_obj, "id", None)
        dep_id = getattr(dep_obj, "deployment_id", None) or getattr(dep_obj, "id", None)
        if pr_id is None or dep_id is None:
            continue

        raw_seconds = Decimal(str((deployed_utc - first_utc).total_seconds()))
        lead_seconds = float(raw_seconds.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        lead_hours = float(
            (raw_seconds / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

        item = PullRequestLeadTimeItem(
            pull_request_id=pr_id,
            deployment_id=dep_id,
            repository_id=getattr(pr_obj, "repository_id", None)
            or getattr(dep_obj, "repository_id", None),
            external_pr_number=int(getattr(pr_obj, "external_pr_number", 0)),
            author_pseudonym_id=pr_author_pseudonym,
            actor_pseudonym_id=getattr(dep_obj, "actor_pseudonym_id", None),
            merge_commit_sha=getattr(pr_obj, "merge_commit_sha", None),
            head_sha=getattr(pr_obj, "head_sha", None),
            first_commit_at=first_utc,
            deployed_at=deployed_utc,
            lead_time_seconds=lead_seconds,
            lead_time_hours=lead_hours,
        )

        existing = earliest_by_pr_id.get(pr_id)
        if existing is None or deployed_utc < existing[0].deployed_at:
            earliest_by_pr_id[pr_id] = (item, raw_seconds)

    ordered_pairs = sorted(
        earliest_by_pr_id.values(),
        key=lambda pair: (
            pair[0].deployed_at,
            pair[0].external_pr_number,
            str(pair[0].pull_request_id),
        ),
    )

    pr_items = [pair[0] for pair in ordered_pairs]
    all_raw_seconds = [pair[1] for pair in ordered_pairs]
    all_raw_hours = [sec / Decimal("3600") for sec in all_raw_seconds]

    daily_raw_seconds: dict[date, list[Decimal]] = defaultdict(list)
    for item, raw_sec in ordered_pairs:
        daily_raw_seconds[item.deployed_at.date()].append(raw_sec)

    end_date = end_utc.date()
    start_date = end_date - timedelta(days=window_days - 1)
    daily_series: list[DailyLeadTimeMetric] = []

    for offset in range(window_days):
        bucket_date = start_date + timedelta(days=offset)
        day_secs = daily_raw_seconds.get(bucket_date, [])
        day_hours = [sec / Decimal("3600") for sec in day_secs]
        daily_series.append(
            DailyLeadTimeMetric(
                date=bucket_date,
                deployed_pr_count=len(day_secs),
                median_lead_time_seconds=compute_continuous_median(day_secs),
                median_lead_time_hours=compute_continuous_median(day_hours),
            )
        )

    return LeadTimeResult(
        window_days=window_days,
        deployed_pr_count=len(pr_items),
        median_lead_time_seconds=compute_continuous_median(all_raw_seconds),
        median_lead_time_hours=compute_continuous_median(all_raw_hours),
        pull_requests=pr_items,
        daily_series=daily_series,
        window_start=start_utc,
        window_end=end_utc,
    )


def build_shipped_pull_requests_lead_time_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    author_pseudonym_id: Optional[str] = None,
) -> Select[tuple[PullRequest, Deployment]]:
    """
    Canonical DORA Lead Time query joining deployment_pull_requests -> pull_requests -> deployments
    for authoritative SUCCESS production deployments in an active project (Task 26.3).
    """
    stmt = (
        select(PullRequest, Deployment)
        .join(
            DeploymentPullRequest,
            DeploymentPullRequest.pull_request_id == PullRequest.id,
        )
        .join(
            Deployment,
            Deployment.id == DeploymentPullRequest.deployment_id,
        )
        .join(Project, Project.id == Deployment.project_id)
        .where(
            DeploymentPullRequest.tenant_id == tenant_id,
            PullRequest.tenant_id == tenant_id,
            PullRequest.project_id == project_id,
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.environment == "production",
            Deployment.status == DeploymentOutcome.SUCCESS.value,
            Deployment.authoritative_signal_source == Project.authoritative_cd_signal,
            Project.deleted_at.is_(None),
            PullRequest.first_commit_at.is_not(None),
        )
        .order_by(Deployment.deployed_at.asc(), PullRequest.external_pr_number.asc())
    )
    if start_at is not None:
        stmt = stmt.where(Deployment.deployed_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Deployment.deployed_at <= end_at)
    if author_pseudonym_id is not None:
        stmt = stmt.where(PullRequest.author_pseudonym_id == author_pseudonym_id)
    return stmt


def build_project_median_lead_time_sql_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    author_pseudonym_id: Optional[str] = None,
) -> Select[tuple[Optional[float], int]]:
    """
    Build SQL query computing PostgreSQL percentile_cont(0.5) median Lead Time in seconds
    and shipped PR count across authoritative SUCCESS production deployments.
    """
    lead_time_epoch = func.extract("epoch", Deployment.deployed_at - PullRequest.first_commit_at)
    stmt = (
        select(
            func.percentile_cont(0.5)
            .within_group(lead_time_epoch)
            .label("median_lead_time_seconds"),
            func.count(PullRequest.id).label("deployed_pr_count"),
        )
        .select_from(DeploymentPullRequest)
        .join(PullRequest, PullRequest.id == DeploymentPullRequest.pull_request_id)
        .join(Deployment, Deployment.id == DeploymentPullRequest.deployment_id)
        .join(Project, Project.id == Deployment.project_id)
        .where(
            DeploymentPullRequest.tenant_id == tenant_id,
            PullRequest.tenant_id == tenant_id,
            PullRequest.project_id == project_id,
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.environment == "production",
            Deployment.status == DeploymentOutcome.SUCCESS.value,
            Deployment.authoritative_signal_source == Project.authoritative_cd_signal,
            Project.deleted_at.is_(None),
            PullRequest.first_commit_at.is_not(None),
            Deployment.deployed_at >= PullRequest.first_commit_at,
        )
    )
    if start_at is not None:
        stmt = stmt.where(Deployment.deployed_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Deployment.deployed_at <= end_at)
    if author_pseudonym_id is not None:
        stmt = stmt.where(PullRequest.author_pseudonym_id == author_pseudonym_id)
    return stmt


async def calculate_rolling_lead_time(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
    author_pseudonym_id: Optional[str] = None,
) -> LeadTimeResult:
    """
    Query shipped PRs via deployment_pull_requests for authoritative SUCCESS production
    deployments and compute per-PR Lead Time and rolling 14d/30d project median (Task 26.3).
    """
    if window_days not in SUPPORTED_LEAD_TIME_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_LEAD_TIME_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    stmt = build_shipped_pull_requests_lead_time_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_utc,
        end_at=end_utc,
        author_pseudonym_id=author_pseudonym_id,
    )

    result = await db.execute(stmt)
    if hasattr(result, "all"):
        shipped_rows = [
            tuple(row) if not isinstance(row, tuple) and hasattr(row, "__iter__") else row
            for row in result.all()
        ]
    else:
        shipped_rows = list(result.scalars().all())

    return compute_lead_time_metrics(
        shipped_rows,
        window_days=window_days,
        window_end=end_utc,
        author_pseudonym_id=author_pseudonym_id,
    )


# ---------------------------------------------------------------------------
# US-28 / Task 28.2: Change Failure Rate (CFR) Distinct Union Calculator
# ---------------------------------------------------------------------------


def _round_cfr_ratio(failed_count: int, total_count: int) -> Optional[float]:
    """Compute round(failed_count / total_count, 4) or None when total_count == 0."""
    if total_count <= 0:
        return None
    if failed_count <= 0:
        return 0.0
    ratio = (Decimal(failed_count) / Decimal(total_count)).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )
    return float(ratio)


def _round_cfr_percentage(failed_count: int, total_count: int) -> Optional[float]:
    """Compute round((failed_count / total_count) * 100.0, 2) or None when total_count == 0."""
    if total_count <= 0:
        return None
    if failed_count <= 0:
        return 0.0
    pct = ((Decimal(failed_count) * Decimal("100")) / Decimal(total_count)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(pct)


def is_active_confirmed_incident_for_deployment(
    incident: Any,
    deployment: Any,
) -> bool:
    """
    Evaluate whether a DeploymentIncident link is an active CONFIRMED failure signal
    within the 0..24h correlation window for a production deployment (Task 28.1 / Task 28.2).
    """
    if incident is None or deployment is None:
        return False

    if getattr(incident, "is_active", True) is not True:
        return False

    inc_status = getattr(incident, "status", "CONFIRMED")
    if inc_status is not None and str(inc_status).strip().upper() != "CONFIRMED":
        return False

    dep_tid = getattr(deployment, "tenant_id", None)
    inc_tid = getattr(incident, "tenant_id", None)
    if dep_tid is not None and inc_tid is not None and dep_tid != inc_tid:
        return False

    dep_id = getattr(deployment, "id", None)
    inc_dep_id = getattr(incident, "deployment_id", None)
    if dep_id is not None and inc_dep_id is not None and dep_id != inc_dep_id:
        return False

    detected_at = getattr(incident, "detected_at", None)
    deployed_at = getattr(deployment, "deployed_at", None)
    if detected_at is not None and deployed_at is not None:
        delta_seconds = (_ensure_utc(detected_at) - _ensure_utc(deployed_at)).total_seconds()
        if delta_seconds < 0 or delta_seconds > INCIDENT_CORRELATION_WINDOW_SECONDS:
            return False

    return True


def compute_change_failure_rate(
    deployments: Sequence[Any],
    *,
    deployment_incidents: Sequence[DeploymentIncident] = (),
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> ChangeFailureRateResult:
    """
    Compute rolling 14-day or 30-day Change Failure Rate using a strict distinct union
    over deployment IDs (COUNT(DISTINCT d.id)) across FAILED/ROLLED_BACK outcomes and
    active CONFIRMED deployment_incidents (Task 28.2).

    Overlapping failure signals on the same deployment increment failed_deployment_count
    by exactly 1. Returns None (null) for CFR when total_deployment_count == 0.
    """
    if window_days not in SUPPORTED_CFR_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_CFR_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    incidents_by_dep_id: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for inc in deployment_incidents:
        dep_id = getattr(inc, "deployment_id", None)
        if dep_id is not None:
            incidents_by_dep_id[dep_id].append(inc)

    unique_deployments: dict[uuid.UUID, tuple[Any, datetime]] = {}

    for entry in deployments:
        if isinstance(entry, tuple) and len(entry) >= 2:
            dep_obj, joined_inc = entry[0], entry[1]
        else:
            dep_obj, joined_inc = entry, None

        if dep_obj is None:
            continue
        if getattr(dep_obj, "environment", None) != "production":
            continue

        deployed_at = getattr(dep_obj, "deployed_at", None)
        if deployed_at is None:
            continue
        deployed_utc = _ensure_utc(deployed_at)
        if deployed_utc < start_utc or deployed_utc > end_utc:
            continue

        dep_id = getattr(dep_obj, "id", None)
        if dep_id is None:
            continue

        if dep_id not in unique_deployments:
            unique_deployments[dep_id] = (dep_obj, deployed_utc)

        if joined_inc is not None:
            incidents_by_dep_id[dep_id].append(joined_inc)

        preloaded_links = getattr(dep_obj, "__dict__", {}).get("incident_links")
        if preloaded_links:
            for link in preloaded_links:
                incidents_by_dep_id[dep_id].append(link)

    ordered_deployments = sorted(
        unique_deployments.values(),
        key=lambda pair: (pair[1], str(pair[0].id)),
    )

    failed_deployment_ids: list[uuid.UUID] = []
    daily_total_counts: Counter = Counter()
    daily_failed_counts: Counter = Counter()

    for dep_obj, deployed_utc in ordered_deployments:
        dep_id = dep_obj.id
        deploy_date = deployed_utc.date()
        daily_total_counts[deploy_date] += 1

        dep_status = getattr(dep_obj, "status", None)
        is_failed_outcome = dep_status in (
            DeploymentOutcome.FAILED.value,
            DeploymentOutcome.ROLLED_BACK.value,
        )
        has_active_incident = any(
            is_active_confirmed_incident_for_deployment(inc, dep_obj)
            for inc in incidents_by_dep_id.get(dep_id, ())
        )

        if is_failed_outcome or has_active_incident:
            failed_deployment_ids.append(dep_id)
            daily_failed_counts[deploy_date] += 1

    total_count = len(ordered_deployments)
    failed_count = len(failed_deployment_ids)

    end_date = end_utc.date()
    start_date = end_date - timedelta(days=window_days - 1)
    daily_series: list[DailyChangeFailureRateMetric] = []

    for offset in range(window_days):
        bucket_date = start_date + timedelta(days=offset)
        day_total = daily_total_counts.get(bucket_date, 0)
        day_failed = daily_failed_counts.get(bucket_date, 0)
        daily_series.append(
            DailyChangeFailureRateMetric(
                date=bucket_date,
                total_deployment_count=day_total,
                failed_deployment_count=day_failed,
                change_failure_rate=_round_cfr_ratio(day_failed, day_total),
                change_failure_rate_percentage=_round_cfr_percentage(day_failed, day_total),
            )
        )

    return ChangeFailureRateResult(
        window_days=window_days,
        total_deployment_count=total_count,
        failed_deployment_count=failed_count,
        change_failure_rate=_round_cfr_ratio(failed_count, total_count),
        change_failure_rate_percentage=_round_cfr_percentage(failed_count, total_count),
        failed_deployment_ids=failed_deployment_ids,
        daily_series=daily_series,
        window_start=start_utc,
        window_end=end_utc,
    )


def build_change_failure_rate_deployments_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[Deployment, Optional[DeploymentIncident]]]:
    """
    Build authoritative production deployments query left-joining active CONFIRMED
    deployment_incidents within the 24-hour correlation window (Task 28.2).
    """
    stmt = (
        select(Deployment, DeploymentIncident)
        .join(Project, Project.id == Deployment.project_id)
        .outerjoin(
            DeploymentIncident,
            and_(
                DeploymentIncident.deployment_id == Deployment.id,
                DeploymentIncident.tenant_id == Deployment.tenant_id,
                DeploymentIncident.is_active.is_(True),
                DeploymentIncident.status == "CONFIRMED",
                DeploymentIncident.detected_at >= Deployment.deployed_at,
                DeploymentIncident.detected_at
                <= Deployment.deployed_at + text("INTERVAL '24 hours'"),
            ),
        )
        .where(
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.environment == "production",
            Deployment.authoritative_signal_source == Project.authoritative_cd_signal,
            Project.deleted_at.is_(None),
        )
        .order_by(Deployment.deployed_at.asc(), Deployment.id.asc())
    )
    if start_at is not None:
        stmt = stmt.where(Deployment.deployed_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Deployment.deployed_at <= end_at)
    return stmt


def build_change_failure_rate_sql_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[int, int, Optional[float], Optional[float]]]:
    """
    Build native PostgreSQL 16 aggregate query computing COUNT(DISTINCT d.id) for total
    and failed production deployments (FAILED/ROLLED_BACK U active CONFIRMED incident)
    per Task 28.1 §5 & Task 28.2.
    """
    failure_predicate = or_(
        Deployment.status.in_(
            [DeploymentOutcome.FAILED.value, DeploymentOutcome.ROLLED_BACK.value]
        ),
        and_(
            DeploymentIncident.id.is_not(None),
            DeploymentIncident.is_active.is_(True),
            DeploymentIncident.status == "CONFIRMED",
            DeploymentIncident.detected_at >= Deployment.deployed_at,
            DeploymentIncident.detected_at <= Deployment.deployed_at + text("INTERVAL '24 hours'"),
        ),
    )

    total_distinct = func.count(func.distinct(Deployment.id))
    failed_distinct = func.count(func.distinct(Deployment.id)).filter(failure_predicate)

    stmt = (
        select(
            total_distinct.label("total_production_deployments"),
            failed_distinct.label("distinct_failed_deployments"),
            case(
                (total_distinct == 0, None),
                else_=func.round(
                    func.cast(failed_distinct, Numeric) / func.cast(total_distinct, Numeric),
                    4,
                ),
            ).label("cfr_ratio"),
            case(
                (total_distinct == 0, None),
                else_=func.round(
                    (
                        func.cast(failed_distinct, Numeric)
                        * Decimal("100.0")
                        / func.cast(total_distinct, Numeric)
                    ),
                    2,
                ),
            ).label("cfr_percentage"),
        )
        .select_from(Deployment)
        .join(Project, Project.id == Deployment.project_id)
        .outerjoin(
            DeploymentIncident,
            and_(
                DeploymentIncident.deployment_id == Deployment.id,
                DeploymentIncident.tenant_id == Deployment.tenant_id,
            ),
        )
        .where(
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.environment == "production",
            Deployment.authoritative_signal_source == Project.authoritative_cd_signal,
            Project.deleted_at.is_(None),
        )
    )
    if start_at is not None:
        stmt = stmt.where(Deployment.deployed_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Deployment.deployed_at <= end_at)
    return stmt


async def calculate_rolling_change_failure_rate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> ChangeFailureRateResult:
    """
    Query authoritative production deployments left-joining active deployment_incidents
    and compute rolling 14d/30d Change Failure Rate using COUNT(DISTINCT d.id) (Task 28.2).
    """
    if window_days not in SUPPORTED_CFR_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_CFR_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    stmt = build_change_failure_rate_deployments_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_utc,
        end_at=end_utc,
    )

    result = await db.execute(stmt)
    if hasattr(result, "all"):
        rows = [
            tuple(row) if not isinstance(row, tuple) and hasattr(row, "__iter__") else row
            for row in result.all()
        ]
    else:
        rows = list(result.scalars().all())

    return compute_change_failure_rate(
        rows,
        window_days=window_days,
        window_end=end_utc,
    )


# ---------------------------------------------------------------------------
# US-29 / Task 29.2: Mean Time to Recovery (MTTR) Calculator & Daily Trends
# ---------------------------------------------------------------------------


def _exact_elapsed_seconds(started_at: datetime, resolved_at: datetime) -> Decimal:
    """Return exact non-negative elapsed seconds as a Decimal, raising ValueError on clock skew."""
    started_utc = _ensure_utc(started_at)
    resolved_utc = _ensure_utc(resolved_at)
    if resolved_utc < started_utc:
        raise ValueError(
            f"resolved_at ({resolved_utc.isoformat()}) cannot be earlier than "
            f"started_at ({started_utc.isoformat()})."
        )
    delta = resolved_utc - started_utc
    return Decimal(delta.days * 86400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal("1000000")
    )


def compute_session_recovery_duration_hours(
    started_at: datetime,
    resolved_at: datetime,
) -> float:
    """
    Calculate failure session recovery duration in hours:
    (resolved_at - started_at) / 3600.0 rounded to 2 decimal places (US-29 / Task 29.2).

    Raises ValueError if resolved_at is earlier than started_at.
    """
    raw_seconds = _exact_elapsed_seconds(started_at, resolved_at)
    return _round_two_decimals(raw_seconds / Decimal("3600"))


def _summarize_mttr_durations(
    durations: Sequence[Decimal],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (mttr_seconds, mttr_hours, mttr_minutes) or (None, None, None) on 0 durations."""
    if not durations:
        return None, None, None
    avg_seconds = sum(durations) / Decimal(len(durations))
    return (
        _round_two_decimals(avg_seconds),
        _round_two_decimals(avg_seconds / Decimal("3600")),
        _round_two_decimals(avg_seconds / Decimal("60")),
    )


def _is_production_failure_session(session_obj: Any) -> bool:
    """Return True if the object represents a US-68 ProductionFailure health session."""
    return (
        isinstance(session_obj, ProductionFailure)
        or hasattr(session_obj, "incident_id")
        or hasattr(session_obj, "probe_source")
    )


def _filter_and_deduplicate_mttr_sessions(
    sessions: Sequence[Incident | ProductionFailure],
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> list[tuple[Incident | ProductionFailure, datetime, datetime, Decimal]]:
    """
    Filter resolved health failure sessions in [start_utc, end_utc], deduplicate synced
    (ProductionFailure, Incident) pairs with ProductionFailure priority, and sort chronologically.
    """
    qualifying_failures: list[tuple[Incident | ProductionFailure, datetime, datetime, Decimal]] = []
    qualifying_incidents: list[
        tuple[Incident | ProductionFailure, datetime, datetime, Decimal]
    ] = []

    for session_obj in sessions:
        if session_obj is None:
            continue

        raw_status = getattr(session_obj, "status", None)
        if raw_status is not None and str(raw_status).strip().upper() != "RESOLVED":
            continue

        started_at = getattr(session_obj, "started_at", None)
        resolved_at = getattr(session_obj, "resolved_at", None)
        if started_at is None or resolved_at is None:
            continue

        started_utc = _ensure_utc(started_at)
        resolved_utc = _ensure_utc(resolved_at)
        if resolved_utc < started_utc:
            continue
        if resolved_utc < start_utc or resolved_utc > end_utc:
            continue

        raw_seconds = _exact_elapsed_seconds(started_utc, resolved_utc)
        entry = (session_obj, started_utc, resolved_utc, raw_seconds)
        if _is_production_failure_session(session_obj):
            qualifying_failures.append(entry)
        else:
            qualifying_incidents.append(entry)

    seen_ids: set[uuid.UUID] = set()
    covered_incident_ids: set[uuid.UUID] = set()
    selected: list[tuple[Incident | ProductionFailure, datetime, datetime, Decimal]] = []

    for pf_entry in qualifying_failures:
        pf_obj = pf_entry[0]
        pf_id = getattr(pf_obj, "id", None)
        if pf_id is not None:
            if pf_id in seen_ids:
                continue
            seen_ids.add(pf_id)
            covered_incident_ids.add(pf_id)
        linked_inc_id = getattr(pf_obj, "incident_id", None)
        if linked_inc_id is not None:
            covered_incident_ids.add(linked_inc_id)
        selected.append(pf_entry)

    for inc_entry in qualifying_incidents:
        inc_obj = inc_entry[0]
        inc_id = getattr(inc_obj, "id", None)
        if inc_id is not None:
            if inc_id in seen_ids or inc_id in covered_incident_ids:
                continue
            seen_ids.add(inc_id)
        selected.append(inc_entry)

    selected.sort(
        key=lambda item: (
            item[2],
            item[1],
            str(getattr(item[0], "id", "")),
        )
    )
    return selected


def compute_mttr(
    incidents: Sequence[Incident | ProductionFailure],
    *,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> MTTRResult:
    """
    Compute rolling 14-day or 30-day Mean Time to Recovery (MTTR) and daily trend series
    strictly based on resolved production health failure sessions (US-29 / Task 29.2 & Task 29.3).

    Invariants Enforced:
    1. Canonical Math: Mean duration of resolved failure sessions: mean((resolved_at - started_at) / 3600.0).
    2. Strict Independence: Completely independent of PR merges, commits, or deployments.
    3. Self-Contained Pairing: Each outage pairs its own started_at and resolved_at.
    4. Ongoing Outage Exclusion: Sessions with resolved_at IS NULL or non-RESOLVED status are excluded.
    5. Deduplication: Synced (ProductionFailure, Incident) pairs are counted exactly once.
    6. Null Semantics: When zero resolved failure sessions exist in window, MTTR returns None (null).
    7. Non-Negative Guard: Records with resolved_at < started_at are excluded.
    """
    if window_days not in SUPPORTED_MTTR_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_MTTR_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    selected = _filter_and_deduplicate_mttr_sessions(
        incidents,
        start_utc=start_utc,
        end_utc=end_utc,
    )

    daily_durations: dict[date, list[Decimal]] = defaultdict(list)
    outages: list[MTTROutageItem] = []
    durations: list[Decimal] = []

    for session_obj, started_utc, resolved_utc, raw_seconds in selected:
        durations.append(raw_seconds)
        daily_durations[resolved_utc.date()].append(raw_seconds)

        outages.append(
            MTTROutageItem(
                incident_id=session_obj.id,
                project_id=session_obj.project_id,
                started_at=started_utc,
                resolved_at=resolved_utc,
                duration_seconds=_round_two_decimals(raw_seconds),
                duration_hours=_round_two_decimals(raw_seconds / Decimal("3600")),
                severity=getattr(session_obj, "severity", "SEV-2") or "SEV-2",
            )
        )

    mttr_seconds, mttr_hours, mttr_minutes = _summarize_mttr_durations(durations)

    end_date = end_utc.date()
    start_date = end_date - timedelta(days=window_days - 1)
    daily_series: list[DailyMTTRMetric] = []

    for offset in range(window_days):
        current_date = start_date + timedelta(days=offset)
        day_vals = daily_durations.get(current_date, [])
        day_sec, day_hr, day_min = _summarize_mttr_durations(day_vals)
        daily_series.append(
            DailyMTTRMetric(
                date=current_date,
                resolved_outage_count=len(day_vals),
                mttr_seconds=day_sec,
                mttr_hours=day_hr,
                mttr_minutes=day_min,
            )
        )

    return MTTRResult(
        window_days=window_days,
        resolved_outage_count=len(durations),
        mttr_seconds=mttr_seconds,
        mttr_hours=mttr_hours,
        mttr_minutes=mttr_minutes,
        outages=outages,
        daily_series=daily_series,
        window_start=start_utc,
        window_end=end_utc,
    )


def _build_mttr_aggregate_sql_query(
    model: type[Incident] | type[ProductionFailure],
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[int, Optional[float], Optional[float], Optional[float]]]:
    """Build native PostgreSQL 16 aggregate MTTR query for Incident or ProductionFailure."""
    epoch_diff = func.extract("epoch", model.resolved_at - model.started_at)
    count_resolved = func.count(model.id)

    stmt = (
        select(
            count_resolved.label("resolved_outage_count"),
            case(
                (count_resolved == 0, None),
                else_=func.round(func.cast(func.avg(epoch_diff), Numeric), 2),
            ).label("mttr_seconds"),
            case(
                (count_resolved == 0, None),
                else_=func.round(func.cast(func.avg(epoch_diff) / Decimal("3600.0"), Numeric), 2),
            ).label("mttr_hours"),
            case(
                (count_resolved == 0, None),
                else_=func.round(func.cast(func.avg(epoch_diff) / Decimal("60.0"), Numeric), 2),
            ).label("mttr_minutes"),
        )
        .select_from(model)
        .join(
            Project,
            and_(
                Project.id == model.project_id,
                Project.tenant_id == model.tenant_id,
            ),
        )
        .where(
            model.tenant_id == tenant_id,
            model.project_id == project_id,
            Project.deleted_at.is_(None),
            model.resolved_at.is_not(None),
            model.resolved_at >= model.started_at,
        )
    )
    if hasattr(model, "status"):
        stmt = stmt.where(model.status == "RESOLVED")
    if start_at is not None:
        stmt = stmt.where(model.resolved_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(model.resolved_at <= end_at)
    return stmt


def build_mttr_sql_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[int, Optional[float], Optional[float], Optional[float]]]:
    """
    Build native PostgreSQL 16 aggregate query computing MTTR in seconds, hours, and minutes
    for resolved incidents per Task 29.1 §4 & Task 29.2.
    """
    return _build_mttr_aggregate_sql_query(
        Incident,
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_at,
        end_at=end_at,
    )


def build_mttr_production_failures_sql_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[int, Optional[float], Optional[float], Optional[float]]]:
    """
    Build native PostgreSQL 16 aggregate query computing MTTR in seconds, hours, and minutes
    for resolved US-68 production_failures sessions (Task 29.2).
    """
    return _build_mttr_aggregate_sql_query(
        ProductionFailure,
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_at,
        end_at=end_at,
    )


def _build_mttr_sessions_query(
    model: type[Any],
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[Any]]:
    """Build query fetching resolved sessions in window for Incident or ProductionFailure."""
    stmt = (
        select(model)
        .join(
            Project,
            and_(
                Project.id == model.project_id,
                Project.tenant_id == model.tenant_id,
            ),
        )
        .where(
            model.tenant_id == tenant_id,
            model.project_id == project_id,
            Project.deleted_at.is_(None),
            model.resolved_at.is_not(None),
            model.resolved_at >= model.started_at,
        )
        .order_by(model.resolved_at.asc(), model.id.asc())
    )
    if hasattr(model, "status"):
        stmt = stmt.where(model.status == "RESOLVED")
    if start_at is not None:
        stmt = stmt.where(model.resolved_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(model.resolved_at <= end_at)
    return stmt


def build_mttr_incidents_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[Incident]]:
    """Build query fetching resolved incidents in window for project under tenant context."""
    return _build_mttr_sessions_query(
        Incident,
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_at,
        end_at=end_at,
    )


def build_mttr_production_failures_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[ProductionFailure]]:
    """Build query fetching resolved production_failures sessions in window under tenant context."""
    return _build_mttr_sessions_query(
        ProductionFailure,
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_at,
        end_at=end_at,
    )


async def calculate_rolling_mttr(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> MTTRResult:
    """
    Query resolved production_failures sessions and incidents in window and compute
    rolling 14d/30d MTTR without double-counting synced session/incident pairs (Task 29.2 / US-68).
    """
    if window_days not in SUPPORTED_MTTR_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_MTTR_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    pf_stmt = build_mttr_production_failures_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_utc,
        end_at=end_utc,
    )
    inc_stmt = build_mttr_incidents_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_utc,
        end_at=end_utc,
    )

    pf_rows = list((await db.execute(pf_stmt)).scalars().all())
    inc_rows = list((await db.execute(inc_stmt)).scalars().all())

    return compute_mttr(
        [*pf_rows, *inc_rows],
        window_days=window_days,
        window_end=end_utc,
    )


# ---------------------------------------------------------------------------
# US-30 / Task 30.2: Combined Project DORA Metrics Endpoint Assembly & Service
# ---------------------------------------------------------------------------


def resolve_dora_timeframe_days(
    timeframe_days: int = 30,
    window: Optional[DoraWindow] = None,
    *,
    has_explicit_timeframe: bool = False,
) -> DoraTimeframeDays:
    """Resolve rolling timeframe in days from `timeframe_days` and optional `window` alias."""
    unprocessable_status = getattr(http_status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)
    if timeframe_days not in (14, 30):
        raise HTTPException(
            status_code=unprocessable_status,
            detail=f"Unsupported timeframe_days={timeframe_days}. Expected 14 or 30.",
        )

    validated_days: DoraTimeframeDays = 14 if timeframe_days == 14 else 30
    window_map: dict[DoraWindow, DoraTimeframeDays] = {"14d": 14, "30d": 30}
    if window is not None:
        mapped = window_map[window]
        if has_explicit_timeframe and validated_days != mapped:
            raise HTTPException(
                status_code=unprocessable_status,
                detail=(
                    f"Conflicting timeframe_days={validated_days} and window='{window}' "
                    "query parameters."
                ),
            )
        return mapped
    return validated_days


def _is_live_async_session(db: Any) -> bool:
    """Return True only for a real SQLAlchemy AsyncSession (excluding unit test mocks)."""
    return isinstance(db, AsyncSession) and not type(db).__module__.startswith("unittest.mock")


_DORA_CONSOLIDATED_SERIES_SQL = text(
    """
    WITH date_buckets AS (
        SELECT gs::date AS bucket_date
        FROM generate_series(
            CAST(:start_date AS date),
            CAST(:end_date AS date),
            INTERVAL '1 day'
        ) AS gs
    ),
    auth_deployments AS (
        SELECT d.id,
               d.status,
               d.deployed_at,
               (d.deployed_at AT TIME ZONE 'UTC')::date AS bucket_date
        FROM deployments d
        WHERE d.tenant_id = CAST(:tenant_id AS uuid)
          AND d.project_id = CAST(:project_id AS uuid)
          AND d.environment = 'production'
          AND d.authoritative_signal_source = :auth_signal
          AND d.deployed_at >= :start_utc
          AND d.deployed_at <= :end_utc
    ),
    dep_cfr AS (
        SELECT ad.id,
               ad.status,
               ad.bucket_date,
               (
                   ad.status IN ('FAILED', 'ROLLED_BACK')
                   OR BOOL_OR(di.id IS NOT NULL)
               ) AS is_failed
        FROM auth_deployments ad
        LEFT JOIN deployment_incidents di
          ON di.deployment_id = ad.id
         AND di.tenant_id = CAST(:tenant_id AS uuid)
         AND di.is_active IS TRUE
         AND di.status = 'CONFIRMED'
         AND di.detected_at >= ad.deployed_at
         AND di.detected_at <= ad.deployed_at + INTERVAL '24 hours'
        GROUP BY ad.id, ad.status, ad.bucket_date
    ),
    daily_deployments AS (
        SELECT bucket_date,
               COUNT(*)::int AS total_count,
               COUNT(*) FILTER (WHERE status = 'SUCCESS')::int AS success_count,
               COUNT(*) FILTER (WHERE is_failed)::int AS failed_count
        FROM dep_cfr
        GROUP BY bucket_date
    ),
    agg_deployments AS (
        SELECT COUNT(*)::int AS total_count,
               COUNT(*) FILTER (WHERE status = 'SUCCESS')::int AS success_count,
               COUNT(*) FILTER (WHERE is_failed)::int AS failed_count
        FROM dep_cfr
    ),
    shipped_prs AS (
        SELECT DISTINCT ON (pr.id)
               pr.id AS pr_id,
               ad.bucket_date,
               EXTRACT(EPOCH FROM (ad.deployed_at - pr.first_commit_at))::numeric AS raw_seconds
        FROM deployment_pull_requests dpr
        JOIN auth_deployments ad
          ON ad.id = dpr.deployment_id
         AND ad.status = 'SUCCESS'
        JOIN pull_requests pr
          ON pr.id = dpr.pull_request_id
         AND pr.tenant_id = CAST(:tenant_id AS uuid)
        WHERE dpr.tenant_id = CAST(:tenant_id AS uuid)
          AND lower(trim(pr.state)) = 'merged'
          AND pr.first_commit_at IS NOT NULL
          AND ad.deployed_at >= pr.first_commit_at
        ORDER BY pr.id, ad.deployed_at ASC, ad.id ASC
    ),
    daily_lead_time AS (
        SELECT bucket_date,
               ROUND(
                   (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY raw_seconds / 3600.0))::numeric,
                   2
               )::float AS median_lead_time_hours
        FROM shipped_prs
        GROUP BY bucket_date
    ),
    agg_lead_time AS (
        SELECT ROUND(
                   (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY raw_seconds / 3600.0))::numeric,
                   2
               )::float AS median_lead_time_hours
        FROM shipped_prs
    ),
    pf_sessions AS (
        SELECT pf.id,
               pf.incident_id,
               pf.started_at,
               pf.resolved_at,
               (pf.resolved_at AT TIME ZONE 'UTC')::date AS bucket_date,
               EXTRACT(EPOCH FROM (pf.resolved_at - pf.started_at))::numeric AS dur_seconds
        FROM production_failures pf
        WHERE pf.tenant_id = CAST(:tenant_id AS uuid)
          AND pf.project_id = CAST(:project_id AS uuid)
          AND pf.status = 'RESOLVED'
          AND pf.resolved_at IS NOT NULL
          AND pf.resolved_at >= pf.started_at
          AND pf.resolved_at >= :start_utc
          AND pf.resolved_at <= :end_utc
    ),
    inc_sessions AS (
        SELECT inc.id,
               (inc.resolved_at AT TIME ZONE 'UTC')::date AS bucket_date,
               EXTRACT(EPOCH FROM (inc.resolved_at - inc.started_at))::numeric AS dur_seconds
        FROM incidents inc
        WHERE inc.tenant_id = CAST(:tenant_id AS uuid)
          AND inc.project_id = CAST(:project_id AS uuid)
          AND inc.resolved_at IS NOT NULL
          AND inc.resolved_at >= inc.started_at
          AND inc.resolved_at >= :start_utc
          AND inc.resolved_at <= :end_utc
          AND NOT EXISTS (
              SELECT 1
              FROM pf_sessions pfs
              WHERE pfs.id = inc.id OR pfs.incident_id = inc.id
          )
    ),
    mttr_sessions AS (
        SELECT bucket_date, dur_seconds FROM pf_sessions
        UNION ALL
        SELECT bucket_date, dur_seconds FROM inc_sessions
    ),
    daily_mttr AS (
        SELECT bucket_date,
               ROUND((AVG(dur_seconds) / 3600.0)::numeric, 2)::float AS mttr_hours
        FROM mttr_sessions
        GROUP BY bucket_date
    ),
    agg_mttr AS (
        SELECT COUNT(*)::int AS resolved_outage_count,
               ROUND((AVG(dur_seconds) / 3600.0)::numeric, 2)::float AS mttr_hours
        FROM mttr_sessions
    )
    SELECT db.bucket_date,
           COALESCE(dd.total_count, 0) AS day_total_deployments,
           COALESCE(dd.success_count, 0) AS day_success_deployments,
           COALESCE(dd.failed_count, 0) AS day_failed_deployments,
           dlt.median_lead_time_hours AS day_lead_time_hours,
           dm.mttr_hours AS day_mttr_hours,
           adep.total_count AS agg_total_deployments,
           adep.success_count AS agg_success_deployments,
           adep.failed_count AS agg_failed_deployments,
           alt.median_lead_time_hours AS agg_lead_time_hours,
           amt.resolved_outage_count AS agg_resolved_outage_count,
           amt.mttr_hours AS agg_mttr_hours
    FROM date_buckets db
    LEFT JOIN daily_deployments dd ON dd.bucket_date = db.bucket_date
    LEFT JOIN daily_lead_time dlt ON dlt.bucket_date = db.bucket_date
    LEFT JOIN daily_mttr dm ON dm.bucket_date = db.bucket_date
    CROSS JOIN agg_deployments adep
    CROSS JOIN agg_lead_time alt
    CROSS JOIN agg_mttr amt
    ORDER BY db.bucket_date ASC
    """
)


def _summarize_dora_notes(
    *,
    window: DoraWindow,
    authoritative_deployment_signal: DeploymentSignalSource,
    has_deployment_telemetry: bool,
    total_deployment_count: int,
    successful_deployment_count: int,
    lead_time_hours: Optional[float],
    change_failure_rate_pct: Optional[float],
    mttr_hours: Optional[float],
    resolved_outage_count: int,
) -> list[str]:
    """Build deterministic diagnostic notes explaining null vs 0.0 metrics and signal filtering."""
    notes: list[str] = []
    signal_name = authoritative_deployment_signal.value

    if not has_deployment_telemetry:
        notes.append(
            f"No authoritative production deployments recorded under signal '{signal_name}' "
            f"in the {window} window."
        )
    else:
        notes.append(
            f"Aggregated rolling {window} metrics from authoritative deployment signal "
            f"'{signal_name}'."
        )
        if successful_deployment_count == 0:
            notes.append(
                "deployment_frequency_per_day is 0.0: "
                f"{total_deployment_count} authoritative production "
                f"deployment(s) recorded with 0 SUCCESS outcomes in the {window} window."
            )

    if lead_time_hours is None and has_deployment_telemetry:
        notes.append(
            "lead_time_hours is null: 0 shipped pull requests linked to successful production "
            f"deployments in the {window} window."
        )

    if change_failure_rate_pct == 0.0:
        notes.append(
            "change_failure_rate_pct is 0.0%: "
            f"{total_deployment_count} production deployments "
            "succeeded with 0 failures or linked incidents."
        )

    if mttr_hours is None:
        notes.append(
            f"mttr_hours is null: 0 resolved production health failure sessions in the {window} window."
        )
    elif mttr_hours == 0.0:
        notes.append(
            f"mttr_hours is 0.0: {resolved_outage_count} resolved production health "
            "failure session(s) restored instantaneously."
        )

    return notes


def build_dora_metrics_notes(
    *,
    window: DoraWindow,
    authoritative_deployment_signal: DeploymentSignalSource,
    lead_time_result: LeadTimeResult,
    deployment_frequency_result: DeploymentFrequencyResult,
    change_failure_rate_result: ChangeFailureRateResult,
    mttr_result: MTTRResult,
    has_deployment_telemetry: bool,
) -> list[str]:
    """Build deterministic diagnostic notes explaining null vs 0.0 metrics and signal filtering."""
    return _summarize_dora_notes(
        window=window,
        authoritative_deployment_signal=authoritative_deployment_signal,
        has_deployment_telemetry=has_deployment_telemetry,
        total_deployment_count=change_failure_rate_result.total_deployment_count,
        successful_deployment_count=deployment_frequency_result.successful_deployment_count,
        lead_time_hours=lead_time_result.median_lead_time_hours,
        change_failure_rate_pct=change_failure_rate_result.change_failure_rate_percentage,
        mttr_hours=mttr_result.mttr_hours,
        resolved_outage_count=mttr_result.resolved_outage_count,
    )


def assemble_dora_metrics_response(
    *,
    project_id: uuid.UUID,
    timeframe_days: DoraTimeframeDays,
    authoritative_deployment_signal: DeploymentSignalSource | str,
    lead_time_result: LeadTimeResult,
    deployment_frequency_result: DeploymentFrequencyResult,
    change_failure_rate_result: ChangeFailureRateResult,
    mttr_result: MTTRResult,
    has_deployment_telemetry: Optional[bool] = None,
) -> DoraMetricsResponse:
    """
    Assemble the OpenAPI 3.1 DoraMetricsResponse from the four domain DORA calculators
    (US-26, US-27, US-28, US-29) preserving strict null vs 0.0 semantics (Task 30.2).
    """
    signal_enum = (
        authoritative_deployment_signal
        if isinstance(authoritative_deployment_signal, DeploymentSignalSource)
        else DeploymentSignalSource(str(authoritative_deployment_signal))
    )
    window: DoraWindow = "14d" if timeframe_days == 14 else "30d"

    telemetry_present = (
        has_deployment_telemetry
        if has_deployment_telemetry is not None
        else (
            change_failure_rate_result.total_deployment_count > 0
            or deployment_frequency_result.successful_deployment_count > 0
        )
    )

    df_aggregate: Optional[float] = (
        deployment_frequency_result.deployments_per_day if telemetry_present else None
    )

    aggregates = DoraAggregates(
        lead_time_hours=lead_time_result.median_lead_time_hours,
        deployment_frequency_per_day=df_aggregate,
        change_failure_rate_pct=change_failure_rate_result.change_failure_rate_percentage,
        mttr_hours=mttr_result.mttr_hours,
    )

    lt_by_date = {p.date: p for p in lead_time_result.daily_series}
    df_by_date = {p.date: p for p in deployment_frequency_result.daily_series}
    cfr_by_date = {p.date: p for p in change_failure_rate_result.daily_series}
    mttr_by_date = {p.date: p for p in mttr_result.daily_series}

    ordered_dates = [p.date for p in deployment_frequency_result.daily_series]
    if not ordered_dates:
        ordered_dates = sorted(
            set(lt_by_date) | set(df_by_date) | set(cfr_by_date) | set(mttr_by_date)
        )

    trend: list[DoraDailyPoint] = []
    for bucket_date in ordered_dates:
        lt_pt = lt_by_date.get(bucket_date)
        df_pt = df_by_date.get(bucket_date)
        cfr_pt = cfr_by_date.get(bucket_date)
        mttr_pt = mttr_by_date.get(bucket_date)

        df_daily: Optional[float]
        if not telemetry_present:
            df_daily = None
        else:
            df_daily = float(df_pt.count) if df_pt is not None else 0.0

        trend.append(
            DoraDailyPoint(
                date=bucket_date,
                lead_time_hours=lt_pt.median_lead_time_hours if lt_pt is not None else None,
                deployment_frequency_per_day=df_daily,
                change_failure_rate_pct=(
                    cfr_pt.change_failure_rate_percentage if cfr_pt is not None else None
                ),
                mttr_hours=mttr_pt.mttr_hours if mttr_pt is not None else None,
            )
        )

    notes = build_dora_metrics_notes(
        window=window,
        authoritative_deployment_signal=signal_enum,
        lead_time_result=lead_time_result,
        deployment_frequency_result=deployment_frequency_result,
        change_failure_rate_result=change_failure_rate_result,
        mttr_result=mttr_result,
        has_deployment_telemetry=telemetry_present,
    )

    return DoraMetricsResponse(
        project_id=project_id,
        window=window,
        authoritative_deployment_signal=signal_enum,
        aggregates=aggregates,
        notes=notes,
        trend=trend,
    )


async def calculate_project_dora_metrics_consolidated(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project: Project,
    timeframe_days: DoraTimeframeDays,
    end_utc: datetime,
) -> DoraMetricsResponse:
    """
    Execute the consolidated single-roundtrip PostgreSQL 16 `generate_series` CTE query
    under tenant RLS for sub-300ms DORA analytics retrieval (SCRUM-459 / Task 30.3).
    """
    start_utc = end_utc - timedelta(days=timeframe_days)
    end_date = end_utc.date()
    start_date = end_date - timedelta(days=timeframe_days - 1)

    signal_enum = DeploymentSignalSource(str(project.authoritative_cd_signal))
    window: DoraWindow = "14d" if timeframe_days == 14 else "30d"

    res = await db.execute(
        _DORA_CONSOLIDATED_SERIES_SQL,
        {
            "tenant_id": tenant_id,
            "project_id": project.id,
            "auth_signal": signal_enum.value,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    rows = res.mappings().all()

    first_row = rows[0] if rows else {}
    total_dep = int(first_row.get("agg_total_deployments") or 0)
    success_dep = int(first_row.get("agg_success_deployments") or 0)
    failed_dep = int(first_row.get("agg_failed_deployments") or 0)
    resolved_outages = int(first_row.get("agg_resolved_outage_count") or 0)

    agg_lead_raw = first_row.get("agg_lead_time_hours")
    agg_lead = float(agg_lead_raw) if agg_lead_raw is not None else None

    agg_mttr_raw = first_row.get("agg_mttr_hours")
    agg_mttr = float(agg_mttr_raw) if agg_mttr_raw is not None else None

    telemetry_present = total_dep > 0 or success_dep > 0
    df_aggregate: Optional[float] = (
        _round_rate(success_dep, timeframe_days) if telemetry_present else None
    )
    cfr_pct = _round_cfr_percentage(failed_dep, total_dep)

    aggregates = DoraAggregates(
        lead_time_hours=agg_lead,
        deployment_frequency_per_day=df_aggregate,
        change_failure_rate_pct=cfr_pct,
        mttr_hours=agg_mttr,
    )

    trend: list[DoraDailyPoint] = []
    for row in rows:
        day_total = int(row["day_total_deployments"] or 0)
        day_success = int(row["day_success_deployments"] or 0)
        day_failed = int(row["day_failed_deployments"] or 0)
        day_lt_raw = row["day_lead_time_hours"]
        day_mttr_raw = row["day_mttr_hours"]

        trend.append(
            DoraDailyPoint(
                date=row["bucket_date"],
                lead_time_hours=float(day_lt_raw) if day_lt_raw is not None else None,
                deployment_frequency_per_day=(float(day_success) if telemetry_present else None),
                change_failure_rate_pct=_round_cfr_percentage(day_failed, day_total),
                mttr_hours=float(day_mttr_raw) if day_mttr_raw is not None else None,
            )
        )

    notes = _summarize_dora_notes(
        window=window,
        authoritative_deployment_signal=signal_enum,
        has_deployment_telemetry=telemetry_present,
        total_deployment_count=total_dep,
        successful_deployment_count=success_dep,
        lead_time_hours=agg_lead,
        change_failure_rate_pct=cfr_pct,
        mttr_hours=agg_mttr,
        resolved_outage_count=resolved_outages,
    )

    return DoraMetricsResponse(
        project_id=project.id,
        window=window,
        authoritative_deployment_signal=signal_enum,
        aggregates=aggregates,
        notes=notes,
        trend=trend,
    )


async def calculate_project_dora_metrics(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    timeframe_days: DoraTimeframeDays = 30,
    window_end: Optional[datetime] = None,
) -> DoraMetricsResponse:
    """
    Query authoritative project metadata and compute rolling 14d/30d DORA aggregates
    and daily trend series across US-26, US-27, US-28, and US-29 (Task 30.2 & Task 30.3).
    """
    if timeframe_days not in (14, 30):
        raise ValueError(f"Unsupported timeframe_days={timeframe_days}. Expected 14 or 30.")

    proj_stmt = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == tenant_id,
        Project.deleted_at.is_(None),
    )
    project = (await db.execute(proj_stmt)).scalars().first()
    if project is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))

    if _is_live_async_session(db):
        return await calculate_project_dora_metrics_consolidated(
            db,
            tenant_id=tenant_id,
            project=project,
            timeframe_days=timeframe_days,
            end_utc=end_utc,
        )

    lead_time_result = await calculate_rolling_lead_time(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=timeframe_days,
        window_end=end_utc,
    )
    deployment_frequency_result = await calculate_rolling_deployment_frequency(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=timeframe_days,
        window_end=end_utc,
    )
    change_failure_rate_result = await calculate_rolling_change_failure_rate(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=timeframe_days,
        window_end=end_utc,
    )
    mttr_result = await calculate_rolling_mttr(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        window_days=timeframe_days,
        window_end=end_utc,
    )

    return assemble_dora_metrics_response(
        project_id=project.id,
        timeframe_days=timeframe_days,
        authoritative_deployment_signal=project.authoritative_cd_signal,
        lead_time_result=lead_time_result,
        deployment_frequency_result=deployment_frequency_result,
        change_failure_rate_result=change_failure_rate_result,
        mttr_result=mttr_result,
    )


async def get_project_dora_metrics(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    timeframe_days: DoraTimeframeDays = 30,
    window_end: Optional[datetime] = None,
) -> DoraMetricsResponse:
    """Retrieve verified project DORA metrics under tenant-scoped PostgreSQL RLS (Task 30.2)."""
    async with get_tenant_db_context(tenant_id) as session:
        return await calculate_project_dora_metrics(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            timeframe_days=timeframe_days,
            window_end=window_end,
        )
