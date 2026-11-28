"""Security policy validators for pinned @sha256 images, seccomp profiles, and sandbox bounds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import Any

from app.config import settings
from app.verification.schemas import (
    MAX_SANDBOX_TIMEOUT_SECONDS,
    PinnedImageReference,
    SandboxFailureCode,
)

# Strict OCI image reference with mandatory lowercase 64-hex @sha256 digest.
_PINNED_IMAGE_RE = re.compile(
    r"^(?:(?P<registry>(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z0-9-]+(?::[0-9]{1,5})?|localhost(?::[0-9]{1,5})?)/)?"
    r"(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]{0,127}))?"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_SHELL_CHARS = frozenset({";", "|", "&", "$", "`", "\n", "\r", "\x00"})
_NAMESPACE_CLONE_MASK = 0x7E020080  # 2114060416

# Syscalls that must never appear inside SCMP_ACT_ALLOW in sandbox/seccomp/.
FORBIDDEN_SANDBOX_SYSCALLS: frozenset[str] = frozenset(
    {
        "acct",
        "add_key",
        "adjtimex",
        "bpf",
        "chroot",
        "clock_adjtime",
        "clock_settime",
        "delete_module",
        "finit_module",
        "init_module",
        "io_uring_enter",
        "io_uring_register",
        "io_uring_setup",
        "ioperm",
        "iopl",
        "kcmp",
        "kexec_file_load",
        "kexec_load",
        "keyctl",
        "lookup_dcookie",
        "mknod",
        "mknodat",
        "modify_ldt",
        "mount",
        "name_to_handle_at",
        "nfsservctl",
        "open_by_handle_at",
        "perf_event_open",
        "pivot_root",
        "process_vm_readv",
        "process_vm_writev",
        "ptrace",
        "quotactl",
        "reboot",
        "request_key",
        "setdomainname",
        "sethostname",
        "setns",
        "settimeofday",
        "swapoff",
        "swapon",
        "syslog",
        "umount2",
        "unshare",
        "userfaultfd",
        "vhangup",
    }
)

# Host environment keys blocked from leaking into untrusted sandbox execution.
BLOCKED_SANDBOX_ENV_KEYS: frozenset[str] = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "CLOUD_ZDR_API_KEY",
        "DATABASE_URL",
        "ENCRYPTION_KEYRING_JSON",
        "ENCRYPTION_MASTER_KEY",
        "FCM_CREDENTIALS_JSON",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "KC_SERVICE_CLIENT_SECRET",
        "KEYCLOAK_ADMIN_PASSWORD",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "MIGRATION_DATABASE_URL",
        "PYTHONPATH",
        "REDIS_URL",
        "SMTP_PASSWORD",
    }
)


class SandboxPolicyError(ValueError):
    """Base error raised when a sandbox security policy invariant fails validation."""

    def __init__(self, message: str, *, failure_code: SandboxFailureCode) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class UnpinnedImageError(SandboxPolicyError):
    """Raised when an image reference lacks a valid @sha256 digest or uses :latest."""

    def __init__(self, message: str) -> None:
        super().__init__(message, failure_code=SandboxFailureCode.SANDBOX_IMAGE_UNPINNED)


class SandboxDigestMismatchError(SandboxPolicyError):
    """Raised when local image RepoDigests do not match the pinned @sha256 digest."""

    def __init__(self, message: str) -> None:
        super().__init__(message, failure_code=SandboxFailureCode.SANDBOX_DIGEST_MISMATCH)


class SeccompProfileError(SandboxPolicyError):
    """Raised when the sandbox seccomp profile is missing, malformed, or unsafe."""

    def __init__(self, message: str) -> None:
        super().__init__(message, failure_code=SandboxFailureCode.SANDBOX_CONFINEMENT_ERROR)


class SandboxRuntimeUnavailableError(SandboxPolicyError):
    """Raised when required gVisor (`runsc`) or fallback Docker runtime is unavailable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, failure_code=SandboxFailureCode.SANDBOX_RUNTIME_UNAVAILABLE)


