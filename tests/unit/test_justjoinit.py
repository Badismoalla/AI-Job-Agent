"""
Unit tests for modules.scraper.justjoinit.JustJoinITScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction, TestFiltering) — pure,
   no network at all. JUSTJOINIT_FIXTURE_OFFER below is the single place
   documenting the JSON shape this scraper assumes (see the schema caveat
   in justjoinit.py's module docstring — this one carries more risk than
   nofluffjobs.py/pracuj.py since the live site may have moved to a
   Next.js RSC frontend; a failing test here is the signal to check that
   first). If the live API's shape has drifted, update this fixture and
   the tests will point at exactly which assumption broke.

2. Integration test (TestFullScrapeViaMockTransport) — exercises
   fetch_page + parse_page + local filtering + pagination together
   through a real httpx.AsyncClient wired to httpx.MockTransport
   (in-process fake, zero real network). Also verifies the "fetch once,
   cache, filter/paginate locally" strategy only hits the network once
   per scrape() call regardless of how many pages are walked.

No test in this file makes a real network call.
"""

from typing import Any

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.justjoinit import JustJoinITScraper
from modules.scraper.rate_limiter import RateLimiter

# ── Fixture: the assumed JustJoin.it bulk offers API response shape ────────
# See the schema caveat at the top of modules/scraper/justjoinit.py.

JUSTJOINIT_FIXTURE_OFFER: dict[str, Any] = {
    "id": "acme-senior-test-engineer-warszawa-python",
    "title": "Senior Test Engineer",
    "company_name": "Acme Software",
    "city": "Warszawa",
    "remote": False,
    "employment_types": [
        {"type": "b2b", "salary": {"from": 18000, "to": 25000, "currency": "PLN"}}
    ],
    "skills": [{"name": "Python", "level": 3}, {"name": "AUTOSAR", "level": 2}],
}


def make_offer(offer_id: str, title: str = "Test Engineer", city: str = "Warszawa", **overrides) -> dict:
    offer = dict(JUSTJOINIT_FIXTURE_OFFER)
    offer["id"] = offer_id
    offer["title"] = title
    offer["city"] = city
    offer.update(overrides)
    return offer


@pytest.fixture
def scraper() -> JustJoinITScraper:
    return JustJoinITScraper(rate_limiter=RateLimiter(min_delay=0, max_delay=0))


def parse_with_filters(scraper, raw_page, page, roles, cities):
    """Helper: set the cached roles/cities the way fetch_page() would, then parse."""
    scraper._current_roles = roles
    scraper._current_cities = cities
    return scraper.parse_page(raw_page, page=page)


# ── parse_page(): the full page ─────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_offers_into_job_listings(self, scraper):
        result = parse_with_filters(scraper, [JUSTJOINIT_FIXTURE_OFFER], page=1, roles=[], cities=[])

        assert len(result.listings) == 1
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Acme Software"
        assert job.city == "Warszawa"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.JUSTJOINIT

    def test_url_built_from_id(self, scraper):
        result = parse_with_filters(scraper, [JUSTJOINIT_FIXTURE_OFFER], page=1, roles=[], cities=[])
        assert result.listings[0].url == (
            "https://justjoin.it/job-offer/acme-senior-test-engineer-warszawa-python"
        )

    def test_non_list_page_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="list of offers"):
            scraper.parse_page({"not": "a list"}, page=1)

    def test_empty_list_is_valid_zero_results(self, scraper):
        result = parse_with_filters(scraper, [], page=1, roles=[], cities=[])
        assert result.listings == []
        assert result.has_next_page is False

    def test_non_dict_items_in_list_are_skipped(self, scraper):
        raw = [JUSTJOINIT_FIXTURE_OFFER, "not-a-dict", 123, None]
        result = parse_with_filters(scraper, raw, page=1, roles=[], cities=[])
        assert len(result.listings) == 1

    def test_offer_missing_title_or_id_is_skipped_not_fatal(self, scraper):
        bad = {"company_name": "TestCo"}  # missing title and id
        good = JUSTJOINIT_FIXTURE_OFFER
        result = parse_with_filters(scraper, [bad, good], page=1, roles=[], cities=[])
        assert len(result.listings) == 1
        assert result.listings[0].title == "Senior Test Engineer"

    def test_every_returned_item_is_a_joblisting(self, scraper):
        raw = [make_offer("a"), make_offer("b")]
        result = parse_with_filters(scraper, raw, page=1, roles=[], cities=[])
        assert all(isinstance(j, JobListing) for j in result.listings)


# ── Local filtering (role/city) ──────────────────────────────────────────────

