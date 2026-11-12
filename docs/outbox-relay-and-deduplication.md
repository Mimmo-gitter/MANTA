# Outbox Relay Polling Protocol, RFC 9562 UUIDv7 Generation, Redis Stream Format & Two-Tier Consumer Deduplication

**Task:** `Task 33.1` (Design application-side UUIDv7 monotonic generator, `XADD` stream payload format, and consumer deduplication ledger)  
**Parent User Story:** `US-33` (Reliable Outbox Relay & Idempotent Stream Consumption)  
**Assignee:** Dmytro Hontarenko  
**Authoritative References:**
- **M.A.N.T.A. SRS v1.0 (01.10.2026)** (§2.4.1 Strict Multi-Tenant Isolation & RLS, §2.4.3 Performance SLOs `< 30s` p95 event processing & `< 2s` p95 live alert delivery, §3.1.3 PostgreSQL System of Record, §3.1.4 & §3.2.6.3 Redis Streams Event Bus)
- **Architectural Blueprint v3.0** (§4.1, §5.1, §5.1.1, §5.3) — *Note: Where Blueprint v3.0 and SRS v1.0 / Task 33.x differ, SRS v1.0 and this specification take precedence.*
- **Related Predecessor Spec:** [`docs/transactional-outbox-pattern.md`](./transactional-outbox-pattern.md) (`SCRUM-475` / `Task 32.1`)

---

## 1. Purpose & Scope

This document specifies the end-to-end protocol connecting committed PostgreSQL `outbox_events` rows (`Task 32.2` / `Task 32.3`) to downstream consumer execution with **at-least-once stream transport** and **effectively-once consumer side effects**:

1. **Application-side RFC 9562 UUIDv7 monotonic identifier generation** (`backend/app/core/uuidv7.py`) assigned once at `OutboxEvent` creation.
2. **Concurrent, RLS-compliant Outbox Relay polling** (`backend/app/outbox/relay.py`) using `ORDER BY id ASC LIMIT 100 FOR UPDATE SKIP LOCKED`.
3. **Per-tenant Redis / Valkey Stream publication** (`XADD tenant_stream:{tenant_id} MAXLEN ~ 10000`) and crash recovery between `XADD` and `published_at` commit.
4. **Two-tier stream consumer idempotency filtering** (`backend/app/outbox/consumer.py`):
   - **Tier 1 (Redis fast path):** `outbox:dedup:{tenant_id}:{event_id}` (`7-day` TTL / `EX 604800`).
   - **Tier 2 (PostgreSQL durable ledger):** `processed_outbox_events` (`INSERT ... ON CONFLICT (event_id) DO NOTHING`, `30-day` retention + purge helper).

*Out of Scope:* WebSocket hub `REPLAY_GAP` fan-out (`Task 33.4`), full end-to-end WebSocket load verification (`Task 33.5`), and domain-specific consumers (DORA / Refactoring).

---

## 2. Application-Side RFC 9562 UUIDv7 Monotonic Generator

