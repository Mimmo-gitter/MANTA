# Adversarial Tenant Boundary Test Scenarios

**Task:** Design adversarial test scenarios for tenant boundaries (`SCRUM-301`)  
**Parent User Story:** `SCRUM-246` (`US-10` Tenant boundary security tests)  
**Related Tickets:**  
- `SCRUM-244` / `SCRUM-294` / `SCRUM-295` (US-8: Database-enforced tenant isolation via PostgreSQL RLS)  
- `SCRUM-245` / `SCRUM-296` / `SCRUM-299` (US-9: PgBouncer transaction pooling tenant state cleanup)  
- `SCRUM-240` / `SCRUM-277` / `SCRUM-278` (US-4 & US-5: RS256 JWT validation, JWKS refresh & HTTP 401 on failure)  
- `SCRUM-248` / `SCRUM-322` / `SCRUM-323` / `SCRUM-324` (US-12 & US-13: Tenant integration connection, ID-isolated management & test scenarios, see [`docs/integration-test-scenarios.md`](integration-test-scenarios.md))  
- `SCRUM-292` (`eng_app_user` least-privilege role), `SCRUM-318` (`FORCE ROW LEVEL SECURITY`), `SCRUM-293` (Automated RLS schema guardrails)  
- `SCRUM-305` (Negative RLS safety net verification: pipeline fails when RLS policy is disabled, see [`docs/negative-rls-security-test.md`](negative-rls-security-test.md))  
**Target Architecture Layers:**  
1. **API Layer:** FastAPI, Starlette `JWTAuthMiddleware`, Pydantic claim validators, route handlers, and dependency injection (`get_current_tenant_id`, `require_permission`).  
2. **Database Layer:** PostgreSQL 16 (`pgvector/pgvector:pg16`), Row-Level Security (`tenant_isolation_policy`), least-privilege runtime role (`eng_app_user`), and PgBouncer connection pool reset (`DISCARD ALL`).  
**Automated Pytest Suite:** [`backend/tests/test_tenant_boundary_adversarial.py`](../backend/tests/test_tenant_boundary_adversarial.py)  
**Supporting Pytest Suites:**  
- [`backend/tests/security/test_cross_tenant_read_write.py`](../backend/tests/security/test_cross_tenant_read_write.py)  
- [`backend/tests/security/test_forged_tenant_claim_and_session_without_tenant_context.py`](../backend/tests/security/test_forged_tenant_claim_and_session_without_tenant_context.py)  
- [`backend/tests/security/test_rls_negative_pipeline.py`](../backend/tests/security/test_rls_negative_pipeline.py)  
- [`backend/tests/test_rls_isolation.py`](../backend/tests/test_rls_isolation.py)  
- [`backend/tests/test_jwt_claims_validation.py`](../backend/tests/test_jwt_claims_validation.py)  
- [`backend/tests/test_integration_connect.py`](../backend/tests/test_integration_connect.py)  
- [`backend/tests/test_session.py`](../backend/tests/test_session.py)  
- [`backend/tests/test_rls_guardrails.py`](../backend/tests/test_rls_guardrails.py)  
- [`backend/tests/test_pgbouncer_isolation.py`](../backend/tests/test_pgbouncer_isolation.py)  

---

## 1. Objective & Adversarial Threat Model

### 1.1 Objective
Think like an attacker and systematically design, execute, and verify adversarial test scenarios proving that **no tenant can reach, view, mutate, tamper with, or delete another tenant's data** across any layer of the MANTA platform.

Every scenario explicitly defines:
1. The **attack vector** and adversarial technique.
2. The **stopping layer** (**API** or **Database**), along with defense-in-depth interactions.
3. The **expected result** (**`403 Forbidden`**, **`404 Not Found`**, **`401 Unauthorized`**, or **`zero rows`**).
4. Exactly **where it is tested** in the automated test suite.

### 1.2 Adversarial Personas & Threat Vectors
An attacker against tenant boundaries may operate from four distinct threat positions:

```
+-------------------------------------------------------------------------------------------------------+
|                                      ADVERSARIAL ATTACK SURFACES                                       |
+------------------------------------+-----------------------------------+------------------------------+
| 1. Authenticated Rogue Tenant      | 2. External / Unauthenticated     | 3. Compromised Internal Role |
|    (Tenant Beta -> Tenant Alpha)   |    (Forged Claims & Spoofing)     |    (Direct eng_app_user SQL)  |
+------------------------------------+-----------------------------------+------------------------------+
| * Target IDs via REST APIs         | * Self-signed JWTs (rogue RSA)    | * Raw queries without context|
| * Cross-tenant UPDATE / DELETE     | * Tampered JWT payload claims     | * Attempt SET row_security=OFF|
| * Injected tenant_id in payload    | * Spoofed X-Tenant-ID headers     | * Direct INSERT into tenants |
| * HTTP keep-alive session reuse    | * Omitted / nil tenant claims     | * DDL / table drop attempts  |
+------------------------------------+-----------------------------------+------------------------------+
                                                  |
                                                  v
+-------------------------------------------------------------------------------------------------------+
|                                     DEFENSE-IN-DEPTH ENFORCEMENT                                      |
+----------------------------------------------------+--------------------------------------------------+
| API LAYER (FastAPI / Auth Middleware)              | DATABASE LAYER (PostgreSQL RLS / eng_app_user)   |
+----------------------------------------------------+--------------------------------------------------+
| * Cryptographic RS256 signature verification       | * Mandatory FORCE ROW LEVEL SECURITY on 41 tables|
| * Strict TokenClaims extraction (claims['tenant_id'])| * Fail-safe closed NULLIF(current_setting(...))|
| * Denial of unauthenticated/malformed tokens (401) | * Permissive tenant_isolation_policy (USING/CHECK|
| * Information hiding: 404 Not Found on cross-ID    | * Zero rows returned on cross-tenant reads       |
| * Deny-by-default permission checks (403)          | * Zero rows affected on cross-tenant writes      |
| * Overwrite request context per incoming request   | * SQLSTATE 42501 on cross-INSERT / reassignment  |
+----------------------------------------------------+--------------------------------------------------+
```

