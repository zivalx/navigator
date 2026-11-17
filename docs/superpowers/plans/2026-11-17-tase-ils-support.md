# Tel Aviv (TASE) and Shekel Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tel Aviv shares and ETFs (and, by the same fix, London listings) work end to end in Navigator and trender, priced in major units (₪, £), with no silent wrong money.

**Architecture:** Each app gets one normalizer that turns Yahoo's minor units (`ILA`, `GBp`/`GBX`, `ZAc`) into the major currency at every Yahoo entry point. Navigator gains ILS/IL and a dozen venue currencies through a Postgres enum migration. It derives an asset's currency, exchange and region from Yahoo when the asset is created. Any holding it cannot value correctly is flagged instead of summed. Trender formats every price with the asset's currency and warns when its listing disagrees with Navigator's.

**Tech Stack:**
- Navigator: FastAPI, sync SQLAlchemy, Postgres (prod), pytest on in-memory SQLite, React/Vite/TS.
- Trender: FastAPI, async SQLModel, APScheduler, Jinja templates, structlog, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-11-17-tase-ils-support-design.md` (Navigator repo, branch `feat/tase-ils`). Section numbers below (§6.1 …) refer to it.

## Repos, branches, commands

| | Navigator | Trender |
|---|---|---|
| Worktree | `/Users/ziv.a/repos_/_wt/navigator-tase` | `/Users/ziv.a/repos_/_wt/trender-tase` (created in Task 12) |
| Branch | `feat/tase-ils` (on `feat/export-positions`) | `feat/tase-ils` (on `feat/navigator-sync`) |
| Backend tests | `cd backend && /Users/ziv.a/repos_/navigator/backend/.venv/bin/pytest -q` | `uv run pytest -q` |
| Frontend | `cd frontend && npm run build && npm run lint` (no test runner) | — |

Never touch `/Users/ziv.a/repos_/trender` (the owner's uncommitted work). Never open any `.env*` file other than `.env.example`. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No pushes.

## Global Constraints

- Minor units: `"ILA"→("ILS",0.01)`, `"GBp"→("GBP",0.01)`, `"GBX"→("GBP",0.01)`, `"ZAc"→("ZAR",0.01)`. Match the exact case first. The case-insensitive fallback applies only to `GBX`/`ILA`/`ZAC`, so `"GBP"` stays GBP. Anything else maps to `(code.upper(), 1.0)`.
- The factor applies to prices and absolute changes only, **never** to volume or percentages.
- US/USD assets behave identically before and after: factor 1.0, same code paths.
- New Navigator `Currency` members: `ILS HKD CNY KRW TWD SGD INR SEK DKK NOK AUD CAD ZAR`. New `MarketRegion` member: `IL`. In both enums value == name, because Postgres stores the member NAME as the label.
- Venue map (Yahoo `exchangeName` → region):
  - `TLV`→IL.
  - `NMS NYQ NGM NCM ASE PCX BTS`→US.
  - `LSE GER PAR AMS MIL MCE EBS STO CPH HEL OSL BRU LIS VIE IOB`→EU.
  - `TYO HKG SHH SHZ KSC TAI SES NSI BSE`→ASIA.
  - Unknown venue: keep the client value and log.
- Unavailable reasons (exact strings): `fx_unavailable`, `lot_currency_check`, plus the plan-added `price_unavailable` (see Rulings).
- Maintenance markers (exact names): `minor_units_cutover`, `minor_unit_alerts_converted`.
- Trender sync issue codes: the new `listing_mismatch` (warning only: never changes `status`, `cost_comparable` or ownership). `suspect_units` is raised only when the quote was in a minor unit **and** the cost/price ratio is within `[50, 200]`.
- Audit and log output never prints quantities, costs or tokens. Symbols and currency codes only.
- UI copy for the minor-unit hint: "price per share in shekels (₪), not agorot". Use "pounds (£), not pence" for GBP.
- Dashboard banner copy: "Totals exclude N holdings (no exchange rate / check lot currency)", followed by their symbols.
- Trender alert-form hint: "Prices are in the asset's currency (₪ for Tel Aviv, £ for London)."

## Rulings made while planning (the spec leaves these open)

1. **`price_unavailable` reason.** A holding whose quote fails today loses `marketValue`, but its cost still counts. That produces a fake loss, and the "holding quietly disappearing" case the spec's anti-goal forbids. Holdings flag it with this third reason and are excluded the same way.
2. **Alert pause on stale quotes.** The `db_cache` fallback quote carries no unit information. Until `minor_unit_alerts_converted` exists, a pre-cutover alert is paused when its quote has `unitFactor != 1` **or** `stale == True`. The pause ends as soon as the audit `--apply` runs.
3. **Quote dict key.** It is `unitFactor` (camelCase, like `changePercent`). The quote cache key becomes `quote:v2:{SYMBOL}`, so pre-deploy cached agorot quotes are never served.
4. **`AssetCreate.currency` and `market_region` become optional.**
   - For stock/etf/fund they are ignored and derived from Yahoo.
   - Crypto defaults to USD/US when they are missing.
   - `other` requires a currency (422 without one).
5. **Trender normalizer location.** It lives in the new `app/currency.py`. `navigator_sync/currency.quote_unit` delegates to it.
6. **Trender suspect band.** It is fixed at 50–200. The `navigator_cost_magnitude_factor` setting and the `SyncPolicy.cost_magnitude_factor` field are removed; both were added on the unmerged `feat/navigator-sync`.
7. **Trender legacy-alert warning cutoff.** It uses the constant `MINOR_UNIT_CUTOVER = datetime(2026, 11, 17, tzinfo=UTC)`.
8. **History paths on FX failure:**
   - `_get_nav_at_date` returns `None`, and callers omit that point.
   - `_asset_usd_price_at` returns `None`.
   - `get_daily_pnl_series` returns `[]` with a warning log when the display rate is missing.

## Review Focus

1. **A pre-deploy cached or stale quote meets a pre-cutover pence/agorot alert.** Expected: no trigger before the audit. Tests are in Task 6 (`test_pre_cutover_alert_paused_on_stale_quote`) and Task 1 (cache key).
2. **A US holding, a USD base and a working FX feed.** Every number must be unchanged. Covered by the existing suites staying green, plus `test_usd_holding_unchanged` in Task 5.
3. **The ILS base currency with a USD holding while the USD→ILS rate is missing.** Expected: the USD holding is flagged `fx_unavailable`, not summed as shekels. `test_fx_failure_flags_holding_in_ils_base` in Task 5.
4. **Two gunicorn workers start together against an old-enum database.** Expected: both succeed, and the type gains every value exactly once. The opt-in Postgres test in Task 2 runs the migration concurrently.
5. **A symbol Yahoo knows but whose currency Navigator still lacks (e.g. `BRL`).** Expected: a 422 with a clear message, and no USD row stored. `test_create_asset_unsupported_currency_422` in Task 3.

---

## Navigator backend

### Task 1: Normalizer and normalized Yahoo prices

**Files:**
- Create: `backend/app/services/currency_units.py`
- Modify: `backend/app/providers/yahoo.py` (`get_quote` :34-62; add `get_chart_meta`)
- Modify: `backend/app/services/market_data.py` (cache key in `get_quote` :27)
- Modify: `backend/app/tasks/scheduler.py` (`backfill_historical_prices` :117-205)
- Test: `backend/tests/test_currency_units.py`, `backend/tests/test_yahoo_normalization.py`

**Interfaces:**
- Produces:
  - `normalize_quote_currency(code: str | None) -> tuple[str, float]`. Blank or `None` gives `("USD", 1.0)`.
  - `YahooFinanceProvider.get_quote(symbol) -> dict`. It gains the key `unitFactor: float`. `currency` is now major, and `price` and `change` are multiplied by the factor.
  - `YahooFinanceProvider.get_chart_meta(symbol: str) -> dict | None` returns `{"currency": major, "unitFactor": f, "exchangeName": str|None, "fullExchangeName": str|None, "instrumentType": str|None}`. It returns `None` on 404 or an empty result and raises `httpx.HTTPError` on network failure.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_currency_units.py
@pytest.mark.parametrize("code,expected", [
    ("ILA", ("ILS", 0.01)), ("GBp", ("GBP", 0.01)), ("GBX", ("GBP", 0.01)),
    ("gbx", ("GBP", 0.01)), ("ZAc", ("ZAR", 0.01)), ("zac", ("ZAR", 0.01)),
    ("GBP", ("GBP", 1.0)), ("usd", ("USD", 1.0)), ("ILS", ("ILS", 1.0)),
    (None, ("USD", 1.0)), ("  ", ("USD", 1.0)),
])
def test_normalize_quote_currency(code, expected):
    assert normalize_quote_currency(code) == expected
```

