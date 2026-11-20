import {
  UserManager,
  UserManagerSettings,
  WebStorageStateStore,
  SigninRedirectArgs,
  User,
} from "oidc-client-ts";
import {
  withRenewLock,
  broadcastAuthMessage,
  getTabId,
} from "@/lib/auth-tab-sync";

const KEYCLOAK_URL = process.env.NEXT_PUBLIC_KEYCLOAK_URL || "http://localhost:8080";
const REALM = process.env.NEXT_PUBLIC_KEYCLOAK_REALM || "manta";
const CLIENT_ID = process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID || "manta-portal";
const AUTHORITY = `${KEYCLOAK_URL}/realms/${REALM}`;
const BACKEND_API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface BackendUserClaims {
  sub: string;
  email?: string | null;
  preferred_username?: string | null;
  name?: string | null;
  tenant_id: string;
  tenant_name?: string | null;
  roles: string[];
}

export const KNOWN_TENANTS: Record<string, string> = {
  "11111111-1111-1111-1111-111111111111": "Tenant Alpha",
  "22222222-2222-2222-2222-222222222222": "Tenant Beta",
};

export function getTenantDisplayName(
  tenantId?: string | null,
  tenantName?: string | null
): string {
  if (tenantName && tenantName.trim().length > 0) {
    return tenantName.trim();
  }
  if (!tenantId) return "Unknown Tenant";
  if (KNOWN_TENANTS[tenantId]) {
    return KNOWN_TENANTS[tenantId];
  }
  if (tenantId.length >= 8) {
    return `Tenant: ${tenantId.slice(0, 8)}...`;
  }
  return `Tenant: ${tenantId}`;
}

export const ACCESS_TOKEN_EXPIRING_NOTIFICATION_TIME_SECONDS = 15;
export const RENEW_SKIP_THRESHOLD_SECONDS = 45;

export function getOidcSettings(): UserManagerSettings {
  const redirectUri =
    process.env.NEXT_PUBLIC_REDIRECT_URI ||
    (typeof window !== "undefined"
      ? `${window.location.origin}/auth/callback`
      : "http://localhost:3000/auth/callback");

  const postLogoutRedirectUri =
    process.env.NEXT_PUBLIC_POST_LOGOUT_REDIRECT_URI ||
    (typeof window !== "undefined"
      ? `${window.location.origin}/signed-out`
      : "http://localhost:3000/signed-out");

  return {
    authority: AUTHORITY,
    client_id: CLIENT_ID,
    redirect_uri: redirectUri,
    post_logout_redirect_uri: postLogoutRedirectUri,
    response_type: "code",
    scope: "openid profile email",
    automaticSilentRenew: true,
    accessTokenExpiringNotificationTimeInSeconds: ACCESS_TOKEN_EXPIRING_NOTIFICATION_TIME_SECONDS,
    // Explicit endpoints so redirection works even before Keycloak network discovery
    metadata: {
      issuer: AUTHORITY,
      authorization_endpoint: `${AUTHORITY}/protocol/openid-connect/auth`,
      token_endpoint: `${AUTHORITY}/protocol/openid-connect/token`,
      userinfo_endpoint: `${AUTHORITY}/protocol/openid-connect/userinfo`,
      end_session_endpoint: `${AUTHORITY}/protocol/openid-connect/logout`,
      jwks_uri: `${AUTHORITY}/protocol/openid-connect/certs`,
    },
    userStore:
      typeof window !== "undefined"
        ? new WebStorageStateStore({ store: window.localStorage })
        : undefined,
    stateStore:
      typeof window !== "undefined"
        ? new WebStorageStateStore({ store: window.localStorage })
        : undefined,
  };
}

