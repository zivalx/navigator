"""Outbound notifications for triggered alerts.

One channel for now: Telegram Bot API. Configured via TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID env vars; when unset the service is disabled and sends are
skipped (logged once per process), so local use without a bot keeps working.

See docs/superpowers/specs/2026-08-12-trailing-stop-alerts-design.md.
"""
import logging

import httpx

from ..config import settings
from ..models.alert import AlertIntent, AlertRule, PriceAlert

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

_warned_disabled = False


def format_alert_message(alert: PriceAlert) -> str:
    """Action-first, human-readable trigger message.

    Examples:
        🔴 SELL — trailing stop hit: AAPL at $301.10 (high $327.50, trail 8% → stop $301.30)
        🟢 BUY — target hit: NVDA ≤ $150.00 (now $149.20)
    """
    symbol = alert.asset.symbol if alert.asset else "?"
    price = alert.triggered_price

    intent = alert.intent
    if intent is None and alert.rule == AlertRule.TRAILING_STOP:
        intent = AlertIntent.SELL

    if intent == AlertIntent.BUY:
        prefix = "🟢 BUY"
    elif intent == AlertIntent.SELL:
        prefix = "🔴 SELL"
    else:
        prefix = "🔔 ALERT"

    if alert.rule == AlertRule.TRAILING_STOP:
        if alert.trail_percent is not None:
            trail = f"trail {alert.trail_percent:g}%"
        else:
            trail = f"trail ${alert.trail_amount:,.2f}"
        stop = alert.stop_price
        body = (
            f"trailing stop hit: {symbol} at ${price:,.2f} "
            f"(high ${alert.high_water_mark:,.2f}, {trail}"
            + (f" → stop ${stop:,.2f}" if stop is not None else "")
            + ")"
        )
    else:
        op = "≤" if alert.rule == AlertRule.PRICE_BELOW else "≥"
        body = f"target hit: {symbol} {op} ${alert.threshold:,.2f} (now ${price:,.2f})"

    message = f"{prefix} — {body}"
    if alert.note:
        message += f"\n📝 {alert.note}"
    return message


class NotificationService:
    """Sends alert-trigger notifications via Telegram."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.bot_token = bot_token if bot_token is not None else settings.telegram_bot_token
        self.chat_id = chat_id if chat_id is not None else settings.telegram_chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str) -> bool:
        """Send a Telegram message. Returns True on success.

        Never raises — a delivery failure is logged and reported as False so
        the caller can retry on the next evaluation cycle.
        """
        global _warned_disabled
        if not self.enabled:
            if not _warned_disabled:
                logger.info(
                    "Telegram notifications disabled "
                    "(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable)"
                )
                _warned_disabled = True
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    TELEGRAM_API_URL.format(token=self.bot_token),
                    json={"chat_id": self.chat_id, "text": text},
                )
                response.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
            return False
