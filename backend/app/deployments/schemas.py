"""Pydantic schemas and canonical enums for production deployment ingestion (US-25)."""

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any, Optional, Union
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMMIT_SHA_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")


class DeploymentOutcome(StrEnum):
    """Canonical deployment outcomes enforced by deployments.ck_deployments_status."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class DeploymentSignalSource(StrEnum):
    """Canonical CD signal channels enforced by deployments.ck_deployments_signal_source."""

    GITHUB_DEPLOYMENTS_API = "github_deployments_api"
    ARGOCD_POSTSYNC_WEBHOOK = "argocd_postsync_webhook"
    DIRECT_DEPLOYMENT_API = "direct_deployment_api"


class DeploymentActorInput(BaseModel):
    """Normalized deployer identity prior to Task 20.3 pseudonymization."""

    model_config = ConfigDict(str_strip_whitespace=True)

    external_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    email: Optional[str] = Field(default=None, min_length=3, max_length=320)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    handle: Optional[str] = Field(default=None, min_length=1, max_length=255)
    provider: Optional[str] = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def _ensure_at_least_one_identifier(self) -> "DeploymentActorInput":
        if not any((self.external_id, self.email, self.handle, self.name)):
            raise ValueError(
                "Actor object must provide at least one of external_id, email, handle, or name"
            )
        return self

    @classmethod
    def from_raw(
        cls,
        raw: Union[str, "DeploymentActorInput", dict[str, Any], None],
        *,
        default_provider: str,
    ) -> Optional["DeploymentActorInput"]:
        """Coerce either a string identifier or structured actor object cleanly."""
        if raw is None:
            return None
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            cleaned = raw.strip()
            if not cleaned:
                raise ValueError("Actor string must not be empty")
            if "@" in cleaned:
                return cls(
                    external_id=cleaned.lower()[:128],
                    email=cleaned.lower(),
                    provider=default_provider,
                )
            return cls(
                external_id=cleaned[:128],
                handle=cleaned[:255],
                provider=default_provider,
            )
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        raise ValueError("Unsupported actor format")

    def to_contributor_params(
        self, default_provider: str
    ) -> tuple[str, str, Optional[str], Optional[str], Optional[str]]:
        """Return arguments expected by Task 20.3 resolve_or_create_contributor_pseudonym."""
        resolved_provider = (self.provider or default_provider).strip().lower()
        resolved_actor_id = (
            self.external_id
            or (self.email.lower() if self.email else None)
            or self.handle
            or self.name
            or ""
        )[:128]
        return (
            resolved_provider,
            resolved_actor_id,
            self.email.lower() if self.email else None,
            self.name,
            self.handle,
        )


class NormalizedDeploymentPayload(BaseModel):
    """Internal normalized deployment representation across all 3 CD channels."""

    commit_sha: str
    environment: str
    is_production: bool
    outcome: DeploymentOutcome
    is_change_failure: bool
    deployed_at: datetime
    authoritative_signal_source: DeploymentSignalSource
    external_deployment_id: Optional[str] = None
    actor: Optional[DeploymentActorInput] = None


class DirectDeploymentIngestRequest(BaseModel):
    """Request payload for POST /api/v1/projects/{project_id}/deployments (Task 25.4)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    commit_sha: str = Field(..., min_length=40, max_length=40)
    environment: str = Field(..., min_length=1, max_length=64)
    outcome: DeploymentOutcome
    deployed_at: datetime
    actor: Optional[Union[DeploymentActorInput, str]] = None
    external_deployment_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    repository_id: Optional[uuid.UUID] = None

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not COMMIT_SHA_REGEX.match(cleaned):
            raise ValueError("commit_sha must be a valid 40-character hexadecimal Git SHA")
        return cleaned

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("environment must not be blank")
        return value.strip()

    @field_validator("deployed_at")
    @classmethod
    def _ensure_utc_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deployed_at must include an explicit timezone offset or 'Z'")
        return value.astimezone(timezone.utc)

    @field_validator("actor")
    @classmethod
    def _validate_actor(
        cls, value: Optional[Union[DeploymentActorInput, str]]
    ) -> Optional[DeploymentActorInput]:
        return DeploymentActorInput.from_raw(
            value,
            default_provider=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
        )


class DirectDeploymentResponse(BaseModel):
    """Privacy-safe response DTO (never returns raw actor PII)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    repository_id: Optional[uuid.UUID] = None
    commit_sha: str
    environment: str
    is_production: bool
    status: DeploymentOutcome
    is_change_failure: bool
    authoritative_signal_source: DeploymentSignalSource
    is_authoritative_for_project: bool
    actor_pseudonym_id: Optional[str] = None
    external_deployment_id: Optional[str] = None
    deployed_at: datetime
    created_at: datetime
    deduplicated: bool = False
