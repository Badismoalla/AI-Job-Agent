"""
modules/scraper/base.py
-----------------------
Abstract framework that every job board scraper is built on.

Why a framework, not just an interface?
- A bare abstract `scrape()` method means every board reimplements
  pagination, rate limiting, retry, logging, and deduplication from
  scratch — and each one gets it slightly differently.
- Instead, `scrape()` is a concrete *template method* implemented once,
  here. Every concrete scraper (PracujScraper, NoFluffJobsScraper, ...)
  only implements two small, focused methods:

    fetch_page(page, roles, cities) -> raw page data   (I/O — network)
    parse_page(raw, page)           -> PageResult       (pure — no I/O)

  That fetch/parse split is deliberate: parse_page is a plain synchronous
  function with no network access, so it can be unit tested against a
  static fixture (saved HTML/JSON) with zero mocking. fetch_page is the
  only place that touches the network, so retry/rate-limit/logging concerns
  live in exactly one place (this base class) instead of every subclass.

Every scraper gets, for free:
- A shared, retry-wrapped HTTP client (`self._get()`)
- Configurable rate limiting between requests (modules/scraper/rate_limiter.py)
- Configurable retry with exponential backoff (SCRAPER_MAX_RETRIES)
- Pagination up to SCRAPER_MAX_PAGES, with fail-soft partial results
- Structured logging with the board name bound automatically
- Validation that parse_page() actually returned JobListing objects
- Deduplication by id before returning results

Every scraper returns the same model: list[core.models.JobListing]. Nothing
downstream (matcher, AI generator, tracker) needs to know which board a
listing came from to process it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential
from tenacity.wait import wait_base

from config.settings import settings
from core.exceptions import ScraperBlockedError, ScraperError, ScraperParseError, ScraperRateLimitError
from core.logger import get_logger
from core.models import JobListing
from modules.scraper.rate_limiter import RateLimiter


@dataclass
class PageResult:
    """
    What parse_page() must return for a single page.

    has_next_page tells the pagination loop in scrape() whether to keep
    going. Concrete scrapers decide how they know this — a "Next" link
    being present, a total-results count vs. items-seen-so-far, etc.
    """

    listings: list[JobListing] = field(default_factory=list)
    has_next_page: bool = False


class BaseJobScraper(ABC):
    """
    Abstract base for all job board scrapers.

    Concrete implementations (e.g. PracujScraper, NoFluffJobsScraper) only
    need to implement `fetch_page()` and `parse_page()`. `scrape()` itself
    is implemented here and should not normally be overridden.

    Usage:
        async with PracujScraper() as scraper:
            listings = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

    Fully testable:
        - Inject a fake/mock httpx.AsyncClient via `client=` for `_get()`
          tests (e.g. httpx.AsyncClient(transport=httpx.MockTransport(...))).
        - Inject a RateLimiter(min_delay=0, max_delay=0) to skip real waits.
        - Or skip HTTP entirely: subclass with fetch_page()/parse_page()
          that return canned data, and test scrape()'s orchestration
          (pagination, dedup, validation) in complete isolation.
    """

    board_name: str = "unknown"

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        max_retries: int | None = None,
        max_pages: int | None = None,
        client: httpx.AsyncClient | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        """
        Args:
            rate_limiter: Override the default settings-derived RateLimiter
                (e.g. inject a zero-delay limiter in tests).
            max_retries: Override SCRAPER_MAX_RETRIES for this instance.
            max_pages: Override SCRAPER_MAX_PAGES for this instance.
            client: Inject an existing httpx.AsyncClient (e.g. wired to
                httpx.MockTransport for tests). If provided, this scraper
                will NOT close it on __aexit__ — the caller owns its lifecycle.
            retry_wait: Override the tenacity wait strategy used between
                retry attempts in `_get()`. Defaults to exponential backoff
                (4s-30s) — the production behaviour. Tests inject
                `tenacity.wait_none()` so retry tests run instantly instead
                of actually sleeping through real backoff delays.
        """
        self.logger = get_logger(f"scraper.{self.board_name}")
        self.rate_limiter = rate_limiter or RateLimiter.from_settings(settings.scraper)
        self.max_retries = max_retries if max_retries is not None else settings.scraper.max_retries
        self.max_pages = max_pages if max_pages is not None else settings.scraper.max_pages
        self._retry_wait = retry_wait or wait_exponential(multiplier=1, min=4, max=30)

        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "BaseJobScraper":
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": settings.scraper.user_agent},
                timeout=settings.scraper.timeout,
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()

    # ── Subclass contract ────────────────────────────────────────────────────

    @abstractmethod
    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        """
        Fetch one page of results (I/O only — no parsing here).

        Should use `self._get(url)` for the actual HTTP call so retry,
        rate-limit-triggering status codes, and logging are handled
        consistently. Return whatever `parse_page()` needs — a raw
        httpx.Response, its parsed JSON body, a BeautifulSoup tree, etc.

        Args:
            page: 1-indexed page number.
            roles: Target role titles to search for.
            cities: Target cities to search in.
        """
        ...

    @abstractmethod
    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        """
        Parse one already-fetched page into a PageResult.

        Deliberately synchronous: this method must not perform any I/O.
        Keeping parsing pure and separate from fetching is what makes it
        possible to unit test parsing logic against a static fixture file,
        with no network mocking required.

        Args:
            raw_page: Whatever fetch_page() returned for this page.
            page: The page number that was fetched (1-indexed).
        """
        ...

    # ── Template method — do not override ───────────────────────────────────

    async def scrape(self, roles: list[str], cities: list[str]) -> list[JobListing]:
        """
        Scrape the job board for matching listings across all pages.

        Orchestrates: fetch_page -> parse_page -> validate -> paginate,
        applying rate limiting between pages, up to `self.max_pages`.
        Deduplicates before returning.

        Fail-soft: if a page fails after retries are exhausted, the scrape
        stops pagination and returns whatever was collected so far, rather
        than losing already-scraped results from earlier pages. If the very
        first page fails, the error propagates — there's nothing useful to
        return, and the caller should know the board failed outright.

        Returns:
            Deduplicated list of JobListing objects.
        """
        if self._client is None:
            raise RuntimeError("Scraper must be used as an async context manager")

        self.logger.info(
            "Scrape started | board={board} | roles={roles} | cities={cities}",
            board=self.board_name, roles=roles, cities=cities,
        )

        all_listings: list[JobListing] = []
        page = 1
        reached_max_pages = False

        while page <= self.max_pages:
            if page > 1:
                await self.rate_limiter.wait()

            try:
                raw_page = await self.fetch_page(page=page, roles=roles, cities=cities)
                result = self.parse_page(raw_page, page=page)
            except ScraperError as e:
                if page == 1:
                    self.logger.error(
                        "First page failed — aborting scrape | board={board} | error={error}",
                        board=self.board_name, error=str(e),
                    )
                    raise
                self.logger.warning(
                    "Page failed after retries — stopping pagination with partial results | "
                    "board={board} | page={page} | collected_so_far={count} | error={error}",
                    board=self.board_name, page=page, count=len(all_listings), error=str(e),
                )
                break

            self._validate_page_result(result)
            all_listings.extend(result.listings)

            self.logger.info(
                "Page scraped | board={board} | page={page} | found={found} | total={total}",
                board=self.board_name, page=page, found=len(result.listings), total=len(all_listings),
            )

            if not result.has_next_page:
                break

            page += 1
        else:
            reached_max_pages = True

        if reached_max_pages:
            self.logger.warning(
                "Max pages reached | board={board} | max_pages={max_pages}",
                board=self.board_name, max_pages=self.max_pages,
            )

        deduped = self._deduplicate(all_listings)
        self.logger.info(
            "Scrape complete | board={board} | total_found={total} | after_dedup={deduped}",
            board=self.board_name, total=len(all_listings), deduped=len(deduped),
        )
        return deduped

    # ── Shared HTTP helper (retry + status-code handling) ──────────────────

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        """
        GET with automatic retry and rate-limit/block detection.

        Retries up to `self.max_retries` times with exponential backoff on
        ANY exception raised during the attempt (network errors, timeouts,
        and the ScraperRateLimitError/ScraperBlockedError raised below).
        Raises the last exception if all attempts are exhausted.
        """
        if self._client is None:
            raise RuntimeError("Scraper must be used as an async context manager")

        last_response: httpx.Response | None = None

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=self._retry_wait,
            reraise=True,
        ):
            with attempt:
                self.logger.debug(
                    "GET {url} | attempt={attempt}/{max_retries}",
                    url=url, attempt=attempt.retry_state.attempt_number, max_retries=self.max_retries,
                )
                response = await self._client.get(url, **kwargs)
                last_response = response
                self._raise_for_scraper_status(response, url)

        # AsyncRetrying with reraise=True guarantees either a successful
        # response above or a raised exception — this line is unreachable
        # in practice, but keeps type checkers and linters happy.
        assert last_response is not None
        return last_response

    def _raise_for_scraper_status(self, response: httpx.Response, url: str) -> None:
        """Translate HTTP status codes into scraper-specific exceptions."""
        if response.status_code == 429:
            raise ScraperRateLimitError("Rate limited (429)", board=self.board_name, url=url)
        if response.status_code == 403:
            raise ScraperBlockedError("Blocked (403)", board=self.board_name, url=url)
        if response.status_code >= 500:
            raise ScraperRateLimitError(
                f"Server error ({response.status_code})", board=self.board_name, url=url,
            )
        response.raise_for_status()

    # ── Result shaping ───────────────────────────────────────────────────────

    def _validate_page_result(self, result: PageResult) -> None:
        """
        Ensure parse_page() honoured the framework contract: a PageResult
        whose `listings` is a list of JobListing instances. This is what
        guarantees every scraper returns the same model — a parsing bug in
        one board's scraper fails loudly here instead of quietly handing
        malformed data downstream to the matcher.
        """
        if not isinstance(result, PageResult):
            raise ScraperParseError(
                f"parse_page() must return a PageResult, got {type(result).__name__}",
                board=self.board_name,
            )
        if not isinstance(result.listings, list):
            raise ScraperParseError(
                f"PageResult.listings must be a list, got {type(result.listings).__name__}",
                board=self.board_name,
            )
        for item in result.listings:
            if not isinstance(item, JobListing):
                raise ScraperParseError(
                    f"parse_page() returned a non-JobListing item: {type(item).__name__}",
                    board=self.board_name,
                )

    def _deduplicate(self, jobs: list[JobListing]) -> list[JobListing]:
        """Remove duplicate listings by ID. Logs how many were removed."""
        seen: set[str] = set()
        unique = []
        for job in jobs:
            if job.id not in seen:
                seen.add(job.id)
                unique.append(job)
        removed = len(jobs) - len(unique)
        if removed:
            self.logger.info(
                "Deduplication | board={board} | removed={removed} duplicates",
                board=self.board_name, removed=removed,
            )
        return unique
