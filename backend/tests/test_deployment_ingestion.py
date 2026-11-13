"""Automated integration and end-to-end test suite for unified production deployment ingestion.

Covers all 22+ formal test scenarios specified in Task 25.5 (SCRUM-431 / SCRUM-432):
- Scenarios TC-DEP-GH-001 through TC-DEP-GH-007 (Signal 1: GitHub Deployments API)
- Scenarios TC-DEP-ARGO-001 through TC-DEP-ARGO-009 (Signal 2: ArgoCD PostSync Webhooks)
- Scenarios TC-DEP-DIR-001 through TC-DEP-DIR-008 (Signal 3: Direct Deployment REST API)
- Scenarios TC-DEP-DORA-001 and TC-DEP-DORA-002 (Cross-Signal DORA Authoritative Filtering)
- Scenarios TC-DEP-PRIV-001 and TC-DEP-PRIV-002 (GDPR Actor PII Isolation & Pseudonymization)
- Scenarios TC-DEP-ORD-001 and TC-DEP-ORD-002 (Out-of-Order Commit SHA & Schema Constraints)

Specifications:
- backend/tests/scenarios/test_us25_deployment_ingestion_scenarios.md
- docs/deployment-ingestion-pipeline.md
- docs/deployment-signals-outcomes-and-staging-exclusion-test-scenarios.md
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any, Optional
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.jwks import JWKSClient
from app.contributors.service import resolve_or_create_contributor_pseudonym
from app.db.models.models import (
    Contributor,
    Deployment,
    Integration,
    Project,
    ProjectLink,
    ProjectMember,
    Repository,
    User,
    WebhookEvent,
)
from app.db.session import async_engine, get_tenant_db_context
from app.deployments.service import list_authoritative_production_deployments
from app.integrations.crypto import encryption_service
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter
from app.webhooks.processor import process_webhook_job


# ---------------------------------------------------------------------------
# Test Fixtures & Shared Helper Functions
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _reset_state():
    """Teardown database engine connections and reset tenant registration rate limiters."""
    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


@pytest.fixture
def _mock_auth_ctx():
    """Fixture providing RS256 RSA private key and mocked JWKS client for OIDC token minting."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "us25-test-kid", "kty": "RSA", "alg": "RS256", "use": "sig"})
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"keys": [jwk]}, request=req)
    )
    orig = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs", ttl_seconds=300.0, transport=transport
    )
    yield {"key": private_key, "kid": "us25-test-kid"}
    app.state.jwks_client = orig


def _mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: uuid.UUID,
    roles: list[str],
    sub: str = "us25-ci-admin",
) -> str:
    """Generate a signed RS256 JWT bearer token for testing."""
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode(
        {
            "sub": sub,
            "tenant_id": str(tenant_id),
            "roles": roles,
            "iss": "http://localhost:8080/realms/manta",
            "aud": "manta-backend",
            "iat": now,
            "exp": now + 3600,
        },
        auth_ctx["key"],
        algorithm="RS256",
        headers={"kid": auth_ctx["kid"]},
    )


async def _register_tenant(tag: str) -> uuid.UUID:
    """Register a new isolated test tenant via /tenants/register."""
    slug = f"{tag}-{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/tenants/register",
            json={
                "name": f"US25 Org {slug}",
                "slug": slug,
                "admin_email": f"admin-{slug}@example.com",
                "admin_name": "US25 Test Admin",
            },
        )
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["tenant_id"])


async def _create_project(
    tenant_id: uuid.UUID,
    *,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    authoritative_cd_signal: str = "github_deployments_api",
) -> uuid.UUID:
    """Create an active project under tenant RLS."""
    pid = uuid.uuid4()
    p_name = name or f"Project {pid.hex[:6]}"
    p_slug = slug or f"proj-{pid.hex[:8]}"
    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Project(
                id=pid,
                tenant_id=tenant_id,
                name=p_name,
                slug=p_slug,
                authoritative_cd_signal=authoritative_cd_signal,
            )
        )
        await db.commit()
    return pid


async def _setup_github_context(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    ext_repo_id: int = 10101,
    repo_full_name: str = "acme/web-service",
    secret: str = "gh_whsec_secret_123",
) -> tuple[uuid.UUID, str]:
    """Helper to configure GitHub integration and repository link."""
    integ_id = uuid.uuid4()
    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Integration(
                id=integ_id,
                tenant_id=tenant_id,
                project_id=project_id,
                provider="github",
                external_account_id="acct-gh-101",
                webhook_secret_hash=encryption_service.encrypt(secret),
                is_active=True,
            )
        )
        db.add(
            Repository(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                external_repo_id=ext_repo_id,
                full_name=repo_full_name,
                default_branch="main",
                is_private=True,
            )
        )
        await db.commit()
    return integ_id, secret


async def _setup_argocd_context(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    app_name: str = "cart-service-prod",
    secret: str = "argo_whsec_secret_456",
) -> tuple[uuid.UUID, str]:
    """Helper to configure ArgoCD integration and ProjectLink."""
    integ_id = uuid.uuid4()
    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Integration(
                id=integ_id,
                tenant_id=tenant_id,
                project_id=project_id,
                provider="argocd",
                external_account_id="acct-argo-202",
                webhook_secret_hash=encryption_service.encrypt(secret),
                is_active=True,
            )
        )
        db.add(
            ProjectLink(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                integration_id=integ_id,
                link_type="argocd_application",
                external_id=app_name,
            )
        )
        await db.commit()
    return integ_id, secret


async def _send_and_process_github_webhook(
    integ_id: uuid.UUID,
    tenant_id: uuid.UUID,
    secret: str,
    payload: dict[str, Any],
    event_type: str = "deployment_status",
) -> tuple[int, uuid.UUID | None, str]:
    """Deliver signed GitHub webhook and process staged event synchronously."""
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), raw_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/webhooks/integrations/{integ_id}",
            content=raw_bytes,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": event_type,
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-Hub-Signature-256": sig,
            },
        )
    if res.status_code != 202:
        return res.status_code, None, res.text

    event_id = uuid.UUID(res.json()["event_id"])
    outcome = await process_webhook_job({"event_id": str(event_id), "tenant_id": str(tenant_id)})
    return res.status_code, event_id, outcome


async def _send_and_process_argocd_webhook(
    integ_id: uuid.UUID,
    tenant_id: uuid.UUID,
    secret: str,
    payload: dict[str, Any],
    event_type: str = "postsync",
) -> tuple[int, uuid.UUID | None, str]:
    """Deliver signed ArgoCD webhook and process staged event synchronously."""
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), raw_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/webhooks/integrations/{integ_id}",
            content=raw_bytes,
            headers={
                "Content-Type": "application/json",
                "X-ArgoCD-Event": event_type,
                "X-ArgoCD-Delivery": f"argo-{uuid.uuid4().hex[:8]}",
                "X-ArgoCD-Signature": sig,
            },
        )
    if res.status_code != 202:
        return res.status_code, None, res.text

    event_id = uuid.UUID(res.json()["event_id"])
    outcome = await process_webhook_job({"event_id": str(event_id), "tenant_id": str(tenant_id)})
    return res.status_code, event_id, outcome


