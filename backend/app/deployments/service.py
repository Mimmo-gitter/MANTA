"""Service layer for deployment persistence, project resolution, and authoritative queries."""

from collections import deque
from datetime import datetime, timezone
import logging
from typing import AbstractSet, Mapping, Optional, Sequence
import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Role, resolve_caller_roles
from app.auth.schemas import TokenClaims
from app.contributors.service import resolve_or_create_contributor_pseudonym
from app.db.models.models import (
    Commit,
    Deployment,
    DeploymentPullRequest,
    Project,
    ProjectLink,
    ProjectMember,
    PullRequest,
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
        if (
            deployment.environment == "production"
            and deployment.status == DeploymentOutcome.SUCCESS.value
        ):
            await resolve_deployment_pull_requests(db, deployment=deployment)
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
    if (
        persisted.environment == "production"
        and persisted.status == DeploymentOutcome.SUCCESS.value
    ):
        await resolve_deployment_pull_requests(db, deployment=persisted)
    return persisted, (not inserted)


def _normalize_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_commit_range_shas(
    target_commit_sha: str,
    *,
    previous_deployment_shas: Sequence[str] = (),
    commits_by_sha: Optional[Mapping[str, Commit]] = None,
    parent_shas_by_sha: Optional[Mapping[str, Sequence[str]]] = None,
    max_depth: int = 1000,
) -> set[str]:
    """
    Compute the half-open commit range S_i = (D_{i-1}, D_i] (Task 26.2 / Task 26.1 §2.2).

    Walks commit parent_shas backward from target_commit_sha while pruning any commit
    reachable from previous_deployment_shas (A_{i-1}). If target_commit_sha is already
    in A_{i-1} (e.g. re-deploy or rollback of an already-shipped SHA), returns set().
    """
    target = (target_commit_sha or "").strip().lower()
    if not target:
        return set()

    parents_map: dict[str, list[str]] = {}
    if commits_by_sha:
        for sha_key, commit_obj in commits_by_sha.items():
            norm_sha = (sha_key or getattr(commit_obj, "sha", "") or "").strip().lower()
            if not norm_sha:
                continue
            raw_parents = getattr(commit_obj, "parent_shas", None) or []
            parents_map[norm_sha] = [
                p.strip().lower() for p in raw_parents if isinstance(p, str) and p.strip()
            ]
    if parent_shas_by_sha:
        for sha_key, raw_parents in parent_shas_by_sha.items():
            norm_sha = (sha_key or "").strip().lower()
            if not norm_sha:
                continue
            parents_map[norm_sha] = [
                p.strip().lower() for p in (raw_parents or []) if isinstance(p, str) and p.strip()
            ]

    norm_prev_shas = [
        s.strip().lower() for s in previous_deployment_shas if isinstance(s, str) and s.strip()
    ]

    # Build ancestor exclusion set A_{i-1} reachable from previous SUCCESS production deployments
    ancestor_exclusion_set: set[str] = set()
    exclusion_queue: deque[tuple[str, int]] = deque()
    for prev_sha in norm_prev_shas:
        if prev_sha not in ancestor_exclusion_set:
            ancestor_exclusion_set.add(prev_sha)
            exclusion_queue.append((prev_sha, 0))

    while exclusion_queue:
        curr_sha, depth = exclusion_queue.popleft()
        if depth >= max_depth:
            continue
        for parent_sha in parents_map.get(curr_sha, ()):
            if parent_sha not in ancestor_exclusion_set:
                ancestor_exclusion_set.add(parent_sha)
                exclusion_queue.append((parent_sha, depth + 1))

    if target in ancestor_exclusion_set:
        return set()

    # Walk backward from target_commit_sha to collect S_i = (D_{i-1}, D_i]
    range_shas: set[str] = {target}
    range_queue: deque[tuple[str, int]] = deque([(target, 0)])

    while range_queue:
        curr_sha, depth = range_queue.popleft()
        if depth >= max_depth:
            continue
        for parent_sha in parents_map.get(curr_sha, ()):
            if parent_sha in ancestor_exclusion_set or parent_sha in range_shas:
                continue
            range_shas.add(parent_sha)
            range_queue.append((parent_sha, depth + 1))

    return range_shas


def match_unclaimed_pull_requests_in_range(
    deployment: Deployment,
    *,
    commit_range_shas: AbstractSet[str],
    candidate_pull_requests: Sequence[PullRequest],
    commits_in_range: Sequence[Commit] = (),
    already_claimed_pr_ids: AbstractSet[uuid.UUID] = frozenset(),
    default_branch: Optional[str] = None,
    has_previous_deployment_boundary: bool = True,
) -> list[PullRequest]:
    """
    Match unclaimed merged PRs in commit_range_shas across merge, squash, and rebase strategies
    while ignoring trunk-only non-PR commits and already-shipped PRs (Task 26.2 / Task 26.1 §3-§5).
    """
    if (
        deployment.environment != "production"
        or deployment.status != DeploymentOutcome.SUCCESS.value
    ):
        return []

    normalized_range_shas = {
        s.strip().lower() for s in commit_range_shas if isinstance(s, str) and s.strip()
    }
    if not normalized_range_shas:
        return []

    range_commit_pr_ids: set[uuid.UUID] = {
        c.pull_request_id
        for c in commits_in_range
        if c.pull_request_id is not None
        and getattr(c, "sha", None)
        and c.sha.strip().lower() in normalized_range_shas
    }

    norm_default_branch = default_branch.strip() if default_branch else None
    deployed_at_utc = (
        _normalize_utc(deployment.deployed_at) if deployment.deployed_at is not None else None
    )

    matched_prs: list[PullRequest] = []
    seen_pr_ids: set[uuid.UUID] = set()

    for pr in candidate_pull_requests:
        if pr.id in seen_pr_ids or pr.id in already_claimed_pr_ids:
            continue
        if pr.tenant_id != deployment.tenant_id or pr.project_id != deployment.project_id:
            continue
        if (
            deployment.repository_id is not None
            and pr.repository_id is not None
            and pr.repository_id != deployment.repository_id
        ):
            continue
        if (pr.state or "").strip().lower() != "merged":
            continue
        if (
            norm_default_branch
            and pr.base_branch
            and pr.base_branch.strip() != norm_default_branch
        ):
            continue
        if (
            not has_previous_deployment_boundary
            and pr.merged_at is not None
            and deployed_at_utc is not None
            and _normalize_utc(pr.merged_at) > deployed_at_utc
        ):
            continue

        merge_sha = pr.merge_commit_sha.strip().lower() if pr.merge_commit_sha else None
        head_sha = pr.head_sha.strip().lower() if pr.head_sha else None

        matched_by_merge_sha = bool(merge_sha and merge_sha in normalized_range_shas)
        matched_by_head_sha = bool(head_sha and head_sha in normalized_range_shas)
        matched_by_commit_fk = pr.id in range_commit_pr_ids

        if matched_by_merge_sha or matched_by_head_sha or matched_by_commit_fk:
            seen_pr_ids.add(pr.id)
            matched_prs.append(pr)

    return matched_prs


async def resolve_deployment_pull_requests(
    db: AsyncSession,
    *,
    deployment: Deployment,
    max_depth: int = 1000,
) -> list[DeploymentPullRequest]:
    """
    Resolve and persist DeploymentPullRequest links for a newly ingested SUCCESS production
    deployment across standard merge, squash-and-merge, and rebase-and-merge strategies (Task 26.2).
    """
    if (
        deployment.environment != "production"
        or deployment.status != DeploymentOutcome.SUCCESS.value
    ):
        return []

    # 1. Resolve preceding SUCCESS production deployment commit SHAs for boundary D_{i-1}
    prev_deployments_stmt = (
        select(Deployment.commit_sha)
        .where(
            Deployment.tenant_id == deployment.tenant_id,
            Deployment.project_id == deployment.project_id,
            Deployment.environment == "production",
            Deployment.status == DeploymentOutcome.SUCCESS.value,
            Deployment.id != deployment.id,
            Deployment.deployed_at <= deployment.deployed_at,
        )
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
    )
    if deployment.repository_id is not None:
        prev_deployments_stmt = prev_deployments_stmt.where(
            Deployment.repository_id == deployment.repository_id
        )
    if deployment.authoritative_signal_source:
        prev_deployments_stmt = prev_deployments_stmt.where(
            Deployment.authoritative_signal_source == deployment.authoritative_signal_source
        )
    prev_deployment_shas = list((await db.execute(prev_deployments_stmt)).scalars().all())

    # 2. Load repository default_branch (if repository_id is bound) and commit graph
    default_branch: Optional[str] = None
    if deployment.repository_id is not None:
        default_branch = (
            await db.execute(
                select(Repository.default_branch).where(
                    Repository.tenant_id == deployment.tenant_id,
                    Repository.id == deployment.repository_id,
                )
            )
        ).scalar_one_or_none()
        commits_stmt = select(Commit).where(
            Commit.tenant_id == deployment.tenant_id,
            Commit.repository_id == deployment.repository_id,
        )
    else:
        commits_stmt = (
            select(Commit)
            .join(Repository, Repository.id == Commit.repository_id)
            .where(
                Commit.tenant_id == deployment.tenant_id,
                Repository.project_id == deployment.project_id,
            )
        )

    commits_list = list((await db.execute(commits_stmt)).scalars().all())
    commits_by_sha: dict[str, Commit] = {
        c.sha.strip().lower(): c for c in commits_list if c.sha and c.sha.strip()
    }

    # 3. Compute half-open commit range S_i = (D_{i-1}, D_i]
    commit_range_shas = compute_commit_range_shas(
        deployment.commit_sha,
        previous_deployment_shas=prev_deployment_shas,
        commits_by_sha=commits_by_sha,
        max_depth=max_depth,
    )
    if not commit_range_shas:
        return []

    commits_in_range = [
        c for sha_key, c in commits_by_sha.items() if sha_key in commit_range_shas
    ]

    # 4. Load already-claimed pull_request_ids across deployment_pull_requests in tenant
    claimed_pr_ids = set(
        (
            await db.execute(
                select(DeploymentPullRequest.pull_request_id).where(
                    DeploymentPullRequest.tenant_id == deployment.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )

    # 5. Load candidate merged pull requests in project/repository
    pr_stmt = (
        select(PullRequest)
        .where(
            PullRequest.tenant_id == deployment.tenant_id,
            PullRequest.project_id == deployment.project_id,
            func.lower(PullRequest.state) == "merged",
        )
        .order_by(PullRequest.merged_at.asc().nulls_last(), PullRequest.external_pr_number.asc())
    )
    if deployment.repository_id is not None:
        pr_stmt = pr_stmt.where(PullRequest.repository_id == deployment.repository_id)

    candidate_prs = list((await db.execute(pr_stmt)).scalars().all())

    matched_prs = match_unclaimed_pull_requests_in_range(
        deployment,
        commit_range_shas=commit_range_shas,
        candidate_pull_requests=candidate_prs,
        commits_in_range=commits_in_range,
        already_claimed_pr_ids=claimed_pr_ids,
        default_branch=default_branch,
        has_previous_deployment_boundary=bool(prev_deployment_shas),
    )
    if not matched_prs:
        return []

    # 6. Idempotent insert into deployment_pull_requests (ON CONFLICT DO NOTHING)
    insert_values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": deployment.tenant_id,
            "deployment_id": deployment.id,
            "pull_request_id": pr.id,
        }
        for pr in matched_prs
    ]
    insert_stmt = (
        pg_insert(DeploymentPullRequest)
        .values(insert_values)
        .on_conflict_do_nothing(
            index_elements=[
                DeploymentPullRequest.deployment_id,
                DeploymentPullRequest.pull_request_id,
            ]
        )
        .returning(DeploymentPullRequest)
    )
    inserted_links = list((await db.execute(insert_stmt)).scalars().all())
    return inserted_links


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
