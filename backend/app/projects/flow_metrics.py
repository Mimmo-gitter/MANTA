"""Sprint burndown and WIP flow metrics (US-67)."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import hashlib

from app.db.models.models import (
    Issue,
    IssueTransition,
    Project,
    Sprint,
    WIPLimit,
)
from app.db.session import get_tenant_db_context


async def project_exists(db: AsyncSession, tenant_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    found = (
        await db.execute(
            select(Project.id).where(Project.tenant_id == tenant_id, Project.id == project_id)
        )
    ).first()
    return found is not None


async def find_sprint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: Optional[uuid.UUID] = None,
) -> Optional[Sprint]:
    query = select(Sprint).where(Sprint.tenant_id == tenant_id, Sprint.project_id == project_id)
    if sprint_id is not None:
        query = query.where(Sprint.id == sprint_id)
    else:
        query = query.order_by(
            (Sprint.state == "active").desc(), Sprint.start_date.desc().nullslast()
        )
    return (await db.execute(query.limit(1))).scalar_one_or_none()


async def list_project_sprints_response(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Returns all sprints stored for a project under tenant isolation.
    Active sprints are listed first, followed by the most recent ones.
    """
    async with get_tenant_db_context(tenant_id) as db:
        if not await project_exists(db, tenant_id, project_id):
            raise HTTPException(status_code=404, detail="Project not found")

        query = (
            select(Sprint)
            .where(
                Sprint.tenant_id == tenant_id,
                Sprint.project_id == project_id,
            )
            .order_by(
                (Sprint.state == "active").desc(),
                Sprint.start_date.desc().nullslast(),
                Sprint.created_at.desc(),
            )
        )

        result = await db.execute(query)
        sprints = result.scalars().all()

        return [
            {
                "id": str(sprint.id),
                "external_sprint_id": sprint.external_sprint_id,
                "name": sprint.name,
                "state": sprint.state,
                "start_date": sprint.start_date,
                "end_date": sprint.end_date,
                "complete_date": sprint.complete_date,
                "goal": sprint.goal,
            }
            for sprint in sprints
        ]


async def compute_burndown(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    sprint: Sprint,
    today: Optional[date] = None,
) -> list[dict[str, Any]]:
    if sprint.start_date is None or sprint.end_date is None:
        return []

    rows = (
        await db.execute(
            select(
                Issue.id,
                Issue.story_points,
                Issue.status,
                Issue.resolved_at,
            ).where(
                Issue.tenant_id == tenant_id,
                Issue.sprint_id == sprint.id,
            )
        )
    ).all()

    if not rows:
        return []

    issue_ids = [row.id for row in rows]

    # Load the complete available status-transition history.
    transition_rows = (
        await db.execute(
            select(
                IssueTransition.issue_id,
                IssueTransition.from_status,
                IssueTransition.to_status,
                IssueTransition.transitioned_at,
            )
            .where(
                IssueTransition.tenant_id == tenant_id,
                IssueTransition.issue_id.in_(issue_ids),
            )
            .order_by(
                IssueTransition.issue_id,
                IssueTransition.transitioned_at,
            )
        )
    ).all()

    transitions_by_issue: dict[uuid.UUID, list[Any]] = {}

    for transition in transition_rows:
        transitions_by_issue.setdefault(transition.issue_id, []).append(transition)

    def is_done_status(status: Optional[str]) -> bool:
        normalized = (status or "").strip().casefold()
        return normalized in {
            "done",
            "closed",
            "resolved",
            "complete",
            "completed",
        }

    total_sp = sum(float(row.story_points or 0) for row in rows)

    start_utc = sprint.start_date.astimezone(timezone.utc)
    first_day = start_utc.date()
    last_day = sprint.end_date.astimezone(timezone.utc).date()
    if last_day < first_day:
        last_day = first_day
    day_count = (last_day - first_day).days + 1
    today_date = today or datetime.now(timezone.utc).date()

    series: list[dict[str, Any]] = []
    for index in range(day_count):
        day = first_day + timedelta(days=index)

        # The first point is the sprint-start baseline.
        # Subsequent points represent the end of each calendar day.
        if index == 0:
            cutoff = start_utc
        else:
            cutoff = datetime.combine(
                day + timedelta(days=1),
                time.min,
                tzinfo=timezone.utc,
            )

        done_sp = 0.0
        for row in rows:
            issue_transitions = transitions_by_issue.get(row.id, [])

            if issue_transitions:
                # Reconstruct the issue status at the selected cutoff.
                current_status = issue_transitions[0].from_status

                for transition in issue_transitions:
                    if transition.transitioned_at >= cutoff:
                        break

                    current_status = transition.to_status

                is_done = is_done_status(current_status)

            elif row.resolved_at is not None:
                # Fallback for issues without transition history.
                is_done = row.resolved_at < cutoff

            else:
                # Last available fallback: current Jira issue status.
                is_done = is_done_status(row.status)

            if is_done:
                done_sp += float(row.story_points or 0)

        if day_count > 1:
            ideal_sp = total_sp * (1 - index / (day_count - 1))
        else:
            ideal_sp = total_sp

        remaining_sp: Optional[float] = None
        if day <= today_date:
            remaining_sp = round(max(0.0, total_sp - done_sp), 2)

        series.append(
            {
                "date": day.isoformat(),
                "remaining_sp": remaining_sp,
                "ideal_sp": round(ideal_sp, 2),
            }
        )
    return series


