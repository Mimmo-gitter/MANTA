from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Integration
from app.jira.normalization import normalize_jira_event
from app.jira.service import upsert_issue, upsert_sprint
from app.jira.incident_correlation import (
    CANCELLED_STATUSES,
    correlate_incident,
    unlink_cancelled_incident,
)

logger = logging.getLogger("app.jira.webhook_handler")


async def handle_jira_event(db: AsyncSession, event: Any, tenant_id: uuid.UUID) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    n = normalize_jira_event(event.event_type, payload, received_at=event.received_at)
    if n.kind == "noop":
        return f"noop:{n.reason}"

    project_id = None
    if event.integration_id is not None:
        project_id = (
            await db.execute(
                select(Integration.project_id).where(
                    Integration.id == event.integration_id,
                    Integration.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
    if project_id is None:
        logger.warning("Jira webhook %s has no project-linked integration", event.id)
        return "noop:unlinked_jira_integration"

    if n.kind == "sprint":
        sprint = await upsert_sprint(db, tenant_id=tenant_id, project_id=project_id, s=n.sprint)
        return f"persisted_sprint:{sprint.id}"

    issue, applied = await upsert_issue(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        n=n.issue,
        integration_id=event.integration_id,
    )

    if not applied:
        return f"noop:stale_issue_version:{issue.external_key}"

    if applied and (issue.issue_type or "").lower() == "incident":
        if (issue.status or "").strip().lower() in CANCELLED_STATUSES:
            await unlink_cancelled_incident(db, issue)
        else:
            await correlate_incident(db, issue)

    return f"persisted_issue:{issue.id}"
