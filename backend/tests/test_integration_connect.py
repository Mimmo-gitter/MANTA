"""
SCRUM-322 & SCRUM-323: Integration Connect Endpoints & Unique Webhook URLs Test Suite.

Verifies:
1. Provider connection with live credential verification (GitHub, Jira, Slack).
2. Rejection of invalid credentials (HTTP 400) without persisting any data.
3. Strict administrator authorization (HTTP 403 for non-admins, HTTP 401 for unauthenticated).
4. AES-256-GCM encryption of stored webhook secrets and API tokens in the database.
5. Unique, unguessable UUIDv4 webhook URLs bound 1-to-1 to each integration.
6. End-to-end integration: delivering signed webhooks to generated webhook URL succeeds (HTTP 202).
7. Tenant RLS isolation: Tenant B cannot list, view, or delete Tenant A's integrations.
8. Listing and detail views safely mask secrets.
9. Direct unit tests for provider credential validators with MockTransport.
"""

import hashlib
import hmac
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
from app.config import settings
from app.db.models.models import Integration
from app.db.session import async_engine, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.integrations.validators import (
    InvalidCredentialsError,
    validate_github_credentials,
    validate_jira_credentials,
    validate_slack_credentials,
)
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
    key, jwk = generate_rsa_keypair("integ-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "integ-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID,
    roles: list[str] | None = None,
    sub: str = "test-user-admin",
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
        "exp": int(time.time()) + 3600,
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
# TESTS: Direct Provider Validator Unit Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_validate_github_credentials_unit():
    # 1. Success case
    async def gh_ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ghp_valid123"
        return httpx.Response(200, json={"login": "mona-lisa", "id": 12345}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(gh_ok_handler)) as client:
        account_id = await validate_github_credentials("ghp_valid123", client=client)
        assert account_id == "mona-lisa"

    # 2. Failure case (401 Bad credentials)
    async def gh_err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(gh_err_handler)) as client:
        with pytest.raises(InvalidCredentialsError, match="Bad credentials"):
            await validate_github_credentials("ghp_bad", client=client)


@pytest.mark.asyncio
async def test_validate_jira_credentials_unit():
    # 1. Success case
    async def jira_ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accountId": "acc_jira_123"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(jira_ok_handler)) as client:
        account_id = await validate_jira_credentials(
            "https://test.atlassian.net", "admin@test.com", "token123", client=client
        )
        assert account_id == "acc_jira_123"

    # 2. Failure case (403 Forbidden)
    async def jira_err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(jira_err_handler)) as client:
        with pytest.raises(InvalidCredentialsError, match="invalid email or API token"):
            await validate_jira_credentials(
                "https://test.atlassian.net", "admin@test.com", "bad_token", client=client
            )


@pytest.mark.asyncio
async def test_validate_slack_credentials_unit():
    # 1. Success case
    async def slack_ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer xoxb-slack-token"
        return httpx.Response(
            200, json={"ok": True, "team_id": "T_SLACK_1", "user_id": "U1"}, request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(slack_ok_handler)) as client:
        team_id = await validate_slack_credentials("xoxb-slack-token", client=client)
        assert team_id == "T_SLACK_1"

    # 2. Failure case (ok: False)
    async def slack_err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "token_revoked"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slack_err_handler)) as client:
        with pytest.raises(InvalidCredentialsError, match="token_revoked"):
            await validate_slack_credentials("xoxb-revoked", client=client)


# ==============================================================================
# TESTS: RBAC & Access Control
# ==============================================================================


