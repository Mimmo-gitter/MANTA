"""REST API router for semantic pgvector ADR search (SCRUM-579 / US-45)."""

from __future__ import annotations

from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims
from app.search.schemas import ADRSearchResponse
from app.search.service import search_tenant_adrs

router = APIRouter(prefix="/api/v1/search", tags=["ADR Knowledge Management"])
require_adr_search = require_permission(Permission.SEARCH_ADR)


@router.get(
    "/adrs",
    response_model=ADRSearchResponse,
    summary="Search Architecture Decision Records",
    description=(
        "Execute semantic vector search over approved architectural decision records "
        "within the tenant partition and expose live index rebuild status."
    ),
)
async def search_adrs(
    request: Request,
    q: Optional[str] = Query(
        default=None, max_length=1000, description="Natural-language search query"
    ),
    query: Optional[str] = Query(
        default=None, max_length=1000, description="Alias for q search query parameter"
    ),
    limit: int = Query(default=10, ge=1, le=50),
    min_similarity: float = Query(default=0.0, ge=0.0, le=1.0),
    architecture_pattern: Optional[str] = Query(default=None, max_length=128),
    visibility: Optional[str] = Query(default=None, max_length=32),
    project_id: Optional[uuid.UUID] = Query(default=None),
    user: TokenClaims = Depends(require_adr_search),
) -> ADRSearchResponse:
    """Execute semantic vector search over approved tenant ADRs."""
    effective_query = q if q is not None else query
    ai_client_override = getattr(request.app.state, "search_ai_client", None)
    redis_override = getattr(request.app.state, "adr_redis", None) or getattr(
        request.app.state, "ai_reindex_redis", None
    )
    return await search_tenant_adrs(
        user.tenant_uuid,
        query=effective_query,
        limit=limit,
        min_similarity=min_similarity,
        architecture_pattern=architecture_pattern,
        visibility=visibility,
        project_id=project_id,
        ai_client=ai_client_override,
        redis_client=redis_override,
    )
