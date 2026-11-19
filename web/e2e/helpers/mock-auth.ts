import type { Page, Route } from "@playwright/test";
import type {
  HealthProbeConfig,
  ProductionFailureSession,
  ProjectItem,
  RepositoryItem,
} from "../../src/types/project";

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
  webhook_secret_masked?: string | null;
  is_active: boolean;
  created_at: string;
}

export interface MockWorkspaceOption {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  role: string;
  is_current: boolean;
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
  workspaces?: MockWorkspaceOption[];
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
  const defaultSlug =
    tenantName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "workspace";
  const workspaceList: MockWorkspaceOption[] = options.workspaces ?? [
    {
      tenant_id: tenantId,
      tenant_name: tenantName,
      tenant_slug: defaultSlug,
      role: roles[0] ?? "TENANT_ADMIN",
      is_current: true,
    },
  ];
  let activeTenantId =
    workspaceList.find((w) => w.is_current)?.tenant_id ?? tenantId;
  let activeTenantName =
    workspaceList.find((w) => w.tenant_id === activeTenantId)?.tenant_name ??
    tenantName;

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
      tenant_id: activeTenantId,
      tenant_name: activeTenantName,
      roles,
    });
  });

  await page.route(
    (url) => url.pathname === "/api/v1/auth/workspaces",
    async (route) => {
      if (
        route.request().method() === "GET" ||
        route.request().method() === "OPTIONS"
      ) {
        await fulfillCorsJson(
          route,
          200,
          workspaceList.map((ws) => ({
            ...ws,
            is_current: ws.tenant_id === activeTenantId,
          })),
        );
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => url.pathname === "/api/v1/auth/switch-workspace",
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      if (method === "POST") {
        const body = (route.request().postDataJSON() ?? {}) as {
          tenant_id?: string;
        };
        const target = workspaceList.find(
          (ws) => ws.tenant_id === body.tenant_id,
        );
        if (!target) {
          await fulfillCorsJson(route, 404, {
            detail: "Workspace not found or access revoked.",
          });
          return;
        }
        activeTenantId = target.tenant_id;
        activeTenantName = target.tenant_name;
        await fulfillCorsJson(route, 200, {
          ...target,
          is_current: true,
        });
        return;
      }
      await route.fallback();
    },
  );

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

  await page.route(
    (url) => url.pathname === "/api/v1/dead-letters",
    async (route) => {
      if (route.request().method() === "GET" || route.request().method() === "OPTIONS") {
        await fulfillCorsJson(route, 200, {
          items: [],
          unresolved_count: 0,
          total: 0,
          limit: 50,
          offset: 0,
        });
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
      if (route.request().method() === "GET") {
        await fulfillCorsJson(route, 200, []);
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

export interface StatefulProjectsApiOptions {
  currentRoles?: string[];
  healthConfigByProject?: Record<string, Partial<HealthProbeConfig>>;
  healthSignalsByProject?: Record<string, ProductionFailureSession[]>;
}

/**
 * Stateful in-memory projects, repository linking, deployment-signal, and health-sources API mock
 * for Playwright E2E suites. Supports persistence across reloads and RBAC enforcement (403).
 */
export async function setupStatefulProjectsApi(
  page: Page,
  initialProjects: ProjectItem[] = [],
  options?: StatefulProjectsApiOptions,
): Promise<ProjectItem[]> {
  const projects: ProjectItem[] = initialProjects.map((p) => ({
    ...p,
    repositories: [...(p.repositories || [])],
  }));

  const healthConfigs = new Map<string, HealthProbeConfig>();
  const getOrInitHealthConfig = (project: ProjectItem): HealthProbeConfig => {
    const existing = healthConfigs.get(project.id);
    if (existing) return existing;
    const override = options?.healthConfigByProject?.[project.id] ?? {};
    const created: HealthProbeConfig = {
      project_id: project.id,
      tenant_id: project.tenant_id,
      health_probe_enabled: project.health_probe_enabled ?? true,
      health_probe_source: project.health_probe_source ?? "generic",
      health_probe_url: project.health_probe_url ?? null,
      health_probe_interval_seconds: project.health_probe_interval_seconds ?? 60,
      health_status: project.health_status ?? "HEALTHY",
      webhook_url: `http://localhost:8000/api/v1/webhooks/projects/${project.id}/health`,
      project_health_signals_url: `http://localhost:8000/api/v1/projects/${project.id}/health-signals`,
      webhook_secret_masked: "whsec_manta_••••8c41",
      webhook_secret: null,
      updated_at: project.updated_at,
      ...override,
    };
    healthConfigs.set(project.id, created);
    return created;
  };
  let secretSeq = 1;

  const isRoleAdmin = (options?.currentRoles ?? ["TENANT_ADMIN"]).includes(
    "TENANT_ADMIN",
  );

  await page.route(
    (url) => url.pathname === "/api/v1/projects",
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      if (method === "GET") {
        await fulfillCorsJson(route, 200, projects);
        return;
      }
      if (method === "POST") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        const body = (route.request().postDataJSON() ?? {}) as {
          name: string;
          slug?: string;
          tier_sla_hours?: number;
          wip_limit_per_dev?: number;
          pr_stale_threshold_hours?: number;
          authoritative_deployment_signal?: string;
          authoritative_cd_signal?: string;
        };
        const slug =
          body.slug ||
          body.name
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-|-$/g, "");
        if (projects.some((p) => p.slug === slug)) {
          await fulfillCorsJson(route, 409, {
            detail: `Project with slug '${slug}' already exists in this tenant.`,
          });
          return;
        }
        const created = makeMockProject({
          id: `11111111-1111-1111-1111-00000000000${projects.length + 1}`,
          name: body.name,
          slug,
          tier_sla_hours: body.tier_sla_hours ?? 24,
          wip_limit_per_dev: body.wip_limit_per_dev ?? 3,
          pr_stale_threshold_hours: body.pr_stale_threshold_hours ?? 48,
          authoritative_deployment_signal:
            body.authoritative_deployment_signal ??
            body.authoritative_cd_signal ??
            "github_deployments_api",
          authoritative_cd_signal:
            body.authoritative_cd_signal ??
            body.authoritative_deployment_signal ??
            "github_deployments_api",
          repositories: [],
        });
        projects.push(created);
        await fulfillCorsJson(route, 201, created);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)$/,
      );
      const key = match?.[1] ?? "";
      const project = projects.find((p) => p.id === key || p.slug === key);
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "GET") {
        await fulfillCorsJson(route, 200, project);
        return;
      }
      if (method === "PATCH") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        const body = (route.request().postDataJSON() ?? {}) as Partial<ProjectItem>;
        if (body.name !== undefined) project.name = body.name;
        if (body.slug !== undefined) project.slug = body.slug;
        if (body.tier_sla_hours !== undefined) project.tier_sla_hours = body.tier_sla_hours;
        if (body.wip_limit_per_dev !== undefined) project.wip_limit_per_dev = body.wip_limit_per_dev;
        if (body.pr_stale_threshold_hours !== undefined)
          project.pr_stale_threshold_hours = body.pr_stale_threshold_hours;
        if (body.lead_time_first_commit_strategy !== undefined)
          project.lead_time_first_commit_strategy = body.lead_time_first_commit_strategy;
        if (body.authoritative_deployment_signal !== undefined) {
          project.authoritative_deployment_signal = body.authoritative_deployment_signal;
          project.authoritative_cd_signal =
            body.authoritative_deployment_signal ?? "github_deployments_api";
        }
        if (body.authoritative_cd_signal !== undefined) {
          project.authoritative_deployment_signal = body.authoritative_cd_signal;
          project.authoritative_cd_signal = body.authoritative_cd_signal;
        }
        project.updated_at = new Date().toISOString();
        await fulfillCorsJson(route, 200, project);
        return;
      }
      if (method === "DELETE") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        project.is_active = false;
        project.updated_at = new Date().toISOString();
        await fulfillCorsJson(route, 204);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+\/repositories$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/repositories$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find(
        (p) => p.id === projectId || p.slug === projectId,
      );
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "POST") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        const body = (route.request().postDataJSON() ?? {}) as {
          repository_url?: string;
          full_name?: string;
          default_branch?: string;
          is_private?: boolean;
          integration_id?: string;
        };
        const fullName =
          body.full_name ||
          (body.repository_url || "")
            .replace(/^https?:\/\/github\.com\//, "")
            .replace(/\.git$/, "");
        if (
          project.repositories.some(
            (r) => r.full_name.toLowerCase() === fullName.toLowerCase(),
          )
        ) {
          await fulfillCorsJson(route, 409, {
            detail: `Repository '${fullName}' is already linked to this project.`,
          });
          return;
        }
        const repo = makeMockRepository({
          id: `22222222-2222-2222-2222-00000000000${project.repositories.length + 1}`,
          project_id: project.id,
          integration_id: body.integration_id ?? "int-gh-1",
          full_name: fullName,
          default_branch: body.default_branch || "main",
          is_private: body.is_private ?? true,
        });
        project.repositories.push(repo);
        await fulfillCorsJson(route, 201, repo);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) =>
      /^\/api\/v1\/projects\/[^/]+\/repositories\/[^/]+$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/repositories\/([^/]+)$/,
      );
      const projectId = match?.[1] ?? "";
      const repoId = match?.[2] ?? "";
      const project = projects.find(
        (p) => p.id === projectId || p.slug === projectId,
      );
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "DELETE") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        project.repositories = project.repositories.filter((r) => r.id !== repoId);
        await fulfillCorsJson(route, 204);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) =>
      /^\/api\/v1\/projects\/[^/]+\/deployment-signal$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/deployment-signal$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find(
        (p) => p.id === projectId || p.slug === projectId,
      );
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "GET") {
        await fulfillCorsJson(route, 200, {
          project_id: project.id,
          tenant_id: project.tenant_id,
          authoritative_deployment_signal:
            project.authoritative_deployment_signal ?? "github_deployments_api",
          updated_at: project.updated_at,
        });
        return;
      }
      if (method === "PATCH") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        const body = (route.request().postDataJSON() ?? {}) as {
          authoritative_deployment_signal: string;
        };
        project.authoritative_deployment_signal =
          body.authoritative_deployment_signal;
        project.authoritative_cd_signal = body.authoritative_deployment_signal;
        project.updated_at = new Date().toISOString();
        await fulfillCorsJson(route, 200, {
          project_id: project.id,
          tenant_id: project.tenant_id,
          authoritative_deployment_signal:
            project.authoritative_deployment_signal,
          updated_at: project.updated_at,
        });
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+\/health-config$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/health-config$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find(
        (p) => p.id === projectId || p.slug === projectId,
      );
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      const cfg = getOrInitHealthConfig(project);
      if (method === "GET") {
        await fulfillCorsJson(route, 200, {
          ...cfg,
          webhook_secret: null,
        });
        return;
      }
      if (method === "PATCH") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        const body = (route.request().postDataJSON() ?? {}) as Partial<{
          health_probe_enabled: boolean;
          health_probe_source: string;
          health_probe_url: string | null;
          health_probe_interval_seconds: number;
          health_webhook_secret: string;
        }>;
        if (body.health_probe_enabled !== undefined) {
          cfg.health_probe_enabled = body.health_probe_enabled;
          project.health_probe_enabled = body.health_probe_enabled;
        }
        if (body.health_probe_source !== undefined) {
          cfg.health_probe_source = body.health_probe_source;
          project.health_probe_source = body.health_probe_source;
        }
        if (body.health_probe_url !== undefined) {
          cfg.health_probe_url = body.health_probe_url;
          project.health_probe_url = body.health_probe_url;
        }
        if (body.health_probe_interval_seconds !== undefined) {
          cfg.health_probe_interval_seconds = body.health_probe_interval_seconds;
          project.health_probe_interval_seconds = body.health_probe_interval_seconds;
        }
        let returnedPlaintext: string | null = null;
        if (body.health_webhook_secret) {
          returnedPlaintext = body.health_webhook_secret;
          cfg.webhook_secret_masked = `whsec_••••${returnedPlaintext.slice(-4)}`;
        }
        cfg.updated_at = new Date().toISOString();
        project.updated_at = cfg.updated_at;
        await fulfillCorsJson(route, 200, {
          ...cfg,
          webhook_secret: returnedPlaintext,
        });
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) =>
      /^\/api\/v1\/projects\/[^/]+\/health-config\/rotate-secret$/.test(
        url.pathname,
      ),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/health-config\/rotate-secret$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find(
        (p) => p.id === projectId || p.slug === projectId,
      );
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "POST") {
        if (!isRoleAdmin) {
          await fulfillCorsJson(route, 403, {
            detail: "Forbidden: insufficient permissions",
          });
          return;
        }
        const cfg = getOrInitHealthConfig(project);
        const suffix = String(1000 + secretSeq).slice(-4);
        secretSeq += 1;
        const freshSecret = `whsec_manta_9f41e82b0c7d41a8e592d31b${suffix}`;
        cfg.webhook_secret_masked = `whsec_manta_••••${suffix}`;
        cfg.updated_at = new Date().toISOString();
        await fulfillCorsJson(route, 200, {
          ...cfg,
          webhook_secret: freshSecret,
        });
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+\/health-signals$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/health-signals$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find(
        (p) => p.id === projectId || p.slug === projectId,
      );
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "GET") {
        const list = options?.healthSignalsByProject?.[project.id] ?? [];
        await fulfillCorsJson(route, 200, list);
        return;
      }
      await route.fallback();
    },
  );

  return projects;
}
