# US-76: Sprint Burndown (40 -> 32 -> 20 SP) and WIP Flow Metrics Test Scenarios

**Task:** `SCRUM-472` (`Task 76.5`: Create test scenarios for 10-issue / 40-SP sprint burndown (40 -> 32 -> 20 SP) and 4-issue WIP count)  
**Parent User Story:** `SCRUM-370` (`US-76`: Sprint Burndown and WIP Flow Metrics)  
**Related Tickets:**  
- `SCRUM-468` / `Task 76.1`: Data models and DB schema for sprints, issues, transitions and WIP limits  
- `SCRUM-469` / `Task 76.2`: API endpoints `GET /api/v1/projects/{project_id}/sprints`, `GET /api/v1/projects/{project_id}/sprints/burndown`, and `GET /api/v1/projects/{project_id}/flow-metrics`  
- `SCRUM-470` / `Task 76.3`: Web Portal Sprint Burndown chart component (`SprintBurndownChart.tsx`)  
- `SCRUM-471` / `Task 76.4`: Web Portal WIP Congestion and Developer Capacity component (`WipCongestion.tsx`, `WipByDeveloper.tsx`)  
- `SCRUM-472` / `Task 76.5`: Create test scenarios for 10-issue / 40-SP sprint burndown (40 -> 32 -> 20 SP) and 4-issue WIP count (this document)  
- `SCRUM-473` / `Task 76.6`: Automated backend pytest tests verifying sprint burndown, linear slope calculation, WIP count, and developer limits  
- `SCRUM-474` / `Task 76.7`: Automated Playwright E2E tests verifying Sprint & WIP portal dashboard, burndown curve, status pills, and RBAC 403 enforcement  
- `SCRUM-402` / `US-67`: Jira issue and transition webhook ingestion (`backend/app/jira/`)  
- `SCRUM-311` / `SCRUM-336`: Role access matrix and permission enforcement (`Permission.VIEW_SPRINT_BURNDOWN_WIP`)  

**Target Storage Engine:** PostgreSQL 16 with Row-Level Security (`pgvector/pgvector:pg16`)  
**Target Implementation Modules:**  
- [`backend/app/projects/flow_metrics.py`](../backend/app/projects/flow_metrics.py) (`compute_burndown`, `compute_wip`, `compute_wip_by_developer`)  
- [`backend/app/projects/router.py`](../backend/app/projects/router.py) (`/api/v1/projects/{project_id}/flow-metrics`, `/sprints/burndown`)  
- [`backend/app/auth/permissions.py`](../backend/app/auth/permissions.py) (`Permission.VIEW_SPRINT_BURNDOWN_WIP`)  
- [`backend/app/auth/roles-permissions.json`](../backend/app/auth/roles-permissions.json)  
- [`web/src/app/(portal)/sprint-wip/page.tsx`](../web/src/app/(portal)/sprint-wip/page.tsx)  
- [`web/src/components/sprint-wip/sprint-burndown-chart.tsx`](../web/src/components/sprint-wip/sprint-burndown-chart.tsx)  
- [`web/src/components/sprint-wip/wip-congestion.tsx`](../web/src/components/sprint-wip/wip-congestion.tsx)  
- [`web/src/components/sprint-wip/wip-by-developer.tsx`](../web/src/components/sprint-wip/wip-by-developer.tsx)  
- [`web/src/lib/sprint-wip/sprint-wip-api.ts`](../web/src/lib/sprint-wip/sprint-wip-api.ts)  
**Authoritative Automated Test Suites:**  
- [`backend/tests/test_permission_enforcement.py`](../backend/tests/test_permission_enforcement.py)  
- [`backend/tests/test_role_access_scenarios.py`](../backend/tests/test_role_access_scenarios.py)  
- [`web/e2e/sprint-wip-flow-metrics.spec.ts`](../web/e2e/sprint-wip-flow-metrics.spec.ts)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us76_sprint_burndown_and_wip_scenarios.md`](../backend/tests/scenarios/test_us76_sprint_burndown_and_wip_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Design and formalize the definitive verification scenarios and test specifications for **Sprint Burndown Tracking, Ideal Velocity Slope Modeling, Work In Progress (WIP) Congestion Analysis, and Role-Based Access Control (RBAC)** in M.A.N.T.A. (`US-76` / `SCRUM-472` / `Task 76.5`).

Sprint burndown and WIP flow metrics provide Scrum Masters, Project Managers, and Delivery Directors with immediate visibility into team velocity, delivery bottlenecks, scope stability, and developer overcommitment. To ensure mathematical accuracy and security compliance across both backend calculation services and frontend UI dashboards:
1. The calculation engine must evaluate historical status transitions to reconstruct daily remaining story points accurately across calendar cutoffs.
2. The linear ideal burndown slope must decline monotonically from total committed story points ($40\text{ SP}$) on sprint start to $0.0\text{ SP}$ on sprint completion.
3. Work In Progress (WIP) counts must strictly exclude terminal completion statuses (`Done`, `Closed`, `Resolved`, `Complete`, `Completed`) and evaluate threshold breaches against configured project and per-developer WIP limits.
4. Security guardrails must restrict sprint flow metrics access exclusively to leadership roles (`SCRUM_MASTER`, `PROJECT_MANAGER`, `DELIVERY_DIRECTOR`, `TENANT_ADMIN`) while enforcing `HTTP 403 Forbidden` for `DEVELOPER`, `TOP_MANAGEMENT`, and `SOFTWARE_ARCHITECT`.

### 1.2 Implementation Scope & Fixture Baseline

This specification defines the authoritative **10-Issue / 40-SP Sprint Fixture** with step-by-step verification of:

1. **Initial Sprint Commitment (Day 1 Baseline):**  
   10 active issues committed totaling exactly $40\text{ SP}$. Initial remaining points equal $40.0\text{ SP}$ with an ideal slope origin at $40.0\text{ SP}$.
2. **First Completion Batch on Day 3 ($8\text{ SP}$ Done $\rightarrow 32\text{ SP}$ Remaining):**  
   2 issues totaling $8\text{ SP}$ transition to `Done` before the Day 3 end cutoff. Day 3 remaining points drop to $32.0\text{ SP}$.
3. **Second Completion Batch on Day 6 ($12\text{ SP}$ Done $\rightarrow 20\text{ SP}$ Remaining):**  
   2 additional issues totaling $12\text{ SP}$ transition to `Done` before the Day 6 end cutoff. Cumulative completed points reach $20\text{ SP}$, resulting in remaining points dropping to $20.0\text{ SP}$.
4. **Active WIP Congestion (Exactly 4 Issues in `In Progress`):**  
   Among the remaining 6 unfinished issues ($20\text{ SP}$), exactly **4 issues** reside in the active `In Progress` status, triggering a WIP threshold evaluation against configured limits (`WIPLimit(status='In Progress', max_items=3)` $\rightarrow$ `over_limit = True`).
5. **Linear Ideal Burndown Slope:**  
   Validation of the linear slope calculation across the sprint timeline:
   $$\text{ideal\_sp}(d) = \text{TotalSP} \times \left(1 - \frac{d}{N - 1}\right)$$
6. **RBAC Guard (`Permission.VIEW_SPRINT_BURNDOWN_WIP`):**  
   Enforcement of `HTTP 403 Forbidden` for callers with the `DEVELOPER` role, while granting `HTTP 200 OK` to `SCRUM_MASTER` and authorized managers.

---

## 2. Core Architectural Invariants & Mathematical Formulations

```
+-------------------------------------------------------------------------------------------------------------------+
|                            SPRINT BURNDOWN & WIP FLOW METRICS ARCHITECTURE (US-76)                                |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Reconstructed Historical SP      | 2. Monotonic Ideal Linear Slope     | 3. Active WIP Status Filter           |
|    (Cutoff Status Evaluation)       |    (Velocity Reference Trajectory)  |    (Terminal Exclusion Invariant)     |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * Total SP = sum(Issue.story_points)| * ideal_sp(0) = Total SP            | * WIP(S) = count(Issues in status S)  |
| * Day cutoff = end of calendar day  | * ideal_sp(N-1) = 0.0 SP            | * Terminal statuses excluded:         |
| * Reconstruct status from           | * Slope = Total SP / (N - 1)        |   ('done', 'closed', 'resolved',      |
|   IssueTransition history           | * ideal_sp(d) = Total*(1 - d/(N-1)) |    'complete', 'completed')           |
| * remaining_sp = Total - sum(Done)  | * Monotonically non-increasing      | * Evaluated against status WIP limits |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                                 DEVELOPER WIP CAP & RBAC ACCESS CONTROL GUARDS                                    |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Per-Developer Capacity & Pseudonymization       | 5. Strict RBAC & Tenant Isolation Guard                      |
+----------------------------------------------------+--------------------------------------------------------------+
| * Grouped by Issue.assignee_pseudonym_id           | * Endpoint: Permission.VIEW_SPRINT_BURNDOWN_WIP              |
| * Masked as 'Developer ' + SHA256(pseudo)[:10]     | * DEVELOPER role -> HTTP 403 Forbidden                       |
| * Over limit if dev_wip_count > wip_limit_per_dev  | * SCRUM_MASTER / TENANT_ADMIN -> HTTP 200 OK                 |
| * Unassigned unfinished issues tracked separately  | * Cross-tenant query -> HTTP 404 Not Found (RLS)             |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Cutoff-Based Status Reconstruction & Remaining SP Formulation

