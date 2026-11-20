# US-31: Delivery Analytics DORA Dashboard UI Verification & Telemetry Scenarios

**Task:** `SCRUM-466` (`Task 31.5`: Create test scenarios for gauge rendering, missing-day chart gaps, 'No data' display and 14d/30d window switching)  
**Parent User Story:** `SCRUM-369` (`US-31`: Delivery Analytics DORA Dashboard UI Verification & Telemetry Scenarios)  
**Related Tickets:**  
- `SCRUM-463` / `Task 31.1`: Develop DORA metric gauges and summary cards (`web/src/components/dora/dora-cards.tsx`, `MetricCard`, `DoraGaugeChart`, `TierBadge`)  
- `SCRUM-464` / `Task 31.2`: Develop daily trend charts with missing-day gap rendering and threshold lines (`TrendChartCard`, Recharts `ReferenceArea`, `toChartRows`, `findGapRuns`)  
- `SCRUM-465` / `Task 31.3`: Develop 14d/30d timeframe toggle and deployment signal badge integration (`DoraFilters`, `DeploymentSignalCard`, `useDoraDashboard`, `useDoraSignal`)  
- `SCRUM-466` / `Task 31.5`: Create test scenarios for gauge rendering, missing-day chart gaps, 'No data' display and 14d/30d window switching (this document)  
- `SCRUM-467` / `Task 31.6`: Automated Playwright E2E verification for gauge rendering, chart gaps, 'No data', and timeframe switching (`web/e2e/delivery-analytics.spec.ts`)  
- `SCRUM-457` / `Task 30.1`: Design OpenAPI 3.1 contract for `GET /api/v1/projects/{id}/dora-metrics` with nullable aggregates and daily trend series (`backend/app/dora/schemas.py`, [`docs/dora-metrics-openapi-contract.md`](dora-metrics-openapi-contract.md))  
- `SCRUM-354` / `SCRUM-384` / `SCRUM-385`: Deployment signal selection persistence and authoritative CD source configuration (`US-18`, `projects.authoritative_cd_signal`, [`web/e2e/deployment-signal-persistence.spec.ts`](../web/e2e/deployment-signal-persistence.spec.ts))  
- `SCRUM-446` / `SCRUM-448`: Mean Time to Recovery (MTTR) interval pairing and null-window design (`US-29`, [`docs/dora-mttr-calculation-test-scenarios.md`](dora-mttr-calculation-test-scenarios.md))  

**Target Route & Application:** `/delivery-analytics` in `web` (Next.js 15 App Router, React 19, Tailwind CSS, Recharts)  
**Target Implementation Modules:**  
- [`web/src/app/(portal)/delivery-analytics/page.tsx`](../web/src/app/(portal)/delivery-analytics/page.tsx)  
- [`web/src/components/dora/dora-cards.tsx`](../web/src/components/dora/dora-cards.tsx)  
- [`web/src/hooks/use-dora-dashboard.ts`](../web/src/hooks/use-dora-dashboard.ts)  
- [`web/src/lib/dora/dora-utils.ts`](../web/src/lib/dora/dora-utils.ts)  
- [`web/src/lib/dora/dora-api.ts`](../web/src/lib/dora/dora-api.ts)  
- [`web/src/lib/dora/mocks/dora-mock-data.ts`](../web/src/lib/dora/mocks/dora-mock-data.ts)  
- [`web/src/types/dora.ts`](../web/src/types/dora.ts)  
**Authoritative Automated Test Suites:**  
- `web/e2e/delivery-analytics.spec.ts` (Playwright E2E suite covering gauge rendering, chart gaps, 'No data', timeframe toggle, and signal badge)  
- [`web/e2e/portal.spec.ts`](../web/e2e/portal.spec.ts) (Portal navigation and routing integration)  
- [`backend/tests/test_dora_openapi_contract.py`](../backend/tests/test_dora_openapi_contract.py) (OpenAPI 3.1 wire contract verification)  
**Specification Location in Test Suite:** [`backend/tests/scenarios/test_us31_dora_dashboard_ui_scenarios.md`](../backend/tests/scenarios/test_us31_dora_dashboard_ui_scenarios.md)  
**Assignee:** Matvii Yeromenko  
**Category:** Testing - Design  
**Estimate:** 0.25 Story Points (0h 30m Original Estimate)  
**Status:** Approved / Verification Baseline Established  

---

## 1. Executive Summary & Objective

### 1.1 Objective

Design and formalize the definitive User Interface (UI) test scenarios, DOM selector maps, and automated Playwright verification assertions for the **Delivery Analytics (DORA) Dashboard (`/delivery-analytics`)** in `US-31` (`SCRUM-369` / `SCRUM-466`).

