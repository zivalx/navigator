# Historical FX Rates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Past portfolio values convert foreign-currency amounts at each day's own exchange rate, from a stored daily rate history.

**Architecture:** A new `fx_daily_rates` table holds Yahoo daily closes (`{CCY}=X`, units of CCY per 1 USD) for every supported currency. `FxService.get_rate_at` looks up the latest rate on/before a day (≤7 days back), crossing through USD, with a strict mode for permanent stored rows and a live-rate fallback otherwise. Portfolio history paths, lot cost basis and the daily P&L display use it; a one-time marker-guarded rebuild fixes the stored series.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy (Postgres prod / in-memory SQLite tests), httpx, APScheduler, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-20-historical-fx-rates-design.md`

## Global Constraints

- Run tests from `backend/` with `/Users/ziv.a/repos_/navigator/backend/.venv/bin/python -m pytest -q`. Baseline: 223 passed, 2 skipped.
- Never read, open or grep any `.env` file.
- Never log or print quantities, costs or NAV values — only currency codes, dates, counts.
- Never write a `portfolio_snapshots` row using a fallback (today's) rate.
- Yahoo `{CCY}=X` close = units of CCY per 1 USD (verified: `ILS=X` ≈ 3.33, `EUR=X` ≈ 0.91). Row date = UTC date of the bar timestamp.
- Max rate gap: 7 days (a rate older than `day − 7` counts as missing).
- No migration step: new table comes from `Base.metadata.create_all()`.
- Commit each task; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push.
- Tests stay offline: mock Yahoo/httpx; existing tests stub `FxService.get_rate`.

## Review Focus

1. A lot with a **naive** `purchase_date` (SQLite returns naive datetimes) must still pick the purchase-date rate → `_ensure_aware(...).date()`; test in Task 4.
2. A lot added later with a purchase date **before** stored coverage → next refresh back-fills the gap; test in Task 3.
3. A partial same-day close is **overwritten** by the next refresh (upsert updates `per_usd`); test in Task 3.
4. A base currency with **no history at all** (Yahoo lacks it) → live views fall back to the current rate with one warning per pair, not one per point; test in Task 2.
5. One currency's Yahoo fetch **fails** → the other currencies are still stored; test in Task 3.

---

### Task 1: `fx_daily_rates` model + Yahoo history fetch

**Files:**
- Modify: `backend/app/models/price.py`, `backend/app/models/__init__.py`
- Modify: `backend/app/providers/yahoo.py`
- Test: `backend/tests/test_fx_history.py` (new)

**Interfaces:**
- Produces: `FxDailyRate` (table `fx_daily_rates`): `id: str` PK, `currency: Enum(Currency)`, `date: Date`, `per_usd: Float`, `source: String` (default `"yahoo"`), `fetched_at: DateTime(timezone=True)`; `UniqueConstraint("currency", "date", name="uq_fx_daily_currency_date")`. Exported from `app.models`.
- Produces: `YahooFinanceProvider.get_fx_history(self, currency: str, start: date, end: date) -> list[tuple[date, float]]` — GET `QUERY_URL.format(symbol=f"{currency}=X")` with `{"interval": "1d", "period1": <start 00:00 UTC epoch>, "period2": <(end + 1 day) 00:00 UTC epoch>}`; skips `None` closes; raises `ValueError` when the result is empty.

- [ ] **Step 1: Write failing tests** `test_fx_daily_rate_unique_per_currency_and_date` (second row with same currency+date raises `IntegrityError`) and `test_get_fx_history_parses_closes` (stub `provider.client.get` returning a chart payload with timestamps `[1795046400, 1795132800, 1795219200]` and closes `[3.27, None, 3.33]` → `[(date(2026,11,19), 3.27), (date(2026,11,21), 3.33)]`; assert the requested symbol is `ILS=X` and `period1`/`period2` match).
- [ ] **Step 2: Run** `pytest tests/test_fx_history.py -q` → FAIL (import error).
- [ ] **Step 3: Implement** the model and the provider method.
- [ ] **Step 4: Run** `pytest tests/test_fx_history.py -q` → PASS; full suite still green.
- [ ] **Step 5: Commit** `feat(fx): fx_daily_rates table and Yahoo FX history fetch`.

### Task 2: `FxService.get_rate_at` / `convert_at`

**Files:**
- Modify: `backend/app/services/fx.py`
- Test: `backend/tests/test_fx_history.py`

**Interfaces:**
- Consumes: `FxDailyRate` (Task 1).
- Produces (in `app/services/fx.py`): `MAX_RATE_GAP_DAYS = 7`;
  `async def get_rate_at(self, from_currency: Currency, to_currency: Currency, day: date, strict: bool = False) -> float`;
  `async def convert_at(self, amount: float, from_currency: Currency, to_currency: Currency, day: date, strict: bool = False) -> float`.
  Rate = `per_usd(to, day) / per_usd(from, day)`, `per_usd(USD) = 1.0`. Missing: strict → `ValueError(f"No daily FX rate {f}->{t} on {day}")`; non-strict → `await self.get_rate(from, to)` plus one `logger.warning("FX %s->%s on %s: no daily rate, using current rate", f, t, day)` per (f, t) per instance. Each currency's rows are loaded once per instance into sorted `(dates, rates)` lists and searched with `bisect`.

- [ ] **Step 1: Write failing tests** (seed `FxDailyRate` rows directly; stub `FxService.get_rate` for fallback):
  - `test_rate_at_exact_date`: ILS 3.5 on Mon → `get_rate_at(USD, ILS, Mon) == 3.5`, `get_rate_at(ILS, USD, Mon) == approx(1/3.5)`.
  - `test_rate_at_weekend_uses_prior_day`: ILS 3.5 Fri only → Sat and Sun return 3.5.
  - `test_rate_at_gap_over_7_days_is_missing`: row 8 days before → strict raises `ValueError`.
  - `test_rate_at_missing_falls_back_to_live_rate_once`: no rows, stub `get_rate` → 3.7; two calls on two days return 3.7; exactly one warning record.
  - `test_rate_at_cross_through_usd`: ILS 3.5, EUR 0.875 same day → `get_rate_at(ILS, EUR, d) == approx(0.25)`.
  - `test_rate_at_same_currency_is_one` and `test_convert_at_multiplies`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_fx_history.py -q` → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(fx): date-aware rate lookup through USD`.

### Task 3: `refresh_fx_history`

**Files:**
- Create: `backend/app/services/fx_history.py`
- Test: `backend/tests/test_fx_history.py`

**Interfaces:**
- Consumes: `FxDailyRate`, `YahooFinanceProvider.get_fx_history` (Task 1).
- Produces: `async def refresh_fx_history(db: Session, provider: YahooFinanceProvider | None = None, today: date | None = None) -> int` (rows inserted + updated). For every `Currency` except USD: needed start = min(earliest `HoldingLot.purchase_date`, earliest `PriceSnapshot.timestamp`, `today − 400 days`) dates, minus 7 days. If nothing stored or earliest stored > needed start + 7 days → fetch `[needed start, today]`; else fetch `[latest stored − 5 days, today]`. Upsert by (currency, date): update `per_usd`/`fetched_at` or insert; commit per currency. A failing currency logs `"FX history: %s failed (%s)"` (code, `redact_secrets(e)`) and continues. Closes the provider only if it created it.

- [ ] **Step 1: Write failing tests** with a fake provider recording `(currency, start, end)` calls and returning fixed rows:
  - `test_refresh_backfills_all_currencies_from_needed_start`: lot purchased 2025-01-10, `today=2026-11-25` → every non-USD currency fetched with start `2025-01-03`, end today; returns row count.
  - `test_refresh_is_idempotent_and_overwrites_partial_close`: second run with a changed value for an existing date → one row per (currency, date); value updated.
  - `test_refresh_incremental_window`: coverage already from needed start → fetch start = latest stored − 5 days.
  - `test_refresh_backfills_gap_before_coverage`: stored from 2026-06-01, lot purchased 2025-01-10 → full range fetched.
  - `test_refresh_one_failing_currency_does_not_stop_others`: provider raises for EUR → ILS rows stored, warning names EUR.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.
- [ ] **Step 5: Commit** `feat(fx): refresh daily FX history from Yahoo`.

### Task 4: Portfolio valuation at the day's rate

**Files:**
- Modify: `backend/app/services/portfolio.py` (`__init__`, `_value_lot`, `_get_nav_at_date`, `_asset_usd_price_at`, `compute_daily_snapshot`, `_codes_without_usd_rate`)
- Test: `backend/tests/test_fx_history_portfolio.py` (new); update `backend/tests/test_portfolio_unavailable.py`

**Interfaces:**
- Consumes: `FxService.get_rate_at` / `convert_at` (Task 2).
- Produces: `PortfolioService(db, base_currency=Currency.USD, strict_fx: bool = False)`. `compute_daily_snapshot` builds its USD service with `strict_fx=True`.
  - `_get_nav_at_date`: `convert_at(price, ccy, base, target_date.date(), strict=self.strict_fx)`.
  - `_value_lot` cost: `convert_at(avg_cost, cost_ccy, base, _ensure_aware(purchase_date).date())` (non-strict); `convert` when `purchase_date` is None. Current price conversion unchanged.
  - `_asset_usd_price_at`: rate at `when.date()`, `strict=self.strict_fx`.
  - `_codes_without_usd_rate`: `get_rate_at(code, USD, end_of_day.date(), strict=self.strict_fx)`.
  - `net_flow`: `usd.fx_service.convert_at(avg_cost, cost_ccy, USD, day, strict=True)`.

- [ ] **Step 1: Write failing tests** (ILS asset, ILS daily rows seeded, quotes stubbed at `MarketDataService.get_quote`):
  - `test_nav_at_date_uses_that_days_rate`: one ILS lot, snapshots 100 ILS on D1 and D2, ILS 4.0 on D1 and 3.2 on D2 → USD NAV `qty*25` on D1 and `qty*31.25` on D2.
  - `test_lot_cost_uses_purchase_date_rate`: cost 350 ILS bought when ILS=3.5, today ILS=3.2 (live `get_rate` stub) and quote 350 ILS → `avgCostBase == approx(100)`, `currentPrice == approx(109.375)`, unrealized P&L positive.
  - `test_lot_cost_naive_purchase_date`: same with a naive `purchase_date` → same result.
  - `test_daily_snapshot_skips_day_without_historical_rate`: ILS lot, live `get_rate` stub present, no daily rows → `compute_daily_snapshot` returns None, no row.
  - `test_daily_snapshot_uses_historical_rates`: two days with different ILS rates → stored `nav` and `daily_pnl` use each day's rate (daily_pnl = qty × (p2/r2 − p1/r1)).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update** `test_daily_snapshot_skipped_on_lot_currency_check_purchase_day` to seed an `FxDailyRate` ILS row of `1/0.27` covering `next_day` (expected NAV unchanged). Fix any other existing test that writes stored rows with non-USD prices the same way; do not loosen assertions.
- [ ] **Step 5: Run** full suite → green.
- [ ] **Step 6: Commit** `feat(portfolio): value history and lot cost at the day's FX rate`.

