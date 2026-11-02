from fastapi import APIRouter, Depends

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims

router = APIRouter(prefix="/api/v1/dora", tags=["DORA & Delivery Analytics", "Stubs"])


@router.get(
    "/projects/{project_id}",
    **stub_route_kwargs(
        Permission.VIEW_PROJECT_DORA,
        summary="Get Project DORA Metrics",
        description="Retrieve verified DORA metrics (Lead Time for Changes, Deployment Frequency, Change Failure Rate, Mean Time to Recovery) for an assigned project.",
    ),
)
async def get_project_dora(
    project_id: str,
    user: TokenClaims = Depends(require_permission(Permission.VIEW_PROJECT_DORA)),
) -> StubEndpointResponse:
    """Retrieve verified DORA metrics for an assigned project."""
    return build_stub_response(Permission.VIEW_PROJECT_DORA)


@router.get(
    "/portfolio",
    **stub_route_kwargs(
        Permission.VIEW_PORTFOLIO_OVERVIEW,
        summary="Get Portfolio Delivery Overview",
        description="View aggregate portfolio-wide delivery health, cross-project deadline risks, and consolidated DORA benchmarks.",
    ),
)
async def get_portfolio_overview(
    user: TokenClaims = Depends(require_permission(Permission.VIEW_PORTFOLIO_OVERVIEW)),
) -> StubEndpointResponse:
    """View aggregate portfolio-wide delivery health and consolidated DORA benchmarks."""
    return build_stub_response(Permission.VIEW_PORTFOLIO_OVERVIEW)
