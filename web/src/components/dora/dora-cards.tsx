"use client";

/**
 * Presentational components for the Delivery Analytics (DORA) dashboard:
 * page header and filters, tier badge, gauge, metric card, trend chart card
 * (Recharts), deployment signal card, state panels, and loading skeleton.
 * Props only; no data fetching in this file.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Minus,
  Network,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  LabelList,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import {
  ACCENT_COLORS,
  DORA_METRIC_META,
  findGapRuns,
  formatLongDate,
  formatNumber,
  formatRelativeTime,
  formatShortDate,
  formatValueWithUnit,
  gapBand,
  hasAnyValue,
  signalLabel,
  toChartRows,
  unitLabel,
  unitSuffix,
  type ChartRow,
} from "@/lib/dora/dora-utils";
import {
  DORA_TIMEFRAMES,
  type DoraEmptyVariant,
  type DoraGauge,
  type DoraMetricViewModel,
  type DoraSignalState,
  type DoraSyncStatus,
  type DoraTier,
  type DoraTimeframe,
  type DoraTrendViewModel,
  type DoraUnit,
  type DoraVerification,
} from "../../types/dora";

// ==========================================================================
// Page header and filters
// ==========================================================================

const VERIFICATION_UI = {
  verified: {
    label: "DORA VERIFIED",
    className:
      "border-[rgba(0,229,153,0.3)] bg-[rgba(0,229,153,0.1)] text-[#00e599]",
    dot: "bg-[#00e599]",
  },
  unverified: {
    label: "UNVERIFIED",
    className:
      "border-[rgba(251,191,36,0.3)] bg-[rgba(251,191,36,0.08)] text-[#fbbf24]",
    dot: "bg-[#fbbf24]",
  },
  unavailable: {
    label: "VERIFICATION UNAVAILABLE",
    className: "border-[#252b3b] bg-[#161b26] text-[#7c839b]",
    dot: "bg-[#4a5168]",
  },
} as const;

export function DoraPageHeader({
  verification,
}: {
  verification: DoraVerification;
}) {
  const ui = VERIFICATION_UI[verification.status];
  return (
    <header className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-[24px] font-semibold leading-[32px] tracking-[-0.4px] text-[#e2e8f0]">
          Delivery Analytics
        </h1>
        <span
          data-testid="dora-verification"
          data-status={verification.status}
          title={
            verification.detail ??
            (verification.status === "unavailable"
              ? "The backend has not reported a verification status."
              : undefined)
          }
          className={cn(
            "inline-flex items-center gap-[6px] rounded-full border px-[10px] py-[3px] font-mono text-[10px] font-semibold uppercase tracking-[0.06em]",
            ui.className,
          )}
        >
          <span
            className={cn("size-[6px] rounded-full", ui.dot)}
            aria-hidden="true"
          />
          {ui.label}
        </span>
      </div>
      <p className="text-[13px] text-[#7c839b]">
        Engineering delivery performance, architectural velocity, and DORA
        telemetry benchmarks.
      </p>
    </header>
  );
}

export interface ProjectOption {
  id: string;
  name: string;
  repoCount: number;
}

export interface DataStatus {
  label: string;
  tone: "ok" | "muted" | "warn" | "error";
}

const STATUS_TONE: Record<DataStatus["tone"], string> = {
  ok: "text-[#a4a8b8]",
  muted: "text-[#7c839b]",
  warn: "text-[#fbbf24]",
  error: "text-[#ff6b6b]",
};

interface DoraFiltersProps {
  projects: ProjectOption[];
  selectedProjectId: string | null;
  onProjectChange: (projectId: string) => void;
  timeframe: DoraTimeframe;
  onTimeframeChange: (timeframe: DoraTimeframe) => void;
  dataStatus: DataStatus;
}

const LABEL =
  "font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-[#7c839b]";

export function DoraFilters({
  projects,
  selectedProjectId,
  onProjectChange,
  timeframe,
  onTimeframeChange,
  dataStatus,
}: DoraFiltersProps) {
  const selected = projects.find((p) => p.id === selectedProjectId) ?? null;

  const [isProjectOpen, setIsProjectOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsProjectOpen(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  return (
    <div
      data-testid="dora-filters"
      className="flex flex-wrap items-center justify-between gap-x-8 gap-y-3 rounded-[12px] border border-[#1f2433] bg-[#131822] px-4 py-3"
    >
      {/* Project selector */}
      <div className="flex items-center gap-4">
        <label className={LABEL}>Project</label>

        <div ref={dropdownRef} className="relative">
          <button
            id="dora-project-select"
            data-testid="dora-project-select"
            type="button"
            aria-haspopup="listbox"
            aria-expanded={isProjectOpen}
            onClick={() => setIsProjectOpen((prev) => !prev)}
            className={cn(
              "flex h-[44px] min-w-[300px] items-center gap-3 rounded-[11px] border px-3 transition-all",
              "border-[#252b3b] bg-[#1b202b]",
              "hover:border-[#374151]",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
              isProjectOpen && "border-[#00e599]/50",
            )}
          >
            <Network
              className="size-[20px] shrink-0 text-[#22d3ee]"
              aria-hidden="true"
            />

            <span className="min-w-0 flex-1 truncate text-left text-[14px] font-medium text-[#e2e8f0]">
              {selected?.name ?? "Select project"}
            </span>

            {selected && (
              <span className="shrink-0 rounded-[5px] bg-[#303642] px-2 py-1 font-mono text-[11px] text-[#22d3ee]">
                {selected.repoCount} repos
              </span>
            )}

            <ChevronDown
              className={cn(
                "size-[16px] shrink-0 text-[#a4a8b8] transition-transform duration-200",
                isProjectOpen && "rotate-180",
              )}
              aria-hidden="true"
            />
          </button>

          {isProjectOpen && (
            <div
              role="listbox"
              aria-label="Select project"
              className="absolute left-0 top-[calc(100%+8px)] z-50 min-w-full overflow-hidden rounded-[11px] border border-[#303746] bg-[#1b202b] p-1 shadow-[0_12px_32px_rgba(0,0,0,0.45)]"
            >
              {projects.map((project) => {
                const active = project.id === selectedProjectId;

                return (
                  <button
                    key={project.id}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => {
                      onProjectChange(project.id);
                      setIsProjectOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-[7px] px-3 py-2.5 text-left transition-colors",
                      active ? "bg-[#303642]" : "hover:bg-[#252c39]",
                    )}
                  >
                    <Network
                      className="size-[17px] shrink-0 text-[#22d3ee]"
                      aria-hidden="true"
                    />

                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[#e2e8f0]">
                      {project.name}
                    </span>

                    <span className="shrink-0 rounded-[5px] bg-[#303642] px-2 py-1 font-mono text-[10px] text-[#22d3ee]">
                      {project.repoCount} repos
                    </span>

                    {active && (
                      <span className="size-1.5 shrink-0 rounded-full bg-[#00e599]" />
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Timeframe */}
      <div className="flex items-center gap-3">
        <span id="dora-timeframe-label" className={LABEL}>
          Timeframe
        </span>

        <div
          role="group"
          aria-labelledby="dora-timeframe-label"
          className="flex rounded-[8px] border border-[#252b3b] bg-[#0f131d] p-[3px]"
        >
          {DORA_TIMEFRAMES.map((days) => {
            const active = days === timeframe;

            return (
              <button
                key={days}
                type="button"
                data-testid={`dora-timeframe-${days}d`}
                aria-pressed={active}
                onClick={() => onTimeframeChange(days)}
                className={cn(
                  "h-[28px] rounded-[6px] px-3 font-mono text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                  active
                    ? "bg-[#00e599] text-[#0f131d]"
                    : "text-[#a4a8b8] hover:text-[#e2e8f0]",
                )}
              >
                {days}d
              </button>
            );
          })}
        </div>
      </div>

      {/* Data status */}
      <div className="flex items-center gap-3">
        <span className={LABEL}>Data status</span>

        <span
          data-testid="dora-data-status"
          role="status"
          className={cn(
            "inline-flex h-[34px] items-center gap-2 rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-3 font-mono text-[11px]",
            STATUS_TONE[dataStatus.tone],
          )}
        >
          <RefreshCw className="size-3" aria-hidden="true" />
          {dataStatus.label}
        </span>
      </div>
    </div>
  );
}

// ==========================================================================
// Tier badge
// ==========================================================================

/** Colors are presentation only; the tier itself is always backend-provided. */
export const TIER_COLORS: Record<DoraTier, string> = {
  elite: "#00e599",
  high: "#22d3ee",
  medium: "#fbbf24",
  low: "#ff4d4d",
};

const TIER_CLASSES: Record<DoraTier, string> = {
  elite: "bg-[#00e599] text-[#0f131d]",
  high: "bg-[#22d3ee] text-[#0f131d]",
  medium: "bg-[#fbbf24] text-[#0f131d]",
  low: "bg-[#ff4d4d] text-[#0f131d]",
};

export function TierBadge({ tier }: { tier: DoraTier }) {
  return (
    <span
      data-testid="dora-tier-badge"
      className={cn(
        "inline-flex shrink-0 items-center rounded-[4px] px-[7px] py-[3px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.06em]",
        TIER_CLASSES[tier],
      )}
    >
      {tier}
    </span>
  );
}

// ==========================================================================
// Gauge (presentational)
// ==========================================================================

const CX = 60;
const CY = 58;
const R = 46;
const START_ANGLE = 210;
const SWEEP = 240;

function polar(angleDeg: number, radius = R) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: CX + radius * Math.cos(rad), y: CY - radius * Math.sin(rad) };
}

function arcPath(fromFraction: number, toFraction: number): string {
  const p0 = polar(START_ANGLE - SWEEP * fromFraction);
  const p1 = polar(START_ANGLE - SWEEP * toFraction);
  const large = SWEEP * (toFraction - fromFraction) > 180 ? 1 : 0;
  return `M ${p0.x} ${p0.y} A ${R} ${R} 0 ${large} 1 ${p1.x} ${p1.y}`;
}

const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

interface DoraGaugeProps {
  value: number;
  gauge: DoraGauge;
  color: string;
  ariaLabel: string;
}

/**
 * Purely presentational. The range (min/max), thresholds and labels all come from
 * the backend; if the range is invalid nothing is drawn.
 */
export function DoraGaugeChart({
  value,
  gauge,
  color,
  ariaLabel,
}: DoraGaugeProps) {
  const span = gauge.max - gauge.min;
  if (!Number.isFinite(span) || span <= 0) return null;

  const fraction = clamp01((value - gauge.min) / span);

  return (
    <div
      data-testid="dora-gauge"
      role="img"
      aria-label={ariaLabel}
      className="relative mx-auto h-[92px] w-[120px] shrink-0"
    >
      <svg viewBox="0 0 120 92" className="size-full" aria-hidden="true">
        <path
          d={arcPath(0, 1)}
          fill="none"
          stroke="#1f2433"
          strokeWidth={8}
          strokeLinecap="round"
        />
        {fraction > 0 && (
          <path
            d={arcPath(0, fraction)}
            fill="none"
            stroke={color}
            strokeWidth={8}
            strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 4px ${color}66)` }}
          />
        )}
        {gauge.thresholds.map((t, i) => {
          const f = clamp01((t.value - gauge.min) / span);
          const angle = START_ANGLE - SWEEP * f;
          const a = polar(angle, R - 8);
          const b = polar(angle, R + 5);
          return (
            <line
              key={`${t.value}-${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="#7c839b"
              strokeWidth={1}
              opacity={0.8}
            />
          );
        })}
      </svg>
      {(gauge.label || gauge.caption) && (
        <div className="pointer-events-none absolute inset-x-0 top-[40px] flex flex-col items-center gap-[2px]">
          {gauge.label && (
            <span
              className="font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.05em]"
              style={{ color }}
            >
              {gauge.label}
            </span>
          )}
          {gauge.caption && (
            <span className="font-mono text-[9px] leading-none text-[#7c839b]">
              {gauge.caption}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ==========================================================================
// Metric card
// ==========================================================================

const FALLBACK_COLOR = "#22d3ee";

const EMPTY_MESSAGES: Record<DoraEmptyVariant, string> = {
  not_configured: "Deployment signal not configured",
  awaiting_telemetry: "No telemetry received yet",
  no_data: "No data available for this period",
};

function Comparison({ metric }: { metric: DoraMetricViewModel }) {
  const { comparison, value, unit } = metric;
  if (!comparison || value === null) return null;

  const delta = value - comparison.previousValue;
  const percent =
    comparison.previousValue !== 0
      ? Math.abs((delta / comparison.previousValue) * 100)
      : null;
  const color =
    comparison.sentiment === "improved"
      ? "text-[#00e599]"
      : comparison.sentiment === "regressed"
        ? "text-[#ff6b6b]"
        : "text-[#7c839b]";
  const Icon = delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : Minus;

  return (
    <div
      data-testid="dora-comparison"
      className={cn(
        "flex shrink-0 items-start gap-1 font-mono text-[11px] font-semibold leading-[14px]",
        color,
      )}
    >
      <Icon className="mt-px size-3 shrink-0" aria-hidden="true" />
      <span className="text-right">
        {delta === 0 ? (
          "No change"
        ) : (
          <>
            {formatNumber(Math.abs(delta))}
            {unitSuffix(unit)}
            {percent !== null && ` (${formatNumber(percent, 0)}%)`}
          </>
        )}
        {comparison.periodLabel && (
          <span className="block font-normal text-[#7c839b]">
            {comparison.periodLabel}
          </span>
        )}
      </span>
    </div>
  );
}

export function MetricCard({
  metric,
  emptyVariant,
}: {
  metric: DoraMetricViewModel;
  emptyVariant: DoraEmptyVariant;
}) {
  const meta = DORA_METRIC_META[metric.id];
  const hasValue = metric.value !== null;
  const color = metric.tier ? TIER_COLORS[metric.tier] : FALLBACK_COLOR;

  return (
    <article
      data-testid={`dora-metric-card-${metric.id}`}
      aria-label={meta.title}
      className="flex min-w-0 flex-col gap-4 rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5 shadow-[0px_1px_2px_rgba(0,0,0,0.2)]"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="max-w-[140px] text-[13px] font-medium leading-[18px] text-[#cbd5e1]">
          {meta.title}
        </h3>
        {hasValue && metric.tier ? (
          <TierBadge tier={metric.tier} />
        ) : !hasValue && metric.unavailableLabel ? (
          <span className="inline-flex shrink-0 items-center rounded-[4px] border border-[#252b3b] px-[7px] py-[3px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.06em] text-[#7c839b]">
            {metric.unavailableLabel}
          </span>
        ) : null}
      </div>

      {hasValue ? (
        <>
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 items-baseline gap-2">
              <span
                data-testid="dora-metric-value"
                className="font-mono text-[36px] font-medium leading-[40px] tracking-[-0.02em] text-[#e2e8f0]"
              >
                {formatNumber(metric.value as number)}
              </span>
              <span className="text-[13px] text-[#7c839b]">
                {unitLabel(metric.unit)}
              </span>
            </div>
            <Comparison metric={metric} />
          </div>

          {metric.gauge && (
            <DoraGaugeChart
              value={metric.value as number}
              gauge={metric.gauge}
              color={color}
              ariaLabel={`${meta.title} gauge`}
            />
          )}
        </>
      ) : (
        <div data-testid="dora-metric-no-data" className="flex flex-col gap-2">
          <span className="font-mono text-[28px] font-medium leading-[40px] text-[#3b4257]">
            No data
          </span>
          <p className="rounded-[8px] border border-[#1f2433] bg-[#131822] px-3 py-2 text-[11px] leading-[16px] text-[#7c839b]">
            {metric.unavailableReason ?? EMPTY_MESSAGES[emptyVariant]}
          </p>
        </div>
      )}

      <p
        className={cn(
          "mt-auto text-[12px] leading-[17px]",
          hasValue ? "text-[#a4a8b8]" : "text-[#4a5168]",
        )}
      >
        {meta.description}
      </p>
    </article>
  );
}

// ==========================================================================
// Trend chart card
// ==========================================================================

const AXIS_TICK = { fill: "#7c839b", fontSize: 10 } as const;
const GRID_STROKE = "rgba(124,131,155,0.14)";

interface TooltipContentProps {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: ChartRow }>;
  unit: DoraUnit;
}

function TrendTooltip({ active, payload, unit }: TooltipContentProps) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-3 py-2 shadow-lg">
      <p className="font-mono text-[10px] text-[#7c839b]">
        {formatLongDate(row.date)}
      </p>
      <p className="font-mono text-[12px] font-semibold text-[#e2e8f0]">
        {row.value === null ? "No data" : formatValueWithUnit(row.value, unit)}
      </p>
      {row.note && (
        <p className="mt-0.5 font-mono text-[10px] text-[#7c839b]">
          {row.note}
        </p>
      )}
    </div>
  );
}

const EMPTY_COPY: Record<DoraEmptyVariant, { badge: string; message: string }> =
  {
    not_configured: {
      badge: "PENDING CONFIGURATION",
      message:
        "No telemetry data available yet. DORA metrics will appear once a verified production deployment signal is configured and events are received.",
    },
    awaiting_telemetry: {
      badge: "AWAITING TELEMETRY",
      message:
        "A deployment signal is configured, but no telemetry has been received for the selected timeframe yet.",
    },
    no_data: {
      badge: "NO DATA",
      message:
        "No data is available for this metric in the selected timeframe.",
    },
  };

export function TrendChartCard({
  trend,
  emptyVariant,
}: {
  trend: DoraTrendViewModel;
  emptyVariant: DoraEmptyVariant;
}) {
  const meta = DORA_METRIC_META[trend.id];
  const color = ACCENT_COLORS[meta.accent];
  const gradientId = `dora-grad-${useId().replace(/:/g, "")}`;

  const hasData = hasAnyValue(trend.points);
  const isMttr = trend.id === "mttr";
  const allRows = toChartRows(trend.points);

  // MTTR nulls normally mean "no incident was recorded that day", not a
  // telemetry outage. Keep only measured values and explicitly marked outages.
  // This prevents every incident-free day from becoming a shaded "No telemetry" band.
  const rows = isMttr
    ? allRows.filter((row) => row.value !== null || row.note === "No telemetry")
    : allRows;

  const chartPoints = isMttr
    ? trend.points.filter(
        (point) => point.value !== null || point.note === "No telemetry",
      )
    : trend.points;

  const scale = meta.chartKind === "bar" ? "band" : "point";
  const gapRuns = isMttr
    ? findGapRuns(chartPoints).filter((run) =>
        chartPoints
          .slice(run.startIndex, run.endIndex + 1)
          .some((point) => point.note === "No telemetry"),
      )
    : findGapRuns(chartPoints);

  const gapAreas = gapRuns.map((run) => {
    const { x1, x2 } = gapBand(rows, run, scale);
    return (
      <ReferenceArea
        key={`gap-${run.startIndex}`}
        x1={x1}
        x2={x2}
        fill="#0f131d"
        fillOpacity={0.9}
        stroke="rgba(124,131,155,0.25)"
        strokeDasharray="3 3"
        label={
          isMttr || run.endIndex > run.startIndex
            ? {
                value: isMttr ? "No telemetry" : "No data",
                position: "insideTop",
                fill: "#7c839b",
                fontSize: 10,
              }
            : undefined
        }
      />
    );
  });

  const threshold = trend.threshold;
  const thresholdLine = threshold ? (
    <ReferenceLine
      key="threshold"
      y={threshold.value}
      stroke="#ff4d4d"
      strokeDasharray="4 4"
      label={
        threshold.label
          ? {
              value: threshold.label,
              position: "insideTopRight",
              fill: "#ff6b6b",
              fontSize: 10,
            }
          : undefined
      }
    />
  ) : null;

  const suffix = unitSuffix(trend.unit);
  const mttrMax = Math.max(
    60,
    Math.ceil(
      Math.max(0, ...trend.points.map((point) => point.value ?? 0)) / 30,
    ) * 30,
  );
  const yDomain: [number, number | ((dataMax: number) => number)] = isMttr
    ? [0, mttrMax]
    : [0, (dataMax: number) => Math.max(dataMax, threshold?.value ?? 0)];
  const yTicks = isMttr
    ? Array.from({ length: mttrMax / 30 + 1 }, (_, index) => index * 30)
    : undefined;

  const axes = [
    <CartesianGrid key="grid" stroke={GRID_STROKE} vertical={false} />,
    <XAxis
      key="x"
      dataKey="date"
      tickFormatter={formatShortDate}
      tick={AXIS_TICK}
      axisLine={false}
      tickLine={false}
      minTickGap={28}
      interval="preserveStartEnd"
    />,
    <YAxis
      key="y"
      width={40}
      tick={AXIS_TICK}
      axisLine={false}
      tickLine={false}
      tickCount={isMttr ? undefined : 4}
      ticks={yTicks}
      domain={yDomain}
      tickFormatter={(v: number) => `${v}${suffix}`}
    />,
    <Tooltip
      key="tip"
      content={<TrendTooltip unit={trend.unit} />}
      cursor={
        meta.chartKind === "bar"
          ? { fill: "rgba(255,255,255,0.03)" }
          : { stroke: "#3b4257" }
      }
    />,
  ];
  const margin = { top: 8, right: 8, bottom: 0, left: 0 };
  const dot = { r: 2.5, fill: color, strokeWidth: 0 };

  return (
    <section
      data-testid={`dora-trend-card-${trend.id}`}
      aria-label={`${meta.chartTitle} daily trend`}
      className="flex min-w-0 flex-col gap-4 rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5"
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[15px] font-medium leading-[20px] text-[#e2e8f0]">
            {meta.chartTitle}
          </h3>
          <p className="mt-0.5 font-mono text-[10px] leading-[14px] text-[#7c839b]">
            {hasData
              ? (trend.subtitle ?? "Daily trend")
              : "No incident or telemetry data received"}
          </p>
        </div>
        {hasData && trend.targetLabel ? (
          <span className="shrink-0 rounded-[4px] border border-[#252b3b] bg-[#131822] px-2 py-[3px] font-mono text-[10px] uppercase text-[#22d3ee]">
            {trend.targetLabel}
          </span>
        ) : !hasData ? (
          <span className="shrink-0 rounded-[4px] border border-[#252b3b] px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.05em] text-[#7c839b]">
            {EMPTY_COPY[emptyVariant].badge}
          </span>
        ) : null}
      </header>

      {hasData ? (
        <div
          role="img"
          aria-label={`${meta.chartTitle} chart over ${trend.points.length} days`}
          className="h-[220px] w-full min-w-0"
        >
          <ResponsiveContainer width="100%" height="100%">
            {meta.chartKind === "bar" ? (
              <BarChart data={rows} margin={margin}>
                {axes}
                {gapAreas}
                {thresholdLine}
                <Bar
                  dataKey="value"
                  fill={color}
                  radius={[2, 2, 0, 0]}
                  maxBarSize={18}
                  isAnimationActive={false}
                />
              </BarChart>
            ) : meta.chartKind === "area" ? (
              <AreaChart data={rows} margin={margin}>
                <defs>
                  <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                    <stop offset="100%" stopColor={color} stopOpacity={0} />
                  </linearGradient>
                </defs>
                {axes}
                {gapAreas}
                {thresholdLine}
                <Area
                  type="linear"
                  dataKey="value"
                  stroke={color}
                  strokeWidth={2}
                  fill={`url(#${gradientId})`}
                  connectNulls={false}
                  dot={dot}
                  isAnimationActive={false}
                />
              </AreaChart>
            ) : (
              <LineChart data={rows} margin={margin}>
                {axes}
                {gapAreas}
                {thresholdLine}
                <Line
                  type="linear"
                  dataKey="value"
                  stroke={color}
                  strokeWidth={isMttr ? 1.5 : 2}
                  strokeDasharray={isMttr ? "3 5" : "4 3"}
                  connectNulls={false}
                  dot={
                    isMttr
                      ? {
                          r: 4.5,
                          fill: "#22d3ee",
                          stroke: "#0f131d",
                          strokeWidth: 2,
                        }
                      : { ...dot, r: 3 }
                  }
                  activeDot={
                    isMttr
                      ? {
                          r: 5,
                          fill: "#22d3ee",
                          stroke: "#0f131d",
                          strokeWidth: 2,
                        }
                      : undefined
                  }
                  isAnimationActive={false}
                >
                  {isMttr && (
                    <LabelList
                      dataKey="value"
                      position="top"
                      offset={10}
                      fill="#22d3ee"
                      fontSize={11}
                      fontFamily="monospace"
                      formatter={(value: number | string) => `${value}m`}
                    />
                  )}
                </Line>
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      ) : (
        <div
          data-testid="dora-trend-empty"
          className={cn(
            "flex min-h-[220px] flex-col items-center justify-center gap-3 rounded-[10px] border border-[#1f2433] bg-[#131822] px-6 text-center",
          )}
        >
          <Activity className="size-5 text-[#3b4257]" aria-hidden="true" />
          <p className="max-w-[360px] text-[12px] leading-[18px] text-[#a4a8b8]">
            {trend.emptyReason ?? EMPTY_COPY[emptyVariant].message}
          </p>
        </div>
      )}
    </section>
  );
}

// ==========================================================================
// Deployment signal card
// ==========================================================================

const SYNC_TEXT: Record<DoraSyncStatus, { text: string; dot: string }> = {
  healthy: { text: "Sync healthy", dot: "bg-[#00e599]" },
  delayed: { text: "Sync delayed", dot: "bg-[#fbbf24]" },
  failing: { text: "Sync failing", dot: "bg-[#ff4d4d]" },
  never_synced: { text: "Never synced", dot: "bg-[#7c839b]" },
};

interface DeploymentSignalCardProps {
  state: DoraSignalState;
  canConfigure: boolean;
  nowMs: number;
  onRetry: () => void;
}

const SHELL =
  "flex w-full items-center gap-3 rounded-[10px] border border-[#1f2433] bg-[#161b26] px-4 py-3 md:gap-4";

export function DeploymentSignalCard({
  state,
  canConfigure,
  nowMs,
  onRetry,
}: DeploymentSignalCardProps) {
  if (state.status === "loading") {
    return (
      <section
        data-testid="dora-signal-loading"
        aria-busy="true"
        className={cn(SHELL, "animate-pulse")}
      >
        <div className="size-9 shrink-0 rounded-[8px] bg-[#1f2433]" />
        <div className="h-8 flex-1 rounded-[6px] bg-[#1f2433]" />
        <div className="h-8 w-28 shrink-0 rounded-[6px] bg-[#1f2433]" />
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section
        data-testid="dora-signal-error"
        className={cn(SHELL, "flex-wrap")}
      >
        <ShieldAlert
          className="size-5 shrink-0 text-[#ff6b6b]"
          aria-hidden="true"
        />

        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-semibold text-[#e2e8f0]">
            Unable to load deployment signal
          </p>
          <p className="text-[11px] text-[#7c839b]">{state.message}</p>
        </div>

        <button
          type="button"
          onClick={onRetry}
          className="h-[30px] shrink-0 rounded-[6px] border border-[#252b3b] bg-[#1f2433] px-3 text-[11px] font-medium text-[#e2e8f0] hover:border-[#374151]"
        >
          Retry
        </button>
      </section>
    );
  }

  const { signal } = state;
  const sync = signal.sync;

  const syncMeta = sync?.status ? SYNC_TEXT[sync.status] : null;

  const lastSynced = sync?.lastSyncedAt
    ? formatRelativeTime(sync.lastSyncedAt, nowMs)
    : null;

  return (
    <section
      data-testid="dora-signal-card"
      data-configured={signal.configured}
      aria-label="Authoritative deployment signal"
      className={cn(SHELL, "flex-wrap md:flex-nowrap")}
    >
      {/* Icon */}
      <div className="flex size-9 shrink-0 items-center justify-center rounded-[8px] border border-[#252b3b] bg-[#0f131d]">
        {signal.configured ? (
          <ShieldCheck
            className="size-[18px] text-[#00e599]"
            aria-hidden="true"
          />
        ) : (
          <ShieldAlert
            className="size-[18px] text-[#7c839b]"
            aria-hidden="true"
          />
        )}
      </div>

      {/* Main information */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "rounded-[4px] px-[7px] py-[3px] font-mono text-[9px] font-bold uppercase leading-none tracking-[0.06em]",
              signal.configured
                ? "bg-[#00e599] text-[#0f131d]"
                : "border border-[#252b3b] text-[#fbbf24]",
            )}
          >
            {signal.configured ? "Configured" : "Pending configuration"}
          </span>

          <h3 className="text-[12px] font-semibold text-[#e2e8f0]">
            {signal.configured
              ? `${signalLabel(signal.type)} - Configured`
              : "Awaiting Deployment Integration"}
          </h3>
        </div>

        <p className="mt-1 max-w-[620px] text-[10px] leading-[15px] text-[#a4a8b8]">
          {signal.configured
            ? "Production deployment events are verified using cryptographic provenance attestations and ArgoCD sync-completion webhooks. Merge actions to main are strictly isolated from delivery frequency counts to guarantee metric integrity."
            : "No verified production deployment signal has been configured. Connect GitHub Deployments, ArgoCD, or the Direct Deployment API."}
        </p>
      </div>

      {/* Sync status */}
      {signal.configured && (
        <div
          data-testid="dora-signal-sync"
          className="flex shrink-0 flex-col items-start gap-1 md:items-end"
        >
          {syncMeta && (
            <span className="flex items-center gap-1.5 font-mono text-[9px] font-semibold text-[#00e599]">
              <span className={cn("size-[6px] rounded-full", syncMeta.dot)} />
              {syncMeta.text}
            </span>
          )}

          {lastSynced && (
            <span className="font-mono text-[9px] text-[#7c839b]">
              Last sync {lastSynced}
            </span>
          )}

          {sync?.detail && (
            <span className="font-mono text-[9px] text-[#7c839b]">
              {sync.detail}
            </span>
          )}
        </div>
      )}

      {/* Configure button */}
      {canConfigure ? (
        <Link
          href="/projects"
          data-testid="dora-configure-signals"
          className="inline-flex h-[30px] shrink-0 items-center justify-center rounded-[6px] bg-[#00e599] px-3 text-[10px] font-semibold text-[#0f131d] transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
        >
          Configure signals
        </Link>
      ) : (
        <p className="max-w-[150px] text-[10px] leading-[14px] text-[#7c839b]">
          Ask a workspace administrator to configure the deployment signal.
        </p>
      )}
    </section>
  );
}

// ==========================================================================
// State panels
// ==========================================================================

type Tone = "neutral" | "warning" | "error";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-[#252b3b] bg-[#161b26] text-[#7c839b]",
  warning:
    "border-[rgba(251,191,36,0.3)] bg-[rgba(251,191,36,0.08)] text-[#fbbf24]",
  error:
    "border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.1)] text-[#ff6b6b]",
};

export function DoraStatePanel({
  icon,
  title,
  message,
  tone = "neutral",
  children,
  testId,
}: {
  icon: ReactNode;
  title: string;
  message: string;
  tone?: Tone;
  children?: ReactNode;
  testId: string;
}) {
  return (
    <section
      data-testid={testId}
      className="flex min-h-[320px] w-full items-center justify-center rounded-[12px] border border-[#1f2433] bg-[#161b26] p-8"
    >
      <div className="flex max-w-[460px] flex-col items-center gap-3 text-center">
        <div
          className={cn(
            "flex size-[44px] items-center justify-center rounded-full border",
            TONE_CLASSES[tone],
          )}
        >
          {icon}
        </div>
        <h2 className="text-[16px] font-semibold text-[#e2e8f0]">{title}</h2>
        <p className="text-[13px] leading-[20px] text-[#7c839b]">{message}</p>
        {children}
      </div>
    </section>
  );
}

export function MockDataBanner({ scenario }: { scenario: string }) {
  return (
    <div
      data-testid="dora-mock-banner"
      role="status"
      className="flex flex-wrap items-center gap-2 rounded-[8px] border border-[rgba(251,191,36,0.4)] bg-[rgba(251,191,36,0.08)] px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-[0.05em] text-[#fbbf24]"
    >
      <span>Mock data, not real telemetry</span>
      <span className="font-normal normal-case text-[#a4a8b8]">
        Scenario: {scenario} (development only)
      </span>
    </div>
  );
}

// ==========================================================================
// Loading skeleton
// ==========================================================================

export function DoraSkeleton() {
  return (
    <div
      data-testid="dora-loading-state"
      aria-busy="true"
      aria-label="Loading delivery analytics"
      className="flex flex-col gap-6 animate-pulse"
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="flex h-[280px] flex-col gap-4 rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5"
          >
            <div className="h-[14px] w-[120px] rounded-[4px] bg-[#1f2433]" />
            <div className="h-[36px] w-[96px] rounded-[6px] bg-[#1f2433]" />
            <div className="mx-auto h-[92px] w-[120px] rounded-full bg-[rgba(31,36,51,0.6)]" />
            <div className="h-[12px] w-full rounded-[4px] bg-[rgba(31,36,51,0.5)]" />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="h-[320px] rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5"
          >
            <div className="h-[16px] w-[180px] rounded-[4px] bg-[#1f2433]" />
            <div className="mt-6 h-[220px] rounded-[8px] bg-[rgba(31,36,51,0.4)]" />
          </div>
        ))}
      </div>
    </div>
  );
}
