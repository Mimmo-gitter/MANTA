"""
SCRUM-311: Role-Access Test Scenarios Test Suite.
Parent Story: SCRUM-247 (US-11: Role-based access control).

Automated verification of representative allowed and denied actions for each of the
seven system roles, as defined in `docs/role-access-test-scenarios.md` and the
authoritative matrix in `docs/roles-permissions.json`.

Scenarios covered:
- TC-RBAC-DEV-001: DEVELOPER allowed TRIGGER_REFACTOR_ANALYSIS (HTTP 501 / 200)
- TC-RBAC-DEV-002: DEVELOPER denied MANAGE_WEBHOOK_INTEGRATIONS (HTTP 403)
- TC-RBAC-SM-001:  SCRUM_MASTER allowed VIEW_SPRINT_BURNDOWN_WIP (HTTP 501 / 200)
- TC-RBAC-SM-002:  SCRUM_MASTER denied TRIGGER_REFACTOR_ANALYSIS (HTTP 403)
- TC-RBAC-PM-001:  PROJECT_MANAGER allowed RECEIVE_SCOPE_CREEP_ALERT (HTTP 501 / 200)
- TC-RBAC-PM-002:  PROJECT_MANAGER denied REASSIGN_PR_REVIEWER_MOBILE (HTTP 403)
- TC-RBAC-SA-001:  SOLUTION_ARCHITECT allowed PUBLISH_GLOBAL_ADR (HTTP 501 / 200)
- TC-RBAC-SA-002:  SOLUTION_ARCHITECT denied MANAGE_WEBHOOK_INTEGRATIONS (HTTP 403)
- TC-RBAC-DD-001:  DELIVERY_DIRECTOR allowed VIEW_PORTFOLIO_OVERVIEW (HTTP 501 / 200)
- TC-RBAC-DD-002:  DELIVERY_DIRECTOR denied TRIGGER_REFACTOR_ANALYSIS (HTTP 403)
- TC-RBAC-TM-001:  TOP_MANAGEMENT allowed VIEW_PORTFOLIO_OVERVIEW (HTTP 501 / 200)
- TC-RBAC-TM-002:  TOP_MANAGEMENT denied VIEW_SPRINT_BURNDOWN_WIP (HTTP 403)
- TC-RBAC-TA-001:  TENANT_ADMIN allowed MANAGE_WEBHOOK_INTEGRATIONS (HTTP 200)
- TC-RBAC-EDGE-001: DELIVERY_EXEC alias resolved to DELIVERY_DIRECTOR
- TC-RBAC-EDGE-002: Multi-role composition (DEVELOPER + TENANT_ADMIN)
- TC-RBAC-EDGE-003: Empty or unknown roles denied by default (HTTP 403)
- TC-RBAC-EDGE-004: Unauthenticated request rejected (HTTP 401)
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

from app.auth.jwks import JWKSClient
from app.auth.permissions import FORBIDDEN_RESPONSE
from app.config import settings
from app.main import app

TENANT_ID = "11111111-1111-1111-1111-111111111111"


class MockJWKSServer:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]})


def generate_rsa_keypair(kid: str = "role-test-key") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


def make_token(
    private_key: rsa.RSAPrivateKey,
    kid: str = "role-test-key",
    sub: str = "test_user",
    roles: list[str] | None = None,
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

    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid, "alg": "RS256"})


@pytest.fixture
def auth_fixture():
    private_key, jwk = generate_rsa_keypair("role-test-key")
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
# Role 1: DEVELOPER Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_developer_allowed_trigger_refactor_analysis(auth_fixture):
    """TC-RBAC-DEV-001: DEVELOPER is allowed to TRIGGER_REFACTOR_ANALYSIS."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["DEVELOPER"], sub="alpha_dev")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/refactoring/projects/{TENANT_ID}/analyze",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Stub endpoint returns HTTP 501 with not_implemented schema
    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert body["action"] == "TRIGGER_REFACTOR_ANALYSIS"


@pytest.mark.asyncio
async def test_developer_denied_manage_webhook_integrations(auth_fixture):
    """TC-RBAC-DEV-002: DEVELOPER is denied MANAGE_WEBHOOK_INTEGRATIONS."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["DEVELOPER"], sub="alpha_dev")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/integrations",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# Role 2: SCRUM_MASTER Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_scrum_master_allowed_view_sprint_burndown(auth_fixture):
    """TC-RBAC-SM-001: SCRUM_MASTER is allowed to VIEW_SPRINT_BURNDOWN_WIP."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["SCRUM_MASTER"], sub="alpha_scrum")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/{TENANT_ID}/sprints/burndown",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert body["action"] == "VIEW_SPRINT_BURNDOWN_WIP"


@pytest.mark.asyncio
async def test_scrum_master_denied_trigger_refactor_analysis(auth_fixture):
    """TC-RBAC-SM-002: SCRUM_MASTER is denied TRIGGER_REFACTOR_ANALYSIS."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["SCRUM_MASTER"], sub="alpha_scrum")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/refactoring/projects/{TENANT_ID}/analyze",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# Role 3: PROJECT_MANAGER Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_project_manager_allowed_scope_creep_alert(auth_fixture):
    """TC-RBAC-PM-001: PROJECT_MANAGER is allowed to RECEIVE_SCOPE_CREEP_ALERT."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["PROJECT_MANAGER"], sub="alpha_pm")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/{TENANT_ID}/scope-alerts",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert body["action"] == "RECEIVE_SCOPE_CREEP_ALERT"


