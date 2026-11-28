"""
TASK-313-3 (SCRUM-562 / US-62): Pytest RBAC (HTTP 403 for DEVELOPER) & local_only Outbound Isolation.

Verifies without requiring an external PostgreSQL instance:
1. Strict RBAC enforcement on `GET /api/v1/tenants/settings` and
   `PATCH /api/v1/tenants/settings`:
   - Unauthenticated requests -> HTTP 401
   - `DEVELOPER` and all non-admin roles -> HTTP 403 Forbidden
   - `TENANT_ADMIN` -> HTTP 200 OK
2. Zero outbound WAN calls in `local_only` mode:
   - Fail-closed rejection of WAN endpoints in `create_tenant_ai_client`,
     `assert_ai_client_matches_tenant_policy`, and `PATCH /api/v1/tenants/settings`
     with 0 outbound HTTP/socket calls attempted.
   - Successful `cloud_zdr -> local_only` switch updates `active_embedding_dimensions=384`,
     sets `reindexing=True` (`queued`), and makes 0 outbound WAN calls.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import importlib
import json
import socket
from types import SimpleNamespace
from typing import Any, AsyncIterator
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest

from app.ai.client_factory import (
    AI_MODE_LOCAL_ONLY,
    CloudPolicyViolationError,
    CloudZDRAIClient,
    LocalAIClient,
    LocalOnlyPolicyViolationError,
    create_ai_client,
    enforce_ai_client_policy,
)
from app.ai.reindex_service import TenantReindexState
from app.auth.jwks import JWKSClient
from app.auth.permissions import Role
from app.config import settings
from app.main import app
from tests.test_tenant_settings_and_ai_factory import _FakeAsyncRedis, _MockJWKSServer

tenants_router_mod = importlib.import_module("app.tenants.router")


_TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_PUBLIC_JWK = json.loads(RSAAlgorithm.to_jwk(_TEST_PRIVATE_KEY.public_key()))
_TEST_PUBLIC_JWK.update({"kid": "ws-settings-rbac-kid", "use": "sig", "alg": "RS256"})


def _mint_jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "roles": [role],
        "iss": settings.OIDC_ISSUER or "http://localhost:8080/realms/manta",
        "aud": settings.OIDC_AUDIENCE or "manta-backend",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    return jwt.encode(
        payload,
        _TEST_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": "ws-settings-rbac-kid"},
    )


class _InMemoryTenantSession:
    """Minimal async session double for `GET/PATCH /api/v1/tenants/settings`."""

    def __init__(self, tenant_obj: Any) -> None:
        self.tenant = tenant_obj
        self.added: list[Any] = []
        self.committed = False

    async def scalar(self, _stmt: Any) -> Any:
        return self.tenant

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


def _setup_settings_test_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tenant_id: uuid.UUID,
    ai_processing_mode: str = "cloud_zdr",
) -> tuple[Any, _FakeAsyncRedis]:
    fake_redis = _FakeAsyncRedis()
    tenant_obj = SimpleNamespace(
        id=tenant_id,
        slug="acme-core",
        name="Acme Core",
        allowed_domain="acme.com",
        industry="FinTech",
        tier="ENTERPRISE",
        status="ACTIVE",
        max_projects=25,
        ai_processing_mode=ai_processing_mode,
        retention_days=90,
        adr_deny_list={"terms": ["secret-term"]},
        updated_at=datetime.now(timezone.utc),
    )
    fake_db = _InMemoryTenantSession(tenant_obj)

    @asynccontextmanager
    async def _tenant_ctx(_tid: uuid.UUID) -> AsyncIterator[_InMemoryTenantSession]:
        yield fake_db

    @asynccontextmanager
    async def _no_perm_db(_tid: uuid.UUID) -> AsyncIterator[Any]:
        raise OSError("DB unavailable in unit mock; fallback to JWT claims")
        yield

    monkeypatch.setattr(tenants_router_mod, "get_tenant_db_context", _tenant_ctx)
    monkeypatch.setattr("app.auth.permissions.get_tenant_db_context", _no_perm_db)
    monkeypatch.setattr(
        settings,
        "OIDC_ISSUER",
        settings.OIDC_ISSUER or "http://localhost:8080/realms/manta",
    )
    monkeypatch.setattr(
        settings,
        "OIDC_AUDIENCE",
        settings.OIDC_AUDIENCE or "manta-backend",
    )

    async def _fake_enqueue(*_args: Any, **_kwargs: Any) -> TenantReindexState:
        return TenantReindexState(
            reindexing=True,
            status="queued",
            reindexed_count=0,
            total_count=0,
            job_id="job-reembed-313",
            error=None,
        )

    monkeypatch.setattr(tenants_router_mod, "enqueue_adr_reembed_job", _fake_enqueue)

    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(_MockJWKSServer(_TEST_PUBLIC_JWK).handle_request),
    )
    app.state.ai_reindex_redis = fake_redis
    return tenant_obj, fake_redis


class TestWorkspaceSettingsRBAC:
    """Verify HTTP 403 enforcement for DEVELOPER and non-admin roles on /api/v1/tenants/settings."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "non_admin_role",
        [
            Role.DEVELOPER.value,
            Role.SCRUM_MASTER.value,
            Role.PROJECT_MANAGER.value,
            Role.SOLUTION_ARCHITECT.value,
            Role.DELIVERY_DIRECTOR.value,
            Role.TOP_MANAGEMENT.value,
        ],
    )
    async def test_non_admin_roles_including_developer_receive_403_on_get_and_patch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        non_admin_role: str,
    ) -> None:
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        orig_jwks = getattr(app.state, "jwks_client", None)
        orig_redis = getattr(app.state, "ai_reindex_redis", None)
        tenant_obj, _ = _setup_settings_test_env(monkeypatch, tenant_id=tenant_id)

        try:
            token = _mint_jwt(tenant_id, user_id, non_admin_role)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                get_resp = await client.get(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert get_resp.status_code == 403

                patch_resp = await client.patch(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"ai_processing_mode": "local_only", "retention_days": 30},
                )
                assert patch_resp.status_code == 403

            # Confirm tenant settings were never mutated
            assert tenant_obj.ai_processing_mode == "cloud_zdr"
            assert tenant_obj.retention_days == 90
        finally:
            app.state.jwks_client = orig_jwks
            app.state.ai_reindex_redis = orig_redis

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401_and_tenant_admin_returns_200(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tenant_id = uuid.uuid4()
        admin_id = uuid.uuid4()
        orig_jwks = getattr(app.state, "jwks_client", None)
        orig_redis = getattr(app.state, "ai_reindex_redis", None)
        _setup_settings_test_env(monkeypatch, tenant_id=tenant_id)
        monkeypatch.setattr(settings, "LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")

        try:
            admin_token = _mint_jwt(tenant_id, admin_id, Role.TENANT_ADMIN.value)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                unauth = await client.get("/api/v1/tenants/settings")
                assert unauth.status_code == 401

                ok_get = await client.get(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                assert ok_get.status_code == 200
                assert ok_get.json()["ai_processing_mode"] == "cloud_zdr"

                ok_patch = await client.patch(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"ai_processing_mode": "local_only"},
                )
                assert ok_patch.status_code == 200
                body = ok_patch.json()
                assert body["ai_processing_mode"] == "local_only"
                assert body["active_embedding_dimensions"] == 384
                assert body["reindexing"] is True
                assert body["reindex_status"] == "queued"
        finally:
            app.state.jwks_client = orig_jwks
            app.state.ai_reindex_redis = orig_redis


class TestLocalOnlyZeroWanOutboundIsolation:
    """Verify 0 outbound WAN network calls are attempted when ai_processing_mode='local_only'."""

    @pytest.mark.asyncio
    async def test_local_only_blocks_wan_clients_and_settings_switch_with_zero_wan_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wan_attempts: list[str] = []

        async def _forbid_wan_http(
            _self: httpx.AsyncHTTPTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            wan_attempts.append(str(request.url))
            raise AssertionError(f"Outbound HTTP call attempted: {request.url}")

        def _forbid_socket(*args: Any, **kwargs: Any) -> Any:
            wan_attempts.append(f"socket:{args!r}")
            raise AssertionError(f"Outbound socket connection attempted: {args!r}")

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _forbid_wan_http)
        monkeypatch.setattr(socket, "create_connection", _forbid_socket)

        # 1. Factory refuses WAN URLs before any HTTP or socket call
        for wan_url in (
            "https://api.openai.com/v1",
            "https://1.1.1.1/v1",
            "https://acme-resource.openai.azure.com/openai",
        ):
            with pytest.raises(LocalOnlyPolicyViolationError):
                create_ai_client(AI_MODE_LOCAL_ONLY, local_base_url=wan_url)

        # 2. Policy guard refuses CloudZDRAIClient for local_only tenant
        cloud_client = CloudZDRAIClient(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            llm_model="gpt-4o-mini",
            embedding_model="text-embedding-3-small",
        )
        with pytest.raises(CloudPolicyViolationError):
            enforce_ai_client_policy(AI_MODE_LOCAL_ONLY, ai_client=cloud_client)

        # 3. PATCH /api/v1/tenants/settings rejects cloud_zdr -> local_only when LOCAL_LLM_BASE_URL is WAN
        tenant_id = uuid.uuid4()
        admin_id = uuid.uuid4()
        orig_jwks = getattr(app.state, "jwks_client", None)
        orig_redis = getattr(app.state, "ai_reindex_redis", None)
        tenant_obj, _ = _setup_settings_test_env(monkeypatch, tenant_id=tenant_id)
        monkeypatch.setattr(settings, "LOCAL_LLM_BASE_URL", "https://api.openai.com/v1")

        try:
            admin_token = _mint_jwt(tenant_id, admin_id, Role.TENANT_ADMIN.value)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                bad_switch = await client.patch(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"ai_processing_mode": "local_only"},
                )
                assert bad_switch.status_code == 400
                assert tenant_obj.ai_processing_mode == "cloud_zdr"
        finally:
            app.state.jwks_client = orig_jwks
            app.state.ai_reindex_redis = orig_redis

        assert wan_attempts == []

    @pytest.mark.asyncio
    async def test_local_only_client_only_hits_loopback_and_blocks_wan_requests(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        observed_urls: list[str] = []

        def _local_handler(request: httpx.Request) -> httpx.Response:
            observed_urls.append(str(request.url))
            if str(request.url).endswith("/embeddings"):
                return httpx.Response(200, json={"data": [{"embedding": [0.1] * 384}]})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"summary":"local"}'}}]},
            )

        client = LocalAIClient(
            base_url="http://127.0.0.1:11434/v1",
            llm_model="llama3.2:3b",
            embedding_model="bge-small-en-v1.5",
            transport=httpx.MockTransport(_local_handler),
        )
        enforce_ai_client_policy(AI_MODE_LOCAL_ONLY, ai_client=client)

        vecs = await client.embed_texts(["local-only sovereignty check"])
        assert len(vecs) == 1
        assert len(vecs[0]) == 384
        chat = await client.chat_complete(
            messages=[
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "Summarize."},
            ]
        )
        assert "local" in chat
        assert all(url.startswith("http://127.0.0.1:11434/v1/") for url in observed_urls)
        await client.aclose()
