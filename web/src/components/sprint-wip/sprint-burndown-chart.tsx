"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { BurndownTooltip } from "./burndown-tooltip";
import type { BurndownPoint, SprintWipStatus } from "@/types/sprint-wip";

interface SprintBurndownChartProps {
  data: BurndownPoint[];
  accent: string;
  scenario: SprintWipStatus;
}

export function SprintBurndownChart({
  data,
  accent,
  scenario,
}: SprintBurndownChartProps) {
  const chartData = data.map((point, index) => ({
    ...point,
    sprintDay: index + 1,
  }));

  const totalPoints = data[0]?.ideal ?? data[0]?.actual ?? 0;
  const firstIdeal = data[0]?.ideal ?? 0;
  const lastIdeal = data[data.length - 1]?.ideal ?? 0;

  const targetVelocity =
    data.length > 1 ? (firstIdeal - lastIdeal) / (data.length - 1) : 0;

  const burndownStatus = {
    healthy: {
      label: (
        <>
          Burndown tracking
          <br />
          within planned velocity
        </>
      ),
      className: "border-[#17634e] bg-[#12382f] text-[#42e6b2]",
    },
    warning: {
      label: (
        <>
          Actual remaining points
          <br />
          exceed ideal trajectory
        </>
      ),
      className: "border-[#806326] bg-[#382d17] text-[#fbbf24]",
    },
    critical: {
      label: (
        <>
          High risk: sprint is
          <br />
          behind schedule
        </>
      ),
      className: "border-[#71333a] bg-[#3b2028] text-[#fb7185]",
    },
    upcoming: {
      label: (
        <>
          No sprint data
          <br />
          available
        </>
      ),
      className: "border-[#344156] bg-[#202938] text-[#aab7c9]",
    },
  }[scenario];

  const maxPoints = Math.max(
    0,
    ...chartData.map((point) => Math.max(point.actual ?? 0, point.ideal)),
  );

  const yAxisMax = Math.max(5, Math.ceil(maxPoints / 5) * 5);

  const today = chartData.filter((point) => point.actual !== null).at(-1)?.day;

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-[#e2e8f0]">
              Sprint Burndown
            </h2>

            <span className="rounded-sm bg-[#343b49] px-2 py-0.5 font-mono text-[9px] tracking-wider text-[#aab7c9]">
              {totalPoints} SP TOTAL
            </span>
          </div>

          <p className="max-w-[360px] text-xs leading-relaxed text-[#aab7c9]">
            Actual remaining Story Points compared with the ideal sprint
            burndown trajectory.
          </p>
        </div>

        <div className="flex flex-col items-end gap-2">
          <span
            className={`rounded-sm border px-3 py-2 text-center font-mono text-[10px] leading-tight ${burndownStatus.className}`}
          >
            {burndownStatus.label}
          </span>
        </div>
      </div>

      {/* Legend */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-[#29364a] pb-2">
        <div className="flex flex-wrap items-center gap-4">
          <span className="flex items-center gap-2 font-mono text-[10px] text-[#dce5f0]">
            <span
              className="h-[3px] w-3 rounded-full"
              style={{ backgroundColor: accent }}
            />
            Actual Remaining Story Points
          </span>

          <span className="flex items-center gap-2 font-mono text-[10px] text-[#aab7c9]">
            <span className="w-3 border-t border-dashed border-[#8993a3]" />
            Ideal Burndown
          </span>
        </div>

        <span className="font-mono text-[10px] text-[#dce5f0]">
          Target Velocity: {targetVelocity.toFixed(1)} SP / day
        </span>
      </div>

      {/* Chart */}
      <div
        className="h-[250px] w-full"
        role="img"
        aria-label="Sprint burndown chart comparing actual and ideal remaining Story Points"
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            margin={{ top: 18, right: 16, bottom: 4, left: -8 }}
          >
            <defs>
              <linearGradient
                id={`actualFill-${scenario}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={accent} stopOpacity={0.2} />
                <stop offset="100%" stopColor={accent} stopOpacity={0.01} />
              </linearGradient>
            </defs>

            <CartesianGrid
              stroke="#29364a"
              strokeDasharray="2 5"
              vertical={false}
            />

            <XAxis
              dataKey="day"
              tick={{ fill: "#8997aa", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "#344156" }}
              interval={2}
              dy={8}
            />

            <YAxis
              domain={[0, yAxisMax]}
              tick={{ fill: "#8997aa", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value: number) => `${value} SP`}
              width={52}
            />

            <Tooltip
              content={<BurndownTooltip />}
              cursor={{
                stroke: "#8b9bb1",
                strokeWidth: 1,
                strokeDasharray: "4 4",
              }}
              isAnimationActive={false}
              wrapperStyle={{ outline: "none", zIndex: 50 }}
            />

            <Area
              type="monotone"
              dataKey="actual"
              stroke="none"
              fill={`url(#actualFill-${scenario})`}
              connectNulls={false}
              activeDot={false}
              isAnimationActive={false}
            />

            <Line
              type="linear"
              dataKey="ideal"
              name="Ideal Burndown"
              stroke="#8993a3"
              strokeWidth={1.8}
              strokeDasharray="5 4"
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />

            <Line
              type="monotone"
              dataKey="actual"
              name="Actual Remaining Story Points"
              stroke={accent}
              strokeWidth={2.5}
              dot={{
                r: 2.5,
                fill: "#0b1720",
                stroke: accent,
                strokeWidth: 1.5,
              }}
              activeDot={{ r: 4 }}
              connectNulls={false}
              isAnimationActive={false}
            />

            {today && (
              <ReferenceLine
                x={today}
                stroke="#38d9a5"
                strokeDasharray="3 4"
                strokeWidth={1}
                label={{
                  value: "TODAY",
                  position: "insideTop",
                  fill: "#38d9a5",
                  fontSize: 9,
                  fontWeight: 700,
                }}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
