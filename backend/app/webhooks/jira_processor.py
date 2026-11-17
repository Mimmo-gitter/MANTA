"""
SCRUM-400 (Task 67.3) & SCRUM-401 (Task 67.4) & US-67:
Jira Webhook Ingestion, Acceptance Criteria Volatility, Changelog Precedence,
and 24-Hour Deployment Incident Correlation Engine.

Handles:
1. Jira Issue Creation & Story Ingestion (jira:issue_created):
   - Ingests Story, Bug, Incident, and Task issues.
   - Extracts Acceptance Criteria (customfield_10016 or acceptance_criteria), normalizes text,
     computes SHA-256 hash, and inserts baseline version=1 in acceptance_criteria_versions.
   - Extracts story points, sprint mapping, and structured issue links (blocks, relates).
   - Resolves assignee into contributor pseudonym under tenant-isolated HMAC-SHA256 vault.
2. Jira Issue Update & AC Volatility Tracking (jira:issue_updated):
   - Enforces monotonic version precedence via changelog.id (discards events where C_in <= C_stored).
   - Detects AC hash changes, preserves immutable v1 history, and appends version=v_max+1.
   - Tracks sequential status transitions (To Do -> In Progress -> Done) with active duration_seconds
     in issue_transitions.
   - Authoritatively records resolved_at upon entering terminal statuses.
3. 24-Hour Deployment Incident Correlation Engine (SCRUM-401 / Task 67.4):
   - Evaluates incident tickets created within 0 <= delta <= 24 hours of production deployment.
   - Creates link in deployment_incidents with status='CONFIRMED', correlation_source='automated_time_window_24h'.
   - Incidents outside the strict 24h boundary (>86400s) are rejected from linking.
4. Incident Cancellation Soft-Unlink:
   - When an incident transitions to Cancelled / False Positive, soft-unlinks deployment_incidents
     (status='CANCELLED', is_active=False) and updates resolved_at.
   - Dynamically removes deployment from DORA Change Failure Rate failure numerator.
"""

from datetime import datetime, timezone
import hashlib
import logging
from typing import Any, Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contributors.service import resolve_or_create_contributor_pseudonym
from app.db.models.models import (
    AcceptanceCriteriaVersion,
    Deployment,
    DeploymentIncident,
    Integration,
    Issue,
    IssueTransition,
    Project,
)
from app.webhooks.sweeper import complete_webhook_event

logger = logging.getLogger("app.webhooks.jira_processor")

TERMINAL_STATUSES = {
    "done",
    "resolved",
    "closed",
    "cancelled",
    "rejected",
    "closed - not a bug",
    "false positive",
    "false_positive",
}

CANCELLATION_STATUSES = {
    "cancelled",
    "rejected",
    "closed - not a bug",
    "false positive",
    "false_positive",
}

CORRELATION_WINDOW_SECONDS: int = 86400  # 24 hours


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_jira_datetime(value: Any) -> Optional[datetime]:
    """Parse a Jira timestamp string, epoch millisecond integer, or datetime to UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, (int, float)):
        # Jira webhook timestamp is frequently Unix epoch milliseconds
        if value > 1e11:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        val_str = value.strip()
        if not val_str:
            return None
        try:
            # Handle Jira format: "2026-11-12T08:00:00.000+0000" or ISO format
            iso_str = val_str.replace("Z", "+00:00")
            if len(iso_str) >= 24 and iso_str[-5] in ("+", "-") and ":" not in iso_str[-5:]:
                iso_str = iso_str[:-2] + ":" + iso_str[-2:]
            dt = datetime.fromisoformat(iso_str)
            return _ensure_utc(dt)
        except Exception:
            return None
    return None


def extract_acceptance_criteria(
    fields: dict[str, Any],
    changelog: Optional[dict[str, Any]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Extract Acceptance Criteria text and compute its SHA-256 hash.
    Checks customfield_10016, acceptance_criteria, acceptanceCriteria, and changelog items.
    """
    ac_text: Optional[str] = None

    # 1. Direct fields check
    for key in (
        "customfield_10016",
        "acceptance_criteria",
        "acceptanceCriteria",
        "customfield_10014",
        "customfield_10015",
    ):
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            ac_text = val.strip()
            break
        elif isinstance(val, dict):
            # Atlassian Document Format (ADF) or rich text dictionary
            content = val.get("content") or val.get("text")
            if isinstance(content, str) and content.strip():
                ac_text = content.strip()
                break

    # 2. Fallback to changelog item if not in fields
    if not ac_text and changelog and isinstance(changelog.get("items"), list):
        for item in changelog["items"]:
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field") or "").lower()
            if field_name in ("customfield_10016", "acceptance_criteria", "acceptance criteria"):
                to_str = item.get("toString")
                if isinstance(to_str, str) and to_str.strip():
                    ac_text = to_str.strip()
                    break

    if not ac_text:
        return None, None

    ac_hash = hashlib.sha256(ac_text.encode("utf-8")).hexdigest().lower()
    return ac_text, ac_hash


