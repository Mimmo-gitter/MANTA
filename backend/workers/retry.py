"""
Task 23.3: 5-attempt exponential backoff retry and dead-letter quarantine router.

Retry policy (backoff = 2^attempt seconds):
    attempt 1 fails -> retry in  2s
    attempt 2 fails -> retry in  4s
    attempt 3 fails -> retry in  8s
    attempt 4 fails -> retry in 16s
    attempt 5 fails -> quarantine into dead_letter_events,
                       webhook_events.status = 'dead_lettered'

Delayed retries never block the worker: failed jobs are parked in the Redis ZSET
'arq:queue:delayed' (score = due epoch seconds) and promoted back to their
priority queue tail once due, so subsequent valid jobs keep flowing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
import traceback
from typing import Any
import uuid

from sqlalchemy import text

logger = logging.getLogger("workers.retry")

QUEUE_DELAYED = "arq:queue:delayed"
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 2
BACKOFF_SCHEDULE: tuple[int, ...] = (2, 4, 8, 16, 32)
DEAD_LETTERED_STATUS = "dead_lettered"

# Atomically moves due members from the delayed ZSET to their target queue tail.
# KEYS[1] = delayed zset; ARGV[1] = now; ARGV[2] = limit; ARGV[3] = high q; ARGV[4] = default q
PROMOTE_DUE_RETRIES_LUA = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for _, member in ipairs(due) do
    if redis.call('ZREM', KEYS[1], member) == 1 then
        local target = ARGV[4]
        local ok, decoded = pcall(cjson.decode, member)
        if ok and type(decoded) == 'table' and decoded['priority'] == 'high' then
            target = ARGV[3]
        end
        redis.call('RPUSH', target, member)
    end
end
return #due
"""

_RECORD_ATTEMPT_SQL = text(
    """
    UPDATE webhook_events
    SET attempts = GREATEST(attempts, :attempt),
        status = 'queued',
        error_message = :error_message
    WHERE id = :event_id
    RETURNING attempts;
    """
)

_MARK_DEAD_LETTERED_SQL = text(
    """
    UPDATE webhook_events
    SET attempts = GREATEST(attempts, :attempt),
        status = 'dead_lettered',
        error_message = :error_message
    WHERE id = :event_id
    RETURNING id;
    """
)

_INSERT_DEAD_LETTER_SQL = text(
    """
    INSERT INTO dead_letter_events (
        id, original_event_id, tenant_id, provider, error_class, stack_trace,
        total_attempts, payload, quarantined_at, is_resolved
    ) VALUES (
        :id, :original_event_id, :tenant_id, :provider, :error_class, :stack_trace,
        :total_attempts, CAST(:payload AS jsonb), :quarantined_at, false
    );
    """
)

_SET_TENANT_SQL = text("SELECT set_config('app.current_tenant_id', :tenant_id, true)")


@dataclass(frozen=True)
class FailureOutcome:
    """Result of routing a failed job."""

    action: str  # 'retry' | 'dead_lettered'
    attempt: int
    delay_seconds: int | None = None
    retry_job: str | None = None
    dead_letter_id: uuid.UUID | None = None


def compute_backoff(attempt: int) -> int:
    """Returns the backoff delay (2^attempt seconds) for a failed 1-based attempt."""
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1 (got {attempt})")
    return BACKOFF_BASE_SECONDS**attempt


def _current_attempt(parsed_payload: Mapping[str, Any]) -> int:
    """Envelope 'attempt' counts prior failures (0 on first delivery) -> 1-based attempt."""
    try:
        prior = int(parsed_payload.get("attempt", 0) or 0)
    except (TypeError, ValueError):
        prior = 0
    return max(prior, 0) + 1


def _to_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return uuid.UUID(value.strip())
        except ValueError:
            return None
    return None


async def schedule_retry(
    redis: Any,
    parsed_payload: Mapping[str, Any],
    attempt: int,
    *,
    now: float | None = None,
) -> tuple[str, int]:
    """Parks the job in the delayed ZSET with attempt counter = attempt, due in 2^attempt s."""
    delay = compute_backoff(attempt)
    envelope = dict(parsed_payload)
    envelope.pop("owner_token", None)
    envelope["attempt"] = attempt
    raw = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    due_at = (time.time() if now is None else now) + delay
    await redis.zadd(QUEUE_DELAYED, {raw: due_at})
    return raw, delay


async def promote_due_retries(
    redis: Any,
    *,
    now: float | None = None,
    limit: int = 100,
    high_queue: str = "arq:queue:high",
    default_queue: str = "arq:queue:default",
) -> int:
    """Moves due delayed retries to the tail of their priority queue. Returns count promoted."""
    current = time.time() if now is None else now
    eval_fn = getattr(redis, "eval", None)
    if callable(eval_fn) and getattr(redis, "supports_lua", True):
        result = await eval_fn(
            PROMOTE_DUE_RETRIES_LUA, 1, QUEUE_DELAYED, current, limit, high_queue, default_queue
        )
        return int(result or 0)

    # Fallback: ZREM acts as the claim, so concurrent promoters never duplicate a job.
    due = await redis.zrangebyscore(QUEUE_DELAYED, "-inf", current, start=0, num=limit)
    promoted = 0
    for member in due:
        raw = member.decode("utf-8") if isinstance(member, bytes) else str(member)
        if await redis.zrem(QUEUE_DELAYED, raw):
            try:
                priority = json.loads(raw).get("priority")
            except (json.JSONDecodeError, AttributeError):
                priority = None
            await redis.rpush(high_queue if priority == "high" else default_queue, raw)
            promoted += 1
    return promoted


