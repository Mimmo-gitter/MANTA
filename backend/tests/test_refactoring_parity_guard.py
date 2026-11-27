"""SCRUM-585 (TASK-321-1 / STORY-321 / US-47): SCIP SHA parity guard & arq:queue:high trigger tests."""

from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
from unittest import mock
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
from app.db.models.models import CodeAnalysis, Commit, Project, PullRequest, Repository, ScipIndex
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.refactoring.scip_indexer import generate_python_scip_index, persist_scip_artifact
from app.refactoring.scip_service import (
    RepositoryScipParity,
    execute_refactoring_analysis,
    ingest_scip_index_for_repository,
    upsert_scip_index,
)
from app.refactoring.symbol_graph import InMemorySymbolGraph, symbol_graph_registry
from workers.worker import (
    QUEUE_DEFAULT,
    QUEUE_HIGH,
    ack_job,
    dispatch_worker_job,
    enqueue_job,
    fetch_next_job,
    serialize_job_payload,
)


class _InMemoryQueueRedis:
    """In-memory async Redis supporting RPUSH, LLEN, LMOVE, BLMOVE, and LREM for priority tests."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)

    async def rpush(self, key: str, *values: str) -> int:
        for val in values:
            self.lists[key].append(str(val))
        return len(self.lists[key])

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def lmove(
        self,
        source: str,
        destination: str,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        src_list = self.lists.get(source, [])
        if not src_list:
            return None
        item = src_list.pop(0) if src.upper() == "LEFT" else src_list.pop()
        if dest.upper() == "RIGHT":
            self.lists[destination].append(item)
        else:
            self.lists[destination].insert(0, item)
        return item

    async def blmove(
        self,
        source: str,
        destination: str,
        timeout: float = 0.0,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        return await self.lmove(source, destination, src=src, dest=dest)

    async def lrem(self, key: str, count: int, value: str) -> int:
        items = self.lists.get(key, [])
        removed = 0
        kept: list[str] = []
        for item in items:
            if item == value and (count == 0 or removed < abs(count)):
                removed += 1
                continue
            kept.append(item)
        self.lists[key] = kept
        return removed


class _FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[object]:
        return list(self._rows)


class _InMemoryTenantSession:
    """Minimal async SQLAlchemy-like session for zero-DB unit tests of router & scip_service."""

    def __init__(
        self,
        *,
        projects: list[SimpleNamespace],
        repositories: list[SimpleNamespace],
        commits: list[SimpleNamespace],
        scip_indexes: list[SimpleNamespace],
        pull_requests: list[SimpleNamespace] | None = None,
        code_analyses: list[CodeAnalysis] | None = None,
    ) -> None:
        self.projects = projects
        self.repositories = repositories
        self.commits = commits
        self.scip_indexes = scip_indexes
        self.pull_requests = pull_requests or []
        self.code_analyses = code_analyses if code_analyses is not None else []

    def add(self, obj: object) -> None:
        if isinstance(obj, CodeAnalysis):
            self.code_analyses.append(obj)

    async def flush(self) -> None:
        return None

    async def scalars(self, stmt: object) -> _FakeScalarResult:
        sql = str(stmt)
        params = stmt.compile().params  # type: ignore[attr-defined]
        tenant_id = next((v for k, v in params.items() if "tenant_id" in k), None)

        if "FROM projects" in sql:
            project_id = next(
                (v for k, v in params.items() if "id" in k and "tenant" not in k), None
            )
            rows = [
                p
                for p in self.projects
                if (tenant_id is None or p.tenant_id == tenant_id)
                and (project_id is None or p.id == project_id)
            ]
            return _FakeScalarResult(rows)

        if "FROM repositories" in sql:
            project_id = next((v for k, v in params.items() if "project_id" in k), None)
            repo_id = next(
                (v for k, v in params.items() if k.startswith("id_") or k == "id"),
                None,
            )
            rows = [
                r
                for r in self.repositories
                if (tenant_id is None or r.tenant_id == tenant_id)
                and (project_id is None or r.project_id == project_id)
                and (repo_id is None or r.id == repo_id)
            ]
            rows.sort(key=lambda r: r.full_name)
            return _FakeScalarResult(rows)

        if "FROM commits" in sql:
            repo_id = next((v for k, v in params.items() if "repository_id" in k), None)
            rows = [
                c
                for c in self.commits
                if (tenant_id is None or c.tenant_id == tenant_id)
                and (repo_id is None or c.repository_id == repo_id)
            ]
            rows.sort(key=lambda c: (c.committed_at, c.created_at), reverse=True)
            return _FakeScalarResult([c.sha for c in rows])

        if "FROM pull_requests" in sql:
            repo_id = next((v for k, v in params.items() if "repository_id" in k), None)
            pr_id = next((v for k, v in params.items() if k.startswith("id_") or k == "id"), None)
            rows = [
                pr
                for pr in self.pull_requests
                if (tenant_id is None or pr.tenant_id == tenant_id)
                and (repo_id is None or pr.repository_id == repo_id)
                and (pr_id is None or pr.id == pr_id)
            ]
            rows.sort(key=lambda pr: (pr.updated_at, pr.created_at), reverse=True)
            if "SELECT pull_requests.head_sha" in sql:
                return _FakeScalarResult([pr.head_sha for pr in rows])
            return _FakeScalarResult(rows)

        if "FROM scip_indexes" in sql:
            repo_id = next((v for k, v in params.items() if "repository_id" in k), None)
            lower_sha = next((v for k, v in params.items() if "lower" in k), None)
            rows = [
                s
                for s in self.scip_indexes
                if (tenant_id is None or s.tenant_id == tenant_id)
                and (repo_id is None or s.repository_id == repo_id)
                and (lower_sha is None or s.commit_sha.strip().lower() == lower_sha)
            ]
            rows.sort(key=lambda s: s.created_at, reverse=True)
            return _FakeScalarResult(rows)

        return _FakeScalarResult([])


class _MockJWKSServer:
    def __init__(self, jwk: dict[str, object]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]}, request=request)


def _new_sha() -> str:
    return f"{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"


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
            "name": f"Parity Test {prefix} {suffix}",
            "slug": f"parity-{prefix}-{suffix}",
            "email": f"admin-parity-{prefix}-{suffix}@manta.local",
            "admin_name": f"Parity Admin {prefix}",
            "tenant_id": tenant_id,
        },
    )
    return result.scalar_one()


async def _cleanup_tenants(tenant_ids: list[uuid.UUID]) -> None:
    admin_engine = create_async_engine(_get_admin_url(), poolclass=pool.NullPool)
    try:
        async with admin_engine.begin() as conn:
            for tid in tenant_ids:
                await conn.execute(delete(CodeAnalysis).where(CodeAnalysis.tenant_id == tid))
                await conn.execute(delete(ScipIndex).where(ScipIndex.tenant_id == tid))
                await conn.execute(delete(Commit).where(Commit.tenant_id == tid))
                await conn.execute(delete(PullRequest).where(PullRequest.tenant_id == tid))
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


def _setup_auth_and_redis(fake_redis: _InMemoryQueueRedis):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "parity-test-key", "kty": "RSA", "alg": "RS256", "use": "sig"})
    idp = _MockJWKSServer(jwk)
    orig_jwks = getattr(app.state, "jwks_client", None)
    orig_redis = getattr(app.state, "scip_redis", None)
    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(idp.handle_request),
    )
    app.state.scip_redis = fake_redis
    return private_key, orig_jwks, orig_redis


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
        headers={"kid": "parity-test-key", "alg": "RS256"},
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


# ==============================================================================
# Zero-DB In-Process Unit & FastAPI Router Verification Tests (TASK-321-1)
# ==============================================================================


@pytest.mark.asyncio
async def test_unit_stale_scip_parity_guard_returns_409_skips_analysis_and_preempts_default_queue(
    tmp_path: Path,
) -> None:
    """
    In-process end-to-end verification of TASK-321-1 without external PostgreSQL:
    - Exercises real FastAPI POST /api/v1/refactoring/projects/{id}/analyze
    - Exercises real evaluate_project_scip_parity & parity guard in refactoring/router.py
    - Proves stale SCIP index returns HTTP 409 without executing analysis
    - Proves high-priority scip.index job preempts waiting arq:queue:default backlog via fetch_next_job
    - Proves subsequent call succeeds (HTTP 200) once ScipIndex.commit_sha matches head_sha
    """
    symbol_graph_registry.clear()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    sha1 = _new_sha()
    sha2 = _new_sha()

    repo_dir = _create_sample_python_repo(tmp_path / "unit_repo")
    storage_dir = tmp_path / "unit_scip_storage"
    fake_redis = _InMemoryQueueRedis()
    private_key, orig_jwks, orig_redis = _setup_auth_and_redis(fake_redis)

    code_analyses: list[CodeAnalysis] = []
    scip_indexes: list[SimpleNamespace] = [
        SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            repository_id=repo_id,
            commit_sha=sha1,
            scip_protobuf_storage_uri="s3://manta/old.scip",
            symbols_count=6,
            created_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
        )
    ]
    commits = [
        SimpleNamespace(
            sha=sha1,
            tenant_id=tenant_id,
            repository_id=repo_id,
            committed_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            sha=sha2,
            tenant_id=tenant_id,
            repository_id=repo_id,
            committed_at=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
        ),
    ]
    projects = [SimpleNamespace(id=project_id, tenant_id=tenant_id, name="Unit Parity Project")]
    repositories = [
        SimpleNamespace(
            id=repo_id,
            tenant_id=tenant_id,
            project_id=project_id,
            full_name="acme/unit-parity-repo",
        )
    ]

    @asynccontextmanager
    async def _fake_db_ctx(_tid: uuid.UUID):
        yield _InMemoryTenantSession(
            projects=projects,
            repositories=repositories,
            commits=commits,
            scip_indexes=scip_indexes,
            code_analyses=code_analyses,
        )

    try:
        # Seed 5 default-priority jobs ahead of time to test high-priority preemption
        for idx in range(5):
            env = serialize_job_payload(
                job_id=f"unit-default-{idx}",
                tenant_id=str(tenant_id),
                priority="default",
                job_type="webhook.process",
                payload={"idx": idx},
                provider="github",
            )
            await enqueue_job(fake_redis, env, priority="default")
        assert await fake_redis.llen(QUEUE_DEFAULT) == 5

        dev_token = _create_jwt(private_key, tenant_id=tenant_id, user_id=user_id, role="DEVELOPER")

        with (
            mock.patch("app.refactoring.router.get_tenant_db_context", side_effect=_fake_db_ctx),
            mock.patch(
                "app.refactoring.scip_service.get_tenant_db_context", side_effect=_fake_db_ctx
            ),
            mock.patch(
                "app.refactoring.router.execute_refactoring_analysis",
                wraps=execute_refactoring_analysis,
            ) as exec_spy,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # 1. Stale SCIP index -> HTTP 409, zero analysis execution, high-priority enqueue
                res_stale = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"repo_path": str(repo_dir)},
                )
                assert res_stale.status_code == 409, res_stale.text
                detail = res_stale.json()["detail"]
                assert detail["code"] == "SCIP_INDEX_STALE"
                assert detail["repository_head_sha"] == sha2
                assert detail["scip_index_metadata_sha"] == sha1
                assert detail["queue"] == QUEUE_HIGH
                assert detail["reindex_job_id"]
                assert exec_spy.call_count == 0
                assert code_analyses == []

                # 2. High-priority job is dequeued FIRST ahead of all 5 arq:queue:default jobs
                fetched = await fetch_next_job(fake_redis, "unit-worker-1", poll_timeout=0.0)
                assert fetched is not None
                source_queue, raw_job, parsed = fetched
                assert source_queue == QUEUE_HIGH
                assert parsed["job_id"] == detail["reindex_job_id"]
                assert parsed["job_type"] == "scip.index"
                assert parsed["priority"] == "high"
                assert parsed["payload"]["commit_sha"] == sha2
                assert parsed["payload"]["repository_id"] == str(repo_id)
                assert parsed["payload"]["reason"] == "stale_scip_parity_guard"
                assert await fake_redis.llen(QUEUE_DEFAULT) == 5
                assert await ack_job(fake_redis, "unit-worker-1", raw_job) == 1

                # 3. Simulate worker indexing sha2 (real AST -> SCIP protobuf + RAM hydration)
                raw_bytes, sym_count = generate_python_scip_index(
                    repo_dir, repo_name="acme/unit-parity-repo", commit_sha=sha2
                )
                storage_uri = persist_scip_artifact(
                    raw_bytes,
                    tenant_id=tenant_id,
                    repository_id=repo_id,
                    commit_sha=sha2,
                    storage_dir=str(storage_dir),
                )
                symbol_graph_registry.put(
                    tenant_id=tenant_id,
                    repository_id=repo_id,
                    commit_sha=sha2,
                    graph=InMemorySymbolGraph.from_protobuf_bytes(raw_bytes, commit_sha=sha2),
                )
                scip_indexes.append(
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        repository_id=repo_id,
                        commit_sha=sha2,
                        scip_protobuf_storage_uri=storage_uri,
                        symbols_count=sym_count,
                        created_at=datetime(2026, 11, 26, 12, 5, tzinfo=timezone.utc),
                    )
                )

                # 4. Retry analyze -> succeeds (HTTP 200) once ScipIndex.commit_sha == head_sha
                res_ok = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"target_symbol": "compute"},
                )
                assert res_ok.status_code == 200, res_ok.text
                body = res_ok.json()
                assert body["parity_verified"] is True
                assert body["status"] == "completed"
                assert body["commit_sha"] == sha2
                assert body["repository_head_sha"] == sha2
                assert body["scip_index_metadata_sha"] == sha2
                assert body["graph_loaded_in_ram"] is True
                assert body["symbols_count"] >= 5
                assert body["edges_count"] >= 1
                assert body["callers_count"] == 1
                assert exec_spy.call_count == 1
                assert len(code_analyses) == 1
                assert code_analyses[0].commit_sha == sha2
                assert await fake_redis.llen(QUEUE_HIGH) == 0
    finally:
        app.state.jwks_client = orig_jwks
        app.state.scip_redis = orig_redis
        symbol_graph_registry.clear()


@pytest.mark.asyncio
async def test_unit_missing_scip_index_aborts_409_and_multi_repo_overrides() -> None:
    """Verify MISSING index 409, multi-repo selective enqueue, explicit head_sha override & 422/404."""
    symbol_graph_registry.clear()
    tenant_id = uuid.uuid4()
    other_tenant = uuid.uuid4()
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    repo_fresh_id = uuid.uuid4()
    repo_missing_id = uuid.uuid4()
    sha1 = _new_sha()
    sha2 = _new_sha()
    sha3 = _new_sha()

    fake_redis = _InMemoryQueueRedis()
    private_key, orig_jwks, orig_redis = _setup_auth_and_redis(fake_redis)

    code_analyses: list[CodeAnalysis] = []
    projects = [SimpleNamespace(id=project_id, tenant_id=tenant_id, name="Unit Multi Project")]
    repositories = [
        SimpleNamespace(
            id=repo_fresh_id,
            tenant_id=tenant_id,
            project_id=project_id,
            full_name="acme/alpha-fresh",
        ),
        SimpleNamespace(
            id=repo_missing_id,
            tenant_id=tenant_id,
            project_id=project_id,
            full_name="acme/beta-missing",
        ),
    ]
    commits = [
        SimpleNamespace(
            sha=sha1,
            tenant_id=tenant_id,
            repository_id=repo_fresh_id,
            committed_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            sha=sha2,
            tenant_id=tenant_id,
            repository_id=repo_missing_id,
            committed_at=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    scip_indexes = [
        SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            repository_id=repo_fresh_id,
            commit_sha=sha1,
            scip_protobuf_storage_uri="s3://manta/fresh.scip",
            symbols_count=15,
            created_at=datetime(2026, 11, 25, 10, 5, tzinfo=timezone.utc),
        )
    ]

    @asynccontextmanager
    async def _fake_db_ctx(_tid: uuid.UUID):
        yield _InMemoryTenantSession(
            projects=projects,
            repositories=repositories,
            commits=commits,
            scip_indexes=scip_indexes,
            code_analyses=code_analyses,
        )

    try:
        dev_token = _create_jwt(private_key, tenant_id=tenant_id, user_id=user_id, role="DEVELOPER")
        pm_token = _create_jwt(
            private_key, tenant_id=tenant_id, user_id=user_id, role="PROJECT_MANAGER"
        )
        other_token = _create_jwt(
            private_key, tenant_id=other_tenant, user_id=uuid.uuid4(), role="DEVELOPER"
        )

        with (
            mock.patch("app.refactoring.router.get_tenant_db_context", side_effect=_fake_db_ctx),
            mock.patch(
                "app.refactoring.scip_service.get_tenant_db_context", side_effect=_fake_db_ctx
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # 1. Whole project -> 409 because repo_missing has no SCIP index; only repo_missing enqueued
                res_proj = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                )
                assert res_proj.status_code == 409, res_proj.text
                detail = res_proj.json()["detail"]
                assert detail["repository_id"] == str(repo_missing_id)
                assert detail["scip_index_metadata_sha"] is None
                assert detail["stale_repositories"][0]["freshness"] == "MISSING"
                assert len(detail["reindex_jobs"]) == 1
                assert detail["reindex_jobs"][0]["commit_sha"] == sha2
                assert code_analyses == []
                assert await fake_redis.llen(QUEUE_HIGH) == 1

                # 2. Target fresh repo directly -> 200 OK (metadata-only fallback)
                res_fresh = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"repository_id": str(repo_fresh_id)},
                )
                assert res_fresh.status_code == 200, res_fresh.text
                assert res_fresh.json()["commit_sha"] == sha1
                assert res_fresh.json()["symbols_count"] == 15
                assert res_fresh.json()["graph_loaded_in_ram"] is False
                assert len(code_analyses) == 1

                # 3. Explicit mismatched uppercase head_sha -> 409 + normalized lowercase sha3 enqueued
                res_override = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"repository_id": str(repo_fresh_id), "head_sha": sha3.upper()},
                )
                assert res_override.status_code == 409, res_override.text
                assert res_override.json()["detail"]["repository_head_sha"] == sha3
                assert await fake_redis.llen(QUEUE_HIGH) == 2

                # 4. Conflicting query vs body repository_id -> 422
                res_422 = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    params={"repository_id": str(repo_fresh_id)},
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"repository_id": str(repo_missing_id)},
                )
                assert res_422.status_code == 422

                # 5. Unauthorized role -> 403 without enqueueing
                res_403 = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {pm_token}"},
                )
                assert res_403.status_code == 403
                assert await fake_redis.llen(QUEUE_HIGH) == 2

                # 6. Cross-tenant project lookup -> 404 without enqueueing
                res_404 = await ac.post(
                    f"/api/v1/refactoring/projects/{project_id}/analyze",
                    headers={"Authorization": f"Bearer {other_token}"},
                )
                assert res_404.status_code == 404
                assert await fake_redis.llen(QUEUE_HIGH) == 2

        # 7. Defense-in-depth: execute_refactoring_analysis refuses stale parity directly
        stale_entry = RepositoryScipParity(
            repository_id=repo_fresh_id,
            project_id=project_id,
            full_name="acme/alpha-fresh",
            repository_head_sha=sha2,
            scip_index_metadata_sha=sha1,
            scip_index_id=uuid.uuid4(),
            scip_storage_uri=None,
            symbols_count=15,
            freshness="STALE",
        )
        with pytest.raises(RuntimeError, match="SCIP parity invariant violated"):
            await execute_refactoring_analysis(
                tenant_id=tenant_id,
                project_id=project_id,
                parity_entries=[stale_entry],
            )
    finally:
        app.state.jwks_client = orig_jwks
        app.state.scip_redis = orig_redis
        symbol_graph_registry.clear()


@pytest.mark.asyncio
async def test_unit_analyze_openapi_schema_declares_409_and_non_stub() -> None:
    """Verify POST /api/v1/refactoring/projects/{project_id}/analyze is no longer a stub and documents 409."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/openapi.json")
    assert resp.status_code == 200
    op = resp.json()["paths"]["/api/v1/refactoring/projects/{project_id}/analyze"]["post"]
    assert "Stubs" not in op.get("tags", [])
    assert op.get("x-stub") is None
    assert "200" in op["responses"]
    assert "409" in op["responses"]


