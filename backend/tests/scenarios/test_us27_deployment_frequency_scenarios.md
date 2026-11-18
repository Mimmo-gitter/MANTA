# US-27: Rolling 14-Day & 30-Day DORA Deployment Frequency Calculation Test Scenarios

**Task:** `SCRUM-440` (`Task 27.3`: Create test scenarios with 6 successful production, 3 staging and 1 failed deployment over 14 days (0.43/day))  
**Parent User Story:** `SCRUM-364` (`US-27`: Rolling 14-Day & 30-Day DORA Deployment Frequency)  
**Related Tickets:**  
- `SCRUM-438`: Design rolling 14-day and 30-day Deployment Frequency formula, daily bucketization, and rounding precision (`Task 27.1`, [`docs/dora-deployment-frequency-formula-design.md`](../../docs/dora-deployment-frequency-formula-design.md))  
- `SCRUM-439`: Implement rolling 14-day and 30-day Deployment Frequency calculation engine and zero-filled daily series (`Task 27.2`, [`backend/app/dora/service.py`](../app/dora/service.py), [`backend/app/dora/schemas.py`](../app/dora/schemas.py))  
- `SCRUM-441`: Automated pytest integration test suite for rolling 14-day & 30-day Deployment Frequency (`Task 27.4`, [`backend/tests/test_dora_deployment_frequency.py`](../test_dora_deployment_frequency.py))  
- `SCRUM-427` / `SCRUM-431`: Production deployment ingestion pipeline and outcome mapping (`US-25` / `Task 25.1` / `Task 25.5`, [`docs/deployment-signals-outcomes-and-staging-exclusion-test-scenarios.md`](../../docs/deployment-signals-outcomes-and-staging-exclusion-test-scenarios.md))  
- `SCRUM-384`: Deployment signal selection persistence and DORA response reporting (`US-18` / `Task 18.4`, [`docs/deployment-signal-selection-persistence-and-dora-response-reporting-test-scenarios.md`](../../docs/deployment-signal-selection-persistence-and-dora-response-reporting-test-scenarios.md))  
- `SCRUM-442`: DORA Change Failure Rate formula design and calculation (`US-28`)  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:** [`backend/app/dora/service.py`](../app/dora/service.py), [`backend/app/dora/schemas.py`](../app/dora/schemas.py), [`backend/app/dora/router.py`](../app/dora/router.py), [`backend/app/deployments/service.py`](../app/deployments/service.py)  
**Authoritative Automated Test Suites:** [`backend/tests/test_dora_deployment_frequency.py`](../test_dora_deployment_frequency.py)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us27_deployment_frequency_scenarios.md`](test_us27_deployment_frequency_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Establish the formal, authoritative verification scenarios and test specifications for **rolling 14-day and 30-day DORA Deployment Frequency calculations** in the M.A.N.T.A. Engineering Intelligence Platform (`SCRUM-440` / `Task 27.3`).

This specification formalizes exact fixtures, mathematical formulations, window boundaries, environment and status filtering rules, authoritative signal matching, zero-filled daily series, and multi-tenant isolation guarantees.

### 1.2 Implementation Scope & Core Scenarios

In accordance with Jira Task `SCRUM-440`, this specification explicitly details and verifies:

1. **The Canonical 14-Day Baseline Fixture (`0.43/day`):**
   - **6** production deployments with status `SUCCESS`.
   - **3** deployments targeting non-production environments (`staging`) — strictly excluded.
   - **1** production deployment with status `FAILED` — strictly excluded.
   - **1** production deployment with status `ROLLED_BACK` — strictly excluded.
   - Mathematical rate: $\text{round}_2(6 / 14) = \mathbf{0.43}\text{ deployments/day}$.
   - Daily bucketization: exactly 14 UTC calendar days with zero-filled counts summing to `6`.

2. **The 30-Day Rolling Window Calculation with the Same Fixture (`0.20/day`):**
   - Evaluating the identical 6 successful production deployments over a 30-day window ($W = 30$).
   - Mathematical rate: $6 / 30 = \mathbf{0.20}\text{ deployments/day}$.
   - Daily bucketization: exactly 30 UTC calendar days with zero-filled counts summing to `6`.

3. **The 0-Deployment Window Behavior (`0.00` vs. `null`):**
   - Window with zero qualifying deployments (or zero recorded deployments).
   - Rate value: numeric $\mathbf{0.00}$ (float `0.0`), strictly **not** `null`, NaN, or raising division-by-zero errors.
   - Daily series: $W$ zero-filled buckets, each reporting `count = 0`.
   - Clear contract distinction between numeric zero frequency and non-configured integrations.

4. **Authoritative CD Signal Filtering:**
   - Deployments originating from non-authoritative CD signal sources are strictly excluded from the numerator to prevent double-counting.

5. **Window Temporal Boundaries & UTC Normalization:**
   - Strict inclusive boundaries $[t_{\text{end}} - W\text{ days},\ t_{\text{end}}]$.
   - Deployments falling before the window or in the future are strictly excluded.
   - Date grouping executed purely in UTC calendar dates, unaffected by DST or server timezone.

6. **Multi-Tenant Row-Level Security & Project Isolation:**
   - PostgreSQL RLS enforces that deployments belonging to other tenants or other projects never leak into calculations.

---

## 2. Core Mathematical Formulations & Architectural Invariants

```
+-------------------------------------------------------------------------------------------------------------------+
|                        DORA DEPLOYMENT FREQUENCY CALCULATION TOPOLOGY (US-27)                                     |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Authoritative Production Query   | 2. Canonical Mathematical Formula   | 3. Zero-Filled UTC Series             |
|    (PostgreSQL 16 & Tenant RLS)     |    (Half-Up Rounding to 2 Decimals) |    (generate_series UTC Daily Buckets)|
+-------------------------------------+-------------------------------------+---------------------------------------+
| * environment == 'production'       | * DF_14 = round(count / 14, 2)      | * Exactly W date buckets (14 or 30)   |
| * status == 'SUCCESS'               | * DF_30 = round(count / 30, 2)      | * Default count = 0 (zero-filled)     |
| * signal_source == project.signal   | * Half-up: 6 / 14 = 0.42857 -> 0.43 | * Sum(bucket.count) == count          |
| * deployed_at in [t_end-W, t_end]   | * Zero deploy: 0 / W -> 0.00        | * UTC date grouping (midnight-aligned)|
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                            BOUNDARY, SECURITY & PRIVACY INVARIANTS                                                |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Strict Exclusion Boundaries                     | 5. Multi-Tenant RLS & Anti-Enumeration                       |
+----------------------------------------------------+--------------------------------------------------------------+
| * Staging/Dev/QA/Preview -> Excluded (0 in DF)     | * FORCE ROW LEVEL SECURITY on deployments & projects         |
| * FAILED / ROLLED_BACK -> Excluded (0 in DF)       | * SET LOCAL app.current_tenant_id = :tenant_id               |
| * Non-authoritative CD signals -> Excluded         | * Cross-tenant query returns 0 rows (anti-enumeration 404)    |
| * Timestamp < t_end - W or > t_end -> Excluded     | * Soft-deleted projects (deleted_at IS NOT NULL) -> Excluded |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Mathematical Rate Formulation & Half-Up Rounding
Let $W \in \{14, 30\}$ be the rolling window duration in days, and $t_{\text{end}}$ be the window upper boundary in UTC.
The qualifying production deployment set $\mathcal{D}^*_{\text{auth}}(W)$ is defined as:

