# Database Schema Reproducibility Test Scenarios

**Task:** Create test scenarios for schema reproducibility  
**Parent User Story:** `SCRUM-238` (US-2 Core database schema and migrations)  
**Related Tickets:** `SCRUM-263` (CI migration verification job), `SCRUM-292` (`eng_app_user` role), `SCRUM-293` / `SCRUM-318` (Row-Level Security policies & guardrails), `SCRUM-288` (`provision_tenant` function)  
**Target Database Engine:** PostgreSQL 16 (`pgvector/pgvector:pg16`)  
**Verification Runner:** [`backend/scripts/verify_migrations.py`](../backend/scripts/verify_migrations.py)  
**CI Workflow Job:** `backend-migrations` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

---

## 1. Objective & Scope

This document defines the verification scenarios, inputs, expected outcomes, schema specification checks, and CI/automated test mappings that guarantee the MANTA PostgreSQL database schema can always be rebuilt identically, reversibly, and idempotently from Alembic migrations.

### Migration Revision Chain
| Order | Revision ID | Down Revision | Migration File | Summary of Schema Changes |
| :--- | :--- | :--- | :--- | :--- |
| **1** | `02ec69277327` | `None` (`base`) | [`02ec69277327_initial_core_schema.py`](../backend/migrations/versions/02ec69277327_initial_core_schema.py) | Enables `vector` extension; creates all **42** core tables, **42** Primary Keys, **105** Foreign Keys, **22** Unique Constraints, and **43** explicit `idx_*` B-tree indexes. |
| **2** | `5b1a7d8e2093` | `02ec69277327` | [`5b1a7d8e2093_tenant_rls_and_eng_app_user.py`](../backend/migrations/versions/5b1a7d8e2093_tenant_rls_and_eng_app_user.py) | Creates least-privilege `eng_app_user` role (`NOSUPERUSER`, `NOBYPASSRLS`); enables `ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` with `tenant_isolation_policy` across all **40** tenant-scoped tables (`tenants` + 39 child tables with `tenant_id`). |
| **3 (`head`)** | `c74f19b28a01` | `5b1a7d8e2093` | [`c74f19b28a01_tenant_provisioning_function.py`](../backend/migrations/versions/c74f19b28a01_tenant_provisioning_function.py) | Seeds **5** standard RBAC roles in `roles`; creates atomic `SECURITY DEFINER` function `provision_tenant(...)`; grants `EXECUTE` to `eng_app_user`; revokes direct `INSERT` on `tenants` from `eng_app_user`. |

---

## 2. Schema Reproducibility Test Scenarios Matrix

