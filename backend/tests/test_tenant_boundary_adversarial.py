"""
SCRUM-246 / US-10 (SCRUM-301): Adversarial Test Suite for Tenant Boundaries.

Adversarial test scenarios verifying multi-tenant boundary defenses across both
API and Database (PostgreSQL RLS) layers:
1. Cross-tenant read and write by ID (API: 404, DB: zero rows / RLS violation).
2. Forged tenant_id claim (Self-signed token, tampered claims, spoofed headers -> 401).
3. Missing tenant context (Unauthenticated, missing/nil tenant claim -> 401; DB cleared -> zero rows / violation).
4. Tenant switch mid-session (HTTP keep-alive session reuse, DB session boundary shift -> zero leak).
5. Direct DB access as the app role (eng_app_user without context returns zero rows, SET ROW SECURITY OFF ignored, direct INSERT on tenants denied).
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
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.auth import JWKSClient
from app.config import settings
from app.db.models.models import Integration, Project, Tenant, User
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context
from app.integrations.service import save_or_update_integration
from app.main import app

# Deterministic Multi-Tenant Test Entities
TENANT_ALPHA_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_BETA_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

PROJECT_ALPHA_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROJECT_BETA_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generates an RSA private key and corresponding JWK."""
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
    """Configures mock IdP with legitimate signing keys for JWT authentication."""
    key, jwk = generate_rsa_keypair("adv-key-1")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "adv-key-1"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: str | uuid.UUID | None,
    roles: list[str] | None = None,
    sub: str = "adv-attacker",
    custom_claims: dict[str, Any] | None = None,
    omit_claims: list[str] | None = None,
    signing_key: rsa.RSAPrivateKey | None = None,
    kid: str | None = None,
) -> str:
    """Helper to mint RS256 JWTs with arbitrary tenant context, claims, and signing keys."""
    if roles is None:
        roles = ["TENANT_ADMIN"]

    claims: dict[str, Any] = {
        "sub": sub,
        "email": f"{sub}@adversarial.test",
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

    return jwt.encode(
        claims,
        active_key,
        algorithm="RS256",
        headers={"alg": "RS256", "kid": active_kid},
    )


async def setup_adversarial_seed_data():
    """Ensures deterministic Tenant Alpha and Tenant Beta records exist in DB."""
    async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
        existing_a = await session.get(Tenant, TENANT_ALPHA_ID)
        if not existing_a:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Alpha Corp',
                        p_slug => 'tenant-alpha-adv',
                        p_admin_email => 'admin@alpha-adv.com',
                        p_admin_name => 'Admin Alpha',
                        p_tier => 'enterprise',
                        p_ai_processing_mode => 'cloud_zdr',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_ALPHA_ID},
            )

    async with get_tenant_db_context(TENANT_BETA_ID) as session:
        existing_b = await session.get(Tenant, TENANT_BETA_ID)
        if not existing_b:
            await session.execute(
                text(
                    """
                    SELECT * FROM provision_tenant(
                        p_name => 'Tenant Beta Ltd',
                        p_slug => 'tenant-beta-adv',
                        p_admin_email => 'admin@beta-adv.com',
                        p_admin_name => 'Admin Beta',
                        p_tier => 'starter',
                        p_ai_processing_mode => 'local',
                        p_tenant_id => :tid
                    )
                    """
                ),
                {"tid": TENANT_BETA_ID},
            )

    # Seed Project Alpha under Tenant Alpha
    async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
        proj_a = await session.get(Project, PROJECT_ALPHA_ID)
        if not proj_a:
            session.add(
                Project(
                    id=PROJECT_ALPHA_ID,
                    tenant_id=TENANT_ALPHA_ID,
                    name="Project Alpha Secret",
                    slug="project-alpha-secret",
                    tier_sla_hours=24,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=3,
                    pr_stale_threshold_hours=48,
                    is_active=True,
                )
            )

    # Seed Project Beta under Tenant Beta
    async with get_tenant_db_context(TENANT_BETA_ID) as session:
        proj_b = await session.get(Project, PROJECT_BETA_ID)
        if not proj_b:
            session.add(
                Project(
                    id=PROJECT_BETA_ID,
                    tenant_id=TENANT_BETA_ID,
                    name="Project Beta Secret",
                    slug="project-beta-secret",
                    tier_sla_hours=48,
                    authoritative_cd_signal="github_deployments_api",
                    lead_time_first_commit_strategy="feature_branch_root",
                    wip_limit_per_dev=5,
                    pr_stale_threshold_hours=72,
                    is_active=True,
                )
            )


