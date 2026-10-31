import { test, expect } from "@playwright/test";

test.describe("Login Page Accessibility & Responsiveness (320px to 4K)", () => {
  test("a11y: semantic HTML landmarks and form associations", async ({ page }) => {
    await page.goto("/login");

    // Semantic landmark: <main>
    const mainLandmark = page.locator("main#main-content");
    await expect(mainLandmark).toBeVisible();

    // Semantic heading
    const heading = page.getByRole("heading", { name: "M.A.N.T.A." });
    await expect(heading).toBeVisible();

    // Form control labels
    const emailInput = page.locator("#work-email");
    await expect(emailInput).toBeVisible();
    await expect(emailInput).toHaveValue("");
    await expect(emailInput).toHaveAttribute("placeholder", "alex@manta.ai");
    await expect(emailInput).toHaveAttribute("type", "email");
    await expect(emailInput).toHaveAttribute("aria-required", "true");

    const emailLabel = page.locator('label[for="work-email"]');
    await expect(emailLabel).toBeVisible();
    await expect(emailLabel).toContainText("Work email");

    const passwordInput = page.locator("#password");
    await expect(passwordInput).toBeVisible();
    await expect(passwordInput).toHaveValue("");
    await expect(passwordInput).toHaveAttribute("placeholder", "••••••••••••");
    await expect(passwordInput).toHaveAttribute("type", "password");
    await expect(passwordInput).toHaveAttribute("aria-required", "true");

    const passwordLabel = page.locator('label[for="password"]');
    await expect(passwordLabel).toBeVisible();
    await expect(passwordLabel).toContainText("Password");

    // SSO button
    const ssoButton = page.getByTestId("company-sso-button");
    await expect(ssoButton).toBeVisible();
    const ssoBox = await ssoButton.boundingBox();
    expect(ssoBox).not.toBeNull();
    expect(ssoBox!.height).toBeGreaterThanOrEqual(44);

    // Sign In button
    const signInButton = page.getByRole("button", { name: "Sign In" });
    await expect(signInButton).toBeVisible();
    const signInBox = await signInButton.boundingBox();
    expect(signInBox).not.toBeNull();
    expect(signInBox!.height).toBeGreaterThanOrEqual(44);
  });

  test("a11y: error alert has role=alert and email/password errors have aria-invalid and aria-describedby", async ({
    page,
  }) => {
    // Check initial error state via query param
    await page.goto("/login?error=failed");
    const alert = page.getByTestId("auth-error-alert");
    await expect(alert).toBeVisible();
    await expect(alert).toHaveAttribute("role", "alert");
    await expect(alert).toHaveAttribute("aria-live", "assertive");

    // 1. Trigger credentials error with invalid email and empty password
    const emailInput = page.locator("#work-email");
    const passwordInput = page.locator("#password");
    await emailInput.fill("invalid-email-format");
    await passwordInput.fill("");
    const signInButton = page.getByRole("button", { name: "Sign In" });
    await signInButton.click();

    // Verify email aria-invalid and aria-describedby
    await expect(emailInput).toHaveAttribute("aria-invalid", "true");
    await expect(emailInput).toHaveAttribute("aria-describedby", "email-error-message");
    const emailErrorMsg = page.locator("#email-error-message");
    await expect(emailErrorMsg).toBeVisible();
    await expect(emailErrorMsg).toHaveAttribute("role", "alert");
    await expect(emailErrorMsg).toContainText("Invalid email address");

    // Verify password aria-invalid and aria-describedby
    await expect(passwordInput).toHaveAttribute("aria-invalid", "true");
    await expect(passwordInput).toHaveAttribute("aria-describedby", "password-error-message");
    const passwordErrorMsg = page.locator("#password-error-message");
    await expect(passwordErrorMsg).toBeVisible();
    await expect(passwordErrorMsg).toHaveAttribute("role", "alert");
    await expect(passwordErrorMsg).toContainText("Incorrect password");

    // 2. Typing into email clears email error
    await emailInput.fill("valid.user@manta.ai");
    await expect(emailInput).not.toHaveAttribute("aria-invalid", "true");
    await expect(emailErrorMsg).not.toBeVisible();

    // 3. Typing into password clears password error
    await passwordInput.fill("secret123456");
    await expect(passwordInput).not.toHaveAttribute("aria-invalid", "true");
    await expect(passwordErrorMsg).not.toBeVisible();
  });

  test("a11y: keyboard navigation through all interactive elements", async ({ page }) => {
    await page.goto("/login");

    // Press Tab from page start
    await page.keyboard.press("Tab");
    // Focus should be on Company SSO button
    const ssoButton = page.getByTestId("company-sso-button");
    await expect(ssoButton).toBeFocused();

    // Tab to email input
    await page.keyboard.press("Tab");
    const emailInput = page.locator("#work-email");
    await expect(emailInput).toBeFocused();

    // Tab to Forgot password link
    await page.keyboard.press("Tab");
    const forgotLink = page.getByRole("link", { name: "Forgot password?" });
    await expect(forgotLink).toBeFocused();

    // Tab to password input
    await page.keyboard.press("Tab");
    const passwordInput = page.locator("#password");
    await expect(passwordInput).toBeFocused();

    // Tab to Sign In button
    await page.keyboard.press("Tab");
    const signInButton = page.getByRole("button", { name: "Sign In" });
    await expect(signInButton).toBeFocused();

    // Tab to Sign up link
    await page.keyboard.press("Tab");
    const signUpLink = page.getByRole("link", { name: "Sign up" });
    await expect(signUpLink).toBeFocused();
  });

  test("responsive: 320px viewport has zero horizontal scrollbar and all content is accessible", async ({
    page,
  }) => {
    // Set viewport to absolute minimum 320px (e.g. iPhone SE / small Android)
    await page.setViewportSize({ width: 320, height: 600 });
    await page.goto("/login");

    // Verify no horizontal overflow on window / document
    const hasHorizontalOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasHorizontalOverflow).toBe(false);

    // Verify card fits within 320px
    const card = page.locator("section[aria-labelledby='signin-heading']");
    await expect(card).toBeVisible();
    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(320);

    // Verify all primary elements are visible
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
    await expect(page.locator("#work-email")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();

    // Verify shield icon and SSO text do not overlap on 320px screen
    const ssoBtn = page.getByTestId("company-sso-button");
    const iconBox = await ssoBtn.locator("div.absolute").boundingBox();
    const textBox = await ssoBtn.locator("span").boundingBox();
    expect(iconBox).not.toBeNull();
    expect(textBox).not.toBeNull();
    expect(iconBox!.x + iconBox!.width).toBeLessThanOrEqual(textBox!.x);
  });

  test("responsive: 4K (3840x2160) viewport renders centered and proportional", async ({
    page,
  }) => {
    // Set viewport to 4K resolution
    await page.setViewportSize({ width: 3840, height: 2160 });
    await page.goto("/login");

    const card = page.locator("section[aria-labelledby='signin-heading']");
    await expect(card).toBeVisible();

    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    // Card should be constrained to 380px max-width, perfectly centered horizontally
    expect(box!.width).toBeLessThanOrEqual(380);
    const expectedCenterX = 3840 / 2;
    const actualCenterX = box!.x + box!.width / 2;
    expect(Math.abs(actualCenterX - expectedCenterX)).toBeLessThan(5);

    // Ambient glows and main container visible
    await expect(page.locator("main#main-content")).toBeVisible();
  });
});
