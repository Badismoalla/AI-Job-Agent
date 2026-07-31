"""
Unit tests for modules.scraper.company.smartrecruiters.SmartRecruitersScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction, TestPaginationInference)
   — pure, no network. SMARTRECRUITERS_FIXTURE_POSTING documents the
   assumed schema (see the schema caveat in smartrecruiters.py).

2. Integration test (TestFullScrapeViaMockTransport) — fetch_page +
   parse_page + real offset/limit/totalFound pagination via httpx.MockTransport.

No test in this file makes a real network call.
"""

from datetime import datetime
from typing import Any

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.company.smartrecruiters import SmartRecruitersScraper
from modules.scraper.rate_limiter import RateLimiter

SMARTRECRUITERS_FIXTURE_POSTING: dict[str, Any] = {
    "id": "8a7f8e0d-1234-5678-9abc-def012345678",
    "name": "Senior Test Engineer",
    "location": {"city": "Warsaw", "region": "Mazovia", "country": "pl"},
    "ref": {"jobAdUrl": "https://jobs.smartrecruiters.com/Visa/8a7f8e0d-senior-test-engineer"},
    "releasedDate": "2026-01-15T10:30:00.000Z",
}


def make_fixture_page(postings: list[dict], total_found: int | None = None, offset: int = 0) -> dict:
    return {
        "totalFound": total_found if total_found is not None else len(postings),
        "offset": offset,
        "limit": 100,
        "content": postings,
    }


@pytest.fixture
def scraper() -> SmartRecruitersScraper:
    return SmartRecruitersScraper(
        company={"name": "Visa", "company_id": "Visa"},
        rate_limiter=RateLimiter(min_delay=0, max_delay=0),
    )


def parse_with_filters(scraper, raw_page, page, roles, cities):
    scraper._current_roles = roles
    scraper._current_cities = cities
    return scraper.parse_page(raw_page, page=page)


# ── parse_page() ─────────────────────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_posting_into_joblisting(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING]), 1, [], [])
        assert len(result.listings) == 1
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Visa"
        assert job.city == "Warsaw, Mazovia, pl"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.CAREER_PAGE

    def test_url_from_ref_job_ad_url(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING]), 1, [], [])
        assert result.listings[0].url == SMARTRECRUITERS_FIXTURE_POSTING["ref"]["jobAdUrl"]

    def test_url_falls_back_to_constructed_url_when_ref_absent(self, scraper):
        posting = {k: v for k, v in SMARTRECRUITERS_FIXTURE_POSTING.items() if k != "ref"}
        result = parse_with_filters(scraper, make_fixture_page([posting]), 1, [], [])
        assert result.listings[0].url == f"https://jobs.smartrecruiters.com/Visa/{posting['id']}"

    def test_non_dict_page_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="JSON object"):
            scraper.parse_page(["not", "a", "dict"], page=1)

    def test_missing_content_key_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="content"):
            scraper.parse_page({"totalFound": 0}, page=1)

    def test_non_list_content_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="must be a list"):
            scraper.parse_page({"content": "not a list"}, page=1)

    def test_missing_name_or_id_skipped_not_fatal(self, scraper):
        bad = {"location": {"city": "Warsaw"}}
        good = SMARTRECRUITERS_FIXTURE_POSTING
        result = parse_with_filters(scraper, make_fixture_page([bad, good]), 1, [], [])
        assert len(result.listings) == 1

    def test_role_filter_applied(self, scraper):
        other = dict(SMARTRECRUITERS_FIXTURE_POSTING, id="other", name="Data Scientist")
        result = parse_with_filters(
            scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING, other]), 1,
            roles=["test engineer"], cities=[],
        )
        assert len(result.listings) == 1

    def test_city_filter_applied(self, scraper):
        other = dict(SMARTRECRUITERS_FIXTURE_POSTING, id="other", location={"city": "Berlin", "country": "de"})
        result = parse_with_filters(
            scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING, other]), 1,
            roles=[], cities=["warsaw"],
        )
        assert len(result.listings) == 1

    def test_every_returned_item_is_joblisting(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING]), 1, [], [])
        assert all(isinstance(j, JobListing) for j in result.listings)

    def test_description_and_salary_are_none(self, scraper):
        """Documented limitation: the list endpoint is summary-only."""
        result = parse_with_filters(scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING]), 1, [], [])
        assert result.listings[0].description is None
        assert result.listings[0].salary_range is None