| Scenario ID | Scenario Name | Preconditions & Inputs | Command / Execution Trigger | Expected Outcomes | CI Step & Automated Test Mapping |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-MIG-001** | **Empty DB to `head`** *(Initial Forward Build)* | **Precondition:** Fresh PostgreSQL 16 (`pgvector/pgvector:pg16`) instance with an empty `public` schema (`0` tables, no `alembic_version` table).<br>**Input:** Administrative connection (`manta_user`) and migration scripts `02ec69277327` $\rightarrow$ `5b1a7d8e2093` $\rightarrow$ `c74f19b28a01`. | `python backend/scripts/verify_migrations.py --step upgrade`<br>*(Invokes `alembic -c backend/alembic.ini upgrade head`)* | • Process exits with code `0`.<br>• Logs sequential execution:<br>  1. `Running upgrade  -> 02ec69277327, initial_core_schema`<br>  2. `Running upgrade 02ec69277327 -> 5b1a7d8e2093, Tenant RLS Policies and eng_app_user Role`<br>  3. `Running upgrade 5b1a7d8e2093 -> c74f19b28a01, SECURITY DEFINER provisioning function for tenant registration (SCRUM-288)`<br>• `alembic_version.version_num` = `'c74f19b28a01'`.<br>• All 42 tables, 42 PKs, 105 FKs, 22 UQs, 43 explicit indexes, 40 forced RLS policies, `eng_app_user` role, and `provision_tenant` function exist in `public`. | **CI Step:** `.github/workflows/ci.yml` $\rightarrow$ job `backend-migrations` $\rightarrow$ step `Apply migrations to head (upgrade head)`<br>**Automated Tests:**<br>• `TestMigrationsCICheck::test_alembic_offline_upgrade_and_downgrade_sql` (`backend/tests/test_migrations_ci.py`)<br>• `test_all_tenant_id_tables_have_rls_and_force_rls` (`backend/tests/test_rls_guardrails.py`) |
| **TC-MIG-002** | **`head` to `base`** *(Full Reversibility & Clean Teardown)* | **Precondition:** Database migrated to `head` (`c74f19b28a01`) with all 42 tables, indexes, constraints, RLS policies, and functions active.<br>**Input:** Target revision `base`. | `python backend/scripts/verify_migrations.py --step downgrade`<br>*(Invokes `alembic -c backend/alembic.ini downgrade base` + `verify_downgrade_base_clean()`)* | • Process exits with code `0`.<br>• Logs reverse step execution:<br>  1. `Running downgrade c74f19b28a01 -> 5b1a7d8e2093`<br>  2. `Running downgrade 5b1a7d8e2093 -> 02ec69277327`<br>  3. `Running downgrade 02ec69277327 ->`<br>• Drops `provision_tenant`, revokes/drops RLS policies & `eng_app_user`, and drops all 43 explicit indexes and 42 tables in reverse foreign-key topological order without dependency errors.<br>• `verify_downgrade_base_clean()` confirms `alembic_version` contains `0` rows and `information_schema.tables` has `0` leftover application tables:<br>  `[downgrade] Verified public schema is clean (0 leftover application tables).` | **CI Step:** `.github/workflows/ci.yml` $\rightarrow$ job `backend-migrations` $\rightarrow$ step `Revert all migrations to base (downgrade base)`<br>**Automated Tests:**<br>• `TestMigrationsCICheck::test_initial_migration_matches_models_and_is_reversible`<br>• `TestMigrationsCICheck::test_alembic_offline_upgrade_and_downgrade_sql` (`backend/tests/test_migrations_ci.py`) |
| **TC-MIG-003** | **`base` to `head`** *(Deterministic Rebuild After Downgrade)* | **Precondition:** Database at `base` immediately following `downgrade base` (`alembic_version` empty, `0` application tables in `public`).<br>**Input:** Target revision `head` (`c74f19b28a01`). | `python backend/scripts/verify_migrations.py --step reupgrade`<br>*(Invokes `alembic -c backend/alembic.ini upgrade head`)* | • Process exits with code `0`.<br>• Re-applies `02ec69277327` $\rightarrow$ `5b1a7d8e2093` $\rightarrow$ `c74f19b28a01` cleanly with zero object-already-exists errors.<br>• Reconstructed schema matches the initial `upgrade head` state bit-for-bit (`42` tables, `42` PKs, `105` FKs, `22` UQs, `43` `idx_*` indexes, `40` RLS policies, `alembic_version` = `'c74f19b28a01'`). | **CI Step:** `.github/workflows/ci.yml` $\rightarrow$ job `backend-migrations` $\rightarrow$ step `Re-apply migrations to head (upgrade head)`<br>**Automated Tests:**<br>• `TestMigrationsCICheck::test_ci_workflow_migration_job_invariants` (`backend/tests/test_migrations_ci.py`) |
| **TC-MIG-004** | **Repeated `upgrade head`** *(Idempotent No-Op)* | **Precondition:** Database is already at `head` (`c74f19b28a01`).<br>**Input:** Repeated execution of `upgrade head`. | `python backend/scripts/verify_migrations.py --step upgrade`<br>and<br>`python -m alembic -c backend/alembic.ini current -v` | • Process exits with code `0`.<br>• Alembic detects `alembic_version` is already at `'c74f19b28a01'` (`head`) and executes **zero** `Running upgrade ...` steps.<br>• Zero DDL statements are emitted; schema objects, constraints, indexes, and existing data remain untouched.<br>• `alembic current -v` reports `Current revision(s) for postgresql+asyncpg://...:\nRev: c74f19b28a01 (head)`. | **CI / CLI Runner:** `verify_migrations.py --step upgrade` (idempotent when invoked on an already-migrated DB)<br>**Automated Tests:**<br>• `TestMigrationsCICheck::test_initial_migration_matches_models_and_is_reversible` (`backend/tests/test_migrations_ci.py`) |
| **TC-MIG-005** | **Schema Specification & Model Drift Verification** *(Tables, Constraints, Indexes, RLS)* | **Precondition:** Database at `head` (`c74f19b28a01`); SQLAlchemy declarative models loaded from `app.db.models.Base.metadata`.<br>**Input:** Live PostgreSQL catalog & SQLAlchemy `Base.metadata`. | `python backend/scripts/verify_migrations.py --step check`<br>and<br>`python -m pytest backend/tests/test_migrations_ci.py backend/tests/test_rls_guardrails.py -v` | • `alembic check` exits with code `0` and outputs: `No new upgrade operations detected.`<br>• PostgreSQL catalog counts and AST checks match the exact specification:<br>  - `app_tables` = `42`<br>  - `pk_constraints` = `42`<br>  - `fk_constraints` = `105`<br>  - `uq_constraints` = `22`<br>  - `explicit_indexes` (`idx_*`) = `43`<br>  - `rls_policies` (`tenant_isolation_policy`) = `40` | **CI Step:** `.github/workflows/ci.yml` $\rightarrow$ job `backend-migrations` $\rightarrow$ step `Check for model/schema drift (alembic check)`<br>**Automated Tests:**<br>• `TestMigrationsCICheck::test_initial_migration_matches_models_and_is_reversible`<br>• `TestMigrationsCICheck::test_schema_drift_check_fails_when_autogenerate_detects_diffs`<br>• `test_rls_guardrails.py` (4 guardrail tests) |
| **TC-MIG-006** | **Broken Migration / Unmigrated Model Drift Detection** *(Negative Guardrail)* | **Precondition:** A migration raises an error in `upgrade()` / `downgrade()`, OR a developer modifies `models.py` without generating a migration.<br>**Input:** Fault-injected migration or drifted ORM metadata. | Automated fault-injection in `pytest` (`test_migrations_ci.py`) and CI gate (`ci-gate`). | • Process fails with non-zero exit code (`1`).<br>• `_RevisionStepTracker` (`backend/migrations/env.py`) logs the exact failing revision ID:<br>  `ERROR [alembic.env] Migration failed during <upgrade\|downgrade> at revision: <rev_id>`<br>• Emits GitHub Actions annotation `::error title=Migration Check Failed...` and blocks PR merge via `ci-gate`. | **CI Step:** `.github/workflows/ci.yml` $\rightarrow$ job `backend-migrations` & `ci-gate`<br>**Automated Tests:**<br>• `test_deliberately_broken_upgrade_fails_and_prints_revision`<br>• `test_deliberately_broken_downgrade_fails_and_prints_revision`<br>• `test_schema_drift_check_fails_when_autogenerate_detects_diffs` |

