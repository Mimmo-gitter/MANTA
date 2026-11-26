"""REST API router for ADR Governance review queue, diff viewer & approval gate (SCRUM-576)."""

from __future__ import annotations

import ipaddress
from typing import Optional
import uuid

from fastapi import APIRouter, Body, Depends, Query, Request

from app.adr.schemas import (
    ADRApproveRequest,
    ADRDecisionResponse,
    ADRDiffResponse,
    ADRListResponse,
    ADRRejectRequest,
)
from app.adr.service import (
    approve_tenant_adr,
    get_adr_diff,
    list_tenant_adrs,
    reject_tenant_adr,
)
from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims

router = APIRouter(prefix="/api/v1/adrs", tags=["ADR Knowledge Management"])
require_adr_reviewer = require_permission(Permission.APPROVE_ADR_TENANT_PRIVATE)


def _safe_client_ip(request: Request) -> Optional[str]:
    raw_host = request.client.host if request.client else None
    if not raw_host:
        return None
    try:
        return str(ipaddress.ip_address(raw_host))
    except ValueError:
        return None


@router.get(
    "",
    response_model=ADRListResponse,
    summary="List ADR Governance Queue",
    description="Return tenant ADRs, KPI counts by status, repository context, and Presidio redaction counts.",
)
async def list_adrs(
    status: Optional[str] = Query(
        default=None,
        description="Optional status filter (pending_review, approved, rejected, quarantined, draft).",
    ),
    project_id: Optional[uuid.UUID] = Query(default=None),
    repository_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: TokenClaims = Depends(require_adr_reviewer),
) -> ADRListResponse:
    return await list_tenant_adrs(
        user.tenant_uuid,
        status_filter=status,
        project_id=project_id,
        repository_id=repository_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{adr_id}/diff",
    response_model=ADRDiffResponse,
    summary="Inspect Raw vs Sanitized ADR Diff",
    description="Join tenant-only adr_sources with sanitized AnonymizedADR text and Presidio scrubbing logs.",
)
async def get_adr_diff_view(
    adr_id: uuid.UUID,
    user: TokenClaims = Depends(require_adr_reviewer),
) -> ADRDiffResponse:
    return await get_adr_diff(user.tenant_uuid, adr_id)


@router.post(
    "/{adr_id}/approve",
    response_model=ADRDecisionResponse,
    summary="Approve Tenant-Private ADR",
    description="Verify sanitized ADR and enqueue vector embedding into the tenant's active pgvector partition.",
)
async def approve_adr_tenant_private(
    adr_id: uuid.UUID,
    request: Request,
    body: ADRApproveRequest = Body(default_factory=ADRApproveRequest),
    user: TokenClaims = Depends(require_adr_reviewer),
) -> ADRDecisionResponse:
    redis_override = getattr(request.app.state, "adr_redis", None) or getattr(
        request.app.state, "ai_reindex_redis", None
    )
    return await approve_tenant_adr(
        user.tenant_uuid,
        adr_id,
        current_user=user,
        rationale=body.rationale,
        client_ip=_safe_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        redis_client=redis_override,
    )


@router.post(
    "/{adr_id}/reject",
    response_model=ADRDecisionResponse,
    summary="Reject and Quarantine ADR",
    description="Reject an ADR, move it to quarantined status, and block vector embedding.",
)
async def reject_adr_tenant_private(
    adr_id: uuid.UUID,
    request: Request,
    body: ADRRejectRequest = Body(default_factory=ADRRejectRequest),
    user: TokenClaims = Depends(require_adr_reviewer),
) -> ADRDecisionResponse:
    return await reject_tenant_adr(
        user.tenant_uuid,
        adr_id,
        current_user=user,
        rationale=body.rationale,
        client_ip=_safe_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/{adr_id}/publish-global",
    tags=["ADR Knowledge Management", "Stubs"],
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