$$\mathcal{D}^*_{\text{auth}}(W) = \left\{ d \in \text{deployments} \;\middle|\;
\begin{array}{l}
d.\text{tenant\_id} = \tau \;\land\; d.\text{project\_id} = p \;\land\; \\
d.\text{environment} = \text{'production'} \;\land\; \\
d.\text{status} = \text{'SUCCESS'} \;\land\; \\
d.\text{authoritative\_signal\_source} = \text{project.authoritative\_cd\_signal} \;\land\; \\
d.\text{deployed\_at} \ge (t_{\text{end}} - W\text{ days}) \;\land\; d.\text{deployed\_at} \le t_{\text{end}}
\end{array}
\right\}$$

The rolling Deployment Frequency $\text{DF}_W$ is:

$$\text{DF}_W = \begin{cases}
0.00, & \text{if } |\mathcal{D}^*_{\text{auth}}(W)| = 0 \\
\text{round}_2\!\left(\dfrac{|\mathcal{D}^*_{\text{auth}}(W)|}{W}\right), & \text{if } |\mathcal{D}^*_{\text{auth}}(W)| > 0
\end{cases}$$

Rounding uses standard half-up rounding to **2 decimal places**:
- $6 / 14 = 0.428571\dots \longrightarrow \mathbf{0.43}$
- $6 / 30 = 0.200000\dots \longrightarrow \mathbf{0.20}$
- $15 / 30 = 0.500000\dots \longrightarrow \mathbf{0.50}$
- $0 / 14 = 0.000000\dots \longrightarrow \mathbf{0.00}$

### Invariant 2: Environment Exclusion
Deployments where `environment != 'production'` (e.g. `staging`, `stage`, `dev`, `qa`, `preview`, `sandbox`) are **strictly excluded** from Deployment Frequency calculations.

### Invariant 3: Outcome Exclusion
Deployments with `status != 'SUCCESS'` (i.e. `FAILED` or `ROLLED_BACK`) are **strictly excluded** from Deployment Frequency calculations. They are captured exclusively by the Change Failure Rate metric (US-28) and MTTR (US-29).

### Invariant 4: Authoritative Signal Source Matching
Deployments whose `authoritative_signal_source` does not match the owning project's active `authoritative_cd_signal` are **strictly excluded**. This prevents double-counting when multiple delivery tools report to the same repository.

### Invariant 5: Zero-Deployment Value & Series Semantics
When no qualifying deployments exist in the rolling window ($|\mathcal{D}^*_{\text{auth}}(W)| = 0$):
- `successful_deployment_count` is integer `0`.
- `deployments_per_day` is numeric `0.00` (float `0.0`). It is **never** `null`, `None`, or `NaN`.
- `daily_series` contains exactly $W$ elements, each with `count = 0`.

### Invariant 6: Zero-Filled UTC Daily Time-Series
The time-series contains exactly $W$ continuous calendar days ending at `DATE(t_end)`:
- Range: $[\text{DATE}(t_{\text{end}}) - (W - 1)\text{ days},\ \text{DATE}(t_{\text{end}})]$.
- Every calendar day is present, ordered chronologically ascending.
- If no deployments occurred on a given UTC date, its bucket count is integer `0`.
- The sum of all bucket counts equals `successful_deployment_count`:
  $$\sum_{k=0}^{W-1} \text{daily\_series}[k].\text{count} = |\mathcal{D}^*_{\text{auth}}(W)|$$

