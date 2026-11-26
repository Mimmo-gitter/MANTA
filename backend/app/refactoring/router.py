"""SCRUM-582 (TASK-320-1): Refactoring & SCIP Code Intelligence endpoints."""

from __future__ import annotations

import time
from typing import Optional
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, select

from app.auth.permissions import (
    Permission,
    build_stub_response,
    require_permission,
    stub_route_kwargs,
)
from app.auth.schemas import StubEndpointResponse, TokenClaims
from app.db.models.models import Commit, Project, Repository
from app.db.session import get_tenant_db_context
from app.refactoring.schemas import (
    ScipCallerLookupResponse,
    ScipGraphEdge,
    ScipGraphNode,
    ScipGraphResponse,
    ScipIndexStatusListResponse,
    ScipRepositoryStatusItem,
    TriggerScipIndexRequest,
    TriggerScipIndexResponse,
)
from app.refactoring.scip_service import (
    ZERO_COMMIT_SHA,
    enqueue_scip_index_job,
    get_latest_scip_index,
    list_repository_scip_statuses,
)
from app.refactoring.symbol_graph import SymbolNode, symbol_graph_registry

router = APIRouter(prefix="/api/v1/refactoring", tags=["Code Intelligence & Refactoring"])

require_scip_view = require_permission(Permission.VIEW_TECH_DEBT_AND_SCIP_GRAPH)
require_refactor_trigger = require_permission(Permission.TRIGGER_REFACTOR_ANALYSIS)


def _node_to_schema(node: SymbolNode) -> ScipGraphNode:
    return ScipGraphNode(
        id=node.symbol,
        display_name=node.display_name,
        file_path=node.file_path,
        line=node.line,
        kind=node.kind,
        enclosing_symbol=node.enclosing_symbol,
    )


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
    "/scip-indexes",
    response_model=ScipIndexStatusListResponse,
    summary="List Repository SCIP Index Statuses",
    description="Return SCIP index commit SHA, symbol count, freshness, and RAM cache state for linked repositories.",
)
async def list_scip_indexes(
    project_id: Optional[uuid.UUID] = Query(default=None),
    user: TokenClaims = Depends(require_scip_view),
) -> ScipIndexStatusListResponse:
    async with get_tenant_db_context(user.tenant_uuid) as db:
        items = await list_repository_scip_statuses(
            db,
            tenant_id=user.tenant_uuid,
            project_id=project_id,
        )
    return ScipIndexStatusListResponse(
        repositories=[ScipRepositoryStatusItem(**item) for item in items],
        total=len(items),
    )


@router.post(
    "/repositories/{repository_id}/scip-index",
    response_model=TriggerScipIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger SCIP Repository Indexing",
    description="Enqueue a SCIP protobuf generation and RAM call-graph ingestion job for the repository.",
)
async def trigger_scip_indexing(
    repository_id: uuid.UUID,
    request: Request,
    payload: Optional[TriggerScipIndexRequest] = Body(default=None),
    user: TokenClaims = Depends(require_refactor_trigger),
) -> TriggerScipIndexResponse:
    body = payload or TriggerScipIndexRequest()
    async with get_tenant_db_context(user.tenant_uuid) as db:
        repo = (
            await db.scalars(
                select(Repository).where(
                    Repository.tenant_id == user.tenant_uuid,
                    Repository.id == repository_id,
                )
            )
        ).first()
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository {repository_id} not found",
            )

        commit_sha = body.commit_sha
        if not commit_sha:
            commit_sha = (
                await db.scalars(
                    select(Commit.sha)
                    .where(
                        Commit.tenant_id == user.tenant_uuid,
                        Commit.repository_id == repository_id,
                    )
                    .order_by(desc(Commit.committed_at))
                    .limit(1)
                )
            ).first()
        resolved_sha = (commit_sha or ZERO_COMMIT_SHA)[:40]

    redis_override = getattr(request.app.state, "scip_redis", None)
    enqueued = await enqueue_scip_index_job(
        tenant_id=user.tenant_uuid,
        repository_id=repository_id,
        commit_sha=resolved_sha,
        priority=body.priority,
        repo_path=body.repo_path,
        redis_client=redis_override,
    )
    return TriggerScipIndexResponse(**enqueued)


