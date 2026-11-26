from datetime import datetime
import re
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AIProcessingMode = Literal["local_only", "cloud_zdr"]

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class TenantRegisterRequest(BaseModel):
    """Payload for registering a new organization tenant and bootstrapping administrator."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=2, max_length=255, description="Organization / Tenant name")
    slug: Optional[str] = Field(
        None,
        min_length=3,
        max_length=64,
        description="URL-friendly slug (lowercase alphanumeric and single hyphens)",
    )
    admin_email: str = Field(..., max_length=255, description="Administrator email address")
    admin_name: str = Field(
        ..., min_length=2, max_length=255, description="Administrator full name"
    )
    password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=128,
        description="Initial password for provisioning the administrator in Keycloak OIDC",
    )
    industry: Optional[str] = Field(
        None,
        max_length=255,
        description="Organization industry vertical selected during onboarding",
    )
    invites: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Optional team member emails to invite during registration",
    )
    allowed_domain: Optional[str] = Field(
        None,
        max_length=255,
        description="Restricted email domain for SSO, e.g. company.com",
    )

    @field_validator("admin_email")
    @classmethod
    def validate_admin_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_REGEX.match(v):
            raise ValueError("Invalid email address format")
        return v

    @field_validator("name", "admin_name")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be blank or whitespace-only")
        return v.strip()

    @field_validator("industry")
    @classmethod
    def normalize_industry(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None

    @field_validator("invites")
    @classmethod
    def validate_invites(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in v:
            addr = raw.strip().lower()
            if not addr:
                continue
            if not EMAIL_REGEX.match(addr):
                raise ValueError(f"Invalid invited email address format: {addr}")
            if addr not in cleaned:
                cleaned.append(addr)
        return cleaned

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip().lower()
            if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
                raise ValueError(
                    "Slug must contain only lowercase alphanumeric characters and non-consecutive hyphens"
                )
        return v

    def get_effective_slug(self) -> str:
        """Returns explicitly provided slug, or slugifies the organization name."""
        if self.slug:
            return self.slug.strip().lower()
        s = re.sub(r"[^\w\s-]", "", self.name.lower()).strip()
        s = re.sub(r"[-\s]+", "-", s).strip("-")
        if not s:
            s = f"org-{uuid.uuid4().hex[:8]}"
        return s[:64]


class TenantRegisterResponse(BaseModel):
    """Response returned upon successful tenant registration (HTTP 201)."""

    tenant_id: uuid.UUID
    admin_id: uuid.UUID
    slug: str
    name: str
    admin_email: str
    industry: Optional[str] = None
    invites_sent: int = 0
    message: str = "Tenant and administrator successfully registered"


class TenantSettingsUpdateRequest(BaseModel):
    """Payload for updating workspace settings and AI compliance mode (SCRUM-560 / US-62)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=2, max_length=255)
    allowed_domain: Optional[str] = Field(None, max_length=255)
    industry: Optional[str] = Field(None, max_length=255)
    ai_processing_mode: Optional[AIProcessingMode] = None
    retention_days: Optional[int] = Field(None, ge=7, le=3650)

    @field_validator("name")
    @classmethod
    def validate_name_non_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Organization name cannot be blank")
        return v.strip() if v is not None else None

    @field_validator("allowed_domain")
    @classmethod
    def normalize_allowed_domain(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip().lower().lstrip("@")
        return cleaned or None

    @field_validator("industry")
    @classmethod
    def normalize_industry_field(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None

    @model_validator(mode="after")
    def reject_explicit_null_required_fields(self) -> "TenantSettingsUpdateRequest":
        for required_field in ("name", "ai_processing_mode", "retention_days"):
            if required_field in self.model_fields_set and getattr(self, required_field) is None:
                raise ValueError(f"Field '{required_field}' cannot be null")
        return self


class TenantSettingsResponse(BaseModel):
    """Workspace settings and live ADR re-indexing state returned to Tenant Admins."""

    tenant_id: uuid.UUID
    slug: str
    name: str
    allowed_domain: Optional[str] = None
    industry: Optional[str] = None
    tier: str
    status: str
    max_projects: int
    ai_processing_mode: AIProcessingMode
    retention_days: int
    active_embedding_dimensions: int
    active_embedding_model: str
    active_llm_model: str
    reindexing: bool = False
    reindex_status: str = "idle"
    reindexed_count: int = 0
    reindex_total_count: int = 0
    reindex_job_id: Optional[str] = None
    reindex_error: Optional[str] = None
    updated_at: datetime