---

## 3. Relational Schemas, Pydantic DTOs & API Contracts

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
from typing import Literal
from pydantic import BaseModel, Field


class DailyDeploymentCount(BaseModel):
    """Daily count of successful authoritative production deployments in UTC."""

    date: date
    count: int = Field(ge=0)


class DeploymentFrequencyResult(BaseModel):
    """Rolling 14-day or 30-day Deployment Frequency calculation result."""

    window_days: Literal[14, 30]
    successful_deployment_count: int = Field(ge=0)
    deployments_per_day: float = Field(ge=0.0)
    daily_series: list[DailyDeploymentCount]
    window_start: datetime
    window_end: datetime
```

### 3.3 Authoritative SQL Query (`PostgreSQL 16`)

```sql
SELECT
    COUNT(*) AS successful_deployment_count,
    ROUND((COUNT(*)::numeric / :window_days::numeric), 2) AS deployment_frequency
FROM deployments d
JOIN projects p
  ON p.id = d.project_id
 AND p.tenant_id = d.tenant_id
WHERE d.tenant_id = :tenant_id
  AND d.project_id = :project_id
  AND p.deleted_at IS NULL
  AND d.environment = 'production'
  AND d.status = 'SUCCESS'
  AND d.authoritative_signal_source = p.authoritative_cd_signal
  AND d.deployed_at >= (:window_end - make_interval(days => :window_days))
  AND d.deployed_at <= :window_end;
```

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Test Scenario Description | Window ($W$) | Input Dataset Summary | Expected Count | Expected Rate | Daily Series Sum | Target Acceptance Criteria |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-DORA-DF-001`** | **Canonical 14-Day Acceptance Fixture** | 14 days | 6 prod SUCCESS + 3 staging + 1 prod FAILED + 1 prod ROLLED_BACK | **6** | **`0.43`** | 6 (14 buckets) | Primary AC: Staging & Failed exclusion, half-up rounding ($6/14=0.43$) |
| **`TC-DORA-DF-002`** | **30-Day Window with Same Fixture** | 30 days | Same 6 prod SUCCESS + 3 staging + 1 prod FAILED + 1 prod ROLLED_BACK | **6** | **`0.20`** | 6 (30 buckets) | 30-day window scaling ($6/30=0.20$), 30 zero-filled daily buckets |
| **`TC-DORA-DF-003`** | **Zero-Deployment Window (14-Day)** | 14 days | 0 deployments in window | **0** | **`0.00`** | 0 (14 buckets) | Zero-deployment returns numeric `0.00` (not `null`), 14 zero buckets |
| **`TC-DORA-DF-004`** | **Zero-Deployment Window (30-Day)** | 30 days | 0 deployments in window | **0** | **`0.00`** | 0 (30 buckets) | Zero-deployment returns numeric `0.00` (not `null`), 30 zero buckets |
| **`TC-DORA-DF-005`** | **30-Day High-Cadence Fixture** | 30 days | 15 prod SUCCESS (within window) + 1 too old (day -31) + 1 future (+1 min) | **15** | **`0.50`** | 15 (30 buckets) | Boundary exclusions (too old & future), exact half-up rate ($15/30=0.50$) |
| **`TC-DORA-DF-006`** | **Authoritative Signal Filtering** | 14 days | 4 GitHub prod SUCCESS + 4 ArgoCD prod SUCCESS (Project uses GitHub) | **4** | **`0.29`** | 4 (14 buckets) | Non-authoritative signal exclusion ($4/14=0.29$), no double-counting |
| **`TC-DORA-DF-007`** | **Dynamic CD Signal Switch** | 14 days | Switch project authoritative signal from GitHub to ArgoCD | **4** | **`0.29`** | 4 (14 buckets) | Metric dynamically pivots to ArgoCD events upon project reconfiguration |
| **`TC-DORA-DF-008`** | **Exact Window Temporal Boundaries** | 14 days | Deployments at $t_{\text{end}}$, $t_{\text{start}}$, $t_{\text{start}} - 1\text{s}$, and $t_{\text{end}} + 1\text{s}$ | **2** | **`0.14`** | 2 (14 buckets) | Inclusive endpoints: $t_{\text{start}}$ & $t_{\text{end}}$ counted; outer boundary excluded |
| **`TC-DORA-DF-009`** | **Multi-Tenant Row-Level Security** | 14 days | Tenant A (6 deployments) vs Tenant B (10 deployments) | **6** | **`0.43`** | 6 (14 buckets) | Cross-tenant RLS isolation; Tenant B data never leaks into Tenant A metrics |
| **`TC-DORA-DF-010`** | **Intra-Tenant Project Isolation** | 14 days | Tenant A Project 1 (6 deployments) vs Project 2 (8 deployments) | **6** | **`0.43`** | 6 (14 buckets) | Project ID filter isolation; sibling project deployments excluded |
| **`TC-DORA-DF-011`** | **Soft-Deleted Project Exclusion** | 14 days | Project with `deleted_at IS NOT NULL` having 6 deployments | **0** | **`0.00`** | 0 (or 404) | Soft-deleted project query returns 0 rows; API returns 404 Not Found |
| **`TC-DORA-DF-012`** | **Unsupported Window Validation** | 7, 45, 0 | Arbitrary deployment dataset | N/A | N/A | N/A | Raises `ValueError` at service level and `HTTP 422` at REST API gateway |
| **`TC-DORA-DF-013`** | **UTC Midnight Boundary Grouping** | 14 days | Deployments at 23:59:59 UTC and 00:00:01 UTC | **2** | **`0.14`** | 2 (14 buckets) | Daily bucketization correctly splits adjacent UTC calendar dates |

