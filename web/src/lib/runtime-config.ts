export interface MantaRuntimeConfig {
  apiUrl?: string;
  wsUrl?: string;
  keycloakUrl?: string;
  keycloakRealm?: string;
  keycloakClientId?: string;
  appUrl?: string;
  redirectUri?: string;
  postLogoutRedirectUri?: string;
  envLabel?: string;
}

declare global {
  interface Window {
    __MANTA_RUNTIME_CONFIG__?: Readonly<MantaRuntimeConfig>;
  }
}

const DEFAULT_DEV_API_URL = "http://localhost:8000";
const DEFAULT_DEV_KEYCLOAK_URL = "http://localhost:8080";
const DEFAULT_DEV_APP_URL = "http://localhost:3000";
const DEFAULT_KEYCLOAK_REALM = "manta";
const DEFAULT_KEYCLOAK_CLIENT_ID = "manta-portal";
const DEFAULT_ENV_LABEL = "PRODUCTION";
const DEFAULT_WS_PATH_PREFIX = "/api/v1/ws";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

export function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase();
  if (!normalized) return false;
  return LOOPBACK_HOSTS.has(normalized) || normalized.endsWith(".localhost");
}

export function isLoopbackUrl(rawUrl: string): boolean {
  try {
    const parsed = new URL(rawUrl);
    return isLoopbackHostname(parsed.hostname);
  } catch {
    return false;
  }
}

function normalizeValue(raw?: string | null): string | undefined {
  if (typeof raw !== "string") return undefined;
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  // Ignore unrendered template placeholders such as <REPLACE_DOMAIN>.
  if (trimmed.includes("<") || trimmed.includes(">")) return undefined;
  return trimmed;
}

export function normalizeBaseUrl(raw?: string | null): string | undefined {
  const value = normalizeValue(raw);
  if (!value) return undefined;
  return value.replace(/\/+$/, "");
}