For any sprint with issues $I = \{i_1, i_2, \dots, i_M\}$ and total story points $\text{TotalSP} = \sum_{k=1}^M \text{sp}(i_k)$:
1. At day index $d \in \{0, 1, \dots, N-1\}$ with calendar cutoff timestamp $T_{\text{cutoff}}(d)$:
   $$T_{\text{cutoff}}(d) = \begin{cases} 
   T_{\text{start}}, & \text{if } d = 0 \\
   \text{Midnight UTC of Day } (d + 1), & \text{if } d > 0 
   \end{cases}$$
2. The historical status $\text{Status}(i, T_{\text{cutoff}})$ of issue $i$ is derived from its transition log:
   $$\text{Status}(i, T_{\text{cutoff}}) = \text{to\_status of latest transition where } t_{\text{transition}} < T_{\text{cutoff}}$$
3. Issue $i$ is evaluated as resolved if and only if $\text{Status}(i, T_{\text{cutoff}}) \in \mathcal{S}_{\text{done}}$, where:
   $$\mathcal{S}_{\text{done}} = \{\text{"done"}, \text{"closed"}, \text{"resolved"}, \text{"complete"}, \text{"completed"}\}$$
4. The remaining story points for calendar day $d$ is given by:
   $$\text{DoneSP}(d) = \sum_{i \in I \mid \text{Status}(i, T_{\text{cutoff}}(d)) \in \mathcal{S}_{\text{done}}} \text{sp}(i)$$
   $$\text{RemainingSP}(d) = \text{round}(\max(0.0, \text{TotalSP} - \text{DoneSP}(d)), 2)$$

---

### Invariant 2: Linear Ideal Burndown Reference Slope

1. Across a sprint duration of $N$ calendar days ($d \in [0, N - 1]$):
   $$\text{ideal\_sp}(d) = \text{round}\left(\text{TotalSP} \times \left(1 - \frac{d}{N - 1}\right), 2\right)$$
