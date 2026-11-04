import asyncio
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


class MockClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


class MockJWKSServer:
    """In-memory mock for Keycloak's OIDC certs endpoint with dynamic key support."""

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
) -> str:
    """Helper to mint JWTs for testing."""
    default_claims: dict[str, Any] = {
        "sub": "user-rotator-123",
        "email": "rotator@nure.ua",
        "preferred_username": "rotator_user",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": int(time.time()) + 3600,
        "realm_access": {"roles": ["DEVELOPER"]},
    }
    if claims:
        default_claims.update(claims)

    headers: dict[str, Any] = {"alg": algorithm}
    if kid is not None:
        headers["kid"] = kid

    return jwt.encode(default_claims, private_key, algorithm=algorithm, headers=headers)


@pytest.fixture
def rotation_setup():
    """Sets up RSA keys, mock IdP, mock clock, and overrides app.state.jwks_client."""
    key1, jwk1 = generate_rsa_keypair("rot-key-1")
    key2, jwk2 = generate_rsa_keypair("rot-key-2")
    key3, jwk3 = generate_rsa_keypair("rot-key-3")

    mock_idp = MockJWKSServer({"keys": [jwk1]})
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
        "key1": key1,
        "jwk1": jwk1,
        "key2": key2,
        "jwk2": jwk2,
        "key3": key3,
        "jwk3": jwk3,
        "mock_idp": mock_idp,
        "mock_clock": mock_clock,
        "client": client,
    }

    app.state.jwks_client = original_client


@pytest.mark.asyncio
async def test_key_rotation_accepts_new_key_without_api_restart(rotation_setup):
    """
    AC 1: After Keycloak rotates signing keys, tokens signed with the new key
    are accepted without restarting the API.
    """
    token_old = create_token(rotation_setup["key1"], kid="rot-key-1")
    token_new = create_token(rotation_setup["key2"], kid="rot-key-2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Old key works initially; fetches JWKS once
        resp1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
        assert resp1.status_code == 200
        assert rotation_setup["mock_idp"].call_count == 1
        assert rotation_setup["client"].fetch_count == 1

        # 2. Keycloak rotates keys: rot-key-1 is passive, rot-key-2 is active
        # Advance clock by 15 seconds to ensure cooldown elapsed
        rotation_setup["mock_clock"].advance(15.0)
        rotation_setup["mock_idp"].keys_dict["keys"].append(rotation_setup["jwk2"])

        # 3. Token signed with the new key arrives -> triggers on-demand refetch without restart!
        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_new}"})
        assert resp2.status_code == 200
        assert resp2.json()["sub"] == "user-rotator-123"
        assert rotation_setup["mock_idp"].call_count == 2
        assert rotation_setup["client"].fetch_count == 2

        # 4. Subsequent token signed with the new key is served from cache
        resp3 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_new}"})
        assert resp3.status_code == 200
        assert rotation_setup["mock_idp"].call_count == 2
        assert rotation_setup["client"].fetch_count == 2

        # 5. Tokens signed with the still-valid passive old key STILL WORK!
        resp4 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
        assert resp4.status_code == 200
        assert rotation_setup["mock_idp"].call_count == 2


