"""Domain service for ADR Governance queue, diff review, approval & quarantine (SCRUM-576)."""

from __future__ import annotations

from datetime import datetime, timezone
import difflib
from typing import Any, Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adr.schemas import (
    ADRDecisionResponse,
    ADRDiffLine,
    ADRDiffResponse,
    ADRListResponse,
    ADRQueueItem,
    ADRSanitizationLogItem,
    ADRStatusCounts,
)
from app.ai.client_factory import (
    AI_MODE_LOCAL_ONLY,
    get_mode_metadata,
    normalize_ai_processing_mode,
)
from app.ai.reindex_service import enqueue_adr_embed_job
from app.auth.schemas import TokenClaims
from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import (
    ADRSanitizationLog,
    ADRSource,
    AnonymizedADR,
    AuditLog,
    Repository,
    Tenant,
    User,
)
from app.db.session import get_tenant_db_context


def extract_redaction_summary(
    presidio_entities_scrubbed: dict[str, Any] | None,
) -> tuple[int, dict[str, int]]:
    """
    Normalize heterogeneous JSONB sanitization telemetry payloads into
    `(total_redactions, {category: count})`.
    """
    if not isinstance(presidio_entities_scrubbed, dict) or not presidio_entities_scrubbed:
        return 0, {}

    categories: dict[str, int] = {}

    nested_counts = presidio_entities_scrubbed.get("counts")
    if isinstance(nested_counts, dict):
        for key, val in nested_counts.items():
            if isinstance(val, (int, float)) and int(val) > 0:
                categories[str(key)] = int(val)

    entities_list = presidio_entities_scrubbed.get("entities")
    if isinstance(entities_list, list) and not categories:
        for item in entities_list:
            if isinstance(item, dict):
                label = str(item.get("entity_type") or item.get("type") or "PII")
                categories[label] = categories.get(label, 0) + 1
            elif isinstance(item, str) and item.strip():
                categories[item.strip()] = categories.get(item.strip(), 0) + 1

    if not categories:
        for key, val in presidio_entities_scrubbed.items():
            if key in {"total", "redaction_count", "entities", "counts"}:
                continue
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)) and int(val) > 0:
                categories[str(key)] = int(val)
            elif isinstance(val, list) and val:
                categories[str(key)] = len(val)

    explicit_total = presidio_entities_scrubbed.get("total")
    if isinstance(explicit_total, (int, float)) and explicit_total >= sum(categories.values()):
        total = int(explicit_total)
    else:
        total = sum(categories.values())

    return total, categories


def build_sanitized_adr_text(adr: AnonymizedADR) -> str:
    """Render readable sanitized ADR Markdown representation from AnonymizedADR fields."""
    lines = [f"# {adr.title}"]
    if adr.architecture_pattern:
        lines.append(f"Pattern: {adr.architecture_pattern}")
    if adr.decision_summary:
        lines.append("")
        lines.append(adr.decision_summary)
    return "\n".join(lines).strip()


def compute_side_by_side_diff(raw_text: str | None, sanitized_text: str) -> list[ADRDiffLine]:
    """Compute aligned line-by-line diff between raw ADR Markdown and sanitized output."""
    sanitized_lines = sanitized_text.splitlines() if sanitized_text else []
    if not raw_text:
        return [
            ADRDiffLine(line_type="added", raw_line=None, sanitized_line=line)
            for line in sanitized_lines
        ]

    raw_lines = raw_text.splitlines()
    matcher = difflib.SequenceMatcher(a=raw_lines, b=sanitized_lines)
    result: list[ADRDiffLine] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for r_line, s_line in zip(raw_lines[i1:i2], sanitized_lines[j1:j2]):
                result.append(
                    ADRDiffLine(line_type="equal", raw_line=r_line, sanitized_line=s_line)
                )
        elif tag in ("replace", "delete"):
            for r_line in raw_lines[i1:i2]:
                result.append(
                    ADRDiffLine(line_type="removed", raw_line=r_line, sanitized_line=None)
                )
            if tag == "replace":
                for s_line in sanitized_lines[j1:j2]:
                    result.append(
                        ADRDiffLine(line_type="added", raw_line=None, sanitized_line=s_line)
                    )
        elif tag == "insert":
            for s_line in sanitized_lines[j1:j2]:
                result.append(ADRDiffLine(line_type="added", raw_line=None, sanitized_line=s_line))
    return result