2. Boundary constraints:
   $$\text{ideal\_sp}(0) = \text{TotalSP} \quad (40.0\text{ SP})$$
   $$\text{ideal\_sp}(N - 1) = 0.0\text{ SP}$$
3. Monotonicity:
   $$\forall d \in [0, N - 2]: \quad \text{ideal\_sp}(d + 1) \le \text{ideal\_sp}(d)$$

---

### Invariant 3: Active WIP Status Aggregation & Terminal Exclusion

1. Only issues whose current normalized status is non-terminal enter WIP calculation:
   $$\text{ActiveWIPIssues} = \{i \in I \mid \text{lower}(\text{trim}(\text{status}(i))) \notin \mathcal{S}_{\text{done}}\}$$
2. For each status $s$, the count is given by:
   $$\text{WIP}(s) = |\{i \in \text{ActiveWIPIssues} \mid \text{status}(i) = s\}|$$
3. If a limit $L_s$ is configured in `wip_limits`:
   $$\text{over\_limit}(s) = (L_s \ne \text{null}) \land (\text{WIP}(s) > L_s)$$

---

### Invariant 4: Developer WIP Limit Enforcement & PII Masking

1. Each project configures a maximum concurrent WIP limit per developer: $\text{wip\_limit\_per\_dev} \in [1, 50]$.
2. Individual developer WIP counts are grouped by Jira contributor pseudonym ID:
   $$\text{DevWIP}(P) = |\{i \in \text{ActiveWIPIssues} \mid \text{assignee\_pseudonym\_id}(i) = P\}|$$
3. Contributor identifiers are masked in API responses:
   $$\text{DeveloperLabel}(P) = \text{"Developer "} + \text{SHA-256}(P)[:10].\text{upper}()$$
4. Over-limit flag is raised if and only if:
   $$\text{over\_limit}(P) = \text{DevWIP}(P) > \text{wip\_limit\_per\_dev}$$

---

### Invariant 5: RBAC Role Authorization Matrix (`VIEW_SPRINT_BURNDOWN_WIP`)

| Canonical Realm Role | Decision | HTTP Status Code | Business Rationale |
| :--- | :---: | :---: | :--- |
| **`TENANT_ADMIN`** | **ALLOW** | `200 OK` | Full operational visibility across all projects and sprints within tenant. |
| **`SCRUM_MASTER`** | **ALLOW** | `200 OK` | Core responsibility: sprint velocity, burndown trajectory, and WIP management. |
| **`PROJECT_MANAGER`** | **ALLOW** | `200 OK` | Tracks milestone progress, delivery risk, and team capacity. |
| **`DELIVERY_DIRECTOR`** | **ALLOW** | `200 OK` | Portfolio delivery health and operational blocker monitoring. |
| **`DEVELOPER`** | **DENY** | **`403 Forbidden`** | Team-level WIP and burndown metrics are restricted to management roles. |
| **`TOP_MANAGEMENT`** | **DENY** | **`403 Forbidden`** | Focuses on macro business metrics rather than daily sprint burndowns. |
| **`SOFTWARE_ARCHITECT`** | **DENY** | **`403 Forbidden`** | Architectural governance scope; excludes daily sprint burndown tracking. |

---

## 3. Authoritative 10-Issue (40 SP) Sprint Fixture Specification

### 3.1 Sprint Metadata

- **Sprint Name:** `Sprint 42 (Manta Delivery)`
- **Total Committed Issues:** `10 issues`
- **Total Story Points:** `40.0 SP`
- **Sprint Start Date:** `2026-11-01T00:00:00Z` (Day 1)
- **Sprint End Date:** `2026-11-10T23:59:59Z` (Day 10, $N=10$ days)
- **Project WIP Limit Per Developer:** `2`
- **Status WIP Limit (`In Progress`):** `3 items`

### 3.2 Issue Manifest & Status Transitions

| Issue Key | Story Points | Assignee Pseudonym | Initial Status (Day 1) | Day 3 Status & Transition | Day 6 Status & Transition | Current Status (Day 10) |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| **`MANTA-101`** | `4.0` | `pseudo-dev-alpha` | `To Do` | `Done` (at `2026-11-03T14:00:00Z`) | `Done` | `Done` |
| **`MANTA-102`** | `4.0` | `pseudo-dev-beta` | `To Do` | `Done` (at `2026-11-03T16:30:00Z`) | `Done` | `Done` |
| **`MANTA-103`** | `5.0` | `pseudo-dev-gamma` | `To Do` | `In Progress` | `Done` (at `2026-11-06T11:15:00Z`) | `Done` |
| **`MANTA-104`** | `7.0` | `pseudo-dev-delta` | `To Do` | `In Progress` | `Done` (at `2026-11-06T17:45:00Z`) | `Done` |
| **`MANTA-105`** | `3.0` | `pseudo-dev-alpha` | `To Do` | `To Do` | `In Progress` (at `2026-11-05T09:00:00Z`) | **`In Progress`** |
| **`MANTA-106`** | `5.0` | `pseudo-dev-alpha` | `To Do` | `To Do` | `In Progress` (at `2026-11-05T10:30:00Z`) | **`In Progress`** |
| **`MANTA-107`** | `2.0` | `pseudo-dev-alpha` | `To Do` | `To Do` | `In Progress` (at `2026-11-06T14:00:00Z`) | **`In Progress`** |
| **`MANTA-108`** | `4.0` | `pseudo-dev-beta` | `To Do` | `To Do` | `In Progress` (at `2026-11-06T15:00:00Z`) | **`In Progress`** |
| **`MANTA-109`** | `3.0` | `pseudo-dev-gamma` | `To Do` | `To Do` | `To Do` | **`To Do`** |
| **`MANTA-110`** | `3.0` | `pseudo-dev-delta` | `To Do` | `To Do` | `To Do` | **`To Do`** |
| **TOTALS** | **`40.0 SP`** | — | **`10 Issues To Do`** | **`8 SP Done / 32 SP Rem`** | **`20 SP Done / 20 SP Rem`** | **`4 In Progress (14 SP)`** |

