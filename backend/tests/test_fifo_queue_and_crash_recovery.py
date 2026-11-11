"""
SCRUM-407 (Task 21.4) & SCRUM-408 (Task 21.5):
Automated Pytest Verification Suite for Valkey / Redis 7 Reliable FIFO Queue Ordering,
High-Priority Queue Preemption, and Worker SIGKILL In-Flight Crash Recovery.

Authoritative Specification: docs/fifo-ordering-priority-preemption-and-sigkill-crash-test-scenarios.md
Topology Specification: docs/fifo-queue-topology.md

Test Coverage Matrix:
- TC-FIFO-100: Enqueue 100 numbered jobs and assert execution trace is strictly [1..100].
- TC-PRIO-050: Enqueue 50 default jobs + 1 high-priority job; assert high-priority executes first.
- TC-CRASH-001: Worker claims job into arq:in_flight:{worker_id}, experiences SIGKILL mid-job;
                asserts zero message loss and recovery via LMOVE RIGHT LEFT on worker restart.
- TC-CRASH-002: Worker claims job and crashes permanently; heartbeat expires (>30s); supervisor
                discovers and sweeps stranded in-flight list to queue head for surviving workers.
- TC-CRASH-003: High-priority job crashed in-flight recovers back to arq:queue:high (not default),
                continuing to preempt default-priority jobs.
- TC-FIFO-MULTI: Multi-job in-flight recovery preserves relative FIFO order via RIGHT -> LEFT moves.
- TC-CONCUR-002: Concurrent workers fetch from default queue with complete in-flight list isolation.
- TC-ENVELOPE: Canonical envelope serialization with deterministic JSON (sort_keys=True, compact).
"""

import asyncio
from collections import defaultdict
import fnmatch
import json
import uuid

import pytest

from workers.worker import (
    QUEUE_DEFAULT,
    QUEUE_HIGH,
    ack_job,
    discover_in_flight_keys,
    enqueue_job,
    extract_worker_id_from_in_flight_key,
    fetch_next_job,
    get_in_flight_key,
    get_worker_heartbeat_key,
    is_worker_alive,
    record_worker_heartbeat,
    recover_in_flight_jobs,
    serialize_job_payload,
    sweep_dead_workers,
)


