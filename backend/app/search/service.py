"""Semantic pgvector HNSW search service over tenant-approved ADRs (SCRUM-579)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
import uuid

from fastapi import HTTPException, status
import httpx
from sqlalchemy import select

from app.adr.service import extract_redaction_summary
from app.ai.client_factory import (
    AI_MODE_LOCAL_ONLY,
    AIClientError,
    BaseAIClient,
    create_ai_client,
    get_mode_metadata,
)
from app.ai.reindex_service import get_tenant_reindex_status
from app.db.models.models import ADRSanitizationLog, AnonymizedADR, Tenant
from app.db.session import get_tenant_db_context
from app.search.schemas import ADRSearchResponse, ADRSearchResultItem


@asynccontextmanager
async def _resolve_ai_client(
    active_mode: str,
    ai_client: Optional[Any],
    expected_dims: int,
) -> AsyncIterator[BaseAIClient]:
    if ai_client is not None:
        resolved = ai_client(active_mode) if callable(ai_client) else ai_client
        if getattr(resolved, "embedding_dimensions", None) != expected_dims:
            raise ValueError(
                f"AI client embedding dimension mismatch for mode '{active_mode}': "
                f"expected {expected_dims}, got {getattr(resolved, 'embedding_dimensions', None)}."
            )
        yield resolved
        return

    async with create_ai_client(active_mode) as client:
        yield client


async def _embed_query_text(
    query_text: str,
    *,
    active_mode: str,
    expected_dims: int,
    ai_client: Optional[Any],
) -> list[float]:
    try:
        async with _resolve_ai_client(active_mode, ai_client, expected_dims) as client:
            vectors = await client.embed_texts([query_text])
    except (AIClientError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI embedding service unavailable for mode '{active_mode}': {exc}",
        ) from exc

    if not vectors or len(vectors[0]) != expected_dims:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI embedding service returned invalid vector dimensions for mode '{active_mode}'.",
        )
    return vectors[0]


async def search_tenant_adrs(
    tenant_id: uuid.UUID,
    *,
    query: Optional[str] = None,
    limit: int = 10,
    min_similarity: float = 0.0,
    architecture_pattern: Optional[str] = None,
    visibility: Optional[str] = None,
    project_id: Optional[uuid.UUID] = None,
    ai_client: Optional[Any] = None,
    redis_client: Optional[Any] = None,
) -> ADRSearchResponse:
    """
    Search strictly approved and verified ADRs within the tenant's dedicated pgvector partition.

    Dynamically targets `embedding_384` (`local_only`) or `embedding_1536` (`cloud_zdr`) and exposes
    `rebuilding` from Redis while workspace-wide re-embedding is queued or running.
    """
    cleaned_query = (query or "").strip()
    reindex_state = await get_tenant_reindex_status(tenant_id, redis_client=redis_client)

    async with get_tenant_db_context(tenant_id) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        raw_mode = tenant.ai_processing_mode

    meta = get_mode_metadata(raw_mode)
    active_mode = str(meta["mode"])
    expected_dims = int(meta["embedding_dimensions"])
    embedding_model = str(meta["embedding_model"])

    query_vector: Optional[list[float]] = None
    if cleaned_query:
        query_vector = await _embed_query_text(
            cleaned_query,
            active_mode=active_mode,
            expected_dims=expected_dims,
            ai_client=ai_client,
        )

    async with get_tenant_db_context(tenant_id) as db:
        available_patterns = list(
            (
                await db.scalars(
                    select(AnonymizedADR.architecture_pattern)
                    .where(
                        AnonymizedADR.tenant_id == tenant_id,
                        AnonymizedADR.status == "approved",
                        AnonymizedADR.sanitization_verified.is_(True),
                        AnonymizedADR.architecture_pattern.is_not(None),
                    )
                    .distinct()
                    .order_by(AnonymizedADR.architecture_pattern.asc())
                )
            ).all()
        )

        filters = [
            AnonymizedADR.tenant_id == tenant_id,
            AnonymizedADR.status == "approved",
            AnonymizedADR.sanitization_verified.is_(True),
        ]
        if architecture_pattern and architecture_pattern.strip():
            filters.append(AnonymizedADR.architecture_pattern == architecture_pattern.strip())
        if visibility and visibility.strip():
            filters.append(AnonymizedADR.visibility == visibility.strip().upper())
        if project_id is not None:
            filters.append(AnonymizedADR.project_id == project_id)

        scored_rows: list[tuple[AnonymizedADR, Optional[float]]] = []
        if query_vector is not None:
            embedding_col = (
                AnonymizedADR.embedding_384
                if active_mode == AI_MODE_LOCAL_ONLY
                else AnonymizedADR.embedding_1536
            )
            distance_expr = embedding_col.cosine_distance(query_vector)
            similarity_expr = 1.0 - distance_expr

            stmt = select(AnonymizedADR, distance_expr.label("cosine_dist")).where(
                *filters,
                embedding_col.is_not(None),
            )
            if min_similarity > 0.0:
                stmt = stmt.where(similarity_expr >= min_similarity)
            stmt = stmt.order_by(distance_expr.asc()).limit(limit)

            rows = (await db.execute(stmt)).all()
            for adr_obj, dist in rows:
                sim = round(float(1.0 - float(dist)), 6) if dist is not None else None
                scored_rows.append((adr_obj, sim))
        else:
            stmt = (
                select(AnonymizedADR)
                .where(*filters)
                .order_by(
                    AnonymizedADR.approved_at.desc().nullslast(),
                    AnonymizedADR.created_at.desc(),
                    AnonymizedADR.id.desc(),
                )
                .limit(limit)
            )
            for adr_obj in (await db.scalars(stmt)).all():
                scored_rows.append((adr_obj, None))

        redaction_counts: dict[uuid.UUID, int] = {}
        adr_ids = [adr.id for adr, _ in scored_rows]
        if adr_ids:
            for log_row in (
                await db.scalars(
                    select(ADRSanitizationLog)
                    .where(
                        ADRSanitizationLog.tenant_id == tenant_id,
                        ADRSanitizationLog.adr_id.in_(adr_ids),
                    )
                    .order_by(ADRSanitizationLog.created_at.desc(), ADRSanitizationLog.id.desc())
                )
            ).all():
                if log_row.adr_id not in redaction_counts:
                    count, _ = extract_redaction_summary(log_row.presidio_entities_scrubbed)
                    redaction_counts[log_row.adr_id] = count

    items = [
        ADRSearchResultItem(
            id=adr.id,
            tenant_id=adr.tenant_id,
            project_id=adr.project_id,
            title=adr.title,
            decision_summary=adr.decision_summary,
            architecture_pattern=adr.architecture_pattern,
            visibility=adr.visibility,
            status=adr.status,
            sanitization_verified=adr.sanitization_verified,
            similarity=similarity,
            redaction_count=redaction_counts.get(adr.id, 0),
            source_commit_sha=adr.source_commit_sha,
            approved_by=adr.approved_by,
            approved_at=adr.approved_at,
            created_at=adr.created_at,
            updated_at=adr.updated_at,
        )
        for adr, similarity in scored_rows
    ]

    return ADRSearchResponse(
        query=cleaned_query,
        items=items,
        total=len(items),
        rebuilding=reindex_state.reindexing,
        reindex_status=reindex_state.status,
        reindexed_count=reindex_state.reindexed_count,
        reindex_total_count=reindex_state.total_count,
        ai_processing_mode=active_mode,
        active_embedding_dimensions=expected_dims,
        active_embedding_model=embedding_model,
        available_patterns=[str(p) for p in available_patterns if p],
    )
