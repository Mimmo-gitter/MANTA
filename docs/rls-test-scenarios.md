# Database-Enforced Tenant Isolation (RLS) Test Scenarios

**Task:** Create test scenarios (Tenant A vs Tenant B, cleared tenant setting) (`SCRUM-294`)  
**Parent User Story:** `SCRUM-244` (US-8 Database-enforced tenant isolation)  
**Related Tickets:** `SCRUM-292` (`eng_app_user` least-privilege role), `SCRUM-318` (RLS policies & `FORCE ROW LEVEL SECURITY`), `SCRUM-293` (Automated RLS schema guardrails), `SCRUM-295` (Automated RLS isolation test suite), `SCRUM-297` (`get_tenant_db_context` session dependency)  
**Target Database Engine:** PostgreSQL 16 (`pgvector/pgvector:pg16`)  
**RLS Migration:** [`backend/migrations/versions/5b1a7d8e2093_tenant_rls_and_eng_app_user.py`](../backend/migrations/versions/5b1a7d8e2093_tenant_rls_and_eng_app_user.py)  
**Automated Pytest Suites:** [`backend/tests/test_rls_isolation.py`](../backend/tests/test_rls_isolation.py), [`backend/tests/test_rls_guardrails.py`](../backend/tests/test_rls_guardrails.py)

---

## 1. Objective: How Tenant Isolation Is Verified at the Database Level

The MANTA platform enforces strict multi-tenant data isolation directly inside the PostgreSQL storage engine via **Row-Level Security (RLS)** so that even if an application query omits a `WHERE tenant_id = ...` filter, cross-tenant reads and writes are impossible.

Database-level isolation is verified across four architectural invariants:

1. **Least-Privilege Application Role (`eng_app_user`):**
   All runtime application and worker connections authenticate as `eng_app_user`, provisioned with `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, and `NOCREATEROLE`. Because `NOBYPASSRLS` is enforced, PostgreSQL evaluates RLS policies on every query issued by the application. Direct `INSERT` on `tenants` is revoked from `eng_app_user`; tenant creation is restricted to the `SECURITY DEFINER` function `provision_tenant(...)`.
2. **Mandatory & Forced RLS Across All 40 Tenant Tables:**
   Every tenant-scoped table (`tenants` plus all **39** child tables containing a `tenant_id` column) has both `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` enabled (`relrowsecurity = true`, `relforcerowsecurity = true`), verified continuously by [`backend/tests/test_rls_guardrails.py`](../backend/tests/test_rls_guardrails.py).
3. **Dual-Clause Policy Enforcement (`USING` for Read/Target Selection, `WITH CHECK` for Write Validation):**
   Each table defines a permissive policy `tenant_isolation_policy` (`FOR ALL TO eng_app_user`):
   - On the **39 child tables**:
     ```sql
     CREATE POLICY tenant_isolation_policy ON <table_name>
         AS PERMISSIVE FOR ALL
         TO eng_app_user
         USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
         WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
     ```
   - On the **`tenants` root table**:
     ```sql
     CREATE POLICY tenant_isolation_policy ON tenants
         AS PERMISSIVE FOR ALL
         TO eng_app_user
         USING (id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
         WITH CHECK (id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
     ```
   - **Read Path (`SELECT`):** Governed by `USING`. Only rows matching `app.current_tenant_id` are visible; all other rows are silently filtered out (`0` rows returned).
   - **Write Path (`INSERT`, `UPDATE`, `DELETE`):**
     - `INSERT` validates new rows against `WITH CHECK`; mismatched or unset `tenant_id` raises PostgreSQL `SQLSTATE 42501` (`new row violates row-level security policy`).
     - `UPDATE` first filters target rows via `USING` (making other tenants' rows invisible so cross-tenant `UPDATE` affects `0` rows) and then validates the modified tuple against `WITH CHECK` (preventing a tenant from re-assigning its own row's `tenant_id` to another tenant, which raises `SQLSTATE 42501`).
     - `DELETE` filters target rows via `USING` (cross-tenant `DELETE` affects `0` rows).
4. **Fail-Safe Closed Default & Transaction-Local Scoping:**
   `NULLIF(current_setting('app.current_tenant_id', true), '')::uuid` returns `NULL` whenever `app.current_tenant_id` is unset (`NULL`) or cleared (`''`). In SQL, `tenant_id = NULL` evaluates to `UNKNOWN` (falsy), causing all `SELECT`, `UPDATE`, and `DELETE` statements to match **0 rows** and all `INSERT` statements to fail `WITH CHECK`. Application sessions bind context per transaction using `SELECT set_config('app.current_tenant_id', :tenant_id, true)` (`is_local = true`), which automatically clears at `COMMIT` or `ROLLBACK`.

---

## 2. Canonical Seed Data Fixture (`Tenant A` vs `Tenant B`)

All test scenarios share a deterministic multi-tenant seed dataset provisioned prior to test execution (`setup_test_tenants()` in [`backend/tests/test_rls_isolation.py`](../backend/tests/test_rls_isolation.py#L31-L72)):

### 2.1 Seed Entities Summary

| Entity | Tenant Ownership | Primary Key (`id`) | `tenant_id` | Identifier / Slug / Email | Display Name & Key Attributes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tenant A** (`tenants`) | Tenant A | `11111111-1111-1111-1111-111111111111` | *(self `id`)* | `tenant-alpha-rls` | `name = 'Tenant Alpha Corp'`, `tier = 'enterprise'`, `ai_processing_mode = 'cloud_zdr'` |
| **Tenant B** (`tenants`) | Tenant B | `22222222-2222-2222-2222-222222222222` | *(self `id`)* | `tenant-beta-rls` | `name = 'Tenant Beta Ltd'`, `tier = 'starter'`, `ai_processing_mode = 'local'` |
| **Admin A** (`users`) | Tenant A | *(generated by `provision_tenant`)* | `11111111-1111-1111-1111-111111111111` | `admin@alpha-rls.com` | `full_name = 'Admin Alpha'`, `role = 'TENANT_ADMIN'`, `is_active = true` |
| **Admin B** (`users`) | Tenant B | *(generated by `provision_tenant`)* | `22222222-2222-2222-2222-222222222222` | `admin@beta-rls.com` | `full_name = 'Admin Beta'`, `role = 'TENANT_ADMIN'`, `is_active = true` |
| **User A** (`users`) | Tenant A | `a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1` | `11111111-1111-1111-1111-111111111111` | `alpha_dev@tenanta.com` | `full_name = 'Alpha Developer'`, `google_sub = 'sub-alpha-123'`, `role = 'DEVELOPER'` |
| **User B** (`users`) | Tenant B | `b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2` | `22222222-2222-2222-2222-222222222222` | `beta_dev@tenantb.com` | `full_name = 'Beta Developer'`, `google_sub = 'sub-beta-456'`, `role = 'DEVELOPER'` |
| **Project A** (`projects`) | Tenant A | `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa` | `11111111-1111-1111-1111-111111111111` | `project-alpha` | `name = 'Project Alpha'`, `tier_sla_hours = 24`, `wip_limit_per_dev = 3`, `is_active = true` |
| **Project B** (`projects`) | Tenant B | `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb` | `22222222-2222-2222-2222-222222222222` | `project-beta` | `name = 'Project Beta'`, `tier_sla_hours = 48`, `wip_limit_per_dev = 5`, `is_active = true` |

### 2.2 Seed SQL Statements

```sql
-- 1. Provision Tenant A and Tenant B via SECURITY DEFINER function
SELECT * FROM provision_tenant(
    p_name => 'Tenant Alpha Corp',
    p_slug => 'tenant-alpha-rls',
    p_admin_email => 'admin@alpha-rls.com',
    p_admin_name => 'Admin Alpha',
    p_tier => 'enterprise',
    p_ai_processing_mode => 'cloud_zdr',
    p_tenant_id => '11111111-1111-1111-1111-111111111111'
);

SELECT * FROM provision_tenant(
    p_name => 'Tenant Beta Ltd',
    p_slug => 'tenant-beta-rls',
    p_admin_email => 'admin@beta-rls.com',
    p_admin_name => 'Admin Beta',
    p_tier => 'starter',
    p_ai_processing_mode => 'local',
    p_tenant_id => '22222222-2222-2222-2222-222222222222'
);

-- 2. Seed Tenant A rows under Tenant A context
BEGIN;
SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);
INSERT INTO projects (id, tenant_id, name, slug, tier_sla_hours, authoritative_cd_signal, lead_time_first_commit_strategy, wip_limit_per_dev, pr_stale_threshold_hours, is_active)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Project Alpha', 'project-alpha', 24, 'github_deployments_api', 'feature_branch_root', 3, 48, true);

INSERT INTO users (id, tenant_id, email, full_name, google_sub, role, is_active)
VALUES ('a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1', '11111111-1111-1111-1111-111111111111', 'alpha_dev@tenanta.com', 'Alpha Developer', 'sub-alpha-123', 'DEVELOPER', true);
COMMIT;

-- 3. Seed Tenant B rows under Tenant B context
BEGIN;
SELECT set_config('app.current_tenant_id', '22222222-2222-2222-2222-222222222222', true);
INSERT INTO projects (id, tenant_id, name, slug, tier_sla_hours, authoritative_cd_signal, lead_time_first_commit_strategy, wip_limit_per_dev, pr_stale_threshold_hours, is_active)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'Project Beta', 'project-beta', 48, 'github_deployments_api', 'feature_branch_root', 5, 72, true);

