# Continuous Integration (CI) Pipeline Guide

## 1. Overview & Architecture

The MANTA Engineering Intelligence Platform enforces a deterministic, automated CI pipeline via GitHub Actions (`.github/workflows/ci.yml`). Every change submitted via push (to `develop`, `main`, or feature/fix branches) or pull request is automatically guarded against regressions, syntax errors, test failures, broken or irreversible database migrations, and broken container builds.

```
       Git Push / Pull Request (develop / main / feature/** / fix/**)
                         │
                         ▼
             GitHub Actions CI Pipeline
                         │
         ┌───────────────┼───────────────┬────────────────┬────────────────┐
         ▼               ▼               ▼                ▼                ▼
   Backend Lint    Backend Tests   Security Tests   Migration Check  PgBouncer Test
   (Ruff check)     (pytest core)   (tests/security) (Alembic check)  (Pooler Live)
         │               │               │                │                │
         └───────────────┼───────────────┼────────────────┼────────────────┘
         ┌───────────────┼───────────────┴────────────────┐
         ▼               ▼                                ▼
   RLS Negative    Docker Build                      Web Checks
    (tamper DB)     (Buildx gha)                  (ESLint & E2E)
         │               │                                │
         └───────────────┴───────────────┬────────────────┘
                                         ▼
                                CI Status Check (ci-gate)
                                         │
                          ┌──────────────┴──────────────┐
                          ▼                             ▼
                       [PASS]                        [FAIL]
                  Green status check             Red status check
                  Merge permitted to develop     Merge BLOCKED by protection
```

---

## 2. Pipeline Stages & Invariants

| Stage | Job Name | Primary Tool | Caching Mechanism | Purpose |
|---|---|---|---|---|
| **Backend Lint** | `backend-lint` | Ruff (`format --check`, `check`) | GitHub Actions pip cache (`backend/requirements.txt`) | Enforces PEP 8 style, import sorting (`isort`), code cleanliness, and Python 3.12 compatibility. |
| **Backend Tests** | `backend-test` | `pytest`, `pytest-asyncio` | GitHub Actions pip cache (`backend/requirements.txt`) | Executes the backend core test suite (`backend/tests --ignore=tests/security`), verifying API endpoints, identity configuration, and static pooler settings against migrated PostgreSQL. |
| **Security Tests** | `security-test` | `pytest`, `pytest-asyncio` | GitHub Actions pip cache (`backend/requirements.txt`) | Executes the dedicated tenant boundary security test suite (`backend/tests/security`), verifying cross-tenant isolation, forged tenant claims, and missing tenant contexts against PostgreSQL initialised through migrations. |
| **Migration Check** | `backend-migrations` | Alembic, PostgreSQL 16 (`pgvector/pgvector:pg16`) | GitHub Actions pip cache (`backend/requirements.txt`) | Starts a PostgreSQL + pgvector service and runs `upgrade head`, `downgrade base`, `upgrade head`, and `alembic check` (drift detection), printing the failing revision on any error. |
| **Negative RLS Security** | `security-negative-test` | `verify_rls_negative.py`, PostgreSQL 16 | GitHub Actions pip cache (`backend/requirements.txt`) | Validates the safety net by deliberately tampering with RLS policies/FORCE in throwaway databases to ensure regressions are caught. |
| **PgBouncer Tests** | `pgbouncer-integration` | `docker compose`, `pytest` | GitHub Actions pip cache (`backend/requirements.txt`) | Spins up live Postgres & PgBouncer containers to verify that `pool_mode=transaction` and `server_reset_query_always=1` purge session state across clients. |
| **Docker Build** | `backend-docker-build` | Docker Buildx (`docker/build-push-action`) | GitHub Actions cache backend (`type=gha`, `mode=max`) | Validates multi-stage Docker image compilation and dependency resolution without pushing unverified images. |
| **Web Checks** | `web-checks` | Next.js 15, ESLint | GitHub Actions npm cache (`web/package-lock.json`) | Verifies web portal TypeScript types, ESLint compliance, and production static site builds. |
| **Web E2E Tests** | `web-e2e` | Playwright, Keycloak 24 (`manta` realm) | GitHub Actions npm cache (`web/package-lock.json`) | Boots Keycloak with `infra/keycloak/realm-export.json` and runs automated browser tests covering sign-in, wrong password, cancelled login, sign-out, and protected route access (`docs/auth-test-scenarios.md`). |
| **Mobile Quality & Build** | `mobile-ci` (`.github/workflows/mobile-ci.yml`) | Gradle 9, Ktlint, Android Lint, JUnit | GitHub Actions Gradle cache (`actions/setup-java@v4`) | Enforces Kotlin code formatting (`ktlintCheck`), Android static analysis (`lintDebug`), unit tests (`testDebugUnitTest`), and compiles debug APKs (`assembleDebug`). |
| **Unified Status Gate** | `ci-gate` | Bash status evaluator | N/A | Consolidates upstream results; exits `1` if any stage fails or cancels, emitting a unified status check to GitHub. |

