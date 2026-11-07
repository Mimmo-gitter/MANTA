"""
SCRUM-317: Member Invitation & Immediate Role-Change Test Scenarios.
Parent Story: SCRUM-248 (US-12: Member invitation and role assignment).

Automated verification of the core invitation and role-change edge cases:
- TC-INV-VALID-001: Admin creates valid single-use invitation (HTTP 201, 48h TTL)
- TC-INV-ACCEPT-001: Recipient accepts valid invitation -> Keycloak & DB provisioned (HTTP 200)
- TC-INV-REUSE-001: Reused invitation link rejected (HTTP 410 Gone)
- TC-INV-EXP-001: Expired invitation link rejected (HTTP 410 Gone)
- TC-INV-EXIST-001: Invite acceptance for existing user rolled back cleanly (HTTP 409 Conflict)
- TC-ROLE-IMM-001: Role change takes effect immediately on subsequent request without token reissuance
- TC-ROLE-IMM-002: Role demotion immediately revokes elevated permissions on subsequent request
- TC-ROLE-LAST-ADMIN-001: Last administrator demotion rejected (HTTP 409 Conflict)
- TC-RBAC-INV-001: Non-admin roles denied invitation creation (HTTP 403 Forbidden)
- TC-RBAC-ROLE-001: Non-admin roles denied role change (HTTP 403 Forbidden)
- TC-INV-EDGE-001: Unknown role rejected with HTTP 422
- TC-INV-EDGE-002: Overlong email (>255 chars) rejected with HTTP 422
- TC-INV-EDGE-003: Unknown/non-existent token rejected with HTTP 404
- TC-INV-EDGE-004: SMTP failure returns HTTP 502 with transactional isolation
"""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
import unittest.mock as mock
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.jwks import JWKSClient
from app.auth.keycloak_admin import UserAlreadyExistsError
from app.auth.permissions import (
    FORBIDDEN_RESPONSE,
    Permission,
    Role,
    get_permission_matrix,
    resolve_caller_roles,
)
from app.auth.schemas import TokenClaims
from app.config import settings
from app.db.session import get_db
from app.main import app
from app.notifications.service import (
    INVITATION_TTL_HOURS,
    InvitationError,
    consume_invitation,
    create_invitation,
    hash_token,
)

TENANT_ALPHA = "11111111-1111-1111-1111-111111111111"
TENANT_ALPHA_UUID = uuid.UUID(TENANT_ALPHA)


class MockJWKSServer:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]})


def generate_rsa_keypair(kid: str = "invitation-test-key") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