# ---------------------------------------------------------------------------
# Section 5.1: Signal 1 - GitHub Deployments API (`github_deployments_api`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dep_gh_001_production_deployment_success():
    """
    Scenario TC-DEP-GH-001: GitHub Production Deployment SUCCESS.
    - Valid state = 'success' and production environment normalizes to SUCCESS.
    - Sets is_change_failure = False.
    - Resolves actor pseudonym (64 hex characters) and creates contributor row under tenant.
    - deployments.triggered_by remains NULL.
    """
    tenant_id = await _register_tenant("gh-001")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="github_deployments_api")
    integ_id, secret = await _setup_github_context(
        tenant_id, project_id, ext_repo_id=10101, repo_full_name="acme/web-service"
    )

    commit_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    payload = {
        "deployment_status": {
            "id": 900101,
            "state": "success",
            "environment": "production",
            "description": "Deployment finished successfully via GitHub Actions",
            "created_at": "2026-11-13T10:00:00Z",
            "updated_at": "2026-11-13T10:02:15Z",
            "creator": {"id": 48192, "login": "octocat-deployer", "email": "octocat@acme.com"},
        },
        "deployment": {
            "id": 800101,
            "sha": commit_sha,
            "environment": "production",
            "ref": "main",
            "created_at": "2026-11-13T09:58:00Z",
        },
        "repository": {"id": 10101, "name": "web-service", "full_name": "acme/web-service"},
    }

    status_code, event_id, outcome = await _send_and_process_github_webhook(
        integ_id, tenant_id, secret, payload
    )
    assert status_code == 202
    assert outcome.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        dep = (
            await db.execute(
                select(Deployment).where(Deployment.external_deployment_id == "gh:800101")
            )
        ).scalar_one()

        assert dep.status == "SUCCESS"
        assert dep.is_change_failure is False
        assert dep.environment == "production"
        assert dep.authoritative_signal_source == "github_deployments_api"
        assert dep.commit_sha == commit_sha
        assert dep.triggered_by is None
        assert dep.actor_pseudonym_id is not None
        assert len(dep.actor_pseudonym_id) == 64

        contrib = (
            await db.execute(
                select(Contributor).where(Contributor.pseudonym_id == dep.actor_pseudonym_id)
            )
        ).scalar_one()
        assert contrib.provider == "github"
        assert contrib.external_actor_id == "48192"
        assert contrib.raw_handle == "octocat-deployer"

        ev = (
            await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
        ).scalar_one()
        assert ev.status == "completed"


@pytest.mark.asyncio
async def test_tc_dep_gh_002_production_deployment_failed():
    """
    Scenario TC-DEP-GH-002: GitHub Production Deployment FAILED.
    - state in ('failure', 'error') records status = 'FAILED' and is_change_failure = True.
    """
    tenant_id = await _register_tenant("gh-002")
    project_id = await _create_project(tenant_id)
    integ_id, secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=10102)

    for idx, gh_state in enumerate(("failure", "error")):
        sha = f"b{idx}" * 20
        payload = {
            "deployment_status": {
                "id": 900200 + idx,
                "state": gh_state,
                "environment": "prod",
                "description": f"Health check failed with {gh_state}",
                "updated_at": f"2026-11-13T10:1{idx}:00Z",
                "creator": {"id": 48192, "login": "octocat-deployer"},
            },
            "deployment": {"id": 800200 + idx, "sha": sha, "environment": "prod"},
            "repository": {"id": 10102, "full_name": "acme/web-service"},
        }
        status_code, _, outcome = await _send_and_process_github_webhook(
            integ_id, tenant_id, secret, payload
        )
        assert status_code == 202
        assert outcome.startswith("persisted:")

        async with get_tenant_db_context(tenant_id) as db:
            dep = (
                await db.execute(select(Deployment).where(Deployment.commit_sha == sha))
            ).scalar_one()
            assert dep.status == "FAILED"
            assert dep.is_change_failure is True
            assert dep.environment == "production"
            assert dep.authoritative_signal_source == "github_deployments_api"


@pytest.mark.asyncio
async def test_tc_dep_gh_003_production_deployment_rolled_back():
    """
    Scenario TC-DEP-GH-003: GitHub Production Deployment ROLLED_BACK.
    - state = 'inactive' with explicit rollback cues maps to ROLLED_BACK.
    - Verifies both payload.rollback = True and explicit rollback description.
    """
    tenant_id = await _register_tenant("gh-003")
    project_id = await _create_project(tenant_id)
    integ_id, secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=10103)

    # 1. Cue via payload.rollback = True
    sha1 = "c1" * 20
    payload_flag = {
        "deployment_status": {
            "id": 900301,
            "state": "inactive",
            "environment": "production",
            "payload": {"rollback": True, "previous_release": "v2.4.0"},
            "updated_at": "2026-11-13T10:30:00Z",
            "creator": {"id": 48192, "login": "octocat-deployer"},
        },
        "deployment": {"id": 800301, "sha": sha1, "environment": "production"},
        "repository": {"id": 10103, "full_name": "acme/web-service"},
    }
    _, _, out1 = await _send_and_process_github_webhook(integ_id, tenant_id, secret, payload_flag)
    assert out1.startswith("persisted:")

    # 2. Cue via description
    sha2 = "c2" * 20
    payload_desc = {
        "deployment_status": {
            "id": 900302,
            "state": "inactive",
            "environment": "production",
            "description": "Rolled back production traffic to stable release v2.4.0",
            "updated_at": "2026-11-13T10:35:00Z",
            "creator": {"id": 48192, "login": "octocat-deployer"},
        },
        "deployment": {"id": 800302, "sha": sha2, "environment": "production"},
        "repository": {"id": 10103, "full_name": "acme/web-service"},
    }
    _, _, out2 = await _send_and_process_github_webhook(integ_id, tenant_id, secret, payload_desc)
    assert out2.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        deps = (
            (await db.execute(select(Deployment).where(Deployment.commit_sha.in_([sha1, sha2]))))
            .scalars()
            .all()
        )
        assert len(deps) == 2
        for d in deps:
            assert d.status == "ROLLED_BACK"
            assert d.is_change_failure is True
            assert d.environment == "production"


