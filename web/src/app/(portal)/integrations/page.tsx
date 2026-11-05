"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Filter,
  Info,
  Plus,
  Puzzle,
  RefreshCw,
  X,
} from "lucide-react";
import {
  useShellState,
  type ShellState,
} from "@/components/layout/shell-context";
import { cn } from "@/lib/utils";
import { GitHubConnectDialog } from "@/components/integrations/github-connect-dialog";
import { JiraConnectDialog } from "@/components/integrations/jira-connect-dialog";
import { SlackConnectDialog } from "@/components/integrations/slack-connect-dialog";
import { getIntegrations } from "@/lib/integrations-api";
import type { Integration as ApiIntegration } from "@/types/integration";

/* -------------------------------------------------------------------------- */
/* Types & data                                                               */
/* -------------------------------------------------------------------------- */

type ProviderId = "github" | "jira" | "slack";
type IntegrationStatus = "connected" | "needs_reconnect";
type StatusFilter = "all" | IntegrationStatus;
type LoadState = "loading" | "ready" | "error";

interface Integration {
  id: string;
  provider: ProviderId;
  name: string;
  description: string;
  status: IntegrationStatus;
  initials: string;
}

interface Provider {
  id: ProviderId;
  name: string;
  description: string;
}

const PROVIDERS: Provider[] = [
  {
    id: "github",
    name: "GitHub",
    description: "Sync repositories, pull requests, and development activity.",
  },
  {
    id: "jira",
    name: "Jira",
    description: "Sync project issues and delivery workflow data.",
  },
  {
    id: "slack",
    name: "Slack",
    description: "Connect team communication signals.",
  },
];

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "connected", label: "Connected" },
  { value: "needs_reconnect", label: "Needs reconnect" },
];

/**
 */
async function fetchIntegrations(): Promise<Integration[]> {
  const data: ApiIntegration[] = await getIntegrations();

  return data.map((item) => {
    const providerInfo = {
      github: {
        name: "GitHub",
        description: "Repositories, pull requests, and commit sync",
        initials: "GH",
      },
      jira: {
        name: "Jira",
        description: "Sprint tracking, backlog items, and issue telemetry",
        initials: "JR",
      },
      slack: {
        name: "Slack",
        description: "Team communication and notifications",
        initials: "SL",
      },
    }[item.provider];

    return {
      id: item.id,
      provider: item.provider,
      name: item.external_account_id
        ? `${providerInfo.name} (${item.external_account_id})`
        : providerInfo.name,
      description: providerInfo.description,
      status: item.is_active ? "connected" : "needs_reconnect",
      initials: providerInfo.initials,
    };
  });
}

/* -------------------------------------------------------------------------- */
/* Brand marks (swap for official assets if you keep them in /public)         */
/* -------------------------------------------------------------------------- */

function ProviderLogo({
  provider,
  className,
}: {
  provider: ProviderId;
  className?: string;
}) {
  if (provider === "github") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true" className={className}>
        <path
          fill="#f0f4f8"
          d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
        />
      </svg>
    );
  }
  if (provider === "jira") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
        <path
          fill="#2684ff"
          d="M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005zm5.723-5.756H5.736a5.215 5.215 0 0 0 5.215 5.214h2.129v2.058a5.218 5.218 0 0 0 5.215 5.214V6.758a1.001 1.001 0 0 0-1.001-1.001zM23.013 0H11.455a5.215 5.215 0 0 0 5.215 5.215h2.129v2.057A5.215 5.215 0 0 0 24 12.483V1.005A1.001 1.001 0 0 0 23.013 0Z"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <rect x="9" y="2" width="3" height="8" rx="1.5" fill="#36c5f0" />
      <rect x="14" y="9" width="8" height="3" rx="1.5" fill="#2eb67d" />
      <rect x="12" y="14" width="3" height="8" rx="1.5" fill="#ecb22e" />
      <rect x="2" y="12" width="8" height="3" rx="1.5" fill="#e01e5a" />
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Small building blocks                                                      */
/* -------------------------------------------------------------------------- */

