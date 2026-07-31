"""
modules/scraper/company/base.py
----------------------------------
Shared base for every company-careers-page scraper (Greenhouse, Lever,
SmartRecruiters, Workday).

Why a second base class instead of using BaseJobScraper directly?
Each ATS's public API exposes "all of company X's open roles" as its
natural unit — not a keyword+city search across many employers, like the
job-board scrapers (NoFluffJobs, Pracuj.pl, JustJoin.it) do. So:

- One CompanyJobScraper instance = one company on one ATS platform.
- roles/cities passed to scrape() are applied as LOCAL filters over that
  company's job list (these APIs don't take a free-text search query) —
  the same pattern JustJoinITScraper already established for a
  bulk-fetch-then-filter-locally source.
- Every concrete platform module (greenhouse.py, lever.py, etc.) only adds
  fetch_page()/parse_page(), exactly like the job-board scrapers — this
  class adds nothing to that contract, just company-specific plumbing
  (config lookup, local filtering) shared across all four platforms.

config/company_sources.json holds the list of companies to scrape per
platform. Each platform's config shape is necessarily different (a
Greenhouse company needs a board_token; a Workday company needs a tenant,
data-center, and site path) — see that file for the schema per platform.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.exceptions import ScraperError
from modules.scraper.base import BaseJobScraper

_DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[3] / "config" / "company_sources.json"

# Recognised platform keys in config/company_sources.json — used to validate
# the file's shape without hardcoding per-platform field requirements here
# (those are validated by each platform module against its own entries).
_KNOWN_PLATFORMS = {"greenhouse", "lever", "smartrecruiters", "workday"}


def load_company_sources(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """
    Load config/company_sources.json.

    Args:
        path: Optional override path (tests use a fixture file).

    Returns:
        Dict mapping platform name -> list of company config dicts.

    Raises:
        ScraperError: if the file is missing, isn't valid JSON, or isn't
        shaped as {platform_name: [ {..company config..}, ... ], ...}.
    """
    sources_path = path or _DEFAULT_SOURCES_PATH

    if not sources_path.exists():
        raise ScraperError(f"Company sources file not found: {sources_path}", board="company")

    try:
        raw_text = sources_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ScraperError(
            f"Could not read company sources file: {sources_path} ({e})", board="company",
        ) from e

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ScraperError(
            f"Company sources file is not valid JSON: {sources_path} ({e})", board="company",
        ) from e

    if not isinstance(data, dict):
        raise ScraperError(
            f"Company sources file must be a JSON object at the top level: {sources_path}",
            board="company",
        )

    result: dict[str, list[dict[str, Any]]] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue  # e.g. "_note" — documentation keys, not platform data
        if not isinstance(value, list):
            raise ScraperError(
                f"Company sources entry '{key}' must be a list, got {type(value).__name__}",
                board="company",
            )
        result[key] = value

    return result


class CompanyJobScraper(BaseJobScraper):
    """
    Base for "one company's ATS-hosted careers page" scrapers.

    Subclasses set `platform_name` (e.g. "greenhouse") and implement
    fetch_page()/parse_page() exactly as any BaseJobScraper subclass would.
    This class adds:

    - Construction from a company config dict (from company_sources.json),
      with a `for_company()` convenience lookup by name.
    - `_matches_filters()`: the shared local role/city filter every
      platform module uses in parse_page(), since none of these APIs take
      a search query — they return one company's full job list.
    - A board_name that includes the company (e.g. "greenhouse.stripe"),
      so logs distinguish companies when many scrapers run in sequence.
    """

    platform_name: str = "unknown"

    def __init__(self, company: dict[str, Any], *args, **kwargs) -> None:
        """
        Args:
            company: One company's config dict, e.g. {"name": "Stripe",
                "board_token": "stripe"} for Greenhouse. Shape varies by
                platform — see config/company_sources.json.
        """
        self.company = company
        self.company_name = str(company.get("name", "Unknown Company"))
        # Set before super().__init__() so its self.logger binding picks up
        # the company-specific board_name rather than the class default.
        self.board_name = f"{self.platform_name}.{self._slug(self.company_name)}"

        super().__init__(*args, **kwargs)

    @classmethod
    def for_company(cls, company_name: str, sources_path: Path | None = None) -> "CompanyJobScraper":
        """
        Look up a company's config for this platform by name in
        config/company_sources.json and construct a scraper for it.

        Raises:
            ScraperError: if no config entry matches company_name (case
            insensitive) under this platform.
        """
        sources = load_company_sources(sources_path)
        candidates = sources.get(cls.platform_name, [])

        for entry in candidates:
            if str(entry.get("name", "")).strip().lower() == company_name.strip().lower():
                return cls(company=entry)

        raise ScraperError(
            f"No {cls.platform_name} config found for company '{company_name}' "
            f"in config/company_sources.json",
            board=cls.platform_name,
        )

    @staticmethod
    def _matches_filters(title: str, location: str, roles: list[str], cities: list[str]) -> bool:
        """
        Shared local filter: title must contain at least one role keyword
        (if any given) AND location must contain at least one city (if any
        given). Empty filter lists match everything — these APIs return a
        company's full job list, so an empty roles/cities means "give me
        all of it", same semantics as JustJoinITScraper.
        """
        if roles:
            title_lower = title.lower()
            if not any(role.lower() in title_lower for role in roles):
                return False
        if cities:
            location_lower = location.lower()
            if not any(city.lower() in location_lower for city in cities):
                return False
        return True

    @staticmethod
    def _slug(text: str) -> str:
        """Lowercase, hyphenated version of text for use in board_name/logs."""
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug or "unknown"
