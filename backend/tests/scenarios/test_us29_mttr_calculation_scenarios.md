# US-29: DORA Mean Time to Recovery (MTTR) Calculation & Health-Probe Recovery Test Scenarios

**Task:** `SCRUM-448` (`Task 29.3`: Create test scenarios for 10:00 fail -> 11:48 restore (1.8h) with unrelated 10:20 PR merge, plus zero-failure null window)  
**Parent User Story:** `SCRUM-366` (`US-29`: DORA Mean Time to Recovery)  
**Related Tickets:**  
- `SCRUM-446`: Design Mean Time to Recovery (MTTR) calculation rules based strictly on resolved production health failure sessions (`Task 29.1`, [`docs/dora-mttr-calculation-design.md`](../../docs/dora-mttr-calculation-design.md))  
- `SCRUM-447`: Implement MTTR calculation engine and daily trend series (`Task 29.2`, [`backend/app/dora/service.py`](../app/dora/service.py), [`backend/app/dora/schemas.py`](../app/dora/schemas.py))  
- `SCRUM-449`: Automated pytest integration test suite for Mean Time to Recovery (`Task 29.4`, [`backend/tests/test_dora_mttr.py`](../test_dora_mttr.py))  
- `SCRUM-442` / `SCRUM-444`: DORA Change Failure Rate calculation and test scenarios (`US-28` / `Task 28.1` / `Task 28.3`, [`docs/dora-change-failure-rate-test-scenarios.md`](../../docs/dora-change-failure-rate-test-scenarios.md))  
- `SCRUM-433` / `SCRUM-436`: DORA Lead Time for Changes calculation and test scenarios (`US-26` / `Task 26.1` / `Task 26.4`, [`docs/dora-lead-time-for-changes-test-scenarios.md`](../../docs/dora-lead-time-for-changes-test-scenarios.md))  
- `SCRUM-439` / `SCRUM-440`: DORA Deployment Frequency calculation and test scenarios (`US-27` / `Task 27.1` / `Task 27.3`, [`docs/dora-deployment-frequency-test-scenarios.md`](../../docs/dora-deployment-frequency-test-scenarios.md))  
- `SCRUM-402`: Jira Story AC storage and deployment incident correlation (`US-67` / `Task 67.1`, [`docs/jira-issues-and-deployment-incidents-schema.md`](../../docs/jira-issues-and-deployment-incidents-schema.md))  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:** [`backend/app/dora/service.py`](../app/dora/service.py), [`backend/app/dora/schemas.py`](../app/dora/schemas.py), [`backend/app/dora/router.py`](../app/dora/router.py)  
**Authoritative Automated Test Suites:** [`backend/tests/test_dora_mttr.py`](../test_dora_mttr.py)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us29_mttr_calculation_scenarios.md`](test_us29_mttr_calculation_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Establish the formal, authoritative verification scenarios and deterministic test fixtures for **DORA Mean Time to Recovery (MTTR) calculations** in the M.A.N.T.A. Engineering Intelligence Platform (`SCRUM-448` / `Task 29.3`).

This specification formalizes exact health-probe-based failure detection timestamps, verified service restoration timestamps, interval pairing arithmetic ($T_{\text{restore}} - T_{\text{failure\_detected}}$), **strict independence from pull request merge timestamps**, multi-outage mean calculation, open/unresolved outage exclusions, rolling window calculations (14-day and 30-day), daily trend time-series, and **zero-failure `null` window semantics**.

### 1.2 Implementation Scope & Core Scenarios

In accordance with Jira Task `SCRUM-448`, this specification explicitly formalizes and verifies:

1. **The Canonical Health-Probe Recovery Benchmark Fixture (`10:00` fail $\rightarrow$ `10:20` PR merge $\rightarrow$ `11:48` restore = `1.8h` / `6,480s`):**
   - **Failure Detection:** Automated production health probe reports failure at `10:00:00 UTC` ($T_{\text{failure\_detected}}$).
   - **Unrelated PR Merge:** An unrelated pull request (or hotfix PR #142) is merged to `main` at `10:20:00 UTC` ($T_{\text{pr\_merge}}$).
   - **CI/CD Deployment:** A deployment run completes at `10:35:00 UTC` ($T_{\text{deployed}}$).
   - **Health Probe Restoration:** Automated health probe passes and service health is verified restored at `11:48:00 UTC` ($T_{\text{restore}}$).
   - **Canonical Calculation:**
     $$\Delta t = 11:48:00 - 10:00:00 = 1\text{ hour } 48\text{ minutes } 00\text{ seconds} = 108\text{ minutes} = 6,480.0\text{ seconds} = \mathbf{1.80\text{ hours}}$$
     $$\text{MTTR}_{\text{seconds}} = \mathbf{6480.00}, \quad \text{MTTR}_{\text{hours}} = \mathbf{1.80}, \quad \text{MTTR}_{\text{minutes}} = \mathbf{108.00}$$

2. **The $1.8\text{h}$ vs. Defective Proxy Differential Analysis:**
   - **Defect Model 1 (PR Merge as Restoration Endpoint):** If an engine improperly treats PR merge ($10:20$) as the recovery time:
     $$\Delta t_{\text{defect\_1}} = 10:20:00 - 10:00:00 = 20\text{ minutes} = 1,200\text{ seconds} = \mathbf{0.33\text{ hours}}$$
     This defect underreports outage duration by $81.5\%$, claiming recovery before production health is verified.
   - **Defect Model 2 (PR Merge as Outage Start Timestamp):** If an engine uses the hotfix PR merge ($10:20$) as the incident start:
     $$\Delta t_{\text{defect\_2}} = 11:48:00 - 10:20:00 = 88\text{ minutes} = 5,280\text{ seconds} = \mathbf{1.47\text{ hours}}$$
     This defect erases the initial 20 minutes of production customer outage.
   - **Defect Model 3 (Deployment Time as Restoration Endpoint):** If an engine treats deployment completion ($10:35$) as restoration:
     $$\Delta t_{\text{defect\_3}} = 10:35:00 - 10:00:00 = 35\text{ minutes} = 2,100\text{ seconds} = \mathbf{0.58\text{ hours}}$$
     This defect ignores post-deployment warmup, database migrations, and health check failures persisting until $11:48$.
   - **Verification Requirement:** Automated test suites MUST assert $\text{MTTR}_{\text{hours}} = \mathbf{1.80}$ and explicitly assert $\text{MTTR} \ne 0.33$, $\text{MTTR} \ne 1.47$, and $\text{MTTR} \ne 0.58$, conclusively rejecting all proxy-based shortcuts.

3. **Multi-Outage Arithmetic Mean Calculation:**
   - Multiple resolved failure sessions in the evaluation window are averaged using standard arithmetic mean:
     $$\text{MTTR}_{\text{seconds}} = \text{round}_2\!\left(\frac{1}{|\mathcal{S}_{\text{resolved}}|} \sum_{s \in \mathcal{S}_{\text{resolved}}} (s.\text{resolved\_at} - s.\text{started\_at})\right)$$
   - Benchmark 3-outage fixture: $S_1 = 6,480\text{s}$ ($1.80\text{h}$), $S_2 = 2,160\text{s}$ ($0.60\text{h}$), $S_3 = 9,360\text{s}$ ($2.60\text{h}$):
     $$\text{Total Duration} = 6,480 + 2,160 + 9,360 = 18,000\text{ seconds} = 5.0\text{ hours}$$
     $$\text{MTTR}_{\text{seconds}} = \frac{18,000}{3} = \mathbf{6,000.00\text{ seconds}}, \quad \text{MTTR}_{\text{hours}} = \frac{6,000}{3,600} = \mathbf{1.67\text{ hours}}$$

4. **Concurrent & Overlapping Outages Strict Self-Contained Pairing:**
   - If two outages occur concurrently (e.g. Session A: $10:00 \rightarrow 11:00$, Session B: $10:15 \rightarrow 11:45$), each outage pairs its own `started_at` with its own `resolved_at` ($\Delta t_A = 3,600\text{s}$, $\Delta t_B = 5,400\text{s}$).
   - The engine strictly forbids cross-pairing (e.g., $11:45 - 10:00 = 1.75\text{h}$ or $11:00 - 10:15 = 0.75\text{h}$).

5. **Open / Unresolved Outage Exclusion (Ongoing Incident Handling):**
   - Outage sessions where `resolved_at IS NULL` represent ongoing outages. They MUST be **strictly excluded** from historical MTTR calculations.
   - Contrast Defect: Naive engines using `COALESCE(s.resolved_at, NOW())` corrupt historical metrics by causing MTTR to drift continuously.

6. **Zero-Failure Window Semantics (`null` vs `0.00`):**
   - When zero resolved production failure sessions occur in the evaluation window ($|\mathcal{S}_{\text{resolved}}| = 0$), MTTR evaluates strictly to **`null`** (`None`), never `0.0` or `0.00`.
   - Preserves the critical operational distinction between "no outages occurred" and "instantaneous 0-second recovery".

7. **Rolling Window Durations (14-Day and 30-Day) & Daily Trend Series:**
   - Evaluated over rolling 14-day ($W=14$) and 30-day ($W=30$) windows ending at $t_{\text{end}}$.
   - Contiguous daily buckets generated in UTC: active recovery days report exact daily MTTR; days with 0 resolved outages report `null`.

8. **Multi-Tenant Row-Level Security (RLS) & Project Isolation:**
   - PostgreSQL RLS enforces that incidents from other tenants or sibling projects never leak into calculation queries.
   - Soft-deleted projects (`deleted_at IS NOT NULL`) are strictly excluded.

---

## 2. Core Mathematical Formulations & Architectural Invariants

```
+-------------------------------------------------------------------------------------------------------------------+
|                           DORA MEAN TIME TO RECOVERY (MTTR) CALCULATION TOPOLOGY (US-29)                          |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Authoritative Health Probe Query | 2. Self-Contained Interval Pairing  | 3. Canonical Arithmetic Mean & Null   |
|    (PostgreSQL 16 & Tenant RLS)     |    (T_restore - T_failure_detected) |    (Half-Up Rounding & Null Semantics)|
+-------------------------------------+-------------------------------------+---------------------------------------+
| * tenant_id == :tenant_id           | * started_at = health probe FAIL    | * |S_resolved| == 0 -> MTTR = null    |
| * project_id == :project_id         | * resolved_at = health probe PASS   | * Delta t(s) = resolved_at-started_at |
| * resolved_at IS NOT NULL           | * resolved_at >= started_at (guard) | * MTTR_sec = round(mean(Delta t), 2)  |
| * resolved_at in [t_start, t_end]   | * Completely independent of PR merge| * MTTR_hr  = round(MTTR_sec / 3600, 2)|
| * soft_deleted (deleted_at) IS NULL | * No cross-pairing across concurrent| * MTTR_min = round(MTTR_sec / 60, 2)  |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                            BOUNDARY, SECURITY & PRIVACY INVARIANTS                                                |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Ongoing Outage & Clock Skew Filtering           | 5. Multi-Tenant RLS & Anti-Enumeration                       |
+----------------------------------------------------+--------------------------------------------------------------+
| * resolved_at IS NULL -> EXCLUDED from MTTR        | * FORCE ROW LEVEL SECURITY on incidents & projects           |
| * resolved_at < started_at -> EXCLUDED (data skew) | * SET LOCAL app.current_tenant_id = :tenant_id               |
| * Restoration in window -> INCLUDED                | * Cross-tenant query returns 0 rows (anti-enumeration 404)    |
| * PR merge / commit timestamps -> NEVER READ       | * Soft-deleted projects (deleted_at IS NOT NULL) -> Excluded |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Canonical Mathematical Formula

