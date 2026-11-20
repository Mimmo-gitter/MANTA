from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims
from app.dora.schemas import DoraMetricsResponse, DoraWindow
from app.dora.service import get_project_dora_metrics, resolve_dora_timeframe_days

router = APIRouter(prefix="/api/v1/dora", tags=["DORA & Delivery Analytics"])


@router.get(
    "/projects/{project_id}",
    response_model=DoraMetricsResponse,
    summary="Get Project DORA Metrics",
    description=(
        "Retrieve verified rolling 14-day or 30-day DORA metrics (Lead Time for Changes, "
        "Deployment Frequency, Change Failure Rate, Mean Time to Recovery), nullable aggregates, "
        "authoritative_deployment_signal, diagnostic notes, and UTC daily trend series."
    ),
)
async def get_project_dora(
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
    user: TokenClaims = Depends(require_permission(Permission.VIEW_PROJECT_DORA)),
) -> DoraMetricsResponse:
    """Retrieve verified DORA metrics for an assigned project under tenant RLS."""
    resolved_days = resolve_dora_timeframe_days(
        timeframe_days=timeframe_days,
        window=window,
        has_explicit_timeframe="timeframe_days" in request.query_params,
    )
    return await get_project_dora_metrics(
        user.tenant_uuid,
        project_id,
        timeframe_days=resolved_days,
    )


@router.get(
    "/portfolio",
    tags=["Stubs"],
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
