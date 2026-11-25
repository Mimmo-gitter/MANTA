# US-71: Automated Blocker Detection (`>48h` Stale PRs & `3-Incident / 60m` Spikes) Test Scenarios

**Tasks:**
- `TASK-307-0` (`SCRUM-542`): Author BDD specs & deduplication state-reset rules for `>48h` stale PRs & `3-incident/60m` spikes
- `TASK-307-1` (`SCRUM-543`): Implement scheduled ARQ blocker detector (`SECURITY DEFINER` tenant sweep, `Risk` upsert & `OutboxEvent` emit)
- `TASK-307-2` (`SCRUM-544`): Write scenarios & pytest tests for 49h stale PR, duplicate suppression, activity reset & 2-vs-3 incident spikes

**Parent User Story:** `SCRUM-507` (`STORY-307 — US-71`)  
**Authoritative BDD & State-Machine Specification:** [`docs/blocker-detection-stale-pr-and-incident-spike-bdd-specs.md`](../../../docs/blocker-detection-stale-pr-and-incident-spike-bdd-specs.md)  
**Authoritative Automated Test Suite:** [`backend/tests/test_blocker_detector.py`](../test_blocker_detector.py)

---

## 1. Master Test Scenarios Matrix

| Scenario ID | Category | Scenario Summary | Preconditions | Expected Outcome | Automated Test |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-BLOCKER-001`** | Stale PR / Atomic | Flag open PR idle `> 48h` (default threshold) | Open PR idle `49h`, `is_stale=false`, `pr_stale_threshold_hours=48` | `is_stale=true` (`updated_at` preserved); `1` `Risk(STALE_PR, HIGH, OPEN)` + `1` `OutboxEvent(risk.detected)` committed atomically | `test_stale_pr_detection_atomic_risk_and_outbox_preserves_updated_at` |
| **`TC-BLOCKER-002`** | Stale PR / Boundary | Exact boundary (`== threshold` vs `> threshold`) & custom project thresholds (`24h`/`48h`/`72h`) | Open PRs at `threshold - 1s`, `threshold`, `threshold + 1s` | Strict `>`: `== threshold` stays fresh (`0` rows); `> threshold` flags stale (`1` Risk + `1` OutboxEvent) | `test_stale_pr_threshold_boundary_and_custom_project_hours` |
| **`TC-BLOCKER-003`** | Stale PR / Activity | Recent linked `Commit` or `PullRequestReview` keeps PR fresh | PR created `72h` ago, Commit or Review `10h` ago | `is_stale=false`, `0` Risk, `0` OutboxEvent | `test_recent_commit_or_review_prevents_stale_flagging` |
| **`TC-BLOCKER-004`** | Stale PR / Dedup | Repeated 15m sweeps suppress duplicates for `OPEN` & `ACKNOWLEDGED` | PR already has active `STALE_PR` Risk (`OPEN`, then `ACKNOWLEDGED`) | Strictly `1` Risk and `1` OutboxEvent across repeated sweeps | `test_stale_pr_duplicate_suppression_open_and_acknowledged` |
| **`TC-BLOCKER-005`** | Stale PR / Concurrency | Concurrent sweeps serialize on `uq_risks_active_condition` | 2 workers sweep same tenant concurrently | `ON CONFLICT DO NOTHING RETURNING id` ensures strictly `1` Risk + `1` OutboxEvent | `test_live_pg_partial_unique_index_suppresses_concurrent_duplicates` |
| **`TC-BLOCKER-006`** | State Reset / Commit | GitHub `push` or `pull_request` (`synchronize`) resets `is_stale = false` | Stale PR with active `STALE_PR` Risk | `is_stale=false`, `updated_at` advanced, Risk -> `RESOLVED`, `0` OutboxEvents | `test_push_and_synchronize_reset_is_stale_and_resolve_risk` |
| **`TC-BLOCKER-007`** | State Reset / Review | GitHub `pull_request_review` (`submitted`) resets `is_stale = false` with zero PII | Stale PR with active `STALE_PR` Risk (`ACKNOWLEDGED`) | `PullRequestReview` stored with 64-char HMAC pseudonym, `is_stale=false`, Risk -> `RESOLVED` | `test_pull_request_review_webhook_resets_is_stale_without_pii` |
| **`TC-BLOCKER-008`** | State Reset / OOO | Delayed out-of-order `pull_request` webhook rejected by version guard | Stale PR with `entity_version=2000`; webhook arrives with `1500` | Rejected (`noop:stale_pr_version`), `is_stale` remains `true`, Risk remains `OPEN` | `test_out_of_order_stale_pr_webhook_does_not_reset_is_stale` |
| **`TC-BLOCKER-009`** | State Reset / Re-arm | Reset PR re-arms cleanly after a new `> threshold` idle window | PR reset at `T0+2h`, no activity until `T0+52h` | `is_stale=true`, new Risk #2 (`OPEN`) + new OutboxEvent #2; Risk #1 preserved as `RESOLVED` | `test_stale_pr_rearms_cleanly_after_reset_and_new_idle_period` |
| **`TC-BLOCKER-010`** | Terminal Cleanup | Closed or merged PRs clear `is_stale` and are excluded from future sweeps | Stale PR receives `closed` / `merged` event | `is_stale=false`, Risk -> `RESOLVED`, ignored on subsequent sweeps | `test_closed_and_merged_prs_clear_stale_flag_and_resolve_risk` |
| **`TC-BLOCKER-011`** | Incident Spike / Atomic | `>= 3` incidents in rolling `60m` commits `INCIDENT_SPIKE` Risk + OutboxEvent | 3 incidents at `T-45m`, `T-20m`, `T-5m` | `1` `Risk(INCIDENT_SPIKE, CRITICAL, OPEN)` + `1` `OutboxEvent(risk.detected)` | `test_incident_spike_three_in_60m_commits_risk_and_outbox` |
| **`TC-BLOCKER-012`** | Incident Spike / Boundary | `< 3` incidents, `60m01s` old incident, or 2+2 cross-project split | 2 fresh + 1 expired (`60m01s`) in Proj A; 2 fresh in Proj B | `0` Risks and `0` OutboxEvents created | `test_incident_spike_subthreshold_expired_and_cross_project_ignored` |
| **`TC-BLOCKER-013`** | Incident Spike / Dedup | Ongoing storm (4th/5th incident) and `ACKNOWLEDGED` risk suppress duplicates | Active `INCIDENT_SPIKE` Risk + 4th incident in window | Strictly `1` Risk and `1` OutboxEvent across repeated sweeps | `test_incident_spike_suppresses_duplicates_during_storm_and_ack` |
| **`TC-BLOCKER-014`** | Incident Spike / Reset | Anti-flapping cooldown hold (`count=2`, `age<60m`), auto-resolve, and clean re-arm | Spike at `10:15`; dip at `11:02`; quiet at `11:15`; new burst at `11:30` | Held at `11:02`, resolved at `11:15`, Risk #2 + Outbox #2 emitted at `11:30` | `test_incident_spike_anti_flapping_cooldown_reset_and_rearm` |
| **`TC-BLOCKER-015`** | Atomicity / Rollback | Unhandled exception before commit rolls back all mutations | Error raised after staging Risk & OutboxEvent | `is_stale=false`, `0` Risks, `0` OutboxEvents persisted | `test_atomic_rollback_prevents_partial_risk_or_outbox_writes` |
| **`TC-BLOCKER-016`** | Security / RLS | `SECURITY DEFINER` `list_active_tenants` + `FORCE ROW LEVEL SECURITY` | Active Tenants A & B + suspended Tenant C | Only A & B swept under strict tenant RLS; Tenant C untouched; zero cross-tenant leakage | `test_discover_active_tenants_pagination`, `test_live_pg_multi_tenant_rls_sweep_isolation` |
| **`TC-BLOCKER-017`** | Resilience | Exception in one tenant does not abort remaining tenants | Tenant A raises error; Tenant B has 3 incidents/60m | Tenant A counted in `errors`; Tenant B commits Risk + OutboxEvent cleanly | `test_sweep_all_active_tenants_isolates_single_tenant_failure` |
| **`TC-BLOCKER-018`** | Scheduler / ARQ | 15-minute ARQ `WorkerSettings.cron_jobs` configuration | Import `workers.worker.WorkerSettings` | `blocker_detector_15m` CronJob runs at minutes `{0, 15, 30, 45}`, `second=0`, `unique=True` | `test_arq_worker_settings_registers_15m_blocker_cron` |

---

## 2. TASK-307-2 (`SCRUM-544`) Boundary & RLS Verification Scenarios

| Scenario ID | Focus | Preconditions & Inputs | Expected Outcome | Automated Test |
| :--- | :--- | :--- | :--- | :--- |
| **`TC-307-2-01`** | **47h vs 49h Stale PR Boundary** | Single active project (`pr_stale_threshold_hours=48`) with PR-A idle `47h` and PR-B idle `49h` (`is_stale=false` on both) | PR-A remains `is_stale=false` (`0` Risks, `0` OutboxEvents); PR-B becomes `is_stale=true` with `updated_at` preserved, committing `1` `Risk(STALE_PR, HIGH, OPEN)` + `1` `OutboxEvent(risk.detected)` | `test_task_307_2_47h_vs_49h_stale_pr_boundary` |
| **`TC-307-2-02`** | **Repeat-Run Suppression & Activity Reset** | 49h stale PR flagged -> 2 repeat 15m sweeps (`OPEN` then `ACKNOWLEDGED`) -> `push` & `pull_request_review` activity -> another 49h idle window | Repeat sweeps emit `0` duplicate Risks/OutboxEvents; activity resets `is_stale=false` and transitions Risk to `RESOLVED`; subsequent 49h idle period re-arms cleanly with Risk #2 + OutboxEvent #2 | `test_task_307_2_repeat_run_suppression_and_activity_reset_lifecycle` |
| **`TC-307-2-03`** | **Exact 2-vs-3 Incidents in 60m Boundary** | Active project with `2` incidents inside `[T-60m, T]`, then a `3rd` incident added inside `[T-60m, T]`, followed by a repeat 15m sweep | At `2` incidents: `0` Risks, `0` OutboxEvents; at `3` incidents: `1` `Risk(INCIDENT_SPIKE, CRITICAL, OPEN)` + `1` `OutboxEvent(risk.detected)`; repeat sweep emits `0` duplicates | `test_task_307_2_exact_2_vs_3_incidents_in_60m_boundary` |
| **`TC-307-2-04`** | **Zero Duplicate Alerts Under RLS** | Two active tenants (Tenant A with 49h stale PR, Tenant B with 3 incidents/60m) swept repeatedly via `sweep_all_active_tenants` with tenant-scoped `get_tenant_db_context` (`app.current_tenant_id`) | Each tenant's session only reads/writes its own rows under RLS; repeat sweeps produce `0` duplicate Risks and `0` duplicate OutboxEvents (`duplicates_suppressed == 2`), verified both in deterministic RLS-enforced simulation and live PostgreSQL 16 `FORCE ROW LEVEL SECURITY` (`test_live_pg_multi_tenant_rls_sweep_and_full_lifecycle_e2e`) | `test_task_307_2_zero_duplicate_alerts_under_rls`, `test_live_pg_multi_tenant_rls_sweep_and_full_lifecycle_e2e` |

---

## 3. Verification Commands

```bash
# Unit, state-machine, webhook-reset, and migration contract checks
python -m pytest backend/tests/test_blocker_detector.py backend/tests/test_migrations_ci.py -v

# Linting & formatting
ruff format --check .
ruff check .

# Full live PostgreSQL migration & RLS verification (when PostgreSQL is running)
cd backend
python scripts/verify_migrations.py --step all
python -m pytest tests/test_blocker_detector.py tests/test_rls_guardrails.py -v
```
