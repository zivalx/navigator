import redis.asyncio as redis
import json
from typing import Optional, Any
from .config import settings


class Cache:
    """Redis cache wrapper with async support."""

    def __init__(self):
        self.redis: Optional[redis.Redis] = None

    async def connect(self):
        """Connect to Redis."""
        self.redis = await redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis:
            await self.redis.close()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self.redis:
            return None
        value = await self.redis.get(key)
        if value:
            return json.loads(value)
        return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = 60):
        """Set value in cache with TTL. Pass ttl=None to store without expiry."""
        if not self.redis:
            return
        payload = json.dumps(value, default=str)
        if ttl is None:
            await self.redis.set(key, payload)
        else:
            await self.redis.setex(key, ttl, payload)

    async def delete(self, key: str):
        """Delete key from cache."""
        if not self.redis:
            return
        await self.redis.delete(key)

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self.redis:
            return False
        return await self.redis.exists(key) > 0


# Global cache instance
cache = Cache()