export function isTemporaryNetworkError(error: unknown): boolean {
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return true;
  }
  if (!error) return false;

  const errorObj = error as Record<string, unknown>;
  const errCode = String(errorObj?.error || "").toLowerCase();

  // Terminal OAuth errors
  if (
    errCode === "invalid_grant" ||
    errCode === "invalid_client" ||
    errCode === "invalid_request" ||
    errCode === "unauthorized_client" ||
    errCode === "unsupported_grant_type" ||
    errCode === "login_required" ||
    errCode === "interaction_required"
  ) {
    return false;
  }

  // Transient OAuth server errors
  if (
    errCode === "service_unavailable" ||
    errCode === "temporarily_unavailable" ||
    errCode === "server_error"
  ) {
    return true;
  }

  const msg =
    error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();

  // Explicit non-network / terminal auth errors (should not retry)
  if (
    msg.includes("invalid_grant") ||
    msg.includes("invalid_client") ||
    msg.includes("invalid_request") ||
    msg.includes("unauthorized_client") ||
    msg.includes("unsupported_grant_type") ||
    msg.includes("login_required") ||
    msg.includes("interaction_required") ||
    msg.includes("revoked") ||
    msg.includes("400") ||
    msg.includes("401") ||
    msg.includes("403")
  ) {
    return false;
  }

  // Network or transient server error indicators
  if (
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("network error") ||
    msg.includes("timeout") ||
    msg.includes("aborted") ||
    msg.includes("net::err") ||
    msg.includes("econnreset") ||
    msg.includes("econnrefused") ||
    msg.includes("500") ||
    msg.includes("502") ||
    msg.includes("503") ||
    msg.includes("504") ||
    msg.includes("service unavailable") ||
    msg.includes("service_unavailable") ||
    msg.includes("temporarily_unavailable") ||
    msg.includes("server_error") ||
    msg.includes("bad gateway") ||
    msg.includes("gateway timeout")
  ) {
    return true;
  }

  return false;
}

