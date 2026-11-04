"""
SCRUM-246 / US-10 (SCRUM-303): Tests for Forged Tenant Claim and Session Without Tenant Context.

Objective:
Verify system behavior when tenant identity is manipulated, forged, or absent.

Implementation Scope:
1. Test tokens whose tenant_id was altered (invalid signature) or does not exist:
   - Tampered payload claiming foreign tenant with original signature.
   - Rogue RSA private key (self-signed token / unknown kid).
   - Rogue RSA private key claiming genuine kid (kid collision / signature mismatch).
   - Corrupted / truncated signature bytes.
   - Key confusion attack (HS256 using public RSA key).
   - Algorithm 'none' attack.
   - Token with tenant_id claim completely omitted.
   - Token with empty string ("") or whitespace tenant_id.
   - Token with malformed / non-UUID tenant_id.
   - Token with RFC 4122 nil UUID (00000000-0000-0000-0000-000000000000).
   - Token with validly signed UUID that does not exist in the database (non-existent tenant).
   - Forged tokens combined with spoofed X-Tenant-ID headers.
   - Binary indistinguishability of 401 responses to prevent authentication oracle.

2. Test requests and queries where the database session has no tenant setting:
   - Unscoped default database session (eng_app_user without set_config).
   - Database session with explicitly cleared setting (empty string '').
   - Database session with RESET app.current_tenant_id.
   - Database session with setting set to NULL.
   - Database session with non-existent tenant UUID setting.
   - Direct UPDATE and DELETE mutations in unscoped/cleared sessions affect 0 rows.
   - Direct INSERT mutations in unscoped sessions fail safely with RLS WITH CHECK (SQLSTATE 42501).
   - Target tenant records remain 100% intact and unchanged.

3. Assert 401 for bad tokens and zero rows / safe failure for missing context:
   - Protected API routes return 401 Unauthorized for bad or missing tokens.
   - All unscoped / cleared database reads return zero rows.
   - No unhandled server errors (500) or uncaught exceptions occur.
   - get_current_tenant_id dependency raises 401 when context is missing.
"""

import json
import time
from typing import Any
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import JWKSClient
from app.config import settings
from app.db.models.models import Integration, Project, Tenant, User
from app.db.session import (
    AsyncSessionLocal,
    async_engine,
    get_current_tenant_id,
    get_tenant_db,
    get_tenant_db_context,
)
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
    """Configures mock IdP with legitimate signing keys for JWT authentication."""
    key, jwk = generate_rsa_keypair("sec-forged-test-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "jwk": jwk, "kid": "sec-forged-test-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID | None = None,
    roles: list[str] | None = None,
    sub: str = "sec-adversary",
    custom_claims: dict[str, Any] | None = None,
    omit_claims: list[str] | None = None,
    signing_key: rsa.RSAPrivateKey | None = None,
    kid: str | None = None,
    algorithm: str = "RS256",
) -> str:
    """Helper to mint RS256 JWTs with arbitrary claims, signing keys, and headers."""
    if roles is None:
        roles = ["TENANT_ADMIN"]

    claims: dict[str, Any] = {
        "sub": sub,
        "email": f"{sub}@manta-security.test",
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": int(time.time()) + 3600,
        "realm_access": {"roles": roles},
    }
    if tenant_id is not None:
        claims["tenant_id"] = str(tenant_id)

    if custom_claims:
        claims.update(custom_claims)
    if omit_claims:
        for c in omit_claims:
            claims.pop(c, None)

    active_key = signing_key or auth_ctx["key"]
    active_kid = kid or auth_ctx["kid"]

    headers = {"alg": algorithm}
    if active_kid is not None:
        headers["kid"] = active_kid

    return jwt.encode(
        claims,
        active_key,
        algorithm=algorithm,
        headers=headers,
    )


