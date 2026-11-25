# Historical FX rates for portfolio history — design

Date: 2026-11-20 · Branch: `feat/historical-fx` (based on `feat/tase-ils`, PR zivalx/navigator#3)

## Intent

Every past figure in Navigator (NAV history chart, daily/weekly/monthly P&L, the stored
daily P&L series, and a lot's cost basis) converts foreign-currency amounts at **today's**
exchange rate. USD/ILS moved between ~3.10 and ~3.66 over 2021–2026, so for an ILS holder
the history is materially wrong. Each past day must use that day's own rate.

The user chose the **shekel-investor view**: figures shown in a base currency include
currency gains and losses, the way a bank or broker statement shows them.

## Goals

- A stored daily rate history (`per_usd` for every supported currency) back to the earliest
  lot or price snapshot, kept current by a daily append.
- `FxService.get_rate_at(from, to, day)`: latest stored daily rate on or before `day`,
  crossing through USD.
- Past values use the past rate: NAV at a date, the daily P&L rollup (NAV, per-asset moves,
  net flow), and the daily P&L display series.
- A lot's cost converts at its **purchase-date** rate, so unrealized P&L includes FX
  gains/losses (user's decision).
- Daily P&L bars in a non-USD base include that day's currency move (my call, for
  consistency with the cost-basis decision and the NAV line).

## Non-goals

- No frontend changes; API shapes are unchanged.
- Live current values (current price × today's rate) are unchanged.
- No realized P&L, no intraday historical rates.

## Anti-goals

- Never write a permanent `portfolio_snapshots` row using a fallback (today's) rate.
- Never log or print quantities, costs or NAV values — only currency codes, dates, counts.

## Approaches considered

- **A. New `fx_daily_rates` table — chosen by the user.** One row per (currency, date),
  unique key, `source` column; `create_all()` creates it, no migration; live intraday
  rows stay in `fx_rates`.
- **B. Reuse `fx_rates`.** No unique key or source column; adding them needs hand-written
  `ALTER TABLE`; daily closes would mix with intraday live rows. Rejected.
- **Static rate file.** Frozen at creation and still needs a fetcher. Rejected (brief).

## Design

### Storage — `app/models/price.py`

`FxDailyRate` → table `fx_daily_rates`: `id`, `currency` (Currency enum, never USD),
`date` (Date), `per_usd` (Float: units of `currency` per 1 USD), `source` (str, `"yahoo"`),
`fetched_at`. Unique constraint `(currency, date)`.

### Fetching — `app/providers/yahoo.py`

`YahooProvider.get_fx_history(currency, start: date, end: date) -> list[(date, per_usd)]`
calls the chart API for `{CCY}=X` with `interval=1d&period1&period2`. Verified 2026-11-20:
`ILS=X` ≈ 3.33 and `EUR=X` ≈ 0.91, i.e. `{CCY}=X` is CCY per 1 USD for every currency.
Timestamps are 00:00 UTC, so the row date is the UTC date. `None` closes are skipped.

### Refresh — `app/services/fx_history.py`

`refresh_fx_history(db) -> int` (rows upserted), for **every `Currency` except USD**
(17 currencies; tiny data, and it covers any base currency the UI may request):

- Needed start = earliest of (min lot `purchase_date`, min `PriceSnapshot.timestamp`,
  today − 400 days) − 7 days.
- Per currency: if nothing stored, or the earliest stored date is more than 7 days after
  the needed start, fetch `[needed start, today]`; otherwise fetch `[latest stored − 5 days,
  today]` (re-fetching a few days lets a partial same-day close be overwritten).
- Upsert by `(currency, date)`. A failing currency is logged (code only) and skipped; the
  others continue.

Callers:
1. Startup: `backfill_daily_snapshots_job` (45 s after start) first calls
   `refresh_fx_history`, then the rebuild below, then the existing empty-table backfill.
2. Daily: `snapshot_eod_prices` calls `refresh_fx_history` right before
   `compute_daily_snapshot`, so today's rollup has today's rate.

### Lookup — `FxService.get_rate_at(from_ccy, to_ccy, day, strict=False) -> float`

- Same currency → 1.0.
- `per_usd(ccy, day)`: USD → 1.0; else the latest stored row with `date <= day` and
  `date >= day − 7 days` (covers weekends and holidays; an older rate counts as missing).
- Rate = `per_usd(to, day) / per_usd(from, day)`.
- Missing rate: `strict=True` raises `ValueError`. Otherwise fall back to
  `get_rate(from, to)` (today's rate) and log one warning per (pair) per service instance.
- Rates are loaded once per currency per `FxService` instance into a sorted list and
  looked up with `bisect` (history requests loop dates × lots).
- `convert_at(amount, from, to, day, strict=False)` convenience wrapper.

### Use sites — `app/services/portfolio.py`

`PortfolioService` gains `strict_fx: bool = False`; `compute_daily_snapshot`'s internal USD
service is built with `strict_fx=True`.

- `_get_nav_at_date`: snapshot price → base at `target_date.date()` rate (strict per
  `strict_fx`). Feeds the NAV history chart and the summary's daily/weekly/monthly P&L,
  which therefore include currency moves.
- `_value_lot`: cost → base at the lot's `purchase_date` rate (today's rate if
  `purchase_date` is None, or via the non-strict fallback). Current price unchanged.
- `_asset_usd_price_at`, `_codes_without_usd_rate`, `net_flow` in
  `compute_daily_snapshot`: rate at the relevant day, strict. A missing rate skips the day
  (existing behaviour on a missing rate).
- `get_daily_pnl_series` (non-USD base): with `r_t` = USD→base rate on the row date and
  `r_p` on the previous stored row's date (one extra row before the window is read):
  - `nav = nav_usd × r_t`, `netFlow = net_flow_usd × r_t`
  - `dailyPnL = daily_pnl_usd × r_t + prev_nav_usd × (r_t − r_p)` — the USD move plus the
    currency move on what was held at the previous close (exact for the per-asset sum).
  - `dailyPnLPercent = dailyPnL / (prev_nav_usd × r_p) × 100`; first-ever row stays 0.
  - Non-strict per row; if even today's rate is unavailable, return empty points as now.

### One-time rebuild of the stored series

Existing `portfolio_snapshots` rows were computed at an old single rate. After the FX
refresh on startup, if `maintenance_runs` has no `fx_history_rebuild` marker **and** the
refresh stored rows: delete all `portfolio_snapshots`, run `backfill_daily_snapshots`,
then write the marker. A crash before the marker just reruns it next startup.

## Tests (`backend/tests/test_fx_history.py` + updates)

- `get_rate_at`: exact date; weekend/gap falls back to the prior day; gap > 7 days counts
  as missing; missing → strict raises, non-strict falls back to the live rate with a
  warning; cross through USD (ILS→EUR = per_usd(EUR)/per_usd(ILS)); same currency = 1.
- `refresh_fx_history`: upsert is idempotent; gap at the start is back-filled; incremental
  fetch window; one failing currency doesn't stop the others (Yahoo mocked).
- NAV at date uses the date's rate (two dates, two rates, one ILS lot).
- `_value_lot` cost at purchase-date rate; unrealized P&L includes the FX move.
- `compute_daily_snapshot` skips a day with no historical rate even when a live rate exists.
- `get_daily_pnl_series` in ILS: the currency-move term, percent, first row.
- Rebuild runs once and writes its marker.
- Existing tests that seed only `fx_rates` keep passing via the non-strict fallback; tests
  of stored rows with non-USD lots seed `fx_daily_rates` instead.

## Left to the builder

Helper names, exact log wording, test fixture layout, and whether `get_fx_history` lives on
`YahooProvider` or as a module function (keep it in `providers/`).
