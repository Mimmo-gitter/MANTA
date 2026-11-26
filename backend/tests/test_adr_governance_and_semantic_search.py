"""SCRUM-576 & SCRUM-579: ADR Governance approval gate & partitioned pgvector semantic search tests."""

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
    CloudZDRAIClient,
    LocalAIClient,
)
from app.ai.reindex_service import (
    ADR_REEMBED_JOB_TYPE,
    TenantReindexState,
    set_tenant_reindex_status,
)
from app.auth.jwks import JWKSClient
from app.config import settings
from app.db.models.models import (
    ADRSanitizationLog,
    ADRSource,
    AnonymizedADR,
    AuditLog,
    Project,
    Repository,
    Tenant,
    User,
    UserRole,
)
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from workers.worker import QUEUE_HIGH, dispatch_worker_job


class _FakeAsyncRedis:
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
            "name": f"ADR Gov Test {prefix} {suffix}",
            "slug": f"adr-gov-{prefix}-{suffix}",
            "email": f"admin-adr-{prefix}-{suffix}@manta.local",
            "admin_name": f"ADR Admin {prefix}",
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
                await conn.execute(
                    delete(ADRSanitizationLog).where(ADRSanitizationLog.tenant_id == tid)
                )
                await conn.execute(delete(ADRSource).where(ADRSource.tenant_id == tid))
                await conn.execute(delete(AnonymizedADR).where(AnonymizedADR.tenant_id == tid))
                await conn.execute(text(f"DROP TABLE IF EXISTS anonymized_adrs_t_{tid.hex}"))
                await conn.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
                await conn.execute(delete(Repository).where(Repository.tenant_id == tid))
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
) -> str:
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
        "realm_access": {"roles": [role]},
    }
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "adr-gov-test-key", "alg": "RS256"},
    )


def _setup_mock_jwks() -> tuple[rsa.RSAPrivateKey, JWKSClient]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "adr-gov-test-key", "kty": "RSA", "alg": "RS256", "use": "sig"})
    return (
        private_key,
        JWKSClient(
            jwks_url="http://mock-idp/certs",
            ttl_seconds=300.0,
            transport=httpx.MockTransport(_MockJWKSServer(jwk).handle_request),
        ),
    )


def _unit_axis_vector(dims: int, axis: int) -> list[float]:
    vec = [0.0] * dims
    vec[axis] = 1.0
    return vec


