"""Asynchronous ADR re-embedding service and Redis progress tracking (SCRUM-560 / US-62)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
import uuid

from redis.exceptions import RedisError
from sqlalchemy import select

from app.ai.client_factory import (
    BaseAIClient,
    create_ai_client,
    get_mode_metadata,
    normalize_ai_processing_mode,
)
from app.config import settings
from app.db.models.models import AnonymizedADR, Tenant
from app.db.session import get_tenant_db_context
from app.webhooks.enqueue import get_redis
from workers.worker import QUEUE_DEFAULT, QUEUE_HIGH, enqueue_job, serialize_job_payload

logger = logging.getLogger("app.ai.reindex_service")

ADR_REEMBED_JOB_TYPE = "adr.reembed"
ADR_REEMBED_BATCH_SIZE = 32
_REDIS_ERRORS = (RedisError, OSError, RuntimeError, TimeoutError)


def get_adr_reindex_redis_key(tenant_id: uuid.UUID | str) -> str:
    """Return canonical Redis key holding live ADR re-index state for a tenant."""
    return f"manta:tenant:{tenant_id}:adr_reindex"


@dataclass
class TenantReindexState:
    """Snapshot of tenant ADR re-embedding progress surfaced via /api/v1/tenants/settings."""

    reindexing: bool = False
    status: str = "idle"
    target_mode: Optional[str] = None
    target_dimensions: Optional[int] = None
    job_id: Optional[str] = None
    reindexed_count: int = 0
    total_count: int = 0
    error: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TenantReindexState:
        return cls(
            reindexing=bool(data.get("reindexing", False)),
            status=str(data.get("status") or "idle"),
            target_mode=data.get("target_mode"),
            target_dimensions=(
                int(data["target_dimensions"])
                if data.get("target_dimensions") is not None
                else None
            ),
            job_id=data.get("job_id"),
            reindexed_count=int(data.get("reindexed_count", 0) or 0),
            total_count=int(data.get("total_count", 0) or 0),
            error=data.get("error"),
            updated_at=data.get("updated_at"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_tenant_reindex_status(
    tenant_id: uuid.UUID,
    *,
    redis_client: Optional[Any] = None,
) -> TenantReindexState:
    """Load the tenant's live ADR re-embedding state from Redis, defaulting to idle."""
    client = redis_client or get_redis()
    key = get_adr_reindex_redis_key(tenant_id)
    try:
        raw = await client.get(key)
    except _REDIS_ERRORS as exc:
        logger.warning("Failed to read ADR reindex status for tenant %s: %s", tenant_id, exc)
        return TenantReindexState()

    if raw is None:
        return TenantReindexState()

    try:
        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        parsed = json.loads(decoded)
        if not isinstance(parsed, dict):
            return TenantReindexState()
        return TenantReindexState.from_dict(parsed)
    except (ValueError, TypeError) as exc:
        logger.warning("Corrupt ADR reindex status JSON for tenant %s: %s", tenant_id, exc)
        return TenantReindexState()


async def set_tenant_reindex_status(
    tenant_id: uuid.UUID,
    state: TenantReindexState,
    *,
    redis_client: Optional[Any] = None,
    best_effort: bool = False,
) -> None:
    """Persist the tenant's ADR re-embedding progress in Redis with a bounded TTL."""
    client = redis_client or get_redis()
    key = get_adr_reindex_redis_key(tenant_id)
    state.updated_at = state.updated_at or _now_iso()
    payload = json.dumps(state.to_dict(), separators=(",", ":"), sort_keys=True)
    try:
        await client.set(key, payload, ex=settings.AI_REINDEX_STATUS_TTL_SECONDS)
    except _REDIS_ERRORS as exc:
        if not best_effort:
            raise
        logger.warning("Best-effort Redis update failed for tenant %s: %s", tenant_id, exc)


def _build_adr_embedding_text(adr: AnonymizedADR) -> str:
    """Construct canonical text input for embedding an anonymized approved ADR."""
    parts = [adr.title]
    if adr.architecture_pattern:
        parts.append(f"Pattern: {adr.architecture_pattern}")
    if adr.decision_summary:
        parts.append(adr.decision_summary)
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


