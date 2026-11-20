"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  Eye,
  Loader2,
  Lock,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { DeadLetterDrawer } from "@/components/dead-letters/dead-letter-drawer";
import { DeadLetterProviderAvatar } from "@/components/dead-letters/provider-avatar";
import {
  FilterDropdown,
  type FilterDropdownOption,
} from "@/components/ui/filter-dropdown";
import { usePermissions } from "@/hooks/use-permissions";
import {
  useDeadLettersQuery,
  useRedriveDeadLetterMutation,
} from "@/hooks/use-dead-letters";
import { isApiHttpError } from "@/lib/api-client";
import {
  extractDeadLetterMeta,
  formatProviderLabel,
  formatQuarantinedTableTime,
  isSameUtcDay,
  normalizeDeadLetterProvider,
  type NormalizedDeadLetterProvider,
} from "@/lib/dead-letters-api";
import { getPrimaryRoleLabel } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import type { DeadLetterItem } from "@/types/dead-letter";

type ProviderFilterValue = "all" | NormalizedDeadLetterProvider;
type StatusFilterValue = "all" | "unresolved" | "resolved";

const PAGE_SIZE = 5;
const PROVIDER_ORDER: readonly NormalizedDeadLetterProvider[] = [
  "github",
  "jira",
  "slack",
  "argocd",
  "generic",
];

interface QueueToastState {
  id: number;
  kind: "success" | "error";
  itemId: string;
  title: string;
  description: string;
}

function formatLastCheckedLabel(updatedAt: number, nowMs: number | null): string {
  if (!updatedAt || nowMs === null) {
    return "Last checked just now";
  }
  const diffSeconds = Math.max(0, Math.floor((nowMs - updatedAt) / 1000));
  if (diffSeconds < 60) {
    return "Last checked just now";
  }
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `Last checked ${diffMinutes}m ago`;
  }
  const diffHours = Math.floor(diffMinutes / 60);
  return `Last checked ${diffHours}h ago`;
}

function summarizeErrorDetail(error: unknown, fallbackErrorClass: string): {
  inlineMessage: string;
  attemptFailureDetail: string;
} {
  if (isApiHttpError(error)) {
    const clean = (error.detail ?? "").trim();
    if (clean.length > 0) {
      return {
        inlineMessage: clean,
        attemptFailureDetail: clean,
      };
    }
  }
  if (error instanceof Error && error.message.trim().length > 0) {
    const clean = error.message.trim();
    return {
      inlineMessage: clean,
      attemptFailureDetail: clean,
    };
  }
  return {
    inlineMessage: "downstream queue rejected re-drive",
    attemptFailureDetail: `${fallbackErrorClass} persisted`,
  };
}