@pytest.mark.asyncio
async def test_tc_dep_gh_004_superseded_inactive_exclusion():
    """
    Scenario TC-DEP-GH-004: GitHub Superseded Inactive Deployment (NOOP Exclusion).
    - Standard auto_inactive events without rollback cues are NOOPs (0 rows inserted).
    - Webhook event marked completed.
    """
    tenant_id = await _register_tenant("gh-004")
    project_id = await _create_project(tenant_id)
    integ_id, secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=10104)

    payload = {
        "deployment_status": {
            "id": 900401,
            "state": "inactive",
            "environment": "production",
            "description": "Deployment superseded by newer release",
            "updated_at": "2026-11-13T10:45:00Z",
        },
        "deployment": {
            "id": 800401,
            "sha": "d4" * 20,
            "environment": "production",
        },
        "repository": {"id": 10104, "full_name": "acme/web-service"},
    }
    status_code, event_id, outcome = await _send_and_process_github_webhook(
        integ_id, tenant_id, secret, payload
    )
    assert status_code == 202
    assert outcome == "noop:superseded_inactive"

    async with get_tenant_db_context(tenant_id) as db:
        count = (
            await db.execute(
                select(Deployment).where(Deployment.external_deployment_id == "gh:800401")
            )
        ).scalar_one_or_none()
        assert count is None
        ev = (
            await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
        ).scalar_one()
        assert ev.status == "completed"


@pytest.mark.asyncio
async def test_tc_dep_gh_005_non_terminal_states_exclusion():
    """
    Scenario TC-DEP-GH-005: GitHub Non-Terminal States (NOOP Exclusion).
    - States in ('pending', 'queued', 'in_progress', 'waiting', 'requested') are ignored.
    - Zero deployment rows inserted.
    """
    tenant_id = await _register_tenant("gh-005")
    project_id = await _create_project(tenant_id)
    integ_id, secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=10105)

    non_terminals = ("pending", "queued", "in_progress", "waiting", "requested")
    for idx, st in enumerate(non_terminals):
        payload = {
            "deployment_status": {
                "id": 900500 + idx,
                "state": st,
                "environment": "production",
                "updated_at": "2026-11-13T10:50:00Z",
            },
            "deployment": {"id": 800500 + idx, "sha": f"e{idx}" * 20, "environment": "production"},
            "repository": {"id": 10105, "full_name": "acme/web-service"},
        }
        _, event_id, outcome = await _send_and_process_github_webhook(
            integ_id, tenant_id, secret, payload
        )
        assert outcome == f"noop:non_terminal_state:{st}"

        async with get_tenant_db_context(tenant_id) as db:
            ev = (
                await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
            ).scalar_one()
            assert ev.status == "completed"

    async with get_tenant_db_context(tenant_id) as db:
        all_deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(all_deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_gh_006_staging_environment_exclusion():
    """
    Scenario TC-DEP-GH-006: GitHub Staging Environment Exclusion (NOOP Non-Production).
    - Deployments targeting non-production environments are discarded on intake.
    - Zero rows inserted into deployments.
    """
    tenant_id = await _register_tenant("gh-006")
    project_id = await _create_project(tenant_id)
    integ_id, secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=10106)

    non_prod_envs = ("staging", "stage", "stg", "preprod", "dev", "qa", "preview", "pr-99")
    for idx, env_val in enumerate(non_prod_envs):
        payload = {
            "deployment_status": {
                "id": 900600 + idx,
                "state": "success",
                "environment": env_val,
                "updated_at": "2026-11-13T11:00:00Z",
            },
            "deployment": {"id": 800600 + idx, "sha": f"f{idx}" * 20, "environment": env_val},
            "repository": {"id": 10106, "full_name": "acme/web-service"},
        }
        _, event_id, outcome = await _send_and_process_github_webhook(
            integ_id, tenant_id, secret, payload
        )
        assert outcome.startswith("noop:non_production_environment:")

        async with get_tenant_db_context(tenant_id) as db:
            ev = (
                await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
            ).scalar_one()
            assert ev.status == "completed"

    async with get_tenant_db_context(tenant_id) as db:
        deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_gh_007_unlinked_repository_noop():
    """
    Scenario TC-DEP-GH-007: GitHub Unlinked Repository (Graceful NOOP).
    - Webhook for a repository unlinked in the tenant completes without inserting rows.
    """
    tenant_id = await _register_tenant("gh-007")
    project_id = await _create_project(tenant_id)
    # Repository 99999 is NOT linked in the database
    integ_id, secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=10107)

    payload = {
        "deployment_status": {
            "id": 900701,
            "state": "success",
            "environment": "production",
            "updated_at": "2026-11-13T11:05:00Z",
        },
        "deployment": {"id": 800701, "sha": "10" * 20, "environment": "production"},
        "repository": {"id": 99999, "full_name": "unknown/external-repo"},
    }
    status_code, event_id, outcome = await _send_and_process_github_webhook(
        integ_id, tenant_id, secret, payload
    )
    assert status_code == 202
    assert outcome == "noop:unlinked_github_repository"

    async with get_tenant_db_context(tenant_id) as db:
        ev = (
            await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
        ).scalar_one()
        assert ev.status == "completed"
        deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(deps) == 0


# ---------------------------------------------------------------------------
# Section 5.2: Signal 2 - ArgoCD `PostSync` Webhook (`argocd_postsync_webhook`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dep_argo_001_postsync_production_success():
    """
    Scenario TC-DEP-ARGO-001: ArgoCD PostSync Production SUCCESS.
    - phase = 'Succeeded' and health.status = 'Healthy' normalizes to SUCCESS.
    - is_change_failure = False.
    - Initiating user is pseudonymized and stored in contributors.
    """
    tenant_id = await _register_tenant("argo-001")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-prod-001")

    commit_sha = "f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1"
    payload = {
        "hookType": "PostSync",
        "app": {
            "metadata": {
                "name": "cart-prod-001",
                "labels": {
                    "app.kubernetes.io/name": "cart-service",
                    "environment": "prod",
                },
            },
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "startedAt": "2026-11-13T11:10:00Z",
                    "finishedAt": "2026-11-13T11:12:30Z",
                    "syncResult": {"revision": commit_sha},
                    "operation": {"initiatedBy": {"username": "sre-lead@acme.com"}},
                },
            },
        },
    }

    status_code, event_id, outcome = await _send_and_process_argocd_webhook(
        integ_id, tenant_id, secret, payload
    )
    assert status_code == 202
    assert outcome.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        dep = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == commit_sha))
        ).scalar_one()

        assert dep.status == "SUCCESS"
        assert dep.is_change_failure is False
        assert dep.environment == "production"
        assert dep.authoritative_signal_source == "argocd_postsync_webhook"
        assert dep.actor_pseudonym_id is not None
        assert len(dep.actor_pseudonym_id) == 64
        assert dep.triggered_by is None

        contrib = (
            await db.execute(
                select(Contributor).where(Contributor.pseudonym_id == dep.actor_pseudonym_id)
            )
        ).scalar_one()
        assert contrib.provider == "argocd"
        assert contrib.external_actor_id == "sre-lead@acme.com"
        assert contrib.raw_email == "sre-lead@acme.com"


