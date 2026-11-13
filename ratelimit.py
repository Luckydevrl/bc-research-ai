"""Small in-process sliding-window rate limiter (protects the simulator from rapid repeats)."""
from __future__ import annotations

import threading
import time
from collections import deque


class RateLimitExceeded(Exception):
    pass


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float = 60.0, clock=time.monotonic):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._clock = clock
        self._calls: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str = "default") -> None:
        now = self._clock()
        with self._lock:
            q = self._calls.setdefault(key, deque())
            while q and now - q[0] > self.per_seconds:
                q.popleft()
            if len(q) >= self.max_calls:
                raise RateLimitExceeded(f"Too many requests; limit is {self.max_calls} per {int(self.per_seconds)}s")
            q.append(now)
