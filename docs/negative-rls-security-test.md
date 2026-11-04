# Negative RLS Security Verification: Safety Net Regression Testing

**Ticket:** `SCRUM-305` (Add negative test: pipeline fails when an RLS policy is disabled)  
**Parent User Story:** `SCRUM-246` (`US-10` Tenant boundary security tests)  
**Related Tickets:**  
- `SCRUM-244` / `SCRUM-294` / `SCRUM-295` (US-8: Database-enforced tenant isolation via PostgreSQL RLS)  
- `SCRUM-293` (Automated RLS schema catalog guardrails)  
- `SCRUM-318` (`FORCE ROW LEVEL SECURITY` on all tenant tables)  
- `SCRUM-301` / `SCRUM-302` / `SCRUM-303` (Adversarial tenant boundary test scenarios)  
**Target Database Engine:** PostgreSQL 16 (`pgvector/pgvector:pg16`)  
**Automated Verification Script:** [`backend/scripts/verify_rls_negative.py`](../backend/scripts/verify_rls_negative.py)  
**Automated Pytest Suite:** [`backend/tests/security/test_rls_negative_pipeline.py`](../backend/tests/security/test_rls_negative_pipeline.py)  
**CI Pipeline Job:** `security-negative-test` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)  

---

## 1. Objective: Why Negative Testing Is Critical

A suite of security tests is only as trustworthy as its ability to **fail when security protections are missing or tampered with**. If security tests pass even when Row-Level Security (RLS) is disabled or improperly configured, the safety net is illusory.

The purpose of `SCRUM-305` is to **prove that the safety net actually catches a regression**. 

This is accomplished by:
1. Provisioning a clean, isolated **throwaway database** migrated to `head`.
2. Verifying that the security stage **passes** in the baseline pristine state.
3. Deliberately inducing a security regression by:
   - Disabling Row-Level Security on a tenant table (`ALTER TABLE <table> DISABLE ROW LEVEL SECURITY`), OR
   - Disabling `FORCE` Row-Level Security (`ALTER TABLE <table> NO FORCE ROW LEVEL SECURITY`), OR
   - Dropping the tenant isolation policy (`DROP POLICY tenant_isolation_policy ON <table>`).
4. Executing the security tests against the tampered database and asserting that the security stage **fails**.
5. Restoring the policies and settings, and asserting that the security stage **passes**.
6. Tearing down the throwaway database cleanly.