export async function purgeAllAuthTokens(): Promise<void> {
  if (typeof window === "undefined") return;

  try {
    const manager = getUserManager();
    await manager.removeUser();
    await manager.clearStaleState();
  } catch (err) {
    console.warn("Error clearing UserManager store:", err);
  }

  const keysToRemove: string[] = [];
  for (let i = 0; i < window.localStorage.length; i++) {
    const key = window.localStorage.key(i);
    if (key && (key.startsWith("oidc.") || key.startsWith("manta."))) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach((key) => window.localStorage.removeItem(key));
}

let userManagerInstance: UserManager | null = null;
let silentRenewPromise: ReturnType<UserManager["signinSilent"]> | null = null;

let isLoggingOutInProgress = false;

export function setIsLoggingOut(inProgress: boolean): void {
  isLoggingOutInProgress = inProgress;
}

export function getIsLoggingOut(): boolean {
  return isLoggingOutInProgress;
}

export function getUserManager(): UserManager {
  if (typeof window === "undefined") {
    return new UserManager(getOidcSettings());
  }

  if (!userManagerInstance) {
    userManagerInstance = new UserManager(getOidcSettings());

    const originalSigninSilent =
      userManagerInstance.signinSilent.bind(userManagerInstance);

    userManagerInstance.signinSilent = (...args) => {
      if (!silentRenewPromise) {
        silentRenewPromise = withRenewLock(async () => {
          // Read the latest user from shared localStorage
          const currentUser = await userManagerInstance!.getUser();

          // If another tab already renewed the token (i.e. it is no longer
          // inside the expiring window), reuse it instead of sending another
          // request. Checking only `expired` would skip the scheduled renewal,
          // which fires before expiry.
          if (
            currentUser &&
            (currentUser.expires_in ?? 0) > RENEW_SKIP_THRESHOLD_SECONDS
          ) {
            return currentUser;
          }

          broadcastAuthMessage({
            type: "RENEW_STARTED",
            tabId: getTabId(),
          });

          const executeWithRetry = async (
            retriesLeft = 2,
            delayMs = 1000,
          ): Promise<User | null> => {
            try {
              return await originalSigninSilent(...args);
            } catch (err) {
              if (retriesLeft > 0 && isTemporaryNetworkError(err)) {
                console.warn(
                  `Silent renew temporary network failure. Retrying in ${delayMs}ms (${retriesLeft} retries left)...`,
                  err,
                );
                await new Promise((resolve) => setTimeout(resolve, delayMs));
                return executeWithRetry(retriesLeft - 1, delayMs * 2);
              }
              throw err;
            }
          };

          try {
            const result = await executeWithRetry();

            broadcastAuthMessage({
              type: "RENEW_COMPLETED",
              tabId: getTabId(),
            });

            return result;
          } catch (error) {
            if (typeof window !== "undefined") {
              try {
                window.sessionStorage.setItem("manta_session_expired", "true");
              } catch {
                // ignore
              }
            }

            broadcastAuthMessage({
              type: "RENEW_FAILED",
              tabId: getTabId(),
            });

            broadcastAuthMessage({
              type: "SESSION_EXPIRED",
              tabId: getTabId(),
            });

            await purgeAllAuthTokens();

            try {
              if (userManagerInstance) {
                await (userManagerInstance.events as any)?._raiseSilentRenewError(error);
              }
            } catch {
              // ignore
            }

            throw error;
          }
        }).finally(() => {
          silentRenewPromise = null;
        });
      }

      return silentRenewPromise;
    };
  }

  if (typeof window !== "undefined") {
    (window as unknown as { __manta_auth?: { getUserManager: typeof getUserManager } }).__manta_auth = {
      getUserManager,
    };
  }

  return userManagerInstance;
}

export function generateNonce(): string {
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

/**
 * Initiates standard Authorization Code Flow with PKCE (RFC 7636).
 * Generates fresh code_verifier, code_challenge (S256), state and nonce,
 * persists them in sessionStorage for callback validation,
 * and redirects the browser to the Keycloak authorization endpoint.
 * No client_secret is used.
 */
export async function initiateLogin(args?: SigninRedirectArgs): Promise<void> {
  const manager = getUserManager();
  const nonce = args?.nonce || generateNonce();

  let state = args?.state;
  if (!state && typeof window !== "undefined") {
    try {
      const returnTo = window.sessionStorage.getItem("manta_auth_return_to");
      if (returnTo) {
        state = { returnTo };
      }
    } catch {
      // ignore
    }
  }

  await manager.signinRedirect({
    nonce,
    state,
    ...args,
  });
}

/**
 * Completes standard Authorization Code Flow with PKCE callback.
 * Validates state, exchanges code with verifier,
 * validates nonce and ID token claims, and persists the User in localStorage.
 */
export async function handleSigninCallback(url?: string): Promise<User> {
  const manager = getUserManager();
  const user = await manager.signinRedirectCallback(url);

  // If user state contained returnTo, ensure it's preserved for the destination redirect
  if (
    typeof window !== "undefined" &&
    user?.state &&
    typeof user.state === "object" &&
    "returnTo" in (user.state as Record<string, unknown>)
  ) {
    const returnTo = (user.state as Record<string, unknown>).returnTo;
    if (typeof returnTo === "string" && returnTo.startsWith("/") && !returnTo.startsWith("//")) {
      try {
        window.sessionStorage.setItem("manta_auth_return_to", returnTo);
      } catch {
        // ignore
      }
    }
  }

  broadcastAuthMessage({
    type: "LOGIN_COMPLETED",
    tabId: getTabId(),
  });

  return user;
}

/**
 * Retrieves the currently active authenticated User from localStorage.
 */
export async function getCurrentUser(): Promise<User | null> {
  if (typeof window === "undefined") return null;
  const manager = getUserManager();
  return await manager.getUser();
}

function decodeJwtPayload(token?: string): Record<string, unknown> {
  if (!token) return {};
  try {
    const parts = token.split(".");
    if (parts.length < 2) return {};
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const json =
      typeof window !== "undefined"
        ? decodeURIComponent(
            Array.from(window.atob(padded))
              .map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, "0")}`)
              .join("")
          )
        : Buffer.from(padded, "base64").toString("utf-8");
    const parsed = JSON.parse(json);
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/**
 * Authenticates directly with email/password against Keycloak OIDC token endpoint
 * using Direct Access Grants (grant_type=password), persists the resulting User
 * in oidc-client-ts UserManager store, and broadcasts LOGIN_COMPLETED across tabs.
 */
export async function loginWithCredentials(
  email: string,
  password: string
): Promise<User> {
  const normalizedEmail = email.trim().toLowerCase();
  const body = new URLSearchParams({
    grant_type: "password",
    client_id: CLIENT_ID,
    username: normalizedEmail,
    password,
    scope: "openid profile email",
  });

  const response = await fetch(`${AUTHORITY}/protocol/openid-connect/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });

  const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok || typeof data.access_token !== "string") {
    const errorCode = typeof data.error === "string" ? data.error : "";
    const description =
      typeof data.error_description === "string"
        ? data.error_description
        : "Invalid email or password.";
    if (errorCode === "invalid_grant") {
      throw new Error("Invalid email or password.");
    }
    throw new Error(description);
  }

  const expiresIn = Number(data.expires_in ?? 300);
  const accessClaims = decodeJwtPayload(data.access_token);
  const idClaims = decodeJwtPayload(
    typeof data.id_token === "string" ? data.id_token : undefined
  );
  const profile = {
    ...accessClaims,
    ...idClaims,
  };

  const user = new User({
    access_token: data.access_token,
    token_type: typeof data.token_type === "string" ? data.token_type : "Bearer",
    id_token: typeof data.id_token === "string" ? data.id_token : undefined,
    refresh_token:
      typeof data.refresh_token === "string" ? data.refresh_token : undefined,
    scope:
      typeof data.scope === "string" ? data.scope : "openid profile email",
    expires_at: Math.floor(Date.now() / 1000) + (Number.isFinite(expiresIn) ? expiresIn : 300),
    profile: profile as never,
  });

  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem("user_signed_out");
  }

  const manager = getUserManager();
  await manager.storeUser(user);
  manager.events.load(user);

  broadcastAuthMessage({
    type: "LOGIN_COMPLETED",
    tabId: getTabId(),
  });

  return user;
}

