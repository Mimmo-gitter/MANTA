"""Provider payload and environment normalization for US-25 deployment ingestion."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from app.deployments.schemas import (
    COMMIT_SHA_REGEX,
    DeploymentActorInput,
    DeploymentOutcome,
    DeploymentSignalSource,
)

PRODUCTION_ENV_TOKENS: frozenset[str] = frozenset({"production", "prod", "prd", "live"})
STAGING_ENV_TOKENS: frozenset[str] = frozenset(
    {"staging", "stage", "stg", "preprod", "pre-prod", "uat"}
)
DEV_ENV_TOKENS: frozenset[str] = frozenset({"dev", "development", "local"})
QA_ENV_TOKENS: frozenset[str] = frozenset({"qa", "test", "testing"})

_PREVIEW_PREFIXES: tuple[str, ...] = ("pr-", "preview-", "ephemeral-", "sandbox")
_GITHUB_ROLLBACK_KEYWORDS: tuple[str, ...] = ("rolled back", "rollback")
_SSH_GIT_REGEX = re.compile(r"^(?:[^@]+@)?[^:]+:(?P<path>.+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class NormalizationResult:
    """Result of normalizing a provider deployment webhook payload."""

    kind: Literal["normalized", "noop"]
    reason: Optional[str] = None
    commit_sha: Optional[str] = None
    environment: Optional[str] = None
    is_production: bool = False
    outcome: Optional[DeploymentOutcome] = None
    deployed_at: Optional[datetime] = None
    external_deployment_id: Optional[str] = None
    actor: Optional[DeploymentActorInput] = None
    authoritative_signal_source: Optional[DeploymentSignalSource] = None
    repo_external_id: Optional[int] = None
    repo_full_name: Optional[str] = None
    app_name: Optional[str] = None
    hint_project_id: Optional[str] = None
    hint_project_slug: Optional[str] = None
    repo_url: Optional[str] = None


def normalize_environment(raw_env: str) -> tuple[str, bool]:
    """
    Normalize an environment name into (canonical_environment, is_production).

    Exact production tokens map strictly to ('production', True).
    Non-production aliases normalize to staging/dev/qa/preview (is_production=False).
    """
    if not isinstance(raw_env, str):
        raise ValueError("environment must be a string")
    cleaned = raw_env.strip().lower()
    if not cleaned:
        raise ValueError("environment must not be blank")

    if cleaned in PRODUCTION_ENV_TOKENS:
        return "production", True
    if cleaned in STAGING_ENV_TOKENS:
        return "staging", False
    if cleaned in DEV_ENV_TOKENS:
        return "dev", False
    if cleaned in QA_ENV_TOKENS:
        return "qa", False
    if cleaned == "preview" or any(cleaned.startswith(prefix) for prefix in _PREVIEW_PREFIXES):
        return "preview", False
    return cleaned[:32], False


def is_production_environment(raw_or_normalized_env: str) -> bool:
    """Return True iff the environment token resolves to canonical production."""
    try:
        _, is_prod = normalize_environment(raw_or_normalized_env)
        return is_prod
    except ValueError:
        return False


def normalize_commit_sha(raw_sha: Any) -> Optional[str]:
    """Return lowercase 40-char git hex SHA or None when missing/invalid."""
    if not isinstance(raw_sha, str):
        return None
    cleaned = raw_sha.strip().lower()
    if not COMMIT_SHA_REGEX.match(cleaned):
        return None
    return cleaned


def parse_utc_datetime(raw_value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp or datetime object into UTC timezone-aware datetime."""
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        if raw_value.tzinfo is None or raw_value.utcoffset() is None:
            return raw_value.replace(tzinfo=timezone.utc)
        return raw_value.astimezone(timezone.utc)
    if isinstance(raw_value, str):
        text_val = raw_value.strip()
        if not text_val:
            return None
        if text_val.endswith("Z"):
            text_val = text_val[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text_val)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def extract_github_owner_repo(repo_url: Optional[str]) -> Optional[str]:
    """Extract normalized 'owner/repo' from HTTPS or SSH git remote URLs."""
    if not isinstance(repo_url, str):
        return None
    cleaned = repo_url.strip()
    if not cleaned:
        return None

    if "://" in cleaned:
        parsed = urlparse(cleaned)
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return path.lower() if "/" in path else None

    match = _SSH_GIT_REGEX.match(cleaned)
    if match:
        path = match.group("path").strip("/")
        return path.lower() if "/" in path else None

    return cleaned.lower() if "/" in cleaned else None


