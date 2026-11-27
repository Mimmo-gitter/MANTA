"""SCRUM-582 (TASK-320-1) & SCRUM-585 (TASK-321-1): Pydantic schemas for SCIP indexing, call graph & refactoring analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, Field, field_validator

ScipFreshness = Literal["FRESH", "STALE", "MISSING"]
ScipQueuePriority = Literal["default", "high"]


class ScipRepositoryStatusItem(BaseModel):
    repository_id: uuid.UUID
    project_id: uuid.UUID
    full_name: str
    default_branch: str
    commit_sha: Optional[str] = None
    latest_commit_sha: Optional[str] = None
    freshness: ScipFreshness
    symbols_count: int = 0
    indexer_version: Optional[str] = None
    indexed_at: Optional[datetime] = None
    graph_loaded_in_ram: bool = False
    scip_protobuf_storage_uri: Optional[str] = None


class ScipIndexStatusListResponse(BaseModel):
    repositories: list[ScipRepositoryStatusItem]
    total: int


class TriggerScipIndexRequest(BaseModel):
    commit_sha: Optional[str] = Field(default=None, max_length=40)
    priority: ScipQueuePriority = "default"
    repo_path: Optional[str] = None


class TriggerScipIndexResponse(BaseModel):
    job_id: str
    repository_id: uuid.UUID
    commit_sha: str
    priority: ScipQueuePriority
    queue: str
    status: Literal["queued"] = "queued"


class ScipGraphNode(BaseModel):
    id: str
    display_name: str
    file_path: str
    line: int
    kind: str
    enclosing_symbol: str = ""


class ScipGraphEdge(BaseModel):
    source: str
    target: str
    relation: str = "calls"


class ScipGraphResponse(BaseModel):
    project_id: uuid.UUID
    repository_id: Optional[uuid.UUID] = None
    commit_sha: Optional[str] = None
    freshness: ScipFreshness
    symbols_count: int = 0
    nodes: list[ScipGraphNode] = Field(default_factory=list)
    edges: list[ScipGraphEdge] = Field(default_factory=list)
    lookup_ms: float = 0.0


class ScipCallerLookupResponse(BaseModel):
    repository_id: uuid.UUID
    commit_sha: str
    target_symbol: str
    node: Optional[ScipGraphNode] = None
    callers: list[ScipGraphNode] = Field(default_factory=list)
    callees: list[ScipGraphNode] = Field(default_factory=list)
    lookup_ms: float


class AnalyzeRefactoringRequest(BaseModel):
    repository_id: Optional[uuid.UUID] = None
    head_sha: Optional[str] = Field(default=None, max_length=40)
    pull_request_id: Optional[uuid.UUID] = None
    target_symbol: Optional[str] = Field(default=None, max_length=512)
    repo_path: Optional[str] = None

    @field_validator("head_sha", mode="before")
    @classmethod
    def _normalize_head_sha(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip().lower()
            return cleaned or None
        return str(value).strip().lower() or None


class QueuedScipReindexJob(BaseModel):
    job_id: str
    repository_id: uuid.UUID
    commit_sha: str
    priority: ScipQueuePriority = "high"
    queue: str


class StaleRepositoryParityDetail(BaseModel):
    repository_id: uuid.UUID
    full_name: str
    repository_head_sha: str
    scip_index_metadata_sha: Optional[str] = None
    freshness: ScipFreshness
    reindex_job_id: str
    queue: str


class ScipParityConflictDetail(BaseModel):
    code: Literal["SCIP_INDEX_STALE"] = "SCIP_INDEX_STALE"
    message: str
    project_id: uuid.UUID
    repository_id: uuid.UUID
    repository_head_sha: str
    scip_index_metadata_sha: Optional[str] = None
    queue: str
    reindex_job_id: str
    reindex_jobs: list[QueuedScipReindexJob] = Field(default_factory=list)
    stale_repositories: list[StaleRepositoryParityDetail] = Field(default_factory=list)


class ScipParityConflictResponse(BaseModel):
    detail: ScipParityConflictDetail


class RepositoryAnalysisResultItem(BaseModel):
    analysis_id: uuid.UUID
    repository_id: uuid.UUID
    full_name: str
    commit_sha: str
    scip_index_id: uuid.UUID
    scip_index_metadata_sha: str
    repository_head_sha: str
    symbols_count: int = 0
    edges_count: int = 0
    graph_loaded_in_ram: bool = False
    tech_debt_score: float = 0.0
    callers_count: Optional[int] = None
    callees_count: Optional[int] = None
    created_at: datetime


class RefactoringAnalysisResponse(BaseModel):
    analysis_id: uuid.UUID
    project_id: uuid.UUID
    repository_id: uuid.UUID
    commit_sha: str
    repository_head_sha: str
    scip_index_metadata_sha: str
    scip_index_id: uuid.UUID
    parity_verified: bool = True
    status: Literal["completed"] = "completed"
    symbols_count: int = 0
    edges_count: int = 0
    graph_loaded_in_ram: bool = False
    tech_debt_score: float = 0.0
    target_symbol: Optional[str] = None
    callers_count: Optional[int] = None
    callees_count: Optional[int] = None
    analyses: list[RepositoryAnalysisResultItem] = Field(default_factory=list)
    created_at: datetime