@pytest.mark.asyncio
async def test_project_manager_denied_reassign_pr_reviewer(auth_fixture):
    """TC-RBAC-PM-002: PROJECT_MANAGER is denied REASSIGN_PR_REVIEWER_MOBILE."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["PROJECT_MANAGER"], sub="alpha_pm")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/projects/{TENANT_ID}/pull-requests/pr-1/reassign-reviewer",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# Role 4: SOLUTION_ARCHITECT Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_solution_architect_allowed_publish_global_adr(auth_fixture):
    """TC-RBAC-SA-001: SOLUTION_ARCHITECT is allowed to PUBLISH_GLOBAL_ADR."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["SOLUTION_ARCHITECT"], sub="alpha_arch")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/adrs/adr-1/publish-global",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert body["action"] == "PUBLISH_GLOBAL_ADR"


@pytest.mark.asyncio
async def test_solution_architect_denied_manage_webhooks(auth_fixture):
    """TC-RBAC-SA-002: SOLUTION_ARCHITECT is denied MANAGE_WEBHOOK_INTEGRATIONS."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["SOLUTION_ARCHITECT"], sub="alpha_arch")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/integrations",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# Role 5: DELIVERY_DIRECTOR Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_delivery_director_allowed_view_portfolio(auth_fixture):
    """TC-RBAC-DD-001: DELIVERY_DIRECTOR is allowed to VIEW_PORTFOLIO_OVERVIEW."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["DELIVERY_DIRECTOR"], sub="alpha_director")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/dora/portfolio",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert body["action"] == "VIEW_PORTFOLIO_OVERVIEW"


@pytest.mark.asyncio
async def test_delivery_director_denied_trigger_refactor(auth_fixture):
    """TC-RBAC-DD-002: DELIVERY_DIRECTOR is denied TRIGGER_REFACTOR_ANALYSIS."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["DELIVERY_DIRECTOR"], sub="alpha_director")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/refactoring/projects/{TENANT_ID}/analyze",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# Role 6: TOP_MANAGEMENT Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_top_management_allowed_view_portfolio(auth_fixture):
    """TC-RBAC-TM-001: TOP_MANAGEMENT is allowed to VIEW_PORTFOLIO_OVERVIEW."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["TOP_MANAGEMENT"], sub="alpha_top")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/dora/portfolio",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 501
    body = resp.json()
    assert body["status"] == "not_implemented"
    assert body["action"] == "VIEW_PORTFOLIO_OVERVIEW"


@pytest.mark.asyncio
async def test_top_management_denied_view_sprint_burndown(auth_fixture):
    """TC-RBAC-TM-002: TOP_MANAGEMENT is denied VIEW_SPRINT_BURNDOWN_WIP."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["TOP_MANAGEMENT"], sub="alpha_top")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/{TENANT_ID}/sprints/burndown",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


# ==============================================================================
# Role 7: TENANT_ADMIN Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_tenant_admin_allowed_manage_integrations(auth_fixture):
    """TC-RBAC-TA-001: TENANT_ADMIN is allowed to MANAGE_WEBHOOK_INTEGRATIONS."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")

    with mock.patch(
        "app.integrations.router.list_tenant_integrations",
        mock.AsyncMock(return_value=[]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    assert resp.json() == []


# ==============================================================================
# Edge Case Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_edge_deprecated_delivery_exec_alias(auth_fixture):
    """TC-RBAC-EDGE-001: DELIVERY_EXEC alias resolved to DELIVERY_DIRECTOR permissions."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["DELIVERY_EXEC"], sub="beta_exec")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Allowed for DELIVERY_DIRECTOR: VIEW_PORTFOLIO_OVERVIEW
        r_allow = await ac.get(
            "/api/v1/dora/portfolio",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r_allow.status_code == 501

        # Denied for DELIVERY_DIRECTOR: TRIGGER_REFACTOR_ANALYSIS
        r_deny = await ac.post(
            f"/api/v1/refactoring/projects/{TENANT_ID}/analyze",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r_deny.status_code == 403
        assert r_deny.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
async def test_edge_multi_role_composition(auth_fixture):
    """TC-RBAC-EDGE-002: User holding multiple roles receives union of allowed permissions."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["DEVELOPER", "TENANT_ADMIN"], sub="composite_user")

    with mock.patch(
        "app.integrations.router.list_tenant_integrations",
        mock.AsyncMock(return_value=[]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Granted by TENANT_ADMIN
            r_admin = await ac.get(
                "/api/v1/integrations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_admin.status_code == 200

            # Granted by DEVELOPER
            r_dev = await ac.post(
                f"/api/v1/refactoring/projects/{TENANT_ID}/analyze",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_dev.status_code == 501


@pytest.mark.asyncio
async def test_edge_empty_or_unknown_roles_deny_by_default(auth_fixture):
    """TC-RBAC-EDGE-003: Callers with empty or unrecognized roles receive HTTP 403."""
    pk = auth_fixture["private_key"]
    token_empty = make_token(pk, roles=[])
    token_unknown = make_token(pk, roles=["GUEST", "UNKNOWN_ROLE"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for t in [token_empty, token_unknown]:
            resp = await ac.get(
                "/api/v1/dora/portfolio",
                headers={"Authorization": f"Bearer {t}"},
            )
            assert resp.status_code == 403
            assert resp.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
async def test_edge_unauthenticated_request_returns_401():
    """TC-RBAC-EDGE-004: Requests lacking Bearer token return HTTP 401 Unauthorized."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/integrations")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Not authenticated"}
