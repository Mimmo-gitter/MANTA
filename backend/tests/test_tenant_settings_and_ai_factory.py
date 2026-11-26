"""SCRUM-560 (TASK-313-1): Tenant settings endpoints, dual-mode AI client factory & ARQ re-embedding tests."""

from __future__ import annotations

import json
import os
import re
import time
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import delete, pool, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai.client_factory import (
    AI_MODE_CLOUD_ZDR,
    AI_MODE_LOCAL_ONLY,
    CLOUD_ZDR_EMBEDDING_DIMENSIONS,
    LOCAL_EMBEDDING_DIMENSIONS,
    AIClientConfigurationError,
    CloudZDRAIClient,
    LocalAIClient,
    LocalOnlyPolicyViolationError,
    assert_local_only_endpoint,
    create_ai_client,
)
from app.ai.reindex_service import (
    ADR_REEMBED_JOB_TYPE,
    get_adr_reindex_redis_key,
)
from app.auth.jwks import JWKSClient
from app.config import settings
from app.db.models.models import (
    AnonymizedADR,
    AuditLog,
    Project,
    Tenant,
    User,
    UserRole,
)
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from workers.worker import QUEUE_HIGH, dispatch_worker_job


class _FakeAsyncRedis:
    """In-memory async Redis stand-in supporting lists and key/value operations."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.kv[key] = value
        return True

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


class _MockJWKSServer:
    def __init__(self, jwk: dict[str, object]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]}, request=request)


def _get_admin_url() -> str:
    admin_url = settings.MIGRATION_DATABASE_URL or os.getenv("MIGRATION_DATABASE_URL")
    if admin_url:
        return admin_url
    admin_user = os.getenv("POSTGRES_USER", "manta_user")
    admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
    return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", settings.DATABASE_URL)


async def _provision_tenant(
    session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    prefix: str,
    ai_processing_mode: str = "cloud_zdr",
) -> uuid.UUID:
    suffix = tenant_id.hex[:8]
    result = await session.execute(
        text(
            """
            SELECT user_id FROM provision_tenant(
                p_name => :name,
                p_slug => :slug,
                p_admin_email => :email,
                p_admin_name => :admin_name,
                p_google_sub => :google_sub,
                p_tenant_id => :tenant_id,
                p_user_id => :user_id,
                p_ai_processing_mode => :ai_processing_mode
            )
            """
        ),
        {
            "name": f"Settings Test {prefix} {suffix}",
            "slug": f"settings-{prefix}-{suffix}",
            "email": f"admin-{prefix}-{suffix}@manta.local",
            "admin_name": f"Settings Admin {prefix}",
            "google_sub": str(user_id),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "ai_processing_mode": ai_processing_mode,
        },
    )
    return result.scalar_one()


async def _cleanup_tenants(tenant_ids: list[uuid.UUID]) -> None:
    admin_engine = create_async_engine(_get_admin_url(), poolclass=pool.NullPool)
    try:
        async with admin_engine.begin() as conn:
            for tid in tenant_ids:
                await conn.execute(delete(AnonymizedADR).where(AnonymizedADR.tenant_id == tid))
                await conn.execute(text(f"DROP TABLE IF EXISTS anonymized_adrs_t_{tid.hex}"))
                await conn.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
                await conn.execute(delete(Project).where(Project.tenant_id == tid))
                await conn.execute(
                    text("DELETE FROM user_roles WHERE tenant_id = :tid"), {"tid": tid}
                )
                await conn.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tid})
                await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})
    finally:
        await admin_engine.dispose()


def _create_jwt(
    private_key: rsa.RSAPrivateKey,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    email: str | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
        "realm_access": {"roles": [role]},
    }
    if email:
        claims["email"] = email
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "settings-test-key", "alg": "RS256"},
    )


# ---------------------------------------------------------------------------
# Unit tests: Dual-mode AI client factory & local_only WAN egress guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_url",
    [
        "https://api.openai.com/v1",
        "https://acme-resource.openai.azure.com/openai/deployments/gpt4",
        "http://8.8.8.8:11434/v1",
        "https://example.com/v1",
        "ftp://localhost:11434/v1",
        "",
    ],
)
def test_local_only_rejects_wan_or_invalid_endpoints(forbidden_url: str) -> None:
    """local_only must fail closed BEFORE constructing any outbound HTTP client."""
    with pytest.raises(LocalOnlyPolicyViolationError):
        assert_local_only_endpoint(forbidden_url)

    with pytest.raises(LocalOnlyPolicyViolationError):
        create_ai_client(AI_MODE_LOCAL_ONLY, local_base_url=forbidden_url)


@pytest.mark.parametrize(
    "permitted_url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://ollama:11434/v1",
        "http://vllm.internal:8000/v1",
        "http://10.0.0.5:8000/v1",
        "http://172.18.0.4:11434/v1",
        "http://192.168.1.20:11434/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_local_only_allows_loopback_private_and_internal_dns(permitted_url: str) -> None:
    cleaned = assert_local_only_endpoint(permitted_url)
    assert cleaned == permitted_url.rstrip("/")


@pytest.mark.asyncio
async def test_local_and_cloud_zdr_clients_headers_dimensions_and_validation() -> None:
    recorded_local_requests: list[httpx.Request] = []
    recorded_cloud_requests: list[httpx.Request] = []

    async def local_handler(request: httpx.Request) -> httpx.Response:
        recorded_local_requests.append(request)
        if request.url.path.endswith("/embeddings"):
            body = json.loads(request.content.decode("utf-8"))
            vecs = [[0.25] * LOCAL_EMBEDDING_DIMENSIONS for _ in body["input"]]
            return httpx.Response(
                200,
                json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]},
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok":true}'}}]},
            request=request,
        )

    async def cloud_handler(request: httpx.Request) -> httpx.Response:
        recorded_cloud_requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        vecs = [[0.75] * CLOUD_ZDR_EMBEDDING_DIMENSIONS for _ in body["input"]]
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]},
            request=request,
        )

    local_client = create_ai_client(
        AI_MODE_LOCAL_ONLY,
        local_base_url="http://ollama:11434/v1",
        transport=httpx.MockTransport(local_handler),
    )
    assert isinstance(local_client, LocalAIClient)
    assert local_client.allows_outbound_wan is False
    assert local_client.embedding_dimensions == 384
    assert local_client.embedding_column == "embedding_384"

    async with local_client:
        assert await local_client.embed_texts([]) == []
        local_vecs = await local_client.embed_texts(["adr one"])
        assert len(local_vecs) == 1
        assert len(local_vecs[0]) == 384
        chat_reply = await local_client.chat_complete([{"role": "user", "content": "hi"}])
        assert chat_reply == '{"ok":true}'

    assert len(recorded_local_requests) == 2
    assert str(recorded_local_requests[0].url) == "http://ollama:11434/v1/embeddings"
    assert str(recorded_local_requests[1].url) == "http://ollama:11434/v1/chat/completions"
    assert "authorization" not in {k.lower() for k in recorded_local_requests[0].headers}

    # cloud_zdr requires both URL and API key
    with pytest.raises(AIClientConfigurationError):
        create_ai_client(AI_MODE_CLOUD_ZDR, cloud_base_url="", cloud_api_key="sk-test")
    with pytest.raises(AIClientConfigurationError):
        create_ai_client(
            AI_MODE_CLOUD_ZDR, cloud_base_url="https://api.openai.com/v1", cloud_api_key=""
        )

    cloud_client = create_ai_client(
        AI_MODE_CLOUD_ZDR,
        cloud_base_url="https://api.openai.com/v1",
        cloud_api_key="sk-zdr-secret",
        transport=httpx.MockTransport(cloud_handler),
    )
    assert isinstance(cloud_client, CloudZDRAIClient)
    assert cloud_client.allows_outbound_wan is True
    assert cloud_client.embedding_dimensions == 1536
    assert cloud_client.embedding_column == "embedding_1536"

    async with cloud_client:
        cloud_vecs = await cloud_client.embed_texts(["adr cloud"])
        assert len(cloud_vecs) == 1
        assert len(cloud_vecs[0]) == 1536

    assert recorded_cloud_requests[0].headers["authorization"] == "Bearer sk-zdr-secret"
    assert recorded_cloud_requests[0].headers["x-manta-zdr"] == "enforced"

    # Dimension mismatch must fail loudly
    async def wrong_dim_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1] * 128}]},
            request=request,
        )

    async with create_ai_client(
        AI_MODE_LOCAL_ONLY,
        local_base_url="http://localhost:11434/v1",
        transport=httpx.MockTransport(wrong_dim_handler),
    ) as bad_dim_client:
        with pytest.raises(ValueError, match="dimension mismatch"):
            await bad_dim_client.embed_texts(["bad dim"])


# ---------------------------------------------------------------------------
# Integration tests: GET / PATCH /api/v1/tenants/settings & ARQ re-embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_settings_rbac_update_audit_and_reembed_enqueue() -> None:
    tenant_id = uuid.uuid4()
    admin_user_id = uuid.uuid4()
    dev_user_id = uuid.uuid4()
    arch_user_id = uuid.uuid4()

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = "settings-test-key"
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"

    orig_jwks = app.state.jwks_client
    orig_redis = getattr(app.state, "ai_reindex_redis", None)
    fake_redis = _FakeAsyncRedis()
    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(_MockJWKSServer(jwk).handle_request),
    )
    app.state.ai_reindex_redis = fake_redis

    try:
        async with get_tenant_db_context(tenant_id) as session:
            await _provision_tenant(
                session,
                tenant_id=tenant_id,
                user_id=admin_user_id,
                prefix="rbac",
                ai_processing_mode="cloud_zdr",
            )
            dev_user = User(
                id=dev_user_id,
                tenant_id=tenant_id,
                email=f"dev-{tenant_id.hex[:6]}@manta.local",
                full_name="Dev User",
                google_sub=str(dev_user_id),
                role="DEVELOPER",
            )
            arch_user = User(
                id=arch_user_id,
                tenant_id=tenant_id,
                email=f"arch-{tenant_id.hex[:6]}@manta.local",
                full_name="Architect User",
                google_sub=str(arch_user_id),
                role="SOLUTION_ARCHITECT",
            )
            session.add_all([dev_user, arch_user])
            await session.flush()
            session.add_all(
                [
                    UserRole(tenant_id=tenant_id, user_id=dev_user_id, role_id="DEVELOPER"),
                    UserRole(
                        tenant_id=tenant_id,
                        user_id=arch_user_id,
                        role_id="SOLUTION_ARCHITECT",
                    ),
                ]
            )
            await session.flush()

        admin_token = _create_jwt(
            private_key, tenant_id=tenant_id, user_id=admin_user_id, role="TENANT_ADMIN"
        )
        dev_token = _create_jwt(
            private_key, tenant_id=tenant_id, user_id=dev_user_id, role="DEVELOPER"
        )
        arch_token = _create_jwt(
            private_key,
            tenant_id=tenant_id,
            user_id=arch_user_id,
            role="SOLUTION_ARCHITECT",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Unauthenticated -> 401
            unauth = await client.get("/api/v1/tenants/settings")
            assert unauth.status_code == 401

            # Non-admin roles -> 403 Forbidden
            for token in (dev_token, arch_token):
                r_get = await client.get(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r_get.status_code == 403
                r_patch = await client.patch(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"ai_processing_mode": "local_only"},
                )
                assert r_patch.status_code == 403

            # TENANT_ADMIN GET returns initial state (cloud_zdr, 1536d, idle)
            res_get = await client.get(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert res_get.status_code == 200, res_get.text
            body_get = res_get.json()
            assert body_get["ai_processing_mode"] == "cloud_zdr"
            assert body_get["active_embedding_dimensions"] == 1536
            assert body_get["active_embedding_model"] == settings.CLOUD_ZDR_EMBEDDING_MODEL
            assert body_get["retention_days"] == 90
            assert body_get["reindexing"] is False
            assert body_get["reindex_status"] == "idle"

            # Invalid payloads rejected with 422
            bad_mode = await client.patch(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"ai_processing_mode": "public_untrusted"},
            )
            assert bad_mode.status_code == 422
            bad_retention = await client.patch(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"retention_days": 3},
            )
            assert bad_retention.status_code == 422

            # Retention-only update must NOT enqueue re-embedding
            res_ret = await client.patch(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"retention_days": 180, "industry": "FinTech"},
            )
            assert res_ret.status_code == 200, res_ret.text
            assert res_ret.json()["retention_days"] == 180
            assert res_ret.json()["industry"] == "FinTech"
            assert res_ret.json()["reindexing"] is False
            assert QUEUE_HIGH not in fake_redis.lists

            # Fail-closed check: switching to local_only with WAN LOCAL_LLM_BASE_URL returns 400
            old_local_url = settings.LOCAL_LLM_BASE_URL
            settings.LOCAL_LLM_BASE_URL = "https://api.openai.com/v1"
            try:
                blocked_patch = await client.patch(
                    "/api/v1/tenants/settings",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"ai_processing_mode": "local_only"},
                )
                assert blocked_patch.status_code == 400
            finally:
                settings.LOCAL_LLM_BASE_URL = old_local_url

            # Valid switch cloud_zdr -> local_only commits, audits, sets reindexing=True, enqueues ARQ job
            res_switch = await client.patch(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"ai_processing_mode": "local_only"},
            )
            assert res_switch.status_code == 200, res_switch.text
            switched = res_switch.json()
            assert switched["ai_processing_mode"] == "local_only"
            assert switched["active_embedding_dimensions"] == 384
            assert switched["active_embedding_model"] == settings.LOCAL_EMBEDDING_MODEL
            assert switched["reindexing"] is True
            assert switched["reindex_status"] == "queued"
            assert switched["reindex_job_id"] is not None

            # Subsequent GET reflects queued reindexing state
            res_after = await client.get(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert res_after.status_code == 200
            assert res_after.json()["reindexing"] is True
            assert res_after.json()["reindex_status"] == "queued"

        # Verify Redis queue envelope and persisted AuditLog rows
        queued_raw = fake_redis.lists[QUEUE_HIGH]
        assert len(queued_raw) == 1
        envelope = json.loads(queued_raw[0])
        assert envelope["job_type"] == ADR_REEMBED_JOB_TYPE
        assert envelope["tenant_id"] == str(tenant_id)
        assert envelope["payload"]["target_mode"] == "local_only"
        assert envelope["payload"]["target_dimensions"] == 384

        async with get_tenant_db_context(tenant_id) as session:
            tenant_row = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
            assert tenant_row is not None
            assert tenant_row.ai_processing_mode == "local_only"
            assert tenant_row.retention_days == 180

            audits = list(
                (
                    await session.scalars(
                        select(AuditLog)
                        .where(
                            AuditLog.tenant_id == tenant_id,
                            AuditLog.action == "tenant.settings.updated",
                        )
                        .order_by(AuditLog.id.asc())
                    )
                ).all()
            )
            assert len(audits) == 2
            assert audits[1].actor_id == admin_user_id
            assert audits[1].payload_before["ai_processing_mode"] == "cloud_zdr"
            assert audits[1].payload_after["ai_processing_mode"] == "local_only"
    finally:
        app.state.jwks_client = orig_jwks
        app.state.ai_reindex_redis = orig_redis
        await async_engine.dispose()
        await _cleanup_tenants([tenant_id])


@pytest.mark.asyncio
async def test_arq_adr_reembed_worker_processes_only_approved_adrs(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    admin_user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    approved_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    quarantined_id = uuid.uuid4()
    fake_redis = _FakeAsyncRedis()

    try:
        async with get_tenant_db_context(tenant_id) as session:
            await _provision_tenant(
                session,
                tenant_id=tenant_id,
                user_id=admin_user_id,
                prefix="worker",
                ai_processing_mode="local_only",
            )
            proj = Project(
                id=project_id,
                tenant_id=tenant_id,
                created_by=admin_user_id,
                name="ADR Reembed Project",
                slug=f"adr-reembed-{tenant_id.hex[:6]}",
            )
            session.add(proj)
            await session.flush()

            session.add_all(
                [
                    AnonymizedADR(
                        id=approved_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        title="Use List Partitioning for ADRs",
                        architecture_pattern="Data Isolation",
                        decision_summary="Partition by tenant_id with pgvector HNSW indexes.",
                        status="approved",
                        sanitization_verified=True,
                    ),
                    AnonymizedADR(
                        id=pending_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        title="Unapproved Draft ADR",
                        decision_summary="Still awaiting Security Officer approval.",
                        status="pending_review",
                        sanitization_verified=False,
                    ),
                    AnonymizedADR(
                        id=quarantined_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        title="Quarantined Secret Leak ADR",
                        decision_summary="Must never receive an embedding vector.",
                        status="quarantined",
                        sanitization_verified=False,
                    ),
                ]
            )
            await session.flush()

        async def mock_local_embed_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            assert len(body["input"]) == 1
            assert "Use List Partitioning for ADRs" in body["input"][0]
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.125] * 384}]},
                request=request,
            )

        from app.ai import reindex_service

        monkeypatch.setattr("app.webhooks.enqueue._redis_client", fake_redis)
        monkeypatch.setattr(
            reindex_service,
            "create_ai_client",
            lambda mode: LocalAIClient(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(mock_local_embed_handler),
            ),
        )

        worker_result = await dispatch_worker_job(
            {
                "job_id": str(uuid.uuid4()),
                "job_type": ADR_REEMBED_JOB_TYPE,
                "tenant_id": str(tenant_id),
                "payload": {"target_mode": "local_only"},
            }
        )
        assert worker_result["status"] == "completed"
        assert worker_result["target_mode"] == "local_only"
        assert worker_result["target_dimensions"] == 384
        assert worker_result["reindexed_count"] == 1
        assert worker_result["total_count"] == 1

        raw_redis_state = fake_redis.kv[get_adr_reindex_redis_key(tenant_id)]
        parsed_state = json.loads(raw_redis_state)
        assert parsed_state["reindexing"] is False
        assert parsed_state["status"] == "completed"
        assert parsed_state["reindexed_count"] == 1
        assert parsed_state["total_count"] == 1

        async with get_tenant_db_context(tenant_id) as session:
            approved_row = await session.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == approved_id,
                )
            )
            pending_row = await session.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == pending_id,
                )
            )
            quarantined_row = await session.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == quarantined_id,
                )
            )
            assert approved_row is not None and approved_row.embedding_384 is not None
            assert len(approved_row.embedding_384) == 384
            assert approved_row.embedding_1536 is None
            assert pending_row is not None and pending_row.embedding_384 is None
            assert quarantined_row is not None and quarantined_row.embedding_384 is None

        # Also verify cloud_zdr 1536d re-embedding via dispatch_worker_job
        async def mock_cloud_embed_handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-manta-zdr"] == "enforced"
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.5] * 1536}]},
                request=request,
            )

        monkeypatch.setattr(
            reindex_service,
            "create_ai_client",
            lambda mode: CloudZDRAIClient(
                base_url="https://api.openai.com/v1",
                api_key="sk-zdr-test",
                transport=httpx.MockTransport(mock_cloud_embed_handler),
            ),
        )
        cloud_result = await dispatch_worker_job(
            {
                "job_id": str(uuid.uuid4()),
                "job_type": ADR_REEMBED_JOB_TYPE,
                "tenant_id": str(tenant_id),
                "payload": {"target_mode": "cloud_zdr"},
            }
        )
        assert cloud_result["status"] == "completed"
        assert cloud_result["target_mode"] == "cloud_zdr"
        assert cloud_result["target_dimensions"] == 1536
        assert cloud_result["reindexed_count"] == 1

        async with get_tenant_db_context(tenant_id) as session:
            approved_row = await session.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == approved_id,
                )
            )
            pending_row = await session.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == pending_id,
                )
            )
            quarantined_row = await session.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == quarantined_id,
                )
            )
            assert approved_row is not None and approved_row.embedding_1536 is not None
            assert len(approved_row.embedding_1536) == 1536
            assert approved_row.embedding_384 is not None
            assert pending_row is not None and pending_row.embedding_1536 is None
            assert quarantined_row is not None and quarantined_row.embedding_1536 is None
    finally:
        await async_engine.dispose()
        await _cleanup_tenants([tenant_id])