function normalizeDomain(raw?: string | null): string | undefined {
  const value = normalizeValue(raw);
  if (!value) return undefined;
  const withoutScheme = value.replace(/^https?:\/\//i, "").replace(/\/.*$/, "").toLowerCase();
  if (!withoutScheme || isLoopbackHostname(withoutScheme)) return undefined;
  return withoutScheme;
}

/**
 * Dynamic server-side process.env lookup (bracket access prevents Next.js SWC
 * from statically inlining build-time empty/dev NEXT_PUBLIC_* values at compile time).
 */
function readServerEnv(key: string): string | undefined {
  if (typeof process === "undefined" || !process.env) return undefined;
  const envRecord = process.env as Record<string, string | undefined>;
  return normalizeValue(envRecord[key]);
}

function isStrictProductionServer(): boolean {
  return readServerEnv("APP_ENV")?.toLowerCase() === "production";
}

function getBrowserWindowConfig(): Readonly<MantaRuntimeConfig> | undefined {
  if (typeof window === "undefined") return undefined;
  return window.__MANTA_RUNTIME_CONFIG__;
}

function isBrowserOnPublicDomain(): boolean {
  if (typeof window === "undefined" || !window.location?.hostname) return false;
  return !isLoopbackHostname(window.location.hostname);
}

/**
 * Reads build-time inlined NEXT_PUBLIC_* values (literal access is required by Next.js
 * webpack DefinePlugin for client bundles) as a secondary fallback when runtime config
 * is not present (e.g. unit tests, static storybook, or build-arg-only deployments).
 */
function getBuildTimePublicConfig(): MantaRuntimeConfig {
  return {
    apiUrl: normalizeBaseUrl(process.env.NEXT_PUBLIC_API_URL),
    wsUrl: normalizeBaseUrl(process.env.NEXT_PUBLIC_WS_URL),
    keycloakUrl: normalizeBaseUrl(process.env.NEXT_PUBLIC_KEYCLOAK_URL),
    keycloakRealm: normalizeValue(process.env.NEXT_PUBLIC_KEYCLOAK_REALM),
    keycloakClientId: normalizeValue(process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID),
    appUrl: normalizeBaseUrl(process.env.NEXT_PUBLIC_APP_URL),
    redirectUri: normalizeValue(process.env.NEXT_PUBLIC_REDIRECT_URI),
    postLogoutRedirectUri: normalizeValue(process.env.NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI),
    envLabel: normalizeValue(process.env.NEXT_PUBLIC_ENV_LABEL),
  };
}

/**
 * Collects public runtime configuration from live Node.js container environment
 * variables (supports single-digest container promotion across domains without rebuild).
 * Matches backend/.env.prod.example conventions:
 *   Portal:   https://manta.<domain>
 *   API/WS:   https://api.manta.<domain>
 *   Keycloak: https://auth.manta.<domain>
 */
export function getServerRuntimeConfig(): MantaRuntimeConfig {
  const domain = normalizeDomain(readServerEnv("MANTA_DOMAIN"));
  const strictProd = isStrictProductionServer();
  const build = getBuildTimePublicConfig();
  const filterBuildUrl = (candidate?: string): string | undefined => {
    if (!candidate) return undefined;
    if ((strictProd || domain) && isLoopbackUrl(candidate)) return undefined;
    return candidate;
  };
  const filterRuntimeUrl = (candidate?: string): string | undefined => {
    if (!candidate) return undefined;
    if (strictProd && isLoopbackUrl(candidate)) return undefined;
    return candidate;
  };

  const apiUrl =
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("NEXT_PUBLIC_API_URL"))) ||
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("WEBHOOK_BASE_URL"))) ||
    (domain ? `https://api.manta.${domain}` : undefined) ||
    filterBuildUrl(build.apiUrl);

  const keycloakUrl =
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("NEXT_PUBLIC_KEYCLOAK_URL"))) ||
    (domain ? `https://auth.manta.${domain}` : undefined) ||
    filterBuildUrl(build.keycloakUrl);

  const appUrl =
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("NEXT_PUBLIC_APP_URL"))) ||
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("WEB_BASE_URL"))) ||
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("PORTAL_ORIGIN"))) ||
    (domain ? `https://manta.${domain}` : undefined) ||
    filterBuildUrl(build.appUrl);

  const explicitWsUrl =
    filterRuntimeUrl(normalizeBaseUrl(readServerEnv("NEXT_PUBLIC_WS_URL"))) ||
    (apiUrl && !isLoopbackUrl(apiUrl)
      ? undefined
      : filterBuildUrl(build.wsUrl));

  let wsUrl: string | undefined;
  if (explicitWsUrl) {
    const normalizedWs = toWebSocketProtocol(
      explicitWsUrl,
      strictProd && !isLoopbackUrl(explicitWsUrl),
    );
    const parsedWs = new URL(normalizedWs);
    wsUrl =
      parsedWs.pathname === "/" || parsedWs.pathname === ""
        ? `${normalizedWs}${DEFAULT_WS_PATH_PREFIX}`
        : normalizedWs;
  } else if (apiUrl && !isLoopbackUrl(apiUrl)) {
    const derivedWs = toWebSocketProtocol(apiUrl, true);
    wsUrl = derivedWs.endsWith(DEFAULT_WS_PATH_PREFIX)
      ? derivedWs
      : `${derivedWs}${DEFAULT_WS_PATH_PREFIX}`;
  }

  const keycloakRealm =
    readServerEnv("NEXT_PUBLIC_KEYCLOAK_REALM") ||
    readServerEnv("KEYCLOAK_REALM") ||
    build.keycloakRealm ||
    DEFAULT_KEYCLOAK_REALM;

  const keycloakClientId =
    readServerEnv("NEXT_PUBLIC_KEYCLOAK_CLIENT_ID") ||
    build.keycloakClientId ||
    DEFAULT_KEYCLOAK_CLIENT_ID;

  const redirectUri =
    filterRuntimeUrl(readServerEnv("NEXT_PUBLIC_REDIRECT_URI")) ||
    filterBuildUrl(build.redirectUri);
  const postLogoutRedirectUri =
    filterRuntimeUrl(readServerEnv("NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI")) ||
    filterBuildUrl(build.postLogoutRedirectUri);

  const envLabel =
    readServerEnv("NEXT_PUBLIC_ENV_LABEL") || build.envLabel || DEFAULT_ENV_LABEL;

  const config: MantaRuntimeConfig = {
    keycloakRealm,
    keycloakClientId,
    envLabel,
  };

  if (apiUrl) config.apiUrl = apiUrl;
  if (wsUrl) config.wsUrl = wsUrl;
  if (keycloakUrl) config.keycloakUrl = keycloakUrl;
  if (appUrl) config.appUrl = appUrl;
  if (redirectUri) config.redirectUri = redirectUri;
  if (postLogoutRedirectUri) config.postLogoutRedirectUri = postLogoutRedirectUri;

  return config;
}

