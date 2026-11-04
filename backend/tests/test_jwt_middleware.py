import asyncio
import json
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from starlette.requests import Request

from app.auth import (
    JWKSClient,
    get_current_claims,
    get_current_user,
)
from app.config import settings
from app.main import app


class MockClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


class MockJWKSServer:
    """In-memory mock for Keycloak's OIDC certs endpoint."""

    def __init__(self, keys_dict: dict[str, Any]) -> None:
        self.keys_dict = keys_dict
        self.call_count = 0
        self.should_fail = False
        self.delay = 0.0

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            return httpx.Response(500, json={"error": "IdP internal error"})
        return httpx.Response(200, json=self.keys_dict)


def generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate RSA private key and matching JWK dictionary."""
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


def create_token(
    private_key: rsa.RSAPrivateKey,
    kid: str | None = "key-1",
    claims: dict[str, Any] | None = None,
    algorithm: str = "RS256",
    custom_headers: dict[str, Any] | None = None,
) -> str:
    """Helper to mint JWTs for testing."""
    default_claims: dict[str, Any] = {
        "sub": "user-123",
        "email": "developer@nure.ua",
        "preferred_username": "dev_user",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": int(time.time()) + 3600,
        "realm_access": {"roles": ["DEVELOPER", "TENANT_ADMIN"]},
    }
    if claims:
        default_claims.update(claims)

    headers: dict[str, Any] = {"alg": algorithm}
    if kid is not None:
        headers["kid"] = kid
    if custom_headers:
        headers.update(custom_headers)

    return jwt.encode(default_claims, private_key, algorithm=algorithm, headers=headers)


@pytest.fixture
def auth_setup():
    """Sets up RSA keys, mock IdP, mock clock, and overrides app.state.jwks_client."""
    key1, jwk1 = generate_rsa_keypair("key-1")
    key2, jwk2 = generate_rsa_keypair("key-2")
    rogue_key, _ = generate_rsa_keypair("rogue-key")

    mock_idp = MockJWKSServer({"keys": [jwk1, jwk2]})
    mock_clock = MockClock()

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
        clock=mock_clock,
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {
        "key1": key1,
        "jwk1": jwk1,
        "key2": key2,
        "jwk2": jwk2,
        "rogue_key": rogue_key,
        "mock_idp": mock_idp,
        "mock_clock": mock_clock,
        "client": client,
    }

    app.state.jwks_client = original_client


@pytest.mark.asyncio
async def test_valid_token_reaches_handler_with_claims_attached(auth_setup):
    """AC 1: Valid RS256 token reaches handler with claims attached."""
    token = create_token(
        auth_setup["key1"],
        kid="key-1",
        claims={
            "sub": "user-alpha-dev",
            "email": "alpha.dev@nure.ua",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "realm_access": {"roles": ["DEVELOPER"]},
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["sub"] == "user-alpha-dev"
    assert data["email"] == "alpha.dev@nure.ua"
    assert data["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    assert data["roles"] == ["DEVELOPER"]
    assert data["raw"]["sub"] == "user-alpha-dev"


@pytest.mark.asyncio
async def test_jwks_fetched_once_and_served_from_cache(auth_setup):
    """AC 2: JWKS is fetched once and served from cache for subsequent requests."""
    token = create_token(auth_setup["key1"], kid="key-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for _ in range(5):
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200

    # IdP must be called strictly ONCE
    assert auth_setup["mock_idp"].call_count == 1
    assert auth_setup["client"].fetch_count == 1


@pytest.mark.asyncio
async def test_jwks_cache_ttl_expiration_triggers_refetch(auth_setup):
    """AC 2: After TTL expires, the next request refreshes the JWKS cache."""
    token = create_token(auth_setup["key1"], kid="key-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp1.status_code == 200
        assert auth_setup["mock_idp"].call_count == 1

        # Advance clock past TTL (300 seconds)
        auth_setup["mock_clock"].advance(301.0)

        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200
        assert auth_setup["mock_idp"].call_count == 2


@pytest.mark.asyncio
async def test_concurrent_requests_single_flight_jwks_fetch(auth_setup):
    """AC 2: Concurrent requests on cold cache trigger exactly one JWKS fetch."""
    auth_setup["mock_idp"].delay = 0.05
    token = create_token(auth_setup["key1"], kid="key-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        tasks = [
            ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

    for r in responses:
        assert r.status_code == 200

    assert auth_setup["mock_idp"].call_count == 1


@pytest.mark.asyncio
async def test_request_without_token_rejected_with_401(auth_setup):
    """AC 3: Requests without a token are rejected with HTTP 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
    assert response.json() == {"detail": "Not authenticated"}
    assert auth_setup["mock_idp"].call_count == 0


