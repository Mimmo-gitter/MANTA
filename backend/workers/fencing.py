"""
SCRUM-410 (Task 22.2) & Task 22.3:
Atomic Redis Lua Fencing Sequence, 30s Worker Lease Assignment, 10s Heartbeat Lease Renewal,
and Expired-Lease (>30s) Supervisor Reclamation.

Implements the Task 22.1 (SCRUM-409) specification (docs/fencing-lease-contract.md):
- Atomic Redis Lua script (ACQUIRE_LEASE_LUA) executing INCR arq:fencing_seq to issue a
  strictly monotonically increasing integer owner_token and setting arq:lease:{job_id}
  to "{worker_id}:{owner_token}" with PX 30000 (30-second TTL).
- Atomic Redis Lua scripts for 10-second heartbeat renewal (RENEW_LEASE_LUA via PEXPIRE 30000),
  ownership-verified lease release (RELEASE_LEASE_LUA via DEL), and expired lease reclamation
  (RECLAIM_EXPIRED_LEASE_LUA).
- PostgreSQL JobLease upsert (job_leases table) persisting (job_id, tenant_id, owner_token,
  claimed_by, lease_until = now() + INTERVAL '30 seconds') with monotonic token enforcement.
- Background 10-second async heartbeat loop (run_lease_heartbeat_loop / LeaseHeartbeatManager)
  extending arq:lease:{job_id} and job_leases.lease_until by 30s only if owner_token matches.
- Supervisor reclamation (reclaim_expired_leases / run_supervisor_reclamation_loop) detecting
  expired leases (lease_until < now()), re-enqueueing the job, and enabling a new worker to
  claim it with a strictly higher owner_token.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import json
import logging
from typing import Any
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from app.db.models.models import JobLease
except ImportError:
    from backend.app.db.models.models import JobLease  # type: ignore[no-redef]

logger = logging.getLogger("workers.fencing")

FENCING_SEQ_KEY = "arq:fencing_seq"
LEASE_KEY_PREFIX = "arq:lease:"
IN_FLIGHT_PREFIX = "arq:in_flight:"
QUEUE_HIGH = "arq:queue:high"
QUEUE_DEFAULT = "arq:queue:default"
LEASE_TTL_MS = 30_000  # 30-second lease duration in milliseconds
LEASE_TTL_SECONDS = 30  # 30-second lease duration in seconds
HEARTBEAT_INTERVAL_SEC = 10.0  # 10-second heartbeat renewal interval
DEFAULT_SUPERVISOR_SWEEP_SEC = 5.0  # Default supervisor reclamation poll interval
DEFAULT_TENANT_UUID = uuid.UUID("11111111-1111-1111-1111-111111111111")

ACQUIRE_LEASE_LUA = """
local existing_lease = redis.call('GET', KEYS[1])
if existing_lease ~= false then
    local sep = string.find(existing_lease, ':[^:]*$')
    local current_token = 0
    if sep ~= nil then
        current_token = tonumber(string.sub(existing_lease, sep + 1)) or 0
    end
    return {0, current_token, existing_lease}
end

local floor_val = tonumber(ARGV[3]) or 0
if floor_val > 0 then
    local current_seq = tonumber(redis.call('GET', KEYS[2]) or '0')
    if current_seq < floor_val then
        redis.call('SET', KEYS[2], tostring(floor_val))
    end
end

local owner_token = redis.call('INCR', KEYS[2])
local lease_value = ARGV[1] .. ':' .. tostring(owner_token)
local ttl_ms = tonumber(ARGV[2]) or 30000
redis.call('SET', KEYS[1], lease_value, 'PX', ttl_ms)
return {1, owner_token, lease_value}
"""

RENEW_LEASE_LUA = """
local current_val = redis.call('GET', KEYS[1])
if current_val == false then
    return 0
end
if current_val ~= ARGV[1] then
    return -1
end
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) or 30000)
return 1
"""

RELEASE_LEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

RECLAIM_EXPIRED_LEASE_LUA = """
local floor_val = tonumber(ARGV[2]) or 0
if floor_val > 0 then
    local current_seq = tonumber(redis.call('GET', KEYS[2]) or '0')
    if current_seq < floor_val then
        redis.call('SET', KEYS[2], tostring(floor_val))
    end
end

local current_val = redis.call('GET', KEYS[1])
if current_val == false then
    return 1
end
if current_val == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""

_SET_TENANT_CONTEXT_SQL = text("SELECT set_config('app.current_tenant_id', :tenant_id, true)")

_UPSERT_JOB_LEASE_SQL = text(
    """
    INSERT INTO job_leases (
        job_id, tenant_id, owner_token, claimed_by, lease_until, created_at
    ) VALUES (
        :job_id, :tenant_id, :worker_claimed_token, :worker_id,
        now() + INTERVAL '30 seconds', now()
    )
    ON CONFLICT (job_id) DO UPDATE
    SET owner_token = EXCLUDED.owner_token,
        claimed_by  = EXCLUDED.claimed_by,
        lease_until = now() + INTERVAL '30 seconds'
    WHERE job_leases.owner_token < EXCLUDED.owner_token
    RETURNING owner_token
    """
)