Let $\mathcal{S}_{\text{resolved}}$ be the set of resolved production health failure sessions for `(tenant_id, project_id)` whose restoration timestamp falls within the evaluation window $[t_{\text{start}}, t_{\text{end}}]$:

$$\mathcal{S}_{\text{resolved}} = \left\{ s \in \text{incidents} \;\middle|\; s.\text{tenant\_id} = \tau \land s.\text{project\_id} = p \land s.\text{resolved\_at} \neq \text{NULL} \land s.\text{resolved\_at} \ge s.\text{started\_at} \land s.\text{resolved\_at} \in [t_{\text{start}}, t_{\text{end}}] \right\}$$

For each session $s \in \mathcal{S}_{\text{resolved}}$, the restoration interval duration in seconds is:

$$\Delta t(s) = \text{EXTRACT}\!\left(\text{EPOCH FROM } (s.\text{resolved\_at} - s.\text{started\_at})\right) \ge 0$$

The Mean Time to Recovery is:

$$\text{MTTR}_{\text{seconds}} = \begin{cases} \text{null} & \text{if } |\mathcal{S}_{\text{resolved}}| = 0 \\[6pt] \text{round}_2\!\left(\dfrac{1}{|\mathcal{S}_{\text{resolved}}|} \displaystyle\sum_{s \in \mathcal{S}_{\text{resolved}}} \Delta t(s)\right) & \text{if } |\mathcal{S}_{\text{resolved}}| > 0 \end{cases}$$

$$\text{MTTR}_{\text{hours}} = \begin{cases} \text{null} & \text{if } |\mathcal{S}_{\text{resolved}}| = 0 \\[6pt] \text{round}_2\!\left(\dfrac{\text{MTTR}_{\text{seconds}}}{3600.0}\right) & \text{if } |\mathcal{S}_{\text{resolved}}| > 0 \end{cases}$$

$$\text{MTTR}_{\text{minutes}} = \begin{cases} \text{null} & \text{if } |\mathcal{S}_{\text{resolved}}| = 0 \\[6pt] \text{round}_2\!\left(\dfrac{\text{MTTR}_{\text{seconds}}}{60.0}\right) & \text{if } |\mathcal{S}_{\text{resolved}}| > 0 \end{cases}$$

### Invariant 2: Explicit Independence from Pull Request Merges & Commits

Mean Time to Recovery measures the operational duration of service unavailability experienced by users. Under no circumstances may MTTR:
1. Use `pull_requests.merged_at` as the start or end of recovery.
2. Use `commits.committed_at` as the start or end of recovery.
3. Use `deployments.deployed_at` as verified service recovery.

Merging a pull request or deploying new code to a cluster does **not** signify service restoration; health checks, readiness probes, cache warming, and telemetry signals often lag or fail post-deployment. The session ends **only** when verified health probe restoration is confirmed at `s.resolved_at`.

### Invariant 3: Strict Self-Contained Session Pairing

Each failure session $s_i$ pairs its own detection timestamp $s_i.\text{started\_at}$ with its own restoration timestamp $s_i.\text{resolved\_at}$. When multiple failure sessions overlap in time:
- Session $A$ duration: $\Delta t_A = s_A.\text{resolved\_at} - s_A.\text{started\_at}$.
- Session $B$ duration: $\Delta t_B = s_B.\text{resolved\_at} - s_B.\text{started\_at}$.
- Sessions are **never** cross-paired or merged into an aggregate envelope duration unless explicitly modelled as sub-incidents of a single root session.

### Invariant 4: Open / Unresolved Outage Exclusion

