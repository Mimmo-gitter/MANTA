"""Automated test suite for TASK-322-0 & TASK-322-1 (US-48 Hardened Sandbox Runner)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from unittest import mock
import uuid

import pytest
from sqlalchemy import delete, pool, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, settings
from app.db.models.models import Project, Repository, TestResult, TestRun
from app.db.session import get_tenant_db_context
from app.verification import (
    DEFAULT_SANDBOX_TIMEOUT_SECONDS,
    FORBIDDEN_SANDBOX_SYSCALLS,
    MAX_CAPTURE_BYTES,
    EphemeralSandboxRunner,
    ImagePullPolicy,
    SandboxDigestMismatchError,
    SandboxExecutionRequest,
    SandboxFailureCode,
    SandboxPolicyError,
    SandboxRuntimeMode,
    SandboxRuntimeUnavailableError,
    SeccompProfileError,
    TestRunStatus,
    UnpinnedImageError,
    enqueue_sandbox_verification_job,
    execute_sandbox_test_run,
    load_and_validate_seccomp_profile,
    parse_and_validate_pinned_image,
    resolve_seccomp_profile_path,
)
from workers.worker import QUEUE_HIGH, dispatch_worker_job, fetch_next_job

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DOC_PATH = REPO_ROOT / "docs" / "sandbox-execution-security-contract.md"
SECCOMP_PROFILE_PATH = REPO_ROOT / "sandbox" / "seccomp" / "manta-sandbox-seccomp.json"
PINNED_TEST_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
PINNED_TEST_IMAGE = f"ghcr.io/manta/sandbox-python@sha256:{PINNED_TEST_DIGEST}"


class _FakeAsyncReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    async def read(self, n: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if n <= 0:
            chunk = self._data[self._offset :]
            self._offset = len(self._data)
            return chunk
        chunk = self._data[self._offset : self._offset + n]
        self._offset += len(chunk)
        return chunk


class _FakeSubprocess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        delay_seconds: float = 0.0,
        hang_forever: bool = False,
    ) -> None:
        self.stdout = _FakeAsyncReader(stdout)
        self.stderr = _FakeAsyncReader(stderr)
        self._final_rc = returncode
        self.returncode: int | None = None
        self.delay_seconds = delay_seconds
        self.hang_forever = hang_forever
        self.killed = False
        self._kill_event = asyncio.Event()

    async def wait(self) -> int:
        if self.hang_forever:
            await self._kill_event.wait()
            self.returncode = 137
            return 137
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        self.returncode = self._final_rc
        return self._final_rc

    def kill(self) -> None:
        self.killed = True
        self._kill_event.set()

    def terminate(self) -> None:
        self.killed = True
        self._kill_event.set()


class _DockerScriptController:
    """Deterministic fake Docker CLI dispatcher capturing every invoked command."""

    def __init__(
        self,
        *,
        runtimes: dict[str, Any] | None = None,
        repo_digests: list[str] | None = None,
        inspect_rc_on_run_container: int = 1,
        run_returncode: int = 0,
        run_stdout: bytes = b"1 passed\n",
        run_stderr: bytes = b"",
        run_delay: float = 0.0,
        hang_run: bool = False,
    ) -> None:
        self.runtimes = runtimes if runtimes is not None else {"runc": {}, "runsc": {}}
        self.repo_digests = repo_digests if repo_digests is not None else [PINNED_TEST_IMAGE]
        self.inspect_rc_on_run_container = inspect_rc_on_run_container
        self.run_returncode = run_returncode
        self.run_stdout = run_stdout
        self.run_stderr = run_stderr
        self.run_delay = run_delay
        self.hang_run = hang_run
        self.calls: list[tuple[str, ...]] = []
        self.live_containers: set[str] = set()

    async def __call__(self, *args: str) -> _FakeSubprocess:
        self.calls.append(tuple(args))
        if len(args) >= 2 and args[1] == "info":
            return _FakeSubprocess(returncode=0, stdout=json.dumps(self.runtimes).encode())
        if len(args) >= 3 and args[1] == "image" and args[2] == "inspect":
            return _FakeSubprocess(returncode=0, stdout=json.dumps(self.repo_digests).encode())
        if len(args) >= 2 and args[1] == "pull":
            return _FakeSubprocess(returncode=0, stdout=b"Pulled\n")
        if len(args) >= 2 and args[1] == "run":
            cname = args[args.index("--name") + 1]
            self.live_containers.add(cname)
            proc = _FakeSubprocess(
                returncode=self.run_returncode,
                stdout=self.run_stdout,
                stderr=self.run_stderr,
                delay_seconds=self.run_delay,
                hang_forever=self.hang_run,
            )
            if not self.hang_run:
                self.live_containers.discard(cname)
            return proc
        if len(args) >= 2 and args[1] == "kill":
            cname = args[-1]
            self.live_containers.discard(cname)
            return _FakeSubprocess(returncode=0)
        if len(args) >= 2 and args[1] == "rm":
            cname = args[-1]
            self.live_containers.discard(cname)
            return _FakeSubprocess(returncode=0)
        if len(args) >= 2 and args[1] == "inspect":
            cname = args[-1]
            if cname in self.live_containers:
                return _FakeSubprocess(returncode=0, stdout=b"[{}]")
            return _FakeSubprocess(returncode=self.inspect_rc_on_run_container, stderr=b"No such")
        return _FakeSubprocess(returncode=1, stderr=b"Unknown command")


# ==============================================================================
# TASK-322-0: Security Contract & Seccomp Allowlist Profile Verification
# ==============================================================================


def test_security_contract_doc_and_seccomp_profile_invariants() -> None:
    assert CONTRACT_DOC_PATH.is_file()
    doc_text = CONTRACT_DOC_PATH.read_text(encoding="utf-8")
    for required_term in (
        "@sha256",
        ":latest",
        "sandbox/seccomp/",
        "--network none",
        "--read-only",
        "VERIFICATION_FAILED",
        "SANDBOX_TIMEOUT",
        "120s",
    ):
        assert required_term in doc_text

    resolved = resolve_seccomp_profile_path()
    assert resolved == SECCOMP_PROFILE_PATH
    profile = load_and_validate_seccomp_profile(resolved)
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert profile["defaultErrnoRet"] == 1
    assert "architectures" not in profile
    assert len(profile["archMap"]) >= 2

    # Verify clone3 ENOSYS & socket filtering
    socket_rules = [r for r in profile["syscalls"] if "socket" in r["names"]]
    assert len(socket_rules) == 6
    for rule in socket_rules:
        assert rule["action"] == "SCMP_ACT_ALLOW"
        assert any(
            a["index"] == 1 and a["op"] == "SCMP_CMP_MASKED_EQ" and a["value"] == 15
            for a in rule["args"]
        )

    # Verify zero dangerous escape syscalls allowed
    allowed = {
        name
        for rule in profile["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW"
        for name in rule["names"]
    }
    assert allowed.isdisjoint(FORBIDDEN_SANDBOX_SYSCALLS)


def test_seccomp_validator_rejects_tampered_profiles(tmp_path: Path) -> None:
    base = json.loads(SECCOMP_PROFILE_PATH.read_text(encoding="utf-8"))

    # 1. Missing file
    with pytest.raises(SeccompProfileError):
        load_and_validate_seccomp_profile(tmp_path / "missing.json")

    # 2. Default-allow profile
    tampered_default = dict(base, defaultAction="SCMP_ACT_ALLOW")
    p_def = tmp_path / "default_allow.json"
    p_def.write_text(json.dumps(tampered_default), encoding="utf-8")
    with pytest.raises(SeccompProfileError, match="SCMP_ACT_ERRNO"):
        load_and_validate_seccomp_profile(p_def)

    # 3. Forbidden syscall ptrace added
    tampered_ptrace = json.loads(json.dumps(base))
    tampered_ptrace["syscalls"].append({"names": ["ptrace"], "action": "SCMP_ACT_ALLOW"})
    p_ptrace = tmp_path / "ptrace.json"
    p_ptrace.write_text(json.dumps(tampered_ptrace), encoding="utf-8")
    with pytest.raises(SeccompProfileError, match="ptrace"):
        load_and_validate_seccomp_profile(p_ptrace)

    # 4. Unconditional clone without namespace mask
    tampered_clone = json.loads(json.dumps(base))
    tampered_clone["syscalls"].append({"names": ["clone"], "action": "SCMP_ACT_ALLOW"})
    p_clone = tmp_path / "clone.json"
    p_clone.write_text(json.dumps(tampered_clone), encoding="utf-8")
    with pytest.raises(SeccompProfileError, match="namespace mask"):
        load_and_validate_seccomp_profile(p_clone)


# ==============================================================================
# TASK-322-1: @sha256 Digest Pinning & Production Settings Validation
# ==============================================================================


@pytest.mark.parametrize(
    ("raw_ref", "expected_canonical"),
    [
        (f"python@sha256:{PINNED_TEST_DIGEST}", f"python@sha256:{PINNED_TEST_DIGEST}"),
        (PINNED_TEST_IMAGE, PINNED_TEST_IMAGE),
        (
            f"ghcr.io/manta/sandbox-python:3.12-slim@sha256:{PINNED_TEST_DIGEST}",
            PINNED_TEST_IMAGE,
        ),
        (
            f"localhost:5000/manta/sbx@sha256:{PINNED_TEST_DIGEST}",
            f"localhost:5000/manta/sbx@sha256:{PINNED_TEST_DIGEST}",
        ),
    ],
)
def test_pinned_sha256_image_validation_accepts_canonical_refs(
    raw_ref: str,
    expected_canonical: str,
) -> None:
    parsed = parse_and_validate_pinned_image(raw_ref)
    assert parsed.canonical == expected_canonical
    assert parsed.digest == PINNED_TEST_DIGEST


@pytest.mark.parametrize(
    "bad_ref",
    [
        None,
        "",
        "   ",
        "python",
        "python:latest",
        "python:3.12-slim",
        "ghcr.io/manta/sandbox:latest",
        f"python:latest@sha256:{PINNED_TEST_DIGEST}",
        f"python:LATEST@sha256:{PINNED_TEST_DIGEST}",
        f"python@sha256:{PINNED_TEST_DIGEST.upper()}",
        "python@sha256:deadbeef",
        "python@md5:d41d8cd98f00b204e9800998ecf8427e",
        f"python@sha256:{PINNED_TEST_DIGEST};rm -rf /",
    ],
)
def test_pinned_sha256_image_validation_rejects_unpinned_or_latest(bad_ref: Any) -> None:
    with pytest.raises(UnpinnedImageError) as exc_info:
        parse_and_validate_pinned_image(bad_ref)
    assert exc_info.value.failure_code == SandboxFailureCode.SANDBOX_IMAGE_UNPINNED


def test_settings_production_security_validates_sandbox_digest_and_timeout() -> None:
    bad_cfg = Settings(
        _env_file=None,
        APP_ENV="production",
        SEED_DEV_DATA=False,
        DATABASE_URL="postgresql+asyncpg://eng_app_user:ProdAppSecret998877@pgbouncer:5432/manta_db",
        MIGRATION_DATABASE_URL="postgresql+asyncpg://manta_user:ProdAdminSecret998877@postgres:5432/manta_db",
        CORS_ORIGINS="https://manta.example.com",
        OIDC_ISSUER="https://auth.manta.example.com/realms/manta",
        OIDC_JWKS_URL="http://keycloak:8080/realms/manta/protocol/openid-connect/certs",
        WEB_BASE_URL="https://manta.example.com",
        WEBHOOK_BASE_URL="https://api.manta.example.com",
        ENCRYPTION_MASTER_KEY="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        KEYCLOAK_ADMIN_PASSWORD="RotatedKeycloakAdminPass2026",
        SANDBOX_IMAGE_DIGEST="python:latest",
        SANDBOX_TIMEOUT_SECONDS=180.0,
    )
    errors = bad_cfg.validate_production_security()
    assert any("SANDBOX_IMAGE_DIGEST" in e for e in errors)
    assert any("SANDBOX_TIMEOUT_SECONDS" in e for e in errors)
    assert bad_cfg.sandbox_allow_docker_fallback is False


# ==============================================================================
# TASK-322-1: Runtime Confinement, Fallback, Digest Check & Timeout Kill
# ==============================================================================


@pytest.mark.asyncio
async def test_runner_prefers_runsc_and_enforces_hardened_confinement_flags(tmp_path: Path) -> None:
    ctrl = _DockerScriptController(runtimes={"runc": {}, "runsc": {}})
    runner = EphemeralSandboxRunner(
        preferred_runtime="runsc",
        allow_docker_fallback=False,
        pull_policy=ImagePullPolicy.NEVER,
        subprocess_exec=ctrl,
    )

    req = SandboxExecutionRequest(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        image=PINNED_TEST_IMAGE,
        command=["pytest", "-q"],
        workspace_path=str(tmp_path),
        env={"CI": "true"},
        timeout_seconds=DEFAULT_SANDBOX_TIMEOUT_SECONDS,
    )
    res = await runner.execute(req)
    assert res.status == TestRunStatus.PASSED
    assert res.runtime == SandboxRuntimeMode.RUNSC
    assert res.fallback_used is False
    assert res.image_digest == PINNED_TEST_IMAGE
    assert res.cleanup_verified is True

    run_cmd = next(c for c in ctrl.calls if c[1] == "run")
    assert "--runtime" in run_cmd and run_cmd[run_cmd.index("--runtime") + 1] == "runsc"
    assert "--network" in run_cmd and run_cmd[run_cmd.index("--network") + 1] == "none"
    assert "--read-only" in run_cmd
    assert "--cap-drop" in run_cmd and run_cmd[run_cmd.index("--cap-drop") + 1] == "ALL"
    assert "--ipc" in run_cmd and run_cmd[run_cmd.index("--ipc") + 1] == "none"
    assert "--user" in run_cmd and run_cmd[run_cmd.index("--user") + 1] == "65534:65534"
    assert "no-new-privileges:true" in run_cmd
    assert f"{tmp_path.resolve()}:/workspace:ro" in run_cmd
    assert list(run_cmd[-3:]) == [PINNED_TEST_IMAGE, "pytest", "-q"]


@pytest.mark.asyncio
async def test_runner_runc_fallback_attaches_seccomp_and_prod_fails_closed() -> None:
    ctrl = _DockerScriptController(runtimes={"runc": {}})

    # Local dev fallback succeeds and attaches sandbox/seccomp/manta-sandbox-seccomp.json
    dev_runner = EphemeralSandboxRunner(
        preferred_runtime="runsc",
        allow_docker_fallback=True,
        pull_policy=ImagePullPolicy.NEVER,
        subprocess_exec=ctrl,
    )
    req = SandboxExecutionRequest(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        image=PINNED_TEST_IMAGE,
        command=["python", "-c", "print('ok')"],
    )
    res = await dev_runner.execute(req)
    assert res.status == TestRunStatus.PASSED
    assert res.runtime == SandboxRuntimeMode.RUNC
    assert res.fallback_used is True

    run_cmd = next(c for c in ctrl.calls if c[1] == "run")
    assert "--runtime" not in run_cmd
    assert any(arg == f"seccomp={SECCOMP_PROFILE_PATH}" for arg in run_cmd)

    # Production fail-closed when runsc is missing
    prod_runner = EphemeralSandboxRunner(
        preferred_runtime="runsc",
        allow_docker_fallback=False,
        subprocess_exec=ctrl,
    )
    with pytest.raises(SandboxRuntimeUnavailableError):
        await prod_runner.execute(req)


@pytest.mark.asyncio
async def test_runner_blocks_digest_mismatch_and_secret_env_leakage() -> None:
    ctrl = _DockerScriptController(
        repo_digests=["ghcr.io/manta/sandbox-python@sha256:" + ("0" * 64)]
    )
    runner = EphemeralSandboxRunner(
        allow_docker_fallback=True,
        pull_policy=ImagePullPolicy.NEVER,
        subprocess_exec=ctrl,
    )

    req_mismatch = SandboxExecutionRequest(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        image=PINNED_TEST_IMAGE,
        command=["python", "-V"],
    )
    with pytest.raises(SandboxDigestMismatchError) as exc_digest:
        await runner.execute(req_mismatch)
    assert exc_digest.value.failure_code == SandboxFailureCode.SANDBOX_DIGEST_MISMATCH
    assert not any(c[1] == "run" for c in ctrl.calls)

    # Secret env leakage blocked before container launch
    req_secret = SandboxExecutionRequest(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        image=PINNED_TEST_IMAGE,
        command=["python", "-V"],
        env={"ENCRYPTION_MASTER_KEY": "leaked"},
    )
    with pytest.raises(SandboxPolicyError, match="ENCRYPTION_MASTER_KEY"):
        await runner.execute(req_secret)


@pytest.mark.asyncio
async def test_runner_kills_container_on_timeout_with_sandbox_timeout_and_bounds_output() -> None:
    ctrl_timeout = _DockerScriptController(
        runtimes={"runc": {}, "runsc": {}},
        hang_run=True,
    )
    runner = EphemeralSandboxRunner(
        allow_docker_fallback=True,
        pull_policy=ImagePullPolicy.NEVER,
        subprocess_exec=ctrl_timeout,
    )

    # Pydantic rejects > 120s timeout
    with pytest.raises(Exception):
        SandboxExecutionRequest(
            tenant_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            image=PINNED_TEST_IMAGE,
            command=["python", "-c", "import time; time.sleep(200)"],
            timeout_seconds=121.0,
        )

    req = SandboxExecutionRequest(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        image=PINNED_TEST_IMAGE,
        command=["python", "-c", "import time; time.sleep(200)"],
        timeout_seconds=0.05,
    )
    res = await runner.execute(req)
    assert res.status == TestRunStatus.VERIFICATION_FAILED
    assert res.failure_code == SandboxFailureCode.SANDBOX_TIMEOUT
    assert res.timed_out is True
    assert res.exit_code == 137
    assert res.cleanup_verified is True
    assert ctrl_timeout.live_containers == set()
    assert res.failure_report is not None
    assert res.failure_report.kill_signal == "SIGKILL"
    assert res.failure_report.failure_code == SandboxFailureCode.SANDBOX_TIMEOUT
    assert any(c[1] == "kill" and "--signal=SIGKILL" in c for c in ctrl_timeout.calls)
    assert any(c[1] == "rm" and "-f" in c for c in ctrl_timeout.calls)

    # Output flood (> 128 KB) is strictly bounded
    ctrl_flood = _DockerScriptController(
        run_stdout=b"A" * (MAX_CAPTURE_BYTES * 3),
        run_stderr=b"B" * (MAX_CAPTURE_BYTES * 2),
    )
    flood_runner = EphemeralSandboxRunner(
        allow_docker_fallback=True,
        pull_policy=ImagePullPolicy.NEVER,
        subprocess_exec=ctrl_flood,
    )
    res_flood = await flood_runner.execute(
        SandboxExecutionRequest(
            tenant_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            image=PINNED_TEST_IMAGE,
            command=["python", "-c", "print('A'*200000)"],
        )
    )
    assert res_flood.status == TestRunStatus.PASSED
    assert len(res_flood.stdout) <= MAX_CAPTURE_BYTES + 32
    assert "...[truncated]" in res_flood.stdout


# ==============================================================================
# TestRun Persistence (Unit + PostgreSQL RLS) & ARQ Worker Dispatch
# ==============================================================================


class _FakeTestRunSession:
    def __init__(self, store: dict[uuid.UUID, TestRun], results: list[TestResult]) -> None:
        self.store = store
        self.results = results

    def add(self, obj: Any) -> None:
        if isinstance(obj, TestRun):
            self.store[obj.id] = obj
        elif isinstance(obj, TestResult):
            self.results.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> None:
        params = stmt.compile().params
        target_id = params.get("id_1")
        if target_id in self.store:
            row = self.store[target_id]
            for field in (
                "status",
                "duration_ms",
                "sandbox_runtime",
                "sandbox_image_digest",
                "exit_code",
                "failure_code",
                "failure_details",
                "completed_at",
            ):
                if field in params:
                    setattr(row, field, params[field])


@pytest.mark.asyncio
async def test_execute_sandbox_test_run_persists_timeout_and_worker_dispatch() -> None:
    store: dict[uuid.UUID, TestRun] = {}
    results: list[TestResult] = []
    tx_count = 0

    @asynccontextmanager
    async def _fake_tenant_ctx(_tenant_id: uuid.UUID):
        nonlocal tx_count
        tx_count += 1
        yield _FakeTestRunSession(store, results)

    ctrl = _DockerScriptController(runtimes={"runc": {}, "runsc": {}}, hang_run=True)
    runner = EphemeralSandboxRunner(
        allow_docker_fallback=True,
        pull_policy=ImagePullPolicy.NEVER,
        subprocess_exec=ctrl,
    )

    req = SandboxExecutionRequest(
        tenant_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        image=PINNED_TEST_IMAGE,
        command=["pytest"],
        timeout_seconds=0.05,
    )
    res = await execute_sandbox_test_run(
        req,
        runner=runner,
        tenant_session_factory=_fake_tenant_ctx,
    )
    # Two separate short DB transactions (never held open across container run)
    assert tx_count == 2
    assert res.test_run_id in store
    persisted = store[res.test_run_id]
    assert persisted.status == "VERIFICATION_FAILED"
    assert persisted.failure_code == "SANDBOX_TIMEOUT"
    assert persisted.sandbox_runtime == "runsc"
    assert persisted.sandbox_image_digest == PINNED_TEST_IMAGE
    assert persisted.failure_details["failure_code"] == "SANDBOX_TIMEOUT"
    assert persisted.failure_details["cleanup_verified"] is True
    assert len(results) == 1
    assert results[0].test_name == "__sandbox_timeout__"
    assert results[0].status == "ERROR"

    # Unpinned image fails BEFORE touching DB
    tx_before = tx_count
    with pytest.raises(UnpinnedImageError):
        await execute_sandbox_test_run(
            SandboxExecutionRequest(
                tenant_id=req.tenant_id,
                repository_id=req.repository_id,
                image="python:latest",
                command=["pytest"],
            ),
            runner=runner,
            tenant_session_factory=_fake_tenant_ctx,
        )
    assert tx_count == tx_before

    # ARQ queue enqueue + dispatch_worker_job routing
    class _MemQueue:
        def __init__(self) -> None:
            self.lists: dict[str, list[str]] = {}

        async def rpush(self, k: str, v: str) -> int:
            self.lists.setdefault(k, []).append(v)
            return len(self.lists[k])

        async def lmove(
            self, src_q: str, dst_q: str, src: str = "LEFT", dest: str = "RIGHT"
        ) -> str | None:
            items = self.lists.get(src_q, [])
            if not items:
                return None
            val = items.pop(0)
            self.lists.setdefault(dst_q, []).append(val)
            return val

        async def blmove(self, *_args: Any, **_kwargs: Any) -> str | None:
            return None

    q = _MemQueue()
    enq = await enqueue_sandbox_verification_job(request=req, priority="high", redis_client=q)
    assert enq["queue"] == QUEUE_HIGH
    fetched = await fetch_next_job(q, "sbx-worker-1", poll_timeout=0.0)
    assert fetched is not None
    _squeue, _raw, parsed_job = fetched

    with mock.patch(
        "app.verification.service.execute_sandbox_test_run",
        return_value=res,
    ) as exec_mock:
        dispatched = await dispatch_worker_job(parsed_job)
    assert exec_mock.call_count == 1
    assert dispatched["failure_code"] == "SANDBOX_TIMEOUT"


@pytest.mark.asyncio
async def test_postgres_rls_persists_test_run_passed_and_sandbox_timeout() -> None:
    """Integration test against PostgreSQL RLS (runs in CI; skips if local PG unavailable)."""
    admin_url = settings.MIGRATION_DATABASE_URL or os.getenv("MIGRATION_DATABASE_URL")
    if not admin_url:
        admin_url = settings.DATABASE_URL.replace(
            "eng_app_user:eng_app_pass_change_in_prod", "manta_user:manta_password"
        )

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    probe_engine = create_async_engine(admin_url, poolclass=pool.NullPool)
    try:
        async with probe_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await probe_engine.dispose()
        pytest.skip(f"PostgreSQL unavailable for RLS integration test: {exc}")

    try:
        async with get_tenant_db_context(tenant_a) as session:
            u_res = await session.execute(
                text(
                    "SELECT user_id FROM provision_tenant(:name, :slug, :email, :admin_name, :tid)"
                ),
                {
                    "name": f"Sbx Tenant {tenant_a.hex[:6]}",
                    "slug": f"sbx-{tenant_a.hex[:8]}",
                    "email": f"sbx-{tenant_a.hex[:6]}@manta.local",
                    "admin_name": "Sbx Admin",
                    "tid": tenant_a,
                },
            )
            user_a = u_res.scalar_one()
            proj = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="Sandbox Project",
                slug=f"sbx-proj-{tenant_a.hex[:6]}",
            )
            session.add(proj)
            await session.flush()
            repo = Repository(
                tenant_id=tenant_a,
                project_id=proj.id,
                external_repo_id=int(tenant_a.int % 1_000_000_000),
                full_name="acme/sandbox-repo",
            )
            session.add(repo)
            await session.flush()
            repo_id = repo.id

        ctrl_timeout = _DockerScriptController(runtimes={"runc": {}}, hang_run=True)
        runner = EphemeralSandboxRunner(
            allow_docker_fallback=True,
            pull_policy=ImagePullPolicy.NEVER,
            subprocess_exec=ctrl_timeout,
        )
        res = await execute_sandbox_test_run(
            SandboxExecutionRequest(
                tenant_id=tenant_a,
                repository_id=repo_id,
                image=PINNED_TEST_IMAGE,
                command=["pytest"],
                timeout_seconds=0.05,
            ),
            runner=runner,
        )
        assert res.test_run_id is not None

        async with get_tenant_db_context(tenant_a) as session:
            row = (
                await session.scalars(select(TestRun).where(TestRun.id == res.test_run_id))
            ).first()
            assert row is not None
            assert row.status == "VERIFICATION_FAILED"
            assert row.failure_code == "SANDBOX_TIMEOUT"
            assert row.sandbox_runtime == "runc"
            assert row.completed_at is not None

        # Cross-tenant RLS isolation: Tenant B sees 0 rows
        async with get_tenant_db_context(tenant_b) as session_b:
            cross_row = (
                await session_b.scalars(select(TestRun).where(TestRun.id == res.test_run_id))
            ).first()
            assert cross_row is None
    finally:
        async with probe_engine.begin() as conn:
            await conn.execute(delete(TestResult).where(TestResult.tenant_id == tenant_a))
            await conn.execute(delete(TestRun).where(TestRun.tenant_id == tenant_a))
            await conn.execute(delete(Repository).where(Repository.tenant_id == tenant_a))
            await conn.execute(delete(Project).where(Project.tenant_id == tenant_a))
            await conn.execute(
                text("DELETE FROM user_roles WHERE tenant_id = :tid"), {"tid": tenant_a}
            )
            await conn.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tenant_a})
            await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_a})
        await probe_engine.dispose()


# ==============================================================================
# Live Docker Confinement Tests (auto-run when Docker daemon + image available)
# ==============================================================================


@pytest.mark.asyncio
async def test_live_docker_sandbox_egress_readonly_and_timeout_kill(tmp_path: Path) -> None:
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        pytest.skip(f"Docker CLI unavailable: {exc}")

    if probe.returncode != 0:
        pytest.skip(f"Docker daemon unreachable: {probe.stderr.strip()}")

    image = (os.getenv("SANDBOX_IMAGE_DIGEST") or "").strip()
    if not image:
        pytest.skip("SANDBOX_IMAGE_DIGEST not set for live Docker sandbox test")

    runner = EphemeralSandboxRunner(
        allow_docker_fallback=True, pull_policy=ImagePullPolicy.IF_NOT_PRESENT
    )

    # 1. Outbound egress MUST fail (--network none) & rootfs write MUST fail (--read-only)
    res_confined = await runner.execute(
        SandboxExecutionRequest(
            tenant_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            image=image,
            workspace_path=str(tmp_path),
            command=[
                "python",
                "-c",
                (
                    "import socket, sys; "
                    "ro_denied = False\n"
                    "try:\n"
                    "    open('/rootfs_escape', 'w').write('x')\n"
                    "except OSError:\n"
                    "    ro_denied = True\n"
                    "egress_denied = False\n"
                    "try:\n"
                    "    socket.create_connection(('1.1.1.1', 443), timeout=1)\n"
                    "except OSError:\n"
                    "    egress_denied = True\n"
                    "assert ro_denied and egress_denied, (ro_denied, egress_denied)\n"
                    "print('CONFINEMENT_OK')"
                ),
            ],
            timeout_seconds=15.0,
        )
    )
    assert res_confined.status == TestRunStatus.PASSED, res_confined.stderr
    assert "CONFINEMENT_OK" in res_confined.stdout

    # 2. Timeout kill leaves zero leaked containers
    res_to = await runner.execute(
        SandboxExecutionRequest(
            tenant_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            image=image,
            command=["python", "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.5,
        )
    )
    assert res_to.status == TestRunStatus.VERIFICATION_FAILED
    assert res_to.failure_code == SandboxFailureCode.SANDBOX_TIMEOUT
    assert res_to.cleanup_verified is True