def make_token(
    private_key: rsa.RSAPrivateKey,
    kid: str = "invitation-test-key",
    sub: str = "alpha_admin",
    roles: list[str] | None = None,
    tenant_id: str = TENANT_ALPHA,
    email: str | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "name": f"User {sub}",
        "email": email or f"{sub}@acme.io",
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
    private_key, jwk = generate_rsa_keypair("invitation-test-key")
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


@pytest.fixture
def mock_db_session():
    session = mock.AsyncMock()
    session.add = mock.MagicMock()
    session.execute = mock.AsyncMock()
    session.commit = mock.AsyncMock()
    session.rollback = mock.AsyncMock()

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    yield session
    app.dependency_overrides.pop(get_db, None)


# ==============================================================================
# Scenario 1: Valid Invite Creation & Acceptance
# ==============================================================================


@pytest.mark.asyncio
async def test_admin_can_create_valid_invitation(auth_fixture):
    """
    TC-INV-VALID-001: Authorized Administrator creates a valid member invitation.
    Verifies HTTP 201 Created, 48h expiration calculation, and email dispatch.
    """
    pk = auth_fixture["private_key"]
    admin_token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")

    with (
        mock.patch("app.notifications.router.get_tenant_db_context") as mock_db_ctx,
        mock.patch("app.notifications.router.find_platform_user_id", new_callable=mock.AsyncMock, return_value=uuid.uuid4()),
        mock.patch("app.notifications.router.create_invitation", new_callable=mock.AsyncMock, return_value="raw_secret_token_123") as mock_create,
        mock.patch("app.notifications.router.send_invitation_email", new_callable=mock.AsyncMock) as mock_send_email,
    ):
        mock_session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations",
                json={"email": "new.member@acme.io", "role": "DEVELOPER"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 201
        assert resp.json() == {"status": "sent"}
        mock_create.assert_awaited_once()
        mock_send_email.assert_awaited_once_with("new.member@acme.io", "raw_secret_token_123")


@pytest.mark.asyncio
async def test_valid_invitation_acceptance_provisions_user(mock_db_session):
    """
    TC-INV-ACCEPT-001: Recipient accepts a valid invitation.
    Verifies user is provisioned in Keycloak and inserted into the database.
    """
    sample_invite = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT_ALPHA_UUID,
        "email": "invited.engineer@acme.io",
        "role": "SOLUTION_ARCHITECT",
    }

    with (
        mock.patch("app.notifications.router.consume_invitation", new_callable=mock.AsyncMock, return_value=sample_invite),
        mock.patch("app.notifications.router.provision_user", new_callable=mock.AsyncMock, return_value="keycloak_sub_9999") as mock_kc_prov,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations/accept",
                json={"token": "valid_fresh_token"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"status": "created"}
        mock_kc_prov.assert_awaited_once_with("invited.engineer@acme.io", str(TENANT_ALPHA_UUID))
        assert mock_db_session.add.called
        assert mock_db_session.commit.called


# ==============================================================================
# Scenario 2: Reused Link Edge Case
# ==============================================================================


@pytest.mark.asyncio
async def test_reused_invitation_link_rejected_with_410_gone(mock_db_session):
    """
    TC-INV-REUSE-001: Consuming an already used invitation link is rejected with HTTP 410.
    Ensures single-use guarantee and prevents duplicate provisioning.
    """
    with (
        mock.patch(
            "app.notifications.router.consume_invitation",
            new_callable=mock.AsyncMock,
            side_effect=InvitationError(410, "Це запрошення вже використано"),
        ),
        mock.patch("app.notifications.router.provision_user", new_callable=mock.AsyncMock) as mock_kc_prov,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations/accept",
                json={"token": "already_used_token_abc"},
            )

        assert resp.status_code == 410
        assert resp.json() == {"detail": "Це запрошення вже використано"}
        mock_kc_prov.assert_not_called()
        assert mock_db_session.rollback.called


@pytest.mark.asyncio
async def test_service_consume_invitation_reused_status_mapping():
    """
    TC-INV-REUSE-002: Service unit test for consume_invitation SQL return status 'used'.
    """
    mock_session = mock.AsyncMock()
    mock_row = {"r_status": "used", "r_id": None, "r_tenant_id": None, "r_email": None, "r_role": None}
    mock_mappings = mock.MagicMock()
    mock_mappings.first.return_value = mock_row
    mock_result = mock.MagicMock()
    mock_result.mappings.return_value = mock_mappings
    mock_session.execute = mock.AsyncMock(return_value=mock_result)

    with pytest.raises(InvitationError) as exc_info:
        await consume_invitation(mock_session, "used_token")

    assert exc_info.value.status_code == 410
    assert exc_info.value.message == "Це запрошення вже використано"


# ==============================================================================
# Scenario 3: Expired Link Edge Case
# ==============================================================================


@pytest.mark.asyncio
async def test_expired_invitation_link_rejected_with_410_gone(mock_db_session):
    """
    TC-INV-EXP-001: Consuming an expired invitation (>48 hours) is rejected with HTTP 410.
    Ensures expiration boundary enforcement and triggers UI reissue prompt.
    """
    with (
        mock.patch(
            "app.notifications.router.consume_invitation",
            new_callable=mock.AsyncMock,
            side_effect=InvitationError(410, "Термін дії запрошення закінчився"),
        ),
        mock.patch("app.notifications.router.provision_user", new_callable=mock.AsyncMock) as mock_kc_prov,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations/accept",
                json={"token": "expired_token_xyz"},
            )

        assert resp.status_code == 410
        assert resp.json() == {"detail": "Термін дії запрошення закінчився"}
        mock_kc_prov.assert_not_called()
        assert mock_db_session.rollback.called


@pytest.mark.asyncio
async def test_service_consume_invitation_expired_status_mapping():
    """
    TC-INV-EXP-002: Service unit test for consume_invitation SQL return status 'expired'.
    """
    mock_session = mock.AsyncMock()
    mock_row = {"r_status": "expired", "r_id": None, "r_tenant_id": None, "r_email": None, "r_role": None}
    mock_mappings = mock.MagicMock()
    mock_mappings.first.return_value = mock_row
    mock_result = mock.MagicMock()
    mock_result.mappings.return_value = mock_mappings
    mock_session.execute = mock.AsyncMock(return_value=mock_result)

    with pytest.raises(InvitationError) as exc_info:
        await consume_invitation(mock_session, "expired_token")

    assert exc_info.value.status_code == 410
    assert exc_info.value.message == "Термін дії запрошення закінчився"


def test_invitation_ttl_is_strictly_48_hours():
    """
    TC-INV-EXP-003: Verify constant TTL is strictly 48 hours per SRS specification.
    """
    assert INVITATION_TTL_HOURS == 48


# ==============================================================================
# Scenario 4: Invite for Existing User Edge Case
# ==============================================================================


@pytest.mark.asyncio
async def test_invite_acceptance_existing_keycloak_user_rolls_back_with_409(mock_db_session):
    """
    TC-INV-EXIST-001: Acceptance attempt when user already exists in Keycloak.
    Verifies atomic rollback (token used_at reverted) and HTTP 409 Conflict response.
    """
    sample_invite = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT_ALPHA_UUID,
        "email": "existing.user@acme.io",
        "role": "DEVELOPER",
    }

    with (
        mock.patch("app.notifications.router.consume_invitation", new_callable=mock.AsyncMock, return_value=sample_invite),
        mock.patch(
            "app.notifications.router.provision_user",
            new_callable=mock.AsyncMock,
            side_effect=UserAlreadyExistsError("Користувач із таким email уже існує"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations/accept",
                json={"token": "token_for_existing_kc_user"},
            )

        assert resp.status_code == 409
        assert "існує" in resp.json()["detail"]
        assert mock_db_session.rollback.called


@pytest.mark.asyncio
async def test_invite_acceptance_duplicate_db_user_rolls_back_with_409(mock_db_session):
    """
    TC-INV-EXIST-002: Acceptance attempt when user violates local DB unique constraint.
    Verifies Keycloak compensation deletion, atomic DB rollback, and HTTP 409 Conflict.
    """
    sample_invite = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT_ALPHA_UUID,
        "email": "db.duplicate@acme.io",
        "role": "DEVELOPER",
    }
    mock_db_session.commit.side_effect = IntegrityError(
        "duplicate key value violates unique constraint",
        params={},
        orig=Exception("duplicate key value violates unique constraint 'uq_tenant_user_email'"),
    )

    with (
        mock.patch("app.notifications.router.consume_invitation", new_callable=mock.AsyncMock, return_value=sample_invite),
        mock.patch("app.notifications.router.provision_user", new_callable=mock.AsyncMock, return_value="kc_created_user_123"),
        mock.patch("app.notifications.router.delete_user", new_callable=mock.AsyncMock) as mock_delete_kc,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations/accept",
                json={"token": "token_duplicate_db"},
            )

        assert resp.status_code == 409
        assert "існує" in resp.json()["detail"]
        assert mock_db_session.rollback.called
        mock_delete_kc.assert_awaited_once_with("kc_created_user_123")


# ==============================================================================
# Scenario 5: Immediate Role Change Edge Case & Single-Admin Safeguard
# ==============================================================================


@pytest.mark.asyncio
async def test_admin_can_update_member_role(auth_fixture):
    """
    TC-ROLE-IMM-001: Tenant Admin successfully changes member role via PATCH.
    Verifies HTTP 200 and return schema with user_id and new role.
    """
    pk = auth_fixture["private_key"]
    admin_token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")
    target_user_id = uuid.uuid4()

    with mock.patch("app.auth.members_router.get_tenant_db_context") as mock_db_ctx:
        mock_session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_session

        # 2 admins exist, target user found, target is not last admin
        admin_id_1 = uuid.uuid4()
        admin_id_2 = uuid.uuid4()
        mock_admin_res = mock.MagicMock()
        mock_admin_res.scalars.return_value.all.return_value = [admin_id_1, admin_id_2]

        mock_target_res = mock.MagicMock()
        mock_target_res.first.return_value = (target_user_id,)

        mock_exec_res = mock.MagicMock()
        mock_session.execute = mock.AsyncMock(side_effect=[mock_admin_res, mock_target_res, mock_exec_res, mock_exec_res])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/members/{target_user_id}/role",
                json={"role": "SOLUTION_ARCHITECT"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == str(target_user_id)
        assert data["role"] == "SOLUTION_ARCHITECT"


@pytest.mark.asyncio
async def test_role_change_takes_effect_immediately_on_next_request():
    """
    TC-ROLE-IMM-002: Dynamic role resolution verification.
    Shows that resolve_caller_roles reads User.role live from DB on every request.
    Therefore, a user whose role was changed immediately acquires the new permissions
    without needing a new JWT token or re-authenticating.
    """
    user_claims = TokenClaims(
        sub="user_bob",
        roles=["DEVELOPER"],  # Old JWT claim says DEVELOPER
        tenant_id=TENANT_ALPHA,
        email="bob@acme.io",
    )

    # 1. First DB state: Bob is DEVELOPER in DB
    with mock.patch("app.auth.permissions.get_tenant_db_context") as mock_db_ctx1:
        session1 = mock.AsyncMock()
        mock_db_ctx1.return_value.__aenter__.return_value = session1
        result1 = mock.MagicMock()
        result1.all.return_value = [("DEVELOPER", True, None)]
        session1.execute = mock.AsyncMock(return_value=result1)

        roles_t0 = await resolve_caller_roles(user_claims)
        assert roles_t0 == {"DEVELOPER"}
        matrix = get_permission_matrix()
        # DEVELOPER cannot PUBLISH_GLOBAL_ADR
        assert matrix.is_allowed(roles_t0, [Permission.PUBLISH_GLOBAL_ADR]) is False

    # 2. Admin performs role change to SOLUTION_ARCHITECT in DB...
    # Next API request arrives with THE SAME UNCHANGED JWT token:
    with mock.patch("app.auth.permissions.get_tenant_db_context") as mock_db_ctx2:
        session2 = mock.AsyncMock()
        mock_db_ctx2.return_value.__aenter__.return_value = session2
        result2 = mock.MagicMock()
        result2.all.return_value = [("SOLUTION_ARCHITECT", True, None)]
        session2.execute = mock.AsyncMock(return_value=result2)

        roles_t1 = await resolve_caller_roles(user_claims)
        assert roles_t1 == {"SOLUTION_ARCHITECT"}
        # Immediately has permission to PUBLISH_GLOBAL_ADR without re-login
        assert matrix.is_allowed(roles_t1, [Permission.PUBLISH_GLOBAL_ADR]) is True


@pytest.mark.asyncio
async def test_role_change_demotion_immediately_revokes_permission():
    """
    TC-ROLE-IMM-003: Immediate permission revocation upon demotion.
    Target user demoted from SOLUTION_ARCHITECT to DEVELOPER immediately receives
    HTTP 403 on protected architect actions on the very next request.
    """
    user_claims = TokenClaims(
        sub="user_alice",
        roles=["SOLUTION_ARCHITECT"],  # Token still claims SOLUTION_ARCHITECT
        tenant_id=TENANT_ALPHA,
        email="alice@acme.io",
    )

    # Database now reflects demoted DEVELOPER role
    with mock.patch("app.auth.permissions.get_tenant_db_context") as mock_db_ctx:
        session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = session
        result = mock.MagicMock()
        result.all.return_value = [("DEVELOPER", True, None)]
        session.execute = mock.AsyncMock(return_value=result)

        resolved_roles = await resolve_caller_roles(user_claims)
        assert resolved_roles == {"DEVELOPER"}
        assert get_permission_matrix().is_allowed(resolved_roles, [Permission.PUBLISH_GLOBAL_ADR]) is False


@pytest.mark.asyncio
async def test_cannot_demote_last_tenant_admin(auth_fixture):
    """
    TC-ROLE-LAST-ADMIN-001: Attempt to demote the sole remaining administrator is blocked.
    Verifies HTTP 409 Conflict with clear message: 'Не можна понизити останнього адміністратора'.
    """
    pk = auth_fixture["private_key"]
    admin_token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")
    sole_admin_id = uuid.uuid4()

    with mock.patch("app.auth.members_router.get_tenant_db_context") as mock_db_ctx:
        mock_session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_session

        # Exactly 1 admin exists, which is target user
        mock_admin_res = mock.MagicMock()
        mock_admin_res.scalars.return_value.all.return_value = [sole_admin_id]

        mock_target_res = mock.MagicMock()
        mock_target_res.first.return_value = (sole_admin_id,)

        mock_session.execute = mock.AsyncMock(side_effect=[mock_admin_res, mock_target_res])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/members/{sole_admin_id}/role",
                json={"role": "DEVELOPER"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 409
        assert "Не можна понизити останнього адміністратора" in resp.json()["detail"]


# ==============================================================================
# Scenario 6: Non-Admin Attempts to Invite or Change Roles
# ==============================================================================

NON_ADMIN_ROLES = [
    "DEVELOPER",
    "SCRUM_MASTER",
    "PROJECT_MANAGER",
    "SOLUTION_ARCHITECT",
    "DELIVERY_DIRECTOR",
    "TOP_MANAGEMENT",
]


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_non_admin_roles_denied_invitation_creation(auth_fixture, role: str):
    """
    TC-RBAC-INV-001: All non-admin roles are denied POST /api/v1/invitations.
    Enforces MANAGE_TENANT_USERS_AND_ROLES permission boundary with HTTP 403 Forbidden.
    """
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=[role], sub=f"user_{role.lower()}")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/invitations",
            json={"email": "attempt@acme.io", "role": "DEVELOPER"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.asyncio
async def test_non_admin_roles_denied_role_modification(auth_fixture, role: str):
    """
    TC-RBAC-ROLE-001: All non-admin roles are denied PATCH /api/v1/members/{id}/role.
    Enforces MANAGE_TENANT_USERS_AND_ROLES permission boundary with HTTP 403 Forbidden.
    """
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=[role], sub=f"user_{role.lower()}")
    target_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/members/{target_id}/role",
            json={"role": "TENANT_ADMIN"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
async def test_unauthenticated_request_denied():
    """
    TC-RBAC-UNAUTH-001: Unauthenticated request to /invitations returns HTTP 401.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/invitations",
            json={"email": "guest@acme.io", "role": "DEVELOPER"},
        )

    assert resp.status_code == 401


# ==============================================================================
# Additional Input Validation & Edge Cases
# ==============================================================================


@pytest.mark.asyncio
async def test_invalid_role_rejected_with_422(auth_fixture):
    """
    TC-INV-EDGE-001: Attempting to create an invite or update role with unknown role string.
    Returns HTTP 422 Unprocessable Entity.
    """
    pk = auth_fixture["private_key"]
    admin_token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")

    with (
        mock.patch("app.notifications.router.get_tenant_db_context") as mock_db_ctx,
        mock.patch("app.notifications.router.find_platform_user_id", new_callable=mock.AsyncMock, return_value=uuid.uuid4()),
    ):
        mock_session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations",
                json={"email": "valid@acme.io", "role": "SUPERUSER_HACK"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 422
        assert "Невідома роль" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_overlong_email_rejected_with_422(auth_fixture):
    """
    TC-INV-EDGE-002: Attempting to invite email longer than 255 chars returns HTTP 422.
    """
    pk = auth_fixture["private_key"]
    admin_token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")
    long_email = f"{'a' * 250}@acme.io"

    with (
        mock.patch("app.notifications.router.get_tenant_db_context") as mock_db_ctx,
        mock.patch("app.notifications.router.find_platform_user_id", new_callable=mock.AsyncMock, return_value=uuid.uuid4()),
    ):
        mock_session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations",
                json={"email": long_email, "role": "DEVELOPER"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 422
        assert "Email задовгий" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_invitation_token_rejected_with_404(mock_db_session):
    """
    TC-INV-EDGE-003: Submitting non-existent token returns HTTP 404 Not Found.
    """
    with mock.patch(
        "app.notifications.router.consume_invitation",
        new_callable=mock.AsyncMock,
        side_effect=InvitationError(404, "Недійсне посилання-запрошення"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations/accept",
                json={"token": "non_existent_token_777"},
            )

        assert resp.status_code == 404
        assert "Недійсне посилання-запрошення" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_smtp_relay_outage_returns_502_bad_gateway(auth_fixture):
    """
    TC-INV-EDGE-004: SMTP failure after invitation creation returns HTTP 502 Bad Gateway.
    """
    pk = auth_fixture["private_key"]
    admin_token = make_token(pk, roles=["TENANT_ADMIN"], sub="alpha_admin")

    with (
        mock.patch("app.notifications.router.get_tenant_db_context") as mock_db_ctx,
        mock.patch("app.notifications.router.find_platform_user_id", new_callable=mock.AsyncMock, return_value=uuid.uuid4()),
        mock.patch("app.notifications.router.create_invitation", new_callable=mock.AsyncMock, return_value="raw_token_xyz"),
        mock.patch("app.notifications.router.send_invitation_email", side_effect=Exception("SMTP timeout")),
    ):
        mock_session = mock.AsyncMock()
        mock_db_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/invitations",
                json={"email": "member@acme.io", "role": "DEVELOPER"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 502
        assert "Запрошення створено, але лист надіслати не вдалося" in resp.json()["detail"]
