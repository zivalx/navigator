# Persisted Daily P&L + EOD NAV Series with Bar Chart

**Date:** 2026-08-20
**Status:** Approved (design)
**Branch:** feature branch off `main`

## Problem

The dashboard shows a single live "Daily P&L" number and a NAV line chart, but:
- Nothing stores the portfolio's end-of-day NAV as a durable record — NAV is
  reconstructed on the fly from per-asset price snapshots each request.
- There is no accurate, per-day P&L series to analyze over time. The existing
  chart's `pnl` is just "NAV minus the period's first point," which counts
  money added to the portfolio as if it were a gain.

The user wants: an **accurate daily P&L**, the **actual EOD NAV saved** each
day, and both **presented on a graph for future analysis**.

## Requirements

1. Store the actual end-of-day portfolio NAV, once per day, as a durable
   queryable record (not only reconstructed).
2. Store an accurate, **flow-adjusted** daily P&L per day: deploying cash
   (buying) must never register as a gain.
3. Backfill the series from existing price-snapshot history so the chart is
   populated immediately.
4. Present the daily P&L as a bar chart (green/red per day) over selectable
   periods, alongside the existing NAV line.

## Design

### 1. Data model — `portfolio_snapshots` (new table)

One row per calendar day, canonically in USD:

| column | type | meaning |
|---|---|---|
| `id` | String PK | uuid |
| `date` | Date, unique index | the trading day (UTC calendar date) |
| `nav` | Float | actual EOD portfolio NAV that day (USD) |
| `net_flow` | Float | net money in/out that day (USD): cost of lots purchased that day (buys positive) |
| `daily_pnl` | Float | flow-adjusted: `nav - prev_nav - net_flow` |
| `daily_pnl_pct` | Float | `daily_pnl / prev_nav * 100` (0 when `prev_nav<=0`) |
| `base_currency` | String | always `"USD"` — the canonical storage currency |
| `created_at` | DateTime(tz) | write time |

- **Canonical USD storage.** NAV/flows are stored in USD so the record is
  currency-stable. Presentation converts USD→display currency at read time
  (current FX), consistent with the rest of the app. The daily % is
  currency-invariant, so the bar chart's shape is identical across display
  currencies.
- `date` is unique; writes are **upserts** (idempotent re-runs).
- New model `app/models/portfolio_snapshot.py`; added to the additive startup
  migration (`database.py`) with `CREATE TABLE IF NOT EXISTS` semantics via
  `create_all` (SQLite tests create it from the model).

### 2. Accuracy — flow-adjusted daily P&L

For day D with previous stored day P:

```
net_flow(D)  = Σ cost_basis_usd(lot) for lots with purchase_date == D
daily_pnl(D) = nav(D) - nav(P) - net_flow(D)
daily_pnl_pct(D) = daily_pnl(D) / nav(P) * 100     # 0 if nav(P) <= 0
```

`nav(D)` reuses the existing `_get_nav_at_date` valuation (the single at-date
NAV method) with base = USD. This isolates market performance from cash
deployment: buying $5k of stock adds $5k to both `nav` and `net_flow`, netting
to 0 daily P&L that day.

The **first** day in the series has no prior row, so its `nav` is stored but
`daily_pnl` and `daily_pnl_pct` are `0` (no baseline to diff against) — the
series' P&L starts from the second day forward.

**Known limitation (documented, not fixed here):** the app has no
sell/transaction ledger — removing a holding deletes the lot, so a *sale* is
not recorded and would appear as a NAV drop (a loss) on its day. Only buys are
captured as flows. Accurate sell handling requires a transactions table, a
separate future feature. Called out in the chart's info tooltip.

### 3. Writing the series

- **Service:** `PortfolioService.compute_daily_snapshot(day)` computes one
  day's row (NAV via `_get_nav_at_date`, flow from that day's buys) and upserts
  it. `backfill_daily_snapshots()` iterates the distinct snapshot dates in
  history oldest→newest, computing each day's row using the prior stored row as
  `prev_nav`.
- **Scheduler:** the existing `eod_snapshot` job (16:30 ET), after writing
  per-asset `price_snapshots`, calls `compute_daily_snapshot(today)`. A one-off
  startup job (mirroring `backfill_historical_prices`) runs
  `backfill_daily_snapshots()` once if the table is empty.
- Idempotent: upsert-by-date means re-runs and overlapping backfill/EOD writes
  converge to the same values.

### 4. API + presentation

- **Endpoint:** `GET /api/portfolio/daily-pnl?period=1w|1m|3m|6m|1y` →
  `{ base_currency, period, points: [{date, nav, dailyPnL, dailyPnLPercent, netFlow}] }`,
  reading stored rows in the window and converting USD→requested currency.
  Cached per `(period, currency)` like `/history`.
- **Frontend:** the dashboard Performance card gains a **NAV / Daily P&L**
  toggle. Daily P&L view is a Recharts bar chart: one bar per day, green for
  gains / red for losses, currency-aware axis and tooltip (reusing
  `formatMoney`). NAV view is the existing line chart, unchanged.

### 5. Testing

- `compute_daily_snapshot`: flow-adjustment (a buy-day nets to ~0 P&L; a pure
  market move is captured), `prev_nav<=0` guard, USD canonical values.
- `backfill_daily_snapshots`: builds a correct ordered series from seeded price
  snapshots + lots; idempotent on re-run (upsert, no duplicate dates).
- Endpoint: period filtering, currency conversion, empty-history case.
- NAV is persisted and read back correctly.
- Frontend: bar chart renders from a fixed series (added once a frontend test
  harness exists; until then, verified live).

## Out of scope

- Sell/withdrawal tracking (needs a transactions ledger).
- As-of historical FX (shares the existing chart's current-rate limitation).
- Time-weighted / money-weighted return metrics beyond simple daily P&L.