class TestFiltering:

    def test_matches_filters_role_substring_case_insensitive(self):
        offer = {"title": "Senior Python Developer", "city": "Warszawa"}
        assert JustJoinITScraper._matches_filters(offer, ["python"], []) is True
        assert JustJoinITScraper._matches_filters(offer, ["PYTHON"], []) is True
        assert JustJoinITScraper._matches_filters(offer, ["java"], []) is False

    def test_matches_filters_city_case_insensitive(self):
        offer = {"title": "Test Engineer", "city": "Warszawa"}
        assert JustJoinITScraper._matches_filters(offer, [], ["warszawa"]) is True
        assert JustJoinITScraper._matches_filters(offer, [], ["Krakow"]) is False

    def test_empty_filters_match_everything(self):
        offer = {"title": "Anything", "city": "Anywhere"}
        assert JustJoinITScraper._matches_filters(offer, [], []) is True

    def test_role_and_city_both_required_when_both_given(self):
        offer = {"title": "Senior Python Developer", "city": "Warszawa"}
        assert JustJoinITScraper._matches_filters(offer, ["python"], ["warszawa"]) is True
        assert JustJoinITScraper._matches_filters(offer, ["python"], ["krakow"]) is False
        assert JustJoinITScraper._matches_filters(offer, ["java"], ["warszawa"]) is False

    def test_any_role_in_list_matches_or_semantics(self):
        offer = {"title": "Senior Test Engineer", "city": "Warszawa"}
        assert JustJoinITScraper._matches_filters(offer, ["python", "test engineer"], []) is True

    def test_parse_page_applies_cached_role_and_city_filters(self, scraper):
        offers = [
            make_offer("a", title="Senior Python Developer", city="Warszawa"),
            make_offer("b", title="Senior Java Developer", city="Warszawa"),
            make_offer("c", title="Senior Python Developer", city="Krakow"),
        ]
        result = parse_with_filters(scraper, offers, page=1, roles=["python"], cities=["warszawa"])
        assert len(result.listings) == 1
        assert result.listings[0].id.startswith("acme-software-senior-python-developer-warszawa")


# ── Local pagination over filtered results ──────────────────────────────────

class TestLocalPagination:

    def test_slices_filtered_results_into_pages(self, scraper):
        offers = [make_offer(str(i)) for i in range(25)]
        page1 = parse_with_filters(scraper, offers, page=1, roles=[], cities=[])
        page2 = parse_with_filters(scraper, offers, page=2, roles=[], cities=[])

        assert len(page1.listings) == 20  # _RESULTS_PER_PAGE
        assert len(page2.listings) == 5
        assert page1.has_next_page is True
        assert page2.has_next_page is False

    def test_has_next_page_false_when_fewer_than_one_page(self, scraper):
        offers = [make_offer(str(i)) for i in range(3)]
        result = parse_with_filters(scraper, offers, page=1, roles=[], cities=[])
        assert result.has_next_page is False


# ── Field extraction — each helper tested in isolation ──────────────────────

class TestFieldExtraction:

    def test_city_falls_back_to_remote(self):
        offer = {"id": "x", "title": "T", "remote": True}
        job = JustJoinITScraper()._parse_offer(offer)
        assert job.city == "Remote"

    def test_city_falls_back_to_unknown(self):
        offer = {"id": "x", "title": "T", "remote": False}
        job = JustJoinITScraper()._parse_offer(offer)
        assert job.city == "Unknown"

    def test_company_defaults_to_unknown(self):
        offer = {"id": "x", "title": "T", "city": "Warszawa"}
        job = JustJoinITScraper()._parse_offer(offer)
        assert job.company == "Unknown"

    def test_salary_formatted_with_currency_and_type(self):
        salary = JustJoinITScraper._extract_salary(JUSTJOINIT_FIXTURE_OFFER)
        assert salary == "18 000 - 25 000 PLN (b2b)"

    def test_salary_none_when_absent(self):
        offer = {k: v for k, v in JUSTJOINIT_FIXTURE_OFFER.items() if k != "employment_types"}
        assert JustJoinITScraper._extract_salary(offer) is None

    def test_salary_none_when_employment_types_empty(self):
        offer = dict(JUSTJOINIT_FIXTURE_OFFER, employment_types=[])
        assert JustJoinITScraper._extract_salary(offer) is None

    def test_description_from_skills_list_of_dicts(self):
        desc = JustJoinITScraper._extract_description(JUSTJOINIT_FIXTURE_OFFER)
        assert desc == "Python, AUTOSAR"

    def test_description_from_skills_list_of_strings(self):
        offer = {"skills": ["Python", "AUTOSAR"]}
        assert JustJoinITScraper._extract_description(offer) == "Python, AUTOSAR"

    def test_description_from_explicit_field(self):
        offer = {"description": "A great job.", "skills": [{"name": "Python"}]}
        assert JustJoinITScraper._extract_description(offer) == "A great job."

    def test_description_none_when_nothing_available(self):
        assert JustJoinITScraper._extract_description({}) is None


# ── Full scrape() via MockTransport (fetch + parse + pagination together) ──

class TestFullScrapeViaMockTransport:

    @pytest.mark.asyncio
    async def test_single_page_scrape_with_filter(self):
        offers = [
            make_offer("a", title="Senior Python Developer", city="Warszawa"),
            make_offer("b", title="Senior Java Developer", city="Warszawa"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=offers)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with JustJoinITScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            results = await scraper.scrape(roles=["python"], cities=["Warszawa"])

        assert len(results) == 1
        assert results[0].title == "Senior Python Developer"

    @pytest.mark.asyncio
    async def test_network_fetched_exactly_once_across_multiple_pages(self):
        calls = {"n": 0}
        offers = [make_offer(str(i), title=f"Test Engineer {i}") for i in range(25)]

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=offers)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with JustJoinITScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            results = await scraper.scrape(roles=[], cities=[])

        assert len(results) == 25
        assert calls["n"] == 1  # fetched once, paginated locally across 2 pages

    @pytest.mark.asyncio
    async def test_non_list_response_raises_parse_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "not found"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with JustJoinITScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            with pytest.raises(ScraperParseError, match="JSON array"):
                await scraper.scrape(roles=[], cities=[])
