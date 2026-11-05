import { test, expect, type Page } from "@playwright/test";
import crypto from "crypto";

function base64UrlEncode(objOrBuf: object | Buffer | string): string {
  const buf = Buffer.isBuffer(objOrBuf)
    ? objOrBuf
    : Buffer.from(typeof objOrBuf === "string" ? objOrBuf : JSON.stringify(objOrBuf));
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

interface TestKeyContext {
  privateKey: crypto.KeyObject;
  publicKeyJwk: Record<string, unknown>;
  kid: string;
}

function createTestKey(kid = "manta-renew-test-key-2026"): TestKeyContext {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", {
    modulusLength: 2048,
  });
  const jwk = publicKey.export({ format: "jwk" }) as Record<string, unknown>;
  jwk.kid = kid;
  jwk.use = "sig";
  jwk.alg = "RS256";

  return { privateKey, publicKeyJwk: jwk, kid };
}

function mintIdToken(
  keyCtx: TestKeyContext,
  claims: {
    sub: string;
    nonce: string;
    name?: string;
    preferred_username?: string;
    email?: string;
    tenant_id: string;
    auth_time?: number;
  }
): string {
  const now = Math.floor(Date.now() / 1000);
  const header = {
    alg: "RS256",
    typ: "JWT",
    kid: keyCtx.kid,
  };
  const payload = {
    iss: "http://localhost:8080/realms/manta",
    aud: "manta-portal",
    exp: now + 3600,
    iat: now,
    auth_time: claims.auth_time ?? now,
    ...claims,
  };

  const encodedHeader = base64UrlEncode(header);
  const encodedPayload = base64UrlEncode(payload);
  const signingInput = `${encodedHeader}.${encodedPayload}`;

  const signer = crypto.createSign("RSA-SHA256");
  signer.update(signingInput);
  const signature = signer.sign(keyCtx.privateKey);
  const encodedSignature = base64UrlEncode(signature);

  return `${signingInput}.${encodedSignature}`;
}

