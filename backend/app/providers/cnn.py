from typing import Dict, Optional
import httpx


class CNNFearGreedProvider:
    """CNN Fear & Greed Index provider (stocks sentiment).

    CNN's dataviz API blocks requests without a browser-like User-Agent
    (returns 418/403 for default HTTP clients), so we spoof one.
    """

    URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
        "Origin": "https://www.cnn.com",
    }

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def parse(data: Dict) -> Dict:
        """Parse the raw CNN dataviz JSON payload into a normalized dict.

        Expects a `fear_and_greed` block with `score`, `rating`, and
        `previous_close` (yesterday's score).
        """
        fng = data.get("fear_and_greed") or {}

        score: Optional[float] = fng.get("score")
        raw_rating = fng.get("rating")
        rating: Optional[str] = (
            raw_rating.strip().lower().replace(" ", "_") if raw_rating else None
        )
        previous_close: Optional[float] = fng.get("previous_close")

        change = None
        if score is not None and previous_close is not None:
            change = round(score - previous_close, 2)

        return {
            "value": round(score, 2) if score is not None else None,
            "rating": rating,
            "change": change,
        }

    async def get_fear_greed(self) -> Dict:
        """Fetch and parse the current CNN Fear & Greed index."""
        response = await self.client.get(self.URL, headers=self.HEADERS)
        response.raise_for_status()
        data = response.json()
        return self.parse(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