The Yahoo tests patch the provider's httpx client with `httpx.MockTransport`. The chart body has `meta = {"currency": "ILA", "exchangeName": "TLV", "regularMarketPrice": 6512.0, "chartPreviousClose": 6400.0}`.
- `test_get_quote_normalizes_agorot`: `price == 65.12`, `change == pytest.approx(1.12)`, `currency == "ILS"`, `unitFactor == 0.01`, and `changePercent` is the same value the un-normalized maths gives (1.75).
- `test_get_quote_usd_unchanged`: for a `"USD"` meta, `unitFactor == 1.0` and the price is as given.
- `test_get_chart_meta_tlv`: returns `currency == "ILS"`, `unitFactor == 0.01`, `exchangeName == "TLV"`.
- `test_backfill_closes_normalized`: run `backfill_historical_prices` with a mocked chart for an ILA asset (closes `[6400, 6512]`). The stored `PriceSnapshot` prices are `[64.0, 65.12]` and their currency is `Currency.ILS`. This needs Task 2's enum, so mark it `xfail(strict=True)` until Task 2 and remove the mark there.

- [ ] **Step 2: Run them, expect failures**

Run: `pytest tests/test_currency_units.py tests/test_yahoo_normalization.py -q`. Expected: ImportError / assertion failures.

- [ ] **Step 3: Implement**
  - Write `normalize_quote_currency` (rules in Global Constraints).
  - In `get_quote`, apply it to `meta.currency` and multiply `price` and `change` by the factor.
  - Add `get_chart_meta`, which uses the existing chart URL with `range=1d`.
  - In `MarketDataService.get_quote`, change the cache key to `quote:v2:{SYMBOL}` (Ruling 3).
  - In `backfill_historical_prices`, normalize `meta.currency` and multiply each close by the factor.

- [ ] **Step 4: Run tests → PASS. Run the full suite → green.**

- [ ] **Step 5: Commit** `feat(prices): normalize Yahoo minor units to major currency`

### Task 2: ILS/IL enums, Postgres enum migration, cutover marker

**Files:**
- Modify: `backend/app/models/asset.py:8-27`, `backend/app/schemas/asset.py:7-30`, `backend/app/schemas/market.py:15`
- Create: `backend/app/models/maintenance.py` (`MaintenanceRun`); register it in `backend/app/models/__init__.py` and in the conftest model import
- Modify: `backend/app/database.py` (`init_db` :29-32; add `_migrate_enum_values`, `ensure_cutover_marker`)
- Test: `backend/tests/test_enum_migration.py` (SQLite part plus an opt-in Postgres part)

**Interfaces:**
- Produces:
  - `Currency.ILS` (and the other members in Global Constraints) and `MarketRegion.IL`.
  - `MaintenanceRun` (table `maintenance_runs`): `name: str` primary key, `ran_at: DateTime(timezone=True)` not null.
  - `get_marker(db: Session, name: str) -> datetime | None` and `set_marker(db: Session, name: str, at: datetime | None = None) -> None`, which is idempotent and leaves an existing row unchanged. Both live in `app/models/maintenance.py`.
  - The constants `CUTOVER_MARKER = "minor_units_cutover"` and `ALERTS_CONVERTED_MARKER = "minor_unit_alerts_converted"`.
  - `ensure_cutover_marker()` is called from `init_db()` for every dialect and runs `set_marker(CUTOVER_MARKER)`. A concurrent insert from the other worker is swallowed on `IntegrityError`.
  - `_migrate_enum_values()` runs only on Postgres, after `create_all`, and before `_migrate_price_alerts`. On an AUTOCOMMIT connection it takes `SELECT pg_advisory_lock(7400417)`, runs `ALTER TYPE currency ADD VALUE IF NOT EXISTS '<CODE>'` for each new currency and `ALTER TYPE marketregion ADD VALUE IF NOT EXISTS 'IL'`, then unlocks in `finally`.