function PageHeading({ action }: { action?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-1.5 font-mono text-[11px] leading-[16px] text-[#7c839b]">
          <li>Administration</li>
          <li aria-hidden="true">/</li>
          <li aria-current="page" className="text-[#00e599]">
            Integrations
          </li>
        </ol>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[20px] font-semibold leading-[28px] text-[#e2e8f0]">
            Integrations
          </h1>
          <p className="mt-0.5 text-[12px] leading-[16px] text-[#7c839b]">
            Manage connected tools and sync status.
          </p>
        </div>
        {action}
      </div>
    </div>
  );
}

function PrimaryButton({
  children,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "inline-flex h-[32px] items-center justify-center gap-1.5 rounded-[6px] bg-[#00e599] px-3.5 text-[12px] font-semibold text-[#0f131d] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f131d]",
        className,
      )}
    >
      {children}
    </button>
  );
}

function SecondaryButton({
  children,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "inline-flex h-[32px] items-center justify-center gap-1.5 rounded-[6px] border border-[#252b3b] bg-[#161b26] px-3.5 text-[12px] font-medium text-[#e2e8f0] transition-colors hover:border-[#7c839b]/50 hover:bg-[#1a2030] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
        className,
      )}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Summary strip                                                              */
/* -------------------------------------------------------------------------- */

function formatLastChecked(checkedAt: number | null, now: number) {
  if (!checkedAt) return "Not checked yet";
  const minutes = Math.floor((now - checkedAt) / 60000);
  if (minutes < 1) return "Last checked just now";
  if (minutes === 1) return "Last checked 1 min ago";
  return `Last checked ${minutes} min ago`;
}

