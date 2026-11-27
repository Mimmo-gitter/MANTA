"""SCRUM-582 (TASK-320-1) & SCRUM-585 (TASK-321-1): SCIP persistence, queue dispatch, parity guard & analysis service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import CodeAnalysis, Commit, PullRequest, Repository, ScipIndex
from app.db.session import get_tenant_db_context
from app.observability.tracing import inject_trace_carrier
from app.refactoring.schemas import RefactoringAnalysisResponse, RepositoryAnalysisResultItem
from app.refactoring.scip_indexer import (
    INDEXER_VERSION,
    generate_python_scip_index,
    persist_scip_artifact,
)
from app.refactoring.symbol_graph import (
    InMemorySymbolGraph,
    SymbolGraphRegistry,
    symbol_graph_registry,
)
from app.webhooks.enqueue import get_redis
from workers.worker import (
    QUEUE_DEFAULT,
    QUEUE_HIGH,
    enqueue_job,
    serialize_job_payload,
)

SCIP_INDEX_JOB_TYPE = "scip.index"
ZERO_COMMIT_SHA = "0" * 40


def normalize_commit_sha(raw: Optional[str]) -> Optional[str]:
    """Normalize a commit SHA string to lowercase stripped form (max 40 chars)."""
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    return cleaned[:40] if cleaned else None


@dataclass(frozen=True, slots=True)
class RepositoryScipParity:
    """Parity evaluation between a repository's HEAD SHA and persisted SCIP index metadata."""

    repository_id: uuid.UUID
    project_id: uuid.UUID
    full_name: str
    repository_head_sha: str
    scip_index_metadata_sha: Optional[str]
    scip_index_id: Optional[uuid.UUID]
    scip_storage_uri: Optional[str]
    symbols_count: int
    freshness: str

    @property
    def is_parity_valid(self) -> bool:
        return (
            self.scip_index_metadata_sha is not None
            and self.repository_head_sha == self.scip_index_metadata_sha
        )