---

## 5. Exhaustive Test Scenario Specifications

### Scenario `TC-DORA-DF-001`: Canonical 14-Day Acceptance Fixture (`0.43/day`)

- **Objective:** Verify that in a rolling 14-day window, exactly 6 successful production deployments are counted, while 3 staging deployments, 1 failed production deployment, and 1 rolled-back production deployment are strictly excluded, yielding a Deployment Frequency of `0.43 deployments/day` and a 14-day zero-filled series.
- **Traceability:** `US-27`, `SCRUM-440` (Task 27.3 Primary Scope), SRS §3.2.1, §3.2.2.
- **Preconditions:**
  - Active Tenant `tenant_id = 712020a1-0000-0000-0000-000000000001`
  - Active Project `project_id = 3b2c1d0e-1111-2222-3333-444455556666`
  - Project configuration: `authoritative_cd_signal = 'github_deployments_api'`, `deleted_at = NULL`
  - Window evaluation upper bound: `window_end = 2026-11-13T12:00:00Z`
  - Window evaluation lower bound: `window_start = 2026-10-30T12:00:00Z` (`window_end - 14 days`)
  - Target window duration: `window_days = 14`

#### Test Fixture Dataset (11 Total Deployments)

| Deployment ID | Environment | Status | Signal Source | Offset from `window_end` | Timestamp (`deployed_at` UTC) | Evaluation | Inclusion Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `DEP-01` | `production` | `SUCCESS` | `github_deployments_api` | $-1$ day | `2026-11-12T12:00:00Z` | **INCLUDED** | Prod, SUCCESS, Authoritative, In-Window |
| `DEP-02` | `production` | `SUCCESS` | `github_deployments_api` | $-1$ day $-2$ hrs | `2026-11-12T10:00:00Z` | **INCLUDED** | Prod, SUCCESS, Authoritative, In-Window (Same day as DEP-01) |
| `DEP-03` | `production` | `SUCCESS` | `github_deployments_api` | $-4$ days | `2026-11-09T12:00:00Z` | **INCLUDED** | Prod, SUCCESS, Authoritative, In-Window |
| `DEP-04` | `production` | `SUCCESS` | `github_deployments_api` | $-7$ days | `2026-11-06T12:00:00Z` | **INCLUDED** | Prod, SUCCESS, Authoritative, In-Window |
| `DEP-05` | `production` | `SUCCESS` | `github_deployments_api` | $-10$ days | `2026-11-03T12:00:00Z` | **INCLUDED** | Prod, SUCCESS, Authoritative, In-Window |
| `DEP-06` | `production` | `SUCCESS` | `github_deployments_api` | $-13$ days | `2026-10-31T12:00:00Z` | **INCLUDED** | Prod, SUCCESS, Authoritative, In-Window |
| `DEP-07` | `staging` | `SUCCESS` | `github_deployments_api` | $-2$ days | `2026-11-11T12:00:00Z` | **EXCLUDED** | Non-production environment (`staging`) |
| `DEP-08` | `staging` | `SUCCESS` | `github_deployments_api` | $-5$ days | `2026-11-08T12:00:00Z` | **EXCLUDED** | Non-production environment (`staging`) |
| `DEP-09` | `staging` | `SUCCESS` | `github_deployments_api` | $-8$ days | `2026-11-05T12:00:00Z` | **EXCLUDED** | Non-production environment (`staging`) |
| `DEP-10` | `production` | `FAILED` | `github_deployments_api` | $-3$ days | `2026-11-10T12:00:00Z` | **EXCLUDED** | Terminal outcome failure (`FAILED != SUCCESS`) |
| `DEP-11` | `production` | `ROLLED_BACK` | `github_deployments_api` | $-6$ days | `2026-11-07T12:00:00Z` | **EXCLUDED** | Terminal outcome rollback (`ROLLED_BACK != SUCCESS`) |

#### Mathematical Rate Calculation
$$\text{count} = 6$$
$$\text{rate} = \frac{6}{14} = 0.42857142857142855\dots$$
$$\text{deployments\_per\_day} = \text{Decimal}(6) / \text{Decimal}(14) \xrightarrow[\text{ROUND\_HALF\_UP}]{\text{2 decimals}} \mathbf{0.43}$$

#### Expected Zero-Filled Daily Time-Series (14 Days)

