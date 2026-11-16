"use client";

import type { BurndownPoint } from "@/types/sprint-wip";

interface BurndownTooltipProps {
  active?: boolean;
  payload?: Array<{
    dataKey?: string;
    value?: number | null;
    payload?: BurndownPoint & {
      sprintDay?: number;
    };
  }>;
  label?: string;
}

export function BurndownTooltip({
  active,
  payload,
  label,
}: BurndownTooltipProps) {
  if (!active || !payload?.length) {
    return null;
  }

  const point = payload.find((item) => item.payload)?.payload;

  if (!point) {
    return null;
  }

  const actual = point.actual;
  const ideal = point.ideal;
  const sprintDay = point.sprintDay;

  const variance = actual !== null ? actual - ideal : null;

  return (
    <div className="min-w-[210px] overflow-hidden rounded-xl border border-[#34445a] bg-[#111a28] shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
      <div className="flex items-center justify-between gap-3 border-b border-[#29364a] bg-[#192536] px-3 py-2.5">
        <div>
          <p className="text-[11px] font-semibold text-white">
            {label ?? point.day}
          </p>

          <p className="mt-0.5 text-[8px] uppercase tracking-wider text-[#8293aa]">
            Sprint progress
          </p>
        </div>

        <span className="shrink-0 rounded-md border border-[#17624e] bg-[#10372e] px-2 py-1 text-[8px] font-bold text-[#40e2b0]">
          {sprintDay !== undefined
            ? `ДЕНЬ СПРИНТУ ${sprintDay}`
            : "ДЕНЬ СПРИНТУ"}
        </span>
      </div>

      <div className="space-y-3 p-3">
        <div className="flex items-center justify-between gap-5">
          <div className="flex items-center gap-2">
            <span className="h-[3px] w-4 rounded-full bg-[#00d9a0]" />
            <span className="text-[10px] text-[#aab7c9]">Remaining work</span>
          </div>

          <span className="font-mono text-[11px] font-semibold text-[#40e2b0]">
            {actual === null ? "—" : `${actual.toFixed(1)}%`}
          </span>
        </div>

        <div className="flex items-center justify-between gap-5">
          <div className="flex items-center gap-2">
            <span className="w-4 border-t-2 border-dashed border-[#8993a3]" />
            <span className="text-[10px] text-[#aab7c9]">Guideline</span>
          </div>

          <span className="font-mono text-[11px] font-semibold text-[#aab7c9]">
            {ideal.toFixed(1)}%
          </span>
        </div>

        <div className="border-t border-[#29364a] pt-2.5">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[9px] font-medium uppercase tracking-wider text-[#8293aa]">
              Variance
            </span>

            {variance !== null ? (
              <span
                className={`font-mono text-[11px] font-bold ${
                  variance <= 0 ? "text-[#40e2b0]" : "text-[#f6c453]"
                }`}
              >
                {variance > 0 ? "+" : variance < 0 ? "−" : ""}
                {Math.abs(variance).toFixed(1)}%
              </span>
            ) : (
              <span className="text-[10px] text-[#8293aa]">Not recorded</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