### 2.1 Why Application-Side & Why Python 3.12 Compatible
- `OutboxEvent.id` MUST be assigned when the `OutboxEvent` object is staged (inside the caller's PostgreSQL transaction), **before** `session.commit()` and before the Outbox Relay ever reads it. Because the row retains this immutable `UUIDv7` primary key across restarts, any re-publication after a relay crash re-emits the **exact same `event_id`**.
- CI pipelines execute under **Python 3.12** (`.github/workflows/ci.yml`), whereas `uuid.uuid7()` is only present in Python 3.14+ stdlib. Therefore `generate_uuidv7() -> uuid.UUID` in `backend/app/core/uuidv7.py` uses pure Python 3.12 standard library primitives (`time.time_ns`, `secrets`, `threading.Lock`, `uuid.UUID`).

### 2.2 128-Bit RFC 9562 Layout (`ver = 0b0111`, `var = 0b10`)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           unix_ts_ms                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          unix_ts_ms           |  ver  |       rand_a          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|var|                        rand_b                             |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                            rand_b                             |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Bits `127..80` (48 bits) — `unix_ts_ms`:** Unsigned big-endian integer milliseconds since Unix epoch (`time.time_ns() // 1_000_000`).
- **Bits `79..76` (4 bits) — `ver`:** Fixed `0b0111` (`7`).
- **Bits `75..64` (12 bits) — `rand_a` (Monotonic Counter — RFC 9562 §6.2 Method 1):**
  - On millisecond advance (`now_ms > _last_ts_ms`): `_last_ts_ms = now_ms` and `_last_seq = secrets.randbits(11)`. Leaving bit `11` of the 12-bit field cleared guarantees at least `2048` strict increments within the same millisecond before rollover.
  - On sub-millisecond calls (`now_ms == _last_ts_ms`): `_last_seq += 1`.
  - On clock regression (`now_ms < _last_ts_ms`, e.g., NTP backward step): preserve `_last_ts_ms` and increment `_last_seq += 1` rather than regressing timestamps.
  - On 12-bit counter overflow (`_last_seq > 0xFFF`, >2048–4095 IDs within one ms): advance `_last_ts_ms += 1` and re-seed `_last_seq = secrets.randbits(11)`.
- **Bits `63..62` (2 bits) — `var`:** Fixed `0b10` (`uuid.RFC_4122`).
- **Bits `61..0` (62 bits) — `rand_b`:** Cryptographically random bits via `secrets.randbits(62)`.

All state updates are serialized under a module-level `threading.Lock`, ensuring strict process-local total ordering: for any sequential calls `u1 = generate_uuidv7()`, `u2 = generate_uuidv7()`, both `u1.int < u2.int` and `str(u1) < str(u2)` hold unconditionally.

### 2.3 `OutboxEvent` Model Binding
- `OutboxEvent.id` declares `default=generate_uuidv7`.
- `app/outbox/service.py::record_outbox_event()` calls `generate_uuidv7()` so `payload["event_id"]` and `OutboxEvent.id` always match identically.

---

## 3. Outbox Relay Polling Protocol & RLS Compliance

### 3.1 PostgreSQL Partial Index & Query Shape
Task 33.2 requires polling strictly in monotonic `UUIDv7` order:

```sql
SELECT id, tenant_id, aggregate_type, aggregate_id, event_type, payload, created_at, published_at
FROM outbox_events
WHERE published_at IS NULL
ORDER BY id ASC
LIMIT 100
FOR UPDATE SKIP LOCKED;
```

- **Partial Index (`ix_outbox_events_unpublished_id`):**
  ```sql
  CREATE INDEX ix_outbox_events_unpublished_id
  ON outbox_events (id ASC)
  WHERE published_at IS NULL;
  ```
  - Keeps index scans bounded solely to unpublished backlog rows regardless of historical `outbox_events` table size (`ORDER BY id ASC` matches index order without an external sort).
  - Existing index `ix_outbox_events_unpublished (created_at) WHERE published_at IS NULL` is retained for backwards compatibility with existing `Task 32.x` tests (`TC-OUTBOX-006`).
- **Concurrency Safety (`FOR UPDATE SKIP LOCKED`):** Multiple horizontally scaled `OutboxRelayWorker` instances poll disjoint batches simultaneously without blocking each other or blocking OLTP writers inserting new `outbox_events` rows.

### 3.2 Strict Row-Level Security (RLS) Compliance under `eng_app_user`
Per SRS §2.4.1 / §3.2.1.3, the backend connects exclusively as `eng_app_user` (`NOSUPERUSER`, `NOBYPASSRLS`), and `outbox_events` enforces `FORCE ROW LEVEL SECURITY` (`tenant_id = current_setting('app.current_tenant_id', true)::uuid`). Issuing `SELECT ... FROM outbox_events` without setting `app.current_tenant_id` returns `0` rows.

Following MANTA's established `recovery_sweeper` pattern (`a9c5e1d7b3f2`), `OutboxRelayWorker` preserves strict RLS isolation via a two-step protocol:
1. **Cross-Tenant Discovery (`SECURITY DEFINER` RPC):**
   Call `list_tenants_with_unpublished_outbox_events(p_limit INT DEFAULT 100)` (`SECURITY DEFINER`, `SET search_path = public, pg_temp`, `EXECUTE` granted solely to `eng_app_user`), returning distinct `tenant_id`s with `published_at IS NULL` ordered by earliest `MIN(id::text) ASC`.
2. **Per-Tenant RLS-Scoped Batch Relay:**
   For each discovered `tenant_id`, enter `async with get_tenant_db_context(tenant_id) as session:` (`SELECT set_config('app.current_tenant_id', :tenant_id, true)`), execute the `ORDER BY id ASC LIMIT 100 FOR UPDATE SKIP LOCKED` query, publish rows via `XADD`, update `row.published_at = datetime.now(timezone.utc)`, and commit the transaction. Because Redis Streams are already partitioned per tenant (`tenant_stream:{tenant_id}`), per-tenant polling preserves strict per-stream UUIDv7 ordering with zero RLS bypass on event payloads.

---

## 4. Redis / Valkey Stream `XADD` Payload Specification

Each claimed row is appended to a dedicated per-tenant stream key:

```
XADD tenant_stream:{tenant_id} MAXLEN ~ 10000 *
    event_id    "<str(row.id)>"
    event_type  "<row.event_type>"
    tenant_id   "<str(row.tenant_id)>"
    payload     "<json.dumps(row.payload, separators=(',', ':'), sort_keys=True)>"
    occurred_at "<ISO-8601 UTC timestamp>"
```

### Field Contract
| Field | Type (Redis String) | Source | Notes |
| :--- | :--- | :--- | :--- |
| `event_id` | UUIDv7 string | `str(row.id)` | Immutable primary key from `outbox_events`; preserved identically across relay retries. |
| `event_type` | String (`<= 64` chars) | `row.event_type` | E.g., `deployment.failed`, `dora.updated`. |
| `tenant_id` | UUID string | `str(row.tenant_id)` | Redundant envelope check against `tenant_stream:{tenant_id}` key. |
| `payload` | Compact JSON string | `row.payload` | Canonical JSON envelope from `outbox_events.payload`. |
| `occurred_at` | ISO-8601 UTC string | `row.payload["occurred_at"]` or `row.created_at` | Preserves original domain occurrence timestamp. |

- **Approximate Stream Trimming (`MAXLEN ~ 10000`):** Supersedes Blueprint v3.0's older `~ 5000` estimate per Task 33.1, giving a ~10,000-entry rolling replay buffer per tenant in Valkey 7 (`noeviction`) before `REPLAY_GAP` fallback (SRS §3.2.6.3) is needed.

---

## 5. Relay Crash Recovery Between `XADD` and `published_at` Commit

| Failure Point | Database State | Redis Stream State | Recovery Behavior |
| :--- | :--- | :--- | :--- |
| **Crash before `XADD`** | Transaction uncommitted; `FOR UPDATE` lock released on disconnect; `published_at IS NULL`. | No entry written. | Next relay poll claims the row with the same `id` (`UUIDv7`), runs `XADD`, and commits `published_at`. |
| **Crash after `XADD` and before `COMMIT`** | PostgreSQL rolls back uncommitted transaction; `published_at` remains **`NULL`**; original **`id` (`UUIDv7`) is unchanged**. | Stream contains entry `S_1` carrying `event_id = str(row.id)`. | Restarted relay re-polls the same row (`published_at IS NULL`), appends entry `S_2` via `XADD` with the **exact same `event_id`**, updates `published_at`, and commits. Consumer deduplication suppresses `S_2`. |
| **Transient Redis failure mid-batch (after `k` of `N` rows `XADD`-ed)** | Rows `0..k-1` have `published_at = now` updated and committed; rows `k..N-1` remain `published_at IS NULL` after rollback/release. | Entries published for `0..k-1`; none for `k..N-1`. | Next relay poll resumes cleanly from row `k` in monotonic UUIDv7 order. |

---

## 6. Two-Tier Consumer Deduplication Protocol (`Task 33.3`)

### 6.1 Reconciled `processed_outbox_events` Table Schema & RLS Fix
Earlier migration `02ec69277327` created `(outbox_event_id, consumer_name, processed_at)` without `tenant_id`, and `f1b8a4c9d7e2` enabled `FORCE ROW LEVEL SECURITY` on `processed_outbox_events` with **no RLS policy** (default-deny for `eng_app_user`). Task 33.3 reconciles the table to match SRS §2.4.1 and Task 33.1/33.3 SQL:

```sql
-- Reconciled schema
processed_outbox_events (
    event_id     UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Indexes:
--   idx_processed_outbox_events_tenant_id (tenant_id)
--   idx_processed_outbox_events_processed_at (processed_at)
-- RLS Policy:
--   tenant_isolation_policy FOR ALL TO eng_app_user
--   USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
--   WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
```

### 6.2 Safe Execution Order (Zero `SIGKILL` Data-Loss Window)
Setting the Redis dedup key (`SET NX`) *before* committing the database transaction creates a crash window: if the worker dies via `SIGKILL` after `SET NX` and before PostgreSQL commit, a retry would see the Redis key and skip/acknowledge an unprocessed event. To eliminate this window while preserving `O(1)` fast-path suppression:

1. **Tier 1 Fast Path Check (Redis `EXISTS`):**
   - Key: `outbox:dedup:{tenant_id}:{event_id}` (superseding Blueprint v3.0's older `idempotency:outbox:...` naming per Tasks 33.1 & 33.3).
   - If key exists -> increment duplicate skip metric (`tier="redis"`), emit structured duplicate skip log, execute `XACK` on the stream entry, and return **without** opening PostgreSQL or invoking downstream handlers.
2. **Tier 2 Durable Ledger + Handler Execution (Single Tenant-Scoped PG Transaction):**
   - Open `async with get_tenant_db_context(tenant_id) as session:`.
   - Execute:
     ```sql
     INSERT INTO processed_outbox_events (event_id, tenant_id, processed_at)
     VALUES (:event_id, :tenant_id, now())
     ON CONFLICT (event_id) DO NOTHING
     RETURNING event_id;
     ```
   - **If `RETURNING` is `None` (conflict — already processed within 30-day window):**
     - Warm Tier 1 cache: `SET outbox:dedup:{tenant_id}:{event_id} 1 NX EX 604800`.
     - Increment duplicate skip metric (`tier="postgres"`), log duplicate skip, execute `XACK`, and skip downstream handler.
   - **If `RETURNING` yields `event_id` (first processing):**
     - Under PostgreSQL unique index locking, concurrent duplicate deliveries block until this transaction finishes; if it commits, waiting transactions observe conflict and skip.
     - Execute downstream handler inside the **same `AsyncSession` transaction**.
     - If handler raises -> transaction rolls back both `processed_outbox_events` and handler mutations; Tier-1 Redis key is **not** set and `XACK` is **not** called, leaving the message in Redis Streams PEL for retry.
     - On handler success -> commit PostgreSQL transaction.
3. **Post-Commit Tier-1 Cache Population & Stream Acknowledgement:**
   - Execute `SET outbox:dedup:{tenant_id}:{event_id} 1 NX EX 604800` (7-day TTL = `604,800` seconds).
   - Execute `XACK tenant_stream:{tenant_id} <group_name> <entry_id>`.

### 6.3 30-Day Ledger Retention & RLS-Safe Cleanup
- Rows older than `30` days (`processed_at < now() - INTERVAL '30 days'`) are pruned in bounded batches (`LIMIT 5000` ordered by `processed_at ASC` using `idx_processed_outbox_events_processed_at`) to avoid vacuum/lock spikes.
- Cross-tenant purge uses a `SECURITY DEFINER` discovery RPC `list_tenants_with_expired_processed_outbox_events(p_cutoff, p_limit)` + per-tenant `get_tenant_db_context(tenant_id)` deletion (`purge_expired_processed_outbox_events`).
