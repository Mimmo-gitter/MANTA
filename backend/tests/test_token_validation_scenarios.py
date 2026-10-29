import base64
import json
import logging
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest

from app.auth import JWKSClient
from app.config import settings
from app.main import app

logger = logging.getLogger("app.auth.test")

# Canonical UUID constants for testing multi-tenant isolation
VALID_TENANT_ID = "c3b4a2e1-7d8e-4a6f-9f1b-2c3d4e5f6a7b"
VICTIM_TENANT_ID = "e4d5c6b7-8a9b-0c1d-2e3f-4a5b6c7d8e9f"
NIL_TENANT_ID = "00000000-0000-0000-0000-000000000000"
DEFAULT_SUB = "usr_8f3b2a1c"


class MockClock:
    """Controllable monotonic clock for testing TTL, expiration, and rate limits."""

    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


class MockJWKSServer:
    """In-memory mock for Keycloak OIDC certs endpoint with dynamic key rotation."""

    def __init__(self, keys_dict: dict[str, Any]) -> None:
        self.keys_dict = keys_dict
        self.call_count = 0
        self.should_fail = False

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        if self.should_fail:
            return httpx.Response(500, json={"error": "IdP internal error"})
        return httpx.Response(200, json=self.keys_dict)


def generate_test_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate an RSA 2048-bit private key and matching JWK dictionary."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update(
        {
            "kid": kid,
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
        }
    )
    return private_key, jwk_dict


def build_scenario_token(
    signing_key: rsa.RSAPrivateKey | str | None,
    kid: str | None = "idp-key-2026-v1",
    claims: dict[str, Any] | None = None,
    omit_claims: list[str] | None = None,
    algorithm: str = "RS256",
    custom_headers: dict[str, Any] | None = None,
) -> str:
    """Helper to mint JWTs tailored for specific failure and attack scenarios."""
    now = int(time.time())
    default_claims: dict[str, Any] = {
        "sub": DEFAULT_SUB,
        "email": "engineer@nure.ua",
        "preferred_username": "dev_user",
        "tenant_id": VALID_TENANT_ID,
        "aud": settings.OIDC_AUDIENCE,
        "iss": settings.issuer,
        "iat": now,
        "exp": now + 900,  # 15-minute access token lifespan per Arch §6.1
        "realm_access": {"roles": ["DEVELOPER"]},
    }
    if claims:
        default_claims.update(claims)
    if omit_claims:
        for c in omit_claims:
            default_claims.pop(c, None)

    headers: dict[str, Any] = {"alg": algorithm}
    if kid is not None:
        headers["kid"] = kid
    if custom_headers:
        headers.update(custom_headers)

    if algorithm == "none":
        return jwt.encode(default_claims, None, algorithm="none", headers=headers)

    return jwt.encode(default_claims, signing_key, algorithm=algorithm, headers=headers)


@pytest.fixture
def token_scenarios_setup():
    """
    Sets up RSA test keypairs, in-memory mock IdP with dynamic key support,
    and binds an isolated JWKSClient to app.state.jwks_client.
    """
    key_v1, jwk_v1 = generate_test_rsa_keypair("idp-key-2026-v1")
    key_v2, jwk_v2 = generate_test_rsa_keypair("idp-key-2026-v2")
    key_retired, jwk_retired = generate_test_rsa_keypair("idp-key-2025-deprecated")
    rogue_key, _ = generate_test_rsa_keypair("rogue-key-999")

    mock_idp = MockJWKSServer({"keys": [jwk_v1]})
    mock_clock = MockClock(start=1000.0)

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        min_refresh_interval_seconds=10.0,
        failure_backoff_seconds=30.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
        clock=mock_clock,
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {
        "key_v1": key_v1,
        "jwk_v1": jwk_v1,
        "key_v2": key_v2,
        "jwk_v2": jwk_v2,
        "key_retired": key_retired,
        "jwk_retired": jwk_retired,
        "rogue_key": rogue_key,
        "mock_idp": mock_idp,
        "mock_clock": mock_clock,
        "client": client,
    }

    app.state.jwks_client = original_client


