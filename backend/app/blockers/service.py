"""
TASK-307-1 / SCRUM-543 (STORY-307 — US-71):
Tenant-Scoped Operational Blocker Detection & Atomic Risk + Outbox Service.

Implements the canonical detection, deduplication, and state-reset rules specified in
`docs/blocker-detection-stale-pr-and-incident-spike-bdd-specs.md`:
1. Stale PR detection (`STALE_PR`):
   - Flags open PRs idle strictly longer than `Project.pr_stale_threshold_hours`
     (default 48h), evaluating max activity across PR timestamps, linked commits,
     and PR reviews.
   - Preserves `PullRequest.updated_at` when setting `is_stale = True` so ORM
     `onupdate` defaults never mask staleness on subsequent sweeps.
   - Atomically upserts 1 `Risk` + 1 `OutboxEvent(event_type="risk.detected")`
     using PostgreSQL `INSERT ... ON CONFLICT ... WHERE status IN ('OPEN', 'ACKNOWLEDGED')
     DO NOTHING RETURNING id`, suppressing duplicates across repeated runs and
     concurrent worker sweeps.
   - Cleanly resets `is_stale = False` and transitions active `STALE_PR` risks to
     `RESOLVED` on new commit/review activity or PR closure/merge.
2. Incident Spike detection (`INCIDENT_SPIKE`):
   - Flags active projects with `>= 3` incidents in the rolling 60-minute window
     `[now - 60m, now]` with atomic `Risk` + `OutboxEvent` commit.
   - Applies anti-flapping hysteresis when resetting active `INCIDENT_SPIKE` risks:
     resolves iff `incident_count == 0` OR (`incident_count < 3` AND
     `now - risk.created_at >= 60 minutes`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
from typing import Any, Optional
import uuid

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import (
    Commit,
    Incident,
    OutboxEvent,
    Project,
    PullRequest,
    PullRequestReview,
    Risk,
)
from app.outbox.service import record_outbox_event

logger = logging.getLogger("app.blockers.service")

DEFAULT_PR_STALE_THRESHOLD_HOURS = 48
INCIDENT_SPIKE_WINDOW_MINUTES = 60
INCIDENT_SPIKE_THRESHOLD = 3
RISK_DETECTED_EVENT_TYPE = "risk.detected"


class RiskType(str, Enum):
    STALE_PR = "STALE_PR"
    INCIDENT_SPIKE = "INCIDENT_SPIKE"


class RiskSeverity(str, Enum):
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class RiskSubjectType(str, Enum):
    PULL_REQUEST = "pull_request"
    PROJECT = "project"


ACTIVE_RISK_STATUSES: tuple[str, str] = (
    RiskStatus.OPEN.value,
    RiskStatus.ACKNOWLEDGED.value,
)
_ACTIVE_RISK_INDEX_WHERE = text("status IN ('OPEN', 'ACKNOWLEDGED')")


@dataclass
class StalePrDetectionStats:
    """Per-tenant telemetry for a single stale PR detection pass."""

    projects_evaluated: int = 0
    prs_evaluated: int = 0
    prs_flagged_stale: int = 0
    risks_created: int = 0
    outbox_events_staged: int = 0
    duplicates_suppressed: int = 0
    prs_reset: int = 0
    risks_resolved: int = 0


@dataclass
class IncidentSpikeDetectionStats:
    """Per-tenant telemetry for a single 3-incident / 60m spike detection pass."""

    projects_evaluated: int = 0
    spikes_detected: int = 0
    risks_created: int = 0
    outbox_events_staged: int = 0
    duplicates_suppressed: int = 0
    cooldown_held: int = 0
    risks_resolved: int = 0


@dataclass
class TenantBlockerSweepResult:
    """Combined per-tenant blocker detection summary."""

    tenant_id: uuid.UUID
    stale_pr: StalePrDetectionStats = field(default_factory=StalePrDetectionStats)
    incident_spike: IncidentSpikeDetectionStats = field(default_factory=IncidentSpikeDetectionStats)

    @property
    def total_risks_created(self) -> int:
        return self.stale_pr.risks_created + self.incident_spike.risks_created

    @property
    def total_outbox_events_staged(self) -> int:
        return self.stale_pr.outbox_events_staged + self.incident_spike.outbox_events_staged

    @property
    def total_duplicates_suppressed(self) -> int:
        return self.stale_pr.duplicates_suppressed + self.incident_spike.duplicates_suppressed

    @property
    def total_risks_resolved(self) -> int:
        return self.stale_pr.risks_resolved + self.incident_spike.risks_resolved


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize naive or offset-aware datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_pr_open(pr: PullRequest) -> bool:
    """Return True iff the PR is actively open (case-insensitive) and not closed/merged."""
    return (
        (pr.state or "").strip().lower() == "open" and pr.merged_at is None and pr.closed_at is None
    )


def compute_pr_last_activity_at(
    pr: PullRequest,
    *,
    latest_commit_at: Optional[datetime] = None,
    latest_review_at: Optional[datetime] = None,
) -> datetime:
    """Compute the latest engineering activity timestamp for a pull request in UTC."""
    candidates: list[datetime] = []
    for ts in (
        pr.created_in_git_at,
        pr.updated_at,
        pr.first_review_at,
        latest_commit_at,
        latest_review_at,
    ):
        if ts is not None:
            candidates.append(_ensure_utc(ts))
    if not candidates:
        return datetime.now(timezone.utc)
    return max(candidates)


def is_pr_stale_at(
    last_activity_at: datetime,
    *,
    threshold_hours: int,
    now: datetime,
) -> bool:
    """Return True strictly when idle duration exceeds `threshold_hours` (> boundary)."""
    safe_threshold = max(1, int(threshold_hours))
    idle_delta = _ensure_utc(now) - _ensure_utc(last_activity_at)
    return idle_delta > timedelta(hours=safe_threshold)


def should_reset_incident_spike_risk(
    *,
    incident_count: int,
    risk_created_at: datetime,
    now: datetime,
    window_minutes: int = INCIDENT_SPIKE_WINDOW_MINUTES,
    threshold_count: int = INCIDENT_SPIKE_THRESHOLD,
) -> bool:
    """
    Anti-flapping reset predicate for an active INCIDENT_SPIKE Risk:
    Resolve iff:
      - incident_count == 0 (full rolling window quiet), OR
      - incident_count < threshold_count AND (now - risk.created_at) >= window_minutes.
    """
    if incident_count >= threshold_count:
        return False
    if incident_count <= 0:
        return True
    age = _ensure_utc(now) - _ensure_utc(risk_created_at)
    return age >= timedelta(minutes=max(1, int(window_minutes)))


async def upsert_blocker_risk_with_outbox(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    risk_type: RiskType | str,
    severity: RiskSeverity | str,
    subject_type: RiskSubjectType | str,
    subject_id: str | uuid.UUID,
    outbox_payload: dict[str, Any],
    detected_at: Optional[datetime] = None,
    risk_id: Optional[uuid.UUID] = None,
) -> tuple[Optional[uuid.UUID], Optional[OutboxEvent], bool]:
    """
    Atomically inserts a new active `Risk` row guarded by `uq_risks_active_condition`
    (`ON CONFLICT ... WHERE status IN ('OPEN', 'ACKNOWLEDGED') DO NOTHING RETURNING id`)
    and stages a canonical `OutboxEvent(event_type="risk.detected")` in the SAME
    database transaction ONLY when a new `Risk` row was actually inserted.

    CRITICAL INVARIANT: Does NOT commit the transaction. The caller owns the
    `get_tenant_db_context(tenant_id)` transaction boundary so any exception rolls
    back both the `Risk` row and the `OutboxEvent` atomically.
    """
    now = _ensure_utc(detected_at or datetime.now(timezone.utc))
    candidate_id = risk_id or uuid.uuid4()
    r_type = risk_type.value if isinstance(risk_type, RiskType) else str(risk_type)
    r_severity = severity.value if isinstance(severity, RiskSeverity) else str(severity)
    r_subject_type = (
        subject_type.value if isinstance(subject_type, RiskSubjectType) else str(subject_type)
    )
    r_subject_id = str(subject_id)

    stmt = (
        pg_insert(Risk)
        .values(
            id=candidate_id,
            tenant_id=tenant_id,
            project_id=project_id,
            type=r_type,
            severity=r_severity,
            status=RiskStatus.OPEN.value,
            subject_type=r_subject_type,
            subject_id=r_subject_id,
            created_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "tenant_id",
                "project_id",
                "type",
                "subject_type",
                "subject_id",
            ],
            index_where=_ACTIVE_RISK_INDEX_WHERE,
        )
        .returning(Risk.id)
    )
    inserted_id = (await db.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        return None, None, False

    canonical_data: dict[str, Any] = {
        "risk_id": str(inserted_id),
        "risk_type": r_type,
        "severity": r_severity,
        "status": RiskStatus.OPEN.value,
        "subject_type": r_subject_type,
        "subject_id": r_subject_id,
        "project_id": str(project_id),
        "detected_at": now.isoformat(),
        **outbox_payload,
    }
    outbox_event = record_outbox_event(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        event_type=RISK_DETECTED_EVENT_TYPE,
        aggregate_type="risk",
        aggregate_id=inserted_id,
        payload=canonical_data,
        occurred_at=now,
    )
    await db.flush()
    return inserted_id, outbox_event, True


async def resolve_active_blocker_risks(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    risk_type: RiskType | str,
    subject_type: RiskSubjectType | str,
    subject_id: str | uuid.UUID,
) -> int:
    """Transitions any active (`OPEN` / `ACKNOWLEDGED`) matching Risks to `RESOLVED`."""
    r_type = risk_type.value if isinstance(risk_type, RiskType) else str(risk_type)
    r_subject_type = (
        subject_type.value if isinstance(subject_type, RiskSubjectType) else str(subject_type)
    )
    stmt = (
        update(Risk)
        .where(
            Risk.tenant_id == tenant_id,
            Risk.project_id == project_id,
            Risk.type == r_type,
            Risk.subject_type == r_subject_type,
            Risk.subject_id == str(subject_id),
            Risk.status.in_(ACTIVE_RISK_STATUSES),
        )
        .values(status=RiskStatus.RESOLVED.value)
    )
    res = await db.execute(stmt)
    return int(res.rowcount or 0)


async def reset_pr_staleness_on_activity(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    pr: PullRequest,
    activity_at: Optional[datetime] = None,
    advance_updated_at: bool = True,
) -> tuple[bool, int]:
    """
    Cleanly resets `PullRequest.is_stale = False` on new commit/review/lifecycle
    activity and transitions any active `STALE_PR` Risk for the PR to `RESOLVED`.

    Returns `(was_stale, resolved_risks_count)`.
    """
    was_stale = bool(pr.is_stale)
    update_values: dict[str, Any] = {"is_stale": False}
    should_update_pr = was_stale

    if advance_updated_at and activity_at is not None:
        act_utc = _ensure_utc(activity_at)
        existing_updated = _ensure_utc(pr.updated_at) if pr.updated_at is not None else None
        if existing_updated is None or act_utc > existing_updated:
            update_values["updated_at"] = act_utc
            pr.updated_at = act_utc
            should_update_pr = True
        else:
            update_values["updated_at"] = PullRequest.updated_at
    else:
        update_values["updated_at"] = PullRequest.updated_at

    if should_update_pr:
        await db.execute(
            update(PullRequest)
            .where(
                PullRequest.id == pr.id,
                PullRequest.tenant_id == tenant_id,
            )
            .values(**update_values)
        )
        pr.is_stale = False

    resolved_count = await resolve_active_blocker_risks(
        db,
        tenant_id=tenant_id,
        project_id=pr.project_id,
        risk_type=RiskType.STALE_PR,
        subject_type=RiskSubjectType.PULL_REQUEST,
        subject_id=pr.id,
    )
    return was_stale, resolved_count


async def _load_active_projects(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> dict[uuid.UUID, Project]:
    stmt = select(Project).where(
        Project.tenant_id == tenant_id,
        Project.is_active.is_(True),
        Project.deleted_at.is_(None),
    )
    projects = (await db.execute(stmt)).scalars().all()
    return {project.id: project for project in projects}


async def detect_stale_prs_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: Optional[datetime] = None,
    active_projects: Optional[dict[uuid.UUID, Project]] = None,
) -> StalePrDetectionStats:
    """
    Evaluates all open PRs in active tenant projects against `Project.pr_stale_threshold_hours`.

    - Flags PRs idle `> threshold_hours` (`is_stale = True`, preserving `updated_at`) and
      atomically upserts `Risk(STALE_PR, HIGH, OPEN)` + `OutboxEvent(risk.detected)`.
    - Self-heals/resets any PR whose latest activity is now within threshold or whose state
      is no longer open (`is_stale = False`, active `STALE_PR` Risk -> `RESOLVED`).
    """
    ref_now = _ensure_utc(now or datetime.now(timezone.utc))
    stats = StalePrDetectionStats()

    projects_by_id = (
        active_projects
        if active_projects is not None
        else await _load_active_projects(db, tenant_id=tenant_id)
    )
    if not projects_by_id:
        return stats

    project_ids = list(projects_by_id.keys())
    stats.projects_evaluated = len(project_ids)

    # 1. Load open PRs across active projects (case-insensitive state check)
    open_prs_stmt = select(PullRequest).where(
        PullRequest.tenant_id == tenant_id,
        PullRequest.project_id.in_(project_ids),
        func.lower(PullRequest.state) == "open",
        PullRequest.merged_at.is_(None),
        PullRequest.closed_at.is_(None),
    )
    open_prs = list((await db.execute(open_prs_stmt)).scalars().all())
    stats.prs_evaluated = len(open_prs)

    latest_commits_by_pr: dict[uuid.UUID, datetime] = {}
    latest_reviews_by_pr: dict[uuid.UUID, datetime] = {}

    if open_prs:
        pr_ids = [pr.id for pr in open_prs]

        commit_rows = (
            await db.execute(
                select(
                    Commit.pull_request_id,
                    func.max(func.greatest(Commit.committed_at, Commit.authored_at)),
                )
                .where(
                    Commit.tenant_id == tenant_id,
                    Commit.pull_request_id.in_(pr_ids),
                )
                .group_by(Commit.pull_request_id)
            )
        ).all()
        for pr_id, max_ts in commit_rows:
            if pr_id is not None and max_ts is not None:
                latest_commits_by_pr[pr_id] = _ensure_utc(max_ts)

        review_rows = (
            await db.execute(
                select(
                    PullRequestReview.pull_request_id,
                    func.max(PullRequestReview.submitted_at),
                )
                .where(
                    PullRequestReview.tenant_id == tenant_id,
                    PullRequestReview.pull_request_id.in_(pr_ids),
                )
                .group_by(PullRequestReview.pull_request_id)
            )
        ).all()
        for pr_id, max_ts in review_rows:
            if pr_id is not None and max_ts is not None:
                latest_reviews_by_pr[pr_id] = _ensure_utc(max_ts)

    currently_stale_pr_ids: set[str] = set()

    for pr in open_prs:
        project = projects_by_id[pr.project_id]
        threshold_hours = max(
            1, int(project.pr_stale_threshold_hours or DEFAULT_PR_STALE_THRESHOLD_HOURS)
        )
        last_activity_at = compute_pr_last_activity_at(
            pr,
            latest_commit_at=latest_commits_by_pr.get(pr.id),
            latest_review_at=latest_reviews_by_pr.get(pr.id),
        )

        if is_pr_stale_at(last_activity_at, threshold_hours=threshold_hours, now=ref_now):
            currently_stale_pr_ids.add(str(pr.id))
            if not pr.is_stale:
                # Explicitly preserve updated_at so ORM onupdate does not bump activity time.
                await db.execute(
                    update(PullRequest)
                    .where(
                        PullRequest.id == pr.id,
                        PullRequest.tenant_id == tenant_id,
                    )
                    .values(is_stale=True, updated_at=PullRequest.updated_at)
                )
                stats.prs_flagged_stale += 1

            idle_hours = round((ref_now - last_activity_at).total_seconds() / 3600.0, 2)
            _, _, created = await upsert_blocker_risk_with_outbox(
                db,
                tenant_id=tenant_id,
                project_id=pr.project_id,
                risk_type=RiskType.STALE_PR,
                severity=RiskSeverity.HIGH,
                subject_type=RiskSubjectType.PULL_REQUEST,
                subject_id=pr.id,
                detected_at=ref_now,
                outbox_payload={
                    "pull_request_id": str(pr.id),
                    "repository_id": str(pr.repository_id),
                    "external_pr_number": pr.external_pr_number,
                    "pr_title": pr.title,
                    "author_pseudonym_id": pr.author_pseudonym_id,
                    "threshold_hours": threshold_hours,
                    "idle_hours": idle_hours,
                    "last_activity_at": last_activity_at.isoformat(),
                },
            )
            if created:
                stats.risks_created += 1
                stats.outbox_events_staged += 1
            else:
                stats.duplicates_suppressed += 1
        elif pr.is_stale:
            # Self-healing reset: PR gained recent commit/review activity since being flagged.
            await db.execute(
                update(PullRequest)
                .where(
                    PullRequest.id == pr.id,
                    PullRequest.tenant_id == tenant_id,
                )
                .values(is_stale=False, updated_at=PullRequest.updated_at)
            )
            stats.prs_reset += 1
            stats.risks_resolved += await resolve_active_blocker_risks(
                db,
                tenant_id=tenant_id,
                project_id=pr.project_id,
                risk_type=RiskType.STALE_PR,
                subject_type=RiskSubjectType.PULL_REQUEST,
                subject_id=pr.id,
            )

    # 2. Terminal PR cleanup: ensure closed/merged PRs never remain marked is_stale=True
    terminal_reset_res = await db.execute(
        update(PullRequest)
        .where(
            PullRequest.tenant_id == tenant_id,
            PullRequest.project_id.in_(project_ids),
            PullRequest.is_stale.is_(True),
            or_(
                func.lower(PullRequest.state) != "open",
                PullRequest.merged_at.is_not(None),
                PullRequest.closed_at.is_not(None),
            ),
        )
        .values(is_stale=False, updated_at=PullRequest.updated_at)
    )
    stats.prs_reset += int(terminal_reset_res.rowcount or 0)

    # 3. Resolve any active STALE_PR risks whose PR is no longer open+stale
    active_stale_risks_stmt = select(Risk).where(
        Risk.tenant_id == tenant_id,
        Risk.project_id.in_(project_ids),
        Risk.type == RiskType.STALE_PR.value,
        Risk.subject_type == RiskSubjectType.PULL_REQUEST.value,
        Risk.status.in_(ACTIVE_RISK_STATUSES),
    )
    active_stale_risks = list((await db.execute(active_stale_risks_stmt)).scalars().all())
    orphaned_risk_ids = [
        risk.id for risk in active_stale_risks if risk.subject_id not in currently_stale_pr_ids
    ]
    if orphaned_risk_ids:
        resolve_res = await db.execute(
            update(Risk)
            .where(
                Risk.tenant_id == tenant_id,
                Risk.id.in_(orphaned_risk_ids),
            )
            .values(status=RiskStatus.RESOLVED.value)
        )
        stats.risks_resolved += int(resolve_res.rowcount or 0)

    return stats


async def detect_incident_spikes_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: Optional[datetime] = None,
    window_minutes: int = INCIDENT_SPIKE_WINDOW_MINUTES,
    threshold_count: int = INCIDENT_SPIKE_THRESHOLD,
    active_projects: Optional[dict[uuid.UUID, Project]] = None,
) -> IncidentSpikeDetectionStats:
    """
    Detects active projects with `>= threshold_count` incidents in `[now - window_minutes, now]`.

    - Atomically upserts `Risk(INCIDENT_SPIKE, CRITICAL, OPEN)` + `OutboxEvent(risk.detected)`
      with duplicate suppression across repeated runs.
    - Auto-resolves active `INCIDENT_SPIKE` risks using anti-flapping hysteresis:
      `count == 0` OR (`count < threshold_count` AND `now - risk.created_at >= window_minutes`).
    """
    ref_now = _ensure_utc(now or datetime.now(timezone.utc))
    safe_window = max(1, int(window_minutes))
    safe_threshold = max(1, int(threshold_count))
    window_start = ref_now - timedelta(minutes=safe_window)

    stats = IncidentSpikeDetectionStats()

    projects_by_id = (
        active_projects
        if active_projects is not None
        else await _load_active_projects(db, tenant_id=tenant_id)
    )
    if not projects_by_id:
        return stats

    project_ids = list(projects_by_id.keys())
    stats.projects_evaluated = len(project_ids)

    # 1. Fetch all incidents starting inside the rolling [window_start, ref_now] window
    inc_stmt = (
        select(Incident.id, Incident.project_id, Incident.started_at)
        .where(
            Incident.tenant_id == tenant_id,
            Incident.project_id.in_(project_ids),
            Incident.started_at >= window_start,
            Incident.started_at <= ref_now,
        )
        .order_by(Incident.started_at.asc(), Incident.id.asc())
    )
    inc_rows = (await db.execute(inc_stmt)).all()

    incidents_by_project: dict[uuid.UUID, list[uuid.UUID]] = {}
    for inc_id, proj_id, _started_at in inc_rows:
        incidents_by_project.setdefault(proj_id, []).append(inc_id)

    # 2. Fetch existing active INCIDENT_SPIKE risks for active projects
    active_risks_stmt = select(Risk).where(
        Risk.tenant_id == tenant_id,
        Risk.project_id.in_(project_ids),
        Risk.type == RiskType.INCIDENT_SPIKE.value,
        Risk.subject_type == RiskSubjectType.PROJECT.value,
        Risk.status.in_(ACTIVE_RISK_STATUSES),
    )
    active_risks = list((await db.execute(active_risks_stmt)).scalars().all())
    active_risks_by_project: dict[uuid.UUID, list[Risk]] = {}
    for risk in active_risks:
        active_risks_by_project.setdefault(risk.project_id, []).append(risk)

    # 3. Evaluate each active project
    risk_ids_to_resolve: list[uuid.UUID] = []
    for project_id in project_ids:
        inc_ids = incidents_by_project.get(project_id, [])
        inc_count = len(inc_ids)

        if inc_count >= safe_threshold:
            stats.spikes_detected += 1
            _, _, created = await upsert_blocker_risk_with_outbox(
                db,
                tenant_id=tenant_id,
                project_id=project_id,
                risk_type=RiskType.INCIDENT_SPIKE,
                severity=RiskSeverity.CRITICAL,
                subject_type=RiskSubjectType.PROJECT,
                subject_id=project_id,
                detected_at=ref_now,
                outbox_payload={
                    "incident_count": inc_count,
                    "threshold_count": safe_threshold,
                    "window_minutes": safe_window,
                    "window_start": window_start.isoformat(),
                    "window_end": ref_now.isoformat(),
                    "incident_ids": [str(i) for i in inc_ids],
                },
            )
            if created:
                stats.risks_created += 1
                stats.outbox_events_staged += 1
            else:
                stats.duplicates_suppressed += 1
        else:
            for existing_risk in active_risks_by_project.get(project_id, ()):
                if should_reset_incident_spike_risk(
                    incident_count=inc_count,
                    risk_created_at=existing_risk.created_at,
                    now=ref_now,
                    window_minutes=safe_window,
                    threshold_count=safe_threshold,
                ):
                    risk_ids_to_resolve.append(existing_risk.id)
                else:
                    stats.cooldown_held += 1

    if risk_ids_to_resolve:
        resolve_res = await db.execute(
            update(Risk)
            .where(
                Risk.tenant_id == tenant_id,
                Risk.id.in_(risk_ids_to_resolve),
            )
            .values(status=RiskStatus.RESOLVED.value)
        )
        stats.risks_resolved += int(resolve_res.rowcount or 0)

    return stats


async def detect_tenant_blockers_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: Optional[datetime] = None,
    window_minutes: int = INCIDENT_SPIKE_WINDOW_MINUTES,
    threshold_count: int = INCIDENT_SPIKE_THRESHOLD,
) -> TenantBlockerSweepResult:
    """
    Runs both Stale PR and Incident Spike detectors inside a single tenant-RLS
    database session/transaction boundary.
    """
    ref_now = _ensure_utc(now or datetime.now(timezone.utc))
    active_projects = await _load_active_projects(db, tenant_id=tenant_id)

    stale_stats = await detect_stale_prs_in_session(
        db,
        tenant_id=tenant_id,
        now=ref_now,
        active_projects=active_projects,
    )
    spike_stats = await detect_incident_spikes_in_session(
        db,
        tenant_id=tenant_id,
        now=ref_now,
        window_minutes=window_minutes,
        threshold_count=threshold_count,
        active_projects=active_projects,
    )
    return TenantBlockerSweepResult(
        tenant_id=tenant_id,
        stale_pr=stale_stats,
        incident_spike=spike_stats,
    )
