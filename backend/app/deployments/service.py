"""Service layer for deployment persistence, project resolution, and authoritative queries."""

from datetime import datetime
import logging
from typing import Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Role, resolve_caller_roles
from app.auth.schemas import TokenClaims
from app.contributors.service import resolve_or_create_contributor_pseudonym
from app.db.models.models import (
    Deployment,
    Project,
    ProjectLink,
    ProjectMember,
    Repository,
    User,
)
from app.deployments.normalization import extract_github_owner_repo, normalize_environment
from app.deployments.schemas import (
    DeploymentActorInput,
    DeploymentOutcome,
    DeploymentSignalSource,
    DirectDeploymentIngestRequest,
    DirectDeploymentResponse,
)

logger = logging.getLogger("app.deployments.service")


async def resolve_github_project(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repo_external_id: Optional[int],
    repo_full_name: Optional[str],
) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
    """Resolve (project_id, repository_id) for an active project from GitHub repository metadata."""
    if repo_external_id is not None:
        stmt = (
            select(Repository.project_id, Repository.id)
            .join(Project, Project.id == Repository.project_id)
            .where(
                Repository.tenant_id == tenant_id,
                Repository.external_repo_id == repo_external_id,
                Project.deleted_at.is_(None),
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if row is not None:
            return row[0], row[1]

    if repo_full_name:
        stmt = (
            select(Repository.project_id, Repository.id)
            .join(Project, Project.id == Repository.project_id)
            .where(
                Repository.tenant_id == tenant_id,
                Repository.full_name.ilike(repo_full_name),
                Project.deleted_at.is_(None),
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if row is not None:
            return row[0], row[1]

    return None, None


async def resolve_argocd_project(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    app_name: Optional[str] = None,
    hint_project_id: Optional[str] = None,
    hint_project_slug: Optional[str] = None,
    repo_url: Optional[str] = None,
    integration_project_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """
    Deterministically resolve active Project.id for an ArgoCD webhook under tenant RLS:
    1. ProjectLink(link_type='argocd_application', external_id=app_name)
    2. Explicit project UUID hint (label/annotation/payload) verified in tenant
    3. Explicit project slug hint
    4. Unambiguous Repository.full_name matched from repoURL
    5. Integration.project_id bound on the webhook's integration row
    """
    if app_name:
        stmt = (
            select(ProjectLink.project_id)
            .join(Project, Project.id == ProjectLink.project_id)
            .where(
                ProjectLink.tenant_id == tenant_id,
                ProjectLink.link_type == "argocd_application",
                ProjectLink.external_id == app_name,
                Project.deleted_at.is_(None),
            )
            .limit(1)
        )
        pid = (await db.execute(stmt)).scalar_one_or_none()
        if pid is not None:
            return pid

    if hint_project_id:
        try:
            parsed_pid = uuid.UUID(hint_project_id)
        except ValueError:
            parsed_pid = None
        if parsed_pid is not None:
            stmt = select(Project.id).where(
                Project.tenant_id == tenant_id,
                Project.id == parsed_pid,
                Project.deleted_at.is_(None),
            )
            pid = (await db.execute(stmt)).scalar_one_or_none()
            if pid is not None:
                return pid

    if hint_project_slug:
        stmt = select(Project.id).where(
            Project.tenant_id == tenant_id,
            Project.slug == hint_project_slug,
            Project.deleted_at.is_(None),
        )
        pid = (await db.execute(stmt)).scalar_one_or_none()
        if pid is not None:
            return pid

    owner_repo = extract_github_owner_repo(repo_url)
    if owner_repo:
        stmt = (
            select(Repository.project_id)
            .join(Project, Project.id == Repository.project_id)
            .where(
                Repository.tenant_id == tenant_id,
                Repository.full_name.ilike(owner_repo),
                Project.deleted_at.is_(None),
            )
            .limit(2)
        )
        matches = list((await db.execute(stmt)).scalars().all())
        if len(matches) == 1:
            return matches[0]

    if integration_project_id is not None:
        stmt = select(Project.id).where(
            Project.tenant_id == tenant_id,
            Project.id == integration_project_id,
            Project.deleted_at.is_(None),
        )
        pid = (await db.execute(stmt)).scalar_one_or_none()
        if pid is not None:
            return pid

    return None


async def persist_deployment_idempotent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    repository_id: Optional[uuid.UUID],
    integration_id: Optional[uuid.UUID],
    commit_sha: str,
    environment: str,
    outcome: DeploymentOutcome,
    deployed_at: datetime,
    authoritative_signal_source: DeploymentSignalSource,
    external_deployment_id: Optional[str],
    actor: Optional[DeploymentActorInput],
    actor_default_provider: str,
) -> tuple[Deployment, bool]:
    """
    Pseudonymize actor (Task 20.3) and persist a Deployment record with monotonic idempotency.

    Returns (deployment_row, deduplicated).
    """
    actor_pseudonym_id: Optional[str] = None
    if actor is not None:
        provider, ext_actor_id, raw_email, raw_name, raw_handle = actor.to_contributor_params(
            actor_default_provider
        )
        if ext_actor_id:
            actor_pseudonym_id = await resolve_or_create_contributor_pseudonym(
                db=db,
                tenant_id=tenant_id,
                provider=provider,
                external_actor_id=ext_actor_id,
                raw_email=raw_email,
                raw_name=raw_name,
                raw_handle=raw_handle,
            )

    is_change_failure = outcome in (DeploymentOutcome.FAILED, DeploymentOutcome.ROLLED_BACK)

    if external_deployment_id is None:
        deployment = Deployment(
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repository_id,
            integration_id=integration_id,
            triggered_by=None,
            actor_pseudonym_id=actor_pseudonym_id,
            external_deployment_id=None,
            environment=environment,
            commit_sha=commit_sha,
            status=outcome.value,
            is_change_failure=is_change_failure,
            authoritative_signal_source=authoritative_signal_source.value,
            deployed_at=deployed_at,
        )
        db.add(deployment)
        await db.flush()
        return deployment, False

    clean_ext_id = external_deployment_id.strip()[:128]
    stmt = pg_insert(Deployment).values(
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repository_id,
        integration_id=integration_id,
        triggered_by=None,
        actor_pseudonym_id=actor_pseudonym_id,
        external_deployment_id=clean_ext_id,
        environment=environment,
        commit_sha=commit_sha,
        status=outcome.value,
        is_change_failure=is_change_failure,
        authoritative_signal_source=authoritative_signal_source.value,
        deployed_at=deployed_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            Deployment.tenant_id,
            Deployment.project_id,
            Deployment.authoritative_signal_source,
            Deployment.external_deployment_id,
        ],
        index_where=text("external_deployment_id IS NOT NULL"),
        set_={
            "commit_sha": stmt.excluded.commit_sha,
            "environment": stmt.excluded.environment,
            "status": stmt.excluded.status,
            "is_change_failure": stmt.excluded.is_change_failure,
            "deployed_at": stmt.excluded.deployed_at,
            "repository_id": func.coalesce(stmt.excluded.repository_id, Deployment.repository_id),
            "integration_id": func.coalesce(
                stmt.excluded.integration_id, Deployment.integration_id
            ),
            "actor_pseudonym_id": func.coalesce(
                stmt.excluded.actor_pseudonym_id, Deployment.actor_pseudonym_id
            ),
        },
        where=(stmt.excluded.deployed_at >= Deployment.deployed_at),
    ).returning(Deployment.id, text("(xmax = 0) AS inserted"))

    row = (await db.execute(stmt)).first()
    if row is None:
        # Stale out-of-order retry discarded by monotonic WHERE clause; return current winner.
        existing_stmt = select(Deployment).where(
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.authoritative_signal_source == authoritative_signal_source.value,
            Deployment.external_deployment_id == clean_ext_id,
        )
        existing = (await db.execute(existing_stmt)).scalar_one()
        return existing, True

    deployment_id, inserted = row[0], bool(row[1])
    persisted = await db.get(Deployment, deployment_id)
    assert persisted is not None
    return persisted, (not inserted)


async def _enforce_abac_project_assignment_if_non_admin(
    db: AsyncSession,
    *,
    user: TokenClaims,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """
    SRS §3.2.1.3 ABAC safeguard: if a human DB user without TENANT_ADMIN invokes
    the direct deployment ingestion endpoint, require explicit ProjectMember assignment.
    """
    roles = await resolve_caller_roles(user)
    if Role.TENANT_ADMIN.value in roles:
        return

    user_row = (
        await db.execute(
            select(User.id).where(
                User.tenant_id == tenant_id,
                User.google_sub == user.sub,
            )
        )
    ).scalar_one_or_none()
    if user_row is None:
        # M2M principal without users table entry relies on RBAC role gate + tenant RLS
        return

    member_id = (
        await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.tenant_id == tenant_id,
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_row,
            )
        )
    ).scalar_one_or_none()
    if member_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: caller is not assigned to this project",
        )


async def ingest_direct_deployment(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: DirectDeploymentIngestRequest,
    user: TokenClaims,
) -> DirectDeploymentResponse:
    """Ingest a direct REST deployment report under tenant RLS and Task 20.3 pseudonymization."""
    project = (
        await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found",
        )

    await _enforce_abac_project_assignment_if_non_admin(
        db,
        user=user,
        tenant_id=tenant_id,
        project_id=project.id,
    )

    if payload.repository_id is not None:
        repo = (
            await db.execute(
                select(Repository.id).where(
                    Repository.id == payload.repository_id,
                    Repository.project_id == project.id,
                    Repository.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository '{payload.repository_id}' not found in project",
            )

    canonical_env, is_prod = normalize_environment(payload.environment)

    deployment, deduplicated = await persist_deployment_idempotent(
        db=db,
        tenant_id=tenant_id,
        project_id=project.id,
        repository_id=payload.repository_id,
        integration_id=None,
        commit_sha=payload.commit_sha,
        environment=canonical_env,
        outcome=payload.outcome,
        deployed_at=payload.deployed_at,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        external_deployment_id=payload.external_deployment_id,
        actor=payload.actor,  # type: ignore[arg-type]
        actor_default_provider=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
    )

    return DirectDeploymentResponse(
        id=deployment.id,
        tenant_id=deployment.tenant_id,
        project_id=deployment.project_id,
        repository_id=deployment.repository_id,
        commit_sha=deployment.commit_sha,
        environment=deployment.environment,
        is_production=is_prod,
        status=DeploymentOutcome(deployment.status),
        is_change_failure=deployment.is_change_failure,
        authoritative_signal_source=DeploymentSignalSource(deployment.authoritative_signal_source),
        is_authoritative_for_project=(
            project.authoritative_cd_signal == DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value
        ),
        actor_pseudonym_id=deployment.actor_pseudonym_id,
        external_deployment_id=deployment.external_deployment_id,
        deployed_at=deployment.deployed_at,
        created_at=deployment.created_at,
        deduplicated=deduplicated,
    )


def build_authoritative_production_deployments_query(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> Select[tuple[Deployment]]:
    """
    Canonical DORA base query filtering out non-production environments (staging/dev/qa/preview)
    and excluding non-authoritative signal sources that do not match Project.authoritative_cd_signal.
    Uses composite index idx_deployments_project_env_deployed_at.
    """
    stmt = (
        select(Deployment)
        .join(Project, Project.id == Deployment.project_id)
        .where(
            Deployment.tenant_id == tenant_id,
            Deployment.project_id == project_id,
            Deployment.environment == "production",
            Deployment.authoritative_signal_source == Project.authoritative_cd_signal,
            Project.deleted_at.is_(None),
        )
        .order_by(Deployment.deployed_at.desc())
    )
    if start_at is not None:
        stmt = stmt.where(Deployment.deployed_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(Deployment.deployed_at <= end_at)
    return stmt


async def list_authoritative_production_deployments(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> list[Deployment]:
    """Return authoritative production Deployment rows for a project within tenant RLS."""
    stmt = build_authoritative_production_deployments_query(
        tenant_id=tenant_id,
        project_id=project_id,
        start_at=start_at,
        end_at=end_at,
    )
    return list((await db.execute(stmt)).scalars().all())
