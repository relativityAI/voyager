"""
Rate limiter utility for controlling API call frequency.

This module provides decorators and context managers for rate limiting
HTTP requests to prevent overwhelming external APIs.
"""

import threading
import time
from collections import deque
from functools import wraps
from typing import Any, Callable, Dict, Optional

from loguru import logger


class RateLimiter:
    """
    A thread-safe rate limiter that restricts calls to a specified frequency.

    Uses a sliding window approach to track calls per second.
    """

    def __init__(self, calls_per_second: float = 10.0):
        """
        Initialize the rate limiter.

        Args:
            calls_per_second: Maximum number of calls allowed per second (default: 10)
        """
        if calls_per_second <= 0:
            raise ValueError("calls_per_second must be positive")

        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self.last_call_time = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        """
        Wait if necessary to maintain the rate limit.

        This method should be called before making a request.
        """
        with self._lock:
            now = time.time()

            if self.last_call_time is None:
                self.last_call_time = now
                return

            time_since_last_call = now - self.last_call_time

            if time_since_last_call < self.min_interval:
                sleep_time = self.min_interval - time_since_last_call
                logger.debug(
                    f"Rate limit: sleeping for {sleep_time:.3f}s "
                    f"(calls_per_second={self.calls_per_second})"
                )
                time.sleep(sleep_time)
                self.last_call_time = time.time()
            else:
                self.last_call_time = now

    def reset(self) -> None:
        """Reset the rate limiter state."""
        with self._lock:
            self.last_call_time = None


class SlidingWindowRateLimiter:
    """
    A more sophisticated rate limiter using a sliding window approach.

    Tracks the exact time of each call and maintains a window of recent calls.
    """

    def __init__(self, calls_per_second: float = 10.0, window_size: int = 100):
        """
        Initialize the sliding window rate limiter.

        Args:
            calls_per_second: Maximum number of calls allowed per second (default: 10)
            window_size: Maximum number of calls to track in the window
        """
        if calls_per_second <= 0:
            raise ValueError("calls_per_second must be positive")

        self.calls_per_second = calls_per_second
        self.window_size = window_size
        self.call_times = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def wait(self) -> None:
        """
        Wait if necessary to maintain the rate limit.

        This method checks if we've exceeded the call frequency and sleeps if needed.
        """
        with self._lock:
            now = time.time()

            # Remove calls older than 1 second
            while self.call_times and self.call_times[0] < now - 1.0:
                self.call_times.popleft()

            # If we've hit the limit, wait
            if len(self.call_times) >= self.calls_per_second:
                oldest_call = self.call_times[0]
                sleep_time = 1.0 - (now - oldest_call)

                if sleep_time > 0:
                    logger.debug(
                        f"Rate limit: {len(self.call_times)} calls in last second, "
                        f"sleeping for {sleep_time:.3f}s"
                    )
                    time.sleep(sleep_time)
                    now = time.time()

            self.call_times.append(now)

    def reset(self) -> None:
        """Reset the rate limiter state."""
        with self._lock:
            self.call_times.clear()

    def get_current_load(self) -> float:
        """Get the current load as a percentage of the limit."""
        with self._lock:
            now = time.time()
            # Count calls in the last second
            recent_calls = sum(1 for t in self.call_times if t > now - 1.0)
            return (recent_calls / self.calls_per_second) * 100


# Global rate limiters for different services
_rate_limiters: Dict[str, RateLimiter] = {}
_rate_limiters_lock = threading.Lock()


def get_rate_limiter(service_name: str, calls_per_second: float = 10.0) -> RateLimiter:
    """
    Get or create a rate limiter for a specific service.

    Once a limiter is created for a service, subsequent calls will return the same
    instance regardless of the calls_per_second parameter (to prevent accidental changes).

    Args:
        service_name: Name of the service/website
        calls_per_second: Maximum calls per second (default: 10)

    Returns:
        A RateLimiter instance for the service
    """
    with _rate_limiters_lock:
        if service_name not in _rate_limiters:
            _rate_limiters[service_name] = RateLimiter(calls_per_second)
        return _rate_limiters[service_name]


def reset_rate_limiters() -> None:
    """Reset all rate limiters and clear the cache."""
    with _rate_limiters_lock:
        for limiter in _rate_limiters.values():
            limiter.reset()
        _rate_limiters.clear()


def rate_limit(calls_per_second: float = 10.0, service_name: Optional[str] = None):
    """
    Decorator to apply rate limiting to a function.

    Args:
        calls_per_second: Maximum calls per second (default: 10)
        service_name: Name of the service (uses function name if not provided)

    Example:
        @rate_limit(calls_per_second=5, service_name="screener_api")
        def fetch_data(symbol):
            return requests.get(f"https://api.screener.in/{symbol}").json()
    """

    def decorator(func: Callable) -> Callable:
        svc_name = service_name or func.__module__ + "." + func.__name__

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            limiter = get_rate_limiter(svc_name, calls_per_second)
            limiter.wait()
            return func(*args, **kwargs)

        return wrapper

    return decorator


class RateLimitedSession:
    """
    A wrapper around requests.Session that applies rate limiting to all requests.
    """

    def __init__(self, calls_per_second: float = 10.0, service_name: str = "api"):
        """
        Initialize the rate-limited session.

        Args:
            calls_per_second: Maximum calls per second (default: 10)
            service_name: Name of the service
        """
        try:
            import requests

            self.session = requests.Session()
        except ImportError:
            raise ImportError("requests library is required for RateLimitedSession")

        self.limiter = get_rate_limiter(service_name, calls_per_second)
        self.service_name = service_name

    def _rate_limited_request(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited request."""
        self.limiter.wait()
        return getattr(self.session, method)(*args, **kwargs)

    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited GET request."""
        return self._rate_limited_request("get", *args, **kwargs)

    def post(self, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited POST request."""
        return self._rate_limited_request("post", *args, **kwargs)

    def put(self, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited PUT request."""
        return self._rate_limited_request("put", *args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited DELETE request."""
        return self._rate_limited_request("delete", *args, **kwargs)

    def head(self, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited HEAD request."""
        return self._rate_limited_request("head", *args, **kwargs)

    def options(self, *args: Any, **kwargs: Any) -> Any:
        """Make a rate-limited OPTIONS request."""
        return self._rate_limited_request("options", *args, **kwargs)

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit."""
        self.close()


__all__ = [
    "RateLimiter",
    "SlidingWindowRateLimiter",
    "RateLimitedSession",
    "get_rate_limiter",
    "reset_rate_limiters",
    "rate_limit",
]
