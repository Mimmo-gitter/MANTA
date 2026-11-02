"use client";

import Link from "next/link";
import { ShieldCheck, LogIn, LogOut, User, Building } from "lucide-react";
import { useAuth } from "@/providers/auth-provider";

export function DashboardHeader() {
  const { isAuthenticated, displayName, tenantName, logout, isLoading } = useAuth();

  return (
    <header className="border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white font-bold">
            M
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900">MANTA</h1>
            <p className="text-xs text-slate-500">AI-Augmented Engineering Intelligence Platform</p>
          </div>
        </div>

        <div className="flex items-center space-x-3">
          <span className="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
            <ShieldCheck className="mr-1 h-3.5 w-3.5" />
            RLS Isolated
          </span>

          {isLoading ? (
            <div className="h-7 w-20 animate-pulse rounded bg-slate-100" />
          ) : isAuthenticated ? (
            <div className="flex items-center space-x-3">
              <span
                data-testid="user-tenant-badge"
                className="inline-flex items-center rounded-md bg-purple-50 px-2.5 py-1 text-xs font-semibold text-purple-700 ring-1 ring-inset ring-purple-700/20"
              >
                <Building className="mr-1.5 h-3.5 w-3.5 text-purple-600" />
                {tenantName}
              </span>
              <span
                data-testid="user-profile-name"
                className="inline-flex items-center text-xs font-medium text-slate-700"
              >
                <User className="mr-1 h-3.5 w-3.5 text-slate-500" />
                {displayName}
              </span>
              <button
                type="button"
                data-testid="header-signout-button"
                onClick={() => logout()}
                className="inline-flex items-center justify-center rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 transition-colors"
              >
                <LogOut className="mr-1.5 h-3.5 w-3.5 text-slate-500" />
                Sign Out
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              data-testid="header-signin-button"
              className="inline-flex items-center justify-center rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 transition-colors"
            >
              <LogIn className="mr-1.5 h-3.5 w-3.5" />
              Sign In
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
