import { test, expect, type Page } from "@playwright/test";
import crypto from "crypto";
import fs from "fs";
import path from "path";

interface RealmUser {
  id: string;
  username: string;
  email: string;
  firstName: string;
  lastName: string;
  enabled: boolean;
  attributes?: {
    tenant_id?: string[];
  };
  credentials?: Array<{
    type: string;
    value: string;
  }>;
  realmRoles?: string[];
}

interface RealmExport {
  realm: string;
  users: RealmUser[];
}

interface TestKeyContext {
  privateKey: crypto.KeyObject;
  publicKeyJwk: Record<string, unknown>;
  kid: string;
}

function base64UrlEncode(objOrBuf: object | Buffer | string): string {
  const buf = Buffer.isBuffer(objOrBuf)
    ? objOrBuf
    : Buffer.from(typeof objOrBuf === "string" ? objOrBuf : JSON.stringify(objOrBuf));
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function createTestKey(kid = "keycloak-manta-realm-key"): TestKeyContext {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", {
    modulusLength: 2048,
  });
  const jwk = publicKey.export({ format: "jwk" }) as Record<string, unknown>;
  jwk.kid = kid;
  jwk.use = "sig";
  jwk.alg = "RS256";
  return { privateKey, publicKeyJwk: jwk, kid };
}

function loadMantaRealm(): RealmExport {
  const realmPath = path.resolve(__dirname, "../../infra/keycloak/realm-export.json");
  const raw = fs.readFileSync(realmPath, "utf-8");
  return JSON.parse(raw) as RealmExport;
}

function signJwt(
  keyCtx: TestKeyContext,
  payload: Record<string, unknown>
): string {
  const header = {
    alg: "RS256",
    typ: "JWT",
    kid: keyCtx.kid,
  };
  const signingInput = `${base64UrlEncode(header)}.${base64UrlEncode(payload)}`;
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(signingInput);
  const sig = signer.sign(keyCtx.privateKey);
  return `${signingInput}.${base64UrlEncode(sig)}`;
}

async function isLiveKeycloakAvailable(): Promise<boolean> {
  if (process.env.FORCE_KEYCLOAK_MOCK === "true") {
    return false;
  }
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1500);
    const res = await fetch(
      "http://localhost:8080/realms/manta/.well-known/openid-configuration",
      { signal: controller.signal }
    );
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