INSERT INTO users (id, tenant_id, email, full_name, google_sub, role, is_active)
VALUES ('b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2', '22222222-2222-2222-2222-222222222222', 'beta_dev@tenantb.com', 'Beta Developer', 'sub-beta-456', 'DEVELOPER', true);
COMMIT;
```

---

## 3. Test Scenarios Matrix

| Scenario ID | Scenario Name | Path Coverage | Seed Data | Session Context & Actions | Expected Rows & Database Outcome | Automated Test Mapping (`backend/tests/test_rls_isolation.py`) |
| :--- | :--- | :---: | :--- | :--- | :--- | :--- |
| **TC-RLS-001** | **Tenant A Reads & Writes Own Data** | **READ & WRITE** | Tenant A (`11111111-...`), Tenant B (`22222222-...`), `Project A`, `Project B`, `User A`, `User B`. | Set `app.current_tenant_id = '11111111-...'`.<br>1. `SELECT * FROM tenants;`<br>2. `SELECT * FROM projects;`<br>3. `SELECT * FROM users;`<br>4. `UPDATE projects SET wip_limit_per_dev = 4 WHERE id = 'aaaaaaaa-...';` | • `tenants`: **1 row** (`11111111-...`, `'Tenant Alpha Corp'`).<br>• `projects`: **1 row** (`aaaaaaaa-...`, `'Project Alpha'`).<br>• `users`: **2 rows** (`admin@alpha-rls.com`, `alpha_dev@tenanta.com`).<br>• `UPDATE`: **1 row affected** (`rowcount = 1`).<br>• **0 rows** from Tenant B appear in any result set. | `test_tenant_read_isolation`<br>`test_project_cross_tenant_read_isolation`<br>`test_user_cross_tenant_isolation` |
| **TC-RLS-002** | **Tenant A Cannot Read Tenant B's Rows** | **READ** | Tenant A & Tenant B seeded with `Project A`, `Project B`, `User A`, `User B`. | Set `app.current_tenant_id = '11111111-...'` (Tenant A).<br>1. `SELECT * FROM tenants WHERE id = '22222222-...';`<br>2. `SELECT * FROM projects WHERE id = 'bbbbbbbb-...';`<br>3. `SELECT * FROM projects WHERE tenant_id = '22222222-...';`<br>4. `SELECT * FROM users WHERE id = 'b2b2b2b2-...';` | • `tenants` query: **0 rows** returned.<br>• `projects` by `id`: **0 rows** returned.<br>• `projects` by `tenant_id`: **0 rows** returned.<br>• `users` by `id`: **0 rows** returned.<br>• PostgreSQL `USING` policy silently filters out all Tenant B rows. | `test_tenant_read_isolation`<br>`test_project_cross_tenant_read_isolation`<br>`test_user_cross_tenant_isolation` |
| **TC-RLS-003** | **Tenant A Cannot Write (`INSERT` / `UPDATE` / `DELETE`) Tenant B's Rows** | **WRITE & READ** | Tenant A (`11111111-...`), Tenant B (`22222222-...`), `Project B` (`bbbbbbbb-...`, `name = 'Original Beta Name'`). | Set `app.current_tenant_id = '11111111-...'` (Tenant A).<br>1. `INSERT INTO projects (..., tenant_id) VALUES (..., '22222222-...');`<br>2. `UPDATE projects SET name = 'Hacked by Tenant A' WHERE id = 'bbbbbbbb-...';`<br>3. `DELETE FROM projects WHERE id = 'bbbbbbbb-...';`<br>4. Switch to Tenant B and `SELECT * FROM projects WHERE id = 'bbbbbbbb-...';` | • `INSERT`: **Rejected** (`SQLSTATE 42501`: `new row violates row-level security policy for table "projects"`); **0 rows** inserted.<br>• `UPDATE`: **0 rows affected** (`rowcount = 0`).<br>• `DELETE`: **0 rows affected** (`rowcount = 0`).<br>• Verification read as Tenant B returns **1 row** (`bbbbbbbb-...`, `'Original Beta Name'`) untouched. | `test_cross_tenant_insert_violates_with_check`<br>`test_project_cross_tenant_update_isolation`<br>`test_project_cross_tenant_delete_isolation` |
| **TC-RLS-004** | **Tenant A Cannot Update a Row to Tenant B's `tenant_id`** | **WRITE & READ** | Tenant A (`11111111-...`) with `Project A` (`aaaaaaaa-...`, `tenant_id = '11111111-...'`); Tenant B (`22222222-...`). | Set `app.current_tenant_id = '11111111-...'` (Tenant A).<br>1. `UPDATE projects SET tenant_id = '22222222-2222-2222-2222-222222222222' WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';`<br>2. Verify `SELECT id, tenant_id FROM projects` under Tenant A and Tenant B. | • `USING` matches `Project A`, but `WITH CHECK` rejects the mutated row (`tenant_id = '22222222-...' != '11111111-...'`).<br>• Raises `DBAPIError` (`SQLSTATE 42501`: `new row violates row-level security policy for table "projects"`). **0 rows** updated.<br>• Tenant A still sees **1 row** (`aaaaaaaa-...` with `tenant_id = '11111111-...'`); Tenant B sees **0 rows** for `aaaaaaaa-...`. | `test_cannot_update_own_row_to_another_tenants_id` |
| **TC-RLS-005** | **Tenant Setting Cleared or Unset Returns Nothing & Blocks Writes** | **READ & WRITE** | Tenant A (`Project A`, `User A`) and Tenant B (`Project B`, `User B`) seeded. | **Path A (Unset):** Open raw session with `app.current_tenant_id` unset (`NULL`/`''`).<br>**Path B (Cleared):** Set `app.current_tenant_id = '11111111-...'` (reads **1 row**), then clear via `SELECT set_config('app.current_tenant_id', '', true);` or `RESET app.current_tenant_id;`.<br>Execute `SELECT`, `UPDATE`, `DELETE`, and `INSERT` on `tenants`, `projects`, `users`. | • `SELECT * FROM tenants`: **0 rows**.<br>• `SELECT * FROM projects`: **0 rows**.<br>• `SELECT * FROM users`: **0 rows**.<br>• `UPDATE projects ... WHERE id = 'aaaaaaaa-...'`: **0 rows affected** (`rowcount = 0`).<br>• `DELETE FROM projects WHERE id = 'aaaaaaaa-...'`: **0 rows affected** (`rowcount = 0`).<br>• `INSERT INTO projects ...`: **Rejected** (`SQLSTATE 42501`); **0 rows** inserted. | `test_unauthenticated_session_returns_zero_rows`<br>`test_cleared_tenant_setting_returns_zero_rows_and_blocks_writes` |
| **TC-RLS-006** | **Tenant Setting Changed Mid-Session (`Tenant A` $\rightarrow$ `Tenant B` & Txn Reset)** | **READ & WRITE** | Tenant A (`Project A`, `User A`) and Tenant B (`Project B`, `User B`) seeded. | On the **same** database connection/session:<br>1. Set `app.current_tenant_id = '11111111-...'`; `SELECT` and `UPDATE` `Project A`.<br>2. Mid-session, execute `SELECT set_config('app.current_tenant_id', '22222222-...', true);`.<br>3. `SELECT * FROM tenants` and `SELECT * FROM projects`.<br>4. Attempt `UPDATE`/`DELETE` on `Project A`; execute `UPDATE` on `Project B`.<br>5. End transaction (`COMMIT`) and open next transaction on same connection without setting context. | • **Phase 1 (Tenant A):** `SELECT` returns **1 row** (`Project A`, `11111111-...`); `UPDATE` on `Project A` affects **1 row**.<br>• **Phase 2 (Switched to Tenant B mid-session):** `SELECT` immediately returns **1 row** (`Project B`, `22222222-...`) and **0 rows** from Tenant A; `UPDATE`/`DELETE` on `Project A` affect **0 rows**; `UPDATE` on `Project B` affects **1 row**.<br>• **Phase 3 (Next txn after `COMMIT`):** `app.current_tenant_id` resets to `''` and `SELECT` returns **0 rows**. | `test_tenant_setting_changed_mid_session_switches_isolation_boundary`<br>`test_transaction_local_scoping_prevents_pool_pollution` |