async def _resolve_actor_user_id(db: AsyncSession, current_user: TokenClaims) -> uuid.UUID | None:
    predicates = []
    if current_user.sub:
        predicates.append(User.google_sub == current_user.sub)
    if current_user.email:
        predicates.append(func.lower(User.email) == current_user.email.strip().lower())
    if current_user.user_id is not None:
        predicates.append(User.id == current_user.user_id)
    if not predicates:
        return None
    return await db.scalar(
        select(User.id).where(User.tenant_id == current_user.tenant_uuid, or_(*predicates)).limit(1)
    )


def _is_adr_indexed(adr: AnonymizedADR, active_mode: str) -> bool:
    if normalize_ai_processing_mode(active_mode) == AI_MODE_LOCAL_ONLY:
        return adr.embedding_384 is not None
    return adr.embedding_1536 is not None


def _build_queue_item(
    adr: AnonymizedADR,
    *,
    active_mode: str,
    source: ADRSource | None,
    repo_name: str | None,
    latest_log: ADRSanitizationLog | None,
) -> ADRQueueItem:
    total_redactions, categories = extract_redaction_summary(
        latest_log.presidio_entities_scrubbed if latest_log else None
    )
    return ADRQueueItem(
        id=adr.id,
        tenant_id=adr.tenant_id,
        project_id=adr.project_id,
        source_repository_id=adr.source_repository_id,
        repository_name=repo_name,
        file_path=source.file_path if source else None,
        source_commit_sha=adr.source_commit_sha or (source.commit_sha if source else None),
        title=adr.title,
        decision_summary=adr.decision_summary,
        architecture_pattern=adr.architecture_pattern,
        visibility=adr.visibility,
        status=adr.status,
        sanitization_verified=adr.sanitization_verified,
        is_indexed=_is_adr_indexed(adr, active_mode),
        redaction_count=total_redactions,
        redaction_categories=categories,
        approved_by=adr.approved_by,
        approved_at=adr.approved_at,
        created_at=adr.created_at,
        updated_at=adr.updated_at,
    )


