# Canonical Provider Version Extraction & Out-of-Order Guard Specification

> **Jira Reference:** `US-24` / `SCRUM-422` (`Task 24.1`) & `SCRUM-423` (`Task 24.2`)  
> **Target Module:** `backend/app/webhooks/versioning.py`  
> **Domain Entities Protected:** `pull_requests`, `issues`, `communication_events`

---

## 1. Objective

Webhook deliveries from external providers (GitHub, Jira, Slack) arrive over asynchronous networks and can be delayed, retried, or reordered (e.g., an older `pull_request` `synchronize` or `opened` event arriving after a newer `synchronize` or `closed` event).

To guarantee deterministic state convergence under out-of-order delivery without global locks, every mutable webhook event extracts a canonical, monotonically increasing `entity_version` (`BIGINT`) and enforces conditional PostgreSQL mutations:

```sql
UPDATE <table>
SET ...,
    entity_version = :incoming_version,
    updated_at = now()
WHERE id = :record_id
  AND tenant_id = :tenant_id
  AND entity_version < :incoming_version
RETURNING id;
```

Or for upserts:

```sql
INSERT INTO <table> (..., entity_version)
VALUES (..., :incoming_version)
ON CONFLICT (<natural_key>) DO UPDATE
SET ...,
    entity_version = EXCLUDED.entity_version
WHERE <table>.entity_version < EXCLUDED.entity_version
RETURNING id;
```

If an older webhook arrives (`incoming_version <= stored_entity_version`), PostgreSQL modifies `0 rows` and leaves the newer persisted state completely intact.

---

## 2. Canonical Version Extraction Rules by Provider

| Provider | Target Table | Primary Extraction Source | Fallback Source (When Primary Absent) | Target Unit (`BigInteger`) |
| :--- | :--- | :--- | :--- | :--- |
| **GitHub** | `pull_requests` | `pull_request.updated_at` (ISO-8601) | `pull_request.merged_at` -> `closed_at` -> `created_at` (ISO-8601) -> explicit `entity_version` | **UTC Epoch Milliseconds** (`int(dt.timestamp() * 1000)`) |
| **Jira** | `issues` | `changelog.id` (numeric sequence ID) | `issue.fields.updated` -> `payload.timestamp` -> `issue.fields.created` | **Integer Sequence ID** or **UTC Epoch Milliseconds** |
| **Slack** | `communication_events` | `event.event_ts` / `payload.event_ts` | `event.ts` -> `event.thread_ts` -> `event_time` (seconds) | **UTC Epoch Microseconds** (`int(Decimal(ts) * 1_000_000)`) |

---

## 3. Edge Cases & Precision Guarantees

### 3.1 GitHub (`pull_requests`)
- **Timestamp Parsing:** ISO-8601 timestamps ending with `Z` or explicit offsets (e.g., `2026-11-19T12:30:45.123Z` or `+02:00`) are normalized to UTC and converted to **epoch milliseconds** via `Decimal` / `round()` truncation (`int(dt.timestamp() * 1000)`).
- **Initial Creation vs Subsequent Update:**
  - On `opened` creation: `entity_version` is initialized to the extracted `pull_request.updated_at` (or `created_at`) epoch ms (defaulting to `1` only if no timestamp exists in synthetic payloads).
  - On subsequent updates (`synchronize`, `closed`, `reopened`, `edited`): `entity_version` must be strictly greater (`entity_version < :incoming_version`) to mutate the PR.
  - Note: Terminal `MERGED` PRs also preserve terminal state protection so a delayed `synchronize` or `opened` event with a lower or missing version cannot overwrite a merged PR.

### 3.2 Jira (`issues`)
- **Primary (`changelog.id`):** Jira's `changelog.id` is an authoritative per-instance monotonic sequence ID (`int`). When present and strictly positive, it maps directly to `entity_version` and `issues.changelog_id`.
- **Fallback (`issue.fields.updated`):** When `changelog.id` is absent (such as `jira:issue_created` initial payloads or metadata syncs without changelog), `issue.fields.updated` (or `payload.timestamp`) is parsed to **UTC epoch milliseconds**.
- **Created vs Changelog Comparison Safety:** When an existing record was initially created without a `changelog.id` (using `fields.updated` epoch ms or baseline `1`) and later receives its first changelog event (`changelog.id`), both `issues.changelog_id` precedence (`existing.changelog_id IS NULL OR incoming_changelog > existing.changelog_id`) and `entity_version` conditional SQL guard work consistently: if transitioning from timestamp-seeded creation (`changelog_id IS NULL`) to the first changelog event, the SQL predicate checks `(issues.changelog_id IS NULL OR issues.entity_version < :incoming_version)` when the incoming event carries a real `changelog_id`, while strictly enforcing `issues.entity_version < :incoming_version` for all same-domain or subsequent updates.

### 3.3 Slack (`communication_events`)
- **Sub-second Precision (`Decimal`, NOT `float`):** Slack timestamps are formatted as string seconds with 6 fractional microsecond digits, e.g. `"1731326400.123456"`. Converting via binary IEEE-754 `float` introduces precision rounding drift (`1731326400123455` vs `1731326400123456`).
- **Exact Conversion:** `slack_ts` is parsed using `decimal.Decimal(str(raw_ts).strip())`, multiplied by `Decimal("1000000")`, and converted to an exact 64-bit `int` (**UTC epoch microseconds**).
- **Idempotency & Thread Ordering:** For `communication_events`, the natural unique key is `(project_id, channel_id, thread_ts)`. Thread updates (`reply_broadcast`, `message_changed`, resolution updates) carry increasing `event_ts`; `ON CONFLICT (project_id, channel_id, thread_ts) DO UPDATE ... WHERE communication_events.entity_version < EXCLUDED.entity_version` ensures late-arriving older Slack deliveries never overwrite newer resolution states or response delays.
