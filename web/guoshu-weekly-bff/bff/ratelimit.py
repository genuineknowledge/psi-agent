"""Per-user sliding-window rate limiting.

In-memory is correct for the trial-stage single instance (one BFF process,
one user pool). A horizontally scaled deployment must move the window into
shared storage — the plan (10.2) routes by session stickiness anyway.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self._per_minute:
            return False
        window.append(now)
        return True

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)