| Bucket Index | Calendar Date (UTC) | Expected Count | Contributing Deployments |
| :--- | :--- | :--- | :--- |
| 0 | `2026-10-31` | `1` | `DEP-06` |
| 1 | `2026-11-01` | `0` | None (zero-filled) |
| 2 | `2026-11-02` | `0` | None (zero-filled) |
| 3 | `2026-11-03` | `1` | `DEP-05` |
| 4 | `2026-11-04` | `0` | None (zero-filled) |
| 5 | `2026-11-05` | `0` | `DEP-09` excluded (staging) |
| 6 | `2026-11-06` | `1` | `DEP-04` |
| 7 | `2026-11-07` | `0` | `DEP-11` excluded (ROLLED_BACK) |
| 8 | `2026-11-08` | `0` | `DEP-08` excluded (staging) |
| 9 | `2026-11-09` | `1` | `DEP-03` |
| 10 | `2026-11-10` | `0` | `DEP-10` excluded (FAILED) |
| 11 | `2026-11-11` | `0` | `DEP-07` excluded (staging) |
| 12 | `2026-11-12` | `2` | `DEP-01`, `DEP-02` |
| 13 | `2026-11-13` | `0` | None (zero-filled) |
| **Total** | | **6** | $\sum \text{counts} == \text{successful\_deployment\_count}$ |

#### Expected JSON Response DTO
```json
{
  "window_days": 14,
  "successful_deployment_count": 6,
  "deployments_per_day": 0.43,
  "daily_series": [
    {"date": "2026-10-31", "count": 1},
    {"date": "2026-11-01", "count": 0},
    {"date": "2026-11-02", "count": 0},
    {"date": "2026-11-03", "count": 1},
    {"date": "2026-11-04", "count": 0},
    {"date": "2026-11-05", "count": 0},
    {"date": "2026-11-06", "count": 1},
    {"date": "2026-11-07", "count": 0},
    {"date": "2026-11-08", "count": 0},
    {"date": "2026-11-09", "count": 1},
    {"date": "2026-11-10", "count": 0},
    {"date": "2026-11-11", "count": 0},
    {"date": "2026-11-12", "count": 2},
    {"date": "2026-11-13", "count": 0}
  ],
  "window_start": "2026-10-30T12:00:00Z",
  "window_end": "2026-11-13T12:00:00Z"
}
```

#### Automated Verification Code Snippet
```python
def test_acceptance_criteria_14_day_excludes_staging_and_failed():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        # 6 successful production deployments
        _make_deployment(deployed_at=window_end - timedelta(days=1)),
        _make_deployment(deployed_at=window_end - timedelta(days=1, hours=2)),
        _make_deployment(deployed_at=window_end - timedelta(days=4)),
        _make_deployment(deployed_at=window_end - timedelta(days=7)),
        _make_deployment(deployed_at=window_end - timedelta(days=10)),
        _make_deployment(deployed_at=window_end - timedelta(days=13)),
        # 3 staging deployments (excluded)
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=2)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=5)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=8)),
        # 1 failed production deployment (excluded)
        _make_deployment(status="FAILED", deployed_at=window_end - timedelta(days=3)),
        # 1 rolled-back production deployment (excluded)
        _make_deployment(status="ROLLED_BACK", deployed_at=window_end - timedelta(days=6)),
    ]

    result = compute_deployment_frequency(deployments, window_days=14, window_end=window_end)

    assert result.window_days == 14
    assert result.successful_deployment_count == 6
    assert result.deployments_per_day == 0.43
    assert len(result.daily_series) == 14
    assert sum(bucket.count for bucket in result.daily_series) == 6
    counts_by_date = {bucket.date: bucket.count for bucket in result.daily_series}
    assert counts_by_date[(window_end - timedelta(days=1)).date()] == 2
    assert counts_by_date[(window_end - timedelta(days=2)).date()] == 0
```

---

### Scenario `TC-DORA-DF-002`: 30-Day Window with Same 6-Deployment Fixture (`0.20/day`)

- **Objective:** Verify that evaluating the identical 11-deployment fixture over a 30-day window produces exactly 6 successful deployments, correctly calculates $6 / 30 = \mathbf{0.20}\text{ deployments/day}$, and emits exactly 30 zero-filled UTC daily buckets.
- **Traceability:** `US-27`, `SCRUM-440` (Task 27.3 30-day Scope), SRS §3.2.1.
- **Preconditions:** Same fixture as `TC-DORA-DF-001`, evaluated with `window_days = 30`, `window_end = 2026-11-13T12:00:00Z`.
- **Window Boundaries:**
  - `window_start = 2026-10-14T12:00:00Z` (`window_end - 30 days`)
  - `window_end = 2026-11-13T12:00:00Z`

#### Mathematical Rate Calculation
$$\text{count} = 6$$
$$\text{rate} = \frac{6}{30} = 0.2$$
$$\text{deployments\_per\_day} = \text{Decimal}(6) / \text{Decimal}(30) \xrightarrow[\text{ROUND\_HALF\_UP}]{\text{2 decimals}} \mathbf{0.20}$$

#### Daily Series Distribution (30 Days)
- Date range: `2026-10-15` through `2026-11-13` (30 calendar days).
- Non-zero buckets (5 dates):
  - `2026-10-31`: count = 1 (`DEP-06`)
  - `2026-11-03`: count = 1 (`DEP-05`)
  - `2026-11-06`: count = 1 (`DEP-04`)
  - `2026-11-09`: count = 1 (`DEP-03`)
  - `2026-11-12`: count = 2 (`DEP-01`, `DEP-02`)
- Zero-filled buckets (25 dates): count = 0.
- Total sum: $\sum \text{counts} = 1 + 1 + 1 + 1 + 2 = \mathbf{6}$.