### 3.3 Day-by-Day Burndown Schedule ($N = 10$ Days)

$$\text{Slope} = \frac{40.0}{9} \approx 4.44\text{ SP / day}$$

| Sprint Day | Calendar Date | Completed SP Added | Cumulative Done SP | Actual Remaining SP | Ideal Burndown SP | Delta vs Ideal | Active WIP (`In Progress`) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Day 1** | `2026-11-01` | `0.0 SP` | `0.0 SP` | **`40.0 SP`** | `40.00 SP` | `0.00 SP` | `0` (`10 To Do`) |
| **Day 2** | `2026-11-02` | `0.0 SP` | `0.0 SP` | **`40.0 SP`** | `35.56 SP` | `+4.44 SP` | `2` (`2 In Progress`) |
| **Day 3** | `2026-11-03` | **`8.0 SP`** (`MANTA-101`, `102`) | **`8.0 SP`** | **`32.0 SP`** | `31.11 SP` | `+0.89 SP` | `2` (`2 In Progress`) |
| **Day 4** | `2026-11-04` | `0.0 SP` | `8.0 SP` | **`32.0 SP`** | `26.67 SP` | `+5.33 SP` | `2` (`2 In Progress`) |
| **Day 5** | `2026-11-05` | `0.0 SP` | `8.0 SP` | **`32.0 SP`** | `22.22 SP` | `+9.78 SP` | `4` (`4 In Progress`) |
| **Day 6** | `2026-11-06` | **`12.0 SP`** (`MANTA-103`, `104`) | **`20.0 SP`** | **`20.0 SP`** | `17.78 SP` | `+2.22 SP` | **`4`** (`4 In Progress`) |
| **Day 7** | `2026-11-07` | `0.0 SP` | `20.0 SP` | **`20.0 SP`** | `13.33 SP` | `+6.67 SP` | **`4`** (`4 In Progress`) |
| **Day 8** | `2026-11-08` | `0.0 SP` | `20.0 SP` | **`20.0 SP`** | `8.89 SP` | `+11.11 SP` | **`4`** (`4 In Progress`) |
| **Day 9** | `2026-11-09` | `0.0 SP` | `20.0 SP` | **`20.0 SP`** | `4.44 SP` | `+15.56 SP` | **`4`** (`4 In Progress`) |
| **Day 10** | `2026-11-10` | `0.0 SP` | `20.0 SP` | **`20.0 SP`** | `0.00 SP` | `+20.00 SP` | **`4`** (`4 In Progress`) |

---

## 4. Master Test Scenarios Matrix

| Scenario ID | Scenario Name | Target AC | Category | Preconditions | Trigger / Action | Expected Result | Automated Suite |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-SPRINT-FLOW-001`** | **Sprint Baseline Initialization (40 SP Committed on Day 1)** | `AC-1` | Calculation / Engine | 10 issues created; sprint started at `2026-11-01`. | Compute burndown on Day 1 (`GET .../flow-metrics`). | `remaining_sp = 40.0`, `ideal_sp = 40.0`; all 10 issues in non-terminal state. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-002`** | **First Completion Batch: 8 SP Done on Day 3 $\rightarrow$ 32 SP Remaining** | `AC-1` | Calculation / Transitions | `MANTA-101` (4 SP) and `MANTA-102` (4 SP) transition to `Done` on Day 3. | Request burndown with cutoff at Day 3. | Day 3 point returns `remaining_sp = 32.0`, `ideal_sp = 31.11`. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-003`** | **Second Completion Batch: 12 SP Done on Day 6 $\rightarrow$ 20 SP Remaining** | `AC-1` | Calculation / Transitions | `MANTA-103` (5 SP) and `MANTA-104` (7 SP) transition to `Done` on Day 6. | Request burndown with cutoff at Day 6. | Day 6 point returns `remaining_sp = 20.0`, `ideal_sp = 17.78`. Cumulative Done = 20 SP. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-004`** | **Linear Ideal Burndown Slope Calculation & Contiguity** | `AC-1` | Math / Invariants | 10-day sprint configured from Day 1 to Day 10. | Verify ideal points across entire series ($d = 0 \dots 9$). | `ideal_sp` strictly equals $[40.0, 35.56, 31.11, 26.67, 22.22, 17.78, 13.33, 8.89, 4.44, 0.0]$. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-005`** | **WIP State Counting: Exactly 4 Issues in "In Progress"** | `AC-2` | Aggregation / WIP | 4 issues (`MANTA-105..108`) in `In Progress`; 2 in `To Do`; 4 in `Done`. | Query `wip` array from `flow-metrics`. | `wip` contains entry `{"status": "In Progress", "count": 4}`; terminal statuses excluded. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-006`** | **Status WIP Limit Congestion Alert (`over_limit = True`)** | `AC-2` | Congestion / Threshold | WIP limit for `In Progress` configured to `3`. | Evaluate `wip` response object for `In Progress`. | Entry returns `{"status": "In Progress", "count": 4, "limit": 3, "over_limit": true}`. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-007`** | **Per-Developer WIP Limit Breach & Pseudonym Masking** | `AC-2` | Privacy / Capacity | `pseudo-dev-alpha` holds 3 WIP issues (`MANTA-105, 106, 107`); limit = 2. | Query `wip_by_developer` in `flow-metrics`. | Label masked as `Developer <HEX>`; `count = 3`, `limit = 2`, `over_limit = true`. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-008`** | **RBAC Enforcement: DEVELOPER Role Forbidden (HTTP 403)** | `AC-3` | Security / RBAC | Caller authenticated with valid JWT holding `roles: ["DEVELOPER"]`. | Dispatches `GET /api/v1/projects/{id}/sprints/burndown` and `/flow-metrics`. | Both endpoints return `HTTP 403 Forbidden` (`{"detail": "..."}`). | `test_role_access_scenarios.py` |
| **`TC-SPRINT-FLOW-009`** | **RBAC Enforcement: SCRUM_MASTER & Management Allowed (HTTP 200)** | `AC-3` | Security / RBAC | Caller holds `SCRUM_MASTER`, `PROJECT_MANAGER`, or `TENANT_ADMIN`. | Dispatches `GET .../flow-metrics`. | Returns `HTTP 200 OK` with full burndown and WIP payload. | `test_permission_enforcement.py` |
| **`TC-SPRINT-FLOW-010`** | **Multi-Tenant Row-Level Security (RLS) Isolation** | `AC-3` | Security / Multi-Tenant | Project belongs to Tenant Alpha; Tenant Beta requests flow metrics. | Tenant Beta requests `/api/v1/projects/{alpha_id}/flow-metrics`. | Returns `HTTP 404 Not Found`. Cross-tenant query completely masked by RLS. | `test_sprint_flow_metrics.py` |
| **`TC-SPRINT-FLOW-011`** | **Web Portal: Sprint Burndown Chart Rendering & Trajectory Warning** | `AC-1` / `UI` | UI / E2E | Browser loads `/sprint-wip` with 10-issue fixture data. | Verify Recharts ComposedChart SVG elements and status banner. | Actual curve flattens at 20 SP; Status pill renders `BEHIND SCHEDULE` (warning amber). | `sprint-wip-flow-metrics.spec.ts` |
| **`TC-SPRINT-FLOW-012`** | **Web Portal: WIP Congestion Banner & Critical Alert Rendering** | `AC-2` / `UI` | UI / E2E | Browser loads `/sprint-wip` with 4 `In Progress` items (limit = 3). | Inspect WIP Congestion card and developer capacity items. | Stage card highlights in red with badge `4 / 3 (OVER LIMIT)`; Developer Alpha flagged over limit. | `sprint-wip-flow-metrics.spec.ts` |

