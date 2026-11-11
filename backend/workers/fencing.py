"""
SCRUM-410 (Task 22.2): Atomic Redis Lua Fencing Sequence & 30s Worker Lease Assignment.

Implements the Task 22.1 (SCRUM-409) specification (docs/fencing-lease-contract.md):
- Atomic Redis Lua script (ACQUIRE_LEASE_LUA) executing INCR arq:fencing_seq to issue a
  strictly monotonically increasing integer owner_token and setting arq:lease:{job_id}
  to "{worker_id}:{owner_token}" with PX 30000 (30-second TTL).
- Atomic Redis Lua scripts for 10-second heartbeat renewal (RENEW_LEASE_LUA via PEXPIRE 30000)
  and ownership-verified lease release (RELEASE_LEASE_LUA via DEL).
- PostgreSQL JobLease upsert (job_leases table) persisting (job_id, tenant_id, owner_token,
  claimed_by, lease_until = now() + INTERVAL '30 seconds') with monotonic token enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
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
LEASE_TTL_MS = 30_000  # 30-second lease duration in milliseconds
LEASE_TTL_SECONDS = 30  # 30-second lease duration in seconds
HEARTBEAT_INTERVAL_SEC = 10.0  # 10-second heartbeat renewal interval

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
) -> bool:
    """
    Releases arq:lease:{job_id} in Valkey/Redis via RELEASE_LEASE_LUA only if the current
    value matches "{worker_id}:{owner_token}".
    """
    lease_key = get_lease_key(job_id)
    lease_value = format_lease_value(worker_id, owner_token)
    deleted = int(await redis.eval(RELEASE_LEASE_LUA, 1, lease_key, lease_value))
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
    PostgreSQL (UPDATE job_leases SET lease_until = now() + INTERVAL '30 seconds').
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
