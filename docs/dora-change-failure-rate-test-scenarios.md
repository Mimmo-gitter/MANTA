# US-28: DORA Change Failure Rate (CFR) Calculation & Distinct Union Test Scenarios

**Task:** `SCRUM-444` (`Task 28.3`: Create 10-deployment test scenario (overlapping FAILED + incident <=24h, plus incident at 30h) asserting 30% vs 40%)  
**Parent User Story:** `SCRUM-365` (`US-28`: DORA Change Failure Rate)  
**Related Tickets:**  
- `SCRUM-442`: Design Change Failure Rate calculation engine with distinct union of deployment failures and linked incidents (`Task 28.1`, [`docs/dora-change-failure-rate-design.md`](dora-change-failure-rate-design.md))  
- `SCRUM-443`: Implement Change Failure Rate calculation engine and daily trend series (`Task 28.2`, [`backend/app/dora/service.py`](../backend/app/dora/service.py), [`backend/app/dora/schemas.py`](../backend/app/dora/schemas.py))  
- `SCRUM-445`: Automated pytest integration test suite for Change Failure Rate (`Task 28.4`, [`backend/tests/test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py))  
- `SCRUM-402`: Jira Story AC storage and deployment incident correlation (`US-67` / `Task 67.1` / `Task 67.4`, [`docs/jira-issues-and-deployment-incidents-schema.md`](jira-issues-and-deployment-incidents-schema.md), [`docs/jira-story-ac-storage-incident-linking-and-cancellation-test-scenarios.md`](jira-story-ac-storage-incident-linking-and-cancellation-test-scenarios.md))  
- `SCRUM-427` / `SCRUM-431`: Production deployment ingestion pipeline and outcome mapping (`US-25` / `Task 25.1` / `Task 25.5`, [`docs/deployment-signals-outcomes-and-staging-exclusion-test-scenarios.md`](deployment-signals-outcomes-and-staging-exclusion-test-scenarios.md))  
- `SCRUM-384`: Deployment signal selection persistence and DORA response reporting (`US-18` / `Task 18.4`, [`docs/deployment-signal-selection-persistence-and-dora-response-reporting-test-scenarios.md`](deployment-signal-selection-persistence-and-dora-response-reporting-test-scenarios.md))  
- `SCRUM-440`: DORA Deployment Frequency calculation test scenarios (`US-27` / `Task 27.3`, [`docs/dora-deployment-frequency-test-scenarios.md`](dora-deployment-frequency-test-scenarios.md))  
- `SCRUM-436`: DORA Lead Time for Changes calculation test scenarios (`US-26` / `Task 26.4`, [`docs/dora-lead-time-for-changes-test-scenarios.md`](dora-lead-time-for-changes-test-scenarios.md))  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:** [`backend/app/dora/service.py`](../backend/app/dora/service.py), [`backend/app/dora/schemas.py`](../backend/app/dora/schemas.py), [`backend/app/dora/router.py`](../backend/app/dora/router.py), [`backend/app/deployments/service.py`](../backend/app/deployments/service.py)  
**Authoritative Automated Test Suites:** [`backend/tests/test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us28_change_failure_rate_scenarios.md`](../backend/tests/scenarios/test_us28_change_failure_rate_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Establish the formal, authoritative verification scenarios and deterministic test fixtures for **DORA Change Failure Rate (CFR) calculations** in the M.A.N.T.A. Engineering Intelligence Platform (`SCRUM-444` / `Task 28.3`).

This specification formalizes exact deployment and incident fixtures, timestamps, status combinations, the distinct set union rule, the 24-hour incident correlation temporal boundary, incident cancellation exclusions, rolling window calculations (14-day and 30-day), daily trend time-series, and multi-tenant Row-Level Security (RLS) guarantees.

### 1.2 Implementation Scope & Core Scenarios

In accordance with Jira Task `SCRUM-444`, this specification explicitly formalizes and verifies:

1. **The Canonical 10-Deployment Benchmark Fixture ($30.0\%$ vs $40.0\%$):**
   - **$D_1$:** Production deployment with status `FAILED` + confirmed Jira incident detected at $+4\text{h}$ — **counts as 1 failure** (overlapping failure signals deduplicated via set union).
   - **$D_2$:** Production deployment with status `ROLLED_BACK` — **counts as 1 failure** (terminal rollback outcome).
   - **$D_3$:** Production deployment with status `SUCCESS` + confirmed Jira incident detected at $+18\text{h}$ — **counts as 1 failure** (incident within $\le 24\text{h}$ correlation window).
   - **$D_4$:** Production deployment with status `SUCCESS` + confirmed Jira incident detected at $+30\text{h}$ — **counts as 0 failures** (incident falls outside the 24-hour correlation window, contributes to denominator only).
   - **$D_5$:** Production deployment with status `SUCCESS` + Jira incident detected at $+6\text{h}$ later `CANCELLED` (`is_active = FALSE`) — **counts as 0 failures** (cancelled/inactive incidents are strictly excluded, contributes to denominator only).
   - **$D_6 \dots D_{10}$:** 5 clean production deployments with status `SUCCESS` and no incidents — **count as 0 failures** (contribute to denominator only).
   - **Canonical Calculation:**
     $$\text{Total Production Deployments} = 10, \quad \text{Distinct Failed Deployments} = 3$$
     $$\text{CFR}_{\text{ratio}} = \frac{3}{10} = \mathbf{0.3000}, \quad \text{CFR}_{\%} = \mathbf{30.0\%}$$

2. **The $30.0\%$ vs $40.0\%$ Defect Differential Analysis:**
   - **Defect Class 1 (Temporal Leakage at $+30\text{h}$):** If the 24-hour correlation boundary ($86,400\text{ seconds}$) is missing or improperly computed, $D_4$ is erroneously counted as a failure, producing $\frac{4}{10} = \mathbf{40.0\%}$.
   - **Defect Class 2 (Signal Double-Counting on $D_1$):** If `FAILED` status and the $+4\text{h}$ confirmed incident are summed additively rather than deduplicated via distinct deployment IDs (`COUNT(DISTINCT d.id)`), $D_1$ contributes 2 to the numerator, producing $\frac{2 + 1 + 1}{10} = \mathbf{40.0\%}$.
   - **Defect Class 3 (Cancelled Incident Leakage on $D_5$):** If cancelled or inactive incidents fail to be filtered, $D_5$ contributes 1 to the numerator, producing $\frac{4}{10} = \mathbf{40.0\%}$.
   - **Verification Requirement:** Automated test suites MUST explicitly assert $\text{CFR} = \mathbf{30.0\%}$ and $\text{CFR} \ne \mathbf{40.0\%}$, conclusively rejecting all three defect classes.

3. **SQL `LEFT JOIN` Row Multiplication Deduplication:**
   - A single deployment $D$ linked to 3 active confirmed incidents within 24 hours produces 3 joined rows in PostgreSQL, but `COUNT(DISTINCT d.id)` guarantees $D$ increments total deployments by **1** and failed deployments by **1**.

4. **Zero-Deployment Window Semantics (`null` vs `0.00`):**
   - When zero production deployments occur within the evaluation window ($|\mathcal{D}_{\text{prod}}| = 0$), CFR evaluates strictly to **`null`** (`None`), never `0.0` or `0.00`.
   - Distinguishes "no deployments attempted" from "0% failure rate across successful deployments".

5. **Zero-Failure Window Semantics (`0.00` / `0.0%`):**
   - When $|\mathcal{D}_{\text{prod}}| \ge 1$ and zero deployments failed or triggered confirmed incidents, CFR evaluates to numeric **`0.00`** ($0.0\%$).

6. **Non-Production (`staging`) Exclusion:**
   - Deployments targeting non-production environments (`staging`, `dev`, `qa`) are strictly excluded from both the numerator and the denominator, even if their status is `FAILED` or they have linked confirmed incidents.

7. **Authoritative CD Signal Source Enforcement:**
   - Only deployments matching `project.authoritative_cd_signal` are counted; auxiliary or secondary webhook signals are excluded to prevent duplicate counting.

8. **Multi-Tenant Row-Level Security (RLS) & Project Isolation:**
   - PostgreSQL RLS enforces that deployments and incidents belonging to other tenants or sibling projects never leak into calculation queries.

---

## 2. Core Mathematical Formulations & Architectural Invariants

```
+-------------------------------------------------------------------------------------------------------------------+
|                           DORA CHANGE FAILURE RATE CALCULATION TOPOLOGY (US-28)                                   |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Authoritative Production Query   | 2. Distinct Set Union Numerator     | 3. Canonical Ratio & Percentage       |
|    (PostgreSQL 16 & Tenant RLS)     |    (FAILED U ROLLED_BACK U INC<=24h)|    (Half-Up Rounding & Null Semantics)|
+-------------------------------------+-------------------------------------+---------------------------------------+
| * environment == 'production'       | * d.status == 'FAILED'        (1)   | * |D_prod| == 0 -> CFR = null         |
| * signal == project.auth_cd_signal  | * d.status == 'ROLLED_BACK'   (1)   | * CFR_ratio = round(failed / total, 4)|
| * deployed_at in [t_start, t_end]   | * CONFIRMED inc within 0..24h (1)   | * CFR_% = round((failed / total)*100,2|
| * soft_deleted (deleted_at) IS NULL | * d.id deduplicated ONCE in num     | * Baseline: 3 / 10 -> 0.30 (30.0%)    |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                            BOUNDARY, SECURITY & PRIVACY INVARIANTS                                                |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Strict Incident Boundary & Status Filtering     | 5. Multi-Tenant RLS & Anti-Enumeration                       |
+----------------------------------------------------+--------------------------------------------------------------+
| * Inc detected_at - deployed_at in [0, 86400s]     | * FORCE ROW LEVEL SECURITY on deployments & incidents        |
| * Inc at +30h (>86400s) -> EXCLUDED (0 in CFR)     | * SET LOCAL app.current_tenant_id = :tenant_id               |
| * Inc CANCELLED / FALSE_POSITIVE -> EXCLUDED       | * Cross-tenant query returns 0 rows (anti-enumeration 404)    |
| * Staging / Dev / QA deployments -> EXCLUDED       | * Soft-deleted projects (deleted_at IS NOT NULL) -> Excluded |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Canonical Mathematical Formula & Strict Set Union

Let $\mathcal{D}_{\text{prod}}$ be the set of authoritative production deployments for `(tenant_id, project_id)` within $[t_{\text{start}}, t_{\text{end}}]$:

$$\mathcal{D}_{\text{prod}} = \left\{ d \in \text{deployments} \;\middle|\; d.\text{tenant\_id} = \tau \land d.\text{project\_id} = p \land d.\text{environment} = \text{'production'} \land d.\text{authoritative\_signal\_source} = \text{project.authoritative\_cd\_signal} \land d.\text{deployed\_at} \in [t_{\text{start}}, t_{\text{end}}] \right\}$$

Let $\text{has\_confirmed\_incident}_{\le 24\text{h}}(d)$ be the predicate:

$$\text{has\_confirmed\_incident}_{\le 24\text{h}}(d) \iff \exists\, di \in \text{deployment\_incidents}:\; \left(di.\text{tenant\_id} = d.\text{tenant\_id} \land di.\text{deployment\_id} = d.\text{id} \land di.\text{status} = \text{'CONFIRMED'} \land di.\text{is\_active} = \text{TRUE} \land 0 \le (di.\text{detected\_at} - d.\text{deployed\_at}) \le 86,400\text{ seconds}\right)$$

Then the qualifying failed deployment set $\mathcal{F}_{\text{prod}}$ and Change Failure Rate are:

$$\mathcal{F}_{\text{prod}} = \left\{ d \in \mathcal{D}_{\text{prod}} \;\middle|\; d.\text{status} \in \{\text{'FAILED'}, \text{'ROLLED\_BACK'}\} \lor \text{has\_confirmed\_incident}_{\le 24\text{h}}(d) \right\}$$

$$\text{CFR} = \begin{cases} \text{null} & \text{if } |\mathcal{D}_{\text{prod}}| = 0 \\[6pt] \dfrac{|\mathcal{F}_{\text{prod}}|}{|\mathcal{D}_{\text{prod}}|} & \text{if } |\mathcal{D}_{\text{prod}}| > 0 \end{cases}$$

### Invariant 2: Overlapping Failure Anti-Double-Counting (`COUNT(DISTINCT d.id)`)

If a deployment $d$ experiences a failed pipeline outcome (`d.status = 'FAILED'` or `'ROLLED_BACK'`) **and** simultaneously triggers one or more confirmed Jira incidents within 24 hours, the deployment represents **one single failed change event**. The metric engine MUST evaluate:

$$\Delta \text{Numerator}(d) = 1, \quad \Delta \text{Denominator}(d) = 1$$

Under no circumstances may $d$ contribute $>1$ to the numerator. In SQL, this is enforced by `COUNT(DISTINCT d.id) FILTER (WHERE d.status IN ('FAILED', 'ROLLED_BACK') OR ...)`.

### Invariant 3: Multiple Active Incidents on a Single Deployment (LEFT JOIN Deduplication)

When a deployment is followed by cascading incidents (e.g., $D_3$ triggers an initial performance degradation incident `INC-1` at $+2\text{h}$, a database connectivity incident `INC-2` at $+6\text{h}$, and an API timeout incident `INC-3` at $+12\text{h}$), the relational query `deployments LEFT OUTER JOIN deployment_incidents` yields 3 rows for $D_3$.

The metric engine MUST deduplicate joined rows by distinct deployment ID, ensuring $D_3$ contributes exactly **1** to total deployments and **1** to failed deployments.

### Invariant 4: Strict 24-Hour Temporal Correlation Boundary ($0 \le \Delta t \le 86,400\text{s}$)

The temporal delta between incident detection and deployment completion is defined as:

$$\Delta t = di.\text{detected\_at} - d.\text{deployed\_at}$$

1. **Inclusive Upper Bound:** If $\Delta t = 86,400\text{ seconds}$ ($+24\text{h } 00\text{m } 00\text{s}$), the incident is **INCLUDED**.
2. **Boundary Exclusion:** If $\Delta t = 108,000\text{ seconds}$ ($+30\text{h}$), or $\Delta t = 86,401\text{ seconds}$ ($+24\text{h } 00\text{m } 01\text{s}$), the incident is **EXCLUDED**.
3. **Pre-Deployment Incident Exclusion:** If $\Delta t < 0$ (incident detected before deployment occurred), the incident is **EXCLUDED**.

### Invariant 5: Incident Lifecycle Status & Inactive Flag Filtering

Only incidents with `di.status = 'CONFIRMED'` and `di.is_active = TRUE` can qualify a deployment as failed.

1. **Cancelled Incidents:** Rows with `di.status = 'CANCELLED'` or `di.is_active = FALSE` MUST be excluded.
2. **False Positives:** Rows with `di.status = 'FALSE_POSITIVE'` MUST be excluded.
3. If a `SUCCESS` deployment $d$ has only cancelled or false-positive incidents, $d \notin \mathcal{F}_{\text{prod}}$.

### Invariant 6: Non-Production Environment Exclusion

Deployments where `d.environment != 'production'` (e.g., `'staging'`, `'development'`, `'qa'`, `'preview'`) are strictly excluded from $\mathcal{D}_{\text{prod}}$ and $\mathcal{F}_{\text{prod}}$. Even if a staging deployment fails or has confirmed incidents, it never alters production CFR.

### Invariant 7: Authoritative CD Signal Source Enforcement

The platform supports multiple CD signals (`github_deployments_api`, `argocd_postsync_webhook`, `direct_deployment_api`). Each project defines its single source of truth via `project.authoritative_cd_signal`. Deployments where `d.authoritative_signal_source != p.authoritative_cd_signal` are excluded.

### Invariant 8: Null-Window Semantics on Zero Production Deployments

When $|\mathcal{D}_{\text{prod}}| = 0$:
- `change_failure_rate` (ratio) evaluates to **`null`** (`None`).
- `change_failure_rate_percentage` evaluates to **`null`** (`None`).
- Daily series returns $W$ zero-filled buckets with `total_deployment_count = 0`, `failed_deployment_count = 0`, and `change_failure_rate = null`.

### Invariant 9: Multi-Tenant Row-Level Security (RLS) & Anti-Enumeration Isolation

All database access MUST execute under tenant session context (`SET LOCAL app.current_tenant_id = :tenant_id`). Cross-tenant queries return 0 rows. Sibling project queries within the same tenant must strictly isolate deployments by `project_id`.

### Invariant 10: Rolling Window Evaluation (14-Day and 30-Day) & Daily Trend Series

CFR is computed over rolling 14-day ($W=14$) and 30-day ($W=30$) windows ending at $t_{\text{end}}$. A daily trend series of $W$ buckets is generated:
- For days with $\ge 1$ deployments: `change_failure_rate` is computed as $\frac{\text{day\_failed}}{\text{day\_total}}$.
- For idle days ($0$ deployments): `change_failure_rate` evaluates to `null`.

---

## 3. Relational Schemas, Constraints, Pydantic DTOs & Authoritative PostgreSQL 16 Queries

### 3.1 Relational Schema Definitions

```sql
-- Deployments Table (Enforced by FORCE ROW LEVEL SECURITY)
CREATE TABLE IF NOT EXISTS deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repository_id UUID REFERENCES repositories(id) ON DELETE CASCADE,
    integration_id UUID REFERENCES integrations(id) ON DELETE SET NULL,
    triggered_by UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_pseudonym_id VARCHAR(64),
    external_deployment_id VARCHAR(128),
    environment VARCHAR(32) NOT NULL,
    commit_sha VARCHAR(40) NOT NULL,
    status VARCHAR(32) NOT NULL,
    is_change_failure BOOLEAN NOT NULL DEFAULT FALSE,
    authoritative_signal_source VARCHAR(64) NOT NULL DEFAULT 'github_deployments_api',
    deployed_at TIMESTAMPTZ NOT NULL,
    lead_time_seconds NUMERIC(12, 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_deployments_status CHECK (status IN ('SUCCESS', 'FAILED', 'ROLLED_BACK')),
    CONSTRAINT ck_deployments_signal_source CHECK (authoritative_signal_source IN (
        'github_deployments_api', 'argocd_postsync_webhook', 'direct_deployment_api'
    ))
);

-- Deployment Incidents Table (US-67 / Task 67.1)
CREATE TABLE IF NOT EXISTS deployment_incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    deployment_id UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    issue_id UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'CONFIRMED',
    correlation_source VARCHAR(64) NOT NULL DEFAULT 'automated_time_window_24h',
    detected_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_deployment_incidents_status CHECK (status IN (
        'CONFIRMED', 'CANCELLED', 'FALSE_POSITIVE'
    ))
);

-- Projects Table (Enforced by FORCE ROW LEVEL SECURITY)
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    authoritative_cd_signal VARCHAR(64) NOT NULL DEFAULT 'github_deployments_api',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deleted_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_projects_cd_signal CHECK (authoritative_cd_signal IN (
        'github_deployments_api', 'argocd_postsync_webhook', 'direct_deployment_api'
    ))
);
```

### 3.2 Pydantic Schemas (`app.dora.schemas`)

```python
from datetime import date, datetime
from typing import Literal, Optional
import uuid
from pydantic import BaseModel, Field


class DailyChangeFailureRateMetric(BaseModel):
    """Daily Change Failure Rate trend point in UTC."""

    date: date
    total_deployment_count: int = Field(ge=0)
    failed_deployment_count: int = Field(ge=0)
    change_failure_rate: Optional[float] = None
    change_failure_rate_percentage: Optional[float] = None

    @property
    def cfr(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_ratio(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_percentage(self) -> Optional[float]:
        return self.change_failure_rate_percentage


class ChangeFailureRateResult(BaseModel):
    """Rolling 14-day or 30-day Change Failure Rate calculation result."""

    window_days: Literal[14, 30]
    total_deployment_count: int = Field(ge=0)
    failed_deployment_count: int = Field(ge=0)
    change_failure_rate: Optional[float] = None
    change_failure_rate_percentage: Optional[float] = None
    failed_deployment_ids: list[uuid.UUID] = Field(default_factory=list)
    daily_series: list[DailyChangeFailureRateMetric]
    window_start: datetime
    window_end: datetime

    @property
    def cfr(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_ratio(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_percentage(self) -> Optional[float]:
        return self.change_failure_rate_percentage
```

### 3.3 Authoritative PostgreSQL 16 Aggregate Query

```sql
WITH prod_deployments AS (
    SELECT
        d.id,
        d.tenant_id,
        d.status,
        d.deployed_at
    FROM deployments d
    JOIN projects p
      ON p.id = d.project_id
     AND p.tenant_id = d.tenant_id
    WHERE d.tenant_id = :tenant_id
      AND d.project_id = :project_id
      AND p.deleted_at IS NULL
      AND d.environment = 'production'
      AND d.authoritative_signal_source = p.authoritative_cd_signal
      AND d.deployed_at >= :window_start
      AND d.deployed_at <= :window_end
)
SELECT
    COUNT(DISTINCT d.id) AS total_production_deployments,
    COUNT(DISTINCT d.id) FILTER (
        WHERE d.status IN ('FAILED', 'ROLLED_BACK')
           OR (
               di.id IS NOT NULL
               AND di.status = 'CONFIRMED'
               AND di.is_active = TRUE
               AND di.detected_at >= d.deployed_at
               AND di.detected_at <= d.deployed_at + INTERVAL '24 hours'
           )
    ) AS distinct_failed_deployments,
    CASE
        WHEN COUNT(DISTINCT d.id) = 0 THEN NULL
        ELSE ROUND(
            COUNT(DISTINCT d.id) FILTER (
                WHERE d.status IN ('FAILED', 'ROLLED_BACK')
                   OR (
                       di.id IS NOT NULL
                       AND di.status = 'CONFIRMED'
                       AND di.is_active = TRUE
                       AND di.detected_at >= d.deployed_at
                       AND di.detected_at <= d.deployed_at + INTERVAL '24 hours'
                   )
            )::numeric / COUNT(DISTINCT d.id)::numeric,
            4
        )
    END AS cfr_ratio,
    CASE
        WHEN COUNT(DISTINCT d.id) = 0 THEN NULL
        ELSE ROUND(
            (
                COUNT(DISTINCT d.id) FILTER (
                    WHERE d.status IN ('FAILED', 'ROLLED_BACK')
                       OR (
                           di.id IS NOT NULL
                           AND di.status = 'CONFIRMED'
                           AND di.is_active = TRUE
                           AND di.detected_at >= d.deployed_at
                           AND di.detected_at <= d.deployed_at + INTERVAL '24 hours'
                       )
                )::numeric / COUNT(DISTINCT d.id)::numeric
            ) * 100.0,
            2
        )
    END AS cfr_percentage
FROM prod_deployments d
LEFT JOIN deployment_incidents di
  ON di.deployment_id = d.id
 AND di.tenant_id = d.tenant_id;
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Test Scenario Description | Window ($W$) | Input Dataset Summary | Expected Total ($|\mathcal{D}|$) | Expected Failed ($|\mathcal{F}|$) | Expected CFR Ratio | Expected CFR % | Target Acceptance Criteria |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-DORA-CFR-001`** | **Canonical 10-Deployment Benchmark Fixture** | 14 days | D1 (FAILED+inc@+4h), D2 (ROLLED_BACK), D3 (SUCCESS+inc@+18h), D4 (SUCCESS+inc@+30h), D5 (SUCCESS+inc@+6h Cancelled), D6..D10 (SUCCESS) | **10** | **3** | **`0.3000`** | **`30.0%`** | Primary AC: Overlapping signals count as 1; +30h excluded; cancelled excluded; asserts $30.0\%$ vs $40.0\%$ |
| **`TC-DORA-CFR-002`** | **The $30.0\%$ vs $40.0\%$ Defect Differential Analysis** | 14 days | Same canonical fixture evaluated against 3 distinct defect models | **10** | **3** vs 4 | **`0.3000`** vs 0.40 | **`30.0%`** vs 40.0% | Rejects +30h window leakage, double-counting, and cancelled incident leakage |
| **`TC-DORA-CFR-003`** | **SQL LEFT JOIN Row Multiplication Deduplication** | 14 days | 1 SUCCESS prod deployment linked to 3 CONFIRMED incidents (+2h, +6h, +12h) + 2 clean SUCCESS | **3** | **1** | **`0.3333`** | **`33.33%`** | `COUNT(DISTINCT d.id)` guarantees 3 joined rows collapse to 1 failed deployment |
| **`TC-DORA-CFR-004`** | **Zero-Deployment Window Null Semantics (14-Day)** | 14 days | 0 production deployments in window | **0** | **0** | **`null`** | **`null`** | Returns `null` (not `0.00` or NaN), 14 zero-filled daily buckets with `cfr=null` |
| **`TC-DORA-CFR-005`** | **Zero-Deployment Window Null Semantics (30-Day)** | 30 days | 0 production deployments in window | **0** | **0** | **`null`** | **`null`** | Returns `null` on 30-day window, 30 zero-filled daily buckets with `cfr=null` |
| **`TC-DORA-CFR-006`** | **Zero-Failure Window ($N \ge 1$ Clean Deployments)** | 14 days | 5 clean SUCCESS production deployments without incidents | **5** | **0** | **`0.0000`** | **`0.0%`** | Evaluates to numeric `0.00` ($0.0\%$), distinguishing zero failures from empty window |
| **`TC-DORA-CFR-007`** | **Non-Production (`staging`) Environment Exclusion** | 14 days | 2 prod SUCCESS + 1 staging FAILED + 1 staging with confirmed incident | **2** | **0** | **`0.0000`** | **`0.0%`** | Staging deployments strictly excluded from both numerator and denominator |
| **`TC-DORA-CFR-008`** | **Authoritative CD Signal Source Mismatch Exclusion** | 14 days | 2 prod SUCCESS (authoritative) + 2 prod FAILED (non-authoritative signal) | **2** | **0** | **`0.0000`** | **`0.0%`** | Non-authoritative signal deployments excluded to prevent metric distortion |
| **`TC-DORA-CFR-009`** | **Strict 24-Hour Boundary Edge Cases** | 14 days | D_exact (inc@+24h00m00s, INCLUDED) vs D_late (inc@+24h00m01s, EXCLUDED) vs D_pre (inc@-5m, EXCLUDED) | **3** | **1** | **`0.3333`** | **`33.33%`** | Exact inclusive $\le 86,400\text{s}$ boundary enforcement; negative offset rejection |
| **`TC-DORA-CFR-010`** | **Incident Lifecycle Status & Inactive Flag Filtering** | 14 days | 1 SUCCESS + FALSE_POSITIVE inc + 1 SUCCESS + CANCELLED inc + 1 SUCCESS + is_active=False inc | **3** | **0** | **`0.0000`** | **`0.0%`** | Inactive, cancelled, and false-positive incidents do not mark deployments as failed |
| **`TC-DORA-CFR-011`** | **Rolling 30-Day Window Evaluation with Canonical Fixture** | 30 days | Same canonical 10-deployment benchmark fixture evaluated over 30 days | **10** | **3** | **`0.3000`** | **`30.0%`** | Invariant across window durations; 30 daily buckets with correct dates |
| **`TC-DORA-CFR-012`** | **Daily CFR Trend Time-Series Generation** | 14 days | 10 deployments distributed across 5 active calendar days and 9 idle days | **10** | **3** | **`0.3000`** | **`30.0%`** | Active days report daily CFR ratio/%; idle days report `cfr=null` |
| **`TC-DORA-CFR-013`** | **Multi-Tenant Row-Level Security (RLS) Isolation** | 14 days | Tenant A (10 deployments, CFR 30%) vs Tenant B (8 deployments, CFR 50%) | **10** | **3** | **`0.3000`** | **`30.0%`** | Cross-tenant data leakage strictly prevented; anti-enumeration 404 enforced |
| **`TC-DORA-CFR-014`** | **Unsupported Window Parameter Validation** | 7 days | Invoke CFR calculation with `window_days = 7` | **-** | **-** | **-** | **-** | Fail-closed: raises `ValueError: Unsupported window_days=7`; API returns HTTP 422 |

---

## 5. Exhaustive Test Scenario Specifications

### Scenario `TC-DORA-CFR-001`: Canonical 10-Deployment Benchmark Fixture ($30.0\%$ vs $40.0\%$)

#### Objective
Verify that on the canonical 10-deployment benchmark fixture, the Change Failure Rate calculation engine strictly produces $\text{CFR}_{\text{ratio}} = \mathbf{0.3000}$ ($\text{CFR}_{\%} = \mathbf{30.0\%}$), proving:
1. $D_1$ with both `FAILED` status and confirmed $+4\text{h}$ incident counts as **1** (not 2).
2. $D_2$ with `ROLLED_BACK` status counts as **1**.
3. $D_3$ with `SUCCESS` status and confirmed $+18\text{h}$ incident counts as **1**.
4. $D_4$ with `SUCCESS` status and confirmed $+30\text{h}$ incident counts as **0** (incident outside 24h window).
5. $D_5$ with `SUCCESS` status and $+6\text{h}$ cancelled incident counts as **0**.
6. $D_6 \dots D_{10}$ clean `SUCCESS` deployments count as **0**.
7. Result is strictly $\mathbf{30.0\%}$ and NOT $\mathbf{40.0\%}$.

#### Traceability
- **Parent Story:** `US-28` (`SCRUM-365`)
- **Primary Task:** `SCRUM-444` (`Task 28.3`)
- **SRS References:** §3.2.1, §3.2.2
- **Related Design:** [`docs/dora-change-failure-rate-design.md`](dora-change-failure-rate-design.md) (Task 28.1)

#### Preconditions & Temporal Coordinates
- Window: 14 days ($W = 14$), ending at $t_{\text{end}} = \text{2026-11-14T18:00:00Z}$.
- Base deployment timestamp $T_0 = t_{\text{end}} - 2\text{ days} = \text{2026-11-12T10:00:00Z}$.
- Project Authoritative CD Signal: `github_deployments_api`.

#### Detailed Entity Specifications

| Ref | Entity | Timestamp | Status / Type | Incident Details | Offset | Contributing Signal | Numerator | Denominator | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **$D_1$** | `Deployment` | `2026-11-12T11:00:00Z` ($T_0 + 1\text{h}$) | `FAILED` | `INC-1`: CONFIRMED, active=True, detected `2026-11-12T15:00:00Z` | $+4\text{h}$ | Outcome `FAILED` $\land$ Incident $\le 24\text{h}$ | **1** | **1** | Overlapping signals deduplicated via set union |
| **$D_2$** | `Deployment` | `2026-11-12T12:00:00Z` ($T_0 + 2\text{h}$) | `ROLLED_BACK` | None | N/A | Outcome `ROLLED_BACK` | **1** | **1** | Rollback outcome is terminal change failure |
| **$D_3$** | `Deployment` | `2026-11-12T13:00:00Z` ($T_0 + 3\text{h}$) | `SUCCESS` | `INC-2`: CONFIRMED, active=True, detected `2026-11-13T07:00:00Z` | $+18\text{h}$ | Incident $\le 24\text{h}$ | **1** | **1** | Confirmed incident within 24h marks deployment as failure |
| **$D_4$** | `Deployment` | `2026-11-12T14:00:00Z` ($T_0 + 4\text{h}$) | `SUCCESS` | `INC-3`: CONFIRMED, active=True, detected `2026-11-13T20:00:00Z` | $+30\text{h}$ | None (outside 24h) | **0** | **1** | $+30\text{h} > 24\text{h}$ boundary; excluded from failure |
| **$D_5$** | `Deployment` | `2026-11-12T15:00:00Z` ($T_0 + 5\text{h}$) | `SUCCESS` | `INC-4`: CANCELLED, active=False, detected `2026-11-12T21:00:00Z` | $+6\text{h}$ | None (cancelled) | **0** | **1** | Cancelled incident does not count as failure |
| **$D_6$** | `Deployment` | `2026-11-12T16:00:00Z` ($T_0 + 6\text{h}$) | `SUCCESS` | None | N/A | None (clean) | **0** | **1** | Clean successful production deployment |
| **$D_7$** | `Deployment` | `2026-11-12T17:00:00Z` ($T_0 + 7\text{h}$) | `SUCCESS` | None | N/A | None (clean) | **0** | **1** | Clean successful production deployment |
| **$D_8$** | `Deployment` | `2026-11-12T18:00:00Z` ($T_0 + 8\text{h}$) | `SUCCESS` | None | N/A | None (clean) | **0** | **1** | Clean successful production deployment |
| **$D_9$** | `Deployment` | `2026-11-12T19:00:00Z` ($T_0 + 9\text{h}$) | `SUCCESS` | None | N/A | None (clean) | **0** | **1** | Clean successful production deployment |
| **$D_{10}$** | `Deployment` | `2026-11-12T20:00:00Z` ($T_0 + 10\text{h}$) | `SUCCESS` | None | N/A | None (clean) | **0** | **1** | Clean successful production deployment |
| **Total** | | | | | | | **3** | **10** | **$\text{CFR} = 3 / 10 = \mathbf{0.3000}$ ($\mathbf{30.0\%}$)** |

#### Mathematical Calculation
$$\text{Total Production Deployments } (|\mathcal{D}_{\text{prod}}|) = 10$$
$$\text{Failed Deployments } (|\mathcal{F}_{\text{prod}}|) = |\{D_1, D_2, D_3\}| = 3$$
$$\text{CFR}_{\text{ratio}} = \frac{3}{10} = \mathbf{0.3000}$$
$$\text{CFR}_{\%} = \frac{3}{10} \times 100.0\% = \mathbf{30.0\%}$$

#### Expected JSON Response DTO
```json
{
  "window_days": 14,
  "total_deployment_count": 10,
  "failed_deployment_count": 3,
  "change_failure_rate": 0.30,
  "change_failure_rate_percentage": 30.0,
  "failed_deployment_ids": [
    "01932c50-d001-7000-8000-000000000001",
    "01932c50-d002-7000-8000-000000000002",
    "01932c50-d003-7000-8000-000000000003"
  ],
  "daily_series": [
    {"date": "2026-11-01", "total_deployment_count": 0, "failed_deployment_count": 0, "change_failure_rate": null, "change_failure_rate_percentage": null},
    {"date": "2026-11-12", "total_deployment_count": 10, "failed_deployment_count": 3, "change_failure_rate": 0.30, "change_failure_rate_percentage": 30.0},
    {"date": "2026-11-14", "total_deployment_count": 0, "failed_deployment_count": 0, "change_failure_rate": null, "change_failure_rate_percentage": null}
  ],
  "window_start": "2026-10-31T18:00:00Z",
  "window_end": "2026-11-14T18:00:00Z"
}
```

#### Automated Verification Code Snippet
```python
def test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent():
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    deployments, incidents, joined_rows = _build_10_deployment_benchmark_fixture(
        base_time=window_end - timedelta(days=2),
    )

    result = compute_change_failure_rate(
        deployments,
        deployment_incidents=incidents,
        window_days=14,
        window_end=window_end,
    )

    assert result.total_deployment_count == 10
    assert result.failed_deployment_count == 3
    assert result.change_failure_rate == 0.30
    assert result.change_failure_rate_percentage == 30.0
    assert result.failed_deployment_ids == [deployments[0].id, deployments[1].id, deployments[2].id]

    # Explicit assertion: 30.0% vs 40.0%
    assert result.change_failure_rate != 0.40
    assert result.change_failure_rate_percentage != 40.0
```

---

### Scenario `TC-DORA-CFR-002`: The $30.0\%$ vs $40.0\%$ Defect Differential Analysis

#### Objective
Formalize the exact mathematical and algorithmic defect models that would erroneously produce $40.0\%$ instead of $30.0\%$, and assert that the platform engine protects against each of them.

#### Defect Contrast Models

```
+-------------------------------------------------------------------------------------------------------------------+
|                           THE 30.0% vs. 40.0% DEFECT DIFFERENTIAL COMPARISON                                      |
+--------------------------+----------------------------+-----------------------------+-----------------------------+
| Dimension                | Authoritative Engine (30%) | Bug A: Temporal Leak (40%)  | Bug B: Double Counting (40%)|
+--------------------------+----------------------------+-----------------------------+-----------------------------+
| D1 (FAILED + inc@+4h)    | 1 failure (distinct union) | 1 failure                   | 2 failures (double counted) |
| D2 (ROLLED_BACK)         | 1 failure                  | 1 failure                   | 1 failure                   |
| D3 (SUCCESS + inc@+18h)  | 1 failure (<=24h incident) | 1 failure                   | 1 failure                   |
| D4 (SUCCESS + inc@+30h)  | 0 failure (>24h window)    | 1 failure (LEAKAGE >24h)    | 0 failure                   |
| D5 (SUCCESS + inc@+6h X) | 0 failure (cancelled)      | 0 failure                   | 0 failure                   |
| D6..D10 (SUCCESS)        | 0 failure                  | 0 failure                   | 0 failure                   |
+--------------------------+----------------------------+-----------------------------+-----------------------------+
| Numerator / Denominator  | 3 / 10                     | 4 / 10                      | 4 / 10                      |
| Calculated CFR           | 30.0% (0.3000)             | 40.0% (0.4000) [DEFECTIVE]  | 40.0% (0.4000) [DEFECTIVE]  |
| Assertion Status         | EXPECTED / PASS            | STRICTLY REJECTED           | STRICTLY REJECTED           |
+--------------------------+----------------------------+-----------------------------+-----------------------------+
```

1. **Defect Model A — Missing or Loose 24-Hour Boundary Filter:**
   - Cause: Join condition on `deployment_incidents` joins on `di.deployment_id = d.id` without `di.detected_at <= d.deployed_at + INTERVAL '24 hours'`.
   - Impact: $D_4$'s incident detected at $+30\text{h}$ satisfies `is_not(None)` and increments the failed count.
   - Result: $\mathcal{F} = \{D_1, D_2, D_3, D_4\} \implies \text{CFR} = \frac{4}{10} = \mathbf{40.0\%}$.
   - Defense: PostgreSQL query enforces `AND di.detected_at <= d.deployed_at + INTERVAL '24 hours'`. Python service enforces `delta_seconds <= INCIDENT_CORRELATION_WINDOW_SECONDS` ($86,400\text{s}$).

2. **Defect Model B — Additive Signal Double-Counting on $D_1$:**
   - Cause: Computing failures as $\text{COUNT}(\text{FAILED}) + \text{COUNT}(\text{INCIDENTS})$ instead of distinct deployment IDs.
   - Impact: $D_1$ has status `FAILED` ($+1$) and an incident ($+1$).
   - Result: $\text{Numerator} = 1 (D_1 \text{ status}) + 1 (D_1 \text{ inc}) + 1 (D_2) + 1 (D_3) = 4 \implies \text{CFR} = \frac{4}{10} = \mathbf{40.0\%}$.
   - Defense: Set union over distinct IDs: `COUNT(DISTINCT d.id) FILTER (...)`.

3. **Defect Model C — Cancelled Incident Inclusion on $D_5$:**
   - Cause: Failure to filter `di.status = 'CONFIRMED'` and `di.is_active = TRUE`.
   - Impact: $D_5$'s cancelled incident at $+6\text{h}$ is treated as valid.
   - Result: $\mathcal{F} = \{D_1, D_2, D_3, D_5\} \implies \text{CFR} = \frac{4}{10} = \mathbf{40.0\%}$.
   - Defense: Status and active flag filters in both SQL and Python.

---

### Scenario `TC-DORA-CFR-003`: SQL `LEFT JOIN` Row Multiplication Deduplication

#### Objective
Verify that when a deployment is linked to multiple confirmed incidents within 24 hours, the resulting row multiplication in a relational `LEFT JOIN` is deduplicated, counting the deployment exactly once in both the denominator and numerator.

#### Preconditions & Inputs
- Deployment $D_1$: `SUCCESS`, `deployed_at = 2026-11-12T12:00:00Z`.
- Incident $I_{1a}$: `CONFIRMED`, `detected_at = 2026-11-12T12:15:00Z` ($+15\text{m}$).
- Incident $I_{1b}$: `CONFIRMED`, `detected_at = 2026-11-12T12:45:00Z` ($+45\text{m}$).
- Incident $I_{1c}$: `CONFIRMED`, `detected_at = 2026-11-12T14:00:00Z` ($+2\text{h}$).
- Deployments $D_2, D_3$: Clean `SUCCESS`, no incidents.
- Raw SQL `LEFT JOIN` produces 5 rows:
  - Row 1: `(D1, I1a)`
  - Row 2: `(D1, I1b)`
  - Row 3: `(D1, I1c)`
  - Row 4: `(D2, NULL)`
  - Row 5: `(D3, NULL)`

#### Expected Outcome
- `total_deployment_count = 3` (not 5).
- `failed_deployment_count = 1` (not 3).
- `change_failure_rate = 0.3333` ($\frac{1}{3}$).
- `change_failure_rate_percentage = 33.33%`.

---

### Scenario `TC-DORA-CFR-004`: Zero-Deployment Window Null Semantics (14-Day)

#### Objective
Verify that an evaluation window containing zero qualifying production deployments returns `null` for both ratio and percentage, with 14 zero-filled daily buckets.

#### Execution & Inputs
- Window: 14 days ($W = 14$).
- Dataset: Empty collection `[]`.

#### Expected Outcome
- `total_deployment_count = 0`.
- `failed_deployment_count = 0`.
- `change_failure_rate = null` (`None`).
- `change_failure_rate_percentage = null` (`None`).
- `daily_series`: exactly 14 items, each with `total_deployment_count = 0`, `failed_deployment_count = 0`, `change_failure_rate = null`.

---

### Scenario `TC-DORA-CFR-005`: Zero-Deployment Window Null Semantics (30-Day)

#### Objective
Verify that the 30-day window evaluates to `null` with 30 zero-filled daily buckets when zero production deployments occur.

#### Execution & Inputs
- Window: 30 days ($W = 30$).
- Dataset: Empty collection `[]`.

#### Expected Outcome
- `total_deployment_count = 0`, `failed_deployment_count = 0`.
- `change_failure_rate = null`, `change_failure_rate_percentage = null`.
- `daily_series`: exactly 30 items with `change_failure_rate = null`.

---

### Scenario `TC-DORA-CFR-006`: Zero-Failure Window ($N \ge 1$ Clean Deployments)

#### Objective
Verify that a window containing successful production deployments with zero failures returns numeric `0.0000` ($0.0\%$), clearly distinguished from an empty window (`null`).

#### Execution & Inputs
- Window: 14 days.
- Deployments: 5 production deployments, all `status = 'SUCCESS'`, zero incidents.

#### Expected Outcome
- `total_deployment_count = 5`.
- `failed_deployment_count = 0`.
- `change_failure_rate = 0.0000` (`0.0`).
- `change_failure_rate_percentage = 0.0%`.
- `failed_deployment_ids = []`.

---

### Scenario `TC-DORA-CFR-007`: Non-Production (`staging`) Environment Exclusion

#### Objective
Verify that deployments targeting non-production environments (`staging`, `dev`) are excluded from both numerator and denominator, even when their status is `FAILED` or they have linked confirmed incidents.

#### Execution & Inputs
- $D_{\text{prod\_1}}$: `production`, `SUCCESS`, no incidents.
- $D_{\text{prod\_2}}$: `production`, `SUCCESS`, no incidents.
- $D_{\text{staging\_failed}}$: `staging`, `FAILED`, no incidents.
- $D_{\text{staging\_inc}}$: `staging`, `SUCCESS`, linked confirmed incident at $+1\text{h}$.

#### Expected Outcome
- `total_deployment_count = 2` (only prod deployments).
- `failed_deployment_count = 0`.
- `change_failure_rate = 0.0000` ($0.0\%$).

---

### Scenario `TC-DORA-CFR-008`: Authoritative CD Signal Source Mismatch Exclusion

#### Objective
Verify that deployments generated by secondary or non-authoritative CD signal sources are filtered out.

#### Preconditions & Inputs
- Project configured with `authoritative_cd_signal = 'github_deployments_api'`.
- $D_1$: `production`, `SUCCESS`, signal `github_deployments_api` (matched).
- $D_2$: `production`, `SUCCESS`, signal `github_deployments_api` (matched).
- $D_3$: `production`, `FAILED`, signal `argocd_postsync_webhook` (mismatched).
- $D_4$: `production`, `SUCCESS` + confirmed incident, signal `direct_deployment_api` (mismatched).

#### Expected Outcome
- `total_deployment_count = 2` ($D_1, D_2$).
- `failed_deployment_count = 0`.
- `change_failure_rate = 0.0000` ($0.0\%$).

---

### Scenario `TC-DORA-CFR-009`: Strict 24-Hour Boundary Edge Cases

#### Objective
Verify precise boundary conditions for incident detection timestamps:
1. Exact upper bound: $+24\text{h } 00\text{m } 00\text{s}$ ($86,400\text{ seconds}$) is **INCLUDED**.
2. Beyond upper bound: $+24\text{h } 00\text{m } 01\text{s}$ ($86,401\text{ seconds}$) is **EXCLUDED**.
3. Negative delta: incident detected before deployment completed ($\Delta t = -300\text{s}$) is **EXCLUDED**.

#### Preconditions & Inputs
- $D_{\text{exact}}$: `SUCCESS`, `deployed_at = T0`. Incident at $T_0 + 86,400\text{s}$ ($+24\text{h}$).
- $D_{\text{late}}$: `SUCCESS`, `deployed_at = T0 + 1h`. Incident at $(T_0 + 1\text{h}) + 86,401\text{s}$ ($+24\text{h } 1\text{s}$).
- $D_{\text{pre}}$: `SUCCESS`, `deployed_at = T0 + 2h`. Incident at $(T_0 + 2\text{h}) - 300\text{s}$ ($-5\text{m}$).

#### Expected Outcome
- $D_{\text{exact}}$: `has_confirmed_incident = True` $\implies$ Failed.
- $D_{\text{late}}$: `has_confirmed_incident = False` $\implies$ Clean.
- $D_{\text{pre}}$: `has_confirmed_incident = False` $\implies$ Clean.
- Total = 3, Failed = 1, CFR = $\frac{1}{3} = \mathbf{33.33\%}$.

---

### Scenario `TC-DORA-CFR-010`: Incident Lifecycle Status & Inactive Flag Filtering

#### Objective
Verify that incidents with statuses other than `CONFIRMED` or with `is_active = FALSE` never mark a successful deployment as a change failure.

#### Inputs
- $D_1$: `SUCCESS` + Incident with `status = 'FALSE_POSITIVE'`, `is_active = False`.
- $D_2$: `SUCCESS` + Incident with `status = 'CANCELLED'`, `is_active = False`.
- $D_3$: `SUCCESS` + Incident with `status = 'CONFIRMED'`, `is_active = False`.

#### Expected Outcome
- Total = 3, Failed = 0, CFR = $\mathbf{0.0\%}$.

---

### Scenario `TC-DORA-CFR-011`: Rolling 30-Day Window Evaluation with Canonical Fixture

#### Objective
Verify that evaluating the canonical 10-deployment benchmark fixture over a 30-day window ($W = 30$) produces the identical $30.0\%$ CFR while generating 30 daily trend buckets.

#### Expected Outcome
- `window_days = 30`.
- `total_deployment_count = 10`.
- `failed_deployment_count = 3`.
- `change_failure_rate = 0.3000` ($30.0\%$).
- `daily_series`: exactly 30 UTC calendar day buckets.

---

### Scenario `TC-DORA-CFR-012`: Daily CFR Trend Time-Series Generation

#### Objective
Verify daily bucketization over the evaluation window:
- Dates with deployments report exact daily total, failed count, ratio, and percentage.
- Dates with zero deployments report total = 0, failed = 0, ratio = `null`, percentage = `null`.
- Sum of daily totals equals window total ($10$); sum of daily failed equals window failed ($3$).

#### Expected Outcome
- All 14 (or 30) dates are contiguous UTC calendar dates.
- Sum of `bucket.total_deployment_count == 10`.
- Sum of `bucket.failed_deployment_count == 3`.

---

### Scenario `TC-DORA-CFR-013`: Multi-Tenant RLS Boundary & Anti-Enumeration Isolation

#### Objective
Verify that executing the CFR query under Tenant A's session context strictly isolates Tenant A's deployments and incidents, completely hiding Tenant B's data.

#### Execution & Inputs
- Tenant A: 10 production deployments (canonical fixture), CFR = $30.0\%$.
- Tenant B: 8 production deployments (4 FAILED), CFR = $50.0\%$.
- Query executed with `SET LOCAL app.current_tenant_id = :tenant_a_id`.

#### Expected Outcome
- Tenant A result: `total = 10`, `failed = 3`, `cfr = 0.30` ($30.0\%$). Zero rows from Tenant B are aggregated.
- Attempting to query Tenant B's project under Tenant A's session returns 0 rows (HTTP 404 in REST API).

---

### Scenario `TC-DORA-CFR-014`: Unsupported Window Parameter Validation

#### Objective
Verify that passing an unsupported window duration (e.g., `window_days = 7`, `60`, or negative values) is rejected fail-closed.

#### Execution & Inputs
- Call `compute_change_failure_rate(..., window_days=7)`.

#### Expected Outcome
- Raises `ValueError: Unsupported window_days=7. Expected one of (14, 30).`
- REST API endpoint returns `HTTP 422 Unprocessable Entity`.

---

## 6. Automated Pytest Verification Suite Mapping (`Task 28.4` / `SCRUM-445`)

The scenarios specified in this design document are verified by the automated pytest suite in [`backend/tests/test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py):

| Scenario ID | Test Method in Test Suite (`test_dora_change_failure_rate.py`) | Target Invariant & Scope | Status |
| :--- | :--- | :--- | :--- |
| **`TC-DORA-CFR-001`** | `test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent` | Canonical 10-deployment benchmark fixture: D1..D10, 30.0% CFR | **Verified** |
| **`TC-DORA-CFR-002`** | `test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent` | 30.0% vs. 40.0% defect contrast (temporal leakage, double-count) | **Verified** |
| **`TC-DORA-CFR-003`** | `test_task_28_2_left_join_row_multiplication_deduplicated_by_distinct_id` | Deduplication of multiple incidents on single deployment | **Verified** |
| **`TC-DORA-CFR-004`** | `test_task_28_2_empty_window_returns_none_vs_zero_failure_window_returns_zero` | 14-day zero-deployment window returns `null` | **Verified** |
| **`TC-DORA-CFR-005`** | `test_task_28_2_empty_window_returns_none_vs_zero_failure_window_returns_zero` | 30-day zero-deployment window returns `null` | **Verified** |
| **`TC-DORA-CFR-006`** | `test_task_28_2_empty_window_returns_none_vs_zero_failure_window_returns_zero` | Clean production deployments evaluate to `0.00` ($0.0\%$) | **Verified** |
| **`TC-DORA-CFR-007`** | `test_task_28_2_incident_correlation_24h_boundary_and_staging_exclusion` | Staging environment exclusion | **Verified** |
| **`TC-DORA-CFR-008`** | `test_task_28_2_sql_queries_and_calculate_rolling_change_failure_rate_async` | Authoritative CD signal source enforcement | **Verified** |
| **`TC-DORA-CFR-009`** | `test_task_28_2_incident_correlation_24h_boundary_and_staging_exclusion` | Exact 24h boundary and negative delta exclusion | **Verified** |
| **`TC-DORA-CFR-010`** | `test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent` | Cancelled / False positive / Inactive incident filtering | **Verified** |
| **`TC-DORA-CFR-011`** | `test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent` | 30-day window evaluation with canonical fixture | **Verified** |
| **`TC-DORA-CFR-012`** | `test_task_28_2_daily_trend_series_and_window_validation` | Daily time-series generation and idle day `null` representation | **Verified** |
| **`TC-DORA-CFR-013`** | `test_task_28_2_live_postgres_10_deployment_benchmark_e2e` | Live PostgreSQL 16 E2E multi-tenant RLS isolation | **Verified** |
| **`TC-DORA-CFR-014`** | `test_task_28_2_daily_trend_series_and_window_validation` | Unsupported `window_days` raises `ValueError` | **Verified** |

---

## 7. Traceability & Acceptance Criteria Verification Matrix

| Requirement / Acceptance Criterion | Verification Status | Covering Scenario(s) | Implementation Reference |
| :--- | :--- | :--- | :--- |
| **Specify 10 production deployments (D1..D10)** | **Documented & Verified** | `TC-DORA-CFR-001`, §1.2, §5 | [`_build_10_deployment_benchmark_fixture`](../backend/tests/test_dora_change_failure_rate.py#L151-L245) |
| **D1 (FAILED + confirmed Jira incident at +4h — counts as 1)** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-002` | [`test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py#L175-L181) |
| **D2 (ROLLED_BACK — counts as 1)** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-002` | [`test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py#L182-L188) |
| **D3 (SUCCESS + confirmed Jira incident at +18h — counts as 1)** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-002` | [`test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py#L189-L195) |
| **D4 (SUCCESS + Jira incident at +30h — outside 24h window, counts as 0)** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-002`, `TC-DORA-CFR-009` | [`test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py#L196-L202) |
| **D5 (SUCCESS + Jira incident at +6h later Cancelled — counts as 0)** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-002`, `TC-DORA-CFR-010` | [`test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py#L203-L209) |
| **D6..D10 (SUCCESS — count as 0)** | **Documented & Verified** | `TC-DORA-CFR-001` | [`test_dora_change_failure_rate.py`](../backend/tests/test_dora_change_failure_rate.py#L210-L219) |
| **Total = 3 / 10 = 30.0% CFR result** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-011` | [`compute_change_failure_rate`](../backend/app/dora/service.py#L570-L693) |
| **Asserting 30% vs 40% (preventing +30h leakage & double-count)** | **Documented & Verified** | `TC-DORA-CFR-001`, `TC-DORA-CFR-002` | [`test_task_28_3_canonical_10_deployment_benchmark_asserting_30_percent_vs_40_percent`](../backend/tests/test_dora_change_failure_rate.py#L315-L425) |
| **Fixture documented with timestamps, statuses, and expected 30.0% CFR result** | **Documented & Verified** | Complete specification §1, §4, §5 | [`docs/dora-change-failure-rate-test-scenarios.md`](dora-change-failure-rate-test-scenarios.md) |
