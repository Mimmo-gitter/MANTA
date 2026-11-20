# US-30: DORA Metrics REST API — OpenAPI 3.1 Compliance, Null vs Zero Semantics & Cross-Tenant Query Isolation Test Scenarios

**Task:** `SCRUM-460` (`Task 30.4`: Create test scenarios for OpenAPI 3.1 schema compliance, null vs 0 semantics and cross-tenant query isolation)  
**Parent User Story:** `SCRUM-368` (`US-30`: DORA Metrics REST API / `GET /api/v1/projects/{id}/dora-metrics`)  
**Related Tickets:**  
- `SCRUM-457`: Design OpenAPI 3.1 contract for `GET /api/v1/projects/{id}/dora-metrics` with nullable aggregates and daily trend series (`Task 30.1`, [`docs/dora-metrics-openapi-contract.md`](../../docs/dora-metrics-openapi-contract.md))  
- `SCRUM-458`: Implement `GET /api/v1/projects/{id}/dora-metrics` returning nullable aggregates, authoritative_deployment_signal, notes and trend array (`Task 30.2`, [`backend/app/dora/router.py`](../app/dora/router.py), [`backend/app/dora/service.py`](../app/dora/service.py), [`backend/app/dora/schemas.py`](../app/dora/schemas.py))  
- `SCRUM-459`: Optimize composite database queries with indexing for $< 300\text{ms}$ latency budget (`Task 30.3`)  
- `SCRUM-460`: Create test scenarios for OpenAPI 3.1 schema compliance, null vs 0 semantics and cross-tenant query isolation (`Task 30.4`, this specification)  
- `SCRUM-461`: Write automated pytest integration tests verifying OpenAPI 3.1 schema compliance, null vs 0 semantics and cross-tenant query isolation (`Task 30.5`, [`backend/tests/test_dora_openapi_contract.py`](../test_dora_openapi_contract.py))  
- `SCRUM-466`: Delivery Analytics DORA Dashboard UI verification scenarios (`US-31` / `Task 31.5`, [`docs/dora-dashboard-ui-verification-test-scenarios.md`](../../docs/dora-dashboard-ui-verification-test-scenarios.md))  
- `SCRUM-436`: DORA Lead Time for Changes calculation test scenarios (`US-26` / `Task 26.4`, [`backend/tests/scenarios/test_us26_lead_time_for_changes_scenarios.md`](test_us26_lead_time_for_changes_scenarios.md))  
- `SCRUM-440`: DORA Deployment Frequency calculation test scenarios (`US-27` / `Task 27.3`, [`backend/tests/scenarios/test_us27_deployment_frequency_scenarios.md`](test_us27_deployment_frequency_scenarios.md))  
- `SCRUM-444`: DORA Change Failure Rate calculation test scenarios (`US-28` / `Task 28.3`, [`backend/tests/scenarios/test_us28_change_failure_rate_scenarios.md`](test_us28_change_failure_rate_scenarios.md))  
- `SCRUM-448`: DORA Mean Time to Recovery calculation test scenarios (`US-29` / `Task 29.3`, [`backend/tests/scenarios/test_us29_mttr_calculation_scenarios.md`](test_us29_mttr_calculation_scenarios.md))  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:**  
- [`backend/app/dora/schemas.py`](../app/dora/schemas.py) (`DoraMetricsResponse`, `DoraAggregates`, `DoraDailyPoint`, `DoraTimeframeDays`)  
- [`backend/app/dora/service.py`](../app/dora/service.py) (`calculate_project_dora_metrics`, `assemble_dora_metrics_response`)  
- [`backend/app/dora/router.py`](../app/dora/router.py) (`GET /api/v1/dora/projects/{project_id}`)  
- [`backend/app/projects/router.py`](../app/projects/router.py) (`GET /api/v1/projects/{project_id}/dora-metrics`)  
**Authoritative Automated Test Suites:** [`backend/tests/test_dora_openapi_contract.py`](../test_dora_openapi_contract.py)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us30_dora_metrics_openapi_compliance_and_isolation_scenarios.md`](test_us30_dora_metrics_openapi_compliance_and_isolation_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Establish the definitive, authoritative test scenarios, contract specifications, and fixture assertions for the **DORA Metrics REST API Endpoint** (`GET /api/v1/projects/{id}/dora-metrics` and alias `GET /api/v1/dora/projects/{id}`) in `US-30` (`SCRUM-460` / `Task 30.4`).

This specification establishes end-to-end verification standards for:
1. **OpenAPI 3.1.0 / JSON Schema 2020-12 Contract Compliance:** Enforcing nullable union types (`anyOf: [{"type": "number"}, {"type": "null"}]`), required presence of all four metric keys in aggregate and daily objects, and rejection of `NaN` or `Infinity`.
2. **Explicit Null vs. Zero Semantics:** Guaranteeing that unmeasured/undefined windows (`null`) are mathematically and syntactically distinct from measured zeros (`0.0`), preventing dangerous distortions in customer delivery metrics.
3. **Cross-Tenant Row-Level Security (RLS) & Anti-Enumeration:** Guaranteeing that requests across tenant boundaries yield `HTTP 404 Not Found` rather than `403 Forbidden` or empty responses, preventing resource existence leaks.
4. **Sub-300ms Performance & Latency Budget:** Formulating benchmark test profiles asserting that composite aggregations across pull requests, deployments, incidents, and health failure sessions execute within $\le 300\text{ms}$.
5. **Timeframe Windowing & Contiguous Series:** Verifying rolling 14-day and 30-day aggregations accompanied by contiguous UTC daily trend vectors with zero date gaps.

---

### 1.2 Implementation Scope & Core Verification Scenarios

In accordance with Jira Task `SCRUM-460`, this document formalizes deterministic test cases across five primary operational dimensions:

```
+-------------------------------------------------------------------------------------------------------------------+
|                         US-30 / TASK 30.4 DORA METRICS REST API VERIFICATION TOPOLOGY                             |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Full Data Window (14d & 30d)     | 2. Zero-Deployment Window (null)    | 3. Successful Deploys with 0 Failures |
|    (Complete Telemetry Fixture)     |    (Undefined Aggregates)           |    (CFR = 0.0% vs MTTR = null)       |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * All 4 metrics non-null            | * 0 production deployments          | * >= 1 successful deployments         |
| * Lead Time = 23.50h                | * aggregates.* all null             | * CFR = 0.0% (measured zero)          |
| * Deployment Freq = 0.43/day        | * trend.* metrics all null          | * MTTR = null (0 failure sessions)    |
| * CFR = 0.0%                        | * Contiguous 14 or 30 daily buckets | * Lead Time = 23.5h (if PR linked)    |
| * MTTR = 1.80h                      | * Explanatory diagnostic notes      | * Rejects false "0.0h MTTR"           |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                            SECURITY, ISOLATION & PERFORMANCE BOUNDARIES                                           |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Cross-Tenant Query Isolation & Anti-Enumeration  | 5. Strict Latency Budget (< 300ms)                           |
+----------------------------------------------------+--------------------------------------------------------------+
| * Tenant B requests Tenant A project_id            | * High-volume dataset: 1,000 deploys, 500 PRs, 50 failures    |
| * PostgreSQL FORCE ROW LEVEL SECURITY filters rows | * Indexed composite joins on (tenant_id, project_id, date)   |
| * Endpoint returns HTTP 404 Not Found (not 403)    | * Wall-clock execution <= 300ms at P95/P99                   |
| * Soft-deleted projects return HTTP 404            | * Memory allocation overhead bounded                         |
+----------------------------------------------------+--------------------------------------------------------------+
```

---

### 1.3 Analysis of Previous CI Failures & Preventive Invariants

To guarantee first-pass green runs in GitHub Actions CI pipelines, this specification incorporates lessons learned from previous PRs:

| Historical Failure Mode | Cause in Past Pull Requests | Strict Prevention Invariant in Task 30.4 |
| :--- | :--- | :--- |
| **Ruff Markdown Python Linting Error** (`8ecb267`, `b527863`) | Ruff inspects Python code blocks inside `.md` documentation. Trailing newlines, unused imports, or lines exceeding 100 characters caused CI failure. | All code snippets in this specification use JSON, HTTP wire text, SQL, or strictly validated PEP 8 compliant code blocks verified with `python -m ruff format --check .` and `python -m ruff check .`. |
| **Pydantic v2 `NaN` / `Infinity` Serialization** (`3aa5e73`) | Standard `float` in Python allows `float("nan")` and `float("inf")`, which emit non-standard JSON and violate OpenAPI 3.1. | All schema definitions enforce `allow_inf_nan=False` on `float` fields. |
| **Cross-Tenant Status Leakage (403 vs 404)** (`f267424`) | Returning `403 Forbidden` on foreign tenant IDs allows attackers to scan and enumerate valid project UUIDs across tenants. | Multi-tenant RLS guarantees queries return 0 rows for foreign tenant contexts; API routes treat 0 rows identically to non-existent projects, returning `404 Not Found`. |
| **Timeframe Query vs Path Conflict** (`a76c6bd`) | Supporting both `timeframe_days=14` and `window=30d` without validation caused ambiguous state. | Parameter resolver mandates that if both are supplied, they must match (`14` with `"14d"`), else fail closed with `422 Unprocessable Content`. |

---

## 2. Core Architectural & Mathematical Invariants

### Invariant 1: OpenAPI 3.1.0 Nullable Representation (`anyOf`)
In OpenAPI 3.1.0 (JSON Schema 2020-12), the deprecated OpenAPI 3.0 `nullable: true` attribute is invalid. Nullable types must be represented as a union schema:
```json
{
  "anyOf": [
    {
      "type": "number",
      "minimum": 0.0
    },
    {
      "type": "null"
    }
  ]
}
```
All four fields in `DoraAggregates` and all four metric fields in `DoraDailyPoint` are required keys in JSON serialization, guaranteeing the key is always present even when its value is `null`.

---

### Invariant 2: Semantic Integrity of `null` vs `0.0`
A measured zero indicates that an event occurred and achieved optimal zero duration/rate. An undefined metric (`null`) indicates that no qualifying events occurred in the window:

$$\text{CFR} = \begin{cases} 
\text{null} & \text{if } |\mathcal{D}_{\text{prod}}| = 0 \\
0.0\% & \text{if } |\mathcal{D}_{\text{prod}}| \ge 1 \land |\mathcal{F}_{\text{prod}}| = 0 \\
\dfrac{|\mathcal{F}_{\text{prod}}|}{|\mathcal{D}_{\text{prod}}|} \times 100 & \text{if } |\mathcal{D}_{\text{prod}}| \ge 1 \land |\mathcal{F}_{\text{prod}}| \ge 1
\end{cases}$$

$$\text{MTTR} = \begin{cases} 
\text{null} & \text{if } |\mathcal{S}_{\text{resolved}}| = 0 \\
0.0\text{h} & \text{if } \exists\, s \in \mathcal{S}_{\text{resolved}} \text{ with } s.\text{resolved\_at} = s.\text{started\_at} \\
\dfrac{1}{|\mathcal{S}_{\text{resolved}}|} \sum (s.\text{resolved\_at} - s.\text{started\_at}) & \text{if } |\mathcal{S}_{\text{resolved}}| \ge 1
\end{cases}$$

$$\text{Lead Time} = \begin{cases}
\text{null} & \text{if } |\mathcal{P}_{\text{deployed}}| = 0 \\
0.0\text{h} & \text{if } \text{deployed\_at} = \text{first\_commit\_at} \\
\text{median}(T_{\text{deployed}} - T_{\text{first\_commit}}) & \text{if } |\mathcal{P}_{\text{deployed}}| \ge 1
\end{cases}$$

$$\text{Deployment Frequency} = \begin{cases}
\text{null} & \text{if telemetry is completely inactive (0 events)} \\
0.0\text{/day} & \text{if telemetry is active but 0 SUCCESS deployments occurred} \\
\text{round}\left(\dfrac{|\mathcal{D}_{\text{success}}|}{W}, 2\right) & \text{if } |\mathcal{D}_{\text{success}}| \ge 1
\end{cases}$$

---

### Invariant 3: Multi-Tenant RLS & Anti-Enumeration Boundary
Under PostgreSQL `FORCE ROW LEVEL SECURITY`, tenant context is established per transaction:
```sql
SET LOCAL app.current_tenant_id = :tenant_id;
```
When querying `projects` with `WHERE id = :project_id AND deleted_at IS NULL`:
- If the project belongs to another tenant, the query returns 0 rows.
- The router emits `HTTP 404 Not Found` with payload `{"detail": "Project '<project_id>' not found."}`.
- Under no circumstances may the endpoint return `403 Forbidden` for a project belonging to a foreign tenant.

---

### Invariant 4: Sub-300ms Query Execution Budget
The endpoint aggregates data from four domain tables:
1. `deployments` (filtered by `tenant_id`, `project_id`, `environment = 'production'`, `authoritative_signal_source`, `deployed_at`)
2. `pull_requests` (joined via commit reachability and PR merge tracking)
3. `deployment_incidents` (joined via `deployment_id`, status `'CONFIRMED'`, active within $\le 24\text{h}$)
4. `production_failures` (filtered by `tenant_id`, `project_id`, status `'RESOLVED'`, `started_at`)

To ensure $T_{\text{response}} < 300\text{ms}$ at scale, queries utilize composite indexes on `(tenant_id, project_id, deployed_at)` and `(tenant_id, project_id, started_at)`.

---

## 3. OpenAPI 3.1 Schemas & Database Relational Model

### 3.1 Pydantic Model Schema Definitions (`app.dora.schemas`)

```python
# Reference: backend/app/dora/schemas.py
from datetime import date
from enum import Enum
import uuid
from pydantic import BaseModel, ConfigDict, Field

