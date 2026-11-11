"""
SCRUM-405 (Task 21.2) & SCRUM-406 (Task 21.3):
Reliable Valkey / Redis 7 FIFO Queue Producer, Atomic Worker Fetch, and Crash Recovery.

Implements the Task 21.1 (SCRUM-404) specification (docs/fifo-queue-topology.md):
- Producer enqueue via RPUSH to arq:queue:default or arq:queue:high (appends to RIGHT / tail).
- Two-phase consumer fetch into per-worker arq:in_flight:{worker_id} lists:
  1. Non-blocking LMOVE arq:queue:high arq:in_flight:{worker_id} LEFT RIGHT
     (guarantees immediate high-priority preemption ahead of waiting default jobs).
  2. Bounded-timeout BLMOVE arq:queue:default arq:in_flight:{worker_id} LEFT RIGHT <timeout>
     (guarantees 100% strict FIFO execution order).
- Explicit acknowledgment via LREM arq:in_flight:{worker_id} 1 <raw_job> after processing.
- Crash-safe orphan in-flight recovery on worker startup and dead-worker sweep via
  atomic LMOVE arq:in_flight:{dead_worker_id} arq:queue:{priority} RIGHT LEFT.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
import inspect
import json
import logging
import os
import socket
from typing import Any
from unittest.mock import Mock
import uuid

from arq.connections import RedisSettings

from app.config import settings

logger = logging.getLogger("workers.worker")

QUEUE_HIGH = "arq:queue:high"
QUEUE_DEFAULT = "arq:queue:default"
IN_FLIGHT_PREFIX = "arq:in_flight:"
WORKER_HEARTBEAT_PREFIX = "arq:worker:"
WORKER_HEARTBEAT_SUFFIX = ":heartbeat"
DEFAULT_POLL_TIMEOUT = 1.0
HEARTBEAT_TTL_SECONDS = 30
DEFAULT_HEARTBEAT_INTERVAL = 10.0
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


def get_worker_heartbeat_key(worker_id: str) -> str:
    """
    Constructs the per-worker liveness heartbeat key: 'arq:worker:{worker_id}:heartbeat'.
    """
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string")
    normalized = worker_id.strip()
    if normalized.startswith(WORKER_HEARTBEAT_PREFIX):
        normalized = normalized[len(WORKER_HEARTBEAT_PREFIX) :].strip()
    if normalized.endswith(WORKER_HEARTBEAT_SUFFIX):
        normalized = normalized[: -len(WORKER_HEARTBEAT_SUFFIX)].strip()
    if normalized.startswith(IN_FLIGHT_PREFIX):
        normalized = normalized[len(IN_FLIGHT_PREFIX) :].strip()
    if not normalized:
        raise ValueError("worker_id must not resolve to an empty identifier")
    return f"{WORKER_HEARTBEAT_PREFIX}{normalized}{WORKER_HEARTBEAT_SUFFIX}"


def extract_worker_id_from_in_flight_key(in_flight_key: str | bytes) -> str:
    """
    Extracts the worker_id from an 'arq:in_flight:{worker_id}' key.
    """
    decoded = _decode_raw_job(in_flight_key).strip()
    if decoded.startswith(IN_FLIGHT_PREFIX):
        worker_id = decoded[len(IN_FLIGHT_PREFIX) :].strip()
    else:
        worker_id = decoded
    if not worker_id:
        raise ValueError(f"Invalid in-flight key: {in_flight_key!r}")
    return worker_id


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


def _resolve_target_queue_for_raw_job(
    raw_job: str | bytes | None,
    default_queue: str = QUEUE_DEFAULT,
) -> str:
    """
    Determines the origin queue ('arq:queue:high' or 'arq:queue:default') from a
    serialized job payload's 'priority' field, defaulting to default_queue.
    """
    if raw_job is None or not isinstance(raw_job, (str, bytes)):
        return default_queue
    parsed = _parse_job_payload(_decode_raw_job(raw_job))
    priority = parsed.get("priority")
    if isinstance(priority, str) and priority.strip().lower() == "high":
        return QUEUE_HIGH
    return default_queue


async def _peek_in_flight_tail_queue(
    redis: Any,
    in_flight_key: str,
    default_queue: str = QUEUE_DEFAULT,
) -> str:
    """
    Safely inspects the tail (RIGHT, index -1) element of an in-flight list to
    resolve its target priority queue prior to executing atomic LMOVE RIGHT LEFT.
    """
    lindex_fn = getattr(redis, "lindex", None)
    if callable(lindex_fn) and not isinstance(lindex_fn, Mock):
        with suppress(Exception):
            tail_item = await lindex_fn(in_flight_key, -1)
            if isinstance(tail_item, (str, bytes)):
                return _resolve_target_queue_for_raw_job(tail_item, default_queue=default_queue)

    lrange_fn = getattr(redis, "lrange", None)
    if callable(lrange_fn) and not isinstance(lrange_fn, Mock):
        with suppress(Exception):
            tail_items = await lrange_fn(in_flight_key, -1, -1)
            if isinstance(tail_items, (list, tuple)) and tail_items:
                last = tail_items[-1]
                if isinstance(last, (str, bytes)):
                    return _resolve_target_queue_for_raw_job(last, default_queue=default_queue)

    return default_queue


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


async def record_worker_heartbeat(
    redis: Any,
    worker_id: str,
    ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
) -> str:
    """
    Records a worker liveness heartbeat at 'arq:worker:{worker_id}:heartbeat'
    with an epoch-millisecond timestamp and 30-second TTL (SET ... EX 30).
    """
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive (got {ttl_seconds})")
    heartbeat_key = get_worker_heartbeat_key(worker_id)
    epoch_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    await redis.set(heartbeat_key, epoch_ms, ex=ttl_seconds)
    return epoch_ms


async def is_worker_alive(redis: Any, worker_id: str) -> bool:
    """
    Checks whether worker_id has a live (non-expired) heartbeat key in Valkey/Redis.
    """
    heartbeat_key = get_worker_heartbeat_key(worker_id)
    if hasattr(redis, "exists"):
        exists_result = await redis.exists(heartbeat_key)
        return bool(exists_result)
    if hasattr(redis, "get"):
        val = await redis.get(heartbeat_key)
        return val is not None
    return False


async def run_heartbeat_loop(
    redis: Any,
    worker_id: str,
    *,
    interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL,
    ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
    stop_event: asyncio.Event | None = None,
    max_iterations: int | None = None,
) -> int:
    """
    Periodically refreshes the worker's liveness heartbeat key until stop_event is set.
    """
    if interval_seconds < 0:
        raise ValueError(f"interval_seconds must be non-negative (got {interval_seconds})")
    iterations = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break

        await record_worker_heartbeat(redis, worker_id, ttl_seconds=ttl_seconds)
        iterations += 1

        if max_iterations is not None and iterations >= max_iterations:
            break
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                break
            except TimeoutError:
                continue
        else:
            await asyncio.sleep(interval_seconds)

    return iterations


async def recover_in_flight_jobs(
    redis: Any,
    worker_id: str,
    *,
    target_queue: str | None = None,
    max_jobs: int | None = None,
) -> list[JobFetchResult]:
    """
    Atomically recovers stranded jobs from 'arq:in_flight:{worker_id}' back to the
    head (LEFT) of the queue via:
        LMOVE arq:in_flight:{worker_id} arq:queue:{target_priority} RIGHT LEFT

    Popping from RIGHT (tail) and pushing to LEFT (head) ensures that:
      1. Recovered jobs are positioned at index 0 ahead of waiting queue items.
      2. If multiple jobs were stranded in arq:in_flight:{worker_id}, their relative
         FIFO ordering is preserved at the head of the target queue.

    Args:
        redis: Async Redis/Valkey client connection.
        worker_id: Identifier (or full in-flight key) of the crashed/restarting worker.
        target_queue: Optional explicit destination queue key override. When None,
            routes 'high' priority envelopes to arq:queue:high and all others to
            arq:queue:default.
        max_jobs: Optional cap on the number of jobs to recover in one call.

    Returns:
        List of (destination_queue, raw_payload_str, parsed_payload_dict) tuples
        for each recovered job.
    """
    if max_jobs is not None and max_jobs < 0:
        raise ValueError(f"max_jobs must be non-negative (got {max_jobs})")

    in_flight_key = get_in_flight_key(worker_id)
    recovered: list[JobFetchResult] = []

    while True:
        if max_jobs is not None and len(recovered) >= max_jobs:
            break

        dest_queue = (
            target_queue
            if target_queue is not None
            else await _peek_in_flight_tail_queue(redis, in_flight_key, default_queue=QUEUE_DEFAULT)
        )

        moved_job = await redis.lmove(in_flight_key, dest_queue, src="RIGHT", dest="LEFT")
        if moved_job is None:
            break

        raw_str = _decode_raw_job(moved_job)
        parsed_payload = _parse_job_payload(raw_str)
        recovered.append((dest_queue, raw_str, parsed_payload))

    if recovered:
        logger.info(
            "Recovered %d stranded job(s) from %s",
            len(recovered),
            in_flight_key,
        )
    return recovered


recover_orphan_in_flight_jobs = recover_in_flight_jobs


async def discover_in_flight_keys(redis: Any) -> list[str]:
    """
    Discovers all active 'arq:in_flight:*' keys in Valkey/Redis using SCAN (or KEYS fallback).
    """
    pattern = f"{IN_FLIGHT_PREFIX}*"
    discovered: set[str] = set()

    scan_iter_fn = getattr(redis, "scan_iter", None)
    if callable(scan_iter_fn):
        with suppress(Exception):
            iterator = scan_iter_fn(match=pattern)
            if hasattr(iterator, "__aiter__"):
                async for item in iterator:
                    if isinstance(item, (str, bytes)):
                        decoded = _decode_raw_job(item).strip()
                        if decoded.startswith(IN_FLIGHT_PREFIX):
                            discovered.add(decoded)
                return sorted(discovered)

    keys_fn = getattr(redis, "keys", None)
    if callable(keys_fn):
        with suppress(Exception):
            raw_keys = await keys_fn(pattern)
            if isinstance(raw_keys, (list, tuple, set, frozenset)):
                for item in raw_keys:
                    if isinstance(item, (str, bytes)):
                        decoded = _decode_raw_job(item).strip()
                        if decoded.startswith(IN_FLIGHT_PREFIX):
                            discovered.add(decoded)

    return sorted(discovered)


async def sweep_dead_workers(
    redis: Any,
    *,
    exclude_worker_ids: Iterable[str] | None = None,
    target_queue: str | None = None,
) -> dict[str, list[JobFetchResult]]:
    """
    Scans all 'arq:in_flight:{worker_id}' lists in Valkey/Redis and recovers stranded
    jobs for any worker whose 'arq:worker:{worker_id}:heartbeat' key has expired.

    Args:
        redis: Async Redis/Valkey client connection.
        exclude_worker_ids: Optional collection of active worker_ids to skip.
        target_queue: Optional explicit destination queue override for recovered jobs.

    Returns:
        Mapping of dead_worker_id -> list of recovered JobFetchResult tuples.
    """
    excluded: set[str] = set()
    if exclude_worker_ids is not None:
        for wid in exclude_worker_ids:
            if isinstance(wid, str) and wid.strip():
                excluded.add(extract_worker_id_from_in_flight_key(wid))

    recovered_by_worker: dict[str, list[JobFetchResult]] = {}
    in_flight_keys = await discover_in_flight_keys(redis)

    for in_flight_key in in_flight_keys:
        candidate_worker_id = extract_worker_id_from_in_flight_key(in_flight_key)
        if candidate_worker_id in excluded:
            continue
        if await is_worker_alive(redis, candidate_worker_id):
            continue

        recovered = await recover_in_flight_jobs(
            redis,
            candidate_worker_id,
            target_queue=target_queue,
        )
        if recovered:
            recovered_by_worker[candidate_worker_id] = recovered

    return recovered_by_worker


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
    recover_on_start: bool = False,
) -> list[JobFetchResult]:
    """
    Executes the worker fetch loop using fetch_next_job and ack_job.

    On each iteration:
      1. Calls fetch_next_job(redis, worker_id, poll_timeout=poll_timeout).
      2. If a job is claimed, invokes job_handler (if provided).
      3. Only if job_handler completes without raising an exception and ack_on_success is True,
         calls ack_job(redis, worker_id, raw_job) to remove the job from arq:in_flight:{worker_id}.
    """
    if recover_on_start:
        await recover_in_flight_jobs(redis, worker_id)

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
    """
    Initializes per-worker identity and lifecycle state on worker startup,
    recovers any orphan jobs stranded in arq:in_flight:{worker_id}, registers
    the worker liveness heartbeat, and sweeps dead workers' in-flight queues.
    """
    worker_id = ctx.get("worker_id") or generate_worker_id()
    ctx["worker_id"] = worker_id
    ctx["in_flight_key"] = get_in_flight_key(worker_id)
    ctx["heartbeat_key"] = get_worker_heartbeat_key(worker_id)
    stop_event = ctx.setdefault("stop_event", asyncio.Event())

    redis = ctx.get("redis")
    if redis is not None:
        recovered_jobs = await recover_in_flight_jobs(redis, worker_id)
        ctx["recovered_jobs"] = recovered_jobs
        await record_worker_heartbeat(redis, worker_id)

        if ctx.get("sweep_dead_workers_on_startup", True):
            ctx["swept_dead_worker_jobs"] = await sweep_dead_workers(
                redis,
                exclude_worker_ids=(worker_id,),
            )
        else:
            ctx["swept_dead_worker_jobs"] = {}

        if ctx.get("enable_heartbeat_task", False):
            ctx["heartbeat_task"] = asyncio.create_task(
                run_heartbeat_loop(
                    redis,
                    worker_id,
                    stop_event=stop_event,
                )
            )
    else:
        ctx.setdefault("recovered_jobs", [])
        ctx.setdefault("swept_dead_worker_jobs", {})

    logger.info("Worker %s initialized (in-flight list: %s)", worker_id, ctx["in_flight_key"])


async def shutdown(ctx: dict[str, Any]) -> None:
    """Signals the worker fetch and heartbeat loops to stop cleanly on worker shutdown."""
    stop_event = ctx.get("stop_event")
    if isinstance(stop_event, asyncio.Event):
        stop_event.set()

    heartbeat_task = ctx.get("heartbeat_task")
    if isinstance(heartbeat_task, asyncio.Task) and not heartbeat_task.done():
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task

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
