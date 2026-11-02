from fastapi import APIRouter, Depends

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims

router = APIRouter(prefix="/api/v1/projects", tags=["Sprint & Risk Management", "Stubs"])


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
