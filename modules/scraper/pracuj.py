"""
modules/scraper/pracuj.py
---------------------------
Pracuj.pl scraper — the second concrete implementation of BaseJobScraper.

Unlike NoFluffJobs, Pracuj.pl does not expose a simple public JSON search
API for this scraper to call directly — it's a server-rendered HTML site.
This scraper fetches the search-results HTML and parses it with
BeautifulSoup (already a project dependency: beautifulsoup4 + lxml).

    GET https://www.pracuj.pl/praca/{keyword};kw/{city};wp?pn={page}

The `;kw` / `;wp` URL segment suffixes and `pn=` pagination parameter are
Pracuj.pl's own URL scheme (keyword segment suffixed `;kw`, location
segment suffixed `;wp`, page number as `pn`) — e.g. a real Pracuj.pl-backed
listing was observed with `sourceListUrl` matching exactly this pattern:
`.../praca/python developer;kw/warszawa;wp?rd=30&pn=1`. This part of the
implementation is corroborated by that evidence, not just assumed.

╔══════════════════════════════════════════════════════════════════════════╗
║ SCHEMA CAVEAT — read before relying on this in production                ║
║                                                                          ║
║ This sandbox has no network access to pracuj.pl, so the exact live HTML  ║
║ could not be fetched and verified while writing this file. The URL      ║
║ pattern above is corroborated by external evidence (see above), but the ║
║ HTML selectors in _parse_offer_card() below (the `data-test="..."`      ║
║ attributes) are this scraper's best-effort match to Pracuj.pl's         ║
║ well-documented convention of tagging elements with `data-test`         ║
║ attributes for automated testing — not verified against a live page.    ║
║                                                                          ║
║ Parsing is written defensively — each field extractor tries the most    ║
║ likely selector with sensible fallbacks, and a genuinely unparseable    ║
║ card is skipped with a logged warning rather than crashing the whole    ║
║ page. tests/unit/test_pracuj.py encodes the assumed HTML shape          ║
║ explicitly as a fixture (PRACUJ_FIXTURE_HTML). Before production use:   ║
║ fetch one real search results page, diff its markup against that       ║
║ fixture, and adjust the CSS selectors in _parse_offer_card() — that's   ║
║ the only place HTML-shape knowledge lives.                              ║
╚══════════════════════════════════════════════════════════════════════════╝

Search strategy:
Like NoFluffJobs, Pracuj.pl's URL scheme takes one keyword segment and one
city segment — not an arbitrary combination of multiple roles/cities. This
scraper's `scrape(roles, cities)` uses the *first* role and *first* city
given; call `scrape()` once per (role, city) combination for full coverage.

Full job descriptions are not present on the search-results page (only
title/company/location/salary/a short tag list) — this scraper populates
`description` from whatever short tech/requirement tags the search card
shows, falling back to None. A complete description would require an
additional per-listing detail-page fetch, out of scope here for the same
reason noted in nofluffjobs.py: avoiding compounding unverified-endpoint
risk in a single pass.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup
from bs4.element import Tag
from slugify import slugify

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.base import BaseJobScraper, PageResult

# Pracuj.pl is a Poland-first board; every listing found here maps to this
# market unless/until multi-market parsing is added.
_DEFAULT_MARKET = Market.POLAND

# CSS selectors, isolated here so a live-schema adjustment (see caveat above)
# only ever touches this one place.
_OFFER_CARD_SELECTOR = '[data-test="default-offer"]'
_TITLE_SELECTOR = '[data-test="offer-title"]'
_COMPANY_SELECTOR = '[data-test="text-company-name"]'
_CITY_SELECTOR = '[data-test="text-region"]'
_SALARY_SELECTOR = '[data-test="offer-salary"]'
_LINK_SELECTOR = '[data-test="link-offer"]'
_TECH_TAG_SELECTOR = '[data-test="offer-technology-item"]'
_NEXT_PAGE_SELECTOR = '[data-test="bottom-pagination-button-next"]'


class PracujScraper(BaseJobScraper):
    """Scraper for pracuj.pl's server-rendered search results HTML."""

    board_name = "pracuj"

    def __init__(self, *args, base_url: str = "https://www.pracuj.pl", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = base_url.rstrip("/")

    # ── fetch(): I/O only ────────────────────────────────────────────────────

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> str:
        """
        Fetch one page of search results as raw HTML.

        Uses the first role and first city given — see module docstring
        for why Pracuj.pl's URL scheme doesn't support multiple independent
        role/city combinations in one search.
        """
        url = self._build_search_url(roles, cities, page)
        response = await self._get(url)
        return response.text

    # ── parse(): pure, no I/O ──────────────────────────────────────────────

    def parse_page(self, raw_page: str, page: int) -> PageResult:
        """
        Parse one page of Pracuj.pl search HTML into a PageResult.

        Deliberately synchronous and side-effect-free (besides logging a
        warning per skipped card) — see BaseJobScraper docstring for why
        that separation matters for testability.
        """
        if not isinstance(raw_page, str):
            raise ScraperParseError(
                f"Expected HTML text from the search page, got {type(raw_page).__name__}",
                board=self.board_name,
            )

        soup = BeautifulSoup(raw_page, "lxml")
        cards = soup.select(_OFFER_CARD_SELECTOR)

        listings: list[JobListing] = []
        for card in cards:
            job = self._parse_offer_card(card)
            if job is not None:
                listings.append(job)

        has_next_page = self._has_next_page(soup)

        return PageResult(listings=listings, has_next_page=has_next_page)

    # ── Parsing helpers (each isolated + independently testable) ───────────

    def _parse_offer_card(self, card: Tag) -> JobListing | None:
        """
        Parse a single offer card into a JobListing.

        Returns None (and logs a warning) rather than raising if a card is
        missing something un-defaultable — one bad card shouldn't sink the
        whole page.
        """
        title = self._text_of(card, _TITLE_SELECTOR)
        url = self._extract_url(card)

        if not title or not url:
            self.logger.warning(
                "Skipping unparseable offer card — missing title or url | board={board}",
                board=self.board_name,
            )
            return None

        company = self._text_of(card, _COMPANY_SELECTOR) or "Unknown"
        city = self._text_of(card, _CITY_SELECTOR) or "Unknown"
        salary = self._text_of(card, _SALARY_SELECTOR)
        description = self._extract_description(card)

        job_id = slugify(f"{company}-{title}-{city}", separator="-")

        return JobListing(
            id=job_id,
            title=title,
            company=company,
            city=city,
            market=_DEFAULT_MARKET,
            url=url,
            source=ApplicationSource.PRACUJ,
            description=description,
            salary_range=salary,
        )

    @staticmethod
    def _text_of(card: Tag, selector: str) -> str | None:
        """Get stripped text content of the first element matching selector, or None."""
        element = card.select_one(selector)
        if element is None:
            return None
        text = element.get_text(strip=True)
        return text or None

    def _extract_url(self, card: Tag) -> str | None:
        """Extract and absolutize the offer's URL, or None if not found."""
        link = card.select_one(_LINK_SELECTOR)
        if link is None or not link.has_attr("href"):
            return None

        href = link["href"]
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return f"{self.base_url}{href if href.startswith('/') else '/' + href}"

    @staticmethod
    def _extract_description(card: Tag) -> str | None:
        """
        Short summary from technology/requirement tag chips shown on the
        card, if present. Full JD text requires a separate detail-page
        fetch — see module docstring.
        """
        tags = card.select(_TECH_TAG_SELECTOR)
        texts = [t.get_text(strip=True) for t in tags if t.get_text(strip=True)]
        if not texts:
            return None
        return ", ".join(texts)

    @staticmethod
    def _has_next_page(soup: BeautifulSoup) -> bool:
        """True if an enabled 'next page' pagination control is present."""
        next_button = soup.select_one(_NEXT_PAGE_SELECTOR)
        if next_button is None:
            return False
        if next_button.has_attr("disabled"):
            return False
        aria_disabled = next_button.get("aria-disabled", "").lower()
        if aria_disabled == "true":
            return False
        return True

    @staticmethod
    def _build_search_url(roles: list[str], cities: list[str], page: int) -> str:
        """
        Build a Pracuj.pl search URL: /praca/{keyword};kw/{city};wp?pn={page}

        Uses the first role and first city given (see module docstring).
        """
        if not roles:
            raise ValueError("At least one role keyword is required to search Pracuj.pl")

        keyword_segment = quote(roles[0])
        url = f"https://www.pracuj.pl/praca/{keyword_segment};kw"

        if cities:
            city_segment = quote(cities[0])
            url += f"/{city_segment};wp"

        url += f"?pn={page}"
        return url