@pytest.mark.asyncio
async def test_old_keys_remain_valid_until_they_leave_jwks(rotation_setup):
    """
    Scope 3: Keep still-valid old keys until they leave the JWKS.
    When an old key is formally retired from the IdP JWKS, it is purged on refresh.
    """
    # Start with both key1 and key2 in IdP
    rotation_setup["mock_idp"].keys_dict["keys"].append(rotation_setup["jwk2"])

    token1 = create_token(rotation_setup["key1"], kid="rot-key-1")
    token2 = create_token(rotation_setup["key2"], kid="rot-key-2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Both keys are authenticated
        resp1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token1}"})
        resp2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token2}"})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert rotation_setup["client"].fetch_count == 1

        # Now IdP formally purges key1: only key2 remains
        rotation_setup["mock_idp"].keys_dict["keys"] = [rotation_setup["jwk2"]]

        # Advance clock past cache TTL (300s) to force routine TTL refresh
        rotation_setup["mock_clock"].advance(301.0)

        # Token 2 still succeeds
        resp2_after = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token2}"})
        assert resp2_after.status_code == 200
        assert rotation_setup["client"].fetch_count == 2

        # Token 1 (which left the JWKS) is rejected with HTTP 401 generic
        resp1_after = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token1}"})
        assert resp1_after.status_code == 401
        assert resp1_after.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_unknown_kid_rate_limiting_prevents_refresh_storm(rotation_setup):
    """
    AC 2: Tokens with an unknown kid cause at most one refetch per interval and are then rejected.
    Subsequent unknown kids arriving during cooldown do not hit the IdP.
    """
    token_unknown_1 = create_token(rotation_setup["key3"], kid="unknown-kid-1")
    token_unknown_2 = create_token(rotation_setup["key3"], kid="unknown-kid-2")
    token_unknown_3 = create_token(rotation_setup["key3"], kid="unknown-kid-3")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Initial warm up with valid key
        valid_token = create_token(rotation_setup["key1"], kid="rot-key-1")
        resp_warm = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {valid_token}"}
        )
        assert resp_warm.status_code == 200
        assert rotation_setup["mock_idp"].call_count == 1

        # 2. Advance time past the 10s cooldown
        rotation_setup["mock_clock"].advance(15.0)

        # 3. First unknown kid triggers on-demand refetch, fails, returns 401
        resp_u1 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown_1}"}
        )
        assert resp_u1.status_code == 401
        assert resp_u1.json() == {"detail": "Not authenticated"}
        assert rotation_setup["mock_idp"].call_count == 2  # Refetch occurred

        # 4. Immediate second unknown kid (only 1s later) is rate-limited: NO HTTP CALL!
        rotation_setup["mock_clock"].advance(1.0)
        resp_u2 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown_2}"}
        )
        assert resp_u2.status_code == 401
        assert resp_u2.json() == {"detail": "Not authenticated"}
        assert rotation_setup["mock_idp"].call_count == 2  # Still 2! Zero extra fetches

        # 5. Send 5 more attacker requests with different random kids within cooldown
        for i in range(5):
            bogus_token = create_token(rotation_setup["key3"], kid=f"attacker-kid-{i}")
            resp_bogus = await ac.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {bogus_token}"}
            )
            assert resp_bogus.status_code == 401
        assert rotation_setup["mock_idp"].call_count == 2  # Rate limit completely halted storm!

        # 6. Cooldown interval elapses (advance 10.1s from last refetch)
        rotation_setup["mock_clock"].advance(10.1)

        # 7. Next unknown kid is now allowed to trigger exactly one refetch
        resp_u3 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown_3}"}
        )
        assert resp_u3.status_code == 401
        assert rotation_setup["mock_idp"].call_count == 3


@pytest.mark.asyncio
async def test_concurrent_requests_with_new_key_single_flight(rotation_setup):
    """
    Single-flight concurrency: 20 concurrent requests presenting a newly rotated key
    execute exactly one HTTP fetch to IdP, and all 20 succeed.
    """
    token_old = create_token(rotation_setup["key1"], kid="rot-key-1")
    token_new = create_token(rotation_setup["key2"], kid="rot-key-2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Warm cache with key1
        await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
        assert rotation_setup["mock_idp"].call_count == 1

        # Rotate key and add delay to mock IdP to widen concurrent race window
        rotation_setup["mock_clock"].advance(15.0)
        rotation_setup["mock_idp"].keys_dict["keys"].append(rotation_setup["jwk2"])
        rotation_setup["mock_idp"].delay = 0.05

        # Dispatch 20 concurrent requests with the new key
        tasks = [
            ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_new}"})
            for _ in range(20)
        ]
        responses = await asyncio.gather(*tasks)

        # All 20 requests must succeed with 200 OK
        for r in responses:
            assert r.status_code == 200
            assert r.json()["sub"] == "user-rotator-123"

        # Exactly 1 HTTP fetch occurred for the batch (call_count went from 1 to 2)
        assert rotation_setup["mock_idp"].call_count == 2


