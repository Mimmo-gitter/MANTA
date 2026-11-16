
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
import type {
  BurndownPoint,
  SprintWipStatus,
} from "@/types/sprint-wip";

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
  const initialWork = data[0]?.ideal || data[0]?.actual || 1;

  const chartData = data.map((point, index) => ({
    ...point,
    actual:
      point.actual === null
        ? null
        : (point.actual / initialWork) * 100,
    ideal: (point.ideal / initialWork) * 100,
    sprintDay: index + 1,
  }));

  const today = chartData
    .filter((point) => point.actual !== null)
    .at(-1)?.day;

  return (
    <div className="w-full">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-5">
          <span className="flex items-center gap-2 text-[13px] text-[#c0cada]">
            <span
              className="h-[3px] w-6 rounded-full"
              style={{ backgroundColor: accent }}
            />
            Remaining work
          </span>

          <span className="flex items-center gap-2 text-[13px] text-[#c0cada]">
            <span className="w-6 border-t-2 border-dashed border-[#8993a3]" />
            Guideline
          </span>
        </div>

        <span className="text-xs text-[#94a1b5]">
          Work remaining (%)
        </span>
      </div>

      <div
        className="h-[300px] w-full"
        role="img"
        aria-label="Sprint burndown chart comparing actual and ideal remaining work"
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            margin={{ top: 18, right: 16, bottom: 8, left: -8 }}
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
              tick={{ fill: "#aab7c9", fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: "#344156" }}
              interval={2}
              dy={10}
            />

            <YAxis
              domain={[0, 120]}
              ticks={[0, 20, 40, 60, 80, 100, 120]}
              tick={{ fill: "#aab7c9", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value: number) => `${value}%`}
              width={48}
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
              name="Guideline"
              stroke="#8993a3"
              strokeWidth={2}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />

            <Line
              type="monotone"
              dataKey="actual"
              name="Remaining work"
              stroke={accent}
              strokeWidth={2.8}
              dot={false}
              activeDot={false}
              connectNulls={false}
              isAnimationActive={false}
            />

            {today && (
              <ReferenceLine
                x={today}
                stroke="#38d9a5"
                strokeDasharray="3 4"
                strokeWidth={1.2}
                label={{
                  value: "TODAY",
                  position: "insideTop",
                  fill: "#38d9a5",
                  fontSize: 11,
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