### Task 5: Daily P&L display series in the base currency

**Files:**
- Modify: `backend/app/services/portfolio.py` (`get_daily_pnl_series`)
- Test: `backend/tests/test_fx_history_portfolio.py`

**Interfaces:**
- Consumes: `get_rate_at` (Task 2).
- Produces: same response shape. For non-USD base, read window rows plus the latest row before the window. Per row `r_t = get_rate_at(USD, base, row.date)` (non-strict), `r_p` = rate on the previous row's date:
  `nav = nav_usd*r_t`; `netFlow = net_flow_usd*r_t`; `dailyPnL = daily_pnl_usd*r_t + prev_nav_usd*(r_t − r_p)`; `dailyPnLPercent = dailyPnL / (prev_nav_usd*r_p) * 100` (0 if that is 0). First-ever row: `dailyPnL = daily_pnl_usd*r_t`, percent = stored. If any rate raises → empty points with the existing warning text `"daily P&L series unavailable: no FX rate USD->%s"`. USD base unchanged.

- [ ] **Step 1: Write failing tests:**
  - `test_series_ils_includes_currency_move`: rows D1 (nav 1000, pnl 0) and D2 (nav 1010, pnl 10), ILS 3.5 / 3.6 → D2 `nav == approx(3636)`, `dailyPnL == approx(10*3.6 + 1000*0.1) == 136`, `dailyPnLPercent == approx(136/3500*100)`.
  - `test_series_uses_row_before_window_as_prev`: prev row outside the window still drives the first in-window point's FX term.
  - `test_series_usd_unchanged`: USD base returns stored values as-is.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** full suite → green (incl. `test_daily_pnl_series_empty_when_display_rate_missing`).