Sessions where `s.resolved_at IS NULL` represent active, unresolved outages.
1. Active outages must be excluded from historical MTTR calculations ($s \notin \mathcal{S}_{\text{resolved}}$).
2. The engine MUST NOT substitute `NOW()` for `resolved_at` in historical MTTR metrics, as this would cause historical reports to drift dynamically across query executions.
3. Active outages may be surfaced in operational dashboards as `active_incident_count`, but never aggregated into Mean Time to Recovery.

### Invariant 5: Zero-Failure Window Null Semantics (`null` vs `0.00`)

When zero resolved failure sessions occur within the window ($|\mathcal{S}_{\text{resolved}}| = 0$):
- $\text{MTTR}_{\text{seconds}} = \textbf{null}$ (`None` in Python, `null` in JSON).
- $\text{MTTR}_{\text{hours}} = \textbf{null}$.
- $\text{MTTR}_{\text{minutes}} = \textbf{null}$.
- `resolved_outage_count} = 0`.
- The engine MUST NOT coerce `null` to `0` or `0.0`. A metric of `0.0` seconds indicates instantaneous recovery, which is fundamentally distinct from an absence of failures.

### Invariant 6: Window Ingestion & Determination by Restoration Timestamp

A failure session qualifies for the evaluation window $[t_{\text{start}}, t_{\text{end}}]$ if and only if:

$$s.\text{resolved\_at} \in [t_{\text{start}}, t_{\text{end}}]$$

- If an outage started before $t_{\text{start}}$ but resolved within $[t_{\text{start}}, t_{\text{end}}]$, it is **INCLUDED**, and its full recovery duration ($s.\text{resolved\_at} - s.\text{started\_at}$) is factored into MTTR.
- If an outage started inside $[t_{\text{start}}, t_{\text{end}}]$ but resolves after $t_{\text{end}}$, it is **EXCLUDED** from the current window (it will be accounted for in the subsequent window containing its `resolved_at`).

### Invariant 7: Non-Negative Duration & Clock Skew Guard

Any anomalous record where $s.\text{resolved\_at} < s.\text{started\_at}$ (due to clock skew, NTP desynchronization, or data corruption) MUST be excluded from aggregation via `s.resolved_at >= s.started_at`.

### Invariant 8: Multi-Tenant Row-Level Security (RLS) Isolation

PostgreSQL Row-Level Security (`FORCE ROW LEVEL SECURITY`) guarantees that tenant boundaries cannot be breached. Incidents belonging to Tenant A are invisible to Tenant B queries, returning 0 rows and enforcing anti-enumeration.

---

## 3. Relational Schemas, Constraints, Pydantic DTOs & Authoritative PostgreSQL 16 Queries

### 3.1 Relational Schema Definitions

```sql
-- Incidents Table (Enforced by FORCE ROW LEVEL SECURITY)
CREATE TABLE IF NOT EXISTS incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    deployment_id UUID REFERENCES deployments(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NULL,
    recovery_time_seconds NUMERIC(12, 2) NULL,
    severity VARCHAR(16) NOT NULL DEFAULT 'SEV-2',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_incidents_recovery_non_negative CHECK (
        resolved_at IS NULL OR resolved_at >= started_at
    ),
    CONSTRAINT ck_incidents_severity CHECK (
        severity IN ('SEV-1', 'SEV-2', 'SEV-3', 'SEV-4')
    )
);

CREATE INDEX IF NOT EXISTS idx_incidents_tenant_project_resolved
ON incidents (tenant_id, project_id, resolved_at)
WHERE resolved_at IS NOT NULL;
```

### 3.2 SQLAlchemy ORM Model (`app.db.models.models.py`)

```python
class Incident(Base):
    __tablename__ = "incidents"

    __table_args__ = (Index("idx_incidents_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recovery_time_seconds: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    severity: Mapped[str] = mapped_column(String(16), default="SEV-2", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
```

### 3.3 Pydantic Schemas (`app.dora.schemas.py`)

```python
class DailyMTTRMetric(BaseModel):
    """Daily Mean Time to Recovery trend point in UTC."""

    date: date
    resolved_outage_count: int = Field(ge=0)
    mttr_seconds: Optional[float] = None
    mttr_hours: Optional[float] = None
    mttr_minutes: Optional[float] = None


class MTTROutageItem(BaseModel):
    """Detailed item representation of an individual resolved outage session."""

    incident_id: uuid.UUID
    project_id: uuid.UUID
    started_at: datetime
    resolved_at: datetime
    duration_seconds: float = Field(ge=0.0)
    duration_hours: float = Field(ge=0.0)
    severity: str = "SEV-2"


class MTTRResult(BaseModel):
    """Rolling 14-day or 30-day Mean Time to Recovery (MTTR) calculation result (US-29 / Task 29.2)."""

    window_days: Literal[14, 30]
    resolved_outage_count: int = Field(ge=0)
    mttr_seconds: Optional[float] = None
    mttr_hours: Optional[float] = None
    mttr_minutes: Optional[float] = None
    outages: list[MTTROutageItem] = Field(default_factory=list)
    daily_series: list[DailyMTTRMetric]
    window_start: datetime
    window_end: datetime

    @property
    def mttr(self) -> Optional[float]:
        """Convenience accessor returning MTTR in hours."""
        return self.mttr_hours
```

### 3.4 Authoritative PostgreSQL 16 Aggregate Query