function SummaryStrip({
  total,
  healthy,
  attention,
  checkedAt,
}: {
  total: number;
  healthy: number;
  attention: number;
  checkedAt: number | null;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 rounded-[8px] border border-[#1f2433] bg-[#161b26] px-4 py-2.5 text-[12px] leading-[16px]"
    >
      <p className="flex flex-wrap items-center gap-x-5 gap-y-1">
        <span className="text-[#e2e8f0]">
          {total} {total === 1 ? "integration" : "integrations"}
        </span>
        <span className="text-[#00e599]">{healthy} healthy</span>
        {attention > 0 && (
          <span className="text-[#fbbf24]">{attention} needs attention</span>
        )}
      </p>
      <span className="font-mono text-[11px] text-[#7c839b]">
        {formatLastChecked(checkedAt, now)}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Status filter                                                              */
/* -------------------------------------------------------------------------- */

function StatusFilterMenu({
  value,
  onChange,
  counts,
}: {
  value: StatusFilter;
  onChange: (value: StatusFilter) => void;
  counts: Record<StatusFilter, number>;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const current = STATUS_FILTERS.find((f) => f.value === value)!;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex h-[28px] items-center gap-1.5 rounded-[6px] border px-2.5 text-[11px] text-[#e2e8f0] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
          open
            ? "border-[#00e599]/60 bg-[#1a2030]"
            : "border-[#252b3b] bg-[#0f131d] hover:border-[#7c839b]/50",
        )}
      >
        <Filter className="size-3 text-[#7c839b]" aria-hidden="true" />
        <span>{current.label}</span>
        <span className="rounded-[4px] bg-white/[0.06] px-1.5 font-mono text-[10px] leading-[16px] text-[#7c839b]">
          {counts[value]}
        </span>
        <ChevronDown
          className={cn(
            "size-3 text-[#7c839b] transition-transform duration-150",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <ul
          id={menuId}
          role="listbox"
          aria-label="Filter by status"
          className="absolute right-0 top-[calc(100%+6px)] z-20 w-[200px] rounded-[10px] border border-[#1f2433] bg-[#161b26] p-1.5 shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.7)]"
        >
          {STATUS_FILTERS.map((f) => {
            const selected = f.value === value;
            return (
              <li key={f.value} role="presentation">
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    onChange(f.value);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between rounded-[6px] px-2.5 py-2 text-left text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                    selected
                      ? "bg-[rgba(0,229,153,0.1)] text-[#e2e8f0]"
                      : "text-[#a3acc2] hover:bg-white/[0.04]",
                  )}
                >
                  <span>{f.label}</span>
                  <span className="font-mono text-[10px] text-[#7c839b]">
                    {counts[f.value]}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Connected tools list                                                       */
/* -------------------------------------------------------------------------- */

function IntegrationRow({
  integration,
  onConfigure,
  onReconnect,
}: {
  integration: Integration;
  onConfigure: (i: Integration) => void;
  onReconnect: (i: Integration) => void;
}) {
  const needsReconnect = integration.status === "needs_reconnect";

  return (
    <li className="flex flex-col gap-3 border-t border-[#1f2433] px-4 py-3.5 first:border-t-0 sm:flex-row sm:items-center sm:gap-4">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div
          aria-hidden="true"
          className="flex size-[32px] shrink-0 items-center justify-center rounded-[6px] border border-[#252b3b] bg-[#0f131d] font-mono text-[10px] font-semibold text-[#00e599]"
        >
          {integration.initials}
        </div>
        <div className="min-w-0">
          <h3 className="truncate text-[13px] font-medium leading-[18px] text-[#e2e8f0]">
            {integration.name}
          </h3>
          <p
            className={cn(
              "truncate text-[11px] leading-[16px]",
              needsReconnect ? "text-[#fbbf24]/80" : "text-[#7c839b]",
            )}
          >
            {integration.description}
          </p>
        </div>
      </div>

      <div className="flex items-center justify-between gap-4 sm:justify-end">
        <span
          className={cn(
            "text-[11px] font-medium",
            needsReconnect ? "text-[#fbbf24]" : "text-[#00e599]",
          )}
        >
          {needsReconnect ? "Needs Reconnect" : "Connected"}
        </span>

        {needsReconnect ? (
          <button
            type="button"
            onClick={() => onReconnect(integration)}
            aria-label={`Reconnect ${integration.name}`}
            className="inline-flex h-[28px] min-w-[84px] items-center justify-center rounded-[6px] border border-[#fbbf24]/60 bg-[#fbbf24]/10 px-3 text-[11px] font-medium text-[#fbbf24] transition-colors hover:bg-[#fbbf24]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#fbbf24]"
          >
            Reconnect
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onConfigure(integration)}
            aria-label={`Configure ${integration.name}`}
            className="inline-flex h-[28px] min-w-[84px] items-center justify-center rounded-[6px] border border-[#252b3b] bg-[#0f131d] px-3 text-[11px] font-medium text-[#e2e8f0] transition-colors hover:border-[#00e599] hover:bg-[#00e599] hover:text-[#0f131d] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
          >
            Configure
          </button>
        )}
      </div>
    </li>
  );
}

function ConnectedToolsPanel({
  items,
  allCount,
  filter,
  onFilterChange,
  counts,
  onConfigure,
  onReconnect,
}: {
  items: Integration[];
  allCount: number;
  filter: StatusFilter;
  onFilterChange: (f: StatusFilter) => void;
  counts: Record<StatusFilter, number>;
  onConfigure: (i: Integration) => void;
  onReconnect: (i: Integration) => void;
}) {
  return (
    <section
      aria-labelledby="connected-tools-heading"
      className="rounded-[10px] border border-[#1f2433] bg-[#131824]"
    >
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <h2
          id="connected-tools-heading"
          className="font-mono text-[11px] font-semibold uppercase tracking-[0.8px] text-[#e2e8f0]"
        >
          Connected tools ({allCount})
        </h2>
        <StatusFilterMenu
          value={filter}
          onChange={onFilterChange}
          counts={counts}
        />
      </div>

      {items.length > 0 ? (
        <ul className="border-t border-[#1f2433]">
          {items.map((integration) => (
            <IntegrationRow
              key={integration.id}
              integration={integration}
              onConfigure={onConfigure}
              onReconnect={onReconnect}
            />
          ))}
        </ul>
      ) : (
        <div className="flex flex-col items-center gap-3 border-t border-[#1f2433] px-4 py-10 text-center">
          <p className="text-[12px] text-[#7c839b]">
            No integrations match this filter.
          </p>
          <SecondaryButton onClick={() => onFilterChange("all")}>
            Show all statuses
          </SecondaryButton>
        </div>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Loading / empty / error states                                             */
/* -------------------------------------------------------------------------- */

function LoadingState() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading integrations…</span>
      <div aria-hidden="true" className="flex flex-col gap-4">
        <div className="h-[40px] rounded-[8px] border border-[#1f2433] bg-[#161b26]" />
        <div className="rounded-[10px] border border-[#1f2433] bg-[#131824]">
          <div className="flex items-center justify-between px-4 py-3.5">
            <div className="h-3 w-40 animate-pulse rounded bg-[#1f2433]" />
            <div className="flex gap-2">
              <div className="h-6 w-20 animate-pulse rounded-full bg-[#1f2433]" />
              <div className="hidden h-6 w-14 animate-pulse rounded-full bg-[#1a2030] sm:block" />
              <div className="hidden h-6 w-14 animate-pulse rounded-full bg-[#1a2030] sm:block" />
            </div>
          </div>
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="flex items-center gap-3 border-t border-[#1f2433] px-4 py-4"
            >
              <div className="size-[32px] shrink-0 animate-pulse rounded-full bg-[#1f2433]" />
              <div className="flex flex-1 flex-col gap-2">
                <div className="flex gap-2">
                  <div className="h-3 w-32 animate-pulse rounded bg-[#1f2433]" />
                  <div className="h-3 w-12 animate-pulse rounded bg-[#1a2030]" />
                </div>
                <div className="h-2.5 w-56 max-w-full animate-pulse rounded bg-[#1a2030]" />
              </div>
              <div className="hidden h-7 w-20 animate-pulse rounded-[6px] bg-[#0a0d14] sm:block" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onConnect }: { onConnect: () => void }) {
  return (
    <div className="flex min-h-[420px] flex-col items-center justify-center px-4 py-12 text-center">
      <div
        aria-hidden="true"
        className="mb-5 flex size-[40px] items-center justify-center rounded-[8px] border border-[#252b3b] bg-[#161b26]"
      >
        <Puzzle className="size-[18px] text-[#7c839b]" />
      </div>
      <h2 className="text-[16px] font-semibold leading-[24px] text-[#e2e8f0]">
        No integrations connected
      </h2>
      <p className="mt-1 max-w-[360px] text-[12px] leading-[18px] text-[#7c839b]">
        Connect your development tools to sync project activity and monitor
        delivery health.
      </p>
      <PrimaryButton onClick={onConnect} className="mt-5 h-[34px] px-4">
        <Plus className="size-3.5" aria-hidden="true" />
        Connect Integration
      </PrimaryButton>
    </div>
  );
}

function ErrorState({
  onRetry,
  retrying,
  message,
}: {
  onRetry: () => void;
  retrying: boolean;
  message: string;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const detailsId = useId();

  return (
    <div className="flex min-h-[420px] items-center justify-center px-4 py-8">
      <div
        className="flex w-full max-w-[512px] flex-col items-center rounded-[12px] border border-[#1f2433] bg-[#161b26] px-6 py-8 text-center shadow-lg"
        role="alert"
      >
        <div
          aria-hidden="true"
          className="mb-5 flex size-[48px] items-center justify-center rounded-full border border-[#ef4444]/20 bg-[#ef4444]/[0.08]"
        >
          <AlertTriangle className="size-[20px] text-[#ef4444]" />
        </div>

        <h2 className="text-[18px] font-semibold leading-[26px] text-[#e2e8f0]">
          Unable to load integrations
        </h2>

        <p className="mt-2 text-[13px] leading-[20px] text-[#7c839b]">
          We couldn’t load your integrations. Please try again.
        </p>

        <div className="mt-6 flex items-center gap-3">
          <SecondaryButton onClick={onRetry} disabled={retrying}>
            <RefreshCw
              className={cn("size-3", retrying && "animate-spin")}
              aria-hidden="true"
            />
            Retry
          </SecondaryButton>

          <div className="group relative">
            <SecondaryButton
              className="border-transparent bg-transparent text-[#a3acc2] hover:border-transparent hover:bg-white/[0.04] hover:text-[#e2e8f0]"
              aria-describedby={detailsId}
            >
              <Info className="size-3" aria-hidden="true" />
              Error details
            </SecondaryButton>

            <div
              id={detailsId}
              role="tooltip"
              className="invisible absolute left-1/2 top-[calc(100%+8px)] z-50 w-[288px] -translate-x-1/2 rounded-[8px] border border-[#252b3b] bg-[#0f131d] p-3 text-left text-[12px] leading-[18px] text-[#e2e8f0] opacity-0 shadow-xl transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
            >
              The integrations could not be loaded. Retry the request. If the
              problem persists, contact your administrator.
            </div>
          </div>
        </div>

        {showDetails && (
          <pre
            id={detailsId}
            className="mt-3 w-full max-w-[440px] whitespace-pre-wrap break-words rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3 text-left font-mono text-[11px] leading-[17px] text-[#a3acc2]"
          >
            {message}
          </pre>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Connect modal                                                              */
/* -------------------------------------------------------------------------- */

function ConnectDialog({
  open,
  onClose,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (provider: Provider) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    if (!open) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;

      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-[#05070b]/80 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="w-full max-w-[520px] rounded-[12px] border border-[#252b3b] bg-[#161b26] p-5 shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.7)]"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2
              id={titleId}
              className="text-[16px] font-semibold leading-[24px] text-[#e2e8f0]"
            >
              Connect an integration
            </h2>
            <p
              id={descId}
              className="mt-0.5 text-[12px] leading-[16px] text-[#7c839b]"
            >
              Choose a provider to connect your development tools to M.A.N.T.A.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="-mr-1 -mt-1 flex size-[28px] shrink-0 items-center justify-center rounded-[6px] text-[#7c839b] transition-colors hover:bg-white/[0.05] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>

        <ul className="mt-4 flex flex-col gap-2.5">
          {PROVIDERS.map((provider) => (
            <li
              key={provider.id}
              className={cn(
                "group flex items-center gap-3 rounded-[8px] border border-[#252b3b] bg-[#1a2030]/60 p-3 transition-colors",
                provider.id === "github" &&
                  "focus-within:border-[#00e599]/60 hover:border-[#00e599]/60",
                provider.id === "jira" &&
                  "focus-within:border-[#00C2CB]/60 hover:border-[#00C2CB]/60",
                provider.id === "slack" &&
                  "focus-within:border-[#FCD34D]/60 hover:border-[#FCD34D]/60",
              )}
            >
              <div
                aria-hidden="true"
                className="flex size-[32px] shrink-0 items-center justify-center rounded-[6px] bg-[#0a0d14]"
              >
                <ProviderLogo provider={provider.id} className="size-[18px]" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-[13px] font-medium leading-[18px] text-[#e2e8f0]">
                  {provider.name}
                </h3>
                <p className="text-[11px] leading-[15px] text-[#7c839b]">
                  {provider.description}
                </p>
              </div>
              <button
                type="button"
                onClick={() => onSelect(provider)}
                aria-label={`Configure ${provider.name}`}
                className="inline-flex h-[28px] shrink-0 items-center gap-1 rounded-[6px] border border-[#252b3b] bg-[#0f131d] px-3 text-[11px] font-medium text-[#e2e8f0] transition-colors hover:border-[#00e599] hover:bg-[#00e599] hover:text-[#0f131d] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
              >
                Configure
                <ChevronRight className="size-3" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function IntegrationsPage() {
  const { shellState, setShellState } = useShellState();
  const searchParams = useSearchParams();
  const stateParam = searchParams.get("state") as ShellState | null;

  useEffect(() => {
    if (
      stateParam &&
      ["default", "empty", "error", "loading"].includes(stateParam)
    ) {
      setShellState(stateParam);
    }
  }, [stateParam, setShellState]);

  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [errorMessage, setErrorMessage] = useState("");
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [githubOpen, setGithubOpen] = useState(false);
  const [jiraOpen, setJiraOpen] = useState(false);
  const [slackOpen, setSlackOpen] = useState(false);

  const load = useCallback(async () => {
    setLoadState("loading");
    try {
      const data = await fetchIntegrations();
      setIntegrations(data);
      setCheckedAt(Date.now());
      setLoadState("ready");
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : "Unknown error while loading integrations",
      );
      setLoadState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /*
   * ShellState lets designers/QA force a view (loading / empty / error)
   * from the shell. In "default" the real request state is used.
   */
  const view: LoadState | "empty" = useMemo(() => {
    if (shellState === "loading") return "loading";
    if (shellState === "error") return "error";
    if (shellState === "empty") return "empty";
    if (loadState === "ready" && integrations.length === 0) return "empty";
    return loadState;
  }, [shellState, loadState, integrations.length]);

  const counts = useMemo<Record<StatusFilter, number>>(
    () => ({
      all: integrations.length,
      connected: integrations.filter((i) => i.status === "connected").length,
      needs_reconnect: integrations.filter(
        (i) => i.status === "needs_reconnect",
      ).length,
    }),
    [integrations],
  );

  const visible = useMemo(
    () =>
      filter === "all"
        ? integrations
        : integrations.filter((i) => i.status === filter),
    [integrations, filter],
  );

  const openDialog = () => setDialogOpen(true);
  const closeDialog = useCallback(() => setDialogOpen(false), []);

  // TODO: route to provider setup, e.g. router.push(`/integrations/${provider.id}`)
  const handleSelectProvider = (provider: Provider) => {
    setDialogOpen(false);

    if (provider.id === "github") {
      setGithubOpen(true);
      return;
    }

    if (provider.id === "jira") {
      setJiraOpen(true);
      return;
    }

    if (provider.id === "slack") {
      setSlackOpen(true);
      return;
    }

    console.info("Configure provider", provider.id);
  };

  const closeGithub = useCallback(() => {
    setGithubOpen(false);
  }, []);

  const closeJira = useCallback(() => {
    setJiraOpen(false);
  }, []);

  const handleConfigure = (integration: Integration) => {
    console.info("Configure integration", integration.id);
  };
  // TODO: start the provider's OAuth re-auth flow
  const handleReconnect = (integration: Integration) => {
    console.info("Reconnect integration", integration.id);
  };

  const showConnectAction = view === "ready";

  return (
    <div className="flex min-h-full flex-col gap-4 px-4 py-5 sm:px-6">
      <PageHeading
        action={
          showConnectAction ? (
            <PrimaryButton onClick={openDialog}>
              <Plus className="size-3.5" aria-hidden="true" />
              Connect Integration
            </PrimaryButton>
          ) : null
        }
      />

      {view === "loading" && <LoadingState />}

      {view === "empty" && <EmptyState onConnect={openDialog} />}

      {view === "error" && (
        <ErrorState
          onRetry={load}
          retrying={loadState === "loading"}
          message={
            errorMessage || "Request failed: GET /api/integrations (500)"
          }
        />
      )}

      {view === "ready" && (
        <>
          <SummaryStrip
            total={counts.all}
            healthy={counts.connected}
            attention={counts.needs_reconnect}
            checkedAt={checkedAt}
          />
          <ConnectedToolsPanel
            items={visible}
            allCount={counts.all}
            filter={filter}
            onFilterChange={setFilter}
            counts={counts}
            onConfigure={handleConfigure}
            onReconnect={handleReconnect}
          />
        </>
      )}

      {/* Compact Floating State Switcher for QA & Figma Frame Switching */}
      <div
        data-testid="integrations-state-bar"
        role="radiogroup"
        aria-label="Integrations Viewport State Selector"
        className="fixed bottom-3 right-3 z-40 flex items-center gap-[2px] rounded-[8px] border border-[#1f2433] bg-[#161b26]/95 p-[3px] shadow-lg backdrop-blur-sm"
      >
        {(
          [
            { id: "default", label: "Default" },
            { id: "empty", label: "Empty" },
            { id: "error", label: "Error" },
            { id: "loading", label: "Skeleton" },
          ] as const
        ).map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={shellState === item.id}
            data-testid={`state-toggle-${item.id}`}
            onClick={() => setShellState(item.id)}
            className={cn(
              "rounded-[5px] px-[8px] py-[4px] font-mono text-[10px] leading-[14px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
              shellState === item.id
                ? "bg-[rgba(0,229,153,0.15)] text-[#00e599] font-semibold border border-[rgba(0,229,153,0.3)]"
                : "text-[#7c839b] hover:text-[#e2e8f0]",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      <ConnectDialog
        open={dialogOpen}
        onClose={closeDialog}
        onSelect={handleSelectProvider}
      />
      <GitHubConnectDialog
        open={githubOpen}
        onClose={closeGithub}
        onConnected={() => void load()}
      />
      <JiraConnectDialog
        open={jiraOpen}
        onClose={closeJira}
        onConnected={() => void load()}
      />
      <SlackConnectDialog
        open={slackOpen}
        onClose={() => setSlackOpen(false)}
        onConnected={() => void load()}
      />
    </div>
  );
}