@pytest.mark.asyncio
async def test_adr_governance_rbac_queue_diff_approve_and_reject(monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    admin_a = uuid.uuid4()
    arch_a = uuid.uuid4()
    dev_a = uuid.uuid4()
    admin_b = uuid.uuid4()

    project_a = uuid.uuid4()
    repo_a = uuid.uuid4()
    adr_pending = uuid.uuid4()
    adr_to_reject = uuid.uuid4()
    adr_quarantined = uuid.uuid4()
    project_b = uuid.uuid4()
    adr_b = uuid.uuid4()

    private_key, mock_jwks = _setup_mock_jwks()
    orig_jwks = app.state.jwks_client
    orig_adr_redis = getattr(app.state, "adr_redis", None)
    fake_redis = _FakeAsyncRedis()
    app.state.jwks_client = mock_jwks
    app.state.adr_redis = fake_redis

    try:
        async with get_tenant_db_context(tenant_a) as db:
            await _provision_tenant(
                db, tenant_id=tenant_a, user_id=admin_a, prefix="a", ai_processing_mode="cloud_zdr"
            )
            db.add_all(
                [
                    User(
                        id=arch_a,
                        tenant_id=tenant_a,
                        email=f"arch-{tenant_a.hex[:6]}@manta.local",
                        full_name="Solution Architect A",
                        google_sub=str(arch_a),
                        role="SOLUTION_ARCHITECT",
                    ),
                    User(
                        id=dev_a,
                        tenant_id=tenant_a,
                        email=f"dev-{tenant_a.hex[:6]}@manta.local",
                        full_name="Developer A",
                        google_sub=str(dev_a),
                        role="DEVELOPER",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    UserRole(tenant_id=tenant_a, user_id=arch_a, role_id="SOLUTION_ARCHITECT"),
                    UserRole(tenant_id=tenant_a, user_id=dev_a, role_id="DEVELOPER"),
                ]
            )
            db.add(
                Project(
                    id=project_a,
                    tenant_id=tenant_a,
                    created_by=admin_a,
                    name="Core Payments",
                    slug=f"core-pay-{tenant_a.hex[:6]}",
                )
            )
            await db.flush()
            db.add(
                Repository(
                    id=repo_a,
                    tenant_id=tenant_a,
                    project_id=project_a,
                    external_repo_id=int(tenant_a.int % 1_000_000_000),
                    full_name="acme-core/backend-services",
                )
            )
            await db.flush()
            db.add_all(
                [
                    AnonymizedADR(
                        id=adr_pending,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        source_repository_id=repo_a,
                        source_commit_sha="a" * 40,
                        title="Database Connection Pooling Strategy",
                        architecture_pattern="Connection Pooling",
                        decision_summary="Route service traffic via [INTERNAL_GATEWAY] for [TENANT_CORE].",
                        status="pending_review",
                        sanitization_verified=False,
                    ),
                    AnonymizedADR(
                        id=adr_to_reject,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        source_repository_id=repo_a,
                        source_commit_sha="b" * 40,
                        title="Legacy Token Storage ADR",
                        decision_summary="Still mentions internal bucket name.",
                        status="pending_review",
                        sanitization_verified=False,
                    ),
                    AnonymizedADR(
                        id=adr_quarantined,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        source_repository_id=repo_a,
                        title="Quarantined Secret Exposure",
                        decision_summary="Halted by Presidio NER check.",
                        status="quarantined",
                        sanitization_verified=False,
                    ),
                ]
            )
            await db.flush()
            db.add(
                ADRSource(
                    tenant_id=tenant_a,
                    project_id=project_a,
                    repository_id=repo_a,
                    adr_id=adr_pending,
                    file_path="docs/adr/089-pgbouncer.md",
                    commit_sha="a" * 40,
                    raw_markdown=(
                        "# Database Connection Pooling Strategy\n"
                        "Connect pgBouncer at 10.240.12.4:6432 for AlphaBank.\n"
                        "Contact alex.m@alphabank.internal"
                    ),
                    content_hash="f" * 64,
                )
            )
            db.add(
                ADRSanitizationLog(
                    tenant_id=tenant_a,
                    adr_id=adr_pending,
                    raw_input_snippet="10.240.12.4 AlphaBank",
                    presidio_entities_scrubbed={
                        "IP_ADDRESS": 1,
                        "ORGANIZATION": 1,
                        "EMAIL_ADDRESS": 1,
                        "PERSON": 1,
                    },
                    llm_abstraction_prompt_tokens=420,
                    solution_architect_diff_payload={"redactions": [{"entity": "IP_ADDRESS"}]},
                )
            )
            await db.flush()

        async with get_tenant_db_context(tenant_b) as db:
            await _provision_tenant(db, tenant_id=tenant_b, user_id=admin_b, prefix="b")
            db.add(
                Project(
                    id=project_b,
                    tenant_id=tenant_b,
                    created_by=admin_b,
                    name="Tenant B Project",
                    slug=f"tb-proj-{tenant_b.hex[:6]}",
                )
            )
            await db.flush()
            db.add(
                AnonymizedADR(
                    id=adr_b,
                    tenant_id=tenant_b,
                    project_id=project_b,
                    title="Tenant B Secret ADR",
                    status="pending_review",
                    sanitization_verified=False,
                )
            )
            await db.flush()

        arch_token = _create_jwt(
            private_key, tenant_id=tenant_a, user_id=arch_a, role="SOLUTION_ARCHITECT"
        )
        dev_token = _create_jwt(private_key, tenant_id=tenant_a, user_id=dev_a, role="DEVELOPER")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Unauthenticated -> 401
            assert (await client.get("/api/v1/adrs")).status_code == 401
            assert (await client.get(f"/api/v1/adrs/{adr_pending}/diff")).status_code == 401

            # DEVELOPER -> 403 Forbidden across all governance endpoints
            for method, path in (
                ("GET", "/api/v1/adrs"),
                ("GET", f"/api/v1/adrs/{adr_pending}/diff"),
                ("POST", f"/api/v1/adrs/{adr_pending}/approve"),
                ("POST", f"/api/v1/adrs/{adr_pending}/reject"),
            ):
                r = await client.request(
                    method, path, headers={"Authorization": f"Bearer {dev_token}"}
                )
                assert r.status_code == 403, f"Expected 403 for DEVELOPER on {method} {path}"

            # SOLUTION_ARCHITECT GET /api/v1/adrs -> queue + counts + redaction summary
            r_list = await client.get(
                "/api/v1/adrs?status=pending_review",
                headers={"Authorization": f"Bearer {arch_token}"},
            )
            assert r_list.status_code == 200, r_list.text
            body_list = r_list.json()
            assert body_list["total"] == 2
            assert body_list["counts"]["pending_review"] == 2
            assert body_list["counts"]["quarantined"] == 1
            returned_ids = {item["id"] for item in body_list["items"]}
            assert returned_ids == {str(adr_pending), str(adr_to_reject)}
            assert str(adr_b) not in returned_ids
            item_pending = next(i for i in body_list["items"] if i["id"] == str(adr_pending))
            assert item_pending["repository_name"] == "acme-core/backend-services"
            assert item_pending["file_path"] == "docs/adr/089-pgbouncer.md"
            assert item_pending["redaction_count"] == 4
            assert item_pending["is_indexed"] is False

            # Cross-tenant ADR diff attempt -> 404
            r_cross = await client.get(
                f"/api/v1/adrs/{adr_b}/diff",
                headers={"Authorization": f"Bearer {arch_token}"},
            )
            assert r_cross.status_code == 404

            # GET /api/v1/adrs/{id}/diff joins raw source + sanitized + telemetry + diff_lines
            r_diff = await client.get(
                f"/api/v1/adrs/{adr_pending}/diff",
                headers={"Authorization": f"Bearer {arch_token}"},
            )
            assert r_diff.status_code == 200, r_diff.text
            diff_data = r_diff.json()
            assert "10.240.12.4:6432" in diff_data["raw_markdown"]
            assert "[INTERNAL_GATEWAY]" in diff_data["sanitized_text"]
            assert diff_data["presidio_entities_scrubbed"]["IP_ADDRESS"] == 1
            assert len(diff_data["sanitization_logs"]) == 1
            assert any(line["line_type"] == "removed" for line in diff_data["diff_lines"])
            assert any(line["line_type"] == "added" for line in diff_data["diff_lines"])

            # Blank rationale rejected with 422; approving quarantined ADR rejected with 409
            r_blank = await client.post(
                f"/api/v1/adrs/{adr_pending}/approve",
                headers={"Authorization": f"Bearer {arch_token}"},
                json={"rationale": "   "},
            )
            assert r_blank.status_code == 422
            r_quar = await client.post(
                f"/api/v1/adrs/{adr_quarantined}/approve",
                headers={"Authorization": f"Bearer {arch_token}"},
                json={"rationale": "Override quarantine attempt"},
            )
            assert r_quar.status_code == 409

            # POST /approve -> sets approved + sanitization_verified + enqueues single-ADR job
            r_app = await client.post(
                f"/api/v1/adrs/{adr_pending}/approve",
                headers={"Authorization": f"Bearer {arch_token}"},
                json={"rationale": "Verified Presidio scrubbed all tenant PII."},
            )
            assert r_app.status_code == 200, r_app.text
            app_body = r_app.json()
            assert app_body["status"] == "approved"
            assert app_body["sanitization_verified"] is True
            assert app_body["approved_by"] == str(arch_a)
            assert app_body["embedding_queued"] is True
            assert app_body["target_embedding_dimensions"] == 1536

            # Execute queued single-ADR embedding job via worker dispatcher
            assert len(fake_redis.lists[QUEUE_HIGH]) == 1
            envelope = json.loads(fake_redis.lists[QUEUE_HIGH][0])
            assert envelope["job_type"] == ADR_REEMBED_JOB_TYPE
            assert envelope["payload"]["adr_id"] == str(adr_pending)

            from app.ai import reindex_service

            async def mock_cloud_embed(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={"data": [{"index": 0, "embedding": _unit_axis_vector(1536, 0)}]},
                    request=request,
                )

            monkeypatch.setattr("app.webhooks.enqueue._redis_client", fake_redis)
            monkeypatch.setattr(
                reindex_service,
                "create_ai_client",
                lambda mode: CloudZDRAIClient(
                    base_url="https://api.openai.com/v1",
                    api_key="sk-test",
                    transport=httpx.MockTransport(mock_cloud_embed),
                ),
            )
            worker_res = await dispatch_worker_job(envelope)
            assert worker_res["reindexed_count"] == 1
            assert worker_res["adr_id"] == str(adr_pending)

            # POST /reject -> quarantined, no embedding, no new queue job
            r_rej = await client.post(
                f"/api/v1/adrs/{adr_to_reject}/reject",
                headers={"Authorization": f"Bearer {arch_token}"},
                json={"rationale": "Internal bucket identifier remained unmasked."},
            )
            assert r_rej.status_code == 200, r_rej.text
            rej_body = r_rej.json()
            assert rej_body["status"] == "quarantined"
            assert rej_body["sanitization_verified"] is False
            assert rej_body["embedding_queued"] is False
            assert len(fake_redis.lists[QUEUE_HIGH]) == 1

        async with get_tenant_db_context(tenant_a) as db:
            approved_row = await db.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_a,
                    AnonymizedADR.id == adr_pending,
                )
            )
            rejected_row = await db.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_a,
                    AnonymizedADR.id == adr_to_reject,
                )
            )
            assert approved_row is not None and approved_row.embedding_1536 is not None
            assert len(approved_row.embedding_1536) == 1536
            assert rejected_row is not None
            assert rejected_row.status == "quarantined"
            assert rejected_row.embedding_1536 is None
            assert rejected_row.embedding_384 is None

            audits = list(
                (
                    await db.scalars(
                        select(AuditLog)
                        .where(
                            AuditLog.tenant_id == tenant_a,
                            AuditLog.action.in_(["adr.approved", "adr.rejected"]),
                        )
                        .order_by(AuditLog.id.asc())
                    )
                ).all()
            )
            assert [a.action for a in audits] == ["adr.approved", "adr.rejected"]
    finally:
        app.state.jwks_client = orig_jwks
        app.state.adr_redis = orig_adr_redis
        await async_engine.dispose()
        await _cleanup_tenants([tenant_a, tenant_b])


