"""Automated Pytest Suite: Webhook Staging Latency Load Test (SCRUM-251 / US-15).

Verifies:
1. Realistic multi-kilobyte webhook payload generation and HMAC-SHA256 signing
   across GitHub, Jira, and Slack providers.
2. Strict pass/fail gate enforcement: fails if p95 >= 100.0 ms, if any request
   does not return HTTP 202, if error rate > 0%, or if durable DB staging mismatches.
3. End-to-end execution of 200 signed webhook requests producing 200 HTTP 202 responses,
   200 durably staged `webhook_events` rows, p50/p95/p99 calculation with p95 < 100 ms,
   and recorded JSON report output.
"""

import json

import pytest

from app.webhooks.verification import verify_signature_for_provider
from scripts.run_webhook_load_test import (
    PROVIDER_SECRETS,
    WebhookLoadTestRunner,
    build_realistic_github_payload,
    build_realistic_jira_payload,
    build_realistic_slack_payload,
    compute_latency_percentiles,
    evaluate_load_test_gate,
    sign_webhook_headers,
)


def test_realistic_signed_payloads_pass_provider_verification():
    """Scope 1: Realistic payload sizes (multi-KB) and authentic HMAC-SHA256 signatures."""
    gh_body, gh_del, gh_evt = build_realistic_github_payload("unitrun0001", 0)
    assert len(gh_body) >= 8_000, f"Expected realistic GitHub payload >= 8KB, got {len(gh_body)}B"
    gh_headers = sign_webhook_headers("github", PROVIDER_SECRETS["github"], gh_body, gh_del, gh_evt)
    verify_signature_for_provider("github", PROVIDER_SECRETS["github"], gh_body, gh_headers)

    jira_body, jira_del, jira_evt = build_realistic_jira_payload("unitrun0001", 1)
    assert len(jira_body) >= 4_000, f"Expected realistic Jira payload >= 4KB, got {len(jira_body)}B"
    jira_headers = sign_webhook_headers(
        "jira", PROVIDER_SECRETS["jira"], jira_body, jira_del, jira_evt
    )
    verify_signature_for_provider("jira", PROVIDER_SECRETS["jira"], jira_body, jira_headers)

    slack_body, slack_del, slack_evt = build_realistic_slack_payload("unitrun0001", 2)
    assert len(slack_body) >= 3_000, (
        f"Expected realistic Slack payload >= 3KB, got {len(slack_body)}B"
    )
    slack_headers = sign_webhook_headers(
        "slack", PROVIDER_SECRETS["slack"], slack_body, slack_del, slack_evt
    )
    verify_signature_for_provider("slack", PROVIDER_SECRETS["slack"], slack_body, slack_headers)


def test_latency_and_status_gate_fails_at_or_above_100ms():
    """Scope 2 & AC 2: Compute p50/p95/p99 and fail if p95 >= 100 ms or status != 202."""
    sample_latencies = [float(i) for i in range(1, 201)]  # 1..200 ms -> p95 > 100 ms
    pcts = compute_latency_percentiles(sample_latencies)
    assert pcts["p50"] > 0
    assert pcts["p95"] > 100.0
    assert pcts["p99"] > pcts["p95"]

    base_passing = {
        "total_requests": 200,
        "status_202_count": 200,
        "failed_requests": 0,
        "error_rate_percent": 0.0,
        "verified_staged_rows": 200,
        "latency_ms": {"p50": 12.4, "p95": 42.8, "p99": 61.3},
    }
    passed_ok, reasons_ok = evaluate_load_test_gate(base_passing, max_p95_ms=100.0)
    assert passed_ok is True
    assert reasons_ok == []

    # Exact 100.0 ms boundary MUST fail ("fail if p95 is 100 ms or more")
    at_100 = {**base_passing, "latency_ms": {"p50": 45.0, "p95": 100.0, "p99": 120.0}}
    passed_100, reasons_100 = evaluate_load_test_gate(at_100, max_p95_ms=100.0)
    assert passed_100 is False
    assert any("meets or exceeds failure threshold" in r for r in reasons_100)

    # Above 100.0 ms MUST fail
    above_100 = {**base_passing, "latency_ms": {"p50": 60.0, "p95": 135.2, "p99": 180.4}}
    passed_above, reasons_above = evaluate_load_test_gate(above_100, max_p95_ms=100.0)
    assert passed_above is False
    assert any("135.20 ms" in r for r in reasons_above)

    # Non-202 response / non-zero error rate MUST fail
    partial_fail = {
        **base_passing,
        "status_202_count": 199,
        "failed_requests": 1,
        "error_rate_percent": 0.5,
    }
    passed_err, reasons_err = evaluate_load_test_gate(partial_fail, max_p95_ms=100.0)
    assert passed_err is False
    assert len(reasons_err) >= 2


@pytest.mark.asyncio
async def test_200_signed_webhook_requests_p95_below_100ms(tmp_path):
    """SCRUM-251 / US-15 End-to-End Load Test:
    - 200 signed realistic webhook requests (GitHub, Jira, Slack).
    - All 200 return HTTP 202 Accepted and are durably staged in `webhook_events`.
    - p50/p95/p99 latency and error rate computed; p95 strictly below 100 ms.
    - Structured results recorded to JSON report file.
    """
    report_file = tmp_path / "webhook_load_test_results.json"

    runner = WebhookLoadTestRunner(
        total_requests=200,
        concurrency=15,
        in_process=True,
        warmup_requests=3,
        max_p95_ms=100.0,
        report_path=report_file,
    )

    stats = await runner.run()

    # AC 2: All 200 requests produce HTTP 202 and 0% error rate
    assert stats["total_requests"] == 200
    assert stats["status_202_count"] == 200, (
        f"Expected 200 HTTP 202 responses, got {stats['status_202_count']}. "
        f"First error: {stats.get('first_failure_error')}"
    )
    assert stats["status_code_distribution"] == {"202": 200}
    assert stats["failed_requests"] == 0
    assert stats["error_rate_percent"] == 0.0
    assert stats["duplicate_count"] == 0

    # Durable staging invariant: all 200 rows verified in PostgreSQL webhook_events
    assert stats["verified_staged_rows"] == 200

    # Realistic payload sizes verified
    assert stats["payload_size_bytes"]["min"] >= 3_000
    assert stats["payload_size_bytes"]["max"] >= 8_000

    # AC 2: p50/p95/p99 computed and p95 < 100 ms
    lat = stats["latency_ms"]
    assert lat["p50"] > 0
    assert lat["p95"] > 0
    assert lat["p99"] >= lat["p95"]
    assert lat["p95"] < 100.0, f"SLA breach: p95 latency {lat['p95']} ms >= 100.0 ms"
    assert stats["passed"] is True
    assert stats["failure_reasons"] == []

    # AC 3: Results are recorded
    assert report_file.exists()
    persisted = json.loads(report_file.read_text(encoding="utf-8"))
    assert persisted["passed"] is True
    assert persisted["total_requests"] == 200
    assert persisted["status_202_count"] == 200
    assert persisted["latency_ms"]["p95"] < 100.0