test.describe("Silent Session Renewal & Expiration (SCRUM-283)", () => {
  let keyCtx: TestKeyContext;

  test.beforeAll(() => {
    keyCtx = createTestKey("manta-renew-test-key-2026");
  });

  async function setupAuthenticatedUser(
    page: Page,
    options?: { tokenFailMode?: "none" | "revoked" | "transient" }
  ) {
    const authRequests: URL[] = [];
    let lastCapturedNonce = "test-nonce";
    let tokenCallCount = 0;
    const initialAuthTime = Math.floor(Date.now() / 1000);

    // 1. Mock Keycloak JWKS endpoint
    await page.route("**/protocol/openid-connect/certs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
      });
    });

    // 2. Mock Keycloak auth endpoint
    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      const u = new URL(route.request().url());
      authRequests.push(u);
      const n = u.searchParams.get("nonce");
      if (n) lastCapturedNonce = n;
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Authorization Endpoint</body></html>",
      });
    });

    // 3. Mock Keycloak token endpoint
    await page.route("**/protocol/openid-connect/token", async (route) => {
      tokenCallCount++;
      const postData = route.request().postData() || "";
      const isRefreshToken = postData.includes("grant_type=refresh_token");

      if (isRefreshToken && options?.tokenFailMode === "revoked") {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({
            error: "invalid_grant",
            error_description: "Refresh token revoked or expired",
          }),
        });
        return;
      }

      if (isRefreshToken && options?.tokenFailMode === "transient") {
        // Fail transient attempts 1 & 2 of silent renewal with 503, then succeed
        if (tokenCallCount <= 3) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ error: "service_unavailable" }),
          });
          return;
        }
      }

      const idToken = mintIdToken(keyCtx, {
        sub: "user-alpha-dev",
        nonce: lastCapturedNonce,
        name: "Alpha Developer",
        preferred_username: "alpha_dev",
        email: "dev@alpha.manta.local",
        tenant_id: "11111111-1111-1111-1111-111111111111",
        auth_time: initialAuthTime,
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: `mock-access-token-alpha-${tokenCallCount}`,
          refresh_token: `mock-refresh-token-alpha-${tokenCallCount}`,
          id_token: idToken,
          token_type: "Bearer",
          expires_in: 300,
          scope: "openid profile email",
        }),
      });
    });

    // 4. Mock backend /api/v1/auth/me
    await page.route("**/api/v1/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: {
          "access-control-allow-origin": "http://localhost:3000",
          "access-control-allow-credentials": "true",
        },
        body: JSON.stringify({
          sub: "user-alpha-dev",
          name: "Alpha Developer",
          preferred_username: "alpha_dev",
          email: "dev@alpha.manta.local",
          tenant_id: "11111111-1111-1111-1111-111111111111",
          roles: ["DEVELOPER"],
        }),
      });
    });

    // Sign in via standard callback flow
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => authRequests.length, { timeout: 10000 }).toBeGreaterThan(0);

    const initialAuth = authRequests[0];
    const capturedState = initialAuth.searchParams.get("state");

    await page.goto(`/auth/callback?code=valid_auth_code&state=${capturedState}`);
    await expect(page).toHaveURL("/", { timeout: 15000 });
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");

    return {
      getAuthRequests: () => authRequests,
      getTokenCallCount: () => tokenCallCount,
    };
  }

  test("AC 1 & 2: Revoked refresh token leads to session expired prompt, no stale tokens remain, no endless loop", async ({
    page,
  }) => {
    await setupAuthenticatedUser(page, { tokenFailMode: "revoked" });

    // Navigate to a sub-page
    await page.goto("/projects");
    await expect(page).toHaveURL("/projects");

    // Ensure the token is within renewal window (< 120s) so signinSilent contacts IdP
    await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          const val = JSON.parse(window.localStorage.getItem(k) || "{}");
          val.expires_at = Math.floor(Date.now() / 1000) + 30;
          window.localStorage.setItem(k, JSON.stringify(val));
        }
      }
    });

    // Trigger silent renew (which will call the mocked token endpoint and receive 400 invalid_grant)
    await page.evaluate(async () => {
      const auth = (window as unknown as { __manta_auth?: { getUserManager: () => { signinSilent: () => Promise<unknown> } } }).__manta_auth;
      if (auth) {
        try {
          await auth.getUserManager().signinSilent();
        } catch {
          // Expected
        }
      }
    });

    // Verify automatic redirect to /login with reason=session_expired and redirect parameter
    await expect(page).toHaveURL(/\/login\?reason=session_expired(&redirect=.*)?/, {
      timeout: 15000,
    });

    // Verify clear session expired alert / badge is visible
    const expiredBadge = page.getByTestId("session-expired-badge");
    const expiredAlert = page.getByTestId("session-expired-alert");
    await expect(expiredBadge).toBeVisible({ timeout: 20000 });
    await expect(expiredAlert).toBeVisible();
    await expect(page.getByRole("heading", { name: "Session Expired" })).toBeVisible();

    // Verify that NO stale oidc tokens remain in localStorage
    const remainingOidcKeys = await page.evaluate(() => {
      const keys: string[] = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          keys.push(k);
        }
      }
      return keys;
    });
    expect(remainingOidcKeys).toEqual([]);
  });

  test("AC 3: Temporary network errors are retried before prompting", async ({ page }) => {
    const session = await setupAuthenticatedUser(page, { tokenFailMode: "transient" });

    // Navigate to /projects
    await page.goto("/projects");
    await expect(page).toHaveURL("/projects");

    // Ensure the token is within renewal window (< 120s) so signinSilent contacts IdP
    await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          const val = JSON.parse(window.localStorage.getItem(k) || "{}");
          val.expires_at = Math.floor(Date.now() / 1000) + 30;
          window.localStorage.setItem(k, JSON.stringify(val));
        }
      }
    });

    // Trigger silent renewal: should encounter 503 twice, retry with backoff, and succeed on 3rd attempt
    const renewResult = await page.evaluate(async () => {
      const auth = (window as unknown as { __manta_auth?: { getUserManager: () => { signinSilent: () => Promise<{ access_token?: string }> } } }).__manta_auth;
      if (auth) {
        const user = await auth.getUserManager().signinSilent();
        return !!user?.access_token;
      }
      return false;
    });

    expect(renewResult).toBe(true);

    // Verify that retries occurred (total token calls > 2)
    expect(session.getTokenCallCount()).toBeGreaterThanOrEqual(3);

    // Verify user remained on the page without being redirected to login
    expect(page.url()).toContain("/projects");
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");
  });

  test("Scope 3: Returns user to the page they were on after re-authentication", async ({
    page,
  }) => {
    const session = await setupAuthenticatedUser(page, { tokenFailMode: "revoked" });

    // Navigate to /refactoring-verification
    await page.goto("/refactoring-verification");
    await expect(page).toHaveURL("/refactoring-verification");

    // Ensure the token is within renewal window (< 120s) so signinSilent contacts IdP
    await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          const val = JSON.parse(window.localStorage.getItem(k) || "{}");
          val.expires_at = Math.floor(Date.now() / 1000) + 30;
          window.localStorage.setItem(k, JSON.stringify(val));
        }
      }
    });

    // Trigger silent renewal failure
    await page.evaluate(async () => {
      const auth = (window as unknown as { __manta_auth?: { getUserManager: () => { signinSilent: () => Promise<unknown> } } }).__manta_auth;
      if (auth) {
        try {
          await auth.getUserManager().signinSilent();
        } catch {
          // Expected
        }
      }
    });

    // Assert landed on session expired screen with redirect to /refactoring-verification
    await expect(page).toHaveURL(
      /\/login\?reason=session_expired&redirect=%2Frefactoring-verification/,
      { timeout: 15000 }
    );

    // Click Sign In Again button on SignedOutCard
    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    await expect(signInAgainBtn).toBeVisible();
    await signInAgainBtn.click();
    await page.waitForURL(/\/login\?redirect=%2Frefactoring-verification/);

    // Now on SignInCard, click Company SSO to complete re-authentication
    const ssoBtn = page.getByTestId("company-sso-button");
    await expect(ssoBtn).toBeVisible();
    const initialRequestsCount = session.getAuthRequests().length;
    await ssoBtn.click();

    await expect.poll(() => session.getAuthRequests().length, { timeout: 10000 }).toBeGreaterThan(
      initialRequestsCount
    );
    const latestAuth = session.getAuthRequests()[session.getAuthRequests().length - 1];
    const capturedState = latestAuth.searchParams.get("state");

    // Complete authorization code exchange via callback
    await page.goto(`/auth/callback?code=fresh_auth_code_2&state=${capturedState}`);

    // Assert user is returned right back to /refactoring-verification!
    await expect(page).toHaveURL("/refactoring-verification", { timeout: 15000 });
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");
  });

  test("Multi-tab sync: SESSION_EXPIRED message purges tokens and prompts login", async ({
    page,
  }) => {
    await setupAuthenticatedUser(page);

    await page.goto("/projects");
    await expect(page).toHaveURL("/projects");

    // Broadcast SESSION_EXPIRED from a simulated sibling tab
    await page.evaluate(() => {
      const channel = new BroadcastChannel("manta_auth_sync");
      channel.postMessage({
        type: "SESSION_EXPIRED",
        tabId: "sibling-tab-uuid",
      });
      channel.close();
    });

    // Verify current tab transitions to /login?reason=session_expired
    await expect(page).toHaveURL(/\/login\?reason=session_expired/, {
      timeout: 15000,
    });

    // Assert tokens purged
    const remainingKeys = await page.evaluate(() => {
      const keys: string[] = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) keys.push(k);
      }
      return keys;
    });
    expect(remainingKeys).toEqual([]);
  });
});

