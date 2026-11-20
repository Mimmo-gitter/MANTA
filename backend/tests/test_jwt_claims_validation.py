import base64
import json
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from starlette.requests import Request

from app.auth import (
    JWKSClient,
    get_current_tenant_id,
)
from app.config import settings
from app.main import app


class MockJWKSServer:
    """Mock for Keycloak OIDC certs endpoint."""

    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]})


def generate_test_keypair(kid: str = "claims-test-key") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
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


VALID_TENANT_ID = "11111111-1111-1111-1111-111111111111"
NIL_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def make_token(
    private_key: rsa.RSAPrivateKey,
    kid: str = "claims-test-key",
    custom_claims: dict[str, Any] | None = None,
    omit_claims: list[str] | None = None,
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "user-claims-test",
        "email": "claims@nure.ua",
        "preferred_username": "claims_user",
        "tenant_id": VALID_TENANT_ID,
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
        "realm_access": {"roles": ["DEVELOPER"]},
    }
    if custom_claims:
        payload.update(custom_claims)
    if omit_claims:
        for c in omit_claims:
            payload.pop(c, None)

    return jwt.encode(
        payload, private_key, algorithm=algorithm, headers={"kid": kid, "alg": algorithm}
    )


def make_raw_signed_token(
    private_key: rsa.RSAPrivateKey,
    payload: dict[str, Any],
    kid: str = "claims-test-key",
) -> str:
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    h = _b64(json.dumps({"kid": kid, "alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    p = _b64(json.dumps(payload).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    s = _b64(sig)
    return f"{h}.{p}.{s}"


@pytest.fixture
def claims_fixture():
    private_key, jwk = generate_test_keypair("claims-test-key")
    mock_idp = MockJWKSServer(jwk)
    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {
        "private_key": private_key,
        "jwk": jwk,
        "client": client,
    }

    app.state.jwks_client = original_client


@pytest.mark.asyncio
async def test_valid_token_passes_all_checks(claims_fixture):
    """AC 3: A valid token passes all checks and reaches the handler with claims attached."""
    token = make_token(claims_fixture["private_key"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["sub"] == "user-claims-test"
    assert data["tenant_id"] == VALID_TENANT_ID
    assert data["roles"] == ["DEVELOPER"]


@pytest.mark.asyncio
async def test_tampered_signature_returns_generic_401(claims_fixture):
    """AC 1: Tampered signature returns 401."""
    token = make_token(claims_fixture["private_key"])
    parts = token.split(".")
    tampered_token = f"{parts[0]}.{parts[1]}.{parts[2]}tampered"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered_token}"}
        )

    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_expired_token_returns_generic_401(claims_fixture):
    """AC 1: Expired token returns 401."""
    token = make_token(claims_fixture["private_key"], custom_claims={"exp": int(time.time()) - 60})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_missing_exp_claim_returns_generic_401(claims_fixture):
    """Tokens omitting exp claim are rejected with 401."""
    token = make_token(claims_fixture["private_key"], omit_claims=["exp"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_wrong_issuer_returns_generic_401(claims_fixture):
    """AC 1: Wrong issuer returns 401."""
    token = make_token(
        claims_fixture["private_key"], custom_claims={"iss": "https://untrusted-rogue-idp.com"}
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_missing_issuer_claim_returns_generic_401(claims_fixture):
    """Tokens omitting iss claim are rejected with 401."""
    token = make_token(claims_fixture["private_key"], omit_claims=["iss"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_wrong_audience_returns_generic_401(claims_fixture):
    """AC 1: Wrong audience returns 401."""
    token = make_token(
        claims_fixture["private_key"], custom_claims={"aud": "untrusted-client-audience"}
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_missing_audience_claim_returns_generic_401(claims_fixture):
    """Tokens omitting aud claim are rejected with 401."""
    token = make_token(claims_fixture["private_key"], omit_claims=["aud"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_missing_tenant_id_claim_returns_generic_401(claims_fixture):
    """AC 1: Tenant-less tokens (missing tenant_id) return 401."""
    token = make_token(claims_fixture["private_key"], omit_claims=["tenant_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_empty_string_tenant_id_returns_generic_401(claims_fixture):
    """Tokens with empty string tenant_id return 401."""
    token = make_token(claims_fixture["private_key"], custom_claims={"tenant_id": ""})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_whitespace_tenant_id_returns_generic_401(claims_fixture):
    """Tokens with whitespace-only tenant_id return 401."""
    token = make_token(claims_fixture["private_key"], custom_claims={"tenant_id": "   "})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_non_uuid_tenant_id_returns_generic_401(claims_fixture):
    """Tokens with non-UUID format tenant_id return 401."""
    token = make_token(
        claims_fixture["private_key"], custom_claims={"tenant_id": "not-a-valid-uuid"}
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_nil_uuid_tenant_id_returns_generic_401(claims_fixture):
    """Tokens with nil UUID (00000000-0000-0000-0000-000000000000) return 401 per Arch §6.5."""
    token = make_token(claims_fixture["private_key"], custom_claims={"tenant_id": NIL_TENANT_ID})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_malformed_iss_type_returns_generic_401(claims_fixture):
    """Tokens with non-string iss type (e.g. list) are contained and return 401, not 500."""
    now = int(time.time())
    payload = {
        "sub": "user-claims-test",
        "email": "claims@nure.ua",
        "tenant_id": VALID_TENANT_ID,
        "aud": settings.OIDC_AUDIENCE,
        "iss": ["http://list-issuer"],
        "exp": now + 3600,
        "realm_access": {"roles": ["DEVELOPER"]},
    }
    token = make_raw_signed_token(claims_fixture["private_key"], payload)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_missing_sub_claim_returns_generic_401(claims_fixture):
    """Tokens omitting sub claim return 401."""
    token = make_token(claims_fixture["private_key"], omit_claims=["sub"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_error_responses_binary_indistinguishability(claims_fixture):
    """AC 2: Error responses across all failure modes are indistinguishable."""
    pk = claims_fixture["private_key"]
    failure_tokens = [
        make_token(pk, custom_claims={"exp": int(time.time()) - 100}),  # Expired
        make_token(pk, custom_claims={"iss": "https://wrong.iss"}),  # Wrong iss
        make_token(pk, custom_claims={"aud": "wrong.aud"}),  # Wrong aud
        make_token(pk, omit_claims=["tenant_id"]),  # Missing tenant_id
        make_token(pk, custom_claims={"tenant_id": ""}),  # Empty tenant_id
        make_token(pk, custom_claims={"tenant_id": "invalid-uuid"}),  # Non-UUID tenant_id
        make_token(pk, custom_claims={"tenant_id": NIL_TENANT_ID}),  # Nil UUID
        make_token(pk, omit_claims=["sub"]),  # Missing sub
    ]

    expected_status = 401
    expected_header = "Bearer"
    expected_body = {"detail": "Not authenticated"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for t in failure_tokens:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"})
            assert resp.status_code == expected_status
            assert resp.headers.get("WWW-Authenticate") == expected_header
            assert resp.json() == expected_body


@pytest.mark.asyncio
async def test_tenant_id_bound_to_request_state_and_dependency(claims_fixture):
    """Verifies request.state.tenant_id and get_current_tenant_id dependency."""
    # 1. State set on request during live request
    token = make_token(claims_fixture["private_key"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == VALID_TENANT_ID

    # 2. get_current_tenant_id dependency unit test
    fake_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "state": {
                "claims": {
                    "sub": "user-test",
                    "tenant_id": VALID_TENANT_ID,
                }
            },
        }
    )
    tid = get_current_tenant_id(fake_request.state.claims)
    assert tid == VALID_TENANT_ID
