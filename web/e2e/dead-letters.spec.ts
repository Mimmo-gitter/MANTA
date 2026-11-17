import { expect, test } from "@playwright/test";
import type { DeadLetterItem } from "../src/types/dead-letter";
import { fulfillCorsJson, mockAuthenticatedSession } from "./helpers/mock-auth";

const FIXED_NOW_MS = Date.parse("2026-11-17T17:00:00.000Z");

function isoMinutesAgo(minutes: number): string {
  return new Date(FIXED_NOW_MS - minutes * 60_000).toISOString();
}

function buildSampleDeadLetters(): DeadLetterItem[] {
  return [
    {
      id: "9f2a8c41-0000-4000-8000-000000000001",
      provider: "github",
      original_event_id: "9f2a8c41-aaaa-4000-8000-000000000001",
      payload: {
        event: "deployment_status",
        repository: { full_name: "acme-corp/payment-service" },
        deployment_status: { state: "failure", environment: "production" },
        sender: { login: "github-actions[bot]" },
      },
      error_class: "SignatureMismatchError: Invalid HMAC-SHA256 digest",
      stack_trace: [
        "Traceback (most recent call last):",
        '  File "/app/webhooks/github.py", line 142, in verify_github_signature',
        '    raise SignatureMismatchError("Invalid HMAC-SHA256 digest in X-Hub-Signature-256")',
        "SignatureMismatchError: header digest mismatch against rotated secret",
      ].join("\n"),
      total_attempts: 5,
      is_resolved: false,
      resolved_at: null,
      quarantined_at: isoMinutesAgo(12),
    },
    {
      id: "4c1b7e09-0000-4000-8000-000000000002",
      provider: "jira",
      original_event_id: "4c1b7e09-aaaa-4000-8000-000000000002",
      payload: {
        webhookEvent: "issue_updated",
        issue: {
          key: "FIN-892",
          fields: { project: { name: "Core Payments" } },
        },
      },
      error_class: "PayloadSchemaError: Missing changelog field",
      stack_trace: [
        "Traceback (most recent call last):",
        '  File "/app/webhooks/jira.py", line 89, in parse_issue_event',
        "PayloadSchemaError: Missing required property changelog.items",
      ].join("\n"),
      total_attempts: 5,
      is_resolved: false,
      resolved_at: null,
      quarantined_at: isoMinutesAgo(38),
    },
    {
      id: "7e3d01b2-0000-4000-8000-000000000003",
      provider: "slack",
      original_event_id: "7e3d01b2-aaaa-4000-8000-000000000003",
      payload: {
        event: { type: "app_mention", channel: "#release-ops" },
      },
      error_class: "RateLimitExceededError: Slack 429 throttle",
      stack_trace: [
        "Traceback (most recent call last):",
        '  File "/app/webhooks/slack.py", line 64, in dispatch_event',
        "RateLimitExceededError: Downstream queue throttled after 4 attempts",
      ].join("\n"),
      total_attempts: 4,
      is_resolved: false,
      resolved_at: null,
      quarantined_at: isoMinutesAgo(125),
    },
    {
      id: "2a8f4d90-0000-4000-8000-000000000004",
      provider: "github",
      original_event_id: "2a8f4d90-aaaa-4000-8000-000000000004",
      payload: {
        event: "pull_request",
        repository: { full_name: "acme-corp/checkout-api" },
      },
      error_class: "UpstreamTimeoutError: Celery worker timeout",
      stack_trace: [
        "Traceback (most recent call last):",
        '  File "/app/webhooks/github.py", line 211, in enqueue_pr_sync',
        "UpstreamTimeoutError: Timed out connecting to broker",
      ].join("\n"),
      total_attempts: 3,
      is_resolved: false,
      resolved_at: null,
      quarantined_at: isoMinutesAgo(260),
    },
    {
      id: "11c8e5a7-0000-4000-8000-000000000005",
      provider: "github",
      original_event_id: "11c8e5a7-aaaa-4000-8000-000000000005",
      payload: {
        event: "push",
        repository: { full_name: "acme-corp/settlement-ledger" },
      },
      error_class: "TemporaryNetworkError: Transient DNS reset",
      stack_trace: [
        "Traceback (most recent call last):",
        '  File "/app/webhooks/github.py", line 51, in process_push',
        "TemporaryNetworkError: Temporary network failure",
      ].join("\n"),
      total_attempts: 2,
      is_resolved: true,
      resolved_at: isoMinutesAgo(180),
      quarantined_at: isoMinutesAgo(405),
    },
  ];
}

