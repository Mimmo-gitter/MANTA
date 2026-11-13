from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.uuidv7 import generate_uuidv7


class Base(DeclarativeBase):
    pass


# ============================================================================
# DOMAIN 1: TENANTS, USERS, IDENTITIES & SESSIONS (OIDC / OAuth / Multi-IdP)
# ============================================================================


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_domain: Mapped[Optional[str]] = mapped_column(String(255))
    tier: Mapped[str] = mapped_column(String(32), default="starter", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    max_projects: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    ai_processing_mode: Mapped[str] = mapped_column(String(32), default="cloud_zdr", nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    users: Mapped[List["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    projects: Mapped[List["Project"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        Index("idx_users_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "email", name="uq_tenant_user_email"),
        UniqueConstraint("tenant_id", "google_sub", name="uq_tenant_google_sub"),
        Index("idx_users_tenant_role", "tenant_id", "role"),
        Index("idx_users_github_handle", "tenant_id", "github_handle"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    google_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    github_handle: Mapped[Optional[str]] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(50), default="DEVELOPER", nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    identities: Mapped[List["UserIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[List["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    user_roles: Mapped[List["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserIdentity(Base):
    __tablename__ = "user_identities"

    __table_args__ = (
        Index("idx_user_identities_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "issuer_subject", name="uq_tenant_google_identity"),
        Index("idx_user_identities_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="google", nullable=False)  # google
    issuer_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email_at_provider: Mapped[Optional[str]] = mapped_column(String(255))
    claims: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="identities")


class UserSession(Base):
    __tablename__ = "user_sessions"

    __table_args__ = (
        Index("idx_user_sessions_tenant_id", "tenant_id"),
        Index("idx_user_sessions_lookup", "tenant_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(255))
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sessions")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[Optional[str]] = mapped_column(Text)


class UserRole(Base):
    __tablename__ = "user_roles"

    __table_args__ = (Index("idx_user_roles_tenant_id", "tenant_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(String(32), ForeignKey("roles.id"), primary_key=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="user_roles")


class Invitation(Base):
    __tablename__ = "invitations"

    __table_args__ = (
        Index("idx_invitations_tenant_id", "tenant_id"),
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), ForeignKey("roles.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 2: PROJECTS & INTEGRATIONS
# ============================================================================


class Project(Base):
    __tablename__ = "projects"

    __table_args__ = (
        Index("idx_projects_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "slug", name="uq_projects_tenant_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    tier_sla_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    authoritative_cd_signal: Mapped[str] = mapped_column(
        String(64), default="github_deployments_api", nullable=False
    )
    lead_time_first_commit_strategy: Mapped[str] = mapped_column(
        String(32), default="feature_branch_root", nullable=False
    )
    wip_limit_per_dev: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    pr_stale_threshold_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="projects")
    repositories: Mapped[List["Repository"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    deployments: Mapped[List["Deployment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def authoritative_deployment_signal(self) -> str:
        return self.authoritative_cd_signal

    @authoritative_deployment_signal.setter
    def authoritative_deployment_signal(self, value: str) -> None:
        self.authoritative_cd_signal = value


class ProjectMember(Base):
    __tablename__ = "project_members"

    __table_args__ = (
        Index("idx_project_members_tenant_id", "tenant_id"),
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_role: Mapped[str] = mapped_column(String(32), default="contributor", nullable=False)
    allocated_fte: Mapped[float] = mapped_column(Numeric(3, 2), default=1.00, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class Integration(Base):
    __tablename__ = "integrations"

    __table_args__ = (
        Index("idx_integrations_tenant_id", "tenant_id"),
        UniqueConstraint(
            "tenant_id", "provider", "external_account_id", name="uq_tenant_provider_acc"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    api_credentials_vault_ref: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ProjectLink(Base):
    __tablename__ = "project_links"

    __table_args__ = (
        Index("idx_project_links_tenant_id", "tenant_id"),
        UniqueConstraint("project_id", "link_type", "external_id", name="uq_project_link"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 3: WEBHOOKS, LEASES & DEAD LETTER
# ============================================================================


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    __table_args__ = (
        Index("idx_webhook_events_tenant_id", "tenant_id"),
        Index(
            "idx_webhook_events_staged_received",
            "received_at",
            postgresql_where=text("status = 'staged'"),
        ),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_delivery_id",
            name="uq_webhook_provider_delivery",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_delivery_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="staged", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class DeadLetterEvent(Base):
    __tablename__ = "dead_letter_events"

    __table_args__ = (
        Index("idx_dead_letter_events_tenant_id", "tenant_id"),
        Index(
            "idx_dead_letter_events_unresolved",
            "tenant_id",
            "is_resolved",
            text("quarantined_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    original_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_events.id"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    error_class: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[str] = mapped_column(Text, nullable=False)
    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    quarantined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class JobLease(Base):
    __tablename__ = "job_leases"

    __table_args__ = (Index("idx_job_leases_tenant_id", "tenant_id"),)

    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    owner_token: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    claimed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 4: GIT, COMMITS & PULL REQUESTS
# ============================================================================


class Contributor(Base):
    __tablename__ = "contributors"

    __table_args__ = (
        Index("idx_contributors_tenant_id", "tenant_id"),
        Index("idx_contributors_pseudonym", "pseudonym_id"),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_actor_id",
            name="uq_contributors_tenant_provider_actor",
        ),
    )

    pseudonym_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_name: Mapped[Optional[str]] = mapped_column(Text)
    raw_email: Mapped[Optional[str]] = mapped_column(Text)
    raw_handle: Mapped[Optional[str]] = mapped_column(String(255))
    salt_secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_erased: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    erased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Repository(Base):
    __tablename__ = "repositories"

    __table_args__ = (
        Index("idx_repositories_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "external_repo_id", name="uq_repo_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    external_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(128), default="main", nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="repositories")
    commits: Mapped[List["Commit"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    pull_requests: Mapped[List["PullRequest"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class Commit(Base):
    __tablename__ = "commits"

    __table_args__ = (
        Index("idx_commits_tenant_id", "tenant_id"),
        Index("idx_commits_pull_request_id", "tenant_id", "pull_request_id"),
    )

    sha: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    author_pseudonym_id: Mapped[str] = mapped_column(String(64), nullable=False)
    committer_pseudonym_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    lines_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lines_deleted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parent_shas: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)
    is_root_of_branch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pull_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    active_fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    repository: Mapped["Repository"] = relationship(back_populates="commits")


class PullRequest(Base):
    __tablename__ = "pull_requests"

    __table_args__ = (
        Index("idx_pull_requests_tenant_id", "tenant_id"),
        Index("idx_pull_requests_merge_commit", "tenant_id", "merge_commit_sha"),
        UniqueConstraint("repository_id", "external_pr_number", name="uq_repo_pr_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    author_pseudonym_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    merge_commit_sha: Mapped[Optional[str]] = mapped_column(String(40))
    first_commit_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_in_git_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    entity_version: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    active_fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_cycles_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    repository: Mapped["Repository"] = relationship(back_populates="pull_requests")
    reviews: Mapped[List["PullRequestReview"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class PullRequestReview(Base):
    __tablename__ = "pull_request_reviews"

    __table_args__ = (Index("idx_pull_request_reviews_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    reviewer_pseudonym_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    review_cycle_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    pull_request: Mapped["PullRequest"] = relationship(back_populates="reviews")


# ============================================================================
# DOMAIN 5: JIRA SPRINTS, ISSUES & ACCEPTANCE CRITERIA VOLATILITY
# ============================================================================


class Sprint(Base):
    __tablename__ = "sprints"

    __table_args__ = (
        Index("idx_sprints_tenant_id", "tenant_id"),
        UniqueConstraint("project_id", "external_sprint_id", name="uq_project_sprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    external_sprint_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    complete_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    goal: Mapped[Optional[str]] = mapped_column(Text)
    active_fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class Issue(Base):
    __tablename__ = "issues"

    __table_args__ = (
        Index("idx_issues_tenant_id", "tenant_id"),
        Index("idx_issues_tenant_sprint_status", "tenant_id", "sprint_id", "status"),
        Index("idx_issues_changelog_precedence", "tenant_id", "external_key", "changelog_id"),
        Index("idx_issues_resolved_at", "tenant_id", "resolved_at"),
        UniqueConstraint("tenant_id", "external_key", name="uq_tenant_issue_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    sprint_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sprints.id", ondelete="SET NULL")
    )
    parent_issue_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issues.id", ondelete="SET NULL")
    )
    assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    external_key: Mapped[str] = mapped_column(String(64), nullable=False)
    external_issue_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    issue_type: Mapped[str] = mapped_column(String(32), default="Story", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    story_points: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    assignee_pseudonym_id: Mapped[Optional[str]] = mapped_column(String(64))
    issue_links: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    changelog_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    active_fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    acceptance_criteria: Mapped[Optional[str]] = mapped_column(Text)
    acceptance_criteria_hash: Mapped[Optional[str]] = mapped_column(String(64))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    entity_version: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    deployment_links: Mapped[List["DeploymentIncident"]] = relationship(
        back_populates="issue", cascade="all, delete-orphan"
    )


class IssueTransition(Base):
    __tablename__ = "issue_transitions"

    __table_args__ = (Index("idx_issue_transitions_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(64), nullable=False)
    to_status: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class WIPLimit(Base):
    __tablename__ = "wip_limits"

    __table_args__ = (
        Index("idx_wip_limits_tenant_id", "tenant_id"),
        UniqueConstraint("project_id", "status", name="uq_wip_project_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    max_items: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class AcceptanceCriteriaVersion(Base):
    __tablename__ = "acceptance_criteria_versions"

    __table_args__ = (
        Index("idx_acceptance_criteria_versions_tenant_id", "tenant_id"),
        UniqueConstraint("issue_id", "version", name="uq_issue_ac_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    raw_criteria_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lexical_tokens_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class RequirementChange(Base):
    __tablename__ = "requirement_changes"

    __table_args__ = (Index("idx_requirement_changes_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    from_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("acceptance_criteria_versions.id", ondelete="SET NULL")
    )
    to_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("acceptance_criteria_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    risk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("risks.id", ondelete="SET NULL")
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    scope_expansion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    volatility_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), default=0.0)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 6: COMMUNICATIONS, RISKS & NOTIFICATIONS
# ============================================================================


class CommunicationEvent(Base):
    __tablename__ = "communication_events"

    __table_args__ = (
        Index("idx_communication_events_tenant_id", "tenant_id"),
        UniqueConstraint("project_id", "channel_id", "thread_ts", name="uq_communication_thread"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), default="blocker_thread", nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    response_delay_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class Risk(Base):
    __tablename__ = "risks"

    __table_args__ = (Index("idx_risks_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"

    __table_args__ = (Index("idx_notifications_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unread", nullable=False)
    payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 7: CI/CD DEPLOYMENTS, INCIDENTS & DORA
# ============================================================================


class Deployment(Base):
    __tablename__ = "deployments"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "actor_pseudonym_id"],
            ["contributors.tenant_id", "contributors.pseudonym_id"],
            name="fk_deployments_tenant_actor_pseudonym",
        ),
        CheckConstraint(
            "status IN ('SUCCESS', 'FAILED', 'ROLLED_BACK')",
            name="ck_deployments_status",
        ),
        CheckConstraint(
            "authoritative_signal_source IN ('github_deployments_api', 'argocd_postsync_webhook', 'direct_deployment_api')",
            name="ck_deployments_signal_source",
        ),
        Index("idx_deployments_tenant_id", "tenant_id"),
        Index(
            "idx_deployments_project_env_deployed_at",
            "project_id",
            "environment",
            text("deployed_at DESC"),
        ),
        Index(
            "uq_deployments_tenant_proj_source_ext_id",
            "tenant_id",
            "project_id",
            "authoritative_signal_source",
            "external_deployment_id",
            unique=True,
            postgresql_where=text("external_deployment_id IS NOT NULL"),
        ),
        Index("idx_deployments_tenant_commit_sha", "tenant_id", "commit_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE")
    )
    integration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    triggered_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_pseudonym_id: Mapped[Optional[str]] = mapped_column(String(64))
    external_deployment_id: Mapped[Optional[str]] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_change_failure: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    authoritative_signal_source: Mapped[str] = mapped_column(
        String(64), default="github_deployments_api", nullable=False
    )
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lead_time_seconds: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="deployments")
    incident_links: Mapped[List["DeploymentIncident"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )


class DeploymentPullRequest(Base):
    __tablename__ = "deployment_pull_requests"

    __table_args__ = (
        Index("idx_deployment_pull_requests_tenant_id", "tenant_id"),
        UniqueConstraint("deployment_id", "pull_request_id", name="uq_deployment_pr"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class Incident(Base):
    __tablename__ = "incidents"

    __table_args__ = (Index("idx_incidents_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recovery_time_seconds: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    severity: Mapped[str] = mapped_column(String(16), default="SEV-2", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class DeploymentIncident(Base):
    __tablename__ = "deployment_incidents"

    __table_args__ = (
        Index("idx_deployment_incidents_tenant_id", "tenant_id"),
        Index("idx_deployment_incidents_deployment_id", "deployment_id"),
        Index("idx_deployment_incidents_issue_id", "issue_id"),
        Index("idx_deployment_incidents_window", "tenant_id", "detected_at"),
        UniqueConstraint(
            "deployment_id", "issue_id", name="uq_deployment_incidents_deployment_issue"
        ),
        CheckConstraint(
            "status IN ('CONFIRMED', 'CANCELLED', 'FALSE_POSITIVE')",
            name="ck_deployment_incidents_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED", nullable=False)
    correlation_source: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recovery_time_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    deployment: Mapped["Deployment"] = relationship(back_populates="incident_links")
    issue: Mapped["Issue"] = relationship(back_populates="deployment_links")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    __table_args__ = (
        Index("idx_metric_snapshots_tenant_id", "tenant_id"),
        UniqueConstraint(
            "project_id", "metric", "granularity", "period_start", name="uq_metric_snapshot"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    granularity: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 8: ARCHITECTURAL DECISIONS (ADR) & PARTITIONED REPOSITORY
# ============================================================================


class AnonymizedADR(Base):
    __tablename__ = "anonymized_adrs"

    __table_args__ = (Index("idx_anonymized_adrs_tenant_id", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    decision_summary: Mapped[str] = mapped_column(Text, nullable=False)
    architecture_pattern: Mapped[Optional[str]] = mapped_column(String(128))
    visibility: Mapped[str] = mapped_column(String(32), default="TENANT_PRIVATE", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", nullable=False)
    sanitization_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class ADRSanitizationLog(Base):
    __tablename__ = "adr_sanitization_logs"

    __table_args__ = (Index("idx_adr_sanitization_logs_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    adr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_input_snippet: Mapped[Optional[str]] = mapped_column(Text)
    presidio_entities_scrubbed: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    llm_abstraction_prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    solution_architect_diff_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 9: STATIC ANALYSIS, REFACTORING & TEST RESULTS
# ============================================================================


class CodeAnalysis(Base):
    __tablename__ = "code_analyses"

    __table_args__ = (Index("idx_code_analyses_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    tech_debt_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class ScipIndex(Base):
    __tablename__ = "scip_indexes"

    __table_args__ = (
        Index("idx_scip_indexes_tenant_id", "tenant_id"),
        UniqueConstraint("repository_id", "commit_sha", name="uq_repo_commit_scip"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    scip_protobuf_storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    symbols_count: Mapped[int] = mapped_column(Integer, nullable=False)
    indexer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class RefactoringSuggestion(Base):
    __tablename__ = "refactoring_suggestions"

    __table_args__ = (Index("idx_refactoring_suggestions_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("code_analyses.id", ondelete="SET NULL")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    pull_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED", nullable=False)
    proposed_diff: Mapped[str] = mapped_column(Text, nullable=False)
    generated_unit_tests: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class TestRun(Base):
    __tablename__ = "test_runs"

    __table_args__ = (Index("idx_test_runs_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    suggestion_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refactoring_suggestions.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), default="unit", nullable=False)
    run_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class TestResult(Base):
    __tablename__ = "test_results"

    __table_args__ = (Index("idx_test_results_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    test_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    failure_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class FlakyTest(Base):
    __tablename__ = "flaky_tests"

    __table_args__ = (
        Index("idx_flaky_tests_tenant_id", "tenant_id"),
        UniqueConstraint("repository_id", "test_name", name="uq_repo_flaky_test"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    flake_rate: Mapped[float] = mapped_column(Numeric(5, 4), default=0.0, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ============================================================================
# DOMAIN 10: OUTBOX, GDPR & AUDIT LOGS
# ============================================================================


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    __table_args__ = (
        Index("idx_outbox_events_tenant_id", "tenant_id"),
        Index(
            "ix_outbox_events_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index(
            "ix_outbox_events_unpublished_id",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index("ix_outbox_events_tenant_published", "tenant_id", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=generate_uuidv7
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProcessedOutboxEvent(Base):
    __tablename__ = "processed_outbox_events"

    __table_args__ = (
        Index("idx_processed_outbox_events_tenant_id", "tenant_id"),
        Index("idx_processed_outbox_events_processed_at", "processed_at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
        nullable=False,
    )


class ContributorPseudonymSalt(Base):
    __tablename__ = "contributor_pseudonym_salts"

    __table_args__ = (Index("idx_contributor_pseudonym_salts_tenant_id", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    salt_vault_ref: Mapped[str] = mapped_column(Text, nullable=False)
    pseudonym_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_erased: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    erased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    __table_args__ = (Index("idx_audit_log_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    payload_before: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    payload_after: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