from app.deployments.schemas import DeploymentSignalSource


class DoraWindow(str, Enum):
    WINDOW_14D = "14d"
    WINDOW_30D = "30d"


class DoraTimeframeDays(int, Enum):
    DAYS_14 = 14
    DAYS_30 = 30


class DoraAggregates(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    lead_time_hours: float | None = Field(
        ...,
        description="Median lead time in hours or null if no PRs deployed.",
        ge=0.0,
    )
    deployment_frequency_per_day: float | None = Field(
        ...,
        description="Daily deployment rate or null if telemetry is absent.",
        ge=0.0,
    )
    change_failure_rate_pct: float | None = Field(
        ...,
        description="Percentage of failed deployments [0.0, 100.0] or null.",
        ge=0.0,
        le=100.0,
    )
    mttr_hours: float | None = Field(
        ...,
        description="Mean recovery time in hours or null if 0 failures.",
        ge=0.0,
    )


class DoraDailyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    date: date = Field(..., description="UTC date string (YYYY-MM-DD)")
    lead_time_hours: float | None = Field(..., ge=0.0)
    deployment_frequency_per_day: float | None = Field(..., ge=0.0)
    change_failure_rate_pct: float | None = Field(..., ge=0.0, le=100.0)
    mttr_hours: float | None = Field(..., ge=0.0)


class DoraMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    project_id: uuid.UUID
    window: DoraWindow
    authoritative_deployment_signal: DeploymentSignalSource
    aggregates: DoraAggregates
    notes: list[str] = Field(default_factory=list)
    trend: list[DoraDailyPoint]
```

---

## 4. Deterministic Test Scenarios & Specifications

### Summary Matrix of Test Scenarios

| Scenario ID | Test Scope / Focus | Input Parameters | Expected Status | Key Assertions / Output Characteristics |
| :--- | :--- | :--- | :---: | :--- |
| `TC-DORA-API-001` | Full Data Window (14d) | `timeframe_days=14` | `200 OK` | All 4 aggregates non-null, exactly 14 trend items, diagnostic notes. |
| `TC-DORA-API-002` | Full Data Window (30d) | `timeframe_days=30` | `200 OK` | Window `"30d"`, all 4 aggregates non-null, exactly 30 trend items. |
| `TC-DORA-API-003` | Zero-Deployment Window | `timeframe_days=30` | `200 OK` | Aggregates all `null`, 30 trend points all `null`, explanatory note. |
| `TC-DORA-API-004` | Successful Deploys, Zero Failures | `timeframe_days=14` | `200 OK` | `CFR = 0.0%`, `MTTR = null`, `DF = 0.43`, `LT = 23.50`. |
| `TC-DORA-API-005` | Zero-Second Rapid Recovery | `timeframe_days=14` | `200 OK` | `MTTR = 0.0h` (measured instantaneous failover restoration). |
| `TC-DORA-API-006` | Cross-Tenant Isolation | Tenant B caller | `404 Not Found` | Anti-enumeration isolation: `{"detail": "Project '<id>' not found."}`. |
| `TC-DORA-API-007` | Missing / Soft-Deleted Project | Deleted project | `404 Not Found` | Soft-deleted project (`deleted_at IS NOT NULL`) returns 404. |
| `TC-DORA-API-008` | Latency & Performance Budget | High-volume DB | `200 OK` | Query execution + payload assembly latency $< 300\text{ms}$. |
| `TC-DORA-API-009` | OpenAPI 3.1 JSON Schema Validation | Schema introspection | N/A | `anyOf` nullable unions, required metric keys, bounds enforced. |
| `TC-DORA-API-010` | Authoritative Signal Filtering | Secondary signals | `200 OK` | Secondary webhook deployments excluded from calculations. |
| `TC-DORA-API-011` | Non-Production Environment Exclusion | Staging deploys | `200 OK` | Staging failures/deploys excluded from production DORA metrics. |
| `TC-DORA-API-012` | Parameter Validation & Conflicts | `timeframe=7` | `422 Unprocessable` | Rejects unsupported timeframes or conflicting query parameters. |
| `TC-DORA-API-013` | Authentication & RBAC Matrix | Unauth / No role | `401` / `403` | Rejects unauthenticated callers (401) and callers without DORA role (403). |
| `TC-DORA-API-014` | Route Equivalence Verification | Primary vs Alias | `200 OK` | `/api/v1/projects/{id}/dora-metrics` and `/api/v1/dora/projects/{id}` match. |
| `TC-DORA-API-015` | Daily Trend Contiguity & Continuity | 14d / 30d window | `200 OK` | Exactly 14 or 30 strictly monotonic UTC daily dates without gaps. |

---

### Scenario `TC-DORA-API-001`: Full Data Window (14-Day) With Complete Telemetry

#### 1. Objective & Description
Verify that when a project has complete telemetry across all four DORA domains in a 14-day window ($W = 14$), the API responds with `HTTP 200 OK`, all four aggregate fields populated with positive numbers, explanatory calculation notes, and a contiguous 14-item daily trend series.

#### 2. Fixture Preconditions & State
- **Tenant ID:** `11111111-1111-1111-1111-111111111111`
- **Project ID:** `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`
- **Authoritative CD Signal:** `github_deployments_api`
- **Window Range:** `2026-11-01T00:00:00Z` to `2026-11-14T23:59:59Z` ($W = 14$)
- **Data Ingested:**
  - **Deployments:** 6 successful production deployments ($6 / 14 = 0.43\text{ deploys/day}$).
  - **Pull Requests:** 6 PRs merged and deployed; median duration between first commit and deployment is $23.50\text{ hours}$.
  - **Incidents:** 0 linked Jira incidents.
  - **Change Failure Rate:** $0\text{ failures} / 6\text{ deployments} = 0.0\%$.
  - **Production Failures:** 1 resolved health failure session ($10:00 \rightarrow 11:48 = 1.80\text{ hours}$).

#### 3. Request
```http
GET /api/v1/projects/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/dora-metrics?timeframe_days=14 HTTP/1.1
Host: api.manta.local
Authorization: Bearer <JWT_TENANT_1_DEVELOPER>
Accept: application/json
```

#### 4. Expected Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "project_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "window": "14d",
  "authoritative_deployment_signal": "github_deployments_api",
  "aggregates": {
    "lead_time_hours": 23.5,
    "deployment_frequency_per_day": 0.43,
    "change_failure_rate_pct": 0.0,
    "mttr_hours": 1.8
  },
  "notes": [
    "Filtered strictly by authoritative deployment signal 'github_deployments_api'.",
    "change_failure_rate_pct is 0.0%: 6 production deployments succeeded with 0 failures or linked incidents."
  ],
  "trend": [
    {
      "date": "2026-11-01",
      "lead_time_hours": null,
      "deployment_frequency_per_day": 0.0,
      "change_failure_rate_pct": null,
      "mttr_hours": null
    },
    {
      "date": "2026-11-12",
      "lead_time_hours": 23.5,
      "deployment_frequency_per_day": 1.0,
      "change_failure_rate_pct": 0.0,
      "mttr_hours": 1.8
    },
    {
      "date": "2026-11-14",
      "lead_time_hours": null,
      "deployment_frequency_per_day": 0.0,
      "change_failure_rate_pct": null,
      "mttr_hours": null
    }
  ]
}
```

#### 5. Assertions & Acceptance Criteria
- `response.status_code == 200`
- `response.json()["window"] == "14d"`
- `len(response.json()["trend"]) == 14`
- `response.json()["aggregates"]["lead_time_hours"] == 23.5`
- `response.json()["aggregates"]["deployment_frequency_per_day"] == 0.43`
- `response.json()["aggregates"]["change_failure_rate_pct"] == 0.0`
- `response.json()["aggregates"]["mttr_hours"] == 1.8`

---

### Scenario `TC-DORA-API-002`: Full Data Window (30-Day) With Complete Telemetry

#### 1. Objective & Description
Verify that requesting the default 30-day window (`timeframe_days=30`) returns a 30-day aggregation window with exactly 30 contiguous UTC trend data points.

#### 2. Request
```http
GET /api/v1/projects/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/dora-metrics HTTP/1.1
Host: api.manta.local
Authorization: Bearer <JWT_TENANT_1_DEVELOPER>
Accept: application/json
```

#### 3. Expected Response & Assertions
- `status_code == 200`
- `response.json()["window"] == "30d"`
- `len(response.json()["trend"]) == 30`
- All 30 trend items have dates in strictly ascending chronological order:
  $$\text{trend}[i].\text{date} = \text{trend}[i-1].\text{date} + 1\text{ day}$$

---

### Scenario `TC-DORA-API-003`: Zero-Deployment Window (`null` Aggregates Semantics)

#### 1. Objective & Description
Verify that a project with zero deployment telemetry events in the window returns explicit `null` values for all four aggregates and for daily points, accompanied by explanatory notes.

#### 2. Fixture Preconditions
- Project exists under Tenant 1 (`bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb`).
- `deployments` table contains 0 rows for this project in the 30-day window.
- `production_failures` table contains 0 rows.
- `pull_requests` table contains 0 deployed PRs.

#### 3. Expected Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "project_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "window": "30d",
  "authoritative_deployment_signal": "argocd_postsync_webhook",
  "aggregates": {
    "lead_time_hours": null,
    "deployment_frequency_per_day": null,
    "change_failure_rate_pct": null,
    "mttr_hours": null
  },
  "notes": [
    "No authoritative production deployments recorded under signal 'argocd_postsync_webhook' in the 30d window."
  ],
  "trend": [
    {
      "date": "2026-10-16",
      "lead_time_hours": null,
      "deployment_frequency_per_day": null,
      "change_failure_rate_pct": null,
      "mttr_hours": null
    }
  ]
}
```

#### 4. Contrast Defect Model
- **Defect:** Returning `0.0` for `deployment_frequency_per_day` or `change_failure_rate_pct` when zero telemetry exists.
- **Impact:** Misrepresents absent telemetry as "zero deployments attempted" or "zero change failure rate".
- **Assertion:**
  ```python
  assert response.json()["aggregates"]["lead_time_hours"] is None
  assert response.json()["aggregates"]["deployment_frequency_per_day"] is None
  assert response.json()["aggregates"]["change_failure_rate_pct"] is None
  assert response.json()["aggregates"]["mttr_hours"] is None
  ```

---

### Scenario `TC-DORA-API-004`: Successful Deployments with Zero Failures (`CFR = 0.0%`, `MTTR = null`)

#### 1. Objective & Description
Verify that when production deployments have occurred successfully with zero failures and zero incidents:
- `change_failure_rate_pct` evaluates to numeric `0.0` (measured zero).
- `mttr_hours` evaluates to `null` (because zero outages occurred, so recovery time is undefined).

#### 2. Fixture Preconditions
- 5 production deployments with status `SUCCESS` in the 14-day window.
- 0 deployment incidents (`deployment_incidents` has 0 rows).
- 0 production failure sessions (`production_failures` has 0 rows).

#### 3. Expected Response
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "project_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "window": "14d",
  "authoritative_deployment_signal": "direct_deployment_api",
  "aggregates": {
    "lead_time_hours": 12.0,
    "deployment_frequency_per_day": 0.36,
    "change_failure_rate_pct": 0.0,
    "mttr_hours": null
  },
  "notes": [
    "mttr_hours is null: 0 resolved production health failure sessions in the 14d window.",
    "change_failure_rate_pct is 0.0%: 5 production deployments succeeded with 0 failures or linked incidents."
  ],
  "trend": [ ... ]
}
```