### 1.3 Information Leakage Prevention: Why 404 vs 403
A critical adversarial design principle in multi-tenant REST architectures is **preventing tenant enumeration and existence oracles**:
- When Tenant Beta requests an object ID owned by Tenant Alpha (e.g. `GET /api/v1/integrations/{tenant_a_id}`), returning `403 Forbidden` would confirm to the attacker that the entity exists in the system.
- Therefore, the API returns **`404 Not Found`**, rendering other tenants' entities completely indistinguishable from non-existent entities.
- **`403 Forbidden`** is reserved strictly for authorization failures on endpoints where the resource belongs to the tenant or is tenant-agnostic, but the authenticated user's role lacks the declared RBAC permission.
- **`zero rows`** is the database-level equivalent: PostgreSQL RLS silently filters out foreign tuples from `SELECT`, `UPDATE`, and `DELETE` queries.

---

## 2. Canonical Seed Data Fixtures

All adversarial test scenarios utilize deterministic seed entities provisioned across two isolated tenants:

| Entity | Tenant Ownership | Primary Key (`id`) | `tenant_id` | Slug / Email | Role / Attributes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tenant Alpha** (`tenants`) | Tenant Alpha (Victim) | `11111111-1111-1111-1111-111111111111` | *(self `id`)* | `tenant-alpha-adv` | `tier = 'enterprise'`, `ai_processing_mode = 'cloud_zdr'` |
| **Tenant Beta** (`tenants`) | Tenant Beta (Attacker) | `22222222-2222-2222-2222-222222222222` | *(self `id`)* | `tenant-beta-adv` | `tier = 'starter'`, `ai_processing_mode = 'local'` |
| **Project Alpha** (`projects`) | Tenant Alpha | `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa` | `11111111-1111-1111-1111-111111111111` | `project-alpha-secret` | Confidential project belonging to Tenant Alpha |
| **Project Beta** (`projects`) | Tenant Beta | `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb` | `22222222-2222-2222-2222-222222222222` | `project-beta-secret` | Standard project belonging to Tenant Beta |
| **Integration Alpha** (`integrations`) | Tenant Alpha | *(generated UUID)* | `11111111-1111-1111-1111-111111111111` | `gh-org-alpha` | Encrypted GitHub integration owned by Tenant Alpha |
| **Integration Beta** (`integrations`) | Tenant Beta | *(generated UUID)* | `22222222-2222-2222-2222-222222222222` | `gh-org-beta` | Encrypted GitHub integration owned by Tenant Beta |

---

## 3. Adversarial Scenario Matrix