@pytest.mark.asyncio
async def test_tc_dep_argo_002_postsync_production_failed():
    """
    Scenario TC-DEP-ARGO-002: ArgoCD PostSync Production FAILED.
    - Tests both operation failure (phase in {'Failed', 'Error'})
      and health degradation (health.status in {'Degraded', 'Missing'}).
    - Persists status = 'FAILED' and is_change_failure = True.
    """
    tenant_id = await _register_tenant("argo-002")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-prod-002")

    # 1. Phase failure
    sha_phase = "1a" * 20
    payload_phase = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "cart-prod-002", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Failed",
                    "finishedAt": "2026-11-13T11:20:00Z",
                    "syncResult": {"revision": sha_phase},
                    "operation": {"initiatedBy": {"username": "sre-lead@acme.com"}},
                },
            },
        },
    }
    _, _, out1 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_phase)
    assert out1.startswith("persisted:")

    # 2. Degraded health
    sha_health = "1b" * 20
    payload_health = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "cart-prod-002", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Degraded"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T11:25:00Z",
                    "syncResult": {"revision": sha_health},
                    "operation": {"initiatedBy": {"username": "sre-lead@acme.com"}},
                },
            },
        },
    }
    _, _, out2 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_health)
    assert out2.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        for target_sha in (sha_phase, sha_health):
            dep = (
                await db.execute(select(Deployment).where(Deployment.commit_sha == target_sha))
            ).scalar_one()
            assert dep.status == "FAILED"
            assert dep.is_change_failure is True
            assert dep.environment == "production"
            assert dep.authoritative_signal_source == "argocd_postsync_webhook"


@pytest.mark.asyncio
async def test_tc_dep_argo_003_postsync_production_rolled_back():
    """
    Scenario TC-DEP-ARGO-003: ArgoCD PostSync Production ROLLED_BACK.
    - Explicit rollback cues (outcome='ROLLED_BACK', action='rollback', phase='RolledBack')
      normalize directly to ROLLED_BACK with is_change_failure = True.
    """
    tenant_id = await _register_tenant("argo-003")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-prod-003")

    sha_rb = "2b" * 20
    payload_rb = {
        "hookType": "PostSync",
        "outcome": "ROLLED_BACK",
        "action": "rollback",
        "app": {
            "metadata": {"name": "cart-prod-003", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "RolledBack",
                    "finishedAt": "2026-11-13T11:35:00Z",
                    "syncResult": {"revision": sha_rb},
                },
            },
        },
    }

    status_code, _, outcome = await _send_and_process_argocd_webhook(
        integ_id, tenant_id, secret, payload_rb
    )
    assert status_code == 202
    assert outcome.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        dep = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == sha_rb))
        ).scalar_one()
        assert dep.status == "ROLLED_BACK"
        assert dep.is_change_failure is True
        assert dep.environment == "production"


@pytest.mark.asyncio
async def test_tc_dep_argo_004_automated_controller_deployment_null_actor():
    """
    Scenario TC-DEP-ARGO-004: ArgoCD Automated Controller Deployment (Null Actor Pseudonym).
    - initiatedBy.automated = True or absent username sets actor_pseudonym_id = NULL.
    """
    tenant_id = await _register_tenant("argo-004")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-prod-004")

    sha_auto = "3c" * 20
    payload_auto = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "cart-prod-004", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T11:45:00Z",
                    "syncResult": {"revision": sha_auto},
                    "operation": {"initiatedBy": {"automated": True}},
                },
            },
        },
    }

    status_code, _, outcome = await _send_and_process_argocd_webhook(
        integ_id, tenant_id, secret, payload_auto
    )
    assert status_code == 202
    assert outcome.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        dep = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == sha_auto))
        ).scalar_one()
        assert dep.status == "SUCCESS"
        assert dep.actor_pseudonym_id is None
        assert dep.triggered_by is None