#### Expected JSON Response DTO (Summary Excerpt)
```json
{
  "window_days": 30,
  "successful_deployment_count": 6,
  "deployments_per_day": 0.20,
  "daily_series": [
    {"date": "2026-10-15", "count": 0},
    {"date": "2026-10-31", "count": 1},
    {"date": "2026-11-03", "count": 1},
    {"date": "2026-11-06", "count": 1},
    {"date": "2026-11-09", "count": 1},
    {"date": "2026-11-12", "count": 2},
    {"date": "2026-11-13", "count": 0}
  ],
  "window_start": "2026-10-14T12:00:00Z",
  "window_end": "2026-11-13T12:00:00Z"
}
```

#### Automated Verification Code Snippet
```python
def test_30_day_window_with_same_fixture_yields_point_twenty():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        # 6 successful production deployments
        _make_deployment(deployed_at=window_end - timedelta(days=1)),
        _make_deployment(deployed_at=window_end - timedelta(days=1, hours=2)),
        _make_deployment(deployed_at=window_end - timedelta(days=4)),
        _make_deployment(deployed_at=window_end - timedelta(days=7)),
        _make_deployment(deployed_at=window_end - timedelta(days=10)),
        _make_deployment(deployed_at=window_end - timedelta(days=13)),
        # Excluded non-prod & failed
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=2)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=5)),
        _make_deployment(environment="staging", deployed_at=window_end - timedelta(days=8)),
        _make_deployment(status="FAILED", deployed_at=window_end - timedelta(days=3)),
        _make_deployment(status="ROLLED_BACK", deployed_at=window_end - timedelta(days=6)),
    ]

    result = compute_deployment_frequency(deployments, window_days=30, window_end=window_end)

    assert result.window_days == 30
    assert result.successful_deployment_count == 6
    assert result.deployments_per_day == 0.20
    assert len(result.daily_series) == 30
    assert sum(bucket.count for bucket in result.daily_series) == 6
```

---

### Scenario `TC-DORA-DF-003`: Zero-Deployment Window — Numeric Zero vs. Null Handling

- **Objective:** Verify that when zero qualifying deployments occur within the evaluation window, the calculation engine deterministically returns integer `successful_deployment_count = 0`, float `deployments_per_day = 0.0` (SQL `0.00`), strictly avoiding `null`, `None`, or `ZeroDivisionError`, and returns an array of $W$ buckets each with integer `0`.
- **Traceability:** `US-27`, `SCRUM-440` (Task 27.3 Zero-Deployment Scope), SRS §3.2.2.
- **Preconditions:** Project registered with active CD signal; no deployments recorded, or all recorded deployments belong to staging/dev.
- **Inputs:** `deployments = []` (or only non-prod / failed rows), `window_days = 14`, `window_end = 2026-11-13T12:00:00Z`.

#### Semantics of `0.00` vs. `null`
1. **Numeric Zero (`0.00` / `0`):** Represents that the project was evaluated and actively measured, but **zero successful production deployments** occurred during the measured time span. This is the canonical return contract for all DORA rate calculations.
2. **Why `null` is Prohibited for Active Time Windows:**
   - Returning `null` causes frontend charting libraries (Chart.js, Recharts, D3) to break or treat the metric as unconfigured.
   - Returning `null` makes it impossible to distinguish between an inactive delivery cadence and an unconfigured or unintegrated project.
   - SQL `COALESCE(count, 0)` and Python `_round_rate(0, window_days) -> 0.0` guarantee a non-null scalar.

#### Expected Output State
```json
{
  "window_days": 14,
  "successful_deployment_count": 0,
  "deployments_per_day": 0.0,
  "daily_series": [
    {"date": "2026-10-31", "count": 0},
    {"date": "2026-11-01", "count": 0},
    {"date": "2026-11-02", "count": 0},
    {"date": "2026-11-03", "count": 0},
    {"date": "2026-11-04", "count": 0},
    {"date": "2026-11-05", "count": 0},
    {"date": "2026-11-06", "count": 0},
    {"date": "2026-11-07", "count": 0},
    {"date": "2026-11-08", "count": 0},
    {"date": "2026-11-09", "count": 0},
    {"date": "2026-11-10", "count": 0},
    {"date": "2026-11-11", "count": 0},
    {"date": "2026-11-12", "count": 0},
    {"date": "2026-11-13", "count": 0}
  ],
  "window_start": "2026-10-30T12:00:00Z",
  "window_end": "2026-11-13T12:00:00Z"
}
```

#### Verification Assertions
```python
def test_zero_deployments_returns_zero_rate_and_zero_series():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    result = compute_deployment_frequency([], window_days=14, window_end=window_end)

    assert result.successful_deployment_count == 0
    assert result.deployments_per_day == 0.0
    assert result.deployments_per_day is not None
    assert len(result.daily_series) == 14
    assert all(bucket.count == 0 for bucket in result.daily_series)
    assert all(bucket.count is not None for bucket in result.daily_series)
```

---

### Scenario `TC-DORA-DF-005`: 30-Day High-Cadence Fixture with Boundary Exclusions (`0.50/day`)

