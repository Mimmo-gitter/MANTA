"""Pydantic schemas for ADR Governance queue, diff viewer, and approval gate (SCRUM-576 / US-43)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ADRStatusCounts(BaseModel):
    """Per-status summary counts displayed on the ADR Governance KPI cards."""

    pending_review: int = 0
    approved: int = 0
    rejected: int = 0
    quarantined: int = 0
    draft: int = 0


class ADRQueueItem(BaseModel):
    """Single ADR record entry returned in the governance review queue."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    source_repository_id: Optional[uuid.UUID] = None
    repository_name: Optional[str] = None
    file_path: Optional[str] = None
    source_commit_sha: Optional[str] = None
    title: str
    decision_summary: Optional[str] = None
    architecture_pattern: Optional[str] = None
    visibility: str
    status: str
    sanitization_verified: bool
    is_indexed: bool = False
    redaction_count: int = 0
    redaction_categories: dict[str, int] = Field(default_factory=dict)
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ADRListResponse(BaseModel):
    """Paginated ADR governance list with status summary counts."""

    items: list[ADRQueueItem]
    total: int
    counts: ADRStatusCounts


class ADRDiffLine(BaseModel):
    """Line-level entry for rendering the side-by-side raw-vs-sanitized diff pane."""

    line_type: Literal["equal", "removed", "added"]
    raw_line: Optional[str] = None
    sanitized_line: Optional[str] = None


class ADRSanitizationLogItem(BaseModel):
    """Audit record produced by Presidio NER & LLM generalization for an ADR."""

    id: uuid.UUID
    adr_id: uuid.UUID
    raw_input_snippet: Optional[str] = None
    presidio_entities_scrubbed: dict[str, Any]
    llm_abstraction_prompt_tokens: Optional[int] = None
    solution_architect_diff_payload: Optional[dict[str, Any]] = None
    created_at: datetime


class ADRDiffResponse(ADRQueueItem):
    """Full side-by-side raw ADR source vs sanitized ADR diff & telemetry payload."""

    raw_markdown: Optional[str] = None
    sanitized_text: str
    presidio_entities_scrubbed: dict[str, Any] = Field(default_factory=dict)
    diff_payload: Optional[dict[str, Any]] = None
    diff_lines: list[ADRDiffLine] = Field(default_factory=list)
    sanitization_logs: list[ADRSanitizationLogItem] = Field(default_factory=list)


class ADRApproveRequest(BaseModel):
    """Request body for approving a sanitized ADR for vector indexing."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: Optional[str] = Field(None, max_length=2000)
    sanitization_confirmed: bool = True

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("rationale cannot be blank")
        return v.strip() if v is not None else None

    @field_validator("sanitization_confirmed")
    @classmethod
    def require_sanitization_confirmed(cls, v: bool) -> bool:
        if not v:
            raise ValueError("sanitization_confirmed must be true to approve an ADR")
        return v


class ADRRejectRequest(BaseModel):
    """Request body for rejecting and quarantining an ADR."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: Optional[str] = Field(None, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("rationale cannot be blank")
        return v.strip() if v is not None else None


class ADRDecisionResponse(BaseModel):
    """Response returned after approving or rejecting an ADR."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    sanitization_verified: bool
    visibility: str
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    embedding_queued: bool = False
    embedding_job_id: Optional[str] = None
    target_embedding_dimensions: Optional[int] = None
    rationale: Optional[str] = None
    updated_at: datetime
