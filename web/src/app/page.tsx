import Link from "next/link";
import { Activity, ShieldCheck, GitPullRequest, Layers, AlertTriangle, LogIn } from "lucide-react";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
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
            <Link
              href="/login"
              data-testid="header-signin-button"
              className="inline-flex items-center justify-center rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 transition-colors"
            >
              <LogIn className="mr-1.5 h-3.5 w-3.5" />
              Sign In
            </Link>
          </div>
        </div>
      </header>

      <section className="flex-1 p-8 max-w-7xl mx-auto w-full space-y-6">
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
            <p className="mt-1 text-xs text-slate-500">Failed deploys & incident links</p>
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

        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-base font-semibold text-slate-900">Platform Portal Shell (Sprint 1)</h2>
          <p className="mt-1 text-sm text-slate-500">
            Next.js 15 App Router + React 19 + Tailwind CSS + shadcn/ui + TanStack Query v5 + Recharts + Tremor + React Flow + Playwright.
          </p>
        </div>
      </section>
    </main>
  );
}
