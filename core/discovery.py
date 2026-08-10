"""
core/discovery.py
------------------
A minimal DiscoveryProvider abstraction that lets core/pipeline.py consume
jobs from sources that don't share a common execution shape — job-board/
company-ATS scrapers (async, paginated, BaseJobScraper-based) and Gmail/
LinkedIn alert ingestion (sync Gmail API calls + a pure parse step,
explicitly NOT a BaseJobScraper per modules/gmail/linkedin_alert_parser.py's
own docstring) — behind one interface:

    async def discover(self, roles: list[str], cities: list[str]) -> list[JobListing]

Why this exists rather than forcing Gmail into BaseJobScraper:
BaseJobScraper's fetch_page/parse_page split exists specifically for
paginated, retry-able HTTP scraping (see its own docstring). Gmail
ingestion is neither paginated nor a single HTTP resource — it's a Gmail
search + N message fetches + a parse step. Bending that into
fetch_page/parse_page would be the "unnecessary abstraction" this task
explicitly warns against. A thin, separate provider interface that both
shapes can satisfy is smaller and clearer than forcing one contract onto
both.

Why NOT route the existing job-board/ATS scraping through this new layer:
core/pipeline.py's `_build_scraper_instances` / `_run_one_scraper` /
`_scrape_all` already implement exactly this (build instances, run
concurrently, catch-and-record failures, keep going) for BaseJobScraper
objects, and are directly covered by tests/unit/test_pipeline.py. Rewriting
that path to go through DiscoveryProvider would be pure risk for zero
behavioural gain — "no large rewrite" per the brief. BoardScraperProvider
and CompanyATSProvider are still provided below (thin wrappers around the
existing scraper classes) so the *abstraction* is complete and uniform for
any future caller that wants a flat list of providers (e.g. introspection,
a future registry-driven CLI command) — but core/pipeline.py's own
execution of job-board/ATS sources continues to use the existing,
already-tested path unchanged. Only Gmail (a genuinely new integration)
is wired through DiscoveryProvider.discover() in the pipeline itself.

Registries:
- JOB_BOARD_SCRAPERS / COMPANY_ATS_SCRAPERS: moved here from
  core/pipeline.py (which re-exports them for backward compatibility —
  `from core.pipeline import JOB_BOARD_SCRAPERS` still works everywhere it
  already did). This satisfies "don't hardcode the provider list directly
  inside pipeline.py" for the registries themselves.
- GMAIL_PROVIDER_KEY: the enabled_scrapers key that turns on Gmail/
  LinkedIn-alert discovery ("gmail_linkedin"). Recognised alongside the
  two existing registries wherever enabled_scrapers keys are validated.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable

from config.settings import settings
from core.logger import get_logger
from core.models import JobListing
from modules.gmail.client import GmailClient
from modules.gmail.linkedin_alert_parser import fetch_linkedin_alert_emails, parse_alert_email
from modules.scraper.base import BaseJobScraper
from modules.scraper.company.base import CompanyJobScraper
from modules.scraper.company.greenhouse import GreenhouseScraper
from modules.scraper.company.lever import LeverScraper
from modules.scraper.company.smartrecruiters import SmartRecruitersScraper
from modules.scraper.company.workday import WorkdayScraper
from modules.scraper.justjoinit import JustJoinITScraper
from modules.scraper.nofluffjobs import NoFluffJobsScraper
from modules.scraper.pracuj import PracujScraper

logger = get_logger(__name__)

# ── Registries — the single source of truth for known provider keys.
# core/pipeline.py imports and re-exports JOB_BOARD_SCRAPERS/COMPANY_ATS_SCRAPERS
# for backward compatibility with existing imports elsewhere in the project. ──

JOB_BOARD_SCRAPERS: dict[str, type[BaseJobScraper]] = {
    "nofluffjobs": NoFluffJobsScraper,
    "pracuj": PracujScraper,
    "justjoinit": JustJoinITScraper,
}

COMPANY_ATS_SCRAPERS: dict[str, type[CompanyJobScraper]] = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "workday": WorkdayScraper,
}

GMAIL_PROVIDER_KEY = "gmail_linkedin"


def known_provider_keys(
    job_board_scrapers: dict[str, type[BaseJobScraper]] | None = None,
    company_ats_scrapers: dict[str, type[CompanyJobScraper]] | None = None,
) -> set[str]:
    """
    Every key `enabled_scrapers` currently recognises: job boards, ATS
    platforms, and the Gmail provider. Used to validate configured keys
    clearly (see PipelineSettings.enabled_scrapers_list validation and
    render_pipeline_plan's "unknown scraper keys" reporting) — invalid
    keys are reported, not silently swallowed.
    """
    job_board_scrapers = job_board_scrapers if job_board_scrapers is not None else JOB_BOARD_SCRAPERS
    company_ats_scrapers = (
        company_ats_scrapers if company_ats_scrapers is not None else COMPANY_ATS_SCRAPERS
    )
    return set(job_board_scrapers) | set(company_ats_scrapers) | {GMAIL_PROVIDER_KEY}


# ── DiscoveryProvider abstraction ───────────────────────────────────────────

class DiscoveryProvider(ABC):
    """
    Base for anything that can produce `list[JobListing]` for the pipeline.

    `key` identifies the provider in logs, PipelineResult.errors, and the
    `plan` command's execution-plan table — same role `label` already
    plays for BaseJobScraper instances in core/pipeline.py.
    """

    key: str

    def is_available(self) -> bool:
        """
        True if this provider can run right now — e.g. required
        credentials/config are present. Default True: most providers
        (job boards, ATS) need no configuration beyond being enabled.
        Override for providers that do (Gmail).

        An unavailable provider is NOT the same as a disabled one: disabled
        providers are never even instantiated (see build_gmail_provider /
        _build_scraper_instances). An enabled-but-unavailable provider
        (e.g. "gmail_linkedin" enabled with no Gmail credentials
        configured) surfaces as a normal fail-soft ScraperRunError, the
        same as any other source failing, so misconfiguration is visible
        without crashing the pipeline — never silently skipped.
        """
        return True

    def unavailable_reason(self) -> str | None:
        """Human-readable reason `is_available()` is False, or None if available."""
        return None

    @abstractmethod
    async def discover(self, roles: list[str], cities: list[str]) -> list[JobListing]:
        """Return every JobListing this provider currently has for the given filters."""
        ...


class BoardScraperProvider(DiscoveryProvider):
    """Adapts an existing job-board BaseJobScraper (NoFluffJobs, Pracuj.pl,
    JustJoin.it) to the DiscoveryProvider interface. See module docstring
    for why core/pipeline.py doesn't currently route through this directly."""

    def __init__(self, key: str, scraper_cls: type[BaseJobScraper]) -> None:
        self.key = key
        self._scraper_cls = scraper_cls

    async def discover(self, roles: list[str], cities: list[str]) -> list[JobListing]:
        async with self._scraper_cls() as scraper:
            return await scraper.scrape(roles=roles, cities=cities)


class CompanyATSProvider(DiscoveryProvider):
    """Adapts an existing CompanyJobScraper (Greenhouse/Lever/SmartRecruiters/
    Workday) for ONE configured company to the DiscoveryProvider interface."""

    def __init__(self, key: str, scraper_cls: type[CompanyJobScraper], company: dict[str, Any]) -> None:
        self.key = key  # e.g. "greenhouse.stripe" — matches existing label convention
        self._scraper_cls = scraper_cls
        self._company = company

    async def discover(self, roles: list[str], cities: list[str]) -> list[JobListing]:
        async with self._scraper_cls(company=self._company) as scraper:
            return await scraper.scrape(roles=roles, cities=cities)


class GmailAlertProvider(DiscoveryProvider):
    """
    Wraps modules.gmail: fetch LinkedIn Job Alert emails, parse into
    JobListing. Never talks to linkedin.com directly — see the module
    docstring of modules/gmail/linkedin_alert_parser.py.

    Gracefully unavailable when Gmail OAuth2 credentials aren't
    configured (GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN)
    — is_available() reports this without raising, so `python main.py`
    commands never require Gmail credentials just to start.
    """

    key = GMAIL_PROVIDER_KEY

    def __init__(self, gmail_client_factory: Callable[[], GmailClient] | None = None) -> None:
        """
        Args:
            gmail_client_factory: Defaults to GmailClient.from_settings.
                Tests inject a factory that returns a GmailClient wrapping
                a FakeGmailService instead (same injection point
                GmailClient itself already documents) — when a custom
                factory is given, is_available() no longer checks global
                Gmail settings, since the injected factory may not need
                them at all (e.g. a fake service in tests). This mirrors
                GmailClient's own documented rule: "if a service/credentials
                object is injected directly, config-based credentials are
                never touched."
        """
        self._uses_default_factory = gmail_client_factory is None
        self._gmail_client_factory = gmail_client_factory or GmailClient.from_settings

    def is_available(self) -> bool:
        if not self._uses_default_factory:
            # A custom factory was injected — its own construction (called
            # lazily in discover()) is the real availability check; don't
            # gate on global settings that may not apply to it.
            return True
        gs = settings.gmail
        return bool(gs.client_id and gs.client_secret and gs.refresh_token)

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        return (
            "Gmail not configured — set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, "
            "and GMAIL_REFRESH_TOKEN to enable LinkedIn Job Alert discovery."
        )

    async def discover(self, roles: list[str], cities: list[str]) -> list[JobListing]:
        if not self.is_available():
            # Discovered at run time, not at build time — see is_available()
            # docstring for why this is a soft failure, not a silent skip.
            raise RuntimeError(self.unavailable_reason())

        client = self._gmail_client_factory()
        # GmailClient's methods are synchronous (googleapiclient) — run in a
        # thread so this doesn't block the event loop other providers share.
        html_bodies = await asyncio.to_thread(fetch_linkedin_alert_emails, client)

        listings: list[JobListing] = []
        for html in html_bodies:
            listings.extend(parse_alert_email(html))

        return _filter_local(listings, roles, cities)


def _filter_local(listings: list[JobListing], roles: list[str], cities: list[str]) -> list[JobListing]:
    """
    Local role/city filter for providers whose upstream source doesn't
    take a search query (Gmail alerts are whatever LinkedIn already
    matched for the configured saved search) — same semantics as
    CompanyJobScraper._matches_filters: title must contain a role keyword
    (if any given) AND city must contain a configured city (if any given).
    Empty filter lists match everything.
    """
    if not roles and not cities:
        return listings

    def matches(job: JobListing) -> bool:
        if roles and not any(role.lower() in job.title.lower() for role in roles):
            return False
        if cities and not any(city.lower() in job.city.lower() for city in cities):
            return False
        return True

    return [job for job in listings if matches(job)]


def build_gmail_provider(
    enabled: list[str],
    gmail_client_factory: Callable[[], GmailClient] | None = None,
) -> GmailAlertProvider | None:
    """
    Build a GmailAlertProvider if "gmail_linkedin" is in `enabled`, else
    None (disabled providers are never even instantiated — they cannot
    execute). Availability (credentials configured) is checked later, at
    discover() time, not here — see GmailAlertProvider.is_available()
    docstring for why that's a deliberate fail-soft boundary rather than a
    build-time skip.
    """
    if GMAIL_PROVIDER_KEY not in enabled:
        return None
    return GmailAlertProvider(gmail_client_factory=gmail_client_factory)
