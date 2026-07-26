# Portfolio History Chart — Design

**Date:** 2026-07-23
**Status:** Approved for implementation

## Goal

Show portfolio performance over time on the dashboard: NAV (and P&L vs period start) as an area/line chart with a period selector. The daily EOD `price_snapshots` data already exists (scheduler + 6-month backfill); today only sparklines are shown.

## Backend

`GET /api/portfolio/history?period=1w|1m|3m|6m|1y` (default `3m`)

```json
{
  "base_currency": "USD",
  "period": "3m",
  "points": [
    {"date": "2026-04-23", "nav": 152340.12, "pnl": 0.0, "pnl_pct": 0.0},
    {"date": "2026-04-24", "nav": 153010.55, "pnl": 670.43, "pnl_pct": 0.44}
  ]
}
```

- NAV-at-date MUST reuse the same method the existing daily/weekly/monthly P&L uses (see `services/portfolio.py` and the 2026-07-03 P&L spec): value holdings at each date's EOD snapshot price, converted to base currency, plus cash. Respect lot `purchase_date` — a lot contributes only from its purchase date onward (match whatever the existing P&L-at-date logic does; do not invent a second method).
- `pnl`/`pnl_pct` are relative to the first point of the requested period.
- Dates with no snapshot (weekends/holidays) are simply absent — no forward-fill in v1.
- Implemented in `services/portfolio.py` (new method) + route in `routers/portfolio.py`. Redis-cache the response per period (TTL 300s).

## Frontend

- `src/components/dashboard/PerformanceChart.tsx` — Recharts (already a dependency) area chart of NAV; header shows period P&L ($ and %, green/red); period selector (1W/1M/3M/6M/1Y) as small toggle buttons. Tooltip with date, NAV, P&L. Loading skeleton; empty state ("Not enough history yet") when <2 points.
- Follow the dataviz skill for colors/axes/tooltip design; must work in light + dark themes.
- Placement: `src/pages/Index.tsx`, full-width card directly under PortfolioSummaryCards.
- Data via React Query, refetch on period change, staleTime 5 min.

## Out of scope

Benchmark overlay (SPY), per-account breakdown, realized P&L.
