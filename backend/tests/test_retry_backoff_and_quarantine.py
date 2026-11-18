"""
Task 23.3: 5-attempt exponential backoff (2^attempt s) retry and dead-letter quarantine router.

Verifies:
- compute_backoff yields 2, 4, 8, 16, 32 seconds.
- Poison webhooks are parked in 'arq:queue:delayed' with 2s, 4s, 8s, 16s backoff, then
  quarantined into dead_letter_events on the 5th failure (total_attempts=5,
  is_resolved=False) and webhook_events.status='dead_lettered'.
- Subsequent valid jobs continue processing without blockage while poison jobs back off.
- Migration/model declare idx_dead_letter_events_unresolved (tenant_id, is_resolved,
  quarantined_at DESC).
"""

import asyncio
from collections import defaultdict
from pathlib import Path
import uuid

import pytest

from workers import retry as retry_mod
from workers.retry import QUEUE_DELAYED, compute_backoff, handle_job_failure
from workers.worker import (
    QUEUE_DEFAULT,
    enqueue_job,
    get_in_flight_key,
    run_worker_fetch_loop,
    serialize_job_payload,
)


class FakeRedis:
    """Minimal async Redis with lists + sorted sets (no Lua -> exercises ZREM-claim path)."""

    supports_lua = False

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.zsets: dict[str, dict[str, float]] = defaultdict(dict)

    async def rpush(self, key, *values):
        self.lists[key].extend(values)
        return len(self.lists[key])

    async def _move(self, s, d, side_s, side_d):
        if not self.lists[s]:
            return None
        item = self.lists[s].pop(0 if side_s == "LEFT" else -1)
        if side_d == "LEFT":
            self.lists[d].insert(0, item)
        else:
            self.lists[d].append(item)
        return item

    async def lrem(self, key, count, value):
        if value in self.lists[key]:
            self.lists[key].remove(value)
            return 1
        return 0

    async def zadd(self, key, mapping):
        self.zsets[key].update(mapping)
        return len(mapping)

    async def zrangebyscore(self, key, mn, mx, start=0, num=None):
        items = sorted(
            (m for m, s in self.zsets[key].items() if s <= float(mx)),
            key=lambda m: self.zsets[key][m],
        )
        return items[start : start + num if num is not None else None]

    async def zrem(self, key, member):
        return 1 if self.zsets[key].pop(member, None) is not None else 0


async def _lmove(self, first, second, src="LEFT", dest="RIGHT"):
    return await self._move(first, second, src, dest)


async def _blmove(self, first, second, timeout=0, src="LEFT", dest="RIGHT"):
    return await self._move(first, second, src, dest)


FakeRedis.lmove = _lmove
FakeRedis.blmove = _blmove


