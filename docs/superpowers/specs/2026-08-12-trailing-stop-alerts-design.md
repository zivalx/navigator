# Trailing Stop-Loss Alerts + Buy/Sell Triggers with Telegram Delivery

**Date:** 2026-08-12
**Status:** Approved
**Base branch:** `feat/indicators-alerts-history`

## Problem

Not all of the user's trading apps support trailing stop losses, and the user
is not watching them all the time. Navigator should act as an always-on
watcher: the user sets a trailing stop or a buy/sell trigger price here, the
backend tracks the market, and the user is told the moment it fires so they
can go execute in their broker app. Navigator never places orders.

## Requirements

1. **Trailing stop alerts** per asset, defined as either a percent trail or
   an absolute dollar trail below a ratcheting high-water mark.
2. **Buy/sell intent labels** on existing price-above/price-below alerts, so
   notifications say what to do (BUY the dip / SELL at target).
3. **Delivery:** Telegram DM (works when away), toast on entering the app,
   and persistent visibility (bell + alerts page) until acknowledged.
4. **Server-ready:** all configuration via env vars; no local-machine
   assumptions. Runs locally today, identically on the planned production
   server later. Deployment itself is out of scope.

## Design

### 1. Data model (`backend/app/models/alert.py`)

- `AlertRule` enum gains `TRAILING_STOP = "trailing_stop"`.
- New enum `AlertIntent { BUY = "buy", SELL = "sell" }`.
- New nullable columns on `price_alerts`:
  - `intent` — optional on price triggers; trailing stops default to `sell`.
  - `trail_percent` — e.g. `8.0` for an 8% trail.
  - `trail_amount` — e.g. `15.0` for a $15 trail.
  - `high_water_mark` — highest observed price since alert creation;
    initialized to the current quote price at creation time.
  - `notified_at` — timestamp of successful Telegram delivery.
- Exactly one of `trail_percent` / `trail_amount` must be set when
  `rule == trailing_stop`; both must be null otherwise. `threshold` remains
  required for price rules and null for trailing stops.
- The stop level is always **derived**, never stored:
  `hwm * (1 - trail_percent/100)` or `hwm - trail_amount`.
- **Migration:** the project uses `Base.metadata.create_all` (no Alembic).
  Add a small startup step that executes
  `ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS ...` for each new
  column. Additive and idempotent; safe locally and on a future server.

### 2. Evaluator (existing 5-minute job, `backend/app/tasks/scheduler.py`)

For each active `trailing_stop` alert, per cycle:

1. If quote price > `high_water_mark`: ratchet the mark up (never down),
   commit.
2. Compute the derived stop level. If price <= stop: trigger — set
   `is_active = False`, record `triggered_at` and `triggered_price`.

Price-above/price-below alerts behave exactly as today.

After evaluation, every alert that is triggered but has `notified_at IS NULL`
is handed to the notification service — including alerts whose Telegram send
failed on a previous cycle. A delivery failure therefore delays the message
by one cycle rather than losing it.

**Known limitation (accepted):** 5-minute polling means an intra-cycle
spike-and-crash can ratchet less than a broker-side TSL would, or trigger up
to one cycle late. Acceptable for "go open your broker app" semantics.

### 3. Notifications (`backend/app/services/notifications.py`, new)

- `NotificationService` with one channel for now: Telegram Bot API
  `sendMessage`.
- Config via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars, added to
  `.env.example` and passed through docker-compose (dev + prod). When unset,
  the service logs and skips — local use without a bot keeps working.
- Message format leads with the action:
  - `🔴 SELL — trailing stop hit: AAPL at $301.10 (high $327.50, trail 8% → stop $301.30)`
  - `🟢 BUY — target hit: NVDA ≤ $150.00 (now $149.20)`
- On success, set `notified_at`. On failure, log; the evaluator retries next
  cycle. Notification errors must never abort the evaluation loop.

### 4. API (`backend/app/routers/alerts.py`, `backend/app/schemas/`)

- `POST /api/alerts/` accepts `rule = trailing_stop` with
  `trail_percent` XOR `trail_amount` (422 on violation) and optional
  `intent`. On create, the backend fetches the current quote to initialize
  `high_water_mark`.
- Alert responses include `intent`, `trail_percent`, `trail_amount`,
  `high_water_mark`, and a computed `current_stop_price` so the UI can show
  "stop at $301.30, 2.1% away".
- Acknowledge and reactivate endpoints unchanged. Reactivating a triggered
  trailing stop re-initializes `high_water_mark` to the current price and
  clears `notified_at`.

### 5. Frontend (`frontend/src/components/alerts/`, alerts page)

- **CreateAlertDialog:** type selector — *Price trigger* (above/below +
  threshold + Buy/Sell intent) or *Trailing stop* (%/$ toggle + trail value;
  intent fixed to Sell). Live preview of the initial stop price computed from
  the current quote.
- **Alerts page:** intent badge per row (🟢 BUY / 🔴 SELL); trailing-stop
  rows show high-water mark, current stop, and distance to stop.
- **Toast on entry:** on app load, fetch triggered-unacknowledged alerts and
  raise a Sonner toast per alert (Toaster already mounted in `App.tsx`).
- **Bell:** existing `AlertsBell` remains the persistent indicator until the
  alert is acknowledged.

### 6. Testing (`backend/tests/test_alerts.py` + new)

- High-water-mark ratchets up, never down.
- Percent-trail and amount-trail trigger at the exact boundary (inclusive).
- Validation: XOR of trail fields; threshold required for price rules.
- Notification formatting for both intents and both rule families.
- Failed Telegram send leaves `notified_at` null and is retried next cycle;
  evaluation continues past notification errors.
- Manual end-to-end check: create a TSL with a tight trail on a liquid
  symbol, watch it ratchet and fire, receive the Telegram message.

## Out of scope

- Order execution of any kind (Navigator only notifies).
- Indicator-based alerts (RSI, MA cross), repeating alerts, % daily-move
  alerts.
- Multi-user auth / per-user Telegram routing (single-user app).
- Production server deployment (tracked separately in `TODO-DEPLOYMENT.md`).

## Operational note

Alerts evaluate only while the backend is running. Until the production
server exists, that means while the Mac is awake with Docker up. The design
requires no changes to become 24/7 — only deployment.
