"""
Unit tests for modules.scraper.base.BaseJobScraper.

Two layers of testing, matching the fetch/parse separation the framework
is built around:

1. Orchestration tests (TestPagination, TestValidation, TestDeduplication,
   TestFailSoftPartialResults) use a FakeScraper whose fetch_page/parse_page
   are pure in-memory functions — no httpx, no network, no mocking beyond
   plain Python. These prove scrape()'s pagination/rate-limit/dedup/
   validation logic is correct in complete isolation from any real board.

2. HTTP-layer tests (TestGetRetryAndStatusHandling) exercise the shared
   `_get()` helper against a real httpx.AsyncClient wired to
   httpx.MockTransport — a fake in-process transport, so still zero real
   network access, but this layer proves retry/backoff and status-code
   translation actually work against the httpx client.

Nothing in this file makes a real network call.
"""

from typing import Any

import httpx
import pytest
from tenacity import wait_none

from core.exceptions import ScraperBlockedError, ScraperParseError, ScraperRateLimitError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import BaseJobScraper, PageResult
from modules.scraper.rate_limiter import RateLimiter


def make_job(job_id: str, title: str = "Test Engineer") -> JobListing:
    return JobListing(
        id=job_id,
        title=title,
        company="TestCo",
        city="Krakow",
        market=Market.POLAND,
        url=f"https://example.com/jobs/{job_id}",
        source=ApplicationSource.PRACUJ,
    )


def zero_delay_limiter() -> RateLimiter:
    """A rate limiter that never actually waits, for fast tests."""
    return RateLimiter(min_delay=0, max_delay=0)


class FakeScraper(BaseJobScraper):
    """
    A scraper whose fetch_page/parse_page are scripted in-memory — no
    network, no async client needed. `pages` maps page number -> PageResult
    (or an exception instance to raise instead).
    """

    board_name = "fake"

    def __init__(self, pages: dict[int, PageResult | Exception], **kwargs):
        super().__init__(**kwargs)
        self.pages = pages
        self.fetch_calls: list[int] = []
        self.parse_calls: list[int] = []

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        self.fetch_calls.append(page)
        entry = self.pages.get(page)
        if isinstance(entry, Exception):
            raise entry
        return entry  # the PageResult itself — parse_page just passes it through

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        self.parse_calls.append(page)
        return raw_page


async def run_fake_scrape(pages: dict[int, PageResult | Exception], **kwargs) -> tuple[FakeScraper, list[JobListing]]:
    kwargs.setdefault("rate_limiter", zero_delay_limiter())
    scraper = FakeScraper(pages=pages, **kwargs)
    async with scraper:
        result = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])
    return scraper, result


# ── Abstract contract ────────────────────────────────────────────────────────

class TestAbstractContract:

    def test_cannot_instantiate_base_directly(self):
        with pytest.raises(TypeError):
            BaseJobScraper()

    def test_only_fetch_and_parse_page_are_abstract(self):
        assert BaseJobScraper.__abstractmethods__ == frozenset({"fetch_page", "parse_page"})

    @pytest.mark.asyncio
    async def test_scrape_requires_context_manager(self):
        scraper = FakeScraper(pages={}, rate_limiter=zero_delay_limiter())
        with pytest.raises(RuntimeError, match="context manager"):
            await scraper.scrape(roles=[], cities=[])


# ── Pagination ───────────────────────────────────────────────────────────────

class TestPagination:

    @pytest.mark.asyncio
    async def test_single_page_no_next(self):
        pages = {1: PageResult(listings=[make_job("a"), make_job("b")], has_next_page=False)}
        scraper, result = await run_fake_scrape(pages)
        assert scraper.fetch_calls == [1]
        assert {j.id for j in result} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_follows_has_next_page_across_multiple_pages(self):
        pages = {
            1: PageResult(listings=[make_job("a")], has_next_page=True),
            2: PageResult(listings=[make_job("b")], has_next_page=True),
            3: PageResult(listings=[make_job("c")], has_next_page=False),
        }
        scraper, result = await run_fake_scrape(pages)
        assert scraper.fetch_calls == [1, 2, 3]
        assert {j.id for j in result} == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_stops_at_max_pages_even_if_has_next_page_true(self):
        pages = {
            1: PageResult(listings=[make_job("a")], has_next_page=True),
            2: PageResult(listings=[make_job("b")], has_next_page=True),
            3: PageResult(listings=[make_job("c")], has_next_page=True),
        }
        scraper, result = await run_fake_scrape(pages, max_pages=2)
        assert scraper.fetch_calls == [1, 2]
        assert {j.id for j in result} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_rate_limiter_invoked_between_pages_not_before_first(self):
        pages = {
            1: PageResult(listings=[make_job("a")], has_next_page=True),
            2: PageResult(listings=[make_job("b")], has_next_page=False),
        }

        wait_calls = {"count": 0}

        class CountingLimiter(RateLimiter):
            async def wait(self):
                wait_calls["count"] += 1

        await run_fake_scrape(pages, rate_limiter=CountingLimiter(min_delay=0, max_delay=0))
        # 2 pages fetched -> exactly 1 inter-page wait (before page 2, not before page 1).
        assert wait_calls["count"] == 1

    @pytest.mark.asyncio
    async def test_fetch_and_parse_called_once_per_page(self):
        pages = {
            1: PageResult(listings=[make_job("a")], has_next_page=True),
            2: PageResult(listings=[make_job("b")], has_next_page=False),
        }
        scraper, _ = await run_fake_scrape(pages)
        assert scraper.fetch_calls == [1, 2]
        assert scraper.parse_calls == [1, 2]


