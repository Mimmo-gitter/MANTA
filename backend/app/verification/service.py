"""PostgreSQL RLS persistence and ARQ worker dispatch for sandbox verification (TASK-322-1)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.models import RefactoringSuggestion, TestResult, TestRun
from app.db.session import get_tenant_db_context
from app.observability.tracing import inject_trace_carrier
from app.verification.policy import (
    load_and_validate_seccomp_profile,
    parse_and_validate_pinned_image,
    resolve_seccomp_profile_path,
    validate_command,
    validate_sandbox_env,
    validate_timeout_seconds,
    validate_workspace_path,
)
from app.verification.runner import EphemeralSandboxRunner
from app.verification.schemas import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxFailureCode,
    TestRunStatus,
)
from app.webhooks.enqueue import get_redis
from workers.worker import QUEUE_DEFAULT, QUEUE_HIGH, enqueue_job, serialize_job_payload

SANDBOX_VERIFICATION_JOB_TYPE = "verification.sandbox"


async def execute_sandbox_test_run(
    request: SandboxExecutionRequest,
    *,
    runner: EphemeralSandboxRunner | None = None,
    tenant_session_factory: Callable[[uuid.UUID], Any] = get_tenant_db_context,
    test_run_id: uuid.UUID | None = None,
) -> SandboxExecutionResult:
    """
    Validate sandbox policy, execute inside ephemeral gVisor/Docker sandbox, and persist
    `TestRun` (plus diagnostic `TestResult` & `RefactoringSuggestion.status`) under tenant RLS.

    Uses two short tenant-scoped DB transactions so no PostgreSQL/PgBouncer transaction is
    held open while waiting up to 120s for container execution.
    """
    active_runner = runner or EphemeralSandboxRunner()

    # Fail closed BEFORE creating any TestRun row or launching a container
    raw_image = request.image if request.image is not None else settings.SANDBOX_IMAGE_DIGEST
    pinned = parse_and_validate_pinned_image(raw_image)
    validate_timeout_seconds(request.timeout_seconds)
    validate_command(request.command)
    validate_workspace_path(request.resolved_workspace())
    validate_sandbox_env(request.env)
    load_and_validate_seccomp_profile(active_runner.seccomp_profile_path)

    run_id = test_run_id or uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    # Phase 1: persist initial RUNNING record under tenant RLS
    async with tenant_session_factory(request.tenant_id) as db:
        await _create_running_test_run(
            db,
            run_id=run_id,
            request=request,
            image_digest=pinned.canonical,
            started_at=started_at,
        )

    # Phase 2: execute ephemeral sandbox outside DB transaction
    result = await active_runner.execute(request, test_run_id=run_id)
    completed_at = datetime.now(timezone.utc)

    # Phase 3: persist terminal outcome (`PASSED` or `VERIFICATION_FAILED`) under tenant RLS
    async with tenant_session_factory(request.tenant_id) as db:
        await _finalize_test_run(
            db,
            run_id=run_id,
            request=request,
            result=result,
            completed_at=completed_at,
        )

    return result.model_copy(update={"test_run_id": run_id})


async def _create_running_test_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    request: SandboxExecutionRequest,
    image_digest: str,
    started_at: datetime,
) -> TestRun:
    row = TestRun(
        id=run_id,
        tenant_id=request.tenant_id,
        repository_id=request.repository_id,
        suggestion_id=request.suggestion_id,
        kind=request.kind,
        run_index=request.run_index,
        status=TestRunStatus.RUNNING.value,
        sandbox_image_digest=image_digest,
        created_at=started_at,
    )
    db.add(row)
    await db.flush()
    return row


async def _finalize_test_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    request: SandboxExecutionRequest,
    result: SandboxExecutionResult,
    completed_at: datetime,
) -> None:
    failure_details = (
        result.failure_report.model_dump(mode="json") if result.failure_report is not None else None
    )
    failure_code = result.failure_code.value if result.failure_code is not None else None

    await db.execute(
        update(TestRun)
        .where(
            TestRun.tenant_id == request.tenant_id,
            TestRun.id == run_id,
        )
        .values(
            status=result.status.value,
            duration_ms=result.duration_ms,
            sandbox_runtime=result.runtime.value,
            sandbox_image_digest=result.image_digest,
            exit_code=result.exit_code,
            failure_code=failure_code,
            failure_details=failure_details,
            completed_at=completed_at,
        )
    )

    if result.status == TestRunStatus.VERIFICATION_FAILED:
        test_name = (
            "__sandbox_timeout__"
            if result.failure_code == SandboxFailureCode.SANDBOX_TIMEOUT
            else "__sandbox_execution__"
        )
        failure_msg = (
            result.failure_report.message
            if result.failure_report is not None
            else f"Sandbox verification failed ({failure_code})"
        )
        if result.stderr:
            failure_msg = f"{failure_msg}\n{result.stderr[-2048:]}"
        db.add(
            TestResult(
                id=uuid.uuid4(),
                tenant_id=request.tenant_id,
                test_run_id=run_id,
                test_name=test_name,
                status="ERROR",
                duration_ms=result.duration_ms,
                failure_message=failure_msg[:4096],
                created_at=completed_at,
            )
        )

    if request.suggestion_id is not None:
        next_suggestion_status = (
            "VERIFIED" if result.status == TestRunStatus.PASSED else "VERIFICATION_FAILED"
        )
        await db.execute(
            update(RefactoringSuggestion)
            .where(
                RefactoringSuggestion.tenant_id == request.tenant_id,
                RefactoringSuggestion.id == request.suggestion_id,
            )
            .values(status=next_suggestion_status)
        )

    await db.flush()


async def enqueue_sandbox_verification_job(
    *,
    request: SandboxExecutionRequest,
    priority: str = "default",
    redis_client: Optional[Any] = None,
) -> dict[str, Any]:
    """Enqueue a `verification.sandbox` job onto `arq:queue:default` or `arq:queue:high`."""
    norm_priority = priority.strip().lower() if isinstance(priority, str) else "default"
    if norm_priority not in ("default", "high"):
        raise ValueError(f"Unsupported verification queue priority: {priority!r}")

    # Fail closed at enqueue time as well if image/seccomp/timeout are invalid
    raw_image = request.image if request.image is not None else settings.SANDBOX_IMAGE_DIGEST
    pinned = parse_and_validate_pinned_image(raw_image)
    validate_timeout_seconds(request.timeout_seconds)
    validate_command(request.command)
    validate_sandbox_env(request.env)
    load_and_validate_seccomp_profile(resolve_seccomp_profile_path())

    job_id = str(uuid.uuid4())
    payload = request.model_dump(mode="json")
    payload["image"] = pinned.canonical

    carrier = inject_trace_carrier()
    envelope = serialize_job_payload(
        job_id=job_id,
        tenant_id=str(request.tenant_id),
        priority=norm_priority,
        job_type=SANDBOX_VERIFICATION_JOB_TYPE,
        payload=payload,
        provider="sandbox",
        traceparent=carrier.get("traceparent"),
        tracestate=carrier.get("tracestate"),
    )
    client = redis_client or get_redis()
    await enqueue_job(client, envelope, priority=norm_priority)
    return {
        "job_id": job_id,
        "repository_id": request.repository_id,
        "image_digest": pinned.canonical,
        "priority": norm_priority,
        "queue": QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT,
        "status": "queued",
    }


async def process_sandbox_verification_job(
    parsed_payload: dict[str, Any],
    *,
    runner: EphemeralSandboxRunner | None = None,
    tenant_session_factory: Callable[[uuid.UUID], Any] = get_tenant_db_context,
) -> dict[str, Any]:
    """ARQ worker handler for `job_type == 'verification.sandbox'`."""
    inner = (
        parsed_payload.get("payload")
        if isinstance(parsed_payload.get("payload"), dict)
        else parsed_payload
    )
    request = SandboxExecutionRequest.model_validate(inner)
    result = await execute_sandbox_test_run(
        request,
        runner=runner,
        tenant_session_factory=tenant_session_factory,
    )
    return result.model_dump(mode="json")


__all__ = [
    "SANDBOX_VERIFICATION_JOB_TYPE",
    "enqueue_sandbox_verification_job",
    "execute_sandbox_test_run",
    "process_sandbox_verification_job",
]