```sql
SELECT
    COUNT(*) AS resolved_outage_count,
    CASE
        WHEN COUNT(*) = 0 THEN NULL
        ELSE ROUND(
            AVG(EXTRACT(EPOCH FROM (s.resolved_at - s.started_at)))::numeric,
            2
        )
    END AS mttr_seconds,
    CASE
        WHEN COUNT(*) = 0 THEN NULL
        ELSE ROUND(
            (AVG(EXTRACT(EPOCH FROM (s.resolved_at - s.started_at))) / 3600.0)::numeric,
            2
        )
    END AS mttr_hours,
    CASE
        WHEN COUNT(*) = 0 THEN NULL
        ELSE ROUND(
            (AVG(EXTRACT(EPOCH FROM (s.resolved_at - s.started_at))) / 60.0)::numeric,
            2
        )
    END AS mttr_minutes
FROM incidents s
JOIN projects p
  ON p.id = s.project_id
 AND p.tenant_id = s.tenant_id
WHERE s.tenant_id = :tenant_id
  AND s.project_id = :project_id
  AND p.deleted_at IS NULL
  AND s.resolved_at IS NOT NULL
  AND s.resolved_at >= s.started_at
  AND s.resolved_at >= :window_start
  AND s.resolved_at <= :window_end;
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Test Scenario Description | Window ($W$) | Input Dataset Summary | Expected Count ($|\mathcal{S}|$) | Expected MTTR Seconds | Expected MTTR Hours | Expected MTTR Minutes | Target Acceptance Criteria |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-DORA-MTTR-001`** | **Canonical Health-Probe Recovery Benchmark Fixture** | 14 days | 10:00 fail $\rightarrow$ 10:20 PR merge $\rightarrow$ 11:48 health restore (1.8h) | **1** | **`6480.00`** | **`1.80`** | **`108.00`** | Primary AC: Exact probe recovery math ($1.8\text{h}$); independence from PR merge ($10:20$) |
| **`TC-DORA-MTTR-002`** | **Defect Differential Analysis (PR Merge vs Health Probe)** | 14 days | Same canonical fixture evaluated against 3 defective temporal proxy models | **1** | **`6480.00`** vs 1200 / 5280 | **`1.80`** vs 0.33 / 1.47 | **`108.00`** vs 20 / 88 | Rejects PR merge as recovery ($0.33\text{h}$) and PR merge as start ($1.47\text{h}$) |
| **`TC-DORA-MTTR-003`** | **Multi-Outage Arithmetic Mean Calculation** | 14 days | 3 outages: $S_1=6,480\text{s}$ ($1.8\text{h}$), $S_2=2,160\text{s}$ ($0.6\text{h}$), $S_3=9,360\text{s}$ ($2.6\text{h}$) | **3** | **`6000.00`** | **`1.67`** | **`100.00`** | Correctly computes mean over multiple outages: $\frac{18000}{3} = 6000\text{s} = 1.67\text{h}$ |
| **`TC-DORA-MTTR-004`** | **Concurrent & Overlapping Outages Strict Session Pairing** | 14 days | Session A ($10:00 \rightarrow 11:00$, 1.0h) + Session B ($10:15 \rightarrow 11:45$, 1.5h) | **2** | **`4500.00`** | **`1.25`** | **`75.00`** | Strict self-contained pairing; forbids cross-pairing ($1.75\text{h}$ / $0.75\text{h}$) |
| **`TC-DORA-MTTR-005`** | **Open / Unresolved Outage Exclusion (Ongoing Incident)** | 14 days | 1 resolved outage ($1.8\text{h}$) + 1 ongoing outage (`resolved_at = NULL`) | **1** | **`6480.00`** | **`1.80`** | **`108.00`** | Ongoing incident excluded from historical MTTR; prevents drifting metric |
| **`TC-DORA-MTTR-006`** | **Zero-Failure Window Null Semantics (14-Day Window)** | 14 days | 0 outages in 14-day window | **0** | **`null`** | **`null`** | **`null`** | Returns `null` (not `0.00`), 14 zero-filled daily buckets with `mttr=null` |
| **`TC-DORA-MTTR-007`** | **Zero-Failure Window Null Semantics (30-Day Window)** | 30 days | 0 outages in 30-day window | **0** | **`null`** | **`null`** | **`null`** | Returns `null` on 30-day window, 30 zero-filled daily buckets with `mttr=null` |
| **`TC-DORA-MTTR-008`** | **Window with Only Open/Unresolved Outages Evaluates to Null** | 14 days | 2 ongoing outages (`resolved_at = NULL`), 0 resolved outages | **0** | **`null`** | **`null`** | **`null`** | Returns `null` because $|\mathcal{S}_{\text{resolved}}| = 0$ |
| **`TC-DORA-MTTR-009`** | **Window Boundary Temporal Edge Cases** | 14 days | Outage spanning window start ($T_{\text{res}} \in [t_{\text{start}}, t_{\text{end}}]$) vs pre/post-window | **3** | **`7200.00`** | **`2.00`** | **`120.00`** | Inclusion determined strictly by `resolved_at IN [window_start, window_end]` |
| **`TC-DORA-MTTR-010`** | **Clock Skew & Negative Duration Filtering** | 14 days | 1 valid outage ($1.8\text{h}$) + 1 corrupted record (`resolved_at < started_at`) | **1** | **`6480.00`** | **`1.80`** | **`108.00`** | Anomaly excluded via `resolved_at >= started_at` non-negative guard |
| **`TC-DORA-MTTR-011`** | **Multi-Tenant Row-Level Security (RLS) Isolation** | 14 days | Tenant A (1 outage, $1.8\text{h}$) vs Tenant B (2 outages, mean $2.0\text{h}$) | **1** | **`6480.00`** | **`1.80`** | **`108.00`** | Cross-tenant data leakage strictly prevented; anti-enumeration 404 enforced |
| **`TC-DORA-MTTR-012`** | **Soft-Deleted Project & Project Scope Isolation** | 14 days | Project A active vs Project B soft-deleted (`deleted_at IS NOT NULL`) | **1** | **`6480.00`** | **`1.80`** | **`108.00`** | Soft-deleted project outages excluded; project isolation guaranteed |
| **`TC-DORA-MTTR-013`** | **Daily MTTR Trend Time-Series Generation** | 14 days | Outages on Day 3 ($1.8\text{h}$) and Day 7 ($0.6\text{h}$, $2.6\text{h}$); 12 idle days | **3** | **`6000.00`** | **`1.67`** | **`100.00`** | Active days report daily MTTR; idle days report `mttr_seconds = null` |
| **`TC-DORA-MTTR-014`** | **Unsupported Window Parameter Validation** | 7 days | Invoke MTTR calculation with `window_days = 7` | **-** | **-** | **-** | **-** | Fail-closed: raises `ValueError: Unsupported window_days=7`; API returns HTTP 422 |
| **`TC-DORA-MTTR-015`** | **Instantaneous Automated Recovery (0-Second Recovery)** | 14 days | Automated failover with `started_at == resolved_at` | **1** | **`0.00`** | **`0.00`** | **`0.00`** | Numeric `0.00` (valid recovery) distinguished from `null` (zero failures) |

---

## 5. Exhaustive Test Scenario Specifications

### Scenario `TC-DORA-MTTR-001`: Canonical Health-Probe Recovery Benchmark Fixture (`10:00` Fail $\rightarrow$ `10:20` Unrelated PR Merge $\rightarrow$ `11:48` Health Restore = `1.80h`)

#### Objective
Verify that in the canonical benchmark scenario where a production health probe fails at `10:00:00 UTC`, an unrelated PR is merged at `10:20:00 UTC`, and health is verified restored at `11:48:00 UTC`, the MTTR calculation engine strictly produces:
$$\text{MTTR}_{\text{seconds}} = \mathbf{6480.00}, \quad \text{MTTR}_{\text{hours}} = \mathbf{1.80}, \quad \text{MTTR}_{\text{minutes}} = \mathbf{108.00}$$
proving that MTTR measures elapsed time to health probe restoration and is **completely independent of PR merge timestamps**.

#### Traceability
- **Parent Story:** `US-29` (`SCRUM-366`)
- **Primary Task:** `SCRUM-448` (`Task 29.3`)
- **SRS References:** §3.2.1, §3.2.2
- **Related Design:** [`docs/dora-mttr-calculation-design.md`](../../docs/dora-mttr-calculation-design.md) (Task 29.1 / SCRUM-446)

#### Preconditions & Temporal Coordinates
- Window: 14 days ($W = 14$), ending at $t_{\text{end}} = \text{2026-11-14T18:00:00Z}$.
- Base Outage Date: $T_0 = \text{2026-11-12T10:00:00Z}$.
- Project: `phoenix-api` (`tenant_id = 01932c00-0001-7000-8000-000000000001`, `project_id = 01932c00-0002-7000-8000-000000000002`).

#### Detailed Entity Specifications

| Ref | Entity | Timestamp | Event Description | Attribute Values | Contributing to MTTR? | Calculation Impact |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **$E_1$** | `Incident` | `2026-11-12T10:00:00Z` | Production health probe fails (SEV-1 outage detected) | `started_at = 2026-11-12T10:00:00Z`, `severity = 'SEV-1'` | **YES** | Defines $T_{\text{failure\_detected}}$ |
| **$E_2$** | `PullRequest` | `2026-11-12T10:20:00Z` | Unrelated feature PR #142 merged to `main` | `external_pr_number = 142`, `merged_at = 2026-11-12T10:20:00Z` | **NO** | Strictly ignored by MTTR engine |
| **$E_3$** | `Deployment` | `2026-11-12T10:35:00Z` | Production deployment completed by CD pipeline | `status = 'SUCCESS'`, `deployed_at = 2026-11-12T10:35:00Z` | **NO** | Strictly ignored by MTTR engine |
| **$E_4$** | `Incident` | `2026-11-12T11:48:00Z` | Automated health check reports healthy; incident closed | `resolved_at = 2026-11-12T11:48:00Z`, `recovery_time_seconds = 6480.0` | **YES** | Defines $T_{\text{restore}}$ |

