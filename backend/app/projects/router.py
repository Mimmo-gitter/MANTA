from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims
from app.dora.schemas import DoraMetricsResponse, DoraWindow
from app.dora.service import (
    get_project_dora_metrics as get_project_dora_metrics_service,
    resolve_dora_timeframe_days,
)
from app.projects.flow_metrics import (
    get_burndown_response,
    get_flow_metrics_response,
    list_project_sprints_response,
)
from app.projects.health_service import (
    get_project_health_config as get_project_health_config_service,
    handle_project_health_signal_http,
    list_project_health_signals as list_project_health_signals_service,
    rotate_project_health_secret as rotate_project_health_secret_service,
    update_project_health_config as update_project_health_config_service,
)
from app.projects.schemas import (
    DeploymentSignalResponse,
    DeploymentSignalUpdateRequest,
    HealthProbeConfigResponse,
    HealthProbeConfigUpdateRequest,
    HealthSignalIngestResponse,
    ProductionFailureResponse,
    ProjectCreateRequest,
    ProjectIncidentResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    RepositoryLinkRequest,
    RepositoryResponse,
)
from app.projects.service import (
    archive_project as archive_project_service,
    create_project as create_project_service,
    get_deployment_signal as get_deployment_signal_service,
    get_project as get_project_service,
    link_repository as link_repository_service,
    list_project_incidents as list_project_incidents_service,
    list_projects as list_projects_service,
    unlink_repository as unlink_repository_service,
    update_deployment_signal as update_deployment_signal_service,
    update_project as update_project_service,
)

router = APIRouter(prefix="/api/v1/projects", tags=["Projects", "Sprint & Risk Management"])

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


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update project metadata and thresholds",
    description="Updates project name, slug, description, or DORA/WIP thresholds. Requires TENANT_ADMIN.",
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdateRequest,
    user: TokenClaims = Depends(require_tenant_admin),
) -> ProjectResponse:
    """Updates an existing project under caller's tenant context."""
    return await update_project_service(user.tenant_uuid, project_id, payload)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a project",
    description="Soft-archives a project and removes it from active listings. Requires TENANT_ADMIN.",
)
async def archive_project(
    project_id: uuid.UUID,
    user: TokenClaims = Depends(require_tenant_admin),
) -> Response:
    """Archives an active project under caller's tenant context."""
    await archive_project_service(user.tenant_uuid, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{project_id}/incidents",
    response_model=list[ProjectIncidentResponse],
    summary="List production failure incidents for a project",
    description="Lists incidents recorded against the project for Change Failure Rate and MTTR tracking.",
)
async def list_project_incidents(
    project_id: uuid.UUID,
    user: TokenClaims = Depends(require_project_view),
) -> list[ProjectIncidentResponse]:
    """Lists incidents recorded for a project under tenant RLS."""
    return await list_project_incidents_service(user.tenant_uuid, project_id)


@router.get(
    "/{project_id}/dora-metrics",
    response_model=DoraMetricsResponse,
    tags=["DORA & Delivery Analytics"],
    summary="Get Project DORA Metrics",
    description=(
        "Retrieve verified rolling 14-day or 30-day DORA metrics (nullable aggregates, "
        "authoritative_deployment_signal, diagnostic notes, and contiguous UTC daily trend series) "
        "for an active project under tenant RLS."
    ),
)
async def get_project_dora_metrics(
    project_id: uuid.UUID,
    request: Request,
    timeframe_days: int = Query(
        default=30,
        description="Rolling evaluation window in days (14 or 30).",
        json_schema_extra={"enum": [14, 30]},
    ),
    window: Optional[DoraWindow] = Query(
        default=None,
        include_in_schema=False,
    ),
    user: TokenClaims = Depends(require_project_view),
) -> DoraMetricsResponse:
    """Retrieve verified rolling DORA metrics for a project under tenant RLS."""
    resolved_days = resolve_dora_timeframe_days(
        timeframe_days=timeframe_days,
        window=window,
        has_explicit_timeframe="timeframe_days" in request.query_params,
    )
    return await get_project_dora_metrics_service(
        user.tenant_uuid,
        project_id,
        timeframe_days=resolved_days,
    )


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
# SCRUM-383: DEPLOYMENT SIGNAL CONFIGURATION ENDPOINTS (US-18)
# ==============================================================================


@router.get(
    "/{project_id}/deployment-signal",
    response_model=DeploymentSignalResponse,
    summary="Get project authoritative deployment signal",
    description="Retrieves the active deployment signal configuration for the project under tenant RLS.",
)
async def get_deployment_signal(
    project_id: uuid.UUID,
    user: TokenClaims = Depends(require_project_view),
) -> DeploymentSignalResponse:
    """Retrieves authoritative production deployment signal. Requires project view permission."""
    return await get_deployment_signal_service(user.tenant_uuid, project_id)


@router.patch(
    "/{project_id}/deployment-signal",
    response_model=DeploymentSignalResponse,
    summary="Update project authoritative deployment signal",
    description="Updates the authoritative production deployment signal. Requires TENANT_ADMIN.",
)
async def update_deployment_signal(
    project_id: uuid.UUID,
    payload: DeploymentSignalUpdateRequest,
    user: TokenClaims = Depends(require_tenant_admin),
) -> DeploymentSignalResponse:
    """Updates authoritative production deployment signal. Requires TENANT_ADMIN."""
    return await update_deployment_signal_service(user.tenant_uuid, project_id, payload)


