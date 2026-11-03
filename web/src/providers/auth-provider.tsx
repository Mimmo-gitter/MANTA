"use client";

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import { User } from "oidc-client-ts";
import {
  getUserManager,
  getCurrentUser,
  logoutUser,
  initiateLogin,
  getTenantDisplayName,
  fetchCurrentUserFromBackend,
  setIsLoggingOut,
  getIsLoggingOut,
  type BackendUserClaims,
} from "@/lib/auth";
import { initAuthSync } from "@/lib/auth-tab-sync";

interface AuthContextValue {
  user: User | null;
  backendUser: BackendUserClaims | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  tenantId: string | null;
  tenantName: string;
  displayName: string;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [backendUser, setBackendUser] = useState<BackendUserClaims | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const syncRequestId = useRef(0);

  const syncUser = useCallback(async (activeUser: User | null) => {
    const requestId = ++syncRequestId.current;

    if (activeUser && typeof window !== "undefined") {
      window.sessionStorage.removeItem("user_signed_out");
    }

    setUser(activeUser);

    if (!activeUser || activeUser.expired || !activeUser.access_token) {
      setBackendUser(null);
      setIsLoading(false);
      return;
    }

    try {
      const currentBackendUser = await fetchCurrentUserFromBackend(
        activeUser.access_token,
      );

      if (requestId !== syncRequestId.current) return;

      setBackendUser(currentBackendUser);
    } catch (error) {
      if (requestId !== syncRequestId.current) return;

      console.error("Failed to fetch backend user:", error);
      setBackendUser(null);
    } finally {
      if (requestId === syncRequestId.current) {
        setIsLoading(false);
      }
    }
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);

    try {
      const currentUser = await getCurrentUser();
      await syncUser(currentUser);
    } catch (error) {
      console.error("Failed to refresh user:", error);
      setUser(null);
      setBackendUser(null);
      setIsLoading(false);
    }
  }, [syncUser]);

  useEffect(() => {
    const manager = getUserManager();

    const stopSync = initAuthSync((message) => {
      if (
        message.type === "RENEW_COMPLETED" ||
        message.type === "LOGIN_COMPLETED"
      ) {
        getCurrentUser().then((updatedUser) => {
          syncUser(updatedUser);
        });
      }

      if (message.type === "RENEW_FAILED") {
        console.error("Token renewal failed in another tab.");
      }

      if (message.type === "LOGOUT") {
        if (typeof window !== "undefined") {
          window.sessionStorage.setItem("user_signed_out", "true");
        }
        setUser(null);
        setBackendUser(null);
        setIsLoading(false);
      }
    });

    // Initial hydration from localStorage
    getCurrentUser().then((initialUser) => {
      syncUser(initialUser);
    });

    const onUserLoaded = (loadedUser: User) => {
      syncUser(loadedUser);
    };

    const onUserUnloaded = () => {
      syncRequestId.current += 1;
      setUser(null);
      setBackendUser(null);
      if (!getIsLoggingOut()) {
        setIsLoading(false);
      }
    };

    const onUserSignedOut = () => {
      syncRequestId.current += 1;
      setUser(null);
      setBackendUser(null);
      if (!getIsLoggingOut()) {
        setIsLoading(false);
      }
    };

    manager.events.addUserLoaded(onUserLoaded);
    manager.events.addUserUnloaded(onUserUnloaded);
    manager.events.addUserSignedOut(onUserSignedOut);

    return () => {
      manager.events.removeUserLoaded(onUserLoaded);
      manager.events.removeUserUnloaded(onUserUnloaded);
      manager.events.removeUserSignedOut(onUserSignedOut);

      stopSync();
    };
  }, [syncUser]);

  const login = useCallback(async () => {
    await initiateLogin();
  }, []);

  const logout = useCallback(async () => {
    syncRequestId.current += 1;
    setIsLoggingOut(true);
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem("user_signed_out", "true");
    }
    setIsLoading(true);
    setUser(null);
    setBackendUser(null);
    try {
      await logoutUser();
    } finally {
      setIsLoading(false);
      setIsLoggingOut(false);
    }
  }, []);

  // Compute authoritative display name
  const displayName =
    backendUser?.name ||
    (user?.profile?.name as string) ||
    (user?.profile?.preferred_username as string) ||
    backendUser?.preferred_username ||
    (user?.profile?.email as string) ||
    "User";

  // Compute authoritative tenant ID and friendly name
  const tenantId =
    backendUser?.tenant_id ||
    (user?.profile?.tenant_id as string) ||
    null;

  const tenantName = getTenantDisplayName(tenantId);

  const value: AuthContextValue = {
    user,
    backendUser,
    isLoading,
    isAuthenticated: !!user && !user.expired,
    tenantId,
    tenantName,
    displayName,
    login,
    logout,
    refresh,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
