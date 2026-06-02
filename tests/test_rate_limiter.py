"""
Test cases for rate limiter functionality.
"""

import threading
import time
from unittest.mock import patch

import pytest

from src.utils.rate_limiter import (
    RateLimitedSession,
    RateLimiter,
    SlidingWindowRateLimiter,
    get_rate_limiter,
    rate_limit,
    reset_rate_limiters,
)


class TestRateLimiter:
    """Tests for the basic RateLimiter class."""

    def test_initialization_valid(self):
        """Test initialization with valid parameters."""
        limiter = RateLimiter(calls_per_second=10)
        assert limiter.calls_per_second == 10
        assert limiter.min_interval == 0.1

    def test_initialization_invalid_zero(self):
        """Test initialization with zero calls per second raises error."""
        with pytest.raises(ValueError):
            RateLimiter(calls_per_second=0)

    def test_initialization_invalid_negative(self):
        """Test initialization with negative calls per second raises error."""
        with pytest.raises(ValueError):
            RateLimiter(calls_per_second=-5)

    def test_default_calls_per_second(self):
        """Test default calls per second is 10."""
        limiter = RateLimiter()
        assert limiter.calls_per_second == 10

    def test_single_wait_no_delay(self):
        """Test first wait call doesn't delay."""
        limiter = RateLimiter(calls_per_second=10)
        start = time.time()
        limiter.wait()
        elapsed = time.time() - start
        assert elapsed < 0.05  # Should be nearly instantaneous

    def test_consecutive_waits_enforce_rate(self):
        """Test that consecutive waits enforce the rate limit."""
        calls_per_second = 10
        limiter = RateLimiter(calls_per_second=calls_per_second)

        expected_interval = 1.0 / calls_per_second
        start = time.time()

        # Make 3 calls
        for i in range(3):
            limiter.wait()

        elapsed = time.time() - start
        expected_time = expected_interval * 2  # 2 intervals for 3 calls

        # Allow 10% tolerance
        assert elapsed >= expected_time * 0.9
        assert elapsed <= expected_time * 1.1

    def test_high_frequency_rate_limit(self):
        """Test rate limiting for high frequency (100 calls per second)."""
        calls_per_second = 100
        limiter = RateLimiter(calls_per_second=calls_per_second)

        expected_interval = 1.0 / calls_per_second
        start = time.time()

        for i in range(10):
            limiter.wait()

        elapsed = time.time() - start
        expected_time = expected_interval * 9

        # Allow some tolerance
        assert elapsed >= expected_time * 0.8
        assert elapsed <= expected_time * 1.2

    def test_low_frequency_rate_limit(self):
        """Test rate limiting for low frequency (1 call per second)."""
        calls_per_second = 1
        limiter = RateLimiter(calls_per_second=calls_per_second)

        start = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - start

        assert elapsed >= 0.9  # Should be approximately 1 second

    def test_reset(self):
        """Test reset functionality."""
        limiter = RateLimiter(calls_per_second=10)
        limiter.wait()
        assert limiter.last_call_time is not None

        limiter.reset()
        assert limiter.last_call_time is None

    def test_thread_safety(self):
        """Test that rate limiter is thread-safe."""
        limiter = RateLimiter(calls_per_second=10)
        call_times = []

        def make_call():
            limiter.wait()
            call_times.append(time.time())

        threads = [threading.Thread(target=make_call) for _ in range(5)]
        start = time.time()

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        elapsed = time.time() - start
        # With 5 threads and 10 calls/sec, should take roughly 0.4 seconds
        assert elapsed >= 0.3 and elapsed <= 0.6


class TestSlidingWindowRateLimiter:
    """Tests for the SlidingWindowRateLimiter class."""

    def test_initialization(self):
        """Test initialization."""
        limiter = SlidingWindowRateLimiter(calls_per_second=5)
        assert limiter.calls_per_second == 5
        assert len(limiter.call_times) == 0

    def test_initialization_invalid(self):
        """Test initialization with invalid parameters."""
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(calls_per_second=0)

    def test_sliding_window_enforcement(self):
        """Test that sliding window enforces rate limit."""
        calls_per_second = 5
        limiter = SlidingWindowRateLimiter(calls_per_second=calls_per_second)

        start = time.time()

        # Make calls beyond the limit
        for i in range(8):
            limiter.wait()

        elapsed = time.time() - start
        # 8 calls at 5/sec should take at least 1+ second
        assert elapsed >= 1.0

    def test_get_current_load(self):
        """Test getting current load."""
        limiter = SlidingWindowRateLimiter(calls_per_second=10)

        for i in range(5):
            limiter.wait()

        load = limiter.get_current_load()
        # Should be around 50%
        assert 40 < load < 60

    def test_reset(self):
        """Test reset functionality."""
        limiter = SlidingWindowRateLimiter(calls_per_second=10)

        for i in range(5):
            limiter.wait()

        assert len(limiter.call_times) > 0
        limiter.reset()
        assert len(limiter.call_times) == 0