@pytest.mark.asyncio
async def test_semantic_search_pgvector_partitioning_dual_modes_and_rebuilding_flag() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    admin_a = uuid.uuid4()
    dev_a = uuid.uuid4()
    admin_b = uuid.uuid4()

    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    adr_outbox = uuid.uuid4()
    adr_rls = uuid.uuid4()
    adr_pending = uuid.uuid4()
    adr_unverified = uuid.uuid4()
    adr_quarantined = uuid.uuid4()
    adr_other_tenant = uuid.uuid4()

    private_key, mock_jwks = _setup_mock_jwks()
    orig_jwks = app.state.jwks_client
    orig_search_ai = getattr(app.state, "search_ai_client", None)
    orig_adr_redis = getattr(app.state, "adr_redis", None)
    fake_redis = _FakeAsyncRedis()
    app.state.jwks_client = mock_jwks
    app.state.adr_redis = fake_redis

    def mock_search_ai_factory(mode: str):
        if mode == AI_MODE_LOCAL_ONLY:

            async def handle_local(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={"data": [{"index": 0, "embedding": _unit_axis_vector(384, 0)}]},
                    request=request,
                )

            return LocalAIClient(
                base_url="http://localhost:11434/v1",
                transport=httpx.MockTransport(handle_local),
            )

        async def handle_cloud(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": _unit_axis_vector(1536, 0)}]},
                request=request,
            )

        return CloudZDRAIClient(
            base_url="https://api.openai.com/v1",
            api_key="sk-search-test",
            transport=httpx.MockTransport(handle_cloud),
        )

    app.state.search_ai_client = mock_search_ai_factory

    try:
        async with get_tenant_db_context(tenant_a) as db:
            await _provision_tenant(
                db, tenant_id=tenant_a, user_id=admin_a, prefix="sa", ai_processing_mode="cloud_zdr"
            )
            db.add(
                User(
                    id=dev_a,
                    tenant_id=tenant_a,
                    email=f"dev-search-{tenant_a.hex[:6]}@manta.local",
                    full_name="Search Developer A",
                    google_sub=str(dev_a),
                    role="DEVELOPER",
                )
            )
            await db.flush()
            db.add(UserRole(tenant_id=tenant_a, user_id=dev_a, role_id="DEVELOPER"))
            db.add(
                Project(
                    id=project_a,
                    tenant_id=tenant_a,
                    created_by=admin_a,
                    name="Search Target",
                    slug=f"search-target-{tenant_a.hex[:6]}",
                )
            )
            await db.flush()

            # Close match (axis 0 -> similarity 1.0) vs orthogonal match (axis 1 -> similarity 0.0)
            vec_match_1536 = _unit_axis_vector(1536, 0)
            vec_other_1536 = _unit_axis_vector(1536, 1)
            vec_match_384 = _unit_axis_vector(384, 0)
            vec_other_384 = _unit_axis_vector(384, 1)

            db.add_all(
                [
                    AnonymizedADR(
                        id=adr_outbox,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        title="Guaranteed Event Delivery via Transactional Outbox",
                        architecture_pattern="Messaging",
                        decision_summary="Use Valkey ARQ stream and outbox table for at-least-once delivery.",
                        status="approved",
                        sanitization_verified=True,
                        embedding_1536=vec_match_1536,
                        embedding_384=vec_match_384,
                    ),
                    AnonymizedADR(
                        id=adr_rls,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        title="PostgreSQL Row-Level Security Isolation",
                        architecture_pattern="Security",
                        decision_summary="Enforce FORCE RLS across every tenant partition.",
                        status="approved",
                        sanitization_verified=True,
                        embedding_1536=vec_other_1536,
                        embedding_384=vec_other_384,
                    ),
                    AnonymizedADR(
                        id=adr_pending,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        title="Unapproved Pending ADR Should Never Leak",
                        architecture_pattern="Hidden",
                        status="pending_review",
                        sanitization_verified=False,
                        embedding_1536=vec_match_1536,
                        embedding_384=vec_match_384,
                    ),
                    AnonymizedADR(
                        id=adr_unverified,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        title="Approved Without Verification Flag Should Never Leak",
                        status="approved",
                        sanitization_verified=False,
                        embedding_1536=vec_match_1536,
                        embedding_384=vec_match_384,
                    ),
                    AnonymizedADR(
                        id=adr_quarantined,
                        tenant_id=tenant_a,
                        project_id=project_a,
                        title="Quarantined ADR Should Never Leak",
                        status="quarantined",
                        sanitization_verified=False,
                        embedding_1536=vec_match_1536,
                        embedding_384=vec_match_384,
                    ),
                ]
            )
            await db.flush()
            db.add(
                ADRSanitizationLog(
                    tenant_id=tenant_a,
                    adr_id=adr_outbox,
                    presidio_entities_scrubbed={"IP_ADDRESS": 2, "EMAIL_ADDRESS": 1},
                )
            )
            await db.flush()

        async with get_tenant_db_context(tenant_b) as db:
            await _provision_tenant(db, tenant_id=tenant_b, user_id=admin_b, prefix="sb")
            db.add(
                Project(
                    id=project_b,
                    tenant_id=tenant_b,
                    created_by=admin_b,
                    name="Tenant B Search",
                    slug=f"sb-search-{tenant_b.hex[:6]}",
                )
            )
            await db.flush()
            db.add(
                AnonymizedADR(
                    id=adr_other_tenant,
                    tenant_id=tenant_b,
                    project_id=project_b,
                    title="Cross-Tenant Approved ADR Must Stay Isolated",
                    status="approved",
                    sanitization_verified=True,
                    embedding_1536=_unit_axis_vector(1536, 0),
                    embedding_384=_unit_axis_vector(384, 0),
                )
            )
            await db.flush()

        dev_token = _create_jwt(private_key, tenant_id=tenant_a, user_id=dev_a, role="DEVELOPER")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Cloud ZDR semantic search (1536d), ranked highest first, approved & verified only
            r_cloud = await client.get(
                "/api/v1/search/adrs?q=guaranteed+event+delivery",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_cloud.status_code == 200, r_cloud.text
            cloud_body = r_cloud.json()
            assert cloud_body["ai_processing_mode"] == AI_MODE_CLOUD_ZDR
            assert cloud_body["active_embedding_dimensions"] == 1536
            assert cloud_body["rebuilding"] is False
            assert cloud_body["available_patterns"] == ["Messaging", "Security"]
            result_ids = [item["id"] for item in cloud_body["items"]]
            assert result_ids == [str(adr_outbox), str(adr_rls)]
            assert cloud_body["items"][0]["similarity"] == pytest.approx(1.0, abs=1e-4)
            assert cloud_body["items"][0]["redaction_count"] == 3
            assert str(adr_pending) not in result_ids
            assert str(adr_unverified) not in result_ids
            assert str(adr_quarantined) not in result_ids
            assert str(adr_other_tenant) not in result_ids

            # 2. Similarity threshold & architecture_pattern filtering
            r_thresh = await client.get(
                "/api/v1/search/adrs?q=guaranteed+event+delivery&min_similarity=0.85",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_thresh.status_code == 200
            assert [i["id"] for i in r_thresh.json()["items"]] == [str(adr_outbox)]

            r_pattern = await client.get(
                "/api/v1/search/adrs?architecture_pattern=Security",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_pattern.status_code == 200
            assert [i["id"] for i in r_pattern.json()["items"]] == [str(adr_rls)]

            # 3. Active workspace re-indexing exposes rebuilding=True
            await set_tenant_reindex_status(
                tenant_a,
                TenantReindexState(
                    reindexing=True,
                    status="running",
                    target_mode=AI_MODE_LOCAL_ONLY,
                    target_dimensions=384,
                    job_id="reindex-active-1",
                    reindexed_count=1,
                    total_count=2,
                ),
                redis_client=fake_redis,
            )
            r_rebuilding = await client.get(
                "/api/v1/search/adrs?q=guaranteed+event+delivery",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_rebuilding.status_code == 200
            rb_body = r_rebuilding.json()
            assert rb_body["rebuilding"] is True
            assert rb_body["reindex_status"] == "running"
            assert rb_body["reindexed_count"] == 1
            assert rb_body["reindex_total_count"] == 2

            # 4. Switch tenant to local_only -> dynamically searches embedding_384
            async with get_tenant_db_context(tenant_a) as db:
                t_row = await db.scalar(select(Tenant).where(Tenant.id == tenant_a))
                assert t_row is not None
                t_row.ai_processing_mode = AI_MODE_LOCAL_ONLY
                await db.flush()

            await set_tenant_reindex_status(
                tenant_a,
                TenantReindexState(
                    reindexing=False,
                    status="completed",
                    target_mode=AI_MODE_LOCAL_ONLY,
                    target_dimensions=384,
                    reindexed_count=2,
                    total_count=2,
                ),
                redis_client=fake_redis,
            )

            r_local = await client.get(
                "/api/v1/search/adrs?q=guaranteed+event+delivery",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert r_local.status_code == 200, r_local.text
            local_body = r_local.json()
            assert local_body["ai_processing_mode"] == AI_MODE_LOCAL_ONLY
            assert local_body["active_embedding_dimensions"] == 384
            assert local_body["rebuilding"] is False
            assert [item["id"] for item in local_body["items"]] == [str(adr_outbox), str(adr_rls)]
            assert local_body["items"][0]["similarity"] == pytest.approx(1.0, abs=1e-4)
    finally:
        app.state.jwks_client = orig_jwks
        app.state.search_ai_client = orig_search_ai
        app.state.adr_redis = orig_adr_redis
        await async_engine.dispose()
        await _cleanup_tenants([tenant_a, tenant_b])