async def teardown_adversarial_seed_data():
    """Teardown test entities and clean connection pool."""
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
# VECTOR 1: Cross-Tenant Read and Write by ID
# ==============================================================================


@pytest.mark.asyncio
async def test_adv_001_cross_tenant_read_by_id_api_and_db(auth_context):
    """
    TC-ADV-001: Cross-Tenant Read by ID.
    - Attacker (Tenant Beta) requests Tenant Alpha's Integration ID via API.
    - Stopping Layer: API returns 404 Not Found (avoids tenant enumeration).
    - Database Layer: PostgreSQL RLS USING clause filters out row -> returns zero rows.
    """
    await setup_adversarial_seed_data()
    try:
        # Create an integration owned by Tenant Alpha
        integ_a = await save_or_update_integration(
            tenant_id=TENANT_ALPHA_ID,
            provider="github",
            external_account_id="gh-org-alpha",
            raw_webhook_secret="wh_secret_alpha_12345",
            api_credentials={"access_token": "ghp_alpha_token"},
            base_url="http://test",
        )
        integ_a_id = integ_a.id

        token_beta = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID)

        # 1. API Layer Verification: Tenant Beta attempts to read Tenant Alpha's integration by ID
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/integrations/{integ_a_id}",
                headers={"Authorization": f"Bearer {token_beta}"},
            )
            assert resp.status_code == 404
            assert f"Integration '{integ_a_id}' not found" in resp.json()["detail"]

        # 2. Database Layer Verification: Querying directly under Tenant Beta context returns zero rows
        async with get_tenant_db_context(TENANT_BETA_ID) as session:
            stmt = select(Integration).where(Integration.id == integ_a_id)
            result = await session.scalars(stmt)
            rows = result.all()
            assert len(rows) == 0, f"Expected zero rows, but found {len(rows)} rows"
    finally:
        await teardown_adversarial_seed_data()


@pytest.mark.asyncio
async def test_adv_002_cross_tenant_write_by_id_api_and_db(auth_context):
    """
    TC-ADV-002: Cross-Tenant Write by ID (UPDATE / DELETE / INSERT).
    - Attacker (Tenant Beta) attempts to DELETE Tenant Alpha's integration by ID.
    - Stopping Layer (API): Returns 404 Not Found.
    - Stopping Layer (Database):
      * UPDATE/DELETE: USING clause filters target -> zero rows affected (rowcount == 0).
      * INSERT: WITH CHECK clause fails -> PostgreSQL SQLSTATE 42501 (DBAPIError).
      * REASSIGNMENT: Changing tenant_id -> WITH CHECK fails -> SQLSTATE 42501.
    """
    await setup_adversarial_seed_data()
    try:
        integ_a = await save_or_update_integration(
            tenant_id=TENANT_ALPHA_ID,
            provider="github",
            external_account_id="gh-org-alpha-write",
            raw_webhook_secret="wh_secret_alpha_write",
            api_credentials={"access_token": "ghp_alpha_write"},
            base_url="http://test",
        )
        integ_a_id = integ_a.id
        token_beta = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID)

        # 1. API Layer: Tenant Beta tries to delete Tenant Alpha's integration -> 404 Not Found
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            del_resp = await ac.delete(
                f"/api/v1/integrations/{integ_a_id}",
                headers={"Authorization": f"Bearer {token_beta}"},
            )
            assert del_resp.status_code == 404

        # 2. Database Layer: Cross-tenant UPDATE affects 0 rows
        async with get_tenant_db_context(TENANT_BETA_ID) as session:
            upd_res = await session.execute(
                update(Project).where(Project.id == PROJECT_ALPHA_ID).values(name="Tampered Name")
            )
            assert upd_res.rowcount == 0

        # 3. Database Layer: Cross-tenant DELETE affects 0 rows
        async with get_tenant_db_context(TENANT_BETA_ID) as session:
            del_res = await session.execute(delete(Project).where(Project.id == PROJECT_ALPHA_ID))
            assert del_res.rowcount == 0

        # 4. Database Layer: Cross-tenant INSERT violates WITH CHECK policy (SQLSTATE 42501)
        with pytest.raises(DBAPIError) as exc_info:
            async with get_tenant_db_context(TENANT_BETA_ID) as session:
                session.add(
                    Project(
                        id=uuid.uuid4(),
                        tenant_id=TENANT_ALPHA_ID,  # Mismatched tenant_id!
                        name="Rogue Injected Project",
                        slug="rogue-project",
                        tier_sla_hours=24,
                        authoritative_cd_signal="github_deployments_api",
                        lead_time_first_commit_strategy="feature_branch_root",
                        wip_limit_per_dev=3,
                        pr_stale_threshold_hours=48,
                        is_active=True,
                    )
                )
                await session.flush()
        assert "row-level security policy" in str(exc_info.value).lower()

        # 5. Database Layer: Tenant A attempts to reassign its project to Tenant B -> WITH CHECK fails
        with pytest.raises(DBAPIError) as exc_info_reassign:
            async with get_tenant_db_context(TENANT_ALPHA_ID) as session:
                await session.execute(
                    update(Project)
                    .where(Project.id == PROJECT_ALPHA_ID)
                    .values(tenant_id=TENANT_BETA_ID)
                )
                await session.flush()
        assert "row-level security policy" in str(exc_info_reassign.value).lower()
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# VECTOR 2: Forged tenant_id Claim
# ==============================================================================


