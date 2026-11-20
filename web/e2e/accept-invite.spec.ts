import { test, expect } from "@playwright/test";
import { fulfillCorsJson } from "./helpers/mock-auth";

test.describe("Public Invitation Acceptance Flow (/accept-invite)", () => {
  test("missing ?token shows clear warning card without redirecting to /login", async ({
    page,
  }) => {
    await page.goto("/accept-invite");
    await expect(page).toHaveURL("/accept-invite");
    await expect(
      page.getByTestId("accept-invite-missing-token"),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Missing invitation token" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Back to Sign In" }),
    ).toHaveAttribute("href", "/login");
  });

  test("valid ?token submits public POST /api/v1/invitations/accept without Bearer header and activates account", async ({
    page,
  }) => {
    let capturedBody: { token?: string } | null = null;
    let capturedAuthHeader: string | undefined;

    await page.route(
      (url) => url.pathname === "/api/v1/invitations/accept",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        capturedAuthHeader = route.request().headers()["authorization"];
        capturedBody = route.request().postDataJSON() as { token?: string };
        await fulfillCorsJson(route, 200, { status: "created" });
      },
    );

    await page.goto("/accept-invite?token=valid-invite-token-abc");
    await expect(page.getByTestId("accept-invite-success")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Account activated" }),
    ).toBeVisible();

    expect(capturedAuthHeader).toBeUndefined();
    expect(capturedBody).toEqual({ token: "valid-invite-token-abc" });

    const signInLink = page.getByTestId("accept-invite-signin-link");
    await expect(signInLink).toHaveAttribute("href", "/login");
  });

  test("handles expired/conflict HTTP 410 and 409 responses cleanly with server detail", async ({
    page,
  }) => {
    await page.route(
      (url) => url.pathname === "/api/v1/invitations/accept",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        await fulfillCorsJson(route, 410, {
          detail: "Invitation token has expired",
        });
      },
    );

    await page.goto("/accept-invite?token=expired-token-123");
    const errCard = page.getByTestId("accept-invite-error");
    await expect(errCard).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Invitation expired" }),
    ).toBeVisible();
    await expect(errCard).toContainText("Invitation token has expired");
    await expect(
      page.getByTestId("accept-invite-retry-btn"),
    ).not.toBeAttached();
  });

  test("transient HTTP 502 shows Retry button and recovers on retry", async ({
    page,
  }) => {
    let failRequest = true;
    await page.route(
      (url) => url.pathname === "/api/v1/invitations/accept",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        if (failRequest) {
          await fulfillCorsJson(route, 502, {
            detail: "Identity provider temporarily unavailable",
          });
          return;
        }
        await fulfillCorsJson(route, 200, { status: "created" });
      },
    );

    await page.goto("/accept-invite?token=transient-token-999");
    await expect(page.getByTestId("accept-invite-error")).toBeVisible();
    await expect(page.getByTestId("accept-invite-error")).toContainText(
      "Identity provider temporarily unavailable",
    );

    const retryBtn = page.getByTestId("accept-invite-retry-btn");
    await expect(retryBtn).toBeVisible();

    failRequest = false;
    await retryBtn.click();
    await expect(page.getByTestId("accept-invite-success")).toBeVisible();
  });
});
