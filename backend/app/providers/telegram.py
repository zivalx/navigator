"""Telegram Bot API provider.

Per the architecture conventions, providers are the only layer that talks to
external APIs — this one sends messages via a bot created with @BotFather.
"""
import logging

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# One long-lived client for the process (created lazily inside the running
# event loop) instead of a TCP+TLS handshake per message.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


class TelegramProvider:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def send_message(self, text: str) -> bool:
        """Send a message to the configured chat. Returns True on success.

        Never raises — a delivery failure is logged and reported as False so
        the caller can retry later.
        """
        try:
            response = await _get_client().post(
                API_URL.format(token=self.bot_token),
                json={"chat_id": self.chat_id, "text": text},
            )
            response.raise_for_status()
            return True
        except Exception as e:
            # The bot token is embedded in the request URL, so httpx exception
            # messages can contain it — log only the exception type / status,
            # never the full error string.
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.warning(
                "Telegram send failed: %s%s",
                type(e).__name__,
                f" (HTTP {status})" if status else "",
            )
            return False