async def enqueue_scip_index_job(
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    commit_sha: str,
    priority: str = "default",
    repo_path: Optional[str] = None,
    scip_protobuf_b64: Optional[str] = None,
    reason: Optional[str] = None,
    redis_client: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Enqueue a `scip.index` job onto `arq:queue:default` or `arq:queue:high`.

    Designed to be reused by both `/refactoring-verification` manual triggers (SCRUM-583)
    and high-priority SHA-mismatch re-indexing guards (SCRUM-585).
    """
    norm_priority = priority.strip().lower() if isinstance(priority, str) else "default"
    if norm_priority not in ("default", "high"):
        raise ValueError(f"Unsupported SCIP queue priority: {priority!r}")

    job_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "repository_id": str(repository_id),
        "commit_sha": commit_sha,
    }
    if repo_path:
        payload["repo_path"] = repo_path
    if scip_protobuf_b64:
        payload["scip_protobuf_b64"] = scip_protobuf_b64
    if reason:
        payload["reason"] = reason

    carrier = inject_trace_carrier()
    envelope = serialize_job_payload(
        job_id=job_id,
        tenant_id=str(tenant_id),
        priority=norm_priority,
        job_type=SCIP_INDEX_JOB_TYPE,
        payload=payload,
        provider="github",
        traceparent=carrier.get("traceparent"),
        tracestate=carrier.get("tracestate"),
    )
    client = redis_client or get_redis()
    await enqueue_job(client, envelope, priority=norm_priority)
    return {
        "job_id": job_id,
        "repository_id": repository_id,
        "commit_sha": commit_sha,
        "priority": norm_priority,
        "queue": QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT,
    }


async def upsert_scip_index(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    commit_sha: str,
    storage_uri: str,
    symbols_count: int,
    indexer_version: str = INDEXER_VERSION,
) -> ScipIndex:
    """Idempotently persist or update a `ScipIndex` row keyed by `(repository_id, commit_sha)`."""
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(ScipIndex)
        .values(
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            scip_protobuf_storage_uri=storage_uri,
            symbols_count=int(symbols_count),
            indexer_version=indexer_version,
            created_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_repo_commit_scip",
            set_={
                "scip_protobuf_storage_uri": storage_uri,
                "symbols_count": int(symbols_count),
                "indexer_version": indexer_version,
                "created_at": now,
            },
        )
        .returning(ScipIndex)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def ingest_scip_index_for_repository(
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    commit_sha: str,
    raw_protobuf_bytes: Optional[bytes] = None,
    repo_path: Optional[Path | str] = None,
    repo_name: Optional[str] = None,
    indexer_version: str = INDEXER_VERSION,
    storage_dir: Optional[str] = None,
    load_into_ram: bool = True,
    registry: SymbolGraphRegistry = symbol_graph_registry,
) -> ScipIndex:
    """
    Generate (from `repo_path`) or accept raw `index.scip` protobuf bytes, persist the binary
    artifact, upsert `ScipIndex` in PostgreSQL under tenant RLS, and hydrate the RAM call graph.
    """
    if raw_protobuf_bytes is None:
        if repo_path is None:
            raise ValueError("Either raw_protobuf_bytes or repo_path must be supplied")
        resolved_name = repo_name or str(repository_id)
        raw_protobuf_bytes, symbols_count = generate_python_scip_index(
            repo_path,
            repo_name=resolved_name,
            commit_sha=commit_sha,
        )
        graph = InMemorySymbolGraph.from_protobuf_bytes(raw_protobuf_bytes, commit_sha=commit_sha)
    else:
        graph = InMemorySymbolGraph.from_protobuf_bytes(raw_protobuf_bytes, commit_sha=commit_sha)
        symbols_count = graph.symbol_count

    storage_uri = persist_scip_artifact(
        raw_protobuf_bytes,
        tenant_id=tenant_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        storage_dir=storage_dir,
    )

    async with get_tenant_db_context(tenant_id) as db:
        row = await upsert_scip_index(
            db,
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            storage_uri=storage_uri,
            symbols_count=symbols_count,
            indexer_version=indexer_version,
        )

    if load_into_ram:
        registry.put(
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            graph=graph,
        )

    return row


async def process_scip_index_job(parsed_payload: dict[str, Any]) -> dict[str, Any]:
    """Worker handler for `job_type == 'scip.index'`."""
    inner = (
        parsed_payload.get("payload")
        if isinstance(parsed_payload.get("payload"), dict)
        else parsed_payload
    )
    raw_tenant_id = parsed_payload.get("tenant_id") or inner.get("tenant_id")
    raw_repo_id = inner.get("repository_id") or parsed_payload.get("repository_id")
    if not raw_tenant_id or not raw_repo_id:
        raise ValueError("SCIP job payload missing required tenant_id or repository_id")

    tenant_id = uuid.UUID(str(raw_tenant_id))
    repository_id = uuid.UUID(str(raw_repo_id))
    commit_sha = str(inner.get("commit_sha") or ZERO_COMMIT_SHA)[:40]

    raw_bytes: Optional[bytes] = None
    if inner.get("scip_protobuf_b64"):
        raw_bytes = base64.b64decode(str(inner["scip_protobuf_b64"]))

    repo_path = inner.get("repo_path")
    async with get_tenant_db_context(tenant_id) as db:
        repo = (
            await db.scalars(
                select(Repository).where(
                    Repository.tenant_id == tenant_id,
                    Repository.id == repository_id,
                )
            )
        ).first()
        if repo is None:
            raise LookupError(f"Repository {repository_id} not found for tenant {tenant_id}")
        repo_name = repo.full_name

        if commit_sha == ZERO_COMMIT_SHA:
            latest_commit = (
                await db.scalars(
                    select(Commit.sha)
                    .where(
                        Commit.tenant_id == tenant_id,
                        Commit.repository_id == repository_id,
                    )
                    .order_by(desc(Commit.committed_at))
                    .limit(1)
                )
            ).first()
            if latest_commit:
                commit_sha = latest_commit

    # When no external directory or protobuf payload was supplied, fall back safely
    # to encoding an empty repository manifest unless repo_path is explicitly provided.
    if raw_bytes is None and repo_path is None:
        from app.refactoring.scip_codec import encode_scip_index

        raw_bytes = encode_scip_index((), (), project_root=repo_name)

    scip_row = await ingest_scip_index_for_repository(
        tenant_id=tenant_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        raw_protobuf_bytes=raw_bytes,
        repo_path=repo_path,
        repo_name=repo_name,
    )
    return {
        "status": "indexed",
        "scip_index_id": str(scip_row.id),
        "repository_id": str(repository_id),
        "commit_sha": scip_row.commit_sha,
        "symbols_count": scip_row.symbols_count,
    }


async def get_latest_scip_index(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
) -> Optional[ScipIndex]:
    """Return the most recently persisted `ScipIndex` record for a repository under tenant RLS."""
    return (
        await db.scalars(
            select(ScipIndex)
            .where(
                ScipIndex.tenant_id == tenant_id,
                ScipIndex.repository_id == repository_id,
            )
            .order_by(desc(ScipIndex.created_at))
            .limit(1)
        )
    ).first()


async def list_repository_scip_statuses(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: Optional[uuid.UUID] = None,
    registry: SymbolGraphRegistry = symbol_graph_registry,
) -> list[dict[str, Any]]:
    """List tenant repositories with latest SCIP index metadata, freshness, and RAM cache state."""
    repo_stmt = select(Repository).where(Repository.tenant_id == tenant_id)
    if project_id is not None:
        repo_stmt = repo_stmt.where(Repository.project_id == project_id)
    repo_stmt = repo_stmt.order_by(Repository.full_name.asc())
    repos = (await db.scalars(repo_stmt)).all()
    if not repos:
        return []

    repo_ids = [r.id for r in repos]

    scip_rows = (
        await db.scalars(
            select(ScipIndex)
            .where(
                ScipIndex.tenant_id == tenant_id,
                ScipIndex.repository_id.in_(repo_ids),
            )
            .order_by(ScipIndex.repository_id, desc(ScipIndex.created_at))
        )
    ).all()
    latest_scip_by_repo: dict[uuid.UUID, ScipIndex] = {}
    for row in scip_rows:
        latest_scip_by_repo.setdefault(row.repository_id, row)

    commit_rows = (
        await db.execute(
            select(Commit.repository_id, Commit.sha)
            .where(
                Commit.tenant_id == tenant_id,
                Commit.repository_id.in_(repo_ids),
            )
            .order_by(Commit.repository_id, desc(Commit.committed_at))
        )
    ).all()
    latest_commit_by_repo: dict[uuid.UUID, str] = {}
    for repo_id_val, sha_val in commit_rows:
        latest_commit_by_repo.setdefault(repo_id_val, sha_val)

    items: list[dict[str, Any]] = []
    for repo in repos:
        latest_scip = latest_scip_by_repo.get(repo.id)
        latest_commit_sha = latest_commit_by_repo.get(repo.id)
        if latest_scip is None:
            freshness = "MISSING"
            indexed_sha = None
            symbols_count = 0
            indexer_version = None
            indexed_at = None
            storage_uri = None
            loaded_in_ram = False
        else:
            indexed_sha = latest_scip.commit_sha
            if latest_commit_sha and latest_commit_sha != indexed_sha:
                freshness = "STALE"
            else:
                freshness = "FRESH"
            symbols_count = latest_scip.symbols_count
            indexer_version = latest_scip.indexer_version
            indexed_at = latest_scip.created_at
            storage_uri = latest_scip.scip_protobuf_storage_uri
            loaded_in_ram = registry.is_loaded(
                tenant_id=tenant_id,
                repository_id=repo.id,
                commit_sha=indexed_sha,
            )

        items.append(
            {
                "repository_id": repo.id,
                "project_id": repo.project_id,
                "full_name": repo.full_name,
                "default_branch": repo.default_branch,
                "commit_sha": indexed_sha,
                "latest_commit_sha": latest_commit_sha,
                "freshness": freshness,
                "symbols_count": symbols_count,
                "indexer_version": indexer_version,
                "indexed_at": indexed_at,
                "graph_loaded_in_ram": loaded_in_ram,
                "scip_protobuf_storage_uri": storage_uri,
            }
        )

    return items


async def get_scip_index_for_commit(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    commit_sha: str,
) -> Optional[ScipIndex]:
    """Return a `ScipIndex` row matching `(repository_id, commit_sha)` under tenant RLS."""
    normalized = normalize_commit_sha(commit_sha)
    if not normalized:
        return None
    return (
        await db.scalars(
            select(ScipIndex)
            .where(
                ScipIndex.tenant_id == tenant_id,
                ScipIndex.repository_id == repository_id,
                func.lower(ScipIndex.commit_sha) == normalized,
            )
            .order_by(desc(ScipIndex.created_at))
            .limit(1)
        )
    ).first()


async def resolve_repository_head_sha(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    explicit_head_sha: Optional[str] = None,
    pr_head_sha: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve the authoritative `repository_head_sha` for a repository.

    Priority order:
    1. Explicit `head_sha` passed by caller
    2. Explicit `pull_request_id`'s `PullRequest.head_sha`
    3. Most recent `Commit.sha` for the repository (`committed_at DESC, created_at DESC`)
    4. Fallback to most recent `PullRequest.head_sha` (`updated_at DESC, created_at DESC`)
    """
    explicit = normalize_commit_sha(explicit_head_sha)
    if explicit:
        return explicit

    pr_sha = normalize_commit_sha(pr_head_sha)
    if pr_sha:
        return pr_sha

    latest_commit_sha = (
        await db.scalars(
            select(Commit.sha)
            .where(
                Commit.tenant_id == tenant_id,
                Commit.repository_id == repository_id,
            )
            .order_by(desc(Commit.committed_at), desc(Commit.created_at))
            .limit(1)
        )
    ).first()
    if latest_commit_sha:
        return normalize_commit_sha(latest_commit_sha)

    latest_pr_sha = (
        await db.scalars(
            select(PullRequest.head_sha)
            .where(
                PullRequest.tenant_id == tenant_id,
                PullRequest.repository_id == repository_id,
            )
            .order_by(desc(PullRequest.updated_at), desc(PullRequest.created_at))
            .limit(1)
        )
    ).first()
    return normalize_commit_sha(latest_pr_sha)


async def evaluate_project_scip_parity(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    repository_id: Optional[uuid.UUID] = None,
    requested_head_sha: Optional[str] = None,
    pull_request_id: Optional[uuid.UUID] = None,
) -> list[RepositoryScipParity]:
    """
    Evaluate `repository_head_sha == scip_index_metadata_sha` parity for target repositories
    of a project under tenant RLS.
    """
    target_repo_id = repository_id
    pr_head_sha: Optional[str] = None

    if pull_request_id is not None:
        pr = (
            await db.scalars(
                select(PullRequest).where(
                    PullRequest.tenant_id == tenant_id,
                    PullRequest.project_id == project_id,
                    PullRequest.id == pull_request_id,
                )
            )
        ).first()
        if pr is None:
            raise LookupError(f"Pull request {pull_request_id} not found in project {project_id}")
        if target_repo_id is not None and pr.repository_id != target_repo_id:
            raise LookupError(
                f"Pull request {pull_request_id} does not belong to repository {target_repo_id}"
            )
        target_repo_id = pr.repository_id
        pr_head_sha = pr.head_sha

    repo_stmt = select(Repository).where(
        Repository.tenant_id == tenant_id,
        Repository.project_id == project_id,
    )
    if target_repo_id is not None:
        repo_stmt = repo_stmt.where(Repository.id == target_repo_id)
    repo_stmt = repo_stmt.order_by(Repository.full_name.asc())
    repos = (await db.scalars(repo_stmt)).all()

    if not repos:
        if target_repo_id is not None:
            raise LookupError(f"Repository {target_repo_id} not found in project {project_id}")
        raise LookupError(f"No repositories linked to project {project_id}")

    parity_entries: list[RepositoryScipParity] = []
    for repo in repos:
        resolved_head = await resolve_repository_head_sha(
            db,
            tenant_id=tenant_id,
            repository_id=repo.id,
            explicit_head_sha=requested_head_sha,
            pr_head_sha=pr_head_sha,
        )

        if resolved_head is not None:
            matched_scip = await get_scip_index_for_commit(
                db,
                tenant_id=tenant_id,
                repository_id=repo.id,
                commit_sha=resolved_head,
            )
            if matched_scip is not None:
                scip_row = matched_scip
                scip_sha = normalize_commit_sha(matched_scip.commit_sha)
            else:
                scip_row = await get_latest_scip_index(
                    db,
                    tenant_id=tenant_id,
                    repository_id=repo.id,
                )
                scip_sha = (
                    normalize_commit_sha(scip_row.commit_sha) if scip_row is not None else None
                )
            head_sha = resolved_head
        else:
            scip_row = await get_latest_scip_index(
                db,
                tenant_id=tenant_id,
                repository_id=repo.id,
            )
            if scip_row is not None:
                scip_sha = normalize_commit_sha(scip_row.commit_sha)
                head_sha = scip_sha or ZERO_COMMIT_SHA
            else:
                scip_sha = None
                head_sha = ZERO_COMMIT_SHA

        if scip_sha is None:
            freshness = "MISSING"
        elif head_sha != scip_sha:
            freshness = "STALE"
        else:
            freshness = "FRESH"

        parity_entries.append(
            RepositoryScipParity(
                repository_id=repo.id,
                project_id=repo.project_id,
                full_name=repo.full_name,
                repository_head_sha=head_sha,
                scip_index_metadata_sha=scip_sha,
                scip_index_id=scip_row.id if scip_row is not None else None,
                scip_storage_uri=scip_row.scip_protobuf_storage_uri
                if scip_row is not None
                else None,
                symbols_count=scip_row.symbols_count if scip_row is not None else 0,
                freshness=freshness,
            )
        )

    return parity_entries


async def execute_refactoring_analysis(
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    parity_entries: list[RepositoryScipParity],
    target_symbol: Optional[str] = None,
    registry: SymbolGraphRegistry = symbol_graph_registry,
) -> RefactoringAnalysisResponse:
    """
    Execute refactoring static analysis and persist `CodeAnalysis` record(s) under tenant RLS.

    Defense-in-depth: asserts that every entry passed `repository_head_sha == scip_index_metadata_sha`.
    """
    if not parity_entries:
        raise ValueError("Cannot execute refactoring analysis without target repositories")

    for entry in parity_entries:
        if not entry.is_parity_valid or entry.scip_index_id is None:
            raise RuntimeError(
                f"SCIP parity invariant violated for repository {entry.repository_id}: "
                f"head_sha={entry.repository_head_sha!r} != scip_sha={entry.scip_index_metadata_sha!r}"
            )

    analysis_records: list[
        tuple[RepositoryScipParity, CodeAnalysis, int, int, bool, Optional[int], Optional[int]]
    ] = []
    async with get_tenant_db_context(tenant_id) as db:
        for entry in parity_entries:
            graph = registry.get(
                tenant_id=tenant_id,
                repository_id=entry.repository_id,
                commit_sha=entry.repository_head_sha,
            )
            if graph is None and entry.scip_storage_uri:
                try:
                    graph = registry.get_or_load(
                        tenant_id=tenant_id,
                        repository_id=entry.repository_id,
                        commit_sha=entry.repository_head_sha,
                        storage_uri=entry.scip_storage_uri,
                    )
                except (FileNotFoundError, OSError, ValueError):
                    graph = None

            graph_loaded = graph is not None
            symbols_count = graph.symbol_count if graph is not None else entry.symbols_count
            edges_count = graph.edge_count if graph is not None else 0
            callers_count = (
                len(graph.get_callers(target_symbol))
                if (graph is not None and target_symbol)
                else None
            )
            callees_count = (
                len(graph.get_callees(target_symbol))
                if (graph is not None and target_symbol)
                else None
            )
            tech_debt_score = (
                round(min(100.0, (edges_count / symbols_count) * 10.0), 2)
                if symbols_count > 0 and edges_count > 0
                else 0.0
            )

            analysis_row = CodeAnalysis(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                repository_id=entry.repository_id,
                commit_sha=entry.repository_head_sha,
                tech_debt_score=tech_debt_score,
                created_at=datetime.now(timezone.utc),
            )
            db.add(analysis_row)
            analysis_records.append(
                (
                    entry,
                    analysis_row,
                    symbols_count,
                    edges_count,
                    graph_loaded,
                    callers_count,
                    callees_count,
                )
            )

        await db.flush()

        items: list[RepositoryAnalysisResultItem] = []
        for (
            entry,
            row,
            symbols_count,
            edges_count,
            graph_loaded,
            callers_count,
            callees_count,
        ) in analysis_records:
            assert entry.scip_index_id is not None
            assert entry.scip_index_metadata_sha is not None
            items.append(
                RepositoryAnalysisResultItem(
                    analysis_id=row.id,
                    repository_id=entry.repository_id,
                    full_name=entry.full_name,
                    commit_sha=row.commit_sha,
                    scip_index_id=entry.scip_index_id,
                    scip_index_metadata_sha=entry.scip_index_metadata_sha,
                    repository_head_sha=entry.repository_head_sha,
                    symbols_count=symbols_count,
                    edges_count=edges_count,
                    graph_loaded_in_ram=graph_loaded,
                    tech_debt_score=float(row.tech_debt_score),
                    callers_count=callers_count,
                    callees_count=callees_count,
                    created_at=row.created_at,
                )
            )

    primary = items[0]
    avg_tech_debt = round(sum(item.tech_debt_score for item in items) / len(items), 2)
    return RefactoringAnalysisResponse(
        analysis_id=primary.analysis_id,
        project_id=project_id,
        repository_id=primary.repository_id,
        commit_sha=primary.commit_sha,
        repository_head_sha=primary.repository_head_sha,
        scip_index_metadata_sha=primary.scip_index_metadata_sha,
        scip_index_id=primary.scip_index_id,
        parity_verified=True,
        status="completed",
        symbols_count=sum(item.symbols_count for item in items),
        edges_count=sum(item.edges_count for item in items),
        graph_loaded_in_ram=all(item.graph_loaded_in_ram for item in items),
        tech_debt_score=avg_tech_debt,
        target_symbol=target_symbol,
        callers_count=primary.callers_count,
        callees_count=primary.callees_count,
        analyses=items,
        created_at=primary.created_at,
    )