# ============================================================================
# TC-AUTH-001: Nominal Access Token (Positive Baseline)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_001_nominal_valid_token(token_scenarios_setup):
    """
    TC-AUTH-001: A valid RS256 token passes signature and claim verification.
    Expected: HTTP 200 OK with sub and tenant_id attached.
    """
    token = build_scenario_token(token_scenarios_setup["key_v1"], kid="idp-key-2026-v1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["sub"] == DEFAULT_SUB
    assert body["tenant_id"] == VALID_TENANT_ID
    assert body["roles"] == ["DEVELOPER"]


# ============================================================================
# TC-AUTH-002: Tampered Payload (Claim Elevation / Signature Mismatch)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_002_tampered_payload(token_scenarios_setup):
    """
    TC-AUTH-002: Token payload is tampered post-issuance to elevate privileges (TENANT_ADMIN).
    Expected: HTTP 401 Unauthorized with WWW-Authenticate header.
    """
    token = build_scenario_token(token_scenarios_setup["key_v1"], kid="idp-key-2026-v1")
    parts = token.split(".")
    # Decode and tamper payload
    payload_json = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    payload_json["realm_access"]["roles"] = ["TENANT_ADMIN"]
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(payload_json).encode()).rstrip(b"=").decode()
    )
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered_token}"}
        )

    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-003: Expired Token (exp Boundary Passed)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_003_expired_token(token_scenarios_setup):
    """
    TC-AUTH-003: Access token whose exp claim is in the past.
    Expected: HTTP 401 Unauthorized.
    """
    expired_time = int(time.time()) - 1800
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="idp-key-2026-v1",
        claims={"exp": expired_time, "iat": expired_time - 900},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-004: Wrong Issuer (iss Untrusted / Mismatch)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_004_wrong_issuer(token_scenarios_setup):
    """
    TC-AUTH-004: Token minted with untrusted or mismatched issuer.
    Expected: HTTP 401 Unauthorized.
    """
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="idp-key-2026-v1",
        claims={"iss": "https://untrusted-idp.rogue.org/auth"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-005: Wrong Audience (aud Mismatch)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_005_wrong_audience(token_scenarios_setup):
    """
    TC-AUTH-005: Token minted for a different application client audience.
    Expected: HTTP 401 Unauthorized.
    """
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="idp-key-2026-v1",
        claims={"aud": "external-accounting-portal"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-006: Forged tenant_id (Attacker Self-Signed Token)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_006_forged_tenant_id_attacker_key(token_scenarios_setup):
    """
    TC-AUTH-006: Attacker generates local key and signs token claiming victim tenant_id.
    Expected: HTTP 401 Unauthorized (public key lookup fails in IdP JWKS).
    """
    token = build_scenario_token(
        token_scenarios_setup["rogue_key"],
        kid="rogue-key-999",
        claims={"tenant_id": VICTIM_TENANT_ID, "sub": "usr_attacker"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-007: Forged tenant_id (Cross-Tenant Token Swapping via Tampering)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_007_forged_tenant_id_cross_tenant_swap(token_scenarios_setup):
    """
    TC-AUTH-007: Attacker takes legitimate Tenant A token and swaps tenant_id to Tenant B.
    Expected: HTTP 401 Unauthorized (signature mismatch prevents tenant context escalation).
    """
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="idp-key-2026-v1",
        claims={"tenant_id": VALID_TENANT_ID},
    )
    parts = token.split(".")
    payload_json = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    payload_json["tenant_id"] = VICTIM_TENANT_ID
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(payload_json).encode()).rstrip(b"=").decode()
    )
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered_token}"}
        )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-008: Rotated Key (Valid Token Signed with Newly Rotated Key)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_008_rotated_key_valid_new_key(token_scenarios_setup):
    """
    TC-AUTH-008: IdP rotates keys. Tokens signed with newly published key (kid: idp-key-2026-v2)
    trigger on-demand JWKS cache refresh and are accepted without restarting the API.
    """
    token_v1 = build_scenario_token(token_scenarios_setup["key_v1"], kid="idp-key-2026-v1")
    token_v2 = build_scenario_token(token_scenarios_setup["key_v2"], kid="idp-key-2026-v2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Warm cache with initial key v1
        resp1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_v1}"})
        assert resp1.status_code == 200
        assert token_scenarios_setup["mock_idp"].call_count == 1

        # 2. Keycloak rotates keys: new key v2 is added to JWKS
        token_scenarios_setup["mock_clock"].advance(15.0)  # cooldown passed
        token_scenarios_setup["mock_idp"].keys_dict["keys"].append(token_scenarios_setup["jwk_v2"])

        # 3. Request with rotated key arrives -> triggers on-demand refresh and succeeds
        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_v2}"})
        assert resp2.status_code == 200
        assert resp2.json()["sub"] == DEFAULT_SUB
        assert token_scenarios_setup["mock_idp"].call_count == 2

        # 4. Subsequent request with new key is served from in-memory cache
        resp3 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_v2}"})
        assert resp3.status_code == 200
        assert token_scenarios_setup["mock_idp"].call_count == 2