@pytest.mark.asyncio
async def test_adv_003_forged_tenant_id_claims_api(auth_context):
    """
    TC-ADV-003: Forged tenant_id Claim.
    - Attacker crafts self-signed token claiming Tenant Alpha.
    - Attacker tampers with valid Token Alpha to claim Tenant Beta without signature update.
    - Attacker passes valid Token Beta but adds spoofed 'X-Tenant-ID: <Tenant Alpha>' header.
    - Stopping Layer: API (JWTAuthMiddleware / Token Validation).
    - Expected Result: 401 Unauthorized (self-signed/tampered); spoofed header ignored (404 on target).
    """
    await setup_adversarial_seed_data()
    try:
        # 1. Attacker signs token with rogue private key claiming Tenant Alpha
        rogue_key, _ = generate_rsa_keypair("rogue-attacker-key")
        forged_self_signed = mint_jwt(
            auth_context,
            tenant_id=TENANT_ALPHA_ID,
            signing_key=rogue_key,
            kid="rogue-attacker-key",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_rogue = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {forged_self_signed}"},
            )
            assert resp_rogue.status_code == 401
            assert resp_rogue.json() == {"detail": "Not authenticated"}

        # 2. Tampered token: take valid token for Tenant Alpha, swap tenant_id in payload
        valid_alpha = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID)
        header_b64, payload_b64, sig_b64 = valid_alpha.split(".")
        # Replace payload with Tenant Beta's ID
        payload_dict = json.loads(jwt.utils.base64url_decode(payload_b64).decode("utf-8"))
        payload_dict["tenant_id"] = str(TENANT_BETA_ID)
        tampered_payload_b64 = jwt.utils.base64url_encode(
            json.dumps(payload_dict).encode("utf-8")
        ).decode("ascii")
        tampered_token = f"{header_b64}.{tampered_payload_b64}.{sig_b64}"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_tampered = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {tampered_token}"},
            )
            assert resp_tampered.status_code == 401
            assert resp_tampered.json() == {"detail": "Not authenticated"}

        # 3. Spoofed X-Tenant-ID header: Tenant Beta passes valid Token Beta + X-Tenant-ID: Tenant Alpha
        integ_a = await save_or_update_integration(
            tenant_id=TENANT_ALPHA_ID,
            provider="github",
            external_account_id="gh-org-spoof",
            raw_webhook_secret="wh_secret_spoof",
            api_credentials={"access_token": "ghp_spoof"},
            base_url="http://test",
        )
        token_beta = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Header attempts to trick API into querying Tenant Alpha's integration
            resp_spoofed = await ac.get(
                f"/api/v1/integrations/{integ_a.id}",
                headers={
                    "Authorization": f"Bearer {token_beta}",
                    "X-Tenant-ID": str(TENANT_ALPHA_ID),
                },
            )
            # Must remain 404 because API derives tenant strictly from verified JWT, ignoring header
            assert resp_spoofed.status_code == 404
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# VECTOR 3: Missing Tenant Context
# ==============================================================================