function renderKeycloakLoginForm(actionUrl: string, cancelUrl: string, errorMessage?: string): string {
  const errorBlock = errorMessage
    ? `<div class="alert-error"><span id="input-error" aria-live="polite">${errorMessage}</span></div>`
    : "";
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Sign in to MANTA Intelligence Platform</title>
</head>
<body>
  <main id="kc-content">
    <h1>MANTA Intelligence Platform</h1>
    ${errorBlock}
    <form id="kc-form-login" method="post" action="${actionUrl}">
      <div>
        <label for="username">Username or email</label>
        <input tabindex="1" id="username" name="username" type="text" autofocus autocomplete="off" />
      </div>
      <div>
        <label for="password">Password</label>
        <input tabindex="2" id="password" name="password" type="password" autocomplete="off" />
      </div>
      <div id="kc-form-buttons">
        <input tabindex="4" id="kc-login" name="login" type="submit" value="Sign In" />
        <a id="kc-cancel" href="${cancelUrl}">Cancel</a>
      </div>
    </form>
  </main>
</body>
</html>`;
}

/**
 * Connects Playwright to the live Keycloak test realm on http://localhost:8080 when running
 * (e.g. in CI or local Docker Compose), or falls back to serving the exact `infra/keycloak/realm-export.json`
 * realm definition in-process when Docker is offline locally.
 */
async function attachKeycloakTestRealm(
  page: Page,
  keyCtx: TestKeyContext,
  realm: RealmExport,
  useLiveKeycloak: boolean
): Promise<void> {
  if (useLiveKeycloak) {
    return;
  }

  interface PendingAuthSession {
    state: string;
    nonce: string;
    redirectUri: string;
    codeChallenge: string;
    codeChallengeMethod: string;
    user?: RealmUser;
  }

  const pendingBySession = new Map<string, PendingAuthSession>();
  const pendingByCode = new Map<string, PendingAuthSession>();

  const corsHeaders = {
    "access-control-allow-origin": "http://localhost:3000",
    "access-control-allow-credentials": "true",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "Content-Type, Authorization",
  };

  await page.route("**/realms/manta/.well-known/openid-configuration", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: corsHeaders,
      body: JSON.stringify({
        issuer: "http://localhost:8080/realms/manta",
        authorization_endpoint: "http://localhost:8080/realms/manta/protocol/openid-connect/auth",
        token_endpoint: "http://localhost:8080/realms/manta/protocol/openid-connect/token",
        userinfo_endpoint: "http://localhost:8080/realms/manta/protocol/openid-connect/userinfo",
        end_session_endpoint: "http://localhost:8080/realms/manta/protocol/openid-connect/logout",
        jwks_uri: "http://localhost:8080/realms/manta/protocol/openid-connect/certs",
        response_types_supported: ["code"],
        subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"],
        code_challenge_methods_supported: ["S256"],
      }),
    });
  });

  await page.route("**/realms/manta/protocol/openid-connect/certs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: corsHeaders,
      body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
    });
  });

  await page.route("**/realms/manta/protocol/openid-connect/auth*", async (route) => {
    const reqUrl = new URL(route.request().url());
    const state = reqUrl.searchParams.get("state") || "";
    const nonce = reqUrl.searchParams.get("nonce") || "";
    const redirectUri = reqUrl.searchParams.get("redirect_uri") || "http://localhost:3000/auth/callback";
    const codeChallenge = reqUrl.searchParams.get("code_challenge") || "";
    const codeChallengeMethod = reqUrl.searchParams.get("code_challenge_method") || "S256";

    const sessionId = crypto.randomUUID();
    pendingBySession.set(sessionId, {
      state,
      nonce,
      redirectUri,
      codeChallenge,
      codeChallengeMethod,
    });

    const actionUrl = `http://localhost:8080/realms/manta/login-actions/authenticate?session_code=${sessionId}`;
    const cancelUrl = `${redirectUri}?error=access_denied&error_description=${encodeURIComponent(
      "User cancelled authentication"
    )}&state=${encodeURIComponent(state)}`;

    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: renderKeycloakLoginForm(actionUrl, cancelUrl),
    });
  });

  await page.route("**/realms/manta/login-actions/authenticate*", async (route) => {
    const reqUrl = new URL(route.request().url());
    const sessionId = reqUrl.searchParams.get("session_code") || "";
    const session = pendingBySession.get(sessionId);

    if (!session) {
      await route.fulfill({ status: 400, body: "Invalid session_code" });
      return;
    }

    const postData = route.request().postData() || "";
    const params = new URLSearchParams(postData);
    const usernameInput = (params.get("username") || "").trim();
    const passwordInput = params.get("password") || "";

    const matchedUser = realm.users.find(
      (u) =>
        u.enabled &&
        (u.username === usernameInput || u.email === usernameInput) &&
        u.credentials?.some((c) => c.type === "password" && c.value === passwordInput)
    );

    if (!matchedUser) {
      const actionUrl = `http://localhost:8080/realms/manta/login-actions/authenticate?session_code=${sessionId}`;
      const cancelUrl = `${session.redirectUri}?error=access_denied&error_description=${encodeURIComponent(
        "User cancelled authentication"
      )}&state=${encodeURIComponent(session.state)}`;
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: renderKeycloakLoginForm(actionUrl, cancelUrl, "Invalid username or password."),
      });
      return;
    }

    session.user = matchedUser;
    const authCode = `kc_code_${crypto.randomUUID()}`;
    pendingByCode.set(authCode, session);

    const callbackUrl = new URL(session.redirectUri);
    callbackUrl.searchParams.set("code", authCode);
    callbackUrl.searchParams.set("state", session.state);
    callbackUrl.searchParams.set("session_state", crypto.randomUUID());
    callbackUrl.searchParams.set("iss", "http://localhost:8080/realms/manta");

    await route.fulfill({
      status: 302,
      headers: {
        Location: callbackUrl.toString(),
      },
    });
  });

  await page.route("**/realms/manta/protocol/openid-connect/token", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 200, headers: corsHeaders });
      return;
    }

    const postData = route.request().postData() || "";
    const params = new URLSearchParams(postData);
    const code = params.get("code") || "";
    const codeVerifier = params.get("code_verifier") || "";
    const session = pendingByCode.get(code);

    if (!session || !session.user) {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        headers: corsHeaders,
        body: JSON.stringify({ error: "invalid_grant", error_description: "Code not valid" }),
      });
      return;
    }

    // Verify PKCE S256 code_verifier matches code_challenge
    if (session.codeChallenge) {
      const computedChallenge = base64UrlEncode(
        crypto.createHash("sha256").update(codeVerifier).digest()
      );
      if (computedChallenge !== session.codeChallenge) {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          headers: corsHeaders,
          body: JSON.stringify({ error: "invalid_grant", error_description: "PKCE verification failed" }),
        });
        return;
      }
    }

    const u = session.user;
    const fullName = `${u.firstName} ${u.lastName}`.trim();
    const tenantId = u.attributes?.tenant_id?.[0] || "";
    const now = Math.floor(Date.now() / 1000);

    const idToken = signJwt(keyCtx, {
      iss: "http://localhost:8080/realms/manta",
      sub: u.id,
      aud: "manta-portal",
      exp: now + 900,
      iat: now,
      auth_time: now,
      nonce: session.nonce,
      name: fullName,
      given_name: u.firstName,
      family_name: u.lastName,
      preferred_username: u.username,
      email: u.email,
      email_verified: true,
      tenant_id: tenantId,
    });

    const accessToken = signJwt(keyCtx, {
      iss: "http://localhost:8080/realms/manta",
      sub: u.id,
      aud: ["manta-backend", "account"],
      azp: "manta-portal",
      exp: now + 900,
      iat: now,
      name: fullName,
      preferred_username: u.username,
      email: u.email,
      tenant_id: tenantId,
      realm_access: {
        roles: u.realmRoles || [],
      },
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: corsHeaders,
      body: JSON.stringify({
        access_token: accessToken,
        id_token: idToken,
        token_type: "Bearer",
        expires_in: 900,
        scope: "openid profile email",
      }),
    });
  });

  await page.route("**/realms/manta/protocol/openid-connect/userinfo", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: corsHeaders,
      body: JSON.stringify({}),
    });
  });

  await page.route("**/realms/manta/protocol/openid-connect/logout*", async (route) => {
    const reqUrl = new URL(route.request().url());
    const postLogoutRedirectUri =
      reqUrl.searchParams.get("post_logout_redirect_uri") || "http://localhost:3000/signed-out";
    await route.fulfill({
      status: 302,
      headers: {
        Location: postLogoutRedirectUri,
      },
    });
  });
}