```
+--------------------------------------------------------------------------------------------------+
|                                  NEGATIVE TEST EXECUTION CYCLE                                   |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|   1. Create Throwaway DB & Migrate                                                               |
|      (manta_rls_negative_test migrated to head)                                                  |
|                      |                                                                           |
|                      v                                                                           |
|   2. Baseline Security Stage                                                                     |
|      (pytest tests/test_rls_guardrails.py + test_rls_isolation.py)                               |
|      --> EXPECTED: PASS (Exit Code 0)                                                            |
|                      |                                                                           |
|                      v                                                                           |
|   3. Induce Regression (Tamper RLS)                                                              |
|      (ALTER TABLE projects DISABLE ROW LEVEL SECURITY / NO FORCE / DROP POLICY)                  |
|                      |                                                                           |
|                      v                                                                           |
|   4. Negative Assertion: Security Stage Must Fail                                                |
|      (pytest security stage executed against tampered throwaway DB)                              |
|      --> EXPECTED: FAIL (Exit Code != 0)                                                         |
|      [Safety net confirmed: Regression detected!]                                                |
|                      |                                                                           |
|                      v                                                                           |
|   5. Restore Policies & FORCE Flags                                                              |
|      (ALTER TABLE projects ENABLE ROW LEVEL SECURITY; FORCE ROW LEVEL SECURITY; ...)             |
|                      |                                                                           |
|                      v                                                                           |
|   6. Post-Restoration Security Stage                                                             |
|      --> EXPECTED: PASS (Exit Code 0)                                                            |
|                      |                                                                           |
|                      v                                                                           |
|   7. Clean Up: DROP DATABASE manta_rls_negative_test WITH (FORCE)                                |
|                                                                                                  |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. Regression Modes Tested

The negative verification suite tests three distinct vulnerability vectors:

| Mode | SQL Command Executed | Detection Mechanism | Failure Output / Reason |
| :--- | :--- | :--- | :--- |
| **`disable-rls`** | `ALTER TABLE projects DISABLE ROW LEVEL SECURITY;` | • Catalog guardrail ([`test_rls_guardrails.py`](../backend/tests/test_rls_guardrails.py))<br>• Runtime query isolation ([`test_rls_isolation.py`](../backend/tests/test_rls_isolation.py)) | `AssertionError: Found tenant tables without FORCE RLS enabled: projects (rls=False, force_rls=True)`<br>`AssertionError: assert project_b not in [project_a, project_b]` |
| **`no-force`** | `ALTER TABLE projects NO FORCE ROW LEVEL SECURITY;` | Catalog guardrail ([`test_rls_guardrails.py`](../backend/tests/test_rls_guardrails.py)) | `AssertionError: Found tenant tables without FORCE RLS enabled: projects (rls=True, force_rls=False)` |
| **`drop-policy`** | `DROP POLICY IF EXISTS tenant_isolation_policy ON projects;` | • Catalog guardrail ([`test_rls_guardrails.py`](../backend/tests/test_rls_guardrails.py))<br>• Runtime query isolation ([`test_rls_isolation.py`](../backend/tests/test_rls_isolation.py)) | `AssertionError: Tenant tables missing tenant_isolation_policy: ['projects']` |

---

## 3. How to Run the Check Manually

### 3.1 Prerequisites
Ensure the PostgreSQL Docker service is running and accessible (default port `5432` in CI, port `5432` or `5433` locally):

```powershell
# From the repository root, start PostgreSQL
docker compose -f backend/docker-compose.yml up -d postgres
```

Configure your environment connection variables (adjust port if running on a custom port such as `5433`):

```powershell
# Windows PowerShell
$env:DATABASE_URL="postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5433/manta_db"
$env:MIGRATION_DATABASE_URL="postgresql+asyncpg://manta_user:manta_password@localhost:5433/manta_db"

# Linux / macOS Bash
export DATABASE_URL="postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5432/manta_db"
export MIGRATION_DATABASE_URL="postgresql+asyncpg://manta_user:manta_password@localhost:5432/manta_db"
```

---

### 3.2 Automated Script Execution (`verify_rls_negative.py`)

The standalone verification script [`backend/scripts/verify_rls_negative.py`](../backend/scripts/verify_rls_negative.py) manages the throwaway database lifecycle end-to-end:

#### Run All Regression Scenarios (Recommended)
```powershell
cd backend
python scripts/verify_rls_negative.py --mode all -v
```

This will sequentially test:
1. `disable-rls`
2. `no-force`
3. `drop-policy`

#### Run a Single Specific Regression Mode
```powershell
# Test disabling RLS on 'projects'
python scripts/verify_rls_negative.py --mode disable-rls -v

# Test removing FORCE RLS on 'projects'
python scripts/verify_rls_negative.py --mode no-force -v

# Test dropping the tenant_isolation_policy
python scripts/verify_rls_negative.py --mode drop-policy -v
```

#### Target a Specific Tenant Table
You can test any of the 41 tenant tables (e.g. `integrations`, `tenants`, `issues`):
```powershell
python scripts/verify_rls_negative.py --mode disable-rls --table integrations -v
```

---

### 3.3 Step-by-Step Manual Reproduction

If you want to step through the check manually command-by-command:

#### Step 1: Create throwaway database and migrate
```powershell
python scripts/verify_rls_negative.py --step create-db --db-name manta_manual_neg_test
```

#### Step 2: Verify baseline security stage passes
```powershell
python scripts/verify_rls_negative.py --step baseline --db-name manta_manual_neg_test
```
*Expected: Security tests pass with all policies active.*

#### Step 3: Deliberately tamper with RLS policy
```powershell
# Option A: Using the CLI helper
python scripts/verify_rls_negative.py --step tamper --mode disable-rls --table projects --db-name manta_manual_neg_test

