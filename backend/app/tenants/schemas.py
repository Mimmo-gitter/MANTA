from datetime import datetime
import re
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AIProcessingMode = Literal["local_only", "cloud_zdr"]
ADRRedactionClass = Literal["EMAIL", "IP_ADDRESS", "API_TOKEN", "COMPANY_NAME"]

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*(?:\+|\*|\{\d+,?\d*\})[^)]*\)(?:\+|\*|\{\d+,?\d*\})")


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


class TenantADRDenyListEntry(BaseModel):
    """Typed tenant deny-list matching rule for ADR Presidio scrubbing (TASK-315-0)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pattern: str = Field(..., min_length=2, max_length=256)
    match_mode: Literal["literal", "regex"] = "literal"
    redaction_class: ADRRedactionClass = "COMPANY_NAME"
    case_sensitive: bool = False

    @field_validator("pattern")
    @classmethod
    def validate_pattern_non_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Deny-list pattern must be at least 2 non-whitespace characters")
        return cleaned

    @model_validator(mode="after")
    def validate_regex_safety(self) -> "TenantADRDenyListEntry":
        if self.match_mode == "regex":
            if _NESTED_QUANTIFIER_RE.search(self.pattern):
                raise ValueError("Unsafe nested quantifier detected in deny-list regex pattern")
            flags = 0 if self.case_sensitive else re.IGNORECASE
            try:
                compiled = re.compile(self.pattern, flags)
            except re.error as exc:
                raise ValueError(f"Invalid deny-list regex pattern: {exc}") from exc
            if compiled.match("") is not None:
                raise ValueError("Deny-list regex pattern must not match empty strings")
        return self


class TenantADRDenyListConfig(BaseModel):
    """Per-tenant ADR scrubbing deny-list configuration stored in tenants.adr_deny_list."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    auto_include_tenant_identity: bool = True
    terms: list[str] = Field(default_factory=list, max_length=500)
    entries: list[TenantADRDenyListEntry] = Field(default_factory=list, max_length=500)

    @field_validator("terms")
    @classmethod
    def normalize_terms(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in v:
            if not isinstance(raw, str):
                raise ValueError("Each deny-list term must be a string")
            term = raw.strip()
            if not term:
                continue
            if len(term) < 2 or len(term) > 256:
                raise ValueError("Each deny-list term must be between 2 and 256 characters")
            key = term.lower()
            if key not in seen:
                seen.add(key)
                cleaned.append(term)
        return cleaned


def normalize_tenant_deny_list(raw: Any) -> TenantADRDenyListConfig:
    """Normalize persisted JSONB/dict/list tenant deny-list payloads into TenantADRDenyListConfig."""
    if raw is None or raw == {}:
        return TenantADRDenyListConfig()
    if isinstance(raw, TenantADRDenyListConfig):
        return raw
    if isinstance(raw, list):
        return TenantADRDenyListConfig(terms=[str(item) for item in raw])
    if isinstance(raw, dict):
        return TenantADRDenyListConfig.model_validate(raw)
    raise ValueError(f"Unsupported adr_deny_list payload type: {type(raw).__name__}")


class TenantSettingsUpdateRequest(BaseModel):
    """Payload for updating workspace settings and AI compliance mode (SCRUM-560 / US-62)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=2, max_length=255)
    allowed_domain: Optional[str] = Field(None, max_length=255)
    industry: Optional[str] = Field(None, max_length=255)
    ai_processing_mode: Optional[AIProcessingMode] = None
    retention_days: Optional[int] = Field(None, ge=7, le=3650)
    adr_deny_list: Optional[TenantADRDenyListConfig] = None

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
        for required_field in ("name", "ai_processing_mode", "retention_days", "adr_deny_list"):
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
    adr_deny_list: TenantADRDenyListConfig = Field(default_factory=TenantADRDenyListConfig)
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
