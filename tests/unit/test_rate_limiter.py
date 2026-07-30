"""
Unit tests for modules.scraper.rate_limiter.RateLimiter.

No test in this file waits on real wall-clock time — RateLimiter._sleep is
monkeypatched to a no-op that just records how long it *would* have slept,
so the whole suite runs instantly while still verifying the delay/RPM math.
"""

import pytest

from modules.scraper.rate_limiter import RateLimiter


class RecordingLimiter(RateLimiter):
    """A RateLimiter whose _sleep records calls instead of actually sleeping."""

    def __post_init__(self):
        super().__post_init__()
        self.sleep_calls: list[float] = []

    async def _sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)


# ── Construction / validation ───────────────────────────────────────────────

class TestConstruction:

    def test_default_construction(self):
        limiter = RateLimiter()
        assert limiter.min_delay == 2.0
        assert limiter.max_delay == 5.0
        assert limiter.requests_per_minute is None

    def test_rejects_negative_min_delay(self):
        with pytest.raises(ValueError, match=">= 0"):
            RateLimiter(min_delay=-1, max_delay=5)

    def test_rejects_negative_max_delay(self):
        with pytest.raises(ValueError, match=">= 0"):
            RateLimiter(min_delay=0, max_delay=-1)

    def test_rejects_min_greater_than_max(self):
        with pytest.raises(ValueError, match="min_delay must be <= max_delay"):
            RateLimiter(min_delay=10, max_delay=5)

    def test_rejects_zero_or_negative_requests_per_minute(self):
        with pytest.raises(ValueError, match="requests_per_minute"):
            RateLimiter(requests_per_minute=0)

    def test_from_settings_reads_scraper_settings(self):
        class FakeScraperSettings:
            delay_min = 1.5
            delay_max = 3.5
            requests_per_minute = 20

        limiter = RateLimiter.from_settings(FakeScraperSettings())
        assert limiter.min_delay == 1.5
        assert limiter.max_delay == 3.5
        assert limiter.requests_per_minute == 20


# ── Polite delay ─────────────────────────────────────────────────────────────

class TestPoliteDelay:

    @pytest.mark.asyncio
    async def test_sleeps_within_configured_range(self):
        limiter = RecordingLimiter(min_delay=1.0, max_delay=2.0)
        await limiter.wait()
        assert len(limiter.sleep_calls) == 1
        assert 1.0 <= limiter.sleep_calls[0] <= 2.0

    @pytest.mark.asyncio
    async def test_zero_max_delay_skips_sleep_entirely(self):
        limiter = RecordingLimiter(min_delay=0, max_delay=0)
        await limiter.wait()
        assert limiter.sleep_calls == []

    @pytest.mark.asyncio
    async def test_equal_min_and_max_produces_exact_delay(self):
        limiter = RecordingLimiter(min_delay=3.0, max_delay=3.0)
        await limiter.wait()
        assert limiter.sleep_calls == [3.0]


# ── Requests-per-minute cap ──────────────────────────────────────────────────

class TestRequestsPerMinuteCap:

    @pytest.mark.asyncio
    async def test_no_cap_by_default(self):
        limiter = RecordingLimiter(min_delay=0, max_delay=0, requests_per_minute=None)
        for _ in range(10):
            await limiter.wait()
        # No RPM-driven sleeps — only the (skipped, since max_delay=0) polite delay.
        assert limiter.sleep_calls == []

    @pytest.mark.asyncio
    async def test_stays_under_cap_without_extra_sleep(self):
        limiter = RecordingLimiter(min_delay=0, max_delay=0, requests_per_minute=5)
        for _ in range(5):
            await limiter.wait()
        # 5 requests against a cap of 5 — no wait was ever necessary.
        assert limiter.sleep_calls == []

    @pytest.mark.asyncio
    async def test_exceeding_cap_forces_a_wait(self, monkeypatch):
        limiter = RecordingLimiter(min_delay=0, max_delay=0, requests_per_minute=2)

        # Simulate 2 requests already made 10 seconds ago (inside the 60s window).
        import time
        now = time.monotonic()
        limiter._request_times = [now - 10, now - 5]

        await limiter.wait()

        assert len(limiter.sleep_calls) == 1
        # Oldest request was ~10s ago inside a 60s window -> should wait ~50s.
        assert 45 <= limiter.sleep_calls[0] <= 51

    @pytest.mark.asyncio
    async def test_old_requests_fall_out_of_the_window(self):
        limiter = RecordingLimiter(min_delay=0, max_delay=0, requests_per_minute=2)

        import time
        now = time.monotonic()
        # Both prior requests are more than 60s old — window should be empty.
        limiter._request_times = [now - 120, now - 90]

        await limiter.wait()

        assert limiter.sleep_calls == []
        assert len(limiter._request_times) == 1  # old ones pruned, this call recorded
