from fastapi import APIRouter, Depends

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims

router = APIRouter(prefix="/api/v1/adrs", tags=["ADR Knowledge Management", "Stubs"])


@router.post(
    "/{adr_id}/approve",
    **stub_route_kwargs(
        Permission.APPROVE_ADR_TENANT_PRIVATE,
        summary="Approve Tenant-Private ADR",
        description="Sign off on sanitized Architecture Decision Records (ADRs) to allow embedding and indexing in the tenant-private vector partition.",
    ),
)
async def approve_adr_tenant_private(
    adr_id: str,
    user: TokenClaims = Depends(require_permission(Permission.APPROVE_ADR_TENANT_PRIVATE)),
) -> StubEndpointResponse:
    """Sign off on sanitized ADRs to allow embedding in tenant-private vector partition."""
    return build_stub_response(Permission.APPROVE_ADR_TENANT_PRIVATE)


@router.post(
    "/{adr_id}/publish-global",
    **stub_route_kwargs(
        Permission.PUBLISH_GLOBAL_ADR,
        summary="Publish Global ADR",
        description="Promote an approved ADR from tenant-private to GLOBAL visibility across all enterprise tenants on the platform.",
    ),
)
async def publish_global_adr(
    adr_id: str,
    user: TokenClaims = Depends(require_permission(Permission.PUBLISH_GLOBAL_ADR)),
) -> StubEndpointResponse:
    """Promote an approved ADR from tenant-private to GLOBAL visibility across all tenants."""
    return build_stub_response(Permission.PUBLISH_GLOBAL_ADR)