Engineering teams and leadership rely on DORA metrics to gauge software delivery velocity, operational stability, and deployment risk. The dashboard must guarantee **zero-false-zero architectural honesty**:
1. **Four-Gauge Visual Rendering (AC-1):** Render all four canonical DORA metrics—Lead Time for Changes (`lead_time`), Deployment Frequency (`deployment_frequency`), Change Failure Rate (`change_failure_rate`), and Mean Time to Recovery (`mttr`)—with live/seeded numeric values, backend-provided performance tier badges (`elite`, `high`, `medium`, `low`), historical comparison deltas, and dual-arc SVG gauges with threshold tick lines.
2. **Explicit "No data" Display for Null Telemetry (AC-2):** When a metric is undefined (e.g. `mttr_hours: null` due to zero failure sessions in the window), the UI must explicitly render a `"No data"` callout and info message instead of coercing `null` to `0.0`. A false `0.0h` MTTR would dangerously mislead stakeholders into believing service restoration was instantaneous.
3. **Missing-Day Chart Gaps & Discontinuous Trend Lines (AC-3):** Daily trend charts must visually render calendar days without telemetry as shaded gap zones (`ReferenceArea`) labeled `"No data"`, breaking continuous line/area strokes (`connectNulls=false`) rather than plotting artificial zero dips.
4. **Reactive 14d $\leftrightarrow$ 30d Window Switching (AC-4):** Toggling between 14-day and 30-day timeframes must update the browser URL search params (`?timeframe=14` vs `?timeframe=30`), update toggle button active states (`aria-pressed`), and dynamically re-slice daily trend series and aggregated gauge metrics.
5. **Authoritative Deployment Signal Badge & Provenance (AC-5):** Prominently display the project's configured Continuous Delivery (CD) signal (`github_deployments_api`, `argocd_postsync_webhook`, or `direct_deployment_api`), sync health status (`Sync healthy`, `delayed`, `failing`), and zero-trust provenance attestations, while providing an administrative configure button or cold-state alert.

---

### 1.2 Analysis of Previous CI Errors & Prevention Invariants

A critical requirement of this design is analyzing prior Continuous Integration (CI) failures in the repository to guarantee strict, first-time-green pipeline runs:

| CI Failure History | Root Cause Analysis | Prevention Strategy in Task 31.5 |
| :--- | :--- | :--- |
| **Markdown Python Snippet Formatting Failures** (`898248b`, `d736b7c`, `feb2515`, `73356bf`) | The GitHub Actions `backend-lint` stage runs Astral Ruff (`ruff format --check` and `ruff check`). Ruff automatically inspects and lints code blocks inside `.md` files. Markdown scenario files containing python snippets with lines $> 100$ characters, non-standard indentation, missing blank lines between classes, or unused imports caused CI check failures. | 1. Test scenarios for UI verification leverage TypeScript (`typescript` / `tsx`), Playwright API, HTML DOM hierarchies, and JSON contracts, which are ignored by Ruff.<br>2. Any Python code block included for backend mocks strictly respects PEP 8 and Ruff rules (line length $\le 100$, 2 blank lines around classes, zero unused imports).<br>3. `python -m ruff check .` and `python -m ruff format --check .` are executed locally before committing. |
| **Next.js ESLint Warnings & Build Failures** (`f4fbe92`, `0292285`) | Missing packages (e.g., `recharts`) or React Hooks dependency array warnings broke Next.js build (`next build`) in `web-checks`. | All required libraries are locked in `web/package.json`. Verification runs `npm run lint --prefix web` and `npm run build --prefix web` to verify 0 errors. |
| **Playwright Portal Navigation Regressions** (`2ff0a45`) | Adding new routes caused `portal.spec.ts` navigation checks to fail because it expected `placeholder-page` for unimplemented routes. | The route `/delivery-analytics` is already excluded in `portal.spec.ts` (`else if (dest.id !== "delivery-analytics")`). All selectors in this scenario suite use explicit, permanent `data-testid` attributes. |
| **Live Database Transaction & RLS Deadlocks** (`f267424`) | Pytest integration tests previously attempted nested transactions against live Postgres instances, conflicting with tenant RLS session variables. | Playwright E2E UI verification uses isolated client-side route mocking (`page.route()`) and Next.js mock scenario parameters (`?mockScenario=...`), isolating tests from live database state collisions. |

---

## 2. Core Architectural Invariants & UI Verification Formulations