@pytest.mark.asyncio
async def test_adv_004_missing_tenant_context_api_and_db(auth_context):
    """
    TC-ADV-004: Missing Tenant Context.
    - Attacker calls API with no token -> 401 Unauthorized.
    - Attacker calls API with token missing 'tenant_id' / empty / nil UUID -> 401 Unauthorized.
    - Attacker / query accesses DB with cleared or unset 'app.current_tenant_id'.
    - Stopping Layer:
      * API: JWTAuthMiddleware returns 401.
      * DB: RLS returns zero rows (fail-safe closed), and blocks INSERT with SQLSTATE 42501.
    """
    await setup_adversarial_seed_data()
    try:
        # 1. API: No Authorization header
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_no_token = await ac.get("/api/v1/integrations")
            assert resp_no_token.status_code == 401

        # 2. API: Token omitting tenant_id entirely
        token_missing_tid = mint_jwt(auth_context, tenant_id=None, omit_claims=["tenant_id"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_missing_tid = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token_missing_tid}"},
            )
            assert resp_missing_tid.status_code == 401

        # 3. API: Token with empty string tenant_id
        token_empty_tid = mint_jwt(auth_context, tenant_id="", custom_claims={"tenant_id": ""})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_empty_tid = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token_empty_tid}"},
            )
            assert resp_empty_tid.status_code == 401

        # 4. API: Token with nil UUID tenant_id
        token_nil_tid = mint_jwt(
            auth_context,
            tenant_id="00000000-0000-0000-0000-000000000000",
            custom_claims={"tenant_id": "00000000-0000-0000-0000-000000000000"},
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp_nil_tid = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token_nil_tid}"},
            )
            assert resp_nil_tid.status_code == 401

        # 5. Database Layer: Unset / cleared tenant setting returns zero rows on SELECT
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Explicitly unset/empty setting
                await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
                tenants = (await session.scalars(select(Tenant))).all()
                projects = (await session.scalars(select(Project))).all()
                integrations = (await session.scalars(select(Integration))).all()
                assert len(tenants) == 0, f"Expected 0 tenants, got {len(tenants)}"
                assert len(projects) == 0, f"Expected 0 projects, got {len(projects)}"
                assert len(integrations) == 0, f"Expected 0 integrations, got {len(integrations)}"

                # Writes with unset tenant context affect zero rows
                upd_res = await session.execute(
                    update(Project)
                    .where(Project.id == PROJECT_ALPHA_ID)
                    .values(name="Modified In Void")
                )
                assert upd_res.rowcount == 0

                # INSERT with unset context raises RLS WITH CHECK policy violation
                session.add(
                    Project(
                        id=uuid.uuid4(),
                        tenant_id=TENANT_ALPHA_ID,
                        name="Insert In Void",
                        slug="insert-in-void",
                        tier_sla_hours=24,
                        authoritative_cd_signal="github_deployments_api",
                        lead_time_first_commit_strategy="feature_branch_root",
                        wip_limit_per_dev=3,
                        pr_stale_threshold_hours=48,
                        is_active=True,
                    )
                )
                with pytest.raises(DBAPIError) as exc_db:
                    await session.flush()
                assert "row-level security policy" in str(exc_db.value).lower()
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# VECTOR 4: Tenant Switch Mid-Session
# ==============================================================================


