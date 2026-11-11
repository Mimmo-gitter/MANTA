import uuid

from fastapi import APIRouter, Depends, Response, status

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims
from app.projects.schemas import (
    ProjectCreateRequest,
    ProjectResponse,
    RepositoryLinkRequest,
    RepositoryResponse,
)
from app.projects.service import (
    create_project as create_project_service,
    get_project as get_project_service,
    link_repository as link_repository_service,
    list_projects as list_projects_service,
    unlink_repository as unlink_repository_service,
)

router = APIRouter(
    prefix="/api/v1/projects", tags=["Projects", "Sprint & Risk Management", "Stubs"]
)

# Permissions:
# - Mutations (create project, link/unlink repos) require administrative tenant control:
#   Permission.MANAGE_WEBHOOK_INTEGRATIONS strictly grants access ONLY to TENANT_ADMIN (HTTP 403 for DEVELOPER).
# - Queries (list, get project) require project viewing authority:
#   Permission.VIEW_PROJECT_DORA allows authorized tenant roles.
require_tenant_admin = require_permission(Permission.MANAGE_WEBHOOK_INTEGRATIONS)
require_project_view = require_permission(Permission.VIEW_PROJECT_DORA)


# ==============================================================================
# SCRUM-377: PROJECT CRUD & REPOSITORY LINKING ENDPOINTS
# ==============================================================================


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project",
    description="Creates a project record scoped to the authenticated tenant under PostgreSQL RLS.",
)
async def create_project(
    payload: ProjectCreateRequest,
    user: TokenClaims = Depends(require_tenant_admin),
) -> ProjectResponse:
    """Creates a new project under the caller's tenant context. Requires TENANT_ADMIN."""
    return await create_project_service(user.tenant_uuid, user, payload)


@router.get(
    "",
    response_model=list[ProjectResponse],
    summary="List all tenant projects",
    description="Retrieves all active projects and linked repositories for the caller's tenant.",
)
async def list_projects(
    user: TokenClaims = Depends(require_project_view),
) -> list[ProjectResponse]:
    """Lists all active projects belonging to the caller's tenant under RLS isolation."""
    return await list_projects_service(user.tenant_uuid)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get project by ID",
    description="Retrieves single project by ID with its linked repositories under tenant RLS isolation.",
)
async def get_project(
    project_id: uuid.UUID,
    user: TokenClaims = Depends(require_project_view),
) -> ProjectResponse:
    """Retrieves project details. Returns HTTP 404 if project is missing or cross-tenant."""
    return await get_project_service(user.tenant_uuid, project_id)


@router.post(
    "/{project_id}/repositories",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a repository to a project",
    description="Links a GitHub repository to the project and creates project_link record. Requires TENANT_ADMIN.",
)
async def link_repository(
    project_id: uuid.UUID,
    payload: RepositoryLinkRequest,
    user: TokenClaims = Depends(require_tenant_admin),
) -> RepositoryResponse:
    """Links repository to project under tenant context. Enforces unique repository linking."""
    return await link_repository_service(user.tenant_uuid, project_id, payload)


@router.delete(
    "/{project_id}/repositories/{repo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink repository from project",
    description="Unlinks a repository and removes its project_link entry. Requires TENANT_ADMIN.",
)
async def unlink_repository(
    project_id: uuid.UUID,
    repo_id: uuid.UUID,
    user: TokenClaims = Depends(require_tenant_admin),
) -> Response:
    """Unlinks a repository from a project. Returns HTTP 204 No Content on success."""
    await unlink_repository_service(user.tenant_uuid, project_id, repo_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ==============================================================================
# EXISTING STUB ENDPOINTS (PRESERVED)
# ==============================================================================


@router.get(
    "/{project_id}/sprints/burndown",
    **stub_route_kwargs(
        Permission.VIEW_SPRINT_BURNDOWN_WIP,
        summary="View Sprint Burndown and WIP",
        description="Monitor team velocity, sprint burndown charts, and Work In Progress (WIP) limit congestion.",
    ),
)
async def get_sprint_burndown_and_wip(
    project_id: str,
    user: TokenClaims = Depends(require_permission(Permission.VIEW_SPRINT_BURNDOWN_WIP)),
) -> StubEndpointResponse:
    """Monitor team velocity, sprint burndown charts, and WIP limit congestion."""
    return build_stub_response(Permission.VIEW_SPRINT_BURNDOWN_WIP)


@router.get(
    "/{project_id}/scope-alerts",
    **stub_route_kwargs(
        Permission.RECEIVE_SCOPE_CREEP_ALERT,
        summary="Receive Scope Creep Alerts",
        description="Receive and act upon automated two-stage Acceptance Criteria scope creep alerts (POSSIBLE_SCOPE_CHANGE).",
    ),
)
async def get_scope_creep_alerts(
    project_id: str,
    user: TokenClaims = Depends(require_permission(Permission.RECEIVE_SCOPE_CREEP_ALERT)),
) -> StubEndpointResponse:
    """Receive and act upon automated two-stage Acceptance Criteria scope creep alerts."""
    return build_stub_response(Permission.RECEIVE_SCOPE_CREEP_ALERT)


@router.post(
    "/{project_id}/pull-requests/{pr_id}/reassign-reviewer",
    **stub_route_kwargs(
        Permission.REASSIGN_PR_REVIEWER_MOBILE,
        summary="Reassign Pull Request Reviewer",
        description="Execute privileged one-tap reviewer nudge or reassignment via mobile client companion or web dashboard.",
    ),
)
async def reassign_pr_reviewer(
    project_id: str,
    pr_id: str,
    user: TokenClaims = Depends(require_permission(Permission.REASSIGN_PR_REVIEWER_MOBILE)),
) -> StubEndpointResponse:
    """Execute privileged reviewer nudge or reassignment via mobile companion or dashboard."""
    return build_stub_response(Permission.REASSIGN_PR_REVIEWER_MOBILE)