test.describe("Authentication Journey E2E against Keycloak Test Realm (SCRUM-240 / US-4)", () => {
  let keyCtx: TestKeyContext;
  let realm: RealmExport;
  let useLiveKeycloak = false;

  test.beforeAll(async () => {
    keyCtx = createTestKey("manta-keycloak-realm-key-2026");
    realm = loadMantaRealm();
    useLiveKeycloak = await isLiveKeycloakAvailable();
  });

  test("Scenario 1A: Successful sign-in via Keycloak test realm (Tenant Alpha) asserts displayed user name and tenant", async ({
    page,
  }) => {
    await attachKeycloakTestRealm(page, keyCtx, realm, useLiveKeycloak);

    // 1. Navigate to /login and click "Continue with Company SSO"
    await page.goto("/login");
    const ssoButton = page.getByTestId("company-sso-button");
    await expect(ssoButton).toBeVisible();
    await ssoButton.click();

    // 2. Authenticate on Keycloak login form with Tenant Alpha test user (alpha_dev / dev123)
    const usernameInput = page.locator("#username");
    const passwordInput = page.locator("#password");
    const kcLoginBtn = page.locator("#kc-login");

    await expect(usernameInput).toBeVisible({ timeout: 15000 });
    await usernameInput.fill("alpha_dev");
    await passwordInput.fill("dev123");
    await kcLoginBtn.click();

    // 3. Verify redirect back to MANTA portal after PKCE code exchange
    await expect(page).toHaveURL("/", { timeout: 15000 });

    // 4. Assert displayed user name and tenant after login (Acceptance Criterion #2)
    const profileName = page.getByTestId("user-profile-name");
    const tenantBadge = page.getByTestId("user-tenant-badge");

    await expect(profileName).toBeVisible();
    await expect(profileName).toContainText("Alpha Developer");

    await expect(tenantBadge).toBeVisible();
    await expect(tenantBadge).toContainText("Tenant Alpha");

    await expect(page.getByTestId("header-signout-button")).not.toBeVisible();
  });

  test("Scenario 1B: Successful sign-in via Keycloak test realm (Tenant Beta) asserts displayed user name and tenant", async ({
    page,
  }) => {
    await attachKeycloakTestRealm(page, keyCtx, realm, useLiveKeycloak);

    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();

    const usernameInput = page.locator("#username");
    await expect(usernameInput).toBeVisible({ timeout: 15000 });
    await usernameInput.fill("beta_dev");
    await page.locator("#password").fill("dev123");
    await page.locator("#kc-login").click();

    await expect(page).toHaveURL("/", { timeout: 15000 });

    // Assert Tenant Beta user name and tenant badge
    const profileName = page.getByTestId("user-profile-name");
    const tenantBadge = page.getByTestId("user-tenant-badge");

    await expect(profileName).toBeVisible();
    await expect(profileName).toContainText("Beta Developer");

    await expect(tenantBadge).toBeVisible();
    await expect(tenantBadge).toContainText("Tenant Beta");
  });

  test("Scenario 2: Wrong password rejected by Keycloak test realm without creating a session", async ({
    page,
  }) => {
    await attachKeycloakTestRealm(page, keyCtx, realm, useLiveKeycloak);

    // 1. Initiate OIDC login from /login
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();

    // 2. Submit valid username with wrong password on Keycloak login form
    const usernameInput = page.locator("#username");
    await expect(usernameInput).toBeVisible({ timeout: 15000 });
    await usernameInput.fill("alpha_dev");
    await page.locator("#password").fill("wrong_password_999");
    await page.locator("#kc-login").click();

    // 3. Verify Keycloak displays credential error and remains on the IdP login page
    const kcError = page.locator("#input-error").or(page.getByText(/Invalid username or password/i));
    await expect(kcError.first()).toBeVisible({ timeout: 10000 });
    await expect(kcError.first()).toContainText(/Invalid username or password/i);
    expect(page.url()).toContain("/realms/manta/");

    // 4. Navigate back to portal and verify unauthenticated user is redirected to /login
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
    await expect(page.getByTestId("user-profile-name")).not.toBeVisible();
    await expect(page.getByTestId("user-tenant-badge")).not.toBeVisible();

    const hasUserToken = await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) return true;
      }
      return false;
    });
    expect(hasUserToken).toBe(false);
  });

  test("Scenario 3: Cancelled login aborts authentication and displays error alert on /login", async ({
    page,
  }) => {
    await attachKeycloakTestRealm(page, keyCtx, realm, useLiveKeycloak);

    // 1. Initiate OIDC sign-in from /login
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();

    // 2. Reach Keycloak login screen and capture OIDC state parameter
    const usernameInput = page.locator("#username");
    await expect(usernameInput).toBeVisible({ timeout: 15000 });
    const authUrl = new URL(page.url());
    const stateParam = authUrl.searchParams.get("state") || "cancelled_state";

    // 3. User cancels/denies login at the identity provider -> redirected to /auth/callback with error=access_denied
    await page.goto(
      `/auth/callback?error=access_denied&error_description=${encodeURIComponent(
        "User cancelled authentication"
      )}&state=${encodeURIComponent(stateParam)}`
    );

    // 4. Verify callback aborts login and redirects to /login with error alert
    await expect(page).toHaveURL(/\/login\?error=auth_callback_failed/, { timeout: 10000 });

    const errorAlert = page.getByTestId("auth-error-alert");
    await expect(errorAlert).toBeVisible();
    await expect(page.getByText("Authentication failed")).toBeVisible();
    await expect(page.getByText(/Login aborted: User cancelled authentication/i)).toBeVisible();

    // 5. Verify user remains unauthenticated
    const hasUserToken = await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) return true;
      }
      return false;
    });
    expect(hasUserToken).toBe(false);
  });

  test("Scenario 4: Sign-out terminates Keycloak & local session and shows confirmation screen", async ({
    page,
  }) => {
    await attachKeycloakTestRealm(page, keyCtx, realm, useLiveKeycloak);

    // 1. Sign in via Keycloak as alpha_dev
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();

    await expect(page.locator("#username")).toBeVisible({ timeout: 15000 });
    await page.locator("#username").fill("alpha_dev");
    await page.locator("#password").fill("dev123");
    await page.locator("#kc-login").click();

    await expect(page).toHaveURL("/", { timeout: 15000 });
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");
    await expect(page.getByTestId("user-tenant-badge")).toContainText("Tenant Alpha");

    // 2. Click Sign Out button in profile menu
    await page.getByTestId("user-profile-menu-trigger").click();
    const signOutBtn = page.getByTestId("dropdown-signout-button");
    await expect(signOutBtn).toBeVisible();
    await signOutBtn.click();

    // 3. Verify redirect through Keycloak end_session_endpoint to /signed-out confirmation page
    await expect(page).toHaveURL(/\/signed-out/, { timeout: 15000 });
    await expect(page.getByTestId("session-terminated-badge")).toContainText("SESSION TERMINATED");
    await expect(
      page.getByRole("heading", { level: 1, name: "You Have Been Signed Out" })
    ).toBeVisible();
    await expect(page.getByRole("note", { name: "Workspace disconnection notice" })).toContainText(
      "Disconnected from workspace Tenant Alpha."
    );

    // 4. Verify localStorage tokens are completely purged
    const remainingKeys = await page.evaluate(() => {
      const keys: string[] = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && (k.startsWith("oidc.") || k.startsWith("manta."))) keys.push(k);
      }
      return keys;
    });
    expect(remainingKeys).toEqual([]);

    // 5. Verify Back button and reload do not restore the authenticated session
    await page.goBack();
    await expect(page.getByTestId("user-profile-name")).not.toBeVisible();
    await expect(page.getByTestId("user-tenant-badge")).not.toBeVisible();
  });

  test("Scenario 5: Access to a protected page (/dashboard) when signed out redirects to /login", async ({
    page,
  }) => {
    await attachKeycloakTestRealm(page, keyCtx, realm, useLiveKeycloak);

    // 1. Attempt to access protected /dashboard while signed out (prior to login)
    await page.goto("/dashboard");

    // 2. Verify automatic redirect to /login and protected content is not accessible
    await expect(page).toHaveURL(/\/login\?redirect=%2Fdashboard/, { timeout: 10000 });
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
    await expect(page.getByTestId("protected-dashboard-content")).not.toBeVisible();
    await expect(page.getByTestId("user-profile-name")).not.toBeVisible();
    await expect(page.getByTestId("user-tenant-badge")).not.toBeVisible();

    // 3. Sign in via Keycloak as alpha_dev
    await page.getByTestId("company-sso-button").click();
    await expect(page.locator("#username")).toBeVisible({ timeout: 15000 });
    await page.locator("#username").fill("alpha_dev");
    await page.locator("#password").fill("dev123");
    await page.locator("#kc-login").click();

    await expect(page).toHaveURL("/", { timeout: 15000 });

    // 4. Navigate to protected /dashboard while authenticated -> access granted, asserts user name & tenant
    await page.goto("/dashboard");
    await expect(page).toHaveURL("/dashboard", { timeout: 10000 });
    await expect(page.getByTestId("protected-dashboard-content")).toBeVisible();
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");
    await expect(page.getByTestId("user-tenant-badge")).toContainText("Tenant Alpha");

    // 5. Sign out from /dashboard and attempt to access /dashboard again while signed out
    await page.getByTestId("header-signout-button").click();
    await expect(page).toHaveURL(/\/signed-out/, { timeout: 15000 });

    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?redirect=%2Fdashboard/, { timeout: 10000 });
    await expect(page.getByTestId("protected-dashboard-content")).not.toBeVisible();
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
  });
});