@pytest.mark.asyncio
async def test_tc_dep_argo_005_non_terminal_phase_and_progressing_health():
    """
    Scenario TC-DEP-ARGO-005: ArgoCD Non-Terminal Phase & Progressing Health (NOOP Exclusion).
    - phase = 'Running' or health.status = 'Progressing' are ignored as NOOPs.
    - Zero rows inserted into deployments.
    """
    tenant_id = await _register_tenant("argo-005")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-prod-005")

    # 1. Non-terminal phase Running
    payload_running = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "cart-prod-005", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Running",
                    "syncResult": {"revision": "4a" * 20},
                },
            },
        },
    }
    _, _, out1 = await _send_and_process_argocd_webhook(
        integ_id, tenant_id, secret, payload_running
    )
    assert out1 == "noop:non_terminal_phase:Running"

    # 2. Non-terminal health Progressing
    payload_prog = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "cart-prod-005", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Progressing"},
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revision": "4b" * 20},
                },
            },
        },
    }
    _, _, out2 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_prog)
    assert out2 == "noop:non_terminal_health:Progressing"

    async with get_tenant_db_context(tenant_id) as db:
        deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_argo_006_presync_hook_skip():
    """
    Scenario TC-DEP-ARGO-006: ArgoCD PreSync Hook Skip (NOOP Exclusion).
    - hookType = 'PreSync' is skipped cleanly without inserting deployment rows.
    """
    tenant_id = await _register_tenant("argo-006")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-prod-006")

    payload_presync = {
        "hookType": "PreSync",
        "app": {
            "metadata": {"name": "cart-prod-006", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revision": "5a" * 20},
                },
            },
        },
    }
    status_code, event_id, outcome = await _send_and_process_argocd_webhook(
        integ_id, tenant_id, secret, payload_presync, event_type="presync"
    )
    assert status_code == 202
    assert outcome in (
        "noop:ignored_argocd_hook:PreSync",
        "noop:ignored_argocd_hook:presync",
    )

    async with get_tenant_db_context(tenant_id) as db:
        ev = (
            await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
        ).scalar_one()
        assert ev.status == "completed"
        deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_argo_007_staging_environment_exclusion():
    """
    Scenario TC-DEP-ARGO-007: ArgoCD Staging Environment Exclusion (NOOP Non-Production).
    - ArgoCD notifications targeting non-production environments are discarded cleanly.
    """
    tenant_id = await _register_tenant("argo-007")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id, app_name="cart-stage-007")

    payload_staging = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "cart-stage-007", "labels": {"environment": "staging"}},
            "spec": {"destination": {"namespace": "staging"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T11:55:00Z",
                    "syncResult": {"revision": "4d" * 20},
                },
            },
        },
    }
    status_code, event_id, outcome = await _send_and_process_argocd_webhook(
        integ_id, tenant_id, secret, payload_staging
    )
    assert status_code == 202
    assert outcome == "noop:non_production_environment:staging"

    async with get_tenant_db_context(tenant_id) as db:
        ev = (
            await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
        ).scalar_one()
        assert ev.status == "completed"
        deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_argo_008_fail_closed_webhook_authentication():
    """
    Scenario TC-DEP-ARGO-008: ArgoCD Fail-Closed Webhook Authentication (401 Unauthorized).
    - Missing signature, invalid signature, or missing integration secret returns 401.
    - Zero rows written to webhook_events or deployments.
    """
    tenant_id = await _register_tenant("argo-008")
    project_id = await _create_project(tenant_id)
    integ_id, secret = await _setup_argocd_context(tenant_id, project_id)

    body = json.dumps({"hookType": "PostSync", "phase": "Succeeded"}).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Missing signature header
        r1 = await ac.post(
            f"/api/v1/webhooks/integrations/{integ_id}",
            content=body,
            headers={"Content-Type": "application/json", "X-ArgoCD-Event": "postsync"},
        )
        assert r1.status_code == 401

        # 2. Invalid HMAC signature
        r2 = await ac.post(
            f"/api/v1/webhooks/integrations/{integ_id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-ArgoCD-Event": "postsync",
                "X-ArgoCD-Signature": "sha256=invalidhexbadsignature1234567890",
            },
        )
        assert r2.status_code == 401

    async with get_tenant_db_context(tenant_id) as db:
        events = (await db.execute(select(WebhookEvent))).scalars().all()
        assert len(events) == 0
        deps = (await db.execute(select(Deployment))).scalars().all()
        assert len(deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_argo_009_multi_strategy_project_resolution():
    """
    Scenario TC-DEP-ARGO-009: ArgoCD Deterministic Multi-Strategy Project Resolution.
    Verifies the 4 resolution tiers:
    1. ProjectLink (link_type='argocd_application', external_id=app_name)
    2. UUID label/annotation manta.project_id
    3. Slug label manta.project_slug
    4. Git repoURL matched against Repository.full_name
    """
    tenant_id = await _register_tenant("argo-009")
    secret = "argo_strat_secret"
    integ_id = uuid.uuid4()

    # Create 4 distinct projects for the 4 tiers
    p1 = await _create_project(tenant_id, name="Project Tier 1", slug="tier1-link")
    p2 = await _create_project(tenant_id, name="Project Tier 2", slug="tier2-uuid")
    p3 = await _create_project(tenant_id, name="Project Tier 3", slug="tier3-slug")
    p4 = await _create_project(tenant_id, name="Project Tier 4", slug="tier4-git")

    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Integration(
                id=integ_id,
                tenant_id=tenant_id,
                project_id=p1,
                provider="argocd",
                external_account_id="acct-strat",
                webhook_secret_hash=encryption_service.encrypt(secret),
                is_active=True,
            )
        )
        # Tier 1 Link
        db.add(
            ProjectLink(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=p1,
                integration_id=integ_id,
                link_type="argocd_application",
                external_id="app-linked-tier1",
            )
        )
        # Tier 4 Repo
        db.add(
            Repository(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=p4,
                external_repo_id=98765,
                full_name="acme/tier4-backend",
                default_branch="main",
                is_private=True,
            )
        )
        await db.commit()

    # Tier 1: Match by ProjectLink
    payload_t1 = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "app-linked-tier1", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T12:00:00Z",
                    "syncResult": {"revision": "a1" * 20},
                },
            },
        },
    }
    _, _, out1 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_t1)
    assert out1.startswith("persisted:")

    # Tier 2: Match by manta.project_id UUID
    payload_t2 = {
        "hookType": "PostSync",
        "app": {
            "metadata": {
                "name": "unlinked-app-2",
                "labels": {"environment": "production", "manta.project_id": str(p2)},
            },
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T12:05:00Z",
                    "syncResult": {"revision": "a2" * 20},
                },
            },
        },
    }
    _, _, out2 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_t2)
    assert out2.startswith("persisted:")

    # Tier 3: Match by manta.project_slug
    payload_t3 = {
        "hookType": "PostSync",
        "app": {
            "metadata": {
                "name": "unlinked-app-3",
                "labels": {"environment": "production", "manta.project_slug": "tier3-slug"},
            },
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T12:10:00Z",
                    "syncResult": {"revision": "a3" * 20},
                },
            },
        },
    }
    _, _, out3 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_t3)
    assert out3.startswith("persisted:")

    # Tier 4: Match by Git repoURL -> Repository.full_name
    payload_t4 = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "unlinked-app-4", "labels": {"environment": "production"}},
            "spec": {"source": {"repoURL": "https://github.com/acme/tier4-backend.git"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T12:15:00Z",
                    "syncResult": {"revision": "a4" * 20},
                },
            },
        },
    }
    _, _, out4 = await _send_and_process_argocd_webhook(integ_id, tenant_id, secret, payload_t4)
    assert out4.startswith("persisted:")

    async with get_tenant_db_context(tenant_id) as db:
        dep1 = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == "a1" * 20))
        ).scalar_one()
        assert dep1.project_id == p1

        dep2 = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == "a2" * 20))
        ).scalar_one()
        assert dep2.project_id == p2

        dep3 = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == "a3" * 20))
        ).scalar_one()
        assert dep3.project_id == p3

        dep4 = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == "a4" * 20))
        ).scalar_one()
        assert dep4.project_id == p4


