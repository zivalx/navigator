from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import httpx


class BaseProvider(ABC):
    """Base class for market data providers."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    @abstractmethod
    async def get_quote(self, symbol: str) -> Dict:
        """
        Get quote for a single symbol.

        Returns:
            {
                "symbol": str,
                "price": float,
                "change": float,
                "changePercent": float,
                "timestamp": datetime,
                "currency": str,
            }
        """
        pass

    @abstractmethod
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Get quotes for multiple symbols.

        Returns:
            {
                "AAPL": {"price": 150.0, "change": 2.5, ...},
                "GOOGL": {"price": 2800.0, "change": -10.0, ...},
            }
        """
        pass

    @abstractmethod
    def supports_symbol(self, symbol: str, asset_type: str = "stock") -> bool:
        """Check if provider supports this symbol/asset type."""
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