- [ ] **Step 5: Commit** `feat(portfolio): daily P&L series at each day's rate`.

### Task 6: Scheduler wiring, one-time rebuild, docs

**Files:**
- Modify: `backend/app/models/maintenance.py` (add `FX_REBUILD_MARKER = "fx_history_rebuild"`)
- Modify: `backend/app/tasks/scheduler.py` (`backfill_daily_snapshots_job`, `snapshot_eod_prices`)
- Modify: `CLAUDE.md` (Architecture jobs + a Gotchas bullet)
- Test: `backend/tests/test_fx_history_jobs.py` (new)

**Interfaces:**
- Consumes: `refresh_fx_history` (Task 3), `get_marker`/`set_marker`.
- Produces:
  - `backfill_daily_snapshots_job`: `await refresh_fx_history(db)` (own try/except, warning on failure); then if `fx_daily_rates` has any row and no `FX_REBUILD_MARKER`: delete all `PortfolioSnapshot` rows, commit, `await svc.backfill_daily_snapshots()`, `set_marker(db, FX_REBUILD_MARKER)`, log row count; else keep the existing "populate if empty" behaviour.
  - `snapshot_eod_prices`: `await refresh_fx_history(db)` (try/except) right before `compute_daily_snapshot`.

- [ ] **Step 1: Write failing tests** (point `scheduler.SessionLocal` at the test session as `eval_db` in `conftest.py` does; monkeypatch `scheduler.refresh_fx_history` with a fake that seeds rows and counts calls):
  - `test_job_rebuilds_series_once_and_sets_marker`: pre-existing stale snapshot row → after job, rows recomputed, marker set; second run leaves rows untouched (no delete).
  - `test_job_skips_rebuild_without_fx_rows`: fake refresh stores nothing → stale row kept, no marker.
  - `test_eod_job_refreshes_fx_before_rollup`: call order is refresh then `compute_daily_snapshot`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** + CLAUDE.md lines (daily FX history refreshed at startup and in the EOD job; `fx_history_rebuild` marker; strict vs fallback rule).
- [ ] **Step 4: Run** full suite → green.
- [ ] **Step 5: Commit** `feat(scheduler): keep FX history current and rebuild the P&L series once`.
