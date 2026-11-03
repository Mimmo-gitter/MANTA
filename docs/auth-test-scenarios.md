# Authentication Journey Test Scenarios (Sign-In & Sign-Out)

**Parent User Story:** `SCRUM-240` (US-4: Sign in via the organization's identity provider)  
**Identity Provider:** Keycloak 24.0.5 (`manta` test realm — `infra/keycloak/realm-export.json`)  
**Protocol:** OIDC / OAuth 2.0 Authorization Code Flow with PKCE (`S256`) & RP-Initiated Logout  
**Automated E2E Suite:** `web/e2e/auth-keycloak-journey.spec.ts`

---

## 1. Objective

Cover the complete browser authentication lifecycle in the MANTA Web Portal against the `manta` Keycloak test realm with documented test scenarios and automated Playwright end-to-end tests executed in Continuous Integration (CI):

1. **Successful Sign-In** (verifying multi-tenant identity claims, displayed user name, and tenant badge for both Tenant Alpha and Tenant Beta)
2. **Wrong Password** (invalid credentials rejected at the Keycloak identity provider and login form)
3. **Cancelled Login** (user aborts or cancels authentication at the identity provider)
4. **Sign-Out** (RP-Initiated OIDC logout, `localStorage` token purge, signed-out confirmation screen, and back-button resilience)
5. **Access to a Protected Page When Signed Out** (unauthenticated access to `/dashboard` blocked and redirected to `/login`, both before sign-in and after sign-out)

---

## 2. Test Realm Fixtures (`infra/keycloak/realm-export.json`)

The `manta` Keycloak test realm pre-loads the public OIDC client `manta-portal` (PKCE `S256` enforced, `tenant_id` protocol mapper enabled) and multi-tenant users:

| Username | Password | Full Name (`name` claim) | `tenant_id` Claim | Expected Displayed Tenant | Realm Roles |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `alpha_dev` | `dev123` | `Alpha Developer` | `11111111-1111-1111-1111-111111111111` | `Tenant Alpha` | `DEVELOPER` |
| `alpha_admin` | `admin123` | `Alpha Admin` | `11111111-1111-1111-1111-111111111111` | `Tenant Alpha` | `TENANT_ADMIN`, `SOLUTION_ARCHITECT` |
| `beta_dev` | `dev123` | `Beta Developer` | `22222222-2222-2222-2222-222222222222` | `Tenant Beta` | `DEVELOPER` |
| `beta_admin` | `admin123` | `Beta Admin` | `22222222-2222-2222-2222-222222222222` | `Tenant Beta` | `TENANT_ADMIN` |

---

## 3. Scenario Matrix

| Scenario ID | Scenario Name | Preconditions | User Actions / Steps | Expected System & UI Outcome | Automated Playwright Test |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-E2E-AUTH-001A** | **Successful Sign-In (Tenant Alpha)** | User is unauthenticated; `localStorage` is empty; Keycloak `manta` realm is active. | 1. Navigate to `/login`.<br>2. Click **Continue with Company SSO** (`[data-testid="company-sso-button"]`).<br>3. On Keycloak login page (`/realms/manta/protocol/openid-connect/auth`), enter username `alpha_dev` and password `dev123`.<br>4. Click **Sign In** (`#kc-login`). | 1. Browser redirects with PKCE `code_challenge_method=S256`, `state`, and `nonce`.<br>2. Keycloak authenticates `alpha_dev` and redirects to `/auth/callback?code=...&state=...`.<br>3. Portal exchanges code for RS256 tokens and redirects to `/`.<br>4. Header displays user name **`Alpha Developer`** (`[data-testid="user-profile-name"]`) and tenant **`Tenant Alpha`** (`[data-testid="user-tenant-badge"]`). | `auth-keycloak-journey.spec.ts` $\rightarrow$ `Scenario 1A: Successful sign-in via Keycloak test realm (Tenant Alpha)` |
| **TC-E2E-AUTH-001B** | **Successful Sign-In (Tenant Beta)** | User is unauthenticated; `localStorage` is empty; Keycloak `manta` realm is active. | 1. Navigate to `/login`.<br>2. Click **Continue with Company SSO** (`[data-testid="company-sso-button"]`).<br>3. On Keycloak login page, enter username `beta_dev` and password `dev123`.<br>4. Click **Sign In** (`#kc-login`). | 1. Callback completes PKCE code exchange for Tenant Beta user.<br>2. Header displays user name **`Beta Developer`** (`[data-testid="user-profile-name"]`) and tenant **`Tenant Beta`** (`[data-testid="user-tenant-badge"]`). | `auth-keycloak-journey.spec.ts` $\rightarrow$ `Scenario 1B: Successful sign-in via Keycloak test realm (Tenant Beta)` |
| **TC-E2E-AUTH-002** | **Wrong Password** | User is unauthenticated on `/login`. | 1. Navigate to `/login` and click **Continue with Company SSO**.<br>2. On Keycloak login page, enter valid username `alpha_dev` and wrong password `wrong_password_999`.<br>3. Submit login (`#kc-login`).<br>4. Also verify portal `/login` credentials validation with invalid password. | 1. Keycloak rejects authentication, remains on the Keycloak login page, and displays error `#input-error` (`Invalid username or password.`).<br>2. No authorization code or OIDC token is issued; `localStorage` contains no `oidc.user:*` entry.<br>3. Navigating to `/` shows unauthenticated header (`Sign In` button visible, no user name or tenant badge). | `auth-keycloak-journey.spec.ts` $\rightarrow$ `Scenario 2: Wrong password rejected by Keycloak test realm` |
| **TC-E2E-AUTH-003** | **Cancelled Login** | User initiates SSO sign-in from `/login`. | 1. Click **Continue with Company SSO** on `/login`.<br>2. User cancels or denies authentication at the IdP (IdP redirects to `/auth/callback?error=access_denied&error_description=User+cancelled+login` or user navigates back from IdP before submitting credentials). | 1. Callback handler detects `error=access_denied` and aborts login.<br>2. Browser redirects to `/login?error=auth_callback_failed&error_description=...`.<br>3. Sign-in card displays `[data-testid="auth-error-alert"]` with heading **`Authentication failed`** and descriptive abort reason.<br>4. `localStorage` has no authenticated user tokens and user remains signed out. | `auth-keycloak-journey.spec.ts` $\rightarrow$ `Scenario 3: Cancelled login aborts authentication and displays error alert` |
| **TC-E2E-AUTH-004** | **Sign-Out & Session Termination** | User is signed in as `alpha_dev` (`Alpha Developer`, `Tenant Alpha`). | 1. Click **Sign Out** (`[data-testid="header-signout-button"]`) in the portal header.<br>2. Wait for Keycloak RP-Initiated Logout redirect to `/signed-out`.<br>3. Click browser **Back** button and reload page.<br>4. Click **Sign In Again** (`[data-testid="sign-in-again-button"]`). | 1. Local `oidc.*` and `manta.*` entries in `localStorage` are purged.<br>2. Browser invokes Keycloak `/realms/manta/protocol/openid-connect/logout` with `id_token_hint` and `post_logout_redirect_uri`.<br>3. Browser lands on `/signed-out?workspace=Tenant%20Alpha` showing **`SESSION TERMINATED`** (`[data-testid="session-terminated-badge"]`), heading **`You Have Been Signed Out`**, and **`Disconnected from workspace Tenant Alpha`**.<br>4. User name and tenant badge are no longer visible; browser Back/Reload does not restore session. | `auth-keycloak-journey.spec.ts` $\rightarrow$ `Scenario 4: Sign-out terminates Keycloak & local session and shows confirmation` |
| **TC-E2E-AUTH-005** | **Access to a Protected Page When Signed Out** | User is signed out (either in a fresh browser session or after completing Sign-Out). | 1. Attempt to navigate directly to protected route `/dashboard` while signed out.<br>2. Observe redirect to `/login`.<br>3. Complete sign-in via Keycloak as `alpha_dev` and navigate to `/dashboard`.<br>4. Sign out and attempt to access `/dashboard` again. | 1. When signed out, `/dashboard` blocks access to `[data-testid="protected-dashboard-content"]` and automatically redirects to `/login?redirect=%2Fdashboard`.<br>2. Sign-in card (`[data-testid="company-sso-button"]`) is displayed instead of protected content.<br>3. After signing in, `/dashboard` renders `[data-testid="protected-dashboard-content"]` and asserts user name **`Alpha Developer`** and tenant **`Tenant Alpha`**.<br>4. After signing out, navigating to `/dashboard` again redirects to `/login`. | `auth-keycloak-journey.spec.ts` $\rightarrow$ `Scenario 5: Access to a protected page when signed out redirects to login` |

---

## 4. Detailed Scenario Specifications

### Scenario 1: Successful Sign-In (`TC-E2E-AUTH-001A` & `TC-E2E-AUTH-001B`)
- **Objective:** Verify that a user from Tenant Alpha (`alpha_dev`) or Tenant Beta (`beta_dev`) can sign in through the Keycloak test realm using OIDC Authorization Code Flow with PKCE (`S256`) and that the portal header displays the authenticated user's full name and tenant name.
- **Steps:**
  1. Open `/login` in a clean browser context.
  2. Click `[data-testid="company-sso-button"]` (**Continue with Company SSO**).
  3. Verify redirection to `http://localhost:8080/realms/manta/protocol/openid-connect/auth` with query parameters `client_id=manta-portal`, `response_type=code`, `code_challenge_method=S256`, `state`, and `nonce`.
  4. Fill `#username` (`alpha_dev` or `beta_dev`) and `#password` (`dev123`) on the Keycloak login form and click `#kc-login`.
  5. Wait for `/auth/callback` to exchange the authorization code and redirect to `/`.
- **Assertions:**
  - `[data-testid="user-profile-name"]` is visible and contains `"Alpha Developer"` (for `alpha_dev`) or `"Beta Developer"` (for `beta_dev`).
  - `[data-testid="user-tenant-badge"]` is visible and contains `"Tenant Alpha"` (for `alpha_dev`) or `"Tenant Beta"` (for `beta_dev`).
  - `[data-testid="header-signout-button"]` is visible.

### Scenario 2: Wrong Password (`TC-E2E-AUTH-002`)
- **Objective:** Verify that submitting an incorrect password fails authentication, surfaces a clear error message, and never creates an authenticated session.
- **Steps:**
  1. Navigate to `/login` and click `[data-testid="company-sso-button"]`.
  2. On the Keycloak login page, enter username `alpha_dev` and password `wrong_password_999`, then click `#kc-login`.
  3. Assert that Keycloak remains on the login page and renders `#input-error` containing `"Invalid username or password."`.
  4. Verify that `localStorage` has no `oidc.user:*` tokens and navigating to `/` renders `[data-testid="header-signin-button"]` without `[data-testid="user-profile-name"]` or `[data-testid="user-tenant-badge"]`.

### Scenario 3: Cancelled Login (`TC-E2E-AUTH-003`)
- **Objective:** Verify that cancelling or aborting the login flow at the identity provider returns the user safely to `/login` with an explicit error alert and an unauthenticated state.
- **Steps:**
  1. Navigate to `/login` and click `[data-testid="company-sso-button"]`.
  2. Simulate user cancellation at the OIDC provider:
     - Path A: User aborts on the Keycloak login screen and returns to `/auth/callback?error=access_denied&error_description=User+cancelled+authentication`.
     - Path B: User navigates back to `/login` from the Keycloak login screen without submitting credentials.
- **Assertions:**
  - Browser lands on `/login?error=auth_callback_failed&error_description=...`.
  - `[data-testid="auth-error-alert"]` is visible with `"Authentication failed"` and the cancellation reason.
  - `[data-testid="user-profile-name"]` and `[data-testid="user-tenant-badge"]` are not present, and `localStorage` contains no active user session.

### Scenario 4: Sign-Out (`TC-E2E-AUTH-004`)
- **Objective:** Verify that clicking **Sign Out** terminates both the local browser session and the Keycloak OIDC session, displays the signed-out confirmation screen with the disconnected workspace name, and prevents session restoration via browser Back/Reload.
- **Steps:**
  1. Sign in via Keycloak as `alpha_dev` (`Alpha Developer`, `Tenant Alpha`).
  2. Click `[data-testid="header-signout-button"]` (**Sign Out**).
  3. Wait for redirection through `/realms/manta/protocol/openid-connect/logout` to `/signed-out?workspace=Tenant%20Alpha`.
  4. Press browser Back and reload the page.
- **Assertions:**
  - `/signed-out` displays `[data-testid="session-terminated-badge"]` (`SESSION TERMINATED`), heading `"You Have Been Signed Out"`, and workspace notice `"Disconnected from workspace Tenant Alpha."`.
  - All `oidc.*` and `manta.*` keys in `localStorage` are empty (`[]`).
  - Pressing Back or reloading does not restore `[data-testid="user-profile-name"]` or `[data-testid="user-tenant-badge"]`.

### Scenario 5: Access to a Protected Page When Signed Out (`TC-E2E-AUTH-005`)
- **Objective:** Verify that protected application routes (`/dashboard`) cannot be accessed when the user is signed out (both before initial login and after signing out) and automatically redirect to `/login`.
- **Steps:**
  1. With an unauthenticated browser session, navigate directly to `/dashboard`.
  2. Verify automatic redirection to `/login?redirect=%2Fdashboard` and confirm `[data-testid="protected-dashboard-content"]` is not rendered.
  3. Sign in as `alpha_dev` via Keycloak and navigate to `/dashboard`.
  4. Verify `[data-testid="protected-dashboard-content"]`, `[data-testid="user-profile-name"]` (`Alpha Developer`), and `[data-testid="user-tenant-badge"]` (`Tenant Alpha`) are visible.
  5. Click `[data-testid="header-signout-button"]` to sign out, then navigate to `/dashboard` again.
- **Assertions:**
  - Unauthenticated navigation to `/dashboard` always redirects to `/login` (`[data-testid="company-sso-button"]` visible).
  - Protected dashboard content and user/tenant badges are never exposed when signed out.

---

## 5. Running the Automated Tests

### Locally with Docker Compose (Live Keycloak Test Realm)
```bash
# 1. Start PostgreSQL and Keycloak with the imported 'manta' test realm
docker compose -f backend/docker-compose.yml up -d --wait postgres keycloak

# 2. Run the Playwright E2E test suite
cd web
npx playwright test
```

### In GitHub Actions CI
The `.github/workflows/ci.yml` pipeline runs the Playwright E2E suite against the `manta` Keycloak test realm in the `web-e2e` / `web-checks` workflow job and gates PR merges via `ci-gate`.
