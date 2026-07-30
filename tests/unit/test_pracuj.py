"""
Unit tests for modules.scraper.pracuj.PracujScraper.

Two layers, matching the fetch/parse separation:

1. Parser tests (TestParsePage, TestFieldExtraction, TestPaginationDetection)
   — pure, no network at all. PRACUJ_FIXTURE_HTML below is the single place
   documenting the HTML shape this scraper assumes (see the schema caveat
   in pracuj.py's module docstring). If the live site's markup has drifted,
   update this fixture first — a failing test here tells you exactly which
   selector assumption broke.

2. Integration test (TestFullScrapeViaMockTransport) — exercises
   fetch_page + parse_page + pagination + rate limiting together through a
   real httpx.AsyncClient wired to httpx.MockTransport (in-process fake,
   zero real network).

No test in this file makes a real network call.
"""

import httpx
import pytest

from core.exceptions import ScraperParseError
from core.models import ApplicationSource, JobListing, Market
from modules.scraper.pracuj import PracujScraper
from modules.scraper.rate_limiter import RateLimiter

# ── Fixture: the assumed Pracuj.pl search-results HTML shape ───────────────
# See the schema caveat at the top of modules/scraper/pracuj.py.

PRACUJ_FIXTURE_HTML = """
<html>
<body>
<div class="results">
    <div data-test="default-offer" data-test-offerid="1003226213">
        <a data-test="link-offer" href="/praca/senior-test-engineer-warszawa,oferta,1003226213">
            <h2 data-test="offer-title">Senior Test Engineer</h2>
        </a>
        <div data-test="text-company-name">Bosch Engineering</div>
        <div data-test="offer-additional-info">
            <span data-test="text-region">Warszawa</span>
        </div>
        <span data-test="offer-salary">18 000-25 000 zl/mies. (B2B)</span>
        <div data-test="offer-technology">
            <span data-test="offer-technology-item">AUTOSAR</span>
            <span data-test="offer-technology-item">UDS</span>
        </div>
    </div>
    <div data-test="default-offer" data-test-offerid="1003226214">
        <a data-test="link-offer" href="https://www.pracuj.pl/praca/qa-engineer-krakow,oferta,1003226214">
            <h2 data-test="offer-title">QA Engineer</h2>
        </a>
        <div data-test="text-company-name">TestCo</div>
        <div data-test="offer-additional-info">
            <span data-test="text-region">Krakow</span>
        </div>
    </div>
</div>
<button data-test="bottom-pagination-button-next">Next</button>
</body>
</html>
"""

PRACUJ_FIXTURE_HTML_LAST_PAGE = """
<html>
<body>
<div class="results">
    <div data-test="default-offer">
        <a data-test="link-offer" href="/praca/test-engineer-warszawa,oferta,1">
            <h2 data-test="offer-title">Test Engineer</h2>
        </a>
        <div data-test="text-company-name">TestCo</div>
        <div data-test="offer-additional-info">
            <span data-test="text-region">Warszawa</span>
        </div>
    </div>
</div>
<button data-test="bottom-pagination-button-next" disabled>Next</button>
</body>
</html>
"""

PRACUJ_FIXTURE_HTML_EMPTY = """
<html><body><div class="results"></div></body></html>
"""


@pytest.fixture
def scraper() -> PracujScraper:
    return PracujScraper(rate_limiter=RateLimiter(min_delay=0, max_delay=0))


# ── parse_page(): the full page ─────────────────────────────────────────────

