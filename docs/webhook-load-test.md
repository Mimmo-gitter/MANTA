# Webhook Staging Latency Load Test Guide

**Parent User Story:** `SCRUM-251` (`US-15` Durable event staging before acknowledgment)  
**Load Test Runner:** [`backend/scripts/run_webhook_load_test.py`](../backend/scripts/run_webhook_load_test.py)  
**Automated Pytest Suite:** [`backend/tests/test_webhook_load.py`](../backend/tests/test_webhook_load.py)  
**Recorded Results:** [`backend/reports/webhook_load_test_results.json`](../backend/reports/webhook_load_test_results.json)

---

## 1. Objective & Acceptance Criteria

Verifies that the MANTA webhook intake pipeline (`POST /api/v1/webhooks/integrations/{integration_id}`) durably stages incoming signed webhooks into PostgreSQL (`webhook_events` via PgBouncer in transaction pooling mode with `FORCE ROW LEVEL SECURITY`) while keeping **p95 acknowledgment latency strictly below 100 ms**:

1. **200 Signed Realistic Webhooks:** Sends 200 unique HMAC-SHA256 signed requests distributed across **GitHub** (`push` / `pull_request`, ~9.7–13.8 KB), **Jira** (`jira:issue_updated`, ~5.0–5.5 KB), and **Slack** (`event_callback`, ~3.2–3.5 KB), exercising real AES-256-GCM credential decryption, constant-time signature validation, Slack 5-minute replay protection, delivery ID extraction, tenant RLS scoping, and `INSERT ... ON CONFLICT DO NOTHING` WAL commit prior to HTTP response.
2. **Strict SLA & Status Gate:** Computes `p50`, `p95`, `p99` (plus `min`, `mean`, `p90`, `max`) latency and `error_rate_percent`; exits with code `1` if `p95 >= 100.0 ms`, if any request does not return `HTTP 202 Accepted`, or if any returned `event_id` is missing from `webhook_events` (`status = 'staged'`).
3. **One-Command Repeatability:** Idempotently provisions its dedicated tenant and encrypted integrations and generates unique delivery identifiers per run so consecutive runs execute cleanly with a single command.
4. **Recorded Results:** Writes structured benchmark telemetry to [`backend/reports/webhook_load_test_results.json`](../backend/reports/webhook_load_test_results.json).

---

## 2. Single-Transaction Intake Optimization

To eliminate connection-pool contention under burst webhook traffic, [`ingest_webhook_for_integration`](../backend/app/webhooks/router.py) executes the entire verification-and-staging path over **a single pooled connection and transaction** (`AsyncSessionLocal()`):
1. Calls `SECURITY DEFINER` function `get_webhook_integration_route(:id)` to resolve `tenant_id`, `provider`, encrypted `webhook_secret_hash`, and `is_active`.
2. Decrypts the secret (AES-256-GCM) and verifies the provider HMAC-SHA256 signature (and Slack replay window) in memory before performing any write.
3. Applies transaction-local RLS context (`SELECT set_config('app.current_tenant_id', :tenant_id, true)`) and executes `stage_webhook_event_idempotent(...)`, committing durably before returning `HTTP 202 Accepted`.

---

## 3. One-Command Execution

### Live HTTP Stack (`manta_app` $\rightarrow$ `manta_pgbouncer` $\rightarrow$ `manta_postgres`)
```bash
docker exec manta_app python scripts/run_webhook_load_test.py
```

### Automated Pytest Suite (Local & CI)
```bash
python -m pytest backend/tests/test_webhook_load.py -v
```

---

## 4. Recorded Benchmark Results (`SCRUM-251` / `US-15`)

Executed against the live containerized stack (`http://localhost:8000` $\rightarrow$ `pgbouncer:5432/manta_db` $\rightarrow$ PostgreSQL 16):

| Metric | Target / SLA | Recorded Value | Status |
| :--- | :--- | :--- | :---: |
| **Total Signed Requests** | `200` | `200` (`80` GitHub, `60` Jira, `60` Slack) | **PASSED** |
| **Payload Size Distribution** | Realistic multi-KB | `min = 3,232 B`, `mean = 8,162 B`, `max = 13,800 B` (`1.63 MB` total) | **PASSED** |
| **HTTP `202 Accepted` Count** | `200 / 200` | `200 / 200` | **PASSED** |
| **Error Rate** | `0.0%` | `0.0%` (`0` failed requests) | **PASSED** |
| **Durably Staged Rows (`webhook_events`)** | `200 / 200` | `200 / 200` (`status = 'staged'` verified under RLS) | **PASSED** |
| **`p50` Latency** | Recorded | `35.40 ms` | **PASSED** |
| **`p95` Latency** | **`< 100.00 ms`** | **`66.93 ms`** | **PASSED** |
| **`p99` Latency** | Recorded | `82.75 ms` | **PASSED** |
| **Throughput** | Recorded | `390.34 req/s` (`0.512 s` total duration, concurrency `15`) | **PASSED** |