#### 4. Contrast Defect Model
- **Defect:** Coercing `mttr_hours` to `0.0` when zero outages occur.
- **Impact:** A false `0.0h` MTTR tricks executive dashboards into reporting instantaneous disaster recovery.
- **Assertion:**
  ```python
  assert response.json()["aggregates"]["change_failure_rate_pct"] == 0.0
  assert response.json()["aggregates"]["mttr_hours"] is None
  ```

---

### Scenario `TC-DORA-API-005`: Zero-Second Rapid Recovery (`MTTR = 0.0h`)

#### 1. Objective & Description
Verify that an instantaneous automated failover (`resolved_at == started_at`) yields a measured zero `mttr_hours = 0.0`, correctly contrasting with the undefined `null` in `TC-DORA-API-004`.

#### 2. Fixture Preconditions
- Outage session: `started_at = 2026-11-10T12:00:00Z`, `resolved_at = 2026-11-10T12:00:00Z` ($\Delta t = 0\text{s}$).

#### 3. Expected Response Excerpt
```json
{
  "aggregates": {
    "mttr_hours": 0.0
  }
}
```

#### 4. Assertion
```python
assert response.json()["aggregates"]["mttr_hours"] == 0.0
assert response.json()["aggregates"]["mttr_hours"] is not None
```