# ============================================================================
# TC-AUTH-009: Rotated Key (Stale / Retired Key Used Post-Rotation)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_009_rotated_key_retired_key(token_scenarios_setup):
    """
    TC-AUTH-009: Token signed with a retired key that has been evicted from IdP JWKS.
    Expected: HTTP 401 Unauthorized.
    """
    token_retired = build_scenario_token(
        token_scenarios_setup["key_retired"],
        kid="idp-key-2025-deprecated",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_retired}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-010: Unknown kid (Non-Existent in Cache and Upstream JWKS)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_010_unknown_kid(token_scenarios_setup):
    """
    TC-AUTH-010: Token header specifies a kid that does not exist in cached or upstream JWKS.
    Expected: Triggers rate-limited refresh attempt, fails, and returns HTTP 401.
    """
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="unknown-kid-xyz-789",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-011: Algorithm Confusion Attack (alg: none)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_011_algorithm_none(token_scenarios_setup):
    """
    TC-AUTH-011: Token specifying unsecured alg: none.
    Expected: Immediately rejected with HTTP 401 Unauthorized.
    """
    token = build_scenario_token(
        None,
        kid="idp-key-2026-v1",
        algorithm="none",
        claims={"role": "TENANT_ADMIN"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-012: Algorithm Key Confusion Attack (RS256 to HS256 Downgrade)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_012_algorithm_key_confusion_hs256(token_scenarios_setup):
    """
    TC-AUTH-012: Symmetric HMAC substitution attack attempting algorithm downgrade (HS256).
    Expected: Rejected with HTTP 401 (platform strictly allows only asymmetric RS256).
    """
    token = build_scenario_token(
        "attacker-controlled-hmac-secret-key-32",
        kid="idp-key-2026-v1",
        algorithm="HS256",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-013: Premature Token (nbf in Future)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_013_premature_token_nbf(token_scenarios_setup):
    """
    TC-AUTH-013: Token whose Not-Before (nbf) claim is in the future.
    Expected: HTTP 401 Unauthorized.
    """
    now = int(time.time())
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="idp-key-2026-v1",
        claims={"nbf": now + 1200, "iat": now, "exp": now + 2400},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-014: Malformed Token Structure (Truncated / Invalid Base64URL)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_014_malformed_token_structure(token_scenarios_setup):
    """
    TC-AUTH-014: Authorization headers with truncated or structurally corrupt JWT segments.
    Expected: HTTP 401 Unauthorized with WWW-Authenticate: Bearer header.
    """
    malformed_variants = [
        "not.a.valid.jwt",
        "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0In0",  # 2 segments (missing signature)
        "a.b.c.d",  # 4 segments
        "!!!invalid-base64url!!!.payload.signature",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for malformed in malformed_variants:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {malformed}"})
            assert resp.status_code == 401
            assert resp.headers.get("WWW-Authenticate") == "Bearer"
            assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# TC-AUTH-015: Missing Mandatory Multi-Tenant Claim (tenant_id Omitted)
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_015_missing_tenant_id_claim(token_scenarios_setup):
    """
    TC-AUTH-015: Token signed by legitimate IdP but omitting the mandatory tenant_id claim.
    Expected: HTTP 401 Unauthorized (session context cannot be established).
    """
    token = build_scenario_token(
        token_scenarios_setup["key_v1"],
        kid="idp-key-2026-v1",
        omit_claims=["tenant_id"],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


# ============================================================================
# Binary Indistinguishability Verification Across All Failure Modes
# ============================================================================
@pytest.mark.asyncio
async def test_tc_auth_binary_indistinguishability_of_failures(token_scenarios_setup):
    """
    Verifies that all rejection scenarios produce identical HTTP status, headers,
    and response payloads to prevent timing/enumeration side-channels.
    """
    pk = token_scenarios_setup["key_v1"]
    now = int(time.time())

    rejection_tokens = [
        build_scenario_token(pk, claims={"exp": now - 100}),  # Expired
        build_scenario_token(pk, claims={"iss": "https://wrong.iss"}),  # Wrong iss
        build_scenario_token(pk, claims={"aud": "wrong.aud"}),  # Wrong aud
        build_scenario_token(pk, omit_claims=["tenant_id"]),  # Missing tenant_id
        build_scenario_token(pk, claims={"tenant_id": ""}),  # Empty tenant_id
        build_scenario_token(pk, claims={"tenant_id": "not-a-uuid"}),  # Non-UUID
        build_scenario_token(pk, claims={"tenant_id": NIL_TENANT_ID}),  # Nil UUID
        build_scenario_token(pk, omit_claims=["sub"]),  # Missing sub
        build_scenario_token(pk, kid="non-existent-kid"),  # Unknown kid
    ]

    expected_status = 401
    expected_header = "Bearer"
    expected_body = {"detail": "Not authenticated"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for token in rejection_tokens:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == expected_status
            assert resp.headers.get("WWW-Authenticate") == expected_header
            assert resp.json() == expected_body
