"""
Simple in-memory rate limiter using token bucket algorithm.
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float, burst: int | None = None) -> None:
        """
        Args:
            rate: Tokens per second (0 = unlimited).
            burst: Maximum burst size (defaults to rate if not set).
        """
        self.rate = rate
        self.burst = burst or max(int(rate), 1)
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """Try to acquire one token. Returns True if allowed, False if rate limited."""
        if self.rate <= 0:
            return True

        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    @property
    def available_tokens(self) -> float:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            return min(self.burst, self._tokens + elapsed * self.rate)