# ==============================================================================
# Full PostgreSQL RLS + Valkey Queue Integration Tests (CI / Docker PostgreSQL)
# ==============================================================================


@pytest.mark.asyncio
async def test_stale_scip_index_aborts_409_preempts_default_queue_and_succeeds_after_reindex(
    tmp_path: Path,
) -> None:
    """
    TASK-321-1 end-to-end acceptance criteria verification against real PostgreSQL RLS:
    1. Stale SCIP index returns HTTP 409 without executing analysis (0 CodeAnalysis rows, spy not called)
       and enqueues a high-priority scip.index job on arq:queue:high ahead of arq:queue:default.
    2. Worker fetches the high-priority re-index job FIRST ahead of queued arq:queue:default backlog
       and updates ScipIndex.commit_sha to match repository head_sha.
    3. Subsequent analyze call succeeds (HTTP 200) once ScipIndex.commit_sha == head_sha.
    """
    symbol_graph_registry.clear()
    tenant_a = uuid.uuid4()
    repo_dir = _create_sample_python_repo(tmp_path / "parity_repo")
    storage_dir = tmp_path / "parity_scip_storage"
    sha1 = _new_sha()
    sha2 = _new_sha()

    fake_redis = _InMemoryQueueRedis()
    private_key, orig_jwks, orig_redis = _setup_auth_and_redis(fake_redis)
    old_storage_dir = settings.SCIP_STORAGE_DIR
    settings.SCIP_STORAGE_DIR = str(storage_dir)

    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_tenant(session, tenant_id=tenant_a, prefix="stale")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="Parity Guard Project",
                slug=f"parity-proj-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            repo_a = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=int(tenant_a.int % 1_000_000_000),
                full_name="acme/parity-repo",
            )
            session.add(repo_a)
            await session.flush()

            session.add(
                Commit(
                    sha=sha1,
                    tenant_id=tenant_a,
                    repository_id=repo_a.id,
                    author_pseudonym_id="pseudo-1",
                    authored_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                    committed_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                )
            )

        # Build SCIP index at sha1
        await ingest_scip_index_for_repository(
            tenant_id=tenant_a,
            repository_id=repo_a.id,
            commit_sha=sha1,
            repo_path=repo_dir,
            repo_name="acme/parity-repo",
            storage_dir=str(storage_dir),
        )

        # Advance repository HEAD to sha2 so ScipIndex(sha1) becomes stale
        async with get_tenant_db_context(tenant_a) as session:
            session.add(
                Commit(
                    sha=sha2,
                    tenant_id=tenant_a,
                    repository_id=repo_a.id,
                    author_pseudonym_id="pseudo-1",
                    authored_at=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
                    committed_at=datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc),
                )
            )

        # Pre-seed 5 backlog jobs on arq:queue:default to prove high-priority preemption
        for idx in range(5):
            default_envelope = serialize_job_payload(
                job_id=f"default-backlog-{idx}",
                tenant_id=str(tenant_a),
                priority="default",
                job_type="webhook.process",
                payload={"seq": idx},
                provider="github",
            )
            await enqueue_job(fake_redis, default_envelope, priority="default")
        assert await fake_redis.llen(QUEUE_DEFAULT) == 5
        assert await fake_redis.llen(QUEUE_HIGH) == 0

        dev_token = _create_jwt(private_key, tenant_id=tenant_a, user_id=user_a, role="DEVELOPER")

        with mock.patch(
            "app.refactoring.router.execute_refactoring_analysis",
            wraps=execute_refactoring_analysis,
        ) as execute_spy:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                # 1. Stale SCIP index -> HTTP 409 without executing analysis
                res_stale = await client.post(
                    f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"repo_path": str(repo_dir)},
                )
                assert res_stale.status_code == 409, res_stale.text
                detail = res_stale.json()["detail"]
                assert detail["code"] == "SCIP_INDEX_STALE"
                assert detail["project_id"] == str(proj_a.id)
                assert detail["repository_id"] == str(repo_a.id)
                assert detail["repository_head_sha"] == sha2
                assert detail["scip_index_metadata_sha"] == sha1
                assert detail["queue"] == QUEUE_HIGH
                assert detail["reindex_job_id"]
                assert len(detail["reindex_jobs"]) == 1
                assert detail["reindex_jobs"][0]["queue"] == QUEUE_HIGH
                assert detail["reindex_jobs"][0]["priority"] == "high"
                assert detail["reindex_jobs"][0]["commit_sha"] == sha2

                # Verify analysis did NOT execute
                assert execute_spy.call_count == 0
                async with get_tenant_db_context(tenant_a) as session:
                    analyses = (
                        await session.scalars(
                            select(CodeAnalysis).where(CodeAnalysis.project_id == proj_a.id)
                        )
                    ).all()
                    assert analyses == []

                # Verify high-priority job queued ahead of arq:queue:default
                assert await fake_redis.llen(QUEUE_HIGH) == 1
                assert await fake_redis.llen(QUEUE_DEFAULT) == 5

                fetched = await fetch_next_job(fake_redis, "worker-parity-01", poll_timeout=0.0)
                assert fetched is not None
                source_queue, raw_job, parsed_payload = fetched
                assert source_queue == QUEUE_HIGH
                assert parsed_payload["job_id"] == detail["reindex_job_id"]
                assert parsed_payload["job_type"] == "scip.index"
                assert parsed_payload["priority"] == "high"
                assert parsed_payload["payload"]["repository_id"] == str(repo_a.id)
                assert parsed_payload["payload"]["commit_sha"] == sha2
                assert parsed_payload["payload"]["reason"] == "stale_scip_parity_guard"
                # All 5 default jobs remain waiting behind the preempted high-priority job
                assert await fake_redis.llen(QUEUE_DEFAULT) == 5

                # 2. Worker processes high-priority re-indexing job -> ScipIndex.commit_sha == sha2
                worker_res = await dispatch_worker_job(parsed_payload)
                assert worker_res["status"] == "indexed"
                assert worker_res["commit_sha"] == sha2
                assert await ack_job(fake_redis, "worker-parity-01", raw_job) == 1
                assert await fake_redis.llen(QUEUE_HIGH) == 0

                # 3. Retry analyze -> succeeds (HTTP 200) once ScipIndex.commit_sha matches head_sha
                res_ok = await client.post(
                    f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                    headers={"Authorization": f"Bearer {dev_token}"},
                    json={"target_symbol": "compute"},
                )
                assert res_ok.status_code == 200, res_ok.text
                body = res_ok.json()
                assert body["parity_verified"] is True
                assert body["status"] == "completed"
                assert body["project_id"] == str(proj_a.id)
                assert body["repository_id"] == str(repo_a.id)
                assert body["commit_sha"] == sha2
                assert body["repository_head_sha"] == sha2
                assert body["scip_index_metadata_sha"] == sha2
                assert body["graph_loaded_in_ram"] is True
                assert body["symbols_count"] >= 5
                assert body["edges_count"] >= 1
                assert body["callers_count"] == 1
                assert len(body["analyses"]) == 1

                # Analysis executed exactly once and no extra re-index job was queued
                assert execute_spy.call_count == 1
                assert await fake_redis.llen(QUEUE_HIGH) == 0

                async with get_tenant_db_context(tenant_a) as session:
                    analyses = (
                        await session.scalars(
                            select(CodeAnalysis).where(CodeAnalysis.project_id == proj_a.id)
                        )
                    ).all()
                    assert len(analyses) == 1
                    assert analyses[0].commit_sha == sha2
                    assert analyses[0].repository_id == repo_a.id
    finally:
        settings.SCIP_STORAGE_DIR = old_storage_dir
        app.state.jwks_client = orig_jwks
        app.state.scip_redis = orig_redis
        symbol_graph_registry.clear()
        await _cleanup_tenants([tenant_a])
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_missing_scip_index_aborts_409_and_succeeds_after_metadata_index() -> None:
    """
    Missing SCIP index must also abort with HTTP 409, enqueue re-indexing on arq:queue:high,
    and succeed once a matching ScipIndex row exists (even if storage URI is remote/metadata-only).
    """
    symbol_graph_registry.clear()
    tenant_a = uuid.uuid4()
    sha1 = _new_sha()
    fake_redis = _InMemoryQueueRedis()
    private_key, orig_jwks, orig_redis = _setup_auth_and_redis(fake_redis)

    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_tenant(session, tenant_id=tenant_a, prefix="missing")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="Missing SCIP Project",
                slug=f"missing-scip-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            repo_a = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=int(tenant_a.int % 1_000_000_000),
                full_name="acme/missing-scip-repo",
            )
            session.add(repo_a)
            await session.flush()

            session.add(
                Commit(
                    sha=sha1,
                    tenant_id=tenant_a,
                    repository_id=repo_a.id,
                    author_pseudonym_id="pseudo-1",
                    authored_at=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc),
                    committed_at=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc),
                )
            )

        dev_token = _create_jwt(private_key, tenant_id=tenant_a, user_id=user_a, role="DEVELOPER")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # 1. Missing SCIP index -> HTTP 409 + enqueue on arq:queue:high
            res_missing = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert res_missing.status_code == 409, res_missing.text
            detail = res_missing.json()["detail"]
            assert detail["code"] == "SCIP_INDEX_STALE"
            assert detail["repository_head_sha"] == sha1
            assert detail["scip_index_metadata_sha"] is None
            assert detail["stale_repositories"][0]["freshness"] == "MISSING"
            assert detail["queue"] == QUEUE_HIGH
            assert await fake_redis.llen(QUEUE_HIGH) == 1

            # 2. Persist matching ScipIndex row with non-local URI (metadata-only fallback)
            async with get_tenant_db_context(tenant_a) as session:
                await upsert_scip_index(
                    session,
                    tenant_id=tenant_a,
                    repository_id=repo_a.id,
                    commit_sha=sha1,
                    storage_uri="s3://manta-artifacts/missing-scip-repo/index.scip",
                    symbols_count=42,
                )

            # 3. Retry -> succeeds with HTTP 200 without crashing on missing local file
            res_ok = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert res_ok.status_code == 200, res_ok.text
            body = res_ok.json()
            assert body["parity_verified"] is True
            assert body["commit_sha"] == sha1
            assert body["symbols_count"] == 42
            assert body["graph_loaded_in_ram"] is False
    finally:
        app.state.jwks_client = orig_jwks
        app.state.scip_redis = orig_redis
        symbol_graph_registry.clear()
        await _cleanup_tenants([tenant_a])
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_explicit_head_sha_override_multi_repo_rbac_and_rls_isolation() -> None:
    """
    Verify against real PostgreSQL RLS:
    - Multi-repo project aborts with HTTP 409 if any repo is stale and only enqueues stale repo(s).
    - Targeting the fresh repository_id succeeds with HTTP 200.
    - Explicit mismatched head_sha in request body triggers HTTP 409 and enqueues that SHA.
    - Conflicting query & body repository_id returns HTTP 422.
    - RBAC denies unauthorized roles with HTTP 403 without enqueueing.
    - Cross-tenant requests return HTTP 404 without enqueueing.
    """
    symbol_graph_registry.clear()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    sha1 = _new_sha()
    sha2 = _new_sha()
    sha3 = _new_sha()
    fake_redis = _InMemoryQueueRedis()
    private_key, orig_jwks, orig_redis = _setup_auth_and_redis(fake_redis)

    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_tenant(session, tenant_id=tenant_a, prefix="multi-a")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="Multi Repo Parity Project",
                slug=f"multi-parity-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            base_ext_id = int(tenant_a.int % 500_000_000) + 100
            repo_fresh = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=base_ext_id,
                full_name="acme/alpha-fresh",
            )
            repo_stale = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=base_ext_id + 1,
                full_name="acme/beta-stale",
            )
            session.add_all([repo_fresh, repo_stale])
            await session.flush()

            session.add_all(
                [
                    Commit(
                        sha=sha1,
                        tenant_id=tenant_a,
                        repository_id=repo_fresh.id,
                        author_pseudonym_id="pseudo-1",
                        authored_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                        committed_at=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                    ),
                    Commit(
                        sha=sha2,
                        tenant_id=tenant_a,
                        repository_id=repo_stale.id,
                        author_pseudonym_id="pseudo-1",
                        authored_at=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc),
                        committed_at=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc),
                    ),
                ]
            )
            await upsert_scip_index(
                session,
                tenant_id=tenant_a,
                repository_id=repo_fresh.id,
                commit_sha=sha1,
                storage_uri="s3://manta/fresh.scip",
                symbols_count=15,
            )
            await upsert_scip_index(
                session,
                tenant_id=tenant_a,
                repository_id=repo_stale.id,
                commit_sha=sha1,
                storage_uri="s3://manta/stale.scip",
                symbols_count=20,
            )

        async with get_tenant_db_context(tenant_b) as session:
            user_b = await _provision_tenant(session, tenant_id=tenant_b, prefix="multi-b")

        dev_token = _create_jwt(private_key, tenant_id=tenant_a, user_id=user_a, role="DEVELOPER")
        sa_token = _create_jwt(
            private_key, tenant_id=tenant_a, user_id=user_a, role="SOLUTION_ARCHITECT"
        )
        pm_token = _create_jwt(
            private_key, tenant_id=tenant_a, user_id=user_a, role="PROJECT_MANAGER"
        )
        tenant_b_token = _create_jwt(
            private_key, tenant_id=tenant_b, user_id=user_b, role="DEVELOPER"
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # 1. Whole project -> aborts 409 and enqueues ONLY repo_stale
            res_multi = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {dev_token}"},
            )
            assert res_multi.status_code == 409, res_multi.text
            detail = res_multi.json()["detail"]
            assert detail["repository_id"] == str(repo_stale.id)
            assert len(detail["reindex_jobs"]) == 1
            assert detail["reindex_jobs"][0]["repository_id"] == str(repo_stale.id)
            assert detail["reindex_jobs"][0]["commit_sha"] == sha2
            assert await fake_redis.llen(QUEUE_HIGH) == 1

            # 2. Targeting repo_fresh directly succeeds (HTTP 200) for SOLUTION_ARCHITECT
            res_fresh = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {sa_token}"},
                json={"repository_id": str(repo_fresh.id)},
            )
            assert res_fresh.status_code == 200, res_fresh.text
            assert res_fresh.json()["commit_sha"] == sha1
            assert await fake_redis.llen(QUEUE_HIGH) == 1

            # 3. Explicit mismatched head_sha override on repo_fresh -> HTTP 409 + enqueue sha3
            res_override = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {dev_token}"},
                json={"repository_id": str(repo_fresh.id), "head_sha": sha3.upper()},
            )
            assert res_override.status_code == 409, res_override.text
            ov_detail = res_override.json()["detail"]
            assert ov_detail["repository_head_sha"] == sha3
            assert ov_detail["scip_index_metadata_sha"] == sha1
            assert await fake_redis.llen(QUEUE_HIGH) == 2

            # 4. Conflicting repository_id in query vs body -> HTTP 422
            res_conflict_id = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                params={"repository_id": str(repo_fresh.id)},
                headers={"Authorization": f"Bearer {dev_token}"},
                json={"repository_id": str(repo_stale.id)},
            )
            assert res_conflict_id.status_code == 422

            # 5. Unauthorized role -> HTTP 403 and zero new queued jobs
            res_forbidden = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {pm_token}"},
            )
            assert res_forbidden.status_code == 403
            assert await fake_redis.llen(QUEUE_HIGH) == 2

            # 6. Cross-tenant access -> HTTP 404 and zero new queued jobs
            res_cross = await client.post(
                f"/api/v1/refactoring/projects/{proj_a.id}/analyze",
                headers={"Authorization": f"Bearer {tenant_b_token}"},
            )
            assert res_cross.status_code == 404
            assert await fake_redis.llen(QUEUE_HIGH) == 2
    finally:
        app.state.jwks_client = orig_jwks
        app.state.scip_redis = orig_redis
        symbol_graph_registry.clear()
        await _cleanup_tenants([tenant_a, tenant_b])
        await async_engine.dispose()
