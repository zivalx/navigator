# Tel Aviv (TASE) and shekel support — design

Date: 2026-11-17. Repos: **navigator** (branch `feat/tase-ils`, stacked on `feat/export-positions` / zivalx/navigator#2) and **trender** (branch `feat/tase-ils`, stacked on `feat/navigator-sync` / zivalx/trender#4).

## 1. Intent

The owner holds and follows Tel Aviv–listed shares and Israeli ETFs and wants them to work end to end:
- Navigator (the source of truth for holdings) values them correctly in shekels.
- Trender (signals) tracks them and speaks about their prices in shekels.

The owner's words: "start with what we have… and do it correctly". Correctness beats breadth. No silent wrong numbers.

Recon found that the same failure class already breaks London listings, which Yahoo quotes in pence (`GBp`). Fixing minor units once fixes both.

## 2. Goals

1. A Tel Aviv share or ETF (`TEVA.TA`, `TA35.TA`-style ETFs) can be found in search, starred, held with lots in ILS, valued, charted from history and alerted on, all in **₪, major units**.
2. Prices quoted by Yahoo in a **minor unit** are converted to the major unit at the moment they enter either app, and nothing downstream ever sees a minor unit. The minor units are `ILA` (agorot), `GBp`/`GBX` (pence) and `ZAc` (cents).
3. ILS is a first-class currency in Navigator: assets, lots, cash, price snapshots, FX and the display base currency. IL is a market region.
4. An asset's currency, exchange and region come from Yahoo when it is added. Today they come from client defaults, which are always USD/US.
5. No silent wrong money:
   - a missing FX rate makes a value **unavailable** rather than unconverted;
   - an unstorable currency is logged, never swallowed.
6. Trender follows the exact listing Navigator holds (the `.TA` one, not the NYSE one) and warns when they disagree.
7. Every price shown or messaged uses the asset's currency symbol, never a hardcoded `$`.

## 3. Non-goals

- Israeli mutual funds (קרנות נאמנות). They are not on Yahoo and need another price source.
- Israeli market overview: movers, breadth, TA-125 index cards, region tabs with Israeli content. The IL region must be *accepted everywhere* but needs no content.
- Trender discovery of `.TA` symbols from influencer mentions (cashtags, SEC ticker list).
- Historical FX (purchase-date rates). Navigator applies the current rate to all history today, and that stays.
- Backfill for assets added after the first backfill run (existing `eod_count >= 5` gate). This is pre-existing and affects all assets equally.
- Trading-calendar logic. TASE trades Mon–Fri as of 2026 (verified from Yahoo bars), and both apps' jobs already run after the TASE close.
- Removing trender's navigator-sync removal machinery (architect review F7), and migrations in trender production (F10).

## 4. Anti-goals (would make this a failure even if it "works")

- Any path where a price in agorot or pence is treated as shekels or pounds, or the reverse: the 100× class.
- Any path where an ILS amount is added into a USD total unconverted: the 3.2× class.
- Silently rewriting the owner's money data. Lot costs are never changed automatically.
- A Tel Aviv holding quietly disappearing from the portfolio total.

## 5. Facts this design relies on (verified 2026-11-17)

**Yahoo**
- `chart` for `TEVA.TA` returns `meta.currency = "ILA"`, `exchangeName = "TLV"` and timezone `Asia/Jerusalem`.
- `search` returns `exchange: "TLV"` and **no currency**.
- A 1y daily range is about 246 bars.

**Navigator schema and FX**
- Navigator `Currency` and `MarketRegion` are **native Postgres enums**, with type names `currency` and `marketregion`.
- The schema is built by `create_all` in `init_db()` (`database.py`), which never alters an existing type. There is no Alembic.
- The one precedent for adding an enum value is `_migrate_price_alerts()`: `ALTER TYPE … ADD VALUE IF NOT EXISTS` on an AUTOCOMMIT connection. Its gate ("new columns missing") cannot be reused.
- Prod runs gunicorn with 2 workers, and both run `init_db()`.
- The FX keyless endpoint returns ILS. The keyed v6 endpoint is documented to support ILS (not exercised).

**What fails today with a minor unit**
- `PriceSnapshot.currency` write: rejected by Postgres, swallowed.
- `portfolio.py:92` `Currency(...)`: the holding is dropped from NAV.
- EOD and backfill: they fail quietly.
- Alert asset creation: falls back to USD.

**Asset creation paths**
- Search star and watchlist add both create assets **in the browser** via `POST /assets/`, with USD/US defaults (`GlobalSearch.tsx:136-146`, `WatchlistTable.tsx:121-130`). The watchlist router itself only takes an existing `asset_id`.
- Alert resolve-or-create builds assets in the backend (`routers/alerts.py:125-176`).
- In every path, the backend stores what it is given.

**Navigator quote paths**
- Navigator fetches no OHLC bars. `get_quote` returns price, absolute change and percent change (`yahoo.py:53-62`).
- Backfill reads daily closes (`scheduler.py:178-205`); watchlist history reads percentages only.
- The quote fallbacks are Yahoo → Polygon → Finnhub. Alpha Vantage is used only for movers.

**Navigator totals and lots**
- The dashboard computes portfolio totals **in the browser** from `/portfolio/holdings`, a bare list (`PortfolioContext.tsx:224-262`). `/portfolio/summary` has no caller.
- `costBaseOf` falls back to the native `avgCost` when `avgCostBase` is missing (`PortfolioContext.tsx:232-233`).
- The lot edit dialog cannot change a lot's cost currency (`PortfolioTable.tsx:101-112`), although the backend accepts `costCurrency`.

**Navigator alert prices**
- Price alerts compare raw quote prices every 5 minutes (`scheduler.py:280-292, 426-434`).
- Stored price fields:
  - `price_alerts.threshold`, `trail_amount`, `high_water_mark` and `triggered_price`;
  - `watchlist_items.target_price`.

**Running scripts in prod**
- Prod Postgres isn't published to the host, and the API image is built from `./backend`. Maintenance scripts therefore live in `backend/scripts/` and run inside the API container.

**FX failure**
- Five places in `services/portfolio.py` treat an FX failure as "already in base currency".
- A sixth, `get_grouped_holdings` (`:148`), has no `except`, so it raises.

**Trender**
- The price cache is in memory only.
- `TrackedAsset.currency` is a free string, written today only by navigator-sync (raw unit, e.g. `ILA`, after the F4 fix).
- The local trender DB has 5 assets and none of them is `.L`, `.TA` or `.JO`. Trender is not yet deployed.
- Trender formats every price with a hardcoded `$` (Telegram, price monitor, templates).

## 6. Decisions

### 6.1 Minor units → major at the boundary (owner's choice: shekels, ₪65.12)

Each app gets **one** function and calls it everywhere a Yahoo price enters:

```
normalize_quote_currency(code) -> (major_code, factor)
  "ILA" -> ("ILS", 0.01); "GBp","GBX" -> ("GBP", 0.01); "ZAc" -> ("ZAR", 0.01)
  exact-case match first; case-insensitive fallback only for GBX/ILA/ZAC (so "GBP" stays GBP); else (CODE.upper(), 1.0)
```

The rules are the same as trender's existing `navigator_sync/currency.py`.

**Applied to (Navigator):**
- the Yahoo quote's price and absolute change;
- backfill closes.

**Applied to (trender):**
- every OHLC value from `fetch_price_data`;
- the price monitor, the volume indicator and any other direct Yahoo chart call;
- the navigator-sync probe.

**Not applied to:** volume and percentages.

After this, the quote's `currency` field is the major code, and the quote also carries `unit_factor`, which is 0.01 when Yahoo quoted a minor unit and 1.0 otherwise (used in 6.8).

In trender, `navigator_sync` stops doing its own `×factor` comparison maths: prices are already major. Its `quote_unit`/`assess_cost` logic collapses to "cost currency must equal the asset's major currency". The `suspect_units` check becomes **minor-unit-only**: it flags when the Yahoo quote was in a minor unit and cost and price differ by a factor of about 100 (between 50× and 200×). That is exactly the "cost typed in agorot/pence" mistake (architect review F5), and long-held winners are no longer flagged.

### 6.2 ILS and IL in Navigator

**Enums.**
- Add `ILS` to `Currency` and `IL` to `MarketRegion`, in the model, the schemas (all copies, including `schemas/market.py`) and the frontend `lib/types.ts` unions.
- Also add the currencies of the other venues in the 6.3 map: `HKD CNY KRW TWD SGD INR SEK DKK NOK AUD CAD ZAR`. Without them, deriving the currency from Yahoo would turn today's (wrongly USD-labelled) watch-only adds into 422s, a regression.
- These additions use the same migration. FX and `Intl` already handle them. They appear in the Settings and "Other" currency lists only as needed: Settings gets ILS; "Other" gets ILS plus the missing JPY.

**Prod DB migration**, in `init_db()`, as a new `_migrate_enum_values()`:
- Postgres only. It runs on every start.
- Uses an AUTOCOMMIT connection.
- Takes `pg_advisory_lock(<constant>)` to serialise the two gunicorn workers.
- Then runs `ALTER TYPE currency ADD VALUE IF NOT EXISTS '<code>'` for every new currency code, and `ALTER TYPE marketregion ADD VALUE IF NOT EXISTS 'IL'`.
- Then unlocks.
- Idempotent by construction, with no gate. It must run **after** `create_all`; on a fresh DB `create_all` already creates the types with the new values.

**FX:** ILS goes through the existing FX service unchanged. **Base currency:** ILS is added to the Settings list.

### 6.3 Asset identity derived from Yahoo on creation

**`POST /assets/` for `stock`/`etf`/`fund`.** The backend fetches Yahoo chart meta for the symbol and sets:
- `currency` = the normalized major code. The asset is rejected with a clear 422 if that currency is still not in `Currency` after 6.2, rather than stored as USD.
- `exchange` = `fullExchangeName`/`exchangeName`.
- `market_region`, from a small exchange-code map: `TLV`→IL; US venues (`NMS NYQ NGM NCM ASE PCX BTS`)→US; European venues (`LSE GER PAR AMS MIL MCE EBS STO CPH HEL OSL BRU LIS VIE IOB`)→EU; Asian venues (`TYO HKG SHH SHZ KSC TAI SES NSI BSE`)→ASIA. An unknown venue keeps the client's value and is logged.
- Client-sent currency/region/exchange are ignored for these types.
- If Yahoo is unreachable, the request fails with 503 "couldn't verify symbol, try again". It never stores defaults.

**Other paths.**
- Search star and watchlist add already go through `POST /assets/`, so they are covered.
- Alert resolve-or-create (backend) calls the same derivation helper.
- The frontend stops sending currency/region defaults for these types.
- Crypto and `other` are unchanged; `other` keeps the user-chosen currency.

**Yahoo search `quoteType` `INDEX`.** It is not starrable (the star is hidden or disabled for it), so `^TA125.TA` cannot hit a 422. Index support is a non-goal.

### 6.4 Lots in ILS

- The add-lot form keeps `costCurrency = asset.currency`; with 6.3 that is ILS for TASE.
- The form shows the currency symbol next to the price field.
- For a minor-unit market (ILS, GBP) it shows a hint: "price per share in shekels (₪), not agorot".
- It shows a soft, non-blocking warning when the entered price is 50–200× the current price.
- The **lot edit dialog gains a cost-currency field**, so a mislabelled lot can be fixed in place. The backend already accepts it.
- A lot whose `cost_currency` differs from its asset's currency shows a small "currency differs from listing" badge in the portfolio table. This is legitimate in general (e.g. a USD-funded purchase of a EUR listing), so it is only a hint.
- **Exception:** an ILS asset with a non-ILS lot has no realistic legitimate case; it is the known result of the old USD default. Such a holding is treated as **unvalued** by the same rule as 6.5 (`valueUnavailable`, reason `lot_currency_check`) until the lot is fixed.

### 6.5 No silent wrong money in Navigator

**FX failure: live views.** In the five `except` paths and in `get_grouped_holdings`, an FX failure no longer passes the native amount through as base currency.
- Each holding in `/portfolio/holdings` (and grouped holdings) gains `valueUnavailable: bool` and `unavailableReason` (`fx_unavailable` | `lot_currency_check`). Base-currency fields are null when it is set. This is an additive change to the list items.
- The frontend (`PortfolioContext`) excludes such holdings from every total.
- `costBaseOf` stops falling back to the native `avgCost` when the holding's currency differs from the base.
- The dashboard shows "Totals exclude N holdings (no exchange rate / check lot currency)", listing their symbols.
- `/portfolio/summary` applies the same rule, so it stays consistent if it ever gets used.

**FX failure: stored history.** `compute_daily_snapshot` writes a permanent daily row. If any held currency has no rate, that day's portfolio snapshot is **skipped**, with a warning log, rather than written with a holding missing. Gaps are already handled by omission, and a fake dip in history is worse than a missing day.

**Snapshot storage.** `_store_price_snapshot` logs at warning level when it cannot store (it currently swallows silently). With 6.1 this should no longer happen for supported markets.

**Provider fallbacks.** Polygon and Finnhub hardcode `currency: "USD"`. For an asset whose stored currency is not USD, their quotes are **not used**; the chain falls through to the last DB price. That prevents an ILS asset being priced by a USD-labelled number.

### 6.6 Currency-aware display

**Navigator frontend.** Replace hardcoded `$` with the asset or quote currency via the existing `Intl.NumberFormat` helpers in:
- `MarketAssetCard.tsx`;
- `WatchlistTable.tsx`;
- `AddHoldingDialog.tsx`;
- `alertUtils.ts`;
- `CreateAlertDialog.tsx`.

`Intl` renders ILS as ₪.

**Navigator notifications.** `services/notifications.py` gets the same treatment.

**IL region in the frontend.** Add IL to `AssetBadge`/`RegionBadge` (it would throw on an unknown region), the market-card region lists and the region tabs. The tab may be empty.

**Trender.**
- A small `format_price(value, currency)` helper is used by:
  - Telegram (`notifications/telegram.py`);
  - `workers/price_monitor.py`;
  - the templates (`alerts.html`, `alert_form.html`, `alert_history.html`, `partials/quote.html`);
  - `ui/routes.py`.
- `TrackedAsset.currency` becomes the **major** code, for both sync-created and user-added assets.
- When trender's price layer fetches chart meta and the asset's stored currency differs from the normalized major code, it updates the stored currency. This replaces the F4 "raw unit" rule now that prices are major.
- The `alert_form.html` pence hint is replaced by "prices in the asset's currency (₪ for Tel Aviv, £ for London)".

### 6.7 Exact listing across the two apps (architect review F6)

- **No contract change.** Navigator's `symbol` for stocks, ETFs and funds is already the Yahoo symbol (`TEVA.TA`), since search returns Yahoo symbols and Navigator prices by `symbol`. A separate `provider_symbol` would always equal it, and would be wrong for crypto (Navigator `BTC`, Yahoo `BTC-USD`), so none is added. Trender keeps its existing mapping.
- **Trender.**
  - On every probe it compares Navigator's `currency` with Yahoo's normalized major currency.
  - On a mismatch it records the issue `listing_mismatch` on the link: a **warning only**, which never blocks tracking or removes anything. Legacy Navigator data, such as ADRs seeded as EUR, must not cause deactivations.

### 6.8 Existing data: audit, don't auto-rewrite

**Alert safety across the deploy.** Price data saved before the deploy for minor-unit listings was compared against raw pence/agorot quotes, so after 6.1 it is 100× too high. To stop a false trigger in the window between deploy and audit:
- **Cutover marker.** On first start, `init_db()` writes `maintenance_runs('minor_units_cutover', at=now)`. The new `maintenance_runs` table is created by `create_all`, and the marker is written once.
- **Paused alerts.** `evaluate_price_alerts` **skips** any price alert created before the cutover whose current quote has `unit_factor != 1`, until the marker `minor_unit_alerts_converted` exists. It logs one warning per run naming the paused symbols.
- **Unaffected.** Alerts created after the cutover are in major units, so they evaluate normally.

**New script `backend/scripts/audit_asset_currencies.py`.** It runs inside the API container: `docker compose exec api python -m scripts.audit_asset_currencies`.
- **Dry run (default).** It **prints symbols and codes only, never quantities or costs**. For each stock/etf/fund asset it fetches Yahoo meta and reports:
  - asset currency/region/exchange vs Yahoo-derived values;
  - lots whose `cost_currency` differs from the derived currency;
  - pre-cutover price data on minor-unit assets.
- **`--apply`** does the following:
  - fixes asset currency/region/exchange;
  - converts, **only for rows created before the cutover**, these fields ×0.01:
    - `price_alerts.threshold`, `trail_amount`, `high_water_mark` and `triggered_price`;
    - `watchlist_items.target_price`. If the row has no creation time, it converts all rows on minor-unit assets.
  - writes `minor_unit_alerts_converted`, which both un-pauses those alerts and makes a second `--apply` a no-op for this step.

**Lots are never changed automatically.** Mismatched lots are listed for the owner to fix with the lot edit dialog (6.4).

**Trender.** No stored price data exists in minor units (verified locally; not deployed). The trender plan adds a startup log warning if any price-type `alert_rules` row exists on an asset whose symbol ends in `.L`/`.TA`/`.JO` and was created before this change. There is no automatic conversion.

## 7. Approaches considered

1. **Convert to major units at the boundary — chosen.** One normalizer per app at every Yahoo entry point; ILS/IL added to Navigator; identity derived from Yahoo. Every downstream sum, alert, chart and message deals in one unit. The cost is touching every Yahoo entry point once, plus the frontend `$` cleanup.
2. **Store minor units as currencies of their own (`ILA`, `GBp` in the enum) and convert at valuation/display.** Rejected: every money operation would need to know the ÷100 rule, the FX API has no `ILA`, and it is the most likely route to a 100× bug.
3. **Minimal: add ILS and let the owner type `.TA`.** Rejected: search-added assets stay USD/US, values stay 100× off, and failures stay silent.

Display unit: shekels (₪65.12), the owner's choice over agorot, consistent with Navigator.

## 8. Blast radius

**Navigator**
- Backend:
  - enums and the `database.py` migration, plus the cutover marker;
  - Yahoo provider, `market_data.py` (fallback rule), scheduler EOD/backfill/alert evaluation;
  - `routers/assets.py`, `routers/alerts.py`;
  - `services/portfolio.py` (holding flags, EOD skip), `services/notifications.py`;
  - the new `backend/scripts/audit_asset_currencies.py` and the `maintenance_runs` model.
- Frontend:
  - types, Settings, AddHoldingDialog, GlobalSearch, WatchlistTable;
  - `PortfolioContext` (exclusion, `costBaseOf`), `PortfolioTable` (lot currency edit and badge);
  - MarketAssetCard, alert UI, badges and region lists, plus a dashboard exclusion banner.
- US/USD assets must behave identically: factor 1, the same code paths.

**Trender**
- Price layer (all Yahoo chart callers), `navigator_sync` (client, currency, sync, mapping), formatting helper and templates, Telegram, price monitor.

**Contract.** Unchanged (`schema_version` 1). New enum values (`ILS`, `IL`, …) arrive as plain strings, which trender already accepts.

**Deploy order**
1. Navigator first: the enum migration and the cutover marker run on start. Minor-unit alerts stay paused.
2. Run the audit dry run, then `--apply`.
3. Then trender.

Trender works against either Navigator version.

## 9. Testing

**Unit (both apps)**
- Normalizer table, including case rules (`GBP` stays GBP and `GBX`/`gbx` → GBP).
- Quote and history normalization (price ÷100, percent unchanged).

**Navigator (SQLite suite)**
- ILS lot valuation in USD and ILS base.
- FX failure gives `valueUnavailable` (holdings and grouped holdings) with null base fields.
- The EOD portfolio snapshot is skipped on a missing rate.
- An ILS asset with a USD lot gets `lot_currency_check`.
- Snapshot stored with ILS.
- Asset creation derives currency/region/exchange for stock/etf/fund (Yahoo mocked), plus the 503 and 422 paths.
- Fallback provider skipped for non-USD assets.
- Pre-cutover minor-unit alerts are paused; post-cutover alerts evaluate normally.
- Audit script dry run, `--apply` (cutover filter, all listed fields, marker idempotency), and its output contains no quantities or costs.

**Navigator Postgres check**
- An opt-in test (skipped unless `TEST_POSTGRES_URL` is set) for `_migrate_enum_values` on a DB created with the *old* enums.
- It also runs twice concurrently.
- The executor runs it on a scratch DB.

**Trender**
- Normalized price fetch.
- Asset currency update.
- `format_price` (₪, £, $).
- Sync: `listing_mismatch` warning (never blocks), the new `suspect_units` rule (100× flagged, 30× winner not flagged).
- Frontend: there is no test setup. The executor verifies the exclusion banner, lot-currency edit and ₪ rendering in the browser preview.

**Regression:** both full suites stay green.

## 10. Left to the builder

- Exact placement and naming of the normalizer and `format_price` helpers.
- The advisory-lock constant.
- How `valueUnavailable`/`unavailableReason` are threaded through the existing holding schemas (additive), and the exact banner component.
- The exact UI wording, within the hints above.
- Whether the trender price layer returns currency alongside bars or via a separate meta call (prefer one call).
