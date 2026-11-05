import { test, expect, type Page } from "@playwright/test";
import crypto from "crypto";

interface KeyContext {
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

function createTestKey(kid = "manta-reg-test-key"): KeyContext {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", {
    modulusLength: 2048,
  });
  const jwk = publicKey.export({ format: "jwk" }) as Record<string, unknown>;
  jwk.kid = kid;
  jwk.use = "sig";
  jwk.alg = "RS256";
  return { privateKey, publicKeyJwk: jwk, kid };
}

function signJwt(keyCtx: KeyContext, payload: Record<string, unknown>): string {
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

function renderKeycloakLoginForm(actionUrl: string, cancelUrl: string, initialUsername = ""): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Sign in to MANTA Intelligence Platform</title>
</head>
<body>
  <main id="kc-content">
    <h1>MANTA Intelligence Platform</h1>
    <form id="kc-form-login" method="post" action="${actionUrl}">
      <div>
        <label for="username">Username or email</label>
        <input tabindex="1" id="username" name="username" type="text" value="${initialUsername}" autofocus autocomplete="off" />
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

test.describe("Tenant / Organization Registration Flow (SCRUM-243 / US-7)", () => {
  let keyCtx: KeyContext;

  test.beforeAll(() => {
    keyCtx = createTestKey("reg-test-key-2026");
  });

  test.beforeEach(async ({ page }) => {
    // Intercept default backend registration endpoint with mock fallback
    await page.route("**/tenants/register", async (route) => {
      if (route.request().method() === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        const uniqueId = crypto.randomUUID().slice(0, 8);
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            tenant_id: `tenant-${uniqueId}`,
            admin_id: `admin-${uniqueId}`,
            slug: body.slug || `org-${uniqueId}`,
            name: body.name || `Org ${uniqueId}`,
            admin_email: body.admin_email || `admin@${uniqueId}.manta.local`,
            message: "Tenant provisioned successfully",
          }),
        });
      } else {
        await route.continue();
      }
    });
  });

  test("AC 1: Full 3-step registration happy path with API submission & success state", async ({
    page,
  }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    const orgName = `Acme Corporation ${uniqueSuffix}`;
    const orgSlug = `acme-corp-${uniqueSuffix}`;
    const adminEmail = `admin@${uniqueSuffix}.acmecorp.com`;
    const adminName = "Wile E. Coyote";

    let capturedPayload: any = null;
    await page.route("**/tenants/register", async (route) => {
      capturedPayload = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          tenant_id: `t-${uniqueSuffix}`,
          slug: orgSlug,
          name: orgName,
          admin_email: adminEmail,
          message: "Tenant registered",
        }),
      });
    });

    await page.goto("/register");

    // 1. Verify Step 1 elements
    const step1Pill = page.getByTestId("step1-status-pill");
    await expect(step1Pill).toBeVisible();
    await expect(step1Pill).toContainText("STEP 1 OF 3 · TENANT PROFILE");

    const orgInput = page.locator("#org-name");
    const slugInput = page.locator("#org-slug");
    const continueBtn1 = page.getByTestId("step1-continue-button");

    await expect(continueBtn1).toBeDisabled();

    // Type organization name -> test auto-slug generation
    await orgInput.fill(orgName);
    await expect(slugInput).toHaveValue(
      orgName
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, "")
        .trim()
        .replace(/[\s-]+/g, "-")
    );

    // Adjust slug manually to specific unique slug
    await slugInput.fill(orgSlug);
    await expect(continueBtn1).toBeEnabled();

    // Tap target check (>= 44px)
    const btnBox1 = await continueBtn1.boundingBox();
    expect(btnBox1?.height).toBeGreaterThanOrEqual(44);

    await continueBtn1.click();

    // 2. Verify Step 2 elements & dynamic password policy
    const step2Pill = page.getByTestId("step2-status-pill");
    await expect(step2Pill).toBeVisible();
    await expect(step2Pill).toContainText("STEP 2 OF 3 · ADMIN CREDENTIALS");

    const adminNameInput = page.locator("#admin-name");
    const adminEmailInput = page.locator("#admin-email");
    const passwordInput = page.locator("#create-password");
    const confirmInput = page.locator("#confirm-password");
    const continueBtn2 = page.getByTestId("step2-continue-button");

    await expect(continueBtn2).toBeDisabled();

    await adminNameInput.fill(adminName);
    await adminEmailInput.fill(adminEmail);

    // Test real-time password checklist rules
    const ruleMinLength = page.getByTestId("rule-min-length");
    const ruleUppercase = page.getByTestId("rule-uppercase");
    const ruleNumber = page.getByTestId("rule-number");
    const ruleSymbol = page.getByTestId("rule-symbol");

    // Initially all unchecked / neutral
    await passwordInput.fill("short");
    await expect(ruleMinLength).toContainText("✗");

    // Fulfill all password complexity requirements: 12+ chars, uppercase, number, symbol
    await passwordInput.fill("SuperSecret!2026");
    await expect(ruleMinLength).toContainText("✓");
    await expect(ruleUppercase).toContainText("✓");
    await expect(ruleNumber).toContainText("✓");
    await expect(ruleSymbol).toContainText("✓");

    // Mismatched confirm password keeps continue disabled
    await confirmInput.fill("WrongPassword!2026");
    await expect(continueBtn2).toBeDisabled();

    // Matching confirm password enables continue
    await confirmInput.fill("SuperSecret!2026");
    await expect(continueBtn2).toBeEnabled();

    await continueBtn2.click();

    // 3. Verify Step 3 elements
    const step3Pill = page.getByTestId("step3-status-pill");
    await expect(step3Pill).toBeVisible();
    await expect(step3Pill).toContainText("STEP 3 OF 3 · COLLABORATION");

    const invite1 = page.locator("#invite-email-0");
    await invite1.fill(`teammate1@${uniqueSuffix}.acmecorp.com`);

    const submitBtn = page.getByTestId("step3-submit-button");
    await expect(submitBtn).toBeEnabled();

    await submitBtn.click();

    // Verify backend received exact contract
    expect(capturedPayload).not.toBeNull();
    expect(capturedPayload.name).toBe(orgName);
    expect(capturedPayload.slug).toBe(orgSlug);
    expect(capturedPayload.admin_name).toBe(adminName);
    expect(capturedPayload.admin_email).toBe(adminEmail);
    expect(capturedPayload.allowed_domain).toBe(`${uniqueSuffix}.acmecorp.com`);

    // 4. Verify Success State (Figma 491:1156)
    const successCard = page.getByTestId("register-success-card");
    await expect(successCard).toBeVisible();

    const successPill = page.getByTestId("success-status-pill");
    await expect(successPill).toContainText("PROVISIONING COMPLETE");

    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      `${orgName} Workspace Ready`
    );
    await expect(page.getByText(`${orgSlug}_schema`)).toBeVisible();
    await expect(page.getByText(`manta.ai/app/${orgSlug}`)).toBeVisible();
    await expect(page.getByText("RLS: ENFORCED")).toBeVisible();

    // Launch Workspace Dashboard Button
    const launchBtn = page.getByTestId("launch-dashboard-button");
    await expect(launchBtn).toBeVisible();
    const href = await launchBtn.getAttribute("href");
    expect(href).toContain(`/login?workspace=${encodeURIComponent(orgSlug)}`);
    expect(href).toContain(`email=${encodeURIComponent(adminEmail)}`);
  });

  test("AC 1b: Skip team invites directly provisions the tenant", async ({ page }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    const orgName = `Quick Start Org ${uniqueSuffix}`;
    const orgSlug = `quick-start-org-${uniqueSuffix}`;
    const adminEmail = `dev@${uniqueSuffix}.quickstart.io`;

    let capturedPayload: any = null;
    await page.route("**/tenants/register", async (route) => {
      capturedPayload = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          tenant_id: `t-skip-${uniqueSuffix}`,
          slug: orgSlug,
          name: orgName,
          admin_email: adminEmail,
        }),
      });
    });

    await page.goto("/register");

    // Step 1
    await page.locator("#org-name").fill(orgName);
    await page.getByTestId("step1-continue-button").click();

    // Step 2
    await page.locator("#admin-name").fill("Quick Lead");
    await page.locator("#admin-email").fill(adminEmail);
    await page.locator("#create-password").fill("QuickSecure#2026");
    await page.locator("#confirm-password").fill("QuickSecure#2026");
    await page.getByTestId("step2-continue-button").click();

    // Step 3 - click Skip
    const skipBtn = page.getByTestId("step3-skip-button");
    await expect(skipBtn).toBeVisible();
    await skipBtn.click();

    // Verify submission still fired
    expect(capturedPayload).not.toBeNull();
    expect(capturedPayload.slug).toBe(orgSlug);

    // Verify Success screen
    await expect(page.getByTestId("register-success-card")).toBeVisible();
  });

  test("AC 2: Client-side validation shows field errors without firing API request", async ({
    page,
  }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    let apiCalled = false;
    await page.route("**/tenants/register", async (route) => {
      apiCalled = true;
      await route.fulfill({ status: 201, body: "{}" });
    });

    await page.goto("/register");

    // 1. Invalid slug with spaces or illegal characters
    const orgInput = page.locator("#org-name");
    const slugInput = page.locator("#org-slug");
    const continueBtn1 = page.getByTestId("step1-continue-button");

    await orgInput.fill(`Valid Org ${uniqueSuffix}`);
    await slugInput.fill("invalid slug with spaces!");
    await expect(page.locator("#org-slug-error")).toBeVisible();
    await expect(slugInput).toHaveAttribute("aria-invalid", "true");
    await expect(continueBtn1).toBeDisabled();

    // Fix slug and advance
    await slugInput.fill(`valid-org-${uniqueSuffix}`);
    await continueBtn1.click();

    // 2. Step 2 invalid email
    const emailInput = page.locator("#admin-email");
    await emailInput.fill("not-an-email");
    await emailInput.blur();
    await expect(page.locator("#admin-email-error")).toBeVisible();
    await expect(emailInput).toHaveAttribute("aria-invalid", "true");

    // Assert no API call was sent
    expect(apiCalled).toBe(false);
  });

  test("Server Error 409 Conflict: returns user to Step 1 with duplicate detected banner", async ({
    page,
  }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    const takenSlug = `taken-org-${uniqueSuffix}`;

    await page.route("**/tenants/register", async (route) => {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: `Tenant with slug '${takenSlug}' already exists.`,
        }),
      });
    });

    await page.goto("/register");

    // Step 1
    await page.locator("#org-name").fill(`Taken Org ${uniqueSuffix}`);
    await page.locator("#org-slug").fill(takenSlug);
    await page.getByTestId("step1-continue-button").click();

    // Step 2
    await page.locator("#admin-name").fill("Alice Admin");
    await page.locator("#admin-email").fill(`alice@${uniqueSuffix}.taken.org`);
    await page.locator("#create-password").fill("StrongPass#1234");
    await page.locator("#confirm-password").fill("StrongPass#1234");
    await page.getByTestId("step2-continue-button").click();

    // Step 3
    await page.getByTestId("step3-submit-button").click();

    // Should return to Step 1 per Figma 491:1188
    const duplicatePill = page.getByTestId("step1-status-pill-duplicate");
    await expect(duplicatePill).toBeVisible();
    await expect(duplicatePill).toContainText("STEP 1 · DUPLICATE DETECTED");

    const duplicateBanner = page.getByTestId("duplicate-slug-alert");
    await expect(duplicateBanner).toBeVisible();
    await expect(duplicateBanner).toContainText("Organization already registered");
  });

  test("Server Error 500: displays friendly error without raw stack traces", async ({
    page,
  }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);

    await page.route("**/tenants/register", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal database connection failure" }),
      });
    });

    await page.goto("/register");

    // Fill form to Step 3
    await page.locator("#org-name").fill(`Crash Org ${uniqueSuffix}`);
    await page.locator("#org-slug").fill(`crash-org-${uniqueSuffix}`);
    await page.getByTestId("step1-continue-button").click();

    await page.locator("#admin-name").fill("Bob Admin");
    await page.locator("#admin-email").fill(`bob@${uniqueSuffix}.crash.org`);
    await page.locator("#create-password").fill("StrongPass#1234");
    await page.locator("#confirm-password").fill("StrongPass#1234");
    await page.getByTestId("step2-continue-button").click();

    await page.getByTestId("step3-submit-button").click();

    // Check error banner
    const errorBanner = page.getByTestId("step3-error-banner");
    await expect(errorBanner).toBeVisible();
    await expect(errorBanner).toContainText(
      "We could not provision your workspace due to a server error."
    );
  });

  test("AC 3: Prevents double submission during pending request", async ({ page }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    let releaseResponse: () => void = () => {};
    const responsePromise = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });

    await page.route("**/tenants/register", async (route) => {
      await responsePromise;
      await route.fulfill({
        status: 201,
        body: JSON.stringify({
          slug: `slow-org-${uniqueSuffix}`,
          name: `Slow Org ${uniqueSuffix}`,
          admin_email: `slow@${uniqueSuffix}.org.com`,
        }),
      });
    });

    await page.goto("/register");

    await page.locator("#org-name").fill(`Slow Org ${uniqueSuffix}`);
    await page.locator("#org-slug").fill(`slow-org-${uniqueSuffix}`);
    await page.getByTestId("step1-continue-button").click();

    await page.locator("#admin-name").fill("Slow Admin");
    await page.locator("#admin-email").fill(`slow@${uniqueSuffix}.org.com`);
    await page.locator("#create-password").fill("StrongPass#1234");
    await page.locator("#confirm-password").fill("StrongPass#1234");
    await page.getByTestId("step2-continue-button").click();

    const submitBtn = page.getByTestId("step3-submit-button");
    await submitBtn.click();

    // Button should be disabled and aria-busy should be true while request is in flight
    await expect(submitBtn).toBeDisabled();
    await expect(submitBtn).toHaveAttribute("aria-busy", "true");
    await expect(page.getByText("Provisioning Workspace...")).toBeVisible();

    // Complete the pending request
    releaseResponse();

    // Lands on success
    await expect(page.getByTestId("register-success-card")).toBeVisible();
  });

  test("Navigation & Cross-linking: from /login to /register and back", async ({ page }) => {
    await page.goto("/login");

    const registerLink = page.getByTestId("register-organization-link");
    await expect(registerLink).toBeVisible();
    await expect(registerLink).toContainText("Register your organization →");

    await registerLink.click();
    await page.waitForURL("**/register");

    // On /register, verify link to /login
    const loginLink = page.getByTestId("step1-login-link");
    await expect(loginLink).toBeVisible();
    await loginLink.click();
    await page.waitForURL("**/login");
  });

  test("Scope 2: Verify the new administrator can sign in afterward and access tenant workspace", async ({
    page,
  }) => {
    const uniqueSuffix = crypto.randomUUID().slice(0, 8);
    const orgName = `Nexus Labs ${uniqueSuffix}`;
    const orgSlug = `nexus-labs-${uniqueSuffix}`;
    const adminName = `Nexus Admin ${uniqueSuffix}`;
    const adminEmail = `admin@${orgSlug}.io`;
    const adminPassword = "SuperAdminPassword!2026";
    const tenantId = crypto.randomUUID();
    const adminId = crypto.randomUUID();

    let capturedNonce = "init-nonce";
    let capturedState = "";
    let capturedRedirectUri = "http://localhost:3000/auth/callback";

    const corsHeaders = {
      "access-control-allow-origin": "http://localhost:3000",
      "access-control-allow-credentials": "true",
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "Content-Type, Authorization",
    };

    // 1. Mock Tenant Registration Endpoint
    await page.route("**/tenants/register", async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          tenant_id: tenantId,
          admin_id: adminId,
          slug: orgSlug,
          name: orgName,
          admin_email: adminEmail,
          message: "Tenant provisioned successfully",
        }),
      });
    });

    // 2. Mock OIDC Discovery Configuration
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

    // 3. Mock JWKS Certs Endpoint
    await page.route("**/realms/manta/protocol/openid-connect/certs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: corsHeaders,
        body: JSON.stringify({ keys: [keyCtx.publicKeyJwk] }),
      });
    });

    // 4. Mock OIDC Authorization Endpoint
    await page.route("**/realms/manta/protocol/openid-connect/auth*", async (route) => {
      const reqUrl = new URL(route.request().url());
      capturedState = reqUrl.searchParams.get("state") || "";
      capturedNonce = reqUrl.searchParams.get("nonce") || "reg-test-nonce";
      capturedRedirectUri =
        reqUrl.searchParams.get("redirect_uri") || "http://localhost:3000/auth/callback";

      const sessionId = `session-${uniqueSuffix}`;
      const actionUrl = `http://localhost:8080/realms/manta/login-actions/authenticate?session_code=${sessionId}`;
      const cancelUrl = `${capturedRedirectUri}?error=access_denied&error_description=User+cancelled&state=${encodeURIComponent(
        capturedState
      )}`;

      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: renderKeycloakLoginForm(actionUrl, cancelUrl, adminEmail),
      });
    });

    // 5. Mock OIDC Authentication Submission
    await page.route("**/realms/manta/login-actions/authenticate*", async (route) => {
      const postData = route.request().postData() || "";
      const params = new URLSearchParams(postData);
      const usernameInput = (params.get("username") || "").trim();
      const passwordInput = params.get("password") || "";

      // Validate credentials entered by the administrator
      if (
        (usernameInput === adminEmail || usernameInput === "nexus_admin") &&
        passwordInput === adminPassword
      ) {
        const callbackUrl = new URL(capturedRedirectUri);
        callbackUrl.searchParams.set("code", `auth_code_${uniqueSuffix}`);
        callbackUrl.searchParams.set("state", capturedState);
        callbackUrl.searchParams.set("session_state", crypto.randomUUID());
        callbackUrl.searchParams.set("iss", "http://localhost:8080/realms/manta");

        await route.fulfill({
          status: 302,
          headers: {
            Location: callbackUrl.toString(),
          },
        });
      } else {
        await route.fulfill({
          status: 401,
          contentType: "text/html",
          body: "<html><body>Invalid Credentials</body></html>",
        });
      }
    });

    // 6. Mock OIDC Token Endpoint
    await page.route("**/realms/manta/protocol/openid-connect/token", async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 200, headers: corsHeaders });
        return;
      }

      const idToken = signJwt(keyCtx, {
        sub: adminId,
        iss: "http://localhost:8080/realms/manta",
        aud: "manta-portal",
        exp: Math.floor(Date.now() / 1000) + 3600,
        iat: Math.floor(Date.now() / 1000),
        auth_time: Math.floor(Date.now() / 1000),
        nonce: capturedNonce,
        name: adminName,
        preferred_username: `admin_${uniqueSuffix}`,
        email: adminEmail,
        tenant_id: tenantId,
        tenant_name: orgName,
        realm_access: {
          roles: ["TENANT_ADMIN"],
        },
        roles: ["TENANT_ADMIN"],
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: corsHeaders,
        body: JSON.stringify({
          access_token: `mock-access-token-${uniqueSuffix}`,
          id_token: idToken,
          token_type: "Bearer",
          expires_in: 3600,
          scope: "openid profile email",
        }),
      });
    });

    // 7. Mock Backend Current User Claims (/api/v1/auth/me)
    await page.route("**/api/v1/auth/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: corsHeaders,
        body: JSON.stringify({
          sub: adminId,
          name: adminName,
          preferred_username: `admin_${uniqueSuffix}`,
          email: adminEmail,
          tenant_id: tenantId,
          tenant_name: orgName,
          roles: ["TENANT_ADMIN"],
        }),
      });
    });

    // --- EXECUTE REGISTRATION JOURNEY ---
    await page.goto("/register");

    // Step 1: Fill Organization Profile
    await page.locator("#org-name").fill(orgName);
    await expect(page.locator("#org-slug")).toHaveValue(orgSlug);
    await page.getByTestId("step1-continue-button").click();

    // Step 2: Fill Administrator Credentials & Verify Password Complexity
    await page.locator("#admin-name").fill(adminName);
    await page.locator("#admin-email").fill(adminEmail);
    await page.locator("#create-password").fill(adminPassword);
    await page.locator("#confirm-password").fill(adminPassword);

    await expect(page.getByTestId("rule-min-length")).toContainText("✓");
    await expect(page.getByTestId("rule-uppercase")).toContainText("✓");
    await expect(page.getByTestId("rule-number")).toContainText("✓");
    await expect(page.getByTestId("rule-symbol")).toContainText("✓");

    await page.getByTestId("step2-continue-button").click();

    // Step 3: Review & Submit Registration
    const submitBtn = page.getByTestId("step3-submit-button");
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    // Verify Success Card
    const successCard = page.getByTestId("register-success-card");
    await expect(successCard).toBeVisible();
    await expect(page.getByTestId("success-status-pill")).toContainText(
      "PROVISIONING COMPLETE"
    );
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      `${orgName} Workspace Ready`
    );
    await expect(page.getByText(`${orgSlug}_schema`)).toBeVisible();

    // --- VERIFY THE NEW ADMINISTRATOR CAN SIGN IN AFTERWARD ---
    const launchBtn = page.getByTestId("launch-dashboard-button");
    await expect(launchBtn).toBeVisible();
    await launchBtn.click();

    // 1. Verify redirected to /login with workspace & email query parameters
    await page.waitForURL("**/login**");
    expect(page.url()).toContain(`workspace=${encodeURIComponent(orgSlug)}`);
    expect(page.url()).toContain(`email=${encodeURIComponent(adminEmail)}`);
    expect(page.url()).toContain("registered=true");

    // 2. Verify Work Email field is prefilled with administrator email
    const emailInput = page.locator("#work-email");
    await expect(emailInput).toHaveValue(adminEmail);

    // 3. Verify registered success confirmation banner is visible
    const regBanner = page.getByTestId("registered-success-banner");
    await expect(regBanner).toBeVisible();
    await expect(regBanner).toContainText("Workspace provisioned successfully");

    // 4. Administrator signs in via Company SSO
    const ssoBtn = page.getByTestId("company-sso-button");
    await expect(ssoBtn).toBeVisible();
    await ssoBtn.click();

    // 5. Enter credentials on Keycloak login page
    const kcUsername = page.locator("#username");
    const kcPassword = page.locator("#password");
    const kcSubmit = page.locator("#kc-login");

    await expect(kcUsername).toBeVisible({ timeout: 10000 });
    await kcUsername.fill(adminEmail);
    await kcPassword.fill(adminPassword);
    await kcSubmit.click();

    // 6. Callback exchanges authorization code and navigates to portal dashboard
    await expect(page).toHaveURL("/", { timeout: 15000 });

    // 7. Verify Administrator identity on portal header
    const profileName = page.getByTestId("user-profile-name");
    await expect(profileName).toBeVisible();
    await expect(profileName).toContainText(adminName);

    // 8. Verify Tenant identity is displayed
    const tenantBadge = page.getByTestId("user-tenant-badge");
    await expect(tenantBadge).toBeVisible();
    await expect(tenantBadge).toContainText(orgName);

    // 9. Verify Tenant Administrator role privileges (Admin links visible)
    await expect(page.getByText("ADMINISTRATION", { exact: true })).toBeVisible();
    await expect(page.getByTestId("nav-link-workspace-settings")).toBeVisible();
    await expect(page.getByTestId("nav-link-access-roles")).toBeVisible();
    await expect(page.getByTestId("nav-link-privacy-erasure")).toBeVisible();

    // 10. Verify successful navigation to an administrator-restricted route
    await page.getByTestId("nav-link-access-roles").click();
    await expect(page).toHaveURL("/access-roles");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});