class TestParsePage:

    def test_parses_valid_page_into_job_listings(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)

        assert len(result.listings) == 2
        job = result.listings[0]
        assert isinstance(job, JobListing)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Bosch Engineering"
        assert job.city == "Warszawa"
        assert job.market == Market.POLAND
        assert job.source == ApplicationSource.PRACUJ

    def test_relative_url_is_absolutized(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.listings[0].url == (
            "https://www.pracuj.pl/praca/senior-test-engineer-warszawa,oferta,1003226213"
        )

    def test_absolute_url_kept_as_is(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.listings[1].url == (
            "https://www.pracuj.pl/praca/qa-engineer-krakow,oferta,1003226214"
        )

    def test_non_string_page_raises_parse_error(self, scraper):
        with pytest.raises(ScraperParseError, match="HTML text"):
            scraper.parse_page({"not": "a string"}, page=1)

    def test_empty_results_page_is_valid_zero_results(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML_EMPTY, page=1)
        assert result.listings == []
        assert result.has_next_page is False

    def test_card_missing_title_is_skipped_not_fatal(self, scraper):
        html = """
        <div data-test="default-offer">
            <a data-test="link-offer" href="/praca/x,oferta,1"></a>
            <div data-test="text-company-name">TestCo</div>
        </div>
        <div data-test="default-offer">
            <a data-test="link-offer" href="/praca/y,oferta,2">
                <h2 data-test="offer-title">Valid Offer</h2>
            </a>
            <div data-test="text-company-name">TestCo</div>
            <div data-test="text-region">Warszawa</div>
        </div>
        """
        result = scraper.parse_page(html, page=1)
        assert len(result.listings) == 1
        assert result.listings[0].title == "Valid Offer"

    def test_card_missing_url_is_skipped_not_fatal(self, scraper):
        html = """
        <div data-test="default-offer">
            <h2 data-test="offer-title">No Link Offer</h2>
            <div data-test="text-company-name">TestCo</div>
        </div>
        """
        result = scraper.parse_page(html, page=1)
        assert result.listings == []

    def test_every_returned_item_is_a_joblisting(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert all(isinstance(j, JobListing) for j in result.listings)


# ── Pagination detection ─────────────────────────────────────────────────────

class TestPaginationDetection:

    def test_has_next_page_true_when_next_button_enabled(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.has_next_page is True

    def test_has_next_page_false_when_next_button_disabled(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML_LAST_PAGE, page=2)
        assert result.has_next_page is False

    def test_has_next_page_false_when_no_pagination_control(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML_EMPTY, page=1)
        assert result.has_next_page is False

    def test_has_next_page_false_when_aria_disabled(self, scraper):
        html = """
        <div data-test="default-offer">
            <a data-test="link-offer" href="/praca/x,oferta,1">
                <h2 data-test="offer-title">Offer</h2>
            </a>
        </div>
        <button data-test="bottom-pagination-button-next" aria-disabled="true">Next</button>
        """
        result = scraper.parse_page(html, page=1)
        assert result.has_next_page is False


# ── Field extraction — each helper tested in isolation ──────────────────────

class TestFieldExtraction:

    def test_company_defaults_to_unknown_when_missing(self, scraper):
        html = """
        <div data-test="default-offer">
            <a data-test="link-offer" href="/praca/x,oferta,1">
                <h2 data-test="offer-title">Offer</h2>
            </a>
        </div>
        """
        result = scraper.parse_page(html, page=1)
        assert result.listings[0].company == "Unknown"

    def test_city_defaults_to_unknown_when_missing(self, scraper):
        html = """
        <div data-test="default-offer">
            <a data-test="link-offer" href="/praca/x,oferta,1">
                <h2 data-test="offer-title">Offer</h2>
            </a>
            <div data-test="text-company-name">TestCo</div>
        </div>
        """
        result = scraper.parse_page(html, page=1)
        assert result.listings[0].city == "Unknown"

    def test_salary_none_when_absent(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.listings[1].salary_range is None

    def test_salary_present_when_available(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.listings[0].salary_range == "18 000-25 000 zl/mies. (B2B)"

    def test_description_from_technology_tags(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.listings[0].description == "AUTOSAR, UDS"

    def test_description_none_when_no_tags(self, scraper):
        result = scraper.parse_page(PRACUJ_FIXTURE_HTML, page=1)
        assert result.listings[1].description is None

    def test_build_search_url_with_role_and_city(self):
        url = PracujScraper._build_search_url(
            roles=["Test Engineer", "QA Engineer"], cities=["Krakow", "Warsaw"], page=2
        )
        assert url == "https://www.pracuj.pl/praca/Test%20Engineer;kw/Krakow;wp?pn=2"

    def test_build_search_url_without_city(self):
        url = PracujScraper._build_search_url(roles=["Test Engineer"], cities=[], page=1)
        assert url == "https://www.pracuj.pl/praca/Test%20Engineer;kw?pn=1"

    def test_build_search_url_requires_at_least_one_role(self):
        with pytest.raises(ValueError, match="role keyword"):
            PracujScraper._build_search_url(roles=[], cities=["Krakow"], page=1)


# ── Full scrape() via MockTransport (fetch + parse + pagination together) ──

class TestFullScrapeViaMockTransport:

    @pytest.mark.asyncio
    async def test_single_page_scrape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "Test%20Engineer" in str(request.url)
            assert "Krakow" in str(request.url)
            return httpx.Response(200, text=PRACUJ_FIXTURE_HTML_LAST_PAGE)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with PracujScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            results = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

        assert len(results) == 1
        assert results[0].title == "Test Engineer"

    @pytest.mark.asyncio
    async def test_multi_page_scrape_follows_pagination(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params["pn"]
            if page == "1":
                return httpx.Response(200, text=PRACUJ_FIXTURE_HTML)
            return httpx.Response(200, text=PRACUJ_FIXTURE_HTML_LAST_PAGE)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with PracujScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            results = await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

        # page 1 has 2 offers, page 2 (last page) has 1 more distinct offer
        assert len(results) == 3
        titles = {j.title for j in results}
        assert titles == {"Senior Test Engineer", "QA Engineer", "Test Engineer"}

    @pytest.mark.asyncio
    async def test_scrape_stops_when_no_next_page(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text=PRACUJ_FIXTURE_HTML_LAST_PAGE)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with PracujScraper(
            client=client, rate_limiter=RateLimiter(min_delay=0, max_delay=0)
        ) as scraper:
            await scraper.scrape(roles=["Test Engineer"], cities=["Krakow"])

        assert calls["n"] == 1  # stopped after page 1 since next button was disabled