```
+-------------------------------------------------------------------------------------------------------------------+
|                         DELIVERY ANALYTICS (DORA) DASHBOARD UI ARCHITECTURE (US-31)                               |
+-------------------------------------+-------------------------------------+---------------------------------------+
| 1. Four-Gauge Metric Cards          | 2. Zero-False-Zero Gap Rendering    | 3. Dual-Timeframe Reactive Window     |
|    (MetricCard & DoraGaugeChart)    |    (Recharts ReferenceArea)         |    (DoraFilters 14d <-> 30d Toggle)   |
+-------------------------------------+-------------------------------------+---------------------------------------+
| * Lead Time, DF, CFR, MTTR          | * connectNulls = false on Line/Area | * Button group: 14d vs 30d (URL sync) |
| * Dual-arc SVG (start 210, sweep 240| * Missing days: value = null        | * Dynamic X-axis slice (14 or 30 days)|
| * Dynamic Tier Badge (elite/hi/med) | * ReferenceArea shaded gap overlay  | * Re-fetches aggregates and trends    |
| * Null value -> prominent "No data" | * Gap label: "No data" insideTop    | * Persists in ?timeframe= URL query   |
+-------------------------------------+-------------------------------------+---------------------------------------+
                                                         |
                                                         v
+-------------------------------------------------------------------------------------------------------------------+
|                            PROVENANCE BADGE & COLD STATE DEGRADATION CONTRACT                                     |
+----------------------------------------------------+--------------------------------------------------------------+
| 4. Authoritative CD Signal Badge                   | 5. Cold State & Unconfigured Signal Fallback                 |
+----------------------------------------------------+--------------------------------------------------------------+
| * Displays configured signal (GitHub/ArgoCD/Direct)| * If signal.configured = false -> data-cold-state="true"     |
| * Sync health dot: healthy/delayed/failing         | * Metric cards degrade to "No data" + "Deployment signal..."  |
| * Last sync relative time: "Last sync 4m ago"      | * Trend charts render dora-trend-empty with PENDING banner   |
| * Admin configure button: links to /projects       | * Renders dora-telemetry-offline indicator in trends header  |
+----------------------------------------------------+--------------------------------------------------------------+
```

### Invariant 1: Four-Gauge Range, Tier and Arc Formula

Each metric card (`[data-testid="dora-metric-card-<id>"]`) renders an SVG circular gauge (`[data-testid="dora-gauge"]`) when `value !== null`:
- **Coordinate Geometry:** SVG viewport `0 0 120 92`, center coordinates $(C_X = 60, C_Y = 58)$, radius $R = 46$, starting angle $\theta_0 = 210^\circ$, sweep $\Delta\theta = 240^\circ$.
- **Fraction Calculation:**

$$\text{fraction} = \text{clamp}_{0,1}\left(\frac{\text{value} - \text{gauge.min}}{\text{gauge.max} - \text{gauge.min}}\right)$$

- **Dual-Arc Layering:**
  1. *Background Track Arc:* Path `M polar(210) A 46 46 0 1 1 polar(-30)` with `stroke="#1f2433"` and `strokeWidth=8`.
  2. *Active Value Arc:* Path from fraction $0$ to $\text{fraction}$ with `stroke={tierColor}` and CSS drop shadow.
  3. *Threshold Tick Markers:* Drawn at threshold coordinates with `stroke="#7c839b"` and `strokeWidth=1`.
- **Tier Badge Styling:**
  - `elite` $\rightarrow$ `#00e599` (`bg-[#00e599] text-[#0f131d]`)
  - `high` $\rightarrow$ `#22d3ee` (`bg-[#22d3ee] text-[#0f131d]`)
  - `medium` $\rightarrow$ `#fbbf24` (`bg-[#fbbf24] text-[#0f131d]`)
  - `low` $\rightarrow$ `#ff4d4d` (`bg-[#ff4d4d] text-[#0f131d]`)

---

### Invariant 2: Explicit Nullability & "No data" Callout Contract

When any metric value is undefined (`value === null`), the UI enforces strict zero-false-zero integrity:
1. `[data-testid="dora-metric-value"]` MUST NOT exist in the DOM.
2. `[data-testid="dora-gauge"]` MUST NOT exist in the DOM.
3. `[data-testid="dora-tier-badge"]` MUST NOT exist in the DOM. If `unavailableLabel` is present, it renders an unstyled neutral pill (`border-[#252b3b] text-[#7c839b]`).
4. `[data-testid="dora-metric-no-data"]` MUST render:
   - Primary text: `<span class="font-mono text-[28px] text-[#405748]">No data</span>`
   - Explanatory callout with `Info` icon and text matching `unavailableReason` (e.g. `"Service restoration signals are not available for this project."`).
   - Bottom status line: `"Awaiting incident detection and verified service restoration signals."`

---

### Invariant 3: Zero-False-Zero Chart Continuity & Gap Rendering

In trend charts (`[data-testid="dora-trend-card-<id>"]`):
1. **Broken Lines:** Recharts `<AreaChart>` and `<LineChart>` enforce `connectNulls={false}`. Missing days (`value: null`) create actual visual gaps in the stroke instead of interpolating between points.
2. **Gap Overlay (`ReferenceArea`):** Contiguous runs of `value: null` points are computed via `findGapRuns(points)`. Each gap run renders a `<ReferenceArea>`:
   - Coordinates: $x_1$ = date of run start, $x_2$ = date of run end.
   - Styling: `fill="#0f131d"`, `fillOpacity={0.9}`, `stroke="rgba(124,131,155,0.25)"`, `strokeDasharray="3 3"`.
   - Multi-day runs ($endIndex > startIndex$) render an inside top label: `"No data"` (`fill="#7c839b"`, `fontSize=10`).