@pytest.mark.asyncio
async def test_malformed_authorization_headers_rejected_with_401(auth_setup):
    """AC 3: Requests with malformed or non-Bearer authorization headers are rejected."""
    malformed_headers = [
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer    ",
        "Bearer not.a.valid.jwt",
        "Token some-other-token",
        "Bearer header.payload",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for header_val in malformed_headers:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": header_val})
            assert resp.status_code == 401
            assert resp.headers.get("WWW-Authenticate") == "Bearer"
            assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_multi_key_jwks_resolves_correct_key_by_kid(auth_setup):
    """Resolves specific signing key from JWKS by kid matching the token."""
    token2 = create_token(auth_setup["key2"], kid="key-2", claims={"sub": "user-key-2"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token2}"})

    assert resp.status_code == 200
    assert resp.json()["sub"] == "user-key-2"


@pytest.mark.asyncio
async def test_unknown_or_missing_kid_rejected_with_401(auth_setup):
    """Tokens with unknown kid or missing kid are rejected with 401."""
    # Unknown kid
    token_unknown = create_token(auth_setup["key1"], kid="unknown-kid")
    # Missing kid (omit kid header)
    token_no_kid = create_token(auth_setup["key1"], kid=None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown}"}
        )
        assert resp1.status_code == 401

        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_no_kid}"})
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_tampered_signature_rejected_with_401(auth_setup):
    """Token signed with untrusted key asserting valid kid is rejected."""
    rogue_token = create_token(auth_setup["rogue_key"], kid="key-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {rogue_token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_disallowed_algorithms_rejected_with_401(auth_setup):
    """Tokens with algorithms other than RS256 (e.g. none, HS256) are rejected."""
    # Algorithm none
    claims = {
        "sub": "user-none",
        "aud": settings.OIDC_AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    none_token = jwt.encode(claims, None, algorithm="none", headers={"kid": "key-1"})

    # Algorithm HS256
    hs_token = jwt.encode(claims, "a" * 32, algorithm="HS256", headers={"kid": "key-1"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {none_token}"})
        assert resp1.status_code == 401

        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {hs_token}"})
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_and_wrong_audience_rejected_with_401(auth_setup):
    """Expired token or token with wrong audience is rejected with 401."""
    expired_token = create_token(
        auth_setup["key1"],
        kid="key-1",
        claims={"exp": int(time.time()) - 100},
    )
    wrong_aud_token = create_token(
        auth_setup["key1"],
        kid="key-1",
        claims={"aud": "wrong-client-aud"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert resp1.status_code == 401

        resp2 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong_aud_token}"}
        )
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_public_paths_exempt_from_authentication(auth_setup):
    """Public endpoints bypass authentication; OPTIONS requests pass through."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # /health
        health_resp = await ac.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok"}

        # /openapi.json
        openapi_resp = await ac.get("/openapi.json")
        assert openapi_resp.status_code == 200

        # /docs
        docs_resp = await ac.get("/docs")
        assert docs_resp.status_code == 200

        # OPTIONS on protected route (CORS preflight)
        options_resp = await ac.options("/api/v1/auth/me")
        assert options_resp.status_code != 401

        # Non-public prefix extension attempting path bypass
        fake_health = await ac.get("/health-bypass")
        assert fake_health.status_code == 401


@pytest.mark.asyncio
async def test_idp_outage_stale_on_error_and_cold_cache_503(auth_setup):
    """Cold cache with IdP down returns 503; warm cache with expired TTL serves stale keys."""
    # 1. Cold cache with IdP outage -> 503
    auth_setup["mock_idp"].should_fail = True
    auth_setup["client"].clear_cache()
    token = create_token(auth_setup["key1"], kid="key-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp_cold = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp_cold.status_code == 503
        assert resp_cold.json() == {"detail": "Authentication service unavailable"}

        # 2. Warm up cache with successful IdP
        auth_setup["mock_idp"].should_fail = False
        resp_warm = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp_warm.status_code == 200
        assert auth_setup["mock_idp"].call_count == 2

        # 3. Simulate TTL expiration followed by IdP outage -> served stale keys
        auth_setup["mock_clock"].advance(301.0)
        auth_setup["mock_idp"].should_fail = True

        resp_stale = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp_stale.status_code == 200  # Stale-on-error succeeds!
        assert auth_setup["mock_idp"].call_count == 3

        # 4. Immediate subsequent request uses backoff window (no immediate retry storm)
        auth_setup["mock_clock"].advance(5.0)  # within 30s backoff
        resp_stale2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp_stale2.status_code == 200
        assert auth_setup["mock_idp"].call_count == 3  # Did not retry IdP


@pytest.mark.asyncio
async def test_jwks_with_non_signature_keys_ignored():
    """Keys with use != 'sig' or algorithm != 'RS256' are ignored; sig keys are utilized."""
    sig_key, sig_jwk = generate_rsa_keypair("sig-key-1")
    enc_jwk = {
        "kid": "enc-key-1",
        "kty": "RSA",
        "alg": "RSA-OAEP",
        "use": "enc",
        "n": sig_jwk["n"],
        "e": sig_jwk["e"],
    }
    oct_jwk = {
        "kid": "sym-key-1",
        "kty": "oct",
        "k": "c2VjcmV0",
    }

    mock_idp = MockJWKSServer({"keys": [sig_jwk, enc_jwk, oct_jwk]})
    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    orig_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    try:
        token = create_token(sig_key, kid="sig-key-1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        # Assert only 1 key was loaded into _keys (the sig RS256 key)
        assert len(client._keys) == 1
        assert "sig-key-1" in client._keys
        assert "enc-key-1" not in client._keys
        assert "sym-key-1" not in client._keys
    finally:
        app.state.jwks_client = orig_client


@pytest.mark.asyncio
async def test_dependency_injection_get_current_claims_and_user(auth_setup):
    """Verifies get_current_claims and get_current_user dependencies directly."""
    # 1. When request.state has claims
    fake_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "state": {
                "claims": {
                    "sub": "user-dep-test",
                    "email": "dep@nure.ua",
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "realm_access": {"roles": ["SOLUTION_ARCHITECT"]},
                }
            },
        }
    )
    claims = get_current_claims(fake_request)
    assert claims["sub"] == "user-dep-test"

    user = get_current_user(claims)
    assert user.sub == "user-dep-test"
    assert user.email == "dep@nure.ua"
    assert user.tenant_id == "22222222-2222-2222-2222-222222222222"
    assert user.roles == ["SOLUTION_ARCHITECT"]

    # 2. When request.state lacks claims (e.g. bypassed route incorrectly injecting dep)
    empty_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "state": {},
        }
    )
    with pytest.raises(Exception) as exc_info:
        get_current_claims(empty_request)
    assert "401" in str(exc_info.value)
