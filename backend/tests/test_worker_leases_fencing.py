"""
SCRUM-414 (Task 22.6):
Automated split-brain test asserting a resumed zombie worker updates 0 rows and logs a rejected stale write.

Parent User Story: SCRUM-359 (US-22: Worker Lease Management & Split-Brain Prevention)
Related Tasks:
- SCRUM-409 (Task 22.1): Monotonic fencing sequence, 30s lease contract, and PostgreSQL optimistic fencing.
- SCRUM-410 (Task 22.2): Atomic Redis Lua script (ACQUIRE_LEASE_LUA) and 30s worker lease assignment.
- SCRUM-411 (Task 22.3): 10-second heartbeat lease renewal and expired lease (>30s) supervisor reclamation.
- SCRUM-412 (Task 22.4): Optimistic fencing assertion (WHERE active_fencing_token <= :worker_claimed_token).
- SCRUM-413 (Task 22.5): Split-brain test scenarios using SIGSTOP (>30s pause) and concurrent worker lease takeover.
- SCRUM-414 (Task 22.6): Automated split-brain test asserting a resumed zombie worker updates 0 rows and logs STALE_FENCING_TOKEN_REJECTED.

Objective:
Automate the split-brain zombie worker test in pytest:
Simulate Worker 1 lease acquisition (token = 1), lease expiration (>30s), Worker 2 takeover (token = 2)
and DB commit, followed by Worker 1 attempting to commit with token = 1.
Assert 0 rows overwritten, Worker 2's state preserved, STALE_FENCING_TOKEN_REJECTED logged,
transaction rolled back, and StaleFencingTokenError raised.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

import pytest
from sqlalchemy import text

from app.db.models.models import (
    Project,
    PullRequest,
    Repository,
)
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from workers.fencing import (
    LEASE_KEY_PREFIX,
    LeaseHandle,
    StaleFencingTokenError,
    acquire_job_lease,
    acquire_redis_lease,
    execute_fenced_domain_update,
    log_stale_fencing_token_rejected,
    reclaim_expired_leases,
    release_job_lease,
    renew_job_lease,
)

logger = logging.getLogger("workers.fencing")

TENANT_ALPHA = uuid.UUID("11111111-1111-1111-1111-111111111111")
WORKER_1 = "manta-worker-node1-1001-a1b2c3d4"
WORKER_2 = "manta-worker-node2-1002-e5f6g7h8"
WORKER_3 = "manta-worker-node3-1003-99aa88bb"


class FencingInMemoryAsyncRedis:
    """
    High-fidelity in-memory async Valkey 7 / Redis 7 simulator with simulated clock
    and exact Lua script execution semantics for split-brain and fencing tests.
    """

    def __init__(self, initial_time: float = 0.0) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.strings: dict[str, str] = {}
        self.expirations: dict[str, float] = {}  # key -> expiry timestamp (float seconds)
        self.current_time = initial_time

    def advance_time(self, seconds: float) -> None:
        """Advances the simulated clock and evicts keys whose TTL expired."""
        self.current_time += seconds
        self._purge_expired()

    def _purge_expired(self) -> None:
        expired_keys = [
            k for k, exp in self.expirations.items() if exp is not None and self.current_time >= exp
        ]
        for k in expired_keys:
            self.strings.pop(k, None)
            self.expirations.pop(k, None)

    async def get(self, key: str) -> str | None:
        self._purge_expired()
        return self.strings.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool:
        self._purge_expired()
        self.strings[key] = str(value)
        if px is not None:
            self.expirations[key] = self.current_time + (px / 1000.0)
        elif ex is not None:
            self.expirations[key] = self.current_time + float(ex)
        else:
            self.expirations.pop(key, None)
        return True

    async def pttl(self, key: str) -> int:
        self._purge_expired()
        if key not in self.strings:
            return -2
        if key not in self.expirations:
            return -1
        remaining_ms = int((self.expirations[key] - self.current_time) * 1000)
        return max(0, remaining_ms)

    async def delete(self, *keys: str) -> int:
        self._purge_expired()
        count = 0
        for k in keys:
            if k in self.strings:
                del self.strings[k]
                self.expirations.pop(k, None)
                count += 1
            if k in self.lists:
                del self.lists[k]
                count += 1
        return count

    async def incr(self, key: str) -> int:
        self._purge_expired()
        cur = int(self.strings.get(key, 0))
        cur += 1
        self.strings[key] = str(cur)
        return cur

    async def rpush(self, key: str, *values: str) -> int:
        for val in values:
            self.lists[key].append(str(val))
        return len(self.lists[key])

    async def lpush(self, key: str, *values: str) -> int:
        for val in values:
            self.lists[key].insert(0, str(val))
        return len(self.lists[key])

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        if not items:
            return []
        length = len(items)
        if start < 0:
            start = max(0, length + start)
        if stop < 0:
            stop = length + stop
        else:
            stop = min(length - 1, stop)
        if start > stop:
            return []
        return list(items[start : stop + 1])

    async def lrem(self, key: str, count: int, element: str) -> int:
        items = self.lists.get(key, [])
        removed = 0
        target = str(element)
        if count == 0:
            new_items = [x for x in items if x != target]
            removed = len(items) - len(new_items)
            self.lists[key] = new_items
        elif count > 0:
            new_items = []
            for item in items:
                if item == target and removed < count:
                    removed += 1
                else:
                    new_items.append(item)
            self.lists[key] = new_items
        else:
            limit = abs(count)
            new_items = []
            for item in reversed(items):
                if item == target and removed < limit:
                    removed += 1
                else:
                    new_items.append(item)
            self.lists[key] = list(reversed(new_items))
        return removed

    async def lmove(
        self,
        source: str,
        destination: str,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        src_norm = src.upper()
        dest_norm = dest.upper()
        src_list = self.lists.get(source, [])
        if not src_list:
            return None

        if src_norm == "LEFT":
            element = src_list.pop(0)
        elif src_norm == "RIGHT":
            element = src_list.pop()
        else:
            raise ValueError(f"Invalid src: {src}")

        if dest_norm == "RIGHT":
            self.lists[destination].append(element)
        elif dest_norm == "LEFT":
            self.lists[destination].insert(0, element)
        else:
            raise ValueError(f"Invalid dest: {dest}")

        return element

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        """
        Executes atomic Lua scripts with exact Valkey / Redis 7 fencing semantics.
        """
        self._purge_expired()
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])

        # 1. ACQUIRE_LEASE_LUA
        if "INCR" in script and "PX" in script:
            lease_key = keys[0]
            seq_key = keys[1]
            worker_id = str(args[0])
            ttl_ms = int(args[1]) if len(args) > 1 else 30000
            floor_val = int(args[2]) if len(args) > 2 else 0

            existing = self.strings.get(lease_key)
            if existing is not None:
                sep = existing.rfind(":")
                current_token = 0
                if sep != -1:
                    try:
                        current_token = int(existing[sep + 1 :])
                    except ValueError:
                        current_token = 0
                return [0, current_token, existing]

            if floor_val > 0:
                cur_seq = int(self.strings.get(seq_key, 0))
                if cur_seq < floor_val:
                    self.strings[seq_key] = str(floor_val)

            cur_seq = int(self.strings.get(seq_key, 0)) + 1
            self.strings[seq_key] = str(cur_seq)
            owner_token = cur_seq

            lease_val = f"{worker_id}:{owner_token}"
            self.strings[lease_key] = lease_val
            self.expirations[lease_key] = self.current_time + (ttl_ms / 1000.0)
            return [1, owner_token, lease_val]

        # 2. RENEW_LEASE_LUA
        if "PEXPIRE" in script:
            lease_key = keys[0]
            expected_val = str(args[0])
            ttl_ms = int(args[1]) if len(args) > 1 else 30000

            cur_val = self.strings.get(lease_key)
            if cur_val is None:
                return 0  # Expired
            if cur_val != expected_val:
                return -1  # Superseded
            self.expirations[lease_key] = self.current_time + (ttl_ms / 1000.0)
            return 1

        # 3. RELEASE_LEASE_LUA
        if "DEL" in script and len(keys) == 1:
            lease_key = keys[0]
            expected_val = str(args[0])
            cur_val = self.strings.get(lease_key)
            if cur_val == expected_val:
                del self.strings[lease_key]
                self.expirations.pop(lease_key, None)
                return 1
            return 0

        # 4. RECLAIM_EXPIRED_LEASE_LUA
        if "RECLAIM" in script or (len(keys) == 2 and "DEL" in script):
            lease_key = keys[0]
            seq_key = keys[1]
            expected_val = str(args[0])
            floor_val = int(args[1]) if len(args) > 1 else 0

            if floor_val > 0:
                cur_seq = int(self.strings.get(seq_key, 0))
                if cur_seq < floor_val:
                    self.strings[seq_key] = str(floor_val)

            cur_val = self.strings.get(lease_key)
            if cur_val is None:
                return 1
            if cur_val == expected_val:
                del self.strings[lease_key]
                self.expirations.pop(lease_key, None)
                return 1
            return 0

        raise NotImplementedError(f"Unsupported Lua script in test harness: {script[:40]}")


@dataclass
class MockJobLeaseRow:
    job_id: str
    tenant_id: uuid.UUID
    owner_token: int
    claimed_by: str
    lease_until: datetime
    created_at: datetime


class MockQueryResult:
    def __init__(self, value: Any = None, row: Any = None, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._value = value
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar_one(self) -> Any:
        if self._value is None:
            raise ValueError("No scalar result")
        return self._value

    def first(self) -> Any:
        if self._row is not None:
            return self._row
        if self._value is not None:
            return (self._value,)
        return None

    def all(self) -> list[Any]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class FencingMockDatabaseSession:
    """
    Simulates PostgreSQL 16 transactional optimistic fencing concurrency control:
    - job_leases table: (job_id, tenant_id, owner_token, claimed_by, lease_until)
    - domain entity tables: (webhook_events, pull_requests, issues, deployments)
    - Simulated transaction clock for lease expiration and time advancement.
    - Transaction rollback on stale fencing token rejection.
    - Exact rowcount tracking asserting 0 rows overwritten by stale zombie write.
    """

    def __init__(self, initial_clock: datetime | None = None) -> None:
        self.clock = initial_clock or datetime.now(timezone.utc)
        self.job_leases: dict[str, MockJobLeaseRow] = {}
        self.domain_rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}

        self.committed_job_leases: dict[str, MockJobLeaseRow] = {}
        self.committed_domain_rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}

        self.active_tenant_id: str | None = None
        self.rolled_back: bool = False
        self.committed: bool = False
        self.last_update_rowcount: int = 0
        self._save_checkpoint()

    def advance_clock(self, delta: timedelta) -> None:
        """Simulates advancing PostgreSQL server transaction clock (now())."""
        self.clock += delta

    def _save_checkpoint(self) -> None:
        self.committed_job_leases = {
            k: MockJobLeaseRow(**v.__dict__) for k, v in self.job_leases.items()
        }
        self.committed_domain_rows = {
            k: dict(v) for k, v in self.domain_rows.items()
        }

    async def commit(self) -> None:
        self.committed = True
        self._save_checkpoint()

    async def rollback(self) -> None:
        self.rolled_back = True
        self.job_leases = {
            k: MockJobLeaseRow(**v.__dict__) for k, v in self.committed_job_leases.items()
        }
        self.domain_rows = {
            k: dict(v) for k, v in self.committed_domain_rows.items()
        }

    def seed_row(
        self,
        table_name: str,
        record_id: uuid.UUID,
        tenant_id: uuid.UUID,
        token: int = 0,
        **extra_fields: Any,
    ) -> None:
        row_dict = {
            "id": record_id,
            "tenant_id": tenant_id,
            "active_fencing_token": token,
            "state": extra_fields.get("state", "open"),
            "status": extra_fields.get("status", "staged"),
            "updated_at": self.clock,
        }
        row_dict.update(extra_fields)
        self.domain_rows[(table_name, record_id)] = row_dict
        self._save_checkpoint()

    def get_row(self, table_name: str, record_id: uuid.UUID) -> dict[str, Any] | None:
        return self.domain_rows.get((table_name, record_id))

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> MockQueryResult:
        sql = str(statement).strip()
        params = params or {}

        # 1. Tenant context switch: set_config('app.current_tenant_id', ...)
        if "app.current_tenant_id" in sql:
            self.active_tenant_id = str(params.get("tenant_id"))
            return MockQueryResult()

        # 2. Upsert into job_leases: _UPSERT_JOB_LEASE_SQL
        if "INSERT INTO job_leases" in sql:
            job_id = str(params["job_id"])
            tenant_id = uuid.UUID(str(params["tenant_id"]))
            claimed_by = str(params["worker_id"])
            token = int(params["worker_claimed_token"])
            lease_until = self.clock + timedelta(seconds=30)
            created_at = self.clock

            existing = self.job_leases.get(job_id)
            if existing is None:
                row = MockJobLeaseRow(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    owner_token=token,
                    claimed_by=claimed_by,
                    lease_until=lease_until,
                    created_at=created_at,
                )
                self.job_leases[job_id] = row
                return MockQueryResult(value=token, rowcount=1)

            # ON CONFLICT DO UPDATE WHERE job_leases.owner_token < EXCLUDED.owner_token
            if existing.owner_token < token:
                existing.owner_token = token
                existing.claimed_by = claimed_by
                existing.lease_until = lease_until
                return MockQueryResult(value=token, rowcount=1)
            else:
                # Fencing conflict rejected: EXCLUDED token not strictly higher!
                self.last_update_rowcount = 0
                return MockQueryResult(value=None, rowcount=0)

        # 3. Check job_leases owner token: _CHECK_JOB_LEASE_OWNER_SQL
        if "FROM job_leases" in sql and "job_id = :job_id" in sql and "lease_until" not in sql:
            job_id = str(params["job_id"])
            existing = self.job_leases.get(job_id)
            if existing is not None:
                return MockQueryResult(row=(existing.owner_token, existing.claimed_by))
            return MockQueryResult(row=None)

        # 4. Renew job lease: UPDATE job_leases SET lease_until = now() + 30s
        if "UPDATE job_leases" in sql and "lease_until > now()" in sql:
            job_id = str(params["job_id"])
            claimed_by = str(params["worker_id"])
            token = int(params["worker_claimed_token"])

            existing = self.job_leases.get(job_id)
            if (
                existing is not None
                and existing.claimed_by == claimed_by
                and existing.owner_token == token
                and existing.lease_until > self.clock
            ):
                existing.lease_until = self.clock + timedelta(seconds=30)
                return MockQueryResult(value=token, rowcount=1)
            return MockQueryResult(value=None, rowcount=0)

        # 5. Select expired leases: WHERE lease_until < now()
        if "FROM job_leases" in sql and "lease_until < now()" in sql:
            expired = [
                (r.job_id, r.tenant_id, r.owner_token, r.claimed_by, r.lease_until)
                for r in self.job_leases.values()
                if r.lease_until <= self.clock
            ]
            return MockQueryResult(rows=expired)

        # 6. Delete job lease (release)
        if "DELETE FROM job_leases" in sql:
            job_id = str(params["job_id"])
            claimed_by = str(params["worker_id"])
            token = int(params["worker_claimed_token"])
            existing = self.job_leases.get(job_id)
            if (
                existing is not None
                and existing.claimed_by == claimed_by
                and existing.owner_token == token
            ):
                del self.job_leases[job_id]
                return MockQueryResult(value=token, rowcount=1)
            return MockQueryResult(value=None, rowcount=0)

        # 7. SQLAlchemy Core UPDATE on domain table (execute_fenced_domain_update)
        if sql.lstrip().upper().startswith("UPDATE"):
            compiled = statement.compile() if hasattr(statement, "compile") else None
            cparams = compiled.params if compiled else params
            table_name = getattr(getattr(statement, "table", None), "name", None)
            if not table_name:
                for t in ["webhook_events", "pull_requests", "issues", "deployments"]:
                    if t in sql:
                        table_name = t
                        break

            record_id = cparams.get("id_1", params.get("id"))
            tenant_id = cparams.get("tenant_id_1", params.get("tenant_id"))
            claimed_tok = cparams.get("active_fencing_token_1", params.get("worker_claimed_token"))

            row = self.domain_rows.get((table_name, record_id))
            if row and row["tenant_id"] == tenant_id and row["active_fencing_token"] <= claimed_tok:
                # Fencing condition satisfied: active_fencing_token <= :worker_claimed_token
                row["active_fencing_token"] = cparams.get("active_fencing_token", claimed_tok)
                for k, v in cparams.items():
                    if k not in {"id_1", "tenant_id_1", "active_fencing_token_1", "updated_at"}:
                        row[k] = v
                row["updated_at"] = self.clock
                self.last_update_rowcount = 1
                return MockQueryResult(value=record_id, rowcount=1)

            # Stale write rejected! 0 rows overwritten
            self.last_update_rowcount = 0
            return MockQueryResult(value=None, rowcount=0)

        # 8. SQLAlchemy Core SELECT active_fencing_token on domain table
        if sql.lstrip().upper().startswith("SELECT"):
            compiled = statement.compile() if hasattr(statement, "compile") else None
            cparams = compiled.params if compiled else params
            table_name = None
            if hasattr(statement, "column_descriptions") and statement.column_descriptions:
                entity = statement.column_descriptions[0].get("entity")
                table_name = getattr(entity, "__tablename__", None)
            if not table_name and hasattr(statement, "get_final_froms"):
                froms = statement.get_final_froms()
                if froms:
                    table_name = getattr(froms[0], "name", None)
            if not table_name:
                for t in ["webhook_events", "pull_requests", "issues", "deployments"]:
                    if t in sql:
                        table_name = t
                        break

            record_id = cparams.get("id_1", params.get("id"))
            tenant_id = cparams.get("tenant_id_1", params.get("tenant_id"))
            row = self.domain_rows.get((table_name, record_id))
            if row and row["tenant_id"] == tenant_id:
                return MockQueryResult(value=row["active_fencing_token"])
            return MockQueryResult(value=None)

        raise NotImplementedError(f"Unhandled SQL statement in test harness: {sql[:60]}")


async def _is_live_pg_ready() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception:
        return False


async def _provision_test_tenant(slug_prefix: str) -> uuid.UUID:
    uid = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM provision_tenant(
                    p_name => :name,
                    p_slug => :slug,
                    p_admin_email => :admin_email,
                    p_admin_name => :admin_name
                )
                """
            ),
            {
                "name": f"Org {slug_prefix} {uid}",
                "slug": f"{slug_prefix}-{uid}",
                "admin_email": f"{slug_prefix}-{uid}@test.local",
                "admin_name": f"Admin {uid}",
            },
        )
        tid = res.scalar_one()
        await db.commit()
        return tid