def extract_story_points(fields: dict[str, Any]) -> Optional[float]:
    """Extract story points from customfield_10020, customfield_10028, or standard keys."""
    for key in (
        "customfield_10020",
        "story_points",
        "storyPoints",
        "customfield_10028",
        "customfield_10026",
    ):
        val = fields.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def extract_issue_links(fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Jira issue links to JSONB array format."""
    links_input = fields.get("issue_links") or fields.get("issuelinks")
    if not links_input:
        return []
    if isinstance(links_input, list):
        normalized: list[dict[str, Any]] = []
        for item in links_input:
            if not isinstance(item, dict):
                continue
            # Already normalized format
            if "type" in item and "direction" in item and "target_key" in item:
                normalized.append(item)
                continue
            # Native Jira issuelinks structure
            type_obj = item.get("type") if isinstance(item.get("type"), dict) else {}
            type_name = str(type_obj.get("name") or item.get("type") or "Relates").strip()
            if "inwardIssue" in item and isinstance(item["inwardIssue"], dict):
                target = item["inwardIssue"].get("key")
                if target:
                    normalized.append(
                        {"type": type_name, "direction": "inward", "target_key": target}
                    )
            elif "outwardIssue" in item and isinstance(item["outwardIssue"], dict):
                target = item["outwardIssue"].get("key")
                if target:
                    normalized.append(
                        {"type": type_name, "direction": "outward", "target_key": target}
                    )
        return normalized
    return []


async def resolve_jira_project(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    jira_project_key: Optional[str] = None,
    jira_project_name: Optional[str] = None,
    issue_key: Optional[str] = None,
    integration_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """Resolve project_id under tenant RLS using Jira project key, issue key prefix, or integration."""
    # 1. Integration hint
    if integration_id is not None:
        int_stmt = select(Integration.project_id).where(
            Integration.id == integration_id,
            Integration.tenant_id == tenant_id,
        )
        p_id = (await db.execute(int_stmt)).scalar_one_or_none()
        if p_id is not None:
            return p_id

    # 2. Jira project key match against project slug or name
    candidates_to_try: list[str] = []
    if jira_project_key:
        candidates_to_try.append(jira_project_key.strip().lower())
    if issue_key and "-" in issue_key:
        prefix = issue_key.split("-")[0].strip().lower()
        if prefix not in candidates_to_try:
            candidates_to_try.append(prefix)

    for cand in candidates_to_try:
        stmt = (
            select(Project.id)
            .where(
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
                (
                    Project.slug.ilike(cand)
                    | Project.slug.ilike(f"{cand}-%")
                    | Project.name.ilike(cand)
                ),
            )
            .limit(1)
        )
        p_id = (await db.execute(stmt)).scalar_one_or_none()
        if p_id is not None:
            return p_id

    # 3. Project name match
    if jira_project_name:
        stmt = (
            select(Project.id)
            .where(
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
                Project.name.ilike(jira_project_name.strip()),
            )
            .limit(1)
        )
        p_id = (await db.execute(stmt)).scalar_one_or_none()
        if p_id is not None:
            return p_id

    # 4. Fallback: single active project under tenant
    all_projects_stmt = (
        select(Project.id)
        .where(
            Project.tenant_id == tenant_id,
            Project.deleted_at.is_(None),
        )
        .limit(2)
    )
    all_projects = (await db.execute(all_projects_stmt)).scalars().all()
    if len(all_projects) == 1:
        return all_projects[0]

    return None


async def correlate_incident_to_deployment_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    issue_id: uuid.UUID,
    detected_at: datetime,
) -> Optional[DeploymentIncident]:
    """
    Correlate a production incident ticket to the most recent preceding production deployment
    if detected within the 24-hour operational window (0 <= delta <= 86400s).
    """
    # Check if a link already exists
    existing_stmt = select(DeploymentIncident).where(
        DeploymentIncident.tenant_id == tenant_id,
        DeploymentIncident.issue_id == issue_id,
    )
    existing_link = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing_link is not None:
        return existing_link

    # Find the latest completed production deployment finished at or prior to incident detection
    dep_stmt = (
        select(Deployment)
        .where(
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.environment == "production",
            Deployment.status.in_(["SUCCESS", "COMPLETED"]),
            Deployment.deployed_at <= detected_at,
        )
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    candidate_deployment = (await db.execute(dep_stmt)).scalar_one_or_none()
    if candidate_deployment is None:
        logger.info(
            "No preceding production deployment found for incident %s (project=%s)",
            issue_id,
            project_id,
        )
        return None

    delta_seconds = (
        _ensure_utc(detected_at) - _ensure_utc(candidate_deployment.deployed_at)
    ).total_seconds()
    if 0 <= delta_seconds <= CORRELATION_WINDOW_SECONDS:
        link = DeploymentIncident(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            deployment_id=candidate_deployment.id,
            issue_id=issue_id,
            status="CONFIRMED",
            correlation_source="automated_time_window_24h",
            detected_at=_ensure_utc(detected_at),
            is_active=True,
        )
        db.add(link)
        await db.flush()
        logger.info(
            "Correlated incident %s to production deployment %s (delta=%.1fh <= 24h)",
            issue_id,
            candidate_deployment.id,
            delta_seconds / 3600.0,
        )
        return link

    logger.info(
        "Incident %s detected at %s is outside 24h window (delta=%.1fh > 24h) from deployment %s; skipping link",
        issue_id,
        detected_at,
        delta_seconds / 3600.0,
        candidate_deployment.id,
    )
    return None


async def soft_unlink_cancelled_incident(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    issue_id: uuid.UUID,
    cancelled_at: Optional[datetime] = None,
) -> int:
    """Soft-unlinks any active deployment incident links when an incident is cancelled or resolved as not a bug."""
    ts = _ensure_utc(cancelled_at or datetime.now(timezone.utc))
    stmt = select(DeploymentIncident).where(
        DeploymentIncident.tenant_id == tenant_id,
        DeploymentIncident.issue_id == issue_id,
        DeploymentIncident.is_active.is_(True),
    )
    links = (await db.execute(stmt)).scalars().all()
    count = 0
    for link in links:
        link.status = "CANCELLED"
        link.is_active = False
        link.resolved_at = ts
        if link.detected_at:
            rec_sec = int((ts - _ensure_utc(link.detected_at)).total_seconds())
            link.recovery_time_seconds = max(0, rec_sec)
        count += 1
    if count > 0:
        await db.flush()
        logger.info("Soft-unlinked %s active deployment incident(s) for issue %s", count, issue_id)
    return count


async def ingest_jira_event_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    event_id: uuid.UUID,
    integration_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Ingests a staged Jira webhook event under tenant RLS.
    Enforces monotonic changelog.id precedence, AC SHA-256 versioning,
    status transition audit duration, and 24-hour temporal incident correlation.
    """
    issue_dict = payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
    external_key = str(issue_dict.get("key") or "").strip()
    if not external_key:
        logger.warning("Ignoring Jira webhook without issue key: event=%s", event_id)
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:missing_issue_key"

    fields = issue_dict.get("fields") if isinstance(issue_dict.get("fields"), dict) else {}
    changelog_dict = payload.get("changelog") if isinstance(payload.get("changelog"), dict) else {}

    # Extract changelog_id
    changelog_id: Optional[int] = None
    if changelog_dict.get("id"):
        try:
            changelog_id = int(changelog_dict["id"])
        except (ValueError, TypeError):
            pass
    elif payload.get("changelog_id"):
        try:
            changelog_id = int(payload["changelog_id"])
        except (ValueError, TypeError):
            pass

    # Resolve project under tenant RLS
    proj_dict = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    jira_proj_key = proj_dict.get("key")
    jira_proj_name = proj_dict.get("name")

    project_id = await resolve_jira_project(
        db,
        tenant_id=tenant_id,
        jira_project_key=jira_proj_key,
        jira_project_name=jira_proj_name,
        issue_key=external_key,
        integration_id=integration_id,
    )
    if project_id is None:
        logger.warning(
            "Ignoring Jira webhook for unlinked project (issue=%s, proj_key=%s)",
            external_key,
            jira_proj_key,
        )
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:unlinked_jira_project"

    # Extract issue metadata
    raw_issue_id = issue_dict.get("id")
    external_issue_id: Optional[int] = None
    if raw_issue_id is not None:
        try:
            external_issue_id = int(raw_issue_id)
        except (ValueError, TypeError):
            pass

    type_dict = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    issue_type = str(type_dict.get("name") or fields.get("issue_type") or "Story").strip()
    title = str(fields.get("summary") or issue_dict.get("summary") or external_key).strip()

    status_dict = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    status_name = str(status_dict.get("name") or fields.get("status") or "To Do").strip()

    story_points = extract_story_points(fields)
    ac_text, ac_hash = extract_acceptance_criteria(fields, changelog_dict)
    issue_links = extract_issue_links(fields)

    event_ts = (
        parse_jira_datetime(payload.get("timestamp"))
        or parse_jira_datetime(fields.get("updated"))
        or datetime.now(timezone.utc)
    )
    created_ts = parse_jira_datetime(fields.get("created")) or event_ts

    # Determine terminal resolution
    is_terminal = status_name.lower() in TERMINAL_STATUSES
    resolved_ts: Optional[datetime] = None
    if is_terminal:
        resolved_ts = (
            parse_jira_datetime(fields.get("resolutiondate"))
            or parse_jira_datetime(fields.get("updated"))
            or event_ts
        )

    # Resolve assignee pseudonym if present
    assignee_pseudo: Optional[str] = None
    assignee_dict = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}
    if assignee_dict:
        assignee_actor = (
            assignee_dict.get("accountId")
            or assignee_dict.get("key")
            or assignee_dict.get("name")
            or assignee_dict.get("emailAddress")
            or "unknown_jira_assignee"
        )
        assignee_pseudo = await resolve_or_create_contributor_pseudonym(
            db,
            tenant_id=tenant_id,
            provider="jira",
            external_actor_id=str(assignee_actor),
            raw_email=assignee_dict.get("emailAddress"),
            raw_name=assignee_dict.get("displayName") or assignee_dict.get("name"),
            raw_handle=assignee_dict.get("name"),
        )

    # 1. Check existing issue under tenant RLS
    stmt = select(Issue).where(
        Issue.tenant_id == tenant_id,
        Issue.external_key == external_key,
    )
    existing_issue = (await db.execute(stmt)).scalar_one_or_none()

    if existing_issue is not None:
        # Invariant 2: Monotonic Changelog ID Version Precedence (TC-JIRA-EDGE-007)
        if (
            changelog_id is not None
            and existing_issue.changelog_id is not None
            and changelog_id <= existing_issue.changelog_id
        ):
            logger.warning(
                "Stale out-of-order Jira webhook received for %s: changelog_id=%s <= %s; discarding event.",
                external_key,
                changelog_id,
                existing_issue.changelog_id,
            )
            await complete_webhook_event(db, event_id, tenant_id=tenant_id)
            return "stale_rejected:changelog_precedence"

        # Invariant 5: Issue Status Transition Audit History & Duration (TC-JIRA-TR-003)
        old_status = existing_issue.status
        if old_status != status_name:
            prev_tr_stmt = (
                select(IssueTransition)
                .where(
                    IssueTransition.tenant_id == tenant_id,
                    IssueTransition.issue_id == existing_issue.id,
                )
                .order_by(IssueTransition.transitioned_at.desc())
                .limit(1)
            )
            prev_tr = (await db.execute(prev_tr_stmt)).scalar_one_or_none()
            prev_time = prev_tr.transitioned_at if prev_tr else existing_issue.created_at
            duration_sec = (
                int((event_ts - _ensure_utc(prev_time)).total_seconds()) if prev_time else None
            )
            if duration_sec is not None and duration_sec < 0:
                duration_sec = 0

            new_tr = IssueTransition(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                issue_id=existing_issue.id,
                from_status=old_status,
                to_status=status_name,
                duration_seconds=duration_sec,
                transitioned_at=event_ts,
            )
            db.add(new_tr)

        # Update existing issue fields
        existing_issue.title = title
        existing_issue.status = status_name
        existing_issue.issue_type = issue_type
        if external_issue_id is not None:
            existing_issue.external_issue_id = external_issue_id
        if story_points is not None:
            existing_issue.story_points = story_points
        if issue_links:
            existing_issue.issue_links = issue_links
        if changelog_id is not None:
            existing_issue.changelog_id = changelog_id
        if resolved_ts is not None:
            existing_issue.resolved_at = resolved_ts
        elif not is_terminal and existing_issue.resolved_at is not None:
            # Reopened issue clears resolved_at
            existing_issue.resolved_at = None
        if assignee_pseudo:
            existing_issue.assignee_pseudonym_id = assignee_pseudo
        existing_issue.updated_at = event_ts

        # Invariant 1: Acceptance Criteria Versioning (TC-JIRA-AC-002)
        if ac_text and ac_hash:
            if ac_hash != existing_issue.acceptance_criteria_hash:
                max_v_stmt = select(
                    func.coalesce(func.max(AcceptanceCriteriaVersion.version), 0)
                ).where(
                    AcceptanceCriteriaVersion.issue_id == existing_issue.id,
                    AcceptanceCriteriaVersion.tenant_id == tenant_id,
                )
                max_v = (await db.execute(max_v_stmt)).scalar() or 0
                next_v = max_v + 1

                ac_version = AcceptanceCriteriaVersion(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    issue_id=existing_issue.id,
                    version=next_v,
                    raw_criteria_text=ac_text,
                    content_hash=ac_hash,
                    lexical_tokens_count=len(ac_text.split()),
                    created_at=event_ts,
                )
                db.add(ac_version)
                existing_issue.acceptance_criteria = ac_text
                existing_issue.acceptance_criteria_hash = ac_hash
                existing_issue.entity_version += 1

        target_issue = existing_issue
        await db.flush()

    else:
        # Initial Story / Issue Creation (TC-JIRA-AC-001)
        new_issue = Issue(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            integration_id=integration_id,
            external_key=external_key,
            external_issue_id=external_issue_id,
            issue_type=issue_type,
            title=title,
            status=status_name,
            story_points=story_points,
            assignee_pseudonym_id=assignee_pseudo,
            issue_links=issue_links,
            changelog_id=changelog_id,
            active_fencing_token=int(payload.get("active_fencing_token") or 0),
            acceptance_criteria=ac_text,
            acceptance_criteria_hash=ac_hash,
            resolved_at=resolved_ts,
            entity_version=1,
            created_at=created_ts,
            updated_at=event_ts,
        )
        db.add(new_issue)
        await db.flush()

        if ac_text and ac_hash:
            ac_v1 = AcceptanceCriteriaVersion(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                issue_id=new_issue.id,
                version=1,
                raw_criteria_text=ac_text,
                content_hash=ac_hash,
                lexical_tokens_count=len(ac_text.split()),
                created_at=created_ts,
            )
            db.add(ac_v1)
            await db.flush()

        target_issue = new_issue

    # 2. Incident Correlation & Cancellation Engine (SCRUM-401 / Task 67.4)
    is_incident = target_issue.issue_type.strip().lower() in (
        "incident",
        "outage",
        "production incident",
    )
    is_cancelled = target_issue.status.strip().lower() in CANCELLATION_STATUSES

    if is_incident:
        if is_cancelled:
            # TC-JIRA-CANC-006: Incident Transitioned to Cancelled (Unlinked / Excluded from CFR)
            await soft_unlink_cancelled_incident(
                db,
                tenant_id=tenant_id,
                issue_id=target_issue.id,
                cancelled_at=resolved_ts or event_ts,
            )
        else:
            # TC-JIRA-CORR-004 & TC-JIRA-CORR-005: 24-Hour Incident Correlation
            detected_at = created_ts
            await correlate_incident_to_deployment_in_session(
                db,
                tenant_id=tenant_id,
                project_id=project_id,
                issue_id=target_issue.id,
                detected_at=detected_at,
            )

    await complete_webhook_event(db, event_id, tenant_id=tenant_id)
    return f"persisted_jira_issue:{target_issue.id}"
