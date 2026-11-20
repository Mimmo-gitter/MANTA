"""
SCRUM-388 / Task 19.3:
5-Second Background Staged Webhook Recovery Sweeper.

Continuously scans PostgreSQL for stranded 'staged' webhook events older than
the grace threshold (default 10s), claims them in disjoint batches using
PostgreSQL `FOR UPDATE SKIP LOCKED`, pushes them to Valkey/Redis `arq:queue:default`
via RPUSH, and marks them 'queued'.

Zero Data Loss & Crash Recovery:
- The claim, Redis RPUSH, and 'queued' status transition are wrapped in a single
  database transaction per tenant.
- If Redis is unreachable or raises an error, the database transaction rolls back,
  reverting all claimed rows to 'staged' so the next sweep cycle retries cleanly.
- If the sweeper crashes or is killed (SIGKILL), the database automatically rolls
  back uncommitted claims to 'staged'.
- Concurrency: Multiple sweeper instances claim disjoint batches with zero lock contention.
- Liveness Heartbeat: Periodically touches `SWEEPER_HEARTBEAT_FILE` (default /tmp/sweeper.heartbeat)
  on each loop iteration for container orchestration health checks.
- Graceful Shutdown: Handles SIGTERM/SIGINT by completing the active sweep iteration
  and closing connections cleanly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import signal
import time
from typing import Any, Optional
import uuid

from redis.exceptions import RedisError
from sqlalchemy import text

from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context
from app.webhooks.enqueue import build_webhook_envelope, get_redis
from app.webhooks.sweeper import claim_stranded_staged_events, mark_events_queued
from workers.worker import QUEUE_DEFAULT

logger = logging.getLogger("workers.recovery_sweeper")

REDIS_ERRORS = (RedisError, OSError, asyncio.TimeoutError, ConnectionError)

SWEEP_INTERVAL_SECONDS = float(os.getenv("SWEEP_INTERVAL_SECONDS", "5.0"))
STRANDED_THRESHOLD_SECONDS = float(os.getenv("SWEEP_THRESHOLD_SECONDS", "10.0"))
BATCH_SIZE = int(os.getenv("SWEEP_BATCH_SIZE", "100"))
MAX_DRAIN_ROUNDS = int(os.getenv("SWEEP_MAX_DRAIN_ROUNDS", "10"))
MAX_TENANTS_PER_TICK = int(os.getenv("SWEEP_MAX_TENANTS_PER_TICK", "100"))
HEARTBEAT_FILE = Path(os.getenv("SWEEPER_HEARTBEAT_FILE", "/tmp/sweeper.heartbeat"))

_DISCOVER_TENANTS_SQL = text(
    "SELECT tenant_id FROM list_tenants_with_stranded_webhooks("
    "CAST(:cutoff AS timestamptz), CAST(:limit AS integer)"
    ")"
)


@dataclass
class SweepResult:
    """Summary of a single sweep cycle."""

    tenants: int = 0
    claimed: int = 0
    queued: int = 0
    redis_failures: int = 0


async def discover_tenants_with_stranded_events(
    cutoff: datetime,
    limit: int = MAX_TENANTS_PER_TICK,
) -> list[uuid.UUID]:
    """
    Executes the SECURITY DEFINER function list_tenants_with_stranded_webhooks
    to identify tenants holding stranded 'staged' events older than cutoff.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            res = await session.execute(
                _DISCOVER_TENANTS_SQL,
                {"cutoff": cutoff, "limit": limit},
            )
            rows = res.fetchall()
            return [uuid.UUID(str(r[0])) for r in rows]


async def sweep_tenant_batch(
    redis: Any,
    tenant_id: uuid.UUID,
    *,
    threshold_seconds: float = STRANDED_THRESHOLD_SECONDS,
    batch_size: int = BATCH_SIZE,
) -> int:
    """
    Claims up to `batch_size` stranded 'staged' events for a single tenant,
    enqueues them into Valkey/Redis via RPUSH, and marks them 'queued'.

    Guarantees:
    - Entire operation runs in a single database transaction.
    - If redis.rpush fails (REDIS_ERRORS), the transaction rolls back,
      automatically reverting all claimed rows back to 'staged' status.
    - Returns the number of events successfully enqueued and marked 'queued'.
    """
    async with get_tenant_db_context(tenant_id) as db:
        claimed = await claim_stranded_staged_events(
            db,
            threshold_seconds=threshold_seconds,
            batch_size=batch_size,
            tenant_id=tenant_id,
        )
        if not claimed:
            return 0

        envelopes = [
            build_webhook_envelope(
                event_id=c.id,
                tenant_id=c.tenant_id,
                provider=c.provider,
                event_type=c.event_type,
            )
            for c in claimed
        ]

        # Atomic RPUSH to Valkey/Redis (variadic preserves FIFO relative ordering)
        await redis.rpush(QUEUE_DEFAULT, *envelopes)

        # Transition status from 'recovering' to 'queued' within the same transaction
        queued_count = await mark_events_queued(
            db,
            [c.id for c in claimed],
            tenant_id=tenant_id,
        )
        return queued_count


