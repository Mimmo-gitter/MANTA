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

export function getTenantDisplayName(tenantId?: string | null): string {
  if (!tenantId) return "Unknown Tenant";
  if (KNOWN_TENANTS[tenantId]) {
    return KNOWN_TENANTS[tenantId];
  }
  if (tenantId.length >= 8) {
    return `Tenant: ${tenantId.slice(0, 8)}...`;
  }
  return `Tenant: ${tenantId}`;
}

export const DEMO_BACKEND_USER: BackendUserClaims = {
  sub: "demo-user-id",
  name: "Pavlo Zhuk",
  preferred_username: "pzhuk",
  email: "director@manta.ai",
  tenant_id: "",
  roles: ["TENANT_ADMIN"],
};

export function getDemoBackendUser(): BackendUserClaims | null {
  if (typeof window === "undefined") return null;
  const isDemo =
    window.location.search.includes("state=") ||
    window.location.search.includes("demo=true") ||
    window.sessionStorage.getItem("manta_demo") === "true";

  if (!isDemo || window.sessionStorage.getItem("user_signed_out") === "true") {
    return null;
  }

  let roles = ["TENANT_ADMIN"];
  try {
    const customRoles = window.sessionStorage.getItem("manta_demo_roles");
    if (customRoles) {
      roles = JSON.parse(customRoles);
    }
  } catch {
    // fallback to default
  }

  return {
    ...DEMO_BACKEND_USER,
    roles,
  };
}

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
// Must be greater than oidc-client-ts accessTokenExpiringNotificationTimeInSeconds (default 60).
const RENEW_SKIP_THRESHOLD_SECONDS = 120;
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
 * Validates state against sessionStorage, exchanges code with verifier,
 * validates nonce and ID token claims, and stores User in sessionStorage.
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
 * Retrieves the currently active authenticated User from sessionStorage.
 */
export async function getCurrentUser(): Promise<User | null> {
  if (typeof window === "undefined") return null;
  const manager = getUserManager();
  return await manager.getUser();
}

/**
 * Initiates standard OIDC RP-Initiated Logout (RFC 7636 / OpenID Connect RP-Initiated Logout 1.0).
 * Purges tokens and state from sessionStorage, removes user from UserManager store,
 * and redirects the browser to Keycloak end_session_endpoint with id_token_hint and post_logout_redirect_uri.
 */
export async function logoutUser(
  postLogoutRedirectUri?: string,
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
  const workspaceQuery =
    tenantId && KNOWN_TENANTS[tenantId]
      ? `?workspace=${encodeURIComponent(KNOWN_TENANTS[tenantId])}`
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