async def compute_wip(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint: Optional[Sprint],
) -> list[dict[str, Any]]:
    count_query = (
        select(Issue.status, func.count(Issue.id))
        .where(
            Issue.tenant_id == tenant_id,
            Issue.project_id == project_id,
            func.lower(func.trim(Issue.status)).notin_(
                ("done", "closed", "resolved", "complete", "completed")
            ),
        )
        .group_by(Issue.status)
    )
    if sprint is not None:
        count_query = count_query.where(Issue.sprint_id == sprint.id)
    count_rows = (await db.execute(count_query)).all()

    counts: dict[str, int] = {}
    for status_name, issue_count in count_rows:
        counts[status_name] = issue_count

    limit_rows = (
        await db.execute(
            select(WIPLimit.status, WIPLimit.max_items).where(
                WIPLimit.tenant_id == tenant_id, WIPLimit.project_id == project_id
            )
        )
    ).all()
    limits: dict[str, int] = {}
    for status_name, max_items in limit_rows:
        limits[status_name] = max_items

    all_statuses = sorted(set(counts.keys()) | set(limits.keys()))
    result: list[dict[str, Any]] = []
    for status_name in all_statuses:
        current = counts.get(status_name, 0)
        limit = limits.get(status_name)
        result.append(
            {
                "status": status_name,
                "count": current,
                "limit": limit,
                "over_limit": limit is not None and current > limit,
            }
        )
    return result


async def compute_wip_by_developer(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint: Optional[Sprint],
) -> dict[str, Any]:
    # Get the project's WIP limit per developer.
    limit_query = select(Project.wip_limit_per_dev).where(
        Project.tenant_id == tenant_id,
        Project.id == project_id,
    )
    wip_limit = (await db.execute(limit_query)).scalar_one()

    # Count unfinished issues grouped by Jira contributor pseudonym.
    count_query = (
        select(
            Issue.assignee_pseudonym_id,
            func.count(Issue.id),
        )
        .where(
            Issue.tenant_id == tenant_id,
            Issue.project_id == project_id,
            func.lower(func.trim(Issue.status)).notin_(
                ("done", "closed", "resolved", "complete", "completed")
            ),
        )
        .group_by(Issue.assignee_pseudonym_id)
    )

    if sprint is not None:
        count_query = count_query.where(Issue.sprint_id == sprint.id)

    rows = (await db.execute(count_query)).all()

    developers: list[dict[str, Any]] = []
    unassigned_count = 0

    for pseudonym_id, issue_count in rows:
        if pseudonym_id is None:
            unassigned_count = int(issue_count)
            continue

        current = int(issue_count)

        developer_label = (
            "Developer " + hashlib.sha256(pseudonym_id.encode("utf-8")).hexdigest()[:10].upper()
        )

        developers.append(
            {
                "developer_label": developer_label,
                "count": current,
                "limit": wip_limit,
                "over_limit": current > wip_limit,
            }
        )

    developers.sort(key=lambda item: (-item["count"], item["developer_label"]))

    return {
        "limit_per_developer": wip_limit,
        "developers": developers,
        "unassigned_count": unassigned_count,
    }


async def get_burndown_response(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    async with get_tenant_db_context(tenant_id) as db:
        if not await project_exists(db, tenant_id, project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        sprint = await find_sprint(db, tenant_id, project_id, sprint_id)
        if sprint is None:
            raise HTTPException(status_code=404, detail="Sprint not found")
        series = await compute_burndown(db, tenant_id, sprint)
        return {"sprint_id": str(sprint.id), "sprint_name": sprint.name, "series": series}


async def get_flow_metrics_response(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    async with get_tenant_db_context(tenant_id) as db:
        if not await project_exists(db, tenant_id, project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        sprint = await find_sprint(db, tenant_id, project_id, sprint_id)
        series: list[dict[str, Any]] = []
        sprint_info = None
        if sprint is not None:
            series = await compute_burndown(db, tenant_id, sprint)
            sprint_info = {"id": str(sprint.id), "name": sprint.name, "state": sprint.state}
        wip = await compute_wip(db, tenant_id, project_id, sprint)

        wip_by_developer = await compute_wip_by_developer(
            db,
            tenant_id,
            project_id,
            sprint,
        )

        return {
            "sprint": sprint_info,
            "burndown": series,
            "wip": wip,
            "wip_by_developer": wip_by_developer,
        }