def _has_github_explicit_rollback_cue(
    deployment_status: dict[str, Any], deployment: dict[str, Any]
) -> bool:
    for container in (deployment_status, deployment):
        if container.get("rollback") is True or container.get("is_rollback") is True:
            return True
        if str(container.get("action") or "").strip().lower() == "rollback":
            return True
        nested_payload = container.get("payload")
        if isinstance(nested_payload, dict) and (
            nested_payload.get("rollback") is True
            or nested_payload.get("is_rollback") is True
            or str(nested_payload.get("action") or "").strip().lower() == "rollback"
        ):
            return True

    description = str(deployment_status.get("description") or "").strip().lower()
    if any(kw in description for kw in _GITHUB_ROLLBACK_KEYWORDS):
        return True
    return False


def _extract_github_actor(
    deployment_status: dict[str, Any],
    payload: dict[str, Any],
    deployment: dict[str, Any],
) -> Optional[DeploymentActorInput]:
    candidate = (
        deployment_status.get("creator") or payload.get("sender") or deployment.get("creator")
    )
    if not isinstance(candidate, dict):
        return None
    raw_id = candidate.get("id")
    login = candidate.get("login")
    email = candidate.get("email")
    name = candidate.get("name")
    external_id = str(raw_id) if raw_id is not None else (str(login) if login else None)
    if not any((external_id, email, login, name)):
        return None
    return DeploymentActorInput(
        external_id=external_id[:128] if external_id else None,
        email=str(email)[:320] if email else None,
        name=str(name)[:255] if name else None,
        handle=str(login)[:255] if login else None,
        provider="github",
    )


def normalize_github_deployment_status(
    event_type: str,
    payload: dict[str, Any],
    *,
    received_at: datetime,
) -> NormalizationResult:
    """Normalize a GitHub deployment_status webhook event into canonical fields."""
    if event_type.strip().lower() != "deployment_status":
        return NormalizationResult(kind="noop", reason=f"ignored_github_event:{event_type}")

    deployment_status = payload.get("deployment_status")
    deployment = payload.get("deployment")
    repository = payload.get("repository")
    if not isinstance(deployment_status, dict) or not isinstance(deployment, dict):
        return NormalizationResult(kind="noop", reason="malformed_github_deployment_payload")

    raw_state = str(deployment_status.get("state") or "").strip().lower()
    if raw_state == "success":
        outcome = DeploymentOutcome.SUCCESS
    elif raw_state in {"failure", "error"}:
        outcome = DeploymentOutcome.FAILED
    elif raw_state == "inactive":
        if _has_github_explicit_rollback_cue(deployment_status, deployment):
            outcome = DeploymentOutcome.ROLLED_BACK
        else:
            return NormalizationResult(kind="noop", reason="superseded_inactive")
    elif raw_state in {"pending", "queued", "in_progress", "waiting", "requested"}:
        return NormalizationResult(kind="noop", reason=f"non_terminal_state:{raw_state}")
    else:
        return NormalizationResult(kind="noop", reason=f"unrecognized_state:{raw_state}")

    raw_env = deployment_status.get("environment") or deployment.get("environment") or ""
    try:
        canonical_env, is_prod = normalize_environment(str(raw_env))
    except ValueError:
        return NormalizationResult(kind="noop", reason="missing_environment")

    if not is_prod:
        return NormalizationResult(
            kind="noop",
            reason=f"non_production_environment:{canonical_env}",
            environment=canonical_env,
            is_production=False,
        )

    commit_sha = normalize_commit_sha(deployment.get("sha"))
    if commit_sha is None:
        return NormalizationResult(kind="noop", reason="invalid_commit_sha")

    deployed_at = (
        parse_utc_datetime(deployment_status.get("updated_at"))
        or parse_utc_datetime(deployment_status.get("created_at"))
        or parse_utc_datetime(deployment.get("created_at"))
        or parse_utc_datetime(received_at)
        or datetime.now(timezone.utc)
    )

    ext_id: Optional[str] = None
    if deployment.get("id") is not None:
        ext_id = f"gh:{deployment['id']}"[:128]
    elif deployment_status.get("id") is not None:
        ext_id = f"gh-status:{deployment_status['id']}"[:128]

    repo_external_id: Optional[int] = None
    repo_full_name: Optional[str] = None
    if isinstance(repository, dict):
        raw_repo_id = repository.get("id")
        if isinstance(raw_repo_id, int):
            repo_external_id = raw_repo_id
        elif isinstance(raw_repo_id, str) and raw_repo_id.isdigit():
            repo_external_id = int(raw_repo_id)
        if repository.get("full_name"):
            repo_full_name = str(repository["full_name"]).strip().lower()

    return NormalizationResult(
        kind="normalized",
        commit_sha=commit_sha,
        environment=canonical_env,
        is_production=True,
        outcome=outcome,
        deployed_at=deployed_at,
        external_deployment_id=ext_id,
        actor=_extract_github_actor(deployment_status, payload, deployment),
        authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        repo_external_id=repo_external_id,
        repo_full_name=repo_full_name,
    )


