import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";

import { GET as getEnvJsResponse } from "@/app/env.js/route";
import { buildApiUrl, publicApiRequest } from "@/lib/api-client";
import {
  getOidcSettings,
  getUserManager,
  logoutUser,
  resetUserManagerForTesting,
} from "@/lib/auth";
import {
  buildTenantAlertStreamUrl,
  buildWebSocketUrl,
  getApiBaseUrl,
  getAppOrigin,
  getEnvLabel,
  getKeycloakAuthority,
  getKeycloakClientId,
  getKeycloakRealm,
  getKeycloakUrl,
  getPostLogoutRedirectUri,
  getRedirectUri,
  getServerRuntimeConfig,
  getWebSocketBaseUrl,
  serializeRuntimeConfigScript,
  type MantaRuntimeConfig,
} from "@/lib/runtime-config";

const MANAGED_ENV_KEYS = [
  "APP_ENV",
  "MANTA_DOMAIN",
  "WEB_BASE_URL",
  "WEBHOOK_BASE_URL",
  "PORTAL_ORIGIN",
  "KEYCLOAK_REALM",
  "NEXT_PUBLIC_API_URL",
  "NEXT_PUBLIC_WS_URL",
  "NEXT_PUBLIC_APP_URL",
  "NEXT_PUBLIC_KEYCLOAK_URL",
  "NEXT_PUBLIC_KEYCLOAK_REALM",
  "NEXT_PUBLIC_KEYCLOAK_CLIENT_ID",
  "NEXT_PUBLIC_REDIRECT_URI",
  "NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI",
  "NEXT_PUBLIC_ENV_LABEL",
] as const;

const savedEnv: Record<string, string | undefined> = {};
const originalWindow = globalThis.window;
const originalFetch = globalThis.fetch;

function clearManagedEnv(): void {
  for (const key of MANAGED_ENV_KEYS) {
    delete process.env[key];
  }
}

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? (store.get(key) ?? null) : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
  };
}

function installMockBrowser(
  url: string,
  runtimeConfig?: MantaRuntimeConfig,
): {
  location: { href: string; origin: string; hostname: string; protocol: string };
  localStorage: Storage;
  sessionStorage: Storage;
} {
  const parsed = new URL(url);
  const location = {
    href: parsed.toString(),
    origin: parsed.origin,
    hostname: parsed.hostname,
    protocol: parsed.protocol,
  };
  const localStorage = createMemoryStorage();
  const sessionStorage = createMemoryStorage();

  const mockWindow = {
    location,
    localStorage,
    sessionStorage,
    dispatchEvent: () => true,
    __MANTA_RUNTIME_CONFIG__: runtimeConfig ? Object.freeze({ ...runtimeConfig }) : undefined,
  };

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    writable: true,
    value: mockWindow,
  });

  return { location, localStorage, sessionStorage };
}

function uninstallMockBrowser(): void {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    writable: true,
    value: originalWindow,
  });
}

function collectSourceFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx|js|jsx)$/.test(entry.name)) {
      results.push(full);
    }
  }
  return results;
}

beforeEach(() => {
  for (const key of MANAGED_ENV_KEYS) {
    savedEnv[key] = process.env[key];
  }
  clearManagedEnv();
  uninstallMockBrowser();
  resetUserManagerForTesting();
});

afterEach(() => {
  for (const key of MANAGED_ENV_KEYS) {
    if (savedEnv[key] === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = savedEnv[key];
    }
  }
  uninstallMockBrowser();
  globalThis.fetch = originalFetch;
  resetUserManagerForTesting();
});

