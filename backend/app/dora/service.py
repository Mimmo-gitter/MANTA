"""Backend DORA metrics calculation services (US-26 / Task 26.3, US-27 / Task 27.2, US-28 / Task 28.2 & US-29 / Task 29.2)."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Optional, Sequence
import uuid

from sqlalchemy import Numeric, Select, and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import (
    Deployment,
    DeploymentIncident,
    DeploymentPullRequest,
    Incident,
    Project,
    PullRequest,
)
from app.deployments.schemas import DeploymentOutcome
from app.deployments.service import build_authoritative_production_deployments_query
from app.dora.schemas import (
    ChangeFailureRateResult,
    DailyChangeFailureRateMetric,
    DailyDeploymentCount,
    DailyLeadTimeMetric,
    DailyMTTRMetric,
    DeploymentFrequencyResult,
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


def compute_mttr(
    incidents: Sequence[Incident],
    *,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> MTTRResult:
    """
    Compute rolling 14-day or 30-day Mean Time to Recovery (MTTR) and daily trend series
    strictly based on resolved production health failure sessions (US-29 / Task 29.2 & Task 29.3).

    Invariants Enforced:
    1. Canonical Math: Mean duration of resolved failure sessions: mean(resolved_at - started_at).
    2. Strict Independence: Completely independent of PR merges, commits, or deployments.
    3. Self-Contained Pairing: Each outage pairs its own started_at and resolved_at.
    4. Ongoing Outage Exclusion: Incidents with resolved_at IS NULL are strictly excluded.
    5. Null Semantics: When zero resolved failure sessions exist in window, MTTR returns None (null).
    6. Non-Negative Guard: Records with resolved_at < started_at are excluded.
    """
    if window_days not in SUPPORTED_MTTR_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_MTTR_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    daily_durations: dict[date, list[float]] = defaultdict(list)
    outages: list[MTTROutageItem] = []
    durations: list[float] = []

    for inc in incidents:
        if inc.resolved_at is None:
            continue

        started_utc = _ensure_utc(inc.started_at)
        resolved_utc = _ensure_utc(inc.resolved_at)

        if resolved_utc < started_utc:
            continue

        if resolved_utc < start_utc or resolved_utc > end_utc:
            continue

        delta_seconds = (resolved_utc - started_utc).total_seconds()
        durations.append(delta_seconds)
        daily_durations[resolved_utc.date()].append(delta_seconds)

        outages.append(
            MTTROutageItem(
                incident_id=inc.id,
                project_id=inc.project_id,
                started_at=started_utc,
                resolved_at=resolved_utc,
                duration_seconds=_round_two_decimals(delta_seconds),
                duration_hours=_round_two_decimals(delta_seconds / 3600.0),
                severity=getattr(inc, "severity", "SEV-2") or "SEV-2",
            )
        )

    resolved_count = len(durations)
    if resolved_count == 0:
        mttr_seconds = None
        mttr_hours = None
        mttr_minutes = None
    else:
        avg_seconds = sum(durations) / resolved_count
        mttr_seconds = _round_two_decimals(avg_seconds)
        mttr_hours = _round_two_decimals(avg_seconds / 3600.0)
        mttr_minutes = _round_two_decimals(avg_seconds / 60.0)

    end_date = end_utc.date()
    start_date = end_date - timedelta(days=window_days - 1)
    daily_series: list[DailyMTTRMetric] = []

    for offset in range(window_days):
        current_date = start_date + timedelta(days=offset)
        day_vals = daily_durations.get(current_date, [])
        if not day_vals:
            daily_series.append(
                DailyMTTRMetric(
                    date=current_date,
                    resolved_outage_count=0,
                    mttr_seconds=None,
                    mttr_hours=None,
                    mttr_minutes=None,
                )
            )
        else:
            day_avg = sum(day_vals) / len(day_vals)
            daily_series.append(
                DailyMTTRMetric(
                    date=current_date,
                    resolved_outage_count=len(day_vals),
                    mttr_seconds=_round_two_decimals(day_avg),
                    mttr_hours=_round_two_decimals(day_avg / 3600.0),
                    mttr_minutes=_round_two_decimals(day_avg / 60.0),
                )
            )

    return MTTRResult(
        window_days=window_days,
        resolved_outage_count=resolved_count,
        mttr_seconds=mttr_seconds,
        mttr_hours=mttr_hours,
        mttr_minutes=mttr_minutes,
        outages=outages,
        daily_series=daily_series,
        window_start=start_utc,
        window_end=end_utc,
    )


def build_mttr_sql_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[int, Optional[float], Optional[float], Optional[float]]]:
    """
    Build native PostgreSQL 16 aggregate query computing MTTR in seconds, hours, and minutes
    for resolved health failure sessions per Task 29.1 §4 & Task 29.2.
    """
    epoch_diff = func.extract(
        text("epoch"), Incident.resolved_at - Incident.started_at
    )
    count_resolved = func.count(Incident.id)

    stmt = (
        select(
            count_resolved.label("resolved_outage_count"),
            case(
                (count_resolved == 0, None),
                else_=func.round(func.cast(func.avg(epoch_diff), Numeric), 2),
            ).label("mttr_seconds"),
            case(
                (count_resolved == 0, None),
                else_=func.round(
                    func.cast(func.avg(epoch_diff) / Decimal("3600.0"), Numeric), 2
                ),
            ).label("mttr_hours"),
            case(
                (count_resolved == 0, None),
                else_=func.round(
                    func.cast(func.avg(epoch_diff) / Decimal("60.0"), Numeric), 2
                ),
            ).label("mttr_minutes"),
        )
        .select_from(Incident)
        .join(
            Project,
            and_(
                Project.id == Incident.project_id,
                Project.tenant_id == Incident.tenant_id,
            ),
        )
        .where(
            Incident.tenant_id == tenant_id,
            Incident.project_id == project_id,
            Project.deleted_at.is_(None),
            Incident.resolved_at.is_not(None),
            Incident.resolved_at >= Incident.started_at,
        )
    )
    if start_at is not None:
        stmt = stmt.where(Incident.resolved_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Incident.resolved_at <= end_at)
    return stmt


def build_mttr_incidents_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[Incident]]:
    """Build query fetching resolved incidents in window for project under tenant context."""
    stmt = (
        select(Incident)
        .join(
            Project,
            and_(
                Project.id == Incident.project_id,
                Project.tenant_id == Incident.tenant_id,
            ),
        )
        .where(
            Incident.tenant_id == tenant_id,
            Incident.project_id == project_id,
            Project.deleted_at.is_(None),
            Incident.resolved_at.is_not(None),
            Incident.resolved_at >= Incident.started_at,
        )
        .order_by(Incident.resolved_at.asc(), Incident.id.asc())
    )
    if start_at is not None:
        stmt = stmt.where(Incident.resolved_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Incident.resolved_at <= end_at)
    return stmt


async def calculate_rolling_mttr(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    window_days: Literal[14, 30] = 14,
    window_end: Optional[datetime] = None,
) -> MTTRResult:
    """
    Query resolved production incidents in window and compute rolling 14d/30d MTTR (Task 29.2).
    """
    if window_days not in SUPPORTED_MTTR_WINDOWS:
        raise ValueError(
            f"Unsupported window_days={window_days}. Expected one of {SUPPORTED_MTTR_WINDOWS}."
        )

    end_utc = _ensure_utc(window_end or datetime.now(timezone.utc))
    start_utc = end_utc - timedelta(days=window_days)

    stmt = build_mttr_incidents_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_utc,
        end_at=end_utc,
    )

    result = await db.execute(stmt)
    incidents = list(result.scalars().all())

    return compute_mttr(
        incidents,
        window_days=window_days,
        window_end=end_utc,
    )