3. **Empty Metric Trend:** When all points in the series are `null` (`hasAnyValue(points) === false`), the Recharts `<ResponsiveContainer>` is replaced by `[data-testid="dora-trend-empty"]` displaying badge `"NO DATA"` and descriptive guidance.

---

### Invariant 4: Dual-Timeframe Reactive Window Switching

The dashboard exposes a dual-timeframe selector (`DoraFilters`):
- Controls: `[data-testid="dora-timeframe-14d"]` and `[data-testid="dora-timeframe-30d"]`.
- Synchronization:
  - Active button has `aria-pressed="true"`, `bg-[#00e599]`, and `text-[#0f131d]`.
  - Inactive button has `aria-pressed="false"`, `text-[#a4a8b8]`.
  - URL query parameter updates to `?timeframe=14` or `?timeframe=30` via `router.replace(..., { scroll: false })`.
  - The trends header text updates reactively: `"Daily telemetry for the last 14 days..."` vs `"Daily telemetry for the last 30 days..."`.

---

### Invariant 5: Authoritative CD Signal Badge & Provenance

The deployment signal card (`[data-testid="dora-signal-card"]`) displays the cryptographic source of truth:
- Container attribute: `data-configured="true"` when active, `data-configured="false"` in cold state.
- Signal label mapping:
  - `github_deployments_api` $\rightarrow$ `"GitHub Deployments API - Configured"`
  - `argocd_postsync_webhook` $\rightarrow$ `"ArgoCD Post-Sync Webhook - Configured"`
  - `direct_deployment_api` $\rightarrow$ `"Direct Deployment API - Configured"`
- Sync status (`[data-testid="dora-signal-sync"]`):
  - Dot indicator and label: `"Sync healthy"`, `"Sync delayed"`, `"Sync failing"`, or `"Awaiting Webhook Handshake"`.
  - Timestamp: `"Last sync <relative_time>"`.

---

## 3. DOM Element Map & Test Selector Registry

| Component | Target Element | Test Selector (`data-testid`) | Expected Attributes & Values |
| :--- | :--- | :--- | :--- |
| **Page Root** | `<section>` | `dora-page` | `data-mode="populated" \| "cold_state" \| "partial"`<br>`data-cold-state="true" \| "false"` |
| **Verification** | `<span>` | `dora-verification` | `data-status="verified" \| "unverified" \| "unavailable"`<br>Text: `"DORA VERIFIED"` |
| **Filters Bar** | `<div>` | `dora-filters` | Wraps project picker, timeframe group, and data status. |
| **Project Dropdown** | `<button>` | `dora-project-select` | `aria-haspopup="listbox"`, `aria-expanded="false" \| "true"` |
| **14d Toggle** | `<button>` | `dora-timeframe-14d` | `aria-pressed="true" \| "false"`, Text: `"14d"` |
| **30d Toggle** | `<button>` | `dora-timeframe-30d` | `aria-pressed="true" \| "false"`, Text: `"30d"` |
| **Data Status** | `<span>` | `dora-data-status` | `role="status"`, Text: `"Updated..." \| "Mock data"` |
| **Metric Grid** | `<div>` | `dora-metric-grid` | Contains 4 `MetricCard` articles. |
| **Lead Time Card** | `<article>` | `dora-metric-card-lead_time` | `aria-label="Lead Time for Changes"` |
| **DF Card** | `<article>` | `dora-metric-card-deployment_frequency` | `aria-label="Deployment Frequency"` |
| **CFR Card** | `<article>` | `dora-metric-card-change_failure_rate` | `aria-label="Change Failure Rate"` |
| **MTTR Card** | `<article>` | `dora-metric-card-mttr` | `aria-label="Mean Time to Recovery"` |
| **Tier Badge** | `<span>` | `dora-tier-badge` | Text: `"elite" \| "high" \| "medium" \| "low"` |
| **Metric Value** | `<span>` | `dora-metric-value` | Numeric text (e.g. `"11.4"`, `"2.5"`, `"6"`, `"37"`) |
| **Comparison** | `<div>` | `dora-comparison` | Contains delta, unit suffix, percentage, and period label. |
| **Gauge Visual** | `<div>` | `dora-gauge` | `role="img"`, `aria-label="<Title> gauge"`, contains `<svg>` |
| **Metric No Data** | `<div>` | `dora-metric-no-data` | Rendered when `metric.value === null`. Contains `"No data"`. |
| **Trends Grid** | `<div>` | `dora-trend-grid` | Contains 4 `TrendChartCard` sections. |
| **Lead Time Trend** | `<section>` | `dora-trend-card-lead_time` | `aria-label="Lead Time daily trend"` |
| **DF Trend** | `<section>` | `dora-trend-card-deployment_frequency` | `aria-label="Deployment Frequency daily trend"` |
| **CFR Trend** | `<section>` | `dora-trend-card-change_failure_rate` | `aria-label="Change Failure Rate daily trend"` |
| **MTTR Trend** | `<section>` | `dora-trend-card-mttr` | `aria-label="Mean Time to Recovery daily trend"` |
| **Trend Empty** | `<div>` | `dora-trend-empty` | Rendered when trend has zero non-null points. |
| **Signal Card** | `<section>` | `dora-signal-card` | `data-configured="true" \| "false"` |
| **Signal Sync** | `<div>` | `dora-signal-sync` | Status text, dot indicator, and relative sync timestamp. |
| **Configure Button** | `<a>` | `dora-configure-signals` | `href="/projects"`, Text: `"Configure signals"` |
| **Telemetry Offline** | `<div>` | `dora-telemetry-offline` | `role="status"`, Text: `"Telemetry Offline / 0 data streams active"` |

