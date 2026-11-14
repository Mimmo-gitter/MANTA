import type { Page, Route } from "@playwright/test";
import type { ProjectItem, RepositoryItem } from "../../src/types/project";

export interface MockMemberRow {
  id: string;
  email: string;
  role: string;
}

export interface MockIntegrationRow {
  id: string;
  provider: "github" | "jira" | "slack" | string;
  external_account_id: string;
  webhook_url?: string | null;
  is_active: boolean;
  created_at: string;
}

export interface MockSessionOptions {
  sub?: string;
  name?: string;
  preferredUsername?: string;
  email?: string;
  tenantId?: string;
  tenantName?: string;
  roles?: string[];
  projects?: Array<Partial<ProjectItem>>;
  integrations?: MockIntegrationRow[];
}

export const OIDC_STORAGE_KEY =
  "oidc.user:http://localhost:8080/realms/manta:manta-portal";

export function makeMockRepository(
  overrides: Partial<RepositoryItem> = {},
): RepositoryItem {
  return {
    id: "22222222-2222-2222-2222-222222222201",
    tenant_id: "11111111-1111-1111-1111-111111111111",
    project_id: "11111111-1111-1111-1111-111111111101",
    integration_id: "int-gh-1",
    external_repo_id: 1001,
    full_name: "acme/core-api",
    default_branch: "main",
    is_private: true,
    created_at: "2026-11-10T10:00:00Z",
    ...overrides,
  };
}

export function makeMockProject(
  overrides: Partial<ProjectItem> = {},
): ProjectItem {
  return {
    id: "11111111-1111-1111-1111-111111111101",
    tenant_id: "11111111-1111-1111-1111-111111111111",
    name: "Core Platform API",
    slug: "core-platform-api",
    tier_sla_hours: 24,
    authoritative_cd_signal: "github_deployments_api",
    authoritative_deployment_signal: "github_deployments_api",
    lead_time_first_commit_strategy: "earliest_commit",
    wip_limit_per_dev: 3,
    pr_stale_threshold_hours: 48,
    is_active: true,
    created_by: null,
    created_at: "2026-11-10T10:00:00Z",
    updated_at: "2026-11-10T10:00:00Z",
    repositories: [],
    ...overrides,
  };
}

