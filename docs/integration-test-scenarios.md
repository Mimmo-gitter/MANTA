# Integration Test Scenarios: Credential Validation, Role Restrictions & Multi-Tenant Isolation

**Task:** Create test scenarios (invalid token, Developer receives HTTP 403) (`SCRUM-324`)  
**Parent User Story:** `SCRUM-249` (`US-13` Connect GitHub, Jira and Slack)  
**Related Tickets:**  
- `SCRUM-321`: Encrypt webhook secrets & API tokens via AES-256-GCM ([`backend/app/integrations/crypto.py`](../backend/app/integrations/crypto.py))  
- `SCRUM-322`: Provider connection with live credential verification ([`backend/app/integrations/validators.py`](../backend/app/integrations/validators.py))  
- `SCRUM-323`: Cryptographically unguessable UUIDv4 webhook URLs bound 1-to-1 to integrations ([`backend/app/integrations/service.py`](../backend/app/integrations/service.py))  
- `SCRUM-306`: Seven-role permission matrix definition ([`docs/roles-permissions.md`](roles-permissions.md), [`docs/roles-permissions.json`](roles-permissions.json))  
- `SCRUM-309`: Frontend permissions helper & navigation control gating ([`web/src/lib/permissions.ts`](../web/src/lib/permissions.ts))  
- `SCRUM-311`: Role-access test scenarios & automated matrix verification ([`docs/role-access-test-scenarios.md`](role-access-test-scenarios.md))  
- `SCRUM-325`: Provider-specific HMAC-SHA256 signature verification ([`backend/app/webhooks/verification.py`](../backend/app/webhooks/verification.py))  
- `SCRUM-327`: Integration-bound tenant resolution & unknown integration rejection ([`backend/app/webhooks/router.py`](../backend/app/webhooks/router.py))  
- `SCRUM-244`: Database-enforced tenant isolation via PostgreSQL RLS ([`docs/rls-test-scenarios.md`](rls-test-scenarios.md))  
- `SCRUM-269`: Role-based Administration Access ([`web/e2e/admin-role-access.spec.ts`](../web/e2e/admin-role-access.spec.ts))  

**Target Database Tables:** `integrations` (`webhook_secret_hash`, `api_credentials_vault_ref` encrypted via AES-256-GCM), `tenants`, `users`  
**Target API Endpoints:**  
- `POST /api/v1/integrations/github/connect`  
- `POST /api/v1/integrations/jira/connect`  
- `POST /api/v1/integrations/slack/connect`  
- `GET /api/v1/integrations`  
- `GET /api/v1/integrations/{integration_id}`  
- `DELETE /api/v1/integrations/{integration_id}`  

**Automated Pytest Suites:**  
- [`backend/tests/test_integration_scenarios.py`](../backend/tests/test_integration_scenarios.py) (Dedicated SCRUM-324 scenario verification: invalid/expired tokens, Developer 403, all 6 non-admin roles 403, secret masking, tenant isolation)  
- [`backend/tests/test_integration_connect.py`](../backend/tests/test_integration_connect.py) (Credential validators, webhook URL generation, round-trip HMAC delivery, AES-256-GCM encryption)  

**Automated Playwright E2E Suites:**  
- [`web/e2e/integration-role-access.spec.ts`](../web/e2e/integration-role-access.spec.ts) (UI navigation visibility, Developer/non-admin route redirection, Tenant Admin authorized access)  
- [`web/e2e/rbac-permissions.spec.ts`](../web/e2e/rbac-permissions.spec.ts) (`MANAGE_WEBHOOK_INTEGRATIONS` matrix logic & `<Can do="..." />` gating)  
- [`web/e2e/admin-role-access.spec.ts`](../web/e2e/admin-role-access.spec.ts) (Admin navigation suppression and URL guards)  

---

## 1. Objective & Architecture Invariants

The M.A.N.T.A. platform integrates with third-party software engineering services (**GitHub**, **Atlassian Jira**, and **Slack**) to ingest webhooks, track DORA metrics, monitor pull requests, and detect scope creep. Connecting and managing integrations is a high-privilege operational capability governed by six core architectural invariants:

### 1.1 Strict Server-Side RBAC Enforcement (`MANAGE_WEBHOOK_INTEGRATIONS`)
- **Authoritative Gate:** All six integration endpoints enforce `require_permission(Permission.MANAGE_WEBHOOK_INTEGRATIONS)` via FastAPI dependency injection.
- **Allowed Roles:** Only the `TENANT_ADMIN` role possesses `ALLOW` for this permission in the authoritative 7-role matrix ([`docs/roles-permissions.md`](roles-permissions.md)).
- **Developer & Non-Admin Denial:** Requests from users with the `DEVELOPER` role (or any of the other five non-admin roles: `SCRUM_MASTER`, `PROJECT_MANAGER`, `SOLUTION_ARCHITECT`, `DELIVERY_DIRECTOR`, `TOP_MANAGEMENT`) are immediately halted at the FastAPI route boundary with **`HTTP 403 Forbidden`**:
  ```json
  {
    "detail": "Forbidden: insufficient permissions"
  }
  ```
- **Unauthenticated Denial:** Requests lacking a valid Bearer token return **`HTTP 401 Unauthorized`** (`{"detail": "Not authenticated"}`).

### 1.2 Live Upstream Credential Pre-Verification Before Persistence
- **Zero-Trust Validation:** The platform never blindly stores API tokens or webhook secrets. Before any database write, the backend executes a live round-trip authentication check against the upstream provider:
  - **GitHub:** `GET https://api.github.com/user` with `Authorization: Bearer <token>`.
  - **Jira:** `GET {base_url}/rest/api/3/myself` with HTTP Basic Auth (`base64(email:api_token)`), with automatic fallback to `/rest/api/2/myself` for Jira Server / Data Center.
  - **Slack:** `POST https://slack.com/api/auth.test` with `Authorization: Bearer <bot_token>`.
- **Zero-Persistence Guarantee:** If the upstream provider rejects the credentials (invalid token, malformed format, expired token, revoked token, or insufficient scope), the operation raises an `InvalidCredentialsError`, returning **`HTTP 400 Bad Request`**. **Zero rows** are created or updated in PostgreSQL.

### 1.3 Cryptographic Encryption at Rest (AES-256-GCM)
- Under `SCRUM-321` and Architecture Blueprint §6.4, third-party API tokens and webhook secrets are never stored in plaintext.
- All secrets are encrypted via authenticated AES-256-GCM using a 256-bit master key before storage in:
  - `integrations.webhook_secret_hash` (`enc:v1:<iv>:<ciphertext>:<tag>`)
  - `integrations.api_credentials_vault_ref` (`enc:v1:<iv>:<ciphertext>:<tag>`)

### 1.4 Secret Masking & Write-Only Invariant After Save
- **Initial Connection:** Upon successful connection (`POST .../connect`), the API returns **`HTTP 201 Created`** including the plaintext `webhook_secret` so the administrator can configure it on the provider's webhook settings.
- **Subsequent Queries:** In all subsequent list and retrieval operations (`GET /api/v1/integrations`, `GET /api/v1/integrations/{id}`):
  - Third-party API credentials (`access_token`, `api_token`, `bot_token`) are **strictly write-only** and are completely excluded from response schemas.
  - The webhook secret is masked (`webhook_secret_masked`), exposing only a secure prefix/suffix or asterisk mask (`whsec_...` or `********`), preventing shoulder surfing and credential leakage in browser memory.

### 1.5 Multi-Tenant Isolation & Information Hiding (PostgreSQL RLS)
- All rows in `integrations` are isolated by `tenant_id` under PostgreSQL `FORCE ROW LEVEL SECURITY`.
- **List Isolation:** When an administrator lists integrations (`GET /api/v1/integrations`), only integrations belonging to their authenticated tenant (`app.current_tenant_id`) are returned. Another tenant's integrations are completely invisible (`0 rows`).
- **Information Hiding (404 over 403):** If Tenant B attempts to read (`GET /api/v1/integrations/{tenant_a_id}`) or delete (`DELETE /api/v1/integrations/{tenant_a_id}`) Tenant A's integration by ID, the system responds with **`HTTP 404 Not Found`** (`{"detail": "Integration '{id}' not found"}`). The API deliberately avoids returning `HTTP 403`, preventing attackers from enumerating or confirming the existence of integration IDs across tenant boundaries.

### 1.6 UI Visibility & Deny-by-Default Policy
- **DOM Suppression:** Navigation links (`nav-link-integrations`) and the `ADMINISTRATION` sidebar header are conditioned on `can("MANAGE_WEBHOOK_INTEGRATIONS")` using the `<Can do="...">` component. For non-admin roles (`DEVELOPER`, etc.), these controls are **completely removed from the DOM**, not merely disabled.
- **Route Interception:** Direct browser navigation to `/integrations` by unauthorized roles is caught by `PortalAuthGuard` in `web/src/app/(portal)/layout.tsx` and immediately redirected to `/`.

