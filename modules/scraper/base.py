"""
modules/scraper/base.py
-----------------------
Abstract base class that every job board scraper must implement.

Why an abstract base?
- Forces every scraper to implement the same interface
- main.py and the daily runner can call any scraper identically
- Adding a new board = create a new class, nothing else changes

Every scraper gets:
- Shared HTTP client with rate limiting
- Retry logic via tenacity
- Structured logging with board name bound automatically
- Deduplication check before returning results
"""

import asyncio
import random
from abc import ABC, abstractmethod

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from core.exceptions import ScraperBlockedError, ScraperRateLimitError
from core.logger import get_logger
from core.models import JobListing


class BaseJobScraper(ABC):
    """
    Abstract base for all job board scrapers.

    Concrete implementations: PracujScraper, NoFluffJobsScraper, etc.
    Each implements `scrape()` and returns a list of JobListing objects.
    """

    board_name: str = "unknown"

    def __init__(self) -> None:
        self.logger = get_logger(f"scraper.{self.board_name}")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": settings.scraper.user_agent},
            timeout=settings.scraper.timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    @abstractmethod
    async def scrape(self, roles: list[str], cities: list[str]) -> list[JobListing]:
        """
        Scrape the job board for matching listings.

        Args:
            roles: Target role titles from profile.target.roles
            cities: Target cities from profile.target.cities

        Returns:
            List of JobListing objects, deduplicated
        """
        ...

    async def _polite_delay(self) -> None:
        """Wait a random interval between requests — be a good bot."""
        delay = random.uniform(
            settings.scraper.delay_min,
            settings.scraper.delay_max,
        )
        self.logger.debug("Polite delay | seconds={delay:.1f}", delay=delay)
        await asyncio.sleep(delay)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        reraise=True,
    )
    async def _get(self, url: str, **kwargs) -> httpx.Response:
        """
        GET with automatic retry + rate limit handling.
        Raises ScraperRateLimitError or ScraperBlockedError on failure.
        """
        if self._client is None:
            raise RuntimeError("Scraper must be used as async context manager")

        self.logger.debug("GET {url}", url=url)
        response = await self._client.get(url, **kwargs)

        if response.status_code == 429:
            raise ScraperRateLimitError(
                f"Rate limited (429)", board=self.board_name, url=url
            )
        if response.status_code == 403:
            raise ScraperBlockedError(
                f"Blocked (403)", board=self.board_name, url=url
            )
        if response.status_code >= 500:
            raise ScraperRateLimitError(
                f"Server error ({response.status_code})",
                board=self.board_name,
                url=url,
            )

        response.raise_for_status()
        return response

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
                "Deduplication | removed={removed} duplicates", removed=removed
            )
        return unique
