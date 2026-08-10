"""
Unit tests for core.discovery — the DiscoveryProvider abstraction.

Organised to match the phases requested for this integration:
    TestKnownProviderKeys        -> registry / valid+unknown key validation
    TestBoardScraperProvider     -> wraps a fake BaseJobScraper
    TestCompanyATSProvider       -> wraps a fake CompanyJobScraper
    TestGmailAlertProvider       -> wraps Gmail via a FakeGmailService (no real Gmail account)
    TestBuildGmailProvider       -> disabled vs enabled-but-unconfigured vs enabled+configured
    TestGmailIntegrationInPipeline -> failure isolation + cross-provider dedup, through run_pipeline()

No test in this file makes a real network call, a real Gmail API call, or
depends on live external ATS sites.
"""

import base64
from typing import Any

import pytest
from rich.console import Console

from core.discovery import (
    COMPANY_ATS_SCRAPERS,
    GMAIL_PROVIDER_KEY,
    JOB_BOARD_SCRAPERS,
    BoardScraperProvider,
    CompanyATSProvider,
    GmailAlertProvider,
    build_gmail_provider,
    known_provider_keys,
)
from core.models import ApplicationSource, JobListing, Market
from core.pipeline import run_pipeline
from modules.gmail.client import GmailClient
from modules.scraper.base import BaseJobScraper, PageResult
from modules.scraper.company.base import CompanyJobScraper
from modules.tracker.tracker import ApplicationTracker


def make_job(job_id: str, title: str = "Test Engineer", company: str = "Acme", city: str = "Krakow") -> JobListing:
    return JobListing(
        id=job_id, title=title, company=company, city=city, market=Market.POLAND,
        url=f"https://example.com/{job_id}", source=ApplicationSource.NOFLUFFJOBS,
    )


# ── Registry / known provider keys ──────────────────────────────────────────

class TestKnownProviderKeys:

    def test_includes_all_job_boards(self):
        keys = known_provider_keys()
        assert set(JOB_BOARD_SCRAPERS) <= keys

    def test_includes_all_ats_platforms(self):
        keys = known_provider_keys()
        assert set(COMPANY_ATS_SCRAPERS) <= keys

    def test_includes_gmail_provider_key(self):
        assert GMAIL_PROVIDER_KEY in known_provider_keys()

    def test_unknown_key_not_included(self):
        assert "totally_made_up_source" not in known_provider_keys()

    def test_respects_overridden_registries(self):
        keys = known_provider_keys(
            job_board_scrapers={"onlyboard": object}, company_ats_scrapers={"onlyats": object},
        )
        assert keys == {"onlyboard", "onlyats", GMAIL_PROVIDER_KEY}


# ── BoardScraperProvider ─────────────────────────────────────────────────────

class FakeBoardScraper(BaseJobScraper):
    board_name = "fake_board"
    jobs: list[JobListing] = []

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        return self.jobs if page == 1 else []

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        return PageResult(listings=raw_page, has_next_page=False)


def bound_board_scraper(jobs: list[JobListing]) -> type[FakeBoardScraper]:
    return type("BoundFakeBoardScraper", (FakeBoardScraper,), {"jobs": jobs})


class TestBoardScraperProvider:

    @pytest.mark.asyncio
    async def test_discover_returns_scraper_results(self):
        jobs = [make_job("a"), make_job("b")]
        provider = BoardScraperProvider("fake_board", bound_board_scraper(jobs))

        result = await provider.discover(roles=[], cities=[])

        assert result == jobs

    def test_key_is_set(self):
        provider = BoardScraperProvider("nofluffjobs", bound_board_scraper([]))
        assert provider.key == "nofluffjobs"

    def test_is_available_by_default(self):
        provider = BoardScraperProvider("nofluffjobs", bound_board_scraper([]))
        assert provider.is_available() is True
        assert provider.unavailable_reason() is None


# ── CompanyATSProvider ───────────────────────────────────────────────────────

class FakeATSScraper(CompanyJobScraper):
    platform_name = "fake_platform"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_roles: list[str] = []
        self._current_cities: list[str] = []

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        self._current_roles = roles
        self._current_cities = cities
        return self.company.get("_test_jobs", []) if page == 1 else []

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        # Real platform modules (greenhouse.py etc.) apply
        # CompanyJobScraper._matches_filters locally in parse_page(), since
        # these APIs return a company's full job list with no server-side
        # search query — mirror that here so this fake behaves like the
        # real ones for the filtering test below.
        filtered = [
            job for job in raw_page
            if self._matches_filters(job.title, job.city, self._current_roles, self._current_cities)
        ]
        return PageResult(listings=filtered, has_next_page=False)