| Scenario ID | Attack Vector & Adversarial Technique | Attacker Action & Payload / Query | Stopping Layer | Expected Result | Where It Is Tested | Acceptance Criteria & Standard Traceability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-ADV-001** | **Cross-Tenant Read by ID (API)** | Tenant Beta calls `GET /api/v1/integrations/{integ_a_id}` with valid Tenant Beta token. | **API Layer** *(Endpoint Handler & RLS)* | `404 Not Found` | `test_tenant_boundary_adversarial.py::test_adv_001_cross_tenant_read_by_id_api_and_db`<br>`test_integration_connect.py::test_integration_cross_tenant_isolation` | US-10 AC-1; Information hiding (no existence oracle); Architecture §6.3 |
| **TC-ADV-002** | **Cross-Tenant Read by ID (Database)** | Direct SQL `SELECT * FROM integrations WHERE id = :integ_a_id` executed under Tenant Beta context (`app.current_tenant_id = Tenant Beta`). | **Database Layer** *(PostgreSQL RLS `USING`)* | `zero rows` | `test_tenant_boundary_adversarial.py::test_adv_001_cross_tenant_read_by_id_api_and_db`<br>`test_rls_isolation.py::test_project_cross_tenant_read_isolation` | US-10 AC-2; PostgreSQL `tenant_isolation_policy` USING clause filtering; Architecture §6.3 |
| **TC-ADV-003** | **Cross-Tenant Write by ID (DELETE via API)** | Tenant Beta calls `DELETE /api/v1/integrations/{integ_a_id}` targeting Tenant Alpha entity. | **API Layer** *(Endpoint Handler & Service)* | `404 Not Found` | `test_tenant_boundary_adversarial.py::test_adv_002_cross_tenant_write_by_id_api_and_db`<br>`test_integration_connect.py::test_integration_cross_tenant_isolation` | US-10 AC-1; Mutation targeting unowned resource blocked with 404; Architecture §6.3 |
| **TC-ADV-004** | **Cross-Tenant Write by ID (UPDATE via Database)** | Tenant Beta executes SQL `UPDATE projects SET name = 'hacked' WHERE id = :project_a_id` under Tenant Beta context. | **Database Layer** *(PostgreSQL RLS `USING`)* | `zero rows affected` (`rowcount == 0`) | `test_tenant_boundary_adversarial.py::test_adv_002_cross_tenant_write_by_id_api_and_db`<br>`test_rls_isolation.py::test_project_cross_tenant_update_isolation` | US-10 AC-2; Foreign rows excluded from UPDATE target selection; Architecture §6.3 |
| **TC-ADV-005** | **Cross-Tenant Write by ID (DELETE via Database)** | Tenant Beta executes SQL `DELETE FROM projects WHERE id = :project_a_id` under Tenant Beta context. | **Database Layer** *(PostgreSQL RLS `USING`)* | `zero rows affected` (`rowcount == 0`) | `test_tenant_boundary_adversarial.py::test_adv_002_cross_tenant_write_by_id_api_and_db`<br>`test_rls_isolation.py::test_project_cross_tenant_delete_isolation` | US-10 AC-2; Foreign rows excluded from DELETE target selection; Architecture §6.3 |
| **TC-ADV-006** | **Cross-Tenant Write by ID (INSERT via Database)** | Tenant Beta executes SQL `INSERT INTO projects (..., tenant_id = :tenant_a_id)` under Tenant Beta context. | **Database Layer** *(PostgreSQL RLS `WITH CHECK`)* | `SQLSTATE 42501` (`DBAPIError: new row violates row-level security policy`) | `test_tenant_boundary_adversarial.py::test_adv_002_cross_tenant_write_by_id_api_and_db`<br>`test_rls_isolation.py::test_cross_tenant_insert_violates_with_check` | US-10 AC-2; WITH CHECK validation prevents cross-tenant tuple insertion; Architecture §6.3 |
| **TC-ADV-007** | **Cross-Tenant Ownership Reassignment** | Tenant Alpha attempts SQL `UPDATE projects SET tenant_id = :tenant_b_id WHERE id = :project_a_id` under Tenant Alpha context. | **Database Layer** *(PostgreSQL RLS `WITH CHECK`)* | `SQLSTATE 42501` (`DBAPIError: new row violates row-level security policy`) | `test_tenant_boundary_adversarial.py::test_adv_002_cross_tenant_write_by_id_api_and_db`<br>`test_rls_isolation.py::test_prevent_cross_tenant_reassignment` | US-10 AC-2; Prevents smuggling existing entities across tenants; Architecture §6.3 |
| **TC-ADV-008** | **Forged `tenant_id` Claim (Self-Signed JWT)** | Attacker crafts token claiming `tenant_id = Tenant Alpha`, signed with attacker's rogue RSA private key (`kid: "rogue-key"`). | **API Layer** *(Authentication Middleware & JWKS)* | `401 Unauthorized` | `test_tenant_boundary_adversarial.py::test_adv_003_forged_tenant_id_claims_api`<br>`test_token_validation_scenarios.py::test_tc_auth_006_forged_tenant_id_self_signed_token`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_token_forged_tenant_id_signed_with_rogue_rsa_key_rejected_with_401` | US-10 AC-1; Untrusted signing key and missing JWKS entry rejected; Architecture §6.1 |
| **TC-ADV-009** | **Forged `tenant_id` Claim (Tampered Payload)** | Attacker takes valid Tenant Alpha token, tampers payload claim to `"tenant_id": Tenant Beta`, keeps original signature. | **API Layer** *(Authentication Middleware)* | `401 Unauthorized` | `test_tenant_boundary_adversarial.py::test_adv_003_forged_tenant_id_claims_api`<br>`test_jwt_claims_validation.py::test_tampered_signature_returns_generic_401`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_token_altered_tenant_id_invalid_signature_rejected_with_401` | US-10 AC-1; Cryptographic signature verification mismatch; Architecture §6.1 |
| **TC-ADV-010** | **Forged `tenant_id` Claim (Header Spoofing)** | Attacker sends valid Tenant Beta token with spoofed HTTP header `X-Tenant-ID: 11111111-1111-1111-1111-111111111111`. | **API Layer** *(Context Binding)* | `404 Not Found` *(Header Ignored, token tenant enforced)* | `test_tenant_boundary_adversarial.py::test_adv_003_forged_tenant_id_claims_api`<br>`test_jwt_claims_validation.py::test_tenant_id_bound_to_request_state_and_dependency`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_forged_claims_cannot_be_rescued_by_spoofed_headers` | US-10 AC-1; API strictly derives tenant context from cryptographically verified JWT; Architecture §6.1 & §6.3 |
| **TC-ADV-011** | **Missing Tenant Context (No Token)** | Attacker calls protected API routes without `Authorization` header. | **API Layer** *(Authentication Middleware)* | `401 Unauthorized` | `test_tenant_boundary_adversarial.py::test_adv_004_missing_tenant_context_api_and_db`<br>`test_jwt_middleware.py::test_unauthenticated_request_returns_401`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_api_requests_without_authorization_return_401_no_unhandled_error` | US-10 AC-1; Deny-by-default for unauthenticated traffic; Architecture §6.1 |
| **TC-ADV-012** | **Missing Tenant Context (Claim Omitted in JWT)** | Attacker presents well-signed JWT that completely omits the mandatory `"tenant_id"` claim. | **API Layer** *(Claim Validation & Middleware)* | `401 Unauthorized` | `test_tenant_boundary_adversarial.py::test_adv_004_missing_tenant_context_api_and_db`<br>`test_jwt_claims_validation.py::test_missing_tenant_id_claim_returns_generic_401`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_token_missing_tenant_id_claim_rejected_with_401` | US-10 AC-1; Enforces mandatory multi-tenant identity claims; Architecture §6.1 & §6.3 |
| **TC-ADV-013** | **Missing Tenant Context (Empty / Whitespace / Nil UUID)** | Attacker presents token with `"tenant_id": ""` or `"00000000-0000-0000-0000-000000000000"`. | **API Layer** *(Claim Validation & Middleware)* | `401 Unauthorized` | `test_tenant_boundary_adversarial.py::test_adv_004_missing_tenant_context_api_and_db`<br>`test_jwt_claims_validation.py::test_empty_string_tenant_id_returns_generic_401`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_token_empty_or_whitespace_tenant_id_rejected_with_401`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_token_nil_uuid_tenant_id_rejected_with_401` | US-10 AC-1; Strict UUID validation rejects empty/nil identities; Architecture §6.1 |
| **TC-ADV-014** | **Missing Tenant Context (Cleared Database Session)** | Application executes queries with unset or explicitly cleared setting (`SELECT set_config('app.current_tenant_id', '', true)`). | **Database Layer** *(PostgreSQL RLS Fail-Safe Closed)* | `zero rows` (SELECT/UPDATE/DELETE)<br>`SQLSTATE 42501` (INSERT) | `test_tenant_boundary_adversarial.py::test_adv_004_missing_tenant_context_api_and_db`<br>`test_rls_isolation.py::test_cleared_tenant_setting_returns_zero_rows_and_blocks_writes`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_db_session_explicitly_cleared_setting_returns_zero_rows_without_error`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_db_session_mutations_without_tenant_context_affect_zero_rows_and_preserve_data`<br>`test_forged_tenant_claim_and_session_without_tenant_context.py::test_db_session_insert_without_tenant_context_fails_safely_with_rls_violation` | US-10 AC-2; `NULLIF(..., '')::uuid` yields NULL (evaluates to UNKNOWN); Architecture §6.3 |
| **TC-ADV-015** | **Tenant Switch Mid-Session (HTTP Keep-Alive Reuse)** | Client establishes single HTTP connection, issues request with Token Alpha, then immediately issues request with Token Beta targeting Alpha. | **API Layer** *(Per-Request Context Evaluation)* | `404 Not Found` | `test_tenant_boundary_adversarial.py::test_adv_005_tenant_switch_mid_session_api_and_db` | US-10 AC-1; Stateless request lifecycle isolates connection reuse; Architecture §6.1 |
| **TC-ADV-016** | **Tenant Switch Mid-Session (DB Context Switch)** | Application executes `set_config('app.current_tenant_id', Tenant Alpha)`, reads data, then executes `set_config(..., Tenant Beta)` on same connection. | **Database Layer** *(PostgreSQL RLS Dynamic Evaluation)* | `zero rows` for previous tenant | `test_tenant_boundary_adversarial.py::test_adv_005_tenant_switch_mid_session_api_and_db`<br>`test_rls_isolation.py::test_tenant_setting_changed_mid_session_switches_isolation_boundary` | US-10 AC-2; Dynamic RLS policy evaluation instantly updates visible row set; Architecture §6.3 |
| **TC-ADV-017** | **Tenant Switch Mid-Session (PgBouncer Pool Reuse)** | Backend database connection in PgBouncer pool polluted with Tenant Alpha state is re-used by Tenant Beta transaction. | **Database & Pool Layer** *(PgBouncer `DISCARD ALL`)* | `zero rows` leaked | `test_pgbouncer_isolation.py::test_session_state_does_not_leak_to_next_client`<br>`test_pgbouncer_load.py::test_concurrent_multi_tenant_queries_under_load` | US-10 AC-2; `server_reset_query = DISCARD ALL` purges session state; Architecture §6.3 & §6.5 |
| **TC-ADV-018** | **Direct DB Access as App Role (Unscoped Bulk SELECT)** | Attacker connects directly as `eng_app_user` and issues `SELECT * FROM <table_name>` without setting `app.current_tenant_id`. | **Database Layer** *(PostgreSQL RLS `USING`)* | `zero rows` returned across all 41 tables | `test_tenant_boundary_adversarial.py::test_adv_006_direct_db_access_app_role`<br>`test_rls_guardrails.py::test_all_tenant_id_tables_have_rls_and_force_rls` | US-10 AC-2; Least-privilege role cannot read un-scoped tables; Architecture §6.3 |
| **TC-ADV-019** | **Direct DB Access as App Role (Disable RLS via SQL)** | Attacker executes `SET row_security = off` as `eng_app_user` to bypass RLS. | **Database Layer** *(PostgreSQL Security Engine)* | RLS remains forced; returns `zero rows` or raises error | `test_tenant_boundary_adversarial.py::test_adv_006_direct_db_access_app_role`<br>`test_rls_guardrails.py::test_all_tenant_tables_have_isolation_policy_for_eng_app_user` | US-10 AC-2; `NOBYPASSRLS` and `FORCE ROW LEVEL SECURITY` ignore bypass attempts; Architecture §6.3 |
| **TC-ADV-020** | **Direct DB Access as App Role (Direct INSERT / DDL)** | Attacker executes `INSERT INTO tenants (...)` or `DROP TABLE projects` directly as `eng_app_user`. | **Database Layer** *(PostgreSQL Privileges)* | `SQLSTATE 42501` (`permission denied` / `must be owner`) | `test_tenant_boundary_adversarial.py::test_adv_006_direct_db_access_app_role` | US-10 AC-2; `INSERT` on `tenants` revoked from `eng_app_user`; DDL prohibited; Architecture §6.3 |

