"""In-memory sliding window, shared by requests in one app process."""

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RateLimitSettings:
    requests: int = 3
    window_seconds: float = 15

    def __post_init__(self):
        if self.requests < 1 or not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("Rate limit and window must be positive and finite")


class RateLimiter:
    def __init__(self, settings: RateLimitSettings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self._requests: dict[str, deque[float]] = {}

    def check(self, key: str) -> tuple[int, int]:
        """Return (retry_after, remaining). No await: atomic on the app event loop."""
        now = self.clock()
        cutoff = now - self.settings.window_seconds
        # Remove inactive clients too, so historical IPs do not accumulate forever.
        for client, timestamps in list(self._requests.items()):
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if not timestamps:
                del self._requests[client]
        timestamps = self._requests.setdefault(key, deque())
        if len(timestamps) >= self.settings.requests:
            return max(1, math.ceil(timestamps[0] + self.settings.window_seconds - now)), 0
        timestamps.append(now)
        return 0, self.settings.requests - len(timestamps)
