# Price Alerts / Stop-Loss — Design

**Date:** 2026-07-23
**Status:** Approved for implementation (user: "ability to set metrics like stop loss and be alerted (in app) when hit, or price alert")

## Goal

User sets price rules on any asset (stop-loss = price falls below X, target = price rises above X). The backend evaluates rules periodically; the frontend surfaces triggered alerts in-app (header bell + toast). No email/push in v1.

## Data model

New table `price_alerts` (SQLAlchemy model `PriceAlert`, auto-created via `Base.metadata.create_all` like the rest — no Alembic in this project):

- `id`, `asset_id` FK→assets, `rule` enum: `price_below` | `price_above`
- `threshold` (numeric), `note` (optional str, e.g. "stop loss")
- `is_active` bool (default true) — one-shot in v1: set false when triggered
- `created_at`, `triggered_at` (nullable), `triggered_price` (nullable), `acknowledged_at` (nullable)

## API — `/api/alerts`

> **Note (as built):** API request/response fields use camelCase (`assetId`, `isActive`, `createdAt`, `triggeredAt`, `triggeredPrice`, `acknowledgedAt`), matching the codebase's existing watchlist/asset endpoint conventions. GET responses include joined `symbol`/`name` from the asset. The snake_case names below describe the DB columns.

- `GET /` — all alerts, optional `?status=active|triggered|unacknowledged`
- `POST /` — {asset_id | symbol, rule, threshold, note?} (symbol convenience: resolve/create asset like watchlist add does)
- `PUT /{id}` — edit threshold/note/rule; reactivate (`is_active: true` clears triggered_at/acknowledged_at)
- `DELETE /{id}`
- `POST /{id}/acknowledge` — sets acknowledged_at

## Evaluation

- APScheduler job every 5 minutes: load active alerts, batch-fetch quotes via MarketDataService (rides the 60s quote cache), compare price vs rule, on trigger set `triggered_at`, `triggered_price`, `is_active=false`.
- Crypto triggers 24/7; equities just won't move off-hours — no market-hours logic in v1.

## Frontend

- **Header bell** (in `AppLayout` header, next to theme toggle): badge = count of triggered+unacknowledged alerts. Popover lists them (symbol, rule text "AAPL below $180", triggered price, when) with per-item Acknowledge and "Acknowledge all". Link "Manage alerts" → `/alerts`.
- **`/alerts` page**: table of all alerts (asset, rule, threshold, status active/triggered, note, created), create/edit/delete/reactivate. Route + sidebar nav entry.
- **Create-alert entry points**: "Add alert" in the row dropdown menu on the Portfolio table and Watchlist table (dialog pre-filled with symbol + current price; quick presets: "Stop loss −5%/−10% from current", "Target +10%").
- **Polling & toasts**: React Query polls `GET /api/alerts?status=unacknowledged` every 60s; when a new triggered alert appears vs previous poll, fire a toast (sonner, already in the app).
- Types in `src/lib/types.ts`; API functions in existing client layer.

## Tests

Backend: rule-evaluation unit tests (below/above, exact-threshold boundary: trigger on `<=` / `>=`), acknowledge flow, endpoint CRUD with mocked market data.

## Out of scope (v1)

Email/push notifications, recurring/re-arming alerts, %-change or indicator-based rules (VIX > X), alert history page.