---

## 3. Specification Verification Checks (Tables, Constraints, Indexes & RLS)

Scope item #2 requires verifying that all tables, constraints, and indexes built by the migration chain match the platform specification.

### 3.1 Summary of Expected Schema Objects at `head` (`c74f19b28a01`)

| Schema Object Category | Expected Count | Verification Mechanism | Specification Details |
| :--- | :---: | :--- | :--- |
| **Application Base Tables** | **42** | `information_schema.tables` & `test_migrations_ci.py` | **39** tables with `tenant_id` column + **`tenants`** (tenant root table) + **`roles`** (global RBAC lookup) + **`processed_outbox_events`** (global event deduplication). Excludes `alembic_version`. |
| **Primary Key Constraints (`PRIMARY KEY`)** | **42** | `information_schema.table_constraints` & `test_migrations_ci.py` | **38** single-column primary keys (`id`, `sha`, `job_id`) + **4** composite primary keys: `anonymized_adrs(tenant_id, id)`, `contributor_pseudonym_salts(tenant_id, user_id)`, `processed_outbox_events(outbox_event_id, consumer_name)`, and `user_roles(user_id, role_id)`. |
| **Foreign Key Constraints (`FOREIGN KEY`)** | **105** | `information_schema.table_constraints` & `test_migrations_ci.py` | Enforces referential integrity across tenant, user, project, repository, issue, pull request, deployment, and webhook hierarchies with explicit `ON DELETE CASCADE` / `ON DELETE SET NULL` behaviors. |
| **Unique Constraints (`UNIQUE`)** | **22** | `information_schema.table_constraints` & `test_migrations_ci.py` | **19** named composite unique constraints (`uq_*`) + **3** single-column unique constraints (`tenants.slug`, `user_sessions.refresh_token_hash`, `contributor_pseudonym_salts.pseudonym_hash`). |
| **Explicit Secondary Indexes (`idx_*`)** | **43** | `pg_indexes` & `test_migrations_ci.py` | **39** `idx_<table_name>_tenant_id` indexes on every table with `tenant_id` + **4** specialized lookup indexes: `idx_users_tenant_role`, `idx_users_github_handle`, `idx_user_identities_user`, and `idx_user_sessions_lookup`. |
| **Forced RLS Tables & Isolation Policies** | **40** | `pg_class`, `pg_policies` & `test_rls_guardrails.py` | All **39** `tenant_id` tables + **`tenants`** have `relrowsecurity = true`, `relforcerowsecurity = true`, and a `PERMISSIVE FOR ALL TO eng_app_user` policy `tenant_isolation_policy` bound to `current_setting('app.current_tenant_id', true)::uuid`. |
| **Database Extensions & Security Functions** | **1 ext / 1 fn / 5 roles** | `pg_extension`, `pg_proc`, `roles` table | `vector` extension enabled; `provision_tenant(...)` `SECURITY DEFINER` function with `search_path = public, pg_temp`; 5 seeded RBAC roles (`TENANT_ADMIN`, `DEVELOPER`, `SCRUM_MASTER`, `SOLUTION_ARCHITECT`, `DELIVERY_EXEC`). |