def parse_and_validate_pinned_image(image_ref: str | None) -> PinnedImageReference:
    """
    Validate that `image_ref` is immutably pinned by lowercase `@sha256:<64hex>` digest
    and never uses `:latest` or mutable unpinned tags.
    """
    if not isinstance(image_ref, str):
        raise UnpinnedImageError("Sandbox image reference must be a non-empty string")

    cleaned = image_ref.strip()
    if not cleaned:
        raise UnpinnedImageError(
            "Sandbox image reference cannot be empty; an @sha256:<64-hex> pinned image is required"
        )

    if any(ch.isspace() or ch in _FORBIDDEN_SHELL_CHARS for ch in cleaned):
        raise UnpinnedImageError(
            f"Sandbox image reference {cleaned!r} contains forbidden whitespace or shell characters"
        )

    if ":latest" in cleaned.lower():
        raise UnpinnedImageError(
            f"Sandbox image {cleaned!r} uses forbidden mutable ':latest' tag; pin via @sha256:<64-hex>"
        )

    match = _PINNED_IMAGE_RE.match(cleaned)
    if match is None:
        raise UnpinnedImageError(
            f"Sandbox image {cleaned!r} must be immutably pinned with @sha256:<64-lowercase-hex> "
            "(unpinned tags and non-sha256 digests are forbidden)"
        )

    registry = match.group("registry")
    repository = match.group("repository")
    tag = match.group("tag")
    digest = match.group("digest")

    if tag is not None and tag.strip().lower() == "latest":
        raise UnpinnedImageError(
            f"Sandbox image {cleaned!r} uses forbidden ':latest' tag alongside digest"
        )

    repo_with_registry = f"{registry}/{repository}" if registry else repository
    canonical = f"{repo_with_registry}@sha256:{digest}"
    return PinnedImageReference(
        raw=cleaned,
        registry=registry,
        repository=repository,
        tag=tag,
        digest=digest,
        canonical=canonical,
    )


def resolve_seccomp_profile_path(override_path: Path | str | None = None) -> Path:
    """Resolve the authoritative `manta-sandbox-seccomp.json` profile path."""
    if override_path is not None and str(override_path).strip():
        return Path(str(override_path).strip())

    configured = (settings.SANDBOX_SECCOMP_PROFILE or "").strip()
    if configured:
        cfg_path = Path(configured)
        if cfg_path.is_file():
            return cfg_path

    # Locate <repo_root>/sandbox/seccomp/manta-sandbox-seccomp.json relative to this module
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        candidate = parent / "sandbox" / "seccomp" / "manta-sandbox-seccomp.json"
        if candidate.is_file():
            return candidate

    docker_mount_default = Path("/sandbox/seccomp/manta-sandbox-seccomp.json")
    if docker_mount_default.is_file():
        return docker_mount_default

    if configured:
        return Path(configured)
    return module_path.parents[3] / "sandbox" / "seccomp" / "manta-sandbox-seccomp.json"


