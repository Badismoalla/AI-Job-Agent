"""
modules/scraper/company/smartrecruiters.py
----------------------------------------------
SmartRecruiters job board scraper — targets one company's SmartRecruiters
careers page at a time.

    GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings?offset={n}&limit={n}

SmartRecruiters' public postings API is unauthenticated and, per multiple
independent sources consulted while building this file, is the one ATS in
this package with unambiguous, real server-side offset/limit pagination —
the response includes `totalFound`, so the "are there more pages" question
has a precise answer rather than a heuristic one (unlike Lever's
full-page-implies-more heuristic, or Greenhouse's no-pagination-at-all).

The postings LIST endpoint returns summary fields only (title, location,
company, posting URL, released date) — full job descriptions require a
separate per-posting detail call (`/postings/{id}`), which this scraper
does not make, for the same reason noted in nofluffjobs.py/pracuj.py:
avoiding compounding unverified-endpoint risk in a single pass. `description`
is therefore None here; a future `fetch_posting_detail(posting_id)` method
would be the natural place to add it.

Salary is rarely present in the public postings feed and is not part of
this scraper's extracted fields, consistent with SmartRecruiters' own
documented sparse compensation support.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import PageResult
from modules.scraper.company.base import CompanyJobScraper

_RESULTS_PER_PAGE = 100  # SmartRecruiters' own commonly-used page size


class SmartRecruitersScraper(CompanyJobScraper):
    """Scraper for one company's SmartRecruiters postings API."""

    platform_name = "smartrecruiters"

    def __init__(self, *args, base_url: str = "https://api.smartrecruiters.com", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = base_url.rstrip("/")
        self._current_roles: list[str] = []
        self._current_cities: list[str] = []

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> dict[str, Any]:
        """Fetch one page of postings using SmartRecruiters' real offset/limit pagination."""
        self._current_roles = roles
        self._current_cities = cities

        company_id = self.company.get("company_id")
        if not company_id:
            raise ScraperParseError(
                f"Company config for '{self.company_name}' is missing 'company_id'",
                board=self.board_name,
            )

        offset = (page - 1) * _RESULTS_PER_PAGE
        url = f"{self.base_url}/v1/companies/{company_id}/postings"
        response = await self._get(url, params={"offset": offset, "limit": _RESULTS_PER_PAGE})
        data = response.json()
        if not isinstance(data, dict):
            raise ScraperParseError(
                f"Expected a JSON object from {url}, got {type(data).__name__}",
                board=self.board_name,
            )
        return data

    # ── parse(): pure, no I/O ──────────────────────────────────────────────

    def parse_page(self, raw_page: dict[str, Any], page: int) -> PageResult:
        if not isinstance(raw_page, dict):
            raise ScraperParseError(
                f"Expected a JSON object, got {type(raw_page).__name__}", board=self.board_name,
            )

        content = raw_page.get("content")
        if content is None:
            raise ScraperParseError(
                "Response missing 'content' key", board=self.board_name,
            )
        if not isinstance(content, list):
            raise ScraperParseError(
                f"'content' must be a list, got {type(content).__name__}", board=self.board_name,
            )

        listings: list[JobListing] = []
        for posting in content:
            if not isinstance(posting, dict):
                continue
            title = posting.get("name")
            location = self._extract_location(posting)
            if not title or not self._matches_filters(
                str(title), location, self._current_roles, self._current_cities
            ):
                continue
            parsed = self._parse_posting(posting)
            if parsed is not None:
                listings.append(parsed)

        total_found = raw_page.get("totalFound", 0)
        offset = raw_page.get("offset", (page - 1) * _RESULTS_PER_PAGE)
        returned = len(content)
        has_next_page = (offset + returned) < total_found

        return PageResult(listings=listings, has_next_page=has_next_page)

    # ── Parsing helpers ──────────────────────────────────────────────────────

    def _parse_posting(self, posting: dict[str, Any]) -> JobListing | None:
        title = posting.get("name")
        posting_id = posting.get("id")

        if not title or not posting_id:
            self.logger.warning(
                "Skipping unparseable posting — missing name/id | board={board} | posting_id={id}",
                board=self.board_name, id=posting.get("id", "unknown"),
            )
            return None

        location = self._extract_location(posting)
        url = self._extract_url(posting, str(posting_id))
        posted_date = self._extract_posted_date(posting)

        return JobListing(
            id=f"{self.platform_name}-{self.company.get('company_id', self._slug(self.company_name))}-{posting_id}",
            title=str(title),
            company=self.company_name,
            city=location,
            market=self._infer_market(location),
            url=url,
            source=ApplicationSource.CAREER_PAGE,
            description=None,  # see module docstring: list endpoint is summary-only
            salary_range=None,  # rarely present; not part of this scraper's fields
            posted_date=posted_date,
        )

    @staticmethod
    def _extract_location(posting: dict[str, Any]) -> str:
        location = posting.get("location")
        if not isinstance(location, dict):
            return "Unknown"
        parts = [location.get("city"), location.get("region"), location.get("country")]
        parts = [str(p) for p in parts if p]
        return ", ".join(parts) if parts else "Unknown"

    def _extract_url(self, posting: dict[str, Any], posting_id: str) -> str:
        ref = posting.get("ref")
        if isinstance(ref, dict) and ref.get("jobAdUrl"):
            return str(ref["jobAdUrl"])
        company_id = self.company.get("company_id", "")
        return f"https://jobs.smartrecruiters.com/{company_id}/{posting_id}"

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
        released_date = posting.get("releasedDate")
        if not released_date:
            return None
        try:
            return datetime.fromisoformat(str(released_date).replace("Z", "+00:00"))
        except ValueError:
            return None
