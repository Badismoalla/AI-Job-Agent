"""
Unit tests for modules.scraper.company.greenhouse.GreenhouseScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction) — pure, no network.
   GREENHOUSE_FIXTURE_JOB documents the assumed schema (see the schema
   caveat in greenhouse.py — this one has the LEAST risk of the four ATS
   scrapers, since it matches Greenhouse's own public documentation).

2. Integration test (TestFullScrapeViaMockTransport) — fetch_page +
   parse_page + local pagination + dedup together via httpx.MockTransport.

No test in this file makes a real network call.
"""

from typing import Any

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.company.greenhouse import GreenhouseScraper
from modules.scraper.rate_limiter import RateLimiter

GREENHOUSE_FIXTURE_JOB: dict[str, Any] = {
    "id": 4567890,
    "title": "Senior Test Engineer",
    "location": {"name": "Wroclaw, Poland"},
    "absolute_url": "https://boards.greenhouse.io/stripe/jobs/4567890",
    "content": "&lt;p&gt;We are looking for a &lt;b&gt;Senior Test Engineer&lt;/b&gt;.&lt;/p&gt;",
    "updated_at": "2026-01-15T10:30:00-05:00",
}


def make_fixture_response(jobs: list[dict]) -> dict:
    return {"jobs": jobs}


@pytest.fixture
def scraper() -> GreenhouseScraper:
    return GreenhouseScraper(
        company={"name": "Stripe", "board_token": "stripe"},
        rate_limiter=RateLimiter(min_delay=0, max_delay=0),
    )


def parse_with_filters(scraper, raw_page, page, roles, cities):
    scraper._current_roles = roles
    scraper._current_cities = cities
    return scraper.parse_page(raw_page, page=page)


# ── parse_page() ─────────────────────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_job_into_joblisting(self, scraper):
        result = parse_with_filters(scraper, [GREENHOUSE_FIXTURE_JOB], 1, [], [])
        assert len(result.listings) == 1
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Stripe"
        assert job.city == "Wroclaw, Poland"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.CAREER_PAGE
        assert job.url == "https://boards.greenhouse.io/stripe/jobs/4567890"

    def test_non_list_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="list of jobs"):
            scraper.parse_page({"not": "a list"}, page=1)

    def test_missing_title_or_url_skipped_not_fatal(self, scraper):
        bad = {"id": 1}
        good = GREENHOUSE_FIXTURE_JOB
        result = parse_with_filters(scraper, [bad, good], 1, [], [])
        assert len(result.listings) == 1

    def test_role_filter_applied(self, scraper):
        other_job = dict(GREENHOUSE_FIXTURE_JOB, id=999, title="Data Scientist")
        result = parse_with_filters(
            scraper, [GREENHOUSE_FIXTURE_JOB, other_job], 1,
            roles=["test engineer"], cities=[],
        )
        assert len(result.listings) == 1
        assert result.listings[0].title == "Senior Test Engineer"

    def test_city_filter_applied(self, scraper):
        other_job = dict(GREENHOUSE_FIXTURE_JOB, id=999, location={"name": "Berlin, Germany"})
        result = parse_with_filters(
            scraper, [GREENHOUSE_FIXTURE_JOB, other_job], 1,
            roles=[], cities=["wroclaw"],
        )
        assert len(result.listings) == 1

    def test_every_returned_item_is_joblisting(self, scraper):
        result = parse_with_filters(scraper, [GREENHOUSE_FIXTURE_JOB], 1, [], [])
        assert all(isinstance(j, JobListing) for j in result.listings)


# ── Local pagination (fetch-all-then-slice, same strategy as JustJoinIT) ────

class TestLocalPagination:

    def test_slices_into_pages(self, scraper):
        jobs = [dict(GREENHOUSE_FIXTURE_JOB, id=i, title=f"Test Engineer {i}") for i in range(25)]
        page1 = parse_with_filters(scraper, jobs, 1, [], [])
        page2 = parse_with_filters(scraper, jobs, 2, [], [])
        assert len(page1.listings) == 20
        assert len(page2.listings) == 5
        assert page1.has_next_page is True
        assert page2.has_next_page is False


