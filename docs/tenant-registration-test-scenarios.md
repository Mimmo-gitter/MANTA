# Tenant Registration & Cold-Start Isolation Test Scenarios

**Task:** Create test scenarios (registration before any tenant exists, tenant isolation) (`SCRUM-290`)  
**Parent User Story:** `SCRUM-243` (`US-7` Tenant registration)  
**Related Tickets:**  
- `SCRUM-287` (Tenant registration 3-step wizard frontend page)  
- `SCRUM-288` (PostgreSQL `SECURITY DEFINER` provisioning function `provision_tenant`)  
- `SCRUM-289` (Public registration endpoint `POST /tenants/register` & rate limiter)  
- `SCRUM-244` / `SCRUM-294` / `SCRUM-295` (US-8: Database-enforced tenant isolation via PostgreSQL RLS)  
- `SCRUM-246` / `SCRUM-301` / `SCRUM-302` (US-10: Adversarial cross-tenant boundary security tests)  
- `SCRUM-292` (`eng_app_user` least-privilege role & `NOBYPASSRLS`), `SCRUM-318` (`FORCE ROW LEVEL SECURITY`), `SCRUM-293` (Automated RLS schema guardrails)  

**Target Architecture Layers:**  
1. **Frontend / Web UI Layer:** Next.js 14 / React 3-step registration wizard (`web/src/app/register/page.tsx`, `web/src/components/auth/register-step1-card.tsx`, `web/src/components/auth/register-step2-card.tsx`, `web/src/components/auth/register-step3-card.tsx`, `web/src/components/auth/register-success-card.tsx`).  
2. **API Layer:** FastAPI public endpoint `POST /tenants/register` (`backend/app/tenants/router.py`), Pydantic validation schemas (`backend/app/tenants/schemas.py`), sliding-window IP rate limiter (`backend/app/tenants/rate_limiter.py`).  
3. **Database Layer:** PostgreSQL 16 (`pgvector/pgvector:pg16`), least-privilege runtime role (`eng_app_user`), `SECURITY DEFINER` function `provision_tenant(...)` with pinned `search_path = public, pg_temp`, Row-Level Security (`tenant_isolation_policy` on `tenants` and all 39 child tables).  

**Automated Test Suites:**  
- **Pytest Suite:** [`backend/tests/test_tenant_registration.py`](../backend/tests/test_tenant_registration.py)  
- **Playwright E2E Suite:** [`web/e2e/tenant-registration.spec.ts`](../web/e2e/tenant-registration.spec.ts)  
- **Supporting Pytest Suites:**  
  - [`backend/tests/test_rls_isolation.py`](../backend/tests/test_rls_isolation.py)  
  - [`backend/tests/test_tenant_boundary_adversarial.py`](../backend/tests/test_tenant_boundary_adversarial.py)  
  - [`backend/tests/security/test_cross_tenant_read_write.py`](../backend/tests/security/test_cross_tenant_read_write.py)  

---

## 1. Objective & Bootstrapping Architecture

### 1.1 Objective
Multi-tenant architectures that rely on PostgreSQL Row-Level Security (RLS) face an architectural "cold-start paradox":
1. Under normal operation, all application queries execute under the least-privilege role `eng_app_user` (`NOBYPASSRLS`).
2. RLS policies evaluate `tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid`.
3. In a completely empty system (**zero tenants exist yet**), `app.current_tenant_id` is unset (`NULL`). Standard `SELECT`, `UPDATE`, and `DELETE` queries match **0 rows**, and direct `INSERT` violates RLS `WITH CHECK` (`SQLSTATE 42501`).
4. Furthermore, direct `INSERT` privilege on the `tenants` table is explicitly revoked from `eng_app_user` (`SCRUM-288 AC 3`).

