"""Global per-source token-bucket throttle with jitter.

Replaces the fixed-interval ``RateLimiter`` (in ``src.utils.rate_limiter``)
for stealth transport: a token bucket preserves the average request rate while
allowing small bursts (so parallel endpoint fetches don't serialize into a
stall) and jitters the spacing so requests don't follow a machine-like cadence
that bot detectors flag (see D-06).

The limiter is process-global per source name, mirroring
``rate_limiter.get_rate_limiter`` semantics: concurrent threads calling NSE
share one budget, exactly as before.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Dict

DEFAULT_JITTER = 0.2


class TokenBucketThrottle:
    """Thread-safe token bucket with jittered wait."""

    def __init__(
        self,
        calls_per_second: float = 10.0,
        burst: int = 4,
        jitter: float = DEFAULT_JITTER,
    ) -> None:
        if calls_per_second <= 0:
            raise ValueError("calls_per_second must be positive")
        self.calls_per_second = calls_per_second
        self.capacity = max(burst, 1)
        self.jitter = jitter
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_refill
        self._tokens = min(
            self.capacity, self._tokens + elapsed * self.calls_per_second
        )
        self._last_refill = now

    def wait(self) -> None:
        """Block until a token is available, then consume it."""
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # Need to wait for the next token to accrue, plus jitter.
            missing = 1.0 - self._tokens
            sleep_time = missing / self.calls_per_second
            sleep_time *= 1.0 + random.uniform(-self.jitter, self.jitter)
            time.sleep(max(sleep_time, 0.0))
            self._refill(time.monotonic())
            self._tokens -= 1.0

    def reset(self) -> None:
        with self._lock:
            self._tokens = float(self.capacity)
            self._last_refill = time.monotonic()


_throttles: Dict[str, TokenBucketThrottle] = {}
_throttles_lock = threading.Lock()


def get_throttle(
    service_name: str, calls_per_second: float = 10.0, burst: int = 4
) -> TokenBucketThrottle:
    """Get or create the global throttle for a service.

    The first call fixes the rate; later calls ignore ``calls_per_second`` so
    concurrent callers can't accidentally change the shared budget.
    """
    with _throttles_lock:
        if service_name not in _throttles:
            _throttles[service_name] = TokenBucketThrottle(
                calls_per_second=calls_per_second, burst=burst
            )
        return _throttles[service_name]


def reset_throttles() -> None:
    with _throttles_lock:
        for limiter in _throttles.values():
            limiter.reset()
        _throttles.clear()


__all__ = [
    "TokenBucketThrottle",
    "get_throttle",
    "reset_throttles",
]
