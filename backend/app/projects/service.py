"""
SCRUM-377: Project & Repository Service Layer.

Provides business logic for project lifecycle management and repository linking
under PostgreSQL Row-Level Security (RLS) tenant isolation.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.auth.schemas import TokenClaims
from app.db.models.models import Integration, Project, ProjectLink, Repository, User
from app.db.session import get_tenant_db_context
from app.projects.schemas import (
    ProjectCreateRequest,
    ProjectResponse,
    RepositoryLinkRequest,
    RepositoryResponse,
)

logger = logging.getLogger("app.projects.service")


def slugify(text: str) -> str:
    """Generate a clean, url-friendly slug from arbitrary string."""
    text = text.strip().lower()
    # Replace non-alphanumeric characters with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text)
    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:64] or "project"


async def create_project(
    tenant_id: uuid.UUID,
    user: TokenClaims,
    data: ProjectCreateRequest,
) -> ProjectResponse:
    """
    Creates a new project scoped to the authenticated tenant.
    Enforces uniqueness of project slug within tenant (uq_projects_tenant_slug).
    """
    slug = (data.slug or slugify(data.name)).strip().lower()[:64]
    if not slug:
        slug = slugify(data.name)

    async with get_tenant_db_context(tenant_id) as session:
        # Determine internal user ID for audit/provenance
        conditions = [User.google_sub == user.sub]
        if user.email:
            conditions.append(User.email == user.email)

        user_stmt = select(User.id).where(
            User.tenant_id == tenant_id,
            or_(*conditions),
        )
        user_res = await session.execute(user_stmt)
        creator_id: Optional[uuid.UUID] = user_res.scalars().first()

        now = datetime.now(timezone.utc)
        project = Project(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            created_by=creator_id,
            name=data.name.strip(),
            slug=slug,
            tier_sla_hours=data.tier_sla_hours,
            authoritative_cd_signal=data.authoritative_cd_signal,
            lead_time_first_commit_strategy=data.lead_time_first_commit_strategy,
            wip_limit_per_dev=data.wip_limit_per_dev,
            pr_stale_threshold_hours=data.pr_stale_threshold_hours,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(project)

        try:
            await session.flush()
        except IntegrityError as exc:
            err_msg = str(exc.orig) if exc.orig else str(exc)
            if "uq_projects_tenant_slug" in err_msg or "slug" in err_msg.lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Project with slug '{slug}' already exists in this organization.",
                ) from exc
            raise

        # Eagerly refresh to populate empty repositories collection cleanly
        stmt = (
            select(Project)
            .options(selectinload(Project.repositories))
            .where(Project.id == project.id, Project.tenant_id == tenant_id)
        )
        res = await session.execute(stmt)
        refreshed = res.scalars().one()
        return ProjectResponse.model_validate(refreshed)


async def list_projects(tenant_id: uuid.UUID) -> list[ProjectResponse]:
    """
    Lists all non-deleted projects for the given tenant,
    with their associated repositories eagerly loaded.
    """
    async with get_tenant_db_context(tenant_id) as session:
        stmt = (
            select(Project)
            .options(selectinload(Project.repositories))
            .where(
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
            )
            .order_by(Project.created_at.desc())
        )
        res = await session.execute(stmt)
        projects = res.scalars().all()
        return [ProjectResponse.model_validate(p) for p in projects]


async def get_project(tenant_id: uuid.UUID, project_id: uuid.UUID) -> ProjectResponse:
    """
    Retrieves a single project by ID under tenant isolation.
    Returns HTTP 404 if project does not exist or belongs to another tenant.
    """
    async with get_tenant_db_context(tenant_id) as session:
        stmt = (
            select(Project)
            .options(selectinload(Project.repositories))
            .where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
            )
        )
        res = await session.execute(stmt)
        project = res.scalars().first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )
        return ProjectResponse.model_validate(project)


async def link_repository(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    data: RepositoryLinkRequest,
) -> RepositoryResponse:
    """
    Links a GitHub repository to a project.
    Creates rows in 'repositories' and 'project_links' tied to the tenant's GitHub integration.
    Handles duplicate external repo ID conflicts with HTTP 409.
    """
    async with get_tenant_db_context(tenant_id) as session:
        # 1. Verify project exists under this tenant
        proj_stmt = select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
            Project.deleted_at.is_(None),
        )
        proj_res = await session.execute(proj_stmt)
        project = proj_res.scalars().first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )

        # 2. Resolve active GitHub integration for the tenant
        if data.integration_id:
            integ_stmt = select(Integration).where(
                Integration.id == data.integration_id,
                Integration.tenant_id == tenant_id,
                Integration.provider == "github",
                Integration.is_active.is_(True),
            )
            integ_res = await session.execute(integ_stmt)
            integration = integ_res.scalars().first()
            if not integration:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"GitHub integration '{data.integration_id}' is invalid or inactive.",
                )
        else:
            integ_stmt = select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.provider == "github",
                Integration.is_active.is_(True),
            )
            integ_res = await session.execute(integ_stmt)
            integration = integ_res.scalars().first()
            if not integration:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No active GitHub integration found for this tenant. Please connect GitHub first.",
                )

        # 3. Create repository and project_link records
        now = datetime.now(timezone.utc)
        repo_id = uuid.uuid4()
        repo = Repository(
            id=repo_id,
            tenant_id=tenant_id,
            project_id=project_id,
            integration_id=integration.id,
            external_repo_id=data.external_repo_id,
            full_name=data.full_name.strip(),
            default_branch=data.default_branch.strip() or "main",
            is_private=data.is_private,
            created_at=now,
        )
        session.add(repo)

        link = ProjectLink(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            integration_id=integration.id,
            link_type="github_repository",
            external_id=str(data.external_repo_id),
            created_at=now,
        )
        session.add(link)

        try:
            await session.flush()
        except IntegrityError as exc:
            err_msg = str(exc.orig) if exc.orig else str(exc)
            if "uq_repo_external" in err_msg or "external_repo_id" in err_msg.lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Repository with external ID '{data.external_repo_id}' is already linked in this organization.",
                ) from exc
            if "uq_project_link" in err_msg:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Repository '{data.full_name}' is already linked to this project.",
                ) from exc
            raise

        return RepositoryResponse.model_validate(repo)


async def unlink_repository(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> None:
    """
    Unlinks a repository from a project:
    Deletes the repository record and its corresponding project_link.
    """
    async with get_tenant_db_context(tenant_id) as session:
        # 1. Verify project exists
        proj_stmt = select(Project.id).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
            Project.deleted_at.is_(None),
        )
        proj_res = await session.execute(proj_stmt)
        if not proj_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )

        # 2. Find repository
        repo_stmt = select(Repository).where(
            Repository.id == repo_id,
            Repository.project_id == project_id,
            Repository.tenant_id == tenant_id,
        )
        repo_res = await session.execute(repo_stmt)
        repo = repo_res.scalars().first()
        if not repo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository '{repo_id}' not found in project '{project_id}'.",
            )

        # 3. Delete matching project_links
        del_link_stmt = delete(ProjectLink).where(
            ProjectLink.project_id == project_id,
            ProjectLink.tenant_id == tenant_id,
            ProjectLink.external_id == str(repo.external_repo_id),
        )
        await session.execute(del_link_stmt)

        # 4. Delete repository
        await session.delete(repo)
