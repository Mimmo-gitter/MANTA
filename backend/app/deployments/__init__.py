"""Deployment ingestion domain package (US-25 Tasks 25.1, 25.3, 25.4)."""

from app.deployments.normalization import (
    PRODUCTION_ENV_TOKENS,
    NormalizationResult,
    extract_github_owner_repo,
    is_production_environment,
    normalize_argocd_postsync,
    normalize_commit_sha,
    normalize_environment,
    normalize_github_deployment_status,
)
from app.deployments.router import router as deployments_router
from app.deployments.schemas import (
    DeploymentActorInput,
    DeploymentOutcome,
    DeploymentSignalSource,
    DirectDeploymentIngestRequest,
    DirectDeploymentResponse,
    NormalizedDeploymentPayload,
)
from app.deployments.service import (
    build_authoritative_production_deployments_query,
    ingest_direct_deployment,
    list_authoritative_production_deployments,
    persist_deployment_idempotent,
    resolve_argocd_project,
    resolve_github_project,
)

__all__ = [
    "PRODUCTION_ENV_TOKENS",
    "DeploymentActorInput",
    "DeploymentOutcome",
    "DeploymentSignalSource",
    "DirectDeploymentIngestRequest",
    "DirectDeploymentResponse",
    "NormalizationResult",
    "NormalizedDeploymentPayload",
    "build_authoritative_production_deployments_query",
    "deployments_router",
    "extract_github_owner_repo",
    "ingest_direct_deployment",
    "is_production_environment",
    "list_authoritative_production_deployments",
    "normalize_argocd_postsync",
    "normalize_commit_sha",
    "normalize_environment",
    "normalize_github_deployment_status",
    "persist_deployment_idempotent",
    "resolve_argocd_project",
    "resolve_github_project",
]
