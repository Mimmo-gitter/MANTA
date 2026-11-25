"""
TASK-307-1 / SCRUM-543 (STORY-307 — US-71):
Scheduled 15-Minute ARQ Blocker Detector Worker.

Sweeps all active tenants across the platform under strict PostgreSQL FORCE ROW LEVEL
SECURITY:
1. Discovers active tenant UUIDs via the `SECURITY DEFINER` RPC
   `list_active_tenants(p_limit, p_after_id)` (keyset paginated, pinned `search_path`,
   returns ONLY `tenant_id` UUIDs for tenants with `LOWER(status) = 'active'`).
2. For each active tenant, opens an isolated `get_tenant_db_context(tenant_id)` session
   setting transaction-local `app.current_tenant_id`.
3. Runs both Stale PR detection (`idle > Project.pr_stale_threshold_hours`) and
   Incident Spike detection (`>= 3` incidents in rolling 60m) with atomic
   `Risk` upsert + `OutboxEvent(event_type="risk.detected")` dual-commit and
   storage-level duplicate suppression (`uq_risks_active_condition`).
4. Isolates per-tenant failures so one tenant error never aborts remaining tenants.

Execution modes:
- Native ARQ Cron (`WorkerSettings.cron_jobs`: `minute={0, 15, 30, 45}`, `unique=True`).
- Background task inside `workers.worker.run_worker_daemon()` (optional Redis NX lock).
- Standalone CLI daemon (`python -m workers.blocker_detector`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import time
from typing import Any, Optional
import uuid

from sqlalchemy import text

from app.blockers.service import (
    INCIDENT_SPIKE_THRESHOLD,
    INCIDENT_SPIKE_WINDOW_MINUTES,
    TenantBlockerSweepResult,
    detect_tenant_blockers_in_session,
)
from app.config import settings
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context

logger = logging.getLogger("workers.blocker_detector")

BLOCKER_CRON_MINUTES: frozenset[int] = frozenset({0, 15, 30, 45})
BLOCKER_CRON_NAME = "blocker_detector_15m"
BLOCKER_CRON_LOCK_KEY = "arq:cron:blocker_detector:lock"
DEFAULT_SWEEP_INTERVAL_SECONDS = float(
    os.getenv(
        "BLOCKER_DETECTOR_INTERVAL_SECONDS",
        str(settings.BLOCKER_DETECTOR_INTERVAL_SECONDS),
    )
)
DEFAULT_TENANT_BATCH_SIZE = int(
    os.getenv(
        "BLOCKER_TENANT_DISCOVERY_BATCH_SIZE",
        str(settings.BLOCKER_TENANT_DISCOVERY_BATCH_SIZE),
    )
)
DEFAULT_HEARTBEAT_FILE = Path(
    os.getenv(
        "BLOCKER_DETECTOR_HEARTBEAT_FILE",
        settings.BLOCKER_DETECTOR_HEARTBEAT_FILE,
    )
)

_DISCOVER_ACTIVE_TENANTS_SQL = text(
    "SELECT tenant_id FROM list_active_tenants(CAST(:limit AS integer), CAST(:after_id AS uuid))"
)


@dataclass
class BlockerSweepResult:
    """Aggregate summary for one cross-tenant 15-minute blocker detector sweep."""

    tenants_discovered: int = 0
    tenants_swept: int = 0
    tenant_errors: int = 0
    projects_evaluated: int = 0
    prs_evaluated: int = 0
    stale_prs_flagged: int = 0
    stale_pr_risks_created: int = 0
    incident_spikes_detected: int = 0
    incident_spike_risks_created: int = 0
    outbox_events_staged: int = 0
    duplicates_suppressed: int = 0
    prs_reset: int = 0
    risks_resolved: int = 0
    cooldown_held: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_risks_created(self) -> int:
        return self.stale_pr_risks_created + self.incident_spike_risks_created

    def record_tenant_result(self, res: TenantBlockerSweepResult) -> None:
        self.tenants_swept += 1
        self.projects_evaluated += max(
            res.stale_pr.projects_evaluated,
            res.incident_spike.projects_evaluated,
        )
        self.prs_evaluated += res.stale_pr.prs_evaluated
        self.stale_prs_flagged += res.stale_pr.prs_flagged_stale
        self.stale_pr_risks_created += res.stale_pr.risks_created
        self.incident_spikes_detected += res.incident_spike.spikes_detected
        self.incident_spike_risks_created += res.incident_spike.risks_created
        self.outbox_events_staged += res.total_outbox_events_staged
        self.duplicates_suppressed += res.total_duplicates_suppressed
        self.prs_reset += res.stale_pr.prs_reset
        self.risks_resolved += res.total_risks_resolved
        self.cooldown_held += res.incident_spike.cooldown_held

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenants_discovered": self.tenants_discovered,
            "tenants_swept": self.tenants_swept,
            "tenant_errors": self.tenant_errors,
            "projects_evaluated": self.projects_evaluated,
            "prs_evaluated": self.prs_evaluated,
            "stale_prs_flagged": self.stale_prs_flagged,
            "stale_pr_risks_created": self.stale_pr_risks_created,
            "incident_spikes_detected": self.incident_spikes_detected,
            "incident_spike_risks_created": self.incident_spike_risks_created,
            "total_risks_created": self.total_risks_created,
            "outbox_events_staged": self.outbox_events_staged,
            "duplicates_suppressed": self.duplicates_suppressed,
            "prs_reset": self.prs_reset,
            "risks_resolved": self.risks_resolved,
            "cooldown_held": self.cooldown_held,
        }


async def discover_active_tenants(
    *,
    batch_size: int = DEFAULT_TENANT_BATCH_SIZE,
    max_pages: int = 1000,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
) -> list[uuid.UUID]:
    """
    Executes the `SECURITY DEFINER` RPC `list_active_tenants(p_limit, p_after_id)`
    with keyset pagination to enumerate every active tenant UUID without exposing
    any cross-tenant domain data outside PostgreSQL RLS.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer.")

    discovered: list[uuid.UUID] = []
    after_id: Optional[uuid.UUID] = None

    for _ in range(max_pages):
        async with session_factory() as session:
            async with session.begin():
                res = await session.execute(
                    _DISCOVER_ACTIVE_TENANTS_SQL,
                    {"limit": batch_size, "after_id": after_id},
                )
                rows = res.fetchall()

        if not rows:
            break

        page_ids = [uuid.UUID(str(row[0])) for row in rows]
        discovered.extend(page_ids)

        if len(page_ids) < batch_size:
            break
        after_id = page_ids[-1]

    return discovered


