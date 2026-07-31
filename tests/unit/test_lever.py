"""
Unit tests for modules.scraper.company.lever.LeverScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction, TestPagination) —
   pure, no network. LEVER_FIXTURE_POSTING documents the assumed schema
   (see the schema caveat in lever.py).

2. Integration test (TestFullScrapeViaMockTransport) — fetch_page +
   parse_page + real skip/limit pagination together via httpx.MockTransport.

No test in this file makes a real network call.
"""

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.company.lever import LeverScraper
from modules.scraper.rate_limiter import RateLimiter

LEVER_FIXTURE_POSTING: dict[str, Any] = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "text": "Senior Test Engineer",
    "categories": {"location": "Warszawa, Poland", "team": "Engineering", "commitment": "Full-time"},
    "hostedUrl": "https://jobs.lever.co/netflix/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "applyUrl": "https://jobs.lever.co/netflix/a1b2c3d4-e5f6-7890-abcd-ef1234567890/apply",
    "descriptionPlain": "We are looking for a Senior Test Engineer.",
    "createdAt": 1700000000000,
}


@pytest.fixture
def scraper() -> LeverScraper:
    return LeverScraper(
        company={"name": "Netflix", "site_name": "netflix"},
        rate_limiter=RateLimiter(min_delay=0, max_delay=0),
    )


def parse_with_filters(scraper, raw_page, page, roles, cities):
    scraper._current_roles = roles
    scraper._current_cities = cities
    return scraper.parse_page(raw_page, page=page)


# ── parse_page() ─────────────────────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_posting_into_joblisting(self, scraper):
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING], 1, [], [])
        assert len(result.listings) == 1
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Netflix"
        assert job.city == "Warszawa, Poland"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.CAREER_PAGE
        assert job.url == LEVER_FIXTURE_POSTING["hostedUrl"]

    def test_non_list_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="list of postings"):
            scraper.parse_page({"not": "a list"}, page=1)

    def test_missing_required_field_skipped_not_fatal(self, scraper):
        bad = {"id": "x"}  # missing text/url
        good = LEVER_FIXTURE_POSTING
        result = parse_with_filters(scraper, [bad, good], 1, [], [])
        assert len(result.listings) == 1

    def test_falls_back_to_apply_url_when_hosted_url_absent(self, scraper):
        posting = {k: v for k, v in LEVER_FIXTURE_POSTING.items() if k != "hostedUrl"}
        result = parse_with_filters(scraper, [posting], 1, [], [])
        assert result.listings[0].url == LEVER_FIXTURE_POSTING["applyUrl"]

    def test_role_filter_applied(self, scraper):
        other = dict(LEVER_FIXTURE_POSTING, id="other", text="Data Scientist")
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING, other], 1, ["test engineer"], [])
        assert len(result.listings) == 1

    def test_city_filter_applied(self, scraper):
        other = dict(LEVER_FIXTURE_POSTING, id="other", categories={"location": "Berlin, Germany"})
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING, other], 1, [], ["warszawa"])
        assert len(result.listings) == 1

    def test_every_returned_item_is_joblisting(self, scraper):
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING], 1, [], [])
        assert all(isinstance(j, JobListing) for j in result.listings)


# ── Pagination heuristic (full page => maybe more) ──────────────────────────

class TestPagination:

    def test_full_page_implies_has_next_page(self, scraper):
        postings = [dict(LEVER_FIXTURE_POSTING, id=str(i)) for i in range(25)]  # _RESULTS_PER_PAGE
        result = parse_with_filters(scraper, postings, 1, [], [])
        assert result.has_next_page is True

    def test_short_page_implies_no_next_page(self, scraper):
        postings = [dict(LEVER_FIXTURE_POSTING, id=str(i)) for i in range(5)]
        result = parse_with_filters(scraper, postings, 1, [], [])
        assert result.has_next_page is False

    def test_empty_page_has_no_next_page(self, scraper):
        result = parse_with_filters(scraper, [], 1, [], [])
        assert result.has_next_page is False


# ── Field extraction ─────────────────────────────────────────────────────────

class TestFieldExtraction:

    def test_location_defaults_to_unknown(self, scraper):
        posting = {k: v for k, v in LEVER_FIXTURE_POSTING.items() if k != "categories"}
        result = parse_with_filters(scraper, [posting], 1, [], [])
        assert result.listings[0].city == "Unknown"

    def test_description_from_description_plain(self, scraper):
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING], 1, [], [])
        assert result.listings[0].description == "We are looking for a Senior Test Engineer."

    def test_description_none_when_absent(self, scraper):
        posting = {k: v for k, v in LEVER_FIXTURE_POSTING.items() if k != "descriptionPlain"}
        result = parse_with_filters(scraper, [posting], 1, [], [])
        assert result.listings[0].description is None

    def test_posted_date_from_epoch_millis(self, scraper):
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING], 1, [], [])
        expected = datetime.fromtimestamp(1700000000000 / 1000, tz=timezone.utc)
        assert result.listings[0].posted_date == expected

    def test_posted_date_none_when_absent(self, scraper):
        posting = {k: v for k, v in LEVER_FIXTURE_POSTING.items() if k != "createdAt"}
        result = parse_with_filters(scraper, [posting], 1, [], [])
        assert result.listings[0].posted_date is None

    def test_salary_range_formatted_when_present(self, scraper):
        posting = dict(LEVER_FIXTURE_POSTING, salaryRange={"min": 18000, "max": 25000, "currency": "PLN"})
        result = parse_with_filters(scraper, [posting], 1, [], [])
        assert result.listings[0].salary_range == "18 000 - 25 000 PLN"

    def test_salary_none_when_absent(self, scraper):
        result = parse_with_filters(scraper, [LEVER_FIXTURE_POSTING], 1, [], [])
        assert result.listings[0].salary_range is None

    def test_missing_site_name_raises_parse_error(self):
        scraper = LeverScraper(company={"name": "NoSite"})
        with pytest.raises(ScraperParseError, match="site_name"):
            import asyncio
            asyncio.run(scraper.fetch_page(page=1, roles=[], cities=[]))


# ── Full scrape() via MockTransport (real skip/limit pagination) ───────────

class TestFullScrapeViaMockTransport:

    @pytest.mark.asyncio
    async def test_paginates_using_skip_and_limit(self):
        requests_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            skip = int(request.url.params["skip"])
            requests_seen.append(skip)
            if skip == 0:
                postings = [dict(LEVER_FIXTURE_POSTING, id=str(i)) for i in range(25)]
            else:
                postings = [dict(LEVER_FIXTURE_POSTING, id=str(i)) for i in range(25, 30)]
            return httpx.Response(200, json=postings)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with LeverScraper(
            company={"name": "Netflix", "site_name": "netflix"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            results = await scraper.scrape(roles=[], cities=[])

        assert len(results) == 30
        assert requests_seen == [0, 25]

    @pytest.mark.asyncio
    async def test_non_list_response_raises_parse_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "not found"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with LeverScraper(
            company={"name": "Netflix", "site_name": "netflix"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            with pytest.raises(ScraperParseError, match="JSON array"):
                await scraper.scrape(roles=[], cities=[])
