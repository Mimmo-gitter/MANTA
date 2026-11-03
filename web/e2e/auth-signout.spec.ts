import { test, expect } from "@playwright/test";
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

function createTestKey(kid = "test-signout-jwt-key"): TestKeyContext {
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
    auth_time: now,
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

test.describe("OIDC Sign-Out & Session Termination (SCRUM-275)", () => {
  let keyCtx: TestKeyContext;

  test.beforeAll(() => {
    keyCtx = createTestKey("manta-signout-test-key-2026");
  });

  interface UserSessionContext {
    authRequests: URL[];
    logoutRequests: URL[];
  }

  async function setupAuthenticatedUser(
    page: import("@playwright/test").Page
  ): Promise<UserSessionContext> {
    const authRequests: URL[] = [];
    const logoutRequests: URL[] = [];
    let lastCapturedNonce = "test-nonce";

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
      const idToken = mintIdToken(keyCtx, {
        sub: "user-alpha-dev",
        nonce: lastCapturedNonce,
        name: "Alpha Developer",
        preferred_username: "alpha_dev",
        email: "dev@alpha.manta.local",
        tenant_id: "11111111-1111-1111-1111-111111111111",
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "mock-access-token-alpha",
          id_token: idToken,
          token_type: "Bearer",
          expires_in: 3600,
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

    // 5. Mock Keycloak logout endpoint
    await page.route("**/protocol/openid-connect/logout*", async (route) => {
      const u = new URL(route.request().url());
      logoutRequests.push(u);
      const postLogoutRedirectUri =
        u.searchParams.get("post_logout_redirect_uri") || "/signed-out";

      await route.fulfill({
        status: 302,
        headers: {
          Location: postLogoutRedirectUri,
        },
      });
    });

    // Perform sign-in via callback
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => authRequests.length, { timeout: 10000 }).toBeGreaterThan(0);

    const initialAuth = authRequests[0];
    const capturedState = initialAuth.searchParams.get("state");

    await page.goto(`/auth/callback?code=valid_auth_code&state=${capturedState}`);
    await expect(page).toHaveURL("/", { timeout: 15000 });

    const profileName = page.getByTestId("user-profile-name");
    const tenantBadge = page.getByTestId("user-tenant-badge");
    const signOutBtn = page.getByTestId("header-signout-button");

    await expect(profileName).toBeVisible();
    await expect(profileName).toContainText("Alpha Developer");
    await expect(tenantBadge).toBeVisible();
    await expect(tenantBadge).toContainText("Tenant Alpha");
    await expect(page.getByTestId("header-signout-button")).not.toBeVisible();

    return { authRequests, logoutRequests };
  }

  test("AC 1: Nominal Sign-Out triggers Keycloak RP-Initiated logout with id_token_hint and purges storage", async ({
    page,
  }) => {
    const { logoutRequests } = await setupAuthenticatedUser(page);

    // Verify localStorage has OIDC tokens prior to sign out
    const tokenCountBefore = await page.evaluate(() => {
      let count = 0;
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && (k.startsWith("oidc.") || k.startsWith("manta."))) count++;
      }
      return count;
    });
    expect(tokenCountBefore).toBeGreaterThan(0);

    // Click Sign Out button in dropdown menu
    await page.getByTestId("user-profile-menu-trigger").click();
    const dropdownSignOut = page.getByTestId("dropdown-signout-button");
    await expect(dropdownSignOut).toBeVisible();
    await dropdownSignOut.click();

    // Verify browser is redirected to /signed-out via post_logout_redirect_uri
    await expect(page).toHaveURL(/\/signed-out/, { timeout: 10000 });
    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    await expect(signInAgainBtn).toBeVisible();

    // Verify Keycloak end_session_endpoint was invoked with mandatory OIDC parameters
    await expect.poll(() => logoutRequests.length, { timeout: 5000 }).toBe(1);
    const logoutUrl = logoutRequests[0];

    expect(logoutUrl.pathname).toContain("/realms/manta/protocol/openid-connect/logout");
    expect(logoutUrl.searchParams.get("post_logout_redirect_uri")).toContain("/signed-out");
    const idTokenHint = logoutUrl.searchParams.get("id_token_hint");
    expect(idTokenHint).toBeTruthy();

    // Verify clicking Sign In Again takes user to /login
    await signInAgainBtn.click();
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
    await expect(page.getByTestId("company-sso-button")).toBeVisible();

    // Verify complete purging of local tokens & session state in localStorage
    const remainingKeys = await page.evaluate(() => {
      const keys: string[] = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && (k.startsWith("oidc.") || k.startsWith("manta."))) keys.push(k);
      }
      return keys;
    });
    expect(remainingKeys).toEqual([]);
  });

  test("AC 2: Back-button and Page Reload after Sign-Out do NOT restore user session", async ({
    page,
  }) => {
    await setupAuthenticatedUser(page);

    // Perform sign out via dropdown
    await page.getByTestId("user-profile-menu-trigger").click();
    await page.getByTestId("dropdown-signout-button").click();
    await expect(page).toHaveURL(/\/signed-out/, { timeout: 10000 });

    // 1. Test Browser Back Button
    await page.goBack();

    // User session MUST remain terminated regardless of page navigated to
    await expect(page.getByTestId("user-profile-name")).not.toBeVisible();
    await expect(page.getByTestId("user-tenant-badge")).not.toBeVisible();
    await expect(page.getByTestId("header-signout-button")).not.toBeVisible();

    // Storage must remain clean
    const oidcUser = await page.evaluate(() => {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("oidc.user:")) return window.localStorage.getItem(k);
      }
      return null;
    });
    expect(oidcUser).toBeNull();

    // 2. Test Page Reload (F5)
    await page.reload();

    // Verify session is still not restored
    await expect(page.getByTestId("user-profile-name")).not.toBeVisible();
    await expect(page.getByTestId("user-tenant-badge")).not.toBeVisible();
    await expect(page.getByTestId("header-signout-button")).not.toBeVisible();
  });

  test("AC 3: Subsequent Sign-In attempt generates a fresh PKCE challenge and requires re-authentication", async ({
    page,
  }) => {
    const { authRequests, logoutRequests } = await setupAuthenticatedUser(page);

    // Sign out via dropdown
    await page.getByTestId("user-profile-menu-trigger").click();
    await page.getByTestId("dropdown-signout-button").click();
    await expect(page).toHaveURL(/\/signed-out/, { timeout: 10000 });
    expect(logoutRequests.length).toBe(1);

    // Record initial auth request details
    const initialAuthRequest = authRequests[0];
    const initialChallenge = initialAuthRequest.searchParams.get("code_challenge");
    const initialState = initialAuthRequest.searchParams.get("state");
    const initialNonce = initialAuthRequest.searchParams.get("nonce");

    // Click "Sign In Again" on the signed-out confirmation screen to reach /login
    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    await expect(signInAgainBtn).toBeVisible();
    await signInAgainBtn.click();
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });

    // Click SSO button again to initiate re-authentication after page is interactive
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);
    const ssoBtn = page.getByTestId("company-sso-button");
    await expect(ssoBtn).toBeVisible();
    await ssoBtn.click();

    // Verify second fresh OIDC authorization request is dispatched
    await expect.poll(() => authRequests.length, { timeout: 10000 }).toBe(2);
    const freshAuthUrl = authRequests[1];

    expect(freshAuthUrl.pathname).toContain("/realms/manta/protocol/openid-connect/auth");
    expect(freshAuthUrl.searchParams.get("code_challenge_method")).toBe("S256");

    const freshChallenge = freshAuthUrl.searchParams.get("code_challenge");
    const freshState = freshAuthUrl.searchParams.get("state");
    const freshNonce = freshAuthUrl.searchParams.get("nonce");

    expect(freshChallenge).toBeTruthy();
    expect(freshState).toBeTruthy();
    expect(freshNonce).toBeTruthy();

    // Cryptographic proofs must be fresh and non-replayed
    expect(freshChallenge).not.toBe(initialChallenge);
    expect(freshState).not.toBe(initialState);
    expect(freshNonce).not.toBe(initialNonce);
  });
});
