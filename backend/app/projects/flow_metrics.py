"""Sprint burndown and WIP flow metrics (US-67)."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Issue, Project, Sprint, WIPLimit
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
            select(Issue.story_points, Issue.resolved_at).where(
                Issue.tenant_id == tenant_id, Issue.sprint_id == sprint.id
            )
        )
    ).all()

    total_sp = 0.0
    for row in rows:
        total_sp += float(row.story_points or 0)

    first_day = sprint.start_date.astimezone(timezone.utc).date()
    last_day = sprint.end_date.astimezone(timezone.utc).date()
    if last_day < first_day:
        last_day = first_day
    day_count = (last_day - first_day).days + 1
    today_date = today or datetime.now(timezone.utc).date()

    series: list[dict[str, Any]] = []
    for index in range(day_count):
        day = first_day + timedelta(days=index)
        day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)

        done_sp = 0.0
        for row in rows:
            if row.resolved_at is not None and row.resolved_at < day_end:
                done_sp += float(row.story_points or 0)

        if day_count > 1:
            ideal_sp = total_sp * (1 - index / (day_count - 1))
        else:
            ideal_sp = total_sp

        remaining_sp: Optional[float] = None
        if day <= today_date:
            remaining_sp = round(total_sp - done_sp, 2)

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
            Issue.resolved_at.is_(None),
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
        return {"sprint": sprint_info, "burndown": series, "wip": wip}