# ── Fail-soft partial results ────────────────────────────────────────────────

class TestFailSoftPartialResults:

    @pytest.mark.asyncio
    async def test_first_page_failure_propagates(self):
        pages = {1: ScraperBlockedError("blocked", board="fake")}
        with pytest.raises(ScraperBlockedError):
            await run_fake_scrape(pages)

    @pytest.mark.asyncio
    async def test_later_page_failure_returns_partial_results(self):
        pages = {
            1: PageResult(listings=[make_job("a")], has_next_page=True),
            2: ScraperRateLimitError("rate limited", board="fake"),
        }
        scraper, result = await run_fake_scrape(pages)
        assert {j.id for j in result} == {"a"}
        assert scraper.fetch_calls == [1, 2]


# ── Validation of parse_page() output ───────────────────────────────────────

class TestValidation:

    @pytest.mark.asyncio
    async def test_non_page_result_return_raises_parse_error(self):
        pages = {1: {"not": "a PageResult"}}
        with pytest.raises(ScraperParseError, match="must return a PageResult"):
            await run_fake_scrape(pages)

    @pytest.mark.asyncio
    async def test_non_list_listings_raises_parse_error(self):
        bad_result = PageResult(listings=[], has_next_page=False)
        bad_result.listings = "not a list"  # type: ignore[assignment]
        pages = {1: bad_result}
        with pytest.raises(ScraperParseError, match="must be a list"):
            await run_fake_scrape(pages)

    @pytest.mark.asyncio
    async def test_non_joblisting_item_raises_parse_error(self):
        bad_result = PageResult(listings=[{"id": "not-a-job-listing"}], has_next_page=False)
        pages = {1: bad_result}
        with pytest.raises(ScraperParseError, match="non-JobListing item"):
            await run_fake_scrape(pages)


# ── Deduplication ────────────────────────────────────────────────────────────

class TestDeduplication:

    @pytest.mark.asyncio
    async def test_duplicate_ids_across_pages_are_removed(self):
        pages = {
            1: PageResult(listings=[make_job("a"), make_job("b")], has_next_page=True),
            2: PageResult(listings=[make_job("b"), make_job("c")], has_next_page=False),
        }
        scraper, result = await run_fake_scrape(pages)
        ids = [j.id for j in result]
        assert sorted(ids) == ["a", "b", "c"]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_no_duplicates_returns_everything_unchanged(self):
        pages = {1: PageResult(listings=[make_job("a"), make_job("b")], has_next_page=False)}
        _, result = await run_fake_scrape(pages)
        assert len(result) == 2


# ── _get(): retry + status-code handling against a real httpx client ───────

