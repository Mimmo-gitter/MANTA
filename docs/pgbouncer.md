# PgBouncer Hardened Transaction Pooling & Tenant Isolation Guide

**Parent User Story:** SCRUM-245 (US-9 Pooler-safe tenant context)  
**Architecture Reference:** AI-Augmented Engineering Intelligence Platform Blueprint v3.0, §6.4 & §15.1  
**Configuration File:** [`infra/pgbouncer/pgbouncer.ini`](../infra/pgbouncer/pgbouncer.ini)

---

## 1. Overview & System Topology

The MANTA Engineering Intelligence Platform operates as a high-concurrency multi-tenant system. In accordance with Architectural Principle 2.2 ("PostgreSQL is the Sole Authoritative System of Record") and §6.4 ("PgBouncer Hardening & Connection Pooler State Leakage Prevention"), all application database traffic is routed through **PgBouncer in transaction pooling mode**.

```
  ┌──────────────────────────────────────────────────────────────┐
  │                 FastAPI Application Pods                     │
  │   SQLAlchemy 2.0 + asyncpg (statement_cache_size=0)          │
  │   SELECT set_config('app.current_tenant_id', :id, true)      │
  └──────────────────────────────┬───────────────────────────────┘
                                 │
                                 │ TCP (up to 200 client conns)
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │               PgBouncer (Transaction Pooling)                │
  │   - pool_mode = transaction                                  │
  │   - server_reset_query = DISCARD ALL                         │
  │   - server_reset_query_always = 1                            │
  │   - max_prepared_statements = 0                              │
  └──────────────────────────────┬───────────────────────────────┘
                                 │
                                 │ Reused physical conns (max 30)
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                 PostgreSQL 16+ (pgvector)                    │
  │   - Row-Level Security (FORCE ROW LEVEL SECURITY)            │
  │   - app.current_tenant_id tenant isolation                   │
  │   - Max physical connections = 100 (headroom for admin/KC)   │
  └──────────────────────────────▲───────────────────────────────┘
                                 │
                                 │ Direct connection (bypasses pooler)
  ┌──────────────────────────────┴───────────────────────────────┐
  │              Keycloak Identity Provider Pod                  │
  │   - Requires persistent session locks / Liquibase migrations │
  └──────────────────────────────────────────────────────────────┘
```

---

## 2. Tenant Safety Rationale: Why Each Setting Matters

| Configuration Directive | Setting | Why It Matters for Tenant Safety |
|---|---|---|
| `pool_mode` | `transaction` | Multiplexes hundreds of concurrent async client transactions across a small pool of physical PostgreSQL connections. A server connection is returned to the pool immediately upon `COMMIT` or `ROLLBACK`, eliminating idle connection starvation and ensuring horizontal scalability. |
| `server_reset_query` | `DISCARD ALL` | Comprehensive database reset command. Purges all session artifacts before a connection is handed to another client: resets all GUC settings (including `app.current_tenant_id`, `role`, and `search_path`), releases all advisory locks (`pg_advisory_unlock_all()`), drops temporary tables, closes open server-side cursors, deallocates prepared statements, and discards cached plans. |
| `server_reset_query_always` | `1` | **CRITICAL TENANT SAFETY INVARIANT.** By default in PgBouncer (since v1.7), `server_reset_query` is executed **only in session pooling mode** and is silently bypassed in transaction pooling mode! Setting this to `1` forces PgBouncer to run `server_reset_query` whenever a server connection is returned to the pool (i.e. at every transaction boundary), guaranteeing zero session state leakage between consecutive transactions. |
| `max_prepared_statements` | `0` | Disables PgBouncer's internal protocol-level prepared statement tracking. When `DISCARD ALL` executes at each transaction return, all backend prepared statements are deallocated; if PgBouncer attempted to track statements client-side, its cache would desynchronize with the server, causing query crashes. Prepared statements are instead disabled directly in `asyncpg` (`statement_cache_size=0`). |
| `max_client_conn` | `200` | Front-end connection ceiling. Accommodates connection bursts from multiple FastAPI worker threads and background task runners (SQLAlchemy pool 10 + overflow 20 per worker) without crashing or rejecting valid client traffic. |
| `default_pool_size` | `20` | Maximum number of physical PostgreSQL connections maintained for the `(database, user)` pool. Sized to allow high parallelism while staying well within PostgreSQL's server connection budget. |
| `min_pool_size` | `0` | Keeps zero unnecessary persistent connections open on developer workstations or in ephemeral CI runners when the system is idle. Connections are established on-demand. |
| `reserve_pool_size` | `5` | Emergency connection burst headroom activated when the default pool is exhausted and clients have waited longer than `reserve_pool_timeout`. |
| `reserve_pool_timeout` | `3` | Time in seconds a client request waits in queue before the pooler allocates reserve connections. |
| `max_db_connections` | `30` | Hard cap on total server connections across all users for the target database. Prevents PgBouncer from consuming more than 30% of PostgreSQL's total connection capacity (`max_connections = 100`). |
| `query_wait_timeout` | `30` | Fail-fast timeout for client queries waiting for an available server connection. Prevents client threads from hanging indefinitely during unexpected database saturation. |
| `server_idle_timeout` | `300` | Reclaims server connections that have remained idle for 5 minutes, preventing resource exhaustion on the database server. |
| `auth_type` | `scram-sha-256` | Enforces modern, cryptographically secure password authentication between clients and PgBouncer. |

