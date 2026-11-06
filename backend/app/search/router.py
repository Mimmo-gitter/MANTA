from fastapi import APIRouter, Depends

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims

router = APIRouter(prefix="/api/v1/search", tags=["ADR Knowledge Management", "Stubs"])


@router.get(
    "/adrs",
    **stub_route_kwargs(
        Permission.SEARCH_ADR,
        summary="Search Architecture Decision Records",
        description="Execute semantic vector search over approved architectural decision records within the tenant partition or globally visible patterns.",
    ),
)
async def search_adrs(
    user: TokenClaims = Depends(require_permission(Permission.SEARCH_ADR)),
) -> StubEndpointResponse:
    """Execute semantic vector search over approved architectural decision records."""
    return build_stub_response(Permission.SEARCH_ADR)