---

## 4. Detailed Attack Vector Specifications

### 4.1 Attack Vector 1: Cross-Tenant Read and Write by ID

#### Threat Scenario 1.1: Cross-Tenant Read by ID (`TC-ADV-001`)
- **Attacker Objective:** A compromised user or malicious administrator in Tenant Beta discovers a UUID belonging to Tenant Alpha (e.g. from a shared repository URL, public log snippet, or sequential GUID guessing) and attempts to read its confidential configuration via `GET /api/v1/integrations/{tenant_a_integration_id}`.
- **Stopping Layer:** **API Layer** (with Database RLS backstop).
- **Enforcement Mechanics:**
  1. The API endpoint resolves `user.tenant_uuid` from the cryptographically verified JWT token (`Tenant Beta`).
  2. The service layer executes a query bounded to `user.tenant_uuid`:
     ```python
     stmt = select(Integration).where(
         Integration.id == integration_id,
         Integration.tenant_id == tenant_id,
     )
     ```
  3. Because `integ.tenant_id` equals `Tenant Alpha`, the query returns `None`.
  4. The router checks `if not res:` and raises `HTTPException(status_code=404, detail="Integration '<id>' not found")`.
  5. **Information Hiding Guarantee:** Returning `404 Not Found` rather than `403 Forbidden` prevents the attacker from learning whether the UUID exists in another tenant.