@pytest.mark.asyncio
async def test_adv_005_tenant_switch_mid_session_api_and_db(auth_context):
    """
    TC-ADV-005: Tenant Switch Mid-Session.
    - Attacker reuses same HTTP keep-alive connection across requests switching tokens.
    - Stopping Layer (API): Each request evaluated independently from its token.
    - Database Layer: Mid-session set_config immediately shifts visibility;
      previous tenant records return zero rows.
    """
    await setup_adversarial_seed_data()
    try:
        # Create an integration for Tenant Alpha and an integration for Tenant Beta
        integ_a = await save_or_update_integration(
            tenant_id=TENANT_ALPHA_ID,
            provider="github",
            external_account_id="gh-a-switch",
            raw_webhook_secret="wh_secret_a_switch",
            api_credentials={"access_token": "ghp_a_switch"},
            base_url="http://test",
        )
        integ_b = await save_or_update_integration(
            tenant_id=TENANT_BETA_ID,
            provider="github",
            external_account_id="gh-b-switch",
            raw_webhook_secret="wh_secret_b_switch",
            api_credentials={"access_token": "ghp_b_switch"},
            base_url="http://test",
        )

        token_a = mint_jwt(auth_context, tenant_id=TENANT_ALPHA_ID)
        token_b = mint_jwt(auth_context, tenant_id=TENANT_BETA_ID)

        # 1. API: Single AsyncClient instance (simulating HTTP keep-alive connection reuse)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Request 1: Tenant Alpha reads Tenant Alpha integration -> 200 OK
            r1 = await ac.get(
                f"/api/v1/integrations/{integ_a.id}",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r1.status_code == 200
            assert r1.json()["id"] == str(integ_a.id)

            # Request 2 on SAME connection: Swapping Authorization to Token Beta
            # Attempting to read Tenant Alpha's integration -> 404 Not Found
            r2 = await ac.get(
                f"/api/v1/integrations/{integ_a.id}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert r2.status_code == 404

            # Request 3 on SAME connection: Token Beta reads Tenant Beta integration -> 200 OK
            r3 = await ac.get(
                f"/api/v1/integrations/{integ_b.id}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert r3.status_code == 200
            assert r3.json()["id"] == str(integ_b.id)

        # 2. Database: Mid-session tenant switch on the exact same DB session
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # Step 1: Bind context to Tenant Alpha
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(TENANT_ALPHA_ID)},
                )
                projects_a = (await session.scalars(select(Project))).all()
                assert [p.id for p in projects_a] == [PROJECT_ALPHA_ID]

                # Step 2: Switch context mid-session to Tenant Beta
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(TENANT_BETA_ID)},
                )
                session.expire_all()

                # Projects under Tenant Alpha must now return zero rows!
                projects_b = (await session.scalars(select(Project))).all()
                assert [p.id for p in projects_b] == [PROJECT_BETA_ID]

                # Mutation against Tenant Alpha's project must affect 0 rows
                stale_upd = await session.execute(
                    update(Project).where(Project.id == PROJECT_ALPHA_ID).values(name="Should Fail")
                )
                assert stale_upd.rowcount == 0
    finally:
        await teardown_adversarial_seed_data()


# ==============================================================================
# VECTOR 5: Direct DB Access as the App Role (eng_app_user)
# ==============================================================================


@pytest.mark.asyncio
async def test_adv_006_direct_db_access_app_role():
    """
    TC-ADV-006: Direct DB Access as the App Role (eng_app_user).
    - Attacker executes SQL directly as eng_app_user.
    - Stopping Layer: Database (PostgreSQL RLS & Permissions).
    - Checks:
      1. SELECT * without tenant context returns zero rows across tenant tables.
      2. SET row_security = off is rejected or ignored for non-superusers (returns zero rows).
      3. Direct INSERT into 'tenants' table is denied with SQLSTATE 42501 (permission denied).
      4. DDL (e.g. DROP TABLE) is denied with SQLSTATE 42501.
    """
    await setup_adversarial_seed_data()
    try:
        # 1. Direct unscoped SELECT returns zero rows
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.scalars(select(Project))
                assert result.all() == [], "Unscoped SELECT returned rows!"

        # 2. Attempt to disable RLS via SET row_security = off
        async with AsyncSessionLocal() as session:
            async with session.begin():
                try:
                    await session.execute(text("SET row_security = off"))
                    session.expire_all()
                    result_after_off = await session.scalars(select(Project))
                    assert result_after_off.all() == [], "row_security = off bypassed RLS!"
                except DBAPIError as exc_rls_off:
                    assert (
                        "row-level security" in str(exc_rls_off).lower()
                        or "permission denied" in str(exc_rls_off).lower()
                    )

        # 3. Direct INSERT into tenants table must be denied with SQLSTATE 42501
        async with AsyncSessionLocal() as session:
            async with session.begin():
                with pytest.raises(DBAPIError) as exc_insert:
                    await session.execute(
                        text(
                            """
                            INSERT INTO tenants (id, name, slug, tier, ai_processing_mode)
                            VALUES ('99999999-9999-9999-9999-999999999999', 'Rogue Tenant', 'rogue', 'enterprise', 'local')
                            """
                        )
                    )
                err_msg = str(exc_insert.value).lower()
                assert (
                    "permission denied" in err_msg
                    or "violates row-level security policy" in err_msg
                ), f"Unexpected error message: {exc_insert.value}"

        # 4. Attempt DDL operation (e.g. DROP TABLE)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                with pytest.raises(DBAPIError) as exc_ddl:
                    await session.execute(text("DROP TABLE projects CASCADE"))
                err_ddl = str(exc_ddl.value).lower()
                assert (
                    "permission denied" in err_ddl
                    or "must be owner" in err_ddl
                    or "insufficientprivilege" in err_ddl
                    or getattr(getattr(exc_ddl.value, "orig", None), "sqlstate", None) == "42501"
                )
    finally:
        await teardown_adversarial_seed_data()
