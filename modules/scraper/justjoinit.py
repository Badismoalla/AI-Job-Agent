"""
modules/scraper/justjoinit.py
--------------------------------
JustJoin.it scraper — the third concrete implementation of BaseJobScraper.

╔══════════════════════════════════════════════════════════════════════════╗
║ SCHEMA CAVEAT — read before relying on this in production, MORE           ║
║ IMPORTANT than for the other two scrapers                               ║
║                                                                          ║
║ This sandbox has no network access to justjoin.it. Web research done    ║
║ while writing this file surfaced a specific, important risk: JustJoin.it║
║ is reportedly now built on Next.js App Router, with listing data         ║
║ embedded in React Server Component ("RSC") flight payloads inside        ║
║ <script> tags on server-rendered pages — not a clean REST endpoint.      ║
║ RSC payloads are a materially harder parsing target (nested array       ║
║ references, not plain JSON) and are not something this implementation   ║
║ attempts, since guessing at that format without any way to verify it     ║
║ would be more likely wrong than right.                                  ║
║                                                                          ║
║ Instead, this scraper targets JustJoin.it's older, previously public,   ║
║ well-documented bulk JSON endpoint:                                     ║
║                                                                          ║
║     GET https://justjoin.it/api/offers                                  ║
║                                                                          ║
║ ...which historically returned the *entire* current offer list as one   ║
║ flat JSON array (no server-side keyword/city filtering or pagination —  ║
║ the old frontend did both client-side over the full dataset). This      ║
║ scraper replicates that: fetch once, then filter + paginate locally.    ║
║                                                                          ║
║ WHETHER /api/offers STILL WORKS ON THE LIVE SITE IS UNVERIFIED. If the   ║
║ Next.js migration removed it, this scraper will fail loudly with a      ║
║ ScraperParseError (non-list JSON / 404) rather than silently returning  ║
║ nothing — check that error message first. If it's gone, scraping        ║
║ JustJoin.it would need an RSC-payload parser instead, which is a        ║
║ materially different, larger piece of work than this file, and better   ║
║ scoped as its own follow-up once the live payload can actually be       ║
║ inspected. tests/unit/test_justjoinit.py encodes the assumed shape      ║
║ explicitly as JUSTJOINIT_FIXTURE_OFFER.                                 ║
╚══════════════════════════════════════════════════════════════════════════╝

Search strategy:
Because the source endpoint has no server-side filtering, this scraper
fetches the full offer list once (cached on the instance) and filters by
role keyword (substring match against title) and city (case-insensitive
match against city) locally, then paginates the *filtered* results —
unlike NoFluffJobs/Pracuj.pl, roles/cities aren't limited to "first only":
every role and every city given are matched (OR within each list, AND
between the two lists), since filtering happens in Python, not in a
single-phrase site search box.

Full job descriptions are not present in the bulk offers list (only
title/company/city/salary/skills tags) — `description` is populated from
the skills list, falling back to None. See nofluffjobs.py/pracuj.py for
why a full per-listing detail fetch is out of scope here.
"""

from __future__ import annotations

from typing import Any

from slugify import slugify

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import BaseJobScraper, PageResult

# JustJoin.it is a Poland/CEE-first board; every listing found here maps to
# this market unless/until multi-market parsing is added.
_DEFAULT_MARKET = Market.POLAND

# Since the source API returns everything in one shot, this scraper paginates
# the filtered result set locally using this page size.
_RESULTS_PER_PAGE = 20