/**
 * Initiates standard OIDC RP-Initiated Logout (RFC 7636 / OpenID Connect RP-Initiated Logout 1.0).
 * Purges tokens and state from localStorage, removes user from UserManager store,
 * and redirects the browser to Keycloak end_session_endpoint with id_token_hint and post_logout_redirect_uri.
 */
export async function logoutUser(
  postLogoutRedirectUri?: string,
  tenantName?: string | null
): Promise<void> {
  setIsLoggingOut(true);
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem("user_signed_out", "true");
  }

  const manager = getUserManager();
  const user = await manager.getUser();

  broadcastAuthMessage({
    type: "LOGOUT",
    tabId: getTabId(),
  });

  const tenantId = (user?.profile?.tenant_id as string) || undefined;
  const resolvedWorkspaceName =
    tenantName?.trim() ||
    (user?.profile?.tenant_name as string | undefined)?.trim() ||
    (tenantId ? KNOWN_TENANTS[tenantId] : undefined);
  const workspaceQuery = resolvedWorkspaceName
    ? `?workspace=${encodeURIComponent(resolvedWorkspaceName)}`
    : "";

  const defaultRedirectUri =
    typeof window !== "undefined"
      ? `${window.location.origin}/signed-out${workspaceQuery}`
      : `http://localhost:3000/signed-out${workspaceQuery}`;

  const targetRedirectUri = postLogoutRedirectUri || defaultRedirectUri;

  // 1. Purge tokens and state from localStorage
  await manager.removeUser();

  if (typeof window !== "undefined") {
    const keysToRemove: string[] = [];

    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i);

      if (key && (key.startsWith("oidc.") || key.startsWith("manta."))) {
        keysToRemove.push(key);
      }
    }

    keysToRemove.forEach((key) => window.localStorage.removeItem(key));
    window.sessionStorage.setItem("user_signed_out", "true");
  }

  // 2. Call Keycloak end_session_endpoint for IdP session termination
  try {
    if (user?.id_token) {
      await manager.signoutRedirect({
        id_token_hint: user.id_token,
        post_logout_redirect_uri: targetRedirectUri,
      });
    } else {
      await manager.signoutRedirect({
        post_logout_redirect_uri: targetRedirectUri,
      });
    }
  } catch (err) {
    console.warn("Keycloak signoutRedirect failed or offline, falling back to local redirect:", err);
    if (typeof window !== "undefined") {
      window.location.href = targetRedirectUri;
    }
  }
}