_RENEW_JOB_LEASE_SQL = text(
    """
    UPDATE job_leases
    SET lease_until = now() + INTERVAL '30 seconds'
    WHERE job_id = :job_id
      AND tenant_id = :tenant_id
      AND claimed_by = :worker_id
      AND owner_token = :worker_claimed_token
      AND lease_until > now()
    RETURNING owner_token
    """
)

_SELECT_EXPIRED_JOB_LEASES_SQL = text(
    """
    SELECT job_id, tenant_id, owner_token, claimed_by, lease_until
    FROM job_leases
    WHERE lease_until < now()
    ORDER BY lease_until ASC
    """
)

_DELETE_JOB_LEASE_SQL = text(
    """
    DELETE FROM job_leases
    WHERE job_id = :job_id
      AND claimed_by = :worker_id
      AND owner_token = :worker_claimed_token
    RETURNING owner_token
    """
)


class StaleFencingTokenError(RuntimeError):
    """Raised when a worker's fencing token is rejected by PostgreSQL or Redis."""

    def __init__(self, job_id: str, worker_id: str, claimed_token: int) -> None:
        self.job_id = job_id
        self.worker_id = worker_id
        self.claimed_token = claimed_token
        super().__init__(
            f"Fencing check failed for job_id={job_id!r} on worker={worker_id!r} "
            f"with stale claimed_token={claimed_token}"
        )


@dataclass(frozen=True)
class LeaseHandle:
    """Represents an active 30-second worker job lease and its monotonic fencing token."""

    job_id: str
    tenant_id: uuid.UUID
    worker_id: str
    owner_token: int
    lease_key: str
    lease_value: str
    lease_ttl_ms: int = LEASE_TTL_MS
    lease_until: datetime | None = None
    created_at: datetime | None = None
    job_lease: JobLease | None = None

    @property
    def claimed_by(self) -> str:
        """Alias matching JobLease.claimed_by."""
        return self.worker_id

    def to_job_lease(self) -> JobLease:
        """Returns the associated SQLAlchemy JobLease model instance."""
        if self.job_lease is not None:
            return self.job_lease
        now_utc = self.created_at or datetime.now(timezone.utc)
        until_utc = self.lease_until or (now_utc + timedelta(milliseconds=self.lease_ttl_ms))
        return JobLease(
            job_id=self.job_id,
            tenant_id=self.tenant_id,
            owner_token=self.owner_token,
            claimed_by=self.worker_id,
            lease_until=until_utc,
            created_at=now_utc,
        )


@dataclass(frozen=True)
class ReclaimedJobRecord:
    """Metadata describing an expired (>30s) job lease reclaimed by the supervisor."""

    job_id: str
    tenant_id: uuid.UUID
    expired_owner_token: int
    previous_worker_id: str
    target_queue: str
    raw_payload: str
    expired_lease_until: datetime | None = None


def _normalize_job_id(job_id: str | uuid.UUID) -> str:
    """Validates and normalizes a job_id into a non-empty string."""
    if isinstance(job_id, uuid.UUID):
        return str(job_id)
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be a non-empty string or UUID")
    normalized = job_id.strip()
    if normalized.startswith(LEASE_KEY_PREFIX):
        suffix = normalized[len(LEASE_KEY_PREFIX) :].strip()
        if not suffix:
            raise ValueError("job_id must not be an empty lease prefix")
        return suffix
    return normalized