describe("TASK-301-6: Runtime production URL injection & /env.js endpoint", () => {
  it("resolves production HTTPS URLs matching backend/.env.prod.example from explicit NEXT_PUBLIC_* env", () => {
    process.env.APP_ENV = "production";
    process.env.NEXT_PUBLIC_APP_URL = "https://manta.example.com/";
    process.env.NEXT_PUBLIC_API_URL = "https://api.manta.example.com/";
    process.env.NEXT_PUBLIC_KEYCLOAK_URL = "https://auth.manta.example.com/";
    process.env.NEXT_PUBLIC_KEYCLOAK_REALM = "manta";
    process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID = "manta-portal";
    process.env.NEXT_PUBLIC_ENV_LABEL = "PRODUCTION";

    assert.equal(getAppOrigin(), "https://manta.example.com");
    assert.equal(getApiBaseUrl(), "https://api.manta.example.com");
    assert.equal(getKeycloakUrl(), "https://auth.manta.example.com");
    assert.equal(getKeycloakRealm(), "manta");
    assert.equal(getKeycloakClientId(), "manta-portal");
    assert.equal(getKeycloakAuthority(), "https://auth.manta.example.com/realms/manta");
    assert.equal(getEnvLabel(), "PRODUCTION");
    assert.equal(getRedirectUri(), "https://manta.example.com/auth/callback");
    assert.equal(getPostLogoutRedirectUri(), "https://manta.example.com/signed-out");
    assert.equal(getWebSocketBaseUrl(), "wss://api.manta.example.com/api/v1/ws");
    assert.equal(
      buildApiUrl("/api/v1/auth/me"),
      "https://api.manta.example.com/api/v1/auth/me",
    );
  });

  it("derives canonical production URLs from MANTA_DOMAIN and serves dynamic no-store /env.js", async () => {
    process.env.MANTA_DOMAIN = "acme.dev";
    process.env.NEXT_PUBLIC_ENV_LABEL = "STAGING";

    const response1 = getEnvJsResponse();
    assert.equal(response1.status, 200);
    assert.equal(
      response1.headers.get("content-type"),
      "application/javascript; charset=utf-8",
    );
    assert.match(response1.headers.get("cache-control") ?? "", /no-store/);
    assert.equal(response1.headers.get("x-content-type-options"), "nosniff");

    const script1 = await response1.text();
    assert.match(script1, /^window\.__MANTA_RUNTIME_CONFIG__=Object\.freeze\(/);
    assert.ok(script1.includes("https://api.manta.acme.dev"));
    assert.ok(script1.includes("wss://api.manta.acme.dev/api/v1/ws"));
    assert.ok(script1.includes("https://auth.manta.acme.dev"));
    assert.ok(script1.includes("https://manta.acme.dev"));
    assert.ok(script1.includes('"envLabel":"STAGING"'));
    assert.equal(script1.includes("localhost"), false);

    // Single-digest promotion: changing process.env at runtime immediately updates /env.js
    process.env.MANTA_DOMAIN = "prod.example.org";
    process.env.NEXT_PUBLIC_ENV_LABEL = "PRODUCTION";

    const script2 = await getEnvJsResponse().text();
    assert.ok(script2.includes("https://api.manta.prod.example.org"));
    assert.ok(script2.includes("wss://api.manta.prod.example.org/api/v1/ws"));
    assert.ok(script2.includes("https://auth.manta.prod.example.org"));
    assert.ok(script2.includes("https://manta.prod.example.org"));
    assert.ok(script2.includes('"envLabel":"PRODUCTION"'));
  });

  it("strips loopback wsUrl/redirect overrides in APP_ENV=production and derives wss:// from apiUrl", async () => {
    process.env.APP_ENV = "production";
    process.env.NEXT_PUBLIC_API_URL = "https://api.manta.example.com";
    process.env.NEXT_PUBLIC_KEYCLOAK_URL = "https://auth.manta.example.com";
    process.env.NEXT_PUBLIC_APP_URL = "https://manta.example.com";
    process.env.NEXT_PUBLIC_WS_URL = "ws://localhost:8000/api/v1/ws";

    const cfg = getServerRuntimeConfig();
    assert.equal(cfg.wsUrl, "wss://api.manta.example.com/api/v1/ws");

    const script = await getEnvJsResponse().text();
    assert.ok(script.includes("wss://api.manta.example.com/api/v1/ws"));
    assert.equal(script.includes("localhost"), false);
  });

  it("escapes HTML-unsafe characters in runtime config script and ignores placeholder templates", () => {
    process.env.MANTA_DOMAIN = "<REPLACE_DOMAIN>";
    process.env.NEXT_PUBLIC_API_URL = "https://api.manta.<REPLACE_DOMAIN>";

    const cfg = getServerRuntimeConfig();
    assert.equal(cfg.apiUrl, undefined);
    assert.equal(cfg.keycloakUrl, undefined);

    const serialized = serializeRuntimeConfigScript({
      envLabel: "</script><script>alert(1)</script>&",
    });
    assert.equal(serialized.includes("</script>"), false);
    assert.ok(serialized.includes("\\u003c/script\\u003e"));
    assert.ok(serialized.includes("\\u0026"));
  });

  it("blocks localhost fallbacks in deployed browsers and prefers injected runtime config", () => {
    // 1. Valid runtime config injected by /env.js
    installMockBrowser("https://manta.example.com/projects", {
      apiUrl: "https://api.manta.example.com",
      keycloakUrl: "https://auth.manta.example.com",
      keycloakRealm: "manta",
      keycloakClientId: "manta-portal",
      envLabel: "PRODUCTION",
    });

    assert.equal(getApiBaseUrl(), "https://api.manta.example.com");
    assert.equal(getKeycloakUrl(), "https://auth.manta.example.com");
    assert.equal(getRedirectUri(), "https://manta.example.com/auth/callback");
    assert.equal(getPostLogoutRedirectUri(), "https://manta.example.com/signed-out");
    assert.equal(getWebSocketBaseUrl(), "wss://api.manta.example.com/api/v1/ws");

    // 2. Stale localhost runtime values on https://manta.<domain> are rejected & safely derived
    installMockBrowser("https://manta.example.com/projects", {
      apiUrl: "http://localhost:8000",
      keycloakUrl: "http://localhost:8080",
      wsUrl: "ws://localhost:8000/api/v1/ws",
      redirectUri: "http://localhost:3000/auth/callback",
      postLogoutRedirectUri: "http://localhost:3000/signed-out",
    });

    assert.equal(getApiBaseUrl(), "https://api.manta.example.com");
    assert.equal(getKeycloakUrl(), "https://auth.manta.example.com");
    assert.equal(getWebSocketBaseUrl(), "wss://api.manta.example.com/api/v1/ws");
    assert.equal(getRedirectUri(), "https://manta.example.com/auth/callback");
    assert.equal(getPostLogoutRedirectUri(), "https://manta.example.com/signed-out");

    // 3. Custom non-manta public domain without explicit config fails fast (never leaks localhost)
    installMockBrowser("https://portal.customcorp.io/projects", {});
    assert.throws(() => getApiBaseUrl(), /NEXT_PUBLIC_API_URL/);
    assert.throws(() => getKeycloakUrl(), /NEXT_PUBLIC_KEYCLOAK_URL/);
  });

  it("preserves local development & CI Playwright loopback defaults on http://localhost:3000", () => {
    installMockBrowser("http://localhost:3000/projects");

    assert.equal(getApiBaseUrl(), "http://localhost:8000");
    assert.equal(getKeycloakUrl(), "http://localhost:8080");
    assert.equal(getKeycloakAuthority(), "http://localhost:8080/realms/manta");
    assert.equal(getRedirectUri(), "http://localhost:3000/auth/callback");
    assert.equal(getPostLogoutRedirectUri(), "http://localhost:3000/signed-out");
    assert.equal(getWebSocketBaseUrl(), "ws://localhost:8000/api/v1/ws");
  });
});

describe("TASK-301-6: WebSocket wss:// URL builder", () => {
  it("builds production wss:// URLs and deduplicates /api/v1/ws prefix", () => {
    installMockBrowser("https://manta.example.com/risks-alerts", {
      apiUrl: "https://api.manta.example.com/",
    });

    assert.equal(getWebSocketBaseUrl(), "wss://api.manta.example.com/api/v1/ws");
    assert.equal(
      buildWebSocketUrl("/tenants/t-1/stream"),
      "wss://api.manta.example.com/api/v1/ws/tenants/t-1/stream",
    );
    assert.equal(
      buildWebSocketUrl("/api/v1/ws/tenants/t-1/stream"),
      "wss://api.manta.example.com/api/v1/ws/tenants/t-1/stream",
    );
    assert.equal(
      buildTenantAlertStreamUrl("11111111-1111-1111-1111-111111111111", {
        token: "jwt.tok",
        cursor: "17000-0",
      }),
      "wss://api.manta.example.com/api/v1/ws/tenants/11111111-1111-1111-1111-111111111111/stream?token=jwt.tok&cursor=17000-0",
    );
  });

  it("upgrades insecure ws:// override to wss:// on HTTPS production origins and appends /api/v1/ws when bare", () => {
    installMockBrowser("https://manta.example.com/", {
      apiUrl: "https://api.manta.example.com",
      wsUrl: "ws://api.manta.example.com",
    });

    assert.equal(getWebSocketBaseUrl(), "wss://api.manta.example.com/api/v1/ws");
    assert.equal(
      buildTenantAlertStreamUrl("tenant-alpha"),
      "wss://api.manta.example.com/api/v1/ws/tenants/tenant-alpha/stream",
    );
  });
});

describe("TASK-301-6: Auth OIDC settings, SSR fallbacks, post-logout redirect & API client audit", () => {
  it("resolves SSR/non-browser OIDC settings cleanly for public domain without localhost", () => {
    process.env.APP_ENV = "production";
    process.env.NEXT_PUBLIC_API_URL = "https://api.manta.example.com";
    process.env.NEXT_PUBLIC_KEYCLOAK_URL = "https://auth.manta.example.com";
    process.env.NEXT_PUBLIC_KEYCLOAK_REALM = "manta";
    process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID = "manta-portal";

    const settings = getOidcSettings();
    assert.equal(settings.authority, "https://auth.manta.example.com/realms/manta");
    assert.equal(settings.client_id, "manta-portal");
    assert.equal(settings.redirect_uri, "https://manta.example.com/auth/callback");
    assert.equal(settings.post_logout_redirect_uri, "https://manta.example.com/signed-out");
    assert.equal(
      settings.metadata?.authorization_endpoint,
      "https://auth.manta.example.com/realms/manta/protocol/openid-connect/auth",
    );
    assert.equal(
      settings.metadata?.end_session_endpoint,
      "https://auth.manta.example.com/realms/manta/protocol/openid-connect/logout",
    );
    assert.equal(JSON.stringify(settings).includes("localhost"), false);
  });

  it("fails fast in strict SSR APP_ENV=production if production URLs are missing or localhost", () => {
    process.env.APP_ENV = "production";
    process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
    process.env.NEXT_PUBLIC_KEYCLOAK_URL = "http://localhost:8080";

    assert.throws(() => getApiBaseUrl(), /APP_ENV=production/);
    assert.throws(() => getKeycloakUrl(), /APP_ENV=production/);
    assert.throws(() => getAppOrigin(), /APP_ENV=production/);
  });

  it("honors NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI and public domain in logoutUser()", async () => {
    installMockBrowser("https://manta.example.com/projects", {
      apiUrl: "https://api.manta.example.com",
      keycloakUrl: "https://auth.manta.example.com",
      keycloakRealm: "manta",
      keycloakClientId: "manta-portal",
      postLogoutRedirectUri: "https://manta.example.com/signed-out?source=portal",
    });

    const manager = getUserManager();
    let capturedArgs: Record<string, unknown> | undefined;
    manager.getUser = async () => null;
    manager.removeUser = async () => undefined;
    manager.signoutRedirect = async (args) => {
      capturedArgs = args as Record<string, unknown>;
    };

    await logoutUser(undefined, "Acme Engineering");

    assert.equal(
      capturedArgs?.post_logout_redirect_uri,
      "https://manta.example.com/signed-out?source=portal&workspace=Acme+Engineering",
    );
  });

  it("computes public post_logout_redirect_uri in SSR/non-browser mode without localhost:3000", () => {
    process.env.APP_ENV = "production";
    process.env.MANTA_DOMAIN = "example.com";

    assert.equal(
      getPostLogoutRedirectUri({ workspace: "Tenant Alpha" }),
      "https://manta.example.com/signed-out?workspace=Tenant+Alpha",
    );
  });

  it("routes publicApiRequest() to runtime https://api.manta.<domain> with zero localhost leak", async () => {
    installMockBrowser("https://manta.example.com/accept-invite", {
      apiUrl: "https://api.manta.example.com",
      keycloakUrl: "https://auth.manta.example.com",
    });

    let requestedUrl = "";
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      requestedUrl = String(input);
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    const result = await publicApiRequest<{ ok: boolean }>("/api/v1/invitations/verify");
    assert.deepEqual(result, { ok: true });
    assert.equal(requestedUrl, "https://api.manta.example.com/api/v1/invitations/verify");
  });
});

describe("TASK-301-6: Static web/Dockerfile & web/src localhost leak audit", () => {
  const webRoot = process.env.MANTA_WEB_ROOT || path.resolve(__dirname, "..", "..");

  it("web/Dockerfile wires all required NEXT_PUBLIC_* args with zero hardcoded localhost defaults", () => {
    const dockerfile = fs.readFileSync(path.join(webRoot, "Dockerfile"), "utf-8");

    assert.equal(/localhost:8000|localhost:8080|localhost:3000/.test(dockerfile), false);

    for (const requiredVar of [
      "NEXT_PUBLIC_API_URL",
      "NEXT_PUBLIC_WS_URL",
      "NEXT_PUBLIC_APP_URL",
      "NEXT_PUBLIC_KEYCLOAK_URL",
      "NEXT_PUBLIC_KEYCLOAK_REALM",
      "NEXT_PUBLIC_KEYCLOAK_CLIENT_ID",
      "NEXT_PUBLIC_REDIRECT_URI",
      "NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI",
      "NEXT_PUBLIC_ENV_LABEL",
    ]) {
      const argMatches = dockerfile.match(new RegExp(`^ARG\\s+${requiredVar}=`, "gm")) ?? [];
      assert.ok(
        argMatches.length >= 2,
        `Expected ${requiredVar} ARG in both builder and runner stages of web/Dockerfile`,
      );
      assert.ok(
        dockerfile.includes(`${requiredVar}=\${${requiredVar}}`),
        `Expected ${requiredVar} ENV wiring in web/Dockerfile`,
      );
    }
  });

  it("web/src contains zero hardcoded localhost URLs or bypassed NEXT_PUBLIC_ URL reads outside runtime-config.ts", () => {
    const srcRoot = path.join(webRoot, "src");
    const runtimeConfigPath = path.join(srcRoot, "lib", "runtime-config.ts");
    const violations: string[] = [];

    for (const filePath of collectSourceFiles(srcRoot)) {
      if (path.resolve(filePath) === path.resolve(runtimeConfigPath)) continue;
      const content = fs.readFileSync(filePath, "utf-8");
      const rel = path.relative(webRoot, filePath);

      if (/localhost:(3000|8000|8080)|127\.0\.0\.1:(3000|8000|8080)/.test(content)) {
        violations.push(`${rel}: contains hardcoded localhost port URL`);
      }
      if (
        /process\.env\.NEXT_PUBLIC_(API_URL|WS_URL|KEYCLOAK_URL|KEYCLOAK_REALM|KEYCLOAK_CLIENT_ID|APP_URL|REDIRECT_URI|POST_LOGOUT_REDIRECT_URI|ENV_LABEL)/.test(
          content,
        )
      ) {
        violations.push(`${rel}: reads NEXT_PUBLIC_* env directly instead of @/lib/runtime-config`);
      }
    }

    assert.deepEqual(violations, []);
  });
});