---

## 5. Detailed Test Scenario Specifications

### Scenario `TC-SPRINT-FLOW-001`: Sprint Baseline Initialization (40 SP Committed on Day 1) (`AC-1`)

#### 5.1.1 Objective & Traceability
Verify that on sprint start (Day 1 baseline), the burndown engine calculates total committed story points as exactly $40.0\text{ SP}$ across all 10 issues, with remaining points equal to $40.0\text{ SP}$ and ideal slope starting at $40.0\text{ SP}$. Traces to `US-76` AC-1 and Invariant 1.

#### 5.1.2 Preconditions
- Tenant and project exist in PostgreSQL.
- Sprint configured with `start_date = 2026-11-01T00:00:00Z` and `end_date = 2026-11-10T23:59:59Z`.
- 10 issues committed to sprint totaling $40\text{ SP}$. All issues in initial status `To Do`.
- Evaluation date is Day 1 (`2026-11-01`).

#### 5.1.3 Execution Sequence & Concrete Inputs
```python
# Query sprint burndown on Day 1
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get(
        f"/api/v1/projects/{project_id}/sprints/burndown?sprint_id={sprint_id}",
        headers={"Authorization": f"Bearer {scrum_master_jwt}"},
    )
```

#### 5.1.4 Verification Assertions
```python
assert response.status_code == 200
data = response.json()
series = data["series"]

# Day 1 point verification:
day_1 = series[0]
assert day_1["date"] == "2026-11-01"
assert day_1["remaining_sp"] == 40.0
assert day_1["ideal_sp"] == 40.0
```

---

### Scenario `TC-SPRINT-FLOW-002`: First Completion Batch: 8 SP Done on Day 3 $\rightarrow$ 32 SP Remaining (`AC-1`)

#### 5.2.1 Objective & Traceability
Verify that when 2 issues (`MANTA-101` for $4\text{ SP}$ and `MANTA-102` for $4\text{ SP}$) transition to `Done` on Day 3 (`2026-11-03`), the burndown engine deducts $8\text{ SP}$ from the commitment, leaving exactly $32.0\text{ SP}$ remaining at the Day 3 calendar cutoff. Traces to `US-76` AC-1 and Invariant 1.

#### 5.2.2 Preconditions
- Baseline 40 SP sprint active.
- `IssueTransition` records created:
  - `MANTA-101`: `To Do` $\rightarrow$ `Done` at `2026-11-03T14:00:00Z` ($4\text{ SP}$).
  - `MANTA-102`: `To Do` $\rightarrow$ `Done` at `2026-11-03T16:30:00Z` ($4\text{ SP}$).
- All other 8 issues remain in non-terminal statuses.

#### 5.2.3 Execution Sequence & Concrete Inputs
```python
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get(
        f"/api/v1/projects/{project_id}/flow-metrics?sprint_id={sprint_id}",
        headers={"Authorization": f"Bearer {scrum_master_jwt}"},
    )
```

#### 5.2.4 Verification Assertions
```python
assert response.status_code == 200
burndown = response.json()["burndown"]

# Day 2 remains 40 SP
assert burndown[1]["date"] == "2026-11-02"
assert burndown[1]["remaining_sp"] == 40.0

# Day 3 reflects 8 SP completed -> 32.0 SP remaining
day_3 = burndown[2]
assert day_3["date"] == "2026-11-03"
assert day_3["remaining_sp"] == 32.0
assert day_3["ideal_sp"] == 31.11
```

---

### Scenario `TC-SPRINT-FLOW-003`: Second Completion Batch: 12 SP Done on Day 6 $\rightarrow$ 20 SP Remaining (`AC-1`)

