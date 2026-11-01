"""Comprehensive test suite for permission enforcement on every API request (SCRUM-306 / US-11).

Acceptance Criteria:
1. Each protected route has a declared permission.
2. Unauthorised calls return HTTP 403 with consistent error body.
3. A route without a declared permission is rejected (deny-by-default).
4. Role resolution checks token claims or database fallback against roles-permissions.json.
5. Reusable dependency and decorator support.
"""

import json
import time
from typing import Any
import unittest.mock as mock

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from fastapi import Depends
from starlette.requests import Request

from app.auth.jwks import JWKSClient
from app.auth.middleware import JWTAuthMiddleware
from app.auth.permissions import (
    FORBIDDEN_RESPONSE,
    Permission,
    get_permission_matrix,
    get_route_declared_permissions,
    require_permission,
    resolve_caller_roles,
)
from app.auth.schemas import TokenClaims
from app.config import settings
from app.main import app

TENANT_ID = "11111111-1111-1111-1111-111111111111"


class MockJWKSServer:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]})


def generate_rsa_keypair(kid: str = "perm-test-key") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


def make_token(
    private_key: rsa.RSAPrivateKey,
    kid: str = "perm-test-key",
    sub: str = "perm_user",
    roles: list[str] | None = None,
    direct_role: str | None = None,
    tenant_id: str = TENANT_ID,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "name": f"User {sub}",
        "email": f"{sub}@manta.local",
        "preferred_username": sub,
        "tenant_id": tenant_id,
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
    }
    if roles is not None:
        payload["realm_access"] = {"roles": roles}
    if direct_role is not None:
        payload["role"] = direct_role

    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid, "alg": "RS256"})


@pytest.fixture
def auth_fixture():
    private_key, jwk = generate_rsa_keypair("perm-test-key")
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


# ==============================================================================
# AC 1: Each protected route has a declared permission
# ==============================================================================


def test_each_protected_route_has_declared_permission():
    """Verify that every route not exempt by public paths has a declared matrix permission."""
    from fastapi.routing import APIRoute

    matrix = get_permission_matrix()
    valid_actions = set(matrix.actions_data.keys())

    all_routes: list[tuple[str, Any]] = []

    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            original = getattr(route, "original_router", None)
            if original:
                for sub in original.routes:
                    all_routes.append((getattr(sub, "path", ""), sub))
        elif isinstance(route, APIRoute):
            all_routes.append((route.path, route))

    unprotected_routes = []
    for path, r in all_routes:
        if JWTAuthMiddleware.is_public(path):
            continue

        perms = get_route_declared_permissions(r)
        if not perms:
            unprotected_routes.append(path)
        else:
            # Must be valid permissions from matrix
            invalid = perms - valid_actions
            assert not invalid, f"Route {path} has invalid permissions: {invalid}"

    assert not unprotected_routes, (
        f"Protected routes without declared permissions: {unprotected_routes}"
    )


# ==============================================================================
# AC 2: Unauthorised calls return HTTP 403 with consistent error body
# ==============================================================================


