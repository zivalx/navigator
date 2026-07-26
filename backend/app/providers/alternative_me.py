from typing import Dict, List, Optional
import httpx


def normalize_rating(classification: Optional[str]) -> Optional[str]:
    """Map alternative.me's `value_classification` strings to our rating enum:
    extreme_fear | fear | neutral | greed | extreme_greed.
    """
    if not classification:
        return None
    return classification.strip().lower().replace(" ", "_")


class AlternativeMeFearGreedProvider:
    """alternative.me Crypto Fear & Greed Index provider. No API key required."""

    URL = "https://api.alternative.me/fng/"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def parse(data: Dict) -> Dict:
        """Parse the raw alternative.me JSON payload into a normalized dict.

        Expects `data` (a list, most-recent first) with today's entry at
        index 0 and yesterday's at index 1.
        """
        entries: List[Dict] = data.get("data") or []

        if not entries:
            return {"value": None, "rating": None, "change": None}

        today = entries[0]
        value = today.get("value")
        try:
            value = float(value) if value is not None else None
        except (TypeError, ValueError):
            value = None

        rating = normalize_rating(today.get("value_classification"))

        change = None
        if len(entries) >= 2 and value is not None:
            yesterday_raw = entries[1].get("value")
            try:
                yesterday_value = float(yesterday_raw) if yesterday_raw is not None else None
            except (TypeError, ValueError):
                yesterday_value = None
            if yesterday_value is not None:
                change = round(value - yesterday_value, 2)

        return {
            "value": value,
            "rating": rating,
            "change": change,
        }

    async def get_fear_greed(self) -> Dict:
        """Fetch and parse today's + yesterday's Crypto Fear & Greed index."""
        response = await self.client.get(self.URL, params={"limit": 2})
        response.raise_for_status()
        data = response.json()
        return self.parse(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