- **Expected Status Code:** **`404 Not Found`**.
- **Database Backstop:** If an endpoint query omitted `Integration.tenant_id == tenant_id`, PostgreSQL RLS intercepts the query and filters out the row (`0 rows returned`), still resulting in `404 Not Found`.

#### Threat Scenario 1.2: Cross-Tenant Write by ID (`TC-ADV-002` to `TC-ADV-007`)
- **Attacker Objective:** Tenant Beta attempts to modify, hijack, or delete Tenant Alpha's resources:
  - *DELETE:* `DELETE /api/v1/integrations/{tenant_a_id}`.
  - *UPDATE:* Direct SQL `UPDATE projects SET name = 'tampered' WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'`.
  - *INSERT:* Direct SQL `INSERT INTO projects (..., tenant_id = '11111111-1111-1111-1111-111111111111')`.
  - *REASSIGNMENT:* Direct SQL `UPDATE projects SET tenant_id = '22222222-2222-2222-2222-222222222222' WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'`.
- **Stopping Layer:**
  - *API Level (DELETE):* Handled by **API Layer** $\rightarrow$ **`404 Not Found`**.
  - *Database Level (UPDATE / DELETE):* Handled by **Database Layer** $\rightarrow$ PostgreSQL `tenant_isolation_policy` `USING` clause renders Tenant Alpha's rows invisible. The mutation affects **`zero rows`** (`rowcount == 0`).
  - *Database Level (INSERT / REASSIGNMENT):* Handled by **Database Layer** $\rightarrow$ PostgreSQL `WITH CHECK` evaluates `tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid`. When `tenant_id` does not match the active session setting, PostgreSQL raises **`SQLSTATE 42501`** (`new row violates row-level security policy`).
- **Expected Results:**
  - API: **`404 Not Found`**.
  - Database UPDATE/DELETE: **`zero rows affected`**.
  - Database INSERT/Reassignment: **`SQLSTATE 42501`** (`DBAPIError`).