test.describe("Real Browser Silent Renewal & Revocation via Keycloak Admin API (SCRUM-285)", () => {
  const KEYCLOAK_URL = process.env.KEYCLOAK_URL || "http://localhost:8080";
  let useLiveKeycloak = false;

  async function isLiveKeycloakAvailable(): Promise<boolean> {
    if (process.env.FORCE_KEYCLOAK_MOCK === "true") {
      return false;
    }
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2000);
      const res = await fetch(
        `${KEYCLOAK_URL}/realms/manta/.well-known/openid-configuration`,
        { signal: controller.signal }
      );
      clearTimeout(timer);
      return res.ok;
    } catch {
      return false;
    }
  }

  test.beforeAll(async () => {
    useLiveKeycloak = await isLiveKeycloakAvailable();
  });

  test.beforeEach(({}, testInfo) => {
    if (!useLiveKeycloak) {
      testInfo.skip(true, "Keycloak test realm is not running on localhost:8080");
    }
  });

  async function getKeycloakAdminToken(): Promise<string> {
    const res = await fetch(`${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "client_id=admin-cli&username=admin&password=admin&grant_type=password",
    });
    if (!res.ok) {
      throw new Error(`Failed to obtain Keycloak admin token: ${res.status} ${await res.text()}`);
    }
    const data = await res.json();
    return data.access_token;
  }

  async function revokeKeycloakUserSessions(username: string): Promise<void> {
    const adminToken = await getKeycloakAdminToken();
    const searchRes = await fetch(
      `${KEYCLOAK_URL}/admin/realms/manta/users?username=${encodeURIComponent(username)}`,
      {
        headers: { Authorization: `Bearer ${adminToken}` },
      }
    );
    if (!searchRes.ok) {
      throw new Error(`Failed to search user ${username}: ${searchRes.status} ${await searchRes.text()}`);
    }
    const users = await searchRes.json();
    if (!users || users.length === 0) {
      throw new Error(`User ${username} not found in Keycloak manta realm`);
    }
    const userId = users[0].id;
    const logoutRes = await fetch(
      `${KEYCLOAK_URL}/admin/realms/manta/users/${encodeURIComponent(userId)}/logout`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${adminToken}` },
      }
    );
    if (!logoutRes.ok && logoutRes.status !== 204) {
      throw new Error(`Failed to revoke sessions for ${username}: ${logoutRes.status} ${await logoutRes.text()}`);
    }
  }

  async function loginViaKeycloak(page: Page, username = "alpha_dev", password = "dev123"): Promise<void> {
    await page.goto("/login");
    const ssoButton = page.getByTestId("company-sso-button");
    await expect(ssoButton).toBeVisible({ timeout: 15000 });
    await ssoButton.click();

    const usernameInput = page.locator("#username");
    const passwordInput = page.locator("#password");
    const kcLoginBtn = page.locator("#kc-login");

    await expect(usernameInput).toBeVisible({ timeout: 15000 });
    await usernameInput.fill(username);
    await passwordInput.fill(password);
    await kcLoginBtn.click();

    await expect(page).toHaveURL("/", { timeout: 30000 });
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer", {
      timeout: 15000,
    });
  }

  test("Test 1: Session stays alive past the short token lifetime with silent renewal", async ({
    page,
  }) => {
    test.setTimeout(60000);

    // 1. Log in via real Keycloak test realm
    await loginViaKeycloak(page, "alpha_dev", "dev123");

    // 2. Capture initial token details from localStorage
    const initialTokenInfo = await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          const val = JSON.parse(window.localStorage.getItem(k) || "{}");
          return {
            key: k,
            accessToken: val.access_token as string,
            refreshToken: val.refresh_token as string,
            expiresAt: val.expires_at as number,
          };
        }
      }
      return null;
    });

    expect(initialTokenInfo).not.toBeNull();
    expect(initialTokenInfo?.accessToken).toBeDefined();
    expect(initialTokenInfo?.refreshToken).toBeDefined();

    // 3. Simulate passage of short token lifetime by adjusting expires_at in localStorage
    // (access token expired 5 seconds ago, well within SSO session idle timeout of 120s)
    const simulatedExpiredAt = Math.floor(Date.now() / 1000) - 5;
    await page.evaluate(({ storageKey, expiredAt }) => {
      const val = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
      val.expires_at = expiredAt;
      window.localStorage.setItem(storageKey, JSON.stringify(val));
    }, { storageKey: initialTokenInfo!.key, expiredAt: simulatedExpiredAt });

    // 4. Set up event-driven network listener for the silent refresh token exchange with Keycloak
    let renewalResponseBody: { access_token?: string; expires_in?: number } | null = null;
    const renewalResponsePromise = page.waitForResponse(
      async (res) => {
        if (
          res.url().includes("/realms/manta/protocol/openid-connect/token") &&
          res.request().postData()?.includes("grant_type=refresh_token") &&
          res.status() === 200
        ) {
          try {
            renewalResponseBody = await res.json();
          } catch {
            // ignore
          }
          return true;
        }
        return false;
      },
      { timeout: 30000 }
    );

    // 5. Trigger silent renewal via userManager without arbitrary sleeps
    await page.evaluate(async () => {
      const auth = (window as unknown as {
        __manta_auth?: { getUserManager: () => { signinSilent: () => Promise<unknown> } };
      }).__manta_auth;
      if (auth) {
        await auth.getUserManager().signinSilent();
      }
    });

    // 6. Await and assert successful token response from Keycloak
    const renewalResponse = await renewalResponsePromise;
    expect(renewalResponse.status()).toBe(200);
    expect(renewalResponseBody?.access_token).toBeDefined();
    expect(renewalResponseBody?.expires_in).toBeGreaterThanOrEqual(45);
    expect(renewalResponseBody?.expires_in).toBeLessThanOrEqual(60);

    // 7. Verify localStorage is updated with new access token and extended expires_at
    const renewedTokenInfo = await page.evaluate((storageKey) => {
      const val = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
      return {
        accessToken: val.access_token as string,
        refreshToken: val.refresh_token as string,
        expiresAt: val.expires_at as number,
      };
    }, initialTokenInfo!.key);

    expect(renewedTokenInfo.accessToken).toBeDefined();
    expect(renewedTokenInfo.accessToken).not.toEqual(initialTokenInfo!.accessToken);
    expect(renewedTokenInfo.expiresAt).toBeGreaterThan(simulatedExpiredAt);
    expect(renewedTokenInfo.expiresAt).toBeGreaterThan(Math.floor(Date.now() / 1000));

    // 8. Assert user session remains completely alive and valid on protected pages
    await page.goto("/projects");
    await expect(page).toHaveURL("/projects");
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");
    await expect(page.getByTestId("user-tenant-badge")).toContainText("Tenant Alpha");
  });

  test("Test 2: Revoke session through Keycloak admin API and assert re-authentication prompt appears", async ({
    page,
  }) => {
    test.setTimeout(60000);

    // 1. Log in via real Keycloak test realm
    await loginViaKeycloak(page, "alpha_dev", "dev123");

    // 2. Navigate to /projects
    await page.goto("/projects");
    await expect(page).toHaveURL("/projects");
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");

    // 3. Revoke all sessions for alpha_dev via Keycloak Admin API
    await revokeKeycloakUserSessions("alpha_dev");

    // 4. Update expires_at to within renewal threshold (< 120s) so signinSilent contacts Keycloak
    await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          const val = JSON.parse(window.localStorage.getItem(k) || "{}");
          val.expires_at = Math.floor(Date.now() / 1000) + 10;
          window.localStorage.setItem(k, JSON.stringify(val));
        }
      }
    });

    // 5. Set up event-driven listeners for:
    //    a) Keycloak token rejection (HTTP 400 invalid_grant)
    //    b) Navigation redirect to session expired screen
    const tokenRejectionPromise = page.waitForResponse(
      (res) =>
        res.url().includes("/realms/manta/protocol/openid-connect/token") &&
        res.request().postData()?.includes("grant_type=refresh_token") &&
        res.status() === 400,
      { timeout: 30000 }
    );

    const redirectPromise = page.waitForURL(
      /\/login\?reason=session_expired(&redirect=.*)?/,
      { timeout: 30000, waitUntil: "domcontentloaded" }
    );

    // 6. Trigger silent renew without blocking evaluate on destroyed context
    page.evaluate(() => {
      const auth = (window as unknown as {
        __manta_auth?: { getUserManager: () => { signinSilent: () => Promise<unknown> } };
      }).__manta_auth;
      if (auth) {
        auth.getUserManager().signinSilent().catch(() => {
          // Expected rejection
        });
      }
    });

    // 7. Verify Keycloak rejected the revoked session with HTTP 400
    const rejectionResponse = await tokenRejectionPromise;
    expect(rejectionResponse.status()).toBe(400);

    // 8. Verify browser was cleanly redirected to session expired screen with return path
    await redirectPromise;
    expect(page.url()).toContain("redirect=%2Fprojects");

    // 9. Assert re-authentication UI prompt appears
    const sessionExpiredHeading = page.getByRole("heading", { name: "Session Expired" });
    const expiredBadge = page.getByTestId("session-expired-badge");
    const expiredAlert = page.getByTestId("session-expired-alert");
    const signInAgainButton = page.getByTestId("sign-in-again-button");

    await expect(sessionExpiredHeading).toBeVisible({ timeout: 15000 });
    await expect(expiredBadge).toBeVisible();
    await expect(expiredBadge).toContainText("SESSION EXPIRED");
    await expect(expiredAlert).toBeVisible();
    await expect(signInAgainButton).toBeVisible();

    // 10. Assert all oidc tokens were purged from localStorage
    const remainingOidcKeys = await page.evaluate(() => {
      const keys: string[] = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) {
          keys.push(k);
        }
      }
      return keys;
    });
    expect(remainingOidcKeys).toEqual([]);

    // 11. Assert re-authentication prompt action initiates fresh login and preserves return URL
    await signInAgainButton.click();
    const ssoBtn = page.getByTestId("company-sso-button");
    await expect(ssoBtn).toBeVisible({ timeout: 15000 });
    await ssoBtn.click();

    // Keycloak prompts for credentials because the previous SSO session was revoked
    const usernameInput = page.locator("#username");
    const passwordInput = page.locator("#password");
    const kcLoginBtn = page.locator("#kc-login");

    await expect(usernameInput).toBeVisible({ timeout: 15000 });
    await usernameInput.fill("alpha_dev");
    await passwordInput.fill("dev123");
    await kcLoginBtn.click();

    // Successfully returned right back to /projects!
    await expect(page).toHaveURL("/projects", { timeout: 30000 });
    await expect(page.getByTestId("user-profile-name")).toContainText("Alpha Developer");
  });
});