def _first_non_empty_str(*values: Any) -> Optional[str]:
    for val in values:
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _map_argocd_outcome(
    payload: dict[str, Any],
    operation_state: dict[str, Any],
    app_status: dict[str, Any],
) -> tuple[Optional[DeploymentOutcome], Optional[str]]:
    # 1. Explicit rollback cues take highest precedence
    explicit_outcome = str(payload.get("outcome") or "").strip().upper()
    raw_phase = _first_non_empty_str(operation_state.get("phase"), payload.get("phase")) or ""
    raw_status = _first_non_empty_str(payload.get("status")) or ""
    operation_block = (
        operation_state.get("operation")
        if isinstance(operation_state.get("operation"), dict)
        else {}
    )
    sync_block = (
        operation_block.get("sync") if isinstance(operation_block.get("sync"), dict) else {}
    )

    if (
        explicit_outcome == DeploymentOutcome.ROLLED_BACK.value
        or raw_phase.lower() in {"rolledback", "rolled_back"}
        or raw_status.lower() in {"rolledback", "rolled_back"}
        or str(payload.get("action") or "").strip().lower() == "rollback"
        or payload.get("is_rollback") is True
        or payload.get("rollback") is True
        or sync_block.get("rollback") is True
    ):
        return DeploymentOutcome.ROLLED_BACK, None

    # 2. Failure / health degradation cues
    health_block = app_status.get("health") if isinstance(app_status.get("health"), dict) else {}
    if not health_block and isinstance(payload.get("health"), dict):
        health_block = payload["health"]
    health_status = (
        str(health_block.get("status") or payload.get("healthStatus") or "").strip().lower()
    )

    phase_lower = raw_phase.lower()
    status_lower = raw_status.lower()
    if (
        explicit_outcome == DeploymentOutcome.FAILED.value
        or phase_lower in {"failed", "error"}
        or status_lower in {"failed", "error", "outofsync"}
        or health_status in {"degraded", "missing", "suspended"}
    ):
        return DeploymentOutcome.FAILED, None

    # 3. Non-terminal phases / progressing health
    if phase_lower in {"running", "terminating"}:
        return None, f"non_terminal_phase:{raw_phase}"

    # 4. Success cues
    if (
        explicit_outcome == DeploymentOutcome.SUCCESS.value
        or phase_lower == "succeeded"
        or status_lower == "synced"
    ):
        if health_status == "progressing":
            return None, "non_terminal_health:Progressing"
        return DeploymentOutcome.SUCCESS, None

    if health_status == "progressing":
        return None, "non_terminal_health:Progressing"

    return None, f"unrecognized_argocd_state:{raw_phase or raw_status}"