@pytest.mark.asyncio
async def test_concurrent_requests_with_unknown_key_single_flight(rotation_setup):
    """
    Single-flight concurrency: 20 concurrent requests presenting an unknown kid
    execute exactly one HTTP fetch to IdP, and all 20 return HTTP 401.
    """
    token_unknown = create_token(rotation_setup["key3"], kid="unknown-flood-kid")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        rotation_setup["mock_clock"].advance(15.0)
        rotation_setup["mock_idp"].delay = 0.05

        tasks = [
            ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown}"})
            for _ in range(20)
        ]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code == 401
            assert r.json() == {"detail": "Not authenticated"}

        # Exactly 1 HTTP fetch occurred
        assert rotation_setup["mock_idp"].call_count == 1


@pytest.mark.asyncio
async def test_diagnostic_logging_records_rotation_events(rotation_setup, caplog):
    """
    AC 3: Behavior is logged for diagnostics.
    Emits structured diagnostic logs on rotation refresh, rate-limited suppression,
    resolved keys, and unresolved keys.
    """
    caplog.set_level(logging.INFO, logger="app.auth.jwks")

    token_new = create_token(rotation_setup["key2"], kid="rot-key-2")
    token_bogus = create_token(rotation_setup["key3"], kid="bogus-diag-kid")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Warm cache with initial key
        token_old = create_token(rotation_setup["key1"], kid="rot-key-1")
        resp_old = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
        assert resp_old.status_code == 200
        caplog.clear()

        # Rotate key
        rotation_setup["mock_clock"].advance(15.0)
        rotation_setup["mock_idp"].keys_dict["keys"].append(rotation_setup["jwk2"])

        # 1. On-demand refresh & key resolved
        resp_new = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_new}"})
        assert resp_new.status_code == 200

        # Check log messages
        log_text = caplog.text
        assert "Unknown key id 'rot-key-2' encountered" in log_text
        assert "Signing key 'rot-key-2' resolved successfully after JWKS refresh" in log_text

        # 2. Rate-limited rejection log (immediate request with unknown kid)
        caplog.clear()
        resp_bogus = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_bogus}"}
        )
        assert resp_bogus.status_code == 401
        assert "JWKS refresh rate-limited for unknown key id 'bogus-diag-kid'" in caplog.text

        # 3. Unresolved key log after refresh
        caplog.clear()
        rotation_setup["mock_clock"].advance(15.0)
        resp_bogus2 = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_bogus}"}
        )
        assert resp_bogus2.status_code == 401
        assert "Signing key 'bogus-diag-kid' not found in JWKS after refresh" in caplog.text


@pytest.mark.asyncio
async def test_idp_outage_during_unknown_kid_refresh_returns_401(rotation_setup):
    """
    When IdP is unreachable and an unknown kid arrives:
    - If cache has existing keys, it serves existing keys via stale-on-error,
      and returns generic HTTP 401 for the unknown kid.
    """
    token_old = create_token(rotation_setup["key1"], kid="rot-key-1")
    token_unknown = create_token(rotation_setup["key3"], kid="unknown-during-outage")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Warm cache with key1
        await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
        assert rotation_setup["mock_idp"].call_count == 1

        # Simulate IdP going down
        rotation_setup["mock_clock"].advance(15.0)
        rotation_setup["mock_idp"].should_fail = True

        # Unknown kid arrives -> attempts refresh -> fails -> returns 401
        resp_u = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown}"}
        )
        assert resp_u.status_code == 401
        assert resp_u.json() == {"detail": "Not authenticated"}

        # Stale existing key still authenticates with 200 OK!
        resp_old = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_old}"})
        assert resp_old.status_code == 200


@pytest.mark.asyncio
async def test_cold_cache_unknown_kid_idp_outage_returns_503(rotation_setup):
    """
    When IdP is unreachable and cache is completely cold (no keys at all),
    JWKSUnavailable raises and middleware returns 503.
    """
    rotation_setup["client"].clear_cache()
    rotation_setup["mock_idp"].should_fail = True

    token_unknown = create_token(rotation_setup["key3"], kid="unknown-cold")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown}"})
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Authentication service unavailable"}