---

## 4. Detailed Scenario Specifications (Seed Data, Actions & Expected Rows)

### Scenario 1 (`TC-RLS-001`): Tenant A Reads and Writes Own Data
- **Objective:** Verify that when `app.current_tenant_id` is set to Tenant A's UUID, Tenant A can read its own rows across `tenants`, `projects`, and `users` and can insert/update its own rows without seeing or affecting Tenant B's data.
- **Seed Data:**
  - `tenants`: Tenant A (`11111111-1111-1111-1111-111111111111`, `'Tenant Alpha Corp'`) and Tenant B (`22222222-2222-2222-2222-222222222222`, `'Tenant Beta Ltd'`).
  - `projects`: `Project A` (`id = aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`, `tenant_id = 11111111-...`, `name = 'Project Alpha'`) and `Project B` (`id = bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb`, `tenant_id = 22222222-...`, `name = 'Project Beta'`).
  - `users`: `Admin A` + `User A` (`a1a1a1a1-...`, `tenant_id = 11111111-...`) and `Admin B` + `User B` (`b2b2b2b2-...`, `tenant_id = 22222222-...`).
- **Actions (Executed as `eng_app_user`):**
  ```sql
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);

  -- 1. Read path (unqualified SELECTs relying purely on PostgreSQL RLS)
  SELECT id, slug, name FROM tenants;
  SELECT id, tenant_id, slug, name FROM projects;
  SELECT id, tenant_id, email, role FROM users ORDER BY email;

  -- 2. Write path (UPDATE own project row)
  UPDATE projects
     SET wip_limit_per_dev = 4
   WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
  RETURNING id, tenant_id, name, wip_limit_per_dev;
  COMMIT;
  ```