---

## 3. The `server_reset_query_always` Mechanism & Discovery

### The Behavior Difference
In native PgBouncer documentation:
* When `server_reset_query_always = 0` (PgBouncer default):
  `server_reset_query` is **only run when a client disconnects**. In transaction pooling mode, a single client may execute dozens of transactions over one persistent TCP socket. Furthermore, server connections released after a transaction are immediately reassigned to *other* clients without disconnecting. Consequently, with `server_reset_query_always = 0`, `server_reset_query` is **never executed between transactions**.
* When `server_reset_query_always = 1`:
  PgBouncer deterministically invokes `server_reset_query` (`DISCARD ALL`) **every time a server connection is released back to the pool**, regardless of pooling mode or whether the client socket remains open.

### Concrete Failure Mode Prevented
If an application query or rogue code executes a session-level configuration:
```sql
-- Client A (Tenant Alpha) accidentally issues session-level SET instead of SET LOCAL:
SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
COMMIT;
```
1. **Without `server_reset_query_always = 1`:** The backend PostgreSQL connection retains `app.current_tenant_id = '...0001'`. When Client B (Tenant Beta) executes its next query on that reused server connection without explicitly setting its tenant ID, it operates under Tenant Alpha's context. **This is a catastrophic multi-tenant security vulnerability.**
2. **With `server_reset_query_always = 1`:** Upon transaction completion, PgBouncer automatically sends `DISCARD ALL` to the PostgreSQL server. The GUC `app.current_tenant_id` is wiped. When Client B receives that connection, `app.current_tenant_id` is empty/NULL, and PostgreSQL RLS blocks unauthorized record access.

---

## 4. Application-Side Invariants (`backend/app/db/session.py`)

To ensure flawless interoperability with PgBouncer transaction pooling, the backend strictly enforces two application-level invariants:

### 1. Statement Cache Disabling
Because physical connections are shared across arbitrary queries, `asyncpg` must not cache prepared statements on physical connections:
```python
async_engine = create_async_engine(
    DATABASE_URL,
    connect_args={
        "statement_cache_size": 0,  # Disable asyncpg server-side statement cache
        "prepared_statement_cache_size": 0,  # Disable asyncpg client-side prepared statement cache
    },
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
```

### 2. Transaction-Local Tenant Context (`set_config(..., is_local=true)`)
All tenant-scoped operations must use `set_config(..., is_local=true)` (the parameterized, injection-safe SQL equivalent of `SET LOCAL`), which automatically expires upon transaction termination (PostgreSQL's `SET` utility command does not accept bind parameters `$1`):
```python
@asynccontextmanager
async def get_tenant_db_context(tenant_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield session
```
`DISCARD ALL` via `server_reset_query_always = 1` serves as the authoritative fail-safe defense-in-depth layer if an unexpected crash or raw query bypasses transaction-local scoping.

---

## 5. Verification & Testing

### 1. Static Configuration Verification (CI & Local)
Run the automated configuration test suite to ensure the repository configuration invariants remain intact:
```bash
python -m pytest backend/tests/test_pgbouncer_config.py -v
```

### 2. Live Pooler Isolation Test
Start the local stack and execute the live tenant-isolation test suite:
```bash
# 1. Start Postgres and PgBouncer
docker compose -f backend/docker-compose.yml up -d --wait postgres pgbouncer

# 2. Run live isolation tests against PgBouncer port 6432
PGBOUNCER_TEST_DSN="postgresql://manta_user:manta_password@localhost:6432/manta_db" \
  python -m pytest backend/tests/test_pgbouncer_isolation.py -v
```

### 3. Inspect Runtime PgBouncer Configuration
Verify active settings directly via the PgBouncer administrative console:
```bash
docker exec -it manta_pgbouncer psql -h localhost -p 5432 -U manta_user pgbouncer -c "SHOW CONFIG;"
```
Confirm the following output rows:
- `pool_mode` $\rightarrow$ `transaction`
- `server_reset_query` $\rightarrow$ `DISCARD ALL`
- `server_reset_query_always` $\rightarrow$ `1`
- `max_prepared_statements` $\rightarrow$ `0`

### 4. High-Concurrency Multi-Tenant Load Test (1,000 Requests)
Executes 1,000 concurrent requests strictly alternating between Tenant A and Tenant B to stress connection multiplexing across physical PostgreSQL connections:
```bash
# Execute standalone load test runner
docker exec manta_app python scripts/run_tenant_load_test.py

# Or run via automated pytest suite
docker exec manta_app pytest tests/test_pgbouncer_load.py -o asyncio_mode=auto -v
```
Structured results and latency percentiles are automatically saved to `backend/reports/load_test_results.json`.
