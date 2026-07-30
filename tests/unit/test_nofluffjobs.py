"""
Unit tests for modules.scraper.nofluffjobs.NoFluffJobsScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction) — pure, no network at
   all. NOFLUFFJOBS_FIXTURE_POSTING below is the single place documenting
   the JSON shape this scraper assumes (see the schema caveat in
   nofluffjobs.py's module docstring). If the live API's shape has
   drifted, update this fixture first — a failing test here tells you
   exactly which assumption broke.

2. Integration test (TestFullScrapeViaMockTransport) — exercises
   fetch_page + parse_page + pagination + rate limiting together through
   a real httpx.AsyncClient wired to httpx.MockTransport (in-process fake,
   zero real network).

No test in this file makes a real network call.
"""

from typing import Any

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.nofluffjobs import NoFluffJobsScraper
from modules.scraper.rate_limiter import RateLimiter

# ── Fixture: the assumed NoFluffJobs search API response shape ─────────────
# See the schema caveat at the top of modules/scraper/nofluffjobs.py.

NOFLUFFJOBS_FIXTURE_POSTING: dict[str, Any] = {
    "id": "abc123",
    "title": "Senior Test Engineer",
    "name": "Bosch Engineering",
    "url": "senior-test-engineer-bosch-wroclaw",
    "location": {
        "places": [{"city": "Wroclaw", "country": {"code": "PL", "name": "Poland"}}],
        "fullyRemote": False,
    },
    "salary": {"from": 18000, "to": 25000, "currency": "PLN", "type": "B2B"},
    "introduction": "Join our automotive validation team working on ADAS ECUs.",
    "posted": 1700000000000,
}


def make_fixture_page(postings: list[dict], total_count: int | None = None) -> dict:
    return {
        "postings": postings,
        "totalCount": total_count if total_count is not None else len(postings),
    }


@pytest.fixture
def scraper() -> NoFluffJobsScraper:
    return NoFluffJobsScraper(rate_limiter=RateLimiter(min_delay=0, max_delay=0))


# ── parse_page(): the full page ─────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_page_into_job_listings(self, scraper):
        raw = make_fixture_page([NOFLUFFJOBS_FIXTURE_POSTING])
        result = scraper.parse_page(raw, page=1)

        assert len(result.listings) == 1
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Bosch Engineering"
        assert job.city == "Wroclaw"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.NOFLUFFJOBS

    def test_url_built_from_slug(self, scraper):
        raw = make_fixture_page([NOFLUFFJOBS_FIXTURE_POSTING])
        result = scraper.parse_page(raw, page=1)
        assert result.listings[0].url == "https://nofluffjobs.com/pl/job/senior-test-engineer-bosch-wroclaw"

    def test_missing_postings_key_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="postings"):
            scraper.parse_page({"totalCount": 0}, page=1)

    def test_non_dict_page_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="JSON object"):
            scraper.parse_page(["not", "a", "dict"], page=1)

    def test_empty_postings_list_is_valid_zero_results(self, scraper):
        result = scraper.parse_page(make_fixture_page([]), page=1)
        assert result.listings == []
        assert result.has_next_page is False

    def test_unparseable_posting_is_skipped_not_fatal(self, scraper):
        good = NOFLUFFJOBS_FIXTURE_POSTING
        bad = {"id": "no-title-or-url"}  # missing required 'title'/'url'
        raw = make_fixture_page([bad, good])

        result = scraper.parse_page(raw, page=1)

        assert len(result.listings) == 1
        assert result.listings[0].title == "Senior Test Engineer"

    def test_every_returned_item_is_a_joblisting(self, scraper):
        raw = make_fixture_page([NOFLUFFJOBS_FIXTURE_POSTING, NOFLUFFJOBS_FIXTURE_POSTING])
        result = scraper.parse_page(raw, page=1)
        assert all(isinstance(j, JobListing) for j in result.listings)


# ── has_next_page inference ──────────────────────────────────────────────────

class TestPaginationInference:

    def test_has_next_page_true_when_more_results_remain(self, scraper):
        postings = [NOFLUFFJOBS_FIXTURE_POSTING] * 20  # a full page (_RESULTS_PER_PAGE)
        raw = make_fixture_page(postings, total_count=45)
        result = scraper.parse_page(raw, page=1)
        assert result.has_next_page is True

    def test_has_next_page_false_when_last_page(self, scraper):
        postings = [NOFLUFFJOBS_FIXTURE_POSTING] * 5
        raw = make_fixture_page(postings, total_count=25)
        result = scraper.parse_page(raw, page=2)  # page 2 * 20 = 40 >= 25
        assert result.has_next_page is False

    def test_has_next_page_false_when_zero_results(self, scraper):
        raw = make_fixture_page([], total_count=0)
        result = scraper.parse_page(raw, page=1)
        assert result.has_next_page is False


# ── Field extraction — each helper tested in isolation ──────────────────────

