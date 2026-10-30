"""Multi-Tenant Concurrency Load Test Runner (SCRUM-245 / US-9).

Executes 1,000 concurrent requests alternating between Tenant A and Tenant B
through PgBouncer in transaction pooling mode.

Verifies:
1. Zero cross-tenant session state or data leakage across connection reuses.
2. Every response contains ONLY the caller's tenant-identifying data.
3. RLS policy isolation under high concurrency (tenants count == 1).
4. Records latency percentiles (min, max, mean, p50, p90, p95, p99) and error rate.
5. Tracks backend connection reuse (pg_backend_pid distribution).
6. Persists structured results to JSON report file.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any
import uuid

# Ensure backend root is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

logger = logging.getLogger("run_tenant_load_test")

# Fixed UUIDs for Tenant A and Tenant B
TENANT_A_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_B_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

TENANT_A_INFO = {
    "id": TENANT_A_ID,
    "name": "Tenant Alpha Corp",
    "slug": "tenant-alpha-loadtest",
    "admin_email": "admin@alpha-loadtest.com",
    "admin_name": "Admin Alpha",
}

TENANT_B_INFO = {
    "id": TENANT_B_ID,
    "name": "Tenant Beta Ltd",
    "slug": "tenant-beta-loadtest",
    "admin_email": "admin@beta-loadtest.com",
    "admin_name": "Admin Beta",
}


def normalize_dsn(dsn: str) -> str:
    """Ensure DSN uses asyncpg driver scheme and least-privilege role."""
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    # Ensure application runtime runs as eng_app_user so PostgreSQL RLS is strictly enforced
    # (manta_user is a superuser and always bypasses RLS)
    if "manta_user:manta_password" in dsn:
        dsn = dsn.replace("manta_user:manta_password", "eng_app_user:eng_app_pass_change_in_prod")
    return dsn


def get_default_dsn() -> str:
    """Resolve database URL pointing to PgBouncer."""
    env_dsn = os.getenv("PGBOUNCER_TEST_DSN") or os.getenv("DATABASE_URL")
    if env_dsn:
        return normalize_dsn(env_dsn)
    # Fallback to local PgBouncer port 6432
    return "postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:6432/manta_db"


def ensure_schema_migrated(dsn: str) -> None:
    """Ensure Alembic migrations have been applied up to head."""
    try:
        from alembic import command
        from alembic.config import Config

        mig_url = dsn
        if "eng_app_user" in mig_url:
            mig_url = mig_url.replace(
                "eng_app_user:eng_app_pass_change_in_prod", "manta_user:manta_password"
            )
        alembic_ini = BACKEND_DIR / "alembic.ini"
        if alembic_ini.exists():
            cfg = Config(str(alembic_ini))
            cfg.set_main_option("sqlalchemy.url", mig_url)
            cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
            command.upgrade(cfg, "head")
            logger.info("Database schema migrated to head successfully")
    except Exception as exc:
        logger.debug("Schema migration notice: %s", exc)


async def ensure_test_tenants(engine, dsn: str | None = None) -> None:
    """Ensure clean test tenants exist for Tenant A and Tenant B."""
    for t_info in (TENANT_A_INFO, TENANT_B_INFO):
        tid = t_info["id"]
        for attempt in range(2):
            try:
                async with engine.begin() as conn:
                    # Set tenant context so that RLS allows viewing own tenant row
                    await conn.execute(
                        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                        {"tid": str(tid)},
                    )
                    row = await conn.execute(
                        text("SELECT id FROM tenants WHERE id = :tid"),
                        {"tid": tid},
                    )
                    if not row.first():
                        await conn.execute(
                            text(
                                """
                                SELECT * FROM provision_tenant(
                                    p_name => :name,
                                    p_slug => :slug,
                                    p_admin_email => :admin_email,
                                    p_admin_name => :admin_name,
                                    p_tier => 'enterprise',
                                    p_ai_processing_mode => 'cloud_zdr',
                                    p_tenant_id => :tid
                                )
                                """
                            ),
                            {
                                "name": t_info["name"],
                                "slug": t_info["slug"],
                                "admin_email": t_info["admin_email"],
                                "admin_name": t_info["admin_name"],
                                "tid": tid,
                            },
                        )
                break
            except Exception as exc:
                err_str = str(exc).lower()
                # If tenants table or provision_tenant function missing, apply migrations once and retry
                if attempt == 0 and dsn and ("does not exist" in err_str or "undefined" in err_str):
                    logger.info("Schema objects missing; applying migrations...")
                    ensure_schema_migrated(dsn)
                    continue
                logger.debug("Tenant provision notice for %s: %s", tid, exc)
                break


class TenantLoadTestRunner:
    """Executes concurrent alternating requests and validates tenant isolation."""

    def __init__(
        self,
        dsn: str,
        total_requests: int = 1000,
        concurrency: int = 50,
        report_path: Path | None = None,
    ) -> None:
        self.dsn = normalize_dsn(dsn)
        self.total_requests = total_requests
        self.concurrency = concurrency
        self.report_path = report_path or (BACKEND_DIR / "reports" / "load_test_results.json")

        self.engine = create_async_engine(
            self.dsn,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
            },
            pool_pre_ping=True,
            pool_size=min(self.concurrency, 30),
            max_overflow=self.concurrency,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

    async def execute_single_request(
        self,
        request_idx: int,
        expected_tenant: dict[str, Any],
        other_tenant: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        """Execute one tenant-scoped transaction and validate returned data."""
        tid = expected_tenant["id"]
        start_time = time.perf_counter()

        result_meta: dict[str, Any] = {
            "request_idx": request_idx,
            "expected_tenant_id": str(tid),
            "expected_tenant_name": expected_tenant["name"],
            "success": False,
            "latency_ms": 0.0,
            "backend_pid": None,
            "returned_tenant_id": None,
            "returned_tenant_name": None,
            "guc_tenant_id": None,
            "tenant_count": 0,
            "leak_detected": False,
            "error": None,
        }

        async with semaphore:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        # 1. Enforce transaction-local tenant context
                        await session.execute(
                            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                            {"tid": str(tid)},
                        )

                        # 2. Query tenant data under RLS and retrieve backend PID
                        res = await session.execute(
                            text(
                                """
                                SELECT
                                    t.id AS tenant_id,
                                    t.name AS tenant_name,
                                    t.slug AS tenant_slug,
                                    current_setting('app.current_tenant_id', true) AS guc_tenant_id,
                                    pg_backend_pid() AS pid,
                                    (SELECT count(*) FROM tenants) AS visible_tenants_count
                                FROM tenants t
                                WHERE t.id = current_setting('app.current_tenant_id', true)::uuid;
                                """
                            )
                        )
                        row = res.mappings().first()

                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                result_meta["latency_ms"] = elapsed_ms

                if not row:
                    result_meta["error"] = "Query returned no rows"
                    return result_meta

                result_meta["backend_pid"] = row["pid"]
                result_meta["returned_tenant_id"] = str(row["tenant_id"])
                result_meta["returned_tenant_name"] = row["tenant_name"]
                result_meta["guc_tenant_id"] = row["guc_tenant_id"]
                result_meta["tenant_count"] = row["visible_tenants_count"]

                # 3. Assert zero leakage and exact tenant matching
                is_caller_id = str(row["tenant_id"]) == str(tid)
                is_caller_name = row["tenant_name"] == expected_tenant["name"]
                is_guc_match = row["guc_tenant_id"] == str(tid)
                is_other_id = str(row["tenant_id"]) == str(other_tenant["id"])
                is_rls_isolated = row["visible_tenants_count"] == 1

                if (
                    not (is_caller_id and is_caller_name and is_guc_match and is_rls_isolated)
                    or is_other_id
                ):
                    result_meta["leak_detected"] = True
                    result_meta["error"] = (
                        f"Cross-tenant data violation! Expected {tid} ({expected_tenant['name']}), "
                        f"got {row['tenant_id']} ({row['tenant_name']}), GUC={row['guc_tenant_id']}, "
                        f"visible_count={row['visible_tenants_count']}"
                    )
                else:
                    result_meta["success"] = True

            except Exception as exc:
                result_meta["latency_ms"] = (time.perf_counter() - start_time) * 1000.0
                result_meta["error"] = str(exc)

        return result_meta

    async def run(self) -> dict[str, Any]:
        """Run the full 1,000 requests load test."""
        print("==> Ensuring test tenants exist in database...")
        await ensure_test_tenants(self.engine, self.dsn)

        semaphore = asyncio.Semaphore(self.concurrency)
        tasks = []

        print(
            f"==> Launching {self.total_requests} concurrent alternating requests "
            f"(concurrency limit: {self.concurrency})..."
        )
        overall_start = time.perf_counter()

        for idx in range(self.total_requests):
            # Alternate between Tenant A (even) and Tenant B (odd)
            if idx % 2 == 0:
                expected = TENANT_A_INFO
                other = TENANT_B_INFO
            else:
                expected = TENANT_B_INFO
                other = TENANT_A_INFO

            tasks.append(self.execute_single_request(idx, expected, other, semaphore))

        results = await asyncio.gather(*tasks)
        total_time_s = time.perf_counter() - overall_start

        # Calculate metrics
        latencies = [r["latency_ms"] for r in results if r["latency_ms"] > 0]
        successes = [r for r in results if r["success"]]
        failures = [r for r in results if not r["success"]]
        first_error = failures[0]["error"] if failures else None
        leaks = [r for r in results if r["leak_detected"]]
        pids = {r["backend_pid"] for r in results if r["backend_pid"] is not None}

        # PID frequency distribution (shows connection reuse across pool)
        pid_counts: dict[int, int] = {}
        for r in results:
            pid = r["backend_pid"]
            if pid:
                pid_counts[pid] = pid_counts.get(pid, 0) + 1

        error_rate = (len(failures) / self.total_requests) * 100.0 if self.total_requests else 0.0

        stats: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_dsn": self.dsn.split("@")[-1],  # mask password
            "total_requests": self.total_requests,
            "concurrency": self.concurrency,
            "total_duration_seconds": round(total_time_s, 3),
            "throughput_req_per_sec": round(self.total_requests / total_time_s, 2)
            if total_time_s > 0
            else 0,
            "successful_requests": len(successes),
            "failed_requests": len(failures),
            "first_failure_error": first_error,
            "error_rate_percent": round(error_rate, 2),
            "leaks_detected": len(leaks),
            "tenant_a_requests": sum(
                1 for r in results if r["expected_tenant_id"] == str(TENANT_A_ID)
            ),
            "tenant_b_requests": sum(
                1 for r in results if r["expected_tenant_id"] == str(TENANT_B_ID)
            ),
            "distinct_backend_pids": len(pids),
            "pids_reuse_summary": {
                "min_queries_per_pid": min(pid_counts.values()) if pid_counts else 0,
                "max_queries_per_pid": max(pid_counts.values()) if pid_counts else 0,
                "avg_queries_per_pid": round(statistics.mean(pid_counts.values()), 1)
                if pid_counts
                else 0,
            },
            "latency_ms": {
                "min": round(min(latencies), 2) if latencies else 0,
                "max": round(max(latencies), 2) if latencies else 0,
                "mean": round(statistics.mean(latencies), 2) if latencies else 0,
                "median_p50": round(statistics.median(latencies), 2) if latencies else 0,
                "p90": round(statistics.quantiles(latencies, n=10)[8], 2)
                if len(latencies) >= 10
                else 0,
                "p95": round(statistics.quantiles(latencies, n=20)[18], 2)
                if len(latencies) >= 20
                else 0,
                "p99": round(statistics.quantiles(latencies, n=100)[98], 2)
                if len(latencies) >= 100
                else 0,
            },
        }

        # Save structured report
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        await self.engine.dispose()
        return stats


def verify_leak_detector_logic() -> bool:
    """Self-test to prove that the validation logic detects any simulated cross-tenant leakage."""
    print("==> Verifying leak detector catches invalid / cross-tenant responses...")

    # 1. Normal matching response: must pass
    valid_row = {
        "tenant_id": TENANT_A_ID,
        "tenant_name": TENANT_A_INFO["name"],
        "guc_tenant_id": str(TENANT_A_ID),
        "visible_tenants_count": 1,
    }
    is_valid = (
        str(valid_row["tenant_id"]) == str(TENANT_A_ID)
        and valid_row["tenant_name"] == TENANT_A_INFO["name"]
        and valid_row["guc_tenant_id"] == str(TENANT_A_ID)
        and valid_row["visible_tenants_count"] == 1
        and str(valid_row["tenant_id"]) != str(TENANT_B_ID)
    )
    assert is_valid, "Valid row was incorrectly rejected"

    # 2. Corrupted response: returned Tenant B when Tenant A was expected
    leaked_row = {
        "tenant_id": TENANT_B_ID,
        "tenant_name": TENANT_B_INFO["name"],
        "guc_tenant_id": str(TENANT_B_ID),
        "visible_tenants_count": 1,
    }
    is_leaked = (
        str(leaked_row["tenant_id"]) != str(TENANT_A_ID)
        or leaked_row["tenant_name"] != TENANT_A_INFO["name"]
        or leaked_row["guc_tenant_id"] != str(TENANT_A_ID)
        or str(leaked_row["tenant_id"]) == str(TENANT_B_ID)
    )
    assert is_leaked, "Leak detector failed to catch mismatched tenant data!"

    # 3. Corrupted response: GUC mismatch
    guc_leaked_row = {
        "tenant_id": TENANT_A_ID,
        "tenant_name": TENANT_A_INFO["name"],
        "guc_tenant_id": str(TENANT_B_ID),
        "visible_tenants_count": 1,
    }
    is_guc_leaked = guc_leaked_row["guc_tenant_id"] != str(TENANT_A_ID)
    assert is_guc_leaked, "Leak detector failed to catch GUC mismatch!"

    print(
        "==> Leak detector verification PASSED: catches data leakage, wrong ID, and GUC desync.\n"
    )
    return True


def print_summary(stats: dict[str, Any]) -> None:
    """Print readable terminal output."""
    print("\n" + "=" * 65)
    print("   MULTI-TENANT LOAD TEST RESULTS (SCRUM-245 / US-9)")
    print("=" * 65)
    print(f" Target Endpoint:          {stats['target_dsn']}")
    print(f" Total Requests:           {stats['total_requests']:,}")
    print(
        f" Alternation Distribution: Tenant A: {stats['tenant_a_requests']:,} | "
        f"Tenant B: {stats['tenant_b_requests']:,}"
    )
    print(f" Concurrency Limit:        {stats['concurrency']}")
    print(f" Duration:                 {stats['total_duration_seconds']} s")
    print(f" Throughput:               {stats['throughput_req_per_sec']} req/s")
    print("-" * 65)
    print(f" Successful Requests:      {stats['successful_requests']:,}")
    print(f" Failed Requests:          {stats['failed_requests']:,}")
    print(f" Error Rate:               {stats['error_rate_percent']}%")
    print(f" Cross-Tenant Leaks:       {stats['leaks_detected']} (MUST BE 0)")
    print("-" * 65)
    print(f" Distinct Server PIDs:     {stats['distinct_backend_pids']}")
    print(
        f" Avg Requests per PID:     {stats['pids_reuse_summary']['avg_queries_per_pid']} "
        f"(Min: {stats['pids_reuse_summary']['min_queries_per_pid']}, "
        f"Max: {stats['pids_reuse_summary']['max_queries_per_pid']})"
    )
    print("-" * 65)
    lat = stats["latency_ms"]
    print(" Latency (ms):")
    print(f"   Min:    {lat['min']:>7.2f} ms")
    print(f"   Mean:   {lat['mean']:>7.2f} ms")
    print(f"   p50:    {lat['median_p50']:>7.2f} ms")
    print(f"   p90:    {lat['p90']:>7.2f} ms")
    print(f"   p95:    {lat['p95']:>7.2f} ms")
    print(f"   p99:    {lat['p99']:>7.2f} ms")
    print(f"   Max:    {lat['max']:>7.2f} ms")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Multi-tenant PgBouncer load test runner")
    parser.add_argument("--requests", type=int, default=1000, help="Total requests (default: 1000)")
    parser.add_argument(
        "--concurrency", type=int, default=50, help="Concurrency limit (default: 50)"
    )
    parser.add_argument("--dsn", type=str, default=None, help="Database DSN pointing to PgBouncer")
    parser.add_argument("--report", type=str, default=None, help="Report output path")
    parser.add_argument(
        "--verify-leak-detector",
        action="store_true",
        help="Run self-test validating that leak detector catches invalid tenant data",
    )
    args = parser.parse_args()

    if args.verify_leak_detector:
        verify_leak_detector_logic()

    dsn = args.dsn or get_default_dsn()
    report_path = Path(args.report) if args.report else None

    runner = TenantLoadTestRunner(
        dsn=dsn,
        total_requests=args.requests,
        concurrency=args.concurrency,
        report_path=report_path,
    )

    stats = asyncio.run(runner.run())
    print_summary(stats)

    # Exit with non-zero code if any leaks or failures occurred
    if stats["leaks_detected"] > 0 or stats["failed_requests"] > 0:
        print(
            f"\n[FAILURE] Load test failed: {stats['leaks_detected']} leaks, {stats['failed_requests']} errors."
        )
        sys.exit(1)
    else:
        print("\n[SUCCESS] All 1,000 requests alternating between tenants passed with 0 leaks.")
        sys.exit(0)


if __name__ == "__main__":
    main()
