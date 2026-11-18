"""
SCRUM-378 / US-17: Test Suite for GitHub Repository URL Validation & Integration Linkage Service.

Acceptance Criteria Verified:
- Valid GitHub URLs (HTTPS, SSH, slugs) normalize cleanly to 'owner/repo' and link to active Integration.
- Malformed URLs or non-GitHub hosts return HTTP 422 Unprocessable Entity.
- Missing integration_id returns HTTP 404 Not Found.
- Inactive integration returns HTTP 400 Bad Request.
- Tenant without any active GitHub integration returns HTTP 400 Bad Request.
- Cross-tenant integration isolation: Tenant B cannot use Tenant A's integration (HTTP 404).
"""

import json
from typing import Any
import uuid
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.jwks import JWKSClient
from app.config import settings
from app.db.models.models import Integration
from app.db.session import async_engine, get_tenant_db_context
from app.integrations.service import save_or_update_integration
from app.main import app
from app.projects.validators import normalize_and_validate_github_repo


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


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test."""
    from app.tenants.rate_limiter import registration_rate_limiter

    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


@pytest.fixture
def auth_context():
    """Configures mock IdP with signing keys for JWT authentication in tests."""
    key, jwk = generate_rsa_keypair("val-test-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "val-test-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict,
    tenant_id: uuid.UUID,
    roles: list[str],
    sub: str = "admin-user",
) -> str:
    claims = {
        "sub": sub,
        "tenant_id": str(tenant_id),
        "email": f"{sub}@manta.local",
        "realm_access": {"roles": roles},
        "iss": "http://keycloak:8080/realms/manta",
        "aud": settings.OIDC_AUDIENCE,
        "exp": 9999999999,
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
        "name": f"Org {slug_prefix} {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"admin@{slug_prefix}-{unique}.io",
        "admin_name": f"Admin {unique}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/tenants/register", json=payload)
        assert res.status_code == 201, f"Failed to register test tenant: {res.text}"
        data = res.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


# ==============================================================================
# UNIT TESTS: REPOSITORY IDENTIFIER NORMALIZATION & VALIDATION
# ==============================================================================


def test_validator_normalizes_valid_github_urls():
    """Valid URLs in various formats normalize cleanly to 'owner/repo'."""
    cases = [
        ("https://github.com/manta-org/manta-backend", "manta-org/manta-backend"),
        ("https://github.com/manta-org/manta-backend.git", "manta-org/manta-backend"),
        ("https://github.com/manta-org/manta-backend/", "manta-org/manta-backend"),
        ("http://github.com/manta-org/manta-backend", "manta-org/manta-backend"),
        ("git@github.com:manta-org/manta-backend.git", "manta-org/manta-backend"),
        ("git@github.com:manta-org/manta-backend", "manta-org/manta-backend"),
        ("manta-org/manta-backend", "manta-org/manta-backend"),
        ("manta-org/manta-backend.git", "manta-org/manta-backend"),
        ("octocat/Hello-World", "octocat/Hello-World"),
        ("user_123/repo.with.dots", "user_123/repo.with.dots"),
    ]
    for raw, expected in cases:
        normalized, owner, repo = normalize_and_validate_github_repo(raw)
        assert normalized == expected, f"Failed for {raw}: got {normalized}"
        assert f"{owner}/{repo}" == expected


def test_validator_rejects_non_github_and_malformed():
    """Non-GitHub hosts and malformed strings raise HTTP 422."""
    from fastapi import HTTPException

    invalid_cases = [
        "https://gitlab.com/manta-org/manta-backend",
        "https://bitbucket.org/manta-org/manta-backend",
        "https://custom-git.internal/org/repo",
        "not-a-repo",
        "https://github.com/",
        "https://github.com/onlyowner",
        "https://github.com/owner/repo/extra/path",
        "owner/repo/extra",
        "",
        "   ",
        "https://github.com/owner/.",
        "https://github.com/owner/..",
    ]
    for raw in invalid_cases:
        with pytest.raises(HTTPException) as exc_info:
            normalize_and_validate_github_repo(raw)
        assert exc_info.value.status_code == 422, f"Expected 422 for {raw}"


# ==============================================================================
# INTEGRATION TESTS: REPOSITORY LINKING ENDPOINT & INTEGRATION VALIDATION
# ==============================================================================


@pytest.mark.asyncio
async def test_link_repo_with_url_and_slug_formats(auth_context):
    """TC-SCRUM-378-001: Links repository using https URL, SSH URL, and slug formats."""
    tenant_id, _ = await register_test_tenant("val-link")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Connect active GitHub integration
    integ = await save_or_update_integration(
        tenant_id=tenant_id,
        provider="github",
        external_account_id="manta-val-org",
        raw_webhook_secret="whsec_test_secret_378",
        api_credentials={"access_token": "ghp_val_token"},
        base_url="http://test",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create project
        p_res = await ac.post(
            "/api/v1/projects",
            json={"name": "Validation Project", "slug": "val-proj"},
            headers=admin_headers,
        )
        assert p_res.status_code == 201
        project_id = p_res.json()["id"]

        # Link repo via full HTTPS URL
        res_url = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={"repository_url": "https://github.com/manta-val/service-a.git"},
            headers=admin_headers,
        )
        assert res_url.status_code == 201, res_url.text
        data_a = res_url.json()
        assert data_a["full_name"] == "manta-val/service-a"
        assert data_a["integration_id"] == str(integ.id)
        assert data_a["external_repo_id"] > 0

        # Link second repo via slug format with explicit integration_id
        res_slug = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={
                "full_name": "manta-val/service-b",
                "integration_id": str(integ.id),
                "external_repo_id": 12345678,
            },
            headers=admin_headers,
        )
        assert res_slug.status_code == 201, res_slug.text
        data_b = res_slug.json()
        assert data_b["full_name"] == "manta-val/service-b"
        assert data_b["external_repo_id"] == 12345678


@pytest.mark.asyncio
async def test_link_repo_rejects_invalid_urls_with_422(auth_context):
    """TC-SCRUM-378-002: Malformed or non-GitHub URLs return HTTP 422."""
    tenant_id, _ = await register_test_tenant("val-422")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    await save_or_update_integration(
        tenant_id=tenant_id,
        provider="github",
        external_account_id="manta-422-org",
        raw_webhook_secret="whsec_test_secret_422",
        api_credentials={"access_token": "ghp_val_token_422"},
        base_url="http://test",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/v1/projects",
            json={"name": "Proj 422", "slug": "proj-422"},
            headers=admin_headers,
        )
        project_id = p_res.json()["id"]

        # 1. Non-GitHub host (GitLab) -> 422
        res_gl = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={"repository_url": "https://gitlab.com/org/repo"},
            headers=admin_headers,
        )
        assert res_gl.status_code == 422

        # 2. Malformed string -> 422
        res_bad = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={"repository_url": "just_a_random_string"},
            headers=admin_headers,
        )
        assert res_bad.status_code == 422


@pytest.mark.asyncio
async def test_link_repo_integration_status_checks(auth_context):
    """TC-SCRUM-378-003: Missing integration -> 400/404, Inactive integration -> 400."""
    tenant_id, _ = await register_test_tenant("val-integ")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/v1/projects",
            json={"name": "Proj Integ Check", "slug": "proj-integ-check"},
            headers=admin_headers,
        )
        project_id = p_res.json()["id"]

        # Case 1: Tenant has NO GitHub integration at all -> HTTP 400
        res_no_integ = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={"full_name": "org/repo-no-integ"},
            headers=admin_headers,
        )
        assert res_no_integ.status_code == 400
        assert "connect github first" in res_no_integ.json()["detail"].lower()

        # Case 2: Explicit integration_id supplied that does NOT exist -> HTTP 404
        fake_integ_id = str(uuid.uuid4())
        res_404 = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={"full_name": "org/repo-404", "integration_id": fake_integ_id},
            headers=admin_headers,
        )
        assert res_404.status_code == 404
        assert "not found" in res_404.json()["detail"].lower()

        # Connect integration but set it inactive
        integ = await save_or_update_integration(
            tenant_id=tenant_id,
            provider="github",
            external_account_id="inactive-org",
            raw_webhook_secret="whsec_inactive",
            api_credentials={"access_token": "ghp_inactive"},
            base_url="http://test",
        )
        async with get_tenant_db_context(tenant_id) as session:
            stmt = select(Integration).where(Integration.id == integ.id)
            row = (await session.execute(stmt)).scalars().first()
            row.is_active = False
            await session.flush()

        # Case 3: Explicit integration_id supplied that is INACTIVE -> HTTP 400
        res_inactive = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={"full_name": "org/repo-inactive", "integration_id": str(integ.id)},
            headers=admin_headers,
        )
        assert res_inactive.status_code == 400
        assert "inactive" in res_inactive.json()["detail"].lower()


@pytest.mark.asyncio
async def test_link_repo_cross_tenant_isolation(auth_context):
    """TC-SCRUM-378-004: Tenant B cannot use Tenant A's integration_id (HTTP 404 under RLS)."""
    tenant_a, _ = await register_test_tenant("cross-a")
    tenant_b, _ = await register_test_tenant("cross-b")

    admin_b = mint_jwt(auth_context, tenant_id=tenant_b, roles=["TENANT_ADMIN"])

    # Tenant A creates an integration
    integ_a = await save_or_update_integration(
        tenant_id=tenant_a,
        provider="github",
        external_account_id="tenant-a-org",
        raw_webhook_secret="whsec_a",
        api_credentials={"access_token": "ghp_a"},
        base_url="http://test",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Tenant B creates their project
        res_p = await ac.post(
            "/api/v1/projects",
            json={"name": "Tenant B Proj", "slug": "tenant-b-proj"},
            headers={"Authorization": f"Bearer {admin_b}"},
        )
        project_b_id = res_p.json()["id"]

        # Tenant B attempts to link repo using Tenant A's integration_id -> HTTP 404
        res_cross = await ac.post(
            f"/api/v1/projects/{project_b_id}/repositories",
            json={"full_name": "org/repo-cross", "integration_id": str(integ_a.id)},
            headers={"Authorization": f"Bearer {admin_b}"},
        )
        assert res_cross.status_code == 404
