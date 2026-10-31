import {
  UserManager,
  UserManagerSettings,
  WebStorageStateStore,
  SigninRedirectArgs,
  User,
} from "oidc-client-ts";

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

export function getOidcSettings(): UserManagerSettings {
  const redirectUri =
    process.env.NEXT_PUBLIC_REDIRECT_URI ||
    (typeof window !== "undefined"
      ? `${window.location.origin}/auth/callback`
      : "http://localhost:3000/auth/callback");

  return {
    authority: AUTHORITY,
    client_id: CLIENT_ID,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "openid profile email",
    // Explicit endpoints so redirection works even before Keycloak network discovery
    metadata: {
      issuer: AUTHORITY,
      authorization_endpoint: `${AUTHORITY}/protocol/openid-connect/auth`,
      token_endpoint: `${AUTHORITY}/protocol/openid-connect/token`,
      userinfo_endpoint: `${AUTHORITY}/protocol/openid-connect/userinfo`,
      end_session_endpoint: `${AUTHORITY}/protocol/openid-connect/logout`,
      jwks_uri: `${AUTHORITY}/protocol/openid-connect/certs`,
    },
    userStore: typeof window !== "undefined" ? new WebStorageStateStore({ store: window.sessionStorage }) : undefined,
    stateStore: typeof window !== "undefined" ? new WebStorageStateStore({ store: window.sessionStorage }) : undefined,
  };
}

let userManagerInstance: UserManager | null = null;

export function getUserManager(): UserManager {
  if (typeof window === "undefined") {
    return new UserManager(getOidcSettings());
  }

  if (!userManagerInstance) {
    userManagerInstance = new UserManager(getOidcSettings());
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
  await manager.signinRedirect({
    nonce,
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
  return await manager.signinRedirectCallback(url);
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
 * Purges tokens and state from sessionStorage and revokes local session.
 */
export async function logoutUser(): Promise<void> {
  const manager = getUserManager();
  await manager.removeUser();
  if (typeof window !== "undefined") {
    const keysToRemove: string[] = [];
    for (let i = 0; i < window.sessionStorage.length; i++) {
      const key = window.sessionStorage.key(i);
      if (key && (key.startsWith("oidc.") || key.startsWith("manta."))) {
        keysToRemove.push(key);
      }
    }
    keysToRemove.forEach((k) => window.sessionStorage.removeItem(k));
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