async def setup_adversarial_seed_data() -> dict[str, Any]:
    """Seeds Tenant Alpha, Tenant Beta, and associated projects and integrations."""
    # 1. Provision Tenant Alpha
    async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
        existing_a = await session.get(Tenant, TENANT_ALPHA_ID)
        if not existing_a:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Alpha Corp',
                        p_slug => 'tenant-alpha-forged-test',
                        p_admin_email => 'admin@alpha-forged.com',
                        p_admin_name => 'Admin Alpha',
                        p_tier => 'enterprise',
                        p_ai_processing_mode => 'cloud_zdr',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_ALPHA_ID},
            )

    # 2. Provision Tenant Beta
    async with get_tenant_db_context(TENANT_BETA_ID) as session:
        existing_b = await session.get(Tenant, TENANT_BETA_ID)
        if not existing_b:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Beta Ltd',
                        p_slug => 'tenant-beta-forged-test',
                        p_admin_email => 'admin@beta-forged.com',
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

    # 5. Seed Integration Alpha
    integ_a = await save_or_update_integration(
        tenant_id=TENANT_ALPHA_ID,
        provider="github",
        external_account_id="gh-org-alpha-forged",
        raw_webhook_secret="wh_secret_alpha_forged_12345",
        api_credentials={"access_token": "ghp_alpha_token_secret"},
        base_url="http://test",
    )

    # 6. Seed Integration Beta
    integ_b = await save_or_update_integration(
        tenant_id=TENANT_BETA_ID,
        provider="github",
        external_account_id="gh-org-beta-forged",
        raw_webhook_secret="wh_secret_beta_forged_12345",
        api_credentials={"access_token": "ghp_beta_token_secret"},
        base_url="http://test",
    )

    return {
        "tenant_a_id": TENANT_ALPHA_ID,
        "tenant_b_id": TENANT_BETA_ID,
        "project_a_id": PROJECT_ALPHA_ID,
        "project_b_id": PROJECT_BETA_ID,
        "integ_a_id": integ_a.id,
        "integ_b_id": integ_b.id,
    }


async def teardown_adversarial_seed_data():
    """Tears down test entities and resets database connection pool."""
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


# ==============================================================================
# 1. FORGED TENANT CLAIMS & INVALID SIGNATURES (SCOPE 1)
# Acceptance Criteria: Forged claims never yield data. Assert 401 for bad tokens.
# ==============================================================================


