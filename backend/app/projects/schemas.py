from datetime import datetime
from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeploymentSignalType(str, Enum):
    """Allowed authoritative production deployment signal sources (US-18 / SCRUM-383)."""

    GITHUB_DEPLOYMENTS_API = "github_deployments_api"
    ARGOCD_POSTSYNC_WEBHOOK = "argocd_postsync_webhook"
    DIRECT_DEPLOYMENT_API = "direct_deployment_api"


class DeploymentSignalUpdateRequest(BaseModel):
    """Payload to update a project's authoritative production deployment signal."""

    authoritative_deployment_signal: Optional[DeploymentSignalType] = Field(
        None,
        description=(
            "Authoritative production deployment signal source. Must be one of: "
            "'github_deployments_api', 'argocd_postsync_webhook', 'direct_deployment_api'."
        ),
    )
    authoritative_cd_signal: Optional[DeploymentSignalType] = Field(
        None,
        description="Alias for authoritative_deployment_signal.",
    )

    @model_validator(mode="after")
    def validate_signal_present(self) -> "DeploymentSignalUpdateRequest":
        signal = self.authoritative_deployment_signal or self.authoritative_cd_signal
        if not signal:
            raise ValueError(
                "Field 'authoritative_deployment_signal' (or 'authoritative_cd_signal') is required."
            )
        self.authoritative_deployment_signal = signal
        return self


class DeploymentSignalResponse(BaseModel):
    """Response model for project deployment signal configuration."""

    project_id: uuid.UUID
    tenant_id: uuid.UUID
    authoritative_deployment_signal: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectCreateRequest(BaseModel):
    """Payload to create a new project under the authenticated tenant."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable project name")
    slug: Optional[str] = Field(
        None,
        min_length=1,
        max_length=64,
        description="URL-friendly unique slug. If omitted, auto-generated from name.",
    )
    tier_sla_hours: int = Field(default=48, ge=1, le=720, description="Target SLA in hours")
    authoritative_cd_signal: Optional[DeploymentSignalType] = Field(
        None,
        description="Authoritative deployment signal source",
    )
    authoritative_deployment_signal: Optional[DeploymentSignalType] = Field(
        None,
        description="Authoritative deployment signal source",
    )
    lead_time_first_commit_strategy: str = Field(
        default="feature_branch_root",
        max_length=32,
        description="First-commit strategy for lead-time calculation",
    )
    wip_limit_per_dev: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Default work-in-progress limit per developer",
    )
    pr_stale_threshold_hours: int = Field(
        default=48,
        ge=1,
        le=720,
        description="Threshold hours after which a PR is marked stale",
    )

    @model_validator(mode="after")
    def resolve_signal(self) -> "ProjectCreateRequest":
        sig = self.authoritative_deployment_signal or self.authoritative_cd_signal
        if sig:
            self.authoritative_cd_signal = sig
            self.authoritative_deployment_signal = sig
        else:
            self.authoritative_cd_signal = DeploymentSignalType.GITHUB_DEPLOYMENTS_API
            self.authoritative_deployment_signal = DeploymentSignalType.GITHUB_DEPLOYMENTS_API
        return self


class RepositoryLinkRequest(BaseModel):
    """Payload to link a GitHub repository to a project."""

    external_repo_id: int = Field(
        ...,
        description="Unique GitHub repository ID (numeric)",
    )
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Repository full name, e.g. 'organization/repository'",
    )
    default_branch: str = Field(
        default="main",
        min_length=1,
        max_length=128,
        description="Default repository branch",
    )
    is_private: bool = Field(
        default=True,
        description="Whether the repository is private",
    )
    integration_id: Optional[uuid.UUID] = Field(
        None,
        description="Explicit GitHub integration ID. If omitted, the tenant's active GitHub integration is used.",
    )


class RepositoryResponse(BaseModel):
    """Detailed information about a linked repository."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    integration_id: Optional[uuid.UUID] = None
    external_repo_id: int
    full_name: str
    default_branch: str
    is_private: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectResponse(BaseModel):
    """Detailed project response including linked repositories."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    tier_sla_hours: int
    authoritative_cd_signal: str
    authoritative_deployment_signal: Optional[str] = None
    lead_time_first_commit_strategy: str
    wip_limit_per_dev: int
    pr_stale_threshold_hours: int
    is_active: bool
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    repositories: list[RepositoryResponse] = []

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def sync_deployment_signal(self) -> "ProjectResponse":
        if not self.authoritative_deployment_signal:
            self.authoritative_deployment_signal = self.authoritative_cd_signal
        return self
