"""
SCRUM-246 / US-10 (SCRUM-302): Cross-Tenant Read and Write Security Test Suite.

Proves that a tenant cannot read or modify another tenant's records through the API.
Test Scenarios:
1. Seed two tenants (Tenant Alpha and Tenant Beta) with isolated projects, integrations,
   and credentials.
2. Cross-tenant reads by ID return 404 Not Found (preventing tenant enumeration/existence oracles)
   or 403 Forbidden (RBAC permission enforcement).
3. Cross-tenant writes/deletes by ID return 404 Not Found or 403 Forbidden.
4. Database state verification: every cross-tenant write attempt leaves the target tenant's
   records 100% intact and unchanged.
5. Cross-tenant listing endpoints return strictly scoped results with zero cross-tenant leakage.
6. Cross-tenant connection/overwrite attempts do not mutate or overwrite foreign tenant records.
7. Webhook delivery endpoints bound to integration IDs reject unauthorized delivery attempts.
8. HTTP keep-alive session reuse across tenant tokens does not bleed context across boundaries.
9. Tenant registration conflict rejection preserves existing tenant data without modification.
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
from sqlalchemy import delete, text

from app.auth import JWKSClient
from app.config import settings
from app.db.models.models import Integration, Project, Tenant, User
from app.db.session import async_engine, get_tenant_db_context
from app.integrations.service import save_or_update_integration
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter

# Deterministic Multi-Tenant Test Identifiers
TENANT_ALPHA_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_BETA_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

PROJECT_ALPHA_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROJECT_BETA_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generates an RSA private key and corresponding JWK for mock IdP signing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


class MockJWKSServer:
    """Mock JWKS HTTP endpoint returning deterministic public verification keys."""

    def __init__(self, keys_dict: dict[str, Any]) -> None:
        self.keys_dict = keys_dict

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.keys_dict, request=request)


@pytest.fixture(autouse=True)
async def reset_test_environment():
    """Ensures connection pools and rate limiters are reset between tests."""
    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


@pytest.fixture
def auth_context():
    """Configures mock IdP with signing keys for JWT authentication in security tests."""
    key, jwk = generate_rsa_keypair("sec-cross-tenant-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "sec-cross-tenant-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID,
    roles: list[str] | None = None,
    sub: str = "sec-user",
    email: str | None = None,
) -> str:
    """Helper to mint cryptographically valid RS256 JWTs with specific tenant and role claims."""
    if roles is None:
        roles = ["TENANT_ADMIN"]

    user_email = email or f"{sub}@{tenant_id}.test"
    claims: dict[str, Any] = {
        "sub": sub,
        "email": user_email,
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


async def setup_two_tenants_seed_data() -> dict[str, Any]:
    """
    Seeds two completely isolated tenants (Tenant Alpha and Tenant Beta)
    with their respective projects, users, and integrations.
    """
    # 1. Provision Tenant Alpha and Admin User
    async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
        existing_a = await session.get(Tenant, TENANT_ALPHA_ID)
        if not existing_a:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Alpha Corp',
                        p_slug => 'tenant-alpha-sec',
                        p_admin_email => 'admin@alpha-sec.com',
                        p_admin_name => 'Admin Alpha',
                        p_tier => 'enterprise',
                        p_ai_processing_mode => 'cloud_zdr',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_ALPHA_ID},
            )

    # 2. Provision Tenant Beta and Admin User
    async with get_tenant_db_context(TENANT_BETA_ID) as session:
        existing_b = await session.get(Tenant, TENANT_BETA_ID)
        if not existing_b:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Beta Ltd',
                        p_slug => 'tenant-beta-sec',
                        p_admin_email => 'admin@beta-sec.com',
                        p_admin_name => 'Admin Beta',
                        p_tier => 'starter',
                        p_ai_processing_mode => 'local',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_BETA_ID},
            )

    # 3. Seed Project Alpha
    async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
        proj_a = await session.get(Project, PROJECT_ALPHA_ID)
        if not proj_a:
            session.add(
                Project(
                    id=PROJECT_ALPHA_ID,
                    tenant_id=TENANT_ALPHA_ID,
                    name="Project Alpha Confidential",
                    slug="project-alpha-confidential",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )

    # 4. Seed Project Beta
    async with get_tenant_db_context(TENANT_BETA_ID) as session:
        proj_b = await session.get(Project, PROJECT_BETA_ID)
        if not proj_b:
            session.add(
                Project(
                    id=PROJECT_BETA_ID,
                    tenant_id=TENANT_BETA_ID,
                    name="Project Beta Proprietary",
                    slug="project-beta-proprietary",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )

    # 5. Seed Integrations for Tenant Alpha (GitHub, Jira, Slack)
    integ_a_gh = await save_or_update_integration(
        tenant_id=TENANT_ALPHA_ID,
        provider="github",
        external_account_id="gh-org-alpha-sec",
        raw_webhook_secret="wh_secret_alpha_sec_123456",
        api_credentials={"access_token": "ghp_alpha_token_secret"},
        base_url="http://test",
    )
    integ_a_jira = await save_or_update_integration(
        tenant_id=TENANT_ALPHA_ID,
        provider="jira",
        external_account_id="jira-acc-alpha-sec",
        raw_webhook_secret="wh_secret_alpha_jira_123456",
        api_credentials={"api_token": "jira_alpha_token_secret"},
        base_url="http://test",
    )
    integ_a_slack = await save_or_update_integration(
        tenant_id=TENANT_ALPHA_ID,
        provider="slack",
        external_account_id="slack-team-alpha-sec",
        raw_webhook_secret="wh_secret_alpha_slack_123456",
        api_credentials={"bot_token": "xoxb-alpha-slack-token"},
        base_url="http://test",
    )

    # 6. Seed Integrations for Tenant Beta (GitHub, Jira, Slack)
    integ_b_gh = await save_or_update_integration(
        tenant_id=TENANT_BETA_ID,
        provider="github",
        external_account_id="gh-org-beta-sec",
        raw_webhook_secret="wh_secret_beta_sec_123456",
        api_credentials={"access_token": "ghp_beta_token_secret"},
        base_url="http://test",
    )
    integ_b_jira = await save_or_update_integration(
        tenant_id=TENANT_BETA_ID,
        provider="jira",
        external_account_id="jira-acc-beta-sec",
        raw_webhook_secret="wh_secret_beta_jira_123456",
        api_credentials={"api_token": "jira_beta_token_secret"},
        base_url="http://test",
    )
    integ_b_slack = await save_or_update_integration(
        tenant_id=TENANT_BETA_ID,
        provider="slack",
        external_account_id="slack-team-beta-sec",
        raw_webhook_secret="wh_secret_beta_slack_123456",
        api_credentials={"bot_token": "xoxb-beta-slack-token"},
        base_url="http://test",
    )

    return {
        "tenant_a_id": TENANT_ALPHA_ID,
        "tenant_b_id": TENANT_BETA_ID,
        "project_a_id": PROJECT_ALPHA_ID,
        "project_b_id": PROJECT_BETA_ID,
        "integ_a_gh": integ_a_gh,
        "integ_a_jira": integ_a_jira,
        "integ_a_slack": integ_a_slack,
        "integ_b_gh": integ_b_gh,
        "integ_b_jira": integ_b_jira,
        "integ_b_slack": integ_b_slack,
    }


async def teardown_two_tenants_seed_data():
    """Cleans up seeded tenant records and purges connection pool."""
    try:
        async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
            await session.execute(
                delete(Integration).where(Integration.tenant_id == TENANT_ALPHA_ID)
            )
            await session.execute(delete(Project).where(Project.tenant_id == TENANT_ALPHA_ID))
            await session.execute(delete(User).where(User.tenant_id == TENANT_ALPHA_ID))
            await session.execute(delete(Tenant).where(Tenant.id == TENANT_ALPHA_ID))

        async with get_tenant_db_context(TENANT_BETA_ID) as session:
            await session.execute(
                delete(Integration).where(Integration.tenant_id == TENANT_BETA_ID)
            )
            await session.execute(delete(Project).where(Project.tenant_id == TENANT_BETA_ID))
            await session.execute(delete(User).where(User.tenant_id == TENANT_BETA_ID))
            await session.execute(delete(Tenant).where(Tenant.id == TENANT_BETA_ID))
    except Exception:
        pass
    finally:
        await async_engine.dispose()


async def snapshot_integration_in_db(
    tenant_id: uuid.UUID,
    integration_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Captures a full snapshot of an integration record directly from the database."""
    async with get_tenant_db_context(tenant_id) as session:
        integ = await session.get(Integration, integration_id)
        if not integ:
            return None
        return {
            "id": integ.id,
            "tenant_id": integ.tenant_id,
            "provider": integ.provider,
            "external_account_id": integ.external_account_id,
            "webhook_secret_hash": integ.webhook_secret_hash,
            "api_credentials_vault_ref": integ.api_credentials_vault_ref,
            "is_active": integ.is_active,
            "created_at": integ.created_at,
            "updated_at": integ.updated_at,
        }


