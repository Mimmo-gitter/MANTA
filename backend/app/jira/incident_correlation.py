"""Incident -> production deployment correlation (Task 67.4)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Deployment, DeploymentIncident, Issue

logger = logging.getLogger("app.jira.incident_correlation")

CORRELATION_WINDOW = timedelta(hours=24)
CORRELATION_SOURCE = "auto_24h_window"
PRODUCTION_ENVIRONMENT = "production"  # check: SELECT DISTINCT environment FROM deployments
CANCELLED_STATUSES = {"cancelled", "canceled", "rejected", "duplicate", "won't do", "won’t do"}


async def correlate_incident(db: AsyncSession, issue: Issue) -> Optional[DeploymentIncident]:
    if (issue.issue_type or "").lower() != "incident":
        return None
    incident_at = issue.created_at
    if incident_at is None:
        return None

    # Idempotency: an active link for this issue already exists.
    active = (
        await db.execute(
            select(DeploymentIncident).where(
                DeploymentIncident.tenant_id == issue.tenant_id,
                DeploymentIncident.issue_id == issue.id,
                DeploymentIncident.is_active.is_(True),
            )
        )
    ).scalars().first()
    if active is not None:
        return active

    deployment = (
        await db.execute(
            select(Deployment)
            .where(
                Deployment.tenant_id == issue.tenant_id,
                Deployment.project_id == issue.project_id,
                Deployment.environment == PRODUCTION_ENVIRONMENT,
                Deployment.deployed_at <= incident_at,
                Deployment.deployed_at >= incident_at - CORRELATION_WINDOW,
            )
            .order_by(Deployment.deployed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if deployment is None:
        return None

    link = (
        await db.execute(
            select(DeploymentIncident).where(
                DeploymentIncident.tenant_id == issue.tenant_id,
                DeploymentIncident.deployment_id == deployment.id,
                DeploymentIncident.issue_id == issue.id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        link = DeploymentIncident(
            tenant_id=issue.tenant_id,
            deployment_id=deployment.id,
            issue_id=issue.id,
            status="CONFIRMED",
            correlation_source=CORRELATION_SOURCE,
            detected_at=incident_at,
            is_active=True,
        )
        db.add(link)
    else:  # previously cancelled incident was reopened
        link.status = "CONFIRMED"
        link.is_active = True

    deployment.is_change_failure = True
    await db.flush()
    return link


async def unlink_cancelled_incident(db: AsyncSession, issue: Issue) -> int:
    if (issue.status or "").strip().lower() not in CANCELLED_STATUSES:
        return 0

    links = (
        await db.execute(
            select(DeploymentIncident).where(
                DeploymentIncident.tenant_id == issue.tenant_id,
                DeploymentIncident.issue_id == issue.id,
                DeploymentIncident.is_active.is_(True),
            )
        )
    ).scalars().all()

    for link in links:
        link.status = "CANCELLED"
        link.is_active = False
    await db.flush()  # autoflush is off: persist before checking other links

    for link in links:
        other_active = (
            await db.execute(
                select(DeploymentIncident.id).where(
                    DeploymentIncident.tenant_id == issue.tenant_id,
                    DeploymentIncident.deployment_id == link.deployment_id,
                    DeploymentIncident.is_active.is_(True),
                )
            )
        ).first()
        if other_active is None:
            deployment = await db.get(Deployment, link.deployment_id)
            if deployment is not None:
                deployment.is_change_failure = False
    await db.flush()
    return len(links)