- [ ] **Step 1: Write failing tests**
  - `test_enums_include_ils_and_il`: `Currency("ILS")`, `MarketRegion("IL")`, schema `Currency("ILS")` and `schemas.market.MarketRegion("IL")` all resolve.
  - `test_cutover_marker_written_once`: calling `set_marker` twice on SQLite leaves one row and keeps the first `ran_at`.
  - `test_snapshot_stores_ils`: a `PriceSnapshot(currency=Currency.ILS)` commits.
  - Opt-in `test_pg_migrate_enum_values_on_old_db`, skipped unless `TEST_POSTGRES_URL` is set:
    1. Create a scratch schema whose `currency` and `marketregion` types hold only the old values.
    2. Run `_migrate_enum_values` against an engine bound to it, twice concurrently (two threads).
    3. Assert that `enum_range(NULL::currency)` contains every new code exactly once and `IL` is present.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**
  - Make `_migrate_enum_values` accept an optional `engine` parameter (default: the module engine) so the test can bind it.
  - Remove Task 1's `xfail` mark.

- [ ] **Step 4: Run the full suite → green.** Run the Postgres test against a scratch DB:
  ```bash
  docker run --rm -d --name tase-pg -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:15
  TEST_POSTGRES_URL=postgresql://postgres:x@localhost:55432/postgres pytest tests/test_enum_migration.py -q
  docker rm -f tase-pg
  ```
  Expected: all pass, none skipped.

- [ ] **Step 5: Commit** `feat(db): add ILS/IL and venue currencies with a Postgres enum migration`

### Task 3: Asset identity derived from Yahoo

**Files:**
- Create: `backend/app/services/asset_identity.py`
- Modify: `backend/app/schemas/asset.py` (`AssetBase` / `AssetCreate` :~30-45), `backend/app/routers/assets.py:42` (`create_asset`), `backend/app/routers/alerts.py:125-176` (`_resolve_asset`)
- Test: `backend/tests/test_asset_identity.py`