- **Expected Rows:**
  - `SELECT id, slug, name FROM tenants;` $\rightarrow$ **1 row**:
    | `id` | `slug` | `name` |
    | :--- | :--- | :--- |
    | `11111111-1111-1111-1111-111111111111` | `tenant-alpha-rls` | `Tenant Alpha Corp` |
  - `SELECT id, tenant_id, slug, name FROM projects;` $\rightarrow$ **1 row**:
    | `id` | `tenant_id` | `slug` | `name` |
    | :--- | :--- | :--- | :--- |
    | `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa` | `11111111-1111-1111-1111-111111111111` | `project-alpha` | `Project Alpha` |
  - `SELECT id, tenant_id, email, role FROM users ORDER BY email;` $\rightarrow$ **2 rows** (only Tenant A users; `0` Tenant B users):
    | `id` | `tenant_id` | `email` | `role` |
    | :--- | :--- | :--- | :--- |
    | `<admin_a_uuid>` | `11111111-1111-1111-1111-111111111111` | `admin@alpha-rls.com` | `TENANT_ADMIN` |
    | `a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1` | `11111111-1111-1111-1111-111111111111` | `alpha_dev@tenanta.com` | `DEVELOPER` |
  - `UPDATE projects ... RETURNING ...` $\rightarrow$ **`UPDATE 1` (1 row returned)**: `('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Project Alpha', 4)`.

---

### Scenario 2 (`TC-RLS-002`): Tenant A Cannot Read Tenant B's Rows
- **Objective:** Verify that when operating under Tenant A's context, explicit queries targeting Tenant B's primary keys or `tenant_id` return `0` rows.
- **Seed Data:** Same as Scenario 1 (`Tenant A`, `Tenant B`, `Project A`, `Project B`, `User A`, `User B`).
- **Actions (Executed as `eng_app_user`):**
  ```sql
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);

  -- Direct lookups for Tenant B records while in Tenant A session
  SELECT id, slug, name FROM tenants WHERE id = '22222222-2222-2222-2222-222222222222';
  SELECT id, tenant_id, name FROM projects WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  SELECT id, tenant_id, name FROM projects WHERE tenant_id = '22222222-2222-2222-2222-222222222222';
  SELECT id, tenant_id, email FROM users WHERE id = 'b2b2b2b2-b2b2-b2b2-b2b2-b2b2b2b2b2b2';
  COMMIT;
  ```
- **Expected Rows:**
  - `tenants` query: **0 rows** (`(0 rows)`).
  - `projects` by `id = 'bbbbbbbb-...'`: **0 rows** (`(0 rows)`).
  - `projects` by `tenant_id = '22222222-...'`: **0 rows** (`(0 rows)`).
  - `users` by `id = 'b2b2b2b2-...'`: **0 rows** (`(0 rows)`).