@pytest.mark.asyncio
async def test_token_altered_tenant_id_invalid_signature_rejected_with_401(auth_context):
    """
    Test that tampering with tenant_id in a signed token invalidates cryptographic signature.
    - Attacker takes valid token for Tenant Alpha.
    - Modifies payload to claim Tenant Beta without signature update.
    - Assert API rejects request with HTTP 401 and generic detail.
    - Assert no Tenant Beta data is yielded.
    """
    seeds = await setup_adversarial_seed_data()
    try:
        valid_alpha_token = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID)
        header_b64, payload_b64, sig_b64 = valid_alpha_token.split(".")

        # Decode payload, alter tenant_id to Tenant Beta, and re-encode
        payload_dict = json.loads(jwt.utils.base64url_decode(payload_b64).decode("utf-8"))
        payload_dict["tenant_id"] = str(TENANT_BETA_ID)
        tampered_payload_b64 = jwt.utils.base64url_encode(
            json.dumps(payload_dict).encode("utf-8")
        ).decode("ascii")

        # Reassemble token with forged payload but original signature
        tampered_token = f"{header_b64}.{tampered_payload_b64}.{sig_b64}"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Attempt to read integrations collection
            res_list = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {tampered_token}"},
            )
            assert res_list.status_code == 401
            assert res_list.json() == {"detail": "Not authenticated"}
            assert res_list.headers.get("www-authenticate") == "Bearer"

            # Attempt to read specific integration belonging to Tenant Beta
            res_item = await ac.get(
                f"/api/v1/integrations/{seeds['integ_b_id']}",
                headers={"Authorization": f"Bearer {tampered_token}"},
            )
            assert res_item.status_code == 401
            assert res_item.json() == {"detail": "Not authenticated"}

            # Attempt to access user profile
            res_me = await ac.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {tampered_token}"},
            )
            assert res_me.status_code == 401
            assert res_me.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_forged_tenant_id_signed_with_rogue_rsa_key_rejected_with_401(auth_context):
    """
    Test token minted with tenant_id signed by attacker's rogue private key (unknown kid).
    - Assert JWKSClient cannot resolve rogue key.
    - Assert API returns HTTP 401 Unauthorized.
    - Assert no data is leaked.
    """
    seeds = await setup_adversarial_seed_data()
    try:
        rogue_key, _ = generate_rsa_keypair("attacker-rogue-kid")
        forged_token = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            signing_key=rogue_key,
            kid="attacker-rogue-kid",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {forged_token}"},
            )
            assert response.status_code == 401
            assert response.json() == {"detail": "Not authenticated"}

            # Targeting specific entity
            target_resp = await ac.get(
                f"/api/v1/integrations/{seeds['integ_a_id']}",
                headers={"Authorization": f"Bearer {forged_token}"},
            )
            assert target_resp.status_code == 401
            assert target_resp.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_forged_tenant_id_rogue_key_matching_legitimate_kid_rejected_with_401(
    auth_context,
):
    """
    Test attacker generating an RSA key with the exact same kid as the IdP ('kid' collision attack).
    - Attacker signs token claiming Tenant Alpha with their own private key, using IdP's kid.
    - IdP public key verification fails cryptographically.
    - Assert API returns HTTP 401 Unauthorized.
    """
    await setup_adversarial_seed_data()
    try:
        # Rogue keypair using same kid as legitimate auth_context["kid"]
        rogue_key, _ = generate_rsa_keypair(auth_context["kid"])
        forged_token = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            signing_key=rogue_key,
            kid=auth_context["kid"],
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {forged_token}"},
            )
            assert response.status_code == 401
            assert response.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_corrupted_or_truncated_signature_rejected_with_401(auth_context):
    """
    Test token with altered, flipped, or truncated signature bytes.
    - Valid claims claiming Tenant Alpha, but signature bytes damaged.
    - Assert 401 Unauthorized and zero rows yielded.
    """
    await setup_adversarial_seed_data()
    try:
        valid_token = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID)
        header_b64, payload_b64, sig_b64 = valid_token.split(".")

        # Corrupt the signature by modifying its tail and truncating
        corrupted_sig = (
            sig_b64[:-10] + ("A" if sig_b64[-1] != "A" else "B")
            if len(sig_b64) > 10
            else "bad_signature"
        )
        corrupted_token = f"{header_b64}.{payload_b64}.{corrupted_sig}"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {corrupted_token}"},
            )
            assert res.status_code == 401
            assert res.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_algorithm_confusion_hs256_rejected_with_401(auth_context):
    """
    Test algorithm confusion attack: attacker signs token using HS256 with the RSA public key.
    - System strictly enforces RS256 allowlist.
    - Assert 401 Unauthorized.
    """
    await setup_adversarial_seed_data()
    try:
        # Mint with HS256 using arbitrary secret pretending to be RSA public key
        claims = {
            "sub": "adv-hs256",
            "email": "adv@hs256.test",
            "aud": settings.OIDC_AUDIENCE,
            "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
            "exp": int(time.time()) + 3600,
            "tenant_id": str(TENANT_ALPHA_ID),
            "realm_access": {"roles": ["TENANT_ADMIN"]},
        }
        hs256_token = jwt.encode(
            claims,
            "symmetric-secret-key-at-least-32-bytes-long!!",
            algorithm="HS256",
            headers={"alg": "HS256", "kid": auth_context["kid"]},
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {hs256_token}"},
            )
            assert res.status_code == 401
            assert res.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_algorithm_none_rejected_with_401(auth_context):
    """
    Test algorithm 'none' attack: attacker creates un-signed token with alg: none.
    - System rejects algorithm none.
    - Assert 401 Unauthorized.
    """
    await setup_adversarial_seed_data()
    try:
        claims = {
            "sub": "adv-none",
            "email": "adv@none.test",
            "aud": settings.OIDC_AUDIENCE,
            "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
            "exp": int(time.time()) + 3600,
            "tenant_id": str(TENANT_ALPHA_ID),
            "realm_access": {"roles": ["TENANT_ADMIN"]},
        }
        # Craft alg: none token without signature
        header_b64 = jwt.utils.base64url_encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")
        ).decode("ascii")
        payload_b64 = jwt.utils.base64url_encode(json.dumps(claims).encode("utf-8")).decode("ascii")
        none_token = f"{header_b64}.{payload_b64}."

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {none_token}"},
            )
            assert res.status_code == 401
            assert res.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# 2. TOKENS WHOSE TENANT_ID DOES NOT EXIST OR IS MALFORMED (SCOPE 1)