def load_and_validate_seccomp_profile(profile_path: Path) -> dict[str, Any]:
    """Load and validate an OCI/Docker seccomp JSON profile for default-deny safety."""
    if not profile_path.is_file():
        raise SeccompProfileError(f"Seccomp profile file not found: {profile_path}")

    try:
        raw_text = profile_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise SeccompProfileError(f"Failed to parse seccomp profile {profile_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SeccompProfileError("Seccomp profile root must be a JSON object")

    if data.get("defaultAction") != "SCMP_ACT_ERRNO":
        raise SeccompProfileError(
            f"Seccomp profile must enforce default-deny 'SCMP_ACT_ERRNO' (got {data.get('defaultAction')!r})"
        )

    arch_map = data.get("archMap")
    if "architectures" in data and arch_map is not None:
        raise SeccompProfileError(
            "Seccomp profile must not define both 'archMap' and 'architectures'"
        )
    if not isinstance(arch_map, list) or not arch_map:
        raise SeccompProfileError("Seccomp profile must define a non-empty 'archMap' list")

    syscalls = data.get("syscalls")
    if not isinstance(syscalls, list) or not syscalls:
        raise SeccompProfileError("Seccomp profile must define a non-empty 'syscalls' rule list")

    has_clone3_enosys = False
    for rule in syscalls:
        if not isinstance(rule, dict):
            raise SeccompProfileError("Each seccomp syscall entry must be an object")
        names = rule.get("names")
        action = rule.get("action")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise SeccompProfileError("Each seccomp syscall entry must declare string 'names'")

        if action == "SCMP_ACT_ALLOW":
            bad = FORBIDDEN_SANDBOX_SYSCALLS.intersection(names)
            if bad:
                raise SeccompProfileError(
                    f"Seccomp profile allows forbidden syscall(s): {sorted(bad)}"
                )
            if "clone" in names:
                args = rule.get("args")
                if not isinstance(args, list) or not any(
                    isinstance(a, dict)
                    and a.get("index") == 0
                    and a.get("op") == "SCMP_CMP_MASKED_EQ"
                    and a.get("value") == _NAMESPACE_CLONE_MASK
                    and a.get("valueTwo") == 0
                    for a in args
                ):
                    raise SeccompProfileError(
                        "Seccomp profile 'clone' allow rule must enforce namespace mask 0x7E020080"
                    )

        if "clone3" in names and action == "SCMP_ACT_ERRNO" and rule.get("errnoRet") == 38:
            has_clone3_enosys = True

    if not has_clone3_enosys:
        raise SeccompProfileError(
            "Seccomp profile must map 'clone3' to SCMP_ACT_ERRNO with errnoRet=38 (ENOSYS)"
        )

    return data


def validate_timeout_seconds(timeout_seconds: float) -> float:
    """Enforce strictly bounded sandbox wall-clock timeout in `(0, 120.0]` seconds."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError(f"timeout_seconds must be numeric (got {timeout_seconds!r})")
    val = float(timeout_seconds)
    if val <= 0.0 or val > MAX_SANDBOX_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout_seconds must be within (0, {MAX_SANDBOX_TIMEOUT_SECONDS}] (got {val})"
        )
    return val


def validate_command(command: Sequence[str]) -> list[str]:
    """Validate non-empty exec-form container command arguments."""
    if not command:
        raise ValueError("Sandbox execution command must not be empty")
    cleaned: list[str] = []
    for item in command:
        if not isinstance(item, str) or not item:
            raise ValueError("Sandbox command arguments must be non-empty strings")
        if "\x00" in item:
            raise ValueError("Sandbox command argument contains null byte")
        cleaned.append(item)
    return cleaned


def validate_sandbox_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """Validate caller-supplied environment overrides, blocking host secret keys."""
    if not env:
        return {}
    sanitized: dict[str, str] = {}
    for raw_k, raw_v in env.items():
        if not isinstance(raw_k, str) or not _ENV_KEY_RE.match(raw_k):
            raise ValueError(f"Invalid sandbox environment variable name: {raw_k!r}")
        if raw_k.upper() in BLOCKED_SANDBOX_ENV_KEYS:
            raise SandboxPolicyError(
                f"Blocked sensitive environment variable {raw_k!r} from entering sandbox",
                failure_code=SandboxFailureCode.SANDBOX_CONFINEMENT_ERROR,
            )
        if not isinstance(raw_v, str) or "\x00" in raw_v:
            raise ValueError(f"Invalid sandbox environment value for {raw_k!r}")
        sanitized[raw_k] = raw_v
    return sanitized


def validate_workspace_path(workspace_path: Path | str | None) -> Path | None:
    """Validate optional host workspace directory to bind-mount read-only into `/workspace`."""
    if workspace_path is None:
        return None
    resolved = Path(workspace_path).resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(
            f"Sandbox workspace_path must be an existing directory: {workspace_path!r}"
        )
    return resolved
