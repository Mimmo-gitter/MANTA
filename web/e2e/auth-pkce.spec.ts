import { test, expect } from "@playwright/test";

test.describe("OIDC Authorization Code Flow with PKCE (SCRUM-273)", () => {
  test("header Sign In button navigates to /login page", async ({ page }) => {
    await page.goto("/");
    const headerSignIn = page.getByTestId("header-signin-button");
    await expect(headerSignIn).toBeVisible();
    await headerSignIn.click();

    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
    await expect(page.getByRole("heading", { name: "M.A.N.T.A." })).toBeVisible();
    await expect(page.getByText("Engineering Intelligence Platform")).toBeVisible();
  });

  test("clicking SSO button redirects to Keycloak with valid PKCE S256 parameters", async ({
    page,
  }) => {
    let capturedAuthUrl: URL | null = null;

    // Intercept redirect to Keycloak auth endpoint so the test succeeds without requiring a live Keycloak server
    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      capturedAuthUrl = new URL(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Keycloak Login</body></html>",
      });
    });

    await page.goto("/login");
    const ssoButton = page.getByTestId("company-sso-button");
    await expect(ssoButton).toBeVisible();

    await ssoButton.click();

    // Wait until request is triggered
    await expect.poll(() => capturedAuthUrl, { timeout: 10000 }).not.toBeNull();

    expect(capturedAuthUrl).not.toBeNull();
    const url = capturedAuthUrl!;

    // 1. Verify correct endpoint
    expect(url.pathname).toContain("/realms/manta/protocol/openid-connect/auth");

    // 2. Verify mandatory OIDC parameters
    expect(url.searchParams.get("client_id")).toBe("manta-portal");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("redirect_uri")).toContain("/auth/callback");
    expect(url.searchParams.get("scope")).toContain("openid");

    // 3. Verify PKCE S256 parameters
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    const codeChallenge = url.searchParams.get("code_challenge");
    expect(codeChallenge).toBeTruthy();
    // base64url encoded SHA-256 hash has length 43
    expect(codeChallenge).toMatch(/^[A-Za-z0-9_-]{43}$/);

    // 4. Verify state and nonce
    const state = url.searchParams.get("state");
    const nonce = url.searchParams.get("nonce");
    expect(state).toBeTruthy();
    expect(nonce).toBeTruthy();

    // 5. Verify NO client_secret is exposed in URL or query params
    expect(url.searchParams.get("client_secret")).toBeNull();
  });

  test("each sign-in attempt generates fresh state and code_challenge", async ({
    page,
  }) => {
    const capturedChallenges: string[] = [];
    const capturedStates: string[] = [];

    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      const u = new URL(route.request().url());
      capturedChallenges.push(u.searchParams.get("code_challenge") || "");
      capturedStates.push(u.searchParams.get("state") || "");
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Keycloak</body></html>",
      });
    });

    // First attempt
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => capturedChallenges.length, { timeout: 10000 }).toBe(1);

    // Second attempt
    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();
    await expect.poll(() => capturedChallenges.length, { timeout: 10000 }).toBe(2);

    expect(capturedChallenges[0]).not.toBe(capturedChallenges[1]);
    expect(capturedStates[0]).not.toBe(capturedStates[1]);
  });

  test("stores PKCE verifier and state in sessionStorage", async ({ page }) => {
    let capturedStorageValue: string | null = null;

    // Expose a function to capture sessionStorage writes before cross-origin navigation
    await page.exposeFunction("onSessionStorageItemSet", (key: string, val: string) => {
      if (key.startsWith("oidc.")) {
        capturedStorageValue = val;
      }
    });

    await page.addInitScript(() => {
      const originalSetItem = sessionStorage.setItem.bind(sessionStorage);
      sessionStorage.setItem = (key: string, value: string) => {
        originalSetItem(key, value);
        // Notify test runner via exposed function
        const fn = (window as unknown as { onSessionStorageItemSet?: (k: string, v: string) => void })
          .onSessionStorageItemSet;
        if (fn) {
          fn(key, value);
        }
      };
    });

    await page.route("**/protocol/openid-connect/auth*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>Mock Keycloak</body></html>",
      });
    });

    await page.goto("/login");
    await page.getByTestId("company-sso-button").click();

    // Await captured sessionStorage entry
    await expect.poll(() => capturedStorageValue, { timeout: 10000 }).not.toBeNull();

    expect(capturedStorageValue).toBeTruthy();
    const parsed = JSON.parse(capturedStorageValue!);
    expect(parsed.code_verifier).toBeTruthy();
    expect(parsed.client_id).toBe("manta-portal");
    expect(parsed.nonce).toBeTruthy();
  });

  test("renders Figma error alert when query param error=failed is provided", async ({
    page,
  }) => {
    await page.goto("/login?error=failed");

    const alert = page.getByTestId("auth-error-alert");
    await expect(alert).toBeVisible();
    await expect(page.getByText("Authentication failed")).toBeVisible();
    await expect(
      page.getByText("Invalid credentials or SSO sign-in was cancelled.")
    ).toBeVisible();
  });
});