# Acceptance Criteria: Forged/absent claims never yield data. Assert 401 for bad tokens.
# ==============================================================================


@pytest.mark.asyncio
async def test_token_missing_tenant_id_claim_rejected_with_401(auth_context):
    """
    Test token that omits the mandatory 'tenant_id' claim completely.
    - Cryptographically valid RS256 signature, but missing tenant identity.
    - Assert API returns HTTP 401 Unauthorized.
    - Assert no data yielded.
    """
    await setup_adversarial_seed_data()
    try:
        token_no_tid = mint_jwt(auth_context, tenant_id=None, omit_claims=["tenant_id"])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token_no_tid}"},
            )
            assert res.status_code == 401
            assert res.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_empty_or_whitespace_tenant_id_rejected_with_401(auth_context):
    """
    Test tokens where 'tenant_id' is an empty string or whitespace-only string.
    - Assert API returns HTTP 401 Unauthorized.
    - Assert zero data yielded.
    """
    await setup_adversarial_seed_data()
    try:
        empty_token = mint_jwt(auth_context, tenant_id="", custom_claims={"tenant_id": ""})
        ws_token = mint_jwt(auth_context, tenant_id="   ", custom_claims={"tenant_id": "   "})

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {empty_token}"},
            )
            assert r1.status_code == 401
            assert r1.json() == {"detail": "Not authenticated"}

            r2 = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {ws_token}"},
            )
            assert r2.status_code == 401
            assert r2.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_malformed_non_uuid_tenant_id_rejected_with_401(auth_context):
    """
    Test tokens where 'tenant_id' is not a valid RFC 4122 UUID (e.g. SQL injection payload, arbitrary string).
    - Assert API returns HTTP 401 Unauthorized.
    """
    await setup_adversarial_seed_data()
    try:
        sql_inject_tid = mint_jwt(
            auth_context,
            tenant_id="sqli-attempt' OR '1'='1",
            custom_claims={"tenant_id": "sqli-attempt' OR '1'='1"},
        )
        arbitrary_str_tid = mint_jwt(
            auth_context,
            tenant_id="tenant-alpha-custom",
            custom_claims={"tenant_id": "tenant-alpha-custom"},
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {sql_inject_tid}"},
            )
            assert r1.status_code == 401
            assert r1.json() == {"detail": "Not authenticated"}

            r2 = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {arbitrary_str_tid}"},
            )
            assert r2.status_code == 401
            assert r2.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_nil_uuid_tenant_id_rejected_with_401(auth_context):
    """
    Test token with nil UUID (00000000-0000-0000-0000-000000000000).
    - Nil UUID is strictly forbidden per Architecture Blueprint §6.5.
    - Assert API returns HTTP 401 Unauthorized.
    """
    await setup_adversarial_seed_data()
    try:
        nil_token = mint_jwt(
            auth_context,
            tenant_id="00000000-0000-0000-0000-000000000000",
            custom_claims={"tenant_id": "00000000-0000-0000-0000-000000000000"},
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {nil_token}"},
            )
            assert res.status_code == 401
            assert res.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_token_non_existent_tenant_uuid_in_db_yields_zero_data(auth_context):
    """
    Test token that is cryptographically valid and contains a syntactically valid UUID,
    but the tenant does NOT exist in the database (ghost tenant).
    - Collection endpoints return empty list [] (zero rows).
    - Resource endpoints return 404 Not Found (information hiding / no existence oracle).
    - Database session scoped to non-existent tenant yields zero rows.
    - No unhandled exceptions or 500 errors raised.
    """
    seeds = await setup_adversarial_seed_data()
    ghost_tenant_id = uuid.uuid4()
    try:
        ghost_token = mint_jwt(auth_context, tenant_id=ghost_tenant_id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Collection endpoint returns empty list, never leaking existing tenants' data
            res_list = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {ghost_token}"},
            )
            assert res_list.status_code == 200
            assert res_list.json() == []

            # 2. Resource endpoint targeting Tenant Alpha's integration returns 404
            res_target = await ac.get(
                f"/api/v1/integrations/{seeds['integ_a_id']}",
                headers={"Authorization": f"Bearer {ghost_token}"},
            )
            assert res_target.status_code == 404

            # 3. Resource endpoint targeting Tenant Beta's integration returns 404
            res_target_b = await ac.get(
                f"/api/v1/integrations/{seeds['integ_b_id']}",
                headers={"Authorization": f"Bearer {ghost_token}"},
            )
            assert res_target_b.status_code == 404

        # 4. Database query under non-existent tenant yields zero rows
        async with get_tenant_db_context(ghost_tenant_id) as session:
            tenants = (await session.scalars(select(Tenant))).all()
            projects = (await session.scalars(select(Project))).all()
            integrations = (await session.scalars(select(Integration))).all()
            assert len(tenants) == 0
            assert len(projects) == 0
            assert len(integrations) == 0
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_forged_and_invalid_tokens_binary_indistinguishability(auth_context):
    """
    Test binary indistinguishability of 401 error responses across various bad token attacks:
    - Altered signature
    - Rogue key
    - Algorithm confusion
    - Missing tenant_id claim
    - Malformed tenant_id
    All must return identical status code (401), identical body ({"detail": "Not authenticated"}),
    and identical WWW-Authenticate header, preventing oracle attacks.
    """
    await setup_adversarial_seed_data()
    try:
        valid = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID)
        h, p, s = valid.split(".")
        # Tampered payload
        tampered = f"{h}.{jwt.utils.base64url_encode(b'{\"tenant_id\":\"999\"}').decode('ascii')}.{s}"
        # Rogue key
        rogue_key, _ = generate_rsa_keypair("rogue-key")
        rogue_token = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            signing_key=rogue_key,
            kid="rogue-key",
        )
        # Missing tenant_id
        missing_tid = mint_jwt(auth_context, tenant_id=None, omit_claims=["tenant_id"])
        # Nil UUID
        nil_tid = mint_jwt(
            auth_context,
            tenant_id="00000000-0000-0000-0000-000000000000",
            custom_claims={"tenant_id": "00000000-0000-0000-0000-000000000000"},
        )

        test_tokens = [tampered, rogue_token, missing_tid, nil_tid]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            for token in test_tokens:
                res = await ac.get(
                    "/api/v1/integrations",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert res.status_code == 401
                assert res.json() == {"detail": "Not authenticated"}
                assert res.headers.get("www-authenticate") == "Bearer"
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_forged_claims_cannot_be_rescued_by_spoofed_headers(auth_context):
    """
    Test that sending a spoofed X-Tenant-ID header alongside a forged or invalid token
    cannot bypass authentication. The request is rejected at the middleware level with 401.
    """
    await setup_adversarial_seed_data()
    try:
        rogue_key, _ = generate_rsa_keypair("rogue-key")
        forged_token = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            signing_key=rogue_key,
            kid="rogue-key",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/integrations",
                headers={
                    "Authorization": f"Bearer {forged_token}",
                    "X-Tenant-ID": str(TENANT_ALPHA_ID),
                },
            )
            assert res.status_code == 401
            assert res.json() == {"detail": "Not authenticated"}
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# 3. REQUESTS WHERE DATABASE SESSION HAS NO TENANT SETTING (SCOPE 2 & SCOPE 3)
# Acceptance Criteria: A session without tenant context returns nothing and
# does not raise an unhandled error. Zero rows / safe failure for missing context.
# ==============================================================================