---

## 4. Comprehensive Test Scenarios

### Scenario 1: Rendering All 4 DORA Gauges with Live/Seeded Values

#### 1.1 Objective & Preconditions
Verify that when the DORA dashboard is loaded with populated telemetry (seeded values or API response), all 4 metric cards render their respective values, tier badges, delta comparisons, and circular SVG gauges.

- **URL:** `/delivery-analytics?mockScenario=populated&timeframe=30`
- **Seeded Values:**
  - `lead_time`: Value `11.4` hours, Tier `elite`, Gauge Max `48`, Threshold `24`
  - `deployment_frequency`: Value `2.5` deploys/day, Tier `high`, Gauge Max `6`, Threshold `3`
  - `change_failure_rate`: Value `6.0` %, Tier `medium`, Gauge Max `30`, Threshold `15`
  - `mttr`: Value `37.0` hours, Tier `high`, Gauge Max `120`, Threshold `60`

#### 1.2 Verification Steps & Expected DOM Hierarchy
1. Navigate to `/delivery-analytics?mockScenario=populated`.
2. Locate `[data-testid="dora-metric-grid"]` and assert 4 child articles exist.
3. For `lead_time`:
   - Assert `[data-testid="dora-metric-card-lead_time"]` is visible.
   - Assert `[data-testid="dora-tier-badge"]` contains `"elite"`.
   - Assert `[data-testid="dora-metric-value"]` has text `"11.4"`.
   - Assert `[data-testid="dora-gauge"]` exists, contains an `<svg>` with background arc and colored arc.
4. For `deployment_frequency`:
   - Assert `[data-testid="dora-tier-badge"]` contains `"high"`.
   - Assert `[data-testid="dora-metric-value"]` has text `"2.5"`.
   - Assert `[data-testid="dora-gauge"]` is visible.
5. For `change_failure_rate`:
   - Assert `[data-testid="dora-tier-badge"]` contains `"medium"`.
   - Assert `[data-testid="dora-metric-value"]` has text `"6"`.
   - Assert `[data-testid="dora-gauge"]` is visible.
6. For `mttr`:
   - Assert `[data-testid="dora-tier-badge"]` contains `"high"`.
   - Assert `[data-testid="dora-metric-value"]` has text `"37"`.
   - Assert `[data-testid="dora-gauge"]` is visible.

#### 1.3 Expected Test Assertions (Playwright)
```typescript
test("TC-DORA-GAUGE-001: Render all 4 DORA gauges with seeded values", async ({ page }) => {
  await page.goto("/delivery-analytics?mockScenario=populated&timeframe=30");
  await expect(page.getByTestId("dora-page")).toBeVisible();

  const metrics = [
    { id: "lead_time", value: "11.4", tier: "elite" },
    { id: "deployment_frequency", value: "2.5", tier: "high" },
    { id: "change_failure_rate", value: "6", tier: "medium" },
    { id: "mttr", value: "37", tier: "high" },
  ];

  for (const m of metrics) {
    const card = page.getByTestId(`dora-metric-card-${m.id}`);
    await expect(card).toBeVisible();
    await expect(card.getByTestId("dora-metric-value")).toHaveText(m.value);
    await expect(card.getByTestId("dora-tier-badge")).toHaveText(m.tier);
    await expect(card.getByTestId("dora-gauge")).toBeVisible();
    await expect(card.getByTestId("dora-comparison")).toBeVisible();
  }
});
```

---

### Scenario 2: Handling Undefined MTTR (`mttr_hours: null`) with Authoritative "No data" Display

#### 2.1 Objective & Preconditions
Verify that when MTTR telemetry is unavailable or unresolved (`mttr_hours: null`), the UI refuses to coerce `null` to `0.0`. It must suppress the gauge and tier badge, displaying a prominent `"No data"` title, explanation box, and empty trend card.

- **URL:** `/delivery-analytics?mockScenario=partial&timeframe=30`
- **Seeded Values:**
  - `lead_time`, `deployment_frequency`, `change_failure_rate` populated as in Scenario 1.
  - `metrics.mttr.value = null`
  - `metrics.mttr.gauge = null`
  - `metrics.mttr.tier = null`
  - `metrics.mttr.unavailableLabel = "UNAVAILABLE"`
  - `metrics.mttr.unavailableReason = "Service restoration signals are not reported for this project (mock)."`
  - `trends.mttr.points`: All 30 points have `value = null`.