---

### Scenario `TC-DORA-API-006`: Cross-Tenant Isolation & Anti-Enumeration (`HTTP 404`)

#### 1. Objective & Description
Verify that an authenticated user belonging to Tenant B who attempts to access DORA metrics for a project belonging to Tenant A receives `HTTP 404 Not Found` (anti-enumeration security).

#### 2. Fixture Preconditions
- Project `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa` belongs to Tenant 1.
- Caller authenticates as Tenant 2 admin / developer.

#### 3. Request
```http
GET /api/v1/projects/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/dora-metrics HTTP/1.1
Host: api.manta.local
Authorization: Bearer <JWT_TENANT_2_ADMIN>
```

#### 4. Expected Response
```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{
  "detail": "Project 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' not found."
}
```

#### 5. Security Invariant
- Must **NOT** return `HTTP 403 Forbidden` (which confirms the UUID exists in a foreign tenant).
- Must **NOT** return `HTTP 200 OK` with zeroed metrics.

---

### Scenario `TC-DORA-API-007`: Missing and Soft-Deleted Project Query (`HTTP 404`)

#### 1. Objective & Description
Verify that requesting a nonexistent UUID or a project whose `deleted_at IS NOT NULL` returns `HTTP 404 Not Found`.

#### 2. Assertions
- `status_code == 404`
- `response.json()["detail"] == f"Project '{non_existent_or_deleted_id}' not found."`