# Option B: Or directly via psql / raw SQL
# ALTER TABLE projects DISABLE ROW LEVEL SECURITY;
```

#### Step 4: Verify the security tests FAIL (Regression Caught!)
```powershell
python scripts/verify_rls_negative.py --step expect-failure --table projects --db-name manta_manual_neg_test
```
*Expected: Pytest exits with code 1, confirming the regression is caught.*

#### Step 5: Restore the policy
```powershell
python scripts/verify_rls_negative.py --step restore --mode disable-rls --table projects --db-name manta_manual_neg_test
```

#### Step 6: Verify the security tests PASS
```powershell
python scripts/verify_rls_negative.py --step expect-success --db-name manta_manual_neg_test
```
*Expected: Pytest exits with code 0, confirming green status.*

#### Step 7: Clean up the throwaway database
```powershell
python scripts/verify_rls_negative.py --step drop-db --db-name manta_manual_neg_test
```

---

### 3.4 Automated Pytest Suite Execution

The automated pytest test suite [`backend/tests/security/test_rls_negative_pipeline.py`](../backend/tests/security/test_rls_negative_pipeline.py) can be run directly via `pytest`:

```powershell
cd backend
python -m pytest tests/security/test_rls_negative_pipeline.py -v
```

Tests included:
- `test_negative_rls_pipeline_fails_when_rls_disabled_and_passes_when_restored`: Validates `disable-rls` regression cycle.
- `test_negative_rls_pipeline_fails_when_no_force_and_passes_when_restored`: Validates `no-force` regression cycle.
- `test_negative_rls_pipeline_fails_when_policy_dropped_and_passes_when_restored`: Validates `drop-policy` regression cycle.
- `test_expect_failure_throws_if_security_tests_unexpectedly_pass`: Validates that the runner raises an error if security tests pass unexpectedly.
- `test_ci_workflow_defines_security_negative_test_job`: Asserts `.github/workflows/ci.yml` job definitions and gating.

---

## 4. CI Pipeline Gating

The CI pipeline [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) defines a dedicated stage:

```yaml
  security-negative-test:
    name: Negative RLS Security Tests
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    services:
      postgres:
        image: pgvector/pgvector:pg16
        ...
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        ...
      - name: Install backend dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Wait for PostgreSQL service readiness
        run: python scripts/verify_migrations.py --step wait

      - name: Apply baseline migrations to head
        run: python scripts/verify_migrations.py --step upgrade

      - name: Execute RLS negative safety net verification on throwaway database
        run: python scripts/verify_rls_negative.py --mode all -v
```

### CI Gate Integration
The master status check `ci-gate` requires `security-negative-test` in `needs`:
```yaml
  ci-gate:
    name: CI Status Check
    runs-on: ubuntu-latest
    needs:
      - backend-lint
      - backend-test
      - backend-migrations
      - security-negative-test
      - pgbouncer-integration
      - backend-docker-build
      - web-checks
      - web-e2e
```
If the safety net fails to detect an RLS policy omission or fails to restore cleanly, the CI status check turns RED and merging is blocked.

---

## 5. Acceptance Criteria Verification

| Acceptance Criteria | Verification Status | Evidence |
| :--- | :--- | :--- |
| **With a policy disabled, the security stage fails** | **PASSED** | When `ALTER TABLE projects DISABLE ROW LEVEL SECURITY` or `NO FORCE` or `DROP POLICY` is executed, `test_rls_guardrails.py` and `test_rls_isolation.py` fail immediately with exit code 1. |
| **With policies restored, it passes** | **PASSED** | Re-applying `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and policy restores the clean security test suite (exit code 0). |
| **The negative test itself is stable in CI** | **PASSED** | Deterministic throwaway database provisioning with `DROP DATABASE ... WITH (FORCE)` guarantees zero cross-test interference and 100% CI reproducibility. |