class TestCompanyATSProvider:

    @pytest.mark.asyncio
    async def test_discover_returns_scraper_results(self):
        jobs = [make_job("c1"), make_job("c2")]
        company = {"name": "TestCo", "_test_jobs": jobs}
        provider = CompanyATSProvider("greenhouse", FakeATSScraper, company)

        result = await provider.discover(roles=[], cities=[])

        assert result == jobs

    def test_key_includes_platform_and_company_label(self):
        provider = CompanyATSProvider("greenhouse.testco", FakeATSScraper, {"name": "TestCo"})
        assert provider.key == "greenhouse.testco"

    @pytest.mark.asyncio
    async def test_local_filtering_still_applies_via_wrapped_scraper(self):
        jobs = [
            make_job("c1", title="Senior Test Engineer"),
            make_job("c2", title="Marketing Manager"),
        ]
        company = {"name": "TestCo", "_test_jobs": jobs}
        provider = CompanyATSProvider("greenhouse.testco", FakeATSScraper, company)

        result = await provider.discover(roles=["test engineer"], cities=[])

        assert len(result) == 1
        assert result[0].id == "c1"


# ── GmailAlertProvider — via FakeGmailService, no real Gmail account ────────

def make_html_body_part(html: str) -> dict:
    encoded = base64.urlsafe_b64encode(html.encode("utf-8")).decode("ascii").rstrip("=")
    return {"payload": {"mimeType": "text/html", "body": {"data": encoded}}}


SAMPLE_ALERT_HTML = """
<html><body>
<table><tr><td>
  <a href="https://www.linkedin.com/comm/jobs/view/4111111111">Senior Test Engineer</a>
  <div>Bosch Group &middot; Wroclaw, Poland</div>
</td></tr></table>
</body></html>
"""


class _Execute:
    def __init__(self, response: dict):
        self._response = response

    def execute(self):
        return self._response


class FakeGmailService:
    """Minimal stand-in for a googleapiclient Gmail Resource — see
    tests/unit/test_linkedin_alert_parser.py for the full documented pattern
    this reuses."""

    def __init__(self, message_ids: list[str], metadata: dict[str, dict], bodies: dict[str, str]):
        self._message_ids = message_ids
        self._metadata = metadata
        self._bodies = bodies

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        return _Execute({"messages": [{"id": mid} for mid in self._message_ids]})

    def get(self, userId, id, format, metadataHeaders=None):
        if format == "metadata":
            meta = self._metadata[id]
            headers = [
                {"name": "From", "value": meta.get("from", "")},
                {"name": "Subject", "value": meta.get("subject", "")},
                {"name": "Date", "value": meta.get("date", "")},
            ]
            return _Execute({"payload": {"headers": headers}})
        return _Execute(make_html_body_part(self._bodies[id]))


def make_fake_gmail_client() -> GmailClient:
    service = FakeGmailService(
        message_ids=["m1"],
        metadata={"m1": {"from": "jobalerts-noreply@linkedin.com", "subject": "New jobs for you"}},
        bodies={"m1": SAMPLE_ALERT_HTML},
    )
    return GmailClient(service=service)