class JustJoinITScraper(BaseJobScraper):
    """Scraper for justjoin.it's (historically public) bulk offers JSON endpoint."""

    board_name = "justjoinit"

    def __init__(self, *args, base_url: str = "https://justjoin.it", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = base_url.rstrip("/")
        self._cached_offers: list[dict[str, Any]] | None = None
        self._current_roles: list[str] = []
        self._current_cities: list[str] = []

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> list[dict[str, Any]]:
        """
        Fetch the full offer list, once, caching it on the instance.

        The source endpoint has no server-side pagination or filtering, so
        every page after the first reuses the cached response — filtering
        and paging both happen in parse_page(), not here. roles/cities are
        cached too: parse_page() needs them for filtering but only
        fetch_page() receives them per the BaseJobScraper contract.
        """
        self._current_roles = roles
        self._current_cities = cities

        if self._cached_offers is None:
            url = f"{self.base_url}/api/offers"
            response = await self._get(url)
            data = response.json()
            if not isinstance(data, list):
                raise ScraperParseError(
                    f"Expected a JSON array from {url}, got {type(data).__name__}. "
                    f"The endpoint may have been removed — see schema caveat in "
                    f"modules/scraper/justjoinit.py.",
                    board=self.board_name,
                )
            self._cached_offers = data

        return self._cached_offers

    # ── parse(): pure, no I/O ──────────────────────────────────────────────

    def parse_page(self, raw_page: list[dict[str, Any]], page: int) -> PageResult:
        """
        Filter the full offer list by role/city (cached from the most
        recent fetch_page() call), then return the requested page of the
        filtered results.

        Deliberately synchronous and side-effect-free (besides logging a
        warning per skipped offer) — see BaseJobScraper docstring.
        """
        if not isinstance(raw_page, list):
            raise ScraperParseError(
                f"Expected a list of offers, got {type(raw_page).__name__}",
                board=self.board_name,
            )

        matching = [
            o for o in raw_page
            if isinstance(o, dict) and self._matches_filters(o, self._current_roles, self._current_cities)
        ]

        listings: list[JobListing] = []
        for offer in matching:
            job = self._parse_offer(offer)
            if job is not None:
                listings.append(job)

        start = (page - 1) * _RESULTS_PER_PAGE
        end = start + _RESULTS_PER_PAGE
        page_slice = listings[start:end]
        has_next_page = end < len(listings)

        return PageResult(listings=page_slice, has_next_page=has_next_page)

    # ── Filtering (applied before pagination, see parse_page) ──────────────

    @staticmethod
    def _matches_filters(offer: dict[str, Any], roles: list[str], cities: list[str]) -> bool:
        """True if this offer's title matches any given role AND its city
        matches any given city. Empty filter lists match everything."""
        if roles:
            title = str(offer.get("title", "")).lower()
            if not any(role.lower() in title for role in roles):
                return False
        if cities:
            city = str(offer.get("city", "")).lower()
            if not any(c.lower() in city for c in cities):
                return False
        return True

    # ── Parsing helpers (each isolated + independently testable) ───────────

    def _parse_offer(self, offer: dict[str, Any]) -> JobListing | None:
        """
        Parse a single offer dict into a JobListing.

        Returns None (and logs a warning) rather than raising if an offer
        is missing something un-defaultable — one bad offer shouldn't sink
        the whole page.
        """
        title = offer.get("title")
        offer_id = offer.get("id")

        if not title or not offer_id:
            self.logger.warning(
                "Skipping unparseable offer — missing title or id | board={board}",
                board=self.board_name,
            )
            return None

        company = str(offer.get("company_name") or "Unknown")
        city = str(offer.get("city") or ("Remote" if offer.get("remote") else "Unknown"))
        salary = self._extract_salary(offer)
        description = self._extract_description(offer)

        job_id = slugify(f"{company}-{title}-{city}", separator="-")

        return JobListing(
            id=job_id,
            title=str(title),
            company=company,
            city=city,
            market=_DEFAULT_MARKET,
            url=f"{self.base_url}/job-offer/{offer_id}",
            source=ApplicationSource.JUSTJOINIT,
            description=description,
            salary_range=salary,
        )

    @staticmethod
    def _extract_salary(offer: dict[str, Any]) -> str | None:
        """Build a salary range string from the first employment_types entry."""
        employment_types = offer.get("employment_types")
        if not isinstance(employment_types, list) or not employment_types:
            return None

        first = employment_types[0]
        if not isinstance(first, dict):
            return None

        salary = first.get("salary")
        if not isinstance(salary, dict):
            return None

        low = salary.get("from")
        high = salary.get("to")
        currency = salary.get("currency", "")
        contract_type = first.get("type", "")

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
    def _extract_description(offer: dict[str, Any]) -> str | None:
        """
        Short summary from the skills list, if present, since the bulk
        offers endpoint doesn't include a full description field.
        """
        for key in ("description", "body"):
            value = offer.get(key)
            if value:
                return str(value)

        skills = offer.get("skills")
        if isinstance(skills, list) and skills:
            names = []
            for skill in skills:
                if isinstance(skill, dict) and skill.get("name"):
                    names.append(str(skill["name"]))
                elif isinstance(skill, str):
                    names.append(skill)
            if names:
                return ", ".join(names)

        return None
