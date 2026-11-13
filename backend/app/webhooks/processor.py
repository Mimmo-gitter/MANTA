"""Transactional webhook processor for staged GitHub and ArgoCD deployment events (Task 25.3)."""

import logging
from typing import Any, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Integration, WebhookEvent
from app.db.session import get_tenant_db_context
from app.deployments.normalization import (
    NormalizationResult,
    normalize_argocd_postsync,
    normalize_github_deployment_status,
)
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.deployments.service import (
    persist_deployment_idempotent,
    resolve_argocd_project,
    resolve_github_project,
)
from app.webhooks.sweeper import cas_transition_to_processing, complete_webhook_event

logger = logging.getLogger("app.webhooks.processor")


def _extract_event_and_tenant_ids(parsed_payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    """Extract (event_id, tenant_id) from either MantaJobEnvelope or flat payload."""
    inner = (
        parsed_payload.get("payload")
        if isinstance(parsed_payload.get("payload"), dict)
        else parsed_payload
    )
    raw_event_id = inner.get("event_id") or parsed_payload.get("event_id") or parsed_payload.get("job_id")
    raw_tenant_id = inner.get("tenant_id") or parsed_payload.get("tenant_id")
    if not raw_event_id or not raw_tenant_id:
        raise ValueError(
            f"Webhook job payload missing event_id or tenant_id: keys={sorted(parsed_payload.keys())}"
        )
    return uuid.UUID(str(raw_event_id)), uuid.UUID(str(raw_tenant_id))


async def process_staged_deployment_event_in_session(
    db: AsyncSession,
    *,
    event_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str:
    """
    Process a single staged WebhookEvent inside an existing tenant-RLS AsyncSession.

    Claims via cas_transition_to_processing, normalizes GitHub deployment_status or
    ArgoCD PostSync events, resolves project under tenant RLS, pseudonymizes actor
    via Task 20.3, upserts Deployment idempotently, and marks event completed.
    Exceptions propagate intentionally so run_worker_fetch_loop handles retries & DLQ.
    """
    claimed = await cas_transition_to_processing(db, event_id, tenant_id=tenant_id)
    if not claimed:
        return "skipped_already_claimed_or_completed"

    event = (
        await db.execute(
            select(WebhookEvent).where(
                WebhookEvent.id == event_id,
                WebhookEvent.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if event is None:
        return "skipped_missing_event"

    provider = (event.provider or "").strip().lower()
    payload = event.payload if isinstance(event.payload, dict) else {}

    normalized: Optional[NormalizationResult] = None
    if provider == "github":
        normalized = normalize_github_deployment_status(
            event.event_type,
            payload,
            received_at=event.received_at,
        )
    elif provider == "argocd":
        normalized = normalize_argocd_postsync(
            event.event_type,
            payload,
            received_at=event.received_at,
        )
    else:
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return f"noop_unsupported_provider:{provider}"

    if normalized.kind == "noop":
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return f"noop:{normalized.reason or 'ignored'}"

    assert normalized.outcome is not None
    assert normalized.commit_sha is not None
    assert normalized.environment is not None
    assert normalized.deployed_at is not None
    assert normalized.authoritative_signal_source is not None

    project_id: Optional[uuid.UUID] = None
    repository_id: Optional[uuid.UUID] = None

    if normalized.authoritative_signal_source == DeploymentSignalSource.GITHUB_DEPLOYMENTS_API:
        project_id, repository_id = await resolve_github_project(
            db,
            tenant_id=tenant_id,
            repo_external_id=normalized.repo_external_id,
            repo_full_name=normalized.repo_full_name,
        )
        if project_id is None:
            logger.warning(
                "Skipping unlinked GitHub deployment webhook %s (repo_id=%s, full_name=%s)",
                event_id,
                normalized.repo_external_id,
                normalized.repo_full_name,
            )
            await complete_webhook_event(db, event_id, tenant_id=tenant_id)
            return "noop:unlinked_github_repository"

    elif normalized.authoritative_signal_source == DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK:
        integration_project_id: Optional[uuid.UUID] = None
        if event.integration_id is not None:
            integration_project_id = (
                await db.execute(
                    select(Integration.project_id).where(
                        Integration.id == event.integration_id,
                        Integration.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()

        project_id = await resolve_argocd_project(
            db,
            tenant_id=tenant_id,
            app_name=normalized.app_name,
            hint_project_id=normalized.hint_project_id,
            hint_project_slug=normalized.hint_project_slug,
            repo_url=normalized.repo_url,
            integration_project_id=integration_project_id,
        )
        if project_id is None:
            logger.warning(
                "Skipping unmapped ArgoCD PostSync webhook %s (app_name=%s, repo_url=%s)",
                event_id,
                normalized.app_name,
                normalized.repo_url,
            )
            await complete_webhook_event(db, event_id, tenant_id=tenant_id)
            return "noop:unmapped_argocd_project"

    assert project_id is not None

    actor_default_provider = (
        "github"
        if normalized.authoritative_signal_source == DeploymentSignalSource.GITHUB_DEPLOYMENTS_API
        else "argocd"
    )
    deployment, deduplicated = await persist_deployment_idempotent(
        db=db,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repository_id,
        integration_id=event.integration_id,
        commit_sha=normalized.commit_sha,
        environment=normalized.environment,
        outcome=DeploymentOutcome(normalized.outcome),
        deployed_at=normalized.deployed_at,
        authoritative_signal_source=normalized.authoritative_signal_source,
        external_deployment_id=normalized.external_deployment_id,
        actor=normalized.actor,
        actor_default_provider=actor_default_provider,
    )

    await complete_webhook_event(db, event_id, tenant_id=tenant_id)
    logger.info(
        "Processed %s webhook %s -> deployment %s (status=%s, deduplicated=%s)",
        provider,
        event_id,
        deployment.id,
        deployment.status,
        deduplicated,
    )
    return f"persisted:{deployment.id}"


async def process_webhook_job(parsed_payload: dict[str, Any]) -> str:
    """
    Worker entrypoint invoked for queued 'webhook.process' jobs inside an isolated
    tenant-RLS transaction so transient errors roll back cleanly and trigger Task 23.3 retries.
    """
    event_id, tenant_id = _extract_event_and_tenant_ids(parsed_payload)
    async with get_tenant_db_context(tenant_id) as session:
        return await process_staged_deployment_event_in_session(
            session,
            event_id=event_id,
            tenant_id=tenant_id,
        )