# ── Pagination via totalFound/offset (exact, not heuristic) ────────────────

class TestPaginationInference:

    def test_has_next_page_true_when_more_remain(self, scraper):
        postings = [SMARTRECRUITERS_FIXTURE_POSTING] * 100
        result = parse_with_filters(scraper, make_fixture_page(postings, total_found=250, offset=0), 1, [], [])
        assert result.has_next_page is True

    def test_has_next_page_false_on_last_page(self, scraper):
        postings = [SMARTRECRUITERS_FIXTURE_POSTING] * 50
        result = parse_with_filters(scraper, make_fixture_page(postings, total_found=250, offset=200), 3, [], [])
        assert result.has_next_page is False

    def test_has_next_page_false_when_zero_results(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([], total_found=0, offset=0), 1, [], [])
        assert result.has_next_page is False


# ── Field extraction ─────────────────────────────────────────────────────────

class TestFieldExtraction:

    def test_location_builds_from_city_region_country(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING]), 1, [], [])
        assert result.listings[0].city == "Warsaw, Mazovia, pl"

    def test_location_defaults_to_unknown_when_absent(self, scraper):
        posting = {k: v for k, v in SMARTRECRUITERS_FIXTURE_POSTING.items() if k != "location"}
        result = parse_with_filters(scraper, make_fixture_page([posting]), 1, [], [])
        assert result.listings[0].city == "Unknown"

    def test_posted_date_parsed_from_iso8601(self, scraper):
        result = parse_with_filters(scraper, make_fixture_page([SMARTRECRUITERS_FIXTURE_POSTING]), 1, [], [])
        assert result.listings[0].posted_date.year == 2026

    def test_posted_date_none_when_absent(self, scraper):
        posting = {k: v for k, v in SMARTRECRUITERS_FIXTURE_POSTING.items() if k != "releasedDate"}
        result = parse_with_filters(scraper, make_fixture_page([posting]), 1, [], [])
        assert result.listings[0].posted_date is None

    def test_missing_company_id_raises_parse_error(self):
        scraper = SmartRecruitersScraper(company={"name": "NoId"})
        with pytest.raises(ScraperParseError, match="company_id"):
            import asyncio
            asyncio.run(scraper.fetch_page(page=1, roles=[], cities=[]))


# ── Full scrape() via MockTransport (real offset/limit pagination) ─────────

class TestFullScrapeViaMockTransport:

    @pytest.mark.asyncio
    async def test_paginates_using_offset_and_total_found(self):
        offsets_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            offset = int(request.url.params["offset"])
            offsets_seen.append(offset)
            if offset == 0:
                postings = [dict(SMARTRECRUITERS_FIXTURE_POSTING, id=str(i)) for i in range(100)]
            else:
                postings = [dict(SMARTRECRUITERS_FIXTURE_POSTING, id=str(i)) for i in range(100, 130)]
            return httpx.Response(200, json=make_fixture_page(postings, total_found=130, offset=offset))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with SmartRecruitersScraper(
            company={"name": "Visa", "company_id": "Visa"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            results = await scraper.scrape(roles=[], cities=[])

        assert len(results) == 130
        assert offsets_seen == [0, 100]

    @pytest.mark.asyncio
    async def test_missing_content_key_raises_parse_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"totalFound": 0})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with SmartRecruitersScraper(
            company={"name": "Visa", "company_id": "Visa"},
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0),
        ) as scraper:
            with pytest.raises(ScraperParseError, match="content"):
                await scraper.scrape(roles=[], cities=[])
