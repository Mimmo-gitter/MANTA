import { test, expect } from "@playwright/test";

test.describe("Tenant / Organization Registration Flow (SCRUM-243 / SCRUM-287 / US-7)", () => {
  test.beforeEach(async ({ page }) => {
    // Intercept default backend requests
    await page.route("**/tenants/register", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            tenant_id: "tenant-mock-uuid-42",
            admin_id: "admin-mock-uuid-99",
            slug: "acme-corp",
            name: "Acme Corporation",
            admin_email: "admin@acmecorp.com",
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
    let capturedPayload: any = null;
    await page.route("**/tenants/register", async (route) => {
      capturedPayload = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          tenant_id: "t-12345",
          slug: "acme-corp",
          name: "Acme Corporation",
          admin_email: "admin@acmecorp.com",
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
    await orgInput.fill("Acme Corporation");
    await expect(slugInput).toHaveValue("acme-corporation");

    // Adjust slug manually
    await slugInput.fill("acme-corp");
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

    await adminNameInput.fill("Wile E. Coyote");
    await adminEmailInput.fill("admin@acmecorp.com");

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
    await invite1.fill("teammate1@acmecorp.com");

    const submitBtn = page.getByTestId("step3-submit-button");
    await expect(submitBtn).toBeEnabled();

    await submitBtn.click();

    // Verify backend received exact contract
    expect(capturedPayload).not.toBeNull();
    expect(capturedPayload.name).toBe("Acme Corporation");
    expect(capturedPayload.slug).toBe("acme-corp");
    expect(capturedPayload.admin_name).toBe("Wile E. Coyote");
    expect(capturedPayload.admin_email).toBe("admin@acmecorp.com");
    expect(capturedPayload.allowed_domain).toBe("acmecorp.com");

    // 4. Verify Success State (Figma 491:1156)
    const successCard = page.getByTestId("register-success-card");
    await expect(successCard).toBeVisible();

    const successPill = page.getByTestId("success-status-pill");
    await expect(successPill).toContainText("PROVISIONING COMPLETE");

    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "Acme Corporation Workspace Ready"
    );
    await expect(page.getByText("acme-corp_schema")).toBeVisible();
    await expect(page.getByText("manta.ai/app/acme-corp")).toBeVisible();
    await expect(page.getByText("RLS: ENFORCED")).toBeVisible();

    // Launch Workspace Dashboard Button
    const launchBtn = page.getByTestId("launch-dashboard-button");
    await expect(launchBtn).toBeVisible();
    const href = await launchBtn.getAttribute("href");
    expect(href).toContain("/login?workspace=acme-corp");
    expect(href).toContain("email=admin%40acmecorp.com");
  });

  test("AC 1b: Skip team invites directly provisions the tenant", async ({ page }) => {
    let capturedPayload: any = null;
    await page.route("**/tenants/register", async (route) => {
      capturedPayload = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          tenant_id: "t-skip-1",
          slug: "quick-start",
          name: "Quick Start Org",
          admin_email: "dev@quickstart.io",
        }),
      });
    });

    await page.goto("/register");

    // Step 1
    await page.locator("#org-name").fill("Quick Start Org");
    await page.getByTestId("step1-continue-button").click();

    // Step 2
    await page.locator("#admin-name").fill("Quick Lead");
    await page.locator("#admin-email").fill("dev@quickstart.io");
    await page.locator("#create-password").fill("QuickSecure#2026");
    await page.locator("#confirm-password").fill("QuickSecure#2026");
    await page.getByTestId("step2-continue-button").click();

    // Step 3 - click Skip
    const skipBtn = page.getByTestId("step3-skip-button");
    await expect(skipBtn).toBeVisible();
    await skipBtn.click();

    // Verify submission still fired
    expect(capturedPayload).not.toBeNull();
    expect(capturedPayload.slug).toBe("quick-start-org");

    // Verify Success screen
    await expect(page.getByTestId("register-success-card")).toBeVisible();
  });

  test("AC 2: Client-side validation shows field errors without firing API request", async ({
    page,
  }) => {
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

    await orgInput.fill("Valid Org");
    await slugInput.fill("invalid slug with spaces!");
    await expect(page.locator("#org-slug-error")).toBeVisible();
    await expect(slugInput).toHaveAttribute("aria-invalid", "true");
    await expect(continueBtn1).toBeDisabled();

    // Fix slug and advance
    await slugInput.fill("valid-org");
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
    await page.route("**/tenants/register", async (route) => {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "Tenant with slug 'taken-org' already exists.",
        }),
      });
    });

    await page.goto("/register");

    // Step 1
    await page.locator("#org-name").fill("Taken Org");
    await page.locator("#org-slug").fill("taken-org");
    await page.getByTestId("step1-continue-button").click();

    // Step 2
    await page.locator("#admin-name").fill("Alice Admin");
    await page.locator("#admin-email").fill("alice@taken.org");
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
    await page.route("**/tenants/register", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal database connection failure" }),
      });
    });

    await page.goto("/register");

    // Fill form to Step 3
    await page.locator("#org-name").fill("Crash Org");
    await page.locator("#org-slug").fill("crash-org");
    await page.getByTestId("step1-continue-button").click();

    await page.locator("#admin-name").fill("Bob Admin");
    await page.locator("#admin-email").fill("bob@crash.org");
    await page.locator("#create-password").fill("StrongPass#1234");
    await page.locator("#confirm-password").fill("StrongPass#1234");
    await page.getByTestId("step2-continue-button").click();

    await page.getByTestId("step3-submit-button").click();

    // Check error banner
    const errorBanner = page.getByTestId("step3-error-banner");
    await expect(errorBanner).toBeVisible();
    await expect(errorBanner).toContainText("We could not provision your workspace due to a server error.");
  });

  test("AC 3: Prevents double submission during pending request", async ({ page }) => {
    let releaseResponse: () => void = () => {};
    const responsePromise = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });

    await page.route("**/tenants/register", async (route) => {
      await responsePromise;
      await route.fulfill({
        status: 201,
        body: JSON.stringify({ slug: "slow-org", name: "Slow Org", admin_email: "slow@org.com" }),
      });
    });

    await page.goto("/register");

    await page.locator("#org-name").fill("Slow Org");
    await page.locator("#org-slug").fill("slow-org");
    await page.getByTestId("step1-continue-button").click();

    await page.locator("#admin-name").fill("Slow Admin");
    await page.locator("#admin-email").fill("slow@org.com");
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
});