async def enqueue_adr_reembed_job(
    *,
    tenant_id: uuid.UUID,
    target_mode: str,
    priority: str = "high",
    redis_client: Optional[Any] = None,
) -> TenantReindexState:
    """Mark tenant reindexing=True and enqueue an async `adr.reembed` job onto ARQ/Valkey."""
    norm_mode = normalize_ai_processing_mode(target_mode)
    meta = get_mode_metadata(norm_mode)
    norm_priority = priority.strip().lower() if isinstance(priority, str) else "high"
    if norm_priority not in ("default", "high"):
        raise ValueError(f"Unsupported queue priority: {priority!r}")

    job_id = str(uuid.uuid4())
    queued_state = TenantReindexState(
        reindexing=True,
        status="queued",
        target_mode=norm_mode,
        target_dimensions=int(meta["embedding_dimensions"]),
        job_id=job_id,
        reindexed_count=0,
        total_count=0,
        error=None,
        updated_at=_now_iso(),
    )

    client = redis_client or get_redis()
    await set_tenant_reindex_status(tenant_id, queued_state, redis_client=client)

    envelope = serialize_job_payload(
        job_id=job_id,
        tenant_id=str(tenant_id),
        priority=norm_priority,
        job_type=ADR_REEMBED_JOB_TYPE,
        payload={
            "target_mode": norm_mode,
            "target_dimensions": meta["embedding_dimensions"],
            "queue": QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT,
        },
        provider="internal",
    )
    await enqueue_job(client, envelope, priority=norm_priority)
    return queued_state


async def enqueue_adr_embed_job(
    *,
    tenant_id: uuid.UUID,
    adr_id: uuid.UUID,
    target_mode: str,
    priority: str = "high",
    redis_client: Optional[Any] = None,
) -> dict[str, Any]:
    """Enqueue single-ADR vector embedding without mutating the workspace-wide reindexing banner."""
    norm_mode = normalize_ai_processing_mode(target_mode)
    meta = get_mode_metadata(norm_mode)
    norm_priority = priority.strip().lower() if isinstance(priority, str) else "high"
    if norm_priority not in ("default", "high"):
        raise ValueError(f"Unsupported queue priority: {priority!r}")

    job_id = str(uuid.uuid4())
    target_dims = int(meta["embedding_dimensions"])
    client = redis_client or get_redis()

    envelope = serialize_job_payload(
        job_id=job_id,
        tenant_id=str(tenant_id),
        priority=norm_priority,
        job_type=ADR_REEMBED_JOB_TYPE,
        payload={
            "target_mode": norm_mode,
            "target_dimensions": target_dims,
            "adr_id": str(adr_id),
            "queue": QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT,
        },
        provider="internal",
    )
    await enqueue_job(client, envelope, priority=norm_priority)
    return {
        "job_id": job_id,
        "adr_id": str(adr_id),
        "target_mode": norm_mode,
        "target_dimensions": target_dims,
    }


async def _embed_and_persist_batches(
    db: Any,
    client: BaseAIClient,
    adrs: list[AnonymizedADR],
    *,
    tenant_id: uuid.UUID,
    embedding_column: str,
    state: TenantReindexState,
    redis_client: Optional[Any],
    track_redis_progress: bool = True,
) -> int:
    processed = 0
    for start in range(0, len(adrs), ADR_REEMBED_BATCH_SIZE):
        batch = adrs[start : start + ADR_REEMBED_BATCH_SIZE]
        texts = [_build_adr_embedding_text(adr) for adr in batch]
        vectors = await client.embed_texts(texts)
        if len(vectors) != len(batch):
            raise ValueError(
                f"Batch embedding count mismatch: expected {len(batch)}, got {len(vectors)}"
            )
        for adr, vec in zip(batch, vectors):
            setattr(adr, embedding_column, vec)
        await db.flush()
        processed += len(batch)
        state.reindexed_count = processed
        state.updated_at = _now_iso()
        if track_redis_progress:
            await set_tenant_reindex_status(
                tenant_id, state, redis_client=redis_client, best_effort=True
            )
    return processed