def normalize_argocd_postsync(
    event_type: str,
    payload: dict[str, Any],
    *,
    received_at: datetime,
) -> NormalizationResult:
    """Normalize an ArgoCD PostSync webhook payload into canonical deployment fields."""
    hook_type = _first_non_empty_str(
        payload.get("hookType"),
        payload.get("hook_type"),
        event_type,
    )
    if hook_type and hook_type.lower() in {"presync", "syncfail", "skip"}:
        # Note: SyncFail hooks can still represent PostSync failure if phase/status is Failed
        if hook_type.lower() == "presync":
            return NormalizationResult(kind="noop", reason=f"ignored_argocd_hook:{hook_type}")

    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    if not app and isinstance(payload.get("application"), dict):
        app = payload["application"]

    app_metadata = app.get("metadata") if isinstance(app.get("metadata"), dict) else {}
    app_labels = app_metadata.get("labels") if isinstance(app_metadata.get("labels"), dict) else {}
    app_annotations = (
        app_metadata.get("annotations") if isinstance(app_metadata.get("annotations"), dict) else {}
    )
    app_spec = app.get("spec") if isinstance(app.get("spec"), dict) else {}
    app_dest = app_spec.get("destination") if isinstance(app_spec.get("destination"), dict) else {}
    app_source = app_spec.get("source") if isinstance(app_spec.get("source"), dict) else {}
    app_status = app.get("status") if isinstance(app.get("status"), dict) else {}

    operation_state: dict[str, Any] = {}
    if isinstance(payload.get("operationState"), dict):
        operation_state = payload["operationState"]
    elif isinstance(app_status.get("operationState"), dict):
        operation_state = app_status["operationState"]

    outcome, skip_reason = _map_argocd_outcome(payload, operation_state, app_status)
    if outcome is None:
        return NormalizationResult(kind="noop", reason=skip_reason)

    raw_env = _first_non_empty_str(
        payload.get("environment"),
        payload.get("destination_env"),
        app_labels.get("environment"),
        app_labels.get("app.kubernetes.io/environment"),
        app_annotations.get("manta.environment"),
        app_dest.get("namespace"),
        app_dest.get("name"),
    )
    if not raw_env:
        return NormalizationResult(kind="noop", reason="missing_environment")

    try:
        canonical_env, is_prod = normalize_environment(raw_env)
    except ValueError:
        return NormalizationResult(kind="noop", reason="invalid_environment")

    if not is_prod:
        return NormalizationResult(
            kind="noop",
            reason=f"non_production_environment:{canonical_env}",
            environment=canonical_env,
            is_production=False,
        )

    sync_result = (
        operation_state.get("syncResult")
        if isinstance(operation_state.get("syncResult"), dict)
        else {}
    )
    app_sync = app_status.get("sync") if isinstance(app_status.get("sync"), dict) else {}
    operation_block = (
        operation_state.get("operation")
        if isinstance(operation_state.get("operation"), dict)
        else {}
    )
    op_sync = operation_block.get("sync") if isinstance(operation_block.get("sync"), dict) else {}

    raw_sha = _first_non_empty_str(
        sync_result.get("revision"),
        op_sync.get("revision"),
        app_sync.get("revision"),
        payload.get("commit_sha"),
        payload.get("revision"),
    )
    commit_sha = normalize_commit_sha(raw_sha)
    if commit_sha is None:
        return NormalizationResult(kind="noop", reason="invalid_commit_sha")

    deployed_at = (
        parse_utc_datetime(operation_state.get("finishedAt"))
        or parse_utc_datetime(operation_state.get("startedAt"))
        or parse_utc_datetime(payload.get("deployed_at"))
        or parse_utc_datetime(received_at)
        or datetime.now(timezone.utc)
    )

    app_name = _first_non_empty_str(
        app_metadata.get("name"),
        payload.get("app_name"),
        payload.get("application") if isinstance(payload.get("application"), str) else None,
    )

    explicit_ext_id = _first_non_empty_str(
        payload.get("external_deployment_id"),
        payload.get("operation_id"),
    )
    if explicit_ext_id:
        ext_id = explicit_ext_id[:128]
    else:
        identity_basis = f"{app_name or 'argocd'}:{commit_sha}:{deployed_at.isoformat()}"
        digest = hashlib.sha256(identity_basis.encode("utf-8")).hexdigest()[:24]
        ext_id = f"argo:{app_name or 'app'}:{digest}"[:128]

    initiated_by = (
        operation_block.get("initiatedBy")
        if isinstance(operation_block.get("initiatedBy"), dict)
        else {}
    )
    actor_obj: Optional[DeploymentActorInput] = None
    username = _first_non_empty_str(
        initiated_by.get("username"),
        payload.get("actor") if isinstance(payload.get("actor"), str) else None,
    )
    is_automated = initiated_by.get("automated") is True
    if username and not is_automated:
        actor_obj = DeploymentActorInput.from_raw(username, default_provider="argocd")
    elif isinstance(payload.get("actor"), dict):
        actor_obj = DeploymentActorInput.from_raw(payload["actor"], default_provider="argocd")

    hint_project_id = _first_non_empty_str(
        str(payload["project_id"]) if payload.get("project_id") else None,
        app_labels.get("manta.project_id"),
        app_annotations.get("manta.project_id"),
    )
    hint_project_slug = _first_non_empty_str(
        payload.get("project_slug"),
        app_labels.get("manta.project_slug"),
        app_annotations.get("manta.project_slug"),
    )
    repo_url = _first_non_empty_str(
        app_source.get("repoURL"),
        payload.get("repo_url"),
    )

    return NormalizationResult(
        kind="normalized",
        commit_sha=commit_sha,
        environment=canonical_env,
        is_production=True,
        outcome=outcome,
        deployed_at=deployed_at,
        external_deployment_id=ext_id,
        actor=actor_obj,
        authoritative_signal_source=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK,
        app_name=app_name,
        hint_project_id=hint_project_id,
        hint_project_slug=hint_project_slug,
        repo_url=repo_url,
    )