@pytest.mark.asyncio
async def test_connect_endpoints_require_authentication():
    """Unauthenticated requests to /api/v1/integrations/* are rejected with HTTP 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_gh = await ac.post(
            "/api/v1/integrations/github/connect",
            json={"access_token": "ghp_dummy123456789"},
        )
        assert res_gh.status_code == 401

        res_list = await ac.get("/api/v1/integrations")
        assert res_list.status_code == 401


@pytest.mark.asyncio
async def test_connect_endpoints_require_admin_role(auth_context):
    """
    SCRUM-322: Non-admin users (e.g. DEVELOPER without TENANT_ADMIN)
    are strictly rejected with HTTP 403 Forbidden.
    """
    tenant_id, _ = await register_test_tenant("rbac-non-admin")
    non_admin_token = mint_jwt(auth_context, tenant_id=tenant_id, roles=["DEVELOPER"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers = {"Authorization": f"Bearer {non_admin_token}"}
        res = await ac.post(
            "/api/v1/integrations/github/connect",
            json={"access_token": "ghp_dummy123456789"},
            headers=headers,
        )
        assert res.status_code == 403
        assert "administrator privilege required" in res.json()["detail"].lower()


# ==============================================================================
# TESTS: GitHub Connect & Validation (SCRUM-322 & SCRUM-323)
# ==============================================================================


@pytest.mark.asyncio
async def test_github_connect_success_and_webhook_url(auth_context):
    """
    SCRUM-322 & SCRUM-323: Successful GitHub connection.
    - Validates credentials with GitHub API.
    - Generates unique unguessable webhook URL.
    - Encrypts stored credentials with AES-256-GCM.
    - Returns HTTP 201 with webhook URL and generated secret.
    """
    tenant_id, _ = await register_test_tenant("gh-success")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        new=mock.AsyncMock(return_value="octocat-dev"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_valid_mock_token_123456789"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 201
    data = res.json()
    assert data["provider"] == "github"
    assert data["external_account_id"] == "octocat-dev"
    assert data["status"] == "connected"
    assert data["is_active"] is True
    assert "webhook_url" in data
    assert "webhook_secret" in data
    assert len(data["webhook_secret"]) == 64  # 32-byte hex secret
    integration_id = uuid.UUID(data["id"])

    # SCRUM-323: Verify webhook URL format and unguessable UUID
    expected_url = f"http://test/api/v1/webhooks/integrations/{integration_id}"
    assert data["webhook_url"] == expected_url

    # Verify secrets in database are authenticated-encrypted
    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.id == integration_id)
        res_db = await session.execute(stmt)
        record = res_db.scalars().one()

        assert record.provider == "github"
        assert record.external_account_id == "octocat-dev"
        assert record.webhook_secret_hash.startswith("enc:v1:")
        assert record.api_credentials_vault_ref.startswith("enc:v1:")

        # Decryption yields expected stored plaintext
        decrypted_wh_secret = encryption_service.decrypt(record.webhook_secret_hash)
        assert decrypted_wh_secret == data["webhook_secret"]

        decrypted_api_creds = json.loads(
            encryption_service.decrypt(record.api_credentials_vault_ref)
        )
        assert decrypted_api_creds["access_token"] == "ghp_valid_mock_token_123456789"


@pytest.mark.asyncio
async def test_github_connect_invalid_credentials_rejected(auth_context):
    """
    SCRUM-322: Rejection of invalid GitHub access token with HTTP 400.
    Ensures zero database records are created on failure.
    """
    tenant_id, _ = await register_test_tenant("gh-invalid")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        side_effect=InvalidCredentialsError("GitHub credential validation failed: Bad credentials"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_bad_token_000"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "bad credentials" in res.json()["detail"].lower()

    # Zero records created in database
    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(Integration.tenant_id == tenant_id)
        res_db = await session.execute(stmt)
        records = res_db.scalars().all()
        assert len(records) == 0


# ==============================================================================
# TESTS: Jira Connect & Validation (SCRUM-322 & SCRUM-323)
# ==============================================================================


@pytest.mark.asyncio
async def test_jira_connect_success_and_webhook_url(auth_context):
    """
    SCRUM-322 & SCRUM-323: Successful Jira connection.
    - Validates email and API token with Jira REST API.
    - Returns HTTP 201 with unique webhook URL.
    - Persists encrypted credentials.
    """
    tenant_id, _ = await register_test_tenant("jira-success")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    custom_wh_secret = "my_custom_jira_webhook_secret_999"

    with mock.patch(
        "app.integrations.router.validate_jira_credentials",
        new=mock.AsyncMock(return_value="5b10ac8d82e05b22cc7d4ef5"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/jira/connect",
                json={
                    "base_url": "https://enterprise.atlassian.net",
                    "email": "lead-architect@jira.net",
                    "api_token": "jira_api_token_secret_xyz",
                    "webhook_secret": custom_wh_secret,
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 201
    data = res.json()
    assert data["provider"] == "jira"
    assert data["external_account_id"] == "5b10ac8d82e05b22cc7d4ef5"
    assert data["webhook_secret"] == custom_wh_secret
    assert data["status"] == "connected"

    integration_id = uuid.UUID(data["id"])
    assert data["webhook_url"] == f"http://test/api/v1/webhooks/integrations/{integration_id}"


@pytest.mark.asyncio
async def test_jira_connect_invalid_credentials_rejected(auth_context):
    """
    SCRUM-322: Rejection of invalid Jira credentials with HTTP 400.
    """
    tenant_id, _ = await register_test_tenant("jira-invalid")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    with mock.patch(
        "app.integrations.router.validate_jira_credentials",
        side_effect=InvalidCredentialsError(
            "Jira credential validation failed: invalid email or API token"
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/jira/connect",
                json={
                    "base_url": "https://enterprise.atlassian.net",
                    "email": "lead-architect@jira.net",
                    "api_token": "invalid_token",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "invalid email or api token" in res.json()["detail"].lower()


# ==============================================================================
# TESTS: Slack Connect & Validation (SCRUM-322 & SCRUM-323)
# ==============================================================================


@pytest.mark.asyncio
async def test_slack_connect_success_and_webhook_url(auth_context):
    """
    SCRUM-322 & SCRUM-323: Successful Slack connection.
    - Validates bot token with auth.test.
    - Stores signing secret encrypted.
    - Returns HTTP 201 with unique webhook URL.
    """
    tenant_id, _ = await register_test_tenant("slack-success")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    with mock.patch(
        "app.integrations.router.validate_slack_credentials",
        new=mock.AsyncMock(return_value="T0987654321"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/slack/connect",
                json={
                    "bot_token": "xoxb-123456789-abcdef",
                    "signing_secret": "slack_signing_secret_987654321",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 201
    data = res.json()
    assert data["provider"] == "slack"
    assert data["external_account_id"] == "T0987654321"
    assert data["webhook_secret"] == "slack_signing_secret_987654321"

    integration_id = uuid.UUID(data["id"])
    assert data["webhook_url"] == f"http://test/api/v1/webhooks/integrations/{integration_id}"


@pytest.mark.asyncio
async def test_slack_connect_invalid_token_rejected(auth_context):
    """
    SCRUM-322: Rejection of invalid Slack bot token with HTTP 400.
    """
    tenant_id, _ = await register_test_tenant("slack-invalid")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    with mock.patch(
        "app.integrations.router.validate_slack_credentials",
        side_effect=InvalidCredentialsError("Slack credential validation failed: invalid_auth"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/integrations/slack/connect",
                json={
                    "bot_token": "xoxb-invalid-token",
                    "signing_secret": "any_secret",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert res.status_code == 400
    assert "invalid_auth" in res.json()["detail"].lower()


# ==============================================================================
# TESTS: End-to-End Webhook Delivery via Generated Webhook URL
# ==============================================================================


@pytest.mark.asyncio
async def test_round_trip_webhook_intake_via_generated_url(auth_context):
    """
    End-to-end integration verification (SCRUM-322, SCRUM-323 -> SCRUM-327, SCRUM-325):
    1. Connect GitHub integration and obtain generated webhook URL and secret.
    2. Deliver HMAC-SHA256 signed GitHub webhook directly to the generated URL.
    3. Assert HTTP 202 Accepted response and verified signature.
    """
    tenant_id, _ = await register_test_tenant("e2e-intake")
    admin_token = mint_jwt(auth_context, tenant_id=tenant_id)

    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        new=mock.AsyncMock(return_value="e2e-octocat"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            conn_res = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_e2e_token_secret"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    assert conn_res.status_code == 201
    conn_data = conn_res.json()
    webhook_url = conn_data["webhook_url"]  # E.g. "http://test/api/v1/webhooks/integrations/<id>"
    raw_wh_secret = conn_data["webhook_secret"]

    # Extract path from URL
    path = webhook_url.replace("http://test", "")

    # Sign webhook payload using the newly generated secret
    body_payload = {"ref": "refs/heads/main", "commits": [{"id": "abc1234"}]}
    raw_body = json.dumps(body_payload).encode("utf-8")
    mac = hmac.new(raw_wh_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    signature_header = f"sha256={mac}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        intake_res = await ac.post(
            path,
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature_header,
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-GitHub-Event": "push",
            },
        )

    assert intake_res.status_code == 202
    intake_data = intake_res.json()
    assert intake_data["status"] == "accepted"
    assert intake_data["provider"] == "github"
    assert intake_data["duplicate"] is False


# ==============================================================================
# TESTS: Integration Management & Multi-Tenant Isolation
# ==============================================================================


@pytest.mark.asyncio
async def test_list_and_delete_integrations_with_masked_secrets(auth_context):
    """
    Verifies:
    1. GET /api/v1/integrations returns list with masked secrets.
    2. DELETE /api/v1/integrations/{id} deletes integration.
    3. Cross-tenant isolation: Tenant B cannot view or delete Tenant A's integration.
    """
    tenant_a, _ = await register_test_tenant("iso-a")
    tenant_b, _ = await register_test_tenant("iso-b")

    token_a = mint_jwt(auth_context, tenant_id=tenant_a)
    token_b = mint_jwt(auth_context, tenant_id=tenant_b)

    # 1. Connect integration for Tenant A
    with mock.patch(
        "app.integrations.router.validate_github_credentials",
        new=mock.AsyncMock(return_value="tenant-a-user"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_conn = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_tenant_a_token"},
                headers={"Authorization": f"Bearer {token_a}"},
            )
    assert res_conn.status_code == 201
    integ_a_id = res_conn.json()["id"]

    # 2. Tenant A lists integrations -> sees 1 integration with masked secret
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_list_a = await ac.get(
            "/api/v1/integrations",
            headers={"Authorization": f"Bearer {token_a}"},
        )
    assert res_list_a.status_code == 200
    list_a = res_list_a.json()
    assert len(list_a) == 1
    assert list_a[0]["id"] == integ_a_id
    assert (
        list_a[0]["webhook_secret_masked"].startswith("whsec_")
        or "..." in list_a[0]["webhook_secret_masked"]
        or list_a[0]["webhook_secret_masked"] == "********"
    )

    # 3. Tenant B lists integrations -> sees 0 integrations (Multi-tenant RLS isolation)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_list_b = await ac.get(
            "/api/v1/integrations",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert res_list_b.status_code == 200
    assert len(res_list_b.json()) == 0

    # 4. Tenant B attempts to GET or DELETE Tenant A's integration -> 404 Not Found
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_get_cross = await ac.get(
            f"/api/v1/integrations/{integ_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_get_cross.status_code == 404

        res_del_cross = await ac.delete(
            f"/api/v1/integrations/{integ_a_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_del_cross.status_code == 404

    # 5. Tenant A deletes integration -> 204 No Content
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_del = await ac.delete(
            f"/api/v1/integrations/{integ_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res_del.status_code == 204

        # Verify it is gone
        res_get_again = await ac.get(
            f"/api/v1/integrations/{integ_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res_get_again.status_code == 404