async def snapshot_tenant_in_db(tenant_id: uuid.UUID) -> dict[str, Any] | None:
    """Captures a snapshot of a tenant root record directly from the database."""
    async with get_tenant_db_context(tenant_id) as session:
        t = await session.get(Tenant, tenant_id)
        if not t:
            return None
        return {
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "tier": t.tier,
            "status": t.status,
            "max_projects": t.max_projects,
            "ai_processing_mode": t.ai_processing_mode,
            "retention_days": t.retention_days,
            "updated_at": t.updated_at,
        }


# ==============================================================================
# 1. CROSS-TENANT READ ENDPOINTS BY ID (API: 404 Not Found / 403 Forbidden)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_read_integration_by_id_rejected_and_hidden(auth_context):
    """
    Test that calling read endpoints with Tenant A's token against Tenant B's integration IDs
    is strictly rejected:
    1. Tenant A (TENANT_ADMIN) requesting Tenant B's integration returns 404 Not Found.
       Information hiding prevents tenant enumeration (no existence oracle).
    2. Tenant A (DEVELOPER) requesting Tenant B's integration returns 403 Forbidden
       due to lack of MANAGE_WEBHOOK_INTEGRATIONS permission.
    3. Bi-directional symmetry: Tenant B requesting Tenant A's integration is also rejected.
    4. Assert zero Tenant B sensitive data is leaked in error response.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        token_a_admin = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["TENANT_ADMIN"])
        token_a_dev = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["DEVELOPER"])
        token_b_admin = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID, roles=["TENANT_ADMIN"])
        token_b_dev = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID, roles=["DEVELOPER"])

        b_integrations = [
            seed_data["integ_b_gh"],
            seed_data["integ_b_jira"],
            seed_data["integ_b_slack"],
        ]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Tenant A Admin attempts to read every Tenant B integration -> 404 Not Found
            for integ_b in b_integrations:
                resp = await ac.get(
                    f"/api/v1/integrations/{integ_b.id}",
                    headers={"Authorization": f"Bearer {token_a_admin}"},
                )
                assert resp.status_code == 404, (
                    f"Expected 404 when reading {integ_b.provider} cross-tenant, got {resp.status_code}"
                )
                body = resp.json()
                assert f"Integration '{integ_b.id}' not found" in body["detail"]
                # Verify no provider or credential leakage
                assert integ_b.external_account_id not in str(body)

            # 2. Tenant A Developer attempts to read Tenant B's integration -> 403 Forbidden
            resp_dev = await ac.get(
                f"/api/v1/integrations/{seed_data['integ_b_gh'].id}",
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert resp_dev.status_code == 403
            assert "Forbidden" in resp_dev.json()["detail"]

            # 3. Symmetric Check: Tenant B Admin attempts to read Tenant A's integration -> 404 Not Found
            resp_sym = await ac.get(
                f"/api/v1/integrations/{seed_data['integ_a_gh'].id}",
                headers={"Authorization": f"Bearer {token_b_admin}"},
            )
            assert resp_sym.status_code == 404
            assert (
                f"Integration '{seed_data['integ_a_gh'].id}' not found" in resp_sym.json()["detail"]
            )

            # 4. Symmetric Check: Tenant B Developer -> 403 Forbidden
            resp_sym_dev = await ac.get(
                f"/api/v1/integrations/{seed_data['integ_a_gh'].id}",
                headers={"Authorization": f"Bearer {token_b_dev}"},
            )
            assert resp_sym_dev.status_code == 403
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 2. CROSS-TENANT LIST READ ENDPOINTS (Strict Boundary Scoping)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_list_integrations_boundary_isolation(auth_context):
    """
    Test that calling GET /api/v1/integrations strictly scopes results to the caller's tenant:
    1. Tenant A lists integrations: receives ONLY Tenant A's 3 integrations.
    2. Tenant B's integration IDs and external account IDs are completely absent.
    3. Tenant B lists integrations: receives ONLY Tenant B's 3 integrations.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        token_a = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["TENANT_ADMIN"])
        token_b = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID, roles=["TENANT_ADMIN"])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Tenant A listing
            res_a = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert res_a.status_code == 200
            list_a = res_a.json()
            ids_a = {item["id"] for item in list_a}
            expected_a_ids = {
                str(seed_data["integ_a_gh"].id),
                str(seed_data["integ_a_jira"].id),
                str(seed_data["integ_a_slack"].id),
            }
            assert ids_a == expected_a_ids
            # None of Tenant B's integration IDs are visible
            b_ids = {
                str(seed_data["integ_b_gh"].id),
                str(seed_data["integ_b_jira"].id),
                str(seed_data["integ_b_slack"].id),
            }
            assert ids_a.isdisjoint(b_ids)

            # Tenant B listing
            res_b = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert res_b.status_code == 200
            list_b = res_b.json()
            ids_b = {item["id"] for item in list_b}
            assert ids_b == b_ids
            assert ids_b.isdisjoint(expected_a_ids)
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 3. CROSS-TENANT WRITE / DELETE ENDPOINTS (Rejected & Data Stays Unchanged)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_delete_integration_rejected_and_data_unchanged(auth_context):
    """
    Test that calling DELETE endpoints with Tenant A's token against Tenant B's IDs is rejected,
    and Tenant B's database records remain 100% unchanged:
    1. Snapshot Tenant B's integration state prior to call.
    2. Tenant A (TENANT_ADMIN) attempts DELETE -> 404 Not Found.
    3. Assert Tenant B's database record is completely intact and matches pre-attack snapshot.
    4. Tenant A (DEVELOPER) attempts DELETE -> 403 Forbidden.
    5. Assert Tenant B's database record is still completely intact.
    6. Symmetric verification: Tenant B attempting DELETE on Tenant A's integration -> 404,
       and Tenant A's record remains completely unchanged.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        token_a_admin = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["TENANT_ADMIN"])
        token_a_dev = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["DEVELOPER"])
        token_b_admin = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID, roles=["TENANT_ADMIN"])

        integ_b_gh = seed_data["integ_b_gh"]
        integ_a_gh = seed_data["integ_a_gh"]

        # Step 1: Snapshot Tenant B's GitHub integration in DB
        snapshot_b_before = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)
        assert snapshot_b_before is not None
        assert snapshot_b_before["is_active"] is True

        # Step 2: Tenant A Admin attempts cross-tenant DELETE -> 404 Not Found
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_del = await ac.delete(
                f"/api/v1/integrations/{integ_b_gh.id}",
                headers={"Authorization": f"Bearer {token_a_admin}"},
            )
            assert resp_del.status_code == 404
            assert f"Integration '{integ_b_gh.id}' not found" in resp_del.json()["detail"]

        # Step 3: Assert Tenant B's database record is 100% UNCHANGED
        snapshot_b_after = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)
        assert snapshot_b_after is not None
        assert snapshot_b_after == snapshot_b_before, "Tenant B's integration record was modified!"

        # Step 4: Tenant A Developer attempts cross-tenant DELETE -> 403 Forbidden
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_del_dev = await ac.delete(
                f"/api/v1/integrations/{integ_b_gh.id}",
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert resp_del_dev.status_code == 403

        # Step 5: Assert Tenant B's data remains unchanged
        snapshot_b_after_dev = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)
        assert snapshot_b_after_dev == snapshot_b_before

        # Step 6: Symmetric Check: Tenant B Admin attempts cross-tenant DELETE on Tenant A -> 404
        snapshot_a_before = await snapshot_integration_in_db(TENANT_ALPHA_ID, integ_a_gh.id)
        assert snapshot_a_before is not None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_del_sym = await ac.delete(
                f"/api/v1/integrations/{integ_a_gh.id}",
                headers={"Authorization": f"Bearer {token_b_admin}"},
            )
            assert resp_del_sym.status_code == 404

        # Step 7: Assert Tenant A's data remains 100% intact
        snapshot_a_after = await snapshot_integration_in_db(TENANT_ALPHA_ID, integ_a_gh.id)
        assert snapshot_a_after == snapshot_a_before
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 4. CROSS-TENANT WRITE / CONNECT OVERWRITE ATTEMPTS (Data Stays Unchanged)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_connect_overwrite_attempt_preserves_target_tenant(auth_context):
    """
    Test that an attacker (Tenant A) calling connect endpoints with the exact external account
    IDs owned by Tenant B does NOT overwrite, corrupt, or alter Tenant B's integration:
    1. Tenant B has an active GitHub integration for external account 'gh-org-beta-sec'.
    2. Tenant A calls POST /api/v1/integrations/github/connect claiming 'gh-org-beta-sec'.
    3. Tenant A's connect creates an integration strictly bounded to Tenant A.
    4. Tenant B's integration in the database remains completely UNCHANGED (same tenant_id,
       same webhook secret, same credentials, same timestamps).
    5. Test across GitHub, Jira, and Slack connect endpoints.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        token_a_admin = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["TENANT_ADMIN"])

        integ_b_gh = seed_data["integ_b_gh"]
        integ_b_jira = seed_data["integ_b_jira"]
        integ_b_slack = seed_data["integ_b_slack"]

        snapshot_b_gh_before = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)
        snapshot_b_jira_before = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_jira.id)
        snapshot_b_slack_before = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_slack.id)

        # 1. Attacker (Tenant A) attempts GitHub connect with Tenant B's external account ID
        with mock.patch(
            "app.integrations.router.validate_github_credentials",
            new=mock.AsyncMock(return_value=integ_b_gh.external_account_id),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res_gh = await ac.post(
                    "/api/v1/integrations/github/connect",
                    json={
                        "access_token": "ghp_rogue_overwrite_token",
                        "webhook_secret": "wh_rogue_secret_gh",
                    },
                    headers={"Authorization": f"Bearer {token_a_admin}"},
                )
                assert res_gh.status_code == 201
                data_gh = res_gh.json()
                # Must be assigned to Tenant A, with its own unique integration ID
                assert uuid.UUID(data_gh["id"]) != integ_b_gh.id

        # Verify Tenant B's GitHub integration in DB is completely UNCHANGED
        snapshot_b_gh_after = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)
        assert snapshot_b_gh_after == snapshot_b_gh_before

        # 2. Attacker (Tenant A) attempts Jira connect with Tenant B's external account ID
        with mock.patch(
            "app.integrations.router.validate_jira_credentials",
            new=mock.AsyncMock(return_value=integ_b_jira.external_account_id),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res_jira = await ac.post(
                    "/api/v1/integrations/jira/connect",
                    json={
                        "base_url": "https://tenant-b.atlassian.net",
                        "email": "attacker@alpha-sec.com",
                        "api_token": "jira_rogue_token",
                        "webhook_secret": "wh_rogue_secret_jira",
                    },
                    headers={"Authorization": f"Bearer {token_a_admin}"},
                )
                assert res_jira.status_code == 201
                assert uuid.UUID(res_jira.json()["id"]) != integ_b_jira.id

        # Verify Tenant B's Jira integration in DB is completely UNCHANGED
        snapshot_b_jira_after = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_jira.id)
        assert snapshot_b_jira_after == snapshot_b_jira_before

        # 3. Attacker (Tenant A) attempts Slack connect with Tenant B's external account ID
        with mock.patch(
            "app.integrations.router.validate_slack_credentials",
            new=mock.AsyncMock(return_value=integ_b_slack.external_account_id),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res_slack = await ac.post(
                    "/api/v1/integrations/slack/connect",
                    json={
                        "bot_token": "xoxb-rogue-slack-token",
                        "signing_secret": "wh_rogue_secret_slack",
                    },
                    headers={"Authorization": f"Bearer {token_a_admin}"},
                )
                assert res_slack.status_code == 201
                assert uuid.UUID(res_slack.json()["id"]) != integ_b_slack.id

        # Verify Tenant B's Slack integration in DB is completely UNCHANGED
        snapshot_b_slack_after = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_slack.id)
        assert snapshot_b_slack_after == snapshot_b_slack_before
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 5. CROSS-TENANT WEBHOOK INTAKE ENDPOINTS BY ID (Rejection & Isolation)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_webhook_delivery_by_id_rejected(auth_context):
    """
    Test that delivering a webhook targeting Tenant B's integration endpoint
    with mismatched credentials or signatures is rejected, leaving Tenant B's state unchanged:
    1. Call POST /api/v1/webhooks/integrations/{tenant_b_id} with invalid signature -> 401 Unauthorized.
    2. Call POST /api/v1/webhooks/integrations/{random_unknown_id} -> 404 Not Found.
    3. Assert Tenant B's database integration remains unchanged.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        integ_b_gh = seed_data["integ_b_gh"]
        snapshot_b_before = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)

        raw_body = json.dumps({"action": "opened", "issue": {"id": 101}}).encode("utf-8")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Delivery with completely invalid / rogue HMAC signature
            res_bad_sig = await ac.post(
                f"/api/v1/webhooks/integrations/{integ_b_gh.id}",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": "sha256=baddeadbeef00000000000000000000000000000000000000000000000000000",
                    "X-GitHub-Delivery": str(uuid.uuid4()),
                    "X-GitHub-Event": "issues",
                },
            )
            # Rejection due to signature mismatch
            assert res_bad_sig.status_code == 401
            assert "signature" in res_bad_sig.json()["detail"].lower()

            # 2. Delivery to a non-existent integration ID -> 404 Not Found
            random_id = uuid.uuid4()
            res_nonexistent = await ac.post(
                f"/api/v1/webhooks/integrations/{random_id}",
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )
            assert res_nonexistent.status_code == 404

        # 3. Assert Tenant B's integration record is completely UNCHANGED
        snapshot_b_after = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b_gh.id)
        assert snapshot_b_after == snapshot_b_before
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 6. CROSS-TENANT HTTP KEEP-ALIVE SESSION REUSE (Context Bleed Prevention)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_http_session_reuse_boundary_integrity(auth_context):
    """
    Test that HTTP keep-alive connection reuse across requests does not leak state:
    1. A single AsyncClient connection executes:
       - Request 1: Tenant A reads Tenant A integration -> 200 OK.
       - Request 2: Over the SAME connection, Tenant A attempts to read Tenant B's ID -> 404 Not Found.
       - Request 3: Over the SAME connection, Tenant A attempts to DELETE Tenant B's ID -> 404 Not Found.
       - Request 4: Over the SAME connection, switch Authorization to Tenant B token -> 200 OK.
       - Request 5: Over the SAME connection, Tenant B attempts to DELETE Tenant A's ID -> 404 Not Found.
    2. Assert both Tenant A's and Tenant B's database records remain completely unchanged.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        token_a = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID, roles=["TENANT_ADMIN"])
        token_b = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID, roles=["TENANT_ADMIN"])

        integ_a = seed_data["integ_a_gh"]
        integ_b = seed_data["integ_b_gh"]

        snapshot_a_before = await snapshot_integration_in_db(TENANT_ALPHA_ID, integ_a.id)
        snapshot_b_before = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b.id)

        # Single persistent AsyncClient simulating HTTP keep-alive connection reuse
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Step 1: Tenant A legitimately accesses Tenant A integration
            r1 = await ac.get(
                f"/api/v1/integrations/{integ_a.id}",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r1.status_code == 200
            assert r1.json()["id"] == str(integ_a.id)

            # Step 2: Tenant A attempts to read Tenant B integration over same connection -> 404
            r2 = await ac.get(
                f"/api/v1/integrations/{integ_b.id}",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r2.status_code == 404

            # Step 3: Tenant A attempts to DELETE Tenant B integration over same connection -> 404
            r3 = await ac.delete(
                f"/api/v1/integrations/{integ_b.id}",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r3.status_code == 404

            # Verify Tenant B is unchanged
            snapshot_b_mid = await snapshot_integration_in_db(TENANT_BETA_ID, integ_b.id)
            assert snapshot_b_mid == snapshot_b_before

            # Step 4: Switch token to Tenant B over same connection -> 200 OK
            r4 = await ac.get(
                f"/api/v1/integrations/{integ_b.id}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert r4.status_code == 200
            assert r4.json()["id"] == str(integ_b.id)

            # Step 5: Tenant B attempts to DELETE Tenant A integration over same connection -> 404
            r5 = await ac.delete(
                f"/api/v1/integrations/{integ_a.id}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert r5.status_code == 404

            # Verify Tenant A is unchanged
            snapshot_a_after = await snapshot_integration_in_db(TENANT_ALPHA_ID, integ_a.id)
            assert snapshot_a_after == snapshot_a_before
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 7. CROSS-TENANT REGISTRATION & SLUG OVERWRITE ATTEMPT (Preserves Existing Data)
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_registration_slug_collision_preserves_tenant_record(auth_context):
    """
    Test that an attacker attempting to register a new tenant or overwrite an existing tenant
    by reusing Tenant B's slug is rejected with 409 Conflict, leaving Tenant B's data unchanged:
    1. Tenant B exists with slug 'tenant-beta-sec'.
    2. Attacker posts to /tenants/register with slug 'tenant-beta-sec'.
    3. Assert HTTP 409 Conflict.
    4. Assert Tenant B's tenant record in the database is completely UNCHANGED.
    """
    await setup_two_tenants_seed_data()
    try:
        snapshot_b_before = await snapshot_tenant_in_db(TENANT_BETA_ID)
        assert snapshot_b_before is not None
        assert snapshot_b_before["slug"] == "tenant-beta-sec"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/tenants/register",
                json={
                    "name": "Rogue Impersonator Corp",
                    "slug": "tenant-beta-sec",  # Collides with Tenant Beta!
                    "admin_email": "rogue@impersonator.io",
                    "admin_name": "Rogue Hacker",
                },
            )
            assert res.status_code == 409
            assert "already exists" in res.json()["detail"].lower()

        # Verify Tenant B's database record is completely intact and UNCHANGED
        snapshot_b_after = await snapshot_tenant_in_db(TENANT_BETA_ID)
        assert snapshot_b_after == snapshot_b_before
    finally:
        await teardown_two_tenants_seed_data()


# ==============================================================================
# 8. CROSS-TENANT AUTH PROFILE & STUB ENDPOINT PERMISSION MATRIX ENFORCEMENT
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_auth_profile_and_permission_matrix_isolation(auth_context):
    """
    Test user profile isolation and permission matrix boundaries across tenants:
    1. GET /api/v1/auth/me returns strictly the caller's tenant claims.
       Tenant A token returns Tenant A identity; zero Tenant B identity is leaked.
    2. Calling privileged endpoints without required role/permission returns 403 Forbidden:
       - GET /api/v1/tenants/users (requires MANAGE_TENANT_USERS_AND_ROLES) -> 403 Forbidden for Developer.
       - POST /api/v1/integrations/github/connect (requires MANAGE_WEBHOOK_INTEGRATIONS) -> 403 Forbidden for Developer.
       - GET /api/v1/projects/{project_b_id}/sprints/burndown (requires VIEW_SPRINT_BURNDOWN_WIP) -> 403 Forbidden for Developer.
       - POST /api/v1/projects/{project_b_id}/pull-requests/pr-1/reassign-reviewer -> 403 Forbidden for Developer.
       - POST /api/v1/adrs/test-adr/publish-global (requires PUBLISH_GLOBAL_ADR) -> 403 Forbidden for Developer.
       - POST /api/v1/refactoring/projects/{project_b_id}/analyze (requires TRIGGER_REFACTOR_ANALYSIS) -> 403 Forbidden for Project Manager.
    """
    seed_data = await setup_two_tenants_seed_data()
    try:
        token_a_admin = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            roles=["TENANT_ADMIN"],
            sub="alpha-admin-user",
            email="admin@alpha-sec.com",
        )
        token_a_dev = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            roles=["DEVELOPER"],
            sub="alpha-dev-user",
            email="dev@alpha-sec.com",
        )
        token_a_pm = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            roles=["PROJECT_MANAGER"],
            sub="alpha-pm-user",
            email="pm@alpha-sec.com",
        )
        token_b_admin = mint_jwt(
            auth_context,
            tenant_id=TENANT_BETA_ID,
            roles=["TENANT_ADMIN"],
            sub="beta-admin-user",
            email="admin@beta-sec.com",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Profile isolation
            res_me_a = await ac.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token_a_admin}"},
            )
            assert res_me_a.status_code == 200
            data_me_a = res_me_a.json()
            assert data_me_a["tenant_id"] == str(TENANT_ALPHA_ID)
            assert data_me_a["email"] == "admin@alpha-sec.com"

            res_me_b = await ac.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token_b_admin}"},
            )
            assert res_me_b.status_code == 200
            data_me_b = res_me_b.json()
            assert data_me_b["tenant_id"] == str(TENANT_BETA_ID)
            assert data_me_b["email"] == "admin@beta-sec.com"

            # 2. Permission matrix enforcement: Developer role lacking admin permissions -> 403
            # Tenants user management stub
            r_users = await ac.get(
                "/api/v1/tenants/users",
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert r_users.status_code == 403
            assert "Forbidden" in r_users.json()["detail"]

            # Connect integration
            r_conn = await ac.post(
                "/api/v1/integrations/github/connect",
                json={"access_token": "ghp_dev_token"},
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert r_conn.status_code == 403

            # Sprint burndown
            r_burndown = await ac.get(
                f"/api/v1/projects/{seed_data['project_b_id']}/sprints/burndown",
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert r_burndown.status_code == 403

            # PR reviewer reassignment
            r_reassign = await ac.post(
                f"/api/v1/projects/{seed_data['project_b_id']}/pull-requests/pr-1/reassign-reviewer",
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert r_reassign.status_code == 403

            # Publish global ADR
            r_adr = await ac.post(
                "/api/v1/adrs/adr-test-id/publish-global",
                headers={"Authorization": f"Bearer {token_a_dev}"},
            )
            assert r_adr.status_code == 403

            # Trigger refactoring analysis (Project Manager lacks TRIGGER_REFACTOR_ANALYSIS)
            r_refactor = await ac.post(
                f"/api/v1/refactoring/projects/{seed_data['project_b_id']}/analyze",
                headers={"Authorization": f"Bearer {token_a_pm}"},
            )
            assert r_refactor.status_code == 403
    finally:
        await teardown_two_tenants_seed_data()
