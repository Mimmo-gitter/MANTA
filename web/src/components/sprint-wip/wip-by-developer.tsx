"use client";

import { Users, AlertTriangle, Info } from "lucide-react";
import type { FlowMetricsWipByDeveloper } from "@/types/sprint-wip";

interface WipByDeveloperProps {
  data: FlowMetricsWipByDeveloper;
}

export function WipByDeveloper({ data }: WipByDeveloperProps) {
  const hasExceededLimit = data.developers.some(
    (developer) => developer.over_limit,
  );

  const hasReachedLimit = data.developers.some(
    (developer) => developer.count >= developer.limit && !developer.over_limit,
  );

  const status = hasExceededLimit
    ? "LIMIT EXCEEDED"
    : hasReachedLimit
      ? "AT LIMIT"
      : "WITHIN LIMITS";

  const statusClass = hasExceededLimit
    ? "border-[#71333a] bg-[#321e27] text-[#fb7185]"
    : hasReachedLimit
      ? "border-[#66502b] bg-[#30291d] text-[#f6c453]"
      : "border-[#145b4b] bg-[#0d332d] text-[#42e6b2]";

  return (
    <section className="min-w-0 rounded-[10px] border border-[#202738] bg-[#191e2a] p-4">
      <div className="mb-1 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 text-[#e3eaf4]">
          <Users className="size-5 shrink-0 text-[#31c9cf]" />
          <h2 className="text-base font-semibold">WIP per Developer</h2>
        </div>

        <span
          className={`shrink-0 rounded-sm border px-2 py-1 font-mono text-[10px] font-bold tracking-wider ${statusClass}`}
        >
          {status}
        </span>
      </div>

      <p className="mb-4 text-xs text-[#a0adbd]">
        Unfinished tasks assigned to each developer compared with the individual
        WIP limit.
      </p>

      {data.developers.length > 0 ? (
        <div className="space-y-2.5">
          {data.developers.map((developer) => {
            const ratio =
              developer.limit > 0 ? developer.count / developer.limit : 0;

            const progress = Math.min(100, ratio * 100);

            const color = developer.over_limit
              ? "#fb7185"
              : developer.count >= developer.limit
                ? "#f6c453"
                : "#43d6df";

            return (
              <div
                key={developer.developer_label}
                className={`rounded-lg border px-3 py-3 ${
                  developer.over_limit
                    ? "border-[#71333a] bg-[#20212d]"
                    : "border-[#252f40] bg-[#191e2a]"
                }`}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-[#dce5f0]">
                    {developer.developer_label}
                  </span>

                  <span className="font-mono text-xs text-[#dce5f0]">
                    {developer.count} / {developer.limit}
                  </span>
                </div>

                <div className="h-[7px] w-full overflow-hidden rounded-full bg-[#343844]">
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{
                      width: `${progress}%`,
                      backgroundColor: color,
                    }}
                  />
                </div>

                <div className="mt-2 flex justify-between gap-2 text-[10px]">
                  <span
                    className={
                      developer.over_limit
                        ? "text-[#fb7185]"
                        : developer.count >= developer.limit
                          ? "text-[#f6c453]"
                          : "text-[#42e6b2]"
                    }
                  >
                    {developer.over_limit
                      ? `${developer.count - developer.limit} over limit`
                      : developer.count >= developer.limit
                        ? "At limit"
                        : `${Math.max(
                            0,
                            developer.limit - developer.count,
                          )} slots open`}
                  </span>

                  <span className="text-[#879487]">
                    {Math.round(ratio * 100)}% capacity
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-[#252f40] bg-[#191e2a] p-4 text-center text-xs text-[#8795a8]">
          No assigned unfinished tasks in this sprint.
        </div>
      )}

      <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-[#252f40] bg-[#0d121c] p-3">
        <div className="flex items-center gap-2 text-[11px] text-[#aab7c9]">
          <Info className="size-3.5 shrink-0 text-[#31c9cf]" />
          Unassigned tasks
        </div>

        <span className="font-mono text-sm font-semibold text-[#e3eaf4]">
          {data.unassigned_count}
        </span>
      </div>

      {hasExceededLimit && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-[#71333a] bg-[#171923] p-3 text-[11px] leading-relaxed text-[#aab7c9]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-[#fb7185]" />
          One or more developers have exceeded their individual WIP limits.
          Consider redistributing unfinished tasks.
        </div>
      )}
    </section>
  );
}
