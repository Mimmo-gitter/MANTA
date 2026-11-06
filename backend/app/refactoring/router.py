from fastapi import APIRouter, Depends

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims

router = APIRouter(prefix="/api/v1/refactoring", tags=["Code Intelligence & Refactoring", "Stubs"])


@router.get(
    "/code-quality/me",
    **stub_route_kwargs(
        Permission.VIEW_OWN_CODE_QUALITY,
        summary="View Personal Code Quality",
        description="Track personal contributor code quality metrics, review turnaround statistics, and assigned pull request statuses.",
    ),
)
async def get_own_code_quality(
    user: TokenClaims = Depends(require_permission(Permission.VIEW_OWN_CODE_QUALITY)),
) -> StubEndpointResponse:
    """Track personal contributor code quality metrics and review statistics."""
    return build_stub_response(Permission.VIEW_OWN_CODE_QUALITY)


@router.get(
    "/projects/{project_id}/scip-graph",
    **stub_route_kwargs(
        Permission.VIEW_TECH_DEBT_AND_SCIP_GRAPH,
        summary="View Tech Debt and SCIP Call Graph",
        description="Explore interactive React Flow call graph visualizers, SCIP symbol hierarchies, and technical debt hotspots.",
    ),
)
async def get_tech_debt_and_scip_graph(
    project_id: str,
    user: TokenClaims = Depends(require_permission(Permission.VIEW_TECH_DEBT_AND_SCIP_GRAPH)),
) -> StubEndpointResponse:
    """Explore interactive call graph visualizers, SCIP symbol hierarchies, and technical debt hotspots."""
    return build_stub_response(Permission.VIEW_TECH_DEBT_AND_SCIP_GRAPH)


@router.post(
    "/projects/{project_id}/analyze",
    **stub_route_kwargs(
        Permission.TRIGGER_REFACTOR_ANALYSIS,
        summary="Trigger Refactoring Analysis",
        description="Trigger AI-assisted refactoring analysis and candidate PR generation with 4-tier deterministic sandbox verification.",
    ),
)
async def trigger_refactor_analysis(
    project_id: str,
    user: TokenClaims = Depends(require_permission(Permission.TRIGGER_REFACTOR_ANALYSIS)),
) -> StubEndpointResponse:
    """Trigger AI-assisted refactoring analysis and candidate PR generation."""
    return build_stub_response(Permission.TRIGGER_REFACTOR_ANALYSIS)
