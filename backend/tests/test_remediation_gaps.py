"""Unit tests covering all remediated backend endpoints and bug fixes (Issues #1–#16).

Uses FastAPI dependency overrides and mocks for PostgreSQL/Keycloak/SMTP so tests
execute deterministically without requiring live external services.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import time
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
from app.config import settings
from app.db.session import get_db
from app.integrations.validators import InvalidCredentialsError
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SECOND_TENANT_ID = "22222222-2222-2222-2222-222222222222"


class _MockJWKSServer:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]}, request=request)


@pytest.fixture
def auth_fixture():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "remediation-key", "kty": "RSA", "alg": "RS256", "use": "sig"})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(_MockJWKSServer(jwk).handle_request),
    )
    orig = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client
    yield private_key
    app.state.jwks_client = orig


def _make_token(
    private_key: rsa.RSAPrivateKey,
    *,
    roles: list[str] | None = None,
    sub: str = "kc-user-1",
    email: str = "admin@acme.io",
    tenant_id: str = TENANT_ID,
) -> str:
    payload: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "name": "Alice Admin",
        "preferred_username": "alice",
        "tenant_id": tenant_id,
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": int(time.time()) + 3600,
        "realm_access": {"roles": roles if roles is not None else ["TENANT_ADMIN"]},
    }
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": "remediation-key", "alg": "RS256"},
    )


@pytest.fixture(autouse=True)
def _reset_limiter():
    registration_rate_limiter.reset()
    yield
    registration_rate_limiter.reset()


@pytest.mark.asyncio
async def test_register_tenant_provisions_keycloak_user_industry_and_invites():
    tid = uuid.uuid4()
    uid = uuid.uuid4()

    db_session = mock.AsyncMock()
    exec_result = mock.MagicMock()
    exec_result.first.return_value = (tid, uid, "acme-cloud", "founder@acme.io")
    db_session.execute = mock.AsyncMock(return_value=exec_result)
    db_session.commit = mock.AsyncMock()
    db_session.rollback = mock.AsyncMock()

    async def _override_get_db():
        yield db_session

    @asynccontextmanager
    async def _tenant_ctx(_tid):
        s = mock.AsyncMock()
        yield s

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with (
            mock.patch(
                "app.tenants.router.create_user", new=mock.AsyncMock(return_value="kc-sub-99")
            ) as m_kc,
            mock.patch("app.tenants.router.delete_user", new=mock.AsyncMock()) as m_del,
            mock.patch("app.tenants.router.get_tenant_db_context", side_effect=_tenant_ctx),
            mock.patch(
                "app.tenants.router.create_invitation", new=mock.AsyncMock(return_value="tok-1")
            ) as m_inv,
            mock.patch("app.tenants.router.send_invitation_email", new=mock.AsyncMock()) as m_mail,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/tenants/register",
                    json={
                        "name": "Acme Cloud",
                        "slug": "acme-cloud",
                        "industry": "FinTech",
                        "admin_name": "Alice Founder",
                        "admin_email": "founder@acme.io",
                        "password": "StrongPassword123!",
                        "invites": ["teammate@acme.io", "founder@acme.io"],
                    },
                )

                bad_resp = await ac.post(
                    "/tenants/register",
                    json={
                        "name": "Bad Invite Org",
                        "slug": "bad-invite-org",
                        "admin_name": "Alice Founder",
                        "admin_email": "founder2@acme.io",
                        "invites": ["invalid-email"],
                    },
                )
                assert bad_resp.status_code == 422

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["industry"] == "FinTech"
        assert data["invites_sent"] == 1
        m_kc.assert_awaited_once()
        assert m_kc.call_args.kwargs["password"] == "StrongPassword123!"
        assert m_kc.call_args.kwargs["role"] == "TENANT_ADMIN"
        m_del.assert_not_awaited()
        m_inv.assert_awaited_once()
        m_mail.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_register_tenant_rolls_back_keycloak_user_on_db_conflict():
    db_session = mock.AsyncMock()
    db_session.execute = mock.AsyncMock(
        side_effect=IntegrityError("stmt", {}, Exception("duplicate key tenants_slug_key"))
    )
    db_session.rollback = mock.AsyncMock()

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with (
            mock.patch(
                "app.tenants.router.create_user", new=mock.AsyncMock(return_value="kc-orphan-1")
            ),
            mock.patch("app.tenants.router.delete_user", new=mock.AsyncMock()) as m_del,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/tenants/register",
                    json={
                        "name": "Acme Cloud",
                        "slug": "acme-cloud",
                        "admin_name": "Alice Founder",
                        "admin_email": "founder@acme.io",
                        "password": "StrongPassword123!",
                    },
                )

        assert resp.status_code == 409
        m_del.assert_awaited_once_with("kc-orphan-1")
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_auth_me_returns_live_db_roles_and_tenant_metadata(auth_fixture):
    token = _make_token(auth_fixture, roles=["DEVELOPER"])

    @asynccontextmanager
    async def _tenant_ctx(_tid):
        s = mock.AsyncMock()
        r = mock.MagicMock()
        r.first.return_value = ("Acme Cloud", "acme.io")
        s.execute = mock.AsyncMock(return_value=r)
        yield s

    with (
        mock.patch(
            "app.auth.permissions.resolve_caller_roles",
            new=mock.AsyncMock(return_value={"TENANT_ADMIN"}),
        ),
        mock.patch(
            "app.auth.router.resolve_caller_roles",
            new=mock.AsyncMock(return_value={"TENANT_ADMIN"}),
        ),
        mock.patch("app.auth.router.get_tenant_db_context", side_effect=_tenant_ctx),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["roles"] == ["TENANT_ADMIN"]
    assert body["tenant_name"] == "Acme Cloud"
    assert body["allowed_domain"] == "acme.io"


@pytest.mark.asyncio
async def test_workspaces_list_and_switch_workspace(auth_fixture):
    token = _make_token(auth_fixture, roles=["DEVELOPER"])
    t1 = uuid.UUID(TENANT_ID)
    t2 = uuid.UUID(SECOND_TENANT_ID)

    mock_session = mock.AsyncMock()
    list_res = mock.MagicMock()
    list_res.all.return_value = [
        (t1, "Workspace One", "ws-1", "TENANT_ADMIN"),
        (t2, "Workspace Two", "ws-2", "DEVELOPER"),
    ]
    switch_res = mock.MagicMock()
    switch_res.first.return_value = (t2, "Workspace Two", "ws-2", "DEVELOPER")
    mock_session.execute = mock.AsyncMock(side_effect=[list_res, switch_res])

    @asynccontextmanager
    async def _session_ctx():
        yield mock_session

    with (
        mock.patch(
            "app.auth.permissions.resolve_caller_roles",
            new=mock.AsyncMock(return_value={"DEVELOPER"}),
        ),
        mock.patch("app.auth.router.AsyncSessionLocal", side_effect=_session_ctx),
        mock.patch("app.auth.router.switch_user_tenant_context", new=mock.AsyncMock()) as m_switch,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_list = await ac.get(
                "/api/v1/auth/workspaces", headers={"Authorization": f"Bearer {token}"}
            )
            assert r_list.status_code == 200, r_list.text
            items = r_list.json()
            assert len(items) == 2
            assert items[0]["is_current"] is True
            assert items[1]["tenant_name"] == "Workspace Two"

            r_switch = await ac.post(
                "/api/v1/auth/switch-workspace",
                json={"tenant_id": SECOND_TENANT_ID},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_switch.status_code == 200, r_switch.text
            assert r_switch.json()["tenant_name"] == "Workspace Two"
            m_switch.assert_awaited_once()


@pytest.mark.asyncio
async def test_members_enrich_role_sync_and_remove_guard(auth_fixture):
    token = _make_token(auth_fixture, roles=["TENANT_ADMIN"])
    now = datetime.now(timezone.utc)
    member_id = uuid.uuid4()

    # 1. Enriched GET /api/v1/members + 409 when removing sole admin
    @asynccontextmanager
    async def _ctx_list(_tid):
        s = mock.AsyncMock()
        r = mock.MagicMock()
        r.all.return_value = [
            (member_id, "solo@acme.io", "Solo Admin", "TENANT_ADMIN", True, now, now, now),
        ]
        s.execute = mock.AsyncMock(return_value=r)
        yield s

    @asynccontextmanager
    async def _ctx_delete_sole(_tid):
        s = mock.AsyncMock()
        admin_res = mock.MagicMock()
        admin_res.scalars.return_value.all.return_value = [member_id]
        target_res = mock.MagicMock()
        target_res.first.return_value = (member_id, "solo@acme.io", "kc-1")
        s.execute = mock.AsyncMock(side_effect=[admin_res, target_res])
        yield s

    @asynccontextmanager
    async def _ctx_role_sync(_tid):
        s = mock.AsyncMock()
        s.add = mock.MagicMock()
        admin_res = mock.MagicMock()
        admin_res.scalars.return_value.all.return_value = [uuid.uuid4()]
        target_res = mock.MagicMock()
        target_res.first.return_value = (member_id, "dev@acme.io", "kc-dev-1")
        upd = mock.MagicMock()
        del_roles = mock.MagicMock()
        s.execute = mock.AsyncMock(side_effect=[admin_res, target_res, upd, del_roles])
        yield s

    @asynccontextmanager
    async def _ctx_delete_dev(_tid):
        s = mock.AsyncMock()
        admin_res = mock.MagicMock()
        admin_res.scalars.return_value.all.return_value = [uuid.uuid4()]
        target_res = mock.MagicMock()
        target_res.first.return_value = (member_id, "dev@acme.io", "kc-dev-1")
        upd1 = mock.MagicMock()
        del1 = mock.MagicMock()
        upd2 = mock.MagicMock()
        s.execute = mock.AsyncMock(side_effect=[admin_res, target_res, upd1, del1, upd2])
        yield s

    with mock.patch(
        "app.auth.permissions.resolve_caller_roles",
        new=mock.AsyncMock(return_value={"TENANT_ADMIN"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with mock.patch("app.auth.members_router.get_tenant_db_context", side_effect=_ctx_list):
                r_list = await ac.get(
                    "/api/v1/members", headers={"Authorization": f"Bearer {token}"}
                )
            assert r_list.status_code == 200
            assert r_list.json()[0]["name"] == "Solo Admin"
            assert r_list.json()[0]["status"] == "ACTIVE"
            assert r_list.json()[0]["last_active"] is not None

            with mock.patch(
                "app.auth.members_router.get_tenant_db_context", side_effect=_ctx_delete_sole
            ):
                r_sole = await ac.delete(
                    f"/api/v1/members/{member_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_sole.status_code == 409

            with (
                mock.patch(
                    "app.auth.members_router.get_tenant_db_context", side_effect=_ctx_role_sync
                ),
                mock.patch(
                    "app.auth.members_router.sync_user_realm_role", new=mock.AsyncMock()
                ) as m_sync_role,
            ):
                r_role = await ac.patch(
                    f"/api/v1/members/{member_id}/role",
                    json={"role": "PROJECT_MANAGER"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_role.status_code == 200, r_role.text
            assert r_role.json()["role"] == "PROJECT_MANAGER"
            m_sync_role.assert_awaited_once_with(
                email="dev@acme.io",
                new_role="PROJECT_MANAGER",
                google_sub="kc-dev-1",
            )

            with (
                mock.patch(
                    "app.auth.members_router.get_tenant_db_context", side_effect=_ctx_delete_dev
                ),
                mock.patch(
                    "app.auth.members_router.disable_user", new=mock.AsyncMock()
                ) as m_disable,
            ):
                r_del = await ac.delete(
                    f"/api/v1/members/{member_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_del.status_code == 204
            m_disable.assert_awaited_once_with(email="dev@acme.io", google_sub="kc-dev-1")


@pytest.mark.asyncio
async def test_invitations_list_create_update_and_delete(auth_fixture):
    token = _make_token(auth_fixture, roles=["TENANT_ADMIN"])
    now = datetime.now(timezone.utc)
    inv_future = uuid.uuid4()
    inv_past = uuid.uuid4()

    @asynccontextmanager
    async def _ctx_list(_tid):
        s = mock.AsyncMock()
        r = mock.MagicMock()
        r.all.return_value = [
            (inv_future, "pending@acme.io", "DEVELOPER", now + timedelta(hours=24), now, None),
            (
                inv_past,
                "old@acme.io",
                "PROJECT_MANAGER",
                now - timedelta(hours=2),
                now - timedelta(days=3),
                None,
            ),
        ]
        s.execute = mock.AsyncMock(return_value=r)
        yield s

    @asynccontextmanager
    async def _ctx_create(_tid):
        s = mock.AsyncMock()
        r = mock.MagicMock()
        r.first.return_value = (inv_future, now + timedelta(hours=48))
        s.execute = mock.AsyncMock(return_value=r)
        yield s

    @asynccontextmanager
    async def _ctx_patch_delete(_tid):
        s = mock.AsyncMock()
        r = mock.MagicMock()
        r.scalar_one_or_none.return_value = inv_future
        s.execute = mock.AsyncMock(return_value=r)
        yield s

    with mock.patch(
        "app.auth.permissions.resolve_caller_roles",
        new=mock.AsyncMock(return_value={"TENANT_ADMIN"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with mock.patch(
                "app.notifications.router.get_tenant_db_context", side_effect=_ctx_list
            ):
                r_list = await ac.get(
                    "/api/v1/invitations", headers={"Authorization": f"Bearer {token}"}
                )
            assert r_list.status_code == 200
            data = r_list.json()
            assert data[0]["status"] == "INVITE_SENT"
            assert data[1]["status"] == "EXPIRED"

            with (
                mock.patch(
                    "app.notifications.router.get_tenant_db_context", side_effect=_ctx_create
                ),
                mock.patch(
                    "app.notifications.router.find_platform_user_id",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch(
                    "app.notifications.router.create_invitation",
                    new=mock.AsyncMock(return_value="raw-tok-123"),
                ),
                mock.patch("app.notifications.router.send_invitation_email", new=mock.AsyncMock()),
            ):
                r_create = await ac.post(
                    "/api/v1/invitations",
                    json={"email": "new@acme.io", "role": "DEVELOPER"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_create.status_code == 201
            created = r_create.json()
            assert created["id"] == str(inv_future)
            assert created["invite_url"].endswith("/accept-invite?token=raw-tok-123")

            with mock.patch(
                "app.notifications.router.get_tenant_db_context", side_effect=_ctx_patch_delete
            ):
                r_patch = await ac.patch(
                    f"/api/v1/invitations/{inv_future}/role",
                    json={"role": "SOLUTION_ARCHITECT"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_patch.status_code == 200
            assert r_patch.json()["role"] == "SOLUTION_ARCHITECT"

            with mock.patch(
                "app.notifications.router.get_tenant_db_context", side_effect=_ctx_patch_delete
            ):
                r_del = await ac.delete(
                    f"/api/v1/invitations/{inv_future}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_del.status_code == 204


@pytest.mark.asyncio
async def test_projects_update_archive_and_incidents(auth_fixture):
    admin_token = _make_token(auth_fixture, roles=["TENANT_ADMIN"])
    dev_token = _make_token(auth_fixture, roles=["DEVELOPER"])
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    fake_proj = {
        "id": pid,
        "tenant_id": uuid.UUID(TENANT_ID),
        "name": "Updated Core",
        "slug": "updated-core",
        "description": "Updated backend description",
        "tier_sla_hours": 12,
        "authoritative_cd_signal": "github_deployments_api",
        "authoritative_deployment_signal": "github_deployments_api",
        "lead_time_first_commit_strategy": "feature_branch_root",
        "wip_limit_per_dev": 3,
        "pr_stale_threshold_hours": 24,
        "is_active": True,
        "created_by": None,
        "created_at": now,
        "updated_at": now,
        "repositories": [],
    }
    fake_inc = [
        {
            "id": uuid.uuid4(),
            "project_id": pid,
            "deployment_id": None,
            "severity": "high",
            "started_at": now,
            "resolved_at": now,
            "recovery_time_seconds": 300.0,
            "created_at": now,
        }
    ]

    with (
        mock.patch(
            "app.auth.permissions.resolve_caller_roles",
            new=mock.AsyncMock(
                side_effect=[{"TENANT_ADMIN"}, {"DEVELOPER"}, {"TENANT_ADMIN"}, {"DEVELOPER"}]
            ),
        ),
        mock.patch(
            "app.projects.router.update_project_service", new=mock.AsyncMock(return_value=fake_proj)
        ),
        mock.patch("app.projects.router.archive_project_service", new=mock.AsyncMock()),
        mock.patch(
            "app.projects.router.list_project_incidents_service",
            new=mock.AsyncMock(return_value=fake_inc),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_patch = await ac.patch(
                f"/api/v1/projects/{pid}",
                json={"name": "Updated Core", "description": "Updated backend description"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert r_patch.status_code == 200
            assert r_patch.json()["description"] == "Updated backend description"

            r_forbidden = await ac.patch(
                f"/api/v1/projects/{pid}",
                json={"name": "Blocked"},
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_forbidden.status_code == 403

            r_arch = await ac.delete(
                f"/api/v1/projects/{pid}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert r_arch.status_code == 204

            r_inc = await ac.get(
                f"/api/v1/projects/{pid}/incidents",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_inc.status_code == 200
            assert r_inc.json()[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_integration_reconnect_and_obsolete_tenants_users_removed(auth_fixture):
    token = _make_token(auth_fixture, roles=["TENANT_ADMIN"])
    iid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    fake_int = {
        "id": iid,
        "tenant_id": uuid.UUID(TENANT_ID),
        "provider": "github",
        "external_account_id": "acme-org",
        "webhook_url": "https://api.manta.local/api/v1/webhooks/github/1",
        "webhook_secret_masked": "whsec_...1234",
        "is_active": True,
        "status": "connected",
        "created_at": now,
        "updated_at": now,
    }

    with mock.patch(
        "app.auth.permissions.resolve_caller_roles",
        new=mock.AsyncMock(return_value={"TENANT_ADMIN"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with mock.patch(
                "app.integrations.router.reconnect_tenant_integration",
                new=mock.AsyncMock(return_value=fake_int),
            ):
                r_ok = await ac.post(
                    f"/api/v1/integrations/{iid}/reconnect",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_ok.status_code == 200
            assert r_ok.json()["status"] == "connected"

            with mock.patch(
                "app.integrations.router.reconnect_tenant_integration",
                new=mock.AsyncMock(side_effect=InvalidCredentialsError("Token expired")),
            ):
                r_bad = await ac.post(
                    f"/api/v1/integrations/{iid}/reconnect",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert r_bad.status_code == 400

            r_removed = await ac.get(
                "/api/v1/tenants/users",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_removed.status_code == 404


@pytest.mark.asyncio
async def test_contributors_router_endpoints(auth_fixture):
    token = _make_token(auth_fixture, roles=["TENANT_ADMIN"])
    sample = {
        "tenant_id": uuid.UUID(TENANT_ID),
        "pseudonym_id": "p" * 64,
        "provider": "github",
        "external_actor_id": "12345",
        "raw_name": "Dev One",
        "raw_email": "dev@acme.io",
        "raw_handle": "dev1",
        "is_erased": False,
        "erased_at": None,
    }

    @asynccontextmanager
    async def _ctx_contrib(_tid):
        s = mock.AsyncMock()
        r = mock.MagicMock()
        r.scalars.return_value.all.return_value = ["p" * 64]
        s.execute = mock.AsyncMock(return_value=r)
        yield s

    with (
        mock.patch(
            "app.auth.permissions.resolve_caller_roles",
            new=mock.AsyncMock(return_value={"TENANT_ADMIN"}),
        ),
        mock.patch("app.contributors.router.get_tenant_db_context", side_effect=_ctx_contrib),
        mock.patch(
            "app.contributors.router.get_decrypted_contributor",
            new=mock.AsyncMock(return_value=sample),
        ),
        mock.patch(
            "app.contributors.router.erase_contributor_pii", new=mock.AsyncMock(return_value=True)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_list = await ac.get(
                "/api/v1/contributors", headers={"Authorization": f"Bearer {token}"}
            )
            assert r_list.status_code == 200
            assert r_list.json()[0]["raw_handle"] == "dev1"

            r_get = await ac.get(
                f"/api/v1/contributors/{'p' * 64}", headers={"Authorization": f"Bearer {token}"}
            )
            assert r_get.status_code == 200

            r_erase = await ac.delete(
                f"/api/v1/contributors/{'p' * 64}/erase",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r_erase.status_code == 200
            assert r_erase.json()["erased"] is True
