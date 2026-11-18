"""
SCRUM-452 (Task 68.3), SCRUM-453 (Task 68.4) & SCRUM-456 (Task 68.7):
Automated Pytest Suite for Production Health Signal Ingestion, HMAC / Token / JWT
Authentication, Cross-Tenant Isolation, and Idempotent Failure Session State Machine.

Coverage:
1. Schema, Model & Migration Contracts (Task 68.3):
   - `production_failures` columns, check constraints, and partial unique index
     `uq_production_failures_one_open_per_project`.
   - `projects` health probe configuration columns and AES-256-GCM secret wrapper.
   - Migration `e4a9c2f7b1d6` RLS (`ENABLE` + `FORCE ROW LEVEL SECURITY`),
     `tenant_isolation_policy` for `eng_app_user`, and `get_project_health_webhook_route(UUID)`.
2. Idempotent Production Failure Session State Machine (Task 68.4):
   - Canonical cycle: `10:00` FAIL -> `10:05` duplicate FAIL -> `11:48` PASS = 6480.0s (1.80h).
   - Duplicate FAIL never overwrites `started_at` or creates a second OPEN session.
   - Orphan PASS when healthy (or after resolution) creates 0 rows and returns 200 no-op.
   - Sequential outage cycles & 0-second failover recovery (`started_at == resolved_at`).
   - Clock-skew guard rejects PASS earlier than active `started_at` with HTTP 422.
   - Syncs `incidents` and verifies `compute_mttr` yields exact `1.80h` / `6480.0s`.
3. Dual-Endpoint HTTP Auth & Cross-Tenant Security (Task 68.3):
   - `POST /api/v1/projects/{project_id}/health-signals`
   - `POST /api/v1/webhooks/projects/{project_id}/health`
   - Valid HMAC-SHA256 (`X-Hub-Signature-256`, `X-Webhook-Signature`, `X-Health-Signature`),
     shared tokens (`X-Webhook-Secret`, `X-Health-Token`, `Authorization: Bearer <secret>`),
     and `TENANT_ADMIN` OIDC RS256 JWT accepted with HTTP 200 OK.
   - Invalid/tampered HMAC signatures, wrong secret, missing credentials -> HTTP 401.
   - `DEVELOPER` JWT or disabled probe -> HTTP 403.
   - Cross-tenant JWT, cross-tenant `X-Tenant-ID`, or missing/archived project -> HTTP 401/403/404.
   - Health config CRUD & secret rotation invalidates old HMAC and accepts rotated HMAC.
4. Live PostgreSQL 16 + FORCE RLS End-to-End Integration (@skip_without_live_db).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import socket
import time
from typing import Any, Optional
import unittest.mock as mock
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.auth.jwks import JWKSClient
from app.auth.permissions import FORBIDDEN_RESPONSE
from app.config import settings
from app.db.models.models import Incident, ProductionFailure, Project, Tenant, User
from app.db.session import async_engine, get_tenant_db_context
from app.dora.service import calculate_rolling_mttr, compute_mttr
from app.integrations.crypto import encryption_service
from app.main import app
import app.projects.health_service as health_service_module
from app.projects.health_service import (
    process_health_signal,
    verify_health_webhook_hmac,
)
from app.tenants.rate_limiter import registration_rate_limiter


def _is_live_db_available() -> bool:
    """Fast check (<1.5s) for a reachable PostgreSQL test instance."""
    try:
        host = async_engine.url.host or "127.0.0.1"
        port = int(async_engine.url.port or 5432)
        with socket.create_connection((host, port), timeout=0.5):
            pass

        async def _ping() -> None:
            await async_engine.dispose()
            try:
                async with async_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await async_engine.dispose()

        asyncio.run(asyncio.wait_for(_ping(), timeout=1.5))
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = _is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL test instance is not reachable locally; runs in CI.",
)


# ---------------------------------------------------------------------------
# Signing & JWT Helpers
# ---------------------------------------------------------------------------


def _sign_hmac(secret: str, raw_body: bytes, *, prefix: bool = True) -> str:
    mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={mac}" if prefix else mac


def _generate_rsa_keypair(kid: str = "us68-test-kid") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk


def _mint_jwt(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = "us68-test-kid",
    tenant_id: uuid.UUID,
    roles: list[str],
    sub: str = "us68-admin",
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "iat": now,
        "exp": now + 3600,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Stateful In-Memory Tenant-Isolated DB Harness (for fast deterministic unit/HTTP tests)
# ---------------------------------------------------------------------------


class _FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeExecuteResult:
    def __init__(
        self, items: list[Any], *, mappings_rows: Optional[list[dict[str, Any]]] = None
    ) -> None:
        self._items = items
        self._mappings_rows = mappings_rows or []

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._items)

    def scalar_one_or_none(self) -> Any:
        if not self._items:
            return None
        return self._items[0]

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def mappings(self) -> "_FakeMappings":
        return _FakeMappings(self._mappings_rows)


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def first(self) -> Optional[dict[str, Any]]:
        return self._rows[0] if self._rows else None


class InMemoryTenantStore:
    """In-memory multi-tenant state store enforcing RLS filtering & partial unique constraints."""

    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, Project] = {}
        self.failures: list[ProductionFailure] = []
        self.incidents: list[Incident] = []

    def seed_project(
        self,
        *,
        tenant_id: uuid.UUID,
        project_id: Optional[uuid.UUID] = None,
        secret: Optional[str] = "whsec_health_test_secret_123",
        enabled: bool = True,
        is_active: bool = True,
        deleted_at: Optional[datetime] = None,
        probe_source: str = "webhook",
    ) -> Project:
        now = datetime.now(timezone.utc)
        proj = Project(
            id=project_id or uuid.uuid4(),
            tenant_id=tenant_id,
            name="Health Monitored Service",
            slug=f"svc-{uuid.uuid4().hex[:8]}",
            tier_sla_hours=24,
            authoritative_cd_signal="github_deployments_api",
            lead_time_first_commit_strategy="feature_branch_root",
            wip_limit_per_dev=3,
            pr_stale_threshold_hours=48,
            health_probe_enabled=enabled,
            health_probe_source=probe_source,
            health_webhook_secret_hash=encryption_service.encrypt(secret) if secret else None,
            health_probe_interval_seconds=60,
            health_status="HEALTHY",
            is_active=is_active,
            deleted_at=deleted_at,
            created_at=now,
            updated_at=now,
        )
        self.projects[proj.id] = proj
        return proj


class FakeTenantAsyncSession:
    """Minimal AsyncSession implementation backed by InMemoryTenantStore with RLS tenant scoping."""

    def __init__(self, store: InMemoryTenantStore, tenant_id: Optional[uuid.UUID]) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self._pending: list[Any] = []

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    async def flush(self) -> None:
        for obj in self._pending:
            if isinstance(obj, ProductionFailure):
                if obj.status == "OPEN":
                    existing_open = [
                        f
                        for f in self.store.failures
                        if f is not obj
                        and f.tenant_id == obj.tenant_id
                        and f.project_id == obj.project_id
                        and f.status == "OPEN"
                    ]
                    if existing_open:
                        raise IntegrityError(
                            "duplicate key value violates unique constraint uq_production_failures_one_open_per_project",
                            None,
                            Exception("uq_production_failures_one_open_per_project"),
                        )
                if obj not in self.store.failures:
                    self.store.failures.append(obj)
            elif isinstance(obj, Incident):
                if obj not in self.store.incidents:
                    self.store.incidents.append(obj)
            elif isinstance(obj, Project):
                self.store.projects[obj.id] = obj
        self._pending.clear()

    async def execute(
        self, stmt: Any, params: Optional[dict[str, Any]] = None
    ) -> _FakeExecuteResult:
        sql_str = str(stmt)
        if "get_project_health_webhook_route" in sql_str and params:
            pid = params["project_id"]
            proj = self.store.projects.get(pid)
            if proj is None:
                return _FakeExecuteResult([], mappings_rows=[])
            return _FakeExecuteResult(
                [],
                mappings_rows=[
                    {
                        "id": proj.id,
                        "tenant_id": proj.tenant_id,
                        "health_probe_enabled": proj.health_probe_enabled,
                        "health_probe_source": proj.health_probe_source,
                        "health_webhook_secret_hash": proj.health_webhook_secret_hash,
                        "health_status": proj.health_status,
                        "is_active": proj.is_active,
                        "deleted_at": proj.deleted_at,
                    }
                ],
            )

        compiled = stmt.compile()
        bound = compiled.params
        entities = [desc.get("entity") for desc in stmt.column_descriptions]
        target = entities[0] if entities else None

        # Extract common filter params cleanly from compiled SQLAlchemy statement
        proj_id_filter = next(
            (v for k, v in bound.items() if k.startswith("id_") and isinstance(v, uuid.UUID)),
            None,
        )
        fk_proj_id_filter = next(
            (
                v
                for k, v in bound.items()
                if k.startswith("project_id_") and isinstance(v, uuid.UUID)
            ),
            None,
        )
        status_filter = next(
            (v for k, v in bound.items() if k.startswith("status_") and isinstance(v, str)),
            None,
        )

        if target is Project:
            matches = [
                p
                for p in self.store.projects.values()
                if (self.tenant_id is None or p.tenant_id == self.tenant_id)
                and p.deleted_at is None
                and (proj_id_filter is None or p.id == proj_id_filter)
            ]
            # Handle select(Project.id)
            if (
                len(stmt.column_descriptions) == 1
                and stmt.column_descriptions[0].get("name") == "id"
            ):
                return _FakeExecuteResult([p.id for p in matches])
            return _FakeExecuteResult(matches)

        if target is ProductionFailure:
            matches = [
                f
                for f in self.store.failures
                if (self.tenant_id is None or f.tenant_id == self.tenant_id)
                and (fk_proj_id_filter is None or f.project_id == fk_proj_id_filter)
                and (status_filter is None or f.status == status_filter)
            ]
            matches.sort(key=lambda r: r.started_at, reverse=True)
            return _FakeExecuteResult(matches)

        if target is Incident:
            inc_id_filter = proj_id_filter
            matches = [
                inc
                for inc in self.store.incidents
                if (self.tenant_id is None or inc.tenant_id == self.tenant_id)
                and (fk_proj_id_filter is None or inc.project_id == fk_proj_id_filter)
                and (inc_id_filter is None or inc.id == inc_id_filter)
            ]
            if "resolved_at IS NULL" in sql_str:
                matches = [inc for inc in matches if inc.resolved_at is None]
            return _FakeExecuteResult(matches)

        return _FakeExecuteResult([])


class _FakeSessionBegin:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeStandaloneSession(FakeTenantAsyncSession):
    def begin(self) -> _FakeSessionBegin:
        return _FakeSessionBegin()

    async def __aenter__(self) -> "_FakeStandaloneSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_caches():
    registration_rate_limiter.reset()
    health_service_module._DECRYPTED_SECRET_CACHE.clear()
    yield
    registration_rate_limiter.reset()
    health_service_module._DECRYPTED_SECRET_CACHE.clear()


@pytest.fixture
def in_memory_env():
    """Provides isolated InMemoryTenantStore + mock JWKS client + patched DB contexts."""
    store = InMemoryTenantStore()
    priv_key, jwk = _generate_rsa_keypair("us68-test-kid")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"keys": [jwk]}, request=req)
    )
    orig_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=transport,
    )

    @asynccontextmanager
    async def _fake_tenant_ctx(tenant_id: uuid.UUID):
        sess = FakeTenantAsyncSession(store, tenant_id)
        yield sess
        await sess.flush()

    def _fake_session_local():
        return _FakeStandaloneSession(store, None)

    async def _fake_resolve_roles(user):
        return {r.strip().upper() for r in user.roles}

    with (
        mock.patch.object(
            health_service_module, "get_tenant_db_context", side_effect=_fake_tenant_ctx
        ),
        mock.patch.object(
            health_service_module, "AsyncSessionLocal", side_effect=_fake_session_local
        ),
        mock.patch.object(
            health_service_module, "resolve_caller_roles", side_effect=_fake_resolve_roles
        ),
        mock.patch("app.auth.permissions.resolve_caller_roles", side_effect=_fake_resolve_roles),
    ):
        yield {
            "store": store,
            "private_key": priv_key,
            "kid": "us68-test-kid",
        }

    app.state.jwks_client = orig_client


# ===========================================================================
# 1. Schema, Model & Migration Contract Tests (Task 68.3)
# ===========================================================================


def test_task_68_3_production_failures_model_constraints_and_indexes():
    """Verify ProductionFailure & Project ORM schemas match US-68 / Task 68.1 & 68.3."""
    pf_table = ProductionFailure.__table__
    expected_cols = {
        "id",
        "tenant_id",
        "project_id",
        "incident_id",
        "started_at",
        "resolved_at",
        "recovery_time_seconds",
        "status",
        "probe_source",
        "severity",
        "created_at",
        "updated_at",
    }
    assert expected_cols.issubset(set(pf_table.columns.keys()))

    index_names = {idx.name for idx in pf_table.indexes}
    assert "idx_production_failures_tenant_id" in index_names
    assert "idx_production_failures_project_status" in index_names
    assert "idx_production_failures_project_resolved_at" in index_names
    assert "uq_production_failures_one_open_per_project" in index_names

    partial_unique_idx = next(
        idx for idx in pf_table.indexes if idx.name == "uq_production_failures_one_open_per_project"
    )
    assert partial_unique_idx.unique is True
    assert "OPEN" in str(partial_unique_idx.dialect_options["postgresql"]["where"])

    constraint_names = {c.name for c in pf_table.constraints if c.name}
    assert "ck_production_failures_status" in constraint_names
    assert "ck_production_failures_recovery_non_negative" in constraint_names

    proj_cols = set(Project.__table__.columns.keys())
    assert {
        "health_probe_enabled",
        "health_probe_source",
        "health_webhook_secret_hash",
        "health_probe_url",
        "health_probe_interval_seconds",
        "health_status",
    }.issubset(proj_cols)


def test_task_68_3_migration_file_contains_force_rls_and_security_definer_route():
    """Verify migration e4a9c2f7b1d6 enforces FORCE RLS for eng_app_user and lookup RPC."""
    mig_path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "e4a9c2f7b1d6_production_failures_and_project_health_config.py"
    )
    assert mig_path.is_file()
    source = mig_path.read_text(encoding="utf-8")

    assert "ALTER TABLE production_failures ENABLE ROW LEVEL SECURITY;" in source
    assert "ALTER TABLE production_failures FORCE ROW LEVEL SECURITY;" in source
    assert "CREATE POLICY tenant_isolation_policy ON production_failures" in source
    assert "TO eng_app_user" in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON production_failures TO eng_app_user;" in source
    assert (
        "CREATE OR REPLACE FUNCTION get_project_health_webhook_route(p_project_id UUID)" in source
    )
    assert "SECURITY DEFINER" in source
    assert "SET search_path = public, pg_temp" in source
    assert "uq_production_failures_one_open_per_project" in source


def test_task_68_3_project_health_webhook_secret_property_encrypts():
    """Verify Project.health_webhook_secret encrypts via AES-256-GCM on assignment."""
    proj = Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Crypto Test",
        slug="crypto-test",
    )
    proj.health_webhook_secret = "super_secret_health_probe_key_999"
    assert proj.health_webhook_secret_hash is not None
    assert proj.health_webhook_secret_hash.startswith("enc:v1:")
    assert proj.health_webhook_secret == "super_secret_health_probe_key_999"


# ===========================================================================
# 2. Failure Session State Machine Tests (Task 68.4)
# ===========================================================================


@pytest.mark.asyncio
async def test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync(in_memory_env):
    """
    Task 68.4 / Task 68.5 / Task 29.3 Canonical Benchmark:
    - 10:00:00Z FAIL -> opens 1 OPEN session (started_at = 10:00:00Z), syncs Incident, sets FAILING.
    - 10:05:00Z duplicate FAIL -> no-op, keeps started_at = 10:00:00Z unchanged, 0 extra rows.
    - 11:48:00Z PASS -> resolves session (resolved_at = 11:48:00Z, recovery_time_seconds = 6480.0),
      syncs Incident, sets HEALTHY, and compute_mttr yields 1.80h (6480.0s).
    """
    store: InMemoryTenantStore = in_memory_env["store"]
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id)
    session = FakeTenantAsyncSession(store, tenant_id)

    t_fail_1 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_fail_dup = datetime(2026, 11, 12, 10, 5, 0, tzinfo=timezone.utc)
    t_pass = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)

    # 1. First FAIL -> OPEN session
    res_open = await process_health_signal(
        session,
        tenant_id=tenant_id,
        project_id=proj.id,
        status="FAIL",
        signal_timestamp=t_fail_1,
        probe_source="uptime_probe",
    )
    await session.flush()

    assert res_open.action == "opened"
    assert res_open.noop is False
    assert res_open.duplicate is False
    assert res_open.session_status == "OPEN"
    assert res_open.project_health_status == "FAILING"
    assert res_open.started_at == t_fail_1
    assert res_open.resolved_at is None
    assert res_open.recovery_time_seconds is None
    assert len(store.failures) == 1
    assert len(store.incidents) == 1
    assert proj.health_status == "FAILING"

    # 2. Duplicate FAIL -> preserved started_at, 0 extra rows
    res_dup = await process_health_signal(
        session,
        tenant_id=tenant_id,
        project_id=proj.id,
        status="FAIL",
        signal_timestamp=t_fail_dup,
        probe_source="uptime_probe",
    )
    await session.flush()

    assert res_dup.action == "duplicate_fail_ignored"
    assert res_dup.noop is True
    assert res_dup.duplicate is True
    assert res_dup.session_id == res_open.session_id
    assert res_dup.started_at == t_fail_1
    assert len(store.failures) == 1
    assert len(store.incidents) == 1
    assert store.failures[0].started_at == t_fail_1
    assert store.failures[0].status == "OPEN"

    # 3. PASS -> RESOLVED with exact 6480.0s recovery_time_seconds
    res_pass = await process_health_signal(
        session,
        tenant_id=tenant_id,
        project_id=proj.id,
        status="PASS",
        signal_timestamp=t_pass,
        probe_source="uptime_probe",
    )
    await session.flush()

    assert res_pass.action == "resolved"
    assert res_pass.noop is False
    assert res_pass.session_status == "RESOLVED"
    assert res_pass.project_health_status == "HEALTHY"
    assert res_pass.session_id == res_open.session_id
    assert res_pass.started_at == t_fail_1
    assert res_pass.resolved_at == t_pass
    assert res_pass.recovery_time_seconds == 6480.0
    assert len(store.failures) == 1
    assert store.failures[0].status == "RESOLVED"
    assert store.failures[0].recovery_time_seconds == 6480.0
    assert len(store.incidents) == 1
    assert store.incidents[0].resolved_at == t_pass
    assert store.incidents[0].recovery_time_seconds == 6480.0
    assert proj.health_status == "HEALTHY"

    # 4. Feed synced sessions into DORA MTTR engine -> 1.80 hours
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    mttr_from_pf = compute_mttr(store.failures, window_days=14, window_end=window_end)
    mttr_from_inc = compute_mttr(store.incidents, window_days=14, window_end=window_end)
    assert mttr_from_pf.resolved_outage_count == 1
    assert mttr_from_pf.mttr_seconds == 6480.0
    assert mttr_from_pf.mttr_hours == 1.80
    assert mttr_from_inc.mttr_hours == 1.80


@pytest.mark.asyncio
async def test_task_68_4_orphan_pass_creates_zero_rows_and_subsequent_pass_is_noop(in_memory_env):
    """
    Task 68.4 AC:
    Orphan PASS signals when no OPEN session exists create 0 rows and act as a clean no-op,
    and a second PASS after resolution does not alter the resolved session.
    """
    store: InMemoryTenantStore = in_memory_env["store"]
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id)
    session = FakeTenantAsyncSession(store, tenant_id)

    # 1. Orphan PASS while healthy
    res_orphan = await process_health_signal(
        session,
        tenant_id=tenant_id,
        project_id=proj.id,
        status="PASS",
        signal_timestamp=datetime(2026, 11, 12, 9, 0, 0, tzinfo=timezone.utc),
    )
    await session.flush()

    assert res_orphan.action == "orphan_pass_ignored"
    assert res_orphan.noop is True
    assert res_orphan.orphan is True
    assert res_orphan.session_id is None
    assert res_orphan.project_health_status == "HEALTHY"
    assert len(store.failures) == 0
    assert len(store.incidents) == 0

    # 2. FAIL -> PASS -> second orphan PASS
    t1 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 12, 10, 30, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 11, 12, 10, 45, 0, tzinfo=timezone.utc)

    await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="FAIL", signal_timestamp=t1
    )
    await session.flush()
    await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="PASS", signal_timestamp=t2
    )
    await session.flush()

    res_extra_pass = await process_health_signal(
        session,
        tenant_id=tenant_id,
        project_id=proj.id,
        status="PASS",
        signal_timestamp=t3,
    )
    await session.flush()

    assert res_extra_pass.action == "orphan_pass_ignored"
    assert res_extra_pass.noop is True
    assert res_extra_pass.orphan is True
    assert len(store.failures) == 1
    assert store.failures[0].resolved_at == t2
    assert store.failures[0].recovery_time_seconds == 1800.0


@pytest.mark.asyncio
async def test_task_68_4_sequential_outage_cycles_and_zero_second_recovery(in_memory_env):
    """Verify multiple sequential FAIL -> PASS sessions and 0-second instantaneous recovery."""
    store: InMemoryTenantStore = in_memory_env["store"]
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id)
    session = FakeTenantAsyncSession(store, tenant_id)

    t1_start = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t1_end = datetime(2026, 11, 12, 10, 30, 0, tzinfo=timezone.utc)
    t2_both = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)

    await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="FAIL", signal_timestamp=t1_start
    )
    await session.flush()
    r1 = await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="PASS", signal_timestamp=t1_end
    )
    await session.flush()

    await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="FAIL", signal_timestamp=t2_both
    )
    await session.flush()
    r2 = await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="PASS", signal_timestamp=t2_both
    )
    await session.flush()

    assert r1.recovery_time_seconds == 1800.0
    assert r2.recovery_time_seconds == 0.0
    assert r2.session_id != r1.session_id
    assert len(store.failures) == 2
    assert len(store.incidents) == 2


@pytest.mark.asyncio
async def test_task_68_4_pass_preceding_started_at_rejected_422(in_memory_env):
    """Verify PASS timestamp earlier than active started_at fails with HTTP 422 without corrupting session."""
    store: InMemoryTenantStore = in_memory_env["store"]
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id)
    session = FakeTenantAsyncSession(store, tenant_id)

    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_bad_pass = datetime(2026, 11, 12, 9, 55, 0, tzinfo=timezone.utc)

    await process_health_signal(
        session, tenant_id=tenant_id, project_id=proj.id, status="FAIL", signal_timestamp=t_fail
    )
    await session.flush()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await process_health_signal(
            session,
            tenant_id=tenant_id,
            project_id=proj.id,
            status="PASS",
            signal_timestamp=t_bad_pass,
        )
    assert exc_info.value.status_code == 422
    assert store.failures[0].status == "OPEN"
    assert store.failures[0].resolved_at is None


# ===========================================================================
# 3. Dual-Endpoint HTTP Auth & Cross-Tenant Security Tests (Task 68.3)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_template",
    [
        "/api/v1/projects/{project_id}/health-signals",
        "/api/v1/webhooks/projects/{project_id}/health",
    ],
)
async def test_task_68_3_authenticated_hmac_health_signals_accepted_both_endpoints(
    in_memory_env,
    endpoint_template: str,
):
    """
    Task 68.3 AC:
    Both POST /api/v1/projects/{project_id}/health-signals and
    POST /api/v1/webhooks/projects/{project_id}/health accept valid HMAC-SHA256
    signatures (HTTP 200) and execute the FAIL -> duplicate FAIL -> PASS cycle.
    """
    store: InMemoryTenantStore = in_memory_env["store"]
    secret = "whsec_prod_probe_hmac_987654"
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id, secret=secret)
    url = endpoint_template.format(project_id=proj.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. FAIL at 10:00 (X-Hub-Signature-256: sha256=<hex>)
        body_fail_1 = json.dumps(
            {
                "status": "FAIL",
                "signal_timestamp": "2026-11-12T10:00:00Z",
                "probe_source": "prometheus_blackbox",
            }
        ).encode("utf-8")
        r1 = await ac.post(
            url,
            content=body_fail_1,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(secret, body_fail_1, prefix=True),
            },
        )
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["action"] == "opened"
        assert d1["session_status"] == "OPEN"
        assert d1["project_health_status"] == "FAILING"

        # 2. Duplicate FAIL at 10:05 (X-Webhook-Signature: <raw hex>)
        body_fail_2 = json.dumps(
            {
                "status": "FAIL",
                "signal_timestamp": "2026-11-12T10:05:00Z",
            }
        ).encode("utf-8")
        r2 = await ac.post(
            url,
            content=body_fail_2,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign_hmac(secret, body_fail_2, prefix=False),
            },
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["action"] == "duplicate_fail_ignored"
        assert d2["noop"] is True
        assert d2["duplicate"] is True
        assert d2["session_id"] == d1["session_id"]
        assert datetime.fromisoformat(d2["started_at"].replace("Z", "+00:00")) == datetime(
            2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc
        )

        # 3. PASS at 11:48 (X-Health-Signature: sha256=<hex>)
        body_pass = json.dumps(
            {
                "status": "PASS",
                "signal_timestamp": "2026-11-12T11:48:00Z",
            }
        ).encode("utf-8")
        r3 = await ac.post(
            url,
            content=body_pass,
            headers={
                "Content-Type": "application/json",
                "X-Health-Signature": _sign_hmac(secret, body_pass, prefix=True),
            },
        )
        assert r3.status_code == 200, r3.text
        d3 = r3.json()
        assert d3["action"] == "resolved"
        assert d3["session_status"] == "RESOLVED"
        assert d3["project_health_status"] == "HEALTHY"
        assert d3["recovery_time_seconds"] == 6480.0


@pytest.mark.asyncio
async def test_task_68_3_token_and_jwt_authentication_accepted(in_memory_env):
    """Verify shared webhook tokens and TENANT_ADMIN RS256 JWT are accepted."""
    store: InMemoryTenantStore = in_memory_env["store"]
    secret = "whsec_token_auth_abc123"
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id, secret=secret)
    admin_jwt = _mint_jwt(
        in_memory_env["private_key"],
        kid=in_memory_env["kid"],
        tenant_id=tenant_id,
        roles=["TENANT_ADMIN"],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. X-Webhook-Secret opens session
        r1 = await ac.post(
            f"/api/v1/webhooks/projects/{proj.id}/health",
            json={"status": "FAIL", "timestamp": "2026-11-12T10:00:00Z"},
            headers={"X-Webhook-Secret": secret},
        )
        assert r1.status_code == 200
        assert r1.json()["action"] == "opened"

        # 2. Authorization: Bearer <webhook_secret> duplicate FAIL no-op
        r2 = await ac.post(
            f"/api/v1/projects/{proj.id}/health-signals",
            json={"status": "FAIL", "timestamp": "2026-11-12T10:10:00Z"},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert r2.status_code == 200
        assert r2.json()["action"] == "duplicate_fail_ignored"

        # 3. TENANT_ADMIN OIDC RS256 JWT resolves session
        r3 = await ac.post(
            f"/api/v1/projects/{proj.id}/health-signals",
            json={"status": "PASS", "timestamp": "2026-11-12T11:48:00Z"},
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert r3.status_code == 200
        assert r3.json()["action"] == "resolved"
        assert r3.json()["recovery_time_seconds"] == 6480.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_template",
    [
        "/api/v1/projects/{project_id}/health-signals",
        "/api/v1/webhooks/projects/{project_id}/health",
    ],
)
async def test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401(
    in_memory_env,
    endpoint_template: str,
):
    """
    Task 68.3 AC & Task 68.5:
    Missing credentials, invalid HMAC signatures, wrong secrets, and tampered bodies
    return HTTP 401 and create 0 rows.
    """
    store: InMemoryTenantStore = in_memory_env["store"]
    secret = "whsec_correct_secret_key"
    tenant_id = uuid.uuid4()
    proj = store.seed_project(tenant_id=tenant_id, secret=secret)
    url = endpoint_template.format(project_id=proj.id)

    payload_bytes = json.dumps(
        {"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}
    ).encode("utf-8")
    tampered_bytes = json.dumps(
        {"status": "FAIL", "signal_timestamp": "2026-11-12T10:05:00Z"}
    ).encode("utf-8")
    admin_jwt = _mint_jwt(
        in_memory_env["private_key"],
        kid=in_memory_env["kid"],
        tenant_id=tenant_id,
        roles=["TENANT_ADMIN"],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. No auth headers at all -> 401
        r_no_auth = await ac.post(
            url, content=payload_bytes, headers={"Content-Type": "application/json"}
        )
        assert r_no_auth.status_code == 401

        # 2. Invalid HMAC signature -> 401
        r_bad_hmac = await ac.post(
            url,
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=" + ("0" * 64),
            },
        )
        assert r_bad_hmac.status_code == 401

        # 3. Signed with wrong secret -> 401
        r_wrong_secret = await ac.post(
            url,
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac("wrong_secret", payload_bytes),
            },
        )
        assert r_wrong_secret.status_code == 401

        # 4. Tampered payload body after valid signature -> 401
        valid_sig_for_original = _sign_hmac(secret, payload_bytes)
        r_tampered = await ac.post(
            url,
            content=tampered_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": valid_sig_for_original,
            },
        )
        assert r_tampered.status_code == 401

        # 5. Wrong bearer secret token -> 401
        r_bad_bearer = await ac.post(
            url,
            content=payload_bytes,
            headers={"Content-Type": "application/json", "Authorization": "Bearer wrong_secret"},
        )
        assert r_bad_bearer.status_code == 401

        # 6. Valid JWT paired with invalid HMAC signature still fails fail-closed with 401
        r_jwt_bad_hmac = await ac.post(
            url,
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_jwt}",
                "X-Hub-Signature-256": "sha256=" + ("f" * 64),
            },
        )
        assert r_jwt_bad_hmac.status_code == 401

    assert len(store.failures) == 0
    assert len(store.incidents) == 0


@pytest.mark.asyncio
async def test_task_68_3_cross_tenant_rbac_and_missing_project_rejected(in_memory_env):
    """
    Task 68.3 AC:
    Cross-tenant calls and unauthorized roles return HTTP 401/403/404 and leave
    foreign tenant projects untouched.
    """
    store: InMemoryTenantStore = in_memory_env["store"]
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    secret_a = "whsec_tenant_a_secret_111"
    secret_b = "whsec_tenant_b_secret_222"

    proj_a = store.seed_project(tenant_id=tenant_a, secret=secret_a)
    proj_b = store.seed_project(tenant_id=tenant_b, secret=secret_b)
    proj_disabled = store.seed_project(tenant_id=tenant_a, secret=secret_a, enabled=False)
    proj_archived = store.seed_project(
        tenant_id=tenant_a,
        secret=secret_a,
        is_active=False,
        deleted_at=datetime.now(timezone.utc),
    )

    jwt_a_admin = _mint_jwt(
        in_memory_env["private_key"],
        kid=in_memory_env["kid"],
        tenant_id=tenant_a,
        roles=["TENANT_ADMIN"],
        sub="admin-a",
    )
    jwt_a_dev = _mint_jwt(
        in_memory_env["private_key"],
        kid=in_memory_env["kid"],
        tenant_id=tenant_a,
        roles=["DEVELOPER"],
        sub="dev-a",
    )

    body = json.dumps({"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}).encode(
        "utf-8"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Tenant A TENANT_ADMIN JWT targeting Tenant B's project -> 404 anti-enumeration
        r_cross_jwt = await ac.post(
            f"/api/v1/projects/{proj_b.id}/health-signals",
            content=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt_a_admin}"},
        )
        assert r_cross_jwt.status_code == 404

        # 2. Tenant A's HMAC secret targeting Tenant B's project -> 401
        r_cross_hmac = await ac.post(
            f"/api/v1/webhooks/projects/{proj_b.id}/health",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(secret_a, body),
            },
        )
        assert r_cross_hmac.status_code == 401

        # 3. Cross-tenant X-Tenant-ID header -> 403
        r_cross_header = await ac.post(
            f"/api/v1/webhooks/projects/{proj_b.id}/health",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Tenant-ID": str(tenant_a),
                "X-Hub-Signature-256": _sign_hmac(secret_b, body),
            },
        )
        assert r_cross_header.status_code == 403

        # 4. DEVELOPER JWT without webhook secret -> 403
        r_dev_403 = await ac.post(
            f"/api/v1/projects/{proj_a.id}/health-signals",
            content=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt_a_dev}"},
        )
        assert r_dev_403.status_code == 403
        assert r_dev_403.json() == FORBIDDEN_RESPONSE

        # 5. Disabled health probe -> 403
        r_disabled = await ac.post(
            f"/api/v1/projects/{proj_disabled.id}/health-signals",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(secret_a, body),
            },
        )
        assert r_disabled.status_code == 403

        # 6. Archived / soft-deleted project -> 404
        r_archived = await ac.post(
            f"/api/v1/projects/{proj_archived.id}/health-signals",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(secret_a, body),
            },
        )
        assert r_archived.status_code == 404

        # 7. Unknown project UUID -> 404
        r_missing = await ac.post(
            f"/api/v1/projects/{uuid.uuid4()}/health-signals",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(secret_a, body),
            },
        )
        assert r_missing.status_code == 404

    assert len(store.failures) == 0
    assert proj_b.health_status == "HEALTHY"


@pytest.mark.asyncio
async def test_task_68_3_health_config_endpoints_and_secret_rotation(in_memory_env):
    """Verify GET/PATCH health-config, secret rotation, and GET health-signals endpoints."""
    store: InMemoryTenantStore = in_memory_env["store"]
    tenant_id = uuid.uuid4()
    old_secret = "whsec_initial_secret_0001"
    proj = store.seed_project(tenant_id=tenant_id, secret=old_secret)

    admin_jwt = _mint_jwt(
        in_memory_env["private_key"],
        kid=in_memory_env["kid"],
        tenant_id=tenant_id,
        roles=["TENANT_ADMIN"],
    )
    dev_jwt = _mint_jwt(
        in_memory_env["private_key"],
        kid=in_memory_env["kid"],
        tenant_id=tenant_id,
        roles=["DEVELOPER"],
        sub="dev-viewer",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. GET health-config returns masked secret (never plaintext)
        r_get = await ac.get(
            f"/api/v1/projects/{proj.id}/health-config",
            headers={"Authorization": f"Bearer {dev_jwt}"},
        )
        assert r_get.status_code == 200
        cfg = r_get.json()
        assert cfg["webhook_secret"] is None
        assert old_secret not in cfg["webhook_secret_masked"]
        assert f"/api/v1/webhooks/projects/{proj.id}/health" in cfg["webhook_url"]
        assert f"/api/v1/projects/{proj.id}/health-signals" in cfg["project_health_signals_url"]

        # 2. DEVELOPER cannot mutate health-config -> 403
        r_dev_patch = await ac.patch(
            f"/api/v1/projects/{proj.id}/health-config",
            json={"health_probe_source": "datadog"},
            headers={"Authorization": f"Bearer {dev_jwt}"},
        )
        assert r_dev_patch.status_code == 403

        # 3. TENANT_ADMIN updates probe source & interval
        r_patch = await ac.patch(
            f"/api/v1/projects/{proj.id}/health-config",
            json={
                "health_probe_source": "datadog",
                "health_probe_url": "https://api.example.com/healthz",
                "health_probe_interval_seconds": 30,
            },
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert r_patch.status_code == 200
        assert r_patch.json()["health_probe_source"] == "datadog"
        assert r_patch.json()["health_probe_interval_seconds"] == 30

        # 4. Rotate HMAC secret -> old secret rejected (401), new secret accepted (200)
        r_rot = await ac.post(
            f"/api/v1/projects/{proj.id}/health-config/rotate-secret",
            headers={"Authorization": f"Bearer {admin_jwt}"},
        )
        assert r_rot.status_code == 200
        new_secret = r_rot.json()["webhook_secret"]
        assert new_secret and new_secret != old_secret

        fail_body = json.dumps(
            {"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}
        ).encode("utf-8")
        r_old_hmac = await ac.post(
            f"/api/v1/webhooks/projects/{proj.id}/health",
            content=fail_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(old_secret, fail_body),
            },
        )
        assert r_old_hmac.status_code == 401

        r_new_hmac = await ac.post(
            f"/api/v1/webhooks/projects/{proj.id}/health",
            content=fail_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign_hmac(new_secret, fail_body),
            },
        )
        assert r_new_hmac.status_code == 200

        # 5. List recorded production failure sessions
        r_list = await ac.get(
            f"/api/v1/projects/{proj.id}/health-signals",
            headers={"Authorization": f"Bearer {dev_jwt}"},
        )
        assert r_list.status_code == 200
        sessions = r_list.json()
        assert len(sessions) == 1
        assert sessions[0]["status"] == "OPEN"


def test_verify_health_webhook_hmac_constant_time():
    """Verify helper checks sha256=<hex> and raw <hex> case-insensitively and rejects invalid."""
    raw = b'{"status":"FAIL"}'
    secret = "my_secret"
    sig = _sign_hmac(secret, raw, prefix=True)
    assert verify_health_webhook_hmac(secret, raw, sig) is True
    assert verify_health_webhook_hmac(secret, raw, sig.replace("sha256=", "")) is True
    assert verify_health_webhook_hmac(secret, raw, sig.upper()) is True
    assert verify_health_webhook_hmac("wrong", raw, sig) is False
    assert verify_health_webhook_hmac(secret, b'{"status":"PASS"}', sig) is False


@pytest.mark.asyncio
async def test_task_68_3_route_lookup_db_outage_returns_503():
    """Database connectivity outages during route lookup fail closed with HTTP 503 + Retry-After."""
    proj_id = uuid.uuid4()
    db_err = DBAPIError("SELECT 1", {}, ConnectionResetError("connection lost"))
    body = json.dumps({"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}).encode(
        "utf-8"
    )
    with mock.patch.object(
        health_service_module, "_lookup_project_health_route", side_effect=db_err
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/webhooks/projects/{proj_id}/health",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign_hmac("whsec_outage_test", body),
                },
            )
    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "30"


# ===========================================================================
# 4. Live PostgreSQL 16 + FORCE RLS End-to-End Integration (CI)
# ===========================================================================


@skip_without_live_db
@pytest.mark.asyncio
async def test_task_68_3_and_68_4_live_postgres_rls_and_mttr_end_to_end():
    """
    Live PostgreSQL 16 verification (runs in CI under eng_app_user FORCE RLS):
    - Creates two isolated tenants and projects via API / tenant DB context.
    - Verifies HMAC authentication, cross-tenant rejection (401/403/404),
      partial unique index enforcement, FAIL -> duplicate FAIL -> PASS -> orphan PASS,
      sync with `incidents`, and live `calculate_rolling_mttr` == 1.80h.
    """
    priv_key, jwk = _generate_rsa_keypair("us68-live-kid")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"keys": [jwk]}, request=req)
    )
    orig_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=transport,
    )

    tenant_a_id: Optional[uuid.UUID] = None
    tenant_b_id: Optional[uuid.UUID] = None
    await async_engine.dispose()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            reg_a = await ac.post(
                "/tenants/register",
                json={
                    "name": f"Health Alpha {uuid.uuid4().hex[:6]}",
                    "slug": f"health-alpha-{uuid.uuid4().hex[:8]}",
                    "admin_email": f"alpha-{uuid.uuid4().hex[:6]}@example.com",
                    "admin_name": "Alpha Admin",
                },
            )
            assert reg_a.status_code == 201, reg_a.text
            tenant_a_id = uuid.UUID(reg_a.json()["tenant_id"])

            reg_b = await ac.post(
                "/tenants/register",
                json={
                    "name": f"Health Beta {uuid.uuid4().hex[:6]}",
                    "slug": f"health-beta-{uuid.uuid4().hex[:8]}",
                    "admin_email": f"beta-{uuid.uuid4().hex[:6]}@example.com",
                    "admin_name": "Beta Admin",
                },
            )
            assert reg_b.status_code == 201, reg_b.text
            tenant_b_id = uuid.UUID(reg_b.json()["tenant_id"])

            jwt_a = _mint_jwt(
                priv_key, kid="us68-live-kid", tenant_id=tenant_a_id, roles=["TENANT_ADMIN"]
            )
            jwt_b = _mint_jwt(
                priv_key, kid="us68-live-kid", tenant_id=tenant_b_id, roles=["TENANT_ADMIN"]
            )

            secret_a = "whsec_live_alpha_secret_123456"
            secret_b = "whsec_live_beta_secret_654321"

            proj_a_resp = await ac.post(
                "/api/v1/projects",
                json={"name": "Live Alpha API", "health_webhook_secret": secret_a},
                headers={"Authorization": f"Bearer {jwt_a}"},
            )
            assert proj_a_resp.status_code == 201, proj_a_resp.text
            proj_a_id = uuid.UUID(proj_a_resp.json()["id"])

            proj_b_resp = await ac.post(
                "/api/v1/projects",
                json={"name": "Live Beta API", "health_webhook_secret": secret_b},
                headers={"Authorization": f"Bearer {jwt_b}"},
            )
            assert proj_b_resp.status_code == 201, proj_b_resp.text
            proj_b_id = uuid.UUID(proj_b_resp.json()["id"])

            # 1. Orphan PASS -> 200 no-op, 0 rows
            orphan_body = json.dumps(
                {"status": "PASS", "signal_timestamp": "2026-11-12T09:50:00Z"}
            ).encode("utf-8")
            r_orphan = await ac.post(
                f"/api/v1/webhooks/projects/{proj_a_id}/health",
                content=orphan_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign_hmac(secret_a, orphan_body),
                },
            )
            assert r_orphan.status_code == 200
            assert r_orphan.json()["orphan"] is True

            # 2. 10:00 FAIL -> 10:05 duplicate FAIL -> 11:48 PASS
            fail1 = json.dumps(
                {"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}
            ).encode("utf-8")
            fail2 = json.dumps(
                {"status": "FAIL", "signal_timestamp": "2026-11-12T10:05:00Z"}
            ).encode("utf-8")
            pass1 = json.dumps(
                {"status": "PASS", "signal_timestamp": "2026-11-12T11:48:00Z"}
            ).encode("utf-8")

            r_f1 = await ac.post(
                f"/api/v1/projects/{proj_a_id}/health-signals",
                content=fail1,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign_hmac(secret_a, fail1),
                },
            )
            assert r_f1.status_code == 200
            assert r_f1.json()["action"] == "opened"

            r_f2 = await ac.post(
                f"/api/v1/webhooks/projects/{proj_a_id}/health",
                content=fail2,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign_hmac(secret_a, fail2),
                },
            )
            assert r_f2.status_code == 200
            assert r_f2.json()["duplicate"] is True
            assert datetime.fromisoformat(
                r_f2.json()["started_at"].replace("Z", "+00:00")
            ) == datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)

            r_p1 = await ac.post(
                f"/api/v1/projects/{proj_a_id}/health-signals",
                content=pass1,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign_hmac(secret_a, pass1),
                },
            )
            assert r_p1.status_code == 200
            assert r_p1.json()["recovery_time_seconds"] == 6480.0

            # 3. Cross-tenant rejections against Tenant B project
            r_cross_jwt = await ac.post(
                f"/api/v1/projects/{proj_b_id}/health-signals",
                content=fail1,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt_a}"},
            )
            assert r_cross_jwt.status_code == 404

            r_cross_hmac = await ac.post(
                f"/api/v1/webhooks/projects/{proj_b_id}/health",
                content=fail1,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign_hmac(secret_a, fail1),
                },
            )
            assert r_cross_hmac.status_code == 401

        # 4. Verify live DB state under Tenant A & Tenant B RLS and calculate_rolling_mttr
        async with get_tenant_db_context(tenant_a_id) as session_a:
            pf_rows = (
                (
                    await session_a.execute(
                        select(ProductionFailure).where(ProductionFailure.project_id == proj_a_id)
                    )
                )
                .scalars()
                .all()
            )
            inc_rows = (
                (await session_a.execute(select(Incident).where(Incident.project_id == proj_a_id)))
                .scalars()
                .all()
            )
            assert len(pf_rows) == 1
            assert len(inc_rows) == 1
            assert pf_rows[0].status == "RESOLVED"
            assert pf_rows[0].recovery_time_seconds == 6480.0
            assert float(inc_rows[0].recovery_time_seconds) == 6480.0

            mttr = await calculate_rolling_mttr(
                session_a,
                tenant_id=tenant_a_id,
                project_id=proj_a_id,
                window_days=14,
                window_end=datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc),
            )
            assert mttr.resolved_outage_count == 1
            assert mttr.mttr_seconds == 6480.0
            assert mttr.mttr_hours == 1.80

        async with get_tenant_db_context(tenant_b_id) as session_b:
            # Tenant B must see 0 rows for Tenant A under FORCE RLS and 0 for itself
            leaked = (await session_b.execute(select(ProductionFailure))).scalars().all()
            assert len(leaked) == 0
    finally:
        app.state.jwks_client = orig_client
        for tid in (tenant_a_id, tenant_b_id):
            if tid is not None:
                try:
                    async with get_tenant_db_context(tid) as cleanup_sess:
                        await cleanup_sess.execute(
                            delete(ProductionFailure).where(ProductionFailure.tenant_id == tid)
                        )
                        await cleanup_sess.execute(
                            delete(Incident).where(Incident.tenant_id == tid)
                        )
                        await cleanup_sess.execute(delete(Project).where(Project.tenant_id == tid))
                        await cleanup_sess.execute(delete(User).where(User.tenant_id == tid))
                        await cleanup_sess.execute(delete(Tenant).where(Tenant.id == tid))
                except Exception:
                    pass
        await async_engine.dispose()