### 3.2 Complete Catalogue of Named Unique Constraints (22) & Explicit Indexes (43)

| Table Name | Primary Key | Unique Constraints (`22` Total) | Explicit Indexes (`43` Total) | RLS Policy (`40` Total) |
| :--- | :--- | :--- | :--- | :--- |
| `tenants` | `(id)` | `UNIQUE (slug)` | — | `tenant_isolation_policy` (`id`) |
| `roles` | `(id)` | — | — | *Global exempt* |
| `processed_outbox_events` | `(outbox_event_id, consumer_name)` | — | — | *Global exempt* |
| `users` | `(id)` | `uq_tenant_user_email (tenant_id, email)`,<br>`uq_tenant_google_sub (tenant_id, google_sub)` | `idx_users_tenant_id`,<br>`idx_users_tenant_role`,<br>`idx_users_github_handle` | `tenant_isolation_policy` |
| `user_identities` | `(id)` | `uq_tenant_google_identity (tenant_id, issuer_subject)` | `idx_user_identities_tenant_id`,<br>`idx_user_identities_user` | `tenant_isolation_policy` |
| `user_sessions` | `(id)` | `UNIQUE (refresh_token_hash)` | `idx_user_sessions_tenant_id`,<br>`idx_user_sessions_lookup` | `tenant_isolation_policy` |
| `user_roles` | `(user_id, role_id)` | — | `idx_user_roles_tenant_id` | `tenant_isolation_policy` |
| `contributor_pseudonym_salts` | `(tenant_id, user_id)` | `UNIQUE (pseudonym_hash)` | `idx_contributor_pseudonym_salts_tenant_id` | `tenant_isolation_policy` |
| `projects` | `(id)` | `uq_projects_tenant_slug (tenant_id, slug)` | `idx_projects_tenant_id` | `tenant_isolation_policy` |
| `project_members` | `(id)` | `uq_project_member (project_id, user_id)` | `idx_project_members_tenant_id` | `tenant_isolation_policy` |
| `project_links` | `(id)` | `uq_project_link (project_id, link_type, external_id)` | `idx_project_links_tenant_id` | `tenant_isolation_policy` |
| `integrations` | `(id)` | `uq_tenant_provider_acc (tenant_id, provider, external_account_id)` | `idx_integrations_tenant_id` | `tenant_isolation_policy` |
| `webhook_events` | `(id)` | `uq_webhook_provider_delivery (provider, provider_delivery_id)` | `idx_webhook_events_tenant_id` | `tenant_isolation_policy` |
| `dead_letter_events` | `(id)` | — | `idx_dead_letter_events_tenant_id` | `tenant_isolation_policy` |
| `outbox_events` | `(id)` | — | `idx_outbox_events_tenant_id` | `tenant_isolation_policy` |
| `job_leases` | `(job_id)` | — | `idx_job_leases_tenant_id` | `tenant_isolation_policy` |
| `repositories` | `(id)` | `uq_repo_external (tenant_id, external_repo_id)` | `idx_repositories_tenant_id` | `tenant_isolation_policy` |
| `commits` | `(sha)` | — | `idx_commits_tenant_id` | `tenant_isolation_policy` |
| `pull_requests` | `(id)` | `uq_repo_pr_number (repository_id, external_pr_number)` | `idx_pull_requests_tenant_id` | `tenant_isolation_policy` |
| `pull_request_reviews` | `(id)` | — | `idx_pull_request_reviews_tenant_id` | `tenant_isolation_policy` |
| `sprints` | `(id)` | `uq_project_sprint (project_id, external_sprint_id)` | `idx_sprints_tenant_id` | `tenant_isolation_policy` |
| `issues` | `(id)` | `uq_tenant_issue_key (tenant_id, external_key)` | `idx_issues_tenant_id` | `tenant_isolation_policy` |
| `issue_transitions` | `(id)` | — | `idx_issue_transitions_tenant_id` | `tenant_isolation_policy` |
| `acceptance_criteria_versions` | `(id)` | `uq_issue_ac_version (issue_id, version)` | `idx_acceptance_criteria_versions_tenant_id` | `tenant_isolation_policy` |
| `requirement_changes` | `(id)` | — | `idx_requirement_changes_tenant_id` | `tenant_isolation_policy` |
| `wip_limits` | `(id)` | `uq_wip_project_status (project_id, status)` | `idx_wip_limits_tenant_id` | `tenant_isolation_policy` |
| `risks` | `(id)` | — | `idx_risks_tenant_id` | `tenant_isolation_policy` |
| `communication_events` | `(id)` | `uq_communication_thread (project_id, channel_id, thread_ts)` | `idx_communication_events_tenant_id` | `tenant_isolation_policy` |
| `deployments` | `(id)` | — | `idx_deployments_tenant_id` | `tenant_isolation_policy` |
| `deployment_pull_requests` | `(id)` | `uq_deployment_pr (deployment_id, pull_request_id)` | `idx_deployment_pull_requests_tenant_id` | `tenant_isolation_policy` |
| `incidents` | `(id)` | — | `idx_incidents_tenant_id` | `tenant_isolation_policy` |
| `metric_snapshots` | `(id)` | `uq_metric_snapshot (project_id, metric, granularity, period_start)` | `idx_metric_snapshots_tenant_id` | `tenant_isolation_policy` |
| `scip_indexes` | `(id)` | `uq_repo_commit_scip (repository_id, commit_sha)` | `idx_scip_indexes_tenant_id` | `tenant_isolation_policy` |
| `code_analyses` | `(id)` | — | `idx_code_analyses_tenant_id` | `tenant_isolation_policy` |
| `refactoring_suggestions` | `(id)` | — | `idx_refactoring_suggestions_tenant_id` | `tenant_isolation_policy` |
| `test_runs` | `(id)` | — | `idx_test_runs_tenant_id` | `tenant_isolation_policy` |
| `test_results` | `(id)` | — | `idx_test_results_tenant_id` | `tenant_isolation_policy` |
| `flaky_tests` | `(id)` | `uq_repo_flaky_test (repository_id, test_name)` | `idx_flaky_tests_tenant_id` | `tenant_isolation_policy` |
| `anonymized_adrs` | `(tenant_id, id)` | — | `idx_anonymized_adrs_tenant_id` | `tenant_isolation_policy` |
| `adr_sanitization_logs` | `(id)` | — | `idx_adr_sanitization_logs_tenant_id` | `tenant_isolation_policy` |
| `notifications` | `(id)` | — | `idx_notifications_tenant_id` | `tenant_isolation_policy` |
| `audit_log` | `(id)` | — | `idx_audit_log_tenant_id` | `tenant_isolation_policy` |

