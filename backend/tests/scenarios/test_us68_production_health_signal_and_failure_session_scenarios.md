# US-68: Production Health Signal Ingestion, Failure Session State Machine & Authentication Test Scenarios

**Task:** `SCRUM-454` (`Task 68.5`: Create test scenarios for fail -> pass cycle, duplicate failing signals, orphan pass and invalid HMAC rejection)  
**Parent User Story:** `SCRUM-367` (`US-68`: Production health signal ingestion and failure session tracking)  
**Related Tickets:**  
- `SCRUM-450`: Design production health failure session schema, state machine, and interval pairing contracts (`Task 68.1`)  
- `SCRUM-451`: Storage architecture and migration design for `production_failures` (`Task 68.2`)  
- `SCRUM-452`: Implement PostgreSQL schema migration for `production_failures`, health config columns, and SECURITY DEFINER webhook routing (`Task 68.3`, [`backend/migrations/versions/e4a9c2f7b1d6_production_failures_and_project_health_config.py`](../../migrations/versions/e4a9c2f7b1d6_production_failures_and_project_health_config.py))  
- `SCRUM-453`: Implement production health signal ingestion controller, HMAC-SHA256 / token authentication, and failure session state machine (`Task 68.4`, [`backend/app/projects/health_service.py`](../../app/projects/health_service.py), [`backend/app/projects/router.py`](../../app/projects/router.py), [`backend/app/webhooks/router.py`](../../app/webhooks/router.py))  
- `SCRUM-454`: Create test scenarios for fail -> pass cycle, duplicate failing signals, orphan pass and invalid HMAC rejection (`Task 68.5` — THIS DOCUMENT)  
- `SCRUM-455`: Develop project health status UI indicators and health probe webhook secret management UI (`Task 68.6`)  
- `SCRUM-456`: Automated pytest integration test suite for production health signal ingestion, auth, and state machine (`Task 68.7`, [`backend/tests/test_health_signal_ingestion.py`](../test_health_signal_ingestion.py))  
- `SCRUM-446` / `SCRUM-448`: Mean Time to Recovery (MTTR) calculation engine based strictly on resolved failure sessions (`US-29` / `Task 29.1` / `Task 29.3`, [`docs/dora-mttr-calculation-test-scenarios.md`](../../../docs/dora-mttr-calculation-test-scenarios.md))  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:**  
- [`backend/app/projects/health_service.py`](../../app/projects/health_service.py)  
- [`backend/app/projects/router.py`](../../app/projects/router.py)  
- [`backend/app/projects/schemas.py`](../../app/projects/schemas.py)  
- [`backend/app/webhooks/router.py`](../../app/webhooks/router.py)  
- [`backend/app/db/models/models.py`](../../app/db/models/models.py)  
**Authoritative Automated Test Suite:** [`backend/tests/test_health_signal_ingestion.py`](../test_health_signal_ingestion.py)  
**Specification Location in Documentation:** [`docs/production-health-signal-ingestion-and-failure-session-test-scenarios.md`](../../../docs/production-health-signal-ingestion-and-failure-session-test-scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Establish the formal, authoritative verification scenarios and deterministic test fixtures for **production health signal ingestion, webhook authentication, cross-tenant isolation, and the idempotent production failure session state machine** in the M.A.N.T.A. Engineering Intelligence Platform (`SCRUM-454` / `Task 68.5`).

This specification establishes end-to-end mathematical, cryptographic, relational, and HTTP protocol contracts governing:
1. Automated health probe failure and recovery intake across dual HTTP endpoints.
2. Constant-time cryptographic HMAC-SHA256 signature verification and shared token authentication.
3. Strict cross-tenant isolation and anti-enumeration under PostgreSQL 16 `FORCE ROW LEVEL SECURITY`.
4. The idempotent failure session lifecycle (`FAIL` opens, duplicate `FAIL` preserves `started_at`, `PASS` resolves with exact recovery interval math, and orphan `PASS` produces a zero-row no-op).
5. Synchronous dual-table replication to `incidents` ensuring downstream compatibility with DORA Mean Time to Recovery (MTTR, `US-29`).

### 1.2 Implementation Scope & Core Scenarios

In accordance with Jira Task `SCRUM-454`, this specification explicitly formalizes and verifies:

1. **The Canonical Failure-to-Restoration Cycle (`FAIL` at `10:00` $\rightarrow$ duplicate `FAIL` at `10:05` $\rightarrow$ `PASS` at `11:48` = `6,480.0s` / `1.80h`):**
   - **Step 1 (`10:00:00 UTC`):** Ingestion of initial `FAIL` signal opens a new failure session in `production_failures` (`status = 'OPEN'`, `started_at = 10:00:00Z`, `resolved_at = NULL`), synchronizes an open row in `incidents`, and updates `projects.health_status = 'FAILING'`. Returns HTTP 200 with `action = "opened"`.
   - **Step 2 (`10:05:00 UTC`):** Ingestion of duplicate `FAIL` signal while an open session already exists. Enforces idempotency by preserving the original `started_at` (`10:00:00Z`) unchanged and inserting 0 extra rows. Returns HTTP 200 with `action = "duplicate_fail_ignored"`, `duplicate = true`, and `noop = true`.
   - **Step 3 (`11:48:00 UTC`):** Ingestion of `PASS` signal resolves the open session (`status = 'RESOLVED'`, `resolved_at = 11:48:00Z`, `recovery_time_seconds = 6480.00`), updates `incidents`, and transitions `projects.health_status = 'HEALTHY'`. Returns HTTP 200 with `action = "resolved"`.
   - **Exact Interval Math:**
     $$\Delta t = 11:48:00 - 10:00:00 = 1\text{ hour } 48\text{ minutes } 00\text{ seconds} = 108\text{ minutes} = 6{,}480.0\text{ seconds} = \mathbf{1.80\text{ hours}}$$
2. **Duplicate Failing Signals (Deep Idempotency & Storage Layer Invariant):**
   - Rapid-fire repetitive `FAIL` signals from automated monitoring agents (Prometheus Alertmanager, Datadog, AWS CloudWatch, synthetic uptime probes).
   - Invariant: `started_at` is strictly monotonic and immutable once established.
   - Storage-level defense: Partial unique index `uq_production_failures_one_open_per_project` on `production_failures (tenant_id, project_id) WHERE status = 'OPEN'` guarantees at most one active outage session per project, rejecting concurrent duplicate inserts with PostgreSQL error `23505`.
3. **Orphan `PASS` Signal When Healthy (Zero-Row Creation No-Op):**
   - A `PASS` health signal received when the project is already healthy and no active failure session exists (`status = 'OPEN'`).
   - Expected behavior: Clean no-op. Returns HTTP 200 with `action = "orphan_pass_ignored"`, `orphan = true`, `noop = true`, `session_id = null`.
   - Expected database state: 0 rows inserted into `production_failures`, 0 rows inserted into `incidents`, `projects.health_status` remains `'HEALTHY'`.
   - Subsequent `PASS` signal received after outage resolution also acts as an orphan no-op, preserving the immutable resolved session.
4. **Invalid HMAC Signature Rejection (`HTTP 401 Unauthorized`):**
   - Dual intake endpoints:
     - `POST /api/v1/projects/{project_id}/health-signals`
     - `POST /api/v1/webhooks/projects/{project_id}/health`
   - Rejection conditions returning `HTTP 401 Unauthorized` with `WWW-Authenticate: WebhookSignature`:
     - Missing signature and token headers entirely.
     - Tampered payload bytes (even a single flipped bit) invalidating the HMAC-SHA256 digest.
     - Signature computed using an incorrect or expired webhook secret.
     - Malformed signature headers (non-hex characters, invalid prefixes).
   - Security invariants: Constant-time comparison via `hmac.compare_digest` to prevent timing side-channel attacks; payload parsing is deferred until cryptographic authenticity is proven.
5. **Cross-Tenant Isolation & Anti-Enumeration Security:**
   - Enforces strict tenant separation under PostgreSQL 16 `FORCE ROW LEVEL SECURITY`.
   - Rejects cross-tenant `X-Tenant-ID` header mismatch with `HTTP 403 Forbidden`.
   - Rejects mismatched payload tenant ID (`payload.tenant_id != project.tenant_id`) with `HTTP 403 Forbidden`.
   - In JWT-authenticated mode, calls targeting foreign tenant project IDs return `HTTP 404 Not Found` to prevent project existence enumeration.
   - Cross-tenant database queries executed under `eng_app_user` with `app.current_tenant_id` return 0 rows.

---

## 2. Core Architectural Invariants, State Machine & Cryptographic Contracts

```
+-------------------------------------------------------------------------------------------------------------------+
|                         PRODUCTION HEALTH SIGNAL INGESTION & STATE MACHINE ARCHITECTURE                           |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Dual Ingestion Endpoints         | 2. Cryptographic Authentication     | 3. Security Definer Routing Lookup    |
|    (REST & Webhook Flavors)         |    (Constant-Time HMAC-SHA256)      |    (Pre-RLS Tenant Resolution)        |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * /projects/{id}/health-signals     | * HMAC-SHA256 (X-Hub-Signature-256) | * get_project_health_webhook_route()  |
| * /webhooks/projects/{id}/health    | * Shared secret (X-Webhook-Secret)  | * Resolves tenant_id & secret hash    |
| * Identical state machine execution | * OIDC RS256 JWT (TENANT_ADMIN)     | * Prior to SET app.current_tenant_id  |
| * Raw byte auth before JSON parsing | * hmac.compare_digest byte compare  | * Validates probe_enabled & is_active |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                      IDEMPOTENT FAILURE SESSION STATE MACHINE (production_failures)                               |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Failure Session State Transitions               | 5. Storage Layer Guarantees & DORA MTTR Alignment            |
+----------------------------------------------------+--------------------------------------------------------------+
| * FAIL + HEALTHY -> OPEN session (started_at=10:00)| * Partial Unique Index: 1 OPEN session per project           |
| * FAIL + OPEN -> Duplicate ignored (started_at=10:00)| * Check Constraint: resolved_at >= started_at                |
| * PASS + OPEN -> RESOLVED (resolved_at=11:48, 1.8h)| * Synchronous Incident table replication                     |
| * PASS + HEALTHY -> Orphan ignored (0 rows inserted)| * DORA MTTR engine consumes exact duration (6480.0s / 1.80h) |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Idempotent State Machine Transitions

The production failure session state machine transitions deterministically according to the current project health status and incoming signal:

$$\text{Transition}(S_{\text{current}}, \sigma) = \begin{cases}
(\text{OPEN}, \text{"opened"}), & \text{if } \sigma = \text{FAIL} \land S_{\text{current}} \ne \text{OPEN} \\
(\text{OPEN}, \text{"duplicate\_fail\_ignored"}), & \text{if } \sigma = \text{FAIL} \land S_{\text{current}} = \text{OPEN} \\
(\text{RESOLVED}, \text{"resolved"}), & \text{if } \sigma = \text{PASS} \land S_{\text{current}} = \text{OPEN} \\
(\text{HEALTHY}, \text{"orphan\_pass\_ignored"}), & \text{if } \sigma = \text{PASS} \land S_{\text{current}} \ne \text{OPEN}
\end{cases}$$

- **Preservation of Detection Baseline:** When a duplicate `FAIL` arrives at $t_{\text{dup}} > t_{\text{initial}}$, the active session's `started_at` remains pinned to $t_{\text{initial}}$. Under no circumstances may a duplicate signal bump or overwrite the initial failure timestamp.
- **Exact Recovery Duration:** Upon resolution at $t_{\text{pass}}$, the restoration duration is computed with sub-second precision and rounded to two decimal places:
  $$\Delta t = \text{round}_2\!\left(t_{\text{pass}} - t_{\text{initial}}\right) \ge 0.00$$
- **Clean Orphan Handling:** An orphan `PASS` creates 0 records in `production_failures` and 0 records in `incidents`, returning an HTTP 200 payload with `noop: true` and `orphan: true`.

### Invariant 2: Cryptographic HMAC-SHA256 Verification & Timing Attack Immunity

1. **Signature Computation:** Webhook callers sign the raw UTF-8 HTTP request body using HMAC-SHA256 with the project's configured secret $K$:
   $$\text{MAC} = \text{HMAC-SHA256}(K, \text{raw\_body})$$
   $$\text{Expected Header} = \text{"sha256="} \parallel \text{hex}(\text{MAC}) \quad \lor \quad \text{hex}(\text{MAC})$$
2. **Supported Signature Headers:** The ingestion gateway checks the following header aliases case-insensitively:
   - `X-Hub-Signature-256` (GitHub / Standard Webhook convention)
   - `X-Webhook-Signature`
   - `X-Health-Signature`
   - `X-Health-Signature-256`
   - `X-Signature`
   - `X-Hub-Signature`
3. **Constant-Time Comparison:** Signature comparison is executed strictly via `hmac.compare_digest` over ASCII/UTF-8 bytes. String equality operators (`==`) are strictly forbidden to eliminate timing side-channel vulnerabilities.
4. **Fail-Closed Payload Evaluation:** The raw request body is never passed to `json.loads` or Pydantic parsers until cryptographic authentication succeeds. An invalid signature immediately terminates processing with `HTTP 401 Unauthorized`, mitigating remote Denial of Service (DoS) attacks targeting JSON deserialization.

### Invariant 3: Shared Secret Token & OIDC JWT RBAC Authentication

In addition to HMAC signatures, the platform supports alternative authenticated intake channels:
1. **Shared Secret Token:** Callers may transmit the raw secret via headers:
   - `X-Webhook-Secret: <secret>`
   - `X-Health-Token: <secret>`
   - `X-Health-Secret: <secret>`
   - `X-Api-Key: <secret>`
   - `Authorization: Bearer <secret>` (when non-JWT format)
   Comparison against the decrypted project secret is executed in constant time via `hmac.compare_digest`.
2. **OIDC RS256 Bearer JWT:** Platform operators may authenticate using an OIDC JWT. The caller's claims are verified against the JWKS endpoint, and the user must possess `Permission.INGEST_PROJECT_DEPLOYMENTS` (granted to `TENANT_ADMIN` and `TECH_LEAD`). Callers with `DEVELOPER` role lack this permission and receive `HTTP 403 Forbidden`.

### Invariant 4: Dual Ingestion Endpoint Parity

The platform exposes two interchangeable HTTP ingestion endpoints:
- `POST /api/v1/projects/{project_id}/health-signals` (RESTful resource route)
- `POST /api/v1/webhooks/projects/{project_id}/health` (Webhook routing convention)

Both endpoints invoke the identical underlying controller `handle_project_health_signal_http`, guaranteeing 100% functional, cryptographic, and state machine parity.

### Invariant 5: Monotonic Failure Detection & Clock-Skew Guard

A `PASS` signal with timestamp $t_{\text{pass}}$ attempting to resolve an active session with detection timestamp $t_{\text{fail}}$ must satisfy:
$$t_{\text{pass}} \ge t_{\text{fail}}$$
If $t_{\text{pass}} < t_{\text{fail}}$ due to upstream clock skew, NTP divergence, or payload corruption, the request is rejected with `HTTP 422 Unprocessable Content` (or `HTTP 422 Unprocessable Entity`), preventing corrupt negative outage intervals ($\Delta t < 0$).

### Invariant 6: Storage-Layer Partial Unique Index Invariant

To guarantee data integrity across concurrent webhook delivery workers, the PostgreSQL storage engine enforces:
```sql
CREATE UNIQUE INDEX uq_production_failures_one_open_per_project
ON production_failures (tenant_id, project_id)
WHERE status = 'OPEN';
```
Even under extreme race conditions where multiple parallel workers attempt to open a failure session simultaneously, the database serializes insertion: exactly one worker commits, and all other workers collide on the unique index, preventing multi-open session corruption.

### Invariant 7: Dual-Table Replication & DORA MTTR Alignment

To ensure seamless integration with the DORA metrics calculation engine (`US-29` / `Task 29.2` / `Task 29.3`), every lifecycle event in `production_failures` is mirrored atomically to `incidents`:
- When a `ProductionFailure` is created (`OPEN`), a corresponding `Incident` record is inserted with identical `id`, `started_at`, and `severity`.
- When the `ProductionFailure` is resolved (`RESOLVED`), the corresponding `Incident` record is updated with `resolved_at` and `recovery_time_seconds`.
- Downstream MTTR calculations (`compute_mttr`) produce identical results whether querying `production_failures` or `incidents`.

### Invariant 8: PostgreSQL 16 FORCE ROW LEVEL SECURITY & Anti-Enumeration

All queries against `production_failures` and `projects` execute under `eng_app_user` with PostgreSQL `FORCE ROW LEVEL SECURITY`:
```sql
ALTER TABLE production_failures ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_failures FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_policy ON production_failures
    AS PERMISSIVE FOR ALL
    TO eng_app_user
    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
```
- In webhook mode: The project's tenant ID is resolved securely via the `SECURITY DEFINER` function `get_project_health_webhook_route(UUID)` before setting session tenant context.
- Cross-tenant queries under `eng_app_user` return 0 rows.
- In JWT mode: Ingestion attempts targeting a project belonging to a foreign tenant return `HTTP 404 Not Found`, guaranteeing zero leakage of project existence.

---

## 3. Relational Schemas, Constraints, Pydantic DTOs & State Transition Flowcharts

### 3.1 Relational Schema Definitions (`backend/migrations/versions/e4a9c2f7b1d6_...py`)

```sql
-- 1. Extend projects table with health probe configuration
ALTER TABLE projects ADD COLUMN IF NOT EXISTS health_probe_enabled BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS health_probe_source VARCHAR(64) NOT NULL DEFAULT 'webhook';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS health_webhook_secret_hash TEXT NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS health_probe_url VARCHAR(1024) NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS health_probe_interval_seconds INTEGER NOT NULL DEFAULT 60;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS health_status VARCHAR(32) NOT NULL DEFAULT 'HEALTHY';

-- 2. Create production_failures table for US-68 / US-29 failure tracking
CREATE TABLE IF NOT EXISTS production_failures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NULL,
    recovery_time_seconds NUMERIC(12, 2) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    probe_source VARCHAR(64) NOT NULL DEFAULT 'webhook',
    severity VARCHAR(16) NOT NULL DEFAULT 'SEV-1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_production_failures_status CHECK (
        status IN ('OPEN', 'RESOLVED')
    ),
    CONSTRAINT ck_production_failures_recovery_non_negative CHECK (
        resolved_at IS NULL OR resolved_at >= started_at
    )
);

