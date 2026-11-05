# Role-Based Access Control (RBAC) Test Scenarios

**Task:** Create role-access test scenarios (`SCRUM-311`)  
**Parent User Story:** `SCRUM-247` (`US-11` Role-based access control)  
**Related Tickets:**  
- `SCRUM-306`: Define role-permission matrix for the seven roles ([`docs/roles-permissions.md`](roles-permissions.md), [`docs/roles-permissions.json`](roles-permissions.json))  
- `SCRUM-308`: Register placeholder stubs & OpenAPI markings ([`backend/tests/test_permission_enforcement.py`](../backend/tests/test_permission_enforcement.py))  
- `SCRUM-309`: Frontend permissions helper & navigation control gating ([`web/src/lib/permissions.ts`](../web/src/lib/permissions.ts), [`web/e2e/rbac-permissions.spec.ts`](../web/e2e/rbac-permissions.spec.ts))  
- `SCRUM-269`: Role-based Administration Access ([`web/e2e/admin-role-access.spec.ts`](../web/e2e/admin-role-access.spec.ts))  
- `SCRUM-240`: Sign in via organization Identity Provider ([`docs/auth-test-scenarios.md`](auth-test-scenarios.md))  
- `SCRUM-244`: Database-enforced tenant isolation ([`docs/rls-test-scenarios.md`](rls-test-scenarios.md))  

**Authoritative Machine-Readable Matrix:** [`docs/roles-permissions.json`](roles-permissions.json)  
**Authoritative Matrix Specification:** [`docs/roles-permissions.md`](roles-permissions.md)  
**Identity Provider Fixture:** Keycloak 24.0.5 (`manta` test realm — [`infra/keycloak/realm-export.json`](../infra/keycloak/realm-export.json))  
**Automated Pytest Suites:**  
- [`backend/tests/test_role_access_scenarios.py`](../backend/tests/test_role_access_scenarios.py) (Scenario-by-scenario verification of all representative allowed and denied actions)  
- [`backend/tests/test_permission_enforcement.py`](../backend/tests/test_permission_enforcement.py) (Exhaustive verification of all 91 matrix cells & middleware gates)  
- [`backend/tests/test_role_permissions_matrix.py`](../backend/tests/test_role_permissions_matrix.py) (Matrix integrity, fallback sync & schema conformance)  
**Automated E2E Playwright Suite:**  
- [`web/e2e/rbac-permissions.spec.ts`](../web/e2e/rbac-permissions.spec.ts) (UI navigation visibility, `<Can do="..." />` gating, and unauthorized redirect tests)  

---

## 1. Objective & Architecture Invariants

Authorization in the M.A.N.T.A. Engineering Intelligence Platform follows strict defense-in-depth principles:

1. **Server-Side Authorization Boundary (FastAPI Backend):**  
   The frontend (Next.js 15) and mobile client (Flutter 3.x) are untrusted execution environments. Every API route handler enforces mandatory authorization checks via `require_permission(Permission.<ACTION>)` or Starlette route middleware.
2. **Standardized HTTP Status Codes:**  
   - **Allowed Actions (`ALLOW`):**  
     - Fully implemented endpoints return **`HTTP 200 OK`** (e.g. `MANAGE_WEBHOOK_INTEGRATIONS`).  
     - Verified placeholder stubs return **`HTTP 501 Not Implemented`** with schema `{"status": "not_implemented", "action": "<ACTION_ID>", "detail": "Route is reserved for <ACTION_ID> and protected by permission check. Implementation pending."}`.  
   - **Denied Actions (`DENY`):**  
     - Authenticated callers lacking permission for the requested route receive **`HTTP 403 Forbidden`** with consistent response body:  
       ```json
       {
         "detail": "Forbidden: insufficient permissions for this operation"
       }
       ```  
   - **Unauthenticated Callers:**  
     - Requests lacking a valid Bearer token return **`HTTP 401 Unauthorized`** (`{"detail": "Not authenticated"}`).  
3. **UI Visibility & Deny-by-Default Policy (Next.js Portal):**  
   - Navigation links, action buttons, and operational controls are gated using `canUser(roles, action)` and the `<Can do="...">` component.  
   - Controls that a user's role cannot use are **completely removed from the DOM** (`not.toBeVisible()`), not merely disabled, minimizing attack surfaces.  
   - Direct browser URL navigation to unauthorized protected routes (e.g., `/integrations`, `/workspace-settings`) is intercepted by `PortalAuthGuard` in `web/src/app/(portal)/layout.tsx` and immediately redirects to `/`.  
4. **Additive Multi-Role Composition:**  
   If a user is assigned multiple realm roles (e.g., `DEVELOPER` + `SOLUTION_ARCHITECT`), permissions are evaluated as the union of allowed actions ($\text{Roles} = R_1 \cup R_2$). If *any* assigned role grants `ALLOW`, the action is permitted.  
5. **Deprecated Identifier Aliasing:**  
   The deprecated identifier `DELIVERY_EXEC` (from Architectural Blueprint §6.2) is normalized automatically to `DELIVERY_DIRECTOR`.  

---

## 2. Test Realm User Fixtures (`infra/keycloak/realm-export.json`)

The test suite leverages pre-seeded test personas configured in the Keycloak test realm across two isolated test tenants (`Tenant Alpha` and `Tenant Beta`):

