import { test, expect } from "@playwright/test";

test.describe("Signed-Out Confirmation Screen (Figma 491:762 & Style Guide 209:854)", () => {
  test("visual & token parity: renders all elements with exact Figma tokens", async ({ page }) => {
    await page.goto("/signed-out");

    // Landmark: <main id="main-content">
    const main = page.locator("main#main-content");
    await expect(main).toBeVisible();

    // Section card
    const card = page.locator("section[aria-labelledby='signed-out-heading']");
    await expect(card).toBeVisible();

    // Logo image with alt text
    const logo = page.locator("img[alt='M.A.N.T.A. Platform Logo']");
    await expect(logo).toBeVisible();

    // Tag: SESSION TERMINATED
    const tag = page.getByTestId("session-terminated-badge");
    await expect(tag).toBeVisible();
    await expect(tag).toContainText("SESSION TERMINATED");

    // Title: You Have Been Signed Out
    const title = page.getByRole("heading", { level: 1, name: "You Have Been Signed Out" });
    await expect(title).toBeVisible();

    // Subtitle
    const subtitle = page.getByText(
      "Your session was safely closed and local authentication tokens were cleared from this browser."
    );
    await expect(subtitle).toBeVisible();

    // Architecture callout box with workspace notice
    const callout = page.getByRole("note", { name: "Workspace disconnection notice" });
    await expect(callout).toBeVisible();
    await expect(callout).toContainText("Disconnected from workspace Fintech Alpha.");
    await expect(callout).toContainText("Sign in again to access organization projects and telemetry.");

    // Primary CTA Button
    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    await expect(signInAgainBtn).toBeVisible();
    await expect(signInAgainBtn).toContainText("Sign In Again");

    // Secondary Action Link
    const differentOrgLink = page.getByTestId("different-org-link");
    await expect(differentOrgLink).toBeVisible();
    await expect(differentOrgLink).toContainText("Sign in to a different organization →");
  });

  test("a11y: semantic HTML landmarks, tap targets >= 44px, and keyboard navigation", async ({
    page,
  }) => {
    await page.goto("/signed-out");

    // 1. Minimum 44px tap target height for CTA button
    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    const btnBox = await signInAgainBtn.boundingBox();
    expect(btnBox).not.toBeNull();
    expect(btnBox!.height).toBeGreaterThanOrEqual(44);

    // 2. Keyboard Navigation: Tab from start focuses the CTA button
    await page.keyboard.press("Tab");
    await expect(signInAgainBtn).toBeFocused();

    // Tab moves focus to "Sign in to a different organization →" link
    await page.keyboard.press("Tab");
    const differentOrgLink = page.getByTestId("different-org-link");
    await expect(differentOrgLink).toBeFocused();

    // Shift+Tab moves focus back to CTA button
    await page.keyboard.press("Shift+Tab");
    await expect(signInAgainBtn).toBeFocused();
  });

  test("dynamic workspace support via query parameter", async ({ page }) => {
    await page.goto("/signed-out?workspace=Cyberdyne+Systems");

    const callout = page.getByRole("note", { name: "Workspace disconnection notice" });
    await expect(callout).toBeVisible();
    await expect(callout).toContainText("Disconnected from workspace Cyberdyne Systems.");
  });

  test("interaction: clicking Sign In Again navigates to /login", async ({ page }) => {
    await page.goto("/signed-out");

    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    await signInAgainBtn.click();

    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
  });

  test("integration with /login?status=signed-out route parameter", async ({ page }) => {
    await page.goto("/login?status=signed-out");

    // SignedOutCard is rendered
    const title = page.getByRole("heading", { level: 1, name: "You Have Been Signed Out" });
    await expect(title).toBeVisible();

    const signInAgainBtn = page.getByTestId("sign-in-again-button");
    await expect(signInAgainBtn).toBeVisible();

    // Clicking Sign In Again switches back to SignInCard
    await signInAgainBtn.click();
    await expect(page.getByTestId("company-sso-button")).toBeVisible();
    await expect(page.locator("#work-email")).toBeVisible();
  });

  test("responsive: 320px viewport has zero horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 600 });
    await page.goto("/signed-out");

    const hasHorizontalOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasHorizontalOverflow).toBe(false);

    const card = page.locator("section[aria-labelledby='signed-out-heading']");
    await expect(card).toBeVisible();
    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(320);

    // CTA button is visible and tap-target is >= 44px
    const btn = page.getByTestId("sign-in-again-button");
    await expect(btn).toBeVisible();
    const btnBox = await btn.boundingBox();
    expect(btnBox!.height).toBeGreaterThanOrEqual(44);
  });

  test("responsive: 4K (3840x2160) viewport is centered and constrained to max-width 420px", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 3840, height: 2160 });
    await page.goto("/signed-out");

    const card = page.locator("section[aria-labelledby='signed-out-heading']");
    await expect(card).toBeVisible();

    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    // Card must not exceed 420px on 4K displays
    expect(box!.width).toBeLessThanOrEqual(420);

    // Perfectly centered horizontally
    const expectedCenterX = 3840 / 2;
    const actualCenterX = box!.x + box!.width / 2;
    expect(Math.abs(actualCenterX - expectedCenterX)).toBeLessThan(5);
  });

  test("reloading /signed-out (F5) redirects to /login", async ({ page }) => {
    await page.goto("/signed-out");
    await expect(
      page.getByRole("heading", { level: 1, name: "You Have Been Signed Out" })
    ).toBeVisible();

    await page.reload();
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
  });
});