@pytest.mark.asyncio
async def test_all_91_matrix_decisions_enforced(auth_fixture):
    """
    Exhaustively evaluate all 91 (role, action) pairs in docs/roles-permissions.json.
    Verifies that ALLOW yields HTTP 200, and DENY yields HTTP 403 with consistent error body.
    """
    matrix = get_permission_matrix()
    pk = auth_fixture["private_key"]

    # Register temporary test endpoint for each action to isolate matrix evaluation
    from fastapi import APIRouter

    test_router = APIRouter(prefix="/api/v1/test-matrix", tags=["matrix-test"])
    for perm_key in matrix.actions_data.keys():
        perm_action = Permission(perm_key)

        def make_endpoint(p):
            async def handler(u: TokenClaims = Depends(require_permission(p))):
                return {"action": p, "sub": u.sub}

            return handler

        endpoint = make_endpoint(perm_action)
        test_router.add_api_route(
            f"/{perm_key.lower()}",
            endpoint,
            methods=["GET"],
        )

    app.include_router(test_router)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            for role_id, role_actions in matrix.matrix.items():
                token = make_token(pk, roles=[role_id], sub=f"user_{role_id.lower()}")
                headers = {"Authorization": f"Bearer {token}"}

                for action_id, cell in role_actions.items():
                    expected_decision = cell["decision"]
                    url = f"/api/v1/test-matrix/{action_id.lower()}"

                    resp = await ac.get(url, headers=headers)
                    if expected_decision == "ALLOW":
                        assert resp.status_code == 200, (
                            f"Expected ALLOW for [{role_id}][{action_id}], got {resp.status_code}: {resp.text}"
                        )
                    else:
                        assert resp.status_code == 403, (
                            f"Expected DENY for [{role_id}][{action_id}], got {resp.status_code}: {resp.text}"
                        )
                        assert resp.json() == FORBIDDEN_RESPONSE, (
                            f"Expected consistent 403 body, got: {resp.json()}"
                        )
    finally:
        # Clean up temporary test routes
        app.routes[:] = [
            r for r in app.routes if not getattr(r, "path", "").startswith("/api/v1/test-matrix")
        ]


@pytest.mark.asyncio
async def test_empty_or_unrecognized_roles_rejected_with_403(auth_fixture):
    """Callers with empty or non-system roles receive HTTP 403 consistent error."""
    pk = auth_fixture["private_key"]
    token_empty = make_token(pk, roles=[])
    token_unknown = make_token(pk, roles=["GUEST", "default-roles-manta", "offline_access"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Empty roles on /me
        r1 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_empty}"})
        assert r1.status_code == 403
        assert r1.json() == FORBIDDEN_RESPONSE

        # Unknown roles on /me
        r2 = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_unknown}"})
        assert r2.status_code == 403
        assert r2.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
async def test_deprecated_delivery_exec_alias_mapped(auth_fixture):
    """The deprecated DELIVERY_EXEC alias must resolve to DELIVERY_DIRECTOR permissions."""
    pk = auth_fixture["private_key"]
    token_exec = make_token(pk, roles=["DELIVERY_EXEC"])

    claims = TokenClaims.from_claims(jwt.decode(token_exec, options={"verify_signature": False}))
    matrix = get_permission_matrix()
    dd_permissions = matrix.matrix["DELIVERY_DIRECTOR"]

    # In DELIVERY_DIRECTOR: VIEW_PORTFOLIO_OVERVIEW is ALLOW, TRIGGER_REFACTOR_ANALYSIS is DENY
    assert dd_permissions["VIEW_PORTFOLIO_OVERVIEW"]["decision"] == "ALLOW"
    assert dd_permissions["TRIGGER_REFACTOR_ANALYSIS"]["decision"] == "DENY"

    assert matrix.is_allowed(claims.roles, [Permission.VIEW_PORTFOLIO_OVERVIEW]) is True
    assert matrix.is_allowed(claims.roles, [Permission.TRIGGER_REFACTOR_ANALYSIS]) is False


@pytest.mark.asyncio
async def test_multi_role_composition(auth_fixture):
    """A user holding multiple roles is permitted if ANY assigned role allows the action."""
    matrix = get_permission_matrix()
    # DEVELOPER cannot APPROVE_ADR_TENANT_PRIVATE, but SOLUTION_ARCHITECT can
    assert matrix.is_allowed(["DEVELOPER"], [Permission.APPROVE_ADR_TENANT_PRIVATE]) is False
    assert (
        matrix.is_allowed(
            ["DEVELOPER", "SOLUTION_ARCHITECT"], [Permission.APPROVE_ADR_TENANT_PRIVATE]
        )
        is True
    )


# ==============================================================================
# AC 3: A route without a declared permission is rejected (deny-by-default)
# ==============================================================================