class TestGmailAlertProvider:

    @pytest.mark.asyncio
    async def test_discover_returns_parsed_linkedin_jobs(self):
        provider = GmailAlertProvider(gmail_client_factory=make_fake_gmail_client)

        results = await provider.discover(roles=[], cities=[])

        assert len(results) == 1
        job = results[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Bosch Group"
        assert job.source == ApplicationSource.LINKEDIN
        assert job.market == Market.POLAND

    @pytest.mark.asyncio
    async def test_discover_applies_local_role_filter(self):
        provider = GmailAlertProvider(gmail_client_factory=make_fake_gmail_client)

        results = await provider.discover(roles=["Marketing"], cities=[])

        assert results == []

    @pytest.mark.asyncio
    async def test_discover_applies_local_city_filter(self):
        provider = GmailAlertProvider(gmail_client_factory=make_fake_gmail_client)

        results = await provider.discover(roles=[], cities=["Wroclaw"])

        assert len(results) == 1

    def test_key_is_gmail_linkedin(self):
        assert GmailAlertProvider().key == GMAIL_PROVIDER_KEY

    def test_is_available_false_when_unconfigured(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings.gmail, "client_id", None)
        monkeypatch.setattr(settings.gmail, "client_secret", None)
        monkeypatch.setattr(settings.gmail, "refresh_token", None)

        provider = GmailAlertProvider()

        assert provider.is_available() is False
        assert "not configured" in provider.unavailable_reason()

    def test_is_available_true_when_configured(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings.gmail, "client_id", "id")
        monkeypatch.setattr(settings.gmail, "client_secret", "secret")
        monkeypatch.setattr(settings.gmail, "refresh_token", "token")

        provider = GmailAlertProvider()

        assert provider.is_available() is True
        assert provider.unavailable_reason() is None

    @pytest.mark.asyncio
    async def test_discover_raises_clearly_when_unavailable(self, monkeypatch):
        from config.settings import settings
        monkeypatch.setattr(settings.gmail, "client_id", None)
        monkeypatch.setattr(settings.gmail, "client_secret", None)
        monkeypatch.setattr(settings.gmail, "refresh_token", None)

        provider = GmailAlertProvider()

        with pytest.raises(RuntimeError, match="not configured"):
            await provider.discover(roles=[], cities=[])


# ── build_gmail_provider(): disabled / enabled key handling ─────────────────

class TestBuildGmailProvider:

    def test_returns_none_when_key_not_enabled(self):
        assert build_gmail_provider(["nofluffjobs", "pracuj"]) is None

    def test_returns_provider_when_key_enabled(self):
        provider = build_gmail_provider(["nofluffjobs", GMAIL_PROVIDER_KEY])
        assert isinstance(provider, GmailAlertProvider)

    def test_disabled_provider_never_instantiated(self):
        """A disabled provider must not execute — it's not even built."""
        assert build_gmail_provider([]) is None

    def test_custom_factory_is_threaded_through(self):
        sentinel = make_fake_gmail_client
        provider = build_gmail_provider([GMAIL_PROVIDER_KEY], gmail_client_factory=sentinel)
        assert provider._gmail_client_factory is sentinel


# ── Integration through run_pipeline(): failure isolation + cross-provider dedup ──

class TestGmailIntegrationInPipeline:

    @pytest.mark.asyncio
    async def test_gmail_failure_does_not_abort_other_sources(self, tmp_path):
        """If the Gmail provider raises, board scrapers still run and the
        failure is recorded, not fatal — same fail-soft contract as any
        other source."""
        board_jobs = [make_job("board-1")]
        job_board_scrapers = {"fake_board": bound_board_scraper(board_jobs)}

        def broken_client_factory():
            raise RuntimeError("simulated Gmail auth failure")

        gmail_provider = GmailAlertProvider(gmail_client_factory=broken_client_factory)

        tracker = ApplicationTracker(db_path=tmp_path / "db.json")
        try:
            result = await run_pipeline(
                roles=[], cities=[], enabled_scrapers=["fake_board", GMAIL_PROVIDER_KEY],
                job_board_scrapers=job_board_scrapers, company_ats_scrapers={}, company_sources={},
                gmail_provider=gmail_provider, tracker=tracker, console=Console(quiet=True),
                generate_messages=False,
            )
        finally:
            tracker.close()

        assert len(result.scraped) == 1
        assert result.scraped[0].id == "board-1"
        assert any(err.source == GMAIL_PROVIDER_KEY for err in result.errors)

    @pytest.mark.asyncio
    async def test_gmail_and_board_scraper_results_are_deduplicated_together(self, tmp_path):
        """Two providers returning the same job (same URL) -> only one
        reaches matching, no crash."""
        shared_job = make_job("shared", title="Senior Test Engineer")
        board_jobs = [shared_job]
        job_board_scrapers = {"fake_board": bound_board_scraper(board_jobs)}

        # Gmail provider returns a JobListing with the SAME url as the board
        # job — same duplicate-detection signal deduplicate_jobs() already
        # uses for cross-board duplicates.
        duplicate_from_gmail = JobListing(
            id="gmail-dup", title=shared_job.title, company=shared_job.company,
            city=shared_job.city, market=shared_job.market, url=shared_job.url,
            source=ApplicationSource.LINKEDIN,
        )

        class StaticGmailProvider(GmailAlertProvider):
            async def discover(self, roles, cities):
                return [duplicate_from_gmail]

        tracker = ApplicationTracker(db_path=tmp_path / "db.json")
        try:
            result = await run_pipeline(
                roles=[], cities=[], enabled_scrapers=["fake_board", GMAIL_PROVIDER_KEY],
                job_board_scrapers=job_board_scrapers, company_ats_scrapers={}, company_sources={},
                gmail_provider=StaticGmailProvider(), tracker=tracker, console=Console(quiet=True),
                generate_messages=False,
            )
        finally:
            tracker.close()

        assert len(result.scraped) == 2  # both providers' raw results present
        assert len(result.deduplicated) == 1  # but only one survives dedup
        assert result.duplicates_removed == 1
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_gmail_not_enabled_but_explicitly_injected_still_runs(self, tmp_path):
        """Passing gmail_provider explicitly is a test-injection seam
        (mirrors job_board_scrapers/company_ats_scrapers overrides) — it
        runs regardless of enabled_scrapers, same as those overrides do."""
        job_board_scrapers = {"fake_board": bound_board_scraper([make_job("a")])}
        gmail_provider = GmailAlertProvider(gmail_client_factory=make_fake_gmail_client)

        tracker = ApplicationTracker(db_path=tmp_path / "db.json")
        try:
            result = await run_pipeline(
                roles=[], cities=[], enabled_scrapers=["fake_board"],  # gmail_linkedin NOT enabled
                job_board_scrapers=job_board_scrapers, company_ats_scrapers={}, company_sources={},
                gmail_provider=gmail_provider, tracker=tracker, console=Console(quiet=True),
                generate_messages=False,
            )
        finally:
            tracker.close()

        assert len(result.scraped) == 2
