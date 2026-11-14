import { test, expect } from "@playwright/test";
import {
  canUser,
  cannotUser,
  hasRole,
  normalizeRole,
  CANONICAL_ROLES,
  CANONICAL_ACTIONS,
} from "../src/lib/permissions";
import matrixData from "../src/config/roles-permissions.json";
import { mockAuthenticatedSession } from "./helpers/mock-auth";

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
    expect(canUser(["DEVELOPER"], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(false);
    expect(canUser(["TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(true);

    expect(
      canUser(["DEVELOPER", "TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS"),
    ).toBe(true);

    expect(canUser(["SCRUM_MASTER"], "PUBLISH_GLOBAL_ADR")).toBe(false);
    expect(canUser(["SOLUTION_ARCHITECT"], "PUBLISH_GLOBAL_ADR")).toBe(true);
    expect(
      canUser(["SCRUM_MASTER", "SOLUTION_ARCHITECT"], "PUBLISH_GLOBAL_ADR"),
    ).toBe(true);
  });

  test("role alias resolution: maps DELIVERY_EXEC to DELIVERY_DIRECTOR", () => {
    expect(normalizeRole("DELIVERY_EXEC")).toBe("DELIVERY_DIRECTOR");
    expect(hasRole(["DELIVERY_EXEC"], "DELIVERY_DIRECTOR")).toBe(true);
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
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      name: "Dev Member",
      email: "dev@company.com",
    });

    await page.goto("/");

    await expect(page.getByTestId("user-role-badge")).toHaveText("Developer");

    await expect(page.getByTestId("nav-link-overview")).toBeVisible();
    await expect(page.getByTestId("nav-link-projects")).toBeVisible();
    await expect(page.getByTestId("nav-link-refactoring-verification")).toBeVisible();

    await expect(page.getByText("ADMINISTRATION", { exact: true })).not.toBeVisible();
    await expect(page.getByTestId("nav-link-workspace-settings")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-access-roles")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-privacy-erasure")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).not.toBeVisible();

    await page.goto("/integrations");
    await expect(page).toHaveURL("/");
  });

  test("AC 1 & AC 2: controls are shown when role has permission (Tenant Admin)", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
    });

    await page.goto("/");

    await expect(page.getByTestId("user-role-badge")).toHaveText("Tenant Admin");
    await expect(page.getByText("ADMINISTRATION", { exact: true })).toBeVisible();

    await expect(page.getByTestId("nav-link-workspace-settings")).toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).toBeVisible();
    await expect(page.getByTestId("nav-link-access-roles")).toBeVisible();
    await expect(page.getByTestId("nav-link-privacy-erasure")).toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).toBeVisible();

    await page.goto("/integrations");
    await expect(page).toHaveURL("/integrations");
  });

  test("Solution Architect: sees ADR Governance but not integrations/workspace settings", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["SOLUTION_ARCHITECT"],
      name: "Architect User",
      email: "arch@company.com",
    });

    await page.goto("/");

    await expect(page.getByTestId("user-role-badge")).toHaveText("Solution Architect");
    await expect(page.getByTestId("nav-link-adr-governance")).toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-workspace-settings")).not.toBeVisible();

    await page.goto("/adr-governance");
    await expect(page).toHaveURL("/adr-governance");
  });
});