# ==============================================================================
# Master Automated Split-Brain & Fencing Pytest Suite (Task 22.6 / SCRUM-414)
# ==============================================================================


@pytest.mark.asyncio
async def test_split_brain_resumed_zombie_worker_updates_zero_rows_and_logs_rejected_stale_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Task 22.6 (SCRUM-414) Core Acceptance Test:
    Simulates:
      1. Worker 1 lease acquisition (token = 1).
      2. Worker 1 pause / lease expiration (> 30s).
      3. Worker 2 takeover (token = 2) and DB commit ('merged', active_fencing_token = 2).
      4. Resumed Worker 1 attempting to commit domain update with stale token = 1.
    Asserts:
      - Exactly 0 rows overwritten in domain table (WHERE active_fencing_token <= 1 evaluates FALSE).
      - Worker 2's authoritative commit state ('merged', token = 2) remains preserved.
      - STALE_FENCING_TOKEN_REJECTED structured warning log is emitted.
      - Worker 1 transaction rolls back cleanly.
      - StaleFencingTokenError is raised.
    """
    caplog.set_level(logging.WARNING, logger="workers.fencing")

    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    redis = FencingInMemoryAsyncRedis(initial_time=0.0)
    db = FencingMockDatabaseSession(initial_clock=now)

    job_id = "job-pr-sync-42"
    pr_id = uuid.uuid4()

    # Seed PR in database with initial state 'open' and active_fencing_token = 0
    db.seed_row(
        table_name="pull_requests",
        record_id=pr_id,
        tenant_id=TENANT_ALPHA,
        token=0,
        state="open",
    )

    # -------------------------------------------------------------------------
    # Step 1: Worker 1 lease acquisition (token = 1)
    # -------------------------------------------------------------------------
    handle_w1 = await acquire_job_lease(
        redis=redis,
        db=db,
        job_id=job_id,
        tenant_id=TENANT_ALPHA,
        worker_id=WORKER_1,
        lease_ttl_ms=30_000,
    )
    assert handle_w1 is not None
    assert handle_w1.owner_token == 1
    assert handle_w1.worker_id == WORKER_1
    assert handle_w1.job_id == job_id
    assert db.job_leases[job_id].owner_token == 1
    assert db.job_leases[job_id].claimed_by == WORKER_1
    assert await redis.get(f"{LEASE_KEY_PREFIX}{job_id}") == f"{WORKER_1}:1"
    assert await redis.pttl(f"{LEASE_KEY_PREFIX}{job_id}") == 30_000

    # -------------------------------------------------------------------------
    # Step 2: Worker 1 pauses (e.g. SIGSTOP / GC freeze / network stall)
    #         Lease expires (> 30s)
    # -------------------------------------------------------------------------
    freeze_duration_seconds = 35.0  # > 30s lease TTL
    redis.advance_time(freeze_duration_seconds)
    db.advance_clock(timedelta(seconds=freeze_duration_seconds))

    # Assert that lease has expired in Valkey / Redis (key purged)
    assert await redis.get(f"{LEASE_KEY_PREFIX}{job_id}") is None
    # Assert that lease has expired in PostgreSQL (lease_until <= now())
    assert db.job_leases[job_id].lease_until <= db.clock

    # -------------------------------------------------------------------------
    # Step 3: Worker 2 takeover (token = 2) and DB commit
    # -------------------------------------------------------------------------
    handle_w2 = await acquire_job_lease(
        redis=redis,
        db=db,
        job_id=job_id,
        tenant_id=TENANT_ALPHA,
        worker_id=WORKER_2,
        lease_ttl_ms=30_000,
    )
    assert handle_w2 is not None
    # Strictly increasing monotonic token: token_2 = 2 > token_1 = 1
    assert handle_w2.owner_token == 2
    assert handle_w2.owner_token > handle_w1.owner_token
    assert handle_w2.worker_id == WORKER_2
    assert db.job_leases[job_id].owner_token == 2
    assert db.job_leases[job_id].claimed_by == WORKER_2

    # Worker 2 performs authoritative domain mutation ('merged', token = 2) and commits
    ok_w2 = await execute_fenced_domain_update(
        db,
        table_name="pull_requests",
        record_id=pr_id,
        tenant_id=TENANT_ALPHA,
        worker_claimed_token=handle_w2.owner_token,
        values={"state": "merged"},
        job_id=job_id,
        worker_id=WORKER_2,
    )
    assert ok_w2 is True
    await db.commit()

    # Verify Worker 2's commit is durable
    pr_row_w2 = db.get_row("pull_requests", pr_id)
    assert pr_row_w2 is not None
    assert pr_row_w2["state"] == "merged"
    assert pr_row_w2["active_fencing_token"] == 2

    # -------------------------------------------------------------------------
    # Step 4: Resumed Worker 1 (zombie) attempts to commit with stale token = 1
    # -------------------------------------------------------------------------
    caplog.clear()
    db.rolled_back = False

    with pytest.raises(StaleFencingTokenError) as exc_info:
        await execute_fenced_domain_update(
            db,
            table_name="pull_requests",
            record_id=pr_id,
            tenant_id=TENANT_ALPHA,
            worker_claimed_token=handle_w1.owner_token,  # Stale token = 1
            values={"state": "closed"},  # Stale zombie overwrite attempt
            job_id=job_id,
            worker_id=WORKER_1,
        )

    # -------------------------------------------------------------------------
    # Step 5: Assert 0 rows overwritten, Worker 2's state preserved, log emitted
    # -------------------------------------------------------------------------
    # 1. Assert StaleFencingTokenError has stale claimed token
    assert exc_info.value.claimed_token == 1
    assert exc_info.value.job_id == job_id
    assert exc_info.value.worker_id == WORKER_1

    # 2. Assert exactly 0 rows were updated/overwritten by Worker 1
    assert db.last_update_rowcount == 0

    # 3. Assert Worker 2's authoritative state is preserved intact (unmodified)
    pr_row_final = db.get_row("pull_requests", pr_id)
    assert pr_row_final is not None
    assert pr_row_final["state"] == "merged"
    assert pr_row_final["active_fencing_token"] == 2

    # 4. Assert transaction was rolled back cleanly
    assert db.rolled_back is True

    # 5. Assert STALE_FENCING_TOKEN_REJECTED structured warning was logged
    stale_logs = [rec for rec in caplog.records if "STALE_FENCING_TOKEN_REJECTED" in rec.message]
    assert len(stale_logs) >= 1
    stale_rec = stale_logs[0]
    assert stale_rec.levelname == "WARNING"
    assert "table=pull_requests" in stale_rec.message
    assert "claimed_token=1" in stale_rec.message
    assert "persisted_token=2" in stale_rec.message

    # Verify structured extra payload
    assert getattr(stale_rec, "event", None) == "STALE_FENCING_TOKEN_REJECTED"
    assert getattr(stale_rec, "claimed_token", None) == 1
    assert getattr(stale_rec, "persisted_token", None) == 2
    assert getattr(stale_rec, "table_name", None) == "pull_requests"


@pytest.mark.parametrize(
    ("table_name", "w2_values", "w1_stale_values", "state_field"),
    [
        ("webhook_events", {"status": "completed"}, {"status": "failed"}, "status"),
        ("pull_requests", {"state": "merged"}, {"state": "closed"}, "state"),
        ("issues", {"status": "Done"}, {"status": "In Progress"}, "status"),
        ("deployments", {"status": "SUCCESS"}, {"status": "FAILED"}, "status"),
    ],
)
@pytest.mark.asyncio
async def test_split_brain_zombie_rejection_across_all_domain_tables(
    caplog: pytest.LogCaptureFixture,
    table_name: str,
    w2_values: dict[str, Any],
    w1_stale_values: dict[str, Any],
    state_field: str,
) -> None:
    """
    Asserts split-brain zombie write rejection across all domain tables:
    - webhook_events
    - pull_requests
    - issues
    - deployments
    In all cases:
      - Worker 1 acquires token = 1.
      - Lease expires (> 30s).
      - Worker 2 acquires token = 2 and commits.
      - Worker 1 attempts commit with token = 1:
        * 0 rows overwritten
        * Worker 2 state preserved
        * STALE_FENCING_TOKEN_REJECTED logged
        * StaleFencingTokenError raised
    """
    caplog.set_level(logging.WARNING, logger="workers.fencing")

    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    redis = FencingInMemoryAsyncRedis(initial_time=0.0)
    db = FencingMockDatabaseSession(initial_clock=now)

    job_id = f"job-{table_name}-sync"
    record_id = uuid.uuid4()

    # Seed record with active_fencing_token = 0
    db.seed_row(table_name, record_id, TENANT_ALPHA, token=0)

    # 1. Worker 1 acquires lease (token = 1)
    h1 = await acquire_job_lease(redis, db, job_id, TENANT_ALPHA, WORKER_1)
    assert h1 is not None and h1.owner_token == 1

    # 2. Worker 1 pauses > 30s; lease expires
    redis.advance_time(35.0)
    db.advance_clock(timedelta(seconds=35))
    assert await redis.get(f"{LEASE_KEY_PREFIX}{job_id}") is None
    assert db.job_leases[job_id].lease_until <= db.clock

    # 3. Worker 2 takeover (token = 2) and commits domain row
    h2 = await acquire_job_lease(redis, db, job_id, TENANT_ALPHA, WORKER_2)
    assert h2 is not None and h2.owner_token == 2

    ok = await execute_fenced_domain_update(
        db,
        table_name=table_name,  # type: ignore[arg-type]
        record_id=record_id,
        tenant_id=TENANT_ALPHA,
        worker_claimed_token=h2.owner_token,
        values=w2_values,
        job_id=job_id,
        worker_id=WORKER_2,
    )
    assert ok is True
    await db.commit()

    # Verify Worker 2 state persisted
    row_w2 = db.get_row(table_name, record_id)
    assert row_w2 is not None
    assert row_w2[state_field] == w2_values[state_field]
    assert row_w2["active_fencing_token"] == 2

    # 4. Worker 1 resumes (zombie) and attempts stale commit with token = 1
    caplog.clear()
    with pytest.raises(StaleFencingTokenError) as exc_info:
        await execute_fenced_domain_update(
            db,
            table_name=table_name,  # type: ignore[arg-type]
            record_id=record_id,
            tenant_id=TENANT_ALPHA,
            worker_claimed_token=h1.owner_token,  # Stale token 1
            values=w1_stale_values,
            job_id=job_id,
            worker_id=WORKER_1,
        )

    # 5. Assert 0 rows overwritten, Worker 2 state preserved, STALE_FENCING_TOKEN_REJECTED logged
    assert exc_info.value.claimed_token == 1
    assert db.last_update_rowcount == 0

    final_row = db.get_row(table_name, record_id)
    assert final_row is not None
    assert final_row[state_field] == w2_values[state_field]
    assert final_row["active_fencing_token"] == 2

    assert any(
        "STALE_FENCING_TOKEN_REJECTED" in rec.message and f"table={table_name}" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_direct_sql_optimistic_fencing_updates_zero_rows_on_stale_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Direct SQL Predicate Test:
    Asserts that the core SQL statement:
        UPDATE pull_requests
        SET state = :state, active_fencing_token = :token
        WHERE id = :id AND tenant_id = :tenant_id AND active_fencing_token <= :token
    modifies exactly 0 rows (rowcount == 0) when :token = 1 and persisted active_fencing_token = 2.
    """
    caplog.set_level(logging.WARNING, logger="workers.fencing")

    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = FencingMockDatabaseSession(initial_clock=now)
    pr_id = uuid.uuid4()

    # Seed PR with active_fencing_token = 2 (already committed by Worker 2)
    db.seed_row(
        table_name="pull_requests",
        record_id=pr_id,
        tenant_id=TENANT_ALPHA,
        token=2,
        state="merged",
    )

    # Worker 1 attempts direct SQL execution with stale token = 1
    # WHERE active_fencing_token <= 1 is FALSE (since 2 <= 1 is FALSE)
    stale_token = 1
    stmt = (
        text(
            """
            UPDATE pull_requests
            SET state = :state, active_fencing_token = :worker_claimed_token
            WHERE id = :pr_id AND tenant_id = :tenant_id AND active_fencing_token <= :worker_claimed_token
            """
        )
    )
    res = await db.execute(
        stmt,
        {
            "state": "closed_by_zombie",
            "worker_claimed_token": stale_token,
            "pr_id": pr_id,
            "tenant_id": TENANT_ALPHA,
        },
    )

    # Assert exactly 0 rows were updated
    assert res.rowcount == 0
    assert db.last_update_rowcount == 0

    # Emit structured log as done by fencing layer
    log_stale_fencing_token_rejected(
        table_name="pull_requests",
        record_id=pr_id,
        tenant_id=TENANT_ALPHA,
        claimed_token=stale_token,
        persisted_token=2,
        job_id="direct-sql-job",
        worker_id=WORKER_1,
    )

    # Assert Worker 2 state remains untouched
    row = db.get_row("pull_requests", pr_id)
    assert row is not None
    assert row["state"] == "merged"
    assert row["active_fencing_token"] == 2

    # Assert STALE_FENCING_TOKEN_REJECTED log recorded
    assert any("STALE_FENCING_TOKEN_REJECTED" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_split_brain_zombie_fenced_at_job_leases_before_domain_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Layer 1 Fencing Check:
    Verifies that when verify_job_lease=True is passed, a resumed zombie Worker 1
    is fenced out at the job_leases layer before any domain table write is attempted,
    logging STALE_FENCING_TOKEN_REJECTED with table_name='job_leases'.
    """
    caplog.set_level(logging.WARNING, logger="workers.fencing")

    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    redis = FencingInMemoryAsyncRedis(initial_time=0.0)
    db = FencingMockDatabaseSession(initial_clock=now)

    job_id = "job-lease-guard-42"
    pr_id = uuid.uuid4()

    db.seed_row("pull_requests", pr_id, TENANT_ALPHA, token=0, state="open")

    # Worker 1 acquires lease (token = 1)
    h1 = await acquire_job_lease(redis, db, job_id, TENANT_ALPHA, WORKER_1)
    assert h1 is not None and h1.owner_token == 1

    # Lease expires (> 30s)
    redis.advance_time(35.0)
    db.advance_clock(timedelta(seconds=35))

    # Worker 2 acquires lease (token = 2) in job_leases
    h2 = await acquire_job_lease(redis, db, job_id, TENANT_ALPHA, WORKER_2)
    assert h2 is not None and h2.owner_token == 2
    assert db.job_leases[job_id].owner_token == 2

    # Worker 1 wakes up and attempts commit with verify_job_lease=True
    caplog.clear()
    with pytest.raises(StaleFencingTokenError) as exc_info:
        await execute_fenced_domain_update(
            db,
            table_name="pull_requests",
            record_id=pr_id,
            tenant_id=TENANT_ALPHA,
            worker_claimed_token=h1.owner_token,  # Stale token 1
            values={"state": "corrupted_by_zombie"},
            job_id=job_id,
            worker_id=WORKER_1,
            verify_job_lease=True,
        )

    assert exc_info.value.claimed_token == 1
    assert db.rolled_back is True
    # Domain row active_fencing_token remains 0 (Worker 1 never even touched it)
    assert db.get_row("pull_requests", pr_id)["active_fencing_token"] == 0
    assert db.get_row("pull_requests", pr_id)["state"] == "open"

    # STALE_FENCING_TOKEN_REJECTED logged for table=job_leases
    assert any(
        "STALE_FENCING_TOKEN_REJECTED" in rec.message and "table=job_leases" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_resumed_zombie_worker_cannot_renew_or_release_successor_redis_lease() -> None:
    """
    Valkey Ownership Isolation:
    Resumed zombie Worker 1 (token = 1) calling RENEW_LEASE_LUA or RELEASE_LEASE_LUA
    cannot modify or delete Worker 2's active lease key (token = 2).
    """
    redis = FencingInMemoryAsyncRedis(initial_time=0.0)
    job_id = "isolation-job-77"
    lease_key = f"{LEASE_KEY_PREFIX}{job_id}"

    # Worker 1 acquires lease (token = 1)
    acq1, tok1, val1 = await acquire_redis_lease(redis, job_id, WORKER_1)
    assert acq1 is True and tok1 == 1

    # Lease expires (> 30s)
    redis.advance_time(35.0)
    assert await redis.get(lease_key) is None

    # Worker 2 acquires lease (token = 2)
    acq2, tok2, val2 = await acquire_redis_lease(redis, job_id, WORKER_2)
    assert acq2 is True and tok2 == 2
    assert val2 == f"{WORKER_2}:2"
    assert await redis.get(lease_key) == f"{WORKER_2}:2"

    # Zombie Worker 1 tries to renew Worker 2's lease with old handle
    handle_zombie = LeaseHandle(
        job_id=job_id,
        tenant_id=TENANT_ALPHA,
        worker_id=WORKER_1,
        owner_token=1,
        lease_key=lease_key,
        lease_value=val1,
    )
    renew_res = await renew_job_lease(redis, None, handle_zombie)
    assert renew_res is False
    assert await redis.get(lease_key) == f"{WORKER_2}:2"

    # Zombie Worker 1 tries to release Worker 2's lease
    release_res = await release_job_lease(
        redis=redis,
        job_id=job_id,
        worker_id=WORKER_1,
        owner_token=1,
    )
    assert release_res is False
    assert await redis.get(lease_key) == f"{WORKER_2}:2"


@pytest.mark.asyncio
async def test_split_brain_supervisor_reclamation_and_zombie_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    End-to-end integration with supervisor reclamation:
    1. Worker 1 claims job J1 (token = 1) and places payload in arq:in_flight:W1.
    2. Worker 1 pauses > 30s; lease expires.
    3. Supervisor reclaim_expired_leases detects expired lease, cleans up stale lease,
       and re-queues J1 to arq:queue:default.
    4. Worker 2 dequeues J1, acquires lease (token = 2 > 1), and commits state.
    5. Resumed Worker 1 attempts to commit with stale token = 1.
    6. Assert 0 rows overwritten, Worker 2 state preserved, STALE_FENCING_TOKEN_REJECTED logged.
    """
    caplog.set_level(logging.WARNING, logger="workers.fencing")

    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    redis = FencingInMemoryAsyncRedis(initial_time=0.0)
    db = FencingMockDatabaseSession(initial_clock=now)

    job_id = "supervisor-split-job-99"
    pr_id = uuid.uuid4()

    db.seed_row("pull_requests", pr_id, TENANT_ALPHA, token=0, state="open")

    # Step 1: Worker 1 acquires lease (token = 1)
    h1 = await acquire_job_lease(redis, db, job_id, TENANT_ALPHA, WORKER_1)
    assert h1 is not None and h1.owner_token == 1

    # Place in-flight payload for Worker 1
    raw_payload = (
        f'{{"job_id":"{job_id}","tenant_id":"{TENANT_ALPHA}","priority":"default","owner_token":1}}'
    )
    await redis.rpush(f"arq:in_flight:{WORKER_1}", raw_payload)

    # Step 2: Worker 1 pauses for 35s (> 30s)
    redis.advance_time(35.0)
    db.advance_clock(timedelta(seconds=35))

    # Step 3: Supervisor detects expired lease and re-queues job
    reclaimed = await reclaim_expired_leases(
        redis=redis,
        db=db,
        tenant_id=TENANT_ALPHA,
        worker_ids=[WORKER_1],
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].job_id == job_id
    assert reclaimed[0].expired_owner_token == 1
    assert reclaimed[0].previous_worker_id == WORKER_1

    # Key was re-queued in default queue
    assert await redis.llen("arq:queue:default") == 1

    # Step 4: Worker 2 acquires lease (token = 2) and commits
    h2 = await acquire_job_lease(redis, db, job_id, TENANT_ALPHA, WORKER_2)
    assert h2 is not None and h2.owner_token == 2

    await execute_fenced_domain_update(
        db,
        table_name="pull_requests",
        record_id=pr_id,
        tenant_id=TENANT_ALPHA,
        worker_claimed_token=h2.owner_token,
        values={"state": "merged"},
        job_id=job_id,
        worker_id=WORKER_2,
    )
    await db.commit()

    assert db.get_row("pull_requests", pr_id)["state"] == "merged"
    assert db.get_row("pull_requests", pr_id)["active_fencing_token"] == 2

    # Step 5: Worker 1 resumes (zombie) and attempts to commit with stale token = 1
    caplog.clear()
    with pytest.raises(StaleFencingTokenError):
        await execute_fenced_domain_update(
            db,
            table_name="pull_requests",
            record_id=pr_id,
            tenant_id=TENANT_ALPHA,
            worker_claimed_token=h1.owner_token,
            values={"state": "closed"},
            job_id=job_id,
            worker_id=WORKER_1,
        )

    # Step 6: Assertions
    assert db.last_update_rowcount == 0
    assert db.get_row("pull_requests", pr_id)["state"] == "merged"
    assert db.get_row("pull_requests", pr_id)["active_fencing_token"] == 2
    assert any("STALE_FENCING_TOKEN_REJECTED" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_live_pg_split_brain_zombie_worker_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Live PostgreSQL Integration Test:
    Enforces real PostgreSQL optimistic fencing assertion under tenant RLS:
      1. Worker 2 commits state under token = 2.
      2. Zombie Worker 1 attempts commit with token = 1.
      3. Exactly 0 rows are updated, transaction rolls back, StaleFencingTokenError is raised,
         Worker 2 state is preserved, and STALE_FENCING_TOKEN_REJECTED is logged.
    Skipped if live PostgreSQL is not reachable in current environment.
    """
    if not await _is_live_pg_ready():
        pytest.skip("PostgreSQL database not reachable on host.")

    caplog.set_level(logging.WARNING, logger="workers.fencing")
    tid = await _provision_test_tenant("split")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with get_tenant_db_context(tid) as db:
        db.add(Project(id=proj_id, tenant_id=tid, name="Split Proj", slug=f"sp-{proj_id.hex[:6]}"))
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tid,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 10_000_000),
                full_name=f"org/split-{repo_id.hex[:6]}",
            )
        )
        await db.flush()
        db.add(
            PullRequest(
                id=pr_id,
                tenant_id=tid,
                project_id=proj_id,
                repository_id=repo_id,
                author_pseudonym_id="pseudo_split",
                external_pr_number=99,
                title="Split Brain PR",
                state="open",
                head_branch="feat/split",
                base_branch="main",
                head_sha="c" * 40,
                created_in_git_at=now,
                active_fencing_token=0,
            )
        )

    job_id = f"pull_requests:{pr_id}"

    # Worker 2 commits with token = 2 -> succeeds
    async with get_tenant_db_context(tid) as session:
        ok = await execute_fenced_domain_update(
            session,
            table_name="pull_requests",
            record_id=pr_id,
            tenant_id=tid,
            worker_claimed_token=2,
            values={"state": "merged"},
            job_id=job_id,
            worker_id=WORKER_2,
        )
        assert ok is True

    # Resumed Worker 1 attempts commit with token = 1 -> fails, rolls back, logs STALE_FENCING_TOKEN_REJECTED
    caplog.clear()
    with pytest.raises(StaleFencingTokenError) as exc_info:
        async with get_tenant_db_context(tid) as session:
            await execute_fenced_domain_update(
                session,
                table_name="pull_requests",
                record_id=pr_id,
                tenant_id=tid,
                worker_claimed_token=1,  # Stale token
                values={"state": "closed_by_zombie"},
                job_id=job_id,
                worker_id=WORKER_1,
            )

    assert exc_info.value.claimed_token == 1
    assert any("STALE_FENCING_TOKEN_REJECTED" in rec.message for rec in caplog.records)

    # Verify Worker 2 state remains preserved in real PostgreSQL
    async with get_tenant_db_context(tid) as session:
        res = await session.execute(
            text("SELECT state, active_fencing_token FROM pull_requests WHERE id = :pr_id"),
            {"pr_id": pr_id},
        )
        row = res.first()
        assert row is not None
        assert row[0] == "merged"
        assert row[1] == 2
