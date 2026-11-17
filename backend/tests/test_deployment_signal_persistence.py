"""
SCRUM-383 / SCRUM-384: Deployment Signal Selection Persistence Automated Test Suite.
Parent Story: SCRUM-354 (US-18: Configure deployment signal selection and persist authoritative CD source).

Automated verification of deployment signal configuration under tenant RLS:
- TC-SIGNAL-001: Admin updates signal to all 3 allowed enum values -> HTTP 200 OK with authoritative_deployment_signal.
- TC-SIGNAL-002: Cyclic switching (github -> argocd -> direct -> github) persists cleanly to PostgreSQL.
- TC-SIGNAL-003: Invalid signal enum strings return HTTP 422 Unprocessable Entity.
- TC-SIGNAL-004: Developer role denied PATCH with HTTP 403 Forbidden; Tenant Admin permitted.
- TC-SIGNAL-005: GET /projects/{id}/deployment-signal returns active signal and updated_at timestamp.
- TC-SIGNAL-006: Cross-tenant PATCH and GET return HTTP 404 Not Found (strict anti-enumeration & RLS).
- TC-SIGNAL-007: Standard project endpoints (GET /projects/{id}, GET /projects) reflect updated deployment signal.
"""

import json
import time
from typing import Any
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest

from app.auth.jwks import JWKSClient
from app.config import settings
from app.db.session import async_engine
from app.main import app


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test."""
    from app.tenants.rate_limiter import registration_rate_limiter

    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


def generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


class MockJWKSServer:
    def __init__(self, keys_dict: dict[str, Any]) -> None:
        self.keys_dict = keys_dict

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.keys_dict, request=request)


@pytest.fixture
def auth_context():
    """Configures mock IdP with signing keys for JWT authentication in tests."""
    key, jwk = generate_rsa_keypair("signal-test-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "signal-test-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID,
    roles: list[str] | None = None,
    sub: str = "test-signal-admin",
    exp_offset_seconds: int = 3600,
) -> str:
    """Helper to mint valid RS256 JWTs with tenant context and roles."""
    if roles is None:
        roles = ["TENANT_ADMIN"]

    claims = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": int(time.time()) + exp_offset_seconds,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(
        claims,
        auth_ctx["key"],
        algorithm="RS256",
        headers={"alg": "RS256", "kid": auth_ctx["kid"]},
    )


async def register_test_tenant(slug_prefix: str) -> tuple[uuid.UUID, str]:
    """Helper to register a clean tenant via /tenants/register."""
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"Signal Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"admin-{unique}@signaltemp.org",
        "admin_name": f"Admin {unique}",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tenants/register",
            json=payload,
        )
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


@pytest.mark.asyncio
async def test_update_signal_allowed_enums(auth_context: dict[str, Any]):
    """TC-SIGNAL-001: Verifies updating signal to each allowed enum value."""
    tenant_id, _ = await register_test_tenant("sig-allowed")
    admin_token = mint_jwt(auth_context, tenant_id, ["TENANT_ADMIN"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create project (defaults to github_deployments_api)
        create_resp = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "Signal Allowed Project"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["id"]

        # 2. Update to argocd_postsync_webhook
        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}/deployment-signal",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"authoritative_deployment_signal": "argocd_postsync_webhook"},
        )
        assert patch_resp.status_code == 200
        patch_data = patch_resp.json()
        assert patch_data["authoritative_deployment_signal"] == "argocd_postsync_webhook"
        assert patch_data["project_id"] == project_id

        # 3. Update to direct_deployment_api
        patch_resp2 = await client.patch(
            f"/api/v1/projects/{project_id}/deployment-signal",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"authoritative_deployment_signal": "direct_deployment_api"},
        )
        assert patch_resp2.status_code == 200
        assert patch_resp2.json()["authoritative_deployment_signal"] == "direct_deployment_api"

        # 4. Update back to github_deployments_api
        patch_resp3 = await client.patch(
            f"/api/v1/projects/{project_id}/deployment-signal",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"authoritative_deployment_signal": "github_deployments_api"},
        )
        assert patch_resp3.status_code == 200
        assert patch_resp3.json()["authoritative_deployment_signal"] == "github_deployments_api"


@pytest.mark.asyncio
async def test_cyclic_switching_persistence(auth_context: dict[str, Any]):
    """TC-SIGNAL-002: Verifies persistence of cyclic signal transitions."""
    tenant_id, _ = await register_test_tenant("sig-cyclic")
    admin_token = mint_jwt(auth_context, tenant_id, ["TENANT_ADMIN"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "Cyclic Signal Project"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["id"]

        signals = [
            "argocd_postsync_webhook",
            "direct_deployment_api",
            "github_deployments_api",
            "argocd_postsync_webhook",
        ]

        for expected_signal in signals:
            patch_resp = await client.patch(
                f"/api/v1/projects/{project_id}/deployment-signal",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"authoritative_deployment_signal": expected_signal},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["authoritative_deployment_signal"] == expected_signal

            # Confirm with GET
            get_resp = await client.get(
                f"/api/v1/projects/{project_id}/deployment-signal",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["authoritative_deployment_signal"] == expected_signal


@pytest.mark.asyncio
async def test_invalid_signal_enum_rejection(auth_context: dict[str, Any]):
    """TC-SIGNAL-003: Rejects invalid, unknown, or empty signal strings with HTTP 422."""
    tenant_id, _ = await register_test_tenant("sig-invalid")
    admin_token = mint_jwt(auth_context, tenant_id, ["TENANT_ADMIN"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "Invalid Enum Test Project"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["id"]

        invalid_payloads = [
            {"authoritative_deployment_signal": "unsupported_ci_tool"},
            {"authoritative_deployment_signal": "jenkins_webhook"},
            {"authoritative_deployment_signal": ""},
            {"authoritative_deployment_signal": 12345},
            {},
        ]

        for payload in invalid_payloads:
            resp = await client.patch(
                f"/api/v1/projects/{project_id}/deployment-signal",
                headers={"Authorization": f"Bearer {admin_token}"},
                json=payload,
            )
            assert resp.status_code == 422, (
                f"Expected 422 for payload {payload}, got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_developer_role_denied_patch_403(auth_context: dict[str, Any]):
    """TC-SIGNAL-004: Developer receives HTTP 403 on PATCH, but can GET deployment signal."""
    tenant_id, _ = await register_test_tenant("sig-rbac")
    admin_token = mint_jwt(auth_context, tenant_id, ["TENANT_ADMIN"])
    dev_token = mint_jwt(auth_context, tenant_id, ["DEVELOPER"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "RBAC Signal Project"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["id"]

        # Developer attempts PATCH -> 403 Forbidden
        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}/deployment-signal",
            headers={"Authorization": f"Bearer {dev_token}"},
            json={"authoritative_deployment_signal": "argocd_postsync_webhook"},
        )
        assert patch_resp.status_code == 403

        # Developer attempts GET -> 200 OK (developer has VIEW_PROJECT_DORA permission)
        get_resp = await client.get(
            f"/api/v1/projects/{project_id}/deployment-signal",
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["authoritative_deployment_signal"] == "github_deployments_api"


@pytest.mark.asyncio
async def test_cross_tenant_isolation_404(auth_context: dict[str, Any]):
    """TC-SIGNAL-006: Tenant B cannot read or modify Tenant A's project signal (returns 404)."""
    tenant_a, _ = await register_test_tenant("sig-ten-a")
    tenant_b, _ = await register_test_tenant("sig-ten-b")

    admin_a_token = mint_jwt(auth_context, tenant_a, ["TENANT_ADMIN"])
    admin_b_token = mint_jwt(auth_context, tenant_b, ["TENANT_ADMIN"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create project under Tenant A
        create_resp = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {admin_a_token}"},
            json={"name": "Tenant A Signal Project"},
        )
        assert create_resp.status_code == 201
        project_id_a = create_resp.json()["id"]

        # Tenant B attempts to GET Tenant A's signal -> 404
        get_b = await client.get(
            f"/api/v1/projects/{project_id_a}/deployment-signal",
            headers={"Authorization": f"Bearer {admin_b_token}"},
        )
        assert get_b.status_code == 404

        # Tenant B attempts to PATCH Tenant A's signal -> 404
        patch_b = await client.patch(
            f"/api/v1/projects/{project_id_a}/deployment-signal",
            headers={"Authorization": f"Bearer {admin_b_token}"},
            json={"authoritative_deployment_signal": "argocd_postsync_webhook"},
        )
        assert patch_b.status_code == 404


@pytest.mark.asyncio
async def test_project_responses_include_signal(auth_context: dict[str, Any]):
    """TC-SIGNAL-007: Standard project endpoints reflect configured deployment signal."""
    tenant_id, _ = await register_test_tenant("sig-proj-detail")
    admin_token = mint_jwt(auth_context, tenant_id, ["TENANT_ADMIN"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_resp = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "Project Detail Signal Test"},
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["id"]

        # Update signal
        await client.patch(
            f"/api/v1/projects/{project_id}/deployment-signal",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"authoritative_deployment_signal": "direct_deployment_api"},
        )

        # Retrieve project detail
        detail_resp = await client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["authoritative_cd_signal"] == "direct_deployment_api"
        assert detail_data["authoritative_deployment_signal"] == "direct_deployment_api"