class FakeDB:
    """Records SQL executed by the retry router."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, params=None):
        self.statements.append((str(stmt), dict(params or {})))

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def matching(self, fragment: str) -> list[dict]:
        return [p for s, p in self.statements if fragment in s]


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(retry_mod.time, "time", c)
    return c


def _job(tenant_id, event_id, kind: str) -> str:
    return serialize_job_payload(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        priority="default",
        job_type="webhook.process",
        payload={"kind": kind},
        event_id=event_id,
        provider="github",
    )


def test_compute_backoff_schedule():
    assert [compute_backoff(a) for a in range(1, 6)] == [2, 4, 8, 16, 32]
    assert retry_mod.BACKOFF_SCHEDULE == (2, 4, 8, 16, 32)
    with pytest.raises(ValueError):
        compute_backoff(0)


@pytest.mark.asyncio
async def test_handle_failure_schedules_retry_then_quarantines(clock: Clock):
    redis, db = FakeRedis(), FakeDB()
    tenant_id, event_id = uuid.uuid4(), uuid.uuid4()
    envelope = {
        "job_id": "j1",
        "tenant_id": str(tenant_id),
        "event_id": str(event_id),
        "provider": "github",
        "priority": "default",
        "payload": {"x": 1},
        "attempt": 0,
    }

    for expected_attempt, expected_delay in zip(range(1, 5), (2, 4, 8, 16)):
        outcome = await handle_job_failure(redis, db, envelope, RuntimeError("boom"))
        assert outcome.action == "retry"
        assert outcome.attempt == expected_attempt
        assert outcome.delay_seconds == expected_delay
        assert redis.zsets[QUEUE_DELAYED][outcome.retry_job] == clock.now + expected_delay
        redis.zsets[QUEUE_DELAYED].clear()
        envelope = {**envelope, "attempt": expected_attempt}

    attempt_updates = db.matching("status = 'queued'")
    assert [p["attempt"] for p in attempt_updates] == [1, 2, 3, 4]

    outcome = await handle_job_failure(redis, db, envelope, ValueError("poison"))
    assert outcome.action == "dead_lettered"
    assert outcome.attempt == 5
    inserts = db.matching("INSERT INTO dead_letter_events")
    assert len(inserts) == 1
    row = inserts[0]
    assert row["total_attempts"] == 5
    assert row["error_class"] == "builtins.ValueError"
    assert "ValueError: poison" in row["stack_trace"]
    assert row["original_event_id"] == event_id
    assert row["tenant_id"] == tenant_id
    assert "false" in [s for s, _ in db.statements if "INSERT INTO dead_letter_events" in s][0]
    assert db.matching("status = 'dead_lettered'")[0]["event_id"] == event_id
    assert not redis.zsets[QUEUE_DELAYED]


@pytest.mark.asyncio
async def test_poison_webhook_backoff_and_quarantine_without_blocking(clock: Clock):
    redis, db = FakeRedis(), FakeDB()
    tenant_id = uuid.uuid4()
    poison_event = uuid.uuid4()
    worker_id = "w-23-3"

    await enqueue_job(redis, _job(tenant_id, poison_event, "poison"))
    for i in range(3):
        await enqueue_job(redis, _job(tenant_id, uuid.uuid4(), f"valid-{i}"))

    processed_valid: list[str] = []
    poison_runs: list[float] = []

    async def handler(parsed):
        kind = parsed["payload"]["kind"]
        if kind == "poison":
            poison_runs.append(clock.now)
            raise RuntimeError("poison webhook")
        processed_valid.append(kind)

    async def drain():
        await run_worker_fetch_loop(
            redis,
            worker_id,
            handler,
            poll_timeout=0,
            max_iterations=10,
            db=db,
            retry_on_failure=True,
        )

    # Attempt 1 fails; all valid jobs still processed in the same pass (no blockage).
    await drain()
    assert processed_valid == ["valid-0", "valid-1", "valid-2"]
    assert len(poison_runs) == 1

    start = clock.now
    for delay in (2, 4, 8, 16):
        runs_before = len(poison_runs)
        clock.now += delay - 0.5
        await drain()
        assert len(poison_runs) == runs_before  # backoff not yet elapsed
        clock.now += 0.5
        # Interleave a fresh valid job: it must not wait behind the poison job.
        await enqueue_job(redis, _job(tenant_id, uuid.uuid4(), f"late-{delay}"))
        await drain()
        assert len(poison_runs) == runs_before + 1
        assert processed_valid[-1] == f"late-{delay}"

    gaps = [b - a for a, b in zip(poison_runs, poison_runs[1:])]
    assert gaps == [2, 4, 8, 16]
    assert poison_runs[0] == start

    inserts = db.matching("INSERT INTO dead_letter_events")
    assert len(inserts) == 1
    assert inserts[0]["total_attempts"] == 5
    assert inserts[0]["original_event_id"] == poison_event
    assert db.matching("status = 'dead_lettered'")[0]["event_id"] == poison_event

    # Nothing left anywhere: no delayed retry, no in-flight, no queued job.
    assert not redis.zsets[QUEUE_DELAYED]
    assert not redis.lists[get_in_flight_key(worker_id)]
    assert not redis.lists[QUEUE_DEFAULT]


@pytest.mark.asyncio
async def test_failure_propagates_when_retry_disabled():
    redis = FakeRedis()
    await enqueue_job(redis, _job(uuid.uuid4(), uuid.uuid4(), "poison"))

    async def handler(parsed):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await run_worker_fetch_loop(redis, "w", handler, poll_timeout=0, max_iterations=1)


def test_dead_letter_unresolved_index_declared():
    from app.db.models import DeadLetterEvent

    idx = {i.name: i for i in DeadLetterEvent.__table__.indexes}[
        "idx_dead_letter_events_unresolved"
    ]
    rendered = [str(e) for e in idx.expressions]
    assert rendered[:2] == ["dead_letter_events.tenant_id", "dead_letter_events.is_resolved"] or (
        rendered[:2] == ["tenant_id", "is_resolved"]
    )
    assert rendered[2] == "quarantined_at DESC"

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "b3e9f2a6d4c1_dead_letter_events_unresolved_index.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "a9c5e1d7b3f2"' in migration
    assert "idx_dead_letter_events_unresolved" in migration
    assert "quarantined_at DESC" in migration


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(test_poison_webhook_backoff_and_quarantine_without_blocking(Clock()))
