"""
modules/scraper/nofluffjobs.py
-------------------------------
NoFluffJobs.com scraper — the first concrete implementation of
BaseJobScraper.

NoFluffJobs exposes a public JSON search API (no login/cookies required),
so this scraper talks to that API directly instead of parsing rendered
HTML — faster, more stable, and avoids brittle CSS selectors:

    GET https://nofluffjobs.com/api/search/posting?criteria=<text>&page=<n>

╔══════════════════════════════════════════════════════════════════════════╗
║ SCHEMA CAVEAT — read before relying on this in production                ║
║                                                                          ║
║ This sandbox has no network access to nofluffjobs.com, so the exact     ║
║ live JSON response shape could not be verified against the real         ║
║ endpoint while writing this file. Field extraction below targets        ║
║ NoFluffJobs' publicly documented search API shape (confirmed via web    ║
║ search: "Public API — no login or cookies required", used by several    ║
║ third-party scrapers), but exact field names may have drifted.          ║
║                                                                          ║
║ Parsing is written defensively — each field extractor tries the most    ║
║ likely key path with sensible fallbacks, and a genuinely unparseable    ║
║ posting is skipped with a logged warning rather than crashing the whole ║
║ page. tests/unit/test_nofluffjobs.py encodes the assumed shape          ║
║ explicitly as a fixture. Before production use: fetch one real page,    ║
║ diff it against NOFLUFFJOBS_FIXTURE_POSTING in the test file, and       ║
║ adjust the handful of `posting.get(...)` calls in _parse_posting() —    ║
║ that's the only place schema knowledge lives.                          ║
╚══════════════════════════════════════════════════════════════════════════╝

Search strategy:
NoFluffJobs' search bar takes one free-text query at a time (keyword +
optional city, e.g. "test engineer krakow") — it doesn't support multiple
independent role/city combinations in a single query. This scraper's
`scrape(roles, cities)` therefore searches using the *first* role and
*first* city given. To cover multiple roles/cities, call `scrape()` once
per combination (this matches how a person would actually use the site's
own search box, and keeps this scraper consistent with the base
framework's one-query-per-scrape() page-fetching model).

Full job descriptions are not reliably present in the search-results
payload (NoFluffJobs' search API is a summary/listing endpoint) — this
scraper populates `description` from whatever short summary text the
search response provides, falling back to None. Fetching the complete
description would mean an additional per-listing detail-page request;
that's a natural follow-up (e.g. a `fetch_posting_detail(slug)` method)
but is out of scope here to avoid compounding unverified-endpoint risk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from slugify import slugify

from core.exceptions import ScraperParseError
from core.logger import get_logger
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import BaseJobScraper, PageResult

# NoFluffJobs is a Poland/CEE-first board; every listing found here maps to
# this market unless/until multi-market parsing is added.
_DEFAULT_MARKET = Market.POLAND

# NoFluffJobs' own default search page size, used to infer has_next_page
# from (page * page_size) vs. totalCount when the API doesn't say so directly.
_RESULTS_PER_PAGE = 20


class NoFluffJobsScraper(BaseJobScraper):
    """Scraper for nofluffjobs.com's public JSON search API."""

    board_name = "nofluffjobs"

    def __init__(self, *args, base_url: str = "https://nofluffjobs.com", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = base_url.rstrip("/")

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> dict[str, Any]:
        """
        Fetch one page of search results as parsed JSON.

        Uses the first role and first city given — see module docstring
        for why NoFluffJobs' single free-text search doesn't support
        multiple independent role/city combinations in one query.
        """
        criteria = self._build_criteria(roles, cities)
        url = f"{self.base_url}/api/search/posting"
        params = {"criteria": criteria, "page": page}

        response = await self._get(url, params=params)
        return response.json()

    # ── parse(): pure, no I/O ──────────────────────────────────────────────

    def parse_page(self, raw_page: dict[str, Any], page: int) -> PageResult:
        """
        Parse one page of NoFluffJobs search JSON into a PageResult.

        Deliberately synchronous and side-effect-free (besides logging a
        warning per skipped posting) — see BaseJobScraper docstring for why
        that separation matters for testability.
        """
        if not isinstance(raw_page, dict):
            raise ScraperParseError(
                f"Expected a JSON object from the search API, got {type(raw_page).__name__}",
                board=self.board_name,
            )

        postings = raw_page.get("postings")
        if postings is None:
            raise ScraperParseError(
                "Search response missing 'postings' key", board=self.board_name,
            )

        listings: list[JobListing] = []
        for posting in postings:
            job = self._parse_posting(posting)
            if job is not None:
                listings.append(job)

        total_count = raw_page.get("totalCount", 0)
        seen_so_far = page * _RESULTS_PER_PAGE
        has_next_page = bool(postings) and seen_so_far < total_count

        return PageResult(listings=listings, has_next_page=has_next_page)

    # ── Parsing helpers (each field extraction isolated + independently testable) ──

    def _parse_posting(self, posting: dict[str, Any]) -> JobListing | None:
        """
        Parse a single posting dict into a JobListing.

        Returns None (and logs a warning) rather than raising if a posting
        is missing something un-defaultable — one bad posting shouldn't
        sink the whole page.
        """
        try:
            title = self._require(posting, "title")
            slug = self._require(posting, "url")
        except KeyError as e:
            self.logger.warning(
                "Skipping unparseable posting — missing required field | "
                "board={board} | error={error} | posting_id={id}",
                board=self.board_name, error=str(e), id=posting.get("id", "unknown"),
            )
            return None

        company = self._extract_company(posting)
        city = self._extract_city(posting)
        salary = self._extract_salary(posting)
        description = self._extract_description(posting)
        posted_date = self._extract_posted_date(posting)

        job_id = slugify(f"{company}-{title}-{city}", separator="-")

        return JobListing(
            id=job_id,
            title=title,
            company=company,
            city=city,
            market=_DEFAULT_MARKET,
            url=f"{self.base_url}/pl/job/{slug}",
            source=ApplicationSource.NOFLUFFJOBS,
            description=description,
            salary_range=salary,
            posted_date=posted_date,
        )

    @staticmethod
    def _require(posting: dict[str, Any], key: str) -> str:
        value = posting.get(key)
        if not value:
            raise KeyError(key)
        return str(value)

    @staticmethod
    def _extract_company(posting: dict[str, Any]) -> str:
        """Company name: top-level 'name', or nested company.name, or 'Unknown'."""
        if posting.get("name"):
            return str(posting["name"])
        company = posting.get("company")
        if isinstance(company, dict) and company.get("name"):
            return str(company["name"])
        return "Unknown"

    @staticmethod
    def _extract_city(posting: dict[str, Any]) -> str:
        """
        City: first place in location.places[], or a top-level 'city',
        or 'Remote' if the posting is marked fully remote, else 'Unknown'.
        """
        location = posting.get("location")
        if isinstance(location, dict):
            places = location.get("places")
            if isinstance(places, list) and places:
                first_place = places[0]
                if isinstance(first_place, dict) and first_place.get("city"):
                    return str(first_place["city"])
            if location.get("fullyRemote"):
                return "Remote"
            if location.get("city"):
                return str(location["city"])
        if posting.get("city"):
            return str(posting["city"])
        return "Unknown"

    @staticmethod
    def _extract_salary(posting: dict[str, Any]) -> str | None:
        """Build a human-readable salary range string, or None if absent."""
        salary = posting.get("salary")
        if not isinstance(salary, dict):
            return None

        low = salary.get("from")
        high = salary.get("to")
        currency = salary.get("currency", "")
        contract_type = salary.get("type", "")

        if low is None and high is None:
            return None

        if low is not None and high is not None:
            range_text = f"{low:,} - {high:,}".replace(",", " ")
        else:
            range_text = str(low if low is not None else high)

        parts = [range_text, currency]
        if contract_type:
            parts.append(f"({contract_type})")
        return " ".join(p for p in parts if p).strip()

    @staticmethod
    def _extract_description(posting: dict[str, Any]) -> str | None:
        """
        Short summary text if the search API provides one. Full JD text
        typically requires a separate per-listing detail-page fetch — see
        module docstring.
        """
        for key in ("introduction", "description", "about"):
            value = posting.get(key)
            if value:
                return str(value)

        requirements = posting.get("requirements") or posting.get("mustHave")
        if isinstance(requirements, list) and requirements:
            bullets = [str(item) for item in requirements if item]
            if bullets:
                return "\n".join(f"- {b}" for b in bullets)

        return None

    @staticmethod
    def _extract_posted_date(posting: dict[str, Any]) -> datetime | None:
        """Posted date, if present as epoch milliseconds."""
        posted = posting.get("posted")
        if not isinstance(posted, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(posted / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _build_criteria(roles: list[str], cities: list[str]) -> str:
        """
        Build the free-text search criteria NoFluffJobs' search bar expects:
        the first role keyword, optionally followed by the first city.
        """
        if not roles:
            raise ValueError("At least one role keyword is required to search NoFluffJobs")

        parts = [roles[0]]
        if cities:
            parts.append(cities[0])
        return " ".join(parts)