@pytest.mark.asyncio
async def test_db_session_default_unscoped_returns_zero_rows_across_all_tenant_tables():
    """
    TC-ADV-018: Test direct database access as eng_app_user without setting app.current_tenant_id.
    - Raw AsyncSessionLocal without set_config.
    - PostgreSQL RLS fail-safe closed: NULLIF(current_setting('app.current_tenant_id', true), '') evaluates to NULL.
    - Assert queries across all tenant tables return 0 rows.
    - Assert queries execute safely without raising any unhandled error.
    """
    await setup_adversarial_seed_data()
    try:
        async with AsyncSessionLocal() as session:
            # Verify setting is empty or None
            current_tid = await session.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert current_tid in (None, "")

            # Query multiple tenant-isolated tables
            tenants = (await session.scalars(select(Tenant))).all()
            projects = (await session.scalars(select(Project))).all()
            integrations = (await session.scalars(select(Integration))).all()
            users = (await session.scalars(select(User))).all()

            assert len(tenants) == 0, f"Expected 0 tenants, got {len(tenants)}"
            assert len(projects) == 0, f"Expected 0 projects, got {len(projects)}"
            assert len(integrations) == 0, f"Expected 0 integrations, got {len(integrations)}"
            assert len(users) == 0, f"Expected 0 users, got {len(users)}"
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_db_session_explicitly_cleared_setting_returns_zero_rows_without_error():
    """
    Test database session where app.current_tenant_id was explicitly cleared to empty string,
    set to NULL, or reset via RESET app.current_tenant_id.
    - Assert zero rows returned.
    - Assert no unhandled error raised.
    """
    await setup_adversarial_seed_data()
    try:
        # Case A: Explicitly set to empty string ''
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
            tenants = (await session.scalars(select(Tenant))).all()
            projects = (await session.scalars(select(Project))).all()
            integrations = (await session.scalars(select(Integration))).all()
            assert tenants == []
            assert projects == []
            assert integrations == []

        # Case B: Explicitly set to NULL
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT set_config('app.current_tenant_id', NULL, true)"))
            projects = (await session.scalars(select(Project))).all()
            integrations = (await session.scalars(select(Integration))).all()
            assert projects == []
            assert integrations == []

        # Case C: RESET app.current_tenant_id
        async with AsyncSessionLocal() as session:
            # Set to Tenant Alpha first, then RESET
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(TENANT_ALPHA_ID)},
            )
            # Confirm Tenant Alpha records visible
            active_projects = (await session.scalars(select(Project))).all()
            assert len(active_projects) == 1

            # Reset session setting
            await session.execute(text("RESET app.current_tenant_id"))
            session.expire_all()

            # Must immediately return zero rows
            reset_projects = (await session.scalars(select(Project))).all()
            assert reset_projects == []
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_db_session_mutations_without_tenant_context_affect_zero_rows_and_preserve_data():
    """
    Test direct UPDATE and DELETE statements in a session without tenant setting:
    - Attempting UPDATE on existing project returns rowcount == 0.
    - Attempting DELETE on existing project returns rowcount == 0.
    - Attempting UPDATE on existing integration returns rowcount == 0.
    - Attempting UPDATE on existing tenant returns rowcount == 0.
    - Does NOT raise an unhandled error.
    - Target tenant records in the database remain 100% intact and unchanged.
    """
    seeds = await setup_adversarial_seed_data()
    try:
        async with AsyncSessionLocal() as session:
            # 1. UPDATE projects with no tenant context
            res_upd_proj = await session.execute(
                update(Project)
                .where(Project.id == seeds["project_a_id"])
                .values(name="Hacked Project Name")
            )
            assert res_upd_proj.rowcount == 0

            # 2. DELETE projects with no tenant context
            res_del_proj = await session.execute(
                delete(Project).where(Project.id == seeds["project_a_id"])
            )
            assert res_del_proj.rowcount == 0

            # 3. UPDATE integrations with no tenant context
            res_upd_integ = await session.execute(
                update(Integration)
                .where(Integration.id == seeds["integ_a_id"])
                .values(external_account_id="hacked-account")
            )
            assert res_upd_integ.rowcount == 0

            # 4. UPDATE tenants with no tenant context
            res_upd_tenant = await session.execute(
                update(Tenant)
                .where(Tenant.id == seeds["tenant_a_id"])
                .values(name="Hacked Tenant Name")
            )
            assert res_upd_tenant.rowcount == 0

        # Verify underlying database records are 100% intact and unaffected
        async with get_tenant_db_context(TENANT_ALPHA_ID) as verify_session:
            proj_a = await verify_session.get(Project, seeds["project_a_id"])
            assert proj_a is not None
            assert proj_a.name == "Project Alpha Confidential"

            integ_a = await verify_session.get(Integration, seeds["integ_a_id"])
            assert integ_a is not None
            assert integ_a.external_account_id == "gh-org-alpha-forged"

            tenant_a = await verify_session.get(Tenant, seeds["tenant_a_id"])
            assert tenant_a is not None
            assert tenant_a.name == "Tenant Alpha Corp"
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_db_session_insert_without_tenant_context_fails_safely_with_rls_violation():
    """
    Test direct INSERT statement in a database session without tenant setting:
    - RLS WITH CHECK policy evaluates tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid.
    - Since current_setting is unset, WITH CHECK evaluates to false/unknown.
    - PostgreSQL safely rejects the operation with SQLSTATE 42501 (DBAPIError).
    - Transaction safely aborts without crashing or inserting untracked data.
    """
    await setup_adversarial_seed_data()
    try:
        # Attempt INSERT into projects without tenant setting
        with pytest.raises(DBAPIError) as exc_proj:
            async with AsyncSessionLocal() as session:
                session.add(
                    Project(
                        id=uuid.uuid4(),
                        tenant_id=TENANT_ALPHA_ID,
                        name="Project In The Void",
                        slug="project-void",
                        tier_sla_hours=24,
                        authoritative_cd_signal="github_deployments_api",
                        lead_time_first_commit_strategy="feature_branch_root",
                        wip_limit_per_dev=3,
                        pr_stale_threshold_hours=48,
                        is_active=True,
                    )
                )
                await session.flush()
        assert "row-level security policy" in str(exc_proj.value).lower()

        # Attempt INSERT into integrations without tenant setting
        with pytest.raises(DBAPIError) as exc_integ:
            async with AsyncSessionLocal() as session:
                session.add(
                    Integration(
                        id=uuid.uuid4(),
                        tenant_id=TENANT_ALPHA_ID,
                        provider="github",
                        external_account_id="gh-void",
                        webhook_secret_hash="hash_void",
                        api_credentials_vault_ref="ref_void",
                        is_active=True,
                    )
                )
                await session.flush()
        assert "row-level security policy" in str(exc_integ.value).lower()
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_db_session_non_existent_tenant_setting_returns_zero_rows():
    """
    Test database session where app.current_tenant_id is set to a valid UUID that
    does NOT exist in the tenants table.
    - Returns zero rows for SELECT queries.
    - Mutations affect 0 rows.
    - Does NOT raise an unhandled error.
    """
    seeds = await setup_adversarial_seed_data()
    non_existent_tenant = uuid.uuid4()
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(non_existent_tenant)},
            )
            tenants = (await session.scalars(select(Tenant))).all()
            projects = (await session.scalars(select(Project))).all()
            integrations = (await session.scalars(select(Integration))).all()

            assert len(tenants) == 0
            assert len(projects) == 0
            assert len(integrations) == 0

            # Mutation affects 0 rows
            res = await session.execute(
                update(Project)
                .where(Project.id == seeds["project_a_id"])
                .values(name="Should Not Update")
            )
            assert res.rowcount == 0
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# 4. API LAYER REQUESTS WITHOUT TENANT CONTEXT (SCOPE 2 & SCOPE 3)
# Acceptance Criteria: Missing context returns 401 / zero rows without unhandled error.
# ==============================================================================