function DeadLettersHeader({
  action,
}: {
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-1.5 font-mono text-[11px] leading-[16px] text-[#7c839b]">
          <li>Administration</li>
          <li aria-hidden="true" className="text-[#475569]">
            /
          </li>
          <li aria-current="page" className="text-[#00e599]">
            Dead Letters
          </li>
        </ol>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[22px] font-bold tracking-[-0.02em] leading-[28px] text-[#e2e8f0]">
            Dead Letters
          </h1>
          <p className="mt-1 text-[12px] leading-[17px] text-[#7c839b]">
            Inspect quarantined webhook events and re-drive failed deliveries.
          </p>
        </div>
        {action}
      </div>
    </div>
  );
}

function DeadLettersForbiddenState({ roleLabel }: { roleLabel: string }) {
  return (
    <section
      data-testid="dead-letters-forbidden-state"
      aria-labelledby="dlq-forbidden-heading"
      className="flex min-h-[490px] flex-col items-center justify-center rounded-[10px] border border-[#1e2436] bg-[#111521] px-6 py-16 text-center"
    >
      <div className="flex size-[42px] items-center justify-center rounded-[10px] border border-[#7f1d1d]/70 bg-[#2a1216] text-[#ef4444]">
        <Lock className="size-[18px]" aria-hidden="true" />
      </div>

      <span className="mt-4 inline-flex items-center rounded-full border border-[#7f1d1d]/70 bg-[#2a1216] px-3 py-1 font-mono text-[10px] font-semibold tracking-[0.06em] text-[#f87171]">
        HTTP 403 · ACCESS RESTRICTED
      </span>

      <h2
        id="dlq-forbidden-heading"
        className="mt-4 text-[18px] font-semibold leading-[24px] text-[#e2e8f0]"
      >
        Administrator access required
      </h2>

      <p className="mt-2 max-w-[440px] text-[12px] leading-[18px] text-[#7c839b]">
        You don&apos;t have permission to view quarantined webhook payloads or
        trigger dead-letter event re-drives. Request integration permissions
        from your workspace Tenant Administrator.
      </p>

      <div className="mt-5 inline-flex items-center gap-1.5 rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-3.5 py-1.5 font-mono text-[11px]">
        <span className="text-[#7c839b]">Current workspace role:</span>
        <span className="font-semibold text-[#e2e8f0]">{roleLabel}</span>
      </div>

      <div className="mt-6 flex items-center justify-center gap-3">
        <Link
          href="/"
          data-testid="dead-letters-back-to-overview"
          className="inline-flex h-[34px] items-center justify-center rounded-[8px] bg-[#00e599] px-4 text-[12px] font-semibold text-[#0a0e18] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
        >
          Back to Overview
        </Link>
      </div>
    </section>
  );
}

function DeadLettersSummaryStrip({
  isLoading,
  unresolvedCount,
  redrivenTodayCount,
  unresolvedProviderBreakdown,
  activeRedriveShortId,
  lastCheckedText,
}: {
  isLoading: boolean;
  unresolvedCount: number;
  redrivenTodayCount: number;
  unresolvedProviderBreakdown: Array<{
    provider: NormalizedDeadLetterProvider;
    count: number;
  }>;
  activeRedriveShortId: string | null;
  lastCheckedText: string;
}) {
  if (isLoading) {
    return (
      <div
        data-testid="dead-letters-summary-strip"
        className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-[#1e2436] bg-[#111521] px-4 py-2.5"
      >
        <div className="flex items-center gap-3">
          <span className="h-[11px] w-[112px] animate-pulse rounded bg-[#1a2234]" />
          <span className="h-[11px] w-[90px] animate-pulse rounded bg-[#1a2234]" />
          <span className="h-[11px] w-[142px] animate-pulse rounded bg-[#1a2234]" />
        </div>
        <span className="font-mono text-[11px] text-[#64748b]">
          Syncing queue...
        </span>
      </div>
    );
  }

  return (
    <div
      data-testid="dead-letters-summary-strip"
      className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-[#1e2436] bg-[#111521] px-4 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
        <span
          data-testid="dlq-unread-count"
          className={cn(
            "font-semibold",
            unresolvedCount > 0 ? "text-[#f59e0b]" : "text-[#00e599]",
          )}
        >
          {unresolvedCount} unread quarantined
        </span>

        <span aria-hidden="true" className="text-[#475569]">
          ·
        </span>

        <span
          data-testid="dlq-redriven-today"
          className={cn(
            "font-semibold",
            unresolvedCount > 0 ? "text-[#00e599]" : "text-[#cbd5e1]",
          )}
        >
          {redrivenTodayCount} re-driven today
        </span>

        <span aria-hidden="true" className="text-[#475569]">
          ·
        </span>

        {activeRedriveShortId ? (
          <span className="font-medium text-[#22d3ee]">
            Re-driving {activeRedriveShortId}...
          </span>
        ) : unresolvedCount === 0 || unresolvedProviderBreakdown.length === 0 ? (
          <span className="font-sans text-[#94a3b8]">
            All webhook deliveries healthy
          </span>
        ) : (
          <span className="text-[#94a3b8]">
            {unresolvedProviderBreakdown
              .map(({ provider, count }) => `${formatProviderLabel(provider)} (${count})`)
              .join(" · ")}
          </span>
        )}
      </div>

      <span className="font-mono text-[11px] text-[#64748b]">
        {lastCheckedText}
      </span>
    </div>
  );
}

function DeadLettersTableSkeleton() {
  return (
    <div data-testid="dead-letters-loading-skeleton" className="w-full overflow-x-auto">
      <table className="w-full min-w-[780px] border-collapse">
        <thead>
          <tr className="border-b border-[#1e2436] bg-[#101420] text-left font-mono text-[10px] font-semibold tracking-[0.06em] text-[#64748b] uppercase">
            <th className="px-4 py-2.5">Provider</th>
            <th className="px-4 py-2.5">Error Class</th>
            <th className="px-4 py-2.5">Attempts</th>
            <th className="px-4 py-2.5">Quarantined At</th>
            <th className="px-4 py-2.5">Status</th>
            <th className="px-4 py-2.5 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: PAGE_SIZE }).map((_, idx) => (
            <tr key={idx} className="border-b border-[#1b2131]">
              <td className="px-4 py-3.5">
                <div className="flex items-center gap-3">
                  <span className="size-[28px] shrink-0 animate-pulse rounded-[6px] bg-[#1a2234]" />
                  <div className="flex flex-col gap-1.5">
                    <span className="h-[11px] w-[140px] animate-pulse rounded bg-[#1a2234]" />
                    <span className="h-[10px] w-[180px] animate-pulse rounded bg-[#171d2c]" />
                  </div>
                </div>
              </td>
              <td className="px-4 py-3.5">
                <span className="inline-block h-[24px] w-[138px] animate-pulse rounded-[6px] bg-[#1a2234]" />
              </td>
              <td className="px-4 py-3.5">
                <span className="inline-block h-[11px] w-[36px] animate-pulse rounded bg-[#1a2234]" />
              </td>
              <td className="px-4 py-3.5">
                <span className="inline-block h-[11px] w-[108px] animate-pulse rounded bg-[#1a2234]" />
              </td>
              <td className="px-4 py-3.5">
                <span className="inline-block h-[24px] w-[88px] animate-pulse rounded-full bg-[#1a2234]" />
              </td>
              <td className="px-4 py-3.5">
                <div className="flex items-center justify-end gap-2">
                  <span className="size-[28px] animate-pulse rounded-[6px] bg-[#1a2234]" />
                  <span className="h-[28px] w-[82px] animate-pulse rounded-[6px] bg-[#1a2234]" />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex items-center justify-between border-t border-[#1e2436] px-4 py-3">
        <span className="h-[11px] w-[200px] animate-pulse rounded bg-[#1a2234]" />
        <span className="h-[24px] w-[112px] animate-pulse rounded bg-[#1a2234]" />
      </div>
    </div>
  );
}

function DeadLettersEmptyState({
  onRefresh,
  isRefreshing,
}: {
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  return (
    <div
      data-testid="dead-letters-empty-state"
      className="flex flex-col items-center justify-center px-6 py-24 text-center"
    >
      <div className="flex size-[42px] items-center justify-center rounded-[10px] border border-[#00e599]/35 bg-[#06241d] text-[#00e599]">
        <ShieldCheck className="size-[20px]" aria-hidden="true" />
      </div>

      <h2 className="mt-4 text-[15px] font-semibold leading-[20px] text-[#e2e8f0]">
        No dead-letter events
      </h2>

      <p className="mt-1.5 max-w-[400px] text-[12px] leading-[18px] text-[#7c839b]">
        All incoming GitHub, Jira, and Slack webhook deliveries are processing
        normally. Failed events appear here if delivery retries are exhausted.
      </p>

      <button
        type="button"
        onClick={onRefresh}
        disabled={isRefreshing}
        className="mt-5 inline-flex h-[32px] items-center gap-1.5 rounded-[6px] border border-[#252b3b] bg-[#161b26] px-3.5 text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#333c52] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:opacity-60"
      >
        <RefreshCw
          className={cn("size-3.5", isRefreshing && "animate-spin")}
          aria-hidden="true"
        />
        Refresh Queue
      </button>
    </div>
  );
}

function DeadLettersToast({
  toast,
  onInspect,
  onDismiss,
}: {
  toast: QueueToastState | null;
  onInspect: () => void;
  onDismiss: () => void;
}) {
  if (!toast) {
    return null;
  }

  const isError = toast.kind === "error";

  return (
    <div
      role={isError ? "alert" : "status"}
      data-testid="dead-letters-toast"
      data-toast-kind={toast.kind}
      className={cn(
        "fixed right-6 top-[56px] z-40 flex w-[410px] max-w-[calc(100vw-32px)] items-start gap-3 rounded-[10px] border p-3.5 shadow-[0_16px_38px_rgba(0,0,0,0.55)] backdrop-blur-md",
        isError
          ? "border-[#ef4444]/45 bg-[#201116]/95"
          : "border-[#00e599]/40 bg-[#0c1c1a]/95",
      )}
    >
      <div
        className={cn(
          "mt-0.5 flex size-[26px] shrink-0 items-center justify-center rounded-full border",
          isError
            ? "border-[#ef4444]/50 bg-[#ef4444]/15 text-[#f87171]"
            : "border-[#00e599]/50 bg-[#00e599]/15 text-[#00e599]",
        )}
      >
        {isError ? (
          <AlertTriangle className="size-3.5" aria-hidden="true" />
        ) : (
          <Check className="size-3.5" aria-hidden="true" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <p className="text-[12px] font-semibold leading-[17px] text-[#e2e8f0]">
          {toast.title}
        </p>
        <p className="mt-0.5 text-[11px] leading-[16px] text-[#94a3b8]">
          {toast.description}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        {isError && (
          <button
            type="button"
            data-testid="toast-inspect-button"
            onClick={onInspect}
            className="inline-flex h-[24px] items-center rounded-[5px] px-2 text-[11px] font-semibold text-[#fca5a5] transition-colors hover:bg-[#ef4444]/15 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ef4444]"
          >
            Inspect
          </button>
        )}

        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss notification"
          className="inline-flex size-[24px] items-center justify-center rounded-[5px] text-[#7c839b] transition-colors hover:bg-white/[0.06] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

export default function DeadLettersPage() {
  const { backendUser, isAuthenticated, isLoading: isAuthLoading } = useAuth();
  const { can } = usePermissions();
  const canManageWebhooks = can("MANAGE_WEBHOOK_INTEGRATIONS");

  const query = useDeadLettersQuery("all", isAuthenticated && canManageWebhooks);
  const redriveMutation = useRedriveDeadLetterMutation();

  const [searchQuery, setSearchQuery] = useState("");
  const [providerFilter, setProviderFilter] =
    useState<ProviderFilterValue>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilterValue>("all");
  const [currentPage, setCurrentPage] = useState(1);

  const [inspectedItemId, setInspectedItemId] = useState<string | null>(null);
  const [activeRedriveId, setActiveRedriveId] = useState<string | null>(null);
  const [justRedrivenIds, setJustRedrivenIds] = useState<Record<string, boolean>>({});
  const [failedErrorsById, setFailedErrorsById] = useState<Record<string, string>>({});
  const [failedAttemptOffsets, setFailedAttemptOffsets] = useState<
    Record<string, { baseAttempts: number; increment: number }>
  >({});
  const [toast, setToast] = useState<QueueToastState | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNowMs(Date.now());
    }, 30_000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!toast || toast.kind !== "success") {
      return;
    }
    const timer = window.setTimeout(() => {
      setToast((prev) => (prev?.id === toast.id ? null : prev));
    }, 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const items = useMemo(() => query.data?.items ?? [], [query.data?.items]);
  const isForbidden =
    !isAuthLoading &&
    isAuthenticated &&
    (!canManageWebhooks ||
      (query.isError && isApiHttpError(query.error) && query.error.status === 403));

  const isLoading = isAuthLoading || (canManageWebhooks && query.isLoading);
  const isRefreshing = canManageWebhooks && query.isFetching && !query.isLoading;

  const metaById = useMemo(() => {
    const map = new Map<string, ReturnType<typeof extractDeadLetterMeta>>();
    for (const item of items) {
      map.set(item.id, extractDeadLetterMeta(item));
    }
    return map;
  }, [items]);

  const unresolvedCount = useMemo(() => {
    if (query.data && typeof query.data.unresolved_count === "number") {
      return query.data.unresolved_count;
    }
    return items.filter((item) => !item.is_resolved).length;
  }, [items, query.data]);

  const redrivenTodayCount = useMemo(() => {
    return items.filter(
      (item) => item.is_resolved && isSameUtcDay(item.resolved_at),
    ).length;
  }, [items]);

  const unresolvedProviderBreakdown = useMemo(() => {
    const counts: Record<NormalizedDeadLetterProvider, number> = {
      github: 0,
      jira: 0,
      slack: 0,
      argocd: 0,
      generic: 0,
    };

    for (const item of items) {
      if (!item.is_resolved) {
        const provider = normalizeDeadLetterProvider(item.provider);
        counts[provider] += 1;
      }
    }

    return PROVIDER_ORDER.filter((provider) => counts[provider] > 0).map(
      (provider) => ({
        provider,
        count: counts[provider],
      }),
    );
  }, [items]);

  const totalProviderCounts = useMemo(() => {
    const counts: Record<NormalizedDeadLetterProvider, number> = {
      github: 0,
      jira: 0,
      slack: 0,
      argocd: 0,
      generic: 0,
    };
    for (const item of items) {
      counts[normalizeDeadLetterProvider(item.provider)] += 1;
    }
    return counts;
  }, [items]);

  const statusCounts = useMemo(() => {
    let unresolved = 0;
    let resolved = 0;
    for (const item of items) {
      if (item.is_resolved) {
        resolved += 1;
      } else {
        unresolved += 1;
      }
    }
    return { all: items.length, unresolved, resolved };
  }, [items]);

  const providerOptions = useMemo<FilterDropdownOption<ProviderFilterValue>[]>(() => {
    const base: FilterDropdownOption<ProviderFilterValue>[] = [
      {
        value: "all",
        label: "All providers",
        triggerText: "Provider: All",
        count: items.length,
      },
      {
        value: "github",
        label: "GitHub",
        triggerText: "Provider: GitHub",
        count: totalProviderCounts.github,
      },
      {
        value: "jira",
        label: "Jira",
        triggerText: "Provider: Jira",
        count: totalProviderCounts.jira,
      },
      {
        value: "slack",
        label: "Slack",
        triggerText: "Provider: Slack",
        count: totalProviderCounts.slack,
      },
    ];

    if (totalProviderCounts.argocd > 0) {
      base.push({
        value: "argocd",
        label: "ArgoCD",
        triggerText: "Provider: ArgoCD",
        count: totalProviderCounts.argocd,
      });
    }

    if (totalProviderCounts.generic > 0) {
      base.push({
        value: "generic",
        label: "Webhook",
        triggerText: "Provider: Webhook",
        count: totalProviderCounts.generic,
      });
    }

    return base;
  }, [items.length, totalProviderCounts]);

  const statusOptions = useMemo<FilterDropdownOption<StatusFilterValue>[]>(
    () => [
      {
        value: "all",
        label: "All statuses",
        triggerText: "Status: All",
        count: statusCounts.all,
      },
      {
        value: "unresolved",
        label: "Quarantined",
        triggerText: "Status: Quarantined",
        count: statusCounts.unresolved,
      },
      {
        value: "resolved",
        label: "Re-driven",
        triggerText: "Status: Re-driven",
        count: statusCounts.resolved,
      },
    ],
    [statusCounts],
  );

  const filteredItems = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase();

    return items.filter((item) => {
      const provider = normalizeDeadLetterProvider(item.provider);
      if (providerFilter !== "all" && provider !== providerFilter) {
        return false;
      }

      if (statusFilter === "unresolved" && item.is_resolved) {
        return false;
      }
      if (statusFilter === "resolved" && !item.is_resolved) {
        return false;
      }

      if (normalizedSearch.length === 0) {
        return true;
      }

      const meta = metaById.get(item.id);
      const haystack = [
        item.id,
        item.original_event_id,
        item.error_class,
        item.stack_trace,
        item.provider,
        meta?.deliveryShortId,
        meta?.shortErrorClass,
        meta?.eventType,
        meta?.contextTarget,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return haystack.includes(normalizedSearch);
    });
  }, [items, metaById, providerFilter, searchQuery, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / PAGE_SIZE));
  const safePage = Math.min(currentPage, totalPages);
  const paginatedItems = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filteredItems.slice(start, start + PAGE_SIZE);
  }, [filteredItems, safePage]);

  const rangeStart = filteredItems.length === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(filteredItems.length, safePage * PAGE_SIZE);

  const inspectedItem = useMemo(
    () => items.find((item) => item.id === inspectedItemId) ?? null,
    [items, inspectedItemId],
  );

  const activeRedriveMeta = activeRedriveId
    ? metaById.get(activeRedriveId) ?? null
    : null;

  const toastTargetItem = useMemo(
    () => (toast ? items.find((item) => item.id === toast.itemId) ?? null : null),
    [items, toast],
  );

  const handleRefresh = () => {
    if (isLoading || isRefreshing || Boolean(activeRedriveId)) {
      return;
    }
    void query.refetch();
  };

  const handleInspect = (item: DeadLetterItem) => {
    setInspectedItemId(item.id);
  };

  const handleRedrive = async (item: DeadLetterItem) => {
    if (activeRedriveId || item.is_resolved) {
      return;
    }

    const meta = metaById.get(item.id) ?? extractDeadLetterMeta(item);
    setActiveRedriveId(item.id);

    try {
      await redriveMutation.mutateAsync(item.id);
      setJustRedrivenIds((prev) => ({ ...prev, [item.id]: true }));
      setFailedErrorsById((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      setFailedAttemptOffsets((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      setToast({
        id: Date.now(),
        kind: "success",
        itemId: item.id,
        title: "Event re-driven successfully",
        description: `${meta.providerLabel} ${meta.eventType} queued for processing.`,
      });
    } catch (error) {
      const { inlineMessage, attemptFailureDetail } = summarizeErrorDetail(
        error,
        meta.shortErrorClass,
      );
      const currentOffset =
        failedAttemptOffsets[item.id]?.baseAttempts === item.total_attempts
          ? failedAttemptOffsets[item.id].increment
          : 0;
      const nextIncrement = currentOffset + 1;
      const attemptNumber = item.total_attempts + nextIncrement;

      setFailedErrorsById((prev) => ({ ...prev, [item.id]: inlineMessage }));
      setFailedAttemptOffsets((prev) => ({
        ...prev,
        [item.id]: { baseAttempts: item.total_attempts, increment: nextIncrement },
      }));
      setToast({
        id: Date.now(),
        kind: "error",
        itemId: item.id,
        title: "Re-drive failed",
        description: `${meta.shortErrorClass} persisted on attempt ${attemptNumber} (${attemptFailureDetail}).`,
      });
    } finally {
      setActiveRedriveId(null);
    }
  };

  const hasActiveFilters =
    searchQuery.trim().length > 0 ||
    providerFilter !== "all" ||
    statusFilter !== "all";

  if (isForbidden) {
    return (
      <div className="flex min-h-full flex-col gap-4 px-4 py-5 sm:px-6">
        <DeadLettersHeader />
        <DeadLettersForbiddenState
          roleLabel={getPrimaryRoleLabel(backendUser?.roles)}
        />
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col gap-4 px-4 py-5 sm:px-6">
      <DeadLettersToast
        toast={toast}
        onInspect={() => {
          if (toastTargetItem) {
            setInspectedItemId(toastTargetItem.id);
          }
        }}
        onDismiss={() => setToast(null)}
      />

      <DeadLettersHeader
        action={
          <button
            type="button"
            data-testid="refresh-queue-button"
            onClick={handleRefresh}
            disabled={isLoading || isRefreshing || Boolean(activeRedriveId)}
            className={cn(
              "inline-flex h-[32px] items-center justify-center gap-1.5 rounded-[7px] border px-3 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:cursor-not-allowed",
              isLoading || isRefreshing
                ? "border-[#00e599]/40 bg-[#0d1620] text-[#00e599]"
                : "border-[#252b3b] bg-[#161b26] text-[#cbd5e1] hover:border-[#333c52] hover:text-[#e2e8f0] disabled:opacity-50",
            )}
          >
            <RefreshCw
              className={cn(
                "size-3.5",
                (isLoading || isRefreshing) && "animate-spin",
              )}
              aria-hidden="true"
            />
            {isLoading || isRefreshing ? "Refreshing..." : "Refresh Queue"}
          </button>
        }
      />

      <DeadLettersSummaryStrip
        isLoading={isLoading}
        unresolvedCount={unresolvedCount}
        redrivenTodayCount={redrivenTodayCount}
        unresolvedProviderBreakdown={unresolvedProviderBreakdown}
        activeRedriveShortId={activeRedriveMeta?.deliveryShortId ?? null}
        lastCheckedText={formatLastCheckedLabel(query.dataUpdatedAt, nowMs)}
      />

      <section
        aria-label="Dead Letter Queue"
        className="rounded-[10px] border border-[#1e2436] bg-[#111521]"
      >
        {/* Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1e2436] px-4 py-3">
          <div className="flex items-center gap-2.5">
            <h2 className="font-mono text-[11px] font-semibold tracking-[0.06em] text-[#94a3b8] uppercase">
              DEAD LETTER QUEUE
            </h2>
            <span
              data-testid="dlq-queue-badge"
              className={cn(
                "inline-flex h-[20px] min-w-[22px] items-center justify-center rounded-[5px] border px-1.5 font-mono text-[11px] font-semibold",
                isLoading
                  ? "border-[#252b3b] bg-[#161b26] text-[#7c839b]"
                  : unresolvedCount > 0
                    ? "border-[#00e599]/30 bg-[#00e599]/10 text-[#00e599]"
                    : "border-[#252b3b] bg-[#161b26] text-[#94a3b8]",
              )}
            >
              {isLoading ? "-" : unresolvedCount}
            </span>
          </div>

          <search className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            <div className="relative min-w-[200px] flex-1 sm:w-[220px] sm:flex-none">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[#64748b]"
                aria-hidden="true"
              />
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => {
                  setSearchQuery(event.target.value);
                  setCurrentPage(1);
                }}
                disabled={isLoading}
                placeholder="Filter by delivery ID or error..."
                aria-label="Filter dead-letter events by delivery ID or error"
                data-testid="dead-letters-search-input"
                className="h-[32px] w-full rounded-[6px] border border-solid border-[#252b3b] bg-[#0a0e18] pl-8 pr-3 text-[12px] text-[#e2e8f0] placeholder:text-[#64748b] shadow-none focus:border-[#00e599]/60 focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>

            <FilterDropdown<ProviderFilterValue>
              value={providerFilter}
              onChange={(nextValue) => {
                setProviderFilter(nextValue);
                setCurrentPage(1);
              }}
              options={providerOptions}
              headerLabel="FILTER BY PROVIDER"
              triggerLabel={`Provider: ${
                providerFilter === "all"
                  ? "All"
                  : formatProviderLabel(providerFilter)
              }`}
              ariaLabel="Filter by provider"
              testId="filter-provider-dropdown"
              triggerTestId="filter-provider-trigger"
              popoverTestId="filter-provider-popover"
              align="right"
              disabled={isLoading}
              isActive={providerFilter !== "all"}
              triggerClassName="h-[32px] rounded-[6px] text-[12px]"
            />

            <FilterDropdown<StatusFilterValue>
              value={statusFilter}
              onChange={(nextValue) => {
                setStatusFilter(nextValue);
                setCurrentPage(1);
              }}
              options={statusOptions}
              headerLabel="FILTER BY STATUS"
              triggerLabel={`Status: ${
                statusFilter === "all"
                  ? "All"
                  : statusFilter === "unresolved"
                    ? "Quarantined"
                    : "Re-driven"
              }`}
              ariaLabel="Filter by status"
              testId="filter-status-dropdown"
              triggerTestId="filter-status-trigger"
              popoverTestId="filter-status-popover"
              align="right"
              disabled={isLoading}
              isActive={statusFilter !== "all"}
              triggerClassName="h-[32px] rounded-[6px] text-[12px]"
            />
          </search>
        </div>

        {/* Body */}
        {isLoading ? (
          <DeadLettersTableSkeleton />
        ) : query.isError ? (
          <div
            role="alert"
            data-testid="dead-letters-error-state"
            className="flex flex-col items-center justify-center px-6 py-20 text-center"
          >
            <div className="flex size-[40px] items-center justify-center rounded-[10px] border border-[#ef4444]/40 bg-[#2a1216] text-[#f87171]">
              <AlertTriangle className="size-[18px]" aria-hidden="true" />
            </div>
            <h3 className="mt-4 text-[14px] font-semibold text-[#e2e8f0]">
              Unable to load dead-letter queue
            </h3>
            <p className="mt-1 max-w-[420px] text-[12px] text-[#94a3b8]">
              {isApiHttpError(query.error)
                ? query.error.detail
                : query.error instanceof Error
                  ? query.error.message
                  : "Unexpected error while retrieving dead-letter events."}
            </p>
            <button
              type="button"
              onClick={() => void query.refetch()}
              className="mt-4 inline-flex h-[32px] items-center gap-1.5 rounded-[6px] border border-[#252b3b] bg-[#161b26] px-3.5 text-[12px] font-medium text-[#e2e8f0] hover:border-[#333c52]"
            >
              <RefreshCw className="size-3.5" aria-hidden="true" />
              Retry
            </button>
          </div>
        ) : items.length === 0 ? (
          <DeadLettersEmptyState
            onRefresh={handleRefresh}
            isRefreshing={isRefreshing}
          />
        ) : filteredItems.length === 0 ? (
          <div
            data-testid="dead-letters-filtered-empty"
            className="flex flex-col items-center justify-center px-6 py-20 text-center"
          >
            <p className="text-[14px] font-semibold text-[#e2e8f0]">
              No matching dead-letter events
            </p>
            <p className="mt-1 text-[12px] text-[#7c839b]">
              Adjust your search query or filter selections to see queued
              webhook deliveries.
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={() => {
                  setSearchQuery("");
                  setProviderFilter("all");
                  setStatusFilter("all");
                  setCurrentPage(1);
                }}
                className="mt-4 inline-flex h-[32px] items-center rounded-[6px] border border-[#252b3b] bg-[#161b26] px-3.5 text-[12px] font-medium text-[#cbd5e1] hover:border-[#333c52] hover:text-[#e2e8f0]"
              >
                Reset filters
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="w-full overflow-x-auto">
              <table
                data-testid="dead-letters-table"
                className="w-full min-w-[780px] border-collapse"
              >
                <thead>
                  <tr className="border-b border-[#1e2436] bg-[#101420] text-left font-mono text-[10px] font-semibold tracking-[0.06em] text-[#64748b] uppercase">
                    <th className="px-4 py-2.5">Provider</th>
                    <th className="px-4 py-2.5">Error Class</th>
                    <th className="px-4 py-2.5">Attempts</th>
                    <th className="px-4 py-2.5">Quarantined At</th>
                    <th className="px-4 py-2.5">Status</th>
                    <th className="px-4 py-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedItems.map((item) => {
                    const meta =
                      metaById.get(item.id) ?? extractDeadLetterMeta(item);
                    const isRedriving = activeRedriveId === item.id;
                    const inlineFailure = !item.is_resolved
                      ? failedErrorsById[item.id] ?? null
                      : null;
                    const hasFailedRetry = Boolean(inlineFailure);
                    const attemptOffset =
                      !item.is_resolved &&
                      failedAttemptOffsets[item.id]?.baseAttempts ===
                        item.total_attempts
                        ? failedAttemptOffsets[item.id].increment
                        : 0;
                    const displayedAttempts = item.total_attempts + attemptOffset;
                    const wasRedrivenNow =
                      item.is_resolved && Boolean(justRedrivenIds[item.id]);

                    return (
                      <tr
                        key={item.id}
                        data-testid={`dead-letter-row-${item.id}`}
                        onClick={(event) => {
                          if (
                            (event.target as HTMLElement).closest(
                              "button, a, input",
                            )
                          ) {
                            return;
                          }
                          handleInspect(item);
                        }}
                        className={cn(
                          "cursor-pointer transition-colors",
                          isRedriving
                            ? "border-y border-[#0891b2]/50 bg-[#09202a]/45"
                            : hasFailedRetry
                              ? "border-y border-[#7f1d1d]/60 bg-[#241016]/55"
                              : "border-b border-[#1b2131] hover:bg-[#151b29]/70",
                        )}
                      >
                        <td className="px-4 py-3.5 align-middle">
                          <div className="flex items-start gap-3">
                            <DeadLetterProviderAvatar provider={meta.normalizedProvider} />
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 truncate">
                                <span className="text-[13px] font-semibold leading-[18px] text-[#e2e8f0]">
                                  {meta.providerLabel}
                                </span>
                                <span className="font-mono text-[12px] leading-[18px] text-[#94a3b8]">
                                  {meta.eventType}
                                </span>
                              </div>
                              <p className="mt-0.5 max-w-[270px] truncate font-mono text-[11px] leading-[15px] text-[#64748b]">
                                {meta.deliveryShortId}
                                {meta.contextTarget
                                  ? ` · ${meta.contextTarget}`
                                  : ""}
                              </p>
                              {inlineFailure && (
                                <p
                                  data-testid={`row-error-${item.id}`}
                                  className="mt-1 text-[11px] font-medium leading-[15px] text-[#f87171]"
                                >
                                  Last retry failed: {inlineFailure}
                                </p>
                              )}
                            </div>
                          </div>
                        </td>

                        <td className="px-4 py-3.5 align-middle">
                          <span
                            className={cn(
                              "inline-block whitespace-nowrap rounded-[6px] border px-2.5 py-1 font-mono text-[11px] font-medium leading-[15px]",
                              item.is_resolved
                                ? "border-[#252b3b] bg-[#151a27] text-[#64748b]"
                                : meta.errorTone === "warning"
                                  ? "border-[#92400e]/60 bg-[#2a1f0b]/60 text-[#f59e0b]"
                                  : "border-[#7f1d1d]/60 bg-[#2a1216]/60 text-[#f87171]",
                            )}
                          >
                            {meta.shortErrorClass}
                          </span>
                        </td>

                        <td className="px-4 py-3.5 align-middle whitespace-nowrap font-mono text-[12px]">
                          <span
                            className={cn(
                              hasFailedRetry
                                ? "font-semibold text-[#f87171]"
                                : "text-[#e2e8f0]",
                            )}
                          >
                            {displayedAttempts}
                          </span>{" "}
                          <span className="text-[#475569]">/</span>{" "}
                          <span className="text-[#64748b]">5</span>
                        </td>

                        <td className="px-4 py-3.5 align-middle whitespace-nowrap font-mono text-[11px]">
                          {wasRedrivenNow ? (
                            <span className="font-medium text-[#00e599]">
                              Re-driven just now
                            </span>
                          ) : (
                            <span className="text-[#7c839b]">
                              {formatQuarantinedTableTime(item.quarantined_at)}
                            </span>
                          )}
                        </td>

                        <td className="px-4 py-3.5 align-middle">
                          {isRedriving ? (
                            <span
                              data-testid={`status-badge-${item.id}`}
                              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-[#0891b2]/50 bg-[#082f3b]/70 px-2.5 py-1 text-[11px] font-semibold text-[#22d3ee]"
                            >
                              <span className="size-1.5 rounded-full bg-[#22d3ee]" />
                              Re-driving
                            </span>
                          ) : item.is_resolved ? (
                            <span
                              data-testid={`status-badge-${item.id}`}
                              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-[#00e599]/35 bg-[#06241d] px-2.5 py-1 text-[11px] font-semibold text-[#00e599]"
                            >
                              <span className="size-1.5 rounded-full bg-[#00e599]" />
                              Re-driven
                            </span>
                          ) : (
                            <span
                              data-testid={`status-badge-${item.id}`}
                              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-[#7f1d1d]/60 bg-[#2a1216] px-2.5 py-1 text-[11px] font-semibold text-[#f87171]"
                            >
                              <span className="size-1.5 rounded-full bg-[#ef4444]" />
                              Quarantined
                            </span>
                          )}
                        </td>

                        <td className="px-4 py-3.5 align-middle">
                          <div className="flex items-center justify-end gap-2">
                            <button
                              type="button"
                              data-testid={`inspect-button-${item.id}`}
                              onClick={() => handleInspect(item)}
                              aria-label={`Inspect ${meta.deliveryShortId}`}
                              title="Inspect stack trace and payload"
                              className="inline-flex size-[28px] items-center justify-center rounded-[6px] border border-[#252b3b] bg-[#161b26] text-[#94a3b8] transition-colors hover:border-[#333c52] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
                            >
                              <Eye className="size-3.5" aria-hidden="true" />
                            </button>

                            <button
                              type="button"
                              data-testid={`redrive-button-${item.id}`}
                              disabled={
                                isRedriving ||
                                item.is_resolved ||
                                Boolean(activeRedriveId)
                              }
                              onClick={() => void handleRedrive(item)}
                              className={cn(
                                "inline-flex h-[28px] min-w-[84px] items-center justify-center gap-1.5 whitespace-nowrap rounded-[6px] border px-2.5 text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                                isRedriving
                                  ? "border-[#0891b2]/60 bg-[#082f3b] text-[#22d3ee]"
                                  : item.is_resolved
                                    ? "cursor-default border-[#252b3b] bg-[#151a27] text-[#64748b]"
                                    : hasFailedRetry
                                      ? "border-[#ef4444]/60 bg-[#2a1216] text-[#fca5a5] hover:bg-[#3a161c] disabled:opacity-50"
                                      : "border-[#00e599]/40 bg-[#06241d] text-[#00e599] hover:border-[#00e599]/70 hover:bg-[#093329] disabled:opacity-50",
                              )}
                            >
                              {isRedriving ? (
                                <>
                                  <Loader2
                                    className="size-3 animate-spin"
                                    aria-hidden="true"
                                  />
                                  Sending...
                                </>
                              ) : item.is_resolved ? (
                                <>
                                  <Check className="size-3" aria-hidden="true" />
                                  Done
                                </>
                              ) : hasFailedRetry ? (
                                <>
                                  <RotateCcw
                                    className="size-3"
                                    aria-hidden="true"
                                  />
                                  Retry
                                </>
                              ) : (
                                <>
                                  <RotateCcw
                                    className="size-3"
                                    aria-hidden="true"
                                  />
                                  Re-drive
                                </>
                              )}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Footer & Pagination */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#1e2436] px-4 py-2.5">
              {activeRedriveMeta ? (
                <p
                  data-testid="dlq-footer-status"
                  className="font-mono text-[11px] text-[#22d3ee]"
                >
                  Dispatching event {activeRedriveMeta.deliveryShortId} back
                  into webhook processing queue...
                </p>
              ) : (
                <p
                  data-testid="dlq-footer-status"
                  className="text-[11px] text-[#64748b]"
                >
                  Showing {rangeStart}–{rangeEnd} of {filteredItems.length}{" "}
                  events · Failed webhook deliveries enter quarantine after
                  retry exhaustion
                </p>
              )}

              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid="pagination-prev"
                  disabled={safePage <= 1}
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  className="inline-flex h-[24px] items-center rounded-[5px] border border-[#252b3b] bg-[#161b26] px-2 text-[11px] font-medium text-[#94a3b8] transition-colors hover:border-[#333c52] hover:text-[#e2e8f0] disabled:cursor-not-allowed disabled:opacity-45"
                >
                  Prev
                </button>

                {Array.from({ length: totalPages }, (_, index) => index + 1).map(
                  (pageNum) => (
                    <button
                      key={pageNum}
                      type="button"
                      data-testid={`pagination-page-${pageNum}`}
                      aria-current={safePage === pageNum ? "page" : undefined}
                      onClick={() => setCurrentPage(pageNum)}
                      className={cn(
                        "inline-flex h-[24px] min-w-[24px] items-center justify-center rounded-[5px] border px-1.5 font-mono text-[11px] font-semibold transition-colors",
                        safePage === pageNum
                          ? "border-[#00e599]/50 bg-[#06241d] text-[#00e599]"
                          : "border-[#252b3b] bg-[#161b26] text-[#94a3b8] hover:border-[#333c52] hover:text-[#e2e8f0]",
                      )}
                    >
                      {pageNum}
                    </button>
                  ),
                )}

                <button
                  type="button"
                  data-testid="pagination-next"
                  disabled={safePage >= totalPages}
                  onClick={() =>
                    setCurrentPage((p) => Math.min(totalPages, p + 1))
                  }
                  className="inline-flex h-[24px] items-center rounded-[5px] border border-[#252b3b] bg-[#161b26] px-2 text-[11px] font-medium text-[#94a3b8] transition-colors hover:border-[#333c52] hover:text-[#e2e8f0] disabled:cursor-not-allowed disabled:opacity-45"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      <DeadLetterDrawer
        item={inspectedItem}
        open={inspectedItem !== null}
        onClose={() => setInspectedItemId(null)}
        onRedrive={(item) => void handleRedrive(item)}
        isRedriving={
          inspectedItem !== null && activeRedriveId === inspectedItem.id
        }
        hasFailedRetry={
          inspectedItem !== null &&
          Boolean(failedErrorsById[inspectedItem.id]) &&
          !inspectedItem.is_resolved
        }
      />
    </div>
  );
}
