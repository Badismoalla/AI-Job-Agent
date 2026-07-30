"""
modules/scraper/rate_limiter.py
--------------------------------
Configurable rate limiting for job board scrapers, decoupled from any
particular scraper so it's independently testable and reusable.

Two independent, both-optional knobs:
- A randomised "polite" delay between min_delay/max_delay seconds before
  each request — mimics human browsing pace, avoids a fixed-interval
  fingerprint. Set max_delay=0 to disable.
- An optional hard cap on requests per rolling 60-second window, enforced
  with a sliding-window counter. Set requests_per_minute=None to disable.

Usage:
    limiter = RateLimiter.from_settings(settings.scraper)
    await limiter.wait()   # call before every outgoing request

Testable without real time passing: pass min_delay=0, max_delay=0, and/or
monkeypatch RateLimiter._sleep (the single seam that calls asyncio.sleep).
"""

import asyncio
import random
import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Rate limiter combining a polite random delay and an optional RPM cap."""

    min_delay: float = 2.0
    max_delay: float = 5.0
    requests_per_minute: int | None = None

    _request_times: list[float] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.min_delay < 0 or self.max_delay < 0:
            raise ValueError("min_delay and max_delay must be >= 0")
        if self.min_delay > self.max_delay:
            raise ValueError("min_delay must be <= max_delay")
        if self.requests_per_minute is not None and self.requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0 if set")

    @classmethod
    def from_settings(cls, scraper_settings) -> "RateLimiter":
        """Build a RateLimiter from a ScraperSettings instance."""
        return cls(
            min_delay=scraper_settings.delay_min,
            max_delay=scraper_settings.delay_max,
            requests_per_minute=scraper_settings.requests_per_minute,
        )

    async def wait(self) -> None:
        """Block (async) until it's both RPM-allowed and polite to proceed."""
        await self._enforce_rpm_cap()
        await self._polite_delay()

    async def _polite_delay(self) -> None:
        if self.max_delay <= 0:
            return
        delay = random.uniform(self.min_delay, self.max_delay)
        if delay > 0:
            await self._sleep(delay)

    async def _enforce_rpm_cap(self) -> None:
        if not self.requests_per_minute:
            return

        now = time.monotonic()
        window_start = now - 60
        self._request_times = [t for t in self._request_times if t > window_start]

        if len(self._request_times) >= self.requests_per_minute:
            oldest = self._request_times[0]
            wait_for = 60 - (now - oldest)
            if wait_for > 0:
                await self._sleep(wait_for)

        self._request_times.append(time.monotonic())

    async def _sleep(self, seconds: float) -> None:
        """Isolated seam — patch this in tests to avoid real wall-clock waits."""
        await asyncio.sleep(seconds)