---

### 4.2 Attack Vector 2: Forged `tenant_id` Claim

#### Threat Scenario 2.1: Attacker Self-Signed Token (`TC-ADV-008`)
- **Attacker Objective:** Attacker generates an RSA 2048-bit keypair on their local machine, mints a JWT with `"tenant_id": "11111111-1111-1111-1111-111111111111"`, `"role": "TENANT_ADMIN"`, and signs it using their private key (`kid: "rogue-key"`).
- **Stopping Layer:** **API Layer** (`JWTAuthMiddleware` & `JWKSClient`).
- **Enforcement Mechanics:**
  1. `JWTAuthMiddleware` extracts the token and reads `kid` from the header.
  2. `JWKSClient` attempts to fetch the public key for `rogue-key` from the configured IdP JWKS endpoint (`settings.jwks_url`).
  3. Key lookup fails (or public key signature verification fails).
  4. Middleware catches `AuthError` / `jwt.PyJWTError` and responds with `401 Unauthorized` (`{"detail": "Not authenticated"}`).
  5. The request never establishes a database connection.
- **Expected Status Code:** **`401 Unauthorized`**.

#### Threat Scenario 2.2: Tampered Token Payload (`TC-ADV-009`)
- **Attacker Objective:** Attacker obtains a valid JWT issued to Tenant Beta. They split the token (`header.payload.signature`), Base64URL-decode the payload, change `"tenant_id"` from `2222...` to `1111...`, re-encode it, and send it with the original signature.
- **Stopping Layer:** **API Layer** (`JWTAuthMiddleware` / `jwt.decode`).
- **Enforcement Mechanics:**
  1. `jwt.decode` calculates `SHA256(header + "." + tampered_payload)` and decrypts the signature with Keycloak's public RSA key.
  2. Cryptographic signature verification fails due to the payload mismatch.
  3. Middleware catches `PyJWTError` and responds with `401 Unauthorized`.
- **Expected Status Code:** **`401 Unauthorized`**.

#### Threat Scenario 2.3: Spoofed `X-Tenant-ID` Header (`TC-ADV-010`)
- **Attacker Objective:** Attacker sends a valid JWT for Tenant Beta, but includes an HTTP header `X-Tenant-ID: 11111111-1111-1111-1111-111111111111` in an attempt to trick the application into switching the tenant context.
- **Stopping Layer:** **API Layer** (`dependencies.py` & router handlers).
- **Enforcement Mechanics:**
  1. `JWTAuthMiddleware` populates `request.state.tenant_id = claims["tenant_id"]` from the cryptographically verified JWT.
  2. `get_current_tenant_id` strictly checks `getattr(request.state, "tenant_id", None)` before falling back to headers:
     ```python
     tenant_id = getattr(request.state, "tenant_id", None) or request.headers.get("X-Tenant-ID")
     ```
  3. Router endpoints strictly utilize `user.tenant_uuid` from `TokenClaims` (decoded JWT payload).
  4. The spoofed `X-Tenant-ID` header is completely ignored; queries execute under Tenant Beta.
- **Expected Status Code:** **`404 Not Found`** when targeting Tenant Alpha's resources.

---

### 4.3 Attack Vector 3: Missing Tenant Context

#### Threat Scenario 3.1: Unauthenticated Requests (`TC-ADV-011`)
- **Attacker Objective:** Send requests to protected APIs without providing any credentials.
- **Stopping Layer:** **API Layer** (`JWTAuthMiddleware`).
- **Enforcement Mechanics:**
  1. Middleware checks `is_public(request.url.path)`. Protected paths return `False`.
  2. `extract_bearer_token(None)` raises `AuthError("Missing Authorization header")`.
  3. Responds immediately with `401 Unauthorized` (`{"detail": "Not authenticated"}`).
- **Expected Status Code:** **`401 Unauthorized`**.

#### Threat Scenario 3.2: Omitted, Empty, or Nil UUID `tenant_id` in Token (`TC-ADV-012` & `TC-ADV-013`)
- **Attacker Objective:** Present a token without a valid tenant context (e.g. legacy token, system token, empty string, whitespace, or nil UUID `00000000-0000-0000-0000-000000000000`).
- **Stopping Layer:** **API Layer** (`TokenClaims.from_claims`).
- **Enforcement Mechanics:**
  1. `TokenClaims.from_claims(claims)` validates that `tenant_id` is present, non-empty, non-whitespace, and a valid RFC 4122 non-nil UUID.
  2. Any violation raises `AuthError("Invalid tenant_id in token claims")`.
  3. Middleware catches the error and responds with `401 Unauthorized`.
- **Expected Status Code:** **`401 Unauthorized`**.