---

## 4. Step-by-Step Verification Commands & Recorded Execution Outputs

### Step 1: Clean PostgreSQL Container Setup & Readiness (`--step wait`)
```powershell
$env:POSTGRES_PORT = "5433"
$env:MIGRATION_DATABASE_URL = "postgresql+asyncpg://manta_user:manta_password@127.0.0.1:5433/manta_db"
$env:DATABASE_URL = "postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@127.0.0.1:5433/manta_db"

docker compose -f backend/docker-compose.yml up -d --wait postgres
docker exec manta_postgres psql -U manta_user -d manta_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
python backend/scripts/verify_migrations.py --step wait
```
**Expected & Verified Output:**
```text
DROP SCHEMA
CREATE SCHEMA
[wait] PostgreSQL is ready (attempt 1/30).
```

### Step 2: Scenario TC-MIG-001 — Empty DB to `head` (`--step upgrade`)
```powershell
python backend/scripts/verify_migrations.py --step upgrade
```
**Expected & Verified Output:**
```text
==> [upgrade head (initial)] Running: alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 02ec69277327, initial_core_schema
INFO  [alembic.runtime.migration] Running upgrade 02ec69277327 -> 5b1a7d8e2093, Tenant RLS Policies and eng_app_user Role
INFO  [alembic.runtime.migration] Running upgrade 5b1a7d8e2093 -> c74f19b28a01, SECURITY DEFINER provisioning function for tenant registration (SCRUM-288)
==> [upgrade head (initial)] Passed (alembic upgrade head).
```