---

### Scenario 3 (`TC-RLS-003`): Tenant A Cannot Write (`INSERT`, `UPDATE`, `DELETE`) Tenant B's Rows
- **Objective:** Verify that Tenant A cannot insert rows under Tenant B's `tenant_id`, cannot update Tenant B's existing rows, and cannot delete Tenant B's existing rows.
- **Seed Data:**
  - Tenant A (`11111111-1111-1111-1111-111111111111`).
  - Tenant B (`22222222-2222-2222-2222-222222222222`) with `Project B` (`id = bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb`, `tenant_id = 22222222-...`, `name = 'Original Beta Name'`).
- **Actions (Executed as `eng_app_user`):**
  ```sql
  -- 1. Cross-Tenant INSERT attempt under Tenant A context
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);
  INSERT INTO projects (
      id, tenant_id, name, slug, tier_sla_hours,
      authoritative_cd_signal, lead_time_first_commit_strategy,
      wip_limit_per_dev, pr_stale_threshold_hours, is_active
  ) VALUES (
      'cccccccc-cccc-cccc-cccc-cccccccccccc',
      '22222222-2222-2222-2222-222222222222', -- Tenant B's tenant_id!
      'Illegal Cross-Tenant Project',
      'illegal-proj', 24, 'github_deployments_api', 'feature_branch_root', 3, 48, true
  );
  ROLLBACK;

  -- 2. Cross-Tenant UPDATE & DELETE attempts under Tenant A context
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);

  UPDATE projects
     SET name = 'Hacked by Tenant A'
   WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';

  DELETE FROM projects
   WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  COMMIT;

  -- 3. Verification Read under Tenant B context
  BEGIN;
  SELECT set_config('app.current_tenant_id', '22222222-2222-2222-2222-222222222222', true);
  SELECT id, tenant_id, name FROM projects;
  COMMIT;
  ```
- **Expected Rows:**
  - **Action 1 (`INSERT` with `tenant_id = TENANT_B_ID`):** Fails immediately with `ERROR: new row violates row-level security policy for table "projects"` (`SQLSTATE 42501`). **0 rows** inserted.
  - **Action 2 (`UPDATE` targeting `Project B`):** Completes with **`UPDATE 0`** (`rowcount = 0`, **0 rows** updated).
  - **Action 2 (`DELETE` targeting `Project B`):** Completes with **`DELETE 0`** (`rowcount = 0`, **0 rows** deleted).
  - **Action 3 (Verification `SELECT` as Tenant B):** Returns **1 row** proving `Project B` is intact and no forged row was inserted:
    | `id` | `tenant_id` | `name` |
    | :--- | :--- | :--- |
    | `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb` | `22222222-2222-2222-2222-222222222222` | `Original Beta Name` |

---

### Scenario 4 (`TC-RLS-004`): Tenant A Cannot Update a Row to Tenant B's `tenant_id`
- **Objective:** Verify that Tenant A cannot re-assign ownership of its own row (`Project A`) by updating `tenant_id` from `TENANT_A_ID` to `TENANT_B_ID`.
- **Seed Data:**
  - Tenant A (`11111111-1111-1111-1111-111111111111`) with `Project A` (`id = aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`, `tenant_id = 11111111-1111-1111-1111-111111111111`, `name = 'Project Alpha'`).
  - Tenant B (`22222222-2222-2222-2222-222222222222`).
- **Actions (Executed as `eng_app_user`):**
  ```sql
  -- 1. Tenant A attempts to mutate its own row's tenant_id to Tenant B
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);

  UPDATE projects
     SET tenant_id = '22222222-2222-2222-2222-222222222222'
   WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  ROLLBACK;

  -- 2. Verify row ownership under Tenant A and Tenant B
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);
  SELECT id, tenant_id, name FROM projects WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  COMMIT;

  BEGIN;
  SELECT set_config('app.current_tenant_id', '22222222-2222-2222-2222-222222222222', true);
  SELECT id, tenant_id, name FROM projects WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  COMMIT;
  ```
