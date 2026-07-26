# Market Indicators — Design

**Date:** 2026-07-23
**Status:** Approved for implementation (user requested: "indicators like fear and greed, vix, s&p and more")

## Goal

Add a market-indicators layer to Navigator: sentiment gauges (CNN Fear & Greed, Crypto Fear & Greed), volatility (VIX), major indices, rates, dollar, commodities, and BTC — surfaced as a customizable strip at the top of the Markets page.

## Backend

### New endpoint

`GET /api/markets/indicators?keys=vix,sp500,...` (keys optional; omitted = all)

Response:

```json
{
  "as_of": "2026-07-23T14:30:00Z",
  "indicators": [
    {
      "key": "fear_greed_stocks",
      "label": "Fear & Greed",
      "category": "sentiment",
      "value": 38.0,
      "unit": "index",
      "rating": "fear",
      "change": -4.0,
      "change_pct": null,
      "source": "cnn",
      "error": null
    },
    {
      "key": "vix",
      "label": "VIX",
      "category": "volatility",
      "value": 18.42,
      "unit": "points",
      "rating": null,
      "change": -0.35,
      "change_pct": -1.87,
      "source": "yahoo",
      "error": null
    }
  ]
}
```

- A failing indicator returns its entry with `value: null` and `error` set; the endpoint never 500s because one source is down.
- `rating` only for sentiment indicators: extreme_fear | fear | neutral | greed | extreme_greed.

### Indicator registry (v1)

| key | label | category | source | notes |
|---|---|---|---|---|
| fear_greed_stocks | Fear & Greed | sentiment | CNN dataviz API | score 0–100 + rating; change vs previous close |
| fear_greed_crypto | Crypto Fear & Greed | sentiment | alternative.me `/fng/` | score 0–100 + classification; change vs yesterday |
| vix | VIX | volatility | Yahoo `^VIX` | |
| sp500 | S&P 500 | index | Yahoo `^GSPC` | |
| nasdaq | Nasdaq | index | Yahoo `^IXIC` | |
| dow | Dow Jones | index | Yahoo `^DJI` | |
| russell2000 | Russell 2000 | index | Yahoo `^RUT` | |
| stoxx50 | Euro Stoxx 50 | index | Yahoo `^STOXX50E` | |
| dax | DAX | index | Yahoo `^GDAXI` | |
| smi | SMI (Swiss) | index | Yahoo `^SSMI` | |
| nikkei | Nikkei 225 | index | Yahoo `^N225` | |
| us10y | US 10Y Yield | rates | Yahoo `^TNX` | Yahoo quotes yield×10 → divide value/change by 10, unit "%" |
| us30y | US 30Y Yield | rates | Yahoo `^TYX` | same ×10 scaling, unit "%" |
| s5fi | S&P 500 % Above 50-Day MA | breadth | computed | see Breadth section |
| s5th | S&P 500 % Above 200-Day MA | breadth | computed | see Breadth section |
| dxy | Dollar Index | fx | Yahoo `DX-Y.NYB` | |
| gold | Gold | commodities | Yahoo `GC=F` | |
| oil_wti | Crude Oil (WTI) | commodities | Yahoo `CL=F` | |
| btc | Bitcoin | crypto | Yahoo `BTC-USD` | |

### Breadth indicators (S5FI / S5TH)

There is no free API for the S&P DJ breadth indices ($S5FI/$S5TH on StockCharts), so we compute them:

- Constituents: fetch the S&P 500 member list from Wikipedia ("List of S&P 500 companies", first table, via `pandas.read_html` — pandas ships with yfinance) with a bundled fallback list at `app/data/sp500_constituents.txt` (one symbol per line, checked in) if the fetch fails. Dots in symbols (BRK.B) become dashes for Yahoo (BRK-B).
- Computation: batch-download ~1 year of daily closes for all constituents via `yf.download` in chunks (~100 symbols per call). For the latest trading day and the previous one, compute the percentage of constituents whose close is above their 50-day SMA (s5fi) and 200-day SMA (s5th). Skip symbols with insufficient history rather than failing.
- Scheduling: computed in the background — an APScheduler job after the existing EOD snapshot, plus a run shortly after startup if the cache is cold. Results (value, previous value, computed_at) stored in Redis without expiry (overwritten daily).
- Serving: the indicators endpoint reads only the cached value (never computes inline — computation takes minutes). If the cache is cold, the entry returns `value: null, error: "breadth not yet computed"`.
- `value` is 0–100, unit "%", `change` = value − previous day's value.

### Implementation

- `app/providers/cnn.py` — fetch `https://production.dataviz.cnn.io/index/fearandgreed/graphdata` with a browser-like `User-Agent` (CNN blocks default clients). Parse `fear_and_greed` block: score, rating, previous_close.
- `app/providers/alternative_me.py` — fetch `https://api.alternative.me/fng/?limit=2` (no key). Today's value + classification, change vs yesterday.
- `app/services/indicators.py` — `IndicatorsService`: registry above; Yahoo-backed indicators fetched via the existing Yahoo provider quote path (batch where possible); sentiment providers called directly. Redis-cache the sentiment fetches (`indicators_cache_ttl`, default 900s); Yahoo quotes already ride the 60s quote cache.
- `app/routers/markets.py` — add `GET /indicators` following existing router style.
- `app/config.py` — add `indicators_cache_ttl: int = 900`.

### Tests (bootstraps backend test suite)

`backend/tests/` with pytest + pytest-asyncio (already in requirements):
- CNN/alternative.me parsers against canned JSON fixtures.
- `^TNX`/`^TYX` ÷10 scaling.
- Endpoint shape + partial-failure behavior (one provider raising → entry has `error`, others fine) with mocked providers.

## Frontend

- `src/components/markets/IndicatorsStrip.tsx` — strip at top of Markets page: two Fear & Greed gauges + compact tiles (value, change, color-coded) for the rest.
- `src/components/markets/FearGreedGauge.tsx` — semicircular SVG gauge, 0–100, colored zones (extreme fear → extreme greed), needle at current value, rating label. Follow the dataviz skill guidance; must work in light + dark themes.
- `src/components/markets/IndicatorTile.tsx` — compact tile: label, value (formatted per unit), change/change_pct with green/red arrow.
- Customization: gear/"Customize" button opens a dialog with checkbox list (grouped by category — sentiment, volatility, breadth, index, rates, fx, commodities, crypto) to choose visible indicators + reset to defaults. Persist selection in `localStorage` (`navigator-indicators` key), same pattern as MarketCardsContext. Defaults: fear_greed_stocks, fear_greed_crypto, vix, s5fi, sp500, nasdaq, us10y, dxy, gold, btc.
- Data: React Query (`useQuery` on `/api/markets/indicators?keys=...`), 60s refetch interval, loading skeletons, per-tile error state (dash + tooltip) when `error` set.
- Types added to `src/lib/types.ts`; API call added to the existing API client layer.
- No changes to Dashboard in v1 (strip component is reusable later).

## Error handling

- Backend: per-indicator try/except; sentiment provider timeouts of 10s; log warnings.
- Frontend: strip renders whatever came back; a fully failed request shows a single inline error card, not a blank page.

## Out of scope (YAGNI)

- Historical indicator charts, put/call ratio, breadth internals, per-user server-side preferences, dashboard placement.
