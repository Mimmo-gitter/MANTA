import { expect, test } from "@playwright/test";
import type { ProductionFailureSession } from "../src/types/project";
import {
  fulfillCorsJson,
  makeMockProject,
  mockAuthenticatedSession,
  setupStatefulProjectsApi,
} from "./helpers/mock-auth";

const PROJECT_ID = "11111111-1111-1111-1111-000000000042";

function buildResolvedSession(
  overrides: Partial<ProductionFailureSession> = {},
): ProductionFailureSession {
  return {
    id: "aaaa0000-0000-4000-8000-000000000001",
    tenant_id: "11111111-1111-1111-1111-111111111111",
    project_id: PROJECT_ID,
    incident_id: null,
    status: "RESOLVED",
    probe_source: "prometheus",
    severity: "SEV-1",
    started_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
    resolved_at: new Date(Date.now() - 107 * 60_000).toISOString(),
    recovery_time_seconds: 760,
    created_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
    updated_at: new Date(Date.now() - 107 * 60_000).toISOString(),
    ...overrides,
  };
}

test.describe("Project Settings — Health Sources UI (SCRUM-455 / Task 68.6 & SCRUM-451)", () => {
  test("6A: renders first-time setup and generates & reveals initial HMAC signing secret", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: PROJECT_ID,
      name: "Core Payment Gateway",
      slug: "core-payment-gateway",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [project], {
      healthConfigByProject: {
        [PROJECT_ID]: {
          health_probe_source: "generic",
          webhook_secret_masked: "********",
        },
      },
      healthSignalsByProject: { [PROJECT_ID]: [] },
    });

    await page.goto("/projects/core-payment-gateway/settings?tab=health-sources");

    await expect(page.getByTestId("health-status-pill")).toHaveText("Awaiting Probe");
    await expect(page.getByTestId("health-secret-first-time-setup")).toBeVisible();
    await expect(page.getByTestId("health-sessions-badge")).toHaveText("0");
    await expect(page.getByTestId("health-sessions-empty")).toBeVisible();
    await expect(page.getByTestId("health-webhook-url")).toContainText(
      `/api/v1/webhooks/projects/${PROJECT_ID}/health`,
    );

    // Generate initial signing secret
    await page.getByTestId("generate-signing-secret-button").click();

    await expect(page.getByTestId("health-status-pill")).toHaveText("Healthy");
    await expect(page.getByTestId("health-secret-value")).toContainText(
      "whsec_manta_",
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText("REVEALED");
    await expect(page.getByTestId("health-sources-toast")).toContainText(
      "HMAC signing secret generated",
    );

    // Hide & re-reveal during active session
    await page.getByTestId("secret-reveal-button").click();
    await expect(page.getByTestId("health-secret-value")).toContainText("••••");
    await page.getByTestId("secret-reveal-button").click();
    await expect(page.getByTestId("health-secret-value")).toContainText(
      "whsec_manta_9f41e82b",
    );
  });

  test("6B, 6C & AC: configuring probe source and enabled state persists across full page reload", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: PROJECT_ID,
      name: "Core Payment Gateway",
      slug: "core-payment-gateway",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [project], {
      healthConfigByProject: {
        [PROJECT_ID]: {
          health_probe_source: "generic",
          health_probe_enabled: true,
          webhook_secret_masked: "whsec_manta_••••8c41",
        },
      },
      healthSignalsByProject: {
        [PROJECT_ID]: [
          buildResolvedSession({ recovery_time_seconds: 760 }),
          buildResolvedSession({
            id: "aaaa0000-0000-4000-8000-000000000002",
            probe_source: "datadog",
            recovery_time_seconds: 1448,
          }),
        ],
      },
    });

    await page.goto("/projects/core-payment-gateway/settings?tab=health-sources");

    await expect(page.getByTestId("health-status-pill")).toHaveText("Healthy");
    await expect(page.getByTestId("health-sessions-badge")).toHaveText("2");
    await expect(page.getByTestId("health-sessions-table")).toContainText(
      "Datadog Monitor",
    );
    await expect(page.getByTestId("health-sessions-table")).toContainText(
      "12m 40s",
    );

    // Open Probe Source menu (6C) and select Prometheus / Alertmanager
    await page.getByTestId("health-probe-source-trigger").click();
    const menu = page.getByTestId("health-probe-source-menu");
    await expect(menu).toBeVisible();
    await page.getByTestId("health-probe-option-prometheus").click();

    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Prometheus / Alertmanager",
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText(
      '"probe_source": "prometheus"',
    );
    await expect(page.getByTestId("health-sources-toast")).toContainText(
      "Probe source updated",
    );

    // Toggle Probe Enabled -> Paused
    const probeSwitch = page.getByTestId("health-probe-enabled-toggle");
    await expect(probeSwitch).toHaveAttribute("aria-checked", "true");
    await probeSwitch.click();
    await expect(probeSwitch).toHaveAttribute("aria-checked", "false");
    await expect(page.getByTestId("health-status-pill")).toHaveText("Paused");

    // Full reload to verify persistence (Task 68.2 AC)
    await page.reload();

    await expect(page).toHaveURL(
      /\/projects\/core-payment-gateway\/settings\?tab=health-sources/,
    );
    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Prometheus / Alertmanager",
    );
    await expect(page.getByTestId("health-probe-enabled-toggle")).toHaveAttribute(
      "aria-checked",
      "false",
    );
    await expect(page.getByTestId("health-status-pill")).toHaveText("Paused");

    // Clicking Reveal after reload when plaintext is not in memory shows honest guidance
    await page.getByTestId("secret-reveal-button").click();
    await expect(page.getByTestId("health-secret-hint")).toContainText(
      "Rotate Secret",
    );
  });

  test("Task 68.6 / SCRUM-455: configuring health source renders configuration UI, saves provider selection, and persists across reload & navigation", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: PROJECT_ID,
      name: "Core Payment Gateway",
      slug: "core-payment-gateway",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [project], {
      healthConfigByProject: {
        [PROJECT_ID]: {
          health_probe_source: "generic",
          health_probe_enabled: true,
          webhook_secret_masked: "whsec_manta_••••8c41",
        },
      },
      healthSignalsByProject: {
        [PROJECT_ID]: [buildResolvedSession()],
      },
    });

    // 1. UI Rendering: Navigate to project settings and switch to Health Sources tab via UI
    await page.goto("/projects/core-payment-gateway/settings");
    await page.getByTestId("project-tab-health-sources").click();
    await expect(page).toHaveURL(
      /\/projects\/core-payment-gateway\/settings\?tab=health-sources/,
    );

    // Verify initial UI elements
    await expect(page.getByTestId("health-status-pill")).toHaveText("Healthy");
    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Generic Custom Probe",
    );
    await expect(page.getByTestId("health-webhook-url")).toContainText(
      `/api/v1/webhooks/projects/${PROJECT_ID}/health`,
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText(
      '"probe_source": "generic"',
    );

    // 2. Select Datadog Monitor and verify saving & UI updates
    await page.getByTestId("health-probe-source-trigger").click();
    await expect(page.getByTestId("health-probe-source-menu")).toBeVisible();
    await page.getByTestId("health-probe-option-datadog").click();

    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Datadog Monitor",
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText(
      '"probe_source": "datadog"',
    );
    await expect(page.getByTestId("health-sources-toast")).toContainText(
      "Probe source updated",
    );

    // 3. Select PagerDuty Webhook and verify saving & UI updates
    await page.getByTestId("health-probe-source-trigger").click();
    await expect(page.getByTestId("health-probe-source-menu")).toBeVisible();
    await page.getByTestId("health-probe-option-pagerduty").click();

    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "PagerDuty Webhook",
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText(
      '"probe_source": "pagerduty"',
    );
    await expect(page.getByTestId("health-sources-toast")).toContainText(
      "Probe source updated",
    );

    // 4. Persistence across full browser reload
    await page.reload();
    await expect(page).toHaveURL(
      /\/projects\/core-payment-gateway\/settings\?tab=health-sources/,
    );
    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "PagerDuty Webhook",
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText(
      '"probe_source": "pagerduty"',
    );

    // 5. Select Prometheus / Alertmanager, toggle probe switch, and verify persistence
    await page.getByTestId("health-probe-source-trigger").click();
    await page.getByTestId("health-probe-option-prometheus").click();
    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Prometheus / Alertmanager",
    );

    const probeSwitch = page.getByTestId("health-probe-enabled-toggle");
    await expect(probeSwitch).toHaveAttribute("aria-checked", "true");
    await probeSwitch.click();
    await expect(probeSwitch).toHaveAttribute("aria-checked", "false");
    await expect(page.getByTestId("health-status-pill")).toHaveText("Paused");

    await page.reload();
    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Prometheus / Alertmanager",
    );
    await expect(page.getByTestId("health-probe-enabled-toggle")).toHaveAttribute(
      "aria-checked",
      "false",
    );
    await expect(page.getByTestId("health-status-pill")).toHaveText("Paused");

    // 6. Persistence across in-app navigation
    await page.goto("/projects");
    await expect(
      page.getByTestId("project-card-core-payment-gateway"),
    ).toBeVisible();

    await page.goto("/projects/core-payment-gateway/settings?tab=health-sources");
    await expect(page.getByTestId("health-probe-source-trigger")).toContainText(
      "Prometheus / Alertmanager",
    );
    await expect(page.getByTestId("health-probe-enabled-toggle")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  test("6D: opens Rotate HMAC Signing Secret modal, supports Escape & light-dismiss, and reveals new rotated secret", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: PROJECT_ID,
      name: "Core Payment Gateway",
      slug: "core-payment-gateway",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [project], {
      healthConfigByProject: {
        [PROJECT_ID]: {
          health_probe_source: "prometheus",
          webhook_secret_masked: "whsec_manta_••••8c41",
        },
      },
      healthSignalsByProject: { [PROJECT_ID]: [buildResolvedSession()] },
    });

    await page.goto("/projects/core-payment-gateway/settings?tab=health-sources");

    // Open & dismiss with Escape
    await page.getByTestId("rotate-secret-button").click();
    const modal = page.getByTestId("rotate-health-secret-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Rotate HMAC Signing Secret?");
    await expect(modal).toContainText("whsec_manta_••••8c41");

    // Verify dialog is centered both horizontally and vertically inside the viewport
    const centerDelta = await page.evaluate(() => {
      const dialog = document.querySelector("dialog");
      if (!dialog) return null;
      const rect = dialog.getBoundingClientRect();
      return {
        dx: Math.abs(rect.left + rect.width / 2 - window.innerWidth / 2),
        dy: Math.abs(rect.top + rect.height / 2 - window.innerHeight / 2),
      };
    });
    expect(centerDelta).not.toBeNull();
    expect(centerDelta!.dx).toBeLessThan(20);
    expect(centerDelta!.dy).toBeLessThan(20);

    await page.keyboard.press("Escape");
    await expect(modal).not.toBeVisible();

    // Open & light-dismiss by clicking backdrop
    await page.getByTestId("rotate-secret-button").click();
    await expect(modal).toBeVisible();
    await page.mouse.click(12, 12);
    await expect(modal).not.toBeVisible();

    // Open & confirm rotation
    await page.getByTestId("rotate-secret-button").click();
    await page.getByTestId("rotate-secret-confirm-button").click();

    await expect(modal).not.toBeVisible();
    await expect(page.getByTestId("health-secret-value")).toContainText(
      "whsec_manta_9f41e82b",
    );
    await expect(page.getByTestId("health-webhook-card")).toContainText("REVEALED");
    await expect(page.getByTestId("health-sources-toast")).toContainText(
      "HMAC signing secret rotated",
    );
  });

  test("6E & 6F: shows active Failing incident session and transitions live to Recovered toast when PASS signal resolves it", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: PROJECT_ID,
      name: "Core Payment Gateway",
      slug: "core-payment-gateway",
      health_status: "FAILING",
    });

    const startedIso = new Date(Date.now() - 868_000).toISOString();
    let liveStatus: "FAILING" | "HEALTHY" = "FAILING";
    let liveSessions: ProductionFailureSession[] = [
      {
        id: "bbbb0000-0000-4000-8000-000000000099",
        tenant_id: project.tenant_id,
        project_id: PROJECT_ID,
        incident_id: null,
        status: "OPEN",
        probe_source: "prometheus",
        severity: "SEV-1",
        started_at: startedIso,
        resolved_at: null,
        recovery_time_seconds: null,
        created_at: startedIso,
        updated_at: startedIso,
      },
      buildResolvedSession({ recovery_time_seconds: 760 }),
    ];

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [project]);

    // Override health-config & health-signals dynamically to simulate live PASS recovery
    await page.route(
      (url) =>
        url.pathname === `/api/v1/projects/${PROJECT_ID}/health-config`,
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        await fulfillCorsJson(route, 200, {
          project_id: PROJECT_ID,
          tenant_id: project.tenant_id,
          health_probe_enabled: true,
          health_probe_source: "prometheus",
          health_probe_url: null,
          health_probe_interval_seconds: 60,
          health_status: liveStatus,
          webhook_url: `http://localhost:8000/api/v1/webhooks/projects/${PROJECT_ID}/health`,
          project_health_signals_url: `http://localhost:8000/api/v1/projects/${PROJECT_ID}/health-signals`,
          webhook_secret_masked: "whsec_manta_••••8c41",
          webhook_secret: null,
          updated_at: new Date().toISOString(),
        });
      },
    );

    await page.route(
      (url) =>
        url.pathname === `/api/v1/projects/${PROJECT_ID}/health-signals`,
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        await fulfillCorsJson(route, 200, liveSessions);
      },
    );

    await page.goto("/projects/core-payment-gateway/settings?tab=health-sources");

    // 6E assertions
    await expect(page.getByTestId("health-status-pill")).toHaveText("Failing");
    await expect(page.getByTestId("health-failing-timer")).toContainText("Open:");
    await expect(page.getByTestId("health-sessions-badge")).toHaveText("1 OPEN");
    await expect(page.getByTestId("health-sessions-summary")).toContainText(
      "Awaiting PASS recovery signal",
    );
    await expect(
      page.getByTestId("health-session-row-bbbb0000-0000-4000-8000-000000000099"),
    ).toContainText("– (Incident active)");

    // Simulate incoming PASS probe resolving the session on backend before next 5s poll
    const resolvedIso = new Date().toISOString();
    liveStatus = "HEALTHY";
    liveSessions = [
      {
        ...liveSessions[0],
        status: "RESOLVED",
        resolved_at: resolvedIso,
        recovery_time_seconds: 868,
        updated_at: resolvedIso,
      },
      liveSessions[1],
    ];

    // Wait for automatic 5s FAILING poll to pick up resolution -> 6F
    await expect(page.getByTestId("health-sources-toast")).toContainText(
      "Production health recovered",
      { timeout: 10_000 },
    );
    await expect(page.getByTestId("health-status-pill")).toHaveText("Healthy");
    await expect(page.getByTestId("health-last-probe-pill")).toContainText(
      "Recovered",
    );
    await expect(page.getByTestId("health-sessions-badge")).toHaveText("2");
    await expect(
      page.getByTestId("health-session-row-bbbb0000-0000-4000-8000-000000000099"),
    ).toContainText("14m 28s");
  });

  test("6G: non-admin Developer sees Read-Only banner and locked controls while retaining Copy URL & timeline view", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: PROJECT_ID,
      name: "Core Payment Gateway",
      slug: "core-payment-gateway",
    });

    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });
    await setupStatefulProjectsApi(page, [project], {
      currentRoles: ["DEVELOPER"],
      healthConfigByProject: {
        [PROJECT_ID]: {
          health_probe_source: "prometheus",
          webhook_secret_masked: "whsec_manta_••••8c41",
        },
      },
      healthSignalsByProject: {
        [PROJECT_ID]: [buildResolvedSession()],
      },
    });

    await page.goto("/projects/core-payment-gateway/settings?tab=health-sources");

    const readonlyBanner = page.getByTestId("health-sources-readonly-banner");
    await expect(readonlyBanner).toBeVisible();
    await expect(readonlyBanner).toContainText("Read-Only");
    await expect(readonlyBanner).toContainText("Role: Developer");

    await expect(page.getByTestId("health-probe-enabled-toggle")).toHaveCount(0);
    await expect(page.getByTestId("secret-reveal-button")).toBeDisabled();
    await expect(page.getByTestId("rotate-secret-button")).toBeDisabled();
    await expect(page.getByTestId("health-probe-source-trigger")).toBeDisabled();
    await expect(page.getByTestId("health-secret-readonly")).toContainText(
      "Hidden for Developer role",
    );

    await expect(page.getByTestId("copy-webhook-url-button")).toBeEnabled();
    await expect(page.getByTestId("health-sessions-summary")).toContainText(
      "Read-only health timeline",
    );
    await expect(page.getByTestId("health-sessions-table")).toContainText(
      "Prometheus / Alertmanager",
    );
  });
});
