"""SCRUM-582 (TASK-320-1): Pydantic schemas for SCIP indexing, RAM symbol call graph, and refactoring status."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, Field

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