#### 5.3.1 Objective & Traceability
Verify that when 2 additional issues (`MANTA-103` for $5\text{ SP}$ and `MANTA-104` for $7\text{ SP}$) transition to `Done` on Day 6 (`2026-11-06`), the cumulative resolved points equal $20\text{ SP}$, leaving exactly $20.0\text{ SP}$ remaining for Day 6 through Day 10. Traces to `US-76` AC-1.

#### 5.3.2 Preconditions
- Day 3 transitions complete ($8\text{ SP}$ Done).
- `IssueTransition` records added:
  - `MANTA-103`: `In Progress` $\rightarrow$ `Done` at `2026-11-06T11:15:00Z` ($5\text{ SP}$).
  - `MANTA-104`: `In Progress` $\rightarrow$ `Done` at `2026-11-06T17:45:00Z` ($7\text{ SP}$).
- Remaining 6 issues: 4 in `In Progress` ($14\text{ SP}$), 2 in `To Do` ($6\text{ SP}$).

#### 5.3.3 Execution Sequence
```python
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get(
        f"/api/v1/projects/{project_id}/flow-metrics?sprint_id={sprint_id}",
        headers={"Authorization": f"Bearer {scrum_master_jwt}"},
    )
```

#### 5.3.4 Verification Assertions
```python
burndown = response.json()["burndown"]

# Day 4 & Day 5 plateau at 32 SP
assert burndown[3]["remaining_sp"] == 32.0
assert burndown[4]["remaining_sp"] == 32.0

# Day 6 drops by 12 SP to 20.0 SP
day_6 = burndown[5]
assert day_6["date"] == "2026-11-06"
assert day_6["remaining_sp"] == 20.0
assert day_6["ideal_sp"] == 17.78

# Days 7..9 remain at 20.0 SP
for day_idx in range(6, 10):
    assert burndown[day_idx]["remaining_sp"] == 20.0
```

---

### Scenario `TC-SPRINT-FLOW-004`: Linear Ideal Burndown Slope Calculation & Contiguity (`AC-1`)

#### 5.4.1 Objective & Traceability
Verify that the ideal burndown trajectory forms a strict, contiguous linear slope from $40.0\text{ SP}$ to $0.0\text{ SP}$ across all 10 calendar days with zero missing intervals or null entries. Traces to `US-76` AC-1 and Invariant 2.

#### 5.4.2 Preconditions
- Sprint span: 10 days ($d = 0 \dots 9$). Total points = $40.0\text{ SP}$.

#### 5.4.3 Execution Sequence
```python
response = await client.get(
    f"/api/v1/projects/{project_id}/sprints/burndown?sprint_id={sprint_id}",
    headers=auth_headers,
)
series = response.json()["series"]
```

#### 5.4.4 Verification Assertions
```python
expected_ideals = [40.0, 35.56, 31.11, 26.67, 22.22, 17.78, 13.33, 8.89, 4.44, 0.0]
assert len(series) == 10

for idx, point in enumerate(series):
    assert point["ideal_sp"] == expected_ideals[idx]
    if idx > 0:
        # Assert strictly non-increasing slope
        assert point["ideal_sp"] < series[idx - 1]["ideal_sp"]
```

---

### Scenario `TC-SPRINT-FLOW-005`: WIP State Counting: Exactly 4 Issues in "In Progress" (`AC-2`)

#### 5.5.1 Objective & Traceability
Verify that the `wip` array returned by `/flow-metrics` aggregates unfinished issues by current status, accurately reporting exactly **4 issues** in `In Progress`, 2 in `To Do`, and excluding the 4 resolved issues in `Done`. Traces to `US-76` AC-2 and Invariant 3.

#### 5.5.2 Preconditions
- 4 issues in `Done` (`MANTA-101..104`).
- 4 issues in `In Progress` (`MANTA-105..108`).
- 2 issues in `To Do` (`MANTA-109..110`).

#### 5.5.3 Execution Sequence
```python
response = await client.get(
    f"/api/v1/projects/{project_id}/flow-metrics?sprint_id={sprint_id}",
    headers=auth_headers,
)
wip = response.json()["wip"]
```

#### 5.5.4 Verification Assertions
```python
wip_map = {item["status"]: item["count"] for item in wip}

# Assert exactly 4 in 'In Progress' and 2 in 'To Do'
assert wip_map.get("In Progress") == 4
assert wip_map.get("To Do") == 2

# Assert terminal status 'Done' is strictly excluded from WIP array
assert "Done" not in wip_map
assert "Closed" not in wip_map
```

---

### Scenario `TC-SPRINT-FLOW-006`: Status WIP Limit Congestion Alert (`over_limit = True`) (`AC-2`)

#### 5.6.1 Objective & Traceability
Verify that when the number of active issues in `In Progress` (4) exceeds the configured project status limit (3), the `over_limit` boolean is set to `True`, triggering congestion highlighting in the UI. Traces to `US-76` AC-2.

#### 5.6.2 Preconditions
- `WIPLimit` record inserted: `status = "In Progress"`, `max_items = 3`.

#### 5.6.3 Execution Sequence
```python
response = await client.get(
    f"/api/v1/projects/{project_id}/flow-metrics?sprint_id={sprint_id}",
    headers=auth_headers,
)
in_progress_item = next(item for item in response.json()["wip"] if item["status"] == "In Progress")
```

#### 5.6.4 Verification Assertions
```python
assert in_progress_item["count"] == 4
assert in_progress_item["limit"] == 3
assert in_progress_item["over_limit"] is True
```

---

### Scenario `TC-SPRINT-FLOW-007`: Per-Developer WIP Limit Breach & Pseudonym Masking (`AC-2`)

