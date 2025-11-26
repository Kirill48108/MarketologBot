import time

import pytest

from app.services.cache import AsyncTTLCache


@pytest.mark.asyncio
async def test_ttl_cache_basic():
    cache = AsyncTTLCache(ttl_seconds=1)
    assert await cache.get("k") is None
    await cache.set("k", "v")
    assert await cache.get("k") == "v"
    time.sleep(1.1)
    assert await cache.get("k") is None
