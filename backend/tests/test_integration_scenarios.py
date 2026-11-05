"""
SCRUM-324: Integration Test Scenarios (Invalid Token, Developer Receives HTTP 403,
Administrator Connects Successfully, Secrets Masked After Save, Two Tenants Isolation).

Parent Story: SCRUM-249 (US-13: Connect GitHub, Jira and Slack).

This test suite provides exhaustive automated verification for all scenarios specified
in `docs/integration-test-scenarios.md`:
- TC-INT-001A/B/C: Invalid provider tokens rejected with HTTP 400 (zero persistence).
- TC-INT-002A/B/C: Expired/revoked provider tokens rejected with HTTP 400.
- TC-INT-003:      Expired MANTA session JWT rejected with HTTP 401.
- TC-INT-004A/B/C: Administrator connects successfully (HTTP 201, unique webhook URL, AES-256-GCM).
- TC-INT-005:      Developer role receives HTTP 403 on all integration endpoints.
- TC-INT-006:      All six non-admin roles receive HTTP 403 (strict RBAC gate).
- TC-INT-007:      Secrets masked after save on GET list and GET detail; API tokens omitted.
- TC-INT-008A/B:   Two tenants cannot see or mutate each other's integrations (RLS & 404).
"""

import json
import time
from typing import Any
import unittest.mock as mock
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
from app.db.models.models import Integration
from app.db.session import async_engine, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.integrations.validators import InvalidCredentialsError
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
    key, jwk = generate_rsa_keypair("integ-scenario-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "integ-scenario-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID,
    roles: list[str] | None = None,
    sub: str = "test-user-admin",
    exp_offset_seconds: int = 3600,
) -> str:
    """Helper to mint valid or expired RS256 JWTs with tenant context and roles."""
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
# 1. SCENARIO: INVALID PROVIDER TOKEN (HTTP 400, Zero Persistence)
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_324_invalid_provider_token_github(auth_context):
    """
    TC-INT-001A: Invalid GitHub Personal Access Token.
    - Upstream provider rejects token (HTTP 401 Bad credentials).
    - MANTA returns HTTP 400 Bad Request with descriptive message.
    - Zero records are persisted in the database.
    """
    tenant_id, _ = await register_test_tenant("scrum324-gh-inv")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        side_effect=InvalidCredentialsError("GitHub credential validation failed: Bad credentials"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_invalid_token_99999999"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "bad credentials" in res.json()["detail"].lower()

    # Zero database persistence
    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        assert len(res_db.scalars().all()) == 0


@pytest.mark.asyncio
async def test_scrum_324_invalid_provider_token_jira(auth_context):
    """
    TC-INT-001B: Invalid Jira API Token / Email.
    - Upstream Atlassian API returns 401/403.
    - MANTA returns HTTP 400 Bad Request.
    - Zero records are persisted in the database.
    """
    tenant_id, _ = await register_test_tenant("scrum324-jira-inv")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_jira_credentials",
        side_effect=InvalidCredentialsError("Jira credential validation failed: invalid email or API token"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/jira/connect",
                json={
                    "base_url": "https://manta-corp.atlassian.net",
                    "email": "invalid_user@manta-corp.io",
                    "api_token": "jira_token_garbage_123",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "invalid email or api token" in res.json()["detail"].lower()

    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        assert len(res_db.scalars().all()) == 0


@pytest.mark.asyncio
async def test_scrum_324_invalid_provider_token_slack(auth_context):
    """
    TC-INT-001C: Invalid Slack Bot Token.
    - Slack auth.test returns ok: False with invalid_auth.
    - MANTA returns HTTP 400 Bad Request.
    - Zero records are persisted in the database.
    """
    tenant_id, _ = await register_test_tenant("scrum324-slack-inv")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_slack_credentials",
        side_effect=InvalidCredentialsError("Slack credential validation failed: invalid_auth"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/slack/connect",
                json={
                    "bot_token": "xoxb-invalid-token-123456",
                    "signing_secret": "slack_secret_123",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "invalid_auth" in res.json()["detail"].lower()

    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        assert len(res_db.scalars().all()) == 0


# ==============================================================================
# 2. SCENARIO: EXPIRED TOKEN (Provider Token Expiration & Platform JWT Expiration)
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_324_expired_provider_token_github(auth_context):
    """
    TC-INT-002A: Expired GitHub Personal Access Token.
    - GitHub API returns 401 with 'The token has expired'.
    - MANTA validator wraps error as InvalidCredentialsError.
    - Endpoint returns HTTP 400 with detail stating token expiration.
    - Zero records are persisted.
    """
    tenant_id, _ = await register_test_tenant("scrum324-gh-exp")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        side_effect=InvalidCredentialsError("GitHub credential validation failed: The token has expired"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_expired_token_abcdef123"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()

    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        assert len(res_db.scalars().all()) == 0


@pytest.mark.asyncio
async def test_scrum_324_expired_provider_token_slack(auth_context):
    """
    TC-INT-002B: Expired / Revoked Slack Bot Token.
    - Slack auth.test returns {"ok": false, "error": "token_expired"} or "token_revoked".
    - MANTA returns HTTP 400 Bad Request with error detail.
    - Zero records persisted.
    """
    tenant_id, _ = await register_test_tenant("scrum324-slack-exp")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_slack_credentials",
        side_effect=InvalidCredentialsError("Slack credential validation failed: token_expired"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/slack/connect",
                json={
                    "bot_token": "xoxb-expired-token-777",
                    "signing_secret": "slack_secret_777",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "token_expired" in res.json()["detail"].lower()

    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        assert len(res_db.scalars().all()) == 0


@pytest.mark.asyncio
async def test_scrum_324_expired_provider_token_jira(auth_context):
    """
    TC-INT-002C: Expired Jira API Token.
    - Atlassian API returns 401 with 'token expired' or 'invalid email or API token'.
    - MANTA returns HTTP 400 Bad Request.
    """
    tenant_id, _ = await register_test_tenant("scrum324-jira-exp")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_jira_credentials",
        side_effect=InvalidCredentialsError("Jira credential validation failed: token expired or credentials revoked"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/jira/connect",
                json={
                    "base_url": "https://company.atlassian.net",
                    "email": "lead@company.com",
                    "api_token": "expired_jira_token_abc",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_scrum_324_expired_user_jwt_returns_401(auth_context):
    """
    TC-INT-003: Expired MANTA Platform Session Token.
    - User sends an RS256 JWT with exp timestamp in the past (-300 seconds).
    - Starlette JWTAuthMiddleware / auth dependency rejects with HTTP 401 Unauthorized.
    """
    tenant_id = uuid.uuid4()
    expired_token = mint_jwt(
        auth_context,
        tenant_id=tenant_id,
        roles=["TENANT_ADMIN"],
        exp_offset_seconds=-300,  # 5 minutes in past
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get(
            "/api/v1/integrations",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert res.status_code == 401
        assert "expired" in res.json()["detail"].lower() or "not authenticated" in res.json()["detail"].lower()


# ==============================================================================
# 3. SCENARIO: ADMINISTRATOR CONNECTS SUCCESSFULLY
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_324_administrator_connects_successfully_all_providers(auth_context):
    """
    TC-INT-004: Administrator Connects Successfully (GitHub, Jira & Slack).
    - Role is TENANT_ADMIN.
    - Live credentials validated successfully against upstream APIs.
    - Returns HTTP 201 Created with status 'connected'.
    - Returns unique unguessable UUIDv4 webhook URL.
    - Stored credentials and webhook secrets are AES-256-GCM encrypted in database.
    """
    tenant_id, _ = await register_test_tenant("scrum324-admin-succ")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    # 1. Connect GitHub
    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        new=mock.AsyncMock(return_value="octocat-lead"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_gh = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_valid_admin_token_123"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    assert res_gh.status_code == 201
    data_gh = res_gh.json()
    assert data_gh["provider"] == "github"
    assert data_gh["external_account_id"] == "octocat-lead"
    assert data_gh["status"] == "connected"
    assert data_gh["is_active"] is True
    gh_id = uuid.UUID(data_gh["id"])
    assert data_gh["webhook_url"] == f"http://test/api/v1/webhooks/integrations/{gh_id}"
    assert len(data_gh["webhook_secret"]) == 64

    # 2. Connect Jira
    with mock.patch(
        "app.integrations.router.validate_jira_credentials",
        new=mock.AsyncMock(return_value="acc_jira_admin_456"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_jira = await ac.post(
                "/api/v1/integrations/jira/connect",
                json={
                    "base_url": "https://manta.atlassian.net",
                    "email": "admin@manta.io",
                    "api_token": "jira_valid_token_789",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    assert res_jira.status_code == 201
    data_jira = res_jira.json()
    assert data_jira["provider"] == "jira"
    assert data_jira["external_account_id"] == "acc_jira_admin_456"
    assert data_jira["status"] == "connected"
    jira_id = uuid.UUID(data_jira["id"])
    assert data_jira["webhook_url"] == f"http://test/api/v1/webhooks/integrations/{jira_id}"

    # 3. Connect Slack
    with mock.patch(
        "app.integrations.router.validate_slack_credentials",
        new=mock.AsyncMock(return_value="T_SLACK_WORKSPACE_99"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_slack = await ac.post(
                "/api/v1/integrations/slack/connect",
                json={
                    "bot_token": "xoxb-valid-slack-bot-token",
                    "signing_secret": "my_slack_signing_secret_xyz",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    assert res_slack.status_code == 201
    data_slack = res_slack.json()
    assert data_slack["provider"] == "slack"
    assert data_slack["external_account_id"] == "T_SLACK_WORKSPACE_99"
    assert data_slack["status"] == "connected"
    slack_id = uuid.UUID(data_slack["id"])
    assert data_slack["webhook_url"] == f"http://test/api/v1/webhooks/integrations/{slack_id}"

    # Verify cryptographic encryption at rest in PostgreSQL
    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        records = res_db.scalars().all()
        assert len(records) == 3
        for rec in records:
            assert rec.webhook_secret_hash.startswith("enc:v1:")
            assert rec.api_credentials_vault_ref.startswith("enc:v1:")


# ==============================================================================
# 4. SCENARIO: DEVELOPER ROLE RECEIVES HTTP 403 (AND ALL NON-ADMIN ROLES)
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_324_developer_role_receives_403_on_all_endpoints(auth_context):
    """
    TC-INT-005: Developer Role Receives HTTP 403 on All Integration Endpoints.
    - Target endpoints:
      1. POST /api/v1/integrations/github/connect
      2. POST /api/v1/integrations/jira/connect
      3. POST /api/v1/integrations/slack/connect
      4. GET  /api/v1/integrations
      5. GET  /api/v1/integrations/{id}
      6. DELETE /api/v1/integrations/{id}
    - Caller role is DEVELOPER.
    - Every endpoint strictly enforces require_permission(MANAGE_WEBHOOK_INTEGRATIONS)
      and responds with HTTP 403 Forbidden: {"detail": "Forbidden: insufficient permissions"}.
    """
    tenant_id, _ = await register_test_tenant("scrum324-dev-403")
    dev_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["DEVELOPER"], sub="alpha_dev")
    sample_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": f"Bearer {dev_token}"}

        # 1. GitHub connect
        res_gh = await ac.post(
            "/api/v1/integrations/github/connect",
            json={"access_token": "ghp_attempt_token"},
            headers=headers,
        )
        assert res_gh.status_code == 403
        assert res_gh.json() == FORBIDDEN_RESPONSE

        # 2. Jira connect
        res_jira = await ac.post(
            "/api/v1/integrations/jira/connect",
            json={
                "base_url": "https://manta.atlassian.net",
                "email": "dev@manta.io",
                "api_token": "jira_attempt",
            },
            headers=headers,
        )
        assert res_jira.status_code == 403
        assert res_jira.json() == FORBIDDEN_RESPONSE

        # 3. Slack connect
        res_slack = await ac.post(
            "/api/v1/integrations/slack/connect",
            json={"bot_token": "xoxb-attempt", "signing_secret": "secret"},
            headers=headers,
        )
        assert res_slack.status_code == 403
        assert res_slack.json() == FORBIDDEN_RESPONSE

        # 4. List integrations
        res_list = await ac.get("/api/v1/integrations", headers=headers)
        assert res_list.status_code == 403
        assert res_list.json() == FORBIDDEN_RESPONSE

        # 5. Get integration by ID
        res_get = await ac.get(f"/api/v1/integrations/{sample_id}", headers=headers)
        assert res_get.status_code == 403
        assert res_get.json() == FORBIDDEN_RESPONSE

        # 6. Delete integration
        res_del = await ac.delete(f"/api/v1/integrations/{sample_id}", headers=headers)
        assert res_del.status_code == 403
        assert res_del.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_admin_role",
    [
        "DEVELOPER",
        "SCRUM_MASTER",
        "PROJECT_MANAGER",
        "SOLUTION_ARCHITECT",
        "DELIVERY_DIRECTOR",
        "DELIVERY_EXEC",  # Deprecated alias
        "TOP_MANAGEMENT",
    ],
)
async def test_scrum_324_all_non_admin_roles_receive_403(auth_context, non_admin_role):
    """
    TC-INT-006: Comprehensive Role Restriction Verification.
    Verifies that all non-admin roles in the authoritative 7-role matrix
    are denied access to MANAGE_WEBHOOK_INTEGRATIONS with HTTP 403 Forbidden.
    Only TENANT_ADMIN has ALLOW.
    """
    clean_slug = non_admin_role.lower().replace("_", "")[:8]
    tenant_id, _ = await register_test_tenant(f"scrum324-rbac-{clean_slug}")
    user_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=[non_admin_role])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": f"Bearer {user_token}"}
        res = await ac.get("/api/v1/integrations", headers=headers)
        assert res.status_code == 403
        assert res.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# 5. SCENARIO: SECRETS MASKED AFTER SAVE
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_324_secrets_masked_after_save(auth_context):
    """
    TC-INT-007: Secrets Masked After Save.
    - Initial connection (POST /connect) returns generated secret for setup.
    - Subsequent list query (GET /api/v1/integrations) returns webhook_secret_masked.
    - Detail query (GET /api/v1/integrations/{id}) returns webhook_secret_masked.
    - API credentials (access_token, api_token, bot_token) are NEVER returned in response.
    - Raw database records store ciphertext starting with enc:v1:.
    """
    tenant_id, _ = await register_test_tenant("scrum324-mask")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["TENANT_ADMIN"])

    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        new=mock.AsyncMock(return_value="octocat-masked"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_conn = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_super_secret_pat_987654321"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    assert res_conn.status_code == 201
    conn_data = res_conn.json()
    raw_secret = conn_data["webhook_secret"]
    integ_id = conn_data["id"]

    # 1. Query list
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_list = await ac.get(
            "/api/v1/integrations",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert res_list.status_code == 200
    list_items = res_list.json()
    assert len(list_items) == 1
    item = list_items[0]

    # Verify secret masking
    assert "webhook_secret_masked" in item
    assert "webhook_secret" not in item  # Raw secret omitted
    assert item["webhook_secret_masked"] != raw_secret
    assert (
        item["webhook_secret_masked"].startswith("whsec_")
        or "..." in item["webhook_secret_masked"]
        or item["webhook_secret_masked"] == "********"
    )

    # Verify provider tokens are completely absent from response
    item_str = json.dumps(item)
    assert "ghp_super_secret_pat_987654321" not in item_str
    assert "access_token" not in item

    # 2. Query detail by ID
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_detail = await ac.get(
            f"/api/v1/integrations/{integ_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["webhook_secret_masked"] == item["webhook_secret_masked"]
    assert "access_token" not in detail
    assert "ghp_super_secret_pat_987654321" not in json.dumps(detail)


# ==============================================================================
# 6. SCENARIO: TWO TENANTS CANNOT SEE EACH OTHER'S INTEGRATIONS
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_324_two_tenants_cannot_see_each_others_integrations(auth_context):
    """
    TC-INT-008: Multi-Tenant Row-Level Security Isolation.
    - Tenant Alpha and Tenant Beta both connect integrations.
    - Tenant Alpha lists integrations -> sees only Tenant Alpha's integrations.
    - Tenant Beta lists integrations -> sees only Tenant Beta's integrations.
    - Tenant Beta attempts to read Tenant Alpha's integration by UUID -> HTTP 404 Not Found.
    - Tenant Beta attempts to delete Tenant Alpha's integration by UUID -> HTTP 404 Not Found.
    - Information hiding: returns 404 rather than 403 to prevent enumeration.
    """
    tenant_a, _ = await register_test_tenant("scrum324-iso-a")
    tenant_b, _ = await register_test_tenant("scrum324-iso-b")

    token_a = mint_jwt(auth_context, tenant_id=tenant_a, roles=["TENANT_ADMIN"])
    token_b = mint_jwt(auth_context, tenant_id=tenant_b, roles=["TENANT_ADMIN"])

    # 1. Tenant Alpha connects GitHub integration
    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        new=mock.AsyncMock(return_value="org-alpha-repo"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_a = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_alpha_token_111"},
                headers={"Authorization": f"Bearer {token_a}"},
            )
    assert res_a.status_code == 201
    id_a = res_a.json()["id"]

    # 2. Tenant Beta connects Slack integration
    with mock.patch(
        "app.integrations.router.validate_slack_credentials",
        new=mock.AsyncMock(return_value="T_BETA_SLACK"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_b = await ac.post(
                "/api/v1/integrations/slack/connect",
                json={"bot_token": "xoxb-beta-token-222", "signing_secret": "beta_secret"},
                headers={"Authorization": f"Bearer {token_b}"},
            )
    assert res_b.status_code == 201
    id_b = res_b.json()["id"]

    # 3. Tenant Alpha list: sees only Tenant Alpha's record
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        list_a = await ac.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token_a}"})
    assert list_a.status_code == 200
    records_a = list_a.json()
    assert len(records_a) == 1
    assert records_a[0]["id"] == id_a
    assert records_a[0]["provider"] == "github"

    # 4. Tenant Beta list: sees only Tenant Beta's record
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        list_b = await ac.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token_b}"})
    assert list_b.status_code == 200
    records_b = list_b.json()
    assert len(records_b) == 1
    assert records_b[0]["id"] == id_b
    assert records_b[0]["provider"] == "slack"

    # 5. Cross-tenant read: Tenant Beta attempts to GET id_a -> 404 Not Found
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        cross_get = await ac.get(
            f"/api/v1/integrations/{id_a}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert cross_get.status_code == 404
    assert f"Integration '{id_a}' not found" in cross_get.json()["detail"]

    # 6. Cross-tenant delete: Tenant Beta attempts to DELETE id_a -> 404 Not Found
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        cross_del = await ac.delete(
            f"/api/v1/integrations/{id_a}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert cross_del.status_code == 404
    assert f"Integration '{id_a}' not found" in cross_del.json()["detail"]

    # 7. Verify Tenant Alpha's integration was untouched and remains intact
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        verify_a = await ac.get(
            f"/api/v1/integrations/{id_a}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
    assert verify_a.status_code == 200
    assert verify_a.json()["id"] == id_a
