"""
modules/scraper/company/greenhouse.py
----------------------------------------
Greenhouse job board scraper — targets one company's Greenhouse-hosted
careers page at a time.

    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

This is Greenhouse's official, public Job Board API (developers.greenhouse.io/
job-board.html) — unauthenticated for GET, explicitly documented, and
corroborated by multiple independent sources while building this file. Of
the four ATS scrapers in this package, this one carries the LEAST schema
risk: the endpoint, auth model, and field names below match Greenhouse's
own public documentation, not just third-party recollection.

One documented quirk: `content` (the full job description) is returned as
HTML, but HTML-entity-escaped (e.g. literal `&lt;p&gt;` instead of `<p>`).
This scraper unescapes it, then strips tags to plain text.

No server-side pagination: one GET returns every open role on the board
(confirmed by multiple independent sources — Greenhouse boards commonly
list 500+ roles in a single response). This scraper fetches once, caches
on the instance, and paginates + filters the result set locally — the
same "fetch once" strategy JustJoinITScraper uses for the same reason.

Salary data is rarely present on Greenhouse and isn't part of the
documented schema — extraction here is best-effort (checks a `metadata`
field some boards use) and will be None for most companies.
"""

from __future__ import annotations

import html as html_module
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import PageResult
from modules.scraper.company.base import CompanyJobScraper

_RESULTS_PER_PAGE = 20


class GreenhouseScraper(CompanyJobScraper):
    """Scraper for one company's Greenhouse Job Board API."""

    platform_name = "greenhouse"

    def __init__(self, *args, base_url: str = "https://boards-api.greenhouse.io", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = base_url.rstrip("/")
        self._cached_jobs: list[dict[str, Any]] | None = None
        self._current_roles: list[str] = []
        self._current_cities: list[str] = []

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> list[dict[str, Any]]:
        """
        Fetch the full job list for this company, once, caching it on the
        instance. Greenhouse has no server-side pagination — filtering and
        paging both happen in parse_page().
        """
        self._current_roles = roles
        self._current_cities = cities

        if self._cached_jobs is None:
            board_token = self.company.get("board_token")
            if not board_token:
                raise ScraperParseError(
                    f"Company config for '{self.company_name}' is missing 'board_token'",
                    board=self.board_name,
                )
            url = f"{self.base_url}/v1/boards/{board_token}/jobs"
            response = await self._get(url, params={"content": "true"})
            data = response.json()
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(jobs, list):
                raise ScraperParseError(
                    f"Expected a 'jobs' array in the response, got {type(data).__name__}",
                    board=self.board_name,
                )
            self._cached_jobs = jobs

        return self._cached_jobs

    # ── parse(): pure, no I/O ──────────────────────────────────────────────

    def parse_page(self, raw_page: list[dict[str, Any]], page: int) -> PageResult:
        """
        Filter the cached job list by role/city, then return the requested
        page of the filtered results.
        """
        if not isinstance(raw_page, list):
            raise ScraperParseError(
                f"Expected a list of jobs, got {type(raw_page).__name__}", board=self.board_name,
            )

        listings: list[JobListing] = []
        for job in raw_page:
            if not isinstance(job, dict):
                continue
            title = job.get("title")
            location = self._extract_location(job)
            if not title or not self._matches_filters(title, location, self._current_roles, self._current_cities):
                continue
            parsed = self._parse_job(job)
            if parsed is not None:
                listings.append(parsed)

        start = (page - 1) * _RESULTS_PER_PAGE
        end = start + _RESULTS_PER_PAGE
        page_slice = listings[start:end]
        has_next_page = end < len(listings)

        return PageResult(listings=page_slice, has_next_page=has_next_page)

    # ── Parsing helpers ──────────────────────────────────────────────────────

    def _parse_job(self, job: dict[str, Any]) -> JobListing | None:
        title = job.get("title")
        job_id = job.get("id")
        url = job.get("absolute_url")

        if not title or job_id is None or not url:
            self.logger.warning(
                "Skipping unparseable job — missing title/id/url | board={board} | job_id={id}",
                board=self.board_name, id=job.get("id", "unknown"),
            )
            return None

        location = self._extract_location(job)
        description = self._extract_description(job)
        posted_date = self._extract_posted_date(job)
        salary = self._extract_salary(job)

        return JobListing(
            id=f"{self.platform_name}-{self.company.get('board_token', self._slug(self.company_name))}-{job_id}",
            title=str(title),
            company=self.company_name,
            city=location,
            market=self._infer_market(location),
            url=str(url),
            source=ApplicationSource.CAREER_PAGE,
            description=description,
            salary_range=salary,
            posted_date=posted_date,
        )

    @staticmethod
    def _extract_location(job: dict[str, Any]) -> str:
        location = job.get("location")
        if isinstance(location, dict) and location.get("name"):
            return str(location["name"])
        return "Unknown"

    @staticmethod
    def _infer_market(location: str) -> Market:
        """
        Best-effort market inference from the free-text location string.
        Greenhouse doesn't provide a structured country field in this
        endpoint — falls back to Market.POLAND (this project's default
        target market) when nothing more specific is recognisable, since
        an unrecognised market is still more useful triaged than dropped.
        """
        location_lower = location.lower()
        market_keywords = {
            Market.POLAND: ["poland", "warsaw", "krakow", "wroclaw", "gdansk"],
            Market.NETHERLANDS: ["netherlands", "amsterdam", "rotterdam", "utrecht"],
            Market.LUXEMBOURG: ["luxembourg"],
            Market.UAE: ["uae", "united arab emirates", "dubai", "abu dhabi"],
            Market.SAUDI_ARABIA: ["saudi arabia", "riyadh", "jeddah"],
            Market.QATAR: ["qatar", "doha"],
        }
        for market, keywords in market_keywords.items():
            if any(kw in location_lower for kw in keywords):
                return market
        return Market.POLAND

    @staticmethod
    def _extract_description(job: dict[str, Any]) -> str | None:
        """
        Unlike the job-board scrapers, Greenhouse provides the FULL job
        description directly — no separate detail-page fetch needed. It's
        HTML-entity-escaped HTML; unescape then strip tags to plain text.
        """
        content = job.get("content")
        if not content:
            return None
        unescaped = html_module.unescape(str(content))
        text = BeautifulSoup(unescaped, "lxml").get_text("\n", strip=True)
        return text or None

    @staticmethod
    def _extract_posted_date(job: dict[str, Any]) -> datetime | None:
        """
        Note: Greenhouse's `updated_at` reflects the last time the posting
        was edited, not necessarily when it was first published — a
        documented quirk, not a parsing assumption.
        """
        updated_at = job.get("updated_at")
        if not updated_at:
            return None
        try:
            return datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _extract_salary(job: dict[str, Any]) -> str | None:
        """
        Best-effort only: salary is rarely present on Greenhouse and isn't
        part of the documented schema. Checks the optional `metadata` list
        some boards populate with a compensation-related field.
        """
        metadata = job.get("metadata")
        if not isinstance(metadata, list):
            return None
        for field in metadata:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name", "")).lower()
            if any(term in name for term in ("salary", "pay", "compensation")):
                value = field.get("value")
                if value:
                    return str(value)
        return None