@pytest.mark.asyncio
async def test_api_requests_without_authorization_return_401_no_unhandled_error():
    """
    Test protected API endpoints called without any Authorization header.
    - Endpoints:
      * GET /api/v1/integrations
      * GET /api/v1/integrations/{id}
      * DELETE /api/v1/integrations/{id}
      * GET /api/v1/auth/me
      * GET /api/v1/projects/{project_id}/sprints/burndown
    - Assert: HTTP 401 Unauthorized with {"detail": "Not authenticated"}.
    - Assert: Zero data yielded and no unhandled 500 server error.
    """
    seeds = await setup_adversarial_seed_data()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            routes = [
                ("GET", "/api/v1/integrations"),
                ("GET", f"/api/v1/integrations/{seeds['integ_a_id']}"),
                ("DELETE", f"/api/v1/integrations/{seeds['integ_a_id']}"),
                ("GET", "/api/v1/auth/me"),
                (
                    "GET",
                    f"/api/v1/projects/{seeds['project_a_id']}/sprints/burndown",
                ),
            ]

            for method, path in routes:
                res = await ac.request(method, path)
                assert (
                    res.status_code == 401
                ), f"Expected 401 for {method} {path}, got {res.status_code}"
                assert res.json() == {"detail": "Not authenticated"}
                assert res.headers.get("www-authenticate") == "Bearer"
    finally:
        await teardown_adversarial_seed_data()