export interface WorkspaceItem {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  role: string;
  is_current: boolean;
}

/**
 * Lists all active tenant workspaces the authenticated user belongs to.
 */
export async function listWorkspaces(): Promise<WorkspaceItem[]> {
  const manager = getUserManager();
  let user = await manager.getUser();
  if (!user?.access_token) return [];
  if (user.expired) {
    try {
      user = await manager.signinSilent();
    } catch {
      return [];
    }
  }
  if (!user?.access_token) return [];

  const response = await fetch(`${BACKEND_API_URL}/api/v1/auth/workspaces`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${user.access_token}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to load workspaces: ${response.status}`);
  }

  return (await response.json()) as WorkspaceItem[];
}

/**
 * Switches the caller's active Keycloak tenant_id attribute and realm role via
 * POST /api/v1/auth/switch-workspace, then mints a fresh token carrying the new tenant_id.
 */
export async function switchWorkspace(tenantId: string): Promise<WorkspaceItem> {
  const manager = getUserManager();
  const user = await manager.getUser();
  if (!user?.access_token) {
    throw new Error("You must be signed in to switch workspaces.");
  }

  const response = await fetch(
    `${BACKEND_API_URL}/api/v1/auth/switch-workspace`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${user.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tenant_id: tenantId }),
    },
  );

  const payload = (await response.json().catch(() => ({}))) as Record<
    string,
    unknown
  >;
  if (!response.ok) {
    throw new Error(
      typeof payload.detail === "string"
        ? payload.detail
        : `Failed to switch workspace (${response.status})`,
    );
  }

  // Refresh OIDC tokens so the client carries the updated tenant_id and role claims.
  if (user.refresh_token) {
    const tokenBody = new URLSearchParams({
      grant_type: "refresh_token",
      client_id: CLIENT_ID,
      refresh_token: user.refresh_token,
    });
    const tokenResp = await fetch(`${AUTHORITY}/protocol/openid-connect/token`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: tokenBody.toString(),
    });
    const tokenData = (await tokenResp.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    if (tokenResp.ok && typeof tokenData.access_token === "string") {
      const expiresIn = Number(tokenData.expires_in ?? 300);
      const accessClaims = decodeJwtPayload(tokenData.access_token);
      const idClaims = decodeJwtPayload(
        typeof tokenData.id_token === "string" ? tokenData.id_token : undefined,
      );
      const refreshedUser = new User({
        access_token: tokenData.access_token,
        token_type:
          typeof tokenData.token_type === "string"
            ? tokenData.token_type
            : "Bearer",
        id_token:
          typeof tokenData.id_token === "string"
            ? tokenData.id_token
            : user.id_token,
        refresh_token:
          typeof tokenData.refresh_token === "string"
            ? tokenData.refresh_token
            : user.refresh_token,
        scope:
          typeof tokenData.scope === "string"
            ? tokenData.scope
            : user.scope || "openid profile email",
        expires_at:
          Math.floor(Date.now() / 1000) +
          (Number.isFinite(expiresIn) ? expiresIn : 300),
        profile: { ...accessClaims, ...idClaims } as never,
      });
      await manager.storeUser(refreshedUser);
      manager.events.load(refreshedUser);
      broadcastAuthMessage({
        type: "LOGIN_COMPLETED",
        tabId: getTabId(),
      });
      return payload as unknown as WorkspaceItem;
    }
  }

  try {
    await manager.signinSilent();
  } catch {
    await initiateLogin();
  }

  return payload as unknown as WorkspaceItem;
}

/**
 * Ingress query to backend /api/v1/auth/me to verify RS256 token against FastAPI JWKS
 * and obtain server-validated tenant and user claims.
 */
export async function fetchCurrentUserFromBackend(
  accessToken: string
): Promise<BackendUserClaims> {
  const response = await fetch(`${BACKEND_API_URL}/api/v1/auth/me`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch user from backend: ${response.statusText}`);
  }

  return await response.json();
}


