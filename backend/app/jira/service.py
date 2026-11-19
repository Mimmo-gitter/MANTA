"""Persistence for Jira sprints, issues, transitions and AC versions (Task 67.3)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import re
from typing import Optional
import uuid

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import (
    AcceptanceCriteriaVersion,
    Issue,
    IssueTransition,
    Sprint,
)
from app.jira.normalization import NormalizedIssue, NormalizedSprint
from app.webhooks.metrics import record_dropped_stale_event

# TODO: verify import path / signature of the Task 20.3 helper.
from app.contributors.service import resolve_or_create_contributor_pseudonym

logger = logging.getLogger("app.jira.service")


def _to_int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def upsert_sprint(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    s: NormalizedSprint,
) -> Sprint:
    row = (
        await db.execute(
            select(Sprint).where(
                Sprint.tenant_id == tenant_id,
                Sprint.project_id == project_id,
                Sprint.external_sprint_id == s.external_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = Sprint(
            tenant_id=tenant_id,
            project_id=project_id,
            external_sprint_id=s.external_id,
            name=s.name or "",
            state=s.state or "active",
            start_date=s.start_date,
            end_date=s.end_date,
            complete_date=s.complete_date,
            goal=s.goal,
        )
        db.add(row)
    else:
        row.name = s.name or row.name
        row.state = s.state or row.state
        row.start_date = s.start_date or row.start_date
        row.end_date = s.end_date or row.end_date
        row.complete_date = s.complete_date or row.complete_date
        row.goal = s.goal if s.goal is not None else row.goal
    await db.flush()
    return row


async def _record_transition(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    issue: Issue,
    n: NormalizedIssue,
    prev_status: Optional[str],
) -> None:
    if n.event_at is None:
        return
    if n.status_change and n.status_change[1]:
        from_status = n.status_change[0] or prev_status or ""
        to_status = n.status_change[1]
    elif prev_status and n.status and prev_status != n.status:
        from_status, to_status = prev_status, n.status
    else:
        return

    # Retry safety beyond Issue.changelog_id: same target status at the same instant.
    dup = (
        await db.execute(
            select(IssueTransition.id).where(
                IssueTransition.tenant_id == tenant_id,
                IssueTransition.issue_id == issue.id,
                IssueTransition.to_status == to_status,
                IssueTransition.transitioned_at == n.event_at,
            )
        )
    ).first()
    if dup:
        return

    last_at = (
        await db.execute(
            select(func.max(IssueTransition.transitioned_at)).where(
                IssueTransition.tenant_id == tenant_id,
                IssueTransition.issue_id == issue.id,
            )
        )
    ).scalar_one_or_none()
    since = last_at or issue.created_at
    duration = max(0, int((n.event_at - since).total_seconds())) if since else None

    db.add(
        IssueTransition(
            tenant_id=tenant_id,
            issue_id=issue.id,
            from_status=from_status,
            to_status=to_status,
            transitioned_at=n.event_at,
            duration_seconds=duration,
        )
    )
    await db.flush()


def _ac_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


async def _record_ac_version(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    issue: Issue,
    ac_text: Optional[str],
) -> None:
    if ac_text is None:
        return
    latest = (
        await db.execute(
            select(AcceptanceCriteriaVersion)
            .where(
                AcceptanceCriteriaVersion.tenant_id == tenant_id,
                AcceptanceCriteriaVersion.issue_id == issue.id,
            )
            .order_by(AcceptanceCriteriaVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None and not ac_text.strip():
        return  # no AC yet and none in this payload
    new_hash = _ac_hash(ac_text)
    if latest is not None and latest.content_hash == new_hash:
        return
    db.add(
        AcceptanceCriteriaVersion(
            tenant_id=tenant_id,
            issue_id=issue.id,
            version=(latest.version + 1) if latest is not None else 1,
            raw_criteria_text=ac_text,
            content_hash=new_hash,
            lexical_tokens_count=len(ac_text.split()),
        )
    )
    issue.acceptance_criteria = ac_text
    issue.acceptance_criteria_hash = new_hash
    await db.flush()


async def upsert_issue(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    n: NormalizedIssue,
    integration_id: Optional[uuid.UUID] = None,
) -> tuple[Issue, bool]:
    """Returns (issue, applied). applied=False means a stale/duplicate changelog was ignored."""
    issue = (
        await db.execute(
            select(Issue).where(Issue.tenant_id == tenant_id, Issue.external_key == n.key)
        )
    ).scalar_one_or_none()

    incoming = _to_int(n.changelog_id)
    if (
        issue is not None
        and incoming is not None
        and issue.changelog_id is not None
        and incoming <= issue.changelog_id
    ):
        record_dropped_stale_event("jira")
        logger.warning(
            "STALE_OUT_OF_ORDER_REJECTED provider=jira table=issues record_id=%s key=%s "
            "incoming_changelog_id=%s stored_changelog_id=%s",
            issue.id,
            n.key,
            incoming,
            issue.changelog_id,
        )
        return issue, False

    prev_status = issue.status if issue is not None else None

    assignee_id: Optional[str] = None
    if n.assignee and n.assignee.get("id"):
        assignee_id = await resolve_or_create_contributor_pseudonym(
            db,
            tenant_id,
            "jira",
            n.assignee["id"],
            raw_email=n.assignee.get("email"),
            raw_name=n.assignee.get("name"),
        )

    sprint_id = None
    if n.sprint is not None:
        sprint_id = (
            await upsert_sprint(db, tenant_id=tenant_id, project_id=project_id, s=n.sprint)
        ).id

    if issue is None:
        initial_version = (
            n.incoming_version
            if n.incoming_version is not None
            else (incoming if incoming is not None else 1)
        )
        issue = Issue(
            tenant_id=tenant_id,
            project_id=project_id,
            integration_id=integration_id,
            sprint_id=sprint_id,
            external_key=n.key,
            external_issue_id=_to_int(n.external_id),
            issue_type=n.issue_type or "Story",
            title=n.title or "",
            status=n.status or "Unknown",
            story_points=n.story_points,
            assignee_pseudonym_id=assignee_id,
            issue_links=n.links,
            changelog_id=incoming,
            entity_version=initial_version,
        )
        created = n.created_at or n.event_at
        if created is not None:
            issue.created_at = created
        db.add(issue)
        await db.flush()
    else:
        incoming_ver = (
            n.incoming_version
            if n.incoming_version is not None
            else (incoming if incoming is not None else ((issue.entity_version or 0) + 1))
        )

        # Conditional PostgreSQL guard asserting newer entity_version before mutation
        update_values: dict[str, object] = {
            "issue_type": n.issue_type or issue.issue_type,
            "title": n.title or issue.title,
            "status": n.status or issue.status,
            "issue_links": n.links,
            "entity_version": incoming_ver,
            "updated_at": n.event_at or datetime.now(timezone.utc),
        }
        if n.story_points is not None:
            update_values["story_points"] = n.story_points
        if sprint_id is not None:
            update_values["sprint_id"] = sprint_id
        if assignee_id is not None:
            update_values["assignee_pseudonym_id"] = assignee_id
        if incoming is not None:
            update_values["changelog_id"] = incoming

        # Guard across Jira's two version domains:
        # 1. Upgrading from timestamp-seeded creation (changelog_id IS NULL) to first numeric changelog_id:
        #    allow if stored row still has changelog_id IS NULL, OR incoming_ver > stored entity_version.
        # 2. Same-domain numeric changelog_id update: enforce entity_version < incoming_ver.
        # 3. Non-changelog fallback payload (incoming is None): ONLY mutate if stored row also has
        #    changelog_id IS NULL AND entity_version < incoming_ver (prevents delayed jira:issue_created
        #    epoch-ms timestamps from overwriting an authoritative numeric changelog_id).
        if incoming is not None:
            if issue.changelog_id is None:
                version_guard = or_(
                    Issue.changelog_id.is_(None),
                    Issue.entity_version < incoming_ver,
                )
            else:
                version_guard = Issue.entity_version < incoming_ver
        else:
            version_guard = and_(
                Issue.changelog_id.is_(None),
                Issue.entity_version < incoming_ver,
            )

        upd_stmt = (
            update(Issue)
            .where(
                Issue.id == issue.id,
                Issue.tenant_id == tenant_id,
                version_guard,
            )
            .values(**update_values)
            .returning(Issue.id)
        )
        updated_id = (await db.execute(upd_stmt)).scalar_one_or_none()
        if updated_id is None:
            record_dropped_stale_event("jira")
            logger.warning(
                "STALE_OUT_OF_ORDER_REJECTED provider=jira table=issues record_id=%s key=%s incoming_version=%s",
                issue.id,
                n.key,
                incoming_ver,
            )
            return issue, False
        await db.refresh(issue)

    await _record_transition(db, tenant_id=tenant_id, issue=issue, n=n, prev_status=prev_status)
    await _record_ac_version(db, tenant_id=tenant_id, issue=issue, ac_text=n.ac_text)
    return issue, True