# ---------------------------------------------------------------------------
# Section 5.3: Signal 3 - Direct Deployment REST API (`direct_deployment_api`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dep_dir_001_direct_rest_production_success(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-001: Direct REST Ingest Production SUCCESS.
    - POST /api/v1/projects/{project_id}/deployments with outcome='SUCCESS'.
    - Returns HTTP 201 Created with is_production = True, status = 'SUCCESS', is_change_failure = False.
    - Actor is pseudonymized; no raw email or name in response.
    """
    tenant_id = await _register_tenant("dir-001")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    commit_sha = "5e6f1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f"
    payload = {
        "commit_sha": commit_sha,
        "environment": "production",
        "outcome": "SUCCESS",
        "deployed_at": "2026-11-13T12:00:00Z",
        "external_deployment_id": "direct-run-8801",
        "actor": {
            "name": "Jane DevOps",
            "email": "jane.devops@acme.com",
            "handle": "janedev",
            "provider": "direct_deployment_api",
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["status"] == "SUCCESS"
    assert data["is_change_failure"] is False
    assert data["environment"] == "production"
    assert data["is_production"] is True
    assert data["authoritative_signal_source"] == "direct_deployment_api"
    assert data["is_authoritative_for_project"] is True
    assert data["external_deployment_id"] == "direct-run-8801"
    assert len(data["actor_pseudonym_id"]) == 64
    assert "jane.devops@acme.com" not in res.text
    assert "Jane DevOps" not in res.text


@pytest.mark.asyncio
async def test_tc_dep_dir_002_direct_rest_production_failed(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-002: Direct REST Ingest Production FAILED.
    - outcome = 'FAILED' records status = 'FAILED' and is_change_failure = True.
    """
    tenant_id = await _register_tenant("dir-002")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    payload = {
        "commit_sha": "6f1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f1a",
        "environment": "PROD",
        "outcome": "FAILED",
        "deployed_at": "2026-11-13T12:15:00Z",
        "external_deployment_id": "direct-run-8802",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["status"] == "FAILED"
    assert data["is_change_failure"] is True
    assert data["environment"] == "production"
    assert data["is_production"] is True


@pytest.mark.asyncio
async def test_tc_dep_dir_003_direct_rest_production_rolled_back(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-003: Direct REST Ingest Production ROLLED_BACK.
    - outcome = 'ROLLED_BACK' records status = 'ROLLED_BACK' and is_change_failure = True.
    """
    tenant_id = await _register_tenant("dir-003")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    payload = {
        "commit_sha": "7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b",
        "environment": "live",
        "outcome": "ROLLED_BACK",
        "deployed_at": "2026-11-13T12:30:00Z",
        "external_deployment_id": "direct-run-8803",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["status"] == "ROLLED_BACK"
    assert data["is_change_failure"] is True
    assert data["environment"] == "production"
    assert data["is_production"] is True


@pytest.mark.asyncio
async def test_tc_dep_dir_004_direct_rest_staging_dora_exclusion(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-004: Direct REST Staging Ingestion & DORA Query Boundary Exclusion.
    - Staging/dev/qa submissions return 201 with is_production = False.
    - Row is stored in deployments, but strictly excluded from list_authoritative_production_deployments.
    """
    tenant_id = await _register_tenant("dir-004")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    stg_payload = {
        "commit_sha": "8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c",
        "environment": "staging",
        "outcome": "SUCCESS",
        "deployed_at": "2026-11-13T12:45:00Z",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=stg_payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["environment"] == "staging"
    assert data["is_production"] is False

    async with get_tenant_db_context(tenant_id) as db:
        # Row exists in deployments table for audit
        all_deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(all_deps) == 1
        assert all_deps[0].environment == "staging"

        # Authoritative production query excludes staging row completely
        auth_deps = await list_authoritative_production_deployments(
            db, tenant_id=tenant_id, project_id=project_id
        )
        assert len(auth_deps) == 0


@pytest.mark.asyncio
async def test_tc_dep_dir_005_idempotent_replay_and_monotonic_guard(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-005: Direct REST Idempotent Replay & Monotonic Out-of-Order Guard.
    - Identical replay returns HTTP 200 OK with deduplicated = True and same ID.
    - Stale replay with older deployed_at does not overwrite newer result in database.
    """
    tenant_id = await _register_tenant("dir-005")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    # 1. First submission (FAILED at 13:00)
    d1_payload = {
        "commit_sha": "9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d",
        "environment": "production",
        "outcome": "FAILED",
        "deployed_at": "2026-11-13T13:00:00Z",
        "external_deployment_id": "run-500",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=d1_payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert r1.status_code == 201
        dep_id = r1.json()["id"]

        # 2. Identical replay -> HTTP 200 deduplicated
        r2 = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=d1_payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert r2.status_code == 200
        assert r2.json()["deduplicated"] is True
        assert r2.json()["id"] == dep_id

        # 3. Out-of-order stale update with older timestamp (12:50) -> discarded by monotonic guard
        stale_payload = {
            "commit_sha": "9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d",
            "environment": "production",
            "outcome": "SUCCESS",
            "deployed_at": "2026-11-13T12:50:00Z",
            "external_deployment_id": "run-500",
        }
        r3 = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=stale_payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert r3.status_code in (200, 201)

    async with get_tenant_db_context(tenant_id) as db:
        dep = (
            await db.execute(
                select(Deployment).where(Deployment.external_deployment_id == "run-500")
            )
        ).scalar_one()
        # Newer status FAILED must be preserved against older SUCCESS update
        assert dep.status == "FAILED"
        assert dep.is_change_failure is True


@pytest.mark.asyncio
async def test_tc_dep_dir_006_rbac_and_abac_permission_enforcement(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-006: Direct REST RBAC & Non-Admin 403 Denial.
    - Anonymous -> 401 Unauthorized.
    - DEVELOPER role -> 403 Forbidden (missing INGEST_PROJECT_DEPLOYMENTS).
    - User with role but unassigned on ABAC ProjectMember -> 403 Forbidden.
    - TENANT_ADMIN -> 201 Created.
    """
    from fastapi import HTTPException
    from app.auth.schemas import TokenClaims
    from app.deployments.service import _enforce_abac_project_assignment_if_non_admin

    tenant_id = await _register_tenant("dir-006")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")

    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])
    token_dev = _mint_jwt(_mock_auth_ctx, tenant_id, ["DEVELOPER"])

    valid_payload = {
        "commit_sha": "0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e",
        "environment": "production",
        "outcome": "SUCCESS",
        "deployed_at": "2026-11-13T13:10:00Z",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Anonymous -> 401
        r_unauth = await ac.post(f"/api/v1/projects/{project_id}/deployments", json=valid_payload)
        assert r_unauth.status_code == 401

        # 2. DEVELOPER -> 403 (missing INGEST_PROJECT_DEPLOYMENTS permission)
        r_dev = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=valid_payload,
            headers={"Authorization": f"Bearer {token_dev}"},
        )
        assert r_dev.status_code == 403

        # 3. TENANT_ADMIN -> 201
        r_admin = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=valid_payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert r_admin.status_code == 201

    # 4. Service-level ABAC check for human user not assigned to project
    user_sub = f"human-user-{uuid.uuid4().hex[:6]}"
    user_id = uuid.uuid4()
    async with get_tenant_db_context(tenant_id) as db:
        user_row = User(
            id=user_id,
            tenant_id=tenant_id,
            google_sub=user_sub,
            email=f"{user_sub}@example.com",
            full_name="Human PM",
        )
        db.add(user_row)

    claims = TokenClaims(sub=user_sub, tenant_id=str(tenant_id), roles=["PROJECT_MANAGER"])
    # Non-admin human user not in ProjectMember raises 403 "caller is not assigned to this project"
    async with get_tenant_db_context(tenant_id) as db:
        with pytest.raises(HTTPException) as exc_info:
            await _enforce_abac_project_assignment_if_non_admin(
                db, user=claims, tenant_id=tenant_id, project_id=project_id
            )
        assert exc_info.value.status_code == 403
        assert "not assigned to this project" in exc_info.value.detail

    # Assigned in ProjectMember succeeds without exception
    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            ProjectMember(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                role="PROJECT_MANAGER",
            )
        )

    async with get_tenant_db_context(tenant_id) as db:
        await _enforce_abac_project_assignment_if_non_admin(
            db, user=claims, tenant_id=tenant_id, project_id=project_id
        )


@pytest.mark.asyncio
async def test_tc_dep_dir_007_tenant_boundary_rls_isolation(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-007: Direct REST Tenant Boundary RLS Isolation.
    - Caller authenticated in Tenant A calling a Project belonging to Tenant B returns 404.
    """
    tenant_a = await _register_tenant("dir-007a")
    tenant_b = await _register_tenant("dir-007b")
    proj_b = await _create_project(tenant_b, authoritative_cd_signal="direct_deployment_api")

    token_a = _mint_jwt(_mock_auth_ctx, tenant_a, ["TENANT_ADMIN"])
    payload = {
        "commit_sha": "1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f",
        "environment": "production",
        "outcome": "SUCCESS",
        "deployed_at": "2026-11-13T13:20:00Z",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/projects/{proj_b}/deployments",
            json=payload,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_tc_dep_dir_008_schema_validation_rejection(_mock_auth_ctx):
    """
    Scenario TC-DEP-DIR-008: Direct REST Input Schema Validation Rejection.
    - Invalid commit SHA, blank environment, naive timestamp, or unsupported outcome fails with 422.
    """
    tenant_id = await _register_tenant("dir-008")
    project_id = await _create_project(tenant_id)
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    invalid_vectors = [
        # Invalid commit SHA
        {
            "commit_sha": "not-a-valid-sha",
            "environment": "prod",
            "outcome": "SUCCESS",
            "deployed_at": "2026-11-13T12:00:00Z",
        },
        # Blank environment
        {
            "commit_sha": "a" * 40,
            "environment": "   ",
            "outcome": "SUCCESS",
            "deployed_at": "2026-11-13T12:00:00Z",
        },
        # Naive timestamp missing tz offset
        {
            "commit_sha": "a" * 40,
            "environment": "prod",
            "outcome": "SUCCESS",
            "deployed_at": "2026-11-13T12:00:00",
        },
        # Invalid outcome enum
        {
            "commit_sha": "a" * 40,
            "environment": "prod",
            "outcome": "BUILDING",
            "deployed_at": "2026-11-13T12:00:00Z",
        },
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for vec in invalid_vectors:
            res = await ac.post(
                f"/api/v1/projects/{project_id}/deployments",
                json=vec,
                headers={"Authorization": f"Bearer {token_admin}"},
            )
            assert res.status_code == 422


# ---------------------------------------------------------------------------
# Section 5.4: Cross-Signal Ingestion & Authoritative DORA Metric Reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dep_dora_001_multi_signal_authoritative_isolation(_mock_auth_ctx):
    """
    Scenario TC-DEP-DORA-001: Multi-Signal Ingestion with Authoritative Filter Isolation.
    - Ingests deployments from GitHub, ArgoCD, and Direct API into the same project.
    - list_authoritative_production_deployments returns only the deployment matching
      Project.authoritative_cd_signal.
    """
    tenant_id = await _register_tenant("dora-001")
    # Project is configured with authoritative_cd_signal = 'argocd_postsync_webhook'
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")

    gh_integ_id, gh_secret = await _setup_github_context(tenant_id, project_id, ext_repo_id=20201)
    argo_integ_id, argo_secret = await _setup_argocd_context(
        tenant_id, project_id, app_name="argo-dora-001"
    )
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    # 1. Ingest GitHub deployment
    gh_sha = "1" * 40
    gh_payload = {
        "deployment_status": {
            "id": 9901,
            "state": "success",
            "environment": "production",
            "updated_at": "2026-11-13T14:00:00Z",
        },
        "deployment": {"id": 8901, "sha": gh_sha, "environment": "production"},
        "repository": {"id": 20201, "full_name": "acme/web-service"},
    }
    await _send_and_process_github_webhook(gh_integ_id, tenant_id, gh_secret, gh_payload)

    # 2. Ingest ArgoCD deployment
    argo_sha = "2" * 40
    argo_payload = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "argo-dora-001", "labels": {"environment": "production"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T14:05:00Z",
                    "syncResult": {"revision": argo_sha},
                },
            },
        },
    }
    await _send_and_process_argocd_webhook(argo_integ_id, tenant_id, argo_secret, argo_payload)

    # 3. Ingest Direct API deployment
    dir_sha = "3" * 40
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json={
                "commit_sha": dir_sha,
                "environment": "production",
                "outcome": "SUCCESS",
                "deployed_at": "2026-11-13T14:10:00Z",
            },
            headers={"Authorization": f"Bearer {token_admin}"},
        )

    async with get_tenant_db_context(tenant_id) as db:
        # All 3 exist in storage
        all_deps = (
            (await db.execute(select(Deployment).where(Deployment.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(all_deps) == 3

        # Authoritative query strictly isolates ArgoCD deployment
        auth_deps = await list_authoritative_production_deployments(
            db, tenant_id=tenant_id, project_id=project_id
        )
        assert len(auth_deps) == 1
        assert auth_deps[0].authoritative_signal_source == "argocd_postsync_webhook"
        assert auth_deps[0].commit_sha == argo_sha


@pytest.mark.asyncio
async def test_tc_dep_dora_002_dynamic_signal_switch(_mock_auth_ctx):
    """
    Scenario TC-DEP-DORA-002: Dynamic Re-Filtering on Authoritative Signal Switch.
    - When project.authoritative_cd_signal is switched, query dynamically reflects the new signal.
    - Historical deployment records are untouched.
    """
    tenant_id = await _register_tenant("dora-002")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")
    argo_integ_id, argo_secret = await _setup_argocd_context(
        tenant_id, project_id, app_name="argo-dora-002"
    )
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    argo_sha = "4" * 40
    await _send_and_process_argocd_webhook(
        argo_integ_id,
        tenant_id,
        argo_secret,
        {
            "hookType": "PostSync",
            "app": {
                "metadata": {"name": "argo-dora-002", "labels": {"environment": "production"}},
                "status": {
                    "health": {"status": "Healthy"},
                    "operationState": {
                        "phase": "Succeeded",
                        "finishedAt": "2026-11-13T14:20:00Z",
                        "syncResult": {"revision": argo_sha},
                    },
                },
            },
        },
    )

    dir_sha = "5" * 40
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json={
                "commit_sha": dir_sha,
                "environment": "production",
                "outcome": "SUCCESS",
                "deployed_at": "2026-11-13T14:25:00Z",
            },
            headers={"Authorization": f"Bearer {token_admin}"},
        )

    # Phase 1: argocd is authoritative
    async with get_tenant_db_context(tenant_id) as db:
        auth_1 = await list_authoritative_production_deployments(
            db, tenant_id=tenant_id, project_id=project_id
        )
        assert len(auth_1) == 1
        assert auth_1[0].commit_sha == argo_sha

    # Switch project authoritative signal to direct_deployment_api
    async with get_tenant_db_context(tenant_id) as db:
        proj = await db.get(Project, project_id)
        assert proj is not None
        proj.authoritative_cd_signal = "direct_deployment_api"

    # Phase 2: direct_deployment_api is authoritative
    async with get_tenant_db_context(tenant_id) as db:
        auth_2 = await list_authoritative_production_deployments(
            db, tenant_id=tenant_id, project_id=project_id
        )
        assert len(auth_2) == 1
        assert auth_2[0].commit_sha == dir_sha


# ---------------------------------------------------------------------------
# Section 5.5: GDPR Privacy, Contributor Pseudonymization & Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dep_priv_001_actor_pii_isolation(_mock_auth_ctx):
    """
    Scenario TC-DEP-PRIV-001: Actor PII Isolation & No PII Leakage in API Responses.
    - Zero raw PII in deployments table or Direct API response models.
    - actor_pseudonym_id is 64 hex characters.
    """
    tenant_id = await _register_tenant("priv-001")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="direct_deployment_api")
    token_admin = _mint_jwt(_mock_auth_ctx, tenant_id, ["TENANT_ADMIN"])

    payload = {
        "commit_sha": "6" * 40,
        "environment": "production",
        "outcome": "SUCCESS",
        "deployed_at": "2026-11-13T14:30:00Z",
        "actor": {
            "name": "Super Secret Agent",
            "email": "agent007@mi6.gov.uk",
            "handle": "agent007",
            "provider": "direct_deployment_api",
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/projects/{project_id}/deployments",
            json=payload,
            headers={"Authorization": f"Bearer {token_admin}"},
        )
    assert res.status_code == 201
    body = res.json()
    assert "agent007@mi6.gov.uk" not in res.text
    assert "Super Secret Agent" not in res.text
    assert len(body["actor_pseudonym_id"]) == 64

    async with get_tenant_db_context(tenant_id) as db:
        dep = (
            await db.execute(select(Deployment).where(Deployment.commit_sha == "6" * 40))
        ).scalar_one()
        assert dep.actor_pseudonym_id == body["actor_pseudonym_id"]
        assert dep.triggered_by is None


@pytest.mark.asyncio
async def test_tc_dep_priv_002_concurrent_actor_resolution_savepoint_resilience():
    """
    Scenario TC-DEP-PRIV-002: Concurrent Actor Resolution Savepoint Collision Resilience.
    - 5 concurrent resolve_or_create_contributor_pseudonym transactions for the same actor
      succeed without UniqueViolation, returning identical pseudonym ID.
    """
    tenant_id = await _register_tenant("priv-002")

    async def _resolve_in_own_tx():
        async with get_tenant_db_context(tenant_id) as db:
            pseudo = await resolve_or_create_contributor_pseudonym(
                db,
                tenant_id,
                "github",
                "concurrent-actor-99",
                raw_email="race@example.com",
                raw_handle="raceuser",
            )
            await db.commit()
            return pseudo

    results = await asyncio.gather(*[_resolve_in_own_tx() for _ in range(5)])
    assert len(set(results)) == 1
    assert len(results[0]) == 64


# ---------------------------------------------------------------------------
# Section 5.6: Out-of-Order Commit SHA & Schema Integrity Constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dep_ord_001_out_of_order_commit_sha_relaxed_fk():
    """
    Scenario TC-DEP-ORD-001: Out-of-Order Deployment Ingestion Prior to Git Commit Mirroring.
    - Deployments can be inserted with commit SHA not present in commits table.
    """
    tenant_id = await _register_tenant("ord-001")
    project_id = await _create_project(tenant_id)
    now = datetime.now(timezone.utc)

    async with get_tenant_db_context(tenant_id) as db:
        dep = Deployment(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            environment="production",
            commit_sha="0000000000000000000000000000000000000001",  # Not in commits
            status="SUCCESS",
            is_change_failure=False,
            authoritative_signal_source="direct_deployment_api",
            deployed_at=now,
        )
        db.add(dep)
        await db.flush()

        persisted = await db.get(Deployment, dep.id)
        assert persisted is not None
        assert persisted.commit_sha == "0000000000000000000000000000000000000001"


@pytest.mark.asyncio
async def test_tc_dep_ord_002_db_check_constraints_rejection():
    """
    Scenario TC-DEP-ORD-002: Database CHECK Constraint Rejection of Invalid Status or Signal Source.
    - ck_deployments_status rejects invalid status enum.
    - ck_deployments_signal_source rejects invalid signal source enum.
    - fk_deployments_tenant_actor_pseudonym rejects invalid actor pseudonym FK.
    """
    tenant_id = await _register_tenant("ord-002")
    project_id = await _create_project(tenant_id)
    now = datetime.now(timezone.utc)

    # 1. Invalid status rejected by ck_deployments_status
    with pytest.raises(IntegrityError):
        async with get_tenant_db_context(tenant_id) as db:
            db.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    environment="production",
                    commit_sha="7" * 40,
                    status="PENDING",
                    is_change_failure=False,
                    authoritative_signal_source="github_deployments_api",
                    deployed_at=now,
                )
            )
            await db.flush()

    # 2. Invalid signal source rejected by ck_deployments_signal_source
    with pytest.raises(IntegrityError):
        async with get_tenant_db_context(tenant_id) as db:
            db.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    environment="production",
                    commit_sha="8" * 40,
                    status="SUCCESS",
                    is_change_failure=False,
                    authoritative_signal_source="gitlab_ci",
                    deployed_at=now,
                )
            )
            await db.flush()

    # 3. Unknown actor_pseudonym_id rejected by composite FK
    with pytest.raises(IntegrityError):
        async with get_tenant_db_context(tenant_id) as db:
            db.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    environment="production",
                    commit_sha="9" * 40,
                    status="SUCCESS",
                    is_change_failure=False,
                    authoritative_signal_source="github_deployments_api",
                    actor_pseudonym_id="deadbeef" * 8,
                    deployed_at=now,
                )
            )
            await db.flush()