#### Mathematical Calculation
$$\Delta t = T_{\text{restore}} - T_{\text{failure\_detected}} = \text{2026-11-12T11:48:00Z} - \text{2026-11-12T10:00:00Z}$$
$$\Delta t = 1\text{ hour } 48\text{ minutes } 00\text{ seconds} = (1 \times 3600) + (48 \times 60) = 3600 + 2880 = \mathbf{6,480.0\text{ seconds}}$$
$$|\mathcal{S}_{\text{resolved}}| = 1$$
$$\text{MTTR}_{\text{seconds}} = \frac{6480.0}{1} = \mathbf{6480.00\text{ seconds}}$$
$$\text{MTTR}_{\text{hours}} = \text{round}_2\!\left(\frac{6480.0}{3600.0}\right) = \mathbf{1.80\text{ hours}}$$
$$\text{MTTR}_{\text{minutes}} = \text{round}_2\!\left(\frac{6480.0}{60.0}\right) = \mathbf{108.00\text{ minutes}}$$

#### Expected JSON Response DTO
```json
{
  "window_days": 14,
  "resolved_outage_count": 1,
  "mttr_seconds": 6480.0,
  "mttr_hours": 1.8,
  "mttr_minutes": 108.0,
  "outages": [
    {
      "incident_id": "01932c00-0003-7000-8000-000000000001",
      "project_id": "01932c00-0002-7000-8000-000000000002",
      "started_at": "2026-11-12T10:00:00Z",
      "resolved_at": "2026-11-12T11:48:00Z",
      "duration_seconds": 6480.0,
      "duration_hours": 1.8,
      "severity": "SEV-1"
    }
  ],
  "daily_series": [
    {"date": "2026-11-01", "resolved_outage_count": 0, "mttr_seconds": null, "mttr_hours": null, "mttr_minutes": null},
    {"date": "2026-11-12", "resolved_outage_count": 1, "mttr_seconds": 6480.0, "mttr_hours": 1.8, "mttr_minutes": 108.0},
    {"date": "2026-11-14", "resolved_outage_count": 0, "mttr_seconds": null, "mttr_hours": null, "mttr_minutes": null}
  ],
  "window_start": "2026-10-31T18:00:00Z",
  "window_end": "2026-11-14T18:00:00Z"
}
```

#### Automated Verification Code Snippet
```python
def test_task_29_3_canonical_health_probe_recovery_10_00_to_11_48():
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_merge = datetime(2026, 11, 12, 10, 20, 0, tzinfo=timezone.utc)
    t_deploy = datetime(2026, 11, 12, 10, 35, 0, tzinfo=timezone.utc)
    t_restore = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)

    # Construct incident with health-probe timestamps
    incident = _make_incident(
        started_at=t_fail,
        resolved_at=t_restore,
        severity="SEV-1",
    )

    result = compute_mttr([incident], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 1
    assert result.mttr_seconds == 6480.0
    assert result.mttr_hours == 1.80
    assert result.mttr_minutes == 108.00

    # Strict contrast: PR merge and deployment timestamps MUST NOT alter MTTR
    assert result.mttr_hours != 0.33  # Rejects PR merge as recovery
    assert result.mttr_hours != 1.47  # Rejects PR merge as start
    assert result.mttr_hours != 0.58  # Rejects deployment as recovery
```

---

### Scenario `TC-DORA-MTTR-002`: Defect Differential Analysis (PR Merge Temporal Corruption vs Health Probe Restoration)

#### Objective
Formalize the exact mathematical and algorithmic defect models that would erroneously compute MTTR from PR merge or deployment timestamps instead of health probe state transitions, and assert that the M.A.N.T.A. MTTR engine conclusively rejects each defect.

#### Defect Contrast Models

```
+-------------------------------------------------------------------------------------------------------------------+
|                        THE 1.80h vs. DEFECTIVE PROXY DIFFERENTIAL COMPARISON                                      |
+--------------------------+----------------------------+-----------------------------+-----------------------------+
| Metric Dimension         | Authoritative Engine (1.8h)| Defect 1: Merge=Restore     | Defect 2: Merge=Start       |
+--------------------------+----------------------------+-----------------------------+-----------------------------+
| Start Timestamp ($T_s$)  | 10:00:00 (Probe Failure)   | 10:00:00 (Probe Failure)    | 10:20:00 (PR Merge)         |
| End Timestamp ($T_e$)    | 11:48:00 (Probe Restore)   | 10:20:00 (PR Merge)         | 11:48:00 (Probe Restore)    |
| Duration ($\Delta t$)    | 108 minutes (6,480s)       | 20 minutes (1,200s)         | 88 minutes (5,280s)         |
| MTTR Hours               | 1.80 hours                 | 0.33 hours [DEFECTIVE]      | 1.47 hours [DEFECTIVE]      |
| Error Magnitude          | 0.0% (Exact Truth)         | -81.5% Underreporting       | -18.5% Underreporting       |
| Assertion Status         | EXPECTED / PASS            | STRICTLY REJECTED           | STRICTLY REJECTED           |
+--------------------------+----------------------------+-----------------------------+-----------------------------+
```

1. **Defect Model 1 — PR Merge as Recovery Point ($\Delta t = 20\text{m} = 0.33\text{h}$):**
   - **Root Cause:** A query or service joins `pull_requests` and computes recovery duration as $T_{\text{pr\_merge}} - T_{\text{failure\_detected}}$ (assuming "merging the fix recovers the service").
   - **Operational Defect:** At 10:20, code is merely in trunk. CI has not run, deployment has not occurred, and users are still experiencing service degradation.
   - **Assertion:** `assert result.mttr_hours != 0.33`.

2. **Defect Model 2 — PR Merge as Outage Start Point ($\Delta t = 88\text{m} = 1.47\text{h}$):**
   - **Root Cause:** A query assumes the incident started when the hotfix PR was created or merged ($10:20$).
   - **Operational Defect:** Erases the first 20 minutes of production customer outage from MTTR.
   - **Assertion:** `assert result.mttr_hours != 1.47`.

3. **Defect Model 3 — Deployment Time as Recovery Point ($\Delta t = 35\text{m} = 0.58\text{h}$):**
   - **Root Cause:** A query joins `deployments` and computes recovery duration as $T_{\text{deployed}} - T_{\text{failure\_detected}} = 10:35 - 10:00 = 35\text{m} = 0.58\text{h}$.
   - **Operational Defect:** Deploying pods does not mean pods are healthy; health probes remained unhealthy until $11:48$.
   - **Assertion:** `assert result.mttr_hours != 0.58`.

---

### Scenario `TC-DORA-MTTR-003`: Multi-Outage Arithmetic Mean Calculation

#### Objective
Verify that when multiple resolved failure sessions occur within the evaluation window, the engine computes the exact arithmetic mean of all individual recovery durations:
$$\text{MTTR}_{\text{seconds}} = \mathbf{6000.00}, \quad \text{MTTR}_{\text{hours}} = \mathbf{1.67}, \quad \text{MTTR}_{\text{minutes}} = \mathbf{100.00}$$

#### Preconditions & Temporal Coordinates
- Window: 14 days, ending at $t_{\text{end}} = \text{2026-11-14T18:00:00Z}$.
- Base Date: `2026-11-12`.

#### Detailed Entity Specifications

