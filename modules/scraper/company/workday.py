"""
modules/scraper/company/workday.py
--------------------------------------
Workday (CXS) job board scraper — targets one company's Workday-hosted
careers page at a time.

    POST https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    Body: {"appliedFacets": {}, "limit": 20, "offset": <n>, "searchText": ""}

This is the one genuinely undocumented endpoint in this package — Workday's
official API docs are gated behind a Workday Community login, and this is
an internal endpoint the public careers-site frontend calls, not a published
integration. That said, it's corroborated by several independent, mutually
consistent sources found while building this file (exact endpoint shape,
exact request body, exact response shape all agreed across sources
including working curl examples against named real tenants) — enough
agreement that this isn't a guess, but it's still not something this
sandbox could verify against a live tenant directly. If a live call 404s
or the shape has drifted for a particular tenant, that's the first thing
to check.

Per-company config needs three pieces (more than the other three
platforms, which just need one slug) because Workday's URL is genuinely
three-part: which data center (`wd_server`, e.g. "wd5"), which tenant
(`tenant`), and which named career site within that tenant (`site`) — all
three vary independently per company and none can be guessed from the
others. See config/company_sources.json.

Real server-side offset/limit pagination (the response's `total` field
gives an exact stopping point, same as SmartRecruiters). One further
documented quirk: `postedOn` is a relative string ("Posted Today",
"Posted 5 Days Ago") rather than a timestamp — this scraper parses it the
same way modules/gmail/linkedin_alert_parser.py parses LinkedIn's relative
dates, since it's the same kind of text.

The list endpoint returns only title/location/posted-date/path — full
descriptions require a second GET per posting
(`/wday/cxs/{tenant}/{site}/job/{externalPath}`), which this scraper does
not call, for the same reason as the other company scrapers in this
package: avoiding a second unverified endpoint in the same pass.

Workday is also documented to run aggressive bot detection (Akamai) on
some tenants — this scraper makes no attempt to work around that; it
relies entirely on the base framework's ordinary rate limiting and retry,
same as every other scraper in this project.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import PageResult
from modules.scraper.company.base import CompanyJobScraper

_RESULTS_PER_PAGE = 20  # Workday's own hard-coded page size, per multiple sources

_RELATIVE_TIME_PATTERN = re.compile(
    r"(\d+)\s*\+?\s*(hour|hr|day|week|month)s?\s*ago", re.IGNORECASE,
)


class WorkdayScraper(CompanyJobScraper):
    """Scraper for one company's Workday CXS job search API."""

    platform_name = "workday"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._current_roles: list[str] = []
        self._current_cities: list[str] = []

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> dict[str, Any]:
        """
        Fetch one page via Workday's CXS endpoint, which is POST-only —
        see BaseJobScraper._post().
        """
        self._current_roles = roles
        self._current_cities = cities

        tenant = self.company.get("tenant")
        wd_server = self.company.get("wd_server")
        site = self.company.get("site")
        missing = [k for k, v in (("tenant", tenant), ("wd_server", wd_server), ("site", site)) if not v]
        if missing:
            raise ScraperParseError(
                f"Company config for '{self.company_name}' is missing: {', '.join(missing)}",
                board=self.board_name,
            )

        offset = (page - 1) * _RESULTS_PER_PAGE
        url = f"https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        body = {"appliedFacets": {}, "limit": _RESULTS_PER_PAGE, "offset": offset, "searchText": ""}

        response = await self._post(url, json=body)
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

        job_postings = raw_page.get("jobPostings")
        if job_postings is None:
            raise ScraperParseError("Response missing 'jobPostings' key", board=self.board_name)
        if not isinstance(job_postings, list):
            raise ScraperParseError(
                f"'jobPostings' must be a list, got {type(job_postings).__name__}", board=self.board_name,
            )

        listings: list[JobListing] = []
        for posting in job_postings:
            if not isinstance(posting, dict):
                continue
            title = posting.get("title")
            location = self._extract_location(posting)
            if not title or not self._matches_filters(
                str(title), location, self._current_roles, self._current_cities
            ):
                continue
            parsed = self._parse_posting(posting)
            if parsed is not None:
                listings.append(parsed)

        total = raw_page.get("total", 0)
        offset = (page - 1) * _RESULTS_PER_PAGE
        returned = len(job_postings)
        has_next_page = (offset + returned) < total

        return PageResult(listings=listings, has_next_page=has_next_page)

    # ── Parsing helpers ──────────────────────────────────────────────────────

    def _parse_posting(self, posting: dict[str, Any]) -> JobListing | None:
        title = posting.get("title")
        external_path = posting.get("externalPath")

        if not title or not external_path:
            self.logger.warning(
                "Skipping unparseable posting — missing title/externalPath | board={board}",
                board=self.board_name,
            )
            return None

        location = self._extract_location(posting)
        url = self._build_url(str(external_path))
        posted_date = self._parse_relative_date(posting.get("postedOn"))

        return JobListing(
            id=f"{self.platform_name}-{self._slug(self.company_name)}-{self._slug(str(external_path))}",
            title=str(title),
            company=self.company_name,
            city=location,
            market=self._infer_market(location),
            url=url,
            source=ApplicationSource.CAREER_PAGE,
            description=None,  # see module docstring: list endpoint has no description field
            salary_range=None,  # not present in the CXS list response
            posted_date=posted_date,
        )

    def _build_url(self, external_path: str) -> str:
        tenant = self.company["tenant"]
        wd_server = self.company["wd_server"]
        site = self.company["site"]
        path = external_path if external_path.startswith("/") else f"/{external_path}"
        return f"https://{tenant}.{wd_server}.myworkdayjobs.com/en-US/{site}{path}"

    @staticmethod
    def _extract_location(posting: dict[str, Any]) -> str:
        locations_text = posting.get("locationsText")
        if locations_text:
            return str(locations_text)
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
    def _parse_relative_date(posted_on: Any) -> datetime | None:
        """
        Parse Workday's relative "Posted X Days Ago" / "Posted Today" text
        into an absolute datetime. Returns None if the text doesn't match
        a recognised pattern (e.g. "Posted 30+ Days Ago" is treated as 30).
        """
        if not posted_on:
            return None
        text = str(posted_on)

        if re.search(r"\btoday\b", text, re.IGNORECASE):
            return datetime.now(timezone.utc)

        match = _RELATIVE_TIME_PATTERN.search(text)
        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        unit_to_timedelta = {
            "hour": timedelta(hours=amount),
            "hr": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),  # approximation, documented above
        }
        delta = unit_to_timedelta.get(unit)
        if delta is None:
            return None
        return datetime.now(timezone.utc) - delta