async def _db_execute(db: Any, stmt: Any, params: Mapping[str, Any]) -> Any:
    return await db.execute(stmt, dict(params))


async def _db_commit(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()


async def quarantine_event(
    db: Any,
    parsed_payload: Mapping[str, Any],
    exc: BaseException,
    attempt: int,
    *,
    stack_trace: str | None = None,
) -> uuid.UUID | None:
    """Inserts a DeadLetterEvent and sets webhook_events.status = 'dead_lettered' atomically."""
    event_id = _to_uuid(parsed_payload.get("event_id"))
    tenant_id = _to_uuid(parsed_payload.get("tenant_id"))
    if db is None or event_id is None or tenant_id is None:
        logger.error(
            "Cannot quarantine job %s: missing db/event_id/tenant_id",
            parsed_payload.get("job_id"),
        )
        return None

    dead_letter_id = uuid.uuid4()
    error_class = f"{type(exc).__module__}.{type(exc).__qualname__}"
    trace = stack_trace or "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    payload = parsed_payload.get("payload")
    if not isinstance(payload, Mapping):
        payload = {"value": payload}

    await _db_execute(db, _SET_TENANT_SQL, {"tenant_id": str(tenant_id)})
    await _db_execute(
        db,
        _INSERT_DEAD_LETTER_SQL,
        {
            "id": dead_letter_id,
            "original_event_id": event_id,
            "tenant_id": tenant_id,
            "provider": str(parsed_payload.get("provider") or "unknown"),
            "error_class": error_class,
            "stack_trace": trace,
            "total_attempts": attempt,
            "payload": json.dumps(dict(payload)),
            "quarantined_at": datetime.now(timezone.utc),
        },
    )
    await _db_execute(
        db,
        _MARK_DEAD_LETTERED_SQL,
        {"event_id": event_id, "attempt": attempt, "error_message": f"{error_class}: {exc}"},
    )
    await _db_commit(db)
    return dead_letter_id


async def record_failed_attempt(
    db: Any, parsed_payload: Mapping[str, Any], exc: BaseException, attempt: int
) -> None:
    """Persists webhook_events.attempts and returns status to 'queued' for the next CAS claim."""
    event_id = _to_uuid(parsed_payload.get("event_id"))
    tenant_id = _to_uuid(parsed_payload.get("tenant_id"))
    if db is None or event_id is None or tenant_id is None:
        return
    await _db_execute(db, _SET_TENANT_SQL, {"tenant_id": str(tenant_id)})
    await _db_execute(
        db,
        _RECORD_ATTEMPT_SQL,
        {
            "event_id": event_id,
            "attempt": attempt,
            "error_message": f"{type(exc).__qualname__}: {exc}",
        },
    )
    await _db_commit(db)


async def _db_rollback(db: Any) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        try:
            await rollback()
        except Exception:  # pragma: no cover - best effort
            logger.exception("Rollback failed before failure routing")


async def handle_job_failure(
    redis: Any,
    db: Any,
    parsed_payload: Mapping[str, Any],
    exc: BaseException,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    now: float | None = None,
) -> FailureOutcome:
    """
    Routes a failed job: schedules a 2^attempt backoff retry for attempts 1..max-1,
    quarantines into dead_letter_events on attempt == max_attempts.
    """
    stack_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    attempt = _current_attempt(parsed_payload)
    if db is not None:
        await _db_rollback(db)

    if attempt >= max_attempts:
        dl_id = await quarantine_event(db, parsed_payload, exc, attempt, stack_trace=stack_trace)
        logger.warning(
            "Job %s quarantined after %d attempts (%s)",
            parsed_payload.get("job_id"),
            attempt,
            type(exc).__name__,
        )
        return FailureOutcome(action="dead_lettered", attempt=attempt, dead_letter_id=dl_id)

    await record_failed_attempt(db, parsed_payload, exc, attempt)
    raw, delay = await schedule_retry(redis, parsed_payload, attempt, now=now)
    logger.info(
        "Job %s failed attempt %d/%d; retry in %ds",
        parsed_payload.get("job_id"),
        attempt,
        max_attempts,
        delay,
    )
    return FailureOutcome(action="retry", attempt=attempt, delay_seconds=delay, retry_job=raw)


__all__ = [
    "BACKOFF_SCHEDULE",
    "DEAD_LETTERED_STATUS",
    "MAX_ATTEMPTS",
    "PROMOTE_DUE_RETRIES_LUA",
    "QUEUE_DELAYED",
    "FailureOutcome",
    "compute_backoff",
    "handle_job_failure",
    "promote_due_retries",
    "quarantine_event",
    "record_failed_attempt",
    "schedule_retry",
]
