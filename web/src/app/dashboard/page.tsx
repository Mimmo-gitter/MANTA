"use client";

import { Activity, GitPullRequest, Layers, AlertTriangle, ShieldCheck } from "lucide-react";
import { DashboardHeader } from "@/components/dashboard/dashboard-header";
import { ProtectedRoute } from "@/components/auth/protected-route";
import { useAuth } from "@/providers/auth-provider";

export default function ProtectedDashboardPage() {
  const { displayName, tenantName, tenantId } = useAuth();

  return (
    <ProtectedRoute>
      <main
        data-testid="protected-dashboard-content"
        className="flex min-h-screen flex-col bg-slate-50 text-slate-900"
      >
        <DashboardHeader />

        <section className="flex-1 p-8 max-w-7xl mx-auto w-full space-y-6">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-emerald-600" />
                <h2 className="text-lg font-bold text-slate-900">
                  Authenticated Workspace Telemetry
                </h2>
              </div>
              <p className="mt-1 text-sm text-slate-600">
                Signed in as <span className="font-semibold text-slate-900">{displayName}</span> in{" "}
                <span className="font-semibold text-purple-700">{tenantName}</span>
                {tenantId ? ` (${tenantId})` : ""}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-slate-500">Lead Time for Changes</p>
                <Activity className="h-4 w-4 text-blue-500" />
              </div>
              <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">18.5h</p>
              <p className="mt-1 text-xs text-emerald-600">First commit to verified production CD</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-slate-500">Deployment Frequency</p>
                <Layers className="h-4 w-4 text-indigo-500" />
              </div>
              <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">2.4 / day</p>
              <p className="mt-1 text-xs text-slate-500">Rolling 30-day window</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-slate-500">Change Failure Rate</p>
                <AlertTriangle className="h-4 w-4 text-amber-500" />
              </div>
              <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">4.2%</p>
              <p className="mt-1 text-xs text-slate-500">Failed deploys &amp; incident links</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-slate-500">Pending Review (&gt;48h)</p>
                <GitPullRequest className="h-4 w-4 text-rose-500" />
              </div>
              <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">3 PRs</p>
              <p className="mt-1 text-xs text-rose-600">Stale review blockers detected</p>
            </div>
          </div>
        </section>
      </main>
    </ProtectedRoute>
  );
}
