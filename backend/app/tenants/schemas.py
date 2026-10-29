import re
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    message: str = "Tenant and administrator successfully registered"
