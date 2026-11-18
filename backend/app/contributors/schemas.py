from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel


class ContributorSummaryResponse(BaseModel):
    """Pseudonymized contributor record with optionally decrypted PII."""

    pseudonym_id: str
    tenant_id: uuid.UUID
    provider: str
    external_actor_id: str
    raw_name: Optional[str] = None
    raw_email: Optional[str] = None
    raw_handle: Optional[str] = None
    is_erased: bool
    erased_at: Optional[datetime] = None


class ContributorErasureResponse(BaseModel):
    """Result of GDPR Article 17 cryptographic PII erasure."""

    pseudonym_id: str
    erased: bool
    detail: str