The objective of this specification is to document and verify:
1. **First registration on an empty system** (bootstrapping the initial tenant without deadlock or privilege elevation).
2. **Second tenant registration** (concurrent multi-tenant onboarding without crosstalk).
3. **Tenant isolation immediately post-registration** (proving Tenant A cannot see, query, modify, or infer Tenant B's data across API and database layers).
4. **Invalid payloads and schema validation** (strict boundary enforcement rejecting malformed input with `HTTP 422`).
5. **Duplicate registration handling** (global slug uniqueness enforced with `HTTP 409 Conflict`, while allowing cross-tenant user email coexistence).

---

### 1.2 Bootstrapping Architecture & Defense-in-Depth

```
+---------------------------------------------------------------------------------------------------------------+
|                                    TENANT ONBOARDING & ISOLATION LIFECYCLE                                    |
+---------------------------------------------------------------------------------------------------------------+
| 1. COLD START (0 Tenants)       | 2. FIRST REGISTRATION          | 3. SECOND REGISTRATION & ISOLATION         |
+---------------------------------+--------------------------------+--------------------------------------------+
| * Database empty / migrated     | * POST /tenants/register       | * Second organization registers            |
| * No tenant context (NULL)      | * No JWT / auth required       | * Obtains unique tenant_id (UUID)          |
| * Direct INSERT on tenants      | * Sliding IP rate limiter      | * Strict RLS separation enforced           |
|   denied for eng_app_user       | * provision_tenant() executed  | * Tenant A cannot see Tenant B             |
| * Standard queries return 0 rows|   under SECURITY DEFINER       | * Cross-tenant lookups return 404 / 0 rows |
+---------------------------------+--------------------------------+--------------------------------------------+
                                                 |
                                                 v
+---------------------------------------------------------------------------------------------------------------+
|                                    SECURITY DEFINER PROVISIONING ROUTINE                                     |
+---------------------------------------------------------------------------------------------------------------+
| FUNCTION public.provision_tenant(p_name, p_slug, p_admin_email, p_admin_name, p_allowed_domain, ...)         |
|   SECURITY DEFINER                                                                                            |
|   SET search_path = public, pg_temp                                                                           |
|   REVOKE ALL ON FUNCTION FROM PUBLIC; GRANT EXECUTE TO eng_app_user;                                          |
+---------------------------------------------------------------------------------------------------------------+
| Inside Atomic Transaction:                                                                                    |
|   1. INSERT INTO tenants (id, slug, name, allowed_domain, status, tier, max_projects, ai_processing_mode)     |
|   2. INSERT INTO users (id, tenant_id, email, full_name, role, is_active)                                     |
|   3. INSERT INTO user_roles (user_id, role_id, tenant_id) VALUES (admin_id, 'TENANT_ADMIN', tenant_id)        |
|   4. Returns (tenant_id, user_id, slug, admin_email)                                                          |
+---------------------------------------------------------------------------------------------------------------+
                                                 |
                                                 v
+---------------------------------------------------------------------------------------------------------------+
|                                      POST-BOOTSTRAP RLS ENFORCEMENT                                           |
+---------------------------------------------------------------------------------------------------------------+
| * eng_app_user connects with NOBYPASSRLS                                                                      |
| * Session binds: SELECT set_config('app.current_tenant_id', :tenant_id, true)                                 |
| * Policy on child tables: USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)|
| * Policy on root tenants: USING (id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)       |
| * Anti-enumeration: API returns 404 Not Found on cross-tenant ID lookups (no 403 existence oracle)           |
+---------------------------------------------------------------------------------------------------------------+
```

---

## 2. Canonical Test Scenario Matrix

| Scenario ID | Scenario Name / Category | System Preconditions | Inputs / Request Payload | Stopping Layer | Expected Response / Database Outcome | Automated Test Reference | Acceptance Criteria Traceability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-REG-001** | **First Registration on Empty System (Cold-Start)** | Empty database (`tenants` rowcount = 0); no active session or tenant context. | `POST /tenants/register`<br>Payload with valid `name`, `slug`, `admin_email`, `admin_name`. | Database (`SECURITY DEFINER` routine `provision_tenant`) | `HTTP 201 Created`<br>Returns `tenant_id`, `admin_id`, `slug`. Atomic DB commit: exactly 1 tenant (`status='active'`), 1 admin user (`TENANT_ADMIN`), 1 `user_roles` entry. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_success_201`<br>`test_first_registration_and_second_tenant_isolation_post_registration` | SCRUM-243 US-7 AC-1; Cold-start bootstrapping; Zero tenant condition covered |
| **TC-REG-002** | **Second Tenant Registration (Multi-Tenant Onboarding)** | Tenant A already exists in system (`tenants` rowcount $\ge$ 1). | `POST /tenants/register`<br>New organization payload with distinct `name`, `slug`, `admin_email`. | Database (`SECURITY DEFINER` routine `provision_tenant`) | `HTTP 201 Created`<br>Returns distinct `tenant_b_id` $\ne$ `tenant_a_id`. DB contains 2 independent tenants, 2 independent admin users. | `test_tenant_registration.py` $\rightarrow$ `test_first_registration_and_second_tenant_isolation_post_registration` | SCRUM-243 US-7 AC-2; Multi-tenant coexistence |
| **TC-REG-003A** | **Cross-Tenant Read Isolation (Database RLS)** | Tenant A and Tenant B both registered and provisioned. | SQL executed under Tenant A context (`app.current_tenant_id = Tenant A`):<br>`SELECT * FROM tenants;`<br>`SELECT * FROM users;`<br>`SELECT * FROM tenants WHERE id = :tenant_b_id;` | Database Layer (PostgreSQL RLS `USING` clause) | Only Tenant A records returned.<br>Direct lookup of Tenant B ID yields **0 rows**.<br>Tenant B admin user not visible in `users` query. | `test_tenant_registration.py` $\rightarrow$ `test_first_registration_and_second_tenant_isolation_post_registration`<br>`test_rls_isolation.py` $\rightarrow$ `test_project_cross_tenant_read_isolation` | SCRUM-244 US-8 AC-1; RLS database isolation |
| **TC-REG-003B** | **Cross-Tenant Mutation Isolation (Database RLS)** | Tenant A and Tenant B both registered and provisioned. | SQL executed under Tenant A context (`app.current_tenant_id = Tenant A`):<br>`UPDATE tenants SET name = 'hacked' WHERE id = :tenant_b_id;`<br>`DELETE FROM tenants WHERE id = :tenant_b_id;` | Database Layer (PostgreSQL RLS `USING` clause) | **0 rows affected** (`rowcount == 0`). Tenant B data remains completely unchanged. | `test_tenant_registration.py` $\rightarrow$ `test_first_registration_and_second_tenant_isolation_post_registration`<br>`test_rls_isolation.py` $\rightarrow$ `test_project_cross_tenant_update_isolation` | SCRUM-244 US-8 AC-2; Cross-tenant mutation block |
| **TC-REG-003C** | **Session Without Tenant Context (Cleared Context)** | System has active tenants. | SQL executed under `eng_app_user` with `app.current_tenant_id` unset (`NULL`) or empty string (`''`):<br>`SELECT * FROM tenants;`<br>`SELECT * FROM users;` | Database Layer (PostgreSQL RLS fail-safe closed) | **0 rows returned** (`len(rows) == 0`). Fail-safe closed default prevents unauthenticated data exposure. | `test_tenant_registration.py` $\rightarrow$ `test_first_registration_and_second_tenant_isolation_post_registration`<br>`test_rls_isolation.py` $\rightarrow$ `test_cleared_tenant_setting_isolation` | SCRUM-244 US-8 AC-3; Fail-safe closed context |
| **TC-REG-003D** | **Cross-Tenant API Read by ID (Information Hiding)** | Tenant A user authenticated with valid JWT; Tenant B integration exists. | `GET /api/v1/integrations/{tenant_b_integration_id}` with Tenant A token. | API Layer & RLS | `HTTP 404 Not Found`<br>(Never `403 Forbidden` to prevent tenant enumeration and existence oracles). | `test_tenant_boundary_adversarial.py` $\rightarrow$ `test_adv_001_cross_tenant_read_by_id_api_and_db`<br>`test_cross_tenant_read_write.py` $\rightarrow$ `test_cross_tenant_read_by_id_returns_404` | SCRUM-246 US-10 AC-1; Information hiding |
| **TC-REG-004A** | **Duplicate Registration: Existing Slug (Explicit)** | Tenant A registered with slug `acme-corp`. | `POST /tenants/register`<br>Payload with `"slug": "acme-corp"`. | API Layer & Database Unique Constraint (`tenants_slug_key`) | `HTTP 409 Conflict`<br>`detail: "An organization with slug 'acme-corp' already exists."`<br>Transaction rolled back; no orphaned records. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_duplicate_slug_returns_409` | SCRUM-289 AC-6; Conflict handling |
| **TC-REG-004B** | **Duplicate Registration: Auto-Derived Slug Collision** | Tenant A registered with auto-derived slug `beta-innovations`. | `POST /tenants/register`<br>Payload with `"name": "Beta Innovations"`, omitting `slug`. | API Layer (effective slug calculation) & DB unique constraint | `HTTP 409 Conflict`<br>`detail: "An organization with slug 'beta-innovations' already exists."` | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_duplicate_slug_returns_409`<br>`test_register_tenant_auto_slug_generation` | SCRUM-289 AC-4 & AC-6; Slug collision resolution |
| **TC-REG-004C** | **Cross-Tenant Admin Email Coexistence** | Tenant A registered with admin email `lead@shared.com` and slug `org-alpha`. | `POST /tenants/register`<br>Tenant B registration with same admin email `lead@shared.com` and slug `org-beta`. | Database Constraint (`uq_users_tenant_email` is scoped per tenant) | `HTTP 201 Created`<br>Both tenants register successfully. `users` table allows identical email across distinct `tenant_id`s. | `test_tenant_registration.py` $\rightarrow$ `test_cross_tenant_admin_email_coexistence_and_duplicate_slug_rejection` | Multi-tenant user schema validation |
| **TC-REG-005A** | **Invalid Payload: Missing Required Fields** | System ready. | `POST /tenants/register`<br>Payload missing `name`, `admin_email`, or `admin_name`. | API Layer (Pydantic Schema Validation) | `HTTP 422 Unprocessable Entity`<br>Validation error details list missing required field names. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_validation_errors_422` | SCRUM-289 AC-3; Schema validation |
| **TC-REG-005B** | **Invalid Payload: Malformed Email Address** | System ready. | `POST /tenants/register`<br>Payload with `"admin_email": "not-an-email"` or `"admin@"`. | API Layer (`validate_admin_email` regex) | `HTTP 422 Unprocessable Entity`<br>`msg: "Invalid email address format"`. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_validation_errors_422` | SCRUM-289 AC-3; Email format enforcement |
| **TC-REG-005C** | **Invalid Payload: Malformed Slug (Case & Special Characters)** | System ready. | `POST /tenants/register`<br>Payload with `"slug": "Bad_Slug!"` or `"slug": "-leading-dash"`. | API Layer (`validate_slug` regex `^[a-z0-9]+(?:-[a-z0-9]+)*$`) | `HTTP 422 Unprocessable Entity`<br>`msg: "Slug must contain only lowercase alphanumeric characters and non-consecutive hyphens"`. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_validation_errors_422` | SCRUM-289 AC-3; Kebab-case slug enforcement |
| **TC-REG-005D** | **Invalid Payload: Blank / Whitespace-Only String** | System ready. | `POST /tenants/register`<br>Payload with `"name": "   "` or `"admin_name": " \t "`. | API Layer (`validate_non_empty` validator) | `HTTP 422 Unprocessable Entity`<br>`msg: "Field cannot be blank or whitespace-only"`. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_validation_errors_422` | Input sanitization |
| **TC-REG-005E** | **Invalid Payload: Extra Unexpected Fields (`extra='forbid'`)** | System ready. | `POST /tenants/register`<br>Payload containing `"is_superadmin": true`, `"role": "SUPERUSER"`. | API Layer (Pydantic `ConfigDict(extra="forbid")`) | `HTTP 422 Unprocessable Entity`<br>`msg: "Extra inputs are not permitted"`. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_extra_fields_forbidden_422` | Schema strictness & privilege escalation denial |
| **TC-REG-006** | **Automatic Slug Derivation (Omitted Slug)** | System ready. | `POST /tenants/register`<br>Payload with `"name": "Alpha Beta Solutions Inc"`, `"slug": null`. | API Layer (`get_effective_slug()`) | `HTTP 201 Created`<br>Response contains auto-derived slug `"alpha-beta-solutions-inc"`. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_auto_slug_generation` | SCRUM-289 AC-4; Automatic slug generation |
| **TC-REG-007** | **Abuse Prevention: IP Sliding-Window Rate Limiting** | System ready; rate limiter window active (10 req / 60 sec). | Single client IP issues 11 registration requests within 60 seconds. | API Layer (`RegistrationRateLimiter` middleware) | Requests 1-10: `HTTP 201 Created`.<br>Request 11: `HTTP 429 Too Many Requests` with `Retry-After: <seconds>` header. | `test_tenant_registration.py` $\rightarrow$ `test_register_tenant_rate_limiting_429` | SCRUM-289 AC-7; Registration rate limiting |
| **TC-REG-008A** | **Least-Privilege Role Denial: Direct INSERT Blocked** | Database active with `eng_app_user`. | Direct SQL executed as `eng_app_user`: `INSERT INTO tenants (...) VALUES (...);` | Database Storage Engine (PostgreSQL Table Privileges) | `SQLSTATE 42501`<br>`permission denied for table tenants`. Direct insertion without `provision_tenant` is impossible. | `test_tenant_registration.py` $\rightarrow$ `test_direct_insert_tenants_denied_for_eng_app_user` | SCRUM-288 AC-3; Least privilege enforcement |
| **TC-REG-008B** | **SECURITY DEFINER Function Metadata & Permissions** | Database active. | SQL catalog queries against `pg_proc`: prosecdef, proconfig, function privileges. | Database Engine (PostgreSQL Catalog) | `prosecdef = true`; `search_path = public, pg_temp`; `PUBLIC` execute revoked; `eng_app_user` execute granted. | `test_tenant_registration.py` $\rightarrow$ `test_security_definer_function_attributes` | SCRUM-288 AC-1, AC-2, AC-4 |
| **TC-REG-008C** | **Atomic Rollback on Provisioning Failure** | System ready. | Call `provision_tenant(...)` with invalid parameter (e.g. empty admin name `p_admin_name => ''`). | Database Engine (`provision_tenant` exception block) | Function raises `RAISE EXCEPTION`. Transaction rolled back; 0 rows created in `tenants`, `users`, or `user_roles`. | `test_tenant_registration.py` $\rightarrow$ `test_security_definer_atomicity_on_failure` | SCRUM-288 AC-5; Atomic transaction guarantee |

---

## 3. Detailed Scenario Specifications

### Scenario 1: First Registration on an Empty System (`TC-REG-001`)

- **Objective:** Verify that an organization can successfully register and bootstrap when the database contains **zero existing tenants**, without encountering RLS lockouts or permission errors.
- **Preconditions:**
  - Database schema is fully migrated (`alembic upgrade head`).
  - `SELECT count(*) FROM tenants;` returns `0`.
  - The client is unauthenticated (no JWT, no `X-Tenant-ID` header, no session cookies).
- **Request Details:**
  - **Method:** `POST`
  - **Endpoint:** `/tenants/register`
  - **Headers:** `Content-Type: application/json`
  - **Input Payload:**
    ```json
    {
      "name": "First Enterprise Corp",
      "slug": "first-enterprise-corp",
      "admin_email": "admin@firstenterprise.com",
      "admin_name": "First Admin",
      "allowed_domain": "firstenterprise.com"
    }
    ```
- **Execution Flow & Stopping Layer:**
  1. FastAPI passes request to `RegistrationRateLimiter.check_rate_limit(request)` (within quota).
  2. Pydantic validates input against `TenantRegisterRequest` schema.
  3. API invokes PostgreSQL `SECURITY DEFINER` function `provision_tenant(...)` under `eng_app_user`.
  4. The function executes with elevated database owner privileges, bypassing the unpopulated RLS context to insert:
     - Root tenant row into `tenants` with `status = 'active'`, `tier = 'starter'`, `max_projects = 5`, `retention_days = 90`.
     - First user row into `users` with `role = 'TENANT_ADMIN'`, `is_active = true`.
     - First role assignment into `user_roles` with `role_id = 'TENANT_ADMIN'`.
  5. Transaction commits atomically.
- **Expected Results:**
  - **HTTP Status:** `201 Created`
  - **Response Payload:**
    ```json
    {
      "tenant_id": "<generated-uuid-1>",
      "admin_id": "<generated-uuid-2>",
      "slug": "first-enterprise-corp",
      "name": "First Enterprise Corp",
      "admin_email": "admin@firstenterprise.com",
      "message": "Tenant and administrator successfully registered"
    }
    ```
  - **Database Verification:**
    - `SELECT count(*) FROM tenants;` (under admin or tenant context) equals `1`.
    - `SELECT role FROM users WHERE id = '<generated-uuid-2>';` equals `'TENANT_ADMIN'`.
    - Subsequent application transactions setting `app.current_tenant_id = '<generated-uuid-1>'` can query this tenant's data.

---

### Scenario 2: Second Tenant Registration (`TC-REG-002`)

- **Objective:** Verify that after the initial tenant is bootstrapped, a second independent organization can register without crosstalk or interference.
- **Preconditions:**
  - Tenant A exists in the database with `slug = "first-enterprise-corp"`.
- **Request Details:**
  - **Method:** `POST`
  - **Endpoint:** `/tenants/register`
  - **Input Payload:**
    ```json
    {
      "name": "Second Global Ltd",
      "slug": "second-global-ltd",
      "admin_email": "admin@secondglobal.org",
      "admin_name": "Second Admin",
      "allowed_domain": "secondglobal.org"
    }
    ```
- **Expected Results:**
  - **HTTP Status:** `201 Created`
  - **Response Payload:**
    ```json
    {
      "tenant_id": "<generated-uuid-3>",
      "admin_id": "<generated-uuid-4>",
      "slug": "second-global-ltd",
      "name": "Second Global Ltd",
      "admin_email": "admin@secondglobal.org",
      "message": "Tenant and administrator successfully registered"
    }
    ```
  - **Assertions:**
    - `data["tenant_id"] != tenant_a_id`
    - `data["admin_id"] != admin_a_id`
    - `tenants` table now contains exactly 2 active tenants.

---

### Scenario 3: Tenant A Cannot See Tenant B's Data (`TC-REG-003A` - `TC-REG-003D`)

- **Objective:** Prove that immediately following registration, full database-level and API-level tenant isolation is enforced so Tenant A cannot view or manipulate Tenant B's data.
- **Preconditions:**
  - Tenant A (`tenant_a_id`, `slug_a`) and Tenant B (`tenant_b_id`, `slug_b`) are both registered.
- **Sub-scenario 3.1: Database RLS Read Isolation (`TC-REG-003A`)**
  - **Action:** Open database session and execute:
    ```sql
    SELECT set_config('app.current_tenant_id', :tenant_a_id, true);
    SELECT id, slug FROM tenants;
    SELECT id, tenant_id FROM users;
    SELECT id FROM tenants WHERE id = :tenant_b_id;
    ```
  - **Expected Result:**
    - `SELECT id, slug FROM tenants` returns **exactly 1 row** (Tenant A only).
    - `SELECT id, tenant_id FROM users` returns **only Tenant A users**.
    - `SELECT id FROM tenants WHERE id = :tenant_b_id` returns **0 rows**.
- **Sub-scenario 3.2: Database RLS Mutation Isolation (`TC-REG-003B`)**
  - **Action:** Under Tenant A context (`app.current_tenant_id = :tenant_a_id`):
    ```sql
    UPDATE tenants SET name = 'Tampered' WHERE id = :tenant_b_id;
    DELETE FROM tenants WHERE id = :tenant_b_id;
    ```
  - **Expected Result:**
    - `rowcount == 0` for both `UPDATE` and `DELETE`.
    - Tenant B row in `tenants` remains completely untouched.
- **Sub-scenario 3.3: Cleared / Unset Context Fail-Safe Closed (`TC-REG-003C`)**
  - **Action:** Execute queries as `eng_app_user` with `app.current_tenant_id` unset (`NULL`) or empty string (`''`):
    ```sql
    SELECT id FROM tenants;
    SELECT id FROM users;
    ```
  - **Expected Result:**
    - Returns **0 rows**. `NULLIF('', '')` evaluates to `NULL`; in SQL `id = NULL` evaluates to `UNKNOWN` (falsy).
- **Sub-scenario 3.4: API Information Hiding (`TC-REG-003D`)**
  - **Action:** Tenant A user calls `GET /api/v1/integrations/{tenant_b_integration_id}` with Tenant A JWT.
  - **Expected Result:**
    - `HTTP 404 Not Found` (avoids `403 Forbidden` to prevent resource enumeration).

---

### Scenario 4: Duplicate Registration Handling (`TC-REG-004A` - `TC-REG-004C`)

- **Objective:** Verify conflict detection when registering duplicate organizations or slugs.
- **Sub-scenario 4.1: Explicit Duplicate Slug (`TC-REG-004A`)**
  - **Initial State:** Tenant with slug `unique-org` is registered.
  - **Input Payload:**
    ```json
    {
      "name": "Second Attempt Corp",
      "slug": "unique-org",
      "admin_email": "other@second.com",
      "admin_name": "Second Admin"
    }
    ```
  - **Expected Result:**
    - `HTTP 409 Conflict`
    - Response body: `{"detail": "An organization with slug 'unique-org' already exists."}`
- **Sub-scenario 4.2: Auto-Derived Slug Collision (`TC-REG-004B`)**
  - **Initial State:** Tenant with slug `acme-corp` exists.
  - **Input Payload:** `{"name": "Acme Corp", "admin_email": "admin@acme-new.com", "admin_name": "Admin"}` (slug omitted).
  - **Expected Result:** Effective slug evaluates to `acme-corp` and returns `HTTP 409 Conflict`.
- **Sub-scenario 4.3: Cross-Tenant Admin Email Coexistence (`TC-REG-004C`)**
  - **Initial State:** Tenant 1 registered with `admin@shared.com` and slug `tenant-one`.
  - **Input Payload:** Tenant 2 registered with the same `admin@shared.com` and slug `tenant-two`.
  - **Expected Result:** `HTTP 201 Created`. PostgreSQL schema enforces uniqueness on `(tenant_id, email)` rather than global email uniqueness, allowing users to participate across multiple distinct tenants.

---

### Scenario 5: Invalid Payloads & Validation (`TC-REG-005A` - `TC-REG-005E`)

- **Objective:** Verify that malformed payloads are rejected by Pydantic before triggering any database transactions.
- **Sub-scenario 5.1: Missing Required Fields (`TC-REG-005A`)**
  - **Payload:** `{"name": "Incomplete Corp", "admin_name": "John"}` (missing `admin_email`).
  - **Expected Result:** `HTTP 422 Unprocessable Entity` with location `["body", "admin_email"]`.
- **Sub-scenario 5.2: Malformed Email Address (`TC-REG-005B`)**
  - **Payload:** `{"name": "Bad Email", "admin_email": "not-an-email", "admin_name": "Admin"}`.
  - **Expected Result:** `HTTP 422 Unprocessable Entity` (`"Invalid email address format"`).
- **Sub-scenario 5.3: Malformed Slug (`TC-REG-005C`)**
  - **Payload:** `{"name": "Bad Slug", "slug": "Bad_Slug!", "admin_email": "admin@test.com", "admin_name": "Admin"}`.
  - **Expected Result:** `HTTP 422 Unprocessable Entity` (`"Slug must contain only lowercase alphanumeric characters and non-consecutive hyphens"`).
- **Sub-scenario 5.4: Blank / Whitespace Name (`TC-REG-005D`)**
  - **Payload:** `{"name": "   ", "admin_email": "admin@test.com", "admin_name": "Admin"}`.
  - **Expected Result:** `HTTP 422 Unprocessable Entity` (`"Field cannot be blank or whitespace-only"`).
- **Sub-scenario 5.5: Extra Fields Forbidden (`TC-REG-005E`)**
  - **Payload:** `{"name": "Extra Fields Corp", "admin_email": "admin@test.com", "admin_name": "Admin", "is_superadmin": true}`.
  - **Expected Result:** `HTTP 422 Unprocessable Entity` (`"Extra inputs are not permitted"`).

---

### Scenario 6: Abuse Prevention & Rate Limiting (`TC-REG-006`)

- **Objective:** Protect the unauthenticated registration endpoint from automated denial-of-service or bulk registration attempts.
- **Preconditions:** Sliding window configured for 10 requests per 60 seconds per client IP.
- **Action:** Send 11 valid registration requests from client IP `198.51.100.42`.
- **Expected Results:**
  - Requests 1 through 10 succeed with `HTTP 201 Created`.
  - Request 11 receives `HTTP 429 Too Many Requests`.
  - Response header includes `Retry-After: <seconds>` (value $\ge 1$).

---

### Scenario 7: Database Privileges & Atomicity Safeguards (`TC-REG-007A` - `TC-REG-007C`)

- **Objective:** Verify PostgreSQL database-level security invariants for tenant provisioning.
- **Sub-scenario 7.1: Least-Privilege Role Restriction (`TC-REG-007A`)**
  - Direct `INSERT INTO tenants` by `eng_app_user` fails with `SQLSTATE 42501` (`permission denied for table tenants`).
- **Sub-scenario 7.2: Function Security Attributes (`TC-REG-007B`)**
  - Function `provision_tenant` is declared `SECURITY DEFINER` with `SET search_path = public, pg_temp`.
  - `EXECUTE` privilege is granted to `eng_app_user` and revoked from `PUBLIC`.
- **Sub-scenario 7.3: Atomicity on Provisioning Failure (`TC-REG-007C`)**
  - Invoking `provision_tenant` with an invalid parameter (e.g. empty admin name `p_admin_name => ''`) raises an exception.
  - Transaction rolls back completely; zero orphaned rows remain in `tenants`, `users`, or `user_roles`.

---

## 4. Web UI Registration Journey (Playwright E2E)

The frontend registration flow (`web/src/app/register/page.tsx`) implements a 3-step wizard with client-side validation, verified by Playwright in `web/e2e/tenant-registration.spec.ts`:

```
[ Step 1: Tenant Profile ]  --->  [ Step 2: Admin Credentials ]  --->  [ Step 3: Review & Submit ]  --->  [ Success Card ]
  - Organization Name               - Administrator Name                 - Summary Review                    - Tenant ID & Slug
  - Auto-generated Slug             - Administrator Email                - Final Submit Button               - SSO Sign-In Link
  - Manual Slug Override            - Password Policy Checklist          - Loading Indicator                 - Setup Instructions
```

- **Step 1 (Tenant Profile):** Auto-derives slug as user types; continue button disabled until name $\ge 2$ characters and valid kebab-case slug.
- **Step 2 (Admin Credentials):** Dynamic password policy checklist requiring 12+ characters, uppercase, number, and special character.
- **Step 3 (Confirmation):** Submits payload to `POST /tenants/register` and transitions to `RegisterSuccessCard` upon `HTTP 201`.

---

## 5. Traceability & Acceptance Criteria Verification

| Requirement / Acceptance Criteria | Status | Verified In Automated Tests | Traceable Scenario |
| :--- | :---: | :--- | :--- |
| **Scenarios include inputs and expected results** | **PASS** | `backend/tests/test_tenant_registration.py`<br>`backend/tests/test_rls_isolation.py`<br>`web/e2e/tenant-registration.spec.ts` | All scenarios (TC-REG-001 through TC-REG-008C) specify exact HTTP/SQL inputs and status/data expectations. |
| **The 'no tenant exists yet' case is explicitly covered** | **PASS** | `backend/tests/test_tenant_registration.py::test_register_tenant_success_201`<br>`test_first_registration_and_second_tenant_isolation_post_registration` | **TC-REG-001** (Section 1.1, 1.2, 2, 3) details the cold-start bootstrapping edge case and resolution. |
| **First registration on an empty system** | **PASS** | `test_tenant_registration.py::test_register_tenant_success_201` | **TC-REG-001** |
| **Second tenant registers** | **PASS** | `test_tenant_registration.py::test_first_registration_and_second_tenant_isolation_post_registration` | **TC-REG-002** |
| **Tenant A cannot see Tenant B's data** | **PASS** | `test_tenant_registration.py::test_first_registration_and_second_tenant_isolation_post_registration`<br>`test_tenant_boundary_adversarial.py::test_adv_001_cross_tenant_read_by_id_api_and_db` | **TC-REG-003A**, **TC-REG-003B**, **TC-REG-003C**, **TC-REG-003D** |
| **Invalid payloads rejected** | **PASS** | `test_tenant_registration.py::test_register_tenant_validation_errors_422`<br>`test_register_tenant_extra_fields_forbidden_422` | **TC-REG-005A** - **TC-REG-005E** |
| **Duplicate registration handled** | **PASS** | `test_tenant_registration.py::test_register_tenant_duplicate_slug_returns_409`<br>`test_cross_tenant_admin_email_coexistence_and_duplicate_slug_rejection` | **TC-REG-004A** - **TC-REG-004C** |
| **Database least-privilege & atomicity** | **PASS** | `test_tenant_registration.py::test_direct_insert_tenants_denied_for_eng_app_user`<br>`test_security_definer_function_attributes`<br>`test_security_definer_atomicity_on_failure` | **TC-REG-008A** - **TC-REG-008C** |
