"""SCRUM-582 (TASK-320-1): SCIP protobuf indexing, ScipIndex persistence, <1.5ms RAM graph & API tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
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

from app.auth.jwks import JWKSClient
from app.config import settings
from app.db.models.models import Commit, Project, Repository, ScipIndex
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.refactoring.scip_codec import (
    ScipCallEdge,
    ScipSymbolRecord,
    decode_scip_index,
    encode_scip_index,
)
from app.refactoring.scip_indexer import INDEXER_VERSION, generate_python_scip_index
from app.refactoring.scip_service import (
    enqueue_scip_index_job,
    ingest_scip_index_for_repository,
    list_repository_scip_statuses,
)
from app.refactoring.symbol_graph import InMemorySymbolGraph, symbol_graph_registry
from workers.worker import QUEUE_DEFAULT, QUEUE_HIGH, dispatch_worker_job


class _FakeAsyncRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

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


async def _provision_tenant(session, *, tenant_id: uuid.UUID, prefix: str) -> uuid.UUID:
    suffix = tenant_id.hex[:8]
    result = await session.execute(
        text(
            """
            SELECT user_id FROM provision_tenant(
                p_name => :name,
                p_slug => :slug,
                p_admin_email => :email,
                p_admin_name => :admin_name,
                p_tenant_id => :tenant_id
            )
            """
        ),
        {
            "name": f"SCIP Test {prefix} {suffix}",
            "slug": f"scip-{prefix}-{suffix}",
            "email": f"admin-scip-{prefix}-{suffix}@manta.local",
            "admin_name": f"SCIP Admin {prefix}",
            "tenant_id": tenant_id,
        },
    )
    return result.scalar_one()


async def _cleanup_tenants(tenant_ids: list[uuid.UUID]) -> None:
    admin_engine = create_async_engine(_get_admin_url(), poolclass=pool.NullPool)
    try:
        async with admin_engine.begin() as conn:
            for tid in tenant_ids:
                await conn.execute(delete(ScipIndex).where(ScipIndex.tenant_id == tid))
                await conn.execute(delete(Commit).where(Commit.tenant_id == tid))
                await conn.execute(delete(Repository).where(Repository.tenant_id == tid))
                await conn.execute(delete(Project).where(Project.tenant_id == tid))
                await conn.execute(
                    text("DELETE FROM user_roles WHERE tenant_id = :tid"),
                    {"tid": tid},
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
        headers={"kid": "scip-test-key", "alg": "RS256"},
    )


def _create_sample_python_repo(base_dir: Path) -> Path:
    pkg_dir = base_dir / "sample_pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "core.py").write_text(
        "def compute(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (pkg_dir / "service.py").write_text(
        "from sample_pkg.core import compute\n\n"
        "class Pipeline:\n"
        "    def run(self, n: int) -> int:\n"
        "        return compute(n)\n",
        encoding="utf-8",
    )
    return base_dir


def test_python_ast_scip_protobuf_roundtrip_and_call_edges(tmp_path: Path) -> None:
    """Verify AST indexer emits valid binary `index.scip` protobuf with accurate call edges."""
    repo_dir = _create_sample_python_repo(tmp_path / "repo")
    sha = "1" * 40
    raw_bytes, symbols_count = generate_python_scip_index(
        repo_dir,
        repo_name="acme/sample",
        commit_sha=sha,
    )

    assert len(raw_bytes) > 0
    assert symbols_count >= 5

    parsed = decode_scip_index(raw_bytes)
    assert parsed.tool_name == "manta-scip-python"
    assert len(parsed.symbols) == symbols_count

    graph = InMemorySymbolGraph.from_parsed_index(parsed, commit_sha=sha)
    compute_sym = f"scip-python manta acme/sample {sha} `sample_pkg.core`/compute()."
    run_sym = f"scip-python manta acme/sample {sha} `sample_pkg.service`/Pipeline#run()."

    callers = graph.get_callers(compute_sym)
    assert [c.symbol for c in callers] == [run_sym]

    callees = graph.get_callees(run_sym)
    assert [c.symbol for c in callees] == [compute_sym]


def test_50k_symbol_ram_graph_lookup_median_under_1_5ms() -> None:
    """SCRUM-582 / TASK-320-1 benchmark: 50,000-symbol RAM graph caller lookups < 1.5 ms median."""
    num_symbols = 50_000
    symbols = [
        ScipSymbolRecord(
            symbol=f"scip-python manta bench deadbeef `pkg.m{i // 250}`/fn_{i}().",
            display_name=f"fn_{i}",
            file_path=f"pkg/m{i // 250}.py",
            line=(i % 250) + 1,
            kind="function",
        )
        for i in range(num_symbols)
    ]
    edges = [
        ScipCallEdge(
            caller=symbols[i].symbol,
            callee=symbols[i % 100].symbol,
        )
        for i in range(100, num_symbols)
    ]

    raw_bytes = encode_scip_index(symbols, edges)
    graph = InMemorySymbolGraph.from_protobuf_bytes(raw_bytes, commit_sha="deadbeef")
    assert graph.symbol_count == num_symbols

    # Warm-up one call then measure 200 lookups across different hub nodes
    _ = graph.get_callers(symbols[0].symbol, limit=50)
    latencies_ms: list[float] = []
    for i in range(200):
        target = symbols[i % 100].symbol
        t0 = time.perf_counter_ns()
        callers = graph.get_callers(target, limit=50)
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        assert len(callers) == 50
        latencies_ms.append(elapsed_ms)

    median_ms = statistics.median(latencies_ms)
    assert median_ms < 1.5, f"Expected median caller lookup < 1.5 ms, got {median_ms:.4f} ms"


@pytest.mark.asyncio
async def test_scip_persistence_freshness_worker_dispatch_and_rls_isolation(
    tmp_path: Path,
) -> None:
    """Verify PostgreSQL ScipIndex upsert, FRESH/STALE status, worker dispatch, and RLS + RAM isolation."""
    symbol_graph_registry.clear()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    repo_dir = _create_sample_python_repo(tmp_path / "worker_repo")
    storage_dir = tmp_path / "scip_storage"
    sha1 = "a" * 40
    sha2 = "b" * 40

    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_tenant(session, tenant_id=tenant_a, prefix="a")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="SCIP Project A",
                slug=f"scip-proj-a-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            repo_a = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=int(tenant_a.int % 1_000_000_000),
                full_name="acme/scip-repo-a",
            )
            session.add(repo_a)
            await session.flush()

            session.add(
                Commit(
                    sha=sha1,
                    tenant_id=tenant_a,
                    repository_id=repo_a.id,
                    author_pseudonym_id="pseudo-1",
                    authored_at=datetime.now(timezone.utc),
                    committed_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                )
            )

        async with get_tenant_db_context(tenant_b) as session:
            await _provision_tenant(session, tenant_id=tenant_b, prefix="b")

        # 1. Missing before ingestion
        async with get_tenant_db_context(tenant_a) as session:
            statuses = await list_repository_scip_statuses(session, tenant_id=tenant_a)
            assert len(statuses) == 1
            assert statuses[0]["freshness"] == "MISSING"
            assert statuses[0]["graph_loaded_in_ram"] is False

        # 2. Enqueue onto high queue + execute via worker dispatcher
        fake_redis = _FakeAsyncRedis()
        enq_high = await enqueue_scip_index_job(
            tenant_id=tenant_a,
            repository_id=repo_a.id,
            commit_sha=sha1,
            priority="high",
            repo_path=str(repo_dir),
            redis_client=fake_redis,
        )
        assert enq_high["queue"] == QUEUE_HIGH
        assert len(fake_redis.lists[QUEUE_HIGH]) == 1
        assert QUEUE_DEFAULT not in fake_redis.lists

        old_storage_dir = settings.SCIP_STORAGE_DIR
        settings.SCIP_STORAGE_DIR = str(storage_dir)
        try:
            job_envelope = json.loads(fake_redis.lists[QUEUE_HIGH][0])
            worker_res = await dispatch_worker_job(job_envelope)
            assert worker_res["status"] == "indexed"
            assert worker_res["commit_sha"] == sha1
            assert worker_res["symbols_count"] >= 5
        finally:
            settings.SCIP_STORAGE_DIR = old_storage_dir

        # 3. Verify FRESH + RAM loaded + idempotent upsert
        async with get_tenant_db_context(tenant_a) as session:
            statuses = await list_repository_scip_statuses(session, tenant_id=tenant_a)
            assert statuses[0]["freshness"] == "FRESH"
            assert statuses[0]["commit_sha"] == sha1
            assert statuses[0]["indexer_version"] == INDEXER_VERSION
            assert statuses[0]["graph_loaded_in_ram"] is True

        await ingest_scip_index_for_repository(
            tenant_id=tenant_a,
            repository_id=repo_a.id,
            commit_sha=sha1,
            repo_path=repo_dir,
            repo_name="acme/scip-repo-a",
            storage_dir=str(storage_dir),
        )
        async with get_tenant_db_context(tenant_a) as session:
            rows = (
                await session.scalars(
                    select(ScipIndex).where(ScipIndex.repository_id == repo_a.id)
                )
            ).all()
            assert len(rows) == 1

        # 4. Newer commit turns status to STALE until re-indexed
        async with get_tenant_db_context(tenant_a) as session:
            session.add(
                Commit(
                    sha=sha2,
                    tenant_id=tenant_a,
                    repository_id=repo_a.id,
                    author_pseudonym_id="pseudo-1",
                    authored_at=datetime.now(timezone.utc),
                    committed_at=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
                )
            )

        async with get_tenant_db_context(tenant_a) as session:
            statuses = await list_repository_scip_statuses(session, tenant_id=tenant_a)
            assert statuses[0]["freshness"] == "STALE"
            assert statuses[0]["commit_sha"] == sha1
            assert statuses[0]["latest_commit_sha"] == sha2

        # 5. Tenant B RLS + RAM registry isolation
        async with get_tenant_db_context(tenant_b) as session:
            assert (await session.scalars(select(ScipIndex))).all() == []
            assert await list_repository_scip_statuses(session, tenant_id=tenant_b) == []

        assert (
            symbol_graph_registry.get(
                tenant_id=tenant_b,
                repository_id=repo_a.id,
                commit_sha=sha1,
            )
            is None
        )
    finally:
        symbol_graph_registry.clear()
        await _cleanup_tenants([tenant_a, tenant_b])
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_refactoring_scip_api_endpoints_and_rbac(tmp_path: Path) -> None:
    """Verify REST endpoints for SCIP status, trigger, graph slice, caller lookup & RBAC."""
    symbol_graph_registry.clear()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    repo_dir = _create_sample_python_repo(tmp_path / "api_repo")
    storage_dir = tmp_path / "api_scip_storage"
    sha = "c" * 40

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "scip-test-key", "kty": "RSA", "alg": "RS256", "use": "sig"})
    idp = _MockJWKSServer(jwk)
    orig_jwks = getattr(app.state, "jwks_client", None)
    orig_redis = getattr(app.state, "scip_redis", None)
    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(idp.handle_request),
    )
    fake_redis = _FakeAsyncRedis()
    app.state.scip_redis = fake_redis

    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_tenant(session, tenant_id=tenant_a, prefix="apia")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="API SCIP Project",
                slug=f"api-scip-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            repo_a = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=int(tenant_a.int % 1_000_000_000),
                full_name="acme/api-repo",
            )
            session.add(repo_a)
            await session.flush()

        async with get_tenant_db_context(tenant_b) as session:
            user_b = await _provision_tenant(session, tenant_id=tenant_b, prefix="apib")

        await ingest_scip_index_for_repository(
            tenant_id=tenant_a,
            repository_id=repo_a.id,
            commit_sha=sha,
            repo_path=repo_dir,
            repo_name="acme/api-repo",
            storage_dir=str(storage_dir),
        )

        dev_token = _create_jwt(
            private_key, tenant_id=tenant_a, user_id=user_a, role="DEVELOPER"
        )
        sm_token = _create_jwt(
            private_key, tenant_id=tenant_a, user_id=user_a, role="SCRUM_MASTER"
        )
        tenant_b_token = _create_jwt(
            private_key, tenant_id=tenant_b, user_id=user_b, role="DEVELOPER"
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # 1. GET /scip-indexes
            res_list = await client.get(
                "/api/v1/refactoring/scip-indexes",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert res_list.status_code == 200, res_list.text
            body_list = res_list.json()
            assert body_list["total"] == 1
            assert body_list["repositories"][0]["commit_sha"] == sha
            assert body_list["repositories"][0]["freshness"] == "FRESH"
            assert body_list["repositories"][0]["graph_loaded_in_ram"] is True

            # 2. POST /repositories/{id}/scip-index
            res_trig = await client.post(
                f"/api/v1/refactoring/repositories/{repo_a.id}/scip-index",
                headers={"Authorization": f"Bearer {dev_token}"},
                json={"commit_sha": sha, "priority": "default"},
            )
            assert res_trig.status_code == 202, res_trig.text
            assert res_trig.json()["queue"] == QUEUE_DEFAULT
            assert len(fake_redis.lists[QUEUE_DEFAULT]) == 1

            # 3. GET /projects/{id}/scip-graph
            res_graph = await client.get(
                f"/api/v1/refactoring/projects/{proj_a.id}/scip-graph",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert res_graph.status_code == 200, res_graph.text
            graph_body = res_graph.json()
            assert graph_body["commit_sha"] == sha
            assert graph_body["freshness"] == "FRESH"
            assert len(graph_body["nodes"]) >= 5
            assert len(graph_body["edges"]) >= 1

            # 4. GET /repositories/{id}/scip-index/callers
            res_callers = await client.get(
                f"/api/v1/refactoring/repositories/{repo_a.id}/scip-index/callers",
                params={"symbol": "compute"},
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert res_callers.status_code == 200, res_callers.text
            callers_body = res_callers.json()
            assert callers_body["node"]["display_name"] == "compute"
            assert [c["display_name"] for c in callers_body["callers"]] == ["run"]
            assert callers_body["lookup_ms"] < 5.0

            # 5. SCRUM_MASTER gets 403 Forbidden
            for method, url in (
                ("GET", "/api/v1/refactoring/scip-indexes"),
                ("POST", f"/api/v1/refactoring/repositories/{repo_a.id}/scip-index"),
                ("GET", f"/api/v1/refactoring/projects/{proj_a.id}/scip-graph"),
                (
                    "GET",
                    f"/api/v1/refactoring/repositories/{repo_a.id}/scip-index/callers?symbol=compute",
                ),
            ):
                resp = await client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {sm_token}"},
                )
                assert resp.status_code == 403, f"Expected 403 on {method} {url}, got {resp.status_code}"

            # 6. Cross-tenant access returns 404
            resp_cross = await client.get(
                f"/api/v1/refactoring/projects/{proj_a.id}/scip-graph",
                headers={"Authorization": f"Bearer {tenant_b_token}"},
            )
            assert resp_cross.status_code == 404
    finally:
        app.state.jwks_client = orig_jwks
        app.state.scip_redis = orig_redis
        symbol_graph_registry.clear()
        await _cleanup_tenants([tenant_a, tenant_b])
        await async_engine.dispose()