#### 5.7.1 Objective & Traceability
Verify that `compute_wip_by_developer` groups unfinished issues by contributor pseudonym, applies SHA-256 masking (`Developer <HEX>`), and flags developers whose active workload exceeds `project.wip_limit_per_dev` (`3 > 2` for `pseudo-dev-alpha`). Traces to `US-76` AC-2 and Invariant 4.

#### 5.7.2 Preconditions
- `project.wip_limit_per_dev = 2`.
- `pseudo-dev-alpha` assigned to 3 WIP issues (`MANTA-105`, `MANTA-106`, `MANTA-107`).
- `pseudo-dev-beta` assigned to 1 WIP issue (`MANTA-108`).

#### 5.7.3 Execution Sequence
```python
response = await client.get(
    f"/api/v1/projects/{project_id}/flow-metrics?sprint_id={sprint_id}",
    headers=auth_headers,
)
dev_wip = response.json()["wip_by_developer"]
```

#### 5.7.4 Verification Assertions
```python
assert dev_wip["limit_per_developer"] == 2
developers = dev_wip["developers"]

# Developer Alpha check (over limit)
dev_alpha = next(d for d in developers if d["count"] == 3)
assert dev_alpha["limit"] == 2
assert dev_alpha["over_limit"] is True
assert dev_alpha["developer_label"].startswith("Developer ")

# PII Protection: raw pseudonym string must NOT appear in output
assert "pseudo-dev-alpha" not in str(dev_wip)
```

---

### Scenario `TC-SPRINT-FLOW-008`: RBAC Enforcement: DEVELOPER Role Forbidden (HTTP 403) (`AC-3`)

#### 5.8.1 Objective & Traceability
Verify that callers holding the `DEVELOPER` role are strictly denied access to sprint burndown and flow metrics endpoints, returning `HTTP 403 Forbidden`. Traces to `US-76` AC-3, `SCRUM-370`, and Invariant 5.

#### 5.8.2 Preconditions
- User authenticated with RS256 JWT containing `realm_access.roles: ["DEVELOPER"]`.

#### 5.8.3 Execution Sequence
```python
dev_token = create_jwt_token(roles=["DEVELOPER"], tenant_id=tenant_id)

async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    # 1. Burndown endpoint check
    resp_burndown = await client.get(
        f"/api/v1/projects/{project_id}/sprints/burndown",
        headers={"Authorization": f"Bearer {dev_token}"},
    )
    # 2. Flow metrics endpoint check
    resp_flow = await client.get(
        f"/api/v1/projects/{project_id}/flow-metrics",
        headers={"Authorization": f"Bearer {dev_token}"},
    )
```

#### 5.8.4 Verification Assertions
```python
assert resp_burndown.status_code == 403
assert resp_flow.status_code == 403
assert "permission" in resp_burndown.json()["detail"].lower()
```

---

### Scenario `TC-SPRINT-FLOW-009`: RBAC Enforcement: SCRUM_MASTER & Management Allowed (HTTP 200) (`AC-3`)

#### 5.9.1 Objective & Traceability
Verify that callers with authorized roles (`SCRUM_MASTER`, `PROJECT_MANAGER`, `DELIVERY_DIRECTOR`, `TENANT_ADMIN`) are granted `HTTP 200 OK` access to both burndown and flow metrics endpoints. Traces to `US-76` AC-3 and Invariant 5.

#### 5.9.2 Preconditions
- Tokens generated for `SCRUM_MASTER` and `PROJECT_MANAGER`.

#### 5.9.3 Execution Sequence
```python
for role in ["SCRUM_MASTER", "PROJECT_MANAGER", "DELIVERY_DIRECTOR", "TENANT_ADMIN"]:
    token = create_jwt_token(roles=[role], tenant_id=tenant_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/flow-metrics?sprint_id={sprint_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"Role {role} failed with {resp.status_code}"
```

#### 5.9.4 Verification Assertions
```python
assert resp.status_code == 200
assert "burndown" in resp.json()
assert "wip" in resp.json()
```

---

### Scenario `TC-SPRINT-FLOW-010`: Multi-Tenant Row-Level Security (RLS) Isolation (`AC-3`)

#### 5.10.1 Objective & Traceability
Verify that sprint flow metrics cannot be accessed across tenant boundaries. A request from Tenant Beta targeting Tenant Alpha's project ID returns `HTTP 404 Not Found` without disclosing sprint metadata. Traces to `US-76` AC-3 and Invariant 5.

#### 5.10.2 Preconditions
- Project and sprint exist under Tenant Alpha.
- Caller authenticated under Tenant Beta.

#### 5.10.3 Execution Sequence
```python
beta_token = create_jwt_token(roles=["SCRUM_MASTER"], tenant_id=tenant_beta_id)

async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get(
        f"/api/v1/projects/{project_alpha_id}/flow-metrics",
        headers={"Authorization": f"Bearer {beta_token}"},
    )
```

#### 5.10.4 Verification Assertions
```python
assert response.status_code == 404
assert response.json()["detail"] == "Project not found"
```

---

### Scenario `TC-SPRINT-FLOW-011`: Web Portal: Sprint Burndown Chart Rendering & Trajectory Warning (`AC-1` / `UI`)

#### 5.11.1 Objective & Traceability
Verify that the Web Portal `/sprint-wip` page renders `SprintBurndownChart` using Recharts, plotting the actual remaining line ($40 \rightarrow 32 \rightarrow 20\text{ SP}$) and the dashed ideal line. Because actual points exceed the ideal trajectory ($20 > 17.78$ on Day 6), the status pill renders `BEHIND SCHEDULE`. Traces to `US-76` AC-1 and `Task 76.3`.

#### 5.11.2 Preconditions
- Web portal running; Playwright test authenticated as `scrum-master@manta.local`.
- 10-issue fixture returned by mock or live API.