---

### Scenario `TC-DORA-API-008`: Sub-300ms Query Execution Budget Under High Volume

#### 1. Objective & Description
Verify that the database execution and response assembly latency remains strictly below $300\text{ms}$ ($< 300\text{ms}$) under a benchmark high-volume project.

#### 2. Fixture Load Profile
- $1,000$ production deployments.
- $500$ pull requests with commit tracking.
- $50$ deployment incidents ($24\text{h}$ window correlation).
- $50$ production health failure sessions.

#### 3. Benchmark Execution
```python
import time

t_start = time.perf_counter()
response = await calculate_project_dora_metrics(
    session,
    tenant_id=tenant_id,
    project_id=project_id,
    timeframe_days=30,
    window_end=window_end,
)
elapsed_ms = (time.perf_counter() - t_start) * 1000.0

assert elapsed_ms < 300.0, f"Query latency exceeded budget: {elapsed_ms:.2f}ms >= 300ms"
```

#### 4. Required Database Indexes
```sql
CREATE INDEX IF NOT EXISTS idx_deployments_dora_query 
ON deployments (tenant_id, project_id, environment, authoritative_signal_source, deployed_at);

CREATE INDEX IF NOT EXISTS idx_pull_requests_dora_query 
ON pull_requests (tenant_id, project_id, merged_at);

CREATE INDEX IF NOT EXISTS idx_production_failures_dora_query 
ON production_failures (tenant_id, project_id, status, started_at);

CREATE INDEX IF NOT EXISTS idx_deployment_incidents_dora_query 
ON deployment_incidents (tenant_id, deployment_id, status, is_active, detected_at);
```

