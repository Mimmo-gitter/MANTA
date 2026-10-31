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

function createTestKey(kid = "test-jwt-key"): TestKeyContext {
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

test.describe("OIDC Callback Handling & Multi-Tenant Dashboard (SCRUM-274)", () => {
  let keyCtx: TestKeyContext;

  test.beforeAll(() => {
    keyCtx = createTestKey("manta-test-key-2026");
  });

  test("AC 1: Nominal callback exchanges code, validates tokens, and shows user name and Tenant Alpha", async ({
    page,
  }) => {
    let capturedState: string | null = null;
    let capturedNonce: string | null = null;

    // 1. Mock Keycloak JWKS certs endpoint
    await page.route("**/protocol/openid-connect/certs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
      });
    });

    // 2. Mock Keycloak authorization endpoint to capture PKCE state and nonce
    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      const u = new URL(route.request().url());
      capturedState = u.searchParams.get("state");
      capturedNonce = u.searchParams.get("nonce");
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
        nonce: capturedNonce || "test-nonce",
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

    // Initiate sign in from /login
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => capturedState, { timeout: 10000 }).not.toBeNull();

    // Emulate browser redirect to /auth/callback with valid code and state
    await page.goto(`/auth/callback?code=valid_auth_code&state=${capturedState}`);

    // Verify redirect to dashboard
    await expect(page).toHaveURL("/", { timeout: 15000 });

    // Verify user name and tenant badge in dashboard header
    const profileName = page.getByTestId("user-profile-name");
    const tenantBadge = page.getByTestId("user-tenant-badge");
    const signOutBtn = page.getByTestId("header-signout-button");

    await expect(profileName).toBeVisible();
    await expect(profileName).toContainText("Alpha Developer");

    await expect(tenantBadge).toBeVisible();
    await expect(tenantBadge).toContainText("Tenant Alpha");

    await expect(signOutBtn).toBeVisible();
  });

  test("AC 3: Users from Tenant Beta see their own user name and Tenant Beta badge", async ({
    page,
  }) => {
    let capturedState: string | null = null;
    let capturedNonce: string | null = null;

    await page.route("**/protocol/openid-connect/certs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
      });
    });

    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      const u = new URL(route.request().url());
      capturedState = u.searchParams.get("state");
      capturedNonce = u.searchParams.get("nonce");
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Authorization</body></html>",
      });
    });

    await page.route("**/protocol/openid-connect/token", async (route) => {
      const idToken = mintIdToken(keyCtx, {
        sub: "user-beta-dev",
        nonce: capturedNonce || "test-nonce",
        name: "Beta Developer",
        preferred_username: "beta_dev",
        email: "dev@beta.manta.local",
        tenant_id: "22222222-2222-2222-2222-222222222222",
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "mock-access-token-beta",
          id_token: idToken,
          token_type: "Bearer",
          expires_in: 3600,
          scope: "openid profile email",
        }),
      });
    });

    await page.route("**/api/v1/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: {
          "access-control-allow-origin": "http://localhost:3000",
          "access-control-allow-credentials": "true",
        },
        body: JSON.stringify({
          sub: "user-beta-dev",
          name: "Beta Developer",
          preferred_username: "beta_dev",
          email: "dev@beta.manta.local",
          tenant_id: "22222222-2222-2222-2222-222222222222",
          roles: ["DEVELOPER"],
        }),
      });
    });

    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => capturedState, { timeout: 10000 }).not.toBeNull();

    await page.goto(`/auth/callback?code=beta_auth_code&state=${capturedState}`);
    await expect(page).toHaveURL("/", { timeout: 15000 });

    const profileName = page.getByTestId("user-profile-name");
    const tenantBadge = page.getByTestId("user-tenant-badge");

    await expect(profileName).toBeVisible();
    await expect(profileName).toContainText("Beta Developer");

    await expect(tenantBadge).toBeVisible();
    await expect(tenantBadge).toContainText("Tenant Beta");
  });

  test("AC 2: Mismatched state aborts the login with a clear error message", async ({
    page,
  }) => {
    // Navigate directly with an untrusted / forged state
    await page.goto("/auth/callback?code=some_code&state=forged_state_9999");

    // Must abort login and redirect back to /login with error query param
    await expect(page).toHaveURL(/\/login\?error=/, { timeout: 10000 });

    // Verify clear error alert is displayed in Sign In card
    const errorAlert = page.getByTestId("auth-error-alert");
    await expect(errorAlert).toBeVisible();
    await expect(page.getByText("Authentication failed")).toBeVisible();
    await expect(
      page.getByText("Login aborted: Invalid or mismatched authentication state")
    ).toBeVisible();
  });

  test("AC 2: Mismatched nonce aborts the login with a clear error message", async ({
    page,
  }) => {
    let capturedState: string | null = null;

    await page.route("**/protocol/openid-connect/certs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
      });
    });

    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      const u = new URL(route.request().url());
      capturedState = u.searchParams.get("state");
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Authorization</body></html>",
      });
    });

    // Return an ID token with mismatched / tampered nonce
    await page.route("**/protocol/openid-connect/token", async (route) => {
      const idToken = mintIdToken(keyCtx, {
        sub: "user-alpha-dev",
        nonce: "wrong_tampered_nonce_xyz", // Mismatched nonce!
        name: "Alpha Developer",
        preferred_username: "alpha_dev",
        email: "dev@alpha.manta.local",
        tenant_id: "11111111-1111-1111-1111-111111111111",
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "mock-access-token",
          id_token: idToken,
          token_type: "Bearer",
          expires_in: 3600,
        }),
      });
    });

    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => capturedState, { timeout: 10000 }).not.toBeNull();

    await page.goto(`/auth/callback?code=mock_code&state=${capturedState}`);

    // Must abort login and redirect to /login
    await expect(page).toHaveURL(/\/login\?error=/, { timeout: 10000 });

    const errorAlert = page.getByTestId("auth-error-alert");
    await expect(errorAlert).toBeVisible();
    await expect(page.getByText("Authentication failed")).toBeVisible();
    await expect(
      page.getByText("Login aborted: Cryptographic nonce mismatch in ID token")
    ).toBeVisible();
  });

  test("Sign Out button clears user session and restores unauthenticated header", async ({
    page,
  }) => {
    let capturedState: string | null = null;
    let capturedNonce: string | null = null;

    await page.route("**/protocol/openid-connect/certs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
      });
    });

    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      const u = new URL(route.request().url());
      capturedState = u.searchParams.get("state");
      capturedNonce = u.searchParams.get("nonce");
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Authorization</body></html>",
      });
    });

    await page.route("**/protocol/openid-connect/token", async (route) => {
      const idToken = mintIdToken(keyCtx, {
        sub: "user-alpha-dev",
        nonce: capturedNonce || "test-nonce",
        name: "Alpha Developer",
        preferred_username: "alpha_dev",
        email: "dev@alpha.manta.local",
        tenant_id: "11111111-1111-1111-1111-111111111111",
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "mock-access-token",
          id_token: idToken,
          token_type: "Bearer",
          expires_in: 3600,
        }),
      });
    });

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
          tenant_id: "11111111-1111-1111-1111-111111111111",
          roles: ["DEVELOPER"],
        }),
      });
    });

    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => capturedState, { timeout: 10000 }).not.toBeNull();

    await page.goto(`/auth/callback?code=mock_code&state=${capturedState}`);
    await expect(page).toHaveURL("/", { timeout: 15000 });

    const signOutBtn = page.getByTestId("header-signout-button");
    await expect(signOutBtn).toBeVisible();
    await signOutBtn.click();

    // Verify user profile elements are gone and header shows Sign In button
    const headerSignIn = page.getByTestId("header-signin-button");
    await expect(headerSignIn).toBeVisible();
    await expect(page.getByTestId("user-profile-name")).not.toBeVisible();
    await expect(page.getByTestId("user-tenant-badge")).not.toBeVisible();
  });
});
