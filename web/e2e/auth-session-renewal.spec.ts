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
          expires_in: 60,
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
    await expect(expiredBadge).toBeVisible({ timeout: 10000 });
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