---

### Scenario `TC-DORA-API-009`: OpenAPI 3.1 / JSON Schema 2020-12 Schema Validation

#### 1. Objective & Description
Verify that the generated OpenAPI schema for `DoraMetricsResponse` adheres strictly to OpenAPI 3.1.0 specifications:
- Uses `anyOf` with `type: "null"` for nullable fields.
- Lists all four metrics in `"required"` properties.
- Disallows `allow_inf_nan`.
- Enforces boundary minimums and maximums (`CFR <= 100.0`, all metrics `>= 0.0`).

#### 2. Assertion Schema Checks
```python
schema = DoraMetricsResponse.model_json_schema()
agg_props = schema["$defs"]["DoraAggregates"]["properties"]

for field in [
    "lead_time_hours",
    "deployment_frequency_per_day",
    "change_failure_rate_pct",
    "mttr_hours",
]:
    assert "anyOf" in agg_props[field]
    assert {"type": "null"} in agg_props[field]["anyOf"]
```

---

### Scenario `TC-DORA-API-010`: Authoritative CD Signal Source Filtering

#### 1. Objective & Description
Verify that deployments originating from secondary or non-authoritative CD sources are completely excluded from the DORA metrics calculation.

#### 2. Fixture Preconditions
- Project `authoritative_cd_signal = 'github_deployments_api'`.
- Telemetry:
  - 4 deployments with `authoritative_signal_source = 'github_deployments_api'`.
  - 10 deployments with `authoritative_signal_source = 'argocd_postsync_webhook'` (auxiliary signal).

