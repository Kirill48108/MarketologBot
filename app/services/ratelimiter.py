import asyncio
import time
from collections import deque
from typing import Deque, Optional


class SlidingWindowRateLimiter:
    def __init__(self, max_events: int, window_seconds: int):
        self.max_events = max_events
        self.window = window_seconds
        self.events: Deque[float] = deque()
        self._lock = asyncio.Lock()

    async def allow(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window
            while self.events and self.events[0] < cutoff:
                self.events.popleft()
            if len(self.events) < self.max_events:
                self.events.append(now)
                return True
            return False

    async def time_to_reset(self) -> Optional[float]:
        async with self._lock:
            if not self.events:
                return 0.0
            now = time.monotonic()
            return max(0.0, self.events[0] + self.window - now)