- **Objective:** Verify that in a 30-day window with 15 qualifying deployments, plus 1 deployment occurring 31 days prior (too old) and 1 deployment occurring in the future (after `window_end`), exactly 15 deployments are counted, producing `0.50 deployments/day`.
- **Traceability:** `US-27`, `SCRUM-440`, SRS §3.2.1.
- **Preconditions:** `window_days = 30`, `window_end = 2026-11-13T12:00:00Z`.
- **Inputs:**
  - 15 production SUCCESS deployments placed at `window_end - timedelta(days=i * 2)` for $i \in \{0, \dots, 14\}$.
  - 1 deployment at `window_end - timedelta(days=31)` (Out of window: too old).
  - 1 deployment at `window_end + timedelta(minutes=1)` (Out of window: future).

#### Calculation
$$\text{count} = 15$$
$$\text{rate} = \frac{15}{30} = 0.50\text{ deployments/day}$$

#### Verification Assertions
```python
def test_30_day_rolling_window_and_boundary_exclusions():
    window_end = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    deployments = [
        _make_deployment(deployed_at=window_end - timedelta(days=i * 2)) for i in range(15)
    ]
    # Out-of-window deployments
    deployments.append(_make_deployment(deployed_at=window_end - timedelta(days=31)))
    deployments.append(_make_deployment(deployed_at=window_end + timedelta(minutes=1)))

    result = compute_deployment_frequency(deployments, window_days=30, window_end=window_end)

    assert result.window_days == 30
    assert result.successful_deployment_count == 15
    assert result.deployments_per_day == 0.50
    assert len(result.daily_series) == 30
    assert sum(bucket.count for bucket in result.daily_series) == 15
```

---

### Scenario `TC-DORA-DF-006`: Authoritative CD Signal Source Filtering

- **Objective:** Verify that when multiple delivery integrations report deployments for the same project, the DORA Deployment Frequency calculator strictly counts events matching the project's configured `authoritative_cd_signal` and discards unselected channels.
- **Traceability:** `US-18` (SCRUM-384), `US-27` (SCRUM-440), SRS §2.4.2.
- **Preconditions:** Project `P1` has `authoritative_cd_signal = 'github_deployments_api'`.
- **Inputs (10 Production SUCCESS Deployments):**
  - 4 deployments with `authoritative_signal_source = 'github_deployments_api'`
  - 4 deployments with `authoritative_signal_source = 'argocd_postsync_webhook'`
  - 2 deployments with `authoritative_signal_source = 'direct_deployment_api'`
- **Expected Outcome:**
  - `successful_deployment_count = 4` (only GitHub deployments counted).
  - `deployments_per_day = round(4 / 14, 2) = 0.29`.
  - The 6 non-authoritative deployments are excluded from the numerator and the daily series.

#### Verification SQL
```sql
SELECT COUNT(*) 
FROM deployments d
JOIN projects p ON p.id = d.project_id AND p.tenant_id = d.tenant_id
WHERE d.project_id = :project_id
  AND d.environment = 'production'
  AND d.status = 'SUCCESS'
  AND d.authoritative_signal_source = p.authoritative_cd_signal;
-- Returns 4
```

---

### Scenario `TC-DORA-DF-007`: Dynamic CD Signal Switch Historical Recalculation

- **Objective:** Verify that when a project administrator updates `authoritative_cd_signal` from `'github_deployments_api'` to `'argocd_postsync_webhook'`, subsequent metric calculations immediately recalculate using the newly active signal without database corruption.
- **Traceability:** `US-18`, `US-27`, `SCRUM-384`, `SCRUM-440`.
- **Preconditions:** Project `P1` contains the 10 production SUCCESS deployments from `TC-DORA-DF-006` (4 GitHub, 4 ArgoCD, 2 Direct API).
- **Execution:**
  1. Calculate DF with `project.authoritative_cd_signal = 'github_deployments_api'`: returns `count = 4`, `rate = 0.29`.
  2. Execute `PATCH /api/v1/projects/{p_id}` setting `authoritative_cd_signal = 'argocd_postsync_webhook'`.
  3. Re-calculate DF with `window_days = 14`.
- **Expected Outcome:**
  - Metric immediately reflects `successful_deployment_count = 4` (now counting the 4 ArgoCD deployments).
  - Rate is `0.29`.
  - GitHub and Direct API deployments are excluded.

---

### Scenario `TC-DORA-DF-008`: Exact Window Temporal Boundary Conditions

- **Objective:** Verify inclusive temporal boundary conditions at $t_{\text{end}}$ and $t_{\text{end}} - W\text{ days}$, and exclusive boundaries at 1 microsecond outside the window.
- **Traceability:** `US-27`, `SCRUM-440`, Invariant 1.
- **Window:** $W = 14$ days, $t_{\text{end}} = \text{2026-11-13T12:00:00.000000Z}$, $t_{\text{start}} = \text{2026-10-30T12:00:00.000000Z}$.
- **Boundary Test Cases:**
  1. `deployed_at = 2026-11-13T12:00:00.000000Z` (Exact upper bound: $t_{\text{end}}$) $\longrightarrow$ **INCLUDED**.
  2. `deployed_at = 2026-10-30T12:00:00.000000Z` (Exact lower bound: $t_{\text{start}}$) $\longrightarrow$ **INCLUDED**.
  3. `deployed_at = 2026-10-30T11:59:59.999999Z` (1 $\mu\text{s}$ before $t_{\text{start}}$) $\longrightarrow$ **EXCLUDED**.
  4. `deployed_at = 2026-11-13T12:00:00.000001Z` (1 $\mu\text{s}$ after $t_{\text{end}}$) $\longrightarrow$ **EXCLUDED**.