class InMemoryAsyncRedis:
    """
    High-fidelity, in-memory async Redis 7 / Valkey mock implementing the exact list,
    string, key scanning, and atomic (B)LMOVE primitives required by backend/workers/worker.py.
    """

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, float] = {}

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

    async def lindex(self, key: str, index: int) -> str | None:
        items = self.lists.get(key, [])
        if 0 <= index < len(items):
            return items[index]
        if -len(items) <= index < 0:
            return items[index]
        return None

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

        # Pop from source
        if src_norm == "LEFT":
            element = src_list.pop(0)
        elif src_norm == "RIGHT":
            element = src_list.pop()
        else:
            raise ValueError(f"Invalid src: {src} (expected LEFT or RIGHT)")

        # Push to destination
        if dest_norm == "RIGHT":
            self.lists[destination].append(element)
        elif dest_norm == "LEFT":
            self.lists[destination].insert(0, element)
        else:
            raise ValueError(f"Invalid dest: {dest} (expected LEFT or RIGHT)")

        return element

    async def blmove(
        self,
        source: str,
        destination: str,
        timeout: float = 0.0,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        # Check immediately
        moved = await self.lmove(source, destination, src=src, dest=dest)
        if moved is not None:
            return moved
        if timeout <= 0:
            return None
        # Sleep briefly up to timeout or until item appears
        poll_step = min(0.05, timeout)
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(poll_step)
            elapsed += poll_step
            moved = await self.lmove(source, destination, src=src, dest=dest)
            if moved is not None:
                return moved
        return None

    async def lrem(self, key: str, count: int, element: str) -> int:
        items = self.lists.get(key, [])
        if not items:
            return 0
        removed = 0
        target = str(element)
        if count == 0:
            original_len = len(items)
            self.lists[key] = [x for x in items if x != target]
            return original_len - len(self.lists[key])
        if count > 0:
            new_items = []
            for item in items:
                if item == target and removed < count:
                    removed += 1
                else:
                    new_items.append(item)
            self.lists[key] = new_items
            return removed
        # count < 0: remove from tail to head
        new_items_rev = []
        for item in reversed(items):
            if item == target and removed < abs(count):
                removed += 1
            else:
                new_items_rev.append(item)
        self.lists[key] = list(reversed(new_items_rev))
        return removed

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        px: int | None = None,
    ) -> bool:
        self.strings[key] = str(value)
        return True

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def exists(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.strings or (k in self.lists and len(self.lists[k]) > 0):
                count += 1
        return count

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.strings:
                del self.strings[k]
                count += 1
            if k in self.lists:
                del self.lists[k]
                count += 1
        return count

    async def keys(self, pattern: str = "*") -> list[str]:
        all_keys = set(self.strings.keys()) | set(k for k, v in self.lists.items() if len(v) > 0)
        return [k for k in all_keys if fnmatch.fnmatch(k, pattern)]

    async def scan_iter(self, match: str | None = None):
        pattern = match or "*"
        matched = await self.keys(pattern)
        for k in matched:
            yield k


@pytest.fixture
def redis_client() -> InMemoryAsyncRedis:
    """Provides a fresh, hermetic in-memory Redis client for each test."""
    return InMemoryAsyncRedis()


# ==============================================================================
# 1. Deterministic Envelope Serialization Tests
# ==============================================================================


def test_job_envelope_deterministic_serialization():
    """
    Verifies that serialize_job_payload produces canonical, compact, sorted JSON
    with identical key order across multiple invocations.
    """
    job_id = uuid.UUID("9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d")
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    event_id = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")

    raw_json_1 = serialize_job_payload(
        job_id=job_id,
        tenant_id=tenant_id,
        priority="default",
        job_type="webhook_event",
        payload={"pull_request_number": 42, "action": "opened"},
        event_id=event_id,
        provider="github",
        attempt=0,
        enqueued_at="2026-11-11T12:00:00.000000Z",
    )

    raw_json_2 = serialize_job_payload(
        job_id=job_id,
        tenant_id=tenant_id,
        priority="default",
        job_type="webhook_event",
        payload={"action": "opened", "pull_request_number": 42},  # reverse key order in payload
        event_id=event_id,
        provider="github",
        attempt=0,
        enqueued_at="2026-11-11T12:00:00.000000Z",
    )

    # Invariant: Byte-for-byte exact equality guaranteed by sort_keys=True
    assert raw_json_1 == raw_json_2
    assert " " not in raw_json_1  # Compact separators (',', ':')
    parsed = json.loads(raw_json_1)
    assert parsed["job_id"] == str(job_id)
    assert parsed["tenant_id"] == str(tenant_id)
    assert parsed["priority"] == "default"
    assert parsed["attempt"] == 0
    assert parsed["version"] == 1


# ==============================================================================
# 2. TC-FIFO-100: 100-Job Sequential FIFO Execution Order
# ==============================================================================


@pytest.mark.asyncio
async def test_fifo_100_jobs_sequential_execution(redis_client: InMemoryAsyncRedis):
    """
    SCRUM-407 Scenario 1 (TC-FIFO-100):
    Enqueues 100 sequentially numbered jobs [1..100] into arq:queue:default.
    A single consumer fetches and acks all 100 jobs.
    Asserts that the consumed execution sequence strictly equals [1..100] with zero reordering.
    """
    worker_id = "manta-worker-node1-1001-a1b2c3d4"
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    in_flight_key = get_in_flight_key(worker_id)

    # 1. Enqueue 100 numbered jobs sequentially
    enqueued_job_ids = []
    for i in range(1, 101):
        job_id = uuid.uuid4()
        enqueued_job_ids.append(str(job_id))
        envelope = {
            "attempt": 0,
            "enqueued_at": f"2026-11-11T12:00:{i:02d}.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(job_id),
            "job_type": "webhook_event",
            "payload": {"job_index": i, "entity": f"event-{i}"},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        await enqueue_job(redis_client, envelope, priority="default")

    # Assert queue depth is exactly 100
    assert await redis_client.llen(QUEUE_DEFAULT) == 100
    assert await redis_client.llen(QUEUE_HIGH) == 0

    # 2. Consume all 100 jobs sequentially
    consumed_indices = []
    consumed_job_ids = []

    for expected_index in range(1, 101):
        fetch_result = await fetch_next_job(redis_client, worker_id=worker_id, poll_timeout=1.0)
        assert fetch_result is not None, f"Expected job {expected_index}, got None"
        source_queue, raw_job, payload = fetch_result

        # Assert correct source queue
        assert source_queue == QUEUE_DEFAULT

        # Assert atomic in-flight staging (exactly 1 job in-flight during execution)
        assert await redis_client.llen(in_flight_key) == 1

        # Record consumed job
        job_index = payload["payload"]["job_index"]
        consumed_indices.append(job_index)
        consumed_job_ids.append(payload["job_id"])

        # Acknowledge completed job
        ack_count = await ack_job(redis_client, worker_id, raw_job)
        assert ack_count == 1, f"Failed to acknowledge job {expected_index}"

        # Assert in-flight cleared post-ACK
        assert await redis_client.llen(in_flight_key) == 0

    # 3. Assert Strict Monotonic FIFO Order: [1, 2, 3, ..., 100]
    expected_order = list(range(1, 101))
    assert consumed_indices == expected_order, (
        f"FIFO order violated! Difference at index: "
        f"{[i for i, (a, b) in enumerate(zip(consumed_indices, expected_order)) if a != b]}"
    )
    assert consumed_job_ids == enqueued_job_ids

    # 4. Assert queue and in-flight are completely drained
    assert await redis_client.llen(QUEUE_DEFAULT) == 0
    assert await redis_client.llen(in_flight_key) == 0

    # 5. Assert next poll safely times out
    empty_result = await fetch_next_job(redis_client, worker_id=worker_id, poll_timeout=0.01)
    assert empty_result is None


# ==============================================================================
# 3. TC-PRIO-050: High-Priority Preemption behind 50 Default Jobs
# ==============================================================================


@pytest.mark.asyncio
async def test_high_priority_preemption_behind_50_default_jobs(
    redis_client: InMemoryAsyncRedis,
):
    """
    SCRUM-407 Scenario 2 (TC-PRIO-050):
    Enqueues 50 default jobs into arq:queue:default.
    Enqueues 1 high-priority job into arq:queue:high.
    Asserts that the high-priority job executes FIRST, followed by all 50 default jobs in order.
    Execution trace: ['H1', 'D1', 'D2', ..., 'D50'].
    """
    worker_id = "manta-worker-node1-1001-a1b2c3d4"
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    high_job_id = str(uuid.uuid4())

    # 1. Enqueue 50 default jobs
    for i in range(1, 51):
        envelope = {
            "attempt": 0,
            "enqueued_at": "2026-11-11T12:00:00.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "job_type": "standard_webhook",
            "payload": {"job_index": i, "tag": f"default-{i}"},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        await enqueue_job(redis_client, envelope, priority="default")

    assert await redis_client.llen(QUEUE_DEFAULT) == 50
    assert await redis_client.llen(QUEUE_HIGH) == 0

    # 2. Enqueue 1 high-priority job behind the 50 default jobs
    high_envelope = {
        "attempt": 0,
        "enqueued_at": "2026-11-11T12:01:00.000000Z",
        "event_id": str(uuid.uuid4()),
        "job_id": high_job_id,
        "job_type": "dlq_redrive",
        "payload": {"job_index": 999, "tag": "high-priority-urgent"},
        "priority": "high",
        "provider": "jira",
        "tenant_id": str(tenant_id),
        "version": 1,
    }
    await enqueue_job(redis_client, high_envelope, priority="high")

    assert await redis_client.llen(QUEUE_DEFAULT) == 50
    assert await redis_client.llen(QUEUE_HIGH) == 1

    # 3. Consume all 51 jobs and record execution trace
    executed_sequence: list[str] = []

    for cycle in range(1, 52):
        fetch_result = await fetch_next_job(redis_client, worker_id=worker_id, poll_timeout=1.0)
        assert fetch_result is not None, f"Cycle {cycle} returned None unexpectedly"
        source_queue, raw_job, payload = fetch_result

        if cycle == 1:
            # High-priority preemption assertion: cycle 1 MUST yield H1
            assert source_queue == QUEUE_HIGH, (
                f"Preemption failed! Expected {QUEUE_HIGH}, got {source_queue}"
            )
            assert payload["job_id"] == high_job_id
            assert payload["priority"] == "high"
            executed_sequence.append("H1")
        else:
            # Default FIFO drainage: cycles 2..51 MUST yield D1..D50 in order
            assert source_queue == QUEUE_DEFAULT
            assert payload["priority"] == "default"
            idx = payload["payload"]["job_index"]
            executed_sequence.append(f"D{idx}")

        await ack_job(redis_client, worker_id, raw_job)

    # 4. Assert exact execution trace: ['H1', 'D1', 'D2', ..., 'D50']
    expected_sequence = ["H1"] + [f"D{i}" for i in range(1, 51)]
    assert executed_sequence == expected_sequence

    # 5. Assert all queues drained
    assert await redis_client.llen(QUEUE_HIGH) == 0
    assert await redis_client.llen(QUEUE_DEFAULT) == 0
    assert await redis_client.llen(get_in_flight_key(worker_id)) == 0


# ==============================================================================
# 4. TC-CRASH-001: Worker SIGKILL Crash Mid-Job & Self-Restart Recovery
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_sigkill_mid_job_and_self_restart_recovery(
    redis_client: InMemoryAsyncRedis,
):
    """
    SCRUM-407 Scenario 3 (TC-CRASH-001):
    Enqueues 5 jobs [J1..J5]. Worker 1 claims J1 into arq:in_flight:{worker_id}.
    Simulates abrupt worker SIGKILL (process dies, no ack_job dispatched).
    Asserts:
    1. J1 remains safe in arq:in_flight:{worker_id}.
    2. Upon worker restart, recover_in_flight_jobs() restores J1 to HEAD (LEFT) of arq:queue:default.
    3. Worker resumes and completes all 5 jobs in exact order [J1, J2, J3, J4, J5].
    """
    worker_id = "manta-worker-node1-pid100-crash01"
    in_flight_key = get_in_flight_key(worker_id)
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # 1. Enqueue 5 jobs: J1 to J5
    for i in range(1, 6):
        envelope = {
            "attempt": 0,
            "enqueued_at": f"2026-11-11T12:00:0{i}.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "job_type": "webhook_event",
            "payload": {"job_index": i},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        await enqueue_job(redis_client, envelope, priority="default")

    assert await redis_client.llen(QUEUE_DEFAULT) == 5

    # 2. Worker 1 claims Job 1 into in-flight
    claimed_result = await fetch_next_job(redis_client, worker_id=worker_id, poll_timeout=1.0)
    assert claimed_result is not None
    src_queue, raw_j1, payload_j1 = claimed_result
    assert payload_j1["payload"]["job_index"] == 1

    # Verify state while J1 is in-flight:
    # arq:queue:default has 4 remaining jobs [J2, J3, J4, J5]
    # arq:in_flight:{worker_id} has exactly 1 job [J1]
    assert await redis_client.llen(QUEUE_DEFAULT) == 4
    assert await redis_client.llen(in_flight_key) == 1

    # 3. Simulate Worker Process SIGKILL
    # The process crashes abruptly: no ack_job(), no finally blocks, no cleanup.
    # Invariant: J1 is NOT lost; it remains safely staged in Valkey!
    in_flight_items = await redis_client.lrange(in_flight_key, 0, -1)
    assert len(in_flight_items) == 1
    assert json.loads(in_flight_items[0])["payload"]["job_index"] == 1

    # 4. Worker Restarts & Executes on_startup in-flight recovery
    recovered_records = await recover_in_flight_jobs(redis_client, worker_id=worker_id)

    # Assert recovery details
    assert len(recovered_records) == 1
    dest_queue, rec_raw, rec_parsed = recovered_records[0]
    assert dest_queue == QUEUE_DEFAULT
    assert rec_parsed["payload"]["job_index"] == 1

    # Assert Valkey state immediately post-recovery:
    # J1 is restored to the HEAD (index 0) of arq:queue:default!
    # In-flight list is cleared
    assert await redis_client.llen(in_flight_key) == 0
    assert await redis_client.llen(QUEUE_DEFAULT) == 5
    head_item = await redis_client.lindex(QUEUE_DEFAULT, 0)
    assert head_item is not None
    assert json.loads(head_item)["payload"]["job_index"] == 1

    # 5. Worker Resumes Pipeline & Processes all 5 jobs to completion
    consumed_order = []
    for _ in range(5):
        res = await fetch_next_job(redis_client, worker_id=worker_id, poll_timeout=1.0)
        assert res is not None
        _, raw, p = res
        consumed_order.append(p["payload"]["job_index"])
        await ack_job(redis_client, worker_id, raw)

    # Assert zero message loss and preserved [1, 2, 3, 4, 5] FIFO order
    assert consumed_order == [1, 2, 3, 4, 5]
    assert await redis_client.llen(QUEUE_DEFAULT) == 0
    assert await redis_client.llen(in_flight_key) == 0


# ==============================================================================
# 5. TC-CRASH-002: Dead-Worker Supervisor Sweep with Expired Heartbeat
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_sigkill_and_supervisor_orphan_sweep(
    redis_client: InMemoryAsyncRedis,
):
    """
    SCRUM-407 Scenario (TC-CRASH-002):
    Worker 1 claims Job 1, then crashes permanently (e.g. host died).
    Its heartbeat expires.
    A background supervisor detects the dead worker, sweeps its in-flight list back to
    arq:queue:default (RIGHT LEFT), and surviving Worker 2 processes all jobs.
    """
    dead_worker_id = "manta-worker-node1-dead-9999"
    surviving_worker_id = "manta-worker-node2-live-1111"
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # Enqueue 3 jobs
    for i in range(1, 4):
        envelope = {
            "attempt": 0,
            "enqueued_at": "2026-11-11T12:00:00.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "job_type": "webhook_event",
            "payload": {"job_index": i},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        await enqueue_job(redis_client, envelope, priority="default")

    # Worker 1 sets heartbeat and claims Job 1
    await record_worker_heartbeat(redis_client, dead_worker_id, ttl_seconds=30)
    assert await is_worker_alive(redis_client, dead_worker_id) is True

    claim_1 = await fetch_next_job(redis_client, worker_id=dead_worker_id, poll_timeout=1.0)
    assert claim_1 is not None

    # Simulate Worker 1 crashing permanently & heartbeat expiring (>30s)
    heartbeat_key = get_worker_heartbeat_key(dead_worker_id)
    await redis_client.delete(heartbeat_key)
    assert await is_worker_alive(redis_client, dead_worker_id) is False

    # Surviving Worker 2 is alive
    await record_worker_heartbeat(redis_client, surviving_worker_id, ttl_seconds=30)

    # Supervisor runs sweep
    swept_results = await sweep_dead_workers(redis_client, exclude_worker_ids=[surviving_worker_id])
    assert dead_worker_id in swept_results
    assert len(swept_results[dead_worker_id]) == 1
    assert swept_results[dead_worker_id][0][2]["payload"]["job_index"] == 1

    # Surviving Worker 2 consumes all 3 jobs
    consumed = []
    for _ in range(3):
        res = await fetch_next_job(redis_client, worker_id=surviving_worker_id, poll_timeout=1.0)
        assert res is not None
        _, raw, payload = res
        consumed.append(payload["payload"]["job_index"])
        await ack_job(redis_client, surviving_worker_id, raw)

    assert consumed == [1, 2, 3]
    assert await redis_client.llen(QUEUE_DEFAULT) == 0
    assert await redis_client.llen(get_in_flight_key(dead_worker_id)) == 0


# ==============================================================================
# 6. TC-CRASH-003: High-Priority Job Crash Recovery Preserves Preemption
# ==============================================================================


@pytest.mark.asyncio
async def test_high_priority_in_flight_crash_recovery_preserves_preemption(
    redis_client: InMemoryAsyncRedis,
):
    """
    SCRUM-407 Scenario (TC-CRASH-003):
    Worker 1 claims a high-priority job H1 while 5 default jobs are waiting.
    Worker 1 crashes mid-job.
    Asserts that recovery inspects priority='high' and routes H1 back to arq:queue:high,
    ensuring that H1 still preempts all default jobs upon pipeline recovery.
    """
    worker_id = "manta-worker-node1-prio-crash"
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # Enqueue 5 default jobs
    for i in range(1, 6):
        await enqueue_job(
            redis_client,
            {
                "attempt": 0,
                "enqueued_at": "2026-11-11T12:00:00.000000Z",
                "event_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "job_type": "default_job",
                "payload": {"job_index": i},
                "priority": "default",
                "provider": "github",
                "tenant_id": str(tenant_id),
                "version": 1,
            },
            priority="default",
        )

    # Enqueue 1 high-priority job
    high_job_id = str(uuid.uuid4())
    await enqueue_job(
        redis_client,
        {
            "attempt": 0,
            "enqueued_at": "2026-11-11T12:00:01.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": high_job_id,
            "job_type": "high_prio_job",
            "payload": {"job_index": 999},
            "priority": "high",
            "provider": "jira",
            "tenant_id": str(tenant_id),
            "version": 1,
        },
        priority="high",
    )

    # Worker claims H1 via Phase 1
    claimed = await fetch_next_job(redis_client, worker_id=worker_id)
    assert claimed is not None
    assert claimed[0] == QUEUE_HIGH
    assert claimed[2]["job_id"] == high_job_id

    # Worker crashes without ACK
    # Recover in-flight jobs
    recovered = await recover_in_flight_jobs(redis_client, worker_id=worker_id)
    assert len(recovered) == 1
    # Crucial assertion: dynamic routing sent H1 back to arq:queue:high, NOT default!
    assert recovered[0][0] == QUEUE_HIGH

    # Resume consumption: H1 must execute first!
    res_1 = await fetch_next_job(redis_client, worker_id=worker_id)
    assert res_1 is not None
    assert res_1[0] == QUEUE_HIGH
    assert res_1[2]["job_id"] == high_job_id
    await ack_job(redis_client, worker_id, res_1[1])

    # Next 5 jobs are default 1..5 in order
    consumed = []
    for _ in range(5):
        res = await fetch_next_job(redis_client, worker_id=worker_id)
        assert res is not None
        assert res[0] == QUEUE_DEFAULT
        consumed.append(res[2]["payload"]["job_index"])
        await ack_job(redis_client, worker_id, res[1])

    assert consumed == [1, 2, 3, 4, 5]


# ==============================================================================
# 7. TC-FIFO-MULTI: Multi-Job In-Flight Recovery Order Preservation
# ==============================================================================


@pytest.mark.asyncio
async def test_multi_job_in_flight_recovery_order_preservation(
    redis_client: InMemoryAsyncRedis,
):
    """
    SCRUM-407 Scenario (TC-FIFO-MULTI):
    Proves that if an in-flight list contains multiple stranded jobs [J1, J2, J3],
    the atomic LMOVE RIGHT LEFT loop restores them to the queue head in the exact original
    relative order [J1, J2, J3], followed by any waiting jobs.
    """
    worker_id = "manta-worker-multi-crash"
    in_flight_key = get_in_flight_key(worker_id)
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # Simulate 3 jobs already staged in in-flight list in order [J1, J2, J3]
    for i in (1, 2, 3):
        envelope = {
            "attempt": 0,
            "enqueued_at": "2026-11-11T12:00:00.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "job_type": "multi_in_flight",
            "payload": {"job_index": i},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        raw_str = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
        await redis_client.rpush(in_flight_key, raw_str)

    # Default queue also has waiting jobs [J4, J5]
    for i in (4, 5):
        envelope = {
            "attempt": 0,
            "enqueued_at": "2026-11-11T12:00:00.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "job_type": "waiting_in_queue",
            "payload": {"job_index": i},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        await enqueue_job(redis_client, envelope, priority="default")

    assert await redis_client.llen(in_flight_key) == 3
    assert await redis_client.llen(QUEUE_DEFAULT) == 2

    # Execute recovery
    recovered = await recover_in_flight_jobs(redis_client, worker_id=worker_id)
    assert len(recovered) == 3

    # Assert that all 5 jobs in arq:queue:default are now in strict [1, 2, 3, 4, 5] order!
    items = await redis_client.lrange(QUEUE_DEFAULT, 0, -1)
    indices = [json.loads(x)["payload"]["job_index"] for x in items]
    assert indices == [1, 2, 3, 4, 5]


# ==============================================================================
# 8. TC-CONCUR-002: Concurrent Multi-Worker In-Flight Isolation
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_workers_fifo_isolation(redis_client: InMemoryAsyncRedis):
    """
    SCRUM-407 Scenario (TC-CONCUR-002):
    Proves that two workers fetching concurrently from the same default queue
    maintain complete in-flight list isolation with zero duplicate claims.
    """
    w1 = "worker-alpha-01"
    w2 = "worker-beta-02"
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # Enqueue 20 jobs
    for i in range(1, 21):
        envelope = {
            "attempt": 0,
            "enqueued_at": "2026-11-11T12:00:00.000000Z",
            "event_id": str(uuid.uuid4()),
            "job_id": str(uuid.uuid4()),
            "job_type": "concurrent_test",
            "payload": {"job_index": i},
            "priority": "default",
            "provider": "github",
            "tenant_id": str(tenant_id),
            "version": 1,
        }
        await enqueue_job(redis_client, envelope, priority="default")

    w1_processed: list[int] = []
    w2_processed: list[int] = []

    # Workers alternately fetch and process
    while True:
        r1 = await fetch_next_job(redis_client, worker_id=w1, poll_timeout=0.01)
        if r1 is not None:
            w1_processed.append(r1[2]["payload"]["job_index"])
            await ack_job(redis_client, w1, r1[1])

        r2 = await fetch_next_job(redis_client, worker_id=w2, poll_timeout=0.01)
        if r2 is not None:
            w2_processed.append(r2[2]["payload"]["job_index"])
            await ack_job(redis_client, w2, r2[1])

        if r1 is None and r2 is None:
            break

    # Assert total processed jobs = 20
    assert len(w1_processed) + len(w2_processed) == 20

    # Assert no job was processed by both workers (disjoint sets)
    set_w1 = set(w1_processed)
    set_w2 = set(w2_processed)
    assert set_w1.isdisjoint(set_w2)

    # Assert union contains every index 1..20
    assert (set_w1 | set_w2) == set(range(1, 21))

    # Assert each individual worker's execution trace was monotonic FIFO
    assert w1_processed == sorted(w1_processed)
    assert w2_processed == sorted(w2_processed)


# ==============================================================================
# 9. In-Flight Key Discovery & ID Normalization Helpers
# ==============================================================================


@pytest.mark.asyncio
async def test_in_flight_key_discovery_and_extraction(redis_client: InMemoryAsyncRedis):
    """Verifies discover_in_flight_keys and extract_worker_id_from_in_flight_key."""
    w1_key = get_in_flight_key("worker-1")
    w2_key = get_in_flight_key("worker-2")

    await redis_client.rpush(w1_key, "job_a")
    await redis_client.rpush(w2_key, "job_b")

    discovered = await discover_in_flight_keys(redis_client)
    assert w1_key in discovered
    assert w2_key in discovered

    assert extract_worker_id_from_in_flight_key(w1_key) == "worker-1"
    assert extract_worker_id_from_in_flight_key(w2_key) == "worker-2"