class TestRateLimitDecorator:
    """Tests for the rate_limit decorator."""

    def test_decorator_basic(self):
        """Test basic decorator functionality."""

        @rate_limit(calls_per_second=10)
        def dummy_function():
            return "result"

        result = dummy_function()
        assert result == "result"

    def test_decorator_with_service_name(self):
        """Test decorator with custom service name."""

        @rate_limit(calls_per_second=10, service_name="test_service")
        def dummy_function():
            return "result"

        result = dummy_function()
        assert result == "result"

    def test_decorator_enforces_rate_limit(self):
        """Test that decorator enforces rate limiting."""

        @rate_limit(calls_per_second=10, service_name="test_decorator_limit")
        def dummy_function():
            return time.time()

        reset_rate_limiters()

        start = time.time()
        times = [dummy_function() for _ in range(3)]
        elapsed = time.time() - start

        expected_interval = 1.0 / 10.0
        expected_time = expected_interval * 2  # 2 intervals for 3 calls

        assert elapsed >= expected_time * 0.8
        assert elapsed <= expected_time * 1.2

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves function metadata."""

        @rate_limit(calls_per_second=10)
        def dummy_function():
            """Test docstring."""
            return "result"

        assert dummy_function.__name__ == "dummy_function"
        assert dummy_function.__doc__ == "Test docstring."

    def test_decorator_with_arguments(self):
        """Test decorator with function that has arguments."""

        @rate_limit(calls_per_second=10)
        def add(a, b):
            return a + b

        result = add(2, 3)
        assert result == 5

    def test_decorator_with_kwargs(self):
        """Test decorator with function that has keyword arguments."""

        @rate_limit(calls_per_second=10)
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = greet("Alice", greeting="Hi")
        assert result == "Hi, Alice!"


class TestRateLimitedSession:
    """Tests for RateLimitedSession."""

    def test_initialization(self):
        """Test initialization."""
        session = RateLimitedSession(calls_per_second=10, service_name="test")
        assert session.service_name == "test"
        session.close()

    def test_initialization_default_params(self):
        """Test initialization with default parameters."""
        session = RateLimitedSession()
        assert session.service_name == "api"
        session.close()

    @patch("requests.Session.get")
    def test_get_request(self, mock_get):
        """Test GET request through rate-limited session."""
        mock_get.return_value.json.return_value = {"data": "test"}

        session = RateLimitedSession(calls_per_second=100)
        response = session.get("http://example.com")

        mock_get.assert_called_once()
        session.close()

    @patch("requests.Session.post")
    def test_post_request(self, mock_post):
        """Test POST request through rate-limited session."""
        mock_post.return_value.json.return_value = {"data": "test"}

        session = RateLimitedSession(calls_per_second=100)
        response = session.post("http://example.com", data={"key": "value"})

        mock_post.assert_called_once()
        session.close()

    @patch("requests.Session.get")
    def test_rate_limited_session_enforces_limit(self, mock_get):
        """Test that session enforces rate limiting."""
        mock_get.return_value.text = "response"

        session = RateLimitedSession(calls_per_second=10, service_name="test_session")
        reset_rate_limiters()

        start = time.time()
        for i in range(3):
            session.get("http://example.com")
        elapsed = time.time() - start

        expected_time = (1.0 / 10) * 2
        assert elapsed >= expected_time * 0.8

        session.close()

    def test_context_manager(self):
        """Test that session works as a context manager."""
        with RateLimitedSession(calls_per_second=10) as session:
            assert session is not None


class TestGlobalRateLimiters:
    """Tests for global rate limiter functions."""

    def test_get_rate_limiter_creates_new(self):
        """Test get_rate_limiter creates new limiter."""
        reset_rate_limiters()
        limiter = get_rate_limiter("test_service", calls_per_second=5)
        assert limiter.calls_per_second == 5

    def test_get_rate_limiter_returns_same_instance(self):
        """Test get_rate_limiter returns same instance for same service."""
        reset_rate_limiters()
        limiter1 = get_rate_limiter("test_service", calls_per_second=5)
        limiter2 = get_rate_limiter("test_service", calls_per_second=10)

        # Should return the same instance
        assert limiter1 is limiter2
        assert limiter1.calls_per_second == 5

    def test_different_services_have_different_limiters(self):
        """Test different services get different limiters."""
        reset_rate_limiters()
        limiter1 = get_rate_limiter("service_a", calls_per_second=5)
        limiter2 = get_rate_limiter("service_b", calls_per_second=20)

        assert limiter1 is not limiter2
        assert limiter1.calls_per_second == 5
        assert limiter2.calls_per_second == 20

    def test_reset_rate_limiters(self):
        """Test reset_rate_limiters clears all limiters."""
        reset_rate_limiters()
        limiter1 = get_rate_limiter("service_a")
        limiter2 = get_rate_limiter("service_b")

        # Make some calls
        limiter1.wait()
        limiter2.wait()

        assert limiter1.last_call_time is not None
        assert limiter2.last_call_time is not None

        reset_rate_limiters()

        # Limiters should be reset
        limiter3 = get_rate_limiter("service_a")
        limiter4 = get_rate_limiter("service_b")

        assert limiter3.last_call_time is None
        assert limiter4.last_call_time is None


class TestIntegration:
    """Integration tests."""

    def test_multiple_services_independent_limits(self):
        """Test that multiple services maintain independent rate limits."""
        reset_rate_limiters()

        service_a_limiter = get_rate_limiter("service_a", calls_per_second=20)
        service_b_limiter = get_rate_limiter("service_b", calls_per_second=5)

        # Service A should be faster
        start_a = time.time()
        for i in range(3):
            service_a_limiter.wait()
        time_a = time.time() - start_a

        start_b = time.time()
        for i in range(3):
            service_b_limiter.wait()
        time_b = time.time() - start_b

        # Service B should take longer
        assert time_b > time_a

    def test_decorator_and_session_compatibility(self):
        """Test that decorator and session work together."""
        reset_rate_limiters()

        @rate_limit(calls_per_second=10, service_name="test_compat")
        def fetch_data():
            return "data"

        session = RateLimitedSession(calls_per_second=10, service_name="test_compat")

        # Both should use the same rate limiter
        result1 = fetch_data()
        result2 = session.session  # Just access to verify it exists

        assert result1 == "data"
        session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
