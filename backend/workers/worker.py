"""
SCRUM-405 (Task 21.2): Reliable Valkey / Redis 7 FIFO Queue Producer & Atomic Worker Fetch.

Implements the Task 21.1 (SCRUM-404) specification (docs/fifo-queue-topology.md):
- Producer enqueue via RPUSH to arq:queue:default or arq:queue:high (appends to RIGHT / tail).
- Two-phase consumer fetch into per-worker arq:in_flight:{worker_id} lists:
  1. Non-blocking LMOVE arq:queue:high arq:in_flight:{worker_id} LEFT RIGHT
     (guarantees immediate high-priority preemption ahead of waiting default jobs).
  2. Bounded-timeout BLMOVE arq:queue:default arq:in_flight:{worker_id} LEFT RIGHT <timeout>
     (guarantees 100% strict FIFO execution order).
- Explicit acknowledgment via LREM arq:in_flight:{worker_id} 1 <raw_job> after processing.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
import inspect
import json
import logging
import os
import socket
from typing import Any
import uuid

from arq.connections import RedisSettings

from app.config import settings

logger = logging.getLogger("workers.worker")

QUEUE_HIGH = "arq:queue:high"
QUEUE_DEFAULT = "arq:queue:default"
IN_FLIGHT_PREFIX = "arq:in_flight:"
WORKER_HEARTBEAT_PREFIX = "arq:worker:"
DEFAULT_POLL_TIMEOUT = 1.0
VALID_PRIORITIES = frozenset({"default", "high"})

JobFetchResult = tuple[str, str, dict[str, Any]]
JobHandler = Callable[..., Awaitable[Any]]


def generate_worker_id() -> str:
    """
    Returns the worker identifier per docs/fifo-queue-topology.md §2.2.

    Uses the explicit WORKER_ID environment variable when set; otherwise generates
    '<hostname>-<pid>-<uuid_hex[:8]>'.
    """
    env_worker_id = os.environ.get("WORKER_ID", "").strip()
    if env_worker_id:
        return env_worker_id
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def get_in_flight_key(worker_id: str) -> str:
    """
    Constructs the per-worker in-flight list key: 'arq:in_flight:{worker_id}'.
    """
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string")
    normalized = worker_id.strip()
    if normalized.startswith(IN_FLIGHT_PREFIX):
        suffix = normalized[len(IN_FLIGHT_PREFIX) :].strip()
        if not suffix:
            raise ValueError("worker_id must not be an empty in-flight prefix")
        return f"{IN_FLIGHT_PREFIX}{suffix}"
    return f"{IN_FLIGHT_PREFIX}{normalized}"


def serialize_job_payload(
    job_id: uuid.UUID | str,
    tenant_id: uuid.UUID | str,
    priority: str,
    job_type: str,
    payload: dict[str, Any],
    event_id: uuid.UUID | str | None = None,
    provider: str | None = None,
    attempt: int = 0,
    enqueued_at: str | None = None,
) -> str:
    """
    Serializes a canonical MantaJobEnvelope into deterministic compact JSON
    (separators=(',', ':'), sort_keys=True) per docs/fifo-queue-topology.md §3.
    """
    norm_priority = priority.strip().lower() if isinstance(priority, str) else ""
    if norm_priority not in VALID_PRIORITIES:
        raise ValueError(f"Unsupported queue priority: {priority!r} (expected 'default' or 'high')")
    envelope = {
        "attempt": int(attempt),
        "enqueued_at": enqueued_at or datetime.now(timezone.utc).isoformat(),
        "event_id": str(event_id) if event_id is not None else None,
        "job_id": str(job_id),
        "job_type": str(job_type),
        "payload": payload,
        "priority": norm_priority,
        "provider": provider,
        "tenant_id": str(tenant_id),
        "version": 1,
    }
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True)


def _decode_raw_job(raw_value: str | bytes) -> str:
    """Normalizes a Redis response payload (bytes or str) into a UTF-8 string."""
    if isinstance(raw_value, bytes):
        return raw_value.decode("utf-8")
    return str(raw_value)


def _parse_job_payload(raw_str: str) -> dict[str, Any]:
    """
    Parses a raw JSON job string into a dictionary.
    If the raw string is a non-dict JSON primitive or plain string, wraps it safely
    in a dict so callers unpacking (source_queue, raw_str, parsed_dict) never fail.
    """
    try:
        parsed = json.loads(raw_str)
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw_str}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


async def enqueue_job(
    redis: Any,
    job_payload: Mapping[str, Any] | str | bytes,
    priority: str = "default",
    queue_name: str | None = None,
) -> str:
    """
    Enqueues a job into Valkey/Redis via RPUSH (appending to RIGHT / tail).

    Args:
        redis: Async Redis/Valkey client connection (redis.asyncio.Redis / ArqRedis).
        job_payload: Dictionary envelope, raw JSON string, or UTF-8 bytes.
        priority: Queue priority tier ('default' or 'high').
        queue_name: Optional explicit queue key override.

    Returns:
        The exact raw payload string pushed to the target Redis list.
    """
    norm_priority = priority.strip().lower() if isinstance(priority, str) else ""
    if queue_name is None and norm_priority not in VALID_PRIORITIES:
        raise ValueError(f"Unsupported queue priority: {priority!r} (expected 'default' or 'high')")

    target_queue = queue_name or (QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT)

    if isinstance(job_payload, str):
        raw_str = job_payload
    elif isinstance(job_payload, bytes):
        raw_str = job_payload.decode("utf-8")
    elif isinstance(job_payload, Mapping):
        raw_str = json.dumps(dict(job_payload), separators=(",", ":"), sort_keys=True)
    else:
        raise TypeError(
            f"job_payload must be a mapping, str, or bytes (got {type(job_payload).__name__})"
        )

    await redis.rpush(target_queue, raw_str)
    return raw_str


async def fetch_next_job(
    redis: Any,
    worker_id: str,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
) -> JobFetchResult | None:
    """
    Atomically fetches the next job into arq:in_flight:{worker_id} with high-priority precedence:
      1. Phase 1 (Non-blocking):
         LMOVE arq:queue:high arq:in_flight:{worker_id} LEFT RIGHT
      2. Phase 2 (Bounded blocking fallback):
         BLMOVE arq:queue:default arq:in_flight:{worker_id} LEFT RIGHT <poll_timeout>

    Args:
        redis: Async Redis/Valkey client connection.
        worker_id: Unique worker identifier owning the in-flight list.
        poll_timeout: Blocking timeout in seconds for Phase 2 BLMOVE (must be >= 0).

    Returns:
        Tuple of (source_queue, raw_payload_str, parsed_payload_dict), or None on timeout.
    """
    if poll_timeout < 0:
        raise ValueError(f"poll_timeout must be non-negative (got {poll_timeout})")

    in_flight_key = get_in_flight_key(worker_id)

    # Phase 1: Check high-priority queue first (non-blocking atomic move LEFT -> RIGHT)
    high_job = await redis.lmove(QUEUE_HIGH, in_flight_key, src="LEFT", dest="RIGHT")
    if high_job is not None:
        raw = _decode_raw_job(high_job)
        return (QUEUE_HIGH, raw, _parse_job_payload(raw))

    # Phase 2: Fallback to default queue (bounded-timeout blocking atomic move LEFT -> RIGHT)
    default_job = await redis.blmove(
        QUEUE_DEFAULT,
        in_flight_key,
        timeout=poll_timeout,
        src="LEFT",
        dest="RIGHT",
    )
    if default_job is not None:
        raw = _decode_raw_job(default_job)
        return (QUEUE_DEFAULT, raw, _parse_job_payload(raw))

    return None


async def ack_job(
    redis: Any,
    worker_id: str,
    raw_job: str | bytes | Mapping[str, Any] | tuple[Any, ...],
) -> int:
    """
    Acknowledges a completed job by removing it from arq:in_flight:{worker_id} via LREM.
    MUST be called strictly after the downstream PostgreSQL transaction commits.

    Args:
        redis: Async Redis/Valkey client connection.
        worker_id: Unique worker identifier owning the in-flight list.
        raw_job: Exact raw payload string/bytes, parsed dict, or fetch_next_job tuple.

    Returns:
        Number of removed elements (1 if acknowledged and removed, 0 if not found).
    """
    in_flight_key = get_in_flight_key(worker_id)

    if isinstance(raw_job, tuple):
        if len(raw_job) < 2:
            raise ValueError("Tuple passed to ack_job must contain raw payload at index 1")
        candidate = raw_job[1]
    else:
        candidate = raw_job

    if isinstance(candidate, bytes):
        payload_val = candidate.decode("utf-8")
    elif isinstance(candidate, str):
        payload_val = candidate
    elif isinstance(candidate, Mapping):
        payload_val = json.dumps(dict(candidate), separators=(",", ":"), sort_keys=True)
    else:
        raise TypeError(f"Unsupported raw_job type for ack_job: {type(raw_job).__name__}")

    removed_count = await redis.lrem(in_flight_key, 1, payload_val)
    return int(removed_count)


async def _invoke_job_handler(
    job_handler: JobHandler,
    source_queue: str,
    raw_job: str,
    parsed_payload: dict[str, Any],
) -> Any:
    """Invokes a job handler supporting 3-arg (queue, raw, parsed) or 1-arg (parsed) callables."""
    sig = inspect.signature(job_handler)
    positional_params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())

    if has_varargs or len(positional_params) >= 3:
        return await job_handler(source_queue, raw_job, parsed_payload)
    if len(positional_params) == 2:
        return await job_handler(source_queue, parsed_payload)
    if len(positional_params) == 1:
        return await job_handler(parsed_payload)
    return await job_handler()


async def run_worker_fetch_loop(
    redis: Any,
    worker_id: str,
    job_handler: JobHandler | None = None,
    *,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    stop_event: asyncio.Event | None = None,
    max_iterations: int | None = None,
    ack_on_success: bool = True,
) -> list[JobFetchResult]:
    """
    Executes the worker fetch loop using fetch_next_job and ack_job.

    On each iteration:
      1. Calls fetch_next_job(redis, worker_id, poll_timeout=poll_timeout).
      2. If a job is claimed, invokes job_handler (if provided).
      3. Only if job_handler completes without raising an exception and ack_on_success is True,
         calls ack_job(redis, worker_id, raw_job) to remove the job from arq:in_flight:{worker_id}.
    """
    processed: list[JobFetchResult] = []
    iterations = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break

        iterations += 1
        fetched = await fetch_next_job(redis, worker_id, poll_timeout=poll_timeout)
        if fetched is None:
            continue

        source_queue, raw_job, parsed_payload = fetched
        if job_handler is not None:
            await _invoke_job_handler(job_handler, source_queue, raw_job, parsed_payload)

        if ack_on_success:
            await ack_job(redis, worker_id, raw_job)

        processed.append(fetched)

    return processed


async def startup(ctx: dict[str, Any]) -> None:
    """Initializes per-worker identity and lifecycle state on worker startup."""
    worker_id = ctx.get("worker_id") or generate_worker_id()
    ctx["worker_id"] = worker_id
    ctx["in_flight_key"] = get_in_flight_key(worker_id)
    ctx.setdefault("stop_event", asyncio.Event())
    logger.info("Worker %s initialized (in-flight list: %s)", worker_id, ctx["in_flight_key"])


async def shutdown(ctx: dict[str, Any]) -> None:
    """Signals the worker fetch loop to stop cleanly on worker shutdown."""
    stop_event = ctx.get("stop_event")
    if isinstance(stop_event, asyncio.Event):
        stop_event.set()
    logger.info("Worker %s shutting down", ctx.get("worker_id", "unknown"))


def get_default_redis_settings() -> RedisSettings:
    """Creates RedisSettings from settings.REDIS_URL with fallback to default RedisSettings."""
    try:
        return RedisSettings.from_dsn(settings.REDIS_URL)
    except Exception:
        return RedisSettings()


class WorkerSettings:
    functions: list[Any] = []
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = get_default_redis_settings()