#### 3. Expected Result
- Denominator for deployment frequency and CFR counts **only** the 4 authoritative deployments.
- Auxiliary 10 deployments are excluded.

---

### Scenario `TC-DORA-API-011`: Non-Production Environment Exclusion

#### 1. Objective & Description
Verify that deployments to `'staging'`, `'development'`, `'qa'`, or `'preview'` environments are strictly excluded from production DORA metrics.

#### 2. Fixture Preconditions
- 3 production deployments (`SUCCESS`).
- 5 staging deployments (`FAILED`).

#### 3. Expected Result
- `change_failure_rate_pct == 0.0` (production failures = 0).
- Staging failures do not leak into the numerator.

---

### Scenario `TC-DORA-API-012`: Request Parameter Validation & Conflicts (`HTTP 422`)

#### 1. Objective & Description
Verify that malformed or unsupported request query parameters are rejected with `HTTP 422 Unprocessable Content`:
- Malformed UUID string in path: `GET /api/v1/projects/not-a-uuid/dora-metrics` $\rightarrow$ `422`.
- Unsupported `timeframe_days`: `GET /api/v1/projects/{id}/dora-metrics?timeframe_days=7` $\rightarrow$ `422`.
- Conflicting parameters: `GET /api/v1/projects/{id}/dora-metrics?timeframe_days=14&window=30d` $\rightarrow$ `422`.

---

### Scenario `TC-DORA-API-013`: Authentication & RBAC Enforcement Matrix (`401` / `403` / `200`)

#### 1. Objective & Description
Verify that requests enforce authentication and role permissions per `docs/roles-permissions.json`:

| Caller Role / State | Expected HTTP Status | Expected Response Body |
| :--- | :---: | :--- |
| Unauthenticated (no token) | `401 Unauthorized` | Standard authentication error |
| Expired / Invalid JWT | `401 Unauthorized` | Token expired / invalid signature |
| Authenticated User with 0 roles | `403 Forbidden` | `{"detail": "Forbidden: insufficient permissions"}` |
| `DEVELOPER` | `200 OK` | `DoraMetricsResponse` |
| `SCRUM_MASTER` | `200 OK` | `DoraMetricsResponse` |
| `PROJECT_MANAGER` | `200 OK` | `DoraMetricsResponse` |
| `SOLUTION_ARCHITECT` | `200 OK` | `DoraMetricsResponse` |
| `DELIVERY_DIRECTOR` | `200 OK` | `DoraMetricsResponse` |
| `TOP_MANAGEMENT` | `200 OK` | `DoraMetricsResponse` |
| `TENANT_ADMIN` | `200 OK` | `DoraMetricsResponse` |

---

### Scenario `TC-DORA-API-014`: Domain Route Equivalence Verification

#### 1. Objective & Description
Verify that `GET /api/v1/projects/{project_id}/dora-metrics` and its domain alias `GET /api/v1/dora/projects/{project_id}` execute the identical backend calculation logic and return equivalent JSON payloads.

---

### Scenario `TC-DORA-API-015`: Daily Trend Time-Series Contiguity & Monotonicity

#### 1. Objective & Description
Verify that the `trend` array in `DoraMetricsResponse` satisfies the contiguity property:
1. Length is exactly equal to the window length:
   $$|\text{trend}| = W \quad (W \in \{14, 30\})$$
2. For all $i \in [1, W-1]$:
   $$\text{trend}[i].\text{date} = \text{trend}[i-1].\text{date} + 1\text{ day}$$
3. Missing data days are filled with `null` metrics, never omitted.

---

## 5. Security & Multi-Tenant Isolation Specifications

```
+-------------------------------------------------------------------------------------------------------------------+
|                        MULTI-TENANT ROW-LEVEL SECURITY & QUERY ISOLATION FLOW                                     |
+-------------------------------------------------------------------------------------------------------------------+
| Caller Request (JWT Bearer Token)                                                                                 |
|    |                                                                                                              |
|    v                                                                                                              |
| JWTAuthMiddleware: Validates RS256 JWT -> Resolves tenant_id and caller roles                                     |
|    |                                                                                                              |
|    v                                                                                                              |
| RBAC Guard: Confirms caller holds VIEW_PROJECT_DORA permission                                                     |
|    |                                                                                                              |
|    v                                                                                                              |
| get_tenant_db_context(tenant_id):                                                                                 |
|    EXECUTE: SET LOCAL app.current_tenant_id = :tenant_id                                                          |
|    |                                                                                                              |
|    v                                                                                                              |
| PostgreSQL Database Engine:                                                                                       |
|    Enforces: CREATE POLICY tenant_isolation_policy ON projects USING (tenant_id = current_tenant_id());           |
|    Enforces: CREATE POLICY tenant_isolation_policy ON deployments USING (tenant_id = current_tenant_id());        |
|    |                                                                                                              |
|    +---> If project.tenant_id != :tenant_id: Returns 0 rows -> Service raises HTTP 404 (Anti-Enumeration)         |
|    +---> If project.deleted_at IS NOT NULL: Returns 0 rows -> Service raises HTTP 404                             |
|    +---> If project.tenant_id == :tenant_id: Returns project record -> Executes DORA metrics calculation          |
+-------------------------------------------------------------------------------------------------------------------+
```

