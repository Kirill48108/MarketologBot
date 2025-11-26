import asyncio
import time
from typing import Any, Dict, Optional

from redis.asyncio import Redis as AsyncRedis


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._data: Dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            ts, value = item
            if time.time() - ts > self.ttl:
                del self._data[key]
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = (time.time(), value)


class RedisTTLCache:
    def __init__(self, redis: AsyncRedis, ttl_seconds: int):
        self.redis = redis
        self.ttl = ttl_seconds

    async def get(self, key: str) -> Optional[str]:
        if self.redis is None:
            return None
        return await self.redis.get(key)  # type: ignore[no-any-return]

    async def set(self, key: str, value: str) -> None:
        if self.redis is None:
            return
        await self.redis.set(key, value, ex=self.ttl)