@pytest.mark.asyncio
async def test_route_without_declared_permission_rejected_by_default(auth_fixture):
    """
    AC 3: An unexempted route with NO declared permission is rejected with HTTP 403
    even when called by TENANT_ADMIN, and the route handler is never executed.
    """
    from fastapi import APIRouter

    handler_called = False
    undeclared_router = APIRouter(prefix="/api/v1/undeclared")

    @undeclared_router.get("/data")
    async def get_undeclared_data():
        nonlocal handler_called
        handler_called = True
        return {"data": "sensitive"}

    app.include_router(undeclared_router)

    try:
        pk = auth_fixture["private_key"]
        admin_token = make_token(pk, roles=["TENANT_ADMIN"])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/undeclared/data",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN_RESPONSE
        assert handler_called is False, "Handler must not be executed on undeclared route"
    finally:
        app.routes[:] = [
            r for r in app.routes if not getattr(r, "path", "").startswith("/api/v1/undeclared")
        ]


# ==============================================================================
# Database Fallback & Direct Role Claims
# ==============================================================================


@pytest.mark.asyncio
async def test_token_top_level_role_claim_supported(auth_fixture):
    """Direct top-level 'role' string claim in token is parsed into TokenClaims.roles."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, direct_role="SOLUTION_ARCHITECT", sub="arch_user")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["roles"] == ["SOLUTION_ARCHITECT"]


@pytest.mark.asyncio
async def test_database_role_fallback_when_token_lacks_roles():
    """
    When token contains no canonical roles, resolve_caller_roles falls back
    to querying the database under Row-Level Security for users.role and user_roles.
    """
    user = TokenClaims(
        sub="db_user_123",
        email="dbuser@manta.local",
        tenant_id=TENANT_ID,
        roles=[],
    )

    # 1. Mock DB session returning User.role = "DEVELOPER"
    mock_result = mock.MagicMock()
    mock_result.all.return_value = [("DEVELOPER", None)]

    mock_session = mock.AsyncMock()
    mock_session.execute = mock.AsyncMock(return_value=mock_result)

    with mock.patch("app.auth.permissions.get_tenant_db_context") as mock_db_ctx:
        mock_db_ctx.return_value.__aenter__.return_value = mock_session
        roles = await resolve_caller_roles(user)
        assert roles == {"DEVELOPER"}

    # 2. Database exception handled gracefully without crashing (fails closed to empty set)
    with mock.patch("app.auth.permissions.get_tenant_db_context", side_effect=Exception("DB Down")):
        roles_err = await resolve_caller_roles(user)
        assert roles_err == set()


# ==============================================================================
# Reusable Decorator Support
# ==============================================================================


@pytest.mark.asyncio
async def test_require_permission_as_decorator(auth_fixture):
    """@require_permission(...) decorator directly on route enforces 200 vs 403."""
    from fastapi import APIRouter

    dec_router = APIRouter(prefix="/api/v1/dec-test")

    @dec_router.post("/publish-adr")
    @require_permission(Permission.PUBLISH_GLOBAL_ADR)
    async def publish_adr(request: Request):
        return {"published": True}

    app.include_router(dec_router)

    try:
        pk = auth_fixture["private_key"]
        sa_token = make_token(pk, roles=["SOLUTION_ARCHITECT"])
        dev_token = make_token(pk, roles=["DEVELOPER"])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # SOLUTION_ARCHITECT allowed
            resp_sa = await ac.post(
                "/api/v1/dec-test/publish-adr",
                headers={"Authorization": f"Bearer {sa_token}"},
            )
            assert resp_sa.status_code == 200
            assert resp_sa.json() == {"published": True}

            # DEVELOPER denied with 403
            resp_dev = await ac.post(
                "/api/v1/dec-test/publish-adr",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert resp_dev.status_code == 403
            assert resp_dev.json() == FORBIDDEN_RESPONSE
    finally:
        app.routes[:] = [
            r for r in app.routes if not getattr(r, "path", "").startswith("/api/v1/dec-test")
        ]


def test_require_permission_validation_errors():
    """require_permission rejects empty permissions or unknown action identifiers."""
    with pytest.raises(ValueError, match="at least one declared permission"):
        require_permission()

    with pytest.raises(ValueError, match="Unknown permission"):
        require_permission("NON_EXISTENT_ACTION")
