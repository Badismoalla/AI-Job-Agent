"""
Unit tests for modules.scraper.company.workday.WorkdayScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction, TestRelativeDateParsing,
   TestPaginationInference) — pure, no network. WORKDAY_FIXTURE_POSTING
   documents the assumed schema (see the schema caveat in workday.py — the
   one platform in this package without official public docs, though
   corroborated by multiple independent sources).

2. Integration test (TestFullScrapeViaMockTransport) — fetch_page +
   parse_page + real offset/limit/total pagination via httpx.MockTransport,
   confirming this scraper correctly uses POST (via BaseJobScraper._post())
   rather than GET.

No test in this file makes a real network call.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.company.workday import WorkdayScraper
from modules.scraper.rate_limiter import RateLimiter

WORKDAY_FIXTURE_POSTING: dict[str, Any] = {
    "title": "Senior Test Engineer",
    "externalPath": "/job/Wroclaw-Poland/Senior-Test-Engineer_R-12345",
    "locationsText": "Wroclaw, Poland",
    "postedOn": "Posted 5 Days Ago",
    "bulletFields": ["R-12345"],
}

COMPANY_CONFIG = {"name": "HP", "tenant": "hp", "wd_server": "wd5", "site": "ExternalCareerSite"}


def make_fixture_page(postings: list[dict], total: int | None = None) -> dict:
    return {"total": total if total is not None else len(postings), "jobPostings": postings}


@pytest.fixture
def scraper() -> WorkdayScraper:
    return WorkdayScraper(company=COMPANY_CONFIG, rate_limiter=RateLimiter(min_delay=0, max_delay=0))


def parse_with_filters(scraper, raw_page, page, roles, cities):
    scraper._current_roles = roles
    scraper._current_cities = cities
    return scraper.parse_page(raw_page, page=page)


# ── parse_page() ─────────────────────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_posting_into_joblisting(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING]), 1, [], [])
        assert len(result.listings) == 1
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "HP"
        assert job.city == "Wroclaw, Poland"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.CAREER_PAGE

    def test_url_built_from_tenant_server_site_and_external_path(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING]), 1, [], [])
        assert result.listings[0].url == (
            "https://hp.wd5.myworkdayjobs.com/en-US/ExternalCareerSite"
            "/job/Wroclaw-Poland/Senior-Test-Engineer_R-12345"
        )

    def test_non_dict_page_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="JSON object"):
            scraper.parse_page(["not", "a", "dict"], page=1)

    def test_missing_job_postings_key_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="jobPostings"):
            scraper.parse_page({"total": 0}, page=1)

    def test_non_list_job_postings_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="must be a list"):
            scraper.parse_page({"jobPostings": "not a list"}, page=1)

    def test_missing_title_or_path_skipped_not_fatal(self, scraper):
        bad = {"locationsText": "Warsaw"}
        good = WORKDAY_FIXTURE_POSTING
        result = parse_with_filters(scraper, make_fixture_page([bad, good]), 1, [], [])
        assert len(result.listings) == 1

    def test_role_filter_applied(self, scraper):
        other = dict(WORKDAY_FIXTURE_POSTING, title="Data Scientist", externalPath="/job/x/y-2")
        result = parse_with_filters(
            scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING, other]), 1,
            roles=["test engineer"], cities=[],
        )
        assert len(result.listings) == 1

    def test_city_filter_applied(self, scraper):
        other = dict(WORKDAY_FIXTURE_POSTING, locationsText="Berlin, Germany", externalPath="/job/x/y-2")
        result = parse_with_filters(
            scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING, other]), 1,
            roles=[], cities=["wroclaw"],
        )
        assert len(result.listings) == 1

    def test_every_returned_item_is_joblisting(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING]), 1, [], [])
        assert all(isinstance(j, JobListing) for j in result.listings)

    def test_description_and_salary_are_none(self, scraper):
        """Documented limitation: the list endpoint has no description field."""
        result = parse_with_filters(scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING]), 1, [], [])
        assert result.listings[0].description is None
        assert result.listings[0].salary_range is None


# ── Pagination via total/offset (exact, not heuristic) ─────────────────────

class TestPaginationInference:

    def test_has_next_page_true_when_more_remain(self, scraper):
        postings = [WORKDAY_FIXTURE_POSTING] * 20
        result = parse_with_filters(scraper, make_fixture_page(postings, total=45), 1, [], [])
        assert result.has_next_page is True

    def test_has_next_page_false_on_last_page(self, scraper):
        postings = [WORKDAY_FIXTURE_POSTING] * 5
        result = parse_with_filters(scraper, make_fixture_page(postings, total=45), 3, [], [])  # 2*20+5=45
        assert result.has_next_page is False

    def test_has_next_page_false_when_zero_results(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([], total=0), 1, [], [])
        assert result.has_next_page is False


# ── Field extraction ─────────────────────────────────────────────────────────

class TestFieldExtraction:

    def test_location_defaults_to_unknown(self, scraper):
        posting = {k: v for k, v in WORKDAY_FIXTURE_POSTING.items() if k != "locationsText"}
        result = parse_with_filters(scraper, make_fixture_page([posting]), 1, [], [])
        assert result.listings[0].city == "Unknown"

    def test_missing_company_config_raises_parse_error(self):
        scraper = WorkdayScraper(company={"name": "Incomplete", "tenant": "x"})  # missing wd_server, site
        with pytest.raises(ScraperParseError, match="wd_server"):
            import asyncio
            asyncio.run(scraper.fetch_page(page=1, roles=[], cities=[]))


# ── Relative date parsing ("Posted X Days Ago") ─────────────────────────────

class TestRelativeDateParsing:

    def test_posted_today(self):
        result = WorkdayScraper._parse_relative_date("Posted Today")
        assert abs((datetime.now(timezone.utc) - result)) < timedelta(seconds=5)

    def test_posted_n_days_ago(self):
        result = WorkdayScraper._parse_relative_date("Posted 5 Days Ago")
        expected = datetime.now(timezone.utc) - timedelta(days=5)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_posted_n_hours_ago(self):
        result = WorkdayScraper._parse_relative_date("Posted 3 Hours Ago")
        expected = datetime.now(timezone.utc) - timedelta(hours=3)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_posted_n_weeks_ago(self):
        result = WorkdayScraper._parse_relative_date("Posted 2 Weeks Ago")
        expected = datetime.now(timezone.utc) - timedelta(weeks=2)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_posted_30_plus_days_ago_treated_as_30(self):
        result = WorkdayScraper._parse_relative_date("Posted 30+ Days Ago")
        expected = datetime.now(timezone.utc) - timedelta(days=30)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_none_when_absent(self):
        assert WorkdayScraper._parse_relative_date(None) is None
        assert WorkdayScraper._parse_relative_date("") is None

    def test_none_when_unrecognised(self):
        assert WorkdayScraper._parse_relative_date("Recently posted") is None

    def test_posting_posted_date_wired_through_parse_page(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([WORKDAY_FIXTURE_POSTING]), 1, [], [])
        expected = datetime.now(timezone.utc) - timedelta(days=5)
        assert abs(result.listings[0].posted_date - expected) < timedelta(seconds=5)


# ── Full scrape() via MockTransport (POST-based, real offset/limit pagination) ──

class TestFullScrapeViaMockTransport:

    @pytest.mark.asyncio
    async def test_uses_post_not_get(self):
        methods_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods_seen.append(request.method)
            return httpx.Response(200, json=make_fixture_page([WORKDAY_FIXTURE_POSTING]))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with WorkdayScraper(
            company=COMPANY_CONFIG, client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            await scraper.scrape(roles=[], cities=[])

        assert methods_seen == ["POST"]

    @pytest.mark.asyncio
    async def test_request_body_shape(self):
        bodies_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json as json_module
            bodies_seen.append(json_module.loads(request.content))
            return httpx.Response(200, json=make_fixture_page([WORKDAY_FIXTURE_POSTING]))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with WorkdayScraper(
            company=COMPANY_CONFIG, client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            await scraper.scrape(roles=[], cities=[])

        assert bodies_seen[0] == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

    @pytest.mark.asyncio
    async def test_paginates_using_offset_and_total(self):
        offsets_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json as json_module
            body = json_module.loads(request.content)
            offset = body["offset"]
            offsets_seen.append(offset)
            if offset == 0:
                postings = [dict(WORKDAY_FIXTURE_POSTING, externalPath=f"/job/x/y-{i}") for i in range(20)]
            else:
                postings = [dict(WORKDAY_FIXTURE_POSTING, externalPath=f"/job/x/y-{i}") for i in range(20, 25)]
            return httpx.Response(200, json=make_fixture_page(postings, total=25))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with WorkdayScraper(
            company=COMPANY_CONFIG, client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            results = await scraper.scrape(roles=[], cities=[])

        assert len(results) == 25
        assert offsets_seen == [0, 20]

    @pytest.mark.asyncio
    async def test_missing_job_postings_key_raises_parse_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"total": 0})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with WorkdayScraper(
            company=COMPANY_CONFIG, client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            with pytest.raises(ScraperParseError, match="jobPostings"):
                await scraper.scrape(roles=[], cities=[])
