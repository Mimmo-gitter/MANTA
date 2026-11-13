"""Fast unit tests for US-25 payload normalization, outcome mapping, and env filtering."""

from datetime import datetime, timezone
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.deployments.normalization import (
    extract_github_owner_repo,
    is_production_environment,
    normalize_argocd_postsync,
    normalize_environment,
    normalize_github_deployment_status,
)
from app.deployments.schemas import (
    DeploymentOutcome,
    DeploymentSignalSource,
    DirectDeploymentIngestRequest,
)
from app.webhooks.verification import verify_argocd_signature, verify_signature_for_provider


def test_normalize_environment_strict_production_tokens():
    for raw in ("production", "PRODUCTION", "  Prod ", "prd", "live"):
        canonical, is_prod = normalize_environment(raw)
        assert canonical == "production"
        assert is_prod is True
        assert is_production_environment(raw) is True

    for non_prod in (
        "staging",
        "Stage",
        "STG",
        "preprod",
        "pre-prod",
        "uat",
        "prod-staging",
        "non-prod",
        "dev",
        "Development",
        "qa",
        "preview",
        "pr-142",
        "preview-feature",
        "ephemeral-env",
        "sandbox-1",
    ):
        canonical, is_prod = normalize_environment(non_prod)
        assert is_prod is False, f"{non_prod} must not map to production"
        assert canonical != "production"
        assert is_production_environment(non_prod) is False


def test_github_deployment_status_mappings_and_inactive_policy():
    now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    sha = "a" * 40

    def make_payload(state: str, env: str = "production", ds_extra: dict | None = None):
        ds = {
            "id": 9001,
            "state": state,
            "environment": env,
            "created_at": "2026-11-13T09:05:00Z",
            "creator": {"id": 42, "login": "octocat"},
        }
        if ds_extra:
            ds.update(ds_extra)
        return {
            "deployment_status": ds,
            "deployment": {"id": 5001, "sha": sha, "environment": env},
            "repository": {"id": 777, "full_name": "acme/manta"},
        }

    ok = normalize_github_deployment_status("deployment_status", make_payload("success", "prod"), received_at=now)
    assert ok.kind == "normalized"
    assert ok.outcome == DeploymentOutcome.SUCCESS
    assert ok.environment == "production"
    assert ok.authoritative_signal_source == DeploymentSignalSource.GITHUB_DEPLOYMENTS_API
    assert ok.external_deployment_id == "gh:5001"
    assert ok.actor is not None and ok.actor.external_id == "42"

    fail = normalize_github_deployment_status("deployment_status", make_payload("failure"), received_at=now)
    assert fail.kind == "normalized"
    assert fail.outcome == DeploymentOutcome.FAILED

    err = normalize_github_deployment_status("deployment_status", make_payload("error"), received_at=now)
    assert err.kind == "normalized"
    assert err.outcome == DeploymentOutcome.FAILED

    # Standard GitHub supersession emits state='inactive' and must be skipped by default
    superseded = normalize_github_deployment_status("deployment_status", make_payload("inactive"), received_at=now)
    assert superseded.kind == "noop"
    assert superseded.reason == "superseded_inactive"

    # Explicit rollback signal on inactive maps to ROLLED_BACK
    rb = normalize_github_deployment_status(
        "deployment_status",
        make_payload("inactive", ds_extra={"payload": {"rollback": True}}),
        received_at=now,
    )
    assert rb.kind == "normalized"
    assert rb.outcome == DeploymentOutcome.ROLLED_BACK

    # Non-terminal & staging ignored cleanly
    for st in ("pending", "queued", "in_progress"):
        res = normalize_github_deployment_status("deployment_status", make_payload(st), received_at=now)
        assert res.kind == "noop"
    stg = normalize_github_deployment_status("deployment_status", make_payload("success", "staging"), received_at=now)
    assert stg.kind == "noop"
    assert stg.reason == "non_production_environment:staging"


def test_argocd_postsync_outcome_precedence_and_nested_vs_flat():
    now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    sha = "b" * 40

    nested_ok = {
        "hookType": "PostSync",
        "app": {
            "metadata": {
                "name": "manta-api-prod",
                "labels": {"environment": "prod"},
            },
            "spec": {
                "source": {"repoURL": "https://github.com/Acme/Manta.git"},
                "destination": {"namespace": "prod-cluster"},
            },
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T09:10:00Z",
                    "syncResult": {"revision": sha},
                    "operation": {"initiatedBy": {"username": "ci-deployer"}},
                },
            },
        },
    }
    res_ok = normalize_argocd_postsync("postsync", nested_ok, received_at=now)
    assert res_ok.kind == "normalized"
    assert res_ok.outcome == DeploymentOutcome.SUCCESS
    assert res_ok.environment == "production"
    assert res_ok.commit_sha == sha
    assert res_ok.actor is not None and res_ok.actor.handle == "ci-deployer"
    assert extract_github_owner_repo(res_ok.repo_url) == "acme/manta"

    # Rollback precedence overrides Succeeded/Synced
    rb_payload = {
        "phase": "Succeeded",
        "status": "Synced",
        "is_rollback": True,
        "environment": "production",
        "revision": sha,
        "app_name": "manta-api-prod",
    }
    res_rb = normalize_argocd_postsync("postsync", rb_payload, received_at=now)
    assert res_rb.kind == "normalized"
    assert res_rb.outcome == DeploymentOutcome.ROLLED_BACK

    # Degraded health overrides Synced
    degraded = {
        "status": "Synced",
        "health": {"status": "Degraded"},
        "environment": "production",
        "commit_sha": sha,
    }
    res_fail = normalize_argocd_postsync("postsync", degraded, received_at=now)
    assert res_fail.kind == "normalized"
    assert res_fail.outcome == DeploymentOutcome.FAILED

    # Running stays non-terminal
    running = {"phase": "Running", "environment": "production", "commit_sha": sha}
    assert normalize_argocd_postsync("postsync", running, received_at=now).kind == "noop"


def test_argocd_hmac_verification_fail_closed():
    import hashlib
    import hmac

    body = b'{"phase":"Succeeded","environment":"production"}'
    secret = "argo_test_secret"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    verify_argocd_signature(secret, body, {"X-ArgoCD-Signature": sig})
    verify_signature_for_provider("argocd", secret, body, {"X-Hub-Signature-256": sig})

    with pytest.raises(HTTPException) as exc1:
        verify_argocd_signature(secret, body, {})
    assert exc1.value.status_code == 401

    with pytest.raises(HTTPException) as exc2:
        verify_signature_for_provider("unknown_provider", secret, body, {})
    assert exc2.value.status_code == 401


def test_direct_deployment_ingest_request_validation():
    req = DirectDeploymentIngestRequest(
        commit_sha="ABCD" * 10,
        environment="Prod",
        outcome=DeploymentOutcome.ROLLED_BACK,
        deployed_at=datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc),
        actor="DevOps@Example.COM",
        repository_id=uuid.uuid4(),
    )
    assert req.commit_sha == "abcd" * 10
    assert req.actor is not None
    assert req.actor.email == "devops@example.com"  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        DirectDeploymentIngestRequest(
            commit_sha="short-sha",
            environment="production",
            outcome=DeploymentOutcome.SUCCESS,
            deployed_at=datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc),
        )

    with pytest.raises(ValidationError):
        DirectDeploymentIngestRequest(
            commit_sha="a" * 40,
            environment="production",
            outcome="PENDING",  # type: ignore[arg-type]
            deployed_at=datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc),
        )