| Ref | Entity | Started At | Resolved At | Duration Seconds | Duration Hours | Severity | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **$S_1$** | `Incident` | `2026-11-12T10:00:00Z` | `2026-11-12T11:48:00Z` | $6,480.0\text{s}$ | $1.80\text{h}$ | `SEV-1` | Canonical outage from Scenario 001 |
| **$S_2$** | `Incident` | `2026-11-12T14:00:00Z` | `2026-11-12T14:36:00Z` | $2,160.0\text{s}$ | $0.60\text{h}$ | `SEV-2` | Database replica failover recovery (36m) |
| **$S_3$** | `Incident` | `2026-11-12T16:00:00Z` | `2026-11-12T18:36:00Z` | $9,360.0\text{s}$ | $2.60\text{h}$ | `SEV-1` | Third-party payment gateway outage (156m) |
| **Total** | | | | $\mathbf{18,000.0\text{s}}$ | $\mathbf{5.00\text{h}}$ | | $|\mathcal{S}| = 3$ |

#### Mathematical Calculation
$$\sum_{s \in \mathcal{S}} \Delta t(s) = 6480.0 + 2160.0 + 9360.0 = 18,000.0\text{ seconds}$$
$$|\mathcal{S}_{\text{resolved}}| = 3$$
$$\text{MTTR}_{\text{seconds}} = \text{round}_2\!\left(\frac{18,000.0}{3}\right) = \mathbf{6000.00\text{ seconds}}$$
$$\text{MTTR}_{\text{hours}} = \text{round}_2\!\left(\frac{6000.0}{3600.0}\right) = \text{round}_2(1.6666\dots) = \mathbf{1.67\text{ hours}}$$
$$\text{MTTR}_{\text{minutes}} = \text{round}_2\!\left(\frac{6000.0}{60.0}\right) = \mathbf{100.00\text{ minutes}}$$

#### Automated Verification Code Snippet
```python
def test_task_29_3_multi_outage_arithmetic_mean_calculation():
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    s1 = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),  # 6480s / 1.8h
    )
    s2 = _make_incident(
        started_at=datetime(2026, 11, 12, 14, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 14, 36, 0, tzinfo=timezone.utc),  # 2160s / 0.6h
    )
    s3 = _make_incident(
        started_at=datetime(2026, 11, 12, 16, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 18, 36, 0, tzinfo=timezone.utc),  # 9360s / 2.6h
    )

    result = compute_mttr([s1, s2, s3], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 3
    assert result.mttr_seconds == 6000.0
    assert result.mttr_hours == 1.67
    assert result.mttr_minutes == 100.0
```

---

### Scenario `TC-DORA-MTTR-004`: Concurrent & Overlapping Outages Strict Session Pairing

#### Objective
Verify that when two separate service failures occur concurrently within the same project, each session pairs its own detection and resolution timestamps strictly, and no cross-pairing or synthetic union duration is computed.

#### Preconditions & Detailed Entities
- **Session A (Payment Gateway):**
  - `started_at = 2026-11-12T10:00:00Z`
  - `resolved_at = 2026-11-12T11:00:00Z`
  - $\Delta t_A = 60\text{m} = 3,600.0\text{ seconds} = 1.00\text{ hour}$.
- **Session B (Auth Service):**
  - `started_at = 2026-11-12T10:15:00Z`
  - `resolved_at = 2026-11-12T11:45:00Z`
  - $\Delta t_B = 90\text{m} = 5,400.0\text{ seconds} = 1.50\text{ hours}$.

#### Mathematical Calculation
$$\Delta t_A = 3,600.0\text{s}, \quad \Delta t_B = 5,400.0\text{s}$$
$$\text{Total Duration} = 3,600.0 + 5,400.0 = 9,000.0\text{ seconds}$$
$$|\mathcal{S}| = 2$$
$$\text{MTTR}_{\text{seconds}} = \frac{9,000.0}{2} = \mathbf{4500.00\text{ seconds}}$$
$$\text{MTTR}_{\text{hours}} = \frac{4500.0}{3600.0} = \mathbf{1.25\text{ hours}}$$
$$\text{MTTR}_{\text{minutes}} = \frac{4500.0}{60.0} = \mathbf{75.00\text{ minutes}}$$

#### Anti-Cross-Pairing Assertions
- Defect A (Global bounding envelope $\max(T_e) - \min(T_s) = 11:45 - 10:00 = 105\text{m} = 1.75\text{h}$): Rejected ($\text{MTTR} \ne 1.75$).
- Defect B (Cross-pairing Session A start with Session B end = $105\text{m}$): Rejected.
- Defect C (Cross-pairing Session B start with Session A end = $11:00 - 10:15 = 45\text{m} = 0.75\text{h}$): Rejected ($\text{MTTR} \ne 0.75$).

---

### Scenario `TC-DORA-MTTR-005`: Open / Unresolved Outage Exclusion (Ongoing Incident Handling)

#### Objective
Verify that ongoing outages (`resolved_at IS NULL`) are strictly excluded from the MTTR calculation, preventing metric corruption and dynamic drift.

#### Preconditions & Detailed Entities
- **Incident 1 ($S_1$, Resolved):**
  - `started_at = 2026-11-12T10:00:00Z`, `resolved_at = 2026-11-12T11:48:00Z`.
  - $\Delta t_1 = 6,480.0\text{ seconds}$ ($1.80\text{ hours}$).
- **Incident 2 ($S_2$, Active / Ongoing):**
  - `started_at = 2026-11-13T08:00:00Z`, `resolved_at = NULL`.
  - Ongoing for $>24\text{ hours}$.

#### Expected Behavior & Calculation
- $|\mathcal{S}_{\text{resolved}}| = 1$ ($S_2$ excluded because `resolved_at IS NULL`).
- $\text{MTTR}_{\text{seconds}} = \mathbf{6480.00\text{ seconds}}$.
- $\text{MTTR}_{\text{hours}} = \mathbf{1.80\text{ hours}}$.
- If a naive implementation had used `COALESCE(s.resolved_at, NOW())`, $S_2$ would have added $86,400\text{s}$, driving MTTR to $\frac{6480 + 86400}{2} = 46,440\text{s} = 12.9\text{h}$. The engine strictly guards against this drift.

---

### Scenario `TC-DORA-MTTR-006`: Zero-Failure Window Null Semantics (14-Day Rolling Window)

#### Objective
Verify that when zero resolved failure sessions occur within a 14-day evaluation window, the engine returns `null` (`None`), never `0.0` or `0.00`.

#### Preconditions
- Window: 14 days ending at `2026-11-14T18:00:00Z`.
- Incidents in Window: $0$.

#### Expected Response DTO
```json
{
  "window_days": 14,
  "resolved_outage_count": 0,
  "mttr_seconds": null,
  "mttr_hours": null,
  "mttr_minutes": null,
  "outages": [],
  "daily_series": [
    {"date": "2026-11-01", "resolved_outage_count": 0, "mttr_seconds": null, "mttr_hours": null, "mttr_minutes": null},
    {"date": "2026-11-14", "resolved_outage_count": 0, "mttr_seconds": null, "mttr_hours": null, "mttr_minutes": null}
  ],
  "window_start": "2026-10-31T18:00:00Z",
  "window_end": "2026-11-14T18:00:00Z"
}
```

#### Invariant Assertions
```python
assert result.resolved_outage_count == 0
assert result.mttr_seconds is None
assert result.mttr_hours is None
assert result.mttr_minutes is None
assert result.mttr is None
assert len(result.daily_series) == 14
assert all(day.mttr_seconds is None for day in result.daily_series)
```

---

### Scenario `TC-DORA-MTTR-007`: Zero-Failure Window Null Semantics (30-Day Rolling Window)

#### Objective
Verify that zero-failure `null` semantics hold identically over a 30-day evaluation window, generating 30 contiguous zero-filled daily buckets.

#### Expected Verification
- Window: 30 days ending at `2026-11-14T18:00:00Z`.
- `resolved_outage_count = 0`.
- `mttr_seconds = null`, `mttr_hours = null`, `mttr_minutes = null`.
- `len(daily_series) == 30`, with all daily metric values evaluating to `null`.

