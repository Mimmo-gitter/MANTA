"""SCRUM-515 (TASK-315-0 / TASK-315-1) & SCRUM-516 (TASK-316-1): ADR Presidio scrubbing, quarantine & generalization tests."""

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
from pydantic import ValidationError
from sqlalchemy import delete, pool, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.adr.generalizer import (
    ADR_GENERALIZE_JOB_TYPE,
    ADRGeneralizationSchemaError,
    enqueue_adr_generalize_job,
    process_adr_generalize_job,
)
from app.adr.scrubber import (
    PLACEHOLDER_BY_CLASS,
    REDACTION_CLASSES,
    ADRScannerFailureError,
    ADRScrubber,
    compute_shannon_entropy,
    scrub_adr_source,
)
from app.adr.service import extract_redaction_summary
from app.ai.client_factory import (
    AI_MODE_CLOUD_ZDR,
    AI_MODE_LOCAL_ONLY,
    CloudPolicyViolationError,
    CloudZDRAIClient,
    LocalAIClient,
    LocalOnlyPolicyViolationError,
    create_ai_client,
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
    User,
    UserRole,
)
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.tenants.schemas import TenantADRDenyListConfig, TenantADRDenyListEntry
from workers.worker import QUEUE_DEFAULT, dispatch_worker_job


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
    name: str | None = None,
    allowed_domain: str | None = None,
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
                p_allowed_domain => :allowed_domain,
                p_google_sub => :google_sub,
                p_tenant_id => :tenant_id,
                p_user_id => :user_id,
                p_ai_processing_mode => :ai_processing_mode
            )
            """
        ),
        {
            "name": name or f"ADR Scrub Test {prefix} {suffix}",
            "slug": f"adr-scrub-{prefix}-{suffix}",
            "email": f"admin-scrub-{prefix}-{suffix}@manta.local",
            "admin_name": f"Admin {prefix}",
            "allowed_domain": allowed_domain,
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
        headers={"kid": "adr-scrub-test-key", "alg": "RS256"},
    )


def _setup_mock_jwks() -> tuple[rsa.RSAPrivateKey, JWKSClient]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "adr-scrub-test-key", "kty": "RSA", "alg": "RS256", "use": "sig"})
    return (
        private_key,
        JWKSClient(
            jwks_url="http://mock-idp/certs",
            ttl_seconds=300.0,
            transport=httpx.MockTransport(_MockJWKSServer(jwk).handle_request),
        ),
    )


# ---------------------------------------------------------------------------
# Unit tests: Presidio + Shannon Entropy + Tenant Deny-List (TASK-315-0 / TASK-315-1)
# ---------------------------------------------------------------------------


def test_shannon_entropy_and_deny_list_schema_validation() -> None:
    assert compute_shannon_entropy("") == 0.0
    assert compute_shannon_entropy("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == 0.0
    assert (
        compute_shannon_entropy("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08")
        > 3.3
    )

    # Valid deny-list config normalizes and deduplicates terms
    cfg = TenantADRDenyListConfig(
        terms=["  Project Apollo ", "project apollo", "AlphaBank"],
        entries=[
            TenantADRDenyListEntry(
                pattern=r"\bPROJ-[0-9]{4}\b",
                match_mode="regex",
                redaction_class="COMPANY_NAME",
            )
        ],
    )
    assert cfg.terms == ["Project Apollo", "AlphaBank"]

    # Rejects invalid regex, empty-matching regex, and ReDoS nested quantifiers
    with pytest.raises(ValidationError):
        TenantADRDenyListEntry(pattern="[unclosed", match_mode="regex")
    with pytest.raises(ValidationError):
        TenantADRDenyListEntry(pattern="a*", match_mode="regex")
    with pytest.raises(ValidationError):
        TenantADRDenyListEntry(pattern="(a+)+$", match_mode="regex")


def test_scrubber_redacts_all_four_classes_and_preserves_architectural_prose() -> None:
    scrubber = ADRScrubber()
    raw_markdown = (
        "# ADR-042: Transactional Outbox with PostgreSQL 16.2 and Redis v7.2.1\n\n"
        "## Context\n"
        "Acme Financial Corp. runs primary PgBouncer at 10.240.12.4:6432 and IPv6 2001:db8::42.\n"
        "On-call lead is alex.morgan@acmefinancial.internal for Project Phoenix.\n"
        "Legacy sync used sk-live-98a7bc65def4321098a7bc65def43210 and "
        "postgres://svc_outbox:Sup3rS3cretP@ssw0rd!@10.240.12.5:5432/manta_db.\n"
        "High-entropy credential: token=K9mP2xQ7vL4nR8wZ3cF6hJ1bN5tY0dG4sA.\n"
        "Source commit sha: 0123456789abcdef0123456789abcdef01234567 remains traceable.\n\n"
        "## Decision\n"
        "Adopt the Transactional Outbox Pattern using PostgreSQL 16.2 and Redis Streams.\n"
    )
    deny_list = TenantADRDenyListConfig(
        terms=["Project Phoenix"],
        entries=[
            TenantADRDenyListEntry(
                pattern="Acme Financial Corp.",
                match_mode="literal",
                redaction_class="COMPANY_NAME",
            )
        ],
    )

    result = scrubber.scrub_text(
        raw_markdown,
        tenant_deny_list=deny_list,
        tenant_name="Acme Financial Corp.",
        allowed_domain="acmefinancial.internal",
    )

    # All 4 classes redacted with typed placeholders
    for cls in REDACTION_CLASSES:
        assert result.counts[cls] >= 1, f"Expected at least 1 redaction for {cls}: {result.counts}"
        assert PLACEHOLDER_BY_CLASS[cls] in result.scrubbed_text

    # Sensitive values completely removed
    for secret in (
        "alex.morgan@acmefinancial.internal",
        "10.240.12.4",
        "10.240.12.5",
        "2001:db8::42",
        "sk-live-98a7bc65def4321098a7bc65def43210",
        "Sup3rS3cretP@ssw0rd!",
        "K9mP2xQ7vL4nR8wZ3cF6hJ1bN5tY0dG4sA",
        "Acme Financial Corp.",
        "Project Phoenix",
    ):
        assert secret not in result.scrubbed_text

    # Non-sensitive architectural prose and version numbers preserved intact
    assert "Transactional Outbox Pattern" in result.scrubbed_text
    assert "PostgreSQL 16.2" in result.scrubbed_text
    assert "Redis v7.2.1" in result.scrubbed_text
    assert "PgBouncer" in result.scrubbed_text
    assert "0123456789abcdef0123456789abcdef01234567" in result.scrubbed_text

    # ADRSanitizationLog summary helper compatibility
    payload = result.to_presidio_entities_scrubbed()
    total, categories = extract_redaction_summary(payload)
    assert total == result.total_redactions
    assert categories == result.counts


# ---------------------------------------------------------------------------
# Integration tests: Tenant deny-list settings, fail-closed quarantine & ARQ worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_settings_persists_adr_deny_list_and_scrubber_quarantines_fail_closed() -> (
    None
):
    tenant_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    project_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    adr_ok_id = uuid.uuid4()
    adr_fail_id = uuid.uuid4()

    private_key, mock_jwks = _setup_mock_jwks()
    orig_jwks = app.state.jwks_client
    orig_redis = getattr(app.state, "ai_reindex_redis", None)
    app.state.jwks_client = mock_jwks
    app.state.ai_reindex_redis = _FakeAsyncRedis()

    try:
        async with get_tenant_db_context(tenant_id) as db:
            await _provision_tenant(
                db,
                tenant_id=tenant_id,
                user_id=admin_id,
                prefix="dl",
                ai_processing_mode="local_only",
                name="NordicPay Systems",
                allowed_domain="nordicpay.io",
            )
            db.add(
                Project(
                    id=project_id,
                    tenant_id=tenant_id,
                    created_by=admin_id,
                    name="Payments Core",
                    slug=f"pay-{tenant_id.hex[:6]}",
                )
            )
            await db.flush()
            db.add(
                Repository(
                    id=repo_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    external_repo_id=int(tenant_id.int % 1_000_000_000),
                    full_name="nordicpay/core-ledger",
                )
            )
            await db.flush()
            db.add_all(
                [
                    AnonymizedADR(
                        id=adr_ok_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        source_commit_sha="a" * 40,
                        title="Ledger Partitioning at NordicPay Systems",
                        status="draft",
                        sanitization_verified=False,
                    ),
                    AnonymizedADR(
                        id=adr_fail_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        source_commit_sha="b" * 40,
                        title="Scanner Failure Quarantine ADR",
                        status="draft",
                        sanitization_verified=False,
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_ok_id,
                        file_path="docs/adr/001-ledger.md",
                        commit_sha="a" * 40,
                        raw_markdown=(
                            "Project Valkyrie inside NordicPay Systems connects to 10.10.20.30.\n"
                            "Reach security@nordicpay.io with key ghp_AbCdEfGhIjKlMnOpQrStUvWxYz1234567890."
                        ),
                        content_hash="1" * 64,
                    ),
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_fail_id,
                        file_path="docs/adr/002-fail.md",
                        commit_sha="b" * 40,
                        raw_markdown="RAW SECRET DO NOT FORWARD sk-live-123456789012345678901234",
                        content_hash="2" * 64,
                    ),
                ]
            )
            await db.flush()

        admin_token = _create_jwt(
            private_key, tenant_id=tenant_id, user_id=admin_id, role="TENANT_ADMIN"
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_patch = await client.patch(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "adr_deny_list": {
                        "version": 1,
                        "auto_include_tenant_identity": True,
                        "terms": ["Project Valkyrie"],
                        "entries": [],
                    }
                },
            )
            assert r_patch.status_code == 200, r_patch.text
            assert r_patch.json()["adr_deny_list"]["terms"] == ["Project Valkyrie"]

            # Explicit null adr_deny_list rejected with 422
            r_null = await client.patch(
                "/api/v1/tenants/settings",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"adr_deny_list": None},
            )
            assert r_null.status_code == 422

        # Standalone scrubber persists ADRSanitizationLog category counts
        async with get_tenant_db_context(tenant_id) as db:
            outcome_ok = await scrub_adr_source(db, tenant_id=tenant_id, adr_id=adr_ok_id)
            assert outcome_ok.quarantined is False
            assert outcome_ok.counts["EMAIL"] == 1
            assert outcome_ok.counts["IP_ADDRESS"] == 1
            assert outcome_ok.counts["API_TOKEN"] == 1
            assert outcome_ok.counts["COMPANY_NAME"] >= 2
            assert "[REDACTED_COMPANY_NAME]" in (outcome_ok.scrubbed_markdown or "")
            assert "Project Valkyrie" not in (outcome_ok.scrubbed_markdown or "")

            logs = list(
                (
                    await db.scalars(
                        select(ADRSanitizationLog).where(
                            ADRSanitizationLog.tenant_id == tenant_id,
                            ADRSanitizationLog.adr_id == adr_ok_id,
                        )
                    )
                ).all()
            )
            assert len(logs) == 1
            assert logs[0].presidio_entities_scrubbed["EMAIL"] == 1
            assert logs[0].presidio_entities_scrubbed["API_TOKEN"] == 1

        # Fail-closed quarantine when scanner raises an unexpected exception
        class _ExplodingScrubber(ADRScrubber):
            def scrub_text(self, *args, **kwargs):
                raise RuntimeError("Simulated Presidio analyzer crash")

        async with get_tenant_db_context(tenant_id) as db:
            outcome_fail = await scrub_adr_source(
                db,
                tenant_id=tenant_id,
                adr_id=adr_fail_id,
                scrubber=_ExplodingScrubber(),
            )
            assert outcome_fail.quarantined is True
            assert outcome_fail.scrubbed_markdown is None

        async with get_tenant_db_context(tenant_id) as db:
            quarantined_adr = await db.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == adr_fail_id,
                )
            )
            assert quarantined_adr is not None
            assert quarantined_adr.status == "quarantined"
            assert quarantined_adr.sanitization_verified is False
            assert quarantined_adr.decision_summary is None

            q_log = await db.scalar(
                select(ADRSanitizationLog).where(
                    ADRSanitizationLog.tenant_id == tenant_id,
                    ADRSanitizationLog.adr_id == adr_fail_id,
                )
            )
            assert q_log is not None
            assert q_log.raw_input_snippet is None
            assert q_log.solution_architect_diff_payload["status"] == "quarantined"
    finally:
        app.state.jwks_client = orig_jwks
        app.state.ai_reindex_redis = orig_redis
        await async_engine.dispose()
        await _cleanup_tenants([tenant_id])


@pytest.mark.asyncio
async def test_adr_generalize_worker_local_only_zero_wan_policy_guard_and_diff(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    arch_id = uuid.uuid4()
    project_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    adr_id = uuid.uuid4()
    adr_scanner_crash_id = uuid.uuid4()

    fake_redis = _FakeAsyncRedis()
    local_requests: list[httpx.Request] = []

    async def local_ollama_handler(request: httpx.Request) -> httpx.Response:
        local_requests.append(request)
        payload = {
            "title": "Outbox Pattern for Reliable Event Publishing",
            "architecture_pattern": "Transactional Outbox",
            "context_and_problem": (
                "Services require atomic persistence of domain mutations and integration events "
                "across relational databases without dual-write inconsistencies."
            ),
            "decision_outcome": (
                "Persist integration events inside an outbox table within the same database transaction "
                "and publish asynchronously via a background relay worker."
            ),
            "consequences": [
                "Guarantees at-least-once event delivery after database commit.",
                "Requires idempotent downstream event consumers.",
            ],
            "quality_attributes": ["Reliability", "Eventual Consistency"],
        }
        return httpx.Response(
            200,
            json={
                "model": settings.LOCAL_LLM_MODEL,
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 312, "completion_tokens": 98, "total_tokens": 410},
            },
            request=request,
        )

    private_key, mock_jwks = _setup_mock_jwks()
    orig_jwks = app.state.jwks_client
    app.state.jwks_client = mock_jwks

    try:
        async with get_tenant_db_context(tenant_id) as db:
            await _provision_tenant(
                db,
                tenant_id=tenant_id,
                user_id=admin_id,
                prefix="loc",
                ai_processing_mode="local_only",
                name="Helios Banking",
                allowed_domain="heliosbank.internal",
            )
            db.add(
                User(
                    id=arch_id,
                    tenant_id=tenant_id,
                    email=f"arch-{tenant_id.hex[:6]}@manta.local",
                    full_name="Architect Local",
                    google_sub=str(arch_id),
                    role="SOLUTION_ARCHITECT",
                )
            )
            await db.flush()
            db.add(UserRole(tenant_id=tenant_id, user_id=arch_id, role_id="SOLUTION_ARCHITECT"))
            db.add(
                Project(
                    id=project_id,
                    tenant_id=tenant_id,
                    created_by=admin_id,
                    name="Core Banking",
                    slug=f"core-bank-{tenant_id.hex[:6]}",
                )
            )
            await db.flush()
            db.add(
                Repository(
                    id=repo_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    external_repo_id=int(tenant_id.int % 1_000_000_000),
                    full_name="helios/ledger-api",
                )
            )
            await db.flush()
            db.add_all(
                [
                    AnonymizedADR(
                        id=adr_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        source_commit_sha="c" * 40,
                        title="Outbox Relay for Helios Banking",
                        status="draft",
                        sanitization_verified=False,
                    ),
                    AnonymizedADR(
                        id=adr_scanner_crash_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        source_commit_sha="d" * 40,
                        title="Scanner Crash ADR",
                        status="draft",
                        sanitization_verified=False,
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_id,
                        file_path="docs/adr/010-outbox.md",
                        commit_sha="c" * 40,
                        raw_markdown=(
                            "# Outbox Relay for Helios Banking\n"
                            "Owner: lead.dev@heliosbank.internal, host 172.16.40.12.\n"
                            "Webhook signing secret: sk-live-9876543210abcdef9876543210abcdef.\n"
                            "Use PostgreSQL 16 transactional outbox instead of dual writes."
                        ),
                        content_hash="3" * 64,
                    ),
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_scanner_crash_id,
                        file_path="docs/adr/011-crash.md",
                        commit_sha="d" * 40,
                        raw_markdown="Sensitive raw text sk-live-9876543210abcdef9876543210abcdef",
                        content_hash="4" * 64,
                    ),
                ]
            )
            await db.flush()

        # 1. Policy guard refuses unapproved Cloud call when tenant is local_only (0 HTTP calls)
        cloud_requests: list[httpx.Request] = []

        async def forbidden_cloud_handler(request: httpx.Request) -> httpx.Response:
            cloud_requests.append(request)
            return httpx.Response(200, json={}, request=request)

        cloud_client = create_ai_client(
            AI_MODE_CLOUD_ZDR,
            cloud_base_url="https://api.openai.com/v1",
            cloud_api_key="sk-cloud-key",
            transport=httpx.MockTransport(forbidden_cloud_handler),
        )
        assert isinstance(cloud_client, CloudZDRAIClient)
        async with cloud_client:
            with pytest.raises(CloudPolicyViolationError):
                await process_adr_generalize_job(
                    {
                        "job_id": str(uuid.uuid4()),
                        "job_type": ADR_GENERALIZE_JOB_TYPE,
                        "tenant_id": str(tenant_id),
                        "payload": {"adr_id": str(adr_id)},
                    },
                    ai_client=cloud_client,
                )
            with pytest.raises(CloudPolicyViolationError):
                await process_adr_generalize_job(
                    {
                        "job_id": str(uuid.uuid4()),
                        "job_type": ADR_GENERALIZE_JOB_TYPE,
                        "tenant_id": str(tenant_id),
                        "payload": {"adr_id": str(adr_id), "requested_mode": "cloud_zdr"},
                    }
                )
        assert len(cloud_requests) == 0

        # Also verify fail-closed if LOCAL_LLM_BASE_URL is misconfigured to a WAN host
        old_local_url = settings.LOCAL_LLM_BASE_URL
        settings.LOCAL_LLM_BASE_URL = "https://api.openai.com/v1"
        try:
            with pytest.raises(LocalOnlyPolicyViolationError):
                await process_adr_generalize_job(
                    {
                        "job_id": str(uuid.uuid4()),
                        "job_type": ADR_GENERALIZE_JOB_TYPE,
                        "tenant_id": str(tenant_id),
                        "payload": {"adr_id": str(adr_id)},
                    }
                )
        finally:
            settings.LOCAL_LLM_BASE_URL = old_local_url

        # 2. Scanner crash inside worker quarantines ADR fail-closed with 0 AI calls
        class _BrokenScrubber(ADRScrubber):
            def scrub_text(self, *args, **kwargs):
                raise ADRScannerFailureError("Presidio recognizer crashed")

        quarantine_res = await process_adr_generalize_job(
            {
                "job_id": str(uuid.uuid4()),
                "job_type": ADR_GENERALIZE_JOB_TYPE,
                "tenant_id": str(tenant_id),
                "payload": {"adr_id": str(adr_scanner_crash_id)},
            },
            scrubber=_BrokenScrubber(),
        )
        assert quarantine_res["status"] == "quarantined"
        assert len(local_requests) == 0

        # 3. Enqueue & dispatch local_only job -> 0 WAN calls, strict JSON -> pending_review
        enqueue_info = await enqueue_adr_generalize_job(
            tenant_id=tenant_id,
            adr_id=adr_id,
            priority="default",
            redis_client=fake_redis,
        )
        assert enqueue_info["queue"] == QUEUE_DEFAULT
        queued_envelope = json.loads(fake_redis.lists[QUEUE_DEFAULT][0])
        assert queued_envelope["job_type"] == ADR_GENERALIZE_JOB_TYPE

        local_client = LocalAIClient(
            base_url="http://ollama:11434/v1",
            transport=httpx.MockTransport(local_ollama_handler),
        )
        monkeypatch.setattr(
            "app.adr.generalizer.create_ai_client",
            lambda mode, **kw: local_client,
        )
        worker_res = await dispatch_worker_job(queued_envelope)
        assert worker_res["status"] == "pending_review"
        assert worker_res["ai_mode"] == AI_MODE_LOCAL_ONLY
        assert worker_res["architecture_pattern"] == "Transactional Outbox"
        assert worker_res["prompt_tokens"] == 312
        assert len(local_requests) == 1
        assert str(local_requests[0].url) == "http://ollama:11434/v1/chat/completions"

        # Verify LLM request body contained ONLY scrubbed text (0 raw PII/secrets)
        sent_body = json.loads(local_requests[0].content.decode("utf-8"))
        user_msg = sent_body["messages"][1]["content"]
        assert "[REDACTED_EMAIL]" in user_msg
        assert "[REDACTED_IP_ADDRESS]" in user_msg
        assert "[REDACTED_API_TOKEN]" in user_msg
        assert "[REDACTED_COMPANY_NAME]" in user_msg
        assert "lead.dev@heliosbank.internal" not in user_msg
        assert "172.16.40.12" not in user_msg
        assert "sk-live-9876543210abcdef9876543210abcdef" not in user_msg
        assert "Helios Banking" not in user_msg
        assert sent_body["response_format"]["type"] == "json_schema"

        # Verify AnonymizedADR transitioned draft -> pending_review & diff endpoint works
        async with get_tenant_db_context(tenant_id) as db:
            updated_adr = await db.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == adr_id,
                )
            )
            assert updated_adr is not None
            assert updated_adr.status == "pending_review"
            assert updated_adr.sanitization_verified is False
            assert updated_adr.architecture_pattern == "Transactional Outbox"
            assert "## Context & Problem" in (updated_adr.decision_summary or "")

        arch_token = _create_jwt(
            private_key, tenant_id=tenant_id, user_id=arch_id, role="SOLUTION_ARCHITECT"
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_diff = await client.get(
                f"/api/v1/adrs/{adr_id}/diff",
                headers={"Authorization": f"Bearer {arch_token}"},
            )
            assert r_diff.status_code == 200, r_diff.text
            diff_body = r_diff.json()
            assert diff_body["status"] == "pending_review"
            assert diff_body["redaction_count"] >= 4
            assert diff_body["sanitization_logs"][0]["llm_abstraction_prompt_tokens"] == 312
    finally:
        app.state.jwks_client = orig_jwks
        await async_engine.dispose()
        await _cleanup_tenants([tenant_id])


@pytest.mark.asyncio
async def test_adr_generalize_worker_cloud_zdr_strict_schema_and_post_llm_guard() -> None:
    tenant_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    project_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    adr_cloud_id = uuid.uuid4()
    adr_bad_json_id = uuid.uuid4()
    adr_leak_id = uuid.uuid4()

    cloud_requests: list[httpx.Request] = []

    async def cloud_zdr_handler(request: httpx.Request) -> httpx.Response:
        cloud_requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        user_msg = body["messages"][1]["content"]

        if "Bad Schema ADR" in user_msg:
            # Violates extra='forbid' by injecting an unexpected key
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "title": "Invalid Extra Key Pattern",
                                        "architecture_pattern": "Event Sourcing",
                                        "context_and_problem": "Sufficiently long context string.",
                                        "decision_outcome": "Sufficiently long decision string.",
                                        "consequences": ["High complexity"],
                                        "unexpected_field": "forbidden",
                                    }
                                )
                            }
                        }
                    ]
                },
                request=request,
            )

        if "Leaky Output ADR" in user_msg:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "title": "Leaky Pattern for TitanBank Corp.",
                                        "architecture_pattern": "CQRS",
                                        "context_and_problem": "Need read/write separation at 10.99.88.77.",
                                        "decision_outcome": "Split command and query stores cleanly.",
                                        "consequences": ["Eventual consistency on projections."],
                                    }
                                )
                            }
                        }
                    ]
                },
                request=request,
            )

        return httpx.Response(
            200,
            json={
                "model": settings.CLOUD_ZDR_LLM_MODEL,
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Zero-Trust Service Mesh Authorization",
                                    "architecture_pattern": "Zero-Trust Service Mesh",
                                    "context_and_problem": (
                                        "Internal microservices require mutually authenticated encrypted "
                                        "east-west traffic without relying on perimeter trust."
                                    ),
                                    "decision_outcome": (
                                        "Enforce mTLS with short-lived workload identities across all service proxies."
                                    ),
                                    "consequences": [
                                        "Eliminates lateral movement risk inside cluster.",
                                        "Adds minor TLS handshake latency on cold connections.",
                                    ],
                                    "quality_attributes": ["Security", "Zero Trust"],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 250, "completion_tokens": 80, "total_tokens": 330},
            },
            request=request,
        )

    try:
        async with get_tenant_db_context(tenant_id) as db:
            await _provision_tenant(
                db,
                tenant_id=tenant_id,
                user_id=admin_id,
                prefix="zdr",
                ai_processing_mode="cloud_zdr",
                name="TitanBank Corp.",
            )
            db.add(
                Project(
                    id=project_id,
                    tenant_id=tenant_id,
                    created_by=admin_id,
                    name="Security Platform",
                    slug=f"sec-{tenant_id.hex[:6]}",
                )
            )
            await db.flush()
            db.add(
                Repository(
                    id=repo_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    external_repo_id=int(tenant_id.int % 1_000_000_000),
                    full_name="titan/mesh-control",
                )
            )
            await db.flush()
            db.add_all(
                [
                    AnonymizedADR(
                        id=adr_cloud_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        title="Mesh Auth for TitanBank Corp.",
                        status="draft",
                    ),
                    AnonymizedADR(
                        id=adr_bad_json_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        title="Bad Schema ADR",
                        status="draft",
                    ),
                    AnonymizedADR(
                        id=adr_leak_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        source_repository_id=repo_id,
                        title="Leaky Output ADR",
                        status="draft",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_cloud_id,
                        file_path="docs/adr/020-mesh.md",
                        commit_sha="e" * 40,
                        raw_markdown="TitanBank Corp. deploys mTLS mesh with owner sec@titanbank.com.",
                        content_hash="5" * 64,
                    ),
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_bad_json_id,
                        file_path="docs/adr/021-bad.md",
                        commit_sha="f" * 40,
                        raw_markdown="Bad Schema ADR evaluating Event Sourcing.",
                        content_hash="6" * 64,
                    ),
                    ADRSource(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        repository_id=repo_id,
                        adr_id=adr_leak_id,
                        file_path="docs/adr/022-leak.md",
                        commit_sha="9" * 40,
                        raw_markdown="Leaky Output ADR evaluating CQRS.",
                        content_hash="7" * 64,
                    ),
                ]
            )
            await db.flush()

        cloud_client = create_ai_client(
            AI_MODE_CLOUD_ZDR,
            cloud_base_url="https://api.openai.com/v1",
            cloud_api_key="sk-zdr-test-key",
            transport=httpx.MockTransport(cloud_zdr_handler),
        )
        async with cloud_client:
            # 1. Authorized cloud_zdr succeeds with X-MANTA-ZDR: enforced
            res_ok = await process_adr_generalize_job(
                {
                    "job_id": str(uuid.uuid4()),
                    "job_type": ADR_GENERALIZE_JOB_TYPE,
                    "tenant_id": str(tenant_id),
                    "payload": {"adr_id": str(adr_cloud_id)},
                },
                ai_client=cloud_client,
            )
            assert res_ok["status"] == "pending_review"
            assert res_ok["ai_mode"] == AI_MODE_CLOUD_ZDR
            assert res_ok["prompt_tokens"] == 250
            assert cloud_requests[0].headers["x-manta-zdr"] == "enforced"

            # 2. Strict JSON schema failure rejects update and keeps ADR in draft
            with pytest.raises(ADRGeneralizationSchemaError):
                await process_adr_generalize_job(
                    {
                        "job_id": str(uuid.uuid4()),
                        "job_type": ADR_GENERALIZE_JOB_TYPE,
                        "tenant_id": str(tenant_id),
                        "payload": {"adr_id": str(adr_bad_json_id)},
                    },
                    ai_client=cloud_client,
                )

            # 3. Post-LLM guard quarantines ADR if LLM output leaks tenant identity or IP
            res_leak = await process_adr_generalize_job(
                {
                    "job_id": str(uuid.uuid4()),
                    "job_type": ADR_GENERALIZE_JOB_TYPE,
                    "tenant_id": str(tenant_id),
                    "payload": {"adr_id": str(adr_leak_id)},
                },
                ai_client=cloud_client,
            )
            assert res_leak["status"] == "quarantined"

        async with get_tenant_db_context(tenant_id) as db:
            bad_adr = await db.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == adr_bad_json_id,
                )
            )
            leak_adr = await db.scalar(
                select(AnonymizedADR).where(
                    AnonymizedADR.tenant_id == tenant_id,
                    AnonymizedADR.id == adr_leak_id,
                )
            )
            assert bad_adr is not None and bad_adr.status == "draft"
            assert leak_adr is not None and leak_adr.status == "quarantined"
    finally:
        await async_engine.dispose()
        await _cleanup_tenants([tenant_id])