async def sweep_tenant_blockers(
    tenant_id: uuid.UUID,
    *,
    now: Optional[datetime] = None,
    window_minutes: int = INCIDENT_SPIKE_WINDOW_MINUTES,
    threshold_count: int = INCIDENT_SPIKE_THRESHOLD,
    tenant_session_factory: Callable[[uuid.UUID], Any] = get_tenant_db_context,
) -> TenantBlockerSweepResult:
    """
    Executes stale PR and incident spike detection for a single tenant inside
    an isolated `get_tenant_db_context(tenant_id)` PostgreSQL RLS transaction.
    """
    ref_now = now or datetime.now(timezone.utc)
    async with tenant_session_factory(tenant_id) as session:
        return await detect_tenant_blockers_in_session(
            session,
            tenant_id=tenant_id,
            now=ref_now,
            window_minutes=window_minutes,
            threshold_count=threshold_count,
        )


async def sweep_all_active_tenants(
    *,
    now: Optional[datetime] = None,
    tenant_ids: Optional[Sequence[uuid.UUID]] = None,
    batch_size: Optional[int] = None,
    window_minutes: Optional[int] = None,
    threshold_count: Optional[int] = None,
    tenant_session_factory: Callable[[uuid.UUID], Any] = get_tenant_db_context,
    discovery_session_factory: Callable[[], Any] = AsyncSessionLocal,
) -> BlockerSweepResult:
    """
    Performs one full cross-tenant blocker detection sweep:
      1. Discovers active tenant UUIDs via `SECURITY DEFINER` `list_active_tenants`
         (or uses explicit `tenant_ids`).
      2. Sweeps each tenant in an isolated `get_tenant_db_context(tenant_id)` transaction.
      3. Isolates per-tenant exceptions so one tenant error never halts remaining tenants.
    """
    ref_now = now or datetime.now(timezone.utc)
    effective_batch_size = batch_size or settings.BLOCKER_TENANT_DISCOVERY_BATCH_SIZE
    effective_window = (
        window_minutes if window_minutes is not None else settings.BLOCKER_INCIDENT_WINDOW_MINUTES
    )
    effective_threshold = (
        threshold_count
        if threshold_count is not None
        else settings.BLOCKER_INCIDENT_SPIKE_THRESHOLD
    )

    result = BlockerSweepResult()

    if tenant_ids is not None:
        target_tenants = list(tenant_ids)
    else:
        try:
            target_tenants = await discover_active_tenants(
                batch_size=effective_batch_size,
                session_factory=discovery_session_factory,
            )
        except Exception as exc:
            logger.error("Failed to discover active tenants for blocker sweep: %s", exc)
            result.tenant_errors += 1
            result.errors.append(f"discovery:{exc}")
            return result

    result.tenants_discovered = len(target_tenants)

    for tid in target_tenants:
        try:
            tenant_res = await sweep_tenant_blockers(
                tid,
                now=ref_now,
                window_minutes=effective_window,
                threshold_count=effective_threshold,
                tenant_session_factory=tenant_session_factory,
            )
            result.record_tenant_result(tenant_res)
        except Exception as exc:
            result.tenant_errors += 1
            result.errors.append(f"{tid}:{exc}")
            logger.exception("Blocker detector sweep failed for tenant %s: %s", tid, exc)

    if result.total_risks_created > 0 or result.risks_resolved > 0 or result.tenant_errors > 0:
        logger.info(
            "Blocker sweep completed: tenants=%d/%d errors=%d stale_pr_risks=%d "
            "incident_spike_risks=%d outbox=%d suppressed=%d resolved=%d",
            result.tenants_swept,
            result.tenants_discovered,
            result.tenant_errors,
            result.stale_pr_risks_created,
            result.incident_spike_risks_created,
            result.outbox_events_staged,
            result.duplicates_suppressed,
            result.risks_resolved,
        )
    return result