# ── Field extraction ─────────────────────────────────────────────────────────

class TestFieldExtraction:

    def test_description_unescaped_and_stripped_of_tags(self, scraper):
        result = parse_with_filters(scraper, [GREENHOUSE_FIXTURE_JOB], 1, [], [])
        assert result.listings[0].description == "We are looking for a\nSenior Test Engineer\n."

    def test_description_none_when_absent(self, scraper):
        job = {k: v for k, v in GREENHOUSE_FIXTURE_JOB.items() if k != "content"}
        result = parse_with_filters(scraper, [job], 1, [], [])
        assert result.listings[0].description is None

    def test_location_defaults_to_unknown(self, scraper):
        job = {k: v for k, v in GREENHOUSE_FIXTURE_JOB.items() if k != "location"}
        result = parse_with_filters(scraper, [job], 1, [], [])
        assert result.listings[0].city == "Unknown"

    def test_posted_date_parsed_from_iso8601(self, scraper):
        result = parse_with_filters(scraper, [GREENHOUSE_FIXTURE_JOB], 1, [], [])
        assert result.listings[0].posted_date.year == 2026
        assert result.listings[0].posted_date.month == 1

    def test_posted_date_none_when_absent_or_invalid(self, scraper):
        job = dict(GREENHOUSE_FIXTURE_JOB, updated_at="not-a-date")
        result = parse_with_filters(scraper, [job], 1, [], [])
        assert result.listings[0].posted_date is None

    def test_salary_extracted_from_metadata_when_present(self, scraper):
        job = dict(GREENHOUSE_FIXTURE_JOB, metadata=[{"name": "Salary Range", "value": "$120k-$150k"}])
        result = parse_with_filters(scraper, [job], 1, [], [])
        assert result.listings[0].salary_range == "$120k-$150k"

    def test_salary_none_when_no_metadata(self, scraper):
        result = parse_with_filters(scraper, [GREENHOUSE_FIXTURE_JOB], 1, [], [])
        assert result.listings[0].salary_range is None

    def test_missing_board_token_raises_parse_error(self):
        scraper = GreenhouseScraper(company={"name": "NoToken"})
        with pytest.raises(ScraperParseError, match="board_token"):
            import asyncio
            asyncio.run(scraper.fetch_page(page=1, roles=[], cities=[]))


# ── Full scrape() via MockTransport ─────────────────────────────────────────

class TestFullScrapeViaMockTransport:

    @pytest.mark.asyncio
    async def test_single_call_fetches_and_caches(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            assert "content=true" in str(request.url)
            jobs = [dict(GREENHOUSE_FIXTURE_JOB, id=i, title=f"Test Engineer {i}") for i in range(25)]
            return httpx.Response(200, json=make_fixture_response(jobs))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with GreenhouseScraper(
            company={"name": "Stripe", "board_token": "stripe"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            results = await scraper.scrape(roles=[], cities=[])

        assert len(results) == 25
        assert calls["n"] == 1  # fetched once, paginated locally

    @pytest.mark.asyncio
    async def test_dedup_removes_repeated_ids(self):
        def handler(request: httpx.Request) -> httpx.Response:
            jobs = [GREENHOUSE_FIXTURE_JOB, GREENHOUSE_FIXTURE_JOB]  # exact duplicate
            return httpx.Response(200, json=make_fixture_response(jobs))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with GreenhouseScraper(
            company={"name": "Stripe", "board_token": "stripe"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            results = await scraper.scrape(roles=[], cities=[])

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_missing_jobs_key_raises_parse_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with GreenhouseScraper(
            company={"name": "Stripe", "board_token": "stripe"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            with pytest.raises(ScraperParseError, match="jobs"):
                await scraper.scrape(roles=[], cities=[])