**Interfaces:**
- Consumes: `YahooFinanceProvider.get_chart_meta` (Task 1); `Currency`, `MarketRegion` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class ListingIdentity: currency: Currency; exchange: str | None; market_region: MarketRegion | None` (`None` means an unknown venue).
  - `class ListingUnavailable(Exception)` maps to 503 with detail "couldn't verify symbol, try again".
  - `class UnsupportedCurrency(Exception)` maps to 422 with detail "unsupported currency <CODE> for <SYMBOL>".
  - `class SymbolNotFound(Exception)` maps to 404 with detail "symbol not found".
  - `async def derive_listing(symbol: str) -> ListingIdentity`.
  - `VENUE_REGION: dict[str, MarketRegion]` (the map in Global Constraints).
  - `exchange` is `fullExchangeName or exchangeName`. The region lookup uses `exchangeName`.
- Schema: `AssetBase.currency: Optional[Currency] = None` and `market_region: Optional[MarketRegion] = Field(None, alias="marketRegion")`. Keep `AssetUpdate` and the response schema `Asset` non-optional.
- `create_asset` rules:
  - stock/etf/fund: derive the identity. Client currency and exchange are ignored. Region is the derived one, or the client's if unknown, or `US` if neither (log `"unknown venue %s for %s"`).
  - crypto: missing currency/region default to USD/US.
  - other: a missing currency gives 422 "currency is required for other assets"; a missing region defaults to US.
- `_resolve_asset` (alerts): replaces its best-effort quote with `derive_listing`, and maps the same exceptions to the same status codes. The created asset gets `exchange` and the derived region.

- [ ] **Step 1: Write failing tests.** Monkeypatch `YahooFinanceProvider.get_chart_meta`, and build a TestClient over the assets router plus the alerts router with `get_db` overridden (the pattern in conftest's `client`).
  - `test_create_tase_asset_derives_identity`: POST `{symbol:"TEVA.TA", name:"Teva", asset_type:"stock", currency:"USD", market_region:"US"}` with meta (ILS, TLV, "Tel Aviv") gives 201, `currency=="ILS"`, `marketRegion=="IL"` and exchange `"Tel Aviv"`.
  - `test_create_london_asset_from_pence`: meta GBp/LSE gives GBP and EU.
  - `test_create_asset_unknown_venue_keeps_client_region`: venue `"XYZ"` with client `"EU"` gives EU.
  - `test_create_asset_unsupported_currency_422`: meta `BRL` gives 422, and no Asset row exists afterwards.
  - `test_create_asset_yahoo_down_503`: `get_chart_meta` raising `httpx.ConnectError` gives 503, and no row is stored.
  - `test_create_asset_symbol_not_found_404`: meta `None` gives 404.
  - `test_create_crypto_defaults_usd`: no currency gives USD/US, and `get_chart_meta` is not called.
  - `test_create_other_requires_currency`: gives 422.
  - `test_alert_by_symbol_creates_tase_asset`: POST `/api/alerts` with `symbol:"TEVA.TA"` creates an asset with ILS/IL.

- [ ] **Step 2: Run → FAIL. Step 3: Implement. Step 4: Run the full suite → green.**

- [ ] **Step 5: Commit** `feat(assets): derive currency, exchange and region from Yahoo on create`

### Task 4: Provider fallback guard and snapshot logging

**Files:**
- Modify: `backend/app/services/market_data.py` (`get_quote` :27-141, `_store_price_snapshot` :155-175)
- Test: `backend/tests/test_market_data_fallback.py` (extend)

**Interfaces:**
- Behaviour: when the DB asset exists and its currency is not `USD`, the Polygon and Finnhub steps are skipped and the chain goes straight to the DB fallback.
- `_store_price_snapshot` catches exceptions as now, but logs `logger.warning("could not store price snapshot for %s: %s", symbol, exc)`.

- [ ] **Step 1: Write failing tests** (reuse `_all_providers_down`-style patching).
  - `test_non_usd_asset_skips_polygon_and_finnhub`: an ILS asset where Yahoo fails and Polygon/Finnhub would return USD prices. Assert they were not called and the result has `source == "db_cache"`, using a seeded ILS snapshot.
  - `test_usd_asset_still_uses_polygon`.
  - `test_snapshot_store_failure_logs_warning` (`caplog`).
- [ ] **Step 2: FAIL → Step 3: implement → Step 4: full suite green.**
- [ ] **Step 5: Commit** `fix(market-data): never price non-USD assets with USD-only fallbacks`

### Task 5: Holdings flags, totals, and history with no silent wrong money

**Files:**
- Modify: `backend/app/schemas/holding.py` (`HoldingWithAsset` :68, `GroupedHolding` :85)
- Modify: `backend/app/services/portfolio.py`:
  - `get_holdings_with_prices` :46-124
  - `get_grouped_holdings` :127-191
  - `_get_nav_at_date` :193/:254
  - `get_summary` :338
  - `_asset_usd_price_at` :427/:443
  - `compute_daily_snapshot` :446/:475
  - `get_daily_pnl_series` :540/:559
- Test: `backend/tests/test_portfolio_unavailable.py`

**Interfaces:**
- Produces new schema fields:
  - On both `HoldingWithAsset` and `GroupedHolding`: `value_unavailable: bool = Field(False, alias="valueUnavailable")` and `unavailable_reason: Optional[Literal["fx_unavailable","lot_currency_check","price_unavailable"]] = Field(None, alias="unavailableReason")`.
- Per-lot rule, applied in order (first match wins):
  1. The asset's currency is `ILS` and the lot's `cost_currency != ILS`: `lot_currency_check`.
  2. The cost→base FX conversion raises: `fx_unavailable`.
  3. The quote raises: `price_unavailable`.
  4. The quote currency is not a `Currency` member, or the price→base FX conversion raises: `fx_unavailable`.
- When flagged, set `avgCostBase`, `currentPrice`, `priceChange`, `marketValue`, `unrealizedPnL`, `unrealizedPnLPercent` and `weight` to `None`.
- A group is flagged when any of its lots is. It takes the first flagged lot's reason, and its base fields are `None`. `get_grouped_holdings` reuses each lot's `avgCostBase` and stops calling FX itself.
- `get_summary` excludes flagged holdings from every total.
- History (Ruling 8):
  - `_get_nav_at_date` → `None` on FX failure.
  - `_asset_usd_price_at` → `None`.
  - `get_daily_pnl_series` → `[]` with a warning.
- `compute_daily_snapshot` returns `None` and writes nothing when any held lot's cost or price can't be converted to USD. It logs `logger.warning("skipping portfolio snapshot for %s: no FX rate for %s", day, codes)`.

- [ ] **Step 1: Write failing tests.** Seed with factories; monkeypatch `FxService.get_rate` and `MarketDataService.get_quote`.
  - `test_ils_lot_valued_in_usd_base`: ILS asset, ILS lot (10 @ ₪60), quote ₪65.12, rate ILS→USD 0.27. Expect `marketValue == pytest.approx(10*65.12*0.27)`, `avgCostBase == pytest.approx(60*0.27)` and `valueUnavailable is False`.
  - `test_ils_lot_valued_in_ils_base`: base ILS, so `marketValue == pytest.approx(651.2)`.
  - `test_fx_failure_flags_holding_in_ils_base`: base ILS, a USD holding, and `get_rate` raises `ValueError`. Expect `valueUnavailable`, reason `fx_unavailable`, and `marketValue is None` and `avgCostBase is None`.
  - `test_ils_asset_with_usd_lot_is_lot_currency_check`.
  - `test_quote_failure_flags_price_unavailable`.
  - `test_grouped_holding_flagged_when_any_lot_flagged`: also asserts `get_grouped_holdings` does not raise on an FX failure.
  - `test_summary_excludes_flagged`.
  - `test_usd_holding_unchanged`: snapshot the values of a USD/USD-base holding and assert them exactly.
  - `test_daily_snapshot_skipped_when_rate_missing`: returns `None`, no `PortfolioSnapshot` row, warning logged.
- [ ] **Step 2: FAIL → Step 3: implement → Step 4: full suite green.** The existing `test_portfolio_*` and `test_daily_pnl` suites must not change expectations. If one encoded the old pass-through, fix the test only when it asserted the wrong-money behaviour, and say so in the report.
- [ ] **Step 5: Commit** `fix(portfolio): flag holdings that cannot be valued instead of mixing currencies`

### Task 6: Alert pause across the cutover and currency-aware notifications

**Files:**
- Modify: `backend/app/tasks/scheduler.py` (`evaluate_price_alerts` :231-312)
- Modify: `backend/app/services/notifications.py` (`format_alert_message` :23-56)
- Create: `backend/app/services/money_format.py`
- Test: `backend/tests/test_alert_cutover.py`; extend `backend/tests/test_alerts.py` for message format

**Interfaces:**
- Consumes: `get_marker`, `CUTOVER_MARKER`, `ALERTS_CONVERTED_MARKER` (Task 2); the quote `unitFactor` and `stale` keys (Task 1).
- Produces: `format_money(value: float | None, currency: str) -> str`, using the symbols `USD $`, `EUR €`, `GBP £`, `ILS ₪`, `JPY ¥`. `CHF` and other codes render as `"1,234.50 CHF"`, and `None` renders as `"—"`. Two decimals with thousands separators: `format_money(65.12,"ILS") == "₪65.12"`, `format_money(1234.5,"USD") == "$1,234.50"`.
- Pause rule (Ruling 2):
  - Skip an alert when all of these hold:
    - `cutover = get_marker(CUTOVER_MARKER)` exists;
    - `get_marker(ALERTS_CONVERTED_MARKER)` is `None`;
    - `alert.created_at < cutover`;
    - `quote.get("unitFactor", 1.0) != 1.0 or quote.get("stale")`.
  - Then emit one `logger.warning("paused pre-cutover alerts until audit --apply: %s", sorted_symbols)` per run.
  - SQLite returns naive datetimes, so compare as UTC.
- `format_alert_message` uses `format_money(x, alert.asset.currency.value)` for every price. It defaults to `"USD"` if the asset is missing.

- [ ] **Step 1: Write failing tests**
  - `test_pre_cutover_alert_paused_on_minor_quote`: marker at T, alert created at T−1h with PRICE_BELOW threshold 2600, quote `{"price": 26.0, "unitFactor": 0.01}`. The alert is not triggered and a warning names the symbol.
  - `test_pre_cutover_alert_paused_on_stale_quote`: same, with `{"price": 26.0, "stale": True}`.
  - `test_pre_cutover_alert_runs_after_conversion_marker`: set `ALERTS_CONVERTED_MARKER` and threshold 26.5, and the alert triggers.
  - `test_post_cutover_alert_evaluates_normally`: created after T, it triggers on a minor-unit quote.
  - `test_no_cutover_marker_means_no_pause`.
  - `test_format_money` (table above); `test_alert_message_uses_shekel_symbol` asserts `"₪" in msg` and `"$" not in msg`.
- [ ] **Step 2: FAIL → Step 3: implement → Step 4: full suite green.**
- [ ] **Step 5: Commit** `feat(alerts): pause pre-cutover minor-unit alerts; currency-aware messages`

### Task 7: Audit script

**Files:**
- Create: `backend/scripts/__init__.py`, `backend/scripts/audit_asset_currencies.py`
- Test: `backend/tests/test_audit_asset_currencies.py`

**Interfaces:**
- Consumes: `derive_listing`, `ListingUnavailable`, `UnsupportedCurrency`, `SymbolNotFound` (Task 3); `get_chart_meta` (Task 1, for `unitFactor`); markers (Task 2).
- Produces:
  - `async def audit(db: Session, *, apply: bool, out: TextIO) -> AuditReport`.
  - `def main(argv: list[str] | None = None) -> int`, with the flag `--apply`. Run it with `python -m scripts.audit_asset_currencies [--apply]` from `backend/`, which uses `SessionLocal`.
  - `AuditReport` is a dataclass with `asset_fixes: list[str]` (symbols), `lot_mismatches: list[str]` (symbols), `converted_alerts: int`, `converted_watch_targets: int` and `minor_unit_symbols: list[str]`.
- Behaviour:
  - It covers every asset of type stock, etf or fund.
  - Per asset it prints one line: `SYMBOL: currency USD->ILS region US->IL exchange ->Tel Aviv`, listing only the fields that differ. Yahoo failures print `SYMBOL: skipped (<reason>)`.
  - Mismatched lots print `SYMBOL: N lot(s) in USD, listing is ILS - fix in the lot edit dialog`. N is a lot count, not a quantity.
  - Minor-unit assets with pre-cutover alerts or targets print `SYMBOL: N alert(s), M watch target(s) priced in minor units`.
  - With `--apply`:
    - Update the asset fields.
    - For assets whose meta has `unitFactor != 1`, multiply by 0.01 `price_alerts.threshold`, `trail_amount`, `high_water_mark` and `triggered_price`, for rows with `created_at < cutover`. Skip the step entirely when `ALERTS_CONVERTED_MARKER` already exists.
    - Multiply `watchlist_items.target_price` by 0.01 under the same rule. A row with a `NULL` `created_at` counts as pre-cutover.
    - Write `ALERTS_CONVERTED_MARKER`.
    - Commit once at the end.
  - Lots are never modified.

- [ ] **Step 1: Write failing tests**
  - `test_dry_run_reports_and_changes_nothing`: assert the DB is unchanged and the output contains `TEVA.TA` and `ILS`.
  - `test_output_contains_no_quantities_or_costs`: seed a lot with quantity `137.0` and cost `61.23`; assert neither `"137"` nor `"61.23"` appears in the output. Also assert the alert threshold value doesn't appear.
  - `test_apply_fixes_asset_and_converts_pre_cutover_fields`: checks all four alert fields and `target_price`, and that the post-cutover alert is untouched.
  - `test_apply_twice_is_noop_for_prices`: the second run leaves values unchanged.
  - `test_apply_never_changes_lots`.
  - `test_null_created_at_target_converted`.
- [ ] **Step 2: FAIL → Step 3: implement → Step 4: full suite green.**
- [ ] **Step 5: Commit** `feat(scripts): audit and fix asset currencies after the minor-unit cutover`

## Navigator frontend

There is no test runner. Each task ends with `npm run build` and `npm run lint` (no new errors relative to the branch base), and Task 10 adds a browser check. Frontend tasks need the backend running on port 7000 with seeded data. Create `.claude/launch.json` with `api` (backend `uvicorn` on 7000, using the main checkout's venv) and `web` (`npm run dev` on 7070) entries for the preview tools.

### Task 8: Currency and region types, lists, badges, and `$` removal

**Files:**
- Modify: `frontend/src/lib/types.ts:2-3`
- Modify: the region unions in `contexts/MarketCardsContext.tsx:8,77`, `lib/marketCardTypes.ts:6`, `components/markets/MarketsOverview.tsx:14,52-62`, `components/markets/AddMarketCardDialog.tsx:45,166-175`
- Modify: `components/common/AssetBadge.tsx:16,35-41`
- Modify: `components/settings/SettingsPage.tsx:30-34`, `components/portfolio/AddHoldingDialog.tsx:279-290`
- Modify: `components/markets/MarketAssetCard.tsx:79`, `components/watchlist/WatchlistTable.tsx:443,473,577`, `components/alerts/alertUtils.ts:8-9`, `components/alerts/CreateAlertDialog.tsx:274,292,301,371-372`, `components/alerts/AlertsBell.tsx:33,94`, `pages/Alerts.tsx:69,78,80`
- Modify (backend, if needed): `backend/app/routers/alerts.py` `_serialize` :~103 to include `currency` (the asset's currency code)

**Interfaces:**
- `Currency` union adds `'ILS' | 'HKD' | 'CNY' | 'KRW' | 'TWD' | 'SGD' | 'INR' | 'SEK' | 'DKK' | 'NOK' | 'AUD' | 'CAD' | 'ZAR'`. `MarketRegion` adds `'IL'`.
- `RegionBadge` gets `IL: { label: 'Israel', flag: '🇮🇱' }`. Both badges fall back to a neutral badge showing the raw value instead of throwing on an unknown key.
- Settings base currency list adds `ILS - Israeli Shekel`. The "Other" currency list adds `JPY` and `ILS`.
- Markets: the region tabs and the AddMarketCardDialog select add `IL` "🇮🇱 Israel". The tab may be empty.
- Formatting uses `formatMoney(value, currency)` from `lib/utils.ts`, deleting `alertUtils.formatMoney` and updating its imports to pass the alert's `currency` (default `'USD'`). Input prefixes show the currency symbol via a new `currencySymbol(code: string): string` in `lib/utils.ts`, built on `Intl.NumberFormat(...).formatToParts` and picking the `currency` part.
- WatchlistTable uses the item asset's currency.

- [ ] **Step 1:** Make the type changes and run `npm run build`; it fails wherever a `Record<MarketRegion,…>` is now incomplete. Fix each one.
- [ ] **Step 2:** Replace every listed `$`. Verify with `grep -rn '\$\${\|>\$<\|"\$"' frontend/src --include=*.tsx --include=*.ts`: no hits outside template-literal interpolation of non-price text.
- [ ] **Step 3:** `npm run build && npm run lint` → OK. If `_serialize` changed, run the backend suite → green.
- [ ] **Step 4: Commit** `feat(ui): ILS/IL support and currency-aware price display`

### Task 9: Asset creation payloads and add-lot hints

**Files:**
- Modify: `frontend/src/components/layout/GlobalSearch.tsx` (find the path with grep; `ensureAssetInDb` :136-146, star :246-257)
- Modify: `frontend/src/components/watchlist/WatchlistTable.tsx:121-131`
- Modify: `frontend/src/components/portfolio/AddHoldingDialog.tsx:44-79,196-205`

**Interfaces:**
- Consumes: Task 3's optional `currency`/`market_region` for stock, etf and fund; the `api.getQuote(symbol)` result `{price, currency, unitFactor}` (Task 1).
- Creation payloads:
  - stock/etf/fund send only `symbol, name, exchange, asset_type, provider_ids`.
  - crypto keeps sending `currency:'USD', market_region:'US'`.
- On a 503, the toast says "Couldn't verify symbol, try again". On a 422, it shows the backend `detail`.
- The star is not rendered for `asset_type === 'index'`.
- AddHoldingDialog, asset tab:
  - The avg-cost input is prefixed with `currencySymbol(asset.currency)`.
  - When `asset.currency` is `ILS` or `GBP`, it shows the hint from Global Constraints.
  - Once a symbol is chosen, it fetches `api.getQuote(symbol)`. When the entered avg cost / quote price is within `[50, 200]`, it shows a non-blocking amber warning: "This is about 100× the current price — did you enter agorot/pence?" Submit stays enabled.

- [ ] **Step 1:** Implement, then run `npm run build && npm run lint` → OK.
- [ ] **Step 2: Commit** `feat(ui): let the backend derive listing identity; agorot/pence hints`

### Task 10: Portfolio exclusion, banner, lot currency edit, browser check

**Files:**
- Modify: `frontend/src/lib/types.ts` (`HoldingWithAsset` :92-102, `GroupedHolding` :105-116)
- Modify: `frontend/src/contexts/PortfolioContext.tsx` (mapping :~100-130, totals :223-262, grouping :276-305, `updateHolding` :414-430)
- Modify: `frontend/src/components/portfolio/PortfolioTable.tsx` (edit state :48-53, `openEditDialog` :92-99, `handleUpdateHolding` :101-114, dialog :428-490, lot rows :342-410)
- Create: `frontend/src/components/dashboard/ExcludedHoldingsBanner.tsx`; render it in `pages/Index.tsx` between the header (:15) and `<PortfolioSummaryCards />` (:17)

**Interfaces:**
- Consumes: the `valueUnavailable` and `unavailableReason` fields (Task 5).
- Types: add `valueUnavailable?: boolean; unavailableReason?: 'fx_unavailable' | 'lot_currency_check' | 'price_unavailable'` to both holding types.
- Context:
  - Every total (`totalNav`, `totalCost`, `totalUnrealizedPnL`, daily P&L, weights, top holdings/movers) skips holdings with `valueUnavailable`.
  - `costBaseOf(h)` returns `h.quantity * h.avgCostBase` when `avgCostBase != null`. Otherwise it returns `h.quantity * h.avgCost` only when `h.costCurrency === baseCurrency`, else `null`, and that holding is excluded.
  - A new context value `excludedHoldings: { symbol: string; reason: string }[]` is unique by symbol.
- The banner renders nothing when the list is empty. Otherwise it shows the Global Constraints copy, with symbols grouped by reason.
- PortfolioTable:
  - The edit dialog gets a `Cost currency` select (the full `Currency` list, seeded from `lot.costCurrency`), and `handleUpdateHolding` sends `costCurrency`.
  - A lot row whose `costCurrency !== group.asset.currency` shows a small outline badge, "currency differs from listing".
  - A flagged group or lot shows "—" in the value cells, with a tooltip of the reason.

- [ ] **Step 1:** Implement, then run `npm run build && npm run lint` → OK.
- [ ] **Step 2: Browser verification.**
  1. Start the `api` and `web` previews.
  2. Seed through the API: TEVA.TA (POST `/assets/` with live Yahoo), one ILS lot and one deliberately USD lot on it, plus a USD lot on AAPL.
  3. Confirm with `preview_snapshot`:
     - (a) the banner names TEVA.TA with "check lot currency";
     - (b) the dashboard totals equal the AAPL-only value (compare with `/api/portfolio/holdings`);
     - (c) the "currency differs from listing" badge is on the USD lot;
     - (d) after editing that lot's currency to ILS in the dialog, the banner disappears and TEVA.TA shows ₪ prices;
     - (e) switching the base currency to ILS in Settings renders ₪ totals.
  4. Take `preview_screenshot` as proof, then delete the seeded rows.
- [ ] **Step 3: Commit** `feat(ui): exclude unvaluable holdings from totals; edit lot currency`

## Trender

### Task 11: Normalizer and `format_price`, with a new worktree

**Files:**
- Setup (once):
  ```bash
  git -C /Users/ziv.a/repos_/_wt/trender-navigator-sync worktree add /Users/ziv.a/repos_/_wt/trender-tase -b feat/tase-ils feat/navigator-sync
  cd /Users/ziv.a/repos_/_wt/trender-tase && uv run pytest -q   # baseline: all pass (482 at last run)
  ```
- Create: `app/currency.py`
- Modify: `app/navigator_sync/currency.py` (`quote_unit` delegates)
- Test: `tests/test_currency.py`

**Interfaces:**
- Produces:
  - `normalize_quote_currency(code: str | None) -> tuple[str | None, float]`. It uses the same table as Navigator's, but blank or `None` returns `(None, 1.0)`, matching today's `quote_unit` contract.
  - `format_price(value: float | None, currency: str | None) -> str`, which uses the same symbol table and outputs as Navigator's `format_money`. A `None` currency means USD.
  - `CURRENCY_SYMBOLS: dict[str, str]`.

- [ ] **Step 1: Write failing tests.** Use the normalizer table from Task 1 (with `(None, (None, 1.0))`). Then:
  - `format_price(65.12, "ILS") == "₪65.12"`
  - `format_price(26, "GBP") == "£26.00"`
  - `format_price(1234.5, None) == "$1,234.50"`
  - `format_price(10, "CHF") == "10.00 CHF"`
  - `format_price(None, "USD") == "—"`
- [ ] **Step 2: FAIL → Step 3: implement → Step 4:** `uv run pytest tests/test_currency.py tests/test_navigator_sync -q` passes.
- [ ] **Step 5: Commit** `feat(currency): shared minor-unit normalizer and price formatter`

### Task 12: Normalized price layer and asset currency sync

**Files:**
- Modify: `app/indicators/price.py:16-50` (`fetch_price_data`)
- Modify: `app/indicators/price_cache.py:10-50`
- Modify: `app/workers/price_monitor.py:162-200` (`run_price_monitor`)
- Modify: `app/workers/scheduler.py:~170-190` (the job that calls `refresh_all_caches`)
- Modify: `app/ui/routes.py:~636-660` (`add_asset`)
- Test: `tests/test_indicators/test_price_normalization.py`, extend `tests/test_workers/test_price_monitor.py`

**Interfaces:**
- Consumes: `normalize_quote_currency` (Task 11).
- Produces:
  - `@dataclass(frozen=True) class PriceHistory: bars: list[dict]; currency: str | None; unit_factor: float`.
  - `async def fetch_price_history(symbol: str, days: int = 5) -> PriceHistory`. It makes one chart call, reads `meta.currency`, and multiplies `open/high/low/close` by the factor. `volume` is untouched.
  - `fetch_price_data(symbol, days)` keeps its signature and returns `(await fetch_price_history(...)).bars`, so all existing callers get major prices.
  - The cache entry gains `"currency"`. A new `get_cached_currency(symbol: str) -> str | None` reads it.
  - `async def sync_asset_currency(db: AsyncSession, asset: TrackedAsset, currency: str | None) -> bool` lives in `app/indicators/price_cache.py`. When `currency` is set and differs from `asset.currency`, it sets `asset.currency` and `updated_at`, logs `asset_currency_updated`, and returns True. The caller commits.
- Callers:
  - `run_price_monitor` uses `fetch_price_history(days=1)` and calls `sync_asset_currency` per asset.
  - After `refresh_all_caches`, the scheduler job calls `sync_asset_currency` for each refreshed asset with `get_cached_currency(symbol)`.
  - `add_asset` calls `fetch_price_history(symbol, days=5)` best-effort. If it fails, it keeps the USD default and logs.

- [ ] **Step 1: Write failing tests** (use `httpx.MockTransport` or patch `httpx.AsyncClient.get`, following existing patterns).
  - `test_fetch_price_history_normalizes_ila`: the meta is ILA, the bar's close is 6512 and volume 1000. Expect close `65.12`, volume `1000`, currency `"ILS"` and factor `0.01`.
  - `test_fetch_price_data_returns_major_bars`.
  - `test_usd_bars_unchanged`.
  - `test_sync_asset_currency_updates_and_noops`.
  - `test_price_monitor_updates_asset_currency`: an asset with currency `"ILA"` (the old raw-unit rule) ends as `"ILS"`.
- [ ] **Step 2: FAIL → Step 3: implement → Step 4:** `uv run pytest -q` all pass. Update existing price tests only where they patch `fetch_price_data` internals.
- [ ] **Step 5: Commit** `feat(prices): normalize Yahoo minor units and keep asset currency in sync`

### Task 13: Navigator sync on major prices: suspect band and listing mismatch

**Files:**
- Modify: `app/navigator_sync/client.py:133-200` (`YahooQuote`, `_parse_chart`)
- Modify: `app/navigator_sync/currency.py` (`assess_cost`, `comparable_price`, remove `SUSPECT_RATIO`)
- Modify: `app/navigator_sync/sync.py` (`SyncPolicy` :53, `_cost_issues` :73, `_attach` :242-301, probe handling :358-413)
- Modify: `app/config.py` and `.env.example` (remove `navigator_cost_magnitude_factor`), `README.md` if it documents it
- Test: `tests/test_navigator_sync/test_client.py`, `test_currency.py`, `test_sync.py`

**Interfaces:**
- Consumes: `normalize_quote_currency` (Task 11).
- Produces:
  - `YahooQuote(currency: str | None, exchange: str | None, last_price: float | None, unit_factor: float = 1.0)`. `currency` is major and `last_price` is major (already multiplied).
  - `link.quote_currency` stores the major code, and `link.quote_unit_factor` stores the factor (0.01 or 1.0).
  - `assess_cost(*, cost_basis_complete, avg_cost, avg_cost_currency, quote_currency, probe_price, unit_factor: float = 1.0) -> CostAssessment`:
    - `currency_mismatch` when major `quote_currency` != `avg_cost_currency` (upper).
    - `suspect_units` only when `unit_factor != 1.0` and `50 <= max(cost, price) / min(cost, price) <= 200`.
    - `CostAssessment.unit_factor` is always `1.0` from now on.
  - `comparable_price(link, yahoo_price) -> yahoo_price if link.cost_comparable else None`. There is no factor maths.
  - Issue `listing_mismatch`, appended after a successful probe when `link.currency.upper() != quote.currency`. It must not alter `status`, `cost_comparable` or removal logic.
  - `_attach` stores `link.quote_currency` (major) as `TrackedAsset.currency`.
- Remove `SyncPolicy.cost_magnitude_factor`, the setting, and the `magnitude_factor` parameter.

- [ ] **Step 1: Update and write tests (expect failures)**
  - In test_client.py, `q == YahooQuote("GBP", "LSE", 26.0, 0.01)` for the SHEL.L body that has `regularMarketPrice` 2600.
  - In test_currency.py:
    - `test_pence_typed_as_pounds_flags_suspect_units`: cost 2600 GBP, probe 26.0 major, factor 0.01 → suspect.
    - `test_long_held_winner_not_flagged`: cost 10, price 300 (30×), factor 0.01 → not suspect.
    - `test_usd_100x_not_flagged`: factor 1.0, ratio 100 → not suspect.
    - Delete `test_magnitude_factor_is_configurable`.
  - In test_sync.py:
    - Rename `test_created_asset_stores_raw_quote_unit` to `test_created_asset_stores_major_currency` (expects `"GBP"`).
    - Update `test_attached_asset_currency_corrected_to_quote_unit` likewise.
    - Add `test_listing_mismatch_warns_without_blocking`: Navigator currency `"EUR"`, quote `"USD"`, so the link `status == "active"`, `"listing_mismatch" in link.issues`, and the asset stays tracked.
- [ ] **Step 2: FAIL → Step 3: implement → Step 4:** `uv run pytest -q` all pass. Also grep for `quote_unit_factor` and `comparable_price` callers outside `navigator_sync` and confirm they no longer multiply.
- [ ] **Step 5: Commit** `fix(navigator-sync): compare in major units; minor-unit-only suspect check; listing_mismatch warning`

### Task 14: Currency-aware messages and templates, legacy-alert warning

**Files:**
- Modify: `app/notifications/telegram.py:22-24,41-42,74-75`
- Modify: `app/workers/price_monitor.py:73,77-87,112`
- Modify: `app/ui/templates/alerts.html:48-64`, `alert_form.html:99,115,120,201,205`, `alert_history.html:38`, `partials/quote.html:4,23,30`
- Modify: `app/ui/routes.py:748,~808-830` (`partial_quote` passes `currency`), plus wherever the Jinja env is built (register `format_price` as a global)
- Create: `app/startup_checks.py`; call it from `app/main.py` lifespan after `init_db()`
- Test: `tests/test_notifications/test_telegram_currency.py`, `tests/test_startup_checks.py`, extend `tests/test_workers/test_price_monitor.py`

**Interfaces:**
- Consumes: `format_price` (Task 11), `fetch_price_history` (Task 12).
- The Telegram format functions gain a `currency: str | None = None` keyword. Callers pass `asset.currency`.
- The price-monitor reason strings use `format_price(..., asset.currency)`. The absolute trail-mode label uses `CURRENCY_SYMBOLS.get(currency, currency)`.
- Templates call `format_price(value, rule.asset.currency)`, or the equivalent the context provides. Add `currency` to the context where the asset isn't reachable. The alert-form hint is replaced by the Global Constraints copy, and its JS `"$"` uses the selected asset's currency symbol (send `currency` in the `assets` list).
- `MINOR_UNIT_CUTOVER = datetime(2026, 11, 17, tzinfo=UTC)`.
- `async def warn_legacy_minor_unit_alerts(db: AsyncSession) -> list[str]` returns the affected symbols and logs `legacy_minor_unit_alerts` with them. It covers price-type rules (`alert_type in PRICE_ALERT_TYPES`) whose asset symbol ends in `.L`, `.TA` or `.JO`, with `created_at < MINOR_UNIT_CUTOVER`.

- [ ] **Step 1: Write failing tests**
  - `test_telegram_message_uses_shekel`: `"₪65.12" in text` and `"$" not in text`.
  - `test_price_monitor_reason_uses_asset_currency`.
  - `test_warn_legacy_minor_unit_alerts`: returns `["SHEL.L"]` for an old rule; returns `[]` for a new rule or a US asset.
  - A template render test, `GET` on the alerts page with an ILS rule: the response contains `₪` and contains no `$` immediately before a digit.
- [ ] **Step 2: FAIL → Step 3: implement → Step 4:** `uv run pytest -q` all pass. Then `grep -rnE '\$\{?[a-z_]*:?\.2f|"\$%|\$\{\{' app/` shows no price-format hits.
- [ ] **Step 5: Commit** `feat(ui,notifications): show prices in the asset's currency; warn on legacy minor-unit alerts`

---

## Finish

- Both full suites are green. Also run the Navigator frontend build.
- Update `docs/` and READMEs where they describe currency behaviour:
  - Navigator `CLAUDE.md` gotchas: minor units, the audit script and deploy order.
  - Trender README: the removed setting.
- Deploy order (for the PR bodies, not executed here):
  1. Navigator.
  2. `docker compose exec api python -m scripts.audit_asset_currencies`, then the same with `--apply`.
  3. Trender.