---

### Scenario `TC-DORA-MTTR-008`: Window with Only Open/Unresolved Outages Evaluates to Null

#### Objective
Verify that when an evaluation window contains exclusively open/unresolved outages (`resolved_at = NULL`), the engine returns `null` because $|\mathcal{S}_{\text{resolved}}| = 0$.

#### Preconditions
- Window: 14 days ending at `2026-11-14T18:00:00Z`.
- Incidents:
  - $I_1$: `started_at = 2026-11-10T12:00:00Z`, `resolved_at = NULL`.
  - $I_2$: `started_at = 2026-11-12T08:00:00Z`, `resolved_at = NULL`.

#### Expected Output
- `resolved_outage_count = 0`.
- `mttr_seconds = null`, `mttr_hours = null`, `mttr_minutes = null`.

---

### Scenario `TC-DORA-MTTR-009`: Window Boundary Temporal Edge Cases

#### Objective
Verify that inclusion in MTTR is governed strictly by the session's restoration timestamp falling within $[t_{\text{start}}, t_{\text{end}}]$:

| Incident | Started At | Resolved At | In Window? | Reason |
| :--- | :--- | :--- | :--- | :--- |
| **$I_{\text{pre}}$** | $t_{\text{start}} - 4\text{h}$ | $t_{\text{start}} - 1\text{s}$ | **EXCLUDED** | Restored before window start |
| **$I_{\text{span}}$** | $t_{\text{start}} - 2\text{h}$ | $t_{\text{start}} + 2\text{h}$ | **INCLUDED** | Restored inside window; full duration ($4\text{h} = 14,400\text{s}$) counted |
| **$I_{\text{exact\_start}}$** | $t_{\text{start}} - 1\text{h}$ | $t_{\text{start}}$ | **INCLUDED** | Restored exactly on inclusive lower boundary ($1\text{h} = 3,600\text{s}$) |
| **$I_{\text{exact\_end}}$** | $t_{\text{end}} - 1\text{h}$ | $t_{\text{end}}$ | **INCLUDED** | Restored exactly on inclusive upper boundary ($1\text{h} = 3,600\text{s}$) |
| **$I_{\text{post}}$** | $t_{\text{end}} - 30\text{m}$ | $t_{\text{end}} + 1\text{s}$ | **EXCLUDED** | Restored after window end (deferred to next window) |

#### Mathematical Calculation
$$\mathcal{S}_{\text{resolved}} = \{ I_{\text{span}}, I_{\text{exact\_start}}, I_{\text{exact\_end}} \}$$
$$\sum \Delta t = 14,400 + 3,600 + 3,600 = 21,600\text{ seconds}$$
$$\text{MTTR}_{\text{seconds}} = \frac{21,600}{3} = \mathbf{7200.00\text{ seconds}}, \quad \text{MTTR}_{\text{hours}} = \mathbf{2.00\text{ hours}}$$

---

### Scenario `TC-DORA-MTTR-010`: Clock Skew & Anomalous Negative Duration Guard

#### Objective
Verify that any anomalous incident record with `resolved_at < started_at` (e.g., negative duration resulting from clock drift) is excluded from MTTR aggregation.

#### Preconditions
- $I_{\text{valid}}$: `started_at = 10:00:00`, `resolved_at = 11:48:00` ($\Delta t = 6,480.0\text{s} = 1.80\text{h}$).
- $I_{\text{skew}}$: `started_at = 14:00:00`, `resolved_at = 13:30:00` ($\Delta t = -1,800.0\text{s} = -0.50\text{h}$).

#### Expected Behavior
- $I_{\text{skew}}$ is filtered out by `s.resolved_at >= s.started_at`.
- $|\mathcal{S}_{\text{resolved}}| = 1$.
- $\text{MTTR}_{\text{seconds}} = \mathbf{6480.00}$, $\text{MTTR}_{\text{hours}} = \mathbf{1.80}$.

---

### Scenario `TC-DORA-MTTR-011`: Multi-Tenant Row-Level Security (RLS) Isolation

#### Objective
Verify that MTTR calculation queries for Tenant A are strictly isolated from Tenant B under PostgreSQL 16 Row-Level Security.

#### Preconditions
- Tenant A: 1 outage ($6,480.0\text{s} = 1.80\text{h}$).
- Tenant B: 2 outages ($3,600.0\text{s} = 1.0\text{h}$ and $10,800.0\text{s} = 3.0\text{h}$, mean $= 2.00\text{h}$).

#### Expected Result
- Query under Tenant A context:
  $$\text{resolved\_outage\_count} = 1, \quad \text{MTTR}_{\text{hours}} = \mathbf{1.80}$$
- Zero leakage from Tenant B's $2.00\text{h}$ mean.

---

### Scenario `TC-DORA-MTTR-012`: Soft-Deleted Project & Project Scope Isolation

#### Objective
Verify that incidents belonging to soft-deleted projects (`deleted_at IS NOT NULL`) or sibling projects within the same tenant are strictly excluded.

#### Preconditions
- Active Project: 1 outage of $1.80\text{h}$ ($6,480\text{s}$).
- Soft-Deleted Project: 1 outage of $5.00\text{h}$ ($18,000\text{s}$).
- Sibling Project: 1 outage of $0.50\text{h}$ ($1,800\text{s}$).

#### Expected Result
- Query for Active Project returns strictly:
  $$\text{resolved\_outage\_count} = 1, \quad \text{MTTR}_{\text{hours}} = \mathbf{1.80}$$

---

### Scenario `TC-DORA-MTTR-013`: Daily MTTR Trend Time-Series Generation

#### Objective
Verify that the 14-day rolling window generates 14 continuous calendar day buckets in UTC where active recovery days report exact daily MTTR and idle days report `null`.

#### Preconditions
- Window: 14 days ending `2026-11-14T18:00:00Z` (Dates: `2026-11-01` to `2026-11-14`).
- Day 3 (`2026-11-03`): 1 outage ($6,480\text{s} = 1.80\text{h}$).
- Day 7 (`2026-11-07`): 2 outages ($2,160\text{s} = 0.60\text{h}$ and $9,360\text{s} = 2.60\text{h}$; daily mean $= 5,760\text{s} = 1.60\text{h}$).
- Remaining 12 days: 0 outages.

#### Expected Series Output
- Day 3: `count = 1`, `mttr_seconds = 6480.0`, `mttr_hours = 1.80`, `mttr_minutes = 108.0`.
- Day 7: `count = 2`, `mttr_seconds = 5760.0`, `mttr_hours = 1.60`, `mttr_minutes = 96.0`.
- All other 12 days: `count = 0`, `mttr_seconds = null`, `mttr_hours = null`, `mttr_minutes = null`.
- Overall 14-day metric:
  $$\text{MTTR}_{\text{seconds}} = \frac{6480 + 2160 + 9360}{3} = \frac{18000}{3} = \mathbf{6000.00\text{s}}, \quad \text{MTTR}_{\text{hours}} = \mathbf{1.67\text{h}}$$

---

### Scenario `TC-DORA-MTTR-014`: Unsupported Window Parameter Validation

#### Objective
Verify that calling the MTTR calculation engine with an unsupported window (e.g., $W = 7$ or $W = 45$) fails closed by raising a `ValueError`.

#### Preconditions
- Input: `window_days = 7`.

#### Expected Result
- Exception: `ValueError: Unsupported window_days=7. Expected one of (14, 30).`
- API layer returns HTTP `422 Unprocessable Entity`.

---

### Scenario `TC-DORA-MTTR-015`: Instantaneous Automated Recovery (0-Second Recovery)

