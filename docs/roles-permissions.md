# M.A.N.T.A. Role-Based Access Control (RBAC/ABAC) Specification
**Definitive Role-Permission Matrix for the Seven System Roles**

- **Parent User Story:** `SCRUM-247` (`US-11` Role-based access control)
- **Task:** `SCRUM-306` (Define role permission matrix for the seven roles)
- **Version:** `1.0.0`
- **Status:** Approved / Baseline Established
- **Authoritative Machine-Readable Source:** [`docs/roles-permissions.json`](file:///c:/MANTA/MANTA/docs/roles-permissions.json)
- **Governing Specifications:**
  - AI-Augmented Engineering Intelligence Platform Architectural Blueprint & Defensible Technical Specification (v3.0) §6.2, §6.5, §7.5, §10.3, §11.1, §11.2
  - M.A.N.T.A. Software Requirements Specification (SRS v1.0) §1.2, §2.3, §2.4.1, §3.1.1, §3.2.1, §3.2.5, §3.2.6, §3.2.7, §3.2.8, §3.2.9, §3.2.11

---

## 1. Executive Summary & Architectural Invariants

Authorization in the M.A.N.T.A. platform is enforced **strictly on the server-side** (FastAPI backend); the web client (Next.js 15) and mobile client (Flutter 3.x companion) are untrusted and never relied upon to enforce security boundaries.

Permissions are evaluated dynamically across a combination of:
1. **Authenticated Identity (`sub`):** Verified via RS256 JWKS asymmetric signatures against the enterprise Identity Provider (Keycloak / corporate OIDC IdP).
2. **Tenant Boundary (`tenant_id`):** Inviolably isolated at the database storage engine layer via PostgreSQL Row-Level Security (`RLS`) on `app.current_tenant_id` under the non-privileged role `eng_app_user` (`NOSUPERUSER`, `NOBYPASSRLS`).
3. **Project Assignment (ABAC):** Within an authorized tenant, access to project-level datasets (DORA metrics, sprint velocity, commit graphs) is filtered to projects where the user is an active contributor or assignee.
4. **User Role (`role` / `realm_access.roles`):** Evaluated against the explicit permissions tabulated in this document.
5. **Requested Operation / Action:** Checked prior to executing route logic.

---

## 2. Seven System Roles Taxonomy

The seven canonical roles are derived directly from the M.A.N.T.A. Software Requirements Specification (§2.3) and synthesized with the technical gates in Architectural Blueprint §6.2:

| Role Identifier | Display Name | Core Responsibilities & Functional Perspectives | Document Source |
|---|---|---|---|
| `DEVELOPER` | Developer | Core software engineering workflows: personal code quality tracking, AST/SCIP call-tree exploration, PR reviews, and triggering AI refactor analysis with 4-tier verification. | SRS §2.3, Arch §6.2 |
| `SCRUM_MASTER` | Scrum Master | Agile process facilitation: team sprint velocity, burndown charts, WIP limit monitoring, and mobile/web operational roadblock mitigation (e.g. reviewer reassignment). | SRS §2.3, Arch §6.2 |
| `PROJECT_MANAGER` | Project Manager | Project-level delivery milestone tracking, sprint planning, and acting as the primary recipient/evaluator of two-stage Jira Acceptance Criteria scope creep alerts. | SRS §2.3, §3.2.5 |
| `SOLUTION_ARCHITECT` | Solution Architect | Enterprise architecture governance: SCIP symbol hierarchies, technical debt monitoring, mandatory review gate for sanitized ADRs, and sign-off for global pattern publishing. | SRS §2.3, Arch §6.2, §6.5, §7.5 |
| `DELIVERY_DIRECTOR` | Delivery Director | Executive operational delivery health across the enterprise portfolio, deadline risk detection, roadblock intervention, and reviewer nudges/reassignments. *(Alias: `DELIVERY_EXEC` in Arch §6.2)* | SRS §2.3, Arch §6.2, §11.2 |
| `TOP_MANAGEMENT` | Top Management | Strategic C-level and director oversight: portfolio-wide DORA performance, deadline risks, and delivery health trends (read-only governance). | SRS §2.3 |
| `TENANT_ADMIN` | Tenant Administrator | Complete tenant administration: provisioning users, assigning RBAC roles, managing GitHub/Jira/Slack webhook integration credentials, and executing GDPR Article 17 erasure requests. | SRS §2.3, Arch §6.2, §10.3 |

> [!NOTE]
> **Deprecated Role Identifier Alias (`DELIVERY_EXEC`)**:
> Architectural Blueprint §6.2 tabulates the delivery executive role under the identifier `DELIVERY_EXEC`. The canonical system identifier is standardized as `DELIVERY_DIRECTOR` to align with SRS §2.3. The system supports `DELIVERY_EXEC` as a deprecated backward-compatible alias.

---

## 3. Authoritative Role-Permission Matrix (7 Roles × 13 Actions)

Every role-action pair contains an **explicit decision** (`ALLOW` or `DENY`).

| Domain | Action / Operation | `DEV` | `SM` | `PM` | `SA` | `DD` | `TM` | `TA` | Source Reference |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| **DORA & Delivery Analytics** | `VIEW_PROJECT_DORA` | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | Arch §6.2, SRS §3.2.3 |
| | `VIEW_PORTFOLIO_OVERVIEW` | DENY | DENY | DENY | DENY | **ALLOW** | **ALLOW** | **ALLOW** | SRS §2.3, §3.2.1.4 |
| **Sprint & Risk Management** | `VIEW_SPRINT_BURNDOWN_WIP` | DENY | **ALLOW** | **ALLOW** | DENY | **ALLOW** | DENY | **ALLOW** | SRS §2.3, §3.2.4, §3.2.6 |
| | `RECEIVE_SCOPE_CREEP_ALERT` | DENY | DENY | **ALLOW** | DENY | **ALLOW** | DENY | **ALLOW** | SRS §3.2.5.4, Arch Fig 6 |
| | `REASSIGN_PR_REVIEWER_MOBILE` | DENY | **ALLOW** | DENY | DENY | **ALLOW** | DENY | **ALLOW** | Arch §6.2, §11.2 |
| **Code Intelligence & Refactoring** | `VIEW_OWN_CODE_QUALITY` | **ALLOW** | DENY | DENY | DENY | DENY | DENY | **ALLOW** | SRS §2.3, §2.4.1 |
| | `VIEW_TECH_DEBT_AND_SCIP_GRAPH` | **ALLOW** | DENY | DENY | **ALLOW** | DENY | DENY | **ALLOW** | SRS §2.3, Arch §9.2, §11.1 |
| | `TRIGGER_REFACTOR_ANALYSIS` | **ALLOW** | DENY | DENY | **ALLOW** | DENY | DENY | **ALLOW** | Arch §6.2, SRS §3.2.9 |
| **ADR Knowledge Management** | `SEARCH_ADR` | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | **ALLOW** | Arch §7.2, SRS §3.2.8 |
| | `APPROVE_ADR_TENANT_PRIVATE` | DENY | DENY | DENY | **ALLOW** *(Gate)* | DENY | DENY | **ALLOW** | Arch §6.2, §7.5 |
| | `PUBLISH_GLOBAL_ADR` | DENY | DENY | DENY | **ALLOW** *(Sign-Off)* | DENY | DENY | **ALLOW** | Arch §6.2, §6.5 |
| **Tenant Admin & Compliance** | `MANAGE_WEBHOOK_INTEGRATIONS` | DENY | DENY | DENY | DENY | DENY | DENY | **ALLOW** | Arch §6.2, SRS §3.2.11 |
| | `MANAGE_TENANT_USERS_AND_ROLES` | DENY | DENY | DENY | DENY | DENY | DENY | **ALLOW** | SRS §2.3, §3.2.11, Arch §10.3 |

**Legend:**
- `DEV`: `DEVELOPER`
- `SM`: `SCRUM_MASTER`
- `PM`: `PROJECT_MANAGER`
- `SA`: `SOLUTION_ARCHITECT`
- `DD`: `DELIVERY_DIRECTOR` (includes `DELIVERY_EXEC` alias)
- `TM`: `TOP_MANAGEMENT`
- `TA`: `TENANT_ADMIN`

---

## 4. Invariants, Preconditions, and Error Codes

### 4.1 Strict Multi-Tenancy & Project Assignment (ABAC)
- **RLS Boundary:** All relational tables (`pull_requests`, `metrics_dora`, `anonymized_adrs`, `webhook_events`) enforce `FORCE ROW LEVEL SECURITY`. Queries omit `tenant_id` in application code only because `SET LOCAL app.current_tenant_id = :tenant_id` is run upon checkout from PgBouncer. Any attempt to access cross-tenant records returns `0 rows` or triggers an aborted query.
- **Project Boundary:** `VIEW_PROJECT_DORA`, `VIEW_SPRINT_BURNDOWN_WIP`, and `VIEW_TECH_DEBT_AND_SCIP_GRAPH` require that the user is explicitly linked to the project in `project_assignments` (unless the user has the `TENANT_ADMIN`, `DELIVERY_DIRECTOR`, or `TOP_MANAGEMENT` roles). Unauthorized access returns `HTTP 403 Forbidden`.
- **Contributor Boundary:** `VIEW_OWN_CODE_QUALITY` restricts developers to inspecting records matching their own `sub` / `github_handle`.

### 4.2 Architectural Decision Records (ADR) Gates & Error Codes
Under Architectural Blueprint §6.5 and §7.5, publishing an ADR to `GLOBAL` visibility makes it discoverable across all enterprise tenants on the platform:
1. **Strict RBAC Gate:** Only users with `SOLUTION_ARCHITECT` or `TENANT_ADMIN` may execute `PUBLISH_GLOBAL_ADR`. Requests from any other role fail with **`HTTP 403 Forbidden`**.
2. **Mandatory Precondition Assertion:**
   ```python
   assert adr.status == "approved"
   assert adr.sanitization_verified is True
   ```
   If an authorized user attempts to set `visibility = 'GLOBAL'` on a draft, un-sanitized, or rejected record, the mutation is rejected with **`HTTP 422 Unprocessable Entity`**.

### 4.3 Refactoring Analysis Preconditions
Under Architectural Blueprint §9.1:
- Before any code refactoring proposal or sandbox run begins, the system asserts:
  ```python
  assert repository_head_sha == scip_index_metadata_sha
  ```
  If the Git SHA differs from the SCIP index SHA, the runner rejects execution and triggers a high-priority background re-indexing task.

### 4.4 GDPR Article 17 Erasure Protocol
Under Architectural Blueprint §10.3 and SRS §3.2.11:
- Only `TENANT_ADMIN` may invoke `POST /api/v1/compliance/anonymize-contributor`.
- Execution permanently destroys the contributor's pseudonymization salt in KMS, severs cryptographic links, overwrites personal strings in PostgreSQL with `[DELETED]`, purges personal identifiers from vector embeddings, and re-indexes the tenant's partition.

---

## 5. Keycloak Identity Provider Alignment

The local Keycloak identity provider configuration (`infra/keycloak/realm-export.json`) defines realm roles and pre-seeded test users across two test tenants:

### 5.1 Realm Roles Configured
1. `DEVELOPER`
2. `SCRUM_MASTER`
3. `PROJECT_MANAGER`
4. `SOLUTION_ARCHITECT`
5. `DELIVERY_DIRECTOR`
6. `TOP_MANAGEMENT`
7. `TENANT_ADMIN`

*(Note: `DELIVERY_EXEC` is deprecated and superseded by `DELIVERY_DIRECTOR`.)*

### 5.2 Multi-Tenant Test Users
| Tenant | Username | Password | Assigned Realm Roles | Purpose |
|---|---|---|---|---|
| **Tenant Alpha** (`11111111-...`) | `alpha_admin` | `admin123` | `TENANT_ADMIN`, `SOLUTION_ARCHITECT` | Tenant administrator & ADR gate |
| | `alpha_dev` | `dev123` | `DEVELOPER` | Engineering workflows & refactoring |
| | `alpha_scrum` | `scrum123` | `SCRUM_MASTER` | Sprint tracking & PR reviewer reassignments |
| | `alpha_pm` | `pm123` | `PROJECT_MANAGER` | Sprint planning & scope creep alerts |
| | `alpha_director` | `director123` | `DELIVERY_DIRECTOR` | Executive portfolio health & roadblock intervention |
| | `alpha_top` | `top123` | `TOP_MANAGEMENT` | Strategic read-only portfolio governance |
| **Tenant Beta** (`22222222-...`) | `beta_admin` | `admin123` | `TENANT_ADMIN` | Tenant B administrator |
| | `beta_dev` | `dev123` | `DEVELOPER` | Tenant B developer |
| | `beta_exec` | `exec123` | `DELIVERY_DIRECTOR` | Tenant B delivery director |

---

## 6. Business Analyst (BA) Review Checklist

As required by SCRUM-306 ("Review with the Business Analyst"), the following decisions are flagged for confirmation by the Business Analyst:

- [ ] **Item 1: `PROJECT_MANAGER` Scope Alerts** — Confirmed that Project Managers are the primary recipients of automated Jira Acceptance Criteria scope creep alerts (`RECEIVE_SCOPE_CREEP_ALERT: ALLOW`).
- [ ] **Item 2: `PROJECT_MANAGER` Reviewer Reassignment** — Confirmed that operational PR reviewer reassignment is reserved for Scrum Masters and Delivery Directors, remaining `DENY` for Project Managers.
- [ ] **Item 3: `TOP_MANAGEMENT` Read-Only Portfolio Scope** — Confirmed that Top Management maintains purely read-only executive overview (`VIEW_PORTFOLIO_OVERVIEW: ALLOW`), with all operational actions (`REASSIGN_PR_REVIEWER_MOBILE`, `TRIGGER_REFACTOR_ANALYSIS`) set to `DENY`.
- [ ] **Item 4: `SCRUM_MASTER` Scope Alerts** — Confirmed whether Scrum Masters should also receive secondary alerts for scope creep (currently set to `DENY` to prioritize Project Managers and Delivery Leads).
- [ ] **Item 5: `DEVELOPER` Burndown Access** — Confirmed whether Developers should have read access to full sprint burndown/WIP congestion graphs (currently `DENY`, prioritizing personal quality dashboards).