/**
 * Serializes MantaRuntimeConfig into an XSS-safe JavaScript snippet that sets
 * a frozen `window.__MANTA_RUNTIME_CONFIG__` global before React hydrates.
 */
export function serializeRuntimeConfigScript(config: MantaRuntimeConfig): string {
  const safeJson = JSON.stringify(config)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
  return `window.__MANTA_RUNTIME_CONFIG__=Object.freeze(${safeJson});`;
}

function getEffectiveConfig(): MantaRuntimeConfig {
  if (typeof window === "undefined") {
    return getServerRuntimeConfig();
  }

  const runtime = getBrowserWindowConfig();
  const build = getBuildTimePublicConfig();

  return {
    apiUrl: normalizeBaseUrl(runtime?.apiUrl) || build.apiUrl,
    wsUrl: normalizeBaseUrl(runtime?.wsUrl) || build.wsUrl,
    keycloakUrl: normalizeBaseUrl(runtime?.keycloakUrl) || build.keycloakUrl,
    keycloakRealm: normalizeValue(runtime?.keycloakRealm) || build.keycloakRealm,
    keycloakClientId: normalizeValue(runtime?.keycloakClientId) || build.keycloakClientId,
    appUrl: normalizeBaseUrl(runtime?.appUrl) || build.appUrl,
    redirectUri: normalizeValue(runtime?.redirectUri) || build.redirectUri,
    postLogoutRedirectUri:
      normalizeValue(runtime?.postLogoutRedirectUri) || build.postLogoutRedirectUri,
    envLabel: normalizeValue(runtime?.envLabel) || build.envLabel,
  };
}

function deriveServiceOriginFromPortalHost(subdomainPrefix: "api" | "auth"): string | undefined {
  if (typeof window === "undefined" || !window.location?.hostname) return undefined;
  const host = window.location.hostname.toLowerCase();
  if (isLoopbackHostname(host)) return undefined;
  if (host.startsWith("manta.")) {
    return `https://${subdomainPrefix}.${host}`;
  }
  return undefined;
}