---

## 3. Configuring Branch Protection on GitHub (`develop`)

To ensure no code can be merged into `develop` without passing all CI checks, follow these repository configuration steps on GitHub:

### Option A: Via GitHub Web Interface
1. Go to your repository on GitHub: `https://github.com/Mimmo-gitter/MANTA`.
2. Click **Settings** (tab at the top) $\rightarrow$ **Branches** (in the left navigation).
3. Under **Branch protection rules**, click **Add branch protection rule** (or **Add branch ruleset**).
4. Configure the rule for the `develop` branch:
   - **Branch name pattern**: `develop`
   - Check **Require a pull request before merging**
     - *(Optional)* Require approvals (e.g., 1 approval)
   - Check **Require status checks to pass before merging**
   - Check **Require branches to be up to date before merging**
   - In the **Status checks that are required** search box, select:
     - `CI Status Check` (or the underlying jobs: `Backend Lint & Formatting`, `Backend Test Suite`, `Security Test Suite (tests/security)`, `Database Migration Check`, `Negative RLS Security Tests`, `PgBouncer Tenant-Isolation Tests`, `Backend Docker Image Build`, `Web Portal Lint & Build`, `Web Portal Playwright E2E Tests`)
5. Click **Create** or **Save changes**.

*(Repeat the same rule for the `main` branch to guarantee production release protection).*

---

### Option B: Via GitHub CLI (`gh`)
If using GitHub CLI with admin privileges:
```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/Mimmo-gitter/MANTA/branches/develop/protection \
  -f required_status_checks.strict=true \
  -F 'required_status_checks[contexts][]=CI Status Check' \
  -f enforce_admins=false \
  -f required_pull_request_reviews.required_approving_review_count=1 \
  -f restrictions=null
```

---

## 4. Local Developer Commands

Developers can run checks locally before pushing:

### Backend
```bash
# Lint and format checks
ruff check .
ruff format --check .

# Auto-fix formatting and imports
ruff format .
ruff check --fix .

# Run test suite
python -m pytest backend/tests -v

# Run migration lifecycle & drift check (requires running PostgreSQL with pgvector)
python backend/scripts/verify_migrations.py --step all
```

### Web Portal
```bash
cd web
npm run lint
npm run build
npm run test:e2e
```

### Mobile App (Android)
```bash
cd mobile

# Code format check (Ktlint)
./gradlew ktlintCheck

# Auto-format Kotlin code
./gradlew ktlintFormat

# Static analysis (Android Lint - dev and stage debug variants)
./gradlew lintDevDebug lintStageDebug

# Run unit tests (dev and stage debug variants)
./gradlew testDevDebugUnitTest testStageDebugUnitTest

# Build debug APKs (dev and stage flavors)
./gradlew assembleDebug
```