- **Expected Rows:**
  - **Action 1 (`UPDATE projects SET tenant_id = TENANT_B_ID`):** Although `Project A` matches the `USING` clause (existing `tenant_id` is `TENANT_A_ID`), the resulting tuple violates `WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)`. PostgreSQL aborts the statement with `ERROR: new row violates row-level security policy for table "projects"` (`SQLSTATE 42501`). **0 rows** updated.
  - **Action 2 (Verification as Tenant A):** Returns **1 row** (`id = aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`, `tenant_id = 11111111-1111-1111-1111-111111111111`, `name = 'Project Alpha'`).
  - **Action 2 (Verification as Tenant B):** Returns **0 rows** (`(0 rows)`).

---

### Scenario 5 (`TC-RLS-005`): Tenant Setting Cleared or Unset Returns Nothing and Blocks Writes
- **Objective:** Verify that when `app.current_tenant_id` is never set (`NULL`), explicitly cleared to an empty string (`set_config('app.current_tenant_id', '', true)`), or reset (`RESET app.current_tenant_id`), the database fails closed: all read queries return `0` rows and all write operations affect `0` rows or fail `WITH CHECK`.
- **Seed Data:**
  - Tenant A (`11111111-...`) with `Project A` (`aaaaaaaa-...`, `'Project Alpha'`) and `User A` (`a1a1a1a1-...`).
  - Tenant B (`22222222-...`) with `Project B` (`bbbbbbbb-...`, `'Project Beta'`) and `User B` (`b2b2b2b2-...`).
- **Actions (Executed as `eng_app_user`):**
  ```sql
  -- Sub-case 5A: Unset setting on a fresh session
  BEGIN;
  SELECT current_setting('app.current_tenant_id', true) AS current_tid;
  SELECT id, name FROM tenants;
  SELECT id, tenant_id, name FROM projects;
  SELECT id, tenant_id, email FROM users;
  COMMIT;

  -- Sub-case 5B: Setting active, then explicitly cleared via set_config(..., '', true) and RESET
  BEGIN;
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);
  SELECT id, name FROM projects; -- Returns 1 row (Project A)

  -- Clear setting to empty string
  SELECT set_config('app.current_tenant_id', '', true);
  SELECT id, name FROM tenants;
  SELECT id, tenant_id, name FROM projects;
  SELECT id, tenant_id, email FROM users;

  -- Attempt UPDATE and DELETE while setting is cleared
  UPDATE projects SET name = 'Mutated While Cleared' WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  DELETE FROM projects WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

  -- Re-set and then clear via RESET
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);
  RESET app.current_tenant_id;
  SELECT id, tenant_id, name FROM projects;
  COMMIT;

  -- Sub-case 5C: Attempt INSERT while setting is cleared/unset
  BEGIN;
  SELECT set_config('app.current_tenant_id', '', true);
  INSERT INTO projects (
      id, tenant_id, name, slug, tier_sla_hours,
      authoritative_cd_signal, lead_time_first_commit_strategy,
      wip_limit_per_dev, pr_stale_threshold_hours, is_active
  ) VALUES (
      'dddddddd-dddd-dddd-dddd-dddddddddddd',
      '11111111-1111-1111-1111-111111111111',
      'Unauthenticated Project', 'unauth-proj', 24,
      'github_deployments_api', 'feature_branch_root', 3, 48, true
  );
  ROLLBACK;
  ```
- **Expected Rows:**
  - **Sub-case 5A (Unset):** `current_tid` is `NULL` or `''`; `SELECT` on `tenants`, `projects`, and `users` each return **0 rows** (`(0 rows)`).
  - **Sub-case 5B (Cleared via `set_config(..., '', true)` and `RESET`):**
    - Before clearing: `SELECT id, name FROM projects` returns **1 row** (`Project A`).
    - After `set_config('app.current_tenant_id', '', true)`: `SELECT` on `tenants`, `projects`, and `users` each return **0 rows** (`(0 rows)`).
    - `UPDATE projects ...` returns **`UPDATE 0`** (**0 rows** affected).
    - `DELETE FROM projects ...` returns **`DELETE 0`** (**0 rows** affected).
    - After `RESET app.current_tenant_id`: `SELECT id, tenant_id, name FROM projects` returns **0 rows** (`(0 rows)`).
  - **Sub-case 5C (`INSERT` while cleared):** Rejected by `WITH CHECK` with `ERROR: new row violates row-level security policy for table "projects"` (`SQLSTATE 42501`). **0 rows** inserted.

---

