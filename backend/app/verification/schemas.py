"""Pydantic schemas & constants for US-48 Hardened Ephemeral Sandbox Execution (TASK-322-0 / TASK-322-1)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, Field

DEFAULT_SANDBOX_TIMEOUT_SECONDS: float = 120.0
MAX_SANDBOX_TIMEOUT_SECONDS: float = 120.0
MAX_CAPTURE_BYTES: int = 65_536
FAILURE_TAIL_BYTES: int = 8_192
SANDBOX_USER_UID_GID: str = "65534:65534"


class TestRunStatus(StrEnum):
    """Canonical lifecycle states for PostgreSQL `test_runs.status`."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class SandboxFailureCode(StrEnum):
    """Structured failure codes recorded in `test_runs.failure_code` and `failure_details`."""

    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
    TEST_EXECUTION_FAILED = "TEST_EXECUTION_FAILED"
    SANDBOX_IMAGE_UNPINNED = "SANDBOX_IMAGE_UNPINNED"
    SANDBOX_DIGEST_MISMATCH = "SANDBOX_DIGEST_MISMATCH"
    SANDBOX_OOM_KILLED = "SANDBOX_OOM_KILLED"
    SANDBOX_CONFINEMENT_ERROR = "SANDBOX_CONFINEMENT_ERROR"
    SANDBOX_RUNTIME_UNAVAILABLE = "SANDBOX_RUNTIME_UNAVAILABLE"
    SANDBOX_RUNTIME_ERROR = "SANDBOX_RUNTIME_ERROR"


class SandboxRuntimeMode(StrEnum):
    """Supported OCI container execution runtimes."""

    RUNSC = "runsc"
    RUNC = "runc"


class ImagePullPolicy(StrEnum):
    """Allowed image pull strategies (strictly digest-addressed)."""

    NEVER = "never"
    IF_NOT_PRESENT = "if-not-present"
    ALWAYS = "always"


class PinnedImageReference(BaseModel):
    """Validated immutable `@sha256:...` container image reference."""

    raw: str
    registry: Optional[str] = None
    repository: str
    tag: Optional[str] = None
    digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    canonical: str


class SandboxConfinementPolicy(BaseModel):
    """Explicit runtime, network, filesystem, seccomp, and resource confinement policy."""

    network_mode: Literal["none"] = "none"
    read_only_rootfs: bool = True
    tmpfs_mount: str = "/tmp:rw,noexec,nosuid,nodev,size=128m"
    cap_drop: tuple[str, ...] = ("ALL",)
    no_new_privileges: bool = True
    ipc_mode: Literal["none"] = "none"
    user: str = SANDBOX_USER_UID_GID
    pids_limit: int = Field(default=128, ge=1, le=4096)
    memory_limit: str = "512m"
    memory_swap_limit: str = "512m"
    cpu_limit: str = "1.0"
    seccomp_profile_path: Optional[str] = None
    runtime: SandboxRuntimeMode = SandboxRuntimeMode.RUNSC
    fallback_used: bool = False


class SandboxExecutionRequest(BaseModel):
    """Input payload for running an isolated candidate verification test inside a sandbox."""

    tenant_id: uuid.UUID
    repository_id: uuid.UUID
    suggestion_id: Optional[uuid.UUID] = None
    kind: str = Field(default="unit", min_length=1, max_length=32)
    run_index: int = Field(default=1, ge=1)
    image: Optional[str] = None
    command: list[str] = Field(..., min_length=1)
    workspace_path: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(
        default=DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        gt=0.0,
        le=MAX_SANDBOX_TIMEOUT_SECONDS,
    )

    def resolved_workspace(self) -> Optional[Path]:
        return Path(self.workspace_path) if self.workspace_path else None


class SandboxFailureReport(BaseModel):
    """Structured failure report persisted to `TestRun.failure_details` (TASK-322-0 schema)."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["VERIFICATION_FAILED"] = "VERIFICATION_FAILED"
    failure_code: SandboxFailureCode
    message: str = Field(..., min_length=1)
    timeout_seconds: float = Field(..., gt=0.0, le=MAX_SANDBOX_TIMEOUT_SECONDS)
    duration_ms: int = Field(..., ge=0)
    exit_code: Optional[int] = None
    runtime: SandboxRuntimeMode
    fallback_used: bool
    image_digest: str
    container_name: str
    kill_signal: Optional[str] = None
    cleanup_verified: bool
    stdout_tail: str = Field(default="", max_length=FAILURE_TAIL_BYTES)
    stderr_tail: str = Field(default="", max_length=FAILURE_TAIL_BYTES)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SandboxExecutionResult(BaseModel):
    """Complete execution outcome returned by `EphemeralSandboxRunner` and `execute_sandbox_test_run`."""

    test_run_id: Optional[uuid.UUID] = None
    status: TestRunStatus
    exit_code: Optional[int] = None
    duration_ms: int = Field(..., ge=0)
    timed_out: bool = False
    failure_code: Optional[SandboxFailureCode] = None
    failure_report: Optional[SandboxFailureReport] = None
    runtime: SandboxRuntimeMode
    fallback_used: bool
    image_digest: str
    container_name: str
    cleanup_verified: bool = True
    stdout: str = ""
    stderr: str = ""
    confinement: dict[str, Any] = Field(default_factory=dict)
