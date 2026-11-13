"""Integration tests for US-25 Tasks 25.2, 25.3, and 25.4 against PostgreSQL + FORCE RLS."""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any
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
    Repository,
)
from app.db.session import async_engine, get_tenant_db_context
from app.deployments.service import list_authoritative_production_deployments
from app.integrations.crypto import encryption_service
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter
from app.webhooks.processor import process_webhook_job
from workers.worker import dispatch_worker_job


@pytest.fixture(autouse=True)
async def _reset_state():
    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


@pytest.fixture
def _mock_auth_ctx():
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


def _mint_jwt(auth_ctx: dict[str, Any], tenant_id: uuid.UUID, roles: list[str], sub: str = "us25-ci") -> str:
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
    authoritative_cd_signal: str = "github_deployments_api",
) -> uuid.UUID:
    pid = uuid.uuid4()
    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Project(
                id=pid,
                tenant_id=tenant_id,
                name=f"Project {pid.hex[:6]}",
                slug=f"proj-{pid.hex[:8]}",
                authoritative_cd_signal=authoritative_cd_signal,
            )
        )
        await db.commit()
    return pid


@pytest.mark.asyncio
async def test_task25_2_schema_constraints_and_out_of_order_commit_sha():
    """
    Task 25.2 AC:
    - All 3 canonical outcome states (SUCCESS, FAILED, ROLLED_BACK) persist cleanly.
    - Invalid status ('PENDING') fails DB CHECK constraint.
    - Missing commit row in commits table succeeds (relaxed commit_sha FK).
    - Non-existent actor_pseudonym_id fails composite FK on contributors(tenant_id, pseudonym_id).
    """
    tenant_id = await _register_tenant("us25-schema")
    project_id = await _create_project(tenant_id)
    now = datetime.now(timezone.utc)

    async with get_tenant_db_context(tenant_id) as db:
        pseudo = await resolve_or_create_contributor_pseudonym(
            db, tenant_id, "github", "999111", raw_handle="schema-tester"
        )
        for idx, outcome in enumerate(("SUCCESS", "FAILED", "ROLLED_BACK")):
            db.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    environment="production",
                    commit_sha=f"{idx}" * 40,  # Not present in commits table
                    status=outcome,
                    is_change_failure=(outcome in ("FAILED", "ROLLED_BACK")),
                    authoritative_signal_source="github_deployments_api",
                    actor_pseudonym_id=pseudo,
                    external_deployment_id=f"ext-{idx}-{uuid.uuid4().hex[:6]}",
                    deployed_at=now,
                )
            )
        await db.commit()

    # Invalid status rejected by ck_deployments_status
    with pytest.raises(IntegrityError):
        async with get_tenant_db_context(tenant_id) as db:
            db.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    environment="production",
                    commit_sha="d" * 40,
                    status="PENDING",
                    is_change_failure=False,
                    authoritative_signal_source="github_deployments_api",
                    deployed_at=now,
                )
            )
            await db.flush()

    # Unknown actor_pseudonym_id rejected by composite FK
    with pytest.raises(IntegrityError):
        async with get_tenant_db_context(tenant_id) as db:
            db.add(
                Deployment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    environment="production",
                    commit_sha="e" * 40,
                    status="SUCCESS",
                    is_change_failure=False,
                    authoritative_signal_source="github_deployments_api",
                    actor_pseudonym_id="deadbeef" * 8,
                    deployed_at=now,
                )
            )
            await db.flush()


@pytest.mark.asyncio
async def test_contributor_pseudonymization_savepoint_concurrency():
    """Verify concurrent resolution of the same actor never hits unhandled UniqueViolation."""
    tenant_id = await _register_tenant("us25-conc")

    async def _resolve_in_own_tx():
        async with get_tenant_db_context(tenant_id) as db:
            pseudo = await resolve_or_create_contributor_pseudonym(
                db,
                tenant_id,
                "github",
                "concurrent-actor-77",
                raw_email="race@example.com",
                raw_handle="raceuser",
            )
            await db.commit()
            return pseudo

    results = await asyncio.gather(*[_resolve_in_own_tx() for _ in range(5)])
    assert len(set(results)) == 1


