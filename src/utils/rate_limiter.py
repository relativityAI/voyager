"""
Rate limiter utility for controlling API call frequency.

Thread-safe fixed-interval limiter plus a requests.Session wrapper that
waits before each call. For the token-bucket variant the scraper transport
uses, see src/scrapers/throttle.py.
"""

import threading
import time
from typing import Any, Dict

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

    @property
    def cookies(self):
        """Property proxy to internal session cookies"""
        return self.session.cookies

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
    "RateLimitedSession",
    "get_rate_limiter",
    "reset_rate_limiters",
]