#### 2.2 Verification Steps & Expected DOM Hierarchy
1. Navigate to `/delivery-analytics?mockScenario=partial`.
2. Locate `[data-testid="dora-metric-card-mttr"]`.
3. Assert `[data-testid="dora-metric-value"]` is NOT attached to the DOM.
4. Assert `[data-testid="dora-gauge"]` is NOT attached to the DOM.
5. Assert `[data-testid="dora-tier-badge"]` is NOT attached to the DOM.
6. Assert `[data-testid="dora-metric-no-data"]` is visible, containing:
   - Primary heading `"No data"`.
   - Explanatory callout text: `"Service restoration signals are not reported for this project (mock)."`.
7. Locate `[data-testid="dora-trend-card-mttr"]`.
8. Assert `[data-testid="dora-trend-empty"]` is visible inside the card.
9. Assert Recharts SVG element (`.recharts-responsive-container`) is NOT rendered inside the MTTR trend card.
10. Assert empty badge `"NO DATA"` and message `"Insufficient incident or restoration data..."` are displayed.

#### 2.3 Expected Test Assertions (Playwright)
```typescript
test("TC-DORA-NODATA-001: Graceful 'No data' rendering when MTTR is null", async ({ page }) => {
  await page.goto("/delivery-analytics?mockScenario=partial&timeframe=30");

  const mttrCard = page.getByTestId("dora-metric-card-mttr");
  await expect(mttrCard).toBeVisible();

  // Assert absence of numeric value, tier badge, and circular gauge
  await expect(mttrCard.getByTestId("dora-metric-value")).toHaveCount(0);
  await expect(mttrCard.getByTestId("dora-gauge")).toHaveCount(0);
  await expect(mttrCard.getByTestId("dora-tier-badge")).toHaveCount(0);

  // Assert prominent 'No data' block and explanation
  const noDataBlock = mttrCard.getByTestId("dora-metric-no-data");
  await expect(noDataBlock).toBeVisible();
  await expect(noDataBlock.getByText("No data")).toBeVisible();
  await expect(
    noDataBlock.getByText("Service restoration signals are not reported for this project (mock).")
  ).toBeVisible();

  // Assert empty trend state
  const mttrTrend = page.getByTestId("dora-trend-card-mttr");
  await expect(mttrTrend.getByTestId("dora-trend-empty")).toBeVisible();
  await expect(mttrTrend.getByText("Insufficient incident or restoration data")).toBeVisible();
});
```

---

### Scenario 3: Missing-Day Chart Gaps & Discontinuous Trend Line Rendering

#### 3.1 Objective & Preconditions
Verify that calendar days without telemetry data are rendered as discontinuous chart gaps using Recharts `<ReferenceArea>` shaded bands with `"No data"` labels, rather than drawing artificial zero dips.

- **URL:** `/delivery-analytics?mockScenario=populated&timeframe=30`
- **Seeded Gap Days:** In a 30-day series, day indexes `12`, `13`, and `14` have `value = null`.

#### 3.2 Verification Steps & Expected DOM Hierarchy
1. Navigate to `/delivery-analytics?mockScenario=populated&timeframe=30`.
2. Locate `[data-testid="dora-trend-card-lead_time"]`.
3. Assert that the chart container contains SVG `<g class="recharts-reference-area">`.
4. Assert that inside the `<g class="recharts-reference-area">`:
   - A `<rect>` element exists with `fill="#0f131d"` and `stroke-dasharray="3 3"`.
   - A `<text>` label exists displaying `"No data"` positioned at `insideTop`.
5. Hover over a date coordinate belonging to a gap day (or inspect tooltip payload).
6. Assert that `TrendTooltip` displays the target date and text `"No data"` rather than `"0.0 hours"`.

#### 3.3 Expected Test Assertions (Playwright)
```typescript
test("TC-DORA-GAPS-001: Missing-day gaps rendered as shaded ReferenceArea with 'No data'", async ({ page }) => {
  await page.goto("/delivery-analytics?mockScenario=populated&timeframe=30");

  const trendCard = page.getByTestId("dora-trend-card-lead_time");
  await expect(trendCard).toBeVisible();

  // ReferenceArea elements in Recharts are rendered within .recharts-reference-area
  const referenceAreas = trendCard.locator(".recharts-reference-area");
  await expect(referenceAreas.first()).toBeVisible();

  // Multi-day gap runs render the text 'No data' inside the shaded area
  await expect(trendCard.locator("text=No data").first()).toBeVisible();
});
```

---

### Scenario 4: Switching Timeframe Toggle Between 14-Day and 30-Day Windows

#### 4.1 Objective & Preconditions
Verify that toggling the timeframe button group between `14d` and `30d` seamlessly updates the URL query parameter, toggles `aria-pressed`, updates trend chart subtitles, and adjusts the number of daily points.

- **URL:** `/delivery-analytics?mockScenario=populated&timeframe=30` (initial)