- **Expected Calculation:** Exactly 2 deployments counted: $2 / 14 = 0.142857\dots \longrightarrow \mathbf{0.14}\text{ deployments/day}$.

---

### Scenario `TC-DORA-DF-009`: Multi-Tenant Row-Level Security Isolation

- **Objective:** Verify that under PostgreSQL `FORCE ROW LEVEL SECURITY`, a query executed in Tenant A's context cannot view or factor Tenant B's deployments into its Deployment Frequency metric.
- **Traceability:** `US-27`, `SCRUM-440`, `SCRUM-244` (Tenant RLS Isolation), SRS §2.3.1.
- **Preconditions:**
  - Tenant A owns Project A with 6 successful production deployments ($0.43/\text{day}$).
  - Tenant B owns Project B with 10 successful production deployments ($0.71/\text{day}$).
- **Execution:**
  ```python
  # Set tenant context to Tenant A
  await db.execute(text("SET LOCAL app.current_tenant_id = :t_a"), {"t_a": str(tenant_a_id)})
  result_a = await calculate_rolling_deployment_frequency(
      db, tenant_id=tenant_a_id, project_id=project_a_id, window_days=14
  )
  ```
- **Expected Outcome:**
  - `result_a.successful_deployment_count == 6`
  - `result_a.deployments_per_day == 0.43`
  - Zero deployments from Tenant B are counted.
  - Attempting to query `project_b_id` under Tenant A's session returns `0` rows (and HTTP 404 in API router), preserving anti-enumeration guarantees.

---

### Scenario `TC-DORA-DF-010`: Intra-Tenant Project Isolation

- **Objective:** Verify that within the same tenant, querying Project 1 never aggregates deployments from sibling Project 2.
- **Traceability:** `US-27`, `SCRUM-440`.
- **Preconditions:** Tenant A owns Project 1 (6 deployments) and Project 2 (8 deployments).
- **Expected Outcome:** Query for Project 1 strictly yields `successful_deployment_count = 6` ($0.43/\text{day}$); Project 2 strictly yields `successful_deployment_count = 8` ($0.57/\text{day}$).

---

### Scenario `TC-DORA-DF-011`: Soft-Deleted Project Exclusion

- **Objective:** Verify that if a project is soft-deleted (`projects.deleted_at IS NOT NULL`), DORA metric calculation queries join `p.deleted_at IS NULL` and return 0 deployments (or raise a ProjectNotFound exception).
- **Traceability:** `US-27`, `SCRUM-440`, SRS §3.1.2.
- **Expected Outcome:** `COUNT(*) == 0`, `deployment_frequency == 0.00`, API returns `HTTP 404 Not Found`.

---

### Scenario `TC-DORA-DF-012`: Unsupported Window Parameter Validation

- **Objective:** Verify that passing unsupported window sizes (e.g. `window_days = 7`, `window_days = 60`, `0`, or negative numbers) is rejected fail-closed.
- **Traceability:** `US-27`, `SCRUM-440`.
- **Execution:**
  - Invoke `compute_deployment_frequency(..., window_days=7)`.
- **Expected Outcome:**
  - Python raises `ValueError: Unsupported window_days=7. Expected one of (14, 30).`
  - REST API returns `HTTP 422 Unprocessable Entity`.

---

### Scenario `TC-DORA-DF-013`: UTC Midnight Boundary Grouping

- **Objective:** Verify that deployments occurring across the UTC midnight boundary (e.g. `23:59:59Z` and `00:00:01Z`) are correctly bucketized into distinct calendar dates without timezone drift.
- **Traceability:** `US-27`, `SCRUM-440`, Invariant 6.
- **Inputs:**
  - Deployment 1: `2026-11-12T23:59:59Z`
  - Deployment 2: `2026-11-13T00:00:01Z`
- **Expected Outcome:**
  - Bucket for `2026-11-12`: count = 1.
  - Bucket for `2026-11-13`: count = 1.
  - Sum of daily series = 2.

---

## 6. Acceptance Criteria Verification Summary

| Ticket Acceptance Criteria | Verification Status | Covering Scenario(s) |
| :--- | :--- | :--- |
| **Document 14-day fixture (6 prod SUCCESS, 3 staging, 1 prod FAILED -> 0.43/day)** | **Verified** | `TC-DORA-DF-001` |
| **Document 30-day window calculation (6 / 30 = 0.20/day)** | **Verified** | `TC-DORA-DF-002` |
| **Document 0-deployment window (null / 0 behavior)** | **Verified** | `TC-DORA-DF-003`, `TC-DORA-DF-004` |
| **Inputs and expected results fully documented for every scenario** | **Verified** | Complete matrix §4, scenarios §5 (`TC-DORA-DF-001` through `TC-DORA-DF-013`) |
| **Staging & non-production environment exclusion** | **Verified** | `TC-DORA-DF-001`, `TC-DORA-DF-002`, Invariant 2 |
| **Failed & rolled-back outcome exclusion** | **Verified** | `TC-DORA-DF-001`, Invariant 3 |
| **Authoritative CD signal source enforcement** | **Verified** | `TC-DORA-DF-006`, `TC-DORA-DF-007`, Invariant 4 |
| **PostgreSQL RLS multi-tenant isolation** | **Verified** | `TC-DORA-DF-009`, Invariant 5 |