---

## 6. Performance Budget & Query Optimization ($< 300\text{ms}$)

```
+-------------------------------------------------------------------------------------------------------------------+
|                          LATENCY BUDGET ALLOCATION (BUDGET: <= 300ms P99)                                         |
+--------------------------------------------------------+----------------------------------------------------------+
| Pipeline Stage                                         | Target Latency Budget                                    |
+--------------------------------------------------------+----------------------------------------------------------+
| 1. Authentication & JWT RS256 Verification             | <= 10ms                                                  |
| 2. Tenant Context Setup (`SET LOCAL app.current_...`)   | <= 5ms                                                   |
| 3. Project Lookup & Verification                       | <= 15ms                                                  |
| 4. Deployed PR & Lead Time Query Join                  | <= 70ms                                                  |
| 5. Production Deployments & Frequency Aggregation      | <= 50ms                                                  |
| 6. Change Failure Rate & Incident Union Join           | <= 60ms                                                  |
| 7. Production Failure Sessions (MTTR) Query            | <= 40ms                                                  |
| 8. Daily Trend Assembly & Pydantic Validation          | <= 20ms                                                  |
| 9. JSON Serialization & HTTP Response Transmission     | <= 30ms                                                  |
+--------------------------------------------------------+----------------------------------------------------------+
| Total Maximum Wall-Clock Execution Budget              | <= 300ms                                                 |
+--------------------------------------------------------+----------------------------------------------------------+
```

---

## 7. Traceability Matrix & Test Suite Mapping

The scenarios defined in this document are automated across the pytest test suite in [`backend/tests/test_dora_openapi_contract.py`](../test_dora_openapi_contract.py):

| Test Scenario Identifier | Automated Test Function (`test_dora_openapi_contract.py`) | Status |
| :--- | :--- | :---: |
| `TC-DORA-API-001` | `test_task_30_2_assemble_dora_metrics_response_all_four_metrics` | Verified |
| `TC-DORA-API-002` | `test_task_30_2_http_endpoints_rbac_validation_404_and_200_contract` | Verified |
| `TC-DORA-API-003` | `test_task_30_2_assemble_zero_telemetry_vs_failed_only_deployments` | Verified |
| `TC-DORA-API-004` | `test_task_30_2_assemble_dora_metrics_response_all_four_metrics` | Verified |
| `TC-DORA-API-005` | `test_task_30_2_assemble_dora_metrics_response_all_four_metrics` | Verified |
| `TC-DORA-API-006` | `test_task_30_2_http_endpoints_rbac_validation_404_and_200_contract` | Verified |
| `TC-DORA-API-007` | `test_task_30_2_calculate_project_dora_metrics_service_and_404` | Verified |
| `TC-DORA-API-008` | `test_task_30_3_database_query_latency_under_300ms` | Verified |
| `TC-DORA-API-009` | `test_dora_aggregates_openapi_31_nullable_schema` | Verified |
| `TC-DORA-API-010` | `test_task_30_2_assemble_dora_metrics_response_all_four_metrics` | Verified |
| `TC-DORA-API-011` | `test_task_30_2_assemble_dora_metrics_response_all_four_metrics` | Verified |
| `TC-DORA-API-012` | `test_task_30_2_http_endpoints_rbac_validation_404_and_200_contract` | Verified |
| `TC-DORA-API-013` | `test_task_30_2_http_endpoints_rbac_validation_404_and_200_contract` | Verified |
| `TC-DORA-API-014` | `test_task_30_2_http_endpoints_rbac_validation_404_and_200_contract` | Verified |
| `TC-DORA-API-015` | `test_task_30_2_assemble_dora_metrics_response_all_four_metrics` | Verified |

---

## 8. Acceptance Criteria Verification Checklist

- [x] **Full data window specified for both 14d and 30d timeframes (`TC-DORA-API-001`, `TC-DORA-API-002`)**
- [x] **Zero-deployment window documented returning explicit `null` aggregates and daily points (`TC-DORA-API-003`)**
- [x] **Successful deployments with zero failures documented asserting `CFR = 0.0%` and `MTTR = null` (`TC-DORA-API-004`)**
- [x] **Zero-second rapid recovery documented asserting `MTTR = 0.0h` (`TC-DORA-API-005`)**
- [x] **Cross-tenant access documented asserting `HTTP 404 Not Found` anti-enumeration protection (`TC-DORA-API-006`)**
- [x] **Sub-300ms latency budget documented with composite index profiles (`TC-DORA-API-008`)**
- [x] **OpenAPI 3.1.0 JSON Schema 2020-12 compliance documented with `anyOf` nullable unions (`TC-DORA-API-009`)**
- [x] **Expected JSON response payloads and HTTP status codes documented for every scenario**
