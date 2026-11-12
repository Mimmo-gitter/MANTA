# Continuous Integration (CI) Pipeline Guide

## 1. Overview & Architecture

The MANTA Engineering Intelligence Platform enforces a deterministic, path-filtered CI pipeline via GitHub Actions (`.github/workflows/ci.yml`). Push events to `develop` and pull requests execute only the checks relevant to changed paths (avoiding duplicate runs on feature branches), while pushes to `main`, scheduled nightly runs (`03:00 UTC`), and manual `workflow_dispatch` runs execute the full cross-component regression suite.

```
       Pull Request / Push (develop, main) / Nightly / Manual Dispatch
                                     │
                                     ▼
                    Path Filter & Mode (`changes`)
                                     │
         ┌───────────────┬───────────┴───────┬────────────────┐
         ▼               ▼                   ▼                ▼
   Backend Core     Security & DB        PgBouncer        Web Portal
   Lint / Tests     Migrations / RLS    & Webhooks       Lint & Build
   (Ruff, pytest)   (Alembic, Negative)  (Live Pooler)   (Next.js cache)
         │               │                   │                │
         │               │                   │       ┌────────┴────────┐
         │               │                   │       ▼                 ▼
         │               │                   │   Web E2E          Web E2E
         │               │                   │   (Mocked x4)   (Keycloak Live)
         └───────────────┴───────────────────┴───────┴─────────────────┘
                                     │
                                     ▼
                         CI Status Check (`ci-gate`)
                                     │
                      ┌──────────────┴──────────────┐
                      ▼                             ▼
                   [PASS]                        [FAIL]
              Green status check             Red status check
              Merge permitted                Merge BLOCKED by protection
```

---

## 2. Pipeline Stages & Invariants

| Stage | Job Name | Primary Tool | Caching Mechanism | Purpose |
|---|---|---|---|---|
| **Path Detection** | `changes` | `dorny/paths-filter@v3` | N/A | Determines changed component flags and enables full `run_all` mode on `main`, `schedule`, and `workflow_dispatch`. |
| **Backend Lint** | `backend-lint` | `astral-sh/ruff-action@v3` | Built-in action binary cache | Enforces PEP 8 style, import sorting, code cleanliness, and Python 3.12 compatibility (`format --check`, `check`). |
| **Backend Tests** | `backend-test` | `pytest`, `pytest-asyncio` | `astral-sh/setup-uv@v5` (`backend/requirements.txt`) | Executes core backend tests (`tests --ignore=tests/security` and excluding live webhook suites covered by `pgbouncer-integration`). |
| **Security Tests** | `security-test` | `pytest`, `pytest-asyncio` | `astral-sh/setup-uv@v5` (`backend/requirements.txt`) | Executes the dedicated tenant boundary security suite (`backend/tests/security`) against migrated PostgreSQL. |
| **Migration Check** | `backend-migrations` | Alembic, PostgreSQL 16 (`pgvector/pgvector:pg16`) | `astral-sh/setup-uv@v5` (`backend/requirements.txt`) | Runs `upgrade head`, `downgrade base`, `upgrade head`, and `alembic check` (drift detection). |
| **Negative RLS Security** | `security-negative-test` | `verify_rls_negative.py --mode all --skip-baseline`, PostgreSQL 16 | `astral-sh/setup-uv@v5` (`backend/requirements.txt`) | Validates RLS tamper detection across all 3 modes in throwaway DBs, skipping redundant baseline migration/test cycles. |
| **PgBouncer Tests** | `pgbouncer-integration` | `docker compose`, `pytest` | `astral-sh/setup-uv@v5` (`backend/requirements.txt`) | Spins up live Postgres & PgBouncer containers for pooler isolation/load and live webhook auth/load/idempotency tests. |
| **Docker Build** | `backend-docker-build` | Docker Buildx (`docker/build-push-action`) | GitHub Actions cache (`type=gha`, `mode=max`) | Validates multi-stage Docker image compilation using trimmed `.dockerignore` context. |
| **Web Checks** | `web-checks` | Next.js 15, ESLint | npm cache + `web/.next/cache` | Runs TypeScript/ESLint checks, builds production Next.js app once, and uploads `web/.next` as an artifact. |
| **Web E2E (Mocked)** | `web-e2e` | Playwright (`--workers=4`, `FORCE_KEYCLOAK_MOCK=true`) | npm + `~/.cache/ms-playwright` + `web/.next` artifact | Runs mocked/UI Playwright suites in parallel across 4 workers without booting Postgres/Keycloak. |
| **Web E2E (Keycloak)** | `web-e2e-keycloak` | Playwright (`--workers=1`), Keycloak 24 (`manta` realm) | npm + `~/.cache/ms-playwright` + `web/.next` artifact | Triggered on auth/Keycloak changes; boots Keycloak test realm and runs live OIDC journey & session renewal specs serially. |
| **Mobile Quality & Build** | `mobile-ci` (`.github/workflows/mobile-ci.yml`) | Gradle, Ktlint, Android Lint, JUnit | `gradle/actions/setup-gradle@v4` + `--build-cache` | Runs `ktlintCheck`, `lintDevDebug`, `lintStageDebug`, unit tests, and `assembleDebug` in a single Gradle invocation. |
| **Unified Status Gate** | `ci-gate` | Bash status evaluator | N/A | Evaluates all upstream jobs against `changes` outputs; accepts expected `skipped` jobs and blocks on any unexpected skip/failure/cancel. |

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
     - **`CI Status Check`** *(recommended single unified gate because component jobs are path-filtered and may legitimately be `skipped` when unrelated components change)*
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