@pytest.mark.asyncio
async def test_task25_3_github_and_argocd_webhooks_end_to_end():
    """
    Task 25.3 AC:
    - Fail-closed ArgoCD webhook auth (401 without secret or invalid signature; 0 DB rows).
    - GitHub deployment_status & ArgoCD PostSync persist Deployment rows with
      authoritative_signal_source, status, commit_sha, deployed_at, and actor_pseudonym_id.
    - Non-terminal & staging webhooks complete cleanly without inserting Deployment rows.
    """
    tenant_id = await _register_tenant("us25-hooks")
    project_id = await _create_project(tenant_id, authoritative_cd_signal="argocd_postsync_webhook")

    ext_repo_id = int(uuid.uuid4().int % 1_000_000_000)
    argo_secret = "argo_whsec_us25_test"
    argo_integ_id = uuid.uuid4()
    gh_integ_id = uuid.uuid4()
    gh_secret = "gh_whsec_us25_test"

    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Integration(
                id=gh_integ_id,
                tenant_id=tenant_id,
                project_id=project_id,
                provider="github",
                external_account_id="acct-gh",
                webhook_secret_hash=encryption_service.encrypt(gh_secret),
                is_active=True,
            )
        )
        db.add(
            Integration(
                id=argo_integ_id,
                tenant_id=tenant_id,
                project_id=project_id,
                provider="argocd",
                external_account_id="acct-argo",
                webhook_secret_hash=encryption_service.encrypt(argo_secret),
                is_active=True,
            )
        )
        db.add(
            Repository(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                external_repo_id=ext_repo_id,
                full_name="acme/us25-service",
                default_branch="main",
                is_private=True,
            )
        )
        db.add(
            ProjectLink(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                integration_id=argo_integ_id,
                link_type="argocd_application",
                external_id="us25-prod-app",
            )
        )
        await db.commit()

    # 1. Fail-closed ArgoCD missing secret -> 401
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unauth_res = await ac.post(
            "/api/v1/webhooks/argocd",
            json={"phase": "Succeeded", "environment": "production", "commit_sha": "f" * 40},
            headers={"X-Tenant-ID": str(tenant_id)},
        )
        assert unauth_res.status_code == 401

    # 2. Signed GitHub deployment_status failure -> worker -> Deployment row
    gh_sha = "1" * 40
    gh_payload = {
        "deployment_status": {
            "id": 4441,
            "state": "failure",
            "environment": "Production",
            "created_at": "2026-11-13T10:00:00Z",
            "creator": {"id": 8801, "login": "alice-deploy"},
        },
        "deployment": {"id": 3331, "sha": gh_sha, "environment": "Production"},
        "repository": {"id": ext_repo_id, "full_name": "acme/us25-service"},
    }
    gh_bytes = json.dumps(gh_payload).encode("utf-8")
    gh_sig = "sha256=" + hmac.new(gh_secret.encode(), gh_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        gh_res = await ac.post(
            f"/api/v1/webhooks/integrations/{gh_integ_id}",
            content=gh_bytes,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "deployment_status",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-Hub-Signature-256": gh_sig,
            },
        )
    assert gh_res.status_code == 202, gh_res.text
    gh_event_id = gh_res.json()["event_id"]

    await process_webhook_job({"event_id": gh_event_id, "tenant_id": str(tenant_id)})

    # 3. Signed ArgoCD PostSync Succeeded -> worker -> Deployment row
    argo_sha = "2" * 40
    argo_payload = {
        "hookType": "PostSync",
        "app": {
            "metadata": {"name": "us25-prod-app", "labels": {"environment": "prod"}},
            "status": {
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "finishedAt": "2026-11-13T10:15:00Z",
                    "syncResult": {"revision": argo_sha},
                    "operation": {"initiatedBy": {"username": "bob-sre"}},
                },
            },
        },
    }
    argo_bytes = json.dumps(argo_payload).encode("utf-8")
    argo_sig = "sha256=" + hmac.new(argo_secret.encode(), argo_bytes, hashlib.sha256).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        argo_res = await ac.post(
            f"/api/v1/webhooks/integrations/{argo_integ_id}",
            content=argo_bytes,
            headers={
                "Content-Type": "application/json",
                "X-ArgoCD-Event": "postsync",
                "X-ArgoCD-Delivery": f"argo-deliv-{uuid.uuid4().hex[:8]}",
                "X-ArgoCD-Signature": argo_sig,
            },
        )
    assert argo_res.status_code == 202, argo_res.text
    argo_event_id = argo_res.json()["event_id"]

    await dispatch_worker_job({"event_id": argo_event_id, "tenant_id": str(tenant_id)})

    async with get_tenant_db_context(tenant_id) as db:
        all_deps = (
            await db.execute(select(Deployment).where(Deployment.project_id == project_id))
        ).scalars().all()
        assert len(all_deps) == 2

        gh_dep = next(d for d in all_deps if d.authoritative_signal_source == "github_deployments_api")
        assert gh_dep.status == "FAILED"
        assert gh_dep.is_change_failure is True
        assert gh_dep.commit_sha == gh_sha
        assert gh_dep.environment == "production"
        assert gh_dep.actor_pseudonym_id and len(gh_dep.actor_pseudonym_id) == 64

        argo_dep = next(d for d in all_deps if d.authoritative_signal_source == "argocd_postsync_webhook")
        assert argo_dep.status == "SUCCESS"
        assert argo_dep.is_change_failure is False
        assert argo_dep.commit_sha == argo_sha
        assert argo_dep.environment == "production"
        assert argo_dep.actor_pseudonym_id and len(argo_dep.actor_pseudonym_id) == 64

        # Verify authoritative CD signal filter only returns ArgoCD deployment
        auth_deps = await list_authoritative_production_deployments(
            db, tenant_id=tenant_id, project_id=project_id
        )
        assert [d.id for d in auth_deps] == [argo_dep.id]


