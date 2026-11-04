import { test, expect } from "@playwright/test";
import {
  canUser,
  cannotUser,
  hasRole,
  normalizeRole,
  CANONICAL_ROLES,
  CANONICAL_ACTIONS,
  type Role,
  type ActionId,
} from "../src/lib/permissions";
import matrixData from "../src/config/roles-permissions.json";

test.describe("RBAC Permissions Helper & Matrix Logic (SCRUM-309)", () => {
  test("pure helper: correctly resolves permissions for each canonical role against matrixData", () => {
    const rawMatrix = matrixData.matrix as Record<string, Record<string, { decision: string }>>;

    for (const role of CANONICAL_ROLES) {
      for (const action of CANONICAL_ACTIONS) {
        const expectedDecision = rawMatrix[role]?.[action]?.decision;
        const isAllowed = canUser([role], action);
        const isDenied = cannotUser([role], action);

        expect(isAllowed).toBe(expectedDecision === "ALLOW");
        expect(isDenied).toBe(!isAllowed);
      }
    }
  });

  test("multi-role resolution: grants permission if any role has ALLOW", () => {
    // DEVELOPER cannot manage webhooks, but TENANT_ADMIN can
    expect(canUser(["DEVELOPER"], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(false);
    expect(canUser(["TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(true);

    // Composite roles: DEVELOPER + TENANT_ADMIN should be allowed
    expect(
      canUser(["DEVELOPER", "TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS")
    ).toBe(true);

    // Composite roles: SCRUM_MASTER + SOLUTION_ARCHITECT can publish global ADR
    expect(canUser(["SCRUM_MASTER"], "PUBLISH_GLOBAL_ADR")).toBe(false);
    expect(canUser(["SOLUTION_ARCHITECT"], "PUBLISH_GLOBAL_ADR")).toBe(true);
    expect(
      canUser(["SCRUM_MASTER", "SOLUTION_ARCHITECT"], "PUBLISH_GLOBAL_ADR")
    ).toBe(true);
  });

  test("role alias resolution: maps DELIVERY_EXEC to DELIVERY_DIRECTOR", () => {
    expect(normalizeRole("DELIVERY_EXEC")).toBe("DELIVERY_DIRECTOR");
    expect(hasRole(["DELIVERY_EXEC"], "DELIVERY_DIRECTOR")).toBe(true);

    // DELIVERY_DIRECTOR can reassign PR reviewer
    expect(canUser(["DELIVERY_EXEC"], "REASSIGN_PR_REVIEWER_MOBILE")).toBe(true);
  });

  test("edge cases: handles empty, undefined, and unrecognized roles safely (deny-by-default)", () => {
    expect(canUser([], "VIEW_PROJECT_DORA")).toBe(false);
    expect(canUser(null, "VIEW_PROJECT_DORA")).toBe(false);
    expect(canUser(undefined, "VIEW_PROJECT_DORA")).toBe(false);
    expect(canUser(["UNKNOWN_ANONYMOUS_ROLE"], "VIEW_PROJECT_DORA")).toBe(false);
  });
});

test.describe("RBAC UI Menu and Control Filtering (SCRUM-309)", () => {
  test("AC 1: controls a role cannot use are not shown (Developer / Member)", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      // Set non-admin developer role
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["DEVELOPER"]));
    });

    await page.goto("/");

    // 1. Engineering controls for Developer should be visible
    await expect(page.getByTestId("nav-link-overview")).toBeVisible();
    await expect(page.getByTestId("nav-link-projects")).toBeVisible();
    await expect(page.getByTestId("nav-link-refactoring-verification")).toBeVisible();

    // 2. Administration controls that require TENANT_ADMIN must not be shown
    await expect(page.getByText("ADMINISTRATION", { exact: true })).not.toBeVisible();
    await expect(page.getByTestId("nav-link-workspace-settings")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-access-roles")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-privacy-erasure")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).not.toBeVisible();

    // 3. Direct unauthorized navigation redirects to /
    await page.goto("/integrations");
    await expect(page).toHaveURL("/");
  });

  test("AC 1 & AC 2: controls are shown when role has permission (Tenant Admin)", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      // Set Tenant Admin role
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["TENANT_ADMIN"]));
    });

    await page.goto("/");

    // 1. Administration header should be visible
    await expect(page.getByText("ADMINISTRATION", { exact: true })).toBeVisible();

    // 2. All administration entries should be shown
    await expect(page.getByTestId("nav-link-workspace-settings")).toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).toBeVisible();
    await expect(page.getByTestId("nav-link-access-roles")).toBeVisible();
    await expect(page.getByTestId("nav-link-privacy-erasure")).toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).toBeVisible();

    // 3. Authorized navigation to admin route works
    await page.goto("/integrations");
    await expect(page).toHaveURL("/integrations");
  });

  test("Solution Architect: sees ADR Governance but not integrations/workspace settings", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      // Solution Architect role
      window.sessionStorage.setItem(
        "manta_demo_roles",
        JSON.stringify(["SOLUTION_ARCHITECT"])
      );
    });

    await page.goto("/");

    // Solution architect has PUBLISH_GLOBAL_ADR -> sees ADR Governance
    await expect(page.getByTestId("nav-link-adr-governance")).toBeVisible();

    // But lacks MANAGE_WEBHOOK_INTEGRATIONS / MANAGE_TENANT_USERS_AND_ROLES
    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-workspace-settings")).not.toBeVisible();
  });
});
