
"use client";

interface BurndownTooltipProps {
  active?: boolean;
  payload?: Array<{
    dataKey?: string;
    value?: number | null;
    payload?: {
      sprintDay?: number;
    };
  }>;
  label?: string | number;
}

export function BurndownTooltip({
  active,
  payload,
  label,
}: BurndownTooltipProps) {
  if (!active || !payload?.length) return null;

  const actual = payload.find(
    (item) => item.dataKey === "actual"
  )?.value;

  const ideal = payload.find(
    (item) => item.dataKey === "ideal"
  )?.value;

  const sprintDay = payload[0]?.payload?.sprintDay;

  if (actual == null || ideal == null) return null;

  const variance = actual - ideal;
  const variancePercent =
    ideal !== 0 ? (variance / ideal) * 100 : 0;

  const isBehind = variance > 0;

  return (
    <div className="min-w-[210px] overflow-hidden rounded-xl border border-[#344156] bg-[#151c29] shadow-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#29364a] bg-[#1d2737] px-3 py-2">
        <span className="font-mono text-[11px] font-semibold text-[#e2e8f0]">
          {label}
        </span>

        {sprintDay && (
          <span className="rounded bg-[#145b49] px-2 py-1 font-mono text-[9px] font-bold text-[#42e6b2]">
            DAY {sprintDay}
          </span>
        )}
      </div>

      {/* Values */}
      <div className="space-y-2 px-3 py-2.5">
        <div className="flex items-center justify-between gap-4">
          <span className="text-[10px] text-[#aab7c9]">
            Actual Remaining:
          </span>

          <span className="font-mono text-[11px] font-semibold text-[#42e6b2]">
            {actual} SP
          </span>
        </div>

        <div className="flex items-center justify-between gap-4">
          <span className="text-[10px] text-[#aab7c9]">
            Ideal Target:
          </span>

          <span className="font-mono text-[11px] text-[#dce5f0]">
            {ideal} SP
          </span>
        </div>

        <div className="flex items-center justify-between gap-4 border-t border-[#29364a] pt-2">
          <span className="text-[10px] font-semibold text-[#aab7c9]">
            Variance:
          </span>

          <span
            className={`font-mono text-[10px] font-bold ${
              isBehind ? "text-[#42e6b2]" : "text-[#42e6b2]"
            }`}
          >
            {variance > 0 ? "+" : ""}
            {variance} SP ({variance > 0 ? "+" : ""}
            {variancePercent.toFixed(1)}%)
          </span>
        </div>
      </div>
    </div>
  );
}