---

## 2. Test Personas, Fixtures & Target Providers

### 2.1 Keycloak Test Realm Personas (`infra/keycloak/realm-export.json`)

The test scenarios evaluate role permissions across canonical personas in test tenants (`Tenant Alpha` and `Tenant Beta`):

| Username | Canonical Role | Realm Roles Assigned | Tenant Ownership | Intended Platform Perspective |
| :--- | :--- | :--- | :--- | :--- |
| `alpha_dev` | `DEVELOPER` | `["DEVELOPER"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | Software engineer writing code, viewing personal PRs, triggering refactor analysis. **Must be denied integration management.** |
| `alpha_scrum` | `SCRUM_MASTER` | `["SCRUM_MASTER"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | Agile facilitator managing sprint velocity and WIP limits. **Denied integration management.** |
| `alpha_pm` | `PROJECT_MANAGER` | `["PROJECT_MANAGER"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | Project delivery tracking and scope creep alerts. **Denied integration management.** |
| `alpha_arch` | `SOLUTION_ARCHITECT` | `["SOLUTION_ARCHITECT"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | System architecture, SCIP call graphs, global ADR sign-off. **Denied integration management.** |
| `alpha_director`| `DELIVERY_DIRECTOR` | `["DELIVERY_DIRECTOR"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | Portfolio health, DORA benchmarks, cross-project risk tracking. **Denied integration management.** |
| `alpha_top` | `TOP_MANAGEMENT` | `["TOP_MANAGEMENT"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | Executive read-only oversight across enterprise portfolios. **Denied integration management.** |
| `alpha_admin` | `TENANT_ADMIN` | `["TENANT_ADMIN", "SOLUTION_ARCHITECT"]` | `Tenant Alpha` (`11111111-1111-1111-1111-111111111111`) | Tenant Administrator. **Authorized to manage all tenant integrations.** |
| `beta_admin` | `TENANT_ADMIN` | `["TENANT_ADMIN"]` | `Tenant Beta` (`22222222-2222-2222-2222-222222222222`) | Tenant Administrator of isolated Tenant Beta. **Must never see Tenant Alpha integrations.** |

### 2.2 Supported Integration Providers

| Provider Identifier | Display Name | Authentication Method | Upstream Verification Endpoint | Secret Management |
| :--- | :--- | :--- | :--- | :--- |
| `github` | GitHub | Personal Access Token (PAT) or OAuth token (`ghp_...` / `gho_...`) | `GET https://api.github.com/user` | Auto-generated or user-provided 32-byte hex secret |
| `jira` | Atlassian Jira | Email + Atlassian API Token | `GET {base_url}/rest/api/3/myself` (or `/rest/api/2/myself`) | User-supplied or auto-generated webhook secret |
| `slack` | Slack | Bot User OAuth Token (`xoxb-...`) | `POST https://slack.com/api/auth.test` | User-supplied Slack Signing Secret |

---

## 3. Master Test Scenarios Matrix

Below is the definitive scenario matrix covering the six mandatory scope areas specified in `SCRUM-324`:

| Scenario ID | Test Scenario Case | Target Provider / Resource | Actor & Active Role | Request Construction | Expected Status Code | Expected API / DB / UI Outcome | Automated Test Link |
| :--- | :--- | :---: | :---: | :--- | :---: | :--- | :--- |
| **`TC-INT-001A`** | **Invalid Provider Token (GitHub)** | GitHub | `TENANT_ADMIN` | `POST /api/v1/integrations/github/connect`<br>Payload: `{"access_token": "ghp_invalid_token_999"}` | **`400 Bad Request`** | **API:** `{"detail": "GitHub credential validation failed: Bad credentials"}`.<br>**DB:** `0 rows` created in `integrations`.<br>**UI:** Inline error alert; form retains input; status remains Disconnected. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_invalid_provider_token_github` |
| **`TC-INT-001B`** | **Invalid Provider Token (Jira)** | Jira | `TENANT_ADMIN` | `POST /api/v1/integrations/jira/connect`<br>Payload: `{"base_url": "https://manta.atlassian.net", "email": "bad@manta.io", "api_token": "bad_token"}` | **`400 Bad Request`** | **API:** `{"detail": "Jira credential validation failed: invalid email or API token"}`.<br>**DB:** `0 rows` persisted.<br>**UI:** Error banner displayed; no integration registered. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_invalid_provider_token_jira` |
| **`TC-INT-001C`** | **Invalid Provider Token (Slack)** | Slack | `TENANT_ADMIN` | `POST /api/v1/integrations/slack/connect`<br>Payload: `{"bot_token": "xoxb-invalid-token", "signing_secret": "sec"}` | **`400 Bad Request`** | **API:** `{"detail": "Slack credential validation failed: invalid_auth"}`.<br>**DB:** `0 rows` persisted.<br>**UI:** Form error message shown; connection aborted. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_invalid_provider_token_slack` |
| **`TC-INT-002A`** | **Expired Provider Token (GitHub)** | GitHub | `TENANT_ADMIN` | `POST /api/v1/integrations/github/connect`<br>Payload with token expired on GitHub. | **`400 Bad Request`** | **API:** `{"detail": "GitHub credential validation failed: The token has expired"}`.<br>**DB:** `0 rows` persisted.<br>**UI:** Expiration warning alert displayed. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_expired_provider_token_github` |
| **`TC-INT-002B`** | **Expired Provider Token (Slack)** | Slack | `TENANT_ADMIN` | `POST /api/v1/integrations/slack/connect`<br>Payload with revoked/expired Slack token. | **`400 Bad Request`** | **API:** `{"detail": "Slack credential validation failed: token_expired"}`.<br>**DB:** `0 rows` persisted.<br>**UI:** Prompt to regenerate bot token in Slack API console. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_expired_provider_token_slack` |
| **`TC-INT-002C`** | **Expired Provider Token (Jira)** | Jira | `TENANT_ADMIN` | `POST /api/v1/integrations/jira/connect`<br>Payload with expired Jira Atlassian token. | **`400 Bad Request`** | **API:** `{"detail": "Jira credential validation failed: token expired or credentials revoked"}`.<br>**DB:** `0 rows` persisted.<br>**UI:** Error message advises regenerating Atlassian API token. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_expired_provider_token_jira` |
| **`TC-INT-003`** | **Expired Platform Session Token** | MANTA API | Any Role | `GET /api/v1/integrations`<br>Header: `Authorization: Bearer <expired_manta_jwt>` (`exp` in past). | **`401 Unauthorized`** | **API:** `{"detail": "Token expired"}` or `{"detail": "Not authenticated"}`.<br>**UI:** Intercepted by frontend auth layer; triggers silent renewal or redirects to `/login`. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_expired_user_jwt_returns_401` |
| **`TC-INT-004A`** | **Administrator Connects (GitHub)** | GitHub | `TENANT_ADMIN` | `POST /api/v1/integrations/github/connect`<br>Valid PAT token. | **`201 Created`** | **API:** Returns `status: "connected"`, unique `webhook_url` (`/api/v1/webhooks/integrations/{uuid}`), 64-char `webhook_secret`.<br>**DB:** `1 row` saved; credentials AES-256-GCM encrypted (`enc:v1:`).<br>**UI:** "Connected" green badge; copy buttons for URL & secret. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_administrator_connects_successfully_all_providers` |
| **`TC-INT-004B`** | **Administrator Connects (Jira)** | Jira | `TENANT_ADMIN` | `POST /api/v1/integrations/jira/connect`<br>Valid Jira URL, email & API token. | **`201 Created`** | **API:** Returns `status: "connected"`, unique `webhook_url`.<br>**DB:** `1 row` saved with encrypted Atlassian credentials.<br>**UI:** Jira connector marked active. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_administrator_connects_successfully_all_providers` |
| **`TC-INT-004C`** | **Administrator Connects (Slack)** | Slack | `TENANT_ADMIN` | `POST /api/v1/integrations/slack/connect`<br>Valid Slack bot token & signing secret. | **`201 Created`** | **API:** Returns `status: "connected"`, unique `webhook_url`.<br>**DB:** `1 row` saved with encrypted Slack credentials.<br>**UI:** Slack connector marked active. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_administrator_connects_successfully_all_providers` |
| **`TC-INT-005`** | **Developer Role Receives HTTP 403** | All Endpoints | `DEVELOPER` | Direct invocation of all 6 integration routes (`POST /connect`, `GET /integrations`, `GET /{id}`, `DELETE /{id}`) with Developer JWT. | **`403 Forbidden`** | **API:** Every endpoint returns `{"detail": "Forbidden: insufficient permissions"}`.<br>**UI:** `ADMINISTRATION` header and `nav-link-integrations` hidden from DOM; direct navigation to `/integrations` redirects to `/`. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_developer_role_receives_403_on_all_endpoints`<br>`integration-role-access.spec.ts` $\rightarrow$ `Developer role` |
| **`TC-INT-006`** | **Full Role Restrictions Coverage** | All Endpoints | `SCRUM_MASTER`<br>`PROJECT_MANAGER`<br>`SOLUTION_ARCHITECT`<br>`DELIVERY_DIRECTOR`<br>`TOP_MANAGEMENT` | Direct invocation of `GET /api/v1/integrations` with tokens for each non-admin role. | **`403 Forbidden`** | **API:** All 6 non-admin roles strictly denied.<br>**UI:** Navigation links hidden for all non-admin roles; direct URL navigation redirects to `/`. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_all_non_admin_roles_receive_403`<br>`integration-role-access.spec.ts` $\rightarrow$ `Role restriction` |
| **`TC-INT-007`** | **Secrets Masked After Save** | All Integrations | `TENANT_ADMIN` | `GET /api/v1/integrations`<br>`GET /api/v1/integrations/{id}` | **`200 OK`** | **API:** `webhook_secret_masked` returns masked string (`whsec_...` or asterisks); API tokens (`access_token`, `api_token`, `bot_token`) are completely absent from payload.<br>**DB:** Ciphertext stored with `enc:v1:` authenticated tag.<br>**UI:** Secrets masked in table with safety reveal/copy toggles. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_secrets_masked_after_save` |
| **`TC-INT-008A`** | **Two Tenants Cannot See Integrations** | Multi-Tenant | `Tenant Alpha Admin`<br>`Tenant Beta Admin` | Tenant Alpha connects GitHub; Tenant Beta connects Slack. Both list integrations (`GET /api/v1/integrations`). | **`200 OK`** | **API:** Tenant Alpha sees only GitHub integration (`count = 1`). Tenant Beta sees only Slack integration (`count = 1`). Neither sees the other's records.<br>**DB:** RLS filters by `app.current_tenant_id`.<br>**UI:** Each tenant's portal only renders their own connectors. | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_two_tenants_cannot_see_each_others_integrations` |
| **`TC-INT-008B`** | **Two Tenants Cannot Access/Delete** | Multi-Tenant | `Tenant Beta Admin` targeting Tenant Alpha ID | `GET /api/v1/integrations/{alpha_id}`<br>`DELETE /api/v1/integrations/{alpha_id}` as Tenant Beta. | **`404 Not Found`** | **API:** Returns `HTTP 404 Not Found` (`{"detail": "Integration '{alpha_id}' not found"}`). Prevents cross-tenant existence enumeration.<br>**DB:** Tenant Alpha record remains intact and unmodified.<br>**UI:** Error notification "Integration not found". | `test_integration_scenarios.py` $\rightarrow$ `test_scrum_324_two_tenants_cannot_see_each_others_integrations` |

---

## 4. Detailed Scenario Specifications

### 4.1 Scenario 1: Invalid Provider Token (`TC-INT-001A`, `TC-INT-001B`, `TC-INT-001C`)

- **Objective:** Verify that third-party credentials failing upstream authentication are immediately rejected with `HTTP 400 Bad Request` without storing any data in the database.
- **Preconditions:**
  - Tenant Administrator is authenticated (`alpha_admin`).
  - Third-party provider API (GitHub, Jira, or Slack) is reachable.
- **Execution Flow:**
  ```text
  Client (Admin)                FastAPI Gateway              Provider API
       |                              |                            |
       |-- POST /connect (bad token) ->|                            |
       |                              |-- GET /user (Bearer token)->|
       |                              |<-- 401 Bad credentials -----|
       |                              |                            |
       |                              |-- [Abort Transaction]      |
       |                              |   (0 rows persisted)       |
       |<-- 400 Bad Request ----------|                            |
  ```
- **Assertions:**
  1. HTTP response status is **`400 Bad Request`**.
  2. JSON body contains descriptive error detail (e.g. `{"detail": "GitHub credential validation failed: Bad credentials"}`).
  3. Database query `SELECT count(*) FROM integrations WHERE tenant_id = :tenant_id` returns `0`.
  4. In the UI, form inputs are retained, an inline error banner is rendered with the failure message, and the connection state remains "Disconnected".

### 4.2 Scenario 2: Expired Provider Token & Expired Platform JWT (`TC-INT-002`, `TC-INT-003`)

- **Objective:**
  - Verify that expired or revoked upstream provider tokens produce `HTTP 400 Bad Request` during connection attempts.
  - Verify that expired MANTA user session tokens produce `HTTP 401 Unauthorized`.
- **Provider Expiration Cases:**
  - **GitHub (`TC-INT-002A`):** Upstream returns `401 Unauthorized` with `{"message": "The token has expired"}`. MANTA catches this and returns `HTTP 400` with `"GitHub credential validation failed: The token has expired"`.
  - **Slack (`TC-INT-002B`):** Upstream returns `200 OK` with `{"ok": false, "error": "token_expired"}`. MANTA returns `HTTP 400` with `"Slack credential validation failed: token_expired"`.
  - **Jira (`TC-INT-002C`):** Upstream returns `401/403` with token expired message. MANTA returns `HTTP 400`.
- **Platform JWT Expiration (`TC-INT-003`):**
  - When the administrator's MANTA access token has passed its expiration boundary (`exp < current_time`), `JWTAuthMiddleware` rejects the request before hitting route dependencies.
  - HTTP status is **`401 Unauthorized`** (`{"detail": "Token expired"}`).
  - In the web portal, the OIDC client (`oidc-client-ts`) attempts silent renewal via the hidden iframe; if renewal fails, the user is redirected to `/login?redirect=%2Fintegrations`.

### 4.3 Scenario 3: Administrator Connects Successfully (`TC-INT-004A`, `TC-INT-004B`, `TC-INT-004C`)

- **Objective:** Verify nominal third-party connection lifecycle for an authorized administrator.
- **Preconditions:**
  - User has `TENANT_ADMIN` role (`alpha_admin`).
  - Provider credentials are valid.
- **Execution Steps:**
  1. Administrator submits credentials to `POST /api/v1/integrations/{provider}/connect`.
  2. Backend performs live verification against provider API (`200 OK` received).
  3. Backend generates:
     - Cryptographically unguessable UUIDv4 integration ID (`id`).
     - Unique webhook URL: `https://api.manta.io/api/v1/webhooks/integrations/{id}`.
     - 32-byte (64 hex character) cryptographic webhook secret (if not explicitly supplied).
  4. Backend encrypts secrets with AES-256-GCM authenticated cipher (`enc:v1:...`).
  5. Backend persists row to `integrations` under current tenant context.
- **Assertions:**
  1. HTTP status code is **`201 Created`**.
  2. Response payload matches schema:
     ```json
     {
       "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
       "tenant_id": "11111111-1111-1111-1111-111111111111",
       "provider": "github",
       "external_account_id": "octocat-lead",
       "status": "connected",
       "webhook_url": "http://test/api/v1/webhooks/integrations/3fa85f64-5717-4562-b3fc-2c963f66afa6",
       "webhook_secret": "a1b2c3d4e5f6...",
       "is_active": true,
       "created_at": "2026-11-05T12:00:00Z",
       "updated_at": "2026-11-05T12:00:00Z"
     }
     ```
  3. In PostgreSQL:
     - `webhook_secret_hash` starts with `enc:v1:`.
     - `api_credentials_vault_ref` starts with `enc:v1:`.
     - Plaintext tokens are not stored in any table or log file.
  4. In the UI:
     - Provider card transitions from "Disconnected" to "Connected" with green badge.
     - Webhook URL input is populated with copy-to-clipboard action button.
     - Webhook secret is displayed once with a prompt to record it.

### 4.4 Scenario 4: Developer Role Receives HTTP 403 & Full Role Restrictions (`TC-INT-005`, `TC-INT-006`)

- **Objective:** Verify that `DEVELOPER` and all non-admin roles are strictly prohibited from connecting, viewing, or deleting integrations.
- **Matrix Gate:** `Permission.MANAGE_WEBHOOK_INTEGRATIONS` ($\text{Decision} = \text{DENY}$ for all roles except `TENANT_ADMIN`).
- **Target Endpoints Evaluated:**
  - `POST /api/v1/integrations/github/connect` $\rightarrow$ `HTTP 403`
  - `POST /api/v1/integrations/jira/connect` $\rightarrow$ `HTTP 403`
  - `POST /api/v1/integrations/slack/connect` $\rightarrow$ `HTTP 403`
  - `GET /api/v1/integrations` $\rightarrow$ `HTTP 403`
  - `GET /api/v1/integrations/{id}` $\rightarrow$ `HTTP 403`
  - `DELETE /api/v1/integrations/{id}` $\rightarrow$ `HTTP 403`
- **Role Coverage:**
  - `DEVELOPER` $\rightarrow$ `HTTP 403`
  - `SCRUM_MASTER` $\rightarrow$ `HTTP 403`
  - `PROJECT_MANAGER` $\rightarrow$ `HTTP 403`
  - `SOLUTION_ARCHITECT` $\rightarrow$ `HTTP 403`
  - `DELIVERY_DIRECTOR` (including alias `DELIVERY_EXEC`) $\rightarrow$ `HTTP 403`
  - `TOP_MANAGEMENT` $\rightarrow$ `HTTP 403`
- **UI Behavior:**
  - Sidebar: `ADMINISTRATION` header and `nav-link-integrations` are hidden via `can("MANAGE_WEBHOOK_INTEGRATIONS")`.
  - Deep-link / Direct Navigation: Navigating to `https://portal.manta.io/integrations` is intercepted by `PortalAuthGuard` and redirected to `/` (home dashboard).

### 4.5 Scenario 5: Secrets Masked After Save (`TC-INT-007`)

- **Objective:** Verify that saved credentials and webhook secrets cannot be read in plaintext through subsequent read APIs.
- **Assertions:**
  1. Calling `GET /api/v1/integrations` or `GET /api/v1/integrations/{id}` returns:
     ```json
     {
       "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
       "tenant_id": "11111111-1111-1111-1111-111111111111",
       "provider": "github",
       "external_account_id": "octocat-lead",
       "status": "connected",
       "webhook_url": "http://test/api/v1/webhooks/integrations/3fa85f64-5717-4562-b3fc-2c963f66afa6",
       "webhook_secret_masked": "whsec_••••••••••••••••••••••••••••••••••••••••••••••••••",
       "is_active": true,
       "created_at": "2026-11-05T12:00:00Z",
       "updated_at": "2026-11-05T12:00:00Z"
     }
     ```
  2. The raw plaintext webhook secret is not present in `webhook_secret_masked`.
  3. Upstream API tokens (`access_token`, `api_token`, `bot_token`) are completely omitted from the schema.
  4. In the UI: Secret fields display obscured bullet/asterisk characters with safe clipboard copy controls.

### 4.6 Scenario 6: Two Tenants Cannot See Each Other's Integrations (`TC-INT-008A`, `TC-INT-008B`)

- **Objective:** Verify that multi-tenant database isolation (RLS) and API routing prevent cross-tenant data access, discovery, or modification.
- **Scenario Flow:**
  1. Tenant Alpha Administrator connects a GitHub integration (`id = alpha_id`).
  2. Tenant Beta Administrator connects a Slack integration (`id = beta_id`).
  3. Tenant Alpha queries `GET /api/v1/integrations` $\rightarrow$ receives list with only `alpha_id` (`len == 1`).
  4. Tenant Beta queries `GET /api/v1/integrations` $\rightarrow$ receives list with only `beta_id` (`len == 1`). `alpha_id` is completely omitted.
  5. Tenant Beta attempts cross-tenant read: `GET /api/v1/integrations/{alpha_id}`.
     - Backend evaluates query within Tenant Beta's session context (`app.current_tenant_id = :tenant_beta`).
     - PostgreSQL RLS filters out Tenant Alpha's row.
     - Backend raises `HTTPException(status_code=404, detail="Integration '{alpha_id}' not found")`.
  6. Tenant Beta attempts cross-tenant delete: `DELETE /api/v1/integrations/{alpha_id}`.
     - PostgreSQL RLS prevents deletion (`0 rows affected`).
     - Backend raises `HTTPException(status_code=404, detail="Integration '{alpha_id}' not found")`.
  7. Verification: Tenant Alpha's integration remains intact, active, and unmodified.

---

## 5. UI Behavior & Feedback State Machine

### 5.1 Administrator UI States & Feedback

```mermaid
stateDiagram-v2
    [*] --> FormIdle: Navigate to /integrations
    FormIdle --> Submitting: Fill credentials & Submit
    Submitting --> ErrorBanner: Upstream error / Invalid token / Expired token (HTTP 400)
    ErrorBanner --> FormIdle: Correct credentials & Retry
    Submitting --> SuccessView: Valid credentials (HTTP 201)
    SuccessView --> FormIdle: Disconnect integration (HTTP 204)
```

1. **Idle State (`/integrations`):**
   - Active connectors table shows configured integrations with provider icons, external account handles, masked secrets, and unique webhook URLs.
   - Provider setup forms allow entering tokens and optional custom webhook secrets.
2. **Success State (HTTP 201):**
   - Green toast alert: *"Integration connected successfully."*
   - Status badge transitions to **Connected** (emerald badge).
   - Unique unguessable webhook URL rendered with one-click **Copy URL** button.
   - Webhook secret presented with safety notice: *"Save this secret now. It will not be shown in plaintext again."*
3. **Error State (HTTP 400):**
   - Red inline alert banner rendered above form: *"Credential validation failed: <detail>"*.
   - Form inputs are preserved so the administrator does not have to retype all fields.
   - Integration status remains **Disconnected**.

### 5.2 Developer & Non-Admin UI Suppression

1. **Sidebar Navigation Filter:**
   - Navigation component evaluates `can("MANAGE_WEBHOOK_INTEGRATIONS")`.
   - For `DEVELOPER`, `SCRUM_MASTER`, `PROJECT_MANAGER`, `SOLUTION_ARCHITECT`, `DELIVERY_DIRECTOR`, and `TOP_MANAGEMENT`, the `nav-link-integrations` link and `ADMINISTRATION` header are entirely omitted from DOM rendering.
2. **Direct Address Bar Navigation (`/integrations`):**
   - `PortalAuthGuard` in `web/src/app/(portal)/layout.tsx` checks route authorization:
     ```typescript
     if (route === "/integrations" || route.startsWith("/integrations/")) {
       return can("MANAGE_WEBHOOK_INTEGRATIONS");
     }
     ```
   - When unauthorized, the router triggers `router.replace("/")`. The user never sees the page content and lands safely on the platform overview.

---

## 6. Traceability & Automated Test Suites

### 6.1 Pytest Execution Commands (Backend)

```bash
# Run all dedicated SCRUM-324 integration failure and permission scenarios
python -m pytest backend/tests/test_integration_scenarios.py -v

# Run the core integration connect & webhook generation suite
python -m pytest backend/tests/test_integration_connect.py -v

# Run with custom local database port (if 5432 has host conflicts)
$env:DATABASE_URL="postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5433/manta_db"
python -m pytest backend/tests/test_integration_scenarios.py -v
```

### 6.2 Playwright E2E Execution Commands (Frontend)

```bash
# Run integration role access and UI behavior E2E tests
cd web
npx playwright test e2e/integration-role-access.spec.ts

# Run RBAC permissions helper and UI gating tests
npx playwright test e2e/rbac-permissions.spec.ts
```

### 6.3 Test Verification Summary

| Ticket | Scenario Category | Automated Test Identifier | Result |
| :---: | :--- | :--- | :---: |
| `SCRUM-324` | Invalid Provider Token (GitHub) | `test_scrum_324_invalid_provider_token_github` | **PASS** |
| `SCRUM-324` | Invalid Provider Token (Jira) | `test_scrum_324_invalid_provider_token_jira` | **PASS** |
| `SCRUM-324` | Invalid Provider Token (Slack) | `test_scrum_324_invalid_provider_token_slack` | **PASS** |
| `SCRUM-324` | Expired Provider Token (GitHub) | `test_scrum_324_expired_provider_token_github` | **PASS** |
| `SCRUM-324` | Expired Provider Token (Slack) | `test_scrum_324_expired_provider_token_slack` | **PASS** |
| `SCRUM-324` | Expired Provider Token (Jira) | `test_scrum_324_expired_provider_token_jira` | **PASS** |
| `SCRUM-324` | Expired Platform Session JWT | `test_scrum_324_expired_user_jwt_returns_401` | **PASS** |
| `SCRUM-324` | Administrator Connects Successfully | `test_scrum_324_administrator_connects_successfully_all_providers` | **PASS** |
| `SCRUM-324` | Developer Receives HTTP 403 | `test_scrum_324_developer_role_receives_403_on_all_endpoints` | **PASS** |
| `SCRUM-324` | Full Role Restrictions Coverage | `test_scrum_324_all_non_admin_roles_receive_403` (7 roles) | **PASS** |
| `SCRUM-324` | Secrets Masked After Save | `test_scrum_324_secrets_masked_after_save` | **PASS** |
| `SCRUM-324` | Multi-Tenant RLS & 404 Isolation | `test_scrum_324_two_tenants_cannot_see_each_others_integrations` | **PASS** |
| `SCRUM-324` | UI Gating & Redirection E2E | `integration-role-access.spec.ts` | **PASS** |