### Scenario 6 (`TC-RLS-006`): Tenant Setting Changed Mid-Session (`Tenant A` $\rightarrow$ `Tenant B` & Transaction Reset)
- **Objective:** Verify that when `app.current_tenant_id` is changed mid-session on the same database connection from Tenant A to Tenant B, the RLS isolation boundary immediately switches on both read and write paths without leaking prior tenant access, and automatically resets to empty when the transaction finishes.
- **Seed Data:**
  - Tenant A (`11111111-1111-1111-1111-111111111111`, `'Tenant Alpha Corp'`) with `Project A` (`aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`, `'Project Alpha'`) and `User A` (`a1a1a1a1-...`, `'alpha_dev@tenanta.com'`).
  - Tenant B (`22222222-2222-2222-2222-222222222222`, `'Tenant Beta Ltd'`) with `Project B` (`bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb`, `'Project Beta'`) and `User B` (`b2b2b2b2-...`, `'beta_dev@tenantb.com'`).
- **Actions (Executed as `eng_app_user` on a single connection):**
  ```sql
  BEGIN;
  -- Phase 1: Session starts as Tenant A
  SELECT set_config('app.current_tenant_id', '11111111-1111-1111-1111-111111111111', true);
  SELECT id, name FROM tenants;
  SELECT id, tenant_id, name FROM projects;
  UPDATE projects SET name = 'Project Alpha Updated' WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

  -- Phase 2: Change setting mid-session to Tenant B on the SAME connection
  SELECT set_config('app.current_tenant_id', '22222222-2222-2222-2222-222222222222', true);
  SELECT id, name FROM tenants;
  SELECT id, tenant_id, name FROM projects;

  -- Attempt writes against Tenant A's row after switching to Tenant B
  UPDATE projects SET name = 'Stale Tenant A Write' WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
  DELETE FROM projects WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

  -- Execute valid write against Tenant B's row
  UPDATE projects SET name = 'Project Beta Updated' WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
  RETURNING id, tenant_id, name;
  COMMIT;

  -- Phase 3: New transaction on the same connection without setting tenant_id
  BEGIN;
  SELECT current_setting('app.current_tenant_id', true) AS post_commit_tid;
  SELECT id, tenant_id, name FROM projects;
  COMMIT;
  ```
- **Expected Rows:**
  - **Phase 1 (Tenant A active):**
    - `SELECT id, name FROM tenants;` $\rightarrow$ **1 row**: `('11111111-1111-1111-1111-111111111111', 'Tenant Alpha Corp')`.
    - `SELECT id, tenant_id, name FROM projects;` $\rightarrow$ **1 row**: `('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-...', 'Project Alpha')`.
    - `UPDATE projects ... WHERE id = 'aaaaaaaa-...'` $\rightarrow$ **`UPDATE 1`** (**1 row** updated).
  - **Phase 2 (Mid-session switch to Tenant B):**
    - `SELECT id, name FROM tenants;` $\rightarrow$ **1 row**: `('22222222-2222-2222-2222-222222222222', 'Tenant Beta Ltd')` (`Tenant A` row is no longer visible).
    - `SELECT id, tenant_id, name FROM projects;` $\rightarrow$ **1 row**: `('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-...', 'Project Beta')` (`Project A` is no longer visible).
    - `UPDATE` and `DELETE` targeting `Project A` (`aaaaaaaa-...`) $\rightarrow$ **`UPDATE 0`** and **`DELETE 0`** (**0 rows** affected).
    - `UPDATE` targeting `Project B` (`bbbbbbbb-...`) $\rightarrow$ **`UPDATE 1` (1 row returned)**: `('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'Project Beta Updated')`.
  - **Phase 3 (Post-`COMMIT` new transaction):**
    - `post_commit_tid` is `''` (empty); `SELECT id, tenant_id, name FROM projects;` returns **0 rows** (`(0 rows)`).

---

## 5. Running the Automated Verification Suite

### Locally Against PostgreSQL 16
```bash
# 1. Start PostgreSQL 16 service and apply migrations to head
docker compose -f backend/docker-compose.yml up -d --wait postgres
python backend/scripts/verify_migrations.py --step upgrade

# 2. Execute RLS isolation and schema guardrail test suites
python -m pytest backend/tests/test_rls_isolation.py backend/tests/test_rls_guardrails.py -v
```

### In GitHub Actions CI (`.github/workflows/ci.yml`)
Every pull request and push runs `backend/tests/test_rls_isolation.py` and `backend/tests/test_rls_guardrails.py` inside the `backend-test` job using the non-superuser `eng_app_user` connection (`DATABASE_URL=postgresql+asyncpg://eng_app_user:...@localhost:5432/manta_db`), gating merges via `ci-gate`.