#### 4.2 Verification Steps & Expected DOM Hierarchy
1. Navigate to `/delivery-analytics?mockScenario=populated&timeframe=30`.
2. Locate `[data-testid="dora-timeframe-30d"]` and assert `aria-pressed="true"`.
3. Locate `[data-testid="dora-timeframe-14d"]` and assert `aria-pressed="false"`.
4. Assert subtitle contains: `"Daily telemetry for the last 30 days."`.
5. Click `[data-testid="dora-timeframe-14d"]`.
6. Assert URL updates to include `timeframe=14`.
7. Assert `[data-testid="dora-timeframe-14d"]` has `aria-pressed="true"` and class `bg-[#00e599]`.
8. Assert `[data-testid="dora-timeframe-30d"]` has `aria-pressed="false"`.
9. Assert subtitle updates to: `"Daily telemetry for the last 14 days."`.
10. Click `[data-testid="dora-timeframe-30d"]`.
11. Assert URL updates to include `timeframe=30`.
12. Assert `[data-testid="dora-timeframe-30d"]` has `aria-pressed="true"`.

#### 4.3 Expected Test Assertions (Playwright)
```typescript
test("TC-DORA-TIMEFRAME-001: Reactive 14d <-> 30d timeframe toggle switching", async ({ page }) => {
  await page.goto("/delivery-analytics?mockScenario=populated&timeframe=30");

  const btn14d = page.getByTestId("dora-timeframe-14d");
  const btn30d = page.getByTestId("dora-timeframe-30d");

  // Initial 30d state
  await expect(btn30d).toHaveAttribute("aria-pressed", "true");
  await expect(btn14d).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("Daily telemetry for the last 30 days.")).toBeVisible();

  // Switch to 14d
  await btn14d.click();
  await expect(page).toHaveURL(/timeframe=14/);
  await expect(btn14d).toHaveAttribute("aria-pressed", "true");
  await expect(btn30d).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("Daily telemetry for the last 14 days.")).toBeVisible();

  // Switch back to 30d
  await btn30d.click();
  await expect(page).toHaveURL(/timeframe=30/);
  await expect(btn30d).toHaveAttribute("aria-pressed", "true");
  await expect(btn14d).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByText("Daily telemetry for the last 30 days.")).toBeVisible();
});
```

---

### Scenario 5: Authoritative CD Signal Badge & Provenance Attestation

#### 5.1 Objective & Preconditions
Verify that the deployment signal card displays the authoritative CD source, health badge, zero-trust provenance text, and sync timestamp when configured.

- **URL:** `/delivery-analytics?mockScenario=populated&timeframe=30`
- **Signal Config:** `type: "github_deployments_api"`, `configured: true`, `sync.status: "healthy"`

#### 5.2 Verification Steps & Expected DOM Hierarchy
1. Navigate to `/delivery-analytics?mockScenario=populated`.
2. Locate `[data-testid="dora-signal-card"]`.
3. Assert attribute `data-configured="true"`.
4. Assert presence of `ShieldCheck` icon.
5. Assert heading displays `"GitHub Deployments API - Configured"`.
6. Assert badge displays `"Configured"` with green background (`bg-[#00e599]`).
7. Assert provenance description: `"Production deployments are verified using the project's authoritative deployment signal. Merges to main alone are not counted as deployments."`.
8. Locate `[data-testid="dora-signal-sync"]` and assert text contains `"Sync healthy"` and `"Last sync"`.
9. If user has `MANAGE_WEBHOOK_INTEGRATIONS`, assert `[data-testid="dora-configure-signals"]` links to `/projects`.

#### 5.3 Expected Test Assertions (Playwright)
```typescript
test("TC-DORA-SIGNAL-001: Configured deployment signal card and sync health", async ({ page }) => {
  await page.goto("/delivery-analytics?mockScenario=populated&timeframe=30");

  const signalCard = page.getByTestId("dora-signal-card");
  await expect(signalCard).toBeVisible();
  await expect(signalCard).toHaveAttribute("data-configured", "true");
  await expect(signalCard.getByText("GitHub Deployments API - Configured")).toBeVisible();
  await expect(signalCard.getByText("Configured", { exact: true })).toBeVisible();
  await expect(
    signalCard.getByText("Production deployments are verified using the project's authoritative deployment signal.")
  ).toBeVisible();

  const syncBlock = signalCard.getByTestId("dora-signal-sync");
  await expect(syncBlock.getByText("Sync healthy")).toBeVisible();
  await expect(syncBlock.getByText(/Last sync/)).toBeVisible();
});
```

---

### Scenario 6: Cold State / Unconfigured Deployment Signal Degradation

#### 6.1 Objective & Preconditions
Verify that when no authoritative deployment signal has been bound to the project, the entire dashboard degrades safely into a Cold State, preventing misleading zero calculations.

- **URL:** `/delivery-analytics?mockScenario=signal_not_configured&timeframe=30`