async def sweep_once(
    redis: Any,
    *,
    threshold_seconds: float = STRANDED_THRESHOLD_SECONDS,
    batch_size: int = BATCH_SIZE,
    max_drain_rounds: int = MAX_DRAIN_ROUNDS,
    tenant_id: Optional[uuid.UUID] = None,
) -> SweepResult:
    """
    Performs one full sweep pass:
    1. Discovers all tenants with stranded events (or uses explicit tenant_id).
    2. Drains batches per tenant up to max_drain_rounds.
    3. Handles Redis outage by rolling back and aborting the current cycle.
    """
    result = SweepResult()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=threshold_seconds)

    if tenant_id is not None:
        tenant_ids = [tenant_id]
    else:
        try:
            tenant_ids = await discover_tenants_with_stranded_events(cutoff)
        except Exception as exc:
            logger.error(f"Failed to discover tenants with stranded events: {exc}")
            return result

    for tid in tenant_ids:
        result.tenants += 1
        for _ in range(max_drain_rounds):
            try:
                count = await sweep_tenant_batch(
                    redis,
                    tid,
                    threshold_seconds=threshold_seconds,
                    batch_size=batch_size,
                )
            except REDIS_ERRORS as exc:
                result.redis_failures += 1
                logger.warning(
                    f"Redis unavailable during sweep for tenant {tid}. "
                    f"Transaction rolled back; events revert to 'staged': {exc}"
                )
                # Redis outage affects all tenants; abort rest of tick
                return result
            except Exception as exc:
                logger.error(f"Unexpected error during sweep batch for tenant {tid}: {exc}")
                break

            result.claimed += count
            result.queued += count
            if count < batch_size:
                # Drained all currently eligible stranded events for this tenant
                break

    return result


def write_heartbeat(path: Optional[Path] = None) -> None:
    """Writes current epoch timestamp to the liveness heartbeat file."""
    target_path = path or HEARTBEAT_FILE
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(str(time.time()), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to write sweeper heartbeat to {target_path}: {exc}")


async def run_sweeper_loop(
    redis: Any,
    stop_event: asyncio.Event,
    *,
    interval: float = SWEEP_INTERVAL_SECONDS,
    threshold_seconds: float = STRANDED_THRESHOLD_SECONDS,
    batch_size: int = BATCH_SIZE,
    max_drain_rounds: int = MAX_DRAIN_ROUNDS,
    heartbeat_path: Optional[Path] = None,
    max_iterations: Optional[int] = None,
) -> int:
    """
    Main background loop executing sweep_once every `interval` seconds (default 5s).
    Runs until stop_event is set or max_iterations is reached.
    """
    logger.info(
        f"Starting background recovery sweeper loop: interval={interval}s, "
        f"threshold={threshold_seconds}s, batch_size={batch_size}"
    )
    iterations = 0

    while not stop_event.is_set():
        if max_iterations is not None and iterations >= max_iterations:
            break

        started = time.monotonic()
        try:
            res = await sweep_once(
                redis,
                threshold_seconds=threshold_seconds,
                batch_size=batch_size,
                max_drain_rounds=max_drain_rounds,
            )
            if res.queued > 0:
                logger.info(
                    f"Sweeper queued {res.queued} stranded event(s) across {res.tenants} tenant(s)."
                )
        except Exception:
            logger.exception("Unhandled exception during recovery sweeper tick")

        # Update liveness heartbeat file
        write_heartbeat(heartbeat_path)
        iterations += 1

        if stop_event.is_set():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break

        elapsed = time.monotonic() - started
        sleep_time = max(0.0, interval - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_time)
        except TimeoutError:
            pass

    logger.info("Background recovery sweeper loop exited cleanly.")
    return iterations


async def main() -> None:
    """Process entrypoint for `python -m workers.recovery_sweeper`."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Fallback on Windows development environments
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    redis = get_redis()
    try:
        await run_sweeper_loop(redis, stop_event)
    finally:
        logger.info("Cleaning up database and Redis connections on sweeper shutdown...")
        await redis.aclose()
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
