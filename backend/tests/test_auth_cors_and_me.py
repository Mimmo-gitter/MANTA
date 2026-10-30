import json
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


class MockJWKSServer:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]})


def generate_keypair(kid: str = "cors-me-key") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_BETA = "22222222-2222-2222-2222-222222222222"


def make_token(
    private_key: rsa.RSAPrivateKey,
    kid: str = "cors-me-key",
    sub: str = "alpha_dev",
    name: str = "Alpha Developer",
    tenant_id: str = TENANT_ALPHA,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "name": name,
        "email": f"{sub}@manta.local",
        "preferred_username": sub,
        "tenant_id": tenant_id,
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
        "realm_access": {"roles": ["DEVELOPER"]},
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid, "alg": "RS256"})


@pytest.fixture
def auth_fixture():
    private_key, jwk = generate_keypair("cors-me-key")
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
async def test_cors_preflight_options(auth_fixture):
    """Verify CORS preflight OPTIONS returns allowed origin and credentials."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.options(
            "/api/v1/auth/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert resp.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_get_me_nominal_tenant_alpha(auth_fixture):
    """Verify GET /api/v1/auth/me returns name and tenant_id for Tenant Alpha."""
    token = make_token(
        auth_fixture["private_key"],
        sub="alpha_dev",
        name="Alpha Developer",
        tenant_id=TENANT_ALPHA,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://localhost:3000",
            },
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    data = resp.json()
    assert data["sub"] == "alpha_dev"
    assert data["name"] == "Alpha Developer"
    assert data["tenant_id"] == TENANT_ALPHA
    assert data["roles"] == ["DEVELOPER"]


@pytest.mark.asyncio
async def test_get_me_nominal_tenant_beta(auth_fixture):
    """Verify GET /api/v1/auth/me returns name and tenant_id for Tenant Beta."""
    token = make_token(
        auth_fixture["private_key"],
        sub="beta_dev",
        name="Beta Developer",
        tenant_id=TENANT_BETA,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://localhost:3000",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["sub"] == "beta_dev"
    assert data["name"] == "Beta Developer"
    assert data["tenant_id"] == TENANT_BETA


@pytest.mark.asyncio
async def test_get_me_unauthorized_without_token(auth_fixture):
    """Verify GET /api/v1/auth/me returns 401 without Bearer token with CORS headers intact."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me",
            headers={"Origin": "http://localhost:3000"},
        )
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