#### Threat Scenario 3.3: Cleared or Unset Database Session Context (`TC-ADV-014`)
- **Attacker Objective:** Exploit application code or background tasks that execute SQL queries without setting `app.current_tenant_id`.
- **Stopping Layer:** **Database Layer** (PostgreSQL RLS Fail-Safe Closed).
- **Enforcement Mechanics:**
  1. The RLS policy expression is:
     ```sql
     tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
     ```
  2. When unset or cleared, `current_setting('app.current_tenant_id', true)` returns `NULL` or `''`.
  3. `NULLIF(..., '')` evaluates to `NULL`.
  4. In SQL, `tenant_id = NULL` evaluates to `UNKNOWN` (falsy).
  5. **Fail-Safe Closed Guarantee:**
     - `SELECT`, `UPDATE`, and `DELETE` return **`zero rows`**.
     - `INSERT` fails `WITH CHECK` and raises **`SQLSTATE 42501`**.
- **Expected Result:** **`zero rows`** (SELECT/UPDATE/DELETE); **`SQLSTATE 42501`** (INSERT).

---

### 4.4 Attack Vector 4: Tenant Switch Mid-Session

#### Threat Scenario 4.1: HTTP Keep-Alive Connection Reuse Across Tenants (`TC-ADV-015`)
- **Attacker Objective:** A client connects via HTTP/1.1 keep-alive or HTTP/2 multiplexing, makes a legitimate request as Tenant Alpha, and then makes a subsequent request over the same connection using a token for Tenant Beta attempting to access Tenant Alpha's state.
- **Stopping Layer:** **API Layer** (Stateless Middleware Architecture).
- **Enforcement Mechanics:**
  1. FastAPI and Starlette process each incoming HTTP request independently.
  2. `JWTAuthMiddleware` runs per request, decoding the specific `Authorization` header on that request and overwriting `request.state.tenant_id` and `request.state.user`.
  3. No state persists across requests on the connection. The second request is strictly constrained to Tenant Beta and receives `404 Not Found` when requesting Tenant Alpha resources.
- **Expected Status Code:** **`404 Not Found`**.

#### Threat Scenario 4.2: Mid-Session Database Context Switching (`TC-ADV-016`)
- **Attacker Objective:** An application session or malicious multi-statement batch executes `set_config('app.current_tenant_id', Tenant Alpha)`, retrieves data, and then executes `set_config(..., Tenant Beta)` on the same open database connection.
- **Stopping Layer:** **Database Layer** (PostgreSQL RLS Dynamic Evaluation).
- **Enforcement Mechanics:**
  1. When `set_config('app.current_tenant_id', :tid, true)` changes to Tenant Beta, PostgreSQL dynamically re-evaluates the RLS policy on all subsequent queries.
  2. Subsequent `SELECT` queries for Tenant Alpha's rows immediately return **`zero rows`**.
  3. Subsequent `UPDATE` or `DELETE` queries targeting Tenant Alpha's rows affect **`zero rows`** (`rowcount == 0`).
- **Expected Result:** Immediate boundary shift; queries targeting previous tenant yield **`zero rows`**.

#### Threat Scenario 4.3: Connection Pooling State Leakage in PgBouncer (`TC-ADV-017`)
- **Attacker Objective:** In transaction pooling mode, a backend PostgreSQL connection used by Tenant Alpha is checked back into the pool and reassigned to Tenant Beta. If session state persists, Tenant Beta could inherit Tenant Alpha's context.
- **Stopping Layer:** **Database & Connection Pool Layer** (PgBouncer & PostgreSQL).
- **Enforcement Mechanics:**
  1. Application sets context with `is_local = true`:
     ```sql
     SELECT set_config('app.current_tenant_id', :tenant_id, true)
     ```
     This automatically clears upon `COMMIT` or `ROLLBACK`.
  2. PgBouncer enforces transaction pooling with `server_reset_query = DISCARD ALL` and `server_reset_query_always = 1`.
  3. Any residual session variables, temporary tables, or prepared statements are unconditionally wiped before the connection is assigned to another client.
- **Expected Result:** **`zero rows`** leaked across connection pool reuses.

---

### 4.5 Attack Vector 5: Direct DB Access as the App Role (`eng_app_user`)

#### Threat Scenario 5.1: Unscoped Bulk Reads / Exfiltration (`TC-ADV-018`)
- **Attacker Objective:** An attacker achieves SQL injection or gains the credentials of the application runtime user `eng_app_user`. The attacker connects directly to PostgreSQL and executes `SELECT * FROM tenants;`, `SELECT * FROM integrations;`, `SELECT * FROM projects;`.
- **Stopping Layer:** **Database Layer** (PostgreSQL RLS & Role Privileges).
- **Enforcement Mechanics:**
  1. The application role `eng_app_user` is provisioned with `NOSUPERUSER` and `NOBYPASSRLS`.
  2. All 41 tenant tables have both `relrowsecurity = true` and `relforcerowsecurity = true` (`FORCE ROW LEVEL SECURITY`).
  3. Because `app.current_tenant_id` is unset on a raw direct connection, `tenant_isolation_policy` evaluates to `tenant_id = NULL` (`UNKNOWN`).
  4. The query returns **`zero rows`** across all 41 tenant tables. Bulk exfiltration is completely blocked.
- **Expected Result:** **`zero rows`** returned.