class TestFieldExtraction:

    def test_company_falls_back_to_nested_company_name(self):
        posting = dict(NOFLUFFJOBS_FIXTURE_POSTING)
        del posting["name"]
        posting["company"] = {"name": "Nested Co"}
        assert NoFluffJobsScraper._extract_company(posting) == "Nested Co"

    def test_company_defaults_to_unknown(self):
        posting = {k: v for k, v in NOFLUFFJOBS_FIXTURE_POSTING.items() if k != "name"}
        assert NoFluffJobsScraper._extract_company(posting) == "Unknown"

    def test_city_from_places_list(self):
        assert NoFluffJobsScraper._extract_city(NOFLUFFJOBS_FIXTURE_POSTING) == "Wroclaw"

    def test_city_remote_when_fully_remote_and_no_places(self):
        posting = {"location": {"places": [], "fullyRemote": True}}
        assert NoFluffJobsScraper._extract_city(posting) == "Remote"

    def test_city_defaults_to_unknown(self):
        assert NoFluffJobsScraper._extract_city({}) == "Unknown"

    def test_salary_range_formatted_with_currency_and_type(self):
        salary = NoFluffJobsScraper._extract_salary(NOFLUFFJOBS_FIXTURE_POSTING)
        assert salary == "18 000 - 25 000 PLN (B2B)"

    def test_salary_none_when_absent(self):
        posting = {k: v for k, v in NOFLUFFJOBS_FIXTURE_POSTING.items() if k != "salary"}
        assert NoFluffJobsScraper._extract_salary(posting) is None

    def test_salary_single_value_when_only_one_bound_present(self):
        posting = {"salary": {"from": 20000, "currency": "PLN", "type": "B2B"}}
        salary = NoFluffJobsScraper._extract_salary(posting)
        assert salary == "20000 PLN (B2B)"

    def test_description_from_introduction(self):
        desc = NoFluffJobsScraper._extract_description(NOFLUFFJOBS_FIXTURE_POSTING)
        assert desc == "Join our automotive validation team working on ADAS ECUs."

    def test_description_from_requirements_list_fallback(self):
        posting = {"requirements": ["AUTOSAR", "UDS/DoIP", "Python"]}
        desc = NoFluffJobsScraper._extract_description(posting)
        assert desc == "- AUTOSAR\n- UDS/DoIP\n- Python"

    def test_description_none_when_nothing_available(self):
        assert NoFluffJobsScraper._extract_description({}) is None

    def test_posted_date_parsed_from_epoch_millis(self):
        posting = {"posted": 1700000000000}
        dt = NoFluffJobsScraper._extract_posted_date(posting)
        assert dt is not None
        assert dt.year == 2023

    def test_posted_date_none_when_absent_or_invalid(self):
        assert NoFluffJobsScraper._extract_posted_date({}) is None
        assert NoFluffJobsScraper._extract_posted_date({"posted": "not-a-number"}) is None

    def test_build_criteria_uses_first_role_and_city(self):
        criteria = NoFluffJobsScraper._build_criteria(
            roles=["Test Engineer", "QA Engineer"], cities=["Krakow", "Warsaw"]
        )
        assert criteria == "Test Engineer Krakow"

    def test_build_criteria_without_cities(self):
        criteria = NoFluffJobsScraper._build_criteria(roles=["Test Engineer"], cities=[])
        assert criteria == "Test Engineer"

    def test_build_criteria_requires_at_least_one_role(self):
        with pytest.raises(ValueError, match="role keyword"):
            NoFluffJobsScraper._build_criteria(roles=[], cities=["Krakow"])


# ── Full scrape() via MockTransport (fetch + parse + pagination together) ──

class TestFullScrapeViaMockTransport:

    def _make_posting(self, n: int) -> dict:
        posting = dict(NOFLUFFJOBS_FIXTURE_POSTING)
        posting["id"] = f"job-{n}"
        posting["title"] = f"Test Engineer {n}"
        posting["url"] = f"test-engineer-{n}-bosch-wroclaw"
        return posting

    @pytest.mark.asyncio
    async def test_single_page_scrape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["criteria"] == "Test Engineer Krakow"
            return httpx.Response(200, json=make_fixture_page([self._make_posting(1)]))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with NoFluffJobsScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            results = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

        assert len(results) == 1
        assert results[0].title == "Test Engineer 1"

    @pytest.mark.asyncio
    async def test_multi_page_scrape_follows_pagination(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            if page == 1:
                postings = [self._make_posting(i) for i in range(1, 21)]  # full page
                return httpx.Response(200, json=make_fixture_page(postings, total_count=25))
            postings = [self._make_posting(i) for i in range(21, 26)]  # remaining 5
            return httpx.Response(200, json=make_fixture_page(postings, total_count=25))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with NoFluffJobsScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            results = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

        assert len(results) == 25
        assert {j.id for j in results} == {
            f"bosch-engineering-test-engineer-{i}-wroclaw" for i in range(1, 26)
        }

    @pytest.mark.asyncio
    async def test_scrape_deduplicates_across_pages(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            if page == 1:
                return httpx.Response(
                    200,
                    json=make_fixture_page([self._make_posting(1), self._make_posting(2)], total_count=40),
                )
            # page 2 accidentally repeats job-2 (real boards do this)
            return httpx.Response(
                200,
                json=make_fixture_page([self._make_posting(2), self._make_posting(3)], total_count=40),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with NoFluffJobsScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0), max_pages=2
        ) as scraper:
            results = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

        titles = {j.title for j in results}
        assert titles == {"Test Engineer 1", "Test Engineer 2", "Test Engineer 3"}