### Step 3: Scenario TC-MIG-002 — `head` to `base` (`--step downgrade`)
```powershell
python backend/scripts/verify_migrations.py --step downgrade
```
**Expected & Verified Output:**
```text
==> [downgrade base] Running: alembic downgrade base
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running downgrade c74f19b28a01 -> 5b1a7d8e2093, SECURITY DEFINER provisioning function for tenant registration (SCRUM-288)
INFO  [alembic.runtime.migration] Running downgrade 5b1a7d8e2093 -> 02ec69277327, Tenant RLS Policies and eng_app_user Role
INFO  [alembic.runtime.migration] Running downgrade 02ec69277327 -> , initial_core_schema
==> [downgrade base] Passed (alembic downgrade base).
[downgrade] Verified public schema is clean (0 leftover application tables).
```

### Step 4: Scenario TC-MIG-003 — `base` to `head` (`--step reupgrade`)
```powershell
python backend/scripts/verify_migrations.py --step reupgrade
```
**Expected & Verified Output:**
```text
==> [upgrade head (re-apply)] Running: alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 02ec69277327, initial_core_schema
INFO  [alembic.runtime.migration] Running upgrade 02ec69277327 -> 5b1a7d8e2093, Tenant RLS Policies and eng_app_user Role
INFO  [alembic.runtime.migration] Running upgrade 5b1a7d8e2093 -> c74f19b28a01, SECURITY DEFINER provisioning function for tenant registration (SCRUM-288)
==> [upgrade head (re-apply)] Passed (alembic upgrade head).
```

### Step 5: Scenario TC-MIG-004 — Repeated `upgrade head` (Idempotent No-Op)
```powershell
python backend/scripts/verify_migrations.py --step upgrade
python -m alembic -c backend/alembic.ini current -v
```
**Expected & Verified Output:**
```text
==> [upgrade head (initial)] Running: alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
==> [upgrade head (initial)] Passed (alembic upgrade head).

INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
Current revision(s) for postgresql+asyncpg://manta_user:***@127.0.0.1:5433/manta_db:
Rev: c74f19b28a01 (head)
Parent: 5b1a7d8e2093
Path: .../backend/migrations/versions/c74f19b28a01_tenant_provisioning_function.py
    SECURITY DEFINER provisioning function for tenant registration (SCRUM-288)
```