#### Threat Scenario 5.2: Attempted RLS Disabling (`TC-ADV-019`)
- **Attacker Objective:** The attacker attempts to disable RLS via `SET row_security = off;` or `ALTER TABLE projects DISABLE ROW LEVEL SECURITY;`.
- **Stopping Layer:** **Database Layer** (PostgreSQL Privilege Engine).
- **Enforcement Mechanics:**
  1. In PostgreSQL, `SET row_security = off` is strictly restricted: for non-superusers without `BYPASSRLS`, querying a table with RLS either raises an error or RLS continues to be enforced.
  2. Furthermore, `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` requires table ownership (`manta_user`). `eng_app_user` is not the owner and is denied with **`SQLSTATE 42501`** (`must be owner of table`).
- **Expected Result:** RLS remains active; queries return **`zero rows`** or raise **`SQLSTATE 42501`**.

#### Threat Scenario 5.3: Direct INSERT into `tenants` Root Table (`TC-ADV-020`)
- **Attacker Objective:** An attacker connected as `eng_app_user` attempts to insert an unauthorized root tenant record directly into the `tenants` table.
- **Stopping Layer:** **Database Layer** (PostgreSQL Role Permissions).
- **Enforcement Mechanics:**
  1. Direct `INSERT` on `tenants` is explicitly revoked: `REVOKE INSERT ON tenants FROM eng_app_user;`.
  2. Tenant provisioning is strictly confined to the `SECURITY DEFINER` function `provision_tenant(...)`.
  3. Direct SQL `INSERT INTO tenants ...` is denied with **`SQLSTATE 42501`** (`permission denied for table tenants`).
- **Expected Result:** **`SQLSTATE 42501`** (`permission denied`).

---

## 5. Defense-in-Depth Layer Summary

| Attack Vector | Primary Stopping Layer | Primary Enforcement Mechanism | Secondary Stopping Layer | Secondary Enforcement Mechanism | Expected Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Cross-Tenant Read by ID** | **API Layer** | Scoped query by `user.tenant_uuid` $\rightarrow$ entity not found | **Database Layer** | PostgreSQL RLS `USING` clause filters row | `404 Not Found` (API)<br>`zero rows` (Database) |
| **Cross-Tenant Write by ID** | **API Layer** | Scoped lookup yields 404 on target | **Database Layer** | RLS `USING` (0 rows affected) & `WITH CHECK` (SQLSTATE 42501) | `404 Not Found` (API)<br>`zero rows` / `42501` (Database) |
| **Forged `tenant_id` Claim** | **API Layer** | RS256 signature verification & JWKS key lookup failure | **Database Layer** | Even if passed, RLS restricts to bound UUID | `401 Unauthorized` |
| **Missing Tenant Context** | **API Layer** | Mandatory `tenant_id` claim check in JWT middleware | **Database Layer** | RLS fail-safe closed (`NULLIF` returns NULL) | `401 Unauthorized` (API)<br>`zero rows` (Database) |
| **Tenant Switch Mid-Session** | **API Layer** | Stateless per-request token evaluation | **Database Layer** | Dynamic RLS policy evaluation & PgBouncer `DISCARD ALL` | `404 Not Found` (API)<br>`zero rows` leaked (Database) |
| **Direct DB Access as App Role** | **Database Layer** | `NOBYPASSRLS` + `FORCE ROW LEVEL SECURITY` on all 41 tables | **Database Layer** | `REVOKE INSERT ON tenants FROM eng_app_user` | `zero rows` (SELECT)<br>`SQLSTATE 42501` (INSERT/DDL) |

---

## 6. Automated Test Suite Execution Guide

To execute the automated adversarial tenant boundary test suite locally or in CI:

```powershell
# 1. Ensure PostgreSQL is active (e.g. port 5433 or 5432)
docker compose -f backend/docker-compose.yml up -d postgres

# 2. Run the automated adversarial test suite
$env:DATABASE_URL="postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5433/manta_db"
python -m pytest backend/tests/test_tenant_boundary_adversarial.py -v
```

### Pytest Test Mapping:
- `test_adv_001_cross_tenant_read_by_id_api_and_db` $\rightarrow$ Covers `TC-ADV-001` & `TC-ADV-002`.
- `test_adv_002_cross_tenant_write_by_id_api_and_db` $\rightarrow$ Covers `TC-ADV-003`, `TC-ADV-004`, `TC-ADV-005`, `TC-ADV-006`, and `TC-ADV-007`.
- `test_adv_003_forged_tenant_id_claims_api` $\rightarrow$ Covers `TC-ADV-008`, `TC-ADV-009`, and `TC-ADV-010`.
- `test_adv_004_missing_tenant_context_api_and_db` $\rightarrow$ Covers `TC-ADV-011`, `TC-ADV-012`, `TC-ADV-013`, and `TC-ADV-014`.
- `test_adv_005_tenant_switch_mid_session_api_and_db` $\rightarrow$ Covers `TC-ADV-015`, `TC-ADV-016`, and `TC-ADV-017`.
- `test_adv_006_direct_db_access_app_role` $\rightarrow$ Covers `TC-ADV-018`, `TC-ADV-019`, and `TC-ADV-020`.