class TestGetRetryAndStatusHandling:
    """
    Uses httpx.MockTransport — a real httpx.AsyncClient wired to an
    in-process fake transport. No network access, but this exercises the
    real retry/backoff/status-code path, not a mock of it.
    """

    def _client_for(self, handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @pytest.mark.asyncio
    async def test_successful_response_returned(self):
        def handler(request):
            return httpx.Response(200, json={"ok": True})

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=1, retry_wait=wait_none()
        ) as scraper:
            response = await scraper._get("https://example.com/jobs")
            assert response.status_code == 200
            assert response.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit_error_after_retries_exhausted(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429)

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=2, retry_wait=wait_none()
        ) as scraper:
            with pytest.raises(ScraperRateLimitError):
                await scraper._get("https://example.com/jobs")

        assert calls["n"] == 2  # exactly max_retries attempts, no more

    @pytest.mark.asyncio
    async def test_403_raises_blocked_error_after_retries_exhausted(self):
        def handler(request):
            return httpx.Response(403)

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=1, retry_wait=wait_none()
        ) as scraper:
            with pytest.raises(ScraperBlockedError):
                await scraper._get("https://example.com/jobs")

    @pytest.mark.asyncio
    async def test_500_raises_rate_limit_error_treated_as_transient(self):
        def handler(request):
            return httpx.Response(500)

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=1, retry_wait=wait_none()
        ) as scraper:
            with pytest.raises(ScraperRateLimitError):
                await scraper._get("https://example.com/jobs")

    @pytest.mark.asyncio
    async def test_recovers_after_transient_failure_within_retry_budget(self):
        """First call 500s, second call (within the retry budget) succeeds."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500)
            return httpx.Response(200, json={"ok": True})

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=3, retry_wait=wait_none()
        ) as scraper:
            response = await scraper._get("https://example.com/jobs")
            assert response.status_code == 200

        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_max_retries_is_configurable_per_instance(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429)

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=5, retry_wait=wait_none()
        ) as scraper:
            with pytest.raises(ScraperRateLimitError):
                await scraper._get("https://example.com/jobs")

        assert calls["n"] == 5

    @pytest.mark.asyncio
    async def test_404_raises_via_raise_for_status(self):
        def handler(request):
            return httpx.Response(404)

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=1, retry_wait=wait_none()
        ) as scraper:
            with pytest.raises(httpx.HTTPStatusError):
                await scraper._get("https://example.com/jobs")


class TestPostRetryAndStatusHandling:
    """_post() shares _get()'s retry/status-handling core via _request() —
    these tests confirm the refactor works for POST specifically (needed
    for APIs like Workday's CXS endpoint, which is POST-only)."""

    def _client_for(self, handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @pytest.mark.asyncio
    async def test_successful_post_returns_response_and_sends_json_body(self):
        received = {}

        def handler(request):
            received["method"] = request.method
            received["body"] = request.content
            return httpx.Response(200, json={"ok": True})

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=1, retry_wait=wait_none()
        ) as scraper:
            response = await scraper._post("https://example.com/api", json={"limit": 20, "offset": 0})
            assert response.status_code == 200

        assert received["method"] == "POST"
        assert b'"limit": 20' in received["body"] or b'"limit":20' in received["body"]

    @pytest.mark.asyncio
    async def test_post_retries_and_raises_scraper_error_like_get(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429)

        async with FakeScraper(
            pages={}, client=self._client_for(handler), max_retries=3, retry_wait=wait_none()
        ) as scraper:
            with pytest.raises(ScraperRateLimitError):
                await scraper._post("https://example.com/api", json={})

        assert calls["n"] == 3


# ── Client lifecycle / injection ────────────────────────────────────────────

class TestClientLifecycle:

    @pytest.mark.asyncio
    async def test_owns_and_closes_its_own_client_by_default(self):
        scraper = FakeScraper(pages={1: PageResult(listings=[], has_next_page=False)})
        async with scraper:
            assert scraper._client is not None
            assert not scraper._client.is_closed
        assert scraper._client.is_closed

    @pytest.mark.asyncio
    async def test_injected_client_is_not_closed_on_exit(self):
        client = httpx.AsyncClient()
        scraper = FakeScraper(pages={}, client=client)
        async with scraper:
            pass
        assert not client.is_closed
        await client.aclose()

    @pytest.mark.asyncio
    async def test_get_raises_runtime_error_outside_context_manager(self):
        scraper = FakeScraper(pages={})
        with pytest.raises(RuntimeError, match="context manager"):
            await scraper._get("https://example.com")


# ── Configuration overrides ──────────────────────────────────────────────────

class TestConfigurationOverrides:

    def test_defaults_come_from_settings(self):
        scraper = FakeScraper(pages={})
        from config.settings import settings as app_settings
        assert scraper.max_retries == app_settings.scraper.max_retries
        assert scraper.max_pages == app_settings.scraper.max_pages

    def test_per_instance_overrides_win(self):
        scraper = FakeScraper(pages={}, max_retries=9, max_pages=1)
        assert scraper.max_retries == 9
        assert scraper.max_pages == 1

    def test_default_rate_limiter_built_from_settings(self):
        scraper = FakeScraper(pages={})
        from config.settings import settings as app_settings
        assert scraper.rate_limiter.min_delay == app_settings.scraper.delay_min
        assert scraper.rate_limiter.max_delay == app_settings.scraper.delay_max

    def test_default_retry_wait_is_exponential_backoff(self):
        from tenacity import wait_exponential
        scraper = FakeScraper(pages={})
        assert isinstance(scraper._retry_wait, wait_exponential)

    def test_custom_retry_wait_overrides_default(self):
        scraper = FakeScraper(pages={}, retry_wait=wait_none())
        assert scraper._retry_wait is not None
        assert isinstance(scraper._retry_wait, type(wait_none()))