| Role Identifier | Canonical Display Name | Keycloak Test Username | Password | Assigned Realm Roles | Tenant Context (`tenant_id`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `DEVELOPER` | Developer | `alpha_dev` | `dev123` | `DEVELOPER` | `11111111-1111-1111-1111-111111111111` (Alpha) |
| `SCRUM_MASTER` | Scrum Master | `alpha_scrum` | `scrum123` | `SCRUM_MASTER` | `11111111-1111-1111-1111-111111111111` (Alpha) |
| `PROJECT_MANAGER` | Project Manager | `alpha_pm` | `pm123` | `PROJECT_MANAGER` | `11111111-1111-1111-1111-111111111111` (Alpha) |
| `SOLUTION_ARCHITECT`| Solution Architect | `alpha_arch` | `arch123` | `SOLUTION_ARCHITECT` | `11111111-1111-1111-1111-111111111111` (Alpha) |
| `DELIVERY_DIRECTOR` | Delivery Director | `alpha_director` | `director123` | `DELIVERY_DIRECTOR` | `11111111-1111-1111-1111-111111111111` (Alpha) |
| *(Alias)* | *(Deprecated Alias)* | `beta_exec` | `exec123` | `DELIVERY_EXEC` | `22222222-2222-2222-2222-222222222222` (Beta) |
| `TOP_MANAGEMENT` | Top Management | `alpha_top` | `top123` | `TOP_MANAGEMENT` | `11111111-1111-1111-1111-111111111111` (Alpha) |
| `TENANT_ADMIN` | Tenant Administrator| `alpha_admin` | `admin123` | `TENANT_ADMIN`, `SOLUTION_ARCHITECT` | `11111111-1111-1111-1111-111111111111` (Alpha) |

---

## 3. Representative Role-Access Scenarios Matrix (7 Roles)

Below is the definitive scenario matrix mapping representative allowed and denied actions for all seven roles to the authoritative [Role-Permission Matrix](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) and machine-readable [`roles-permissions.json`](roles-permissions.json):

| Scenario ID | Role Under Test | Action Evaluated | Matrix Decision | Target Endpoint & Method | Expected API Status Code | Expected UI Visibility & Client Outcome | Automated Test Link |
| :--- | :--- | :--- | :---: | :--- | :---: | :--- | :--- |
| **`TC-RBAC-DEV-001`** | `DEVELOPER` | [`TRIGGER_REFACTOR_ANALYSIS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `POST /api/v1/refactoring/projects/{id}/analyze` | **`501 Not Implemented`** / **`200 OK`** | Navigation link `nav-link-refactoring-verification` is visible; refactoring trigger button is rendered in DOM. | `test_role_access_scenarios.py` $\rightarrow$ `test_developer_allowed_trigger_refactor_analysis` |
| **`TC-RBAC-DEV-002`** | `DEVELOPER` | [`MANAGE_WEBHOOK_INTEGRATIONS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`DENY`** | `GET /api/v1/integrations` | **`403 Forbidden`** | `ADMINISTRATION` header and `nav-link-integrations` are hidden; direct URL navigation to `/integrations` redirects to `/`. | `test_role_access_scenarios.py` $\rightarrow$ `test_developer_denied_manage_webhook_integrations` |
| **`TC-RBAC-SM-001`** | `SCRUM_MASTER` | [`VIEW_SPRINT_BURNDOWN_WIP`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `GET /api/v1/projects/{id}/sprints/burndown` | **`501 Not Implemented`** / **`200 OK`** | Navigation link `nav-link-sprint-wip` is visible; burndown and WIP congestion graphs render. | `test_role_access_scenarios.py` $\rightarrow$ `test_scrum_master_allowed_view_sprint_burndown` |
| **`TC-RBAC-SM-002`** | `SCRUM_MASTER` | [`TRIGGER_REFACTOR_ANALYSIS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`DENY`** | `POST /api/v1/refactoring/projects/{id}/analyze` | **`403 Forbidden`** | Trigger refactor action button is not rendered in DOM (purged via `<Can do="...">` check). | `test_role_access_scenarios.py` $\rightarrow$ `test_scrum_master_denied_trigger_refactor_analysis` |
| **`TC-RBAC-PM-001`** | `PROJECT_MANAGER` | [`RECEIVE_SCOPE_CREEP_ALERT`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `GET /api/v1/projects/{id}/scope-alerts` | **`501 Not Implemented`** / **`200 OK`** | Navigation link `nav-link-scope-creep` is visible; scope alert badges and warning dots render in sidebar. | `test_role_access_scenarios.py` $\rightarrow$ `test_project_manager_allowed_scope_creep_alert` |
| **`TC-RBAC-PM-002`** | `PROJECT_MANAGER` | [`REASSIGN_PR_REVIEWER_MOBILE`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`DENY`** | `POST /api/v1/projects/{id}/pull-requests/{pr_id}/reassign-reviewer` | **`403 Forbidden`** | Operational reviewer reassignment button is hidden on PR cards; BA checklist item 2 enforced. | `test_role_access_scenarios.py` $\rightarrow$ `test_project_manager_denied_reassign_pr_reviewer` |
| **`TC-RBAC-SA-001`** | `SOLUTION_ARCHITECT` | [`PUBLISH_GLOBAL_ADR`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `POST /api/v1/adrs/{id}/publish-global` | **`501 Not Implemented`** / **`200 OK`** | Navigation link `nav-link-adr-governance` is visible; global ADR promotion sign-off gate is accessible. | `test_role_access_scenarios.py` $\rightarrow$ `test_solution_architect_allowed_publish_global_adr` |
| **`TC-RBAC-SA-002`** | `SOLUTION_ARCHITECT` | [`MANAGE_WEBHOOK_INTEGRATIONS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`DENY`** | `GET /api/v1/integrations` | **`403 Forbidden`** | Navigation link `nav-link-integrations` is not visible; direct navigation to `/integrations` redirects to `/`. | `test_role_access_scenarios.py` $\rightarrow$ `test_solution_architect_denied_manage_webhooks` |
| **`TC-RBAC-DD-001`** | `DELIVERY_DIRECTOR` | [`VIEW_PORTFOLIO_OVERVIEW`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `GET /api/v1/dora/portfolio` | **`501 Not Implemented`** / **`200 OK`** | Executive portfolio dashboard, cross-project deadline risks, and delivery health overview are visible. | `test_role_access_scenarios.py` $\rightarrow$ `test_delivery_director_allowed_view_portfolio` |
| **`TC-RBAC-DD-002`** | `DELIVERY_DIRECTOR` | [`TRIGGER_REFACTOR_ANALYSIS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`DENY`** | `POST /api/v1/refactoring/projects/{id}/analyze` | **`403 Forbidden`** | Refactoring trigger controls are hidden in code telemetry screens. | `test_role_access_scenarios.py` $\rightarrow$ `test_delivery_director_denied_trigger_refactor` |
| **`TC-RBAC-TM-001`** | `TOP_MANAGEMENT` | [`VIEW_PORTFOLIO_OVERVIEW`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `GET /api/v1/dora/portfolio` | **`501 Not Implemented`** / **`200 OK`** | Strategic C-level executive summary, DORA benchmarks, and cross-project velocity trends are visible. | `test_role_access_scenarios.py` $\rightarrow$ `test_top_management_allowed_view_portfolio` |
| **`TC-RBAC-TM-002`** | `TOP_MANAGEMENT` | [`VIEW_SPRINT_BURNDOWN_WIP`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`DENY`** | `GET /api/v1/projects/{id}/sprints/burndown` | **`403 Forbidden`** | Navigation link `nav-link-sprint-wip` is not visible; operational sprint WIP charts are restricted. | `test_role_access_scenarios.py` $\rightarrow$ `test_top_management_denied_view_sprint_burndown` |
| **`TC-RBAC-TA-001`** | `TENANT_ADMIN` | [`MANAGE_WEBHOOK_INTEGRATIONS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) | **`ALLOW`** | `GET /api/v1/integrations` | **`200 OK`** | `ADMINISTRATION` section header and `nav-link-integrations` are visible; webhook management table renders. | `test_role_access_scenarios.py` $\rightarrow$ `test_tenant_admin_allowed_manage_integrations` |
| **`TC-RBAC-TA-002`** | `TENANT_ADMIN` | *(Cross-Tenant Boundary Isolation)* | **`DENY`** *(RLS)* | `GET /api/v1/integrations` *(targeting Tenant Beta)* | **`0 rows returned`** / **`404 Not Found`** | Tenant Beta integration secrets and webhooks are completely invisible to Tenant Alpha Administrator. | `test_tenant_boundary_adversarial.py` $\rightarrow$ `test_cross_tenant_read_by_id_returns_404` |

---

## 4. Detailed Test Scenarios per Role

### 4.1 Role 1: `DEVELOPER`

- **Role Taxonomy:** Software engineer responsible for feature implementation, personal code quality, AST/SCIP call-tree exploration, and triggering refactoring analysis with 4-tier verification (SRS §2.3, Architecture Blueprint §6.2).  
- **Keycloak Fixture:** Username `alpha_dev`, Password `dev123`, Realm Roles: `["DEVELOPER"]`.  

#### Scenario 1.1: Allowed Action — `TRIGGER_REFACTOR_ANALYSIS` (`TC-RBAC-DEV-001`)
- **Matrix Entry:** [`TRIGGER_REFACTOR_ANALYSIS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Preconditions:**  
  1. Authenticated as `DEVELOPER` within target tenant.  
  2. Git commit SHA matches SCIP index metadata SHA (`repository_head_sha == scip_index_metadata_sha`).  
- **Actions / API Request:**
  ```http
  POST /api/v1/refactoring/projects/11111111-1111-1111-1111-111111111111/analyze
  Authorization: Bearer <alpha_dev_token>
  Content-Type: application/json

  {"target_symbol": "app.auth.jwt.verify_token"}
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 501 Not Implemented`** (Verified Stub) or **`HTTP 200 OK`** (Production Implementation).  
  - Response body contains `{"status": "not_implemented", "action": "TRIGGER_REFACTOR_ANALYSIS"}`.  
- **Expected UI Visibility:**  
  - Sidebar displays `nav-link-refactoring-verification` under `ENGINEERING`.  
  - Page `/refactoring-verification` renders active "Trigger Refactor Analysis" button.  

#### Scenario 1.2: Denied Action — `MANAGE_WEBHOOK_INTEGRATIONS` (`TC-RBAC-DEV-002`)
- **Matrix Entry:** [`MANAGE_WEBHOOK_INTEGRATIONS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`DENY`).  
- **Rationale:** Webhook secrets and OAuth credentials require `TENANT_ADMIN` privileges; developers must not inspect or mutate tenant connection tokens.  
- **Actions / API Request:**
  ```http
  GET /api/v1/integrations
  Authorization: Bearer <alpha_dev_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 403 Forbidden`**.  
  - Response body: `{"detail": "Forbidden: insufficient permissions for this operation"}`.  
- **Expected UI Visibility:**  
  - The `ADMINISTRATION` sidebar heading is not present.  
  - Links `nav-link-integrations`, `nav-link-workspace-settings`, `nav-link-access-roles`, and `nav-link-privacy-erasure` are completely absent from the DOM (`expect(...).not.toBeVisible()`).  
  - Direct browser navigation to `http://localhost:3000/integrations` redirects immediately to `/`.  

#### Complete Permissions Breakdown for `DEVELOPER`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`DENY`** | `403` | Portfolio aggregated widget hidden |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`DENY`** | `403` | `nav-link-sprint-wip` hidden |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`DENY`** | `403` | Scope alert badges and push notifications suppressed |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`DENY`** | `403` | PR reviewer reassignment nudge control suppressed |
| `VIEW_OWN_CODE_QUALITY` | **`ALLOW`** | `200` / `501` | Contributor code health card visible (own data only) |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`ALLOW`** | `200` / `501` | SCIP caller/callee React Flow graph visible |
| `TRIGGER_REFACTOR_ANALYSIS` | **`ALLOW`** | `200` / `501` | Trigger refactoring action button visible |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` & `nav-link-semantic-search` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`DENY`** | `403` | ADR sign-off approval button absent from DOM |
| `PUBLISH_GLOBAL_ADR` | **`DENY`** | `403` | `nav-link-adr-governance` hidden from DOM |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`DENY`** | `403` | `nav-link-integrations` hidden; route redirects to `/` |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`DENY`** | `403` | `nav-link-workspace-settings` hidden; route redirects to `/` |

---

### 4.2 Role 2: `SCRUM_MASTER`

- **Role Taxonomy:** Agile process facilitator responsible for team sprint velocity, burndown charts, WIP limit monitoring, and operational roadblock mitigation including PR reviewer reassignment (SRS §2.3, Architecture Blueprint §6.2).  
- **Keycloak Fixture:** Username `alpha_scrum`, Password `scrum123`, Realm Roles: `["SCRUM_MASTER"]`.  

#### Scenario 2.1: Allowed Action — `VIEW_SPRINT_BURNDOWN_WIP` (`TC-RBAC-SM-001`)
- **Matrix Entry:** [`VIEW_SPRINT_BURNDOWN_WIP`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Actions / API Request:**
  ```http
  GET /api/v1/projects/11111111-1111-1111-1111-111111111111/sprints/burndown
  Authorization: Bearer <alpha_scrum_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 501 Not Implemented`** (Stub) / **`HTTP 200 OK`**.  
  - Response body contains `{"status": "not_implemented", "action": "VIEW_SPRINT_BURNDOWN_WIP"}`.  
- **Expected UI Visibility:**  
  - `nav-link-sprint-wip` is rendered under `ENGINEERING`.  
  - Burndown graphs, velocity metrics, and WIP limit congestion alerts are visible.  

#### Scenario 2.2: Denied Action — `TRIGGER_REFACTOR_ANALYSIS` (`TC-RBAC-SM-002`)
- **Matrix Entry:** [`TRIGGER_REFACTOR_ANALYSIS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`DENY`).  
- **Rationale:** Triggering automated code refactoring and sandbox AST mutation runs is strictly restricted to engineering developers and architects.  
- **Actions / API Request:**
  ```http
  POST /api/v1/refactoring/projects/11111111-1111-1111-1111-111111111111/analyze
  Authorization: Bearer <alpha_scrum_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 403 Forbidden`**.  
  - Response body: `{"detail": "Forbidden: insufficient permissions for this operation"}`.  
- **Expected UI Visibility:**  
  - Refactoring trigger buttons are omitted from the DOM via `<Can do="TRIGGER_REFACTOR_ANALYSIS">`.  

#### Complete Permissions Breakdown for `SCRUM_MASTER`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`DENY`** | `403` | Portfolio aggregated widget hidden |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`ALLOW`** | `200` / `501` | `nav-link-sprint-wip` visible |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`DENY`** | `403` | Scope alert badges and push notifications suppressed |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`ALLOW`** | `200` / `501` | One-tap PR reviewer reassignment button visible |
| `VIEW_OWN_CODE_QUALITY` | **`DENY`** | `403` | Developer code quality telemetry suppressed |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`DENY`** | `403` | SCIP symbol hierarchy and call trees hidden |
| `TRIGGER_REFACTOR_ANALYSIS` | **`DENY`** | `403` | Refactor trigger action button omitted from DOM |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`DENY`** | `403` | ADR sign-off approval button absent from DOM |
| `PUBLISH_GLOBAL_ADR` | **`DENY`** | `403` | `nav-link-adr-governance` hidden from DOM |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`DENY`** | `403` | `nav-link-integrations` hidden; route redirects to `/` |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`DENY`** | `403` | `nav-link-workspace-settings` hidden; route redirects to `/` |

---

### 4.3 Role 3: `PROJECT_MANAGER`

- **Role Taxonomy:** Delivery milestone tracker and sprint planner; primary evaluator and recipient of automated two-stage Jira Acceptance Criteria scope creep alerts (SRS §2.3, §3.2.5).  
- **Keycloak Fixture:** Username `alpha_pm`, Password `pm123`, Realm Roles: `["PROJECT_MANAGER"]`.  

#### Scenario 3.1: Allowed Action — `RECEIVE_SCOPE_CREEP_ALERT` (`TC-RBAC-PM-001`)
- **Matrix Entry:** [`RECEIVE_SCOPE_CREEP_ALERT`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Actions / API Request:**
  ```http
  GET /api/v1/projects/11111111-1111-1111-1111-111111111111/scope-alerts
  Authorization: Bearer <alpha_pm_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 501 Not Implemented`** (Stub) / **`HTTP 200 OK`**.  
  - Response body contains `{"status": "not_implemented", "action": "RECEIVE_SCOPE_CREEP_ALERT"}`.  
- **Expected UI Visibility:**  
  - `nav-link-scope-creep` is visible in the sidebar navigation under `ENGINEERING`.  
  - The `Risks & Alerts` section displays red alert dots (`hasAlertDot: true`) and notification badges.  

#### Scenario 3.2: Denied Action — `REASSIGN_PR_REVIEWER_MOBILE` (`TC-RBAC-PM-002`)
- **Matrix Entry:** [`REASSIGN_PR_REVIEWER_MOBILE`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`DENY`).  
- **Rationale:** Confirmed under BA Checklist Item 2 (`docs/roles-permissions.md#6-business-analyst-ba-review-checklist`): Operational PR reviewer reassignments are reserved for agile facilitators (Scrum Masters) and delivery executives (Delivery Directors).  
- **Actions / API Request:**
  ```http
  POST /api/v1/projects/11111111-1111-1111-1111-111111111111/pull-requests/pr-1/reassign-reviewer
  Authorization: Bearer <alpha_pm_token>
  Content-Type: application/json

  {"new_reviewer_id": "usr_alpha_dev2"}
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 403 Forbidden`**.  
  - Response body: `{"detail": "Forbidden: insufficient permissions for this operation"}`.  
- **Expected UI Visibility:**  
  - The "Reassign Reviewer" button is omitted from the PR view.  

#### Complete Permissions Breakdown for `PROJECT_MANAGER`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`DENY`** | `403` | Cross-project portfolio overview suppressed |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`ALLOW`** | `200` / `501` | `nav-link-sprint-wip` visible |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`ALLOW`** | `200` / `501` | `nav-link-scope-creep` visible; alerts displayed |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`DENY`** | `403` | Reassignment action control suppressed (BA Item 2) |
| `VIEW_OWN_CODE_QUALITY` | **`DENY`** | `403` | Developer code quality telemetry suppressed |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`DENY`** | `403` | SCIP symbol hierarchy and call trees hidden |
| `TRIGGER_REFACTOR_ANALYSIS` | **`DENY`** | `403` | Refactor trigger action button omitted from DOM |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`DENY`** | `403` | ADR sign-off approval button absent from DOM |
| `PUBLISH_GLOBAL_ADR` | **`DENY`** | `403` | `nav-link-adr-governance` hidden from DOM |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`DENY`** | `403` | `nav-link-integrations` hidden; route redirects to `/` |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`DENY`** | `403` | `nav-link-workspace-settings` hidden; route redirects to `/` |

---

### 4.4 Role 4: `SOLUTION_ARCHITECT`

- **Role Taxonomy:** Enterprise architecture authority responsible for technical debt monitoring, SCIP call graph lookups, mandatory review gate for sanitized ADRs, and sign-off for global pattern publishing (SRS §2.3, Architecture Blueprint §6.2, §6.5, §7.5).  
- **Keycloak Fixture:** Username `alpha_arch`, Password `arch123`, Realm Roles: `["SOLUTION_ARCHITECT"]`.  

#### Scenario 4.1: Allowed Action — `PUBLISH_GLOBAL_ADR` (`TC-RBAC-SA-001`)
- **Matrix Entry:** [`PUBLISH_GLOBAL_ADR`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Preconditions:**  
  1. Mandatory sign-off gate: caller holds `SOLUTION_ARCHITECT` or `TENANT_ADMIN`.  
  2. Record precondition: `assert adr.status == 'approved' and adr.sanitization_verified is True`.  
- **Actions / API Request:**
  ```http
  POST /api/v1/adrs/adr-1/publish-global
  Authorization: Bearer <alpha_arch_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 501 Not Implemented`** (Stub) / **`HTTP 200 OK`**.  
  - Response body contains `{"status": "not_implemented", "action": "PUBLISH_GLOBAL_ADR"}`.  
- **Expected UI Visibility:**  
  - `nav-link-adr-governance` is visible under `ADMINISTRATION`.  
  - Page `/adr-governance` renders the "Publish to Global" action controls.  

#### Scenario 4.2: Denied Action — `MANAGE_WEBHOOK_INTEGRATIONS` (`TC-RBAC-SA-002`)
- **Matrix Entry:** [`MANAGE_WEBHOOK_INTEGRATIONS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`DENY`).  
- **Rationale:** Solution Architects govern software architecture and ADRs, but do not administer tenant infrastructure credentials, webhook secrets, or KMS keys.  
- **Actions / API Request:**
  ```http
  GET /api/v1/integrations
  Authorization: Bearer <alpha_arch_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 403 Forbidden`**.  
  - Response body: `{"detail": "Forbidden: insufficient permissions for this operation"}`.  
- **Expected UI Visibility:**  
  - While `nav-link-adr-governance` is shown, `nav-link-integrations` and `nav-link-workspace-settings` are not rendered.  
  - Navigating directly to `/integrations` redirects to `/`.  

#### Complete Permissions Breakdown for `SOLUTION_ARCHITECT`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`DENY`** | `403` | Cross-project portfolio overview suppressed |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`DENY`** | `403` | `nav-link-sprint-wip` hidden |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`DENY`** | `403` | Scope alerts suppressed |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`DENY`** | `403` | Reviewer reassignment controls suppressed |
| `VIEW_OWN_CODE_QUALITY` | **`DENY`** | `403` | Contributor-specific telemetry suppressed |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`ALLOW`** | `200` / `501` | Technical debt hotspots & SCIP caller trees visible |
| `TRIGGER_REFACTOR_ANALYSIS` | **`ALLOW`** | `200` / `501` | Trigger refactoring analysis action visible |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`ALLOW`** | `200` / `501` | Human-in-the-loop ADR review & approval controls visible |
| `PUBLISH_GLOBAL_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-governance` visible in sidebar |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`DENY`** | `403` | `nav-link-integrations` hidden; route redirects to `/` |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`DENY`** | `403` | `nav-link-workspace-settings` hidden; route redirects to `/` |

---

### 4.5 Role 5: `DELIVERY_DIRECTOR` (and Alias `DELIVERY_EXEC`)

- **Role Taxonomy:** Delivery executive responsible for portfolio-wide delivery health, cross-project deadline risk detection, roadblock intervention, and one-tap mobile reviewer reassignment (SRS §2.3, Architecture Blueprint §6.2, §11.2).  
- **Keycloak Fixture:** Username `alpha_director`, Password `director123`, Realm Roles: `["DELIVERY_DIRECTOR"]` *(and `beta_exec` with `["DELIVERY_EXEC"]`)*.  

#### Scenario 5.1: Allowed Action — `VIEW_PORTFOLIO_OVERVIEW` (`TC-RBAC-DD-001`)
- **Matrix Entry:** [`VIEW_PORTFOLIO_OVERVIEW`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Actions / API Request:**
  ```http
  GET /api/v1/dora/portfolio
  Authorization: Bearer <alpha_director_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 501 Not Implemented`** (Stub) / **`HTTP 200 OK`**.  
  - Response body contains `{"status": "not_implemented", "action": "VIEW_PORTFOLIO_OVERVIEW"}`.  
- **Expected UI Visibility:**  
  - Cross-project executive delivery metrics, consolidated DORA benchmarks, and SLA risk summaries are visible on the dashboard.  

#### Scenario 5.2: Denied Action — `TRIGGER_REFACTOR_ANALYSIS` (`TC-RBAC-DD-002`)
- **Matrix Entry:** [`TRIGGER_REFACTOR_ANALYSIS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`DENY`).  
- **Rationale:** Delivery Directors focus on portfolio delivery health and SLA interventions; initiating sandbox code refactor transformations is restricted to engineering developers and architects.  
- **Actions / API Request:**
  ```http
  POST /api/v1/refactoring/projects/11111111-1111-1111-1111-111111111111/analyze
  Authorization: Bearer <alpha_director_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 403 Forbidden`**.  
  - Response body: `{"detail": "Forbidden: insufficient permissions for this operation"}`.  
- **Expected UI Visibility:**  
  - Refactoring trigger buttons are not present in the DOM.  

#### Complete Permissions Breakdown for `DELIVERY_DIRECTOR`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`ALLOW`** | `200` / `501` | Cross-project portfolio overview dashboard visible |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`ALLOW`** | `200` / `501` | `nav-link-sprint-wip` visible |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`ALLOW`** | `200` / `501` | `nav-link-scope-creep` visible; alerts received |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`ALLOW`** | `200` / `501` | One-tap PR reviewer reassignment control active |
| `VIEW_OWN_CODE_QUALITY` | **`DENY`** | `403` | Contributor code quality telemetry suppressed |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`DENY`** | `403` | SCIP caller/callee trees hidden |
| `TRIGGER_REFACTOR_ANALYSIS` | **`DENY`** | `403` | Refactor trigger action button omitted from DOM |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`DENY`** | `403` | ADR sign-off approval button absent from DOM |
| `PUBLISH_GLOBAL_ADR` | **`DENY`** | `403` | `nav-link-adr-governance` hidden from DOM |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`DENY`** | `403` | `nav-link-integrations` hidden; route redirects to `/` |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`DENY`** | `403` | `nav-link-workspace-settings` hidden; route redirects to `/` |

---

### 4.6 Role 6: `TOP_MANAGEMENT`

- **Role Taxonomy:** Strategic executive leadership (C-level, board, executive VP) with purely read-only strategic oversight of portfolio delivery health, deadline risks, and high-level DORA benchmarks (SRS §2.3, BA Review Checklist Item 3).  
- **Keycloak Fixture:** Username `alpha_top`, Password `top123`, Realm Roles: `["TOP_MANAGEMENT"]`.  

#### Scenario 6.1: Allowed Action — `VIEW_PORTFOLIO_OVERVIEW` (`TC-RBAC-TM-001`)
- **Matrix Entry:** [`VIEW_PORTFOLIO_OVERVIEW`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Actions / API Request:**
  ```http
  GET /api/v1/dora/portfolio
  Authorization: Bearer <alpha_top_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 501 Not Implemented`** (Stub) / **`HTTP 200 OK`**.  
  - Response body contains `{"status": "not_implemented", "action": "VIEW_PORTFOLIO_OVERVIEW"}`.  
- **Expected UI Visibility:**  
  - Executive summary and strategic portfolio DORA benchmarks render on `/`.  

#### Scenario 6.2: Denied Action — `VIEW_SPRINT_BURNDOWN_WIP` (`TC-RBAC-TM-002`)
- **Matrix Entry:** [`VIEW_SPRINT_BURNDOWN_WIP`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`DENY`).  
- **Rationale:** Top Management maintains high-level read-only portfolio governance; team-level WIP limits, ticket burndowns, and operational task reassignments are strictly hidden to prevent micromanagement and clutter (BA Checklist Item 3).  
- **Actions / API Request:**
  ```http
  GET /api/v1/projects/11111111-1111-1111-1111-111111111111/sprints/burndown
  Authorization: Bearer <alpha_top_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 403 Forbidden`**.  
  - Response body: `{"detail": "Forbidden: insufficient permissions for this operation"}`.  
- **Expected UI Visibility:**  
  - Navigation item `nav-link-sprint-wip` is completely absent from the sidebar.  

#### Complete Permissions Breakdown for `TOP_MANAGEMENT`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`ALLOW`** | `200` / `501` | Strategic portfolio dashboard visible |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`DENY`** | `403` | `nav-link-sprint-wip` hidden (BA Item 3) |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`DENY`** | `403` | Scope alerts suppressed |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`DENY`** | `403` | Reassignment controls suppressed |
| `VIEW_OWN_CODE_QUALITY` | **`DENY`** | `403` | Contributor code quality telemetry suppressed |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`DENY`** | `403` | SCIP caller/callee trees hidden |
| `TRIGGER_REFACTOR_ANALYSIS` | **`DENY`** | `403` | Refactor trigger action button omitted from DOM |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`DENY`** | `403` | ADR sign-off approval button absent from DOM |
| `PUBLISH_GLOBAL_ADR` | **`DENY`** | `403` | `nav-link-adr-governance` hidden from DOM |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`DENY`** | `403` | `nav-link-integrations` hidden; route redirects to `/` |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`DENY`** | `403` | `nav-link-workspace-settings` hidden; route redirects to `/` |

---

### 4.7 Role 7: `TENANT_ADMIN`

- **Role Taxonomy:** Organization administrator with full administrative authority over tenant settings, user provisioning, role assignments, webhook integration credentials, and GDPR Article 17 erasure requests (SRS §2.3, Architecture Blueprint §6.2, §10.3).  
- **Keycloak Fixture:** Username `alpha_admin`, Password `admin123`, Realm Roles: `["TENANT_ADMIN"]`.  

#### Scenario 7.1: Allowed Action — `MANAGE_WEBHOOK_INTEGRATIONS` (`TC-RBAC-TA-001`)
- **Matrix Entry:** [`MANAGE_WEBHOOK_INTEGRATIONS`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) (`ALLOW`).  
- **Actions / API Request:**
  ```http
  GET /api/v1/integrations
  Authorization: Bearer <alpha_admin_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 200 OK`** (Fully implemented endpoint).  
  - Response body contains list of tenant integration records (`id`, `provider`, `status`, `events_count`).  
- **Expected UI Visibility:**  
  - The `ADMINISTRATION` sidebar heading is visible.  
  - Links `nav-link-integrations`, `nav-link-workspace-settings`, `nav-link-access-roles`, and `nav-link-privacy-erasure` are all visible.  
  - Navigating to `/integrations` succeeds and displays the webhook management table.  

#### Scenario 7.2: Denied Action / Boundary Gate — Cross-Tenant Boundary (`TC-RBAC-TA-002`)
- **Matrix Entry:** Multi-Tenancy Architecture Invariant §1.2 & §4.1 (`DENY` across tenants).  
- **Rationale:** Administrative authority is strictly scoped to the administrator's own tenant boundary (`tenant_id = 11111111-...`). Under PostgreSQL Row-Level Security (`FORCE ROW LEVEL SECURITY`), any attempt by Tenant Alpha Admin to query or mutate Tenant Beta's integrations or user records is intercepted by the database engine.  
- **Actions / API Request:**
  ```http
  GET /api/v1/integrations/22222222-2222-2222-2222-222222222222
  Authorization: Bearer <alpha_admin_token>
  ```
- **Expected API Status Code & Body:**  
  - **`HTTP 404 Not Found`** (or zero rows returned).  
  - M.A.N.T.A. never discloses whether a foreign tenant's record exists, preventing enumeration attacks.  
- **Expected UI Visibility:**  
  - Only Tenant Alpha repositories, projects, and webhooks are visible on the admin dashboard.  

#### Complete Permissions Breakdown for `TENANT_ADMIN`:
| Action ID | Matrix Decision | Expected API Status Code | UI Element & State |
| :--- | :---: | :---: | :--- |
| `VIEW_PROJECT_DORA` | **`ALLOW`** | `200` / `501` | `nav-link-delivery-analytics` visible |
| `VIEW_PORTFOLIO_OVERVIEW` | **`ALLOW`** | `200` / `501` | Cross-project portfolio overview visible |
| `VIEW_SPRINT_BURNDOWN_WIP` | **`ALLOW`** | `200` / `501` | `nav-link-sprint-wip` visible |
| `RECEIVE_SCOPE_CREEP_ALERT` | **`ALLOW`** | `200` / `501` | `nav-link-scope-creep` visible |
| `REASSIGN_PR_REVIEWER_MOBILE` | **`ALLOW`** | `200` / `501` | One-tap PR reviewer reassignment control active |
| `VIEW_OWN_CODE_QUALITY` | **`ALLOW`** | `200` / `501` | Contributor code quality telemetry visible |
| `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **`ALLOW`** | `200` / `501` | Technical debt hotspots & SCIP caller trees visible |
| `TRIGGER_REFACTOR_ANALYSIS` | **`ALLOW`** | `200` / `501` | Trigger refactoring analysis action visible |
| `SEARCH_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-knowledge-base` visible |
| `APPROVE_ADR_TENANT_PRIVATE` | **`ALLOW`** | `200` / `501` | ADR sign-off approval controls visible |
| `PUBLISH_GLOBAL_ADR` | **`ALLOW`** | `200` / `501` | `nav-link-adr-governance` visible in sidebar |
| `MANAGE_WEBHOOK_INTEGRATIONS` | **`ALLOW`** | `200` | `nav-link-integrations` visible & operational |
| `MANAGE_TENANT_USERS_AND_ROLES`| **`ALLOW`** | `200` / `501` | `nav-link-workspace-settings`, `access-roles`, `privacy-erasure` visible |

---

## 5. Cross-Cutting & Edge Case Test Scenarios

| Scenario ID | Category | Preconditions & Input State | Expected API & System Outcome | Expected UI Visibility & Outcome | Automated Test Link |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`TC-RBAC-EDGE-001`** | **Role Alias Resolution** | JWT token contains deprecated role `DELIVERY_EXEC`. | Backend normalizes `DELIVERY_EXEC` to `DELIVERY_DIRECTOR`. `VIEW_PORTFOLIO_OVERVIEW` returns `501`/`200`, `TRIGGER_REFACTOR_ANALYSIS` returns `403`. | UI treats user as Delivery Director; executive portfolio controls render. | `test_role_access_scenarios.py` $\rightarrow$ `test_edge_deprecated_delivery_exec_alias` |
| **`TC-RBAC-EDGE-002`** | **Multi-Role Composition** | User has composite roles: `["DEVELOPER", "TENANT_ADMIN"]`. | Additive union of permissions: user can trigger refactor (`DEVELOPER`) AND manage webhooks (`TENANT_ADMIN`). Both endpoints return `200`/`501`. | Both engineering and administration sidebar sections are fully visible. | `test_role_access_scenarios.py` $\rightarrow$ `test_edge_multi_role_composition` |
| **`TC-RBAC-EDGE-003`** | **Empty or Unknown Roles** | JWT contains empty roles `[]` or unrecognized role `["GUEST", "DEFAULT_USER"]`. | Deny-by-default: protected endpoint calls return **`HTTP 403 Forbidden`** with `FORBIDDEN_RESPONSE`. | All privileged navigation items and buttons are hidden. | `test_role_access_scenarios.py` $\rightarrow$ `test_edge_empty_or_unknown_roles_deny_by_default` |
| **`TC-RBAC-EDGE-004`** | **Unauthenticated Request** | Request omits `Authorization` header or supplies corrupted Bearer token. | Authentication gateway rejects call with **`HTTP 401 Unauthorized`** (`{"detail": "Not authenticated"}`). | Portal redirects browser to `/login`. | `test_role_access_scenarios.py` $\rightarrow$ `test_edge_unauthenticated_request_returns_401` |
| **`TC-RBAC-EDGE-005`** | **Undeclared Route Rejection** | API route registered without `@require_permission(...)` or matrix action tag. | Fail-closed security gate rejects request with **`HTTP 403 Forbidden`**; handler is never invoked. | N/A (Server-side defense against developer omission). | `test_permission_enforcement.py` $\rightarrow$ `test_route_without_declared_permission_rejected_by_default` |

---

## 6. Acceptance Criteria Traceability & Sign-Off

| Ticket Requirement / Acceptance Criteria | Status | Verification Evidence |
| :--- | :---: | :--- |
| **AC 1: All seven roles are covered** | **`PASS`** | Detailed specifications, representative allowed/denied actions, expected status codes, and UI visibility provided for all 7 roles: `DEVELOPER`, `SCRUM_MASTER`, `PROJECT_MANAGER`, `SOLUTION_ARCHITECT`, `DELIVERY_DIRECTOR`, `TOP_MANAGEMENT`, and `TENANT_ADMIN`. |
| **AC 2: Scenarios link to matrix entries** | **`PASS`** | Every scenario explicitly links to its matrix entry in [`docs/roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions`](roles-permissions.md#3-authoritative-role-permission-matrix-7-roles--13-actions) and matches machine-readable rules in [`docs/roles-permissions.json`](roles-permissions.json). |
| **AC 3: Status codes & UI visibility specified** | **`PASS`** | Each scenario documents exact HTTP status codes (`200 OK`, `501 Not Implemented`, `403 Forbidden`, `401 Unauthorized`), response JSON schemas, sidebar element testids, and route guard redirect behaviors. |
| **AC 4: Automated Test Execution** | **`PASS`** | All scenarios are covered by automated tests in `backend/tests/test_role_access_scenarios.py`, `backend/tests/test_permission_enforcement.py`, and `web/e2e/rbac-permissions.spec.ts`. |

---

## 7. How to Execute the Automated Test Suites

### 7.1 Backend Role-Access Pytest Suite
```powershell
# Run the dedicated role-access scenario test suite
python -m pytest backend/tests/test_role_access_scenarios.py -v

# Run the exhaustive 91-cell matrix permission enforcement suite
python -m pytest backend/tests/test_permission_enforcement.py -v

# Verify matrix schema and fallback file synchronization
python -m pytest backend/tests/test_role_permissions_matrix.py -v
```

### 7.2 Frontend Playwright E2E Suite
```powershell
# Run the RBAC UI navigation & permission gating end-to-end tests
cd web
npx playwright test e2e/rbac-permissions.spec.ts
```
