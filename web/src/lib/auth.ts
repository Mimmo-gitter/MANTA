import {
  UserManager,
  UserManagerSettings,
  WebStorageStateStore,
  SigninRedirectArgs,
} from "oidc-client-ts";

const KEYCLOAK_URL = process.env.NEXT_PUBLIC_KEYCLOAK_URL || "http://localhost:8080";
const REALM = process.env.NEXT_PUBLIC_KEYCLOAK_REALM || "manta";
const CLIENT_ID = process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID || "manta-portal";
const AUTHORITY = `${KEYCLOAK_URL}/realms/${REALM}`;

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