#### 5.11.3 Execution Sequence
```typescript
// Playwright E2E Test Execution:
await page.goto("/sprint-wip");
await expect(page.locator("h1")).toContainText("Sprint Flow & WIP");
```

#### 5.11.4 Verification Assertions
```typescript
// 1. Burndown SVG elements present
const chart = page.locator(".recharts-responsive-container");
await expect(chart).toBeVisible();

// 2. Trajectory Status Pill shows BEHIND SCHEDULE
const statusPill = page.getByText("BEHIND SCHEDULE");
await expect(statusPill).toBeVisible();

// 3. Summary stats reflect remaining vs committed SP
await expect(page.getByText("20 SP")).toBeVisible(); // Remaining
await expect(page.getByText("40 SP")).toBeVisible(); // Total
```

---

### Scenario `TC-SPRINT-FLOW-012`: Web Portal: WIP Congestion Banner & Critical Alert Rendering (`AC-2` / `UI`)

#### 5.12.1 Objective & Traceability
Verify that the `WipCongestion` and `WipByDeveloper` UI components render the 4 issues in `In Progress`, highlight the status box in red tone with an `OVER LIMIT` alert (`4 / 3`), and flag Developer Alpha in the developer breakdown. Traces to `US-76` AC-2 and `Task 76.4`.

#### 5.12.2 Preconditions
- Web portal on `/sprint-wip`.

#### 5.12.3 Execution Sequence
```typescript
await page.goto("/sprint-wip");
```

#### 5.12.4 Verification Assertions
```typescript
// 1. Stage Card for In Progress indicates congestion
const inProgressCard = page.locator("text=In Progress").locator("..");
await expect(inProgressCard).toContainText("4");
await expect(page.getByText("WIP CRITICAL")).toBeVisible();

// 2. Developer card highlights over-limit developer
const devCard = page.locator("text=Developer ").first().locator("..");
await expect(devCard).toContainText("3");
await expect(devCard).toContainText("Limit 2");
```

---

## 6. Automated Test Suites & Verification Execution Guide

### 6.1 Automated Test Class & Coverage Matrix

| Test Suite / Class | Test File Location | Scenarios Covered | Target Framework |
| :--- | :--- | :--- | :--- |
| **`test_sprint_flow_metrics.py`** | `backend/tests/test_sprint_flow_metrics.py` | `TC-SPRINT-FLOW-001` through `TC-SPRINT-FLOW-007`, `TC-SPRINT-FLOW-010` | pytest, pytest-asyncio, httpx, SQLAlchemy |
| **`test_permission_enforcement.py`** | `backend/tests/test_permission_enforcement.py` | `TC-SPRINT-FLOW-009` (`VIEW_SPRINT_BURNDOWN_WIP` authorized access) | pytest, FastAPI dependencies |
| **`test_role_access_scenarios.py`** | `backend/tests/test_role_access_scenarios.py` | `TC-SPRINT-FLOW-008` (`DEVELOPER` and `TOP_MANAGEMENT` 403 checks) | pytest, RBAC matrix |
| **`sprint-wip-flow-metrics.spec.ts`** | `web/e2e/sprint-wip-flow-metrics.spec.ts` | `TC-SPRINT-FLOW-011`, `TC-SPRINT-FLOW-012` | Playwright E2E, Next.js |

---

### 6.2 Local Execution & CI Verification Commands

```bash
# ==============================================================================
# 1. Run Backend Sprint Flow Metrics Pytest Suite
# ==============================================================================
cd backend
pytest tests/test_permission_enforcement.py -k "VIEW_SPRINT_BURNDOWN_WIP" -v
pytest tests/test_role_access_scenarios.py -k "sprint" -v

# ==============================================================================
# 2. Check Python Code Formatting & Linting
# ==============================================================================
ruff format --check .
ruff check .

# ==============================================================================
# 3. Run Web Portal Playwright E2E Tests for Sprint & WIP Dashboard
# ==============================================================================
cd ../web
npx playwright test e2e/sprint-wip-flow-metrics.spec.ts --project=chromium
```

---

## 7. Acceptance Criteria Verification Sign-Off

- [x] **AC-1 (10-Issue / 40-SP Burndown Trajectory):** Validated by `TC-SPRINT-FLOW-001` through `TC-SPRINT-FLOW-004` and `TC-SPRINT-FLOW-011`. 10 committed issues totaling $40\text{ SP}$ burndown to $32\text{ SP}$ on Day 3 ($8\text{ SP}$ Done) and $20\text{ SP}$ on Day 6 ($12\text{ SP}$ Done). Ideal burndown slope calculates monotonically from $40.0\text{ SP}$ to $0.0\text{ SP}$ over 10 days.
- [x] **AC-2 (Active WIP Congestion & Developer Limits):** Validated by `TC-SPRINT-FLOW-005` through `TC-SPRINT-FLOW-007` and `TC-SPRINT-FLOW-012`. Exactly 4 unfinished issues in `In Progress` status exceed stage limit 3 (`over_limit = True`); Developer Alpha holding 3 issues exceeds per-developer limit 2; contributor pseudonyms masked via SHA-256.
- [x] **AC-3 (RBAC HTTP 403 Enforcement):** Validated by `TC-SPRINT-FLOW-008` through `TC-SPRINT-FLOW-010`. `Permission.VIEW_SPRINT_BURNDOWN_WIP` enforces `HTTP 403 Forbidden` for `DEVELOPER`, `TOP_MANAGEMENT`, and `SOFTWARE_ARCHITECT`, while granting `HTTP 200 OK` to `SCRUM_MASTER`, `PROJECT_MANAGER`, and `TENANT_ADMIN`. Cross-tenant calls yield `HTTP 404 Not Found`.
