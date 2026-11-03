"use client";

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { User } from "oidc-client-ts";
import {
  getUserManager,
  getCurrentUser,
  logoutUser,
  initiateLogin,
  getTenantDisplayName,
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

  const syncUser = useCallback((activeUser: User | null) => {
    if (activeUser && typeof window !== "undefined") {
      window.sessionStorage.removeItem("user_signed_out");
    }
    setUser(activeUser);
    setIsLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    const u = await getCurrentUser();
    syncUser(u);
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
      setUser(null);
      setBackendUser(null);
      if (!getIsLoggingOut()) {
        setIsLoading(false);
      }
    };

    const onUserSignedOut = () => {
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