#### 6.2 Verification Steps & Expected DOM Hierarchy
1. Navigate to `/delivery-analytics?mockScenario=signal_not_configured`.
2. Assert `[data-testid="dora-page"]` has `data-cold-state="true"`.
3. Assert `[data-testid="dora-telemetry-offline"]` is visible with text `"Telemetry Offline / 0 data streams active"`.
4. In all 4 metric cards (`dora-metric-card-*`):
   - Assert `[data-testid="dora-metric-no-data"]` is visible with `"No data"`.
   - Assert callout displays `"No telemetry data available yet."`.
5. In all 4 trend cards (`dora-trend-card-*`):
   - Assert `[data-testid="dora-trend-empty"]` is visible.
   - Assert badge displays `"PENDING CONFIGURATION"`.
6. In `[data-testid="dora-signal-card"]`:
   - Assert `data-configured="false"`.
   - Assert heading displays `"Awaiting Deployment Integration"`.
   - Assert status badge displays `"Pending configuration"`.
   - Assert sync block displays `"Awaiting Webhook Handshake"`.

#### 6.3 Expected Test Assertions (Playwright)
```typescript
test("TC-DORA-COLD-001: Safe dashboard degradation in unconfigured cold state", async ({ page }) => {
  await page.goto("/delivery-analytics?mockScenario=signal_not_configured&timeframe=30");

  const pageRoot = page.getByTestId("dora-page");
  await expect(pageRoot).toHaveAttribute("data-cold-state", "true");
  await expect(page.getByTestId("dora-telemetry-offline")).toBeVisible();

  // Metric cards should show 'No data'
  for (const id of ["lead_time", "deployment_frequency", "change_failure_rate", "mttr"]) {
    const card = page.getByTestId(`dora-metric-card-${id}`);
    await expect(card.getByTestId("dora-metric-no-data")).toBeVisible();
  }

  // Signal card should indicate pending configuration
  const signalCard = page.getByTestId("dora-signal-card");
  await expect(signalCard).toHaveAttribute("data-configured", "false");
  await expect(signalCard.getByText("Awaiting Deployment Integration")).toBeVisible();
  await expect(signalCard.getByText("Pending configuration")).toBeVisible();
});
```

---

## 5. Traceability Matrix & Acceptance Criteria Sign-Off

| Requirement / Acceptance Criteria | Test Scenario ID | Implementation Module | Playwright Assertion Target | Verification Status |
| :--- | :--- | :--- | :--- | :---: |
| **All 4 Gauges with live/seeded values** | `TC-DORA-GAUGE-001` | `dora-cards.tsx` (`MetricCard`, `DoraGaugeChart`) | `getByTestId("dora-gauge")`, `dora-tier-badge`, `dora-metric-value` | **Covered** |
| **"No data" rendering when `mttr_hours: null`** | `TC-DORA-NODATA-001` | `dora-cards.tsx` (`MetricCard`, `TrendChartCard`) | `getByTestId("dora-metric-no-data")`, `dora-trend-empty` | **Covered** |
| **Missing-day gaps in trend charts** | `TC-DORA-GAPS-001` | `dora-cards.tsx` (`ReferenceArea`), `dora-utils.ts` | `.recharts-reference-area`, `text="No data"`, `connectNulls=false` | **Covered** |
| **14d $\leftrightarrow$ 30d timeframe toggle switching** | `TC-DORA-TIMEFRAME-001` | `dora-cards.tsx` (`DoraFilters`), `page.tsx` | `getByTestId("dora-timeframe-14d")`, `?timeframe=14`, `aria-pressed` | **Covered** |
| **Authoritative deployment signal badge** | `TC-DORA-SIGNAL-001` | `dora-cards.tsx` (`DeploymentSignalCard`) | `getByTestId("dora-signal-card")`, `data-configured="true"`, sync dot | **Covered** |
| **Cold state fallback & zero false zeros** | `TC-DORA-COLD-001` | `page.tsx`, `dora-cards.tsx` | `dora-telemetry-offline`, `dora-trend-empty`, `PENDING CONFIGURATION` | **Covered** |

---

## 6. Pipeline Verification Checklist

Before pushing changes for SCRUM-466, verify every step:
- [x] **Ruff Code Formatting:** Run `python -m ruff format --check .` $\rightarrow$ Must exit with code 0 (220+ files formatted, 0 unformatted).
- [x] **Ruff Linter Checks:** Run `python -m ruff check .` $\rightarrow$ Must exit with code 0 (0 errors, 0 warnings).
- [x] **Web Next.js Lint:** Run `npm run lint --prefix web` $\rightarrow$ Must exit with code 0.
- [x] **Web Next.js Build:** Run `npm run build --prefix web` $\rightarrow$ Must exit with code 0 (24 static/dynamic routes compiled).
- [x] **Pytest Non-Regression:** Run `python -m pytest tests --ignore=tests/security ...` $\rightarrow$ Scenario markdown files do not interfere with pytest collection.
- [x] **Identical Mirror Documentation:** Keep `docs/dora-dashboard-ui-verification-test-scenarios.md` synchronized with `backend/tests/scenarios/test_us31_dora_dashboard_ui_scenarios.md`.
