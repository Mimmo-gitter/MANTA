"""Pydantic schemas for partitioned pgvector semantic ADR search (SCRUM-579 / US-45)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, Field


class ADRSearchResultItem(BaseModel):
    """Single approved ADR card returned by semantic vector search."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    title: str
    decision_summary: Optional[str] = None
    architecture_pattern: Optional[str] = None
    visibility: str
    status: str
    sanitization_verified: bool
    similarity: Optional[float] = None
    redaction_count: int = 0
    source_commit_sha: Optional[str] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ADRSearchResponse(BaseModel):
    """Ranked semantic search results plus index rebuild status and active AI mode metadata."""

    query: str
    items: list[ADRSearchResultItem]
    total: int
    rebuilding: bool = False
    reindex_status: str = "idle"
    reindexed_count: int = 0
    reindex_total_count: int = 0
    ai_processing_mode: str
    active_embedding_dimensions: int
    active_embedding_model: str
    available_patterns: list[str] = Field(default_factory=list)