async def list_tenant_adrs(
    tenant_id: uuid.UUID,
    *,
    status_filter: Optional[str] = None,
    project_id: Optional[uuid.UUID] = None,
    repository_id: Optional[uuid.UUID] = None,
    limit: int = 50,
    offset: int = 0,
) -> ADRListResponse:
    """List tenant ADRs enriched with repository metadata, source path, and redaction counts."""
    async with get_tenant_db_context(tenant_id) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        active_mode = tenant.ai_processing_mode if tenant is not None else "cloud_zdr"

        count_rows = (
            await db.execute(
                select(AnonymizedADR.status, func.count())
                .where(AnonymizedADR.tenant_id == tenant_id)
                .group_by(AnonymizedADR.status)
            )
        ).all()
        counts_map = {str(st): int(cnt) for st, cnt in count_rows}
        counts = ADRStatusCounts(
            pending_review=counts_map.get("pending_review", 0),
            approved=counts_map.get("approved", 0),
            rejected=counts_map.get("rejected", 0),
            quarantined=counts_map.get("quarantined", 0),
            draft=counts_map.get("draft", 0),
        )

        filters = [AnonymizedADR.tenant_id == tenant_id]
        if status_filter:
            filters.append(AnonymizedADR.status == status_filter.strip().lower())
        if project_id is not None:
            filters.append(AnonymizedADR.project_id == project_id)
        if repository_id is not None:
            filters.append(AnonymizedADR.source_repository_id == repository_id)

        total = int(
            (await db.scalar(select(func.count()).select_from(AnonymizedADR).where(*filters))) or 0
        )

        adrs = list(
            (
                await db.scalars(
                    select(AnonymizedADR)
                    .where(*filters)
                    .order_by(AnonymizedADR.created_at.desc(), AnonymizedADR.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        if not adrs:
            return ADRListResponse(items=[], total=total, counts=counts)

        adr_ids = [a.id for a in adrs]
        repo_ids = list(
            {a.source_repository_id for a in adrs if a.source_repository_id is not None}
        )

        sources_map: dict[uuid.UUID, ADRSource] = {}
        for src in (
            await db.scalars(
                select(ADRSource).where(
                    ADRSource.tenant_id == tenant_id,
                    ADRSource.adr_id.in_(adr_ids),
                )
            )
        ).all():
            sources_map[src.adr_id] = src

        repos_map: dict[uuid.UUID, str] = {}
        if repo_ids:
            for repo_id, full_name in (
                await db.execute(
                    select(Repository.id, Repository.full_name).where(
                        Repository.tenant_id == tenant_id,
                        Repository.id.in_(repo_ids),
                    )
                )
            ).all():
                repos_map[repo_id] = full_name

        logs_map: dict[uuid.UUID, ADRSanitizationLog] = {}
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
            if log_row.adr_id not in logs_map:
                logs_map[log_row.adr_id] = log_row

        items = [
            _build_queue_item(
                adr,
                active_mode=active_mode,
                source=sources_map.get(adr.id),
                repo_name=repos_map.get(adr.source_repository_id)
                if adr.source_repository_id
                else None,
                latest_log=logs_map.get(adr.id),
            )
            for adr in adrs
        ]
        return ADRListResponse(items=items, total=total, counts=counts)


async def get_adr_diff(tenant_id: uuid.UUID, adr_id: uuid.UUID) -> ADRDiffResponse:
    """Return raw ADR source joined with sanitized output, diff lines, and telemetry logs."""
    async with get_tenant_db_context(tenant_id) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        active_mode = tenant.ai_processing_mode if tenant is not None else "cloud_zdr"

        adr = await db.scalar(
            select(AnonymizedADR).where(
                AnonymizedADR.tenant_id == tenant_id,
                AnonymizedADR.id == adr_id,
            )
        )
        if adr is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ADR not found")

        source = await db.scalar(
            select(ADRSource).where(
                ADRSource.tenant_id == tenant_id,
                ADRSource.adr_id == adr_id,
            )
        )

        repo_name: Optional[str] = None
        if adr.source_repository_id is not None:
            repo_name = await db.scalar(
                select(Repository.full_name).where(
                    Repository.tenant_id == tenant_id,
                    Repository.id == adr.source_repository_id,
                )
            )

        logs = list(
            (
                await db.scalars(
                    select(ADRSanitizationLog)
                    .where(
                        ADRSanitizationLog.tenant_id == tenant_id,
                        ADRSanitizationLog.adr_id == adr_id,
                    )
                    .order_by(ADRSanitizationLog.created_at.desc(), ADRSanitizationLog.id.desc())
                )
            ).all()
        )
        latest_log = logs[0] if logs else None

        base_item = _build_queue_item(
            adr,
            active_mode=active_mode,
            source=source,
            repo_name=repo_name,
            latest_log=latest_log,
        )

        raw_markdown = (
            source.raw_markdown
            if source
            else (latest_log.raw_input_snippet if latest_log else None)
        )
        sanitized_text = build_sanitized_adr_text(adr)
        diff_lines = compute_side_by_side_diff(raw_markdown, sanitized_text)

        return ADRDiffResponse(
            **base_item.model_dump(),
            raw_markdown=raw_markdown,
            sanitized_text=sanitized_text,
            presidio_entities_scrubbed=(
                latest_log.presidio_entities_scrubbed if latest_log else {}
            ),
            diff_payload=(latest_log.solution_architect_diff_payload if latest_log else None),
            diff_lines=diff_lines,
            sanitization_logs=[
                ADRSanitizationLogItem(
                    id=row.id,
                    adr_id=row.adr_id,
                    raw_input_snippet=row.raw_input_snippet,
                    presidio_entities_scrubbed=row.presidio_entities_scrubbed,
                    llm_abstraction_prompt_tokens=row.llm_abstraction_prompt_tokens,
                    solution_architect_diff_payload=row.solution_architect_diff_payload,
                    created_at=row.created_at,
                )
                for row in logs
            ],
        )


async def approve_tenant_adr(
    tenant_id: uuid.UUID,
    adr_id: uuid.UUID,
    *,
    current_user: TokenClaims,
    rationale: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    redis_client: Optional[Any] = None,
) -> ADRDecisionResponse:
    """Approve sanitized ADR, persist audit trail, and enqueue vector embedding."""
    async with get_tenant_db_context(tenant_id) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        active_mode = tenant.ai_processing_mode if tenant is not None else "cloud_zdr"

        adr = await db.scalar(
            select(AnonymizedADR).where(
                AnonymizedADR.tenant_id == tenant_id,
                AnonymizedADR.id == adr_id,
            )
        )
        if adr is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ADR not found")

        if adr.status == "quarantined":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Quarantined ADRs cannot be approved through the normal review workflow.",
            )

        actor_id = await _resolve_actor_user_id(db, current_user)
        now = datetime.now(timezone.utc)
        payload_before = {
            "status": adr.status,
            "sanitization_verified": adr.sanitization_verified,
            "visibility": adr.visibility,
        }

        adr.status = "approved"
        adr.sanitization_verified = True
        adr.approved_by = actor_id
        adr.approved_at = now
        adr.updated_at = now

        payload_after = {
            "status": adr.status,
            "sanitization_verified": adr.sanitization_verified,
            "visibility": adr.visibility,
            "rationale": rationale,
        }
        db.add(
            AuditLog(
                id=generate_uuidv7(),
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="adr.approved",
                entity_type="anonymized_adr",
                entity_id=str(adr.id),
                ip_address=client_ip,
                user_agent=user_agent,
                payload_before=payload_before,
                payload_after=payload_after,
            )
        )
        await db.flush()

    meta = get_mode_metadata(active_mode)
    embed_info = await enqueue_adr_embed_job(
        tenant_id=tenant_id,
        adr_id=adr.id,
        target_mode=str(meta["mode"]),
        priority="high",
        redis_client=redis_client,
    )

    return ADRDecisionResponse(
        id=adr.id,
        tenant_id=adr.tenant_id,
        status=adr.status,
        sanitization_verified=adr.sanitization_verified,
        visibility=adr.visibility,
        approved_by=adr.approved_by,
        approved_at=adr.approved_at,
        embedding_queued=True,
        embedding_job_id=str(embed_info["job_id"]),
        target_embedding_dimensions=int(embed_info["target_dimensions"]),
        rationale=rationale,
        updated_at=adr.updated_at,
    )


async def reject_tenant_adr(
    tenant_id: uuid.UUID,
    adr_id: uuid.UUID,
    *,
    current_user: TokenClaims,
    rationale: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> ADRDecisionResponse:
    """Reject ADR and move it to `quarantined` without creating any vector embedding."""
    async with get_tenant_db_context(tenant_id) as db:
        adr = await db.scalar(
            select(AnonymizedADR).where(
                AnonymizedADR.tenant_id == tenant_id,
                AnonymizedADR.id == adr_id,
            )
        )
        if adr is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ADR not found")

        actor_id = await _resolve_actor_user_id(db, current_user)
        now = datetime.now(timezone.utc)
        payload_before = {
            "status": adr.status,
            "sanitization_verified": adr.sanitization_verified,
            "visibility": adr.visibility,
        }

        adr.status = "quarantined"
        adr.sanitization_verified = False
        adr.embedding_384 = None
        adr.embedding_1536 = None
        adr.updated_at = now

        payload_after = {
            "status": adr.status,
            "sanitization_verified": adr.sanitization_verified,
            "visibility": adr.visibility,
            "rationale": rationale,
        }
        db.add(
            AuditLog(
                id=generate_uuidv7(),
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="adr.rejected",
                entity_type="anonymized_adr",
                entity_id=str(adr.id),
                ip_address=client_ip,
                user_agent=user_agent,
                payload_before=payload_before,
                payload_after=payload_after,
            )
        )
        await db.flush()

    return ADRDecisionResponse(
        id=adr.id,
        tenant_id=adr.tenant_id,
        status=adr.status,
        sanitization_verified=adr.sanitization_verified,
        visibility=adr.visibility,
        approved_by=adr.approved_by,
        approved_at=adr.approved_at,
        embedding_queued=False,
        embedding_job_id=None,
        target_embedding_dimensions=None,
        rationale=rationale,
        updated_at=adr.updated_at,
    )
