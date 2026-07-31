"""
Unit tests for modules.scraper.company.base — the shared CompanyJobScraper
base class and load_company_sources() config loader.

Platform-specific parsing/fetching is tested in test_greenhouse.py,
test_lever.py, test_smartrecruiters.py, test_workday.py. This file covers
only what's shared: config loading, company lookup, local filtering, and
board_name construction.
"""

import json

import pytest

from core.exceptions import ScraperError
from modules.scraper.company.base import CompanyJobScraper, load_company_sources


class _DummyCompanyScraper(CompanyJobScraper):
    """Minimal concrete subclass — only enough to instantiate and test
    the shared base behaviour, not a real platform integration."""

    platform_name = "dummy"

    async def fetch_page(self, page, roles, cities):
        return {}

    def parse_page(self, raw_page, page):
        from modules.scraper.base import PageResult
        return PageResult(listings=[], has_next_page=False)


# ── load_company_sources() ──────────────────────────────────────────────────

class TestLoadCompanySources:

    def test_loads_default_config_successfully(self):
        sources = load_company_sources()
        assert isinstance(sources, dict)
        for platform in ("greenhouse", "lever", "smartrecruiters", "workday"):
            assert platform in sources
            assert isinstance(sources[platform], list)
            assert len(sources[platform]) > 0

    def test_ignores_underscore_prefixed_keys(self):
        sources = load_company_sources()
        assert "_note" not in sources

    def test_missing_file_raises_scraper_error(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(ScraperError, match="not found"):
            load_company_sources(missing)

    def test_malformed_json_raises_scraper_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ScraperError, match="not valid JSON"):
            load_company_sources(bad)

    def test_non_object_json_raises_scraper_error(self, tmp_path):
        bad = tmp_path / "array.json"
        bad.write_text(json.dumps(["a", "list"]), encoding="utf-8")
        with pytest.raises(ScraperError, match="JSON object"):
            load_company_sources(bad)

    def test_non_list_platform_entry_raises_scraper_error(self, tmp_path):
        bad = tmp_path / "bad_platform.json"
        bad.write_text(json.dumps({"greenhouse": {"not": "a list"}}), encoding="utf-8")
        with pytest.raises(ScraperError, match="must be a list"):
            load_company_sources(bad)

    def test_custom_path_used_over_default(self, tmp_path):
        custom = tmp_path / "custom_sources.json"
        custom.write_text(json.dumps({"greenhouse": [{"name": "TestCo", "board_token": "testco"}]}), encoding="utf-8")
        sources = load_company_sources(custom)
        assert sources == {"greenhouse": [{"name": "TestCo", "board_token": "testco"}]}


# ── CompanyJobScraper construction ──────────────────────────────────────────

class TestConstruction:

    def test_board_name_includes_platform_and_company(self):
        scraper = _DummyCompanyScraper(company={"name": "Acme Corp"})
        assert scraper.board_name == "dummy.acme-corp"

    def test_company_name_defaults_when_missing(self):
        scraper = _DummyCompanyScraper(company={})
        assert scraper.company_name == "Unknown Company"
        assert scraper.board_name == "dummy.unknown-company"

    def test_company_dict_stored_as_is(self):
        company = {"name": "Acme", "board_token": "acme", "extra_field": 123}
        scraper = _DummyCompanyScraper(company=company)
        assert scraper.company == company


# ── for_company() lookup ─────────────────────────────────────────────────────

class TestForCompany:

    def _sources_file(self, tmp_path):
        path = tmp_path / "sources.json"
        path.write_text(
            json.dumps({"dummy": [{"name": "Acme Corp", "board_token": "acme"}]}),
            encoding="utf-8",
        )
        return path

    def test_finds_company_by_exact_name(self, tmp_path):
        path = self._sources_file(tmp_path)
        scraper = _DummyCompanyScraper.for_company("Acme Corp", sources_path=path)
        assert scraper.company == {"name": "Acme Corp", "board_token": "acme"}

    def test_lookup_is_case_insensitive(self, tmp_path):
        path = self._sources_file(tmp_path)
        scraper = _DummyCompanyScraper.for_company("acme corp", sources_path=path)
        assert scraper.company_name == "Acme Corp"

    def test_lookup_strips_whitespace(self, tmp_path):
        path = self._sources_file(tmp_path)
        scraper = _DummyCompanyScraper.for_company("  Acme Corp  ", sources_path=path)
        assert scraper.company_name == "Acme Corp"

    def test_unknown_company_raises_scraper_error(self, tmp_path):
        path = self._sources_file(tmp_path)
        with pytest.raises(ScraperError, match="No dummy config found"):
            _DummyCompanyScraper.for_company("Nonexistent", sources_path=path)

    def test_unknown_platform_key_raises_scraper_error(self, tmp_path):
        path = tmp_path / "sources.json"
        path.write_text(json.dumps({"other_platform": [{"name": "X"}]}), encoding="utf-8")
        with pytest.raises(ScraperError, match="No dummy config found"):
            _DummyCompanyScraper.for_company("X", sources_path=path)


# ── _matches_filters() (shared local role/city filter) ─────────────────────

class TestMatchesFilters:

    def test_empty_filters_match_everything(self):
        assert CompanyJobScraper._matches_filters("Any Title", "Anywhere", [], []) is True

    def test_role_substring_case_insensitive(self):
        assert CompanyJobScraper._matches_filters("Senior Test Engineer", "Warsaw", ["test engineer"], []) is True
        assert CompanyJobScraper._matches_filters("Senior TEST ENGINEER", "Warsaw", ["test engineer"], []) is True
        assert CompanyJobScraper._matches_filters("Software Developer", "Warsaw", ["test engineer"], []) is False

    def test_city_substring_case_insensitive(self):
        assert CompanyJobScraper._matches_filters("Engineer", "Wroclaw, Poland", [], ["wroclaw"]) is True
        assert CompanyJobScraper._matches_filters("Engineer", "Berlin, Germany", [], ["wroclaw"]) is False

    def test_both_role_and_city_required_when_both_given(self):
        assert CompanyJobScraper._matches_filters(
            "Test Engineer", "Wroclaw, Poland", ["test engineer"], ["wroclaw"]
        ) is True
        assert CompanyJobScraper._matches_filters(
            "Test Engineer", "Berlin, Germany", ["test engineer"], ["wroclaw"]
        ) is False
        assert CompanyJobScraper._matches_filters(
            "Developer", "Wroclaw, Poland", ["test engineer"], ["wroclaw"]
        ) is False

    def test_any_role_or_city_in_list_matches(self):
        assert CompanyJobScraper._matches_filters(
            "QA Engineer", "Warsaw", ["test engineer", "qa engineer"], ["krakow", "warsaw"]
        ) is True


# ── _slug() ──────────────────────────────────────────────────────────────────

class TestSlug:

    def test_lowercases_and_hyphenates(self):
        assert CompanyJobScraper._slug("Bosch Group") == "bosch-group"

    def test_strips_non_alphanumeric(self):
        assert CompanyJobScraper._slug("Acme, Inc.") == "acme-inc"

    def test_empty_string_falls_back_to_unknown(self):
        assert CompanyJobScraper._slug("") == "unknown"

    def test_purely_symbolic_string_falls_back_to_unknown(self):
        assert CompanyJobScraper._slug("!!!") == "unknown"