### Step 6: Scenario TC-MIG-005 — Drift Detection, Catalog Inspection & Automated Pytest Suite
```powershell
python backend/scripts/verify_migrations.py --step check

docker exec manta_postgres psql -U manta_user -d manta_db -c "
SELECT
  (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name != 'alembic_version') AS app_tables,
  (SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema = 'public' AND constraint_type = 'PRIMARY KEY' AND table_name != 'alembic_version') AS pk_constraints,
  (SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema = 'public' AND constraint_type = 'FOREIGN KEY') AS fk_constraints,
  (SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema = 'public' AND constraint_type = 'UNIQUE') AS uq_constraints,
  (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname LIKE 'idx_%') AS explicit_indexes,
  (SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND policyname = 'tenant_isolation_policy') AS rls_policies;
"

python -m pytest backend/tests/test_migrations_ci.py backend/tests/test_rls_guardrails.py -v
```
**Expected & Verified Output:**
```text
==> [schema drift check] Running: alembic check
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
No new upgrade operations detected.
==> [schema drift check] Passed (alembic check).

 app_tables | pk_constraints | fk_constraints | uq_constraints | explicit_indexes | rls_policies
------------+----------------+----------------+----------------+------------------+--------------
         42 |             42 |            105 |             22 |               43 |           40
(1 row)

backend/tests/test_migrations_ci.py::TestMigrationsCICheck::test_alembic_offline_upgrade_and_downgrade_sql PASSED
backend/tests/test_migrations_ci.py::TestMigrationsCICheck::test_ci_workflow_migration_job_invariants PASSED
backend/tests/test_migrations_ci.py::TestMigrationsCICheck::test_deliberately_broken_downgrade_fails_and_prints_revision PASSED
backend/tests/test_migrations_ci.py::TestMigrationsCICheck::test_deliberately_broken_upgrade_fails_and_prints_revision PASSED
backend/tests/test_migrations_ci.py::TestMigrationsCICheck::test_initial_migration_matches_models_and_is_reversible PASSED
backend/tests/test_migrations_ci.py::TestMigrationsCICheck::test_schema_drift_check_fails_when_autogenerate_detects_diffs PASSED
backend/tests/test_rls_guardrails.py::test_all_tenant_id_tables_have_rls_and_force_rls PASSED
backend/tests/test_rls_guardrails.py::test_all_tenant_tables_have_isolation_policy_for_eng_app_user PASSED
backend/tests/test_rls_guardrails.py::test_no_unshielded_tables_exist_outside_exemption_list PASSED
backend/tests/test_rls_guardrails.py::test_sqlalchemy_models_match_tenant_rls_expectations PASSED
```

---

## 5. Traceability to the CI Migration Job (`backend-migrations`)

Every scenario is linked to the `backend-migrations` job (`Database Migration Check`) in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml#L98-L150) and the `backend-test` job (`Backend Test Suite` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml#L50-L97)), both of which gate pull request merges via `ci-gate`:

```
GitHub Actions CI (.github/workflows/ci.yml)
│
├── Job: backend-migrations ("Database Migration Check", lines 98–150)
│   ├── Service: pgvector/pgvector:pg16 (empty database `manta_db`)
│   ├── Step 1 (lines 136–137): python scripts/verify_migrations.py --step wait       [DB Readiness]
│   ├── Step 2 (lines 139–140): python scripts/verify_migrations.py --step upgrade    [TC-MIG-001: Empty DB -> head]
│   ├── Step 3 (lines 142–143): python scripts/verify_migrations.py --step downgrade  [TC-MIG-002: head -> base + 0 leftover tables]
│   ├── Step 4 (lines 145–146): python scripts/verify_migrations.py --step reupgrade  [TC-MIG-003: base -> head]
│   └── Step 5 (lines 148–149): python scripts/verify_migrations.py --step check      [TC-MIG-005: Zero Model/Schema Drift]
│
├── Job: backend-test ("Backend Test Suite", lines 50–97)
│   ├── Step: python scripts/verify_migrations.py --step upgrade
│   └── Step: python -m pytest tests -v
│       ├── tests/test_migrations_ci.py  [TC-MIG-001..006: AST table/PK/FK/UQ/index parity, offline SQL, fault injection]
│       └── tests/test_rls_guardrails.py [TC-MIG-005: Live pg_class/pg_policies RLS & FORCE RLS verification across 40 tables]
│
└── Job: ci-gate ("CI Status Check", lines 230–262)
    └── Blocks PR merge if `needs.backend-migrations.result != "success"` or `needs.backend-test.result != "success"`
```
