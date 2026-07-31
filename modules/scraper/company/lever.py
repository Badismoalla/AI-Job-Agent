"""
modules/scraper/company/lever.py
-----------------------------------
Lever job board scraper — targets one company's Lever-hosted careers page
at a time.

    GET https://api.lever.co/v0/postings/{site_name}?mode=json&skip={n}&limit={n}

Lever's public postings API is unauthenticated and well documented across
independent sources consulted while building this file, which agree on the
endpoint shape and on Lever supporting `skip`/`limit`/`team`/`department`/
`location`/`commitment`/`level` as real server-side query parameters — so
unlike Greenhouse/JustJoin.it, this scraper uses genuine server-side
pagination (skip/limit) rather than fetch-all-then-slice-locally.

One documented quirk, called out consistently: some Lever configurations
silently cap results around 250 postings without a clear "no more pages"
signal in the response. This scraper's has_next_page falls back to "did
we get a full page back" as the continuation signal (if the page is short,
assume there's no more) — and the base framework's own SCRAPER_MAX_PAGES
setting is a second, independent safety net against exactly this kind of
silent-cap risk.

Salary ("sometimes" present per multiple sources) is extracted best-effort
from a `salaryRange` field if present; description uses `descriptionPlain`,
which is documented as available directly on this endpoint (no separate
detail-page fetch needed, unlike the job-board scrapers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import PageResult
from modules.scraper.company.base import CompanyJobScraper

_RESULTS_PER_PAGE = 25


class LeverScraper(CompanyJobScraper):
    """Scraper for one company's Lever postings API."""

    platform_name = "lever"

    def __init__(self, *args, base_url: str = "https://api.lever.co", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = base_url.rstrip("/")
        self._current_roles: list[str] = []
        self._current_cities: list[str] = []

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> list[dict[str, Any]]:
        """Fetch one page of postings using Lever's real skip/limit pagination."""
        self._current_roles = roles
        self._current_cities = cities

        site_name = self.company.get("site_name")
        if not site_name:
            raise ScraperParseError(
                f"Company config for '{self.company_name}' is missing 'site_name'",
                board=self.board_name,
            )

        skip = (page - 1) * _RESULTS_PER_PAGE
        url = f"{self.base_url}/v0/postings/{site_name}"
        response = await self._get(
            url, params={"mode": "json", "skip": skip, "limit": _RESULTS_PER_PAGE},
        )
        data = response.json()
        if not isinstance(data, list):
            raise ScraperParseError(
                f"Expected a JSON array from {url}, got {type(data).__name__}",
                board=self.board_name,
            )
        return data

    # ── parse(): pure, no I/O ──────────────────────────────────────────────

    def parse_page(self, raw_page: list[dict[str, Any]], page: int) -> PageResult:
        """
        Filter this page's postings by role/city (cached from the most
        recent fetch_page() call — see CompanyJobScraper/JustJoinITScraper
        for why parse_page() needs this cached rather than passed directly).
        """
        if not isinstance(raw_page, list):
            raise ScraperParseError(
                f"Expected a list of postings, got {type(raw_page).__name__}", board=self.board_name,
            )

        listings: list[JobListing] = []
        for posting in raw_page:
            if not isinstance(posting, dict):
                continue
            title = posting.get("text")
            location = self._extract_location(posting)
            if not title or not self._matches_filters(
                str(title), location, self._current_roles, self._current_cities
            ):
                continue
            parsed = self._parse_posting(posting)
            if parsed is not None:
                listings.append(parsed)

        # Real server-side pagination: a full page suggests more may follow;
        # a short page is the natural end (also guards the ~250 silent-cap
        # quirk noted in the module docstring, alongside SCRAPER_MAX_PAGES).
        has_next_page = len(raw_page) >= _RESULTS_PER_PAGE

        return PageResult(listings=listings, has_next_page=has_next_page)

    # ── Parsing helpers ──────────────────────────────────────────────────────

    def _parse_posting(self, posting: dict[str, Any]) -> JobListing | None:
        title = posting.get("text")
        posting_id = posting.get("id")
        url = posting.get("hostedUrl") or posting.get("applyUrl")

        if not title or not posting_id or not url:
            self.logger.warning(
                "Skipping unparseable posting — missing text/id/url | board={board} | posting_id={id}",
                board=self.board_name, id=posting.get("id", "unknown"),
            )
            return None

        location = self._extract_location(posting)
        description = posting.get("descriptionPlain") or None
        posted_date = self._extract_posted_date(posting)
        salary = self._extract_salary(posting)

        return JobListing(
            id=f"{self.platform_name}-{self.company.get('site_name', self._slug(self.company_name))}-{posting_id}",
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
    def _extract_location(posting: dict[str, Any]) -> str:
        categories = posting.get("categories")
        if isinstance(categories, dict) and categories.get("location"):
            return str(categories["location"])
        return "Unknown"

    @staticmethod
    def _infer_market(location: str) -> Market:
        """See GreenhouseScraper._infer_market for the same rationale —
        best-effort free-text matching, Poland as the project's default."""
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
    def _extract_posted_date(posting: dict[str, Any]) -> datetime | None:
        created_at = posting.get("createdAt")
        if not isinstance(created_at, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _extract_salary(posting: dict[str, Any]) -> str | None:
        """Best-effort: Lever "sometimes" includes salary per multiple
        sources, in a `salaryRange` field on postings that opt in."""
        salary_range = posting.get("salaryRange")
        if not isinstance(salary_range, dict):
            return None
        low = salary_range.get("min")
        high = salary_range.get("max")
        currency = salary_range.get("currency", "")
        if low is None and high is None:
            return None
        if low is not None and high is not None:
            range_text = f"{low:,} - {high:,}".replace(",", " ")
        else:
            range_text = str(low if low is not None else high)
        return " ".join(p for p in (range_text, currency) if p).strip()
