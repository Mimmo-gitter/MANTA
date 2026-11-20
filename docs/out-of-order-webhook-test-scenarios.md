# Out-of-Order Webhook Delivery Protection Test Scenarios (GitHub PR, Jira Issue & Slack Message Events)

**Task:** `SCRUM-425` (`Task 24.4`: Create out-of-order webhook test scenarios for GitHub PR, Jira issue (changelog.id 205 then 204) and Slack message events)  
**Parent User Story:** `SCRUM-361` (`US-24`: Out-of-order webhook delivery protection and monotonic versioning)  
**Related Tickets:**  
- `SCRUM-422` / `Task 24.1`: Design monotonic version extraction rules and document per-provider canonical version sources ([`docs/out-of-order-version-extraction-rules.md`](out-of-order-version-extraction-rules.md), [`backend/app/webhooks/versioning.py`](../backend/app/webhooks/versioning.py))  
- `SCRUM-423` / `Task 24.2`: Implement conditional PostgreSQL update guards (`WHERE entity_version < :incoming_version`) and monotonic `entity_version` column on `pull_requests`, `issues`, and `communication_events` ([`backend/migrations/versions/a2c4e6f8b0d1_fencing_versioning_and_dora_indexes.py`](../backend/migrations/versions/a2c4e6f8b0d1_fencing_versioning_and_dora_indexes.py), [`backend/tests/test_out_of_order_versioning.py`](../backend/tests/test_out_of_order_versioning.py))  
- `SCRUM-424` / `Task 24.3`: Implement Prometheus observability metric `dropped_stale_event_total{provider="github"|"jira"|"slack"}` and extractors for out-of-order drops ([`backend/app/webhooks/metrics.py`](../backend/app/webhooks/metrics.py), [`backend/tests/test_version_extractors_and_stale_metrics.py`](../backend/tests/test_version_extractors_and_stale_metrics.py))  
- `SCRUM-425` / `Task 24.4`: Create out-of-order webhook test scenarios for GitHub PR, Jira issue (changelog.id 205 then 204) and Slack message events (this document)  
- `SCRUM-426` / `Task 24.5`: Write automated pytest test suite verifying out-of-order delivery, preserved DB state, and `dropped_stale_event_total` counter increments ([`backend/tests/test_version_extractors_and_stale_metrics.py`](../backend/tests/test_version_extractors_and_stale_metrics.py))  
- `SCRUM-336` / `SCRUM-337`: Idempotent duplicate-delivery protection at intake boundary ([`docs/webhook-duplicate-test-scenarios.md`](webhook-duplicate-test-scenarios.md), [`backend/tests/test_webhook_delivery_idempotency.py`](../backend/tests/test_webhook_delivery_idempotency.py))  
- `SCRUM-404` / `SCRUM-407`: Reliable FIFO queue topology and worker crash recovery ([`docs/fifo-queue-topology.md`](fifo-queue-topology.md), [`docs/fifo-ordering-priority-preemption-and-sigkill-crash-test-scenarios.md`](fifo-ordering-priority-preemption-and-sigkill-crash-test-scenarios.md))  
- `SCRUM-415` / `SCRUM-419`: Dead-Letter Queue (DLQ) poison payload quarantine ([`docs/poison-payload-5x-retry-dlq-navigation-badge-and-redrive-test-scenarios.md`](poison-payload-5x-retry-dlq-navigation-badge-and-redrive-test-scenarios.md))  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`) & Valkey 7 / Redis 7+ (`valkey/valkey:7-alpine`)  
**Target Implementation Modules:** [`backend/app/webhooks/versioning.py`](../backend/app/webhooks/versioning.py), [`backend/app/webhooks/metrics.py`](../backend/app/webhooks/metrics.py), [`backend/app/git/service.py`](../backend/app/git/service.py), [`backend/app/jira/service.py`](../backend/app/jira/service.py), [`backend/app/webhooks/slack_processor.py`](../backend/app/webhooks/slack_processor.py), [`backend/app/webhooks/processor.py`](../backend/app/webhooks/processor.py), [`backend/workers/worker.py`](../backend/workers/worker.py)  
**Authoritative Automated Test Suites:** [`backend/tests/test_out_of_order_versioning.py`](../backend/tests/test_out_of_order_versioning.py), [`backend/tests/test_version_extractors_and_stale_metrics.py`](../backend/tests/test_version_extractors_and_stale_metrics.py)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us24_out_of_order_webhook_scenarios.md`](../backend/tests/scenarios/test_us24_out_of_order_webhook_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Design and formalize the exhaustive test verification scenarios for **out-of-order webhook delivery protection across GitHub, Jira, and Slack** (`SCRUM-425` / `Task 24.4`).

In distributed event-driven systems, incoming webhooks from external software development providers (**GitHub**, **Jira**, **Slack**) transit unreliable public networks, regional edge load balancers, and asynchronous message queues. Consequently, webhook delivery order is **not guaranteed**:
- A delayed Jira update with `changelog.id = 204` (`In Progress`) can arrive **after** a newer Jira event with `changelog.id = 205` (`Done`) has already been committed to the database.
- A delayed GitHub `synchronize` event timestamped at $T+2\text{m}$ can arrive **after** a newer commit push at $T+10\text{m}$ has updated the pull request `head_sha`.
- An earlier Slack thread message or unresolve signal with `event_ts = "1700000005.000100"` can arrive **after** a newer resolution message with `event_ts = "1700000010.000200"` was recorded.

Without robust, monotonic versioning guards, such late-arriving stale events would overwrite newer authoritative state, regressing issue statuses, corrupting PR branch heads, and introducing severe calculation errors into downstream DORA metrics (Lead Time for Changes, MTTR, and Change Failure Rate).

### 1.2 Implementation Scope & Core Scenarios

This specification defines the exact pre-conditions, webhook JSON payloads, execution sequence, database state assertions, and metric counter validations for:
1. **Jira Issue Out-of-Order Delivery:** Authoritative update `changelog.id = 205` (`Done`) arriving first, followed by delayed stale update `changelog.id = 204` (`In Progress`), asserting that the issue remains in status `Done`, title and `entity_version` are preserved, and Prometheus counter `dropped_stale_event_total{provider="jira"}` increments by $+1$.
2. **GitHub Pull Request Out-of-Order Delivery:** Authoritative update with `updated_at = T+10m` arriving first, followed by delayed stale update with `updated_at = T+2m`, asserting that the PR `head_sha`, title, and `entity_version` are preserved, and Prometheus counter `dropped_stale_event_total{provider="github"}` increments by $+1$.
3. **Slack Message Event Out-of-Order Delivery:** Authoritative resolution with `event_ts = "1700000010.000200"` arriving first, followed by delayed stale unresolve/open signal with `event_ts = "1700000005.000100"`, asserting that `is_resolved = true`, kind, and `entity_version` are preserved, and Prometheus counter `dropped_stale_event_total{provider="slack"}` increments by $+1$.
4. **Delayed Jira Issue Creation After Changelog Update:** Delayed `jira:issue_created` (without `changelog.id`, falling back to timestamp) arriving after an authoritative numeric `changelog.id` was already stored, asserting that the creation payload is safely dropped.
5. **Terminal State Protection (GitHub PR MERGED):** Ensuring a PR in terminal `MERGED` state rejects any delayed out-of-order `synchronize` or `opened` events.
6. **Sub-second Precision & Float Drift Defense:** Proving that Slack 6-digit microsecond timestamps are parsed using arbitrary-precision `Decimal` to avoid IEEE-754 binary floating-point rounding drift.
7. **Clean Worker Loop Lifecycle & Zero-DLQ Guarantee:** Verifying that 0-row stale drops are treated as successful NO-OPs (`noop:stale_*`), transitioning `webhook_events.status` to `'completed'`, acknowledging the job from the worker in-flight list without retry, and resulting in **0 entries in `dead_letter_events`**.
8. **Multi-Tenant Row-Level Security (RLS) Isolation:** Verifying that out-of-order deliveries in Tenant A never mutate, collide with, or leak into Tenant B.

---

## 2. Core Architectural Invariants & Mathematical Formulations

```
+-------------------------------------------------------------------------------------------------------------------+
|                        OUT-OF-ORDER WEBHOOK DELIVERY & MONOTONIC VERSIONING TOPOLOGY (US-24)                      |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Canonical Version Extraction     | 2. Conditional PostgreSQL Guards    | 3. Observability & Zero-DLQ Lifecycle |
|    (Deterministic BigInteger)       |    (WHERE entity_version < incoming)|    (dropped_stale_event_total)        |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * GitHub: updated_at -> UTC epoch ms| * pull_requests: UPDATE ... WHERE   | * 0 rows modified -> Stale Drop       |
| * Jira: changelog.id (numeric seq)  |   entity_version < :incoming_version| * record_dropped_stale_event(provider)|
|   Fallback: fields.updated epoch ms | * issues: UPDATE ... WHERE guard    | * dropped_stale_event_total += 1      |
| * Slack: event_ts via Decimal       | * communication_events: ON CONFLICT | * Worker completes cleanly (NOOP)     |
|   Exact microseconds (10^6)         |   DO UPDATE WHERE entity_version <  | * 0 DLQ writes; 0 retries             |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                            MULTI-TENANT ROW-LEVEL SECURITY & CROSS-DOMAIN SAFETY                                  |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Multi-Tenant RLS Boundary (FORCE RLS)           | 5. Jira Changelog vs. Timestamp Cross-Domain Guard            |
+----------------------------------------------------+--------------------------------------------------------------+
| * Session bound to app.current_tenant_id           | * changelog.id numeric sequence takes absolute precedence    |
| * Identical keys across tenants do not collide     | * Delayed jira:issue_created (timestamp) cannot overwrite    |
| * Out-of-order rejection strictly tenant-isolated  |   an authoritative changelog_id already stored in DB         |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Canonical Monotonic Version Extraction per Provider

Every incoming webhook event extracts a 64-bit integer (`BIGINT`) `entity_version` following the deterministic rules established in [`docs/out-of-order-version-extraction-rules.md`](out-of-order-version-extraction-rules.md):

#### 1. GitHub Pull Requests (`pull_requests`)
- **Primary Source:** `pull_request.updated_at` (ISO-8601 string normalized to UTC).
- **Target Unit:** **UTC Epoch Milliseconds** (`BIGINT`).
- **Mathematical Definition:**
  $$V_{\text{GitHub}} = \lfloor t_{\text{updated\_at}} \times 1000 \rfloor \in \mathbb{N}^+$$
- **Fallback Hierarchy:** `pull_request.merged_at` $\rightarrow$ `closed_at` $\rightarrow$ `created_at` $\rightarrow$ explicit payload `entity_version` $\rightarrow$ baseline $1$ (for synthetic payloads).

#### 2. Jira Issues (`issues`)
- **Primary Source:** `changelog.id` (Authoritative per-instance monotonic numeric sequence string).
- **Target Unit:** **Positive BigInteger Sequence ID** (`BIGINT`).
- **Mathematical Definition:**
  $$V_{\text{Jira}} = \text{int}(\text{changelog.id}) \in \mathbb{N}^+ \quad \text{where } \text{changelog.id} > 0$$
- **Fallback Hierarchy:** When `changelog.id` is absent (such as initial `jira:issue_created` payloads or metadata edits without a changelog block):
  $$V_{\text{Jira, fallback}} = \lfloor t_{\text{issue.fields.updated}} \times 1000 \rfloor \in \mathbb{N}^+$$
  falling back through `payload.timestamp` $\rightarrow$ `issue.fields.created` $\rightarrow$ explicit `entity_version` $\rightarrow$ $1$.

#### 3. Slack Communication Events (`communication_events`)
- **Primary Source:** `event.event_ts` (or `payload.event_ts`, `event.ts`, `event.message.edited.ts`).
- **Target Unit:** **UTC Epoch Microseconds** (`BIGINT`).
- **Mathematical Definition (Arbitrary-Precision Decimal):**
  Slack timestamp strings follow the format `"SSSSSSSSSS.FFFFFF"`, where `SSSSSSSSSS` is Unix seconds and `FFFFFF` is 6 decimal microsecond digits. To eliminate binary floating-point IEEE-754 precision drift:
  $$V_{\text{Slack}} = \text{int}\left( \text{Decimal}(\text{raw\_ts}) \times 1\,000\,000 \right) \in \mathbb{N}^+$$
- **Fallback Hierarchy:** `event.thread_ts` $\rightarrow$ `event_time \times 1\,000\,000` $\rightarrow$ explicit `entity_version`.

---

### Invariant 2: Conditional PostgreSQL Update & Upsert Guards

Database mutations enforce optimistic concurrency guards directly within atomic SQL statements:

#### 1. Conditional Update Guard (`pull_requests` & `issues` via `execute_versioned_update`)
```sql
UPDATE pull_requests
SET title = :title,
    state = :state,
    head_branch = :head_branch,
    base_branch = :base_branch,
    head_sha = :head_sha,
    entity_version = :incoming_version,
    updated_at = :updated_at
WHERE id = :record_id
  AND tenant_id = :tenant_id
  AND entity_version < :incoming_version
RETURNING id;
```

#### 2. Jira Cross-Domain Changelog Precedence Guard
Jira operates across two versioning domains: numeric changelog sequence IDs and timestamp-based fallback epoch milliseconds. To prevent a delayed `jira:issue_created` payload from overwriting an authoritative numeric changelog, [`backend/app/jira/service.py`](../backend/app/jira/service.py) enforces:
```python
if incoming_changelog_id is not None:
    if issue.changelog_id is None:
        version_guard = or_(
            Issue.changelog_id.is_(None),
            Issue.entity_version < incoming_ver,
        )
    else:
        version_guard = Issue.entity_version < incoming_ver
else:
    # Incoming payload lacks changelog.id (e.g., delayed jira:issue_created)
    version_guard = and_(
        Issue.changelog_id.is_(None),
        Issue.entity_version < incoming_ver,
    )
```

#### 3. Conditional Upsert Guard (`communication_events` via `upsert_communication_event_versioned`)
```sql
INSERT INTO communication_events (
    id, tenant_id, project_id, integration_id, kind, channel_id, thread_ts,
    asked_at, first_response_at, response_delay_seconds, is_resolved,
    entity_version, created_at
) VALUES (
    :id, :tenant_id, :project_id, :integration_id, :kind, :channel_id, :thread_ts,
    :asked_at, :first_response_at, :response_delay_seconds, :is_resolved,
    :incoming_version, :created_at
)
ON CONFLICT (project_id, channel_id, thread_ts) DO UPDATE
SET kind = EXCLUDED.kind,
    is_resolved = EXCLUDED.is_resolved,
    first_response_at = COALESCE(EXCLUDED.first_response_at, communication_events.first_response_at),
    response_delay_seconds = COALESCE(EXCLUDED.response_delay_seconds, communication_events.response_delay_seconds),
    entity_version = EXCLUDED.entity_version
WHERE communication_events.entity_version < EXCLUDED.entity_version
RETURNING id;
```

---

### Invariant 3: Zero-Row Mutation, Prometheus Metric Delta & Clean NOOP Lifecycle

When a stale webhook event arrives ($V_{\text{incoming}} \le V_{\text{stored}}$):
1. **Zero Rows Mutated:** The conditional PostgreSQL predicate (`entity_version < :incoming_version`) evaluates to `FALSE`, resulting in `0 rows updated` and `RETURNING id` yielding `NULL`.
2. **Prometheus Counter Increment:** The application executes `record_dropped_stale_event(provider)`, which atomically increments:
   $$\Delta \left( \mathtt{dropped\_stale\_event\_total}\{\mathtt{provider} = P\} \right) = +1.0$$
3. **Structured Warning Logging:** Emits a structured log entry:
   `STALE_OUT_OF_ORDER_REJECTED provider=<p> table=<table> record_id=<id> incoming_version=<v_in>`
4. **Clean Job Acknowledgment (Zero-DLQ):** The webhook processing pipeline returns a NOOP result string:
   - GitHub: `"noop:stale_pr_version:<number>"` or `"noop:stale_pr_event:<number>"`
   - Jira: `"noop:stale_issue_version:<key>"`
   - Slack: `"noop:stale_slack_event:<channel>:<thread_ts>"`
   The consumer worker marks `webhook_events.status = 'completed'` and acknowledges the message (`ack_job`). **No exceptions are raised, no retries are scheduled, and zero rows are inserted into `dead_letter_events`.**

---

### Invariant 4: Terminal State Protection (GitHub PR MERGED)

Pull requests entering terminal `state = 'MERGED'` are permanently locked against retrograde state transitions:
$$\text{state}_{\text{current}} = \mathtt{MERGED} \implies \text{state}_{\text{next}} \in \{\mathtt{MERGED}\}$$
Any delayed `synchronize`, `reopened`, or `opened` webhook payload arriving after a PR has been merged is immediately rejected by `_is_stale(pr.state, n)`, calling `record_dropped_stale_event("github")` and returning `"noop:stale_pr_event:<number>"`.

---

## 3. Database Schemas, Fixtures & Prometheus Metric Specification

### 3.1 Relational Schema Definitions

```
+-------------------------------------------------------------------------------------------------------------------+
|                                        TARGET RELATIONAL TABLE SCHEMAS                                            |
+-------------------------------------------------------------------------------------------------------------------+
| 1. pull_requests (PostgreSQL 16)                                                                                  |
|    - id: UUID PRIMARY KEY                                                                                         |
|    - tenant_id: UUID NOT NULL (FK -> tenants.id, RLS enforced)                                                    |
|    - repository_id: UUID NOT NULL (FK -> repositories.id)                                                         |
|    - external_pr_number: INTEGER NOT NULL                                                                         |
|    - title: VARCHAR(512) NOT NULL                                                                                 |
|    - state: VARCHAR(32) NOT NULL ('OPEN', 'MERGED', 'CLOSED')                                                     |
|    - head_sha: VARCHAR(40) NOT NULL                                                                               |
|    - entity_version: BIGINT NOT NULL DEFAULT 1                                                                    |
|    - updated_at: TIMESTAMPTZ NOT NULL                                                                             |
|    - CONSTRAINT uq_pull_requests_repo_pr UNIQUE (tenant_id, repository_id, external_pr_number)                    |
+-------------------------------------------------------------------------------------------------------------------+
| 2. issues (PostgreSQL 16)                                                                                         |
|    - id: UUID PRIMARY KEY                                                                                         |
|    - tenant_id: UUID NOT NULL (FK -> tenants.id, RLS enforced)                                                    |
|    - project_id: UUID NOT NULL (FK -> projects.id)                                                                |
|    - external_key: VARCHAR(64) NOT NULL                                                                           |
|    - title: VARCHAR(512) NOT NULL                                                                                 |
|    - status: VARCHAR(64) NOT NULL                                                                                 |
|    - changelog_id: BIGINT NULL                                                                                    |
|    - entity_version: BIGINT NOT NULL DEFAULT 1                                                                    |
|    - updated_at: TIMESTAMPTZ NOT NULL                                                                             |
|    - CONSTRAINT uq_issues_tenant_key UNIQUE (tenant_id, external_key)                                             |
+-------------------------------------------------------------------------------------------------------------------+
| 3. communication_events (PostgreSQL 16)                                                                           |
|    - id: UUID PRIMARY KEY                                                                                         |
|    - tenant_id: UUID NOT NULL (FK -> tenants.id, RLS enforced)                                                    |
|    - project_id: UUID NOT NULL (FK -> projects.id)                                                                |
|    - channel_id: VARCHAR(64) NOT NULL                                                                             |
|    - thread_ts: VARCHAR(32) NOT NULL                                                                              |
|    - kind: VARCHAR(64) NOT NULL                                                                                   |
|    - is_resolved: BOOLEAN NOT NULL DEFAULT FALSE                                                                  |
|    - entity_version: BIGINT NOT NULL DEFAULT 1                                                                    |
|    - created_at: TIMESTAMPTZ NOT NULL                                                                             |
|    - CONSTRAINT uq_communication_thread UNIQUE (project_id, channel_id, thread_ts)                                |
+-------------------------------------------------------------------------------------------------------------------+
| 4. webhook_events (PostgreSQL 16)                                                                                 |
|    - id: UUID PRIMARY KEY                                                                                         |
|    - tenant_id: UUID NOT NULL (FK -> tenants.id, RLS enforced)                                                    |
|    - provider: VARCHAR(32) NOT NULL ('github', 'jira', 'slack')                                                  |
|    - status: VARCHAR(32) NOT NULL ('staged', 'processing', 'completed', 'dead_lettered')                           |
|    - attempts: INTEGER NOT NULL DEFAULT 0                                                                        |
+-------------------------------------------------------------------------------------------------------------------+
```

### 3.2 Canonical Seed Fixtures

All test scenarios execute against two tenant sandboxes (`Tenant Alpha` and `Tenant Beta`):

| Fixture Entity | Variable Name | Identifier (UUID / Key) | Description / Configuration |
| :--- | :--- | :--- | :--- |
| **Tenant Alpha** | `tid_a` | `11111111-1111-1111-1111-111111111111` | Primary tenant under test (`slug: tenant-alpha-ooo`) |
| **Tenant Beta** | `tid_b` | `22222222-2222-2222-2222-222222222222` | Boundary isolation tenant (`slug: tenant-beta-ooo`) |
| **Project Alpha** | `proj_a` | `aaaa1111-0000-0000-0000-000000000001` | Project linked to Tenant Alpha |
| **GitHub Repo Alpha** | `repo_a` | `bbbb1111-0000-0000-0000-000000000001` | `full_name: acme/manta-core`, `external_repo_id: 10424` |
| **Jira Integration Alpha** | `jira_integ_a` | `cccc1111-0000-0000-0000-000000000001` | `provider: jira`, `account_id: jira-tenant-a` |
| **Slack Integration Alpha**| `slack_integ_a`| `dddd1111-0000-0000-0000-000000000001` | `provider: slack`, `account_id: slack-tenant-a` |

### 3.3 Prometheus Metric Specification

- **Metric Identifier:** `dropped_stale_event_total`
- **Metric Type:** Monotonic Counter
- **Label Names:** `provider`
- **Allowed Label Values:** `github`, `jira`, `slack`
- **Cardinality:** Exactly 3 label series; pre-initialized at process boot to `0.0`.
- **Exposition Format (`GET /metrics`):**
  ```text
  # HELP dropped_stale_event_total Total number of stale out-of-order webhook events dropped after 0-row stale updates.
  # TYPE dropped_stale_event_total counter
  dropped_stale_event_total{provider="github"} 0.0
  dropped_stale_event_total{provider="jira"} 0.0
  dropped_stale_event_total{provider="slack"} 0.0
  ```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Provider | Target Table | Scenario Name | Delivery Sequence | Expected DB State Preserved | Counter Delta $\Delta$ | Automated Pytest Mapping |
| :--- | :---: | :---: | :--- | :--- | :--- | :---: | :--- |
| **`TC-OOO-JIRA-001`** | **Jira** | `issues` | **Out-of-Order Changelog (`205` then `204`)** | 1. `changelog.id = 205` (`Done`, T+10m)<br>2. `changelog.id = 204` (`In Progress`, T+2m) | `status = 'Done'`, `title = 'Done at 205'`, `changelog_id = 205`, `entity_version = 205` | `jira: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_live_pg_e2e_stale_events_preserve_state...` |
| **`TC-OOO-GH-001`** | **GitHub** | `pull_requests` | **Out-of-Order PR Synchronize (`T+10m` then `T+2m`)** | 1. PR sync `updated_at = T+10m` (`head_sha = b*40`)<br>2. PR sync `updated_at = T+2m` (`head_sha = a*40`) | `head_sha = b*40`, `title = 'Authoritative T+10m PR Title'`, `entity_version = 1795083000000` | `github: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_live_pg_e2e_stale_events_preserve_state...` |
| **`TC-OOO-SLACK-001`** | **Slack** | `communication_events` | **Out-of-Order Thread Update (`event_ts = ...10.000200` then `...05.000100`)** | 1. Thread resolved: `1700000010.000200`<br>2. Thread unresolve: `1700000005.000100` | `is_resolved = true`, `kind = 'blocker_resolved'`, `entity_version = 1700000010000200` | `slack: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_live_pg_e2e_stale_events_preserve_state...` |
| **`TC-OOO-JIRA-002`** | **Jira** | `issues` | **Delayed Initial `jira:issue_created` After Changelog Update** | 1. `jira:issue_updated` (`changelog.id = 205`, `Done`)<br>2. Delayed `jira:issue_created` (no changelog, `To Do`) | `status = 'Done'`, `title = 'Done at 205'`, `changelog_id = 205` (creation payload rejected) | `jira: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_live_pg_e2e_stale_events_preserve_state...` |
| **`TC-OOO-GH-002`** | **GitHub** | `pull_requests` | **Terminal State Protection on `MERGED` PR** | 1. PR merged: `state = MERGED`, `merge_commit_sha`<br>2. Delayed `synchronize` or `opened` event | `state = 'MERGED'`, `merge_commit_sha` intact (reopened/sync discarded via terminal guard) | `github: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_process_pull_request_stale_terminal_merged_guard` |
| **`TC-OOO-SLACK-002`** | **Slack** | `communication_events` | **Microsecond Decimal Precision Drift Defense** | 1. Event at `1731326400.123456`<br>2. Delayed event at `1731326400.123455` (1 $\mu$s earlier) | `entity_version = 1731326400123456` preserved (Decimal prevents IEEE-754 float drift) | `slack: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_slack_event_version_extraction_decimal_precision` |
| **`TC-OOO-IDENT-001`** | **All** | All 3 Tables | **Identical Entity Version Duplicate Drop** | 1. Event with version $V_1$ applied<br>2. Second event with identical version $V_1$ arrives | Original row unmodified (predicate `< :incoming_version` rejects equal versions) | `provider: +1` | `test_out_of_order_versioning.py`<br>$\rightarrow$ `test_conditional_update_rejects_older_version_mock` |
| **`TC-OOO-XTEN-001`** | **All** | All 3 Tables | **Multi-Tenant RLS Out-of-Order Independence** | 1. Tenant A receives newer then older event<br>2. Tenant B receives older then newer event | Tenant A retains newer; Tenant B accepts newer; 0 cross-tenant interference | Independent | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_live_pg_e2e_stale_events_preserve_state...` |
| **`TC-OOO-WORKER-001`**| **All** | `webhook_events`, `dead_letter_events` | **Worker Loop Clean NOOP & Zero-DLQ Guarantee** | Stale job dispatched to `run_worker_fetch_loop` with `retry_on_failure=True` | `webhook_events.status = 'completed'`, message acknowledged (`ack_job`), `dead_letter_events = 0 rows` | `provider: +1` | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_worker_completes_stale_event_without_dlq...` |
| **`TC-OOO-PROM-001`** | **All** | Observability | **Prometheus Metric Card, Cardinality & Scrape** | Execute `GET /metrics` via HTTP ASGI client after stale drops | Public scrape surfaces exact counter totals for all 3 labels; 0 unknown labels | Verified | `test_version_extractors_and_stale_metrics.py`<br>$\rightarrow$ `test_metrics_endpoint_exposes_dropped_stale_event_total` |

---

## 5. Exhaustive Test Scenario Specifications

```
+-------------------------------------------------------------------------------------------------------------------+
|                            EXHAUSTIVE TEST SCENARIOS EXECUTION FLOWCHART                                         |
+-------------------------------------------------------------------------------------------------------------------+
|  [Step 1: Record Initial Metric Samples]  -->  b_gh = sample('github'), b_jira = sample('jira'), b_sl = sample('slack') |
|                                                                                                                   |
|  [Step 2: Deliver Authoritative Newer Event]                                                                       |
|      * Jira: changelog.id = 205 (Done, T+10m)          --> Applied: status='Done', version=205, Δ metric = 0      |
|      * GitHub: updated_at = T+10m (head_sha=b*40)       --> Applied: head_sha=b*40, version=T+10m, Δ metric = 0    |
|      * Slack: event_ts = 1700000010.000200 (Resolved)   --> Applied: is_resolved=true, version=...0200, Δ metric=0|
|                                                                                                                   |
|  [Step 3: Deliver Delayed Older Stale Event]                                                                      |
|      * Jira: changelog.id = 204 (In Progress, T+2m)    --> 0 rows modified: status remains 'Done', Δ metric = +1  |
|      * GitHub: updated_at = T+2m (head_sha=a*40)        --> 0 rows modified: head_sha remains b*40, Δ metric = +1  |
|      * Slack: event_ts = 1700000005.000100 (Open)       --> 0 rows modified: is_resolved remains true, Δ metric = +1|
|                                                                                                                   |
|  [Step 4: Assert DB State Preservation, Zero DLQ Entries & Prometheus Counter Totals]                             |
+-------------------------------------------------------------------------------------------------------------------+
```

---

### Scenario `TC-OOO-JIRA-001`: Jira Issue Out-of-Order Changelog (`changelog.id = 205` then `204`)

#### 1. Description & Context
Simulates the canonical out-of-order Jira scenario where an issue is moved to `In Progress` at $T+2\text{m}$ (`changelog.id = 204`), and then closed as `Done` at $T+10\text{m}$ (`changelog.id = 205`). Due to network retry or delay, the authoritative $T+10\text{m}$ payload (`changelog.id = 205`) reaches the MANTA platform first and commits to the database. Moments later, the delayed $T+2\text{m}$ payload (`changelog.id = 204`) arrives.

#### 2. Pre-Conditions & Baseline
- Tenant `tid_a` and Project `proj_a` provisioned.
- Integration `jira_integ_a` active for Tenant `tid_a`.
- Initial metric sample: $M_0 = \mathtt{REGISTRY.get\_sample\_value("dropped\_stale\_event\_total", \{"provider": "jira"\})}$.

#### 3. Payload 1: Authoritative Newer Event (`E1`, `changelog.id = 205`)
- **Event Type:** `jira:issue_updated`
- **Delivery Sequence:** First delivered ($t_1$)
- **JSON Payload:**
```json
{
  "webhookEvent": "jira:issue_updated",
  "issue": {
    "id": "42401",
    "key": "MANTA-424",
    "fields": {
      "summary": "Done at 205",
      "status": {
        "name": "Done"
      },
      "issuetype": {
        "name": "Story"
      },
      "updated": "2026-11-19T10:10:00.000+0000"
    }
  },
  "changelog": {
    "id": "205",
    "items": [
      {
        "field": "status",
        "fromString": "In Progress",
        "toString": "Done"
      }
    ]
  }
}
```

#### 4. Payload 2: Delayed Stale Event (`E2`, `changelog.id = 204`)
- **Event Type:** `jira:issue_updated`
- **Delivery Sequence:** Second delivered ($t_2$, where $t_2 > t_1$)
- **JSON Payload:**
```json
{
  "webhookEvent": "jira:issue_updated",
  "issue": {
    "id": "42401",
    "key": "MANTA-424",
    "fields": {
      "summary": "Stale In Progress at 204",
      "status": {
        "name": "In Progress"
      },
      "issuetype": {
        "name": "Story"
      },
      "updated": "2026-11-19T10:02:00.000+0000"
    }
  },
  "changelog": {
    "id": "204",
    "items": [
      {
        "field": "status",
        "fromString": "To Do",
        "toString": "In Progress"
      }
    ]
  }
}
```

#### 5. Execution Steps & Behavioral Flow
1. **Stage & Process `E1`:**
   - Version extraction: `extract_jira_issue_version(E1)` evaluates `changelog.id = "205"` $\rightarrow 205$.
   - `upsert_issue` executes `INSERT INTO issues (..., entity_version, changelog_id) VALUES (..., 205, 205)`.
   - Result: `"persisted_issue:<issue_id>"`, `applied = True`.
   - Metric assertion: `dropped_stale_event_total{provider="jira"}` equals $M_0$ (no delta).
2. **Stage & Process `E2`:**
   - Version extraction: `extract_jira_issue_version(E2)` evaluates `changelog.id = "204"` $\rightarrow 204$.
   - Precedence guard: `incoming (204) <= issue.changelog_id (205)` triggers stale drop.
   - SQL update modifies `0 rows`.
   - Application calls `record_dropped_stale_event("jira")`.
   - Result: `"noop:stale_issue_version:MANTA-424"`, `applied = False`.
   - Metric assertion: `dropped_stale_event_total{provider="jira"}` equals $M_0 + 1.0$ ($\Delta = +1.0$).

#### 6. Expected Preserved Database State
Execute direct SQL query against PostgreSQL:
```sql
SELECT external_key, title, status, changelog_id, entity_version
FROM issues
WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
  AND external_key = 'MANTA-424';
```
**Assert Column Invariants:**
- `external_key = 'MANTA-424'`
- `title = 'Done at 205'` *(preserved from E1; NOT overwritten by E2)*
- `status = 'Done'` *(preserved from E1; NOT reverted to 'In Progress')*
- `changelog_id = 205` *(preserved from E1)*
- `entity_version = 205` *(preserved from E1)*

#### 7. Queue & Worker Lifecycle Assertions
- `webhook_events` row for `E1`: `status = 'completed'`, `attempts = 0`.
- `webhook_events` row for `E2`: `status = 'completed'`, `attempts = 0`.
- `dead_letter_events` row count for Tenant Alpha: `COUNT(*) = 0`.

---

### Scenario `TC-OOO-GH-001`: GitHub PR Out-of-Order Synchronize (`updated_at = T+10m` then `T+2m`)

#### 1. Description & Context
A pull request receives multiple commit pushes (`synchronize` events). Commit $B$ is pushed at $T+10\text{m}$ (`updated_at = "2026-11-19T10:10:00Z"`, `head_sha = bbb...`), and commits first. Commit $A$, pushed earlier at $T+2\text{m}$ (`updated_at = "2026-11-19T10:02:00Z"`, `head_sha = aaa...`), arrives out-of-order after Commit $B$.

#### 2. Pre-Conditions & Baseline
- Repository `acme/scrum424-repo` provisioned and linked to Project `proj_a`.
- Initial metric sample: $M_0 = \mathtt{REGISTRY.get\_sample\_value("dropped\_stale\_event\_total", \{"provider": "github"\})}$.

#### 3. Payload 1: Authoritative Newer Event (`E1`, $T+10\text{m}$)
- **Event Type:** `pull_request` (Action: `synchronize`)
- **Delivery Sequence:** First delivered ($t_1$)
- **JSON Payload:**
```json
{
  "action": "synchronize",
  "repository": {
    "full_name": "acme/scrum424-repo"
  },
  "pull_request": {
    "number": 424,
    "title": "Authoritative T+10m PR Title",
    "state": "open",
    "head": {
      "ref": "feat/424",
      "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    },
    "base": {
      "ref": "main"
    },
    "user": {
      "login": "octocat"
    },
    "created_at": "2026-11-19T10:00:00Z",
    "updated_at": "2026-11-19T10:10:00Z"
  }
}
```

#### 4. Payload 2: Delayed Stale Event (`E2`, $T+2\text{m}$)
- **Event Type:** `pull_request` (Action: `synchronize`)
- **Delivery Sequence:** Second delivered ($t_2$)
- **JSON Payload:**
```json
{
  "action": "synchronize",
  "repository": {
    "full_name": "acme/scrum424-repo"
  },
  "pull_request": {
    "number": 424,
    "title": "Stale T+2m PR Title",
    "state": "open",
    "head": {
      "ref": "feat/424",
      "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    "base": {
      "ref": "main"
    },
    "user": {
      "login": "octocat"
    },
    "created_at": "2026-11-19T10:00:00Z",
    "updated_at": "2026-11-19T10:02:00Z"
  }
}
```

#### 5. Execution Steps & Behavioral Flow
1. **Stage & Process `E1`:**
   - `extract_github_pr_version(E1)` converts `"2026-11-19T10:10:00Z"` to epoch milliseconds:
     $$V_1 = 1795083000000$$
   - `process_pull_request` executes `INSERT INTO pull_requests (..., head_sha, entity_version) VALUES (..., 'bbbb...', 1795083000000)`.
   - Result: `"persisted_pr:<pr_id>"`.
   - Metric assertion: `dropped_stale_event_total{provider="github"}` equals $M_0$ (no delta).
2. **Stage & Process `E2`:**
   - `extract_github_pr_version(E2)` converts `"2026-11-19T10:02:00Z"` to epoch milliseconds:
     $$V_2 = 1795082520000$$
   - Comparison: $V_2 < V_1$ ($1795082520000 < 1795083000000$).
   - `execute_versioned_update` executes conditional SQL:
     ```sql
     UPDATE pull_requests
     SET title = 'Stale T+2m PR Title',
         head_sha = 'aaaa...',
         entity_version = 1795082520000
     WHERE id = :pr_id
       AND entity_version < 1795082520000;
     ```
   - Matches **0 rows**.
   - `execute_versioned_update` calls `record_dropped_stale_event("github")`.
   - Result: `"noop:stale_pr_version:424"`.
   - Metric assertion: `dropped_stale_event_total{provider="github"}` equals $M_0 + 1.0$ ($\Delta = +1.0$).

#### 6. Expected Preserved Database State
Execute direct SQL query against PostgreSQL:
```sql
SELECT external_pr_number, title, state, head_sha, entity_version
FROM pull_requests
WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
  AND external_pr_number = 424;
```
**Assert Column Invariants:**
- `external_pr_number = 424`
- `title = 'Authoritative T+10m PR Title'` *(preserved from E1)*
- `state = 'OPEN'`
- `head_sha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'` *(preserved from E1; NOT reverted to 'aaaa...')*
- `entity_version = 1795083000000` *(preserved from E1)*

#### 7. Queue & Worker Lifecycle Assertions
- Both events marked `status = 'completed'` in `webhook_events`.
- `dead_letter_events` row count remains `0`.

---

### Scenario `TC-OOO-SLACK-001`: Slack Message Event Out-of-Order Thread Update (`event_ts = 1700000010.000200` then `1700000005.000100`)

#### 1. Description & Context
A blocker thread in Slack channel `C424` (root `thread_ts = "1700000001.000001"`) is resolved by an engineering lead at $t = 1700000010.000200$ (`kind = "blocker_resolved"`, `is_resolved = true`). Due to edge-proxy retries, an unresolve or earlier thread message at $t = 1700000005.000100$ (`kind = "blocker_open"`, `is_resolved = false`) arrives afterwards.

#### 2. Pre-Conditions & Baseline
- Slack integration `slack_integ_a` active for Tenant `tid_a`.
- Initial metric sample: $M_0 = \mathtt{REGISTRY.get\_sample\_value("dropped\_stale\_event\_total", \{"provider": "slack"\})}$.

#### 3. Payload 1: Authoritative Newer Event (`E1`, $t = 1700000010.000200$)
- **Event Type:** `event_callback`
- **Delivery Sequence:** First delivered ($t_1$)
- **JSON Payload:**
```json
{
  "type": "event_callback",
  "kind": "blocker_resolved",
  "is_resolved": true,
  "event": {
    "type": "message",
    "channel": "C424",
    "thread_ts": "1700000001.000001",
    "ts": "1700000010.000200",
    "event_ts": "1700000010.000200"
  }
}
```

#### 4. Payload 2: Delayed Stale Event (`E2`, $t = 1700000005.000100$)
- **Event Type:** `event_callback`
- **Delivery Sequence:** Second delivered ($t_2$)
- **JSON Payload:**
```json
{
  "type": "event_callback",
  "kind": "blocker_open",
  "is_resolved": false,
  "event": {
    "type": "message",
    "channel": "C424",
    "thread_ts": "1700000001.000001",
    "ts": "1700000005.000100",
    "event_ts": "1700000005.000100"
  }
}
```

#### 5. Execution Steps & Behavioral Flow
1. **Stage & Process `E1`:**
   - Exact Decimal microsecond extraction:
     $$V_1 = \text{int}(\text{Decimal}("1700000010.000200") \times 10^6) = 1700000010000200$$
   - `upsert_communication_event_versioned` inserts new row with `entity_version = 1700000010000200`, `is_resolved = true`.
   - Result: `"persisted_communication_event:<id>"`, `applied = True`.
   - Metric assertion: `dropped_stale_event_total{provider="slack"}` equals $M_0$ (no delta).
2. **Stage & Process `E2`:**
   - Decimal microsecond extraction:
     $$V_2 = \text{int}(\text{Decimal}("1700000005.000100") \times 10^6) = 1700000005000100$$
   - Conflict resolution evaluates:
     ```sql
     ON CONFLICT (project_id, channel_id, thread_ts) DO UPDATE
     SET is_resolved = EXCLUDED.is_resolved,
         entity_version = EXCLUDED.entity_version
     WHERE communication_events.entity_version < EXCLUDED.entity_version
     RETURNING id;
     ```
   - Because $1700000010000200 < 1700000005000100$ evaluates to `FALSE`, PostgreSQL executes **0 updates** and returns `NULL`.
   - `upsert_communication_event_versioned` calls `record_dropped_stale_event("slack")`.
   - Result: `"noop:stale_slack_event:C424:1700000001.000001"`, `applied = False`.
   - Metric assertion: `dropped_stale_event_total{provider="slack"}` equals $M_0 + 1.0$ ($\Delta = +1.0$).

#### 6. Expected Preserved Database State
Execute direct SQL query against PostgreSQL:
```sql
SELECT channel_id, thread_ts, kind, is_resolved, entity_version
FROM communication_events
WHERE tenant_id = '11111111-1111-1111-1111-111111111111'
  AND channel_id = 'C424'
  AND thread_ts = '1700000001.000001';
```
**Assert Column Invariants:**
- `channel_id = 'C424'`
- `thread_ts = '1700000001.000001'`
- `kind = 'blocker_resolved'` *(preserved from E1; NOT reverted to 'blocker_open')*
- `is_resolved = true` *(preserved from E1; NOT reverted to false)*
- `entity_version = 1700000010000200` *(preserved from E1)*

#### 7. Queue & Worker Lifecycle Assertions
- Both events marked `status = 'completed'` in `webhook_events`.
- `dead_letter_events` row count remains `0`.

---

### Scenario `TC-OOO-JIRA-002`: Delayed Jira Creation Event without Changelog After Update

#### 1. Description & Context
Validates the cross-domain protection rule in [`backend/app/jira/service.py`](../backend/app/jira/service.py). An issue is already persisted in `issues` with an authoritative `changelog_id = 205`. A delayed initial `jira:issue_created` event (which has **no `changelog.id`**, only `issue.fields.updated = "2026-11-19T10:00:00.000+0000"`) arrives late. Even though its timestamp converted to epoch milliseconds ($1795082400000$) is mathematically larger than $205$, the database must **never** let a timestamp-based creation event overwrite an authoritative changelog sequence.

#### 2. Payload 3: Delayed Creation Payload (`E3`)
```json
{
  "webhookEvent": "jira:issue_created",
  "issue": {
    "id": "42401",
    "key": "MANTA-424",
    "fields": {
      "summary": "Delayed Initial Summary",
      "status": {
        "name": "To Do"
      },
      "issuetype": {
        "name": "Story"
      },
      "updated": "2026-11-19T10:00:00.000+0000"
    }
  }
}
```

#### 3. Execution & Assertion
- Precedence check: `incoming_changelog` is `None`, but `issue.changelog_id` is `205` (`IS NOT NULL`).
- SQL guard `and_(Issue.changelog_id.is_(None), Issue.entity_version < incoming_ver)` evaluates to `FALSE`.
- PostgreSQL modifies **0 rows**.
- Result: `"noop:stale_issue_version:MANTA-424"`.
- Prometheus metric: `dropped_stale_event_total{provider="jira"}` increments by $+1.0$ (cumulative $+2.0$).
- DB state: `status` remains `'Done'`, `title` remains `'Done at 205'`, `changelog_id` remains `205`.

---

### Scenario `TC-OOO-GH-002`: Terminal State Protection on `MERGED` PR

#### 1. Description & Context
Validates that once a PR transitions to terminal `state = 'MERGED'`, subsequent or delayed `synchronize` or `opened` events cannot reopen the PR, modify `head_sha`, or nullify `merge_commit_sha`.

#### 2. Payloads & Execution
1. PR is merged via payload with `action: "closed"`, `pull_request.merged: true`, `merge_commit_sha: "cccc...40"`.
2. Stored DB state: `state = 'MERGED'`, `merge_commit_sha = 'cccc...40'`, `entity_version = V_merge`.
3. Delayed `synchronize` payload arrives with `action: "synchronize"`, `head_sha: "dddd...40"`.
4. `_is_stale(pr.state, n)` returns `True`.
5. Application calls `record_dropped_stale_event("github")` and logs `STALE_OUT_OF_ORDER_REJECTED`.
6. Returns `"noop:stale_pr_event:<pr_number>"`.
7. DB assertion: `state` remains `'MERGED'`, `merge_commit_sha` remains `'cccc...40'`.
8. Metric delta: $\Delta \left( \mathtt{dropped\_stale\_event\_total}\{\mathtt{provider} = \text{"github"}\} \right) = +1.0$.

---

### Scenario `TC-OOO-SLACK-002`: Microsecond Decimal Precision Drift Defense

#### 1. Description & Context
Proves that Slack timestamps with 6 decimal places (microseconds) are parsed using `decimal.Decimal` instead of IEEE-754 `float`. In IEEE-754 binary floating-point representation, `"1731326400.123456"` suffers precision truncation:
$$\text{float}("1731326400.123456") \times 10^6 = 1731326400123455.76 \rightarrow 1731326400123455 \ (\text{error of } 1\mu\text{s})$$

#### 2. Verification Steps
1. Parse `"1731326400.123456"` via `extract_slack_event_version(P1)`.
   - Verified output: `1731326400123456` (exact integer).
2. Deliver event $E_1$ with `event_ts = "1731326400.123456"` $\rightarrow$ applied with `entity_version = 1731326400123456`.
3. Deliver event $E_2$ with `event_ts = "1731326400.123455"` (exactly 1 microsecond earlier).
   - Verified output: `1731326400123455`.
   - Comparison: $1731326400123455 < 1731326400123456 \implies$ dropped.
   - Result: 0 rows mutated; `dropped_stale_event_total{provider="slack"}` increments by $+1.0$.

---

### Scenario `TC-OOO-XTEN-001`: Multi-Tenant RLS Out-of-Order Independence

#### 1. Description & Context
Verifies that out-of-order rejection logic is strictly isolated by tenant under PostgreSQL Row-Level Security (`FORCE ROW LEVEL SECURITY`).

#### 2. Execution Flow
1. Tenant Alpha (`tid_a`) receives PR #424 at $T+10\text{m}$, then delayed $T+2\text{m}$.
2. Tenant Beta (`tid_b`) receives its own PR #424 (same external PR number!) at $T+2\text{m}$, followed by $T+10\text{m}$.
3. **Assertions:**
   - In Tenant Alpha: $T+10\text{m}$ state is preserved; $T+2\text{m}$ event is dropped ($\Delta_{\text{github}} = +1$).
   - In Tenant Beta: $T+2\text{m}$ event is accepted first; $T+10\text{m}$ event successfully updates the row ($0$ drops).
   - Neither tenant observes or affects the other tenant's `entity_version` or database rows.

---

### Scenario `TC-OOO-WORKER-001`: Worker Fetch Loop Zero-DLQ & Clean NOOP Execution

#### 1. Description & Context
Proves that when background worker processes (`run_worker_fetch_loop(..., retry_on_failure=True)`) pick up stale webhook jobs from the queue:
1. The handler returns a clean NOOP string (`"noop:stale_*"`) without raising an exception.
2. `handle_job_failure` is **never invoked**.
3. The job envelope is cleanly popped from `arq:in_flight:{worker_id}` via `ack_job()`.
4. The job is **never** enqueued into `arq:queue:delayed` (0 retries).
5. Exactly **0 rows** are written to `dead_letter_events`.
6. `dropped_stale_event_total{provider=...}` increments by $+1.0$.

---

### Scenario `TC-OOO-PROM-001`: Prometheus `/metrics` Public Exposition

#### 1. Description & Context
Verifies that the standard Prometheus exposition endpoint `GET /metrics` correctly renders the `dropped_stale_event_total` counter across all 3 providers.

#### 2. Verification Assertions
1. Send `GET /metrics` via ASGI client.
2. Assert HTTP status `200 OK`.
3. Assert response body contains:
   ```text
   dropped_stale_event_total{provider="github"}
   dropped_stale_event_total{provider="jira"}
   dropped_stale_event_total{provider="slack"}
   ```
4. Assert label cardinality is strictly restricted to `{"github", "jira", "slack"}` (calling `record_dropped_stale_event("unsupported")` raises `ValueError`).

---

## 6. Acceptance Criteria Verification Summary

| Acceptance Criteria Requirement | Implementation / Specification Detail | Verification Status |
| :--- | :--- | :---: |
| **1. Specify Payloads** | Complete, valid JSON payloads documented for: <br>• Jira `changelog.id = 205` (`Done`) and `204` (`In Progress`)<br>• GitHub PR `updated_at = T+10m` and `T+2m`<br>• Slack `event_ts = 1700000010.000200` and `1700000005.000100` | **PASS** |
| **2. Expected Preserved DB State** | Exact column-by-column invariants documented for: <br>• `issues`: `status = 'Done'`, `title = 'Done at 205'`, `changelog_id = 205`, `entity_version = 205`<br>• `pull_requests`: `title = 'Authoritative T+10m PR Title'`, `head_sha = b*40`, `entity_version = 1795083000000`<br>• `communication_events`: `is_resolved = true`, `kind = 'blocker_resolved'`, `entity_version = 1700000010000200` | **PASS** |
| **3. Expected `dropped_stale_event_total` Counter Deltas** | Exact counter increments specified:<br>• In-order / first event: $\Delta = 0$<br>• Out-of-order / stale event: $\Delta = +1.0$ per provider label (`github`, `jira`, `slack`)<br>• Zero unknown label cardinality | **PASS** |
| **4. Dependencies Satisfied** | • Pre-requisite: Blocked by Task 24.1 (`SCRUM-422`, [`docs/out-of-order-version-extraction-rules.md`](out-of-order-version-extraction-rules.md)) — **Satisfied**<br>• Enabler: Blocks Task 24.5 (`SCRUM-426`, automated pytest test suite) — **Ready for automated test mapping** | **PASS** |
