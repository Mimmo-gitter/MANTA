# Continuous Integration (CI) Pipeline Guide

## 1. Overview & Architecture

The MANTA Engineering Intelligence Platform enforces a deterministic, automated CI pipeline via GitHub Actions (`.github/workflows/ci.yml`). Every change submitted via push (to `develop`, `main`, or feature/fix branches) or pull request is automatically guarded against regressions, syntax errors, test failures, and broken container builds.

```
       Git Push / Pull Request (develop / main / feature/** / fix/**)
                         │
                         ▼
             GitHub Actions CI Pipeline
                         │
         ┌───────────────┼───────────────┬────────────────┐
         ▼               ▼               ▼                ▼
   Backend Lint    Backend Tests   Docker Build      Web Checks
   (Ruff check)      (pytest)      (Buildx gha)   (ESLint & Build)
         │               │               │                │
         └───────────────┼───────────────┴────────────────┘
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
| **Backend Tests** | `backend-test` | `pytest`, `pytest-asyncio` | GitHub Actions pip cache (`backend/requirements.txt`) | Executes the backend test suite (`backend/tests`), verifying API endpoints and identity configuration. |
| **Docker Build** | `backend-docker-build` | Docker Buildx (`docker/build-push-action`) | GitHub Actions cache backend (`type=gha`, `mode=max`) | Validates multi-stage Docker image compilation and dependency resolution without pushing unverified images. |
| **Web Checks** | `web-checks` | Next.js 15, ESLint | GitHub Actions npm cache (`web/package-lock.json`) | Verifies web portal TypeScript types, ESLint compliance, and production static site builds. |
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
     - `CI Status Check` (or the underlying jobs: `Backend Lint & Formatting`, `Backend Test Suite`, `Backend Docker Image Build`, `Web Portal Lint & Build`)
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
```

### Web Portal
```bash
cd web
npm run lint
npm run build
```