# ==============================================================================
# SCRUM-452 / SCRUM-453: PRODUCTION HEALTH SIGNAL INGESTION & CONFIG (US-68)
# ==============================================================================


@router.post(
    "/{project_id}/health-signals",
    response_model=HealthSignalIngestResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest authenticated production health probe signal",
    description=(
        "Ingests FAIL/PASS production health signals verified by HMAC-SHA256 signature, "
        "webhook secret token, or authorized OIDC Bearer JWT, driving the idempotent "
        "production_failures state machine (US-68 / Task 68.3 & Task 68.4)."
    ),
)
async def ingest_project_health_signal(
    project_id: uuid.UUID,
    request: Request,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> HealthSignalIngestResponse:
    """Ingest production health probe FAIL/PASS signal with HMAC/token/JWT auth."""
    return await handle_project_health_signal_http(
        project_id=project_id,
        request=request,
        x_tenant_id=x_tenant_id,
    )


ingest_project_health_signal._required_permissions = frozenset(  # type: ignore[attr-defined]
    {
        Permission.INGEST_PROJECT_DEPLOYMENTS.value,
        Permission.MANAGE_WEBHOOK_INTEGRATIONS.value,
    }
)


@router.get(
    "/{project_id}/health-signals",
    response_model=list[ProductionFailureResponse],
    summary="List project production failure sessions",
    description="Retrieves recorded production health failure sessions for a project under tenant RLS.",
)
async def list_project_health_signals(
    project_id: uuid.UUID,
    user: TokenClaims = Depends(require_project_view),
) -> list[ProductionFailureResponse]:
    """Lists production_failures sessions recorded for the project."""
    return await list_project_health_signals_service(user.tenant_uuid, project_id)


@router.get(
    "/{project_id}/health-config",
    response_model=HealthProbeConfigResponse,
    summary="Get project health probe configuration",
    description="Retrieves health probe webhook configuration, URLs, masked secret, and live health status.",
)
async def get_project_health_config(
    project_id: uuid.UUID,
    request: Request,
    user: TokenClaims = Depends(require_project_view),
) -> HealthProbeConfigResponse:
    """Retrieves health probe webhook configuration for the project."""
    return await get_project_health_config_service(
        user.tenant_uuid,
        project_id,
        str(request.base_url),
    )


@router.patch(
    "/{project_id}/health-config",
    response_model=HealthProbeConfigResponse,
    summary="Update project health probe configuration",
    description="Updates project health probe settings and optional custom webhook secret. Requires TENANT_ADMIN.",
)
async def update_project_health_config(
    project_id: uuid.UUID,
    payload: HealthProbeConfigUpdateRequest,
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> HealthProbeConfigResponse:
    """Updates health probe configuration for the project. Requires TENANT_ADMIN."""
    return await update_project_health_config_service(
        user.tenant_uuid,
        project_id,
        payload,
        str(request.base_url),
    )


@router.post(
    "/{project_id}/health-config/rotate-secret",
    response_model=HealthProbeConfigResponse,
    summary="Rotate project health webhook HMAC secret",
    description="Generates a new AES-256-GCM encrypted HMAC secret for the project health probe webhook.",
)
async def rotate_project_health_secret(
    project_id: uuid.UUID,
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> HealthProbeConfigResponse:
    """Rotates the HMAC webhook secret for the project's health probe."""
    return await rotate_project_health_secret_service(
        user.tenant_uuid,
        project_id,
        str(request.base_url),
    )


# ==============================================================================
# EXISTING STUB ENDPOINTS (PRESERVED)
# ==============================================================================

@router.get(
    "/{project_id}/sprints",
    summary="List project sprints",
    description="Returns all sprints stored for the specified project.",
)
async def list_project_sprints(
    project_id: uuid.UUID,
    user: TokenClaims = Depends(
        require_permission(Permission.VIEW_SPRINT_BURNDOWN_WIP)
    ),
) -> list[dict]:
    """Lists all stored sprints for a project under tenant isolation."""
    return await list_project_sprints_response(
        user.tenant_uuid,
        project_id,
    )


@router.get(
    "/{project_id}/sprints/burndown",
    summary="Sprint burndown",
    description="Daily remaining story points and ideal burndown line for a sprint.",
)
async def get_sprint_burndown(
    project_id: uuid.UUID,
    sprint_id: Optional[uuid.UUID] = Query(default=None),
    user: TokenClaims = Depends(require_permission(Permission.VIEW_SPRINT_BURNDOWN_WIP)),
) -> dict:
    """Daily remaining_sp and linear ideal_sp across the sprint date range."""
    return await get_burndown_response(user.tenant_uuid, project_id, sprint_id)


@router.get(
    "/{project_id}/flow-metrics",
    summary="Sprint flow metrics",
    description="Burndown series plus current WIP counts by status compared with WIP limits.",
)
async def get_flow_metrics(
    project_id: uuid.UUID,
    sprint_id: Optional[uuid.UUID] = Query(default=None),
    user: TokenClaims = Depends(require_permission(Permission.VIEW_SPRINT_BURNDOWN_WIP)),
) -> dict:
    """Burndown series and WIP status counts versus wip_limits."""
    return await get_flow_metrics_response(user.tenant_uuid, project_id, sprint_id)


@router.get(
    "/{project_id}/scope-alerts",
    tags=["Stubs"],
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
    tags=["Stubs"],
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
