"""Automated Pytest Suite: Multi-Tenant Concurrency Load Test (SCRUM-245 / US-9).

Executes 1,000 concurrent requests alternating between Tenant A and Tenant B
through PgBouncer in transaction pooling mode.

Verifies:
1. One-command reproducibility via pytest.
2. 1,000 requests strictly alternating between Tenant A and Tenant B.
3. Every response contains only the caller's tenant data (0 cross-tenant leaks).
4. Response latency percentiles and error rates are captured and saved.
5. Verification that the leak detection engine catches corrupted/leaked tenant data.
"""

import os
import pytest

from scripts.run_tenant_load_test import (
    TenantLoadTestRunner,
    verify_leak_detector_logic,
)

PGBOUNCER_TEST_DSN = os.getenv("PGBOUNCER_TEST_DSN")


def test_leak_detector_catches_anomalies():
    """AC2: Prove that the test detection logic catches any cross-tenant data leak."""
    assert verify_leak_detector_logic() is True


@pytest.mark.skipif(
    not PGBOUNCER_TEST_DSN,
    reason="PGBOUNCER_TEST_DSN not set; live PgBouncer load tests skipped",
)
@pytest.mark.asyncio
async def test_1000_concurrent_requests_alternating_tenants(tmp_path):
    """SCRUM-245 Load Test:

    - 1,000 concurrent requests alternating between Tenant A and Tenant B.
    - Zero data leakage across connection reuses.
    - Captures latency percentiles, error rate, and connection reuse metrics.
    - Saves results to report file.
    """
    report_file = tmp_path / "test_load_results.json"

    runner = TenantLoadTestRunner(
        dsn=PGBOUNCER_TEST_DSN,
        total_requests=1000,
        concurrency=50,
        report_path=report_file,
    )

    stats = await runner.run()

    # 1. Assert all 1,000 requests executed
    assert stats["total_requests"] == 1000
    assert stats["tenant_a_requests"] == 500
    assert stats["tenant_b_requests"] == 500

    # 2. Assert zero cross-tenant leaks (AC2)
    assert stats["leaks_detected"] == 0, f"Cross-tenant leaks detected: {stats['leaks_detected']}"

    # 3. Assert zero failed requests / 0% error rate
    assert stats["failed_requests"] == 0, f"Requests failed: {stats['failed_requests']}"
    assert stats["error_rate_percent"] == 0.0

    # 4. Assert PgBouncer connection reuse took place across the physical pool
    # With default_pool_size=20 and max_db_connections=30, requests reuse backend connections
    assert stats["distinct_backend_pids"] > 0
    if "6432" in PGBOUNCER_TEST_DSN or "pgbouncer" in PGBOUNCER_TEST_DSN:
        assert stats["distinct_backend_pids"] <= 35, (
            f"Expected PgBouncer pool ceiling <= 35, got {stats['distinct_backend_pids']}"
        )
        assert stats["pids_reuse_summary"]["avg_queries_per_pid"] >= 20.0
    else:
        assert stats["pids_reuse_summary"]["avg_queries_per_pid"] >= 5.0

    # 5. Assert report was generated and saved (AC3)
    assert report_file.exists()
    assert report_file.stat().st_size > 0

    # 6. Latency metrics recorded
    lat = stats["latency_ms"]
    assert lat["min"] > 0
    assert lat["median_p50"] > 0
    assert lat["p95"] > 0