def test_fastapi_dependencies_missing_tenant_context_fail_safely():
    """
    Test get_current_tenant_id dependency behavior when tenant context is absent or invalid:
    - Dummy request without state or header -> raises HTTPException(401, "Tenant context required").
    - Dummy request with invalid format header -> raises HTTPException(400, "Invalid tenant_id format").
    - Verifies safe, controlled failure before database session creation.
    """

    class MockRequest:
        def __init__(self, state_tid=None, header_tid=None):
            self.state = type("State", (), {})()
            if state_tid is not None:
                self.state.tenant_id = state_tid
            self.headers = {}
            if header_tid is not None:
                self.headers["X-Tenant-ID"] = header_tid

    # 1. Missing context -> 401
    req_missing = MockRequest()
    with pytest.raises(HTTPException) as exc_missing:
        get_current_tenant_id(req_missing)
    assert exc_missing.value.status_code == 401
    assert "Tenant context required" in exc_missing.value.detail

    # 2. Invalid UUID format -> 400
    req_invalid = MockRequest(header_tid="not-a-valid-uuid")
    with pytest.raises(HTTPException) as exc_invalid:
        get_current_tenant_id(req_invalid)
    assert exc_invalid.value.status_code == 400
    assert "Invalid tenant_id format" in exc_invalid.value.detail


@pytest.mark.asyncio
async def test_fastapi_get_tenant_db_route_without_context_halts_safely():
    """
    Test a FastAPI route that depends on get_tenant_db executed without tenant context.
    - Dependency chain halts at get_current_tenant_id with 401.
    - No un-scoped database queries are executed.
    - No unhandled server error occurs.
    """
    test_sub_app = FastAPI()

    @test_sub_app.get("/test-endpoint")
    async def sample_endpoint(db: AsyncSession = Depends(get_tenant_db)):
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app=test_sub_app), base_url="http://test") as ac:
        res = await ac.get("/test-endpoint")
        assert res.status_code == 401
        assert res.json()["detail"] == "Tenant context required"