def _normalize_worker_id(worker_id: str) -> str:
    """Validates and normalizes a worker_id into a non-empty string."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string")
    return worker_id.strip()


def _normalize_tenant_id(tenant_id: uuid.UUID | str) -> uuid.UUID:
    """Validates and normalizes a tenant_id into a uuid.UUID."""
    if isinstance(tenant_id, uuid.UUID):
        return tenant_id
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id must be a valid UUID or UUID string")
    return uuid.UUID(tenant_id.strip())


def get_lease_key(job_id: str | uuid.UUID) -> str:
    """Constructs the per-job lease key: 'arq:lease:{job_id}'."""
    return f"{LEASE_KEY_PREFIX}{_normalize_job_id(job_id)}"


def format_lease_value(worker_id: str, owner_token: int) -> str:
    """Formats the composite Redis lease value '{worker_id}:{owner_token}'."""
    norm_worker = _normalize_worker_id(worker_id)
    token_int = int(owner_token)
    if token_int < 1:
        raise ValueError(f"owner_token must be a positive integer (got {owner_token})")
    return f"{norm_worker}:{token_int}"


def parse_lease_value(lease_value: str | bytes) -> tuple[str, int]:
    """
    Parses a composite Redis lease value '{worker_id}:{owner_token}' into (worker_id, owner_token).
    Splits on the rightmost ':' so worker_id values containing ':' are preserved intact.
    """
    raw = lease_value.decode("utf-8") if isinstance(lease_value, bytes) else str(lease_value)
    raw = raw.strip()
    if ":" not in raw:
        raise ValueError(f"Invalid lease value format (missing ':' delimiter): {raw!r}")
    worker_id, token_str = raw.rsplit(":", 1)
    if not worker_id:
        raise ValueError(f"Invalid lease value format (empty worker_id): {raw!r}")
    try:
        token_int = int(token_str)
    except ValueError as exc:
        raise ValueError(f"Invalid owner_token in lease value: {raw!r}") from exc
    if token_int < 1:
        raise ValueError(f"owner_token must be >= 1 in lease value: {raw!r}")
    return worker_id, token_int


def _is_live_async_session(db: Any) -> bool:
    """Returns True only when db is a real SQLAlchemy AsyncSession (not a unittest.mock)."""
    if not isinstance(db, AsyncSession):
        return False
    return not type(db).__module__.startswith("unittest.mock")


async def _maybe_await(result: Any) -> Any:
    """Awaits result if it is an awaitable coroutine/future."""
    if inspect.isawaitable(result):
        return await result
    return result


async def acquire_redis_lease(
    redis: Any,
    job_id: str | uuid.UUID,
    worker_id: str,
    lease_ttl_ms: int = LEASE_TTL_MS,
    min_token_floor: int = 0,
) -> tuple[bool, int, str]:
    """
    Executes ACQUIRE_LEASE_LUA atomically in Valkey/Redis:
      - If arq:lease:{job_id} does not exist, increments arq:fencing_seq via INCR
        and sets arq:lease:{job_id} = "{worker_id}:{owner_token}" with PX <lease_ttl_ms>.
      - If arq:lease:{job_id} already exists, returns (False, current_token, existing_lease).

    Returns:
        Tuple of (acquired: bool, owner_token: int, lease_value: str).
    """
    norm_job_id = _normalize_job_id(job_id)
    norm_worker_id = _normalize_worker_id(worker_id)
    if int(lease_ttl_ms) <= 0:
        raise ValueError(f"lease_ttl_ms must be positive (got {lease_ttl_ms})")
    if int(min_token_floor) < 0:
        raise ValueError(f"min_token_floor must be non-negative (got {min_token_floor})")

    lease_key = f"{LEASE_KEY_PREFIX}{norm_job_id}"
    res = await redis.eval(
        ACQUIRE_LEASE_LUA,
        2,
        lease_key,
        FENCING_SEQ_KEY,
        norm_worker_id,
        str(int(lease_ttl_ms)),
        str(int(min_token_floor)),
    )
    acquired_flag = int(res[0])
    owner_token = int(res[1])
    raw_val = res[2].decode("utf-8") if isinstance(res[2], bytes) else str(res[2])
    return (acquired_flag == 1, owner_token, raw_val)


async def upsert_job_lease(
    db: AsyncSession | Any,
    *,
    job_id: str | uuid.UUID,
    tenant_id: uuid.UUID | str,
    owner_token: int,
    claimed_by: str,
    lease_ttl_ms: int = LEASE_TTL_MS,
    commit: bool = True,
) -> JobLease | None:
    """
    Persists or updates the JobLease record in PostgreSQL `job_leases`:
      (job_id, tenant_id, owner_token, claimed_by, lease_until = now() + 30s).

    Enforces monotonic owner_token increase on conflict (job_leases.owner_token < EXCLUDED.owner_token).

    Returns:
        Populated JobLease instance if persisted, or None if rejected by the fencing guard.
    """
    norm_job_id = _normalize_job_id(job_id)
    tenant_uuid = _normalize_tenant_id(tenant_id)
    norm_worker_id = _normalize_worker_id(claimed_by)
    token_int = int(owner_token)
    if token_int < 1:
        raise ValueError(f"owner_token must be >= 1 (got {owner_token})")

    if _is_live_async_session(db):
        await db.execute(_SET_TENANT_CONTEXT_SQL, {"tenant_id": str(tenant_uuid)})

    pg_res = await _maybe_await(
        db.execute(
            _UPSERT_JOB_LEASE_SQL,
            {
                "job_id": norm_job_id,
                "tenant_id": tenant_uuid,
                "worker_claimed_token": token_int,
                "worker_id": norm_worker_id,
            },
        )
    )

    persisted_token: int | None = None
    if pg_res is not None and hasattr(pg_res, "scalar_one_or_none"):
        val = await _maybe_await(pg_res.scalar_one_or_none())
        persisted_token = int(val) if val is not None else None
    elif pg_res is not None and hasattr(pg_res, "first"):
        row = await _maybe_await(pg_res.first())
        if row is not None:
            persisted_token = int(row[0])

    if persisted_token is None:
        if hasattr(db, "rollback"):
            await _maybe_await(db.rollback())
        return None

    if commit and hasattr(db, "commit"):
        await _maybe_await(db.commit())

    now_utc = datetime.now(timezone.utc)
    lease_until_utc = now_utc + timedelta(milliseconds=int(lease_ttl_ms))
    return JobLease(
        job_id=norm_job_id,
        tenant_id=tenant_uuid,
        owner_token=persisted_token,
        claimed_by=norm_worker_id,
        lease_until=lease_until_utc,
        created_at=now_utc,
    )


async def release_job_lease(
    redis: Any,
    job_id: str | uuid.UUID,
    worker_id: str,
    owner_token: int,
    db: AsyncSession | Any | None = None,
    *,
    tenant_id: uuid.UUID | str | None = None,
    commit: bool = True,
) -> bool:
    """
    Releases arq:lease:{job_id} in Valkey/Redis via RELEASE_LEASE_LUA only if the current
    value matches "{worker_id}:{owner_token}", and removes the active lease row from
    PostgreSQL `job_leases` when `db` is provided.
    """
    norm_job_id = _normalize_job_id(job_id)
    norm_worker_id = _normalize_worker_id(worker_id)
    token_int = int(owner_token)
    lease_key = f"{LEASE_KEY_PREFIX}{norm_job_id}"
    lease_value = format_lease_value(norm_worker_id, token_int)
    deleted = int(await redis.eval(RELEASE_LEASE_LUA, 1, lease_key, lease_value))

    if db is not None:
        if tenant_id is not None and _is_live_async_session(db):
            tenant_uuid = _normalize_tenant_id(tenant_id)
            await db.execute(_SET_TENANT_CONTEXT_SQL, {"tenant_id": str(tenant_uuid)})
        await _maybe_await(
            db.execute(
                _DELETE_JOB_LEASE_SQL,
                {
                    "job_id": norm_job_id,
                    "worker_id": norm_worker_id,
                    "worker_claimed_token": token_int,
                },
            )
        )
        if commit and hasattr(db, "commit"):
            await _maybe_await(db.commit())

    return deleted == 1


async def acquire_job_lease(
    redis: Any,
    db: AsyncSession | Any | None,
    job_id: str | uuid.UUID,
    tenant_id: uuid.UUID | str,
    worker_id: str,
    lease_ttl_ms: int = LEASE_TTL_MS,
    min_token_floor: int = 0,
    *,
    commit: bool = True,
) -> LeaseHandle | None:
    """
    Atomically acquires a 30-second worker lease in Valkey via ACQUIRE_LEASE_LUA
    (INCR arq:fencing_seq + SET arq:lease:{job_id} PX 30000) and persists/updates
    JobLease in PostgreSQL job_leases.

    If PostgreSQL rejects the lease upsert or raises an error, the Redis lease is
    immediately rolled back via RELEASE_LEASE_LUA.

    Returns:
        LeaseHandle with strictly increasing owner_token on success, or None if already leased.
    """
    norm_job_id = _normalize_job_id(job_id)
    tenant_uuid = _normalize_tenant_id(tenant_id)
    norm_worker_id = _normalize_worker_id(worker_id)
    lease_key = f"{LEASE_KEY_PREFIX}{norm_job_id}"

    acquired, owner_token, raw_val = await acquire_redis_lease(
        redis=redis,
        job_id=norm_job_id,
        worker_id=norm_worker_id,
        lease_ttl_ms=lease_ttl_ms,
        min_token_floor=min_token_floor,
    )
    if not acquired:
        return None

    now_utc = datetime.now(timezone.utc)
    lease_until_utc = now_utc + timedelta(milliseconds=int(lease_ttl_ms))
    job_lease_record: JobLease | None = None

    if db is not None:
        try:
            job_lease_record = await upsert_job_lease(
                db,
                job_id=norm_job_id,
                tenant_id=tenant_uuid,
                owner_token=owner_token,
                claimed_by=norm_worker_id,
                lease_ttl_ms=lease_ttl_ms,
                commit=commit,
            )
        except Exception:
            await redis.eval(RELEASE_LEASE_LUA, 1, lease_key, raw_val)
            if hasattr(db, "rollback"):
                await _maybe_await(db.rollback())
            raise

        if job_lease_record is None:
            # Roll back Redis lease if PostgreSQL rejected acquisition
            await redis.eval(RELEASE_LEASE_LUA, 1, lease_key, raw_val)
            return None

        lease_until_utc = job_lease_record.lease_until
        now_utc = job_lease_record.created_at

    return LeaseHandle(
        job_id=norm_job_id,
        tenant_id=tenant_uuid,
        worker_id=norm_worker_id,
        owner_token=owner_token,
        lease_key=lease_key,
        lease_value=raw_val,
        lease_ttl_ms=int(lease_ttl_ms),
        lease_until=lease_until_utc,
        created_at=now_utc,
        job_lease=job_lease_record,
    )


async def renew_job_lease(
    redis: Any,
    db: AsyncSession | Any | None,
    handle: LeaseHandle,
    lease_ttl_ms: int = LEASE_TTL_MS,
    *,
    commit: bool = True,
) -> bool:
    """
    Executes the 10-second heartbeat renewal in Valkey (PEXPIRE 30000) and
    PostgreSQL (UPDATE job_leases SET lease_until = now() + INTERVAL '30 seconds')
    only if owner_token and worker_id still match the worker's token.
    """
    redis_status = int(
        await redis.eval(
            RENEW_LEASE_LUA,
            1,
            handle.lease_key,
            handle.lease_value,
            str(int(lease_ttl_ms)),
        )
    )
    if redis_status != 1:
        return False

    if db is None:
        return True

    if _is_live_async_session(db):
        await db.execute(_SET_TENANT_CONTEXT_SQL, {"tenant_id": str(handle.tenant_id)})

    pg_res = await _maybe_await(
        db.execute(
            _RENEW_JOB_LEASE_SQL,
            {
                "job_id": handle.job_id,
                "tenant_id": handle.tenant_id,
                "worker_id": handle.worker_id,
                "worker_claimed_token": handle.owner_token,
            },
        )
    )
    renewed_token = await _maybe_await(pg_res.scalar_one_or_none()) if pg_res is not None else None
    if renewed_token is None:
        if hasattr(db, "rollback"):
            await _maybe_await(db.rollback())
        return False

    if commit and hasattr(db, "commit"):
        await _maybe_await(db.commit())
    return True


async def run_lease_heartbeat_loop(
    redis: Any,
    db: AsyncSession | Any | None,
    handle: LeaseHandle,
    *,
    interval_seconds: float = HEARTBEAT_INTERVAL_SEC,
    lease_ttl_ms: int = LEASE_TTL_MS,
    stop_event: asyncio.Event | None = None,
    pause_event: asyncio.Event | None = None,
    on_renewed: Callable[[LeaseHandle, int], Any] | None = None,
    on_lease_lost: Callable[[LeaseHandle], Any] | None = None,
    target_task: asyncio.Task[Any] | None = None,
    max_renewals: int | None = None,
) -> int:
    """
    Background async task running every `interval_seconds` (default 10s) during job execution.
    Extends `arq:lease:{job_id}` and `job_leases.lease_until` by 30 seconds only if
    `owner_token` still matches the worker's token.

    If `pause_event` is set (simulating a stalled worker / SIGSTOP), renewal calls are
    skipped while paused. If `renew_job_lease` returns False (expired or superseded by a
    newer token), triggers `on_lease_lost`, optionally cancels `target_task`, and terminates.

    Returns:
        Total number of successful lease renewals performed.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be positive (got {interval_seconds})")

    renewal_count = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if max_renewals is not None and renewal_count >= max_renewals:
            break

        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                break
            else:
                await asyncio.sleep(interval_seconds)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break

        if stop_event is not None and stop_event.is_set():
            break

        # If worker is stalled/paused (e.g., simulated SIGSTOP), do not send heartbeats
        if pause_event is not None and pause_event.is_set():
            continue

        try:
            renewed = await renew_job_lease(
                redis=redis,
                db=db,
                handle=handle,
                lease_ttl_ms=lease_ttl_ms,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(
                "Heartbeat renewal error for job_id=%s worker_id=%s token=%d: %s",
                handle.job_id,
                handle.worker_id,
                handle.owner_token,
                exc,
            )
            renewed = False

        if not renewed:
            logger.warning(
                "Lease lost during heartbeat for job_id=%s worker_id=%s owner_token=%d",
                handle.job_id,
                handle.worker_id,
                handle.owner_token,
            )
            if on_lease_lost is not None:
                await _maybe_await(on_lease_lost(handle))
            if target_task is not None and not target_task.done():
                target_task.cancel()
            break

        renewal_count += 1
        if on_renewed is not None:
            await _maybe_await(on_renewed(handle, renewal_count))

    return renewal_count


class LeaseHeartbeatManager:
    """
    Async context manager that manages the 10-second background heartbeat task
    for an active `LeaseHandle` during job execution.
    """

    def __init__(
        self,
        redis: Any,
        db: AsyncSession | Any | None,
        handle: LeaseHandle,
        *,
        interval_seconds: float = HEARTBEAT_INTERVAL_SEC,
        lease_ttl_ms: int = LEASE_TTL_MS,
        pause_event: asyncio.Event | None = None,
        on_lease_lost: Callable[[LeaseHandle], Any] | None = None,
        cancel_on_lease_lost: bool = False,
        target_task: asyncio.Task[Any] | None = None,
    ) -> None:
        self.redis = redis
        self.db = db
        self.handle = handle
        self.interval_seconds = interval_seconds
        self.lease_ttl_ms = lease_ttl_ms
        self.stop_event = asyncio.Event()
        self.pause_event = pause_event or asyncio.Event()
        self._user_on_lease_lost = on_lease_lost
        self.cancel_on_lease_lost = cancel_on_lease_lost
        self.target_task = target_task
        self.renewal_count = 0
        self.lease_lost = False
        self.task: asyncio.Task[int] | None = None

    async def _handle_renewed(self, handle: LeaseHandle, count: int) -> None:
        self.renewal_count = count

    async def _handle_lease_lost(self, handle: LeaseHandle) -> None:
        self.lease_lost = True
        if self._user_on_lease_lost is not None:
            await _maybe_await(self._user_on_lease_lost(handle))

    def pause(self) -> None:
        """Simulates a stalled worker (stops sending 10s heartbeat renewals)."""
        self.pause_event.set()

    def resume(self) -> None:
        """Resumes sending 10s heartbeat renewals."""
        self.pause_event.clear()

    async def start(self) -> LeaseHeartbeatManager:
        """Spawns the background heartbeat loop task."""
        if self.task is not None and not self.task.done():
            return self
        self.stop_event.clear()
        monitored_task = self.target_task
        if monitored_task is None and self.cancel_on_lease_lost:
            monitored_task = asyncio.current_task()

        async def _runner() -> int:
            count = await run_lease_heartbeat_loop(
                redis=self.redis,
                db=self.db,
                handle=self.handle,
                interval_seconds=self.interval_seconds,
                lease_ttl_ms=self.lease_ttl_ms,
                stop_event=self.stop_event,
                pause_event=self.pause_event,
                on_renewed=self._handle_renewed,
                on_lease_lost=self._handle_lease_lost,
                target_task=monitored_task,
            )
            self.renewal_count = count
            return count

        self.task = asyncio.create_task(
            _runner(),
            name=f"lease-heartbeat:{self.handle.job_id}:{self.handle.owner_token}",
        )
        return self

    async def stop(self) -> int:
        """Signals the background heartbeat task to stop and awaits its completion."""
        self.stop_event.set()
        if self.task is not None:
            try:
                self.renewal_count = await self.task
            except asyncio.CancelledError:
                pass
            finally:
                self.task = None
        return self.renewal_count

    async def __aenter__(self) -> LeaseHeartbeatManager:
        return await self.start()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()


def _decode_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _build_reclaimed_envelope(
    job_id: str,
    tenant_id: uuid.UUID,
    priority: str,
    previous_worker_id: str,
    expired_owner_token: int,
    existing_envelope: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Constructs or preserves the deterministic JSON job envelope for a reclaimed job
    and determines its target queue ('arq:queue:high' or 'arq:queue:default').
    """
    norm_priority = priority.strip().lower() if isinstance(priority, str) else "default"
    if existing_envelope is not None:
        env_dict = dict(existing_envelope)
        raw_prio = str(env_dict.get("priority", norm_priority)).strip().lower()
        if raw_prio in {"high", "default"}:
            norm_priority = raw_prio
        env_dict["attempt"] = int(env_dict.get("attempt", 0)) + 1
        raw_str = json.dumps(env_dict, separators=(",", ":"), sort_keys=True)
    else:
        if norm_priority not in {"high", "default"}:
            norm_priority = "default"
        envelope = {
            "attempt": 1,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "event_id": None,
            "job_id": job_id,
            "job_type": "reclaimed_job",
            "payload": {
                "expired_owner_token": expired_owner_token,
                "reclaimed_from": previous_worker_id,
            },
            "priority": norm_priority,
            "provider": None,
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        raw_str = json.dumps(envelope, separators=(",", ":"), sort_keys=True)

    target_queue = QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT
    return target_queue, raw_str


async def _pop_matching_in_flight_job(
    redis: Any,
    worker_id: str,
    job_id: str,
) -> tuple[str, dict[str, Any]] | None:
    """
    Searches `arq:in_flight:{worker_id}` for the raw job string matching `job_id`
    and removes it via LREM if present.
    """
    in_flight_key = f"{IN_FLIGHT_PREFIX}{worker_id}"
    items: list[Any] = []
    if hasattr(redis, "lrange"):
        res = await _maybe_await(redis.lrange(in_flight_key, 0, -1))
        if res:
            items = list(res)
    elif hasattr(redis, "lists") and isinstance(getattr(redis, "lists"), dict):
        items = list(redis.lists.get(in_flight_key, []))

    for raw_item in items:
        raw_str = _decode_str(raw_item)
        try:
            parsed = json.loads(raw_str)
        except Exception:
            continue
        if isinstance(parsed, dict):
            candidate_id = str(parsed.get("job_id") or parsed.get("id") or "").strip()
            if candidate_id == job_id:
                if hasattr(redis, "lrem"):
                    await _maybe_await(redis.lrem(in_flight_key, 1, raw_str))
                return raw_str, parsed
    return None


async def _requeue_job_payload(redis: Any, target_queue: str, raw_payload: str) -> None:
    """
    Re-enqueues a reclaimed job at the head (LEFT via LPUSH) of `target_queue` so it is
    picked up immediately on the next worker fetch, falling back to RPUSH if LPUSH is absent.
    """
    if hasattr(redis, "lpush"):
        await _maybe_await(redis.lpush(target_queue, raw_payload))
    else:
        await _maybe_await(redis.rpush(target_queue, raw_payload))


async def _extract_expired_rows(
    pg_res: Any,
) -> list[tuple[str, uuid.UUID, int, str, datetime | None]]:
    """Normalizes rows returned by _SELECT_EXPIRED_JOB_LEASES_SQL across real/mock sessions."""
    if pg_res is None:
        return []
    rows_raw: Iterable[Any] = []
    if hasattr(pg_res, "fetchall"):
        fetched = await _maybe_await(pg_res.fetchall())
        if fetched is not None:
            rows_raw = fetched
    elif hasattr(pg_res, "all"):
        fetched = await _maybe_await(pg_res.all())
        if fetched is not None:
            rows_raw = fetched
    elif isinstance(pg_res, Iterable):
        rows_raw = pg_res

    normalized: list[tuple[str, uuid.UUID, int, str, datetime | None]] = []
    for row in rows_raw:
        if isinstance(row, Mapping):
            j_id = str(row["job_id"])
            t_id = _normalize_tenant_id(row["tenant_id"])
            tok = int(row["owner_token"])
            c_by = str(row["claimed_by"])
            l_until = row.get("lease_until")
        elif hasattr(row, "job_id") and hasattr(row, "owner_token"):
            j_id = str(row.job_id)
            t_id = _normalize_tenant_id(row.tenant_id)
            tok = int(row.owner_token)
            c_by = str(row.claimed_by)
            l_until = getattr(row, "lease_until", None)
        else:
            seq = list(row)
            j_id = str(seq[0])
            t_id = _normalize_tenant_id(seq[1])
            tok = int(seq[2])
            c_by = str(seq[3])
            l_until = seq[4] if len(seq) > 4 else None
        normalized.append((j_id, t_id, tok, c_by, l_until))
    return normalized


async def reclaim_expired_leases(
    redis: Any,
    db: AsyncSession | Any | None = None,
    *,
    tenant_id: uuid.UUID | str | None = None,
    worker_ids: Iterable[str] | None = None,
    default_priority: str = "default",
) -> list[ReclaimedJobRecord]:
    """
    Supervisor reclamation routine that detects leases expired for >30s (`lease_until < now()`),
    cleans up stale lease state, synchronizes `arq:fencing_seq` floor with the expired
    `owner_token`, and re-enqueues the stranded job into `arq:queue:high` or `arq:queue:default`
    so a new worker can claim it with a strictly higher `owner_token`.

    Supports both:
      1. PostgreSQL `job_leases` inspection (`WHERE lease_until < now()`).
      2. Per-worker `arq:in_flight:{worker_id}` scan for jobs whose `arq:lease:{job_id}` expired.
    """
    reclaimed: list[ReclaimedJobRecord] = []
    reclaimed_job_ids: set[str] = set()

    # Path 1: PostgreSQL authoritative expired lease scan (lease_until < now())
    if db is not None:
        if tenant_id is not None and _is_live_async_session(db):
            tenant_uuid = _normalize_tenant_id(tenant_id)
            await db.execute(_SET_TENANT_CONTEXT_SQL, {"tenant_id": str(tenant_uuid)})

        pg_res = await _maybe_await(db.execute(_SELECT_EXPIRED_JOB_LEASES_SQL))
        expired_rows = await _extract_expired_rows(pg_res)

        for job_id, row_tenant_id, expired_token, claimed_by, lease_until in expired_rows:
            lease_key = get_lease_key(job_id)
            expected_lease_val = format_lease_value(claimed_by, expired_token)

            reclaim_status = int(
                await redis.eval(
                    RECLAIM_EXPIRED_LEASE_LUA,
                    2,
                    lease_key,
                    FENCING_SEQ_KEY,
                    expected_lease_val,
                    str(expired_token),
                )
            )
            if reclaim_status != 1:
                # Lease in Redis is already owned by a newer worker with a higher owner_token
                continue

            in_flight_match = await _pop_matching_in_flight_job(redis, claimed_by, job_id)
            if in_flight_match is not None:
                raw_payload, parsed_env = in_flight_match
                raw_prio = str(parsed_env.get("priority", default_priority)).strip().lower()
                target_queue = QUEUE_HIGH if raw_prio == "high" else QUEUE_DEFAULT
            else:
                target_queue, raw_payload = _build_reclaimed_envelope(
                    job_id=job_id,
                    tenant_id=row_tenant_id,
                    priority=default_priority,
                    previous_worker_id=claimed_by,
                    expired_owner_token=expired_token,
                )

            await _requeue_job_payload(redis, target_queue, raw_payload)
            await _maybe_await(
                db.execute(
                    _DELETE_JOB_LEASE_SQL,
                    {
                        "job_id": job_id,
                        "worker_id": claimed_by,
                        "worker_claimed_token": expired_token,
                    },
                )
            )
            if hasattr(db, "commit"):
                await _maybe_await(db.commit())
            reclaimed_job_ids.add(job_id)
            reclaimed.append(
                ReclaimedJobRecord(
                    job_id=job_id,
                    tenant_id=row_tenant_id,
                    expired_owner_token=expired_token,
                    previous_worker_id=claimed_by,
                    target_queue=target_queue,
                    raw_payload=raw_payload,
                    expired_lease_until=lease_until,
                )
            )

    # Path 2: Scan arq:in_flight:{worker_id} for jobs whose Redis lease key (arq:lease:{job_id}) expired
    if worker_ids is not None:
        fallback_tenant = (
            _normalize_tenant_id(tenant_id) if tenant_id is not None else DEFAULT_TENANT_UUID
        )
        for raw_worker_id in worker_ids:
            norm_worker_id = _normalize_worker_id(raw_worker_id)
            in_flight_key = f"{IN_FLIGHT_PREFIX}{norm_worker_id}"
            items: list[Any] = []
            if hasattr(redis, "lrange"):
                res = await _maybe_await(redis.lrange(in_flight_key, 0, -1))
                if res:
                    items = list(res)
            elif hasattr(redis, "lists") and isinstance(getattr(redis, "lists"), dict):
                items = list(redis.lists.get(in_flight_key, []))

            for raw_item in items:
                raw_str = _decode_str(raw_item)
                try:
                    parsed = json.loads(raw_str)
                except Exception:
                    continue
                if not isinstance(parsed, dict):
                    continue
                job_id = str(parsed.get("job_id") or parsed.get("id") or "").strip()
                if not job_id or job_id in reclaimed_job_ids:
                    continue

                lease_key = get_lease_key(job_id)
                active_val = None
                if hasattr(redis, "get"):
                    active_val = await _maybe_await(redis.get(lease_key))
                elif hasattr(redis, "kv") and isinstance(getattr(redis, "kv"), dict):
                    active_val = redis.kv.get(lease_key)

                if active_val is not None:
                    continue

                if hasattr(redis, "lrem"):
                    await _maybe_await(redis.lrem(in_flight_key, 1, raw_str))

                raw_prio = str(parsed.get("priority", default_priority)).strip().lower()
                target_queue = QUEUE_HIGH if raw_prio == "high" else QUEUE_DEFAULT
                raw_tid = parsed.get("tenant_id")
                try:
                    job_tenant_id = _normalize_tenant_id(raw_tid) if raw_tid else fallback_tenant
                except Exception:
                    job_tenant_id = fallback_tenant

                expired_token = int(parsed.get("owner_token", 0) or 0)
                await _requeue_job_payload(redis, target_queue, raw_str)
                reclaimed_job_ids.add(job_id)
                reclaimed.append(
                    ReclaimedJobRecord(
                        job_id=job_id,
                        tenant_id=job_tenant_id,
                        expired_owner_token=expired_token,
                        previous_worker_id=norm_worker_id,
                        target_queue=target_queue,
                        raw_payload=raw_str,
                    )
                )

    return reclaimed


async def run_supervisor_reclamation_loop(
    redis: Any,
    db: AsyncSession | Any | None = None,
    *,
    sweep_interval: float = DEFAULT_SUPERVISOR_SWEEP_SEC,
    tenant_id: uuid.UUID | str | None = None,
    worker_ids: Iterable[str] | None = None,
    default_priority: str = "default",
    stop_event: asyncio.Event | None = None,
    max_iterations: int | None = None,
) -> list[ReclaimedJobRecord]:
    """
    Runs the supervisor reclamation loop periodically to detect and re-enqueue
    leases expired for >30s (`lease_until < now()`).
    """
    if sweep_interval < 0:
        raise ValueError(f"sweep_interval must be non-negative (got {sweep_interval})")

    all_reclaimed: list[ReclaimedJobRecord] = []
    iterations = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break

        iterations += 1
        batch = await reclaim_expired_leases(
            redis=redis,
            db=db,
            tenant_id=tenant_id,
            worker_ids=worker_ids,
            default_priority=default_priority,
        )
        all_reclaimed.extend(batch)

        if max_iterations is not None and iterations >= max_iterations:
            break
        if stop_event is not None and stop_event.is_set():
            break

        if sweep_interval > 0:
            try:
                if stop_event is not None:
                    await asyncio.wait_for(stop_event.wait(), timeout=sweep_interval)
                    break
                else:
                    await asyncio.sleep(sweep_interval)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    return all_reclaimed