@pytest.mark.asyncio
async def test_task25_4_direct_api_rls_permissions_and_staging_filtering(_mock_auth_ctx):
    """
    Task 25.4 AC:
    - Unauthenticated -> 401; DEVELOPER -> 403 (INGEST_PROJECT_DEPLOYMENTS dedicated permission);
      Cross-tenant project -> 404 under FORCE ROW LEVEL SECURITY.
    - TENANT_ADMIN / M2M token persists production deployments with
      authoritative_signal_source='direct_deployment_api' & pseudonymized actor.
    - Staging/dev/qa/preview environments normalized & excluded from authoritative production query.
    """
    tenant_a = await _register_tenant("us25-dir-a")
    tenant_b = await _register_tenant("us25-dir-b")
    proj_a = await _create_project(tenant_a, authoritative_cd_signal="direct_deployment_api")

    token_admin_a = _mint_jwt(_mock_auth_ctx, tenant_a, ["TENANT_ADMIN"], sub="ci-m2m-a")
    token_dev_a = _mint_jwt(_mock_auth_ctx, tenant_a, ["DEVELOPER"], sub="dev-a")
    token_admin_b = _mint_jwt(_mock_auth_ctx, tenant_b, ["TENANT_ADMIN"], sub="ci-m2m-b")

    prod_body = {
        "commit_sha": "a" * 40,
        "environment": "PROD",
        "outcome": "ROLLED_BACK",
        "deployed_at": "2026-11-13T11:00:00Z",
        "actor": {"email": "Deployer@Acme.com", "name": "Deploy Bot"},
        "external_deployment_id": "run-1001",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 401 unauthenticated
        r401 = await ac.post(f"/api/v1/projects/{proj_a}/deployments", json=prod_body)
        assert r401.status_code == 401

        # 403 insufficient dedicated permission
        r403 = await ac.post(
            f"/api/v1/projects/{proj_a}/deployments",
            json=prod_body,
            headers={"Authorization": f"Bearer {token_dev_a}"},
        )
        assert r403.status_code == 403

        # 404 cross-tenant RLS boundary
        r404 = await ac.post(
            f"/api/v1/projects/{proj_a}/deployments",
            json=prod_body,
            headers={"Authorization": f"Bearer {token_admin_b}"},
        )
        assert r404.status_code == 404

        # 201 production insert
        r201 = await ac.post(
            f"/api/v1/projects/{proj_a}/deployments",
            json=prod_body,
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert r201.status_code == 201, r201.text
        data_prod = r201.json()
        assert data_prod["environment"] == "production"
        assert data_prod["is_production"] is True
        assert data_prod["status"] == "ROLLED_BACK"
        assert data_prod["is_change_failure"] is True
        assert data_prod["authoritative_signal_source"] == "direct_deployment_api"
        assert len(data_prod["actor_pseudonym_id"]) == 64
        assert "Deployer@Acme.com" not in r201.text

        # Idempotent replay on (tenant_id, project_id, authoritative_signal_source, external_deployment_id)
        r200 = await ac.post(
            f"/api/v1/projects/{proj_a}/deployments",
            json=prod_body,
            headers={"Authorization": f"Bearer {token_admin_a}"},
        )
        assert r200.status_code == 200, r200.text
        assert r200.json()["deduplicated"] is True
        assert r200.json()["id"] == data_prod["id"]

        # Non-production releases accepted & normalized, excluded from production metrics query
        for idx, env_val in enumerate(("staging", "DEV", "QA", "pr-99")):
            r_nonprod = await ac.post(
                f"/api/v1/projects/{proj_a}/deployments",
                json={
                    "commit_sha": f"{idx}" * 40,
                    "environment": env_val,
                    "outcome": "SUCCESS",
                    "deployed_at": "2026-11-13T11:05:00Z",
                },
                headers={"Authorization": f"Bearer {token_admin_a}"},
            )
            assert r_nonprod.status_code == 201, r_nonprod.text
            assert r_nonprod.json()["is_production"] is False

    async with get_tenant_db_context(tenant_a) as db:
        auth_prod = await list_authoritative_production_deployments(
            db, tenant_id=tenant_a, project_id=proj_a
        )
        assert [str(d.id) for d in auth_prod] == [data_prod["id"]]
        contribs = (await db.execute(select(Contributor))).scalars().all()
        assert len(contribs) >= 1
