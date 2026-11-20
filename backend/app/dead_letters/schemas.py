"""Task 23.4: Dead-letter queue (DLQ) API schemas."""

from datetime import datetime
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict


class DeadLetterItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_event_id: uuid.UUID
    provider: str
    error_class: str
    stack_trace: str
    total_attempts: int
    payload: dict[str, Any]
    quarantined_at: datetime
    is_resolved: bool
    resolved_by: Optional[uuid.UUID] = None
    resolved_at: Optional[datetime] = None


class DeadLetterListResponse(BaseModel):
    items: list[DeadLetterItem]
    unresolved_count: int
    total: int
    limit: int
    offset: int


class RedriveResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    status: Literal["queued"] = "queued"
    queue: str
    enqueued: bool
    resolved_at: datetime