-- 3. Indexes & Concurrency Controls
CREATE INDEX IF NOT EXISTS idx_production_failures_tenant_id 
    ON production_failures (tenant_id);
CREATE INDEX IF NOT EXISTS idx_production_failures_project_status 
    ON production_failures (tenant_id, project_id, status);
CREATE INDEX IF NOT EXISTS idx_production_failures_project_resolved_at 
    ON production_failures (tenant_id, project_id, resolved_at);

-- Partial Unique Index guaranteeing at most 1 OPEN failure session per project
CREATE UNIQUE INDEX IF NOT EXISTS uq_production_failures_one_open_per_project
    ON production_failures (tenant_id, project_id)
    WHERE status = 'OPEN';
```

### 3.2 SQLAlchemy ORM Model (`app.db.models.models.py`)

```python
class ProductionFailure(Base):
    """Production health failure session tracked under US-68 for MTTR calculation."""

    __tablename__ = "production_failures"

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="ck_production_failures_status",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= started_at",
            name="ck_production_failures_recovery_non_negative",
        ),
        Index("idx_production_failures_tenant_id", "tenant_id"),
        Index("idx_production_failures_project_status", "tenant_id", "project_id", "status"),
        Index(
            "idx_production_failures_project_resolved_at", "tenant_id", "project_id", "resolved_at"
        ),
        Index(
            "uq_production_failures_one_open_per_project",
            "tenant_id",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recovery_time_seconds: Mapped[Optional[float]] = mapped_column(Numeric(12, 2, asdecimal=False))
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)
    probe_source: Mapped[str] = mapped_column(String(64), default="webhook", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="SEV-1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="production_failures")
```

### 3.3 Pydantic Request & Response Schemas (`app.projects.schemas.py`)

```python
class HealthSignalStatus(str, Enum):
    FAIL = "FAIL"
    PASS = "PASS"


class FailureSessionStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ProjectHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    FAILING = "FAILING"


class HealthSignalIngestRequest(BaseModel):
    status: HealthSignalStatus
    signal_timestamp: Optional[datetime] = Field(default=None, alias="timestamp")
    probe_source: Optional[str] = Field(default="webhook", max_length=64)
    severity: Optional[str] = Field(default="SEV-1", max_length=16)
    tenant_id: Optional[uuid.UUID] = None
    deployment_id: Optional[uuid.UUID] = None


class HealthSignalIngestResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    project_id: uuid.UUID
    tenant_id: uuid.UUID
    signal_status: HealthSignalStatus
    project_health_status: ProjectHealthStatus
    action: Literal["opened", "duplicate_fail_ignored", "resolved", "orphan_pass_ignored"]
    noop: bool
    duplicate: bool
    orphan: bool
    session_id: Optional[uuid.UUID] = None
    incident_id: Optional[uuid.UUID] = None
    session_status: Optional[FailureSessionStatus] = None
    probe_source: Optional[str] = None
    started_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    recovery_time_seconds: Optional[float] = None
    message: str
```

### 3.4 State Machine Transition Flowchart

```mermaid
stateDiagram-v2
    [*] --> HEALTHY : Project Created

    state HEALTHY {
        [*] --> Idle
        Idle --> Idle : PASS Signal (Orphan No-Op, 0 rows)
    }

    HEALTHY --> FAILING : FAIL Signal at 10:00 (Session Opened, started_at=10:00)

    state FAILING {
        [*] --> SessionOpen
        SessionOpen --> SessionOpen : Duplicate FAIL at 10:05 (Preserve started_at=10:00, 0 rows)
    }

    FAILING --> HEALTHY : PASS Signal at 11:48 (Session Resolved, dt=6480.0s / 1.80h)
```

### 3.5 PostgreSQL SECURITY DEFINER Routing Function

```sql
CREATE OR REPLACE FUNCTION get_project_health_webhook_route(p_project_id UUID)
RETURNS TABLE (
    id UUID,
    tenant_id UUID,
    health_probe_enabled BOOLEAN,
    health_probe_source VARCHAR(64),
    health_webhook_secret_hash TEXT,
    health_status VARCHAR(32),
    is_active BOOLEAN,
    deleted_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    SELECT
        p.id,
        p.tenant_id,
        p.health_probe_enabled,
        p.health_probe_source,
        p.health_webhook_secret_hash,
        p.health_status,
        p.is_active,
        p.deleted_at
    FROM projects p
    WHERE p.id = p_project_id
    LIMIT 1;
END;
$$;
REVOKE ALL ON FUNCTION get_project_health_webhook_route(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION get_project_health_webhook_route(UUID) TO eng_app_user;
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Test Scenario Description | Ingestion Channel & Auth | Input Payload Summary | Expected HTTP Status | Expected State Action | Expected DB Row Delta (`prod_failures` / `incidents`) | Target Acceptance Criteria |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-HEALTH-SIG-001`** | **Canonical Outage Cycle: 10:00 FAIL $\rightarrow$ 10:05 Duplicate FAIL $\rightarrow$ 11:48 PASS** | Dual Endpoint (`HMAC-SHA256`) | 10:00 FAIL, 10:05 FAIL, 11:48 PASS | **`200 OK`** | `opened` $\rightarrow$ `duplicate_fail_ignored` $\rightarrow$ `resolved` | $+1$ OPEN $\rightarrow$ $+0$ $\rightarrow$ $1$ RESOLVED ($6{,}480.0\text{s}$ / $1.80\text{h}$) | Primary AC: Full state machine cycle, duplicate idempotency, exact MTTR sync |
| **`TC-HEALTH-SIG-002`** | **Duplicate Failing Signals Preserving Original `started_at`** | Webhook (`X-Hub-Signature-256`) | 3 sequential FAIL signals (`10:00`, `10:02`, `10:05`) | **`200 OK`** | `opened` $\rightarrow$ `duplicate_fail_ignored` $\times 2$ | $+1$ OPEN (total: 1 row, `started_at` locked to `10:00`) | AC: Duplicate signals do not overwrite detection timestamp |
| **`TC-HEALTH-SIG-003`** | **Storage-Layer Partial Unique Index Concurrency Guard** | Direct DB Session | Concurrent duplicate `status='OPEN'` INSERT | **`IntegrityError`** | Storage rejection | Unique violation `23505` (`uq_production_failures_one_open_per_project`) | AC: Database guarantees at most one active OPEN session per project |
| **`TC-HEALTH-SIG-004`** | **Orphan PASS Signal When Project Is Healthy** | REST (`X-Webhook-Signature`) | PASS at `09:00:00Z` on healthy project | **`200 OK`** | `orphan_pass_ignored` (`noop=True`, `orphan=True`) | $+0$ rows in `production_failures`, $+0$ in `incidents` | AC: Orphan PASS produces clean zero-row no-op |
| **`TC-HEALTH-SIG-005`** | **Subsequent PASS Signal Following Outage Resolution** | Webhook (`X-Health-Signature`) | PASS at `12:00:00Z` after session resolved at `11:48:00Z` | **`200 OK`** | `orphan_pass_ignored` (`noop=True`, `orphan=True`) | $+0$ rows; resolved session duration ($6{,}480.0\text{s}$) unmodified | AC: Post-resolution recovery signals act as idempotent no-ops |
| **`TC-HEALTH-SIG-006`** | **Invalid HMAC Signature Rejection** | Both Endpoints (`HMAC-SHA256`) | Signature `sha256=0000...0000` | **`401 Unauthorized`** | Cryptographic rejection | $+0$ rows; `WWW-Authenticate: WebhookSignature` | AC: Invalid signature rejected before JSON parsing |
| **`TC-HEALTH-SIG-007`** | **Tampered Webhook Body Rejection** | Webhook (`X-Hub-Signature-256`) | Valid signature for Body A sent with Body B | **`401 Unauthorized`** | Cryptographic rejection | $+0$ rows; payload parsing rejected | AC: Cryptographic digest validates exact message integrity |
| **`TC-HEALTH-SIG-008`** | **Missing Authentication Credentials Rejection** | Both Endpoints (No Auth Headers) | Valid JSON FAIL signal without credentials | **`401 Unauthorized`** | Auth gate rejection | $+0$ rows; returns HTTP 401 | AC: Unauthenticated intake strictly denied |
| **`TC-HEALTH-SIG-009`** | **Shared Secret Token Authentication** | REST & Webhook (`X-Webhook-Secret`) | Valid shared token header / Bearer secret | **`200 OK`** | `opened` / `resolved` | $+1$ session committed under secret token auth | AC: Header tokens and non-JWT bearer secrets accepted |
| **`TC-HEALTH-SIG-010`** | **Constant-Time Signature Comparison Verification** | Crypto Service (`hmac.compare_digest`) | Correct HMAC vs mismatched prefix/length/digest | **True / False** | Microsecond constant-time execution | Immunity to timing side-channel attacks | AC: Verification executes via `hmac.compare_digest` |
| **`TC-HEALTH-SIG-011`** | **Cross-Tenant Health Signal Rejection via `X-Tenant-ID`** | Webhook (`X-Tenant-ID: Tenant B`) | Target Project A (Tenant A) with Tenant B header | **`403 Forbidden`** | Tenant isolation rejection | $+0$ rows; "Cross-tenant health signal rejected" | AC: Header tenant mismatch blocked with HTTP 403 |
| **`TC-HEALTH-SIG-012`** | **Cross-Tenant Payload Rejection (`payload.tenant_id`)** | REST (`payload.tenant_id = Tenant B`) | Target Project A (Tenant A) with foreign tenant ID in body | **`403 Forbidden`** | Tenant isolation rejection | $+0$ rows; "Cross-tenant health signal rejected" | AC: Payload tenant spoofing blocked with HTTP 403 |
| **`TC-HEALTH-SIG-013`** | **Cross-Tenant JWT Rejection & Anti-Enumeration** | REST (`Authorization: Bearer <JWT_B>`) | Tenant B Admin JWT calling Tenant A project | **`404 Not Found`** | Anti-enumeration gate | $+0$ rows; returns HTTP 404 (does not leak project existence) | AC: Cross-tenant JWT caller denied with HTTP 404 |
| **`TC-HEALTH-SIG-014`** | **PostgreSQL FORCE ROW LEVEL SECURITY Cross-Tenant Isolation** | PostgreSQL Session (`eng_app_user`) | Direct SQL query under Tenant B session context | **`0 rows`** | PostgreSQL RLS enforcement | Tenant A failure sessions invisible to Tenant B | AC: Database guarantees zero cross-tenant row leakage |
| **`TC-HEALTH-SIG-015`** | **OIDC RS256 JWT RBAC Permission Gate** | REST (`Authorization: Bearer <JWT>`) | `TENANT_ADMIN` vs `DEVELOPER` role JWT | **`200 OK` / `403 Forbidden`** | RBAC permission evaluation | `TENANT_ADMIN` accepted; `DEVELOPER` rejected | AC: DEVELOPER role lacking deployment ingestion denied 403 |
| **`TC-HEALTH-SIG-016`** | **Disabled Health Probe Ingestion Rejection** | REST (`health_probe_enabled=False`) | Valid HMAC FAIL signal on disabled project | **`403 Forbidden`** | Feature flag gate | $+0$ rows; "Health probe ingestion is disabled" | AC: Inactive probe configuration blocked with HTTP 403 |
| **`TC-HEALTH-SIG-017`** | **Clock-Skew Anomalous PASS Preceding `started_at`** | Both Endpoints | PASS at `09:55:00Z` for session started at `10:00:00Z` | **`422 Unprocessable`** | Clock-skew validation rejection | Active OPEN session preserved; corrupt negative $\Delta t$ rejected | AC: Clock skew rejected with HTTP 422 |
| **`TC-HEALTH-SIG-018`** | **Sequential Outage Cycles & 0-Second Failover Recovery** | Dual Endpoint (`HMAC-SHA256`) | Outage 1 ($1800\text{s}$) + Outage 2 ($0.0\text{s}$ failover) | **`200 OK`** | `opened` $\rightarrow$ `resolved` $\times 2$ | 2 distinct resolved sessions ($1800.0\text{s}$ and $0.0\text{s}$) | AC: Distinct outages handled; 0s recovery distinguished from null |
| **`TC-HEALTH-SIG-019`** | **Health Probe Secret Rotation & Immediate Stale HMAC Invalidation** | Config API (`rotate-secret`) | Secret rotated; signal signed with old vs new secret | **`401` / `200`** | Credential rotation lifecycle | Stale secret rejected 401; fresh secret accepted 200 | AC: Secret rotation immediately invalidates prior HMAC |
| **`TC-HEALTH-SIG-020`** | **Dual-Table Incidents Synchronization & DORA MTTR Verification** | Integration Service | Compute MTTR over synced `incidents` & `production_failures` | **`200 OK`** | Synchronous table mirror | Both tables yield identical MTTR: $6{,}480.0\text{s}$ / $1.80\text{h}$ | AC: Seamless downstream consumption by US-29 MTTR engine |
| **`TC-HEALTH-SIG-021`** | **Route Lookup Database Outage Fail-Closed Behavior** | Webhook Router | PostgreSQL connection outage simulated during routing | **`503 Service Unavailable`** | Circuit breaker / outage handler | `Retry-After: 30`; prevents uncaught 500 crash | AC: Database connectivity failures return HTTP 503 |
| **`TC-HEALTH-SIG-022`** | **Non-Existent or Soft-Deleted Project Intake Rejection** | Both Endpoints | Non-existent UUID or `deleted_at IS NOT NULL` | **`404 Not Found`** | Project lifecycle gate | $+0$ rows; returns HTTP 404 Not Found | AC: Inactive or missing projects cleanly rejected |

---

## 5. Exhaustive Test Scenario Specifications

### Scenario `TC-HEALTH-SIG-001`: Canonical Outage Cycle (`FAIL` at `10:00` $\rightarrow$ Duplicate `FAIL` at `10:05` $\rightarrow$ `PASS` at `11:48` = `6,480.0s` / `1.80h`)

#### Objective
Verify that ingestion of an initial `FAIL` signal opens a failure session, a subsequent duplicate `FAIL` signal preserves the original detection timestamp without creating duplicate rows, and a `PASS` signal resolves the session with exact recovery duration ($6{,}480.0\text{s}$ / $1.80\text{h}$), synchronizing `incidents` and updating `projects.health_status`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Enabling Implementation:** [`backend/app/projects/health_service.py`](../../app/projects/health_service.py) (SCRUM-453 / Task 68.4)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync`

#### Preconditions & Temporal Coordinates
- Tenant ID: $\tau = \text{01933a00-0001-7000-8000-000000000001}$
- Project ID: $p = \text{01933a00-0002-7000-8000-000000000002}$ (`health_status = 'HEALTHY'`, `health_probe_enabled = True`)
- Webhook Secret: $K = \text{"whsec_prod_probe_hmac_987654"}$
- Initial State: Zero rows in `production_failures` and `incidents`.

#### Step-by-Step Execution Sequence

##### Step 1: Initial `FAIL` Signal at `10:00:00 UTC`
- **Request:**
  ```http
  POST /api/v1/projects/01933a00-0002-7000-8000-000000000002/health-signals HTTP/1.1
  Host: api.manta.local
  Content-Type: application/json
  X-Hub-Signature-256: sha256=a1b2c3d4e5f6... (valid HMAC over raw body)

  {
    "status": "FAIL",
    "signal_timestamp": "2026-11-12T10:00:00Z",
    "probe_source": "uptime_probe",
    "severity": "SEV-1"
  }
  ```
- **Expected HTTP Response:** `HTTP 200 OK`
  ```json
  {
    "status": "accepted",
    "project_id": "01933a00-0002-7000-8000-000000000002",
    "tenant_id": "01933a00-0001-7000-8000-000000000001",
    "signal_status": "FAIL",
    "project_health_status": "FAILING",
    "action": "opened",
    "noop": false,
    "duplicate": false,
    "orphan": false,
    "session_id": "01933a00-0003-7000-8000-000000000001",
    "incident_id": "01933a00-0003-7000-8000-000000000001",
    "session_status": "OPEN",
    "probe_source": "uptime_probe",
    "started_at": "2026-11-12T10:00:00Z",
    "resolved_at": null,
    "recovery_time_seconds": null,
    "message": "Opened new production failure session."
  }
  ```
- **Expected Database State After Step 1:**
  - `production_failures`: 1 row (`status = 'OPEN'`, `started_at = 2026-11-12T10:00:00Z`, `resolved_at IS NULL`, `recovery_time_seconds IS NULL`).
  - `incidents`: 1 row (`started_at = 2026-11-12T10:00:00Z`, `resolved_at IS NULL`).
  - `projects`: `health_status = 'FAILING'`.

##### Step 2: Duplicate `FAIL` Signal at `10:05:00 UTC`
- **Request:**
  ```http
  POST /api/v1/projects/01933a00-0002-7000-8000-000000000002/health-signals HTTP/1.1
  Host: api.manta.local
  Content-Type: application/json
  X-Webhook-Signature: c7d8e9f0... (valid HMAC raw hex)

  {
    "status": "FAIL",
    "signal_timestamp": "2026-11-12T10:05:00Z",
    "probe_source": "uptime_probe"
  }
  ```
- **Expected HTTP Response:** `HTTP 200 OK`
  ```json
  {
    "status": "accepted",
    "project_id": "01933a00-0002-7000-8000-000000000002",
    "tenant_id": "01933a00-0001-7000-8000-000000000001",
    "signal_status": "FAIL",
    "project_health_status": "FAILING",
    "action": "duplicate_fail_ignored",
    "noop": true,
    "duplicate": true,
    "orphan": false,
    "session_id": "01933a00-0003-7000-8000-000000000001",
    "incident_id": "01933a00-0003-7000-8000-000000000001",
    "session_status": "OPEN",
    "probe_source": "uptime_probe",
    "started_at": "2026-11-12T10:00:00Z",
    "resolved_at": null,
    "recovery_time_seconds": null,
    "message": "Duplicate FAIL signal ignored; existing OPEN session preserved."
  }
  ```
- **Expected Database State After Step 2:**
  - `production_failures`: Exactly 1 row (row count unchanged; `started_at` remains `2026-11-12T10:00:00Z`).
  - `incidents`: Exactly 1 row (`started_at` remains `2026-11-12T10:00:00Z`).
  - `projects`: `health_status = 'FAILING'`.

##### Step 3: Resolution `PASS` Signal at `11:48:00 UTC`
- **Request:**
  ```http
  POST /api/v1/projects/01933a00-0002-7000-8000-000000000002/health-signals HTTP/1.1
  Host: api.manta.local
  Content-Type: application/json
  X-Health-Signature: sha256=f1e2d3c4... (valid HMAC)

  {
    "status": "PASS",
    "signal_timestamp": "2026-11-12T11:48:00Z",
    "probe_source": "uptime_probe"
  }
  ```
- **Expected HTTP Response:** `HTTP 200 OK`
  ```json
  {
    "status": "accepted",
    "project_id": "01933a00-0002-7000-8000-000000000002",
    "tenant_id": "01933a00-0001-7000-8000-000000000001",
    "signal_status": "PASS",
    "project_health_status": "HEALTHY",
    "action": "resolved",
    "noop": false,
    "duplicate": false,
    "orphan": false,
    "session_id": "01933a00-0003-7000-8000-000000000001",
    "incident_id": "01933a00-0003-7000-8000-000000000001",
    "session_status": "RESOLVED",
    "probe_source": "uptime_probe",
    "started_at": "2026-11-12T10:00:00Z",
    "resolved_at": "2026-11-12T11:48:00Z",
    "recovery_time_seconds": 6480.0,
    "message": "Resolved open production failure session."
  }
  ```
- **Expected Database State After Step 3:**
  - `production_failures`: 1 row (`status = 'RESOLVED'`, `started_at = 2026-11-12T10:00:00Z`, `resolved_at = 2026-11-12T11:48:00Z`, `recovery_time_seconds = 6480.00`).
  - `incidents`: 1 row (`resolved_at = 2026-11-12T11:48:00Z`, `recovery_time_seconds = 6480.00`).
  - `projects`: `health_status = 'HEALTHY'`.

#### Mathematical Verification
$$\Delta t = 11:48:00 - 10:00:00 = 108\text{ minutes} = 6{,}480.0\text{ seconds} = \mathbf{1.80\text{ hours}}$$
$$\text{MTTR} = \frac{6480.0}{3600.0} = \mathbf{1.80\text{ hours}}$$

---

### Scenario `TC-HEALTH-SIG-002`: Rapid Duplicate Failing Signals Preserving Original `started_at`

#### Objective
Verify that when an automated monitoring system emits rapid repetitive `FAIL` signals across multiple minutes (`10:00`, `10:02`, `10:05`), the platform maintains strict idempotency: no additional failure session rows are inserted, and the original detection timestamp (`started_at = 10:00:00 UTC`) is preserved monotonically.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync`

#### Step-by-Step Inputs & Assertions
1. Send `FAIL` at `2026-11-12T10:00:00Z` $\rightarrow$ Returns HTTP 200, `action: "opened"`, `session_status: "OPEN"`.
2. Send `FAIL` at `2026-11-12T10:02:00Z` $\rightarrow$ Returns HTTP 200, `action: "duplicate_fail_ignored"`, `duplicate: true`, `noop: true`.
3. Send `FAIL` at `2026-11-12T10:05:00Z` $\rightarrow$ Returns HTTP 200, `action: "duplicate_fail_ignored"`, `duplicate: true`, `noop: true`.
4. **Database Assertions:**
   ```sql
   SELECT count(*), min(started_at), max(started_at), count(distinct id)
   FROM production_failures
   WHERE project_id = '01933a00-0002-7000-8000-000000000002' AND status = 'OPEN';
   -- count: 1
   -- min(started_at): 2026-11-12 10:00:00+00
   -- max(started_at): 2026-11-12 10:00:00+00
   -- count(distinct id): 1
   ```

---

### Scenario `TC-HEALTH-SIG-003`: Storage-Layer Partial Unique Index Concurrency Guard

#### Objective
Verify that the PostgreSQL partial unique index `uq_production_failures_one_open_per_project` guarantees that at most one active `OPEN` failure session can exist per `(tenant_id, project_id)`, rejecting any concurrent race-condition insert at the database constraint level.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Database Migration:** `backend/migrations/versions/e4a9c2f7b1d6_production_failures_and_project_health_config.py`
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_production_failures_model_constraints_and_indexes`

#### Execution & Expected Database Error
```python
# Session 1 inserts OPEN failure session
failure_1 = ProductionFailure(
    tenant_id=tenant_id,
    project_id=project_id,
    started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
    status="OPEN",
)
db.add(failure_1)
await db.commit()

# Concurrent Session 2 attempts raw insert of another OPEN failure session
failure_2 = ProductionFailure(
    tenant_id=tenant_id,
    project_id=project_id,
    started_at=datetime(2026, 11, 12, 10, 5, 0, tzinfo=timezone.utc),
    status="OPEN",
)
db.add(failure_2)

# Expected Outcome: IntegrityError (PostgreSQL error 23505 unique_violation)
with pytest.raises(IntegrityError) as exc_info:
    await db.commit()
assert "uq_production_failures_one_open_per_project" in str(exc_info.value)
```

---

### Scenario `TC-HEALTH-SIG-004`: Orphan `PASS` Signal When Project Is Healthy

#### Objective
Verify that when an automated health check emits a `PASS` signal while the project is healthy (`health_status = 'HEALTHY'`) and no `OPEN` failure session exists, the ingestion engine treats it as a clean no-op, inserting 0 rows and returning HTTP 200 with `orphan: true`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_orphan_pass_creates_zero_rows_and_subsequent_pass_is_noop`

#### Input Payload & Headers
```http
POST /api/v1/webhooks/projects/01933a00-0002-7000-8000-000000000002/health HTTP/1.1
Host: api.manta.local
Content-Type: application/json
X-Hub-Signature-256: sha256=... (valid HMAC)

{
  "status": "PASS",
  "signal_timestamp": "2026-11-12T09:00:00Z",
  "probe_source": "periodic_heartbeat"
}
```

#### Expected HTTP Response (`HTTP 200 OK`)
```json
{
  "status": "accepted",
  "project_id": "01933a00-0002-7000-8000-000000000002",
  "tenant_id": "01933a00-0001-7000-8000-000000000001",
  "signal_status": "PASS",
  "project_health_status": "HEALTHY",
  "action": "orphan_pass_ignored",
  "noop": true,
  "duplicate": false,
  "orphan": true,
  "session_id": null,
  "incident_id": null,
  "session_status": null,
  "probe_source": null,
  "started_at": null,
  "resolved_at": null,
  "recovery_time_seconds": null,
  "message": "Orphan PASS signal ignored; no OPEN failure session exists."
}
```

#### Expected Database State
- `production_failures`: 0 rows.
- `incidents`: 0 rows.
- `projects.health_status`: Remains `'HEALTHY'`.

---

### Scenario `TC-HEALTH-SIG-005`: Subsequent `PASS` Signal Following Outage Resolution

#### Objective
Verify that when an outage has been resolved at `11:48:00 UTC`, a subsequent routine green heartbeat `PASS` at `12:00:00 UTC` acts as an orphan no-op, leaving the resolved outage's `resolved_at` and `recovery_time_seconds` intact.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_orphan_pass_creates_zero_rows_and_subsequent_pass_is_noop`

#### Step-by-Step Invariants
1. Outage cycle completes: `FAIL` at `10:00` $\rightarrow$ `PASS` at `11:48` ($6{,}480.0\text{s}$). Session is now `RESOLVED`.
2. Green heartbeat signal arrives: `PASS` at `12:00:00Z`.
3. System detects no active `OPEN` session.
4. Response returns `action: "orphan_pass_ignored"`, `orphan: true`, `noop: true`.
5. Database assertions:
   - Total rows in `production_failures`: 1.
   - `production_failures[0].resolved_at`: `2026-11-12T11:48:00Z` (NOT `12:00:00Z`).
   - `production_failures[0].recovery_time_seconds`: `6480.0` (NOT `7200.0`).

---

### Scenario `TC-HEALTH-SIG-006`: Invalid HMAC Signature Rejection (`HTTP 401 Unauthorized`)

#### Objective
Verify that requests carrying an invalid, fabricated, or corrupted HMAC-SHA256 signature are rejected immediately with `HTTP 401 Unauthorized` and `WWW-Authenticate: WebhookSignature`, creating 0 database rows and preventing unauthorized signal tampering.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401`

#### Input Payload & Headers
```http
POST /api/v1/webhooks/projects/01933a00-0002-7000-8000-000000000002/health HTTP/1.1
Host: api.manta.local
Content-Type: application/json
X-Hub-Signature-256: sha256=0000000000000000000000000000000000000000000000000000000000000000

{
  "status": "FAIL",
  "signal_timestamp": "2026-11-12T10:00:00Z"
}
```

#### Expected HTTP Response (`HTTP 401 Unauthorized`)
```json
{
  "detail": "Invalid health webhook HMAC signature"
}
```
**Response Headers:** `WWW-Authenticate: WebhookSignature`

#### Expected Database State
- `production_failures`: 0 rows.
- `incidents`: 0 rows.
- `projects.health_status`: Remains unchanged.

---

### Scenario `TC-HEALTH-SIG-007`: Tampered Webhook Body Rejection (`HTTP 401 Unauthorized`)

#### Objective
Verify that if a valid HMAC-SHA256 signature generated for Payload A is submitted with Payload B (even with a single timestamp character altered from `10:00:00Z` to `10:05:00Z`), the signature mismatch is detected, returning `HTTP 401 Unauthorized`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401`

#### Test Execution Logic
```python
original_body = b'{"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}'
valid_sig = _sign_hmac(secret, original_body)

# Attacker tampers body timestamp
tampered_body = b'{"status": "FAIL", "signal_timestamp": "2026-11-12T10:05:00Z"}'

response = await client.post(
    url,
    content=tampered_body,
    headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
)
assert response.status_code == 401
assert response.json()["detail"] == "Invalid health webhook HMAC signature"
```

---

### Scenario `TC-HEALTH-SIG-008`: Missing Authentication Credentials Rejection (`HTTP 401 Unauthorized`)

#### Objective
Verify that requests sent without any authentication credentials (no signature headers, no token headers, and no Authorization header) are rejected fail-closed with `HTTP 401 Unauthorized`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401`

#### Input & Response
```http
POST /api/v1/projects/01933a00-0002-7000-8000-000000000002/health-signals HTTP/1.1
Host: api.manta.local
Content-Type: application/json

{"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}
```
- **Response:** `HTTP 401 Unauthorized` (`WWW-Authenticate: WebhookSignature`)
- **Detail:** `"Missing health webhook signature or authentication token"`
- **Database Row Delta:** 0 rows.

---

### Scenario `TC-HEALTH-SIG-009`: Shared Secret Token Authentication (`X-Webhook-Secret` / `Bearer <secret>`)

#### Objective
Verify that monitoring systems that do not compute HMAC digests can authenticate using a shared secret token transmitted via `X-Webhook-Secret`, `X-Health-Token`, or `Authorization: Bearer <secret>`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_token_and_jwt_authentication_accepted`

#### Step-by-Step Inputs & Invariants
1. `X-Webhook-Secret: whsec_token_auth_abc123` with `FAIL` payload $\rightarrow$ Accepted with HTTP 200, opens failure session.
2. `Authorization: Bearer whsec_token_auth_abc123` with duplicate `FAIL` $\rightarrow$ Accepted with HTTP 200, duplicate ignored.
3. Incorrect token `X-Webhook-Secret: wrong_token` $\rightarrow$ Rejected with `HTTP 401 Unauthorized`.

---

### Scenario `TC-HEALTH-SIG-010`: Constant-Time Signature Comparison Verification (Timing Side-Channel Immunity)

#### Objective
Formally test that `verify_health_webhook_hmac` utilizes `hmac.compare_digest` across all signature header formats (`sha256=<hex>` and bare hex) and securely rejects unequal digests without timing leakage.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_verify_health_webhook_hmac_constant_time`

#### Test Execution Assertions
```python
secret = "whsec_constant_time_test_key"
raw_body = b'{"status": "FAIL"}'
valid_mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

# 1. Prefixed sha256=<hex> matches
assert verify_health_webhook_hmac(secret, raw_body, f"sha256={valid_mac}") is True
# 2. Bare hex matches
assert verify_health_webhook_hmac(secret, raw_body, valid_mac) is True
# 3. Uppercase hex matches
assert verify_health_webhook_hmac(secret, raw_body, f"sha256={valid_mac.upper()}") is True
# 4. Altered single character fails
tampered = ("0" if valid_mac[0] != "0" else "1") + valid_mac[1:]
assert verify_health_webhook_hmac(secret, raw_body, f"sha256={tampered}") is False
# 5. Invalid prefix / malformed header fails
assert verify_health_webhook_hmac(secret, raw_body, "md5=12345") is False
assert verify_health_webhook_hmac(secret, raw_body, "not_a_valid_hex") is False
```

---

### Scenario `TC-HEALTH-SIG-011`: Cross-Tenant Health Signal Rejection via `X-Tenant-ID` Header

#### Objective
Verify that if an authenticated webhook request transmits an `X-Tenant-ID` header pointing to Tenant A while targeting a project belonging to Tenant B, the request is rejected with `HTTP 403 Forbidden` ("Cross-tenant health signal rejected").

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_cross_tenant_rbac_and_missing_project_rejected`

#### Input Payload & Headers
```http
POST /api/v1/webhooks/projects/01933a00-0002-7000-8000-000000000002/health HTTP/1.1
Host: api.manta.local
Content-Type: application/json
X-Tenant-ID: 01933b00-0001-7000-8000-000000000001  <-- Foreign Tenant B UUID
X-Hub-Signature-256: sha256=... (valid HMAC using Project A secret)

{"status": "FAIL", "signal_timestamp": "2026-11-12T10:00:00Z"}
```

#### Expected HTTP Response (`HTTP 403 Forbidden`)
```json
{
  "detail": "Cross-tenant health signal rejected."
}
```
**Database Row Delta:** 0 rows.

---

### Scenario `TC-HEALTH-SIG-012`: Cross-Tenant Payload Rejection (`payload.tenant_id`)

#### Objective
Verify that if the JSON payload body specifies `"tenant_id": "<foreign_tenant_id>"` that conflicts with the target project's true owning tenant ID, the request is rejected with `HTTP 403 Forbidden`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_cross_tenant_rbac_and_missing_project_rejected`

#### Expected Behavior
```python
payload = {
    "status": "FAIL",
    "tenant_id": str(foreign_tenant_id),  # Mismatched tenant
    "signal_timestamp": "2026-11-12T10:00:00Z",
}
# Result: HTTP 403 Forbidden ("Cross-tenant health signal rejected.")
```

---

### Scenario `TC-HEALTH-SIG-013`: Cross-Tenant JWT Rejection & Anti-Enumeration (`HTTP 404 Not Found`)

#### Objective
Verify that when an operator authenticated via OIDC RS256 JWT belonging to Tenant A attempts to ingest a health signal into a project owned by Tenant B, the API responds with `HTTP 404 Not Found` rather than `HTTP 403 Forbidden`, preventing attackers from enumerating valid project UUIDs across tenants.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_cross_tenant_rbac_and_missing_project_rejected`

#### Execution & Expected Response
- Caller: `User(tenant_id = Tenant_A, role = 'TENANT_ADMIN')`
- Target: `Project_B (tenant_id = Tenant_B)`
- **Response:** `HTTP 404 Not Found`
  ```json
  {
    "detail": "Project '01933b00-0002-7000-8000-000000000002' not found."
  }
  ```

---

### Scenario `TC-HEALTH-SIG-014`: PostgreSQL FORCE ROW LEVEL SECURITY Cross-Tenant Data Isolation

#### Objective
Verify that PostgreSQL `FORCE ROW LEVEL SECURITY` with `tenant_isolation_policy` on `production_failures` strictly confines database queries under `eng_app_user` to the session's tenant, preventing cross-tenant leakage even if application filters were bypassed.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Migration Contract:** `e4a9c2f7b1d6`
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_and_68_4_live_postgres_rls_and_mttr_end_to_end`

#### SQL Verification Sequence
```sql
-- Step 1: Ingest failure session for Tenant A
SET LOCAL app.current_tenant_id = '01933a00-0001-7000-8000-000000000001';
SELECT count(*) FROM production_failures;
-- Returns: 1

-- Step 2: Switch session tenant to Tenant B under eng_app_user
SET LOCAL app.current_tenant_id = '01933b00-0001-7000-8000-000000000001';
SELECT count(*) FROM production_failures;
-- Returns: 0 (Strict RLS isolation guaranteed)
```

---

### Scenario `TC-HEALTH-SIG-015`: OIDC RS256 JWT RBAC Permission Gate (`TENANT_ADMIN` vs `DEVELOPER`)

#### Objective
Verify that JWT-authenticated health signal ingestion enforces the platform RBAC matrix: users with `TENANT_ADMIN` role possess `Permission.INGEST_PROJECT_DEPLOYMENTS` and succeed, while users with `DEVELOPER` role receive `HTTP 403 Forbidden`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_cross_tenant_rbac_and_missing_project_rejected`

#### Execution & Expected HTTP Statuses
1. `TENANT_ADMIN` JWT $\rightarrow$ `HTTP 200 OK` (Session opened/resolved).
2. `DEVELOPER` JWT $\rightarrow$ `HTTP 403 Forbidden`:
   ```json
   {
     "detail": "Forbidden: insufficient permissions for this resource"
   }
   ```

---

### Scenario `TC-HEALTH-SIG-016`: Disabled Health Probe Ingestion Rejection (`HTTP 403 Forbidden`)

#### Objective
Verify that when a project has `health_probe_enabled = false`, incoming health signals (even with valid HMAC signatures) are rejected with `HTTP 403 Forbidden` ("Health probe ingestion is disabled for this project.").

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_cross_tenant_rbac_and_missing_project_rejected`

#### Execution & Assertions
- Project setup: `Project(health_probe_enabled=False)`
- Request: Valid HMAC signature over `FAIL` payload.
- Response: `HTTP 403 Forbidden` (`detail: "Health probe ingestion is disabled for this project."`).
- Database state: 0 rows inserted into `production_failures`.

---

### Scenario `TC-HEALTH-SIG-017`: Clock-Skew Anomalous `PASS` Preceding `started_at` (`HTTP 422`)

#### Objective
Verify that if an incoming `PASS` signal carries a `signal_timestamp` earlier than the active failure session's `started_at` (due to clock skew or upstream replay), the request is rejected with `HTTP 422 Unprocessable Content`, preserving the active session and preventing corrupted negative outage durations ($\Delta t < 0$).

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_pass_preceding_started_at_rejected_422`

#### Execution & Assertions
1. Open session at `2026-11-12T10:00:00Z`.
2. Submit `PASS` with `signal_timestamp = "2026-11-12T09:55:00Z"` ($5\text{ minutes}$ prior to outage start).
3. Response: `HTTP 422 Unprocessable Content`
   ```json
   {
     "detail": "PASS signal_timestamp cannot precede active failure session started_at."
   }
   ```
4. Database state: The active session remains in `status = 'OPEN'`, `resolved_at IS NULL`, `recovery_time_seconds IS NULL`.

---

### Scenario `TC-HEALTH-SIG-018`: Sequential Outage Cycles & Zero-Second Automated Failover Recovery

#### Objective
Verify that the engine accurately processes multiple sequential outage cycles and handles instantaneous zero-second automated failover recovery ($t_{\text{fail}} = t_{\text{pass}}$), correctly storing `recovery_time_seconds = 0.00` and distinguishing it from `null`.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_sequential_outage_cycles_and_zero_second_recovery`

#### Sequence of Events
- **Cycle 1:** FAIL at `10:00:00Z` $\rightarrow$ PASS at `10:30:00Z` ($\Delta t_1 = 1{,}800.0\text{s}$ / $0.50\text{h}$).
- **Cycle 2 (Zero-Second Failover):** FAIL at `14:00:00Z` $\rightarrow$ PASS at `14:00:00Z` ($\Delta t_2 = 0.00\text{s}$).
- **Expected Database State:**
  - Total rows in `production_failures`: 2.
  - Session 1: `recovery_time_seconds = 1800.00`.
  - Session 2: `recovery_time_seconds = 0.00` (valid numerical zero, NOT `null`).
- **DORA MTTR Calculation:**
  $$\text{MTTR}_{\text{seconds}} = \frac{1800.0 + 0.0}{2} = \mathbf{900.00\text{ seconds}} = \mathbf{0.25\text{ hours}}$$

---

### Scenario `TC-HEALTH-SIG-019`: Health Probe Secret Rotation & Immediate Stale HMAC Invalidation

#### Objective
Verify that rotating the project's health webhook secret via `POST /api/v1/projects/{project_id}/health-config/rotate-secret` immediately renders signatures generated with the prior secret invalid (`HTTP 401 Unauthorized`), while signatures generated with the newly issued secret succeed.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_health_config_endpoints_and_secret_rotation`

#### Execution Sequence & Assertions
1. Project configured with Secret $K_1$. Signals signed with $K_1$ return `HTTP 200 OK`.
2. Admin invokes `POST /api/v1/projects/{project_id}/health-config/rotate-secret`.
   - Generates Secret $K_2$.
   - Encrypts and persists $K_2$ with AES-256-GCM in `projects.health_webhook_secret_hash`.
3. Submit signal signed with old secret $K_1$ $\rightarrow$ Rejected with `HTTP 401 Unauthorized`.
4. Submit signal signed with new secret $K_2$ $\rightarrow$ Accepted with `HTTP 200 OK`.

---

### Scenario `TC-HEALTH-SIG-020`: Dual-Table Incidents Synchronization & DORA MTTR Verification

#### Objective
Verify that every lifecycle state change in `production_failures` is mirrored synchronously into `incidents`, ensuring downstream DORA MTTR calculation engines (`US-29`) consume accurate session boundaries without schema mismatch.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Downstream Story:** `US-29` (`SCRUM-366` / `SCRUM-448`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync`

#### Assertions
```python
# Verify synchronous mirror
assert len(store.failures) == 1
assert len(store.incidents) == 1
assert store.failures[0].id == store.incidents[0].id
assert store.failures[0].started_at == store.incidents[0].started_at
assert store.failures[0].resolved_at == store.incidents[0].resolved_at
assert store.failures[0].recovery_time_seconds == store.incidents[0].recovery_time_seconds == 6480.0

# Verify DORA MTTR engine produces identical 1.80h from both tables
mttr_pf = compute_mttr(store.failures, window_days=14, window_end=window_end)
mttr_inc = compute_mttr(store.incidents, window_days=14, window_end=window_end)

assert mttr_pf.mttr_hours == 1.80
assert mttr_inc.mttr_hours == 1.80
```

---

### Scenario `TC-HEALTH-SIG-021`: Route Lookup Database Outage Fail-Closed Behavior (`HTTP 503`)

#### Objective
Verify that if PostgreSQL or PgBouncer experiences a connection failure during pre-RLS webhook route lookup, the gateway fails closed gracefully, returning `HTTP 503 Service Unavailable` with `Retry-After: 30` rather than crashing with an unhandled 500 error.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_route_lookup_db_outage_returns_503`

#### Expected HTTP Response (`HTTP 503 Service Unavailable`)
```json
{
  "detail": "Database storage service unavailable. Please retry delivery."
}
```
**Response Headers:** `Retry-After: 30`

---

### Scenario `TC-HEALTH-SIG-022`: Non-Existent or Soft-Deleted Project Intake Rejection (`HTTP 404`)

#### Objective
Verify that health signal webhooks targeting a non-existent project UUID or an archived project (`deleted_at IS NOT NULL` or `is_active = FALSE`) return `HTTP 404 Not Found`, rejecting signal processing and creating 0 database rows.

#### Traceability
- **Parent Story:** `US-68` (`SCRUM-367`)
- **Primary Task:** `SCRUM-454` (`Task 68.5`)
- **Target Automated Test:** `backend/tests/test_health_signal_ingestion.py::test_task_68_3_cross_tenant_rbac_and_missing_project_rejected`

#### Expected Behavior
1. Unknown UUID $\rightarrow$ `HTTP 404 Not Found` (`"Project '<uuid>' not found."`).
2. Archived project (`is_active = False`) $\rightarrow$ `HTTP 404 Not Found`.
3. Soft-deleted project (`deleted_at = NOW()`) $\rightarrow$ `HTTP 404 Not Found`.

---

## 6. Automated Pytest Verification Suite Mapping (`Task 68.7` / `SCRUM-456`)

| Scenario ID | Test Scenario Summary | Pytest Test Function in `backend/tests/test_health_signal_ingestion.py` | Line Range |
| :--- | :--- | :--- | :--- |
| **`TC-HEALTH-SIG-001`** | Canonical 10:00 FAIL $\rightarrow$ 10:05 Duplicate FAIL $\rightarrow$ 11:48 PASS ($1.80\text{h}$) | `test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync` | L529–L625 |
| **`TC-HEALTH-SIG-002`** | Duplicate FAIL Signals Monotonic Started-At Preservation | `test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync` | L569–L589 |
| **`TC-HEALTH-SIG-003`** | Partial Unique Index Concurrency Guard (`uq_..._one_open_per_project`) | `test_task_68_3_production_failures_model_constraints_and_indexes` | L439–L483 |
| **`TC-HEALTH-SIG-004`** | Orphan PASS When Healthy Produces Zero Rows | `test_task_68_4_orphan_pass_creates_zero_rows_and_subsequent_pass_is_noop` | L628–L656 |
| **`TC-HEALTH-SIG-005`** | Subsequent PASS Signal After Outage Resolution No-Op | `test_task_68_4_orphan_pass_creates_zero_rows_and_subsequent_pass_is_noop` | L657–L686 |
| **`TC-HEALTH-SIG-006`** | Invalid HMAC-SHA256 Signature Rejection (`HTTP 401`) | `test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401` | L908–L953 |
| **`TC-HEALTH-SIG-007`** | Tampered Webhook Body Rejection (`HTTP 401`) | `test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401` | L965–L976 |
| **`TC-HEALTH-SIG-008`** | Missing Authentication Credentials Rejection (`HTTP 401`) | `test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401` | L937–L942 |
| **`TC-HEALTH-SIG-009`** | Shared Secret Token Authentication (`X-Webhook-Secret`) | `test_task_68_3_token_and_jwt_authentication_accepted` | L857–L888 |
| **`TC-HEALTH-SIG-010`** | Constant-Time Signature Comparison Verification | `test_verify_health_webhook_hmac_constant_time` | L1222–L1233 |
| **`TC-HEALTH-SIG-011`** | Cross-Tenant `X-Tenant-ID` Rejection (`HTTP 403`) | `test_task_68_3_cross_tenant_rbac_and_missing_project_rejected` | L1064–L1075 |
| **`TC-HEALTH-SIG-012`** | Cross-Tenant Payload Rejection (`payload.tenant_id`) | `test_task_68_3_cross_tenant_rbac_and_missing_project_rejected` | L1002–L1120 |
| **`TC-HEALTH-SIG-013`** | Cross-Tenant JWT Rejection & Anti-Enumeration (`HTTP 404`) | `test_task_68_3_cross_tenant_rbac_and_missing_project_rejected` | L1045–L1052 |
| **`TC-HEALTH-SIG-014`** | PostgreSQL FORCE ROW LEVEL SECURITY Cross-Tenant Isolation | `test_task_68_3_and_68_4_live_postgres_rls_and_mttr_end_to_end` | L1265–L1474 |
| **`TC-HEALTH-SIG-015`** | OIDC RS256 JWT RBAC Permission Gate (Admin vs Developer) | `test_task_68_3_cross_tenant_rbac_and_missing_project_rejected` | L1076–L1084 |
| **`TC-HEALTH-SIG-016`** | Disabled Health Probe Ingestion Rejection (`HTTP 403`) | `test_task_68_3_cross_tenant_rbac_and_missing_project_rejected` | L1085–L1095 |
| **`TC-HEALTH-SIG-017`** | Clock-Skew Anomalous PASS Preceding `started_at` (`HTTP 422`) | `test_task_68_4_pass_preceding_started_at_rejected_422` | L726–L766 |
| **`TC-HEALTH-SIG-018`** | Sequential Outage Cycles & 0-Second Failover Recovery | `test_task_68_4_sequential_outage_cycles_and_zero_second_recovery` | L689–L723 |
| **`TC-HEALTH-SIG-019`** | Health Probe Secret Rotation & Immediate Stale HMAC Invalidation | `test_task_68_3_health_config_endpoints_and_secret_rotation` | L1123–L1219 |
| **`TC-HEALTH-SIG-020`** | Dual-Table Incidents Synchronization & DORA MTTR Sync | `test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync` | L617–L625 |
| **`TC-HEALTH-SIG-021`** | Route Lookup PostgreSQL Outage Returns HTTP 503 | `test_task_68_3_route_lookup_db_outage_returns_503` | L1235–L1262 |
| **`TC-HEALTH-SIG-022`** | Missing or Archived Project Rejection (`HTTP 404`) | `test_task_68_3_cross_tenant_rbac_and_missing_project_rejected` | L1096–L1117 |

---

## 7. Traceability & Acceptance Criteria Verification Matrix

| Jira Requirement / Acceptance Criteria | Specific Test Scenarios | Verification Method | Status |
| :--- | :--- | :--- | :--- |
| **FAIL at 10:00 $\rightarrow$ duplicate FAIL at 10:05 $\rightarrow$ PASS at 11:48 Cycle** | `TC-HEALTH-SIG-001`, `TC-HEALTH-SIG-002`, `TC-HEALTH-SIG-020` | Automated Pytest (`test_task_68_4_state_machine_fail_duplicate_fail_pass_and_mttr_sync`) asserts `action='opened'` at 10:00, `action='duplicate_fail_ignored'` at 10:05, and `action='resolved'` at 11:48 with `recovery_time_seconds = 6480.0` ($1.80\text{h}$). | **VERIFIED / PASS** |
| **Duplicate Failing Signals Preserve Monotonic `started_at`** | `TC-HEALTH-SIG-002`, `TC-HEALTH-SIG-003` | Automated Pytest asserts `started_at` remains unchanged after duplicate FAIL and DB partial unique index `uq_production_failures_one_open_per_project` prevents duplicate rows. | **VERIFIED / PASS** |
| **Orphan PASS When Healthy Produces Zero Rows** | `TC-HEALTH-SIG-004`, `TC-HEALTH-SIG-005` | Automated Pytest (`test_task_68_4_orphan_pass_creates_zero_rows_...`) asserts HTTP 200 with `orphan: true`, `noop: true`, and 0 rows inserted into `production_failures` and `incidents`. | **VERIFIED / PASS** |
| **Invalid HMAC Signature Rejection (`HTTP 401 Unauthorized`)** | `TC-HEALTH-SIG-006`, `TC-HEALTH-SIG-007`, `TC-HEALTH-SIG-008`, `TC-HEALTH-SIG-010` | Automated Pytest (`test_task_68_3_invalid_hmac_missing_auth_and_tampered_body_rejected_401`) asserts missing auth, corrupt HMAC, and tampered payload return HTTP 401 with `WWW-Authenticate: WebhookSignature`. | **VERIFIED / PASS** |
| **Cross-Tenant Isolation & Anti-Enumeration Security** | `TC-HEALTH-SIG-011`, `TC-HEALTH-SIG-012`, `TC-HEALTH-SIG-013`, `TC-HEALTH-SIG-014` | Automated Pytest (`test_task_68_3_cross_tenant_rbac_...`) asserts cross-tenant `X-Tenant-ID` returns HTTP 403, cross-tenant JWT returns HTTP 404 anti-enumeration, and DB RLS hides foreign rows. | **VERIFIED / PASS** |
| **Scenarios Documented with Inputs and Expected Database States** | All Scenarios (`TC-HEALTH-SIG-001` through `TC-HEALTH-SIG-022`) | Every scenario explicitly details HTTP headers, payload bytes, pre-condition DB state, execution flow, response payload, and post-condition DB state. | **VERIFIED / COMPLETE** |