async def run_blocker_detector_cron(
    ctx: Optional[dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    ARQ cron / task entrypoint executed every 15 minutes (`minute={0, 15, 30, 45}`).
    """
    context = ctx if isinstance(ctx, dict) else {}
    effective_now = now or context.get("now")
    tenant_ids = context.get("tenant_ids")
    tenant_session_factory = context.get("tenant_session_factory") or get_tenant_db_context
    discovery_session_factory = context.get("discovery_session_factory") or AsyncSessionLocal

    sweep_res = await sweep_all_active_tenants(
        now=effective_now,
        tenant_ids=tenant_ids,
        tenant_session_factory=tenant_session_factory,
        discovery_session_factory=discovery_session_factory,
    )
    return sweep_res.to_dict()


def write_heartbeat(path: Optional[Path] = None) -> None:
    """Write the current UNIX timestamp to the blocker detector liveness heartbeat file."""
    target_path = path or DEFAULT_HEARTBEAT_FILE
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to write blocker detector heartbeat to %s: %s", target_path, exc)


async def _try_acquire_redis_sweep_lock(
    redis: Any,
    worker_id: str,
    ttl_seconds: int,
) -> bool:
    """Best-effort distributed lock preventing redundant sweeps across worker replicas."""
    if redis is None:
        return True
    try:
        acquired = await redis.set(
            BLOCKER_CRON_LOCK_KEY,
            worker_id,
            nx=True,
            ex=max(1, ttl_seconds),
        )
        return bool(acquired)
    except Exception as exc:
        # Fail open: PostgreSQL uq_risks_active_condition guarantees zero duplicate writes.
        logger.warning("Blocker detector Redis lock check failed (continuing): %s", exc)
        return True


async def run_blocker_detector_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: Optional[float] = None,
    redis: Optional[Any] = None,
    worker_id: str = "blocker-detector",
    heartbeat_path: Optional[Path] = None,
    max_iterations: Optional[int] = None,
    run_immediately: bool = False,
    tenant_session_factory: Callable[[uuid.UUID], Any] = get_tenant_db_context,
    discovery_session_factory: Callable[[], Any] = AsyncSessionLocal,
) -> int:
    """
    Background 15-minute scheduler loop for `run_worker_daemon()` and standalone CLI mode.
    """
    effective_interval = (
        float(interval_seconds)
        if interval_seconds is not None
        else float(settings.BLOCKER_DETECTOR_INTERVAL_SECONDS)
    )
    if effective_interval < 0:
        raise ValueError(f"interval_seconds must be non-negative (got {effective_interval})")

    lock_ttl = max(30, int(effective_interval * 0.8)) if effective_interval > 0 else 30
    iterations = 0

    if not run_immediately and not stop_event.is_set():
        write_heartbeat(heartbeat_path)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=effective_interval)
            return 0
        except TimeoutError:
            pass

    while not stop_event.is_set():
        if max_iterations is not None and iterations >= max_iterations:
            break

        started = time.monotonic()
        try:
            if await _try_acquire_redis_sweep_lock(redis, worker_id, lock_ttl):
                await sweep_all_active_tenants(
                    tenant_session_factory=tenant_session_factory,
                    discovery_session_factory=discovery_session_factory,
                )
        except Exception:
            logger.exception("Unhandled exception during scheduled blocker detector tick")

        write_heartbeat(heartbeat_path)
        iterations += 1

        if stop_event.is_set():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break

        elapsed = time.monotonic() - started
        sleep_time = max(0.0, effective_interval - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_time)
        except TimeoutError:
            pass

    return iterations


async def main() -> None:
    """Standalone CLI entrypoint for `python -m workers.blocker_detector`."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, ValueError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    try:
        await run_blocker_detector_loop(stop_event, run_immediately=True)
    finally:
        await async_engine.dispose()
        logger.info("Blocker detector stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())


__all__ = [
    "BLOCKER_CRON_LOCK_KEY",
    "BLOCKER_CRON_MINUTES",
    "BLOCKER_CRON_NAME",
    "DEFAULT_HEARTBEAT_FILE",
    "DEFAULT_SWEEP_INTERVAL_SECONDS",
    "DEFAULT_TENANT_BATCH_SIZE",
    "BlockerSweepResult",
    "discover_active_tenants",
    "run_blocker_detector_cron",
    "run_blocker_detector_loop",
    "sweep_all_active_tenants",
    "sweep_tenant_blockers",
    "write_heartbeat",
]
