"""Hardened ephemeral gVisor / Docker sandbox execution runner (TASK-322-1 / US-48)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Protocol
import uuid

from opentelemetry.trace import SpanKind

from app.config import settings
from app.observability.tracing import start_linked_span
from app.verification.policy import (
    SandboxDigestMismatchError,
    SandboxPolicyError,
    SandboxRuntimeUnavailableError,
    load_and_validate_seccomp_profile,
    parse_and_validate_pinned_image,
    resolve_seccomp_profile_path,
    validate_command,
    validate_sandbox_env,
    validate_timeout_seconds,
    validate_workspace_path,
)
from app.verification.schemas import (
    FAILURE_TAIL_BYTES,
    MAX_CAPTURE_BYTES,
    SANDBOX_USER_UID_GID,
    ImagePullPolicy,
    PinnedImageReference,
    SandboxConfinementPolicy,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxFailureCode,
    SandboxFailureReport,
    SandboxRuntimeMode,
    TestRunStatus,
)

logger = logging.getLogger("app.verification.runner")


class SubprocessHandle(Protocol):
    """Structural protocol satisfied by `asyncio.subprocess.Process` and async test fakes."""

    @property
    def stdout(self) -> Any: ...

    @property
    def stderr(self) -> Any: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def kill(self) -> None: ...

    def terminate(self) -> None: ...


SubprocessExecFn = Callable[..., Awaitable[SubprocessHandle]]


async def _default_subprocess_exec(*args: str) -> SubprocessHandle:
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _read_stream_bounded(
    stream: Any,
    *,
    max_bytes: int = MAX_CAPTURE_BYTES,
) -> tuple[str, bool]:
    """
    Drain an async stream to EOF while bounding retained memory to `max_bytes`.

    Returns `(decoded_utf8_text, was_truncated)`.
    """
    if stream is None:
        return "", False

    chunks: list[bytes] = []
    captured = 0
    truncated = False

    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        if captured < max_bytes:
            remaining = max_bytes - captured
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                captured += remaining
                truncated = True
            else:
                chunks.append(chunk)
                captured += len(chunk)
        else:
            truncated = True

    text = b"".join(chunks).decode("utf-8", errors="replace")
    return text, truncated


def _tail_text(text: str, limit: int = FAILURE_TAIL_BYTES) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


class EphemeralSandboxRunner:
    """
    Executes untrusted verification commands inside hardened ephemeral containers.

    Enforces:
    - Strict immutable `@sha256:...` base image pinning (never `:latest`)
    - Preferred `runsc` (`gVisor`) runtime with hardened Docker (`runc` + `sandbox/seccomp/`)
      local-dev fallback
    - `--network none`, `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges:true`,
      `--ipc none`, `--user 65534:65534`, `--pids-limit`, `--memory`, `--cpus`, and `--tmpfs`
    - Hard wall-clock `120s` timeout kill (`SIGKILL` + `docker rm -f -v`) with zero leaked containers
    """

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        preferred_runtime: str | None = None,
        allow_docker_fallback: bool | None = None,
        seccomp_profile_path: Path | str | None = None,
        apply_seccomp_on_runsc: bool | None = None,
        verify_local_digest: bool = True,
        pull_policy: ImagePullPolicy | str | None = None,
        memory_limit: str | None = None,
        cpu_limit: str | None = None,
        pids_limit: int | None = None,
        tmpfs_size: str | None = None,
        subprocess_exec: SubprocessExecFn | None = None,
    ) -> None:
        self.docker_bin = docker_bin
        self.preferred_runtime = (preferred_runtime or settings.SANDBOX_RUNTIME or "runsc").strip()
        self.allow_docker_fallback = (
            settings.sandbox_allow_docker_fallback
            if allow_docker_fallback is None
            else bool(allow_docker_fallback)
        )
        self.seccomp_profile_path = resolve_seccomp_profile_path(seccomp_profile_path)
        self.apply_seccomp_on_runsc = (
            settings.SANDBOX_APPLY_SECCOMP_ON_RUNSC
            if apply_seccomp_on_runsc is None
            else bool(apply_seccomp_on_runsc)
        )
        self.verify_local_digest = verify_local_digest
        raw_pull_policy = pull_policy or settings.SANDBOX_IMAGE_PULL_POLICY
        self.pull_policy = ImagePullPolicy(str(raw_pull_policy).strip().lower())
        self.memory_limit = (memory_limit or settings.SANDBOX_MEMORY_LIMIT).strip()
        self.cpu_limit = (cpu_limit or settings.SANDBOX_CPU_LIMIT).strip()
        self.pids_limit = int(pids_limit if pids_limit is not None else settings.SANDBOX_PIDS_LIMIT)
        self.tmpfs_size = (tmpfs_size or settings.SANDBOX_TMPFS_SIZE).strip()
        self._subprocess_exec: SubprocessExecFn = subprocess_exec or _default_subprocess_exec

    async def _run_control_command(
        self,
        args: Sequence[str],
        *,
        timeout: float = 10.0,
    ) -> tuple[int, str, str]:
        """Run a short-lived Docker control command (`info`, `inspect`, `kill`, `rm`, `pull`)."""
        proc = await self._subprocess_exec(*args)
        stdout_task = asyncio.create_task(_read_stream_bounded(proc.stdout))
        stderr_task = asyncio.create_task(_read_stream_bounded(proc.stderr))
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            with suppress(Exception):
                proc.kill()
            with suppress(Exception):
                await proc.wait()
            for t in (stdout_task, stderr_task):
                t.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await t
            return 124, "", f"Control command timed out after {timeout}s: {' '.join(args)}"

        out_text, _ = await stdout_task
        err_text, _ = await stderr_task
        return rc, out_text.strip(), err_text.strip()

    async def detect_runtime(self) -> tuple[SandboxRuntimeMode, bool]:
        """
        Inspect Docker Engine runtimes and select `runsc` (`gVisor`) or hardened `runc` fallback.

        Returns `(runtime_mode, fallback_used)`.
        """
        try:
            rc, out_text, err_text = await self._run_control_command(
                [self.docker_bin, "info", "--format", "{{json .Runtimes}}"],
                timeout=10.0,
            )
        except OSError as exc:
            raise SandboxRuntimeUnavailableError(
                f"Docker CLI unavailable ({self.docker_bin!r}): {exc}"
            ) from exc

        if rc != 0:
            raise SandboxRuntimeUnavailableError(
                f"Docker daemon unreachable (exit {rc}): {err_text or out_text}"
            )

        try:
            runtimes_raw = json.loads(out_text) if out_text else {}
        except json.JSONDecodeError as exc:
            raise SandboxRuntimeUnavailableError(
                f"Unexpected `docker info` runtimes JSON payload: {out_text!r}"
            ) from exc

        runtimes = (
            {str(k).lower(): v for k, v in runtimes_raw.items()}
            if isinstance(runtimes_raw, dict)
            else {}
        )

        want_runsc = self.preferred_runtime.lower() == SandboxRuntimeMode.RUNSC.value
        if want_runsc and SandboxRuntimeMode.RUNSC.value in runtimes:
            return SandboxRuntimeMode.RUNSC, False

        if want_runsc and not self.allow_docker_fallback:
            raise SandboxRuntimeUnavailableError(
                "Required gVisor runtime 'runsc' is not registered in Docker Engine and "
                "SANDBOX_ALLOW_DOCKER_FALLBACK is disabled"
            )

        if want_runsc:
            logger.warning(
                "gVisor 'runsc' runtime unavailable; falling back to hardened Docker 'runc' with "
                "seccomp profile %s",
                self.seccomp_profile_path,
            )
            return SandboxRuntimeMode.RUNC, True

        return SandboxRuntimeMode.RUNC, False

    async def _inspect_repo_digests(self, canonical_image: str) -> tuple[int, list[str], str]:
        rc, out_text, err_text = await self._run_control_command(
            [
                self.docker_bin,
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                canonical_image,
            ],
            timeout=10.0,
        )
        if rc != 0:
            return rc, [], err_text or out_text
        try:
            parsed = json.loads(out_text) if out_text else []
        except json.JSONDecodeError:
            return 1, [], f"Invalid RepoDigests JSON: {out_text!r}"
        if not isinstance(parsed, list):
            return 1, [], f"Expected RepoDigests list, got {type(parsed).__name__}"
        return 0, [str(item) for item in parsed], ""

    async def verify_image_digest(self, pinned: PinnedImageReference) -> None:
        """
        Verify local Docker image `RepoDigests` matches the exact pinned `@sha256:<64hex>` digest.

        Pulls strictly by `pinned.canonical` according to `self.pull_policy` when missing or
        when `pull_policy == ALWAYS`.
        """
        if self.pull_policy == ImagePullPolicy.ALWAYS:
            rc_pull, out_pull, err_pull = await self._run_control_command(
                [self.docker_bin, "pull", pinned.canonical],
                timeout=60.0,
            )
            if rc_pull != 0:
                raise SandboxDigestMismatchError(
                    f"Failed to pull pinned sandbox image {pinned.canonical}: {err_pull or out_pull}"
                )

        rc, digests, err_msg = await self._inspect_repo_digests(pinned.canonical)
        if rc != 0 and self.pull_policy == ImagePullPolicy.IF_NOT_PRESENT:
            rc_pull, out_pull, err_pull = await self._run_control_command(
                [self.docker_bin, "pull", pinned.canonical],
                timeout=60.0,
            )
            if rc_pull != 0:
                raise SandboxDigestMismatchError(
                    f"Pinned sandbox image {pinned.canonical} missing locally and pull failed: "
                    f"{err_pull or out_pull}"
                )
            rc, digests, err_msg = await self._inspect_repo_digests(pinned.canonical)

        if rc != 0:
            raise SandboxDigestMismatchError(
                f"Could not inspect local RepoDigests for {pinned.canonical}: {err_msg}"
            )

        expected_suffix = f"@sha256:{pinned.digest}"
        if not any(d.strip().lower().endswith(expected_suffix) for d in digests):
            raise SandboxDigestMismatchError(
                f"Local image RepoDigests {digests!r} do not match pinned digest {expected_suffix}"
            )

    def build_confinement_policy(
        self,
        *,
        runtime: SandboxRuntimeMode,
        fallback_used: bool,
        seccomp_attached: bool,
    ) -> SandboxConfinementPolicy:
        """Build structured confinement metadata for `TestRun.failure_details` and telemetry."""
        return SandboxConfinementPolicy(
            tmpfs_mount=f"/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_size}",
            pids_limit=self.pids_limit,
            memory_limit=self.memory_limit,
            memory_swap_limit=self.memory_limit,
            cpu_limit=self.cpu_limit,
            seccomp_profile_path=str(self.seccomp_profile_path) if seccomp_attached else None,
            runtime=runtime,
            fallback_used=fallback_used,
        )

    def build_docker_run_args(
        self,
        *,
        container_name: str,
        pinned: PinnedImageReference,
        command: Sequence[str],
        runtime: SandboxRuntimeMode,
        workspace_path: Path | None = None,
        env: dict[str, str] | None = None,
        tenant_id: uuid.UUID | None = None,
        test_run_id: uuid.UUID | None = None,
    ) -> list[str]:
        """Construct the deterministic, hardened `docker run` CLI argument vector."""
        args: list[str] = [
            self.docker_bin,
            "run",
            "--rm",
            "--init",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_size}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--ipc",
            "none",
            "--user",
            SANDBOX_USER_UID_GID,
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory_limit,
            "--memory-swap",
            self.memory_limit,
            "--cpus",
            self.cpu_limit,
            "--label",
            "manta.sandbox=true",
        ]

        if tenant_id is not None:
            args.extend(["--label", f"manta.tenant_id={tenant_id}"])
        if test_run_id is not None:
            args.extend(["--label", f"manta.test_run_id={test_run_id}"])

        if runtime == SandboxRuntimeMode.RUNSC:
            args.extend(["--runtime", SandboxRuntimeMode.RUNSC.value])
            if self.apply_seccomp_on_runsc:
                args.extend(["--security-opt", f"seccomp={self.seccomp_profile_path}"])
        else:
            args.extend(["--security-opt", f"seccomp={self.seccomp_profile_path}"])

        if workspace_path is not None:
            args.extend(["-v", f"{workspace_path}:/workspace:ro", "-w", "/workspace"])
        else:
            args.extend(["-w", "/tmp"])

        merged_env = {
            "HOME": "/tmp",
            "TMPDIR": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        if env:
            merged_env.update(env)
        for key in sorted(merged_env):
            args.extend(["-e", f"{key}={merged_env[key]}"])

        args.append(pinned.canonical)
        args.extend(command)
        return args

    async def _kill_and_remove_container(self, container_name: str) -> bool:
        """
        Force-kill (`SIGKILL`) and remove `container_name`, returning True iff verified absent.
        """
        with suppress(Exception):
            await self._run_control_command(
                [self.docker_bin, "kill", "--signal=SIGKILL", container_name],
                timeout=10.0,
            )
        with suppress(Exception):
            await self._run_control_command(
                [self.docker_bin, "rm", "-f", "-v", container_name],
                timeout=10.0,
            )
        return await self._verify_container_absent(container_name)

    async def _verify_container_absent(self, container_name: str) -> bool:
        """Confirm via `docker inspect` that no container named `container_name` remains."""
        try:
            rc, _out, _err = await self._run_control_command(
                [self.docker_bin, "inspect", container_name],
                timeout=5.0,
            )
            return rc != 0
        except Exception:
            return False

    async def execute(
        self,
        request: SandboxExecutionRequest,
        *,
        test_run_id: uuid.UUID | None = None,
    ) -> SandboxExecutionResult:
        """
        Execute `request.command` inside an ephemeral confined container.

        All security validations (`@sha256` pin, seccomp profile, timeout bounds, clean env)
        execute fail-closed BEFORE spawning any container.
        """
        raw_image = request.image if request.image is not None else settings.SANDBOX_IMAGE_DIGEST
        pinned = parse_and_validate_pinned_image(raw_image)
        timeout_seconds = validate_timeout_seconds(request.timeout_seconds)
        command = validate_command(request.command)
        workspace = validate_workspace_path(request.resolved_workspace())
        clean_env = validate_sandbox_env(request.env)

        # Always validate seccomp profile on disk regardless of runtime mode
        load_and_validate_seccomp_profile(self.seccomp_profile_path)

        runtime, fallback_used = await self.detect_runtime()
        seccomp_attached = runtime == SandboxRuntimeMode.RUNC or self.apply_seccomp_on_runsc
        confinement = self.build_confinement_policy(
            runtime=runtime,
            fallback_used=fallback_used,
            seccomp_attached=seccomp_attached,
        )

        if self.verify_local_digest:
            await self.verify_image_digest(pinned)

        container_name = f"manta-sbx-{uuid.uuid4().hex[:16]}"
        docker_args = self.build_docker_run_args(
            container_name=container_name,
            pinned=pinned,
            command=command,
            runtime=runtime,
            workspace_path=workspace,
            env=clean_env,
            tenant_id=request.tenant_id,
            test_run_id=test_run_id,
        )

        started_mono = time.monotonic()
        cleanup_verified = False
        timed_out = False
        stdout_text = ""
        stderr_text = ""
        exit_code: int | None = None

        with start_linked_span(
            "verification.sandbox.execute",
            kind=SpanKind.CLIENT,
            attributes={
                "manta.tenant_id": str(request.tenant_id),
                "manta.repository_id": str(request.repository_id),
                "manta.test_run_id": str(test_run_id or ""),
                "manta.sandbox.runtime": runtime.value,
                "manta.sandbox.fallback_used": fallback_used,
                "manta.sandbox.image_digest": pinned.canonical,
                "manta.sandbox.timeout_seconds": timeout_seconds,
            },
        ) as span:
            proc: SubprocessHandle | None = None
            stdout_task: asyncio.Task[tuple[str, bool]] | None = None
            stderr_task: asyncio.Task[tuple[str, bool]] | None = None

            try:
                proc = await self._subprocess_exec(*docker_args)
                stdout_task = asyncio.create_task(_read_stream_bounded(proc.stdout))
                stderr_task = asyncio.create_task(_read_stream_bounded(proc.stderr))

                try:
                    exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    timed_out = True
                    exit_code = 137
                    cleanup_verified = await self._kill_and_remove_container(container_name)
                    with suppress(Exception):
                        proc.kill()
                    with suppress(Exception):
                        await asyncio.wait_for(proc.wait(), timeout=2.0)

                if stdout_task is not None:
                    try:
                        stdout_text, out_trunc = await asyncio.wait_for(stdout_task, timeout=2.0)
                        if out_trunc:
                            stdout_text = f"{stdout_text}\n...[truncated]"
                    except (asyncio.TimeoutError, Exception):
                        stdout_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await stdout_task

                if stderr_task is not None:
                    try:
                        stderr_text, err_trunc = await asyncio.wait_for(stderr_task, timeout=2.0)
                        if err_trunc:
                            stderr_text = f"{stderr_text}\n...[truncated]"
                    except (asyncio.TimeoutError, Exception):
                        stderr_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await stderr_task
            except BaseException:
                if proc is not None and proc.returncode is None:
                    with suppress(Exception):
                        proc.kill()
                for t in (stdout_task, stderr_task):
                    if t is not None and not t.done():
                        t.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await t
                await self._kill_and_remove_container(container_name)
                raise
            finally:
                if not cleanup_verified:
                    cleanup_verified = await self._verify_container_absent(container_name)
                    if not cleanup_verified:
                        cleanup_verified = await self._kill_and_remove_container(container_name)

            duration_ms = max(0, int((time.monotonic() - started_mono) * 1000))
            span.set_attribute("manta.sandbox.duration_ms", duration_ms)
            span.set_attribute("manta.sandbox.timed_out", timed_out)
            span.set_attribute("manta.sandbox.cleanup_verified", cleanup_verified)
            if exit_code is not None:
                span.set_attribute("manta.sandbox.exit_code", exit_code)

            if timed_out:
                failure_code = SandboxFailureCode.SANDBOX_TIMEOUT
                report = SandboxFailureReport(
                    failure_code=failure_code,
                    message=(
                        f"Sandbox execution exceeded {timeout_seconds:g}s timeout and was "
                        f"terminated via SIGKILL (container={container_name})"
                    ),
                    timeout_seconds=timeout_seconds,
                    duration_ms=duration_ms,
                    exit_code=exit_code,
                    runtime=runtime,
                    fallback_used=fallback_used,
                    image_digest=pinned.canonical,
                    container_name=container_name,
                    kill_signal="SIGKILL",
                    cleanup_verified=cleanup_verified,
                    stdout_tail=_tail_text(stdout_text),
                    stderr_tail=_tail_text(stderr_text),
                    occurred_at=datetime.now(timezone.utc),
                )
                span.set_attribute("manta.sandbox.status", TestRunStatus.VERIFICATION_FAILED.value)
                span.set_attribute("manta.sandbox.failure_code", failure_code.value)
                return SandboxExecutionResult(
                    test_run_id=test_run_id,
                    status=TestRunStatus.VERIFICATION_FAILED,
                    exit_code=exit_code,
                    duration_ms=duration_ms,
                    timed_out=True,
                    failure_code=failure_code,
                    failure_report=report,
                    runtime=runtime,
                    fallback_used=fallback_used,
                    image_digest=pinned.canonical,
                    container_name=container_name,
                    cleanup_verified=cleanup_verified,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    confinement=confinement.model_dump(),
                )

            if exit_code == 0:
                span.set_attribute("manta.sandbox.status", TestRunStatus.PASSED.value)
                return SandboxExecutionResult(
                    test_run_id=test_run_id,
                    status=TestRunStatus.PASSED,
                    exit_code=0,
                    duration_ms=duration_ms,
                    timed_out=False,
                    failure_code=None,
                    failure_report=None,
                    runtime=runtime,
                    fallback_used=fallback_used,
                    image_digest=pinned.canonical,
                    container_name=container_name,
                    cleanup_verified=cleanup_verified,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    confinement=confinement.model_dump(),
                )

            if exit_code == 137:
                failure_code = SandboxFailureCode.SANDBOX_OOM_KILLED
                message = (
                    f"Sandbox container terminated with SIGKILL / OOM (exit 137, "
                    f"memory_limit={self.memory_limit})"
                )
                kill_signal: str | None = "SIGKILL"
            elif exit_code in (125, 126, 127):
                failure_code = SandboxFailureCode.SANDBOX_RUNTIME_ERROR
                message = f"Sandbox container runtime invocation failed with exit code {exit_code}"
                kill_signal = None
            else:
                failure_code = SandboxFailureCode.TEST_EXECUTION_FAILED
                message = f"Verification command failed inside sandbox with exit code {exit_code}"
                kill_signal = None

            report = SandboxFailureReport(
                failure_code=failure_code,
                message=message,
                timeout_seconds=timeout_seconds,
                duration_ms=duration_ms,
                exit_code=exit_code,
                runtime=runtime,
                fallback_used=fallback_used,
                image_digest=pinned.canonical,
                container_name=container_name,
                kill_signal=kill_signal,
                cleanup_verified=cleanup_verified,
                stdout_tail=_tail_text(stdout_text),
                stderr_tail=_tail_text(stderr_text),
                occurred_at=datetime.now(timezone.utc),
            )
            span.set_attribute("manta.sandbox.status", TestRunStatus.VERIFICATION_FAILED.value)
            span.set_attribute("manta.sandbox.failure_code", failure_code.value)
            return SandboxExecutionResult(
                test_run_id=test_run_id,
                status=TestRunStatus.VERIFICATION_FAILED,
                exit_code=exit_code,
                duration_ms=duration_ms,
                timed_out=False,
                failure_code=failure_code,
                failure_report=report,
                runtime=runtime,
                fallback_used=fallback_used,
                image_digest=pinned.canonical,
                container_name=container_name,
                cleanup_verified=cleanup_verified,
                stdout=stdout_text,
                stderr=stderr_text,
                confinement=confinement.model_dump(),
            )


__all__ = [
    "EphemeralSandboxRunner",
    "SandboxPolicyError",
]