async def process_adr_reembed_job(
    parsed_payload: dict[str, Any],
    *,
    ai_client: Optional[BaseAIClient] = None,
    redis_client: Optional[Any] = None,
) -> dict[str, Any]:
    """Worker handler for `job_type == 'adr.reembed'`; re-embeds approved tenant ADR(s)."""
    raw_tenant_id = parsed_payload.get("tenant_id")
    if not raw_tenant_id:
        raise ValueError("adr.reembed payload missing tenant_id")
    tenant_id = uuid.UUID(str(raw_tenant_id))

    inner = parsed_payload.get("payload")
    inner_dict = inner if isinstance(inner, dict) else {}
    requested_mode = inner_dict.get("target_mode") or parsed_payload.get("target_mode")
    raw_adr_id = inner_dict.get("adr_id") or parsed_payload.get("adr_id")
    single_adr_id = uuid.UUID(str(raw_adr_id)) if raw_adr_id else None
    track_redis_progress = single_adr_id is None
    job_id = str(parsed_payload.get("job_id") or uuid.uuid4())

    state = TenantReindexState(
        reindexing=True,
        status="running",
        target_mode=str(requested_mode) if requested_mode else None,
        job_id=job_id,
        updated_at=_now_iso(),
    )

    try:
        async with get_tenant_db_context(tenant_id) as db:
            tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
            if tenant is None:
                raise ValueError(f"Tenant {tenant_id} not found for adr.reembed job")

            effective_mode = normalize_ai_processing_mode(
                str(requested_mode) if requested_mode else tenant.ai_processing_mode
            )
            meta = get_mode_metadata(effective_mode)
            target_dims = int(meta["embedding_dimensions"])
            embedding_column = str(meta["embedding_column"])

            stmt = (
                select(AnonymizedADR)
                .where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.status == "approved",
                )
                .order_by(AnonymizedADR.created_at.asc(), AnonymizedADR.id.asc())
            )
            if single_adr_id is not None:
                stmt = stmt.where(AnonymizedADR.id == single_adr_id)

            adrs = list((await db.scalars(stmt)).all())

            total_count = len(adrs)
            state.target_mode = effective_mode
            state.target_dimensions = target_dims
            state.total_count = total_count
            state.reindexed_count = 0
            state.updated_at = _now_iso()
            if track_redis_progress:
                await set_tenant_reindex_status(
                    tenant_id, state, redis_client=redis_client, best_effort=True
                )

            processed = 0
            if adrs:
                if ai_client is not None:
                    if ai_client.embedding_dimensions != target_dims:
                        raise ValueError(
                            f"Injected AI client dimension mismatch: expected {target_dims}, "
                            f"got {ai_client.embedding_dimensions}"
                        )
                    processed = await _embed_and_persist_batches(
                        db,
                        ai_client,
                        adrs,
                        tenant_id=tenant_id,
                        embedding_column=embedding_column,
                        state=state,
                        redis_client=redis_client,
                        track_redis_progress=track_redis_progress,
                    )
                else:
                    async with create_ai_client(effective_mode) as client:
                        processed = await _embed_and_persist_batches(
                            db,
                            client,
                            adrs,
                            tenant_id=tenant_id,
                            embedding_column=embedding_column,
                            state=state,
                            redis_client=redis_client,
                            track_redis_progress=track_redis_progress,
                        )

            completed_state = TenantReindexState(
                reindexing=False,
                status="completed",
                target_mode=effective_mode,
                target_dimensions=target_dims,
                job_id=job_id,
                reindexed_count=processed,
                total_count=total_count,
                error=None,
                updated_at=_now_iso(),
            )
            if track_redis_progress:
                await set_tenant_reindex_status(
                    tenant_id, completed_state, redis_client=redis_client, best_effort=True
                )
            res: dict[str, Any] = {
                "status": "completed",
                "tenant_id": str(tenant_id),
                "job_id": job_id,
                "target_mode": effective_mode,
                "target_dimensions": target_dims,
                "reindexed_count": processed,
                "total_count": total_count,
            }
            if single_adr_id is not None:
                res["adr_id"] = str(single_adr_id)
            return res
    except Exception as exc:
        if track_redis_progress:
            failed_state = TenantReindexState(
                reindexing=False,
                status="failed",
                target_mode=state.target_mode,
                target_dimensions=state.target_dimensions,
                job_id=job_id,
                reindexed_count=state.reindexed_count,
                total_count=state.total_count,
                error=str(exc)[:500],
                updated_at=_now_iso(),
            )
            await set_tenant_reindex_status(
                tenant_id, failed_state, redis_client=redis_client, best_effort=True
            )
        raise