function toBase64Url(input: object | string): string {
  const raw = typeof input === "string" ? input : JSON.stringify(input);
  return Buffer.from(raw, "utf-8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function buildUnsignedJwt(claims: Record<string, unknown>): string {
  const header = toBase64Url({ alg: "RS256", typ: "JWT" });
  const payload = toBase64Url(claims);
  return `${header}.${payload}.mock-signature`;
}

export async function fulfillCorsJson(
  route: Route,
  status: number,
  body?: unknown,
): Promise<void> {
  const req = route.request();
  const origin = req.headers()["origin"] || "http://localhost:3000";
  const corsHeaders = {
    "access-control-allow-origin": origin,
    "access-control-allow-credentials": "true",
    "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "access-control-allow-headers": "Authorization,Content-Type",
  };

  if (req.method() === "OPTIONS" || status === 204) {
    await route.fulfill({
      status: 204,
      headers: corsHeaders,
    });
    return;
  }

  await route.fulfill({
    status,
    contentType: "application/json",
    headers: corsHeaders,
    body: JSON.stringify(body ?? {}),
  });
}

/**
 * Seeds a valid `oidc-client-ts` session into `localStorage` and intercepts
 * `/api/v1/auth/me` plus default `GET /api/v1/projects` and `GET /api/v1/integrations`
 * so E2E tests run against the real auth/RBAC/API client path with zero demo fallbacks.
 */
export async function mockAuthenticatedSession(
  page: Page,
  options: MockSessionOptions = {},
): Promise<void> {
  const sub = options.sub ?? "11111111-aaaa-4444-8888-000000000001";
  const name = options.name ?? "Pavlo Zhuk";
  const email = options.email ?? "p.zhuk@company.com";
  const preferredUsername =
    options.preferredUsername ?? email.split("@")[0] ?? "user";
  const tenantId = options.tenantId ?? "11111111-1111-1111-1111-111111111111";
  const tenantName = options.tenantName ?? "Alpha Workspace";
  const roles = options.roles ?? ["TENANT_ADMIN"];
  const defaultProjects = (options.projects ?? []).map((p) => makeMockProject(p));
  const defaultIntegrations = options.integrations ?? [];

  const profile = {
    sub,
    name,
    preferred_username: preferredUsername,
    email,
    tenant_id: tenantId,
    tenant_name: tenantName,
  };

  const now = Math.floor(Date.now() / 1000);
  const idToken = buildUnsignedJwt({
    iss: "http://localhost:8080/realms/manta",
    aud: "manta-portal",
    iat: now,
    exp: now + 3600,
    ...profile,
  });

  await page.addInitScript(
    ({ key, token, userProfile }) => {
      const expiresAt = Math.floor(Date.now() / 1000) + 3600;
      const oidcUser = {
        id_token: token,
        session_state: "e2e-session-state",
        access_token: "e2e-access-token",
        refresh_token: "e2e-refresh-token",
        token_type: "Bearer",
        scope: "openid profile email",
        profile: userProfile,
        expires_at: expiresAt,
      };
      window.localStorage.setItem(key, JSON.stringify(oidcUser));
      window.sessionStorage.removeItem("manta_session_expired");
    },
    { key: OIDC_STORAGE_KEY, token: idToken, userProfile: profile },
  );

  await page.route("**/api/v1/auth/me", async (route) => {
    await fulfillCorsJson(route, 200, {
      ...profile,
      roles,
    });
  });

  await page.route(
    (url) => url.pathname === "/api/v1/projects",
    async (route) => {
      if (route.request().method() === "GET" || route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 200, defaultProjects);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => url.pathname === "/api/v1/integrations",
    async (route) => {
      if (route.request().method() === "GET" || route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 200, defaultIntegrations);
        return;
      }
      await route.fallback();
    },
  );
}

/**
 * Stateful in-memory FastAPI members & invitations mock for E2E suites.
 */
export async function mockMembersEndpoints(
  page: Page,
  initialMembers: MockMemberRow[],
  options?: {
    onInvite?: (
      payload: { email: string; role: string },
    ) => Promise<{ status: number; body: unknown }> | { status: number; body: unknown };
  },
): Promise<{
  members: MockMemberRow[];
  invitations: Array<{ email: string; role: string }>;
  roleUpdates: Array<{ userId: string; role: string }>;
}> {
  const members = initialMembers.map((m) => ({ ...m }));
  const invitations: Array<{ email: string; role: string }> = [];
  const roleUpdates: Array<{ userId: string; role: string }> = [];

  await page.route(
    (url) => url.pathname === "/api/v1/members",
    async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      await fulfillCorsJson(route, 200, members);
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/members\/[^/]+\/role$/.test(url.pathname),
    async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/members\/([^/]+)\/role$/,
      );
      const userId = match ? decodeURIComponent(match[1]) : "";
      const body = route.request().postDataJSON() as { role: string };
      const target = members.find((m) => m.id === userId);
      if (!target) {
        await fulfillCorsJson(route, 404, { detail: "Member not found" });
        return;
      }

      const adminCount = members.filter((m) => m.role === "TENANT_ADMIN").length;
      if (
        target.role === "TENANT_ADMIN" &&
        body.role !== "TENANT_ADMIN" &&
        adminCount <= 1
      ) {
        await fulfillCorsJson(route, 409, {
          detail: "Не можна понизити останнього адміністратора",
        });
        return;
      }

      target.role = body.role;
      roleUpdates.push({ userId, role: body.role });
      await fulfillCorsJson(route, 200, { user_id: userId, role: body.role });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/v1/invitations",
    async (route) => {
      if (route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const payload = route.request().postDataJSON() as {
        email: string;
        role: string;
      };
      if (options?.onInvite) {
        const res = await options.onInvite(payload);
        if (res.status >= 200 && res.status < 300) {
          invitations.push(payload);
        }
        await fulfillCorsJson(route, res.status, res.body);
        return;
      }
      invitations.push(payload);
      await fulfillCorsJson(route, 201, { status: "sent" });
    },
  );

  return { members, invitations, roleUpdates };
}
