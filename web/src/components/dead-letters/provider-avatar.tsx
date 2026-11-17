import { Webhook, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import type { NormalizedDeadLetterProvider } from "@/lib/dead-letters-api";

export function DeadLetterProviderAvatar({
  provider,
  className,
}: {
  provider: NormalizedDeadLetterProvider;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex size-[30px] shrink-0 items-center justify-center rounded-[8px] border border-[#262c3d] bg-[#1c1f2a]",
        className,
      )}
      aria-hidden="true"
    >
      {provider === "github" ? (
        <svg viewBox="0 0 16 16" className="size-[14px]">
          <path
            fill="#f0f4f8"
            d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
          />
        </svg>
      ) : provider === "jira" ? (
        <svg viewBox="0 0 24 24" className="size-[14px]">
          <path
            fill="#2684ff"
            d="M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005zm5.723-5.756H5.736a5.215 5.215 0 0 0 5.215 5.214h2.129v2.058a5.218 5.218 0 0 0 5.215 5.214V6.758a1.001 1.001 0 0 0-1.001-1.001zM23.013 0H11.455a5.215 5.215 0 0 0 5.215 5.215h2.129v2.057A5.215 5.215 0 0 0 24 12.483V1.005A1.001 1.001 0 0 0 23.013 0Z"
          />
        </svg>
      ) : provider === "slack" ? (
        <svg viewBox="0 0 24 24" className="size-[14px]" fill="none">
          <rect x="8" y="2" width="4" height="9" rx="2" fill="#36C5F0" />
          <rect x="2" y="8" width="9" height="4" rx="2" fill="#36C5F0" />
          <rect x="12" y="2" width="4" height="9" rx="2" fill="#2EB67D" />
          <rect x="13" y="8" width="9" height="4" rx="2" fill="#2EB67D" />
          <rect x="12" y="13" width="4" height="9" rx="2" fill="#ECB22E" />
          <rect x="13" y="12" width="9" height="4" rx="2" fill="#ECB22E" />
          <rect x="8" y="13" width="4" height="9" rx="2" fill="#E01E5A" />
          <rect x="2" y="12" width="9" height="4" rx="2" fill="#E01E5A" />
        </svg>
      ) : provider === "argocd" ? (
        <GitBranch className="size-[14px] text-[#38bdf8]" />
      ) : (
        <Webhook className="size-[14px] text-[#94a3b8]" />
      )}
    </div>
  );
}
