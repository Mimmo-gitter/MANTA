from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.projects.validators import normalize_and_validate_github_repo


class HealthSignalStatus(str, Enum):
    """Canonical health signal outcomes ingested by US-68."""

    FAIL = "FAIL"
    PASS = "PASS"


class FailureSessionStatus(str, Enum):
    """Lifecycle states of a production failure session (Task 68.1 / 68.4)."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ProjectHealthStatus(str, Enum):
    """Aggregate live production health state of a project (Task 68.1)."""

    HEALTHY = "HEALTHY"
    FAILING = "FAILING"


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
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional project summary / description",
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
    health_probe_enabled: bool = Field(
        default=True,
        description="Whether production health signal webhook ingestion is enabled",
    )
    health_probe_source: str = Field(
        default="webhook",
        min_length=1,
        max_length=64,
        description="Configured health probe source identifier (e.g. webhook, prometheus, datadog)",
    )
    health_webhook_secret: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=512,
        description="Optional custom HMAC secret for project health webhooks; auto-generated if omitted",
    )
    health_probe_url: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Optional external probe target URL monitored for this project",
    )
    health_probe_interval_seconds: int = Field(
        default=60,
        ge=5,
        le=86400,
        description="Configured health check cadence in seconds",
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
    """Payload to link a GitHub repository to a project (US-17 / SCRUM-378)."""

    external_repo_id: Optional[int] = Field(
        None,
        description="Unique GitHub repository ID (numeric). Auto-derived if omitted.",
    )
    full_name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Repository full name, e.g. 'organization/repository' or URL",
    )
    repository_url: Optional[str] = Field(
        None,
        description="GitHub repository URL (e.g. 'https://github.com/owner/repo') or 'owner/repo'",
    )
    github_url: Optional[str] = Field(
        None,
        description="Alias for repository_url",
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

    @model_validator(mode="after")
    def validate_and_normalize_identifier(self) -> "RepositoryLinkRequest":
        raw_target = self.repository_url or self.github_url or self.full_name
        if not raw_target:
            raise ValueError("Either 'full_name' or 'repository_url' must be provided.")
        canonical_name, _, _ = normalize_and_validate_github_repo(raw_target)
        self.full_name = canonical_name
        if not self.repository_url:
            self.repository_url = f"https://github.com/{canonical_name}"

        if self.external_repo_id is None:
            digest = hashlib.sha256(canonical_name.encode("utf-8")).digest()
            self.external_repo_id = int.from_bytes(digest[:8], byteorder="big") & 0x7FFFFFFFFFFFFFFF
        return self


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


class ProjectUpdateRequest(BaseModel):
    """Payload to update project metadata or engineering thresholds."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=2000)
    tier_sla_hours: Optional[int] = Field(None, ge=1, le=720)
    wip_limit_per_dev: Optional[int] = Field(None, ge=1, le=50)
    pr_stale_threshold_hours: Optional[int] = Field(None, ge=1, le=720)
    lead_time_first_commit_strategy: Optional[str] = Field(None, max_length=32)
    health_probe_enabled: Optional[bool] = None
    health_probe_source: Optional[str] = Field(None, min_length=1, max_length=64)
    health_webhook_secret: Optional[str] = Field(None, min_length=8, max_length=512)
    health_probe_url: Optional[str] = Field(None, max_length=1024)
    health_probe_interval_seconds: Optional[int] = Field(None, ge=5, le=86400)
    is_active: Optional[bool] = None