function derivePortalOriginFromServiceUrl(serviceUrl?: string): string | undefined {
  if (!serviceUrl) return undefined;
  try {
    const parsed = new URL(serviceUrl);
    if (isLoopbackHostname(parsed.hostname)) return undefined;
    const host = parsed.hostname.toLowerCase();
    if (host.startsWith("api.manta.")) {
      return `${parsed.protocol}//${host.slice("api.".length)}`;
    }
    if (host.startsWith("auth.manta.")) {
      return `${parsed.protocol}//${host.slice("auth.".length)}`;
    }
    if (host.startsWith("api.")) {
      return `${parsed.protocol}//manta.${host.slice("api.".length)}`;
    }
    if (host.startsWith("auth.")) {
      return `${parsed.protocol}//manta.${host.slice("auth.".length)}`;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

export function getApiBaseUrl(): string {
  const cfg = getEffectiveConfig();
  const configured = normalizeBaseUrl(cfg.apiUrl);

  if (isBrowserOnPublicDomain()) {
    if (configured && !isLoopbackUrl(configured)) {
      return configured;
    }
    const derived = deriveServiceOriginFromPortalHost("api");
    if (derived) return derived;
    throw new Error(
      "NEXT_PUBLIC_API_URL must be configured with a non-localhost HTTPS URL in deployed environments.",
    );
  }

  if (typeof window === "undefined" && isStrictProductionServer()) {
    if (configured && !isLoopbackUrl(configured)) {
      return configured;
    }
    throw new Error(
      "NEXT_PUBLIC_API_URL (or MANTA_DOMAIN / WEBHOOK_BASE_URL) must be set to a non-localhost HTTPS URL when APP_ENV=production.",
    );
  }

  return configured || DEFAULT_DEV_API_URL;
}

export function getKeycloakUrl(): string {
  const cfg = getEffectiveConfig();
  const configured = normalizeBaseUrl(cfg.keycloakUrl);

  if (isBrowserOnPublicDomain()) {
    if (configured && !isLoopbackUrl(configured)) {
      return configured;
    }
    const derived = deriveServiceOriginFromPortalHost("auth");
    if (derived) return derived;
    throw new Error(
      "NEXT_PUBLIC_KEYCLOAK_URL must be configured with a non-localhost HTTPS URL in deployed environments.",
    );
  }

  if (typeof window === "undefined" && isStrictProductionServer()) {
    if (configured && !isLoopbackUrl(configured)) {
      return configured;
    }
    throw new Error(
      "NEXT_PUBLIC_KEYCLOAK_URL (or MANTA_DOMAIN) must be set to a non-localhost HTTPS URL when APP_ENV=production.",
    );
  }

  return configured || DEFAULT_DEV_KEYCLOAK_URL;
}

export function getKeycloakRealm(): string {
  const cfg = getEffectiveConfig();
  return normalizeValue(cfg.keycloakRealm) || DEFAULT_KEYCLOAK_REALM;
}

export function getKeycloakClientId(): string {
  const cfg = getEffectiveConfig();
  return normalizeValue(cfg.keycloakClientId) || DEFAULT_KEYCLOAK_CLIENT_ID;
}

export function getKeycloakAuthority(): string {
  return `${getKeycloakUrl()}/realms/${encodeURIComponent(getKeycloakRealm())}`;
}

export function getEnvLabel(): string {
  const cfg = getEffectiveConfig();
  return normalizeValue(cfg.envLabel) || DEFAULT_ENV_LABEL;
}

/**
 * Resolves the portal's public origin (`https://manta.<domain>` in production,
 * `window.location.origin` in browsers, or configured public URL in SSR/non-browser).
 */
export function getAppOrigin(): string {
  if (
    typeof window !== "undefined" &&
    window.location?.origin &&
    window.location.origin !== "null"
  ) {
    return normalizeBaseUrl(window.location.origin) || DEFAULT_DEV_APP_URL;
  }

  const cfg = getEffectiveConfig();
  const explicitAppUrl = normalizeBaseUrl(cfg.appUrl);
  if (explicitAppUrl && (!isStrictProductionServer() || !isLoopbackUrl(explicitAppUrl))) {
    return explicitAppUrl;
  }

  for (const candidateUri of [cfg.redirectUri, cfg.postLogoutRedirectUri]) {
    if (!candidateUri) continue;
    try {
      const origin = new URL(candidateUri).origin;
      if (!isStrictProductionServer() || !isLoopbackUrl(origin)) {
        return normalizeBaseUrl(origin) || origin;
      }
    } catch {
      // Ignore invalid override URLs and continue to next fallback.
    }
  }

  const derivedFromService =
    derivePortalOriginFromServiceUrl(cfg.apiUrl) ||
    derivePortalOriginFromServiceUrl(cfg.keycloakUrl);
  if (derivedFromService) {
    return derivedFromService;
  }

  if (isStrictProductionServer()) {
    throw new Error(
      "NEXT_PUBLIC_APP_URL (or MANTA_DOMAIN / WEB_BASE_URL) must be set to a non-localhost HTTPS URL when APP_ENV=production.",
    );
  }

  return DEFAULT_DEV_APP_URL;
}

function appendQueryParams(
  rawUrl: string,
  params?: Record<string, string | number | boolean | undefined | null>,
): string {
  if (!params) return rawUrl;
  const entries = Object.entries(params).filter(
    ([, val]) => val !== undefined && val !== null && String(val).length > 0,
  );
  if (entries.length === 0) return rawUrl;

  try {
    const parsed = new URL(rawUrl);
    for (const [key, val] of entries) {
      parsed.searchParams.set(key, String(val));
    }
    return parsed.toString();
  } catch {
    const search = new URLSearchParams();
    for (const [key, val] of entries) {
      search.set(key, String(val));
    }
    const separator = rawUrl.includes("?") ? "&" : "?";
    return `${rawUrl}${separator}${search.toString()}`;
  }
}

export function getRedirectUri(): string {
  const cfg = getEffectiveConfig();
  const explicit = normalizeValue(cfg.redirectUri);

  if (explicit) {
    const rejectLoopback =
      isBrowserOnPublicDomain() || (typeof window === "undefined" && isStrictProductionServer());
    if (!rejectLoopback || !isLoopbackUrl(explicit)) {
      return explicit;
    }
  }

  return `${getAppOrigin()}/auth/callback`;
}

export function getPostLogoutRedirectUri(
  queryParams?: Record<string, string | number | boolean | undefined | null>,
): string {
  const cfg = getEffectiveConfig();
  const explicit = normalizeValue(cfg.postLogoutRedirectUri);

  const rejectLoopback =
    isBrowserOnPublicDomain() || (typeof window === "undefined" && isStrictProductionServer());

  const baseUri =
    explicit && (!rejectLoopback || !isLoopbackUrl(explicit))
      ? explicit
      : `${getAppOrigin()}/signed-out`;

  return appendQueryParams(baseUri, queryParams);
}

function toWebSocketProtocol(rawUrl: string, forceSecure: boolean): string {
  const parsed = new URL(rawUrl);
  if (parsed.protocol === "https:") {
    parsed.protocol = "wss:";
  } else if (parsed.protocol === "http:") {
    parsed.protocol = forceSecure ? "wss:" : "ws:";
  } else if (parsed.protocol === "ws:" && forceSecure) {
    parsed.protocol = "wss:";
  }
  return parsed.toString().replace(/\/+$/, "");
}

/**
 * Resolves the WebSocket base URL (e.g. `wss://api.manta.<domain>/api/v1/ws` in production
 * or `ws://localhost:8000/api/v1/ws` locally). Upgrades to `wss://` automatically for
 * HTTPS APIs or HTTPS public portal pages to prevent mixed-content errors.
 */
export function getWebSocketBaseUrl(): string {
  const cfg = getEffectiveConfig();
  const configuredWs = normalizeBaseUrl(cfg.wsUrl);
  const publicBrowser = isBrowserOnPublicDomain();
  const securePage =
    (typeof window !== "undefined" && window.location?.protocol === "https:") ||
    (typeof window === "undefined" && isStrictProductionServer());

  if (configuredWs) {
    const rejectLoopback =
      publicBrowser || (typeof window === "undefined" && isStrictProductionServer());
    if (!rejectLoopback || !isLoopbackUrl(configuredWs)) {
      const normalizedWs = toWebSocketProtocol(
        configuredWs,
        securePage && !isLoopbackUrl(configuredWs),
      );
      const parsedWs = new URL(normalizedWs);
      if (parsedWs.pathname === "/" || parsedWs.pathname === "") {
        return `${normalizedWs}${DEFAULT_WS_PATH_PREFIX}`;
      }
      return normalizedWs;
    }
  }

  const apiBase = getApiBaseUrl();
  const wsOriginWithApiPath = toWebSocketProtocol(
    apiBase,
    securePage && !isLoopbackUrl(apiBase),
  );
  if (wsOriginWithApiPath.endsWith(DEFAULT_WS_PATH_PREFIX)) {
    return wsOriginWithApiPath;
  }
  return `${wsOriginWithApiPath}${DEFAULT_WS_PATH_PREFIX}`;
}

/**
 * Builds a complete WebSocket endpoint URL from either a WS-relative path
 * (e.g. `/tenants/{id}/stream`) or an API-absolute path (`/api/v1/ws/tenants/{id}/stream`).
 */
export function buildWebSocketUrl(
  path: string,
  queryParams?: Record<string, string | number | boolean | undefined | null>,
): string {
  const base = getWebSocketBaseUrl();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  let fullUrl: string;
  if (
    base.endsWith(DEFAULT_WS_PATH_PREFIX) &&
    (normalizedPath === DEFAULT_WS_PATH_PREFIX ||
      normalizedPath.startsWith(`${DEFAULT_WS_PATH_PREFIX}/`))
  ) {
    const rootWithoutPrefix = base.slice(0, -DEFAULT_WS_PATH_PREFIX.length);
    fullUrl = `${rootWithoutPrefix}${normalizedPath}`;
  } else {
    fullUrl = `${base}${normalizedPath}`;
  }

  return appendQueryParams(fullUrl, queryParams);
}

/**
 * Builds the authenticated tenant alert stream WebSocket URL matching
 * backend `app/ws/router.py` (`@router.websocket("/tenants/{tenant_id}/stream")`).
 */
export function buildTenantAlertStreamUrl(
  tenantId: string,
  options?: { token?: string; cursor?: string },
): string {
  return buildWebSocketUrl(`/tenants/${encodeURIComponent(tenantId)}/stream`, {
    token: options?.token,
    cursor: options?.cursor,
  });
}