test.describe("Dead Letters Queue UI (Task 23.2)", () => {
  test("5A, 5B, 5H: renders loading skeleton, live sidebar badge, populated table, search, and Provider/Status filters", async ({
    page,
  }) => {
    await page.clock.install({ time: FIXED_NOW_MS });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    const sampleItems = buildSampleDeadLetters();
    let releaseGetQueue!: () => void;
    const queueGate = new Promise<void>((resolve) => {
      releaseGetQueue = resolve;
    });

    await page.route("**/api/v1/dead-letters**", async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204, {});
        return;
      }

      if (route.request().method() === "GET") {
        await queueGate;
        await fulfillCorsJson(route, 200, {
          items: sampleItems,
          unresolved_count: sampleItems.filter((i) => !i.is_resolved).length,
          total: sampleItems.length,
          limit: 200,
          offset: 0,
        });
        return;
      }

      await route.continue();
    });

    await page.goto("/dead-letters");

    // 5H: Page loading skeleton and "..." sidebar badge while fetching
    await expect(page.getByTestId("dead-letters-loading-skeleton")).toBeVisible();
    await expect(page.getByTestId("refresh-queue-button")).toContainText("Refreshing...");
    await expect(page.getByTestId("nav-dead-letters-badge")).toHaveText("...");

    releaseGetQueue();

    // 5A: Populated table & live sidebar badge
    await expect(page.getByTestId("dead-letters-table")).toBeVisible();
    await expect(page.getByTestId("nav-dead-letters-badge")).toHaveText("4");
    await expect(page.getByTestId("dlq-queue-badge")).toHaveText("4");
    await expect(page.getByTestId("dlq-unread-count")).toHaveText("4 unread quarantined");
    await expect(page.getByTestId("dlq-redriven-today")).toHaveText("1 re-driven today");
    await expect(page.getByTestId("dead-letters-summary-strip")).toContainText(
      "GitHub (2) · Jira (1) · Slack (1)",
    );

    const firstId = sampleItems[0].id;
    const fifthId = sampleItems[4].id;
    await expect(page.getByTestId(`dead-letter-row-${firstId}`)).toBeVisible();
    await expect(page.getByTestId(`dead-letter-row-${firstId}`)).toContainText("SignatureMismatchError");
    await expect(page.getByTestId(`status-badge-${firstId}`)).toHaveText("Quarantined");
    await expect(page.getByTestId(`status-badge-${fifthId}`)).toHaveText("Re-driven");
    await expect(page.getByTestId(`redrive-button-${fifthId}`)).toBeDisabled();
    await expect(page.getByTestId(`redrive-button-${fifthId}`)).toContainText("Done");

    // 5B: Provider filter dropdown
    await page.getByTestId("filter-provider-trigger").click();
    const providerPopover = page.getByTestId("filter-provider-popover");
    await expect(providerPopover).toBeVisible();
    await expect(providerPopover).toContainText("All providers");
    await expect(providerPopover).toContainText("GitHub");
    await expect(providerPopover).toContainText("Jira");
    await expect(providerPopover).toContainText("Slack");
    await providerPopover.getByRole("option", { name: /Jira/ }).click();

    await expect(page.getByTestId(`dead-letter-row-${sampleItems[1].id}`)).toBeVisible();
    await expect(page.getByTestId(`dead-letter-row-${firstId}`)).not.toBeVisible();

    // Reset provider to All providers
    await page.getByTestId("filter-provider-trigger").click();
    await page.getByTestId("filter-provider-popover").getByRole("option", { name: /All providers/ }).click();

    // 5B: Status filter dropdown
    await page.getByTestId("filter-status-trigger").click();
    const statusPopover = page.getByTestId("filter-status-popover");
    await expect(statusPopover).toBeVisible();
    await statusPopover.getByRole("option", { name: /Quarantined/ }).click();

    await expect(page.getByTestId(`dead-letter-row-${firstId}`)).toBeVisible();
    await expect(page.getByTestId(`dead-letter-row-${fifthId}`)).not.toBeVisible();

    // Search input
    await page.getByTestId("dead-letters-search-input").fill("RateLimitExceededError");
    await expect(page.getByTestId(`dead-letter-row-${sampleItems[2].id}`)).toBeVisible();
    await expect(page.getByTestId(`dead-letter-row-${firstId}`)).not.toBeVisible();
  });

  test("5C & 5D: opens inspector drawer with Stack Trace & JSON Payload tabs and supports Escape & light-dismiss", async ({
    page,
  }) => {
    await page.clock.install({ time: FIXED_NOW_MS });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    const sampleItems = buildSampleDeadLetters();
    await page.route("**/api/v1/dead-letters**", async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204, {});
        return;
      }
      await fulfillCorsJson(route, 200, {
        items: sampleItems,
        unresolved_count: 4,
        total: sampleItems.length,
        limit: 200,
        offset: 0,
      });
    });

    await page.goto("/dead-letters");

    const firstId = sampleItems[0].id;
    await page.getByTestId(`inspect-button-${firstId}`).click();

    // 5C: Stack Trace tab active
    const drawer = page.getByTestId("dead-letter-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText("dlv_9f2a8c41");
    await expect(drawer).toContainText("GitHub Webhook");
    await expect(drawer).toContainText("5 of 5 exhausted");
    await expect(page.getByTestId("drawer-stack-trace-content")).toContainText(
      "verify_github_signature",
    );

    // 5D: JSON Payload tab
    await page.getByTestId("drawer-tab-json-payload").click();
    await expect(page.getByTestId("drawer-json-payload-content")).toContainText(
      '"full_name": "acme-corp/payment-service"',
    );

    // Close via Escape
    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();

    // Re-open via clicking row non-button cell and test light-dismiss click outside dialog
    await page.getByTestId(`dead-letter-row-${firstId}`).getByText("SignatureMismatchError").click();
    await expect(drawer).toBeVisible();
    await page.mouse.click(20, 20);
    await expect(drawer).not.toBeVisible();
  });

  test("5E & 5F: re-drives event cleanly (Sending -> Re-driven + Toast + sidebar badge decrements 4 -> 3)", async ({
    page,
  }) => {
    await page.clock.install({ time: FIXED_NOW_MS });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    const sampleItems = buildSampleDeadLetters();
    const targetItem = sampleItems[0];
    let releaseRedrive!: () => void;
    const redriveGate = new Promise<void>((resolve) => {
      releaseRedrive = resolve;
    });

    let redriveRequests = 0;
    await page.route("**/api/v1/dead-letters**", async (route) => {
      const req = route.request();
      if (req.method() === "OPTIONS") {
        await fulfillCorsJson(route, 204, {});
        return;
      }
      if (req.method() === "POST" && req.url().includes(`/${targetItem.id}/redrive`)) {
        redriveRequests += 1;
        await redriveGate;
        await fulfillCorsJson(route, 200, {
          id: targetItem.id,
          event_id: targetItem.original_event_id,
          status: "queued",
          queue: "webhooks.high_priority",
          enqueued: true,
          resolved_at: new Date(FIXED_NOW_MS).toISOString(),
        });
        return;
      }
      if (req.method() === "GET") {
        await fulfillCorsJson(route, 200, {
          items: sampleItems,
          unresolved_count: 4,
          total: sampleItems.length,
          limit: 200,
          offset: 0,
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/dead-letters");
    await expect(page.getByTestId("nav-dead-letters-badge")).toHaveText("4");

    const row = page.getByTestId(`dead-letter-row-${targetItem.id}`);
    const redriveBtn = page.getByTestId(`redrive-button-${targetItem.id}`);

    // Trigger Re-drive
    await redriveBtn.click();

    // 5E: Sending / Re-driving intermediate state
    await expect(page.getByTestId(`status-badge-${targetItem.id}`)).toHaveText("Re-driving");
    await expect(redriveBtn).toBeDisabled();
    await expect(redriveBtn).toContainText("Sending...");
    await expect(page.getByTestId("dlq-footer-status")).toContainText("Dispatching event");

    releaseRedrive();

    // 5F: Success state, Toast, updated row & decremented sidebar badge
    expect(redriveRequests).toBe(1);
    const toast = page.getByTestId("dead-letters-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("Event re-driven successfully");

    await expect(page.getByTestId(`status-badge-${targetItem.id}`)).toHaveText("Re-driven");
    await expect(row).toContainText("Re-driven just now");
    await expect(redriveBtn).toBeDisabled();
    await expect(redriveBtn).toContainText("Done");

    await expect(page.getByTestId("nav-dead-letters-badge")).toHaveText("3");
    await expect(page.getByTestId("dlq-queue-badge")).toHaveText("3");
    await expect(page.getByTestId("dlq-unread-count")).toHaveText("3 unread quarantined");
    await expect(page.getByTestId("dlq-redriven-today")).toHaveText("2 re-driven today");

    // Switching Status filter to Quarantined removes newly resolved event
    await page.getByTestId("filter-status-trigger").click();
    await page.getByTestId("filter-status-popover").getByRole("option", { name: /Quarantined/ }).click();
    await expect(row).not.toBeVisible();
  });

  test("5G: shows inline error, incremented attempts, Retry button, and red toast with Inspect button when re-drive fails", async ({
    page,
  }) => {
    await page.clock.install({ time: FIXED_NOW_MS });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    const sampleItems = buildSampleDeadLetters();
    const targetItem = sampleItems[0];

    await page.route("**/api/v1/dead-letters**", async (route) => {
      const req = route.request();
      if (req.method() === "OPTIONS") {
        await fulfillCorsJson(route, 204, {});
        return;
      }
      if (req.method() === "POST" && req.url().includes(`/${targetItem.id}/redrive`)) {
        await fulfillCorsJson(route, 500, { detail: "header digest mismatch" });
        return;
      }
      if (req.method() === "GET") {
        await fulfillCorsJson(route, 200, {
          items: sampleItems,
          unresolved_count: 4,
          total: sampleItems.length,
          limit: 200,
          offset: 0,
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/dead-letters");

    const row = page.getByTestId(`dead-letter-row-${targetItem.id}`);
    const redriveBtn = page.getByTestId(`redrive-button-${targetItem.id}`);
    await redriveBtn.click();

    // 5G assertions
    await expect(page.getByTestId(`row-error-${targetItem.id}`)).toHaveText(
      "Last retry failed: header digest mismatch",
    );
    await expect(row).toContainText("6 / 5");
    await expect(redriveBtn).toBeEnabled();
    await expect(redriveBtn).toContainText("Retry");
    await expect(page.getByTestId("nav-dead-letters-badge")).toHaveText("4");

    const toast = page.getByTestId("dead-letters-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toHaveAttribute("data-toast-kind", "error");
    await expect(toast).toContainText("Re-drive failed");

    // Clicking Inspect inside the failure toast opens the inspector drawer for that event
    await page.getByTestId("toast-inspect-button").click();
    const drawer = page.getByTestId("dead-letter-drawer");
    await expect(drawer).toBeVisible();
    await expect(page.getByTestId("drawer-redrive-button")).toContainText("Retry Re-drive");
  });

  test("5I: renders clean Empty State and hides sidebar count badge when zero items exist", async ({
    page,
  }) => {
    await page.clock.install({ time: FIXED_NOW_MS });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    await page.route("**/api/v1/dead-letters**", async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204, {});
        return;
      }
      await fulfillCorsJson(route, 200, {
        items: [],
        unresolved_count: 0,
        total: 0,
        limit: 200,
        offset: 0,
      });
    });

    await page.goto("/dead-letters");

    await expect(page.getByTestId("dead-letters-empty-state")).toBeVisible();
    await expect(page.getByTestId("dead-letters-empty-state")).toContainText(
      "No dead-letter events",
    );
    await expect(page.getByTestId("dlq-unread-count")).toHaveText("0 unread quarantined");
    await expect(page.getByTestId("dlq-queue-badge")).toHaveText("0");
    await expect(page.getByTestId("nav-dead-letters-badge")).not.toBeVisible();
  });

  test("5J: hides Dead Letters sidebar item and shows HTTP 403 restricted state for non-admin Developer", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    await page.goto("/");

    await expect(page.getByText("ADMINISTRATION", { exact: true })).not.toBeVisible();
    await expect(page.getByTestId("nav-link-dead-letters")).not.toBeVisible();

    await page.goto("/dead-letters");
    await expect(page).toHaveURL("/dead-letters");

    const forbidden = page.getByTestId("dead-letters-forbidden-state");
    await expect(forbidden).toBeVisible();
    await expect(forbidden).toContainText("HTTP 403 · ACCESS RESTRICTED");
    await expect(forbidden).toContainText("Administrator access required");
    await expect(forbidden).toContainText("Current workspace role:");
    await expect(forbidden).toContainText("Developer");

    await page.getByTestId("dead-letters-back-to-overview").click();
    await expect(page).toHaveURL("/");
  });

  test("pagination: navigates across pages cleanly when more than 5 events exist", async ({
    page,
  }) => {
    await page.clock.install({ time: FIXED_NOW_MS });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "pavlo@acme.dev",
    });

    const base = buildSampleDeadLetters();
    const extra1: DeadLetterItem = {
      ...base[0],
      id: "aaaa1111-0000-4000-8000-000000000006",
      original_event_id: "aaaa1111-aaaa-4000-8000-000000000006",
      error_class: "PaginationCheckErrorPageTwo",
    };
    const allItems = [...base, extra1];

    await page.route("**/api/v1/dead-letters**", async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204, {});
        return;
      }
      await fulfillCorsJson(route, 200, {
        items: allItems,
        unresolved_count: 5,
        total: allItems.length,
        limit: 200,
        offset: 0,
      });
    });

    await page.goto("/dead-letters");

    await expect(page.getByTestId("dlq-footer-status")).toContainText("Showing 1–5 of 6 events");
    await expect(page.getByTestId("pagination-prev")).toBeDisabled();
    await expect(page.getByTestId("pagination-next")).toBeEnabled();

    await page.getByTestId("pagination-next").click();
    await expect(page.getByTestId("dlq-footer-status")).toContainText("Showing 6–6 of 6 events");
    await expect(page.getByTestId(`dead-letter-row-${extra1.id}`)).toBeVisible();
    await expect(page.getByTestId("pagination-next")).toBeDisabled();

    await page.getByTestId("pagination-page-1").click();
    await expect(page.getByTestId("dlq-footer-status")).toContainText("Showing 1–5 of 6 events");
  });
});
