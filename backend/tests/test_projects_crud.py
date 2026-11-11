"""
SCRUM-377: Project CRUD and Repository Linking Automated Test Suite.
Parent Story: SCRUM-352 (US-17: Create project and link repositories).

Automated verification of project lifecycle and repository linking under tenant RLS:
- TC-PROJ-CREATE-001: Admin creates project -> HTTP 201 Created, scoped to tenant_uuid.
- TC-PROJ-SLUG-002:   Slug auto-generated from project name if omitted.
- TC-PROJ-CONF-003:   Duplicate project slug in same tenant rejected with HTTP 409 Conflict.
- TC-PROJ-RBAC-004:   Developer role denied project creation with HTTP 403 Forbidden.
- TC-PROJ-LIST-005:   List projects returns only projects for caller's tenant (RLS isolation).
- TC-PROJ-GET-006:    Get project by ID returns HTTP 200 with repository collection.
- TC-PROJ-CROSS-007:  Cross-tenant project retrieval returns HTTP 404 Not Found (no leakage).
- TC-REPO-LINK-008:   Admin links GitHub repository -> HTTP 201 Created, creates Repository & ProjectLink.
- TC-REPO-CONF-009:   Duplicate repository linking (same external_repo_id) rejected with HTTP 409 Conflict.
- TC-REPO-RBAC-010:   Developer role denied repository linking with HTTP 403 Forbidden.
- TC-REPO-NOINT-011:  Repository linking without active GitHub integration returns HTTP 400 Bad Request.
- TC-REPO-UNLINK-012: Admin unlinks repository -> HTTP 204 No Content, removes Repository & ProjectLink.
- TC-REPO-UNLINK-RBAC-013: Developer role denied repository unlinking with HTTP 403 Forbidden.
- TC-REPO-UNLINK-404-014:  Unlinking cross-tenant or missing repository returns HTTP 404 Not Found.
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
from sqlalchemy import select

from app.auth.jwks import JWKSClient
from app.auth.permissions import FORBIDDEN_RESPONSE
from app.config import settings
from app.db.models.models import ProjectLink, Repository
from app.db.session import async_engine, get_tenant_db_context
from app.integrations.service import save_or_update_integration
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
    key, jwk = generate_rsa_keypair("proj-test-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "proj-test-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID,
    roles: list[str] | None = None,
    sub: str = "test-proj-admin",
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
# PROJECT CRUD TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_377_create_project_admin_success(auth_context):
    """TC-PROJ-CREATE-001: Admin creates valid project under tenant RLS."""
    tenant_id, _ = await register_test_tenant("proj-create")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    payload = {
        "name": "Platform Core",
        "slug": "platform-core",
        "tier_sla_hours": 48,
        "wip_limit_per_dev": 4,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/projects",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["name"] == "Platform Core"
        assert data["slug"] == "platform-core"
        assert data["tenant_id"] == str(tenant_id)
        assert data["tier_sla_hours"] == 48
        assert data["wip_limit_per_dev"] == 4
        assert data["repositories"] == []
        assert "id" in data


@pytest.mark.asyncio
async def test_scrum_377_create_project_slug_auto_generated(auth_context):
    """TC-PROJ-SLUG-002: Auto-generates URL-safe slug from project name when omitted."""
    tenant_id, _ = await register_test_tenant("proj-slug")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    payload = {
        "name": "Engineering Intelligence & Analytics!",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/projects",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["slug"] == "engineering-intelligence-analytics"


@pytest.mark.asyncio
async def test_scrum_377_create_project_duplicate_slug_conflict(auth_context):
    """TC-PROJ-CONF-003: Duplicate project slug in same tenant returns HTTP 409 Conflict."""
    tenant_id, _ = await register_test_tenant("proj-dup")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    payload = {"name": "Service Alpha", "slug": "service-alpha"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # First creation succeeds
        res1 = await ac.post(
            "/api/v1/projects",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res1.status_code == 201

        # Second creation with same slug fails with 409
        res2 = await ac.post(
            "/api/v1/projects",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res2.status_code == 409
        assert "already exists" in res2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_scrum_377_create_project_developer_forbidden(auth_context):
    """TC-PROJ-RBAC-004: Non-admin role (DEVELOPER) denied project creation with HTTP 403."""
    tenant_id, _ = await register_test_tenant("proj-dev-403")
    dev_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["DEVELOPER"])

    payload = {"name": "Dev Unauthorized Project"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/projects",
            json=payload,
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert res.status_code == 403
        assert res.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
async def test_scrum_377_list_and_get_projects_tenant_isolation(auth_context):
    """
    TC-PROJ-LIST-005 & TC-PROJ-GET-006 & TC-PROJ-CROSS-007:
    Strict multi-tenant isolation under PostgreSQL RLS:
    Projects created in Tenant A must never appear in Tenant B's listing or direct fetch.
    """
    tenant_a_id, _ = await register_test_tenant("proj-iso-a")
    tenant_b_id, _ = await register_test_tenant("proj-iso-b")

    admin_a = mint_jwt(auth_context, tenant_id=tenant_a_id, roles=["TENANT_ADMIN"], sub="admin_a")
    admin_b = mint_jwt(auth_context, tenant_id=tenant_b_id, roles=["TENANT_ADMIN"], sub="admin_b")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create Project in Tenant A
        res_a = await ac.post(
            "/api/v1/projects",
            json={"name": "Tenant A Core", "slug": "tenant-a-core"},
            headers={"Authorization": f"Bearer {admin_a}"},
        )
        assert res_a.status_code == 201
        proj_a_id = res_a.json()["id"]

        # Create Project in Tenant B
        res_b = await ac.post(
            "/api/v1/projects",
            json={"name": "Tenant B Core", "slug": "tenant-b-core"},
            headers={"Authorization": f"Bearer {admin_b}"},
        )
        assert res_b.status_code == 201
        proj_b_id = res_b.json()["id"]
        assert proj_b_id != proj_a_id

        # 1. Tenant A lists projects -> sees only Tenant A's project
        list_a = await ac.get("/api/v1/projects", headers={"Authorization": f"Bearer {admin_a}"})
        assert list_a.status_code == 200
        a_slugs = [p["slug"] for p in list_a.json()]
        assert "tenant-a-core" in a_slugs
        assert "tenant-b-core" not in a_slugs

        # 2. Tenant B lists projects -> sees only Tenant B's project
        list_b = await ac.get("/api/v1/projects", headers={"Authorization": f"Bearer {admin_b}"})
        assert list_b.status_code == 200
        b_slugs = [p["slug"] for p in list_b.json()]
        assert "tenant-b-core" in b_slugs
        assert "tenant-a-core" not in b_slugs

        # 3. Direct GET by ID: Tenant A gets project A
        get_a = await ac.get(
            f"/api/v1/projects/{proj_a_id}",
            headers={"Authorization": f"Bearer {admin_a}"},
        )
        assert get_a.status_code == 200
        assert get_a.json()["id"] == proj_a_id

        # 4. Cross-tenant attempt: Tenant B tries to get Tenant A's project -> HTTP 404
        cross_get = await ac.get(
            f"/api/v1/projects/{proj_a_id}",
            headers={"Authorization": f"Bearer {admin_b}"},
        )
        assert cross_get.status_code == 404


# ==============================================================================
# REPOSITORY LINKING & UNLINKING TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_377_repository_linking_workflow(auth_context):
    """
    TC-REPO-LINK-008, TC-REPO-CONF-009, TC-REPO-RBAC-010,
    TC-REPO-UNLINK-012, TC-REPO-UNLINK-RBAC-013:
    End-to-end repository linking and unlinking lifecycle.
    """
    tenant_id, _ = await register_test_tenant("repo-workflow")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])
    dev_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["DEVELOPER"], sub="repo_dev")

    # Seed active GitHub integration for the tenant
    integ = await save_or_update_integration(
        tenant_id=tenant_id,
        provider="github",
        external_account_id="test-github-org",
        raw_webhook_secret="whsec_proj_repo_test_12345",
        api_credentials={"access_token": "ghp_repo_test_pat"},
        base_url="http://test",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        dev_headers = {"Authorization": f"Bearer {dev_token}"}

        # 1. Create target project
        p_res = await ac.post(
            "/api/v1/projects",
            json={"name": "Backend Monorepo", "slug": "backend-monorepo"},
            headers=admin_headers,
        )
        assert p_res.status_code == 201
        project_id = p_res.json()["id"]

        # 2. Developer denied repository linking (HTTP 403)
        repo_payload = {
            "external_repo_id": 987654321,
            "full_name": "manta-org/backend-service",
            "default_branch": "main",
            "is_private": True,
        }
        res_dev_link = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json=repo_payload,
            headers=dev_headers,
        )
        assert res_dev_link.status_code == 403
        assert res_dev_link.json() == FORBIDDEN_RESPONSE

        # 3. Admin links repository successfully (HTTP 201)
        res_admin_link = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json=repo_payload,
            headers=admin_headers,
        )
        assert res_admin_link.status_code == 201, res_admin_link.text
        repo_data = res_admin_link.json()
        repo_id = repo_data["id"]
        assert repo_data["external_repo_id"] == 987654321
        assert repo_data["full_name"] == "manta-org/backend-service"
        assert repo_data["integration_id"] == str(integ.id)
        assert repo_data["project_id"] == project_id

        # 4. Verify ProjectDetail response now includes the linked repository
        res_get_proj = await ac.get(f"/api/v1/projects/{project_id}", headers=admin_headers)
        assert res_get_proj.status_code == 200
        linked_repos = res_get_proj.json()["repositories"]
        assert len(linked_repos) == 1
        assert linked_repos[0]["id"] == repo_id
        assert linked_repos[0]["external_repo_id"] == 987654321

        # 5. Duplicate link attempt fails with HTTP 409 Conflict (uq_repo_external)
        res_dup = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json=repo_payload,
            headers=admin_headers,
        )
        assert res_dup.status_code == 409
        assert "already linked" in res_dup.json()["detail"].lower()

        # 6. Verify ProjectLink row persisted in DB
        async with get_tenant_db_context(tenant_id) as session:
            stmt = select(ProjectLink).where(
                ProjectLink.project_id == uuid.UUID(project_id),
                ProjectLink.external_id == "987654321",
            )
            res = await session.execute(stmt)
            link_row = res.scalars().first()
            assert link_row is not None
            assert link_row.link_type == "github_repository"
            assert link_row.integration_id == integ.id

        # 7. Developer denied unlinking (HTTP 403)
        res_dev_unlink = await ac.delete(
            f"/api/v1/projects/{project_id}/repositories/{repo_id}",
            headers=dev_headers,
        )
        assert res_dev_unlink.status_code == 403
        assert res_dev_unlink.json() == FORBIDDEN_RESPONSE

        # 8. Admin unlinks repository successfully (HTTP 204 No Content)
        res_admin_unlink = await ac.delete(
            f"/api/v1/projects/{project_id}/repositories/{repo_id}",
            headers=admin_headers,
        )
        assert res_admin_unlink.status_code == 204

        # 9. Verify Repository and ProjectLink rows deleted
        async with get_tenant_db_context(tenant_id) as session:
            repo_check = await session.execute(
                select(Repository).where(Repository.id == uuid.UUID(repo_id))
            )
            assert repo_check.scalars().first() is None

            link_check = await session.execute(
                select(ProjectLink).where(
                    ProjectLink.project_id == uuid.UUID(project_id),
                    ProjectLink.external_id == "987654321",
                )
            )
            assert link_check.scalars().first() is None


@pytest.mark.asyncio
async def test_scrum_377_link_repo_without_active_integration_returns_400(auth_context):
    """TC-REPO-NOINT-011: Linking repository with no active GitHub integration returns HTTP 400."""
    tenant_id, _ = await register_test_tenant("repo-no-integ")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Create project
        p_res = await ac.post(
            "/api/v1/projects",
            json={"name": "Isolated Project"},
            headers=admin_headers,
        )
        assert p_res.status_code == 201
        project_id = p_res.json()["id"]

        # Attempt link without GitHub integration configured
        res = await ac.post(
            f"/api/v1/projects/{project_id}/repositories",
            json={
                "external_repo_id": 11223344,
                "full_name": "org/repo",
            },
            headers=admin_headers,
        )
        assert res.status_code == 400
        assert "no active github integration" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_scrum_377_unlink_cross_tenant_or_missing_returns_404(auth_context):
    """TC-REPO-UNLINK-404-014: Unlinking non-existent or cross-tenant repository returns HTTP 404."""
    tenant_a_id, _ = await register_test_tenant("repo-404-a")
    tenant_b_id, _ = await register_test_tenant("repo-404-b")

    admin_a = mint_jwt(auth_context, tenant_id=tenant_a_id, roles=["TENANT_ADMIN"])
    admin_b = mint_jwt(auth_context, tenant_id=tenant_b_id, roles=["TENANT_ADMIN"])

    await save_or_update_integration(
        tenant_id=tenant_a_id,
        provider="github",
        external_account_id="octo-a",
        raw_webhook_secret="whsec_a_123",
        api_credentials={"access_token": "ghp_a"},
        base_url="http://test",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create project & repo in Tenant A
        p_res = await ac.post(
            "/api/v1/projects",
            json={"name": "Project A"},
            headers={"Authorization": f"Bearer {admin_a}"},
        )
        project_a_id = p_res.json()["id"]

        r_res = await ac.post(
            f"/api/v1/projects/{project_a_id}/repositories",
            json={"external_repo_id": 556677, "full_name": "org/proj-a"},
            headers={"Authorization": f"Bearer {admin_a}"},
        )
        repo_a_id = r_res.json()["id"]

        # 1. Unlinking completely random repo_id in Tenant A returns 404
        random_id = uuid.uuid4()
        res_rand = await ac.delete(
            f"/api/v1/projects/{project_a_id}/repositories/{random_id}",
            headers={"Authorization": f"Bearer {admin_a}"},
        )
        assert res_rand.status_code == 404

        # 2. Tenant B admin attempts to unlink Tenant A's repo -> returns 404 (zero leakage)
        res_cross = await ac.delete(
            f"/api/v1/projects/{project_a_id}/repositories/{repo_a_id}",
            headers={"Authorization": f"Bearer {admin_b}"},
        )
        assert res_cross.status_code == 404