@router.get(
    "/projects/{project_id}/scip-graph",
    response_model=ScipGraphResponse,
    summary="View Tech Debt and SCIP Call Graph",
    description="Return the deserialized in-memory SCIP call-graph slice and metadata for a project repository.",
)
async def get_tech_debt_and_scip_graph(
    project_id: uuid.UUID,
    repository_id: Optional[uuid.UUID] = Query(default=None),
    root_symbol: Optional[str] = Query(default=None),
    depth: int = Query(default=1, ge=1, le=4),
    limit: int = Query(default=100, ge=1, le=500),
    user: TokenClaims = Depends(require_scip_view),
) -> ScipGraphResponse:
    """Explore SCIP symbol call graph nodes and edges from the tenant's RAM symbol graph."""
    async with get_tenant_db_context(user.tenant_uuid) as db:
        project = (
            await db.scalars(
                select(Project).where(
                    Project.tenant_id == user.tenant_uuid,
                    Project.id == project_id,
                )
            )
        ).first()
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found",
            )

        statuses = await list_repository_scip_statuses(
            db,
            tenant_id=user.tenant_uuid,
            project_id=project_id,
        )

    if not statuses:
        return ScipGraphResponse(project_id=project_id, freshness="MISSING")

    chosen = None
    if repository_id is not None:
        chosen = next((s for s in statuses if s["repository_id"] == repository_id), None)
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository {repository_id} not found in project {project_id}",
            )
    else:
        chosen = next((s for s in statuses if s["commit_sha"] is not None), statuses[0])

    if not chosen["commit_sha"] or not chosen["scip_protobuf_storage_uri"]:
        return ScipGraphResponse(
            project_id=project_id,
            repository_id=chosen["repository_id"],
            freshness="MISSING",
        )

    try:
        graph = symbol_graph_registry.get_or_load(
            tenant_id=user.tenant_uuid,
            repository_id=chosen["repository_id"],
            commit_sha=chosen["commit_sha"],
            storage_uri=chosen["scip_protobuf_storage_uri"],
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Persisted SCIP index artifact missing on storage",
        ) from exc

    t0 = time.perf_counter_ns()
    nodes_raw, edges_raw = graph.get_graph_slice(root_symbol=root_symbol, depth=depth, limit=limit)
    lookup_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

    return ScipGraphResponse(
        project_id=project_id,
        repository_id=chosen["repository_id"],
        commit_sha=chosen["commit_sha"],
        freshness=chosen["freshness"],
        symbols_count=chosen["symbols_count"],
        nodes=[ScipGraphNode(**n) for n in nodes_raw],
        edges=[ScipGraphEdge(**e) for e in edges_raw],
        lookup_ms=round(lookup_ms, 4),
    )


@router.get(
    "/repositories/{repository_id}/scip-index/callers",
    response_model=ScipCallerLookupResponse,
    summary="Query Callers and Callees in RAM SCIP Symbol Graph",
    description="Query callers and callees of a symbol from the pre-deserialized RAM symbol graph (<1.5ms).",
)
async def query_scip_symbol_callers(
    repository_id: uuid.UUID,
    symbol: str = Query(..., min_length=1),
    depth: int = Query(default=1, ge=1, le=4),
    limit: int = Query(default=100, ge=1, le=500),
    user: TokenClaims = Depends(require_scip_view),
) -> ScipCallerLookupResponse:
    async with get_tenant_db_context(user.tenant_uuid) as db:
        repo = (
            await db.scalars(
                select(Repository).where(
                    Repository.tenant_id == user.tenant_uuid,
                    Repository.id == repository_id,
                )
            )
        ).first()
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository {repository_id} not found",
            )
        scip_row = await get_latest_scip_index(
            db,
            tenant_id=user.tenant_uuid,
            repository_id=repository_id,
        )
        if scip_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No SCIP index found for repository {repository_id}",
            )

    try:
        graph = symbol_graph_registry.get_or_load(
            tenant_id=user.tenant_uuid,
            repository_id=repository_id,
            commit_sha=scip_row.commit_sha,
            storage_uri=scip_row.scip_protobuf_storage_uri,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Persisted SCIP index artifact missing on storage",
        ) from exc

    t0 = time.perf_counter_ns()
    target_node = graph.get_node(symbol)
    callers = graph.get_callers(symbol, max_depth=depth, limit=limit)
    callees = graph.get_callees(symbol, max_depth=depth, limit=limit)
    lookup_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

    return ScipCallerLookupResponse(
        repository_id=repository_id,
        commit_sha=scip_row.commit_sha,
        target_symbol=symbol,
        node=_node_to_schema(target_node) if target_node else None,
        callers=[_node_to_schema(n) for n in callers],
        callees=[_node_to_schema(n) for n in callees],
        lookup_ms=round(lookup_ms, 4),
    )


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