class ProjectIncidentResponse(BaseModel):
    """Production incident summary linked to a project."""

    id: uuid.UUID
    project_id: uuid.UUID
    deployment_id: Optional[uuid.UUID] = None
    severity: str
    started_at: datetime
    resolved_at: Optional[datetime] = None
    recovery_time_seconds: Optional[float] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthSignalIngestRequest(BaseModel):
    """Incoming production health probe signal payload (US-68 / Task 68.3 & 68.4)."""

    model_config = ConfigDict(extra="ignore")

    status: HealthSignalStatus
    signal_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    probe_source: str = Field(default="webhook", min_length=1, max_length=64)
    severity: str = Field(default="SEV-1", min_length=1, max_length=16)
    deployment_id: Optional[uuid.UUID] = None
    tenant_id: Optional[uuid.UUID] = None
    message: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def normalize_incoming_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)

        raw_status = (
            normalized.get("status")
            or normalized.get("signal_status")
            or normalized.get("health_status")
            or normalized.get("state")
            or normalized.get("outcome")
            or normalized.get("probe_status")
        )
        if isinstance(raw_status, str):
            token = raw_status.strip().upper()
            if token in {
                "FAIL",
                "FAILING",
                "FAILED",
                "FAILURE",
                "UNHEALTHY",
                "DOWN",
                "ERROR",
                "CRITICAL",
                "ALERT",
                "FIRING",
                "RED",
            }:
                normalized["status"] = HealthSignalStatus.FAIL.value
            elif token in {
                "PASS",
                "PASSING",
                "PASSED",
                "HEALTHY",
                "UP",
                "OK",
                "RECOVERED",
                "RESOLVED",
                "GREEN",
            }:
                normalized["status"] = HealthSignalStatus.PASS.value

        raw_ts = (
            normalized.get("signal_timestamp")
            if normalized.get("signal_timestamp") is not None
            else (
                normalized.get("timestamp")
                if normalized.get("timestamp") is not None
                else (
                    normalized.get("probe_timestamp")
                    if normalized.get("probe_timestamp") is not None
                    else (
                        normalized.get("occurred_at")
                        if normalized.get("occurred_at") is not None
                        else (
                            normalized.get("checked_at")
                            if normalized.get("checked_at") is not None
                            else (
                                normalized.get("event_time")
                                if normalized.get("event_time") is not None
                                else (
                                    normalized.get("detected_at")
                                    if normalized.get("detected_at") is not None
                                    else (
                                        normalized.get("resolved_at")
                                        if normalized.get("resolved_at") is not None
                                        else normalized.get("started_at")
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
        if raw_ts is not None:
            normalized["signal_timestamp"] = raw_ts

        raw_source = (
            normalized.get("probe_source")
            or normalized.get("source")
            or normalized.get("monitor")
            or normalized.get("provider")
        )
        if isinstance(raw_source, str) and raw_source.strip():
            normalized["probe_source"] = raw_source.strip()[:64]

        return normalized

    @model_validator(mode="after")
    def ensure_utc_timestamp(self) -> "HealthSignalIngestRequest":
        if self.signal_timestamp.tzinfo is None:
            self.signal_timestamp = self.signal_timestamp.replace(tzinfo=timezone.utc)
        else:
            self.signal_timestamp = self.signal_timestamp.astimezone(timezone.utc)
        self.probe_source = (self.probe_source or "webhook").strip()[:64] or "webhook"
        self.severity = (self.severity or "SEV-1").strip().upper()[:16] or "SEV-1"
        return self


class HealthSignalIngestResponse(BaseModel):
    """Deterministic state machine result returned on health signal intake."""

    status: str = "accepted"
    project_id: uuid.UUID
    tenant_id: uuid.UUID
    signal_status: HealthSignalStatus
    project_health_status: ProjectHealthStatus
    action: Literal["opened", "duplicate_fail_ignored", "resolved", "orphan_pass_ignored"]
    noop: bool
    duplicate: bool = False
    orphan: bool = False
    session_id: Optional[uuid.UUID] = None
    incident_id: Optional[uuid.UUID] = None
    session_status: Optional[FailureSessionStatus] = None
    probe_source: Optional[str] = None
    started_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    recovery_time_seconds: Optional[float] = None
    message: str

    model_config = ConfigDict(from_attributes=True)


class ProductionFailureResponse(BaseModel):
    """Serialized production_failures session record."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    incident_id: Optional[uuid.UUID] = None
    status: str
    probe_source: str
    severity: str
    started_at: datetime
    resolved_at: Optional[datetime] = None
    recovery_time_seconds: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthProbeConfigResponse(BaseModel):
    """Project health probe webhook configuration & status response (Task 68.2 / 68.3)."""

    project_id: uuid.UUID
    tenant_id: uuid.UUID
    health_probe_enabled: bool
    health_probe_source: str
    health_probe_url: Optional[str] = None
    health_probe_interval_seconds: int
    health_status: str
    webhook_url: str
    project_health_signals_url: str
    webhook_secret_masked: str
    webhook_secret: Optional[str] = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthProbeConfigUpdateRequest(BaseModel):
    """Payload to update project health probe webhook settings."""

    model_config = ConfigDict(extra="forbid")

    health_probe_enabled: Optional[bool] = None
    health_probe_source: Optional[str] = Field(default=None, min_length=1, max_length=64)
    health_probe_url: Optional[str] = Field(default=None, max_length=1024)
    health_probe_interval_seconds: Optional[int] = Field(default=None, ge=5, le=86400)
    health_webhook_secret: Optional[str] = Field(default=None, min_length=8, max_length=512)


class ProjectResponse(BaseModel):
    """Detailed project response including linked repositories."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    description: Optional[str] = None
    tier_sla_hours: int
    authoritative_cd_signal: str
    authoritative_deployment_signal: Optional[str] = None
    lead_time_first_commit_strategy: str
    wip_limit_per_dev: int
    pr_stale_threshold_hours: int
    health_probe_enabled: bool = True
    health_probe_source: str = "webhook"
    health_probe_url: Optional[str] = None
    health_probe_interval_seconds: int = 60
    health_status: str = "HEALTHY"
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