#### Objective
Verify that an automated failover resolving within 0 seconds (`started_at == resolved_at`) evaluates to numeric `0.00` seconds / `0.00` hours, properly distinguishing a real zero-second recovery from an empty window (`null`).

#### Preconditions
- Incident: `started_at = 2026-11-12T10:00:00.000Z`, `resolved_at = 2026-11-12T10:00:00.000Z`.

#### Expected Result
- `resolved_outage_count = 1`.
- `mttr_seconds = 0.0`.
- `mttr_hours = 0.0`.
- `mttr_minutes = 0.0`.
- Unlike Scenario 006, values are numeric `0.0`, not `None`/`null`.

---

## 6. Automated Pytest Verification Suite Mapping (`Task 29.4` / `SCRUM-449`)

The scenarios specified in this design document are verified by the automated pytest suite in [`backend/tests/test_dora_mttr.py`](../test_dora_mttr.py):

| Test Identifier | Test Function in Pytest Suite (`test_dora_mttr.py`) | Target Invariant & Scope | Status |
| :--- | :--- | :--- | :--- |
| **`TC-DORA-MTTR-001`** | `test_tc_dora_mttr_001_canonical_health_probe_recovery_benchmark_fixture` | Benchmark 10:00 fail $\rightarrow$ 10:20 PR merge $\rightarrow$ 11:48 restore ($1.8\text{h}$) | **VERIFIED** |
| **`TC-DORA-MTTR-002`** | `test_tc_dora_mttr_002_defect_differential_analysis_rejects_pr_merge_proxies` | Asserting $1.8\text{h}$ vs $0.33\text{h}$, $1.47\text{h}$, $0.58\text{h}$ | **VERIFIED** |
| **`TC-DORA-MTTR-003`** | `test_tc_dora_mttr_003_multi_outage_arithmetic_mean_calculation` | Multi-outage mean calculation ($18,000\text{s} / 3 = 6,000\text{s} = 1.67\text{h}$) | **VERIFIED** |
| **`TC-DORA-MTTR-004`** | `test_tc_dora_mttr_004_concurrent_overlapping_outages_strict_pairing` | Concurrent outages self-contained pairing ($4,500\text{s} = 1.25\text{h}$) | **VERIFIED** |
| **`TC-DORA-MTTR-005`** | `test_tc_dora_mttr_005_open_unresolved_outage_exclusion` | Ongoing outage (`resolved_at = NULL`) excluded from historical MTTR | **VERIFIED** |
| **`TC-DORA-MTTR-006`** | `test_tc_dora_mttr_006_zero_failure_window_null_semantics_14d` | Zero resolved outages in 14d returns `null` (not `0.00`) | **VERIFIED** |
| **`TC-DORA-MTTR-007`** | `test_tc_dora_mttr_007_zero_failure_window_null_semantics_30d` | Zero resolved outages in 30d returns `null` across 30 buckets | **VERIFIED** |
| **`TC-DORA-MTTR-008`** | `test_tc_dora_mttr_008_window_with_only_open_outages_evaluates_to_null` | Window with only ongoing outages returns `null` | **VERIFIED** |
| **`TC-DORA-MTTR-009`** | `test_tc_dora_mttr_009_window_boundary_edge_cases` | `resolved_at IN [window_start, window_end]` inclusion rule | **VERIFIED** |
| **`TC-DORA-MTTR-010`** | `test_tc_dora_mttr_010_clock_skew_and_negative_duration_guard` | `resolved_at >= started_at` non-negative guard excludes anomalies | **VERIFIED** |
| **`TC-DORA-MTTR-011`** | `test_tc_dora_mttr_011_multitenant_rls_isolation` | Tenant A vs Tenant B isolation and anti-enumeration | **VERIFIED** |
| **`TC-DORA-MTTR-012`** | `test_tc_dora_mttr_012_soft_deleted_project_exclusion` | Soft-deleted project excluded from MTTR | **VERIFIED** |
| **`TC-DORA-MTTR-013`** | `test_tc_dora_mttr_013_daily_mttr_trend_time_series_generation` | 14 daily contiguous buckets with `null` on idle days | **VERIFIED** |
| **`TC-DORA-MTTR-014`** | `test_tc_dora_mttr_014_unsupported_window_parameter_validation` | `ValueError` on `window_days` not in `(14, 30)` | **VERIFIED** |
| **`TC-DORA-MTTR-015`** | `test_tc_dora_mttr_015_instantaneous_automated_recovery_zero_seconds` | 0-second recovery evaluates to numeric `0.00` | **VERIFIED** |
| **`TC-DORA-MTTR-SQL`** | `test_tc_dora_mttr_sql_query_compilation_and_execution` | Authoritative PostgreSQL 16 SQL query compilation | **VERIFIED** |
| **`TC-DORA-MTTR-LIVE`** | `test_tc_dora_mttr_live_postgres_canonical_benchmark_and_zero_failure_e2e` | Live PostgreSQL 16 E2E multi-tenant RLS & zero-failure verification | **VERIFIED** |

---

## 7. Traceability & Acceptance Criteria Verification Matrix

| Requirement / Acceptance Criterion | Verification Status | Covering Scenario(s) | Implementation Reference |
| :--- | :--- | :--- | :--- |
| **Detail the 10:00 fail $\rightarrow$ 10:20 PR merge $\rightarrow$ 11:48 pass scenario (1.8h)** | **Documented & Verified** | `TC-DORA-MTTR-001`, `TC-DORA-MTTR-002`, §1.2 | [`compute_mttr`](../app/dora/service.py), [`test_dora_mttr.py`](../test_dora_mttr.py) |
| **Verify explicit independence from PR merge timestamps** | **Documented & Verified** | `TC-DORA-MTTR-001`, `TC-DORA-MTTR-002`, Invariant 2 | [`test_dora_mttr.py`](../test_dora_mttr.py) (rejects $0.33\text{h}$ & $1.47\text{h}$) |
| **Multi-outage arithmetic mean calculation** | **Documented & Verified** | `TC-DORA-MTTR-003`, `TC-DORA-MTTR-004`, `TC-DORA-MTTR-013` | [`compute_mttr`](../app/dora/service.py) ($\frac{18000}{3} = 6000\text{s} = 1.67\text{h}$) |
| **Open / unresolved outage handling (`resolved_at IS NULL`)** | **Documented & Verified** | `TC-DORA-MTTR-005`, `TC-DORA-MTTR-008`, Invariant 4 | [`build_mttr_sql_query`](../app/dora/service.py) (`s.resolved_at IS NOT NULL`) |
| **Zero-failure `null` window return semantics** | **Documented & Verified** | `TC-DORA-MTTR-006`, `TC-DORA-MTTR-007`, Invariant 5 | [`compute_mttr`](../app/dora/service.py) (`CASE WHEN COUNT(*) = 0 THEN NULL`) |
| **Scenarios documented with exact timestamps and expected outputs** | **Documented & Verified** | Complete specification §1, §4, §5 | [`docs/dora-mttr-calculation-test-scenarios.md`](../../docs/dora-mttr-calculation-test-scenarios.md) |
| **Multi-tenant Row-Level Security (RLS) enforcement** | **Documented & Verified** | `TC-DORA-MTTR-011`, Invariant 8 | [`test_dora_mttr.py`](../test_dora_mttr.py) |
| **Soft-deleted project filtering (`deleted_at IS NULL`)** | **Documented & Verified** | `TC-DORA-MTTR-012` | [`build_mttr_sql_query`](../app/dora/service.py) |
| **Continuous daily trend series generation (14d/30d)** | **Documented & Verified** | `TC-DORA-MTTR-013`, Invariant 10 | [`compute_mttr`](../app/dora/service.py) |
