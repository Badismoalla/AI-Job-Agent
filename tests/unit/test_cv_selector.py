"""
Tests for core.cv_selector.CVSelector.

Grounded in the real config/cvs.json (2 real CV files, split by market,
verified against resumes/ on disk) — not the illustrative "QA CV / Data CV"
example from the phase instructions, which does not match reality.
"""

from pathlib import Path

import pytest

from core.cv_selector import CVSelector, CVSelection
from core.models import JobListing, Market, ApplicationSource, RoleTier

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG_PATH = PROJECT_ROOT / "config" / "cvs.json"


def _job(market: Market, **overrides) -> JobListing:
    defaults = dict(
        id="test-job", title="Test Engineer", company="Test Corp",
        city="Warsaw", market=market, url="https://example.com/1",
        source=ApplicationSource.NOFLUFFJOBS,
    )
    defaults.update(overrides)
    return JobListing(**defaults)


@pytest.fixture
def selector():
    """Selector against the REAL config and REAL project root — proves it works against actual files."""
    return CVSelector(config_path=REAL_CONFIG_PATH, project_root=PROJECT_ROOT)


class TestCVSelectorAgainstRealFiles:
    """These tests hit the actual resumes/ files on disk — no mocking, real proof."""

    def test_poland_job_selects_europe_cv(self, selector):
        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert result.cv_id == "europe"
        assert result.path is not None
        assert result.path.exists()
        assert result.path.name == "Badis_Moalla.pdf"

    def test_netherlands_job_selects_europe_cv(self, selector):
        result = selector.select(_job(Market.NETHERLANDS), tier=RoleTier.PRIMARY)
        assert result.cv_id == "europe"

    def test_luxembourg_job_selects_europe_cv(self, selector):
        result = selector.select(_job(Market.LUXEMBOURG), tier=RoleTier.PRIMARY)
        assert result.cv_id == "europe"

    def test_uae_job_selects_gcc_cv(self, selector):
        result = selector.select(_job(Market.UAE), tier=RoleTier.PRIMARY)
        assert result.cv_id == "gcc"
        assert result.path.exists()
        assert result.path.name == "Badis_Moalla CV.pdf"

    def test_saudi_arabia_job_selects_gcc_cv(self, selector):
        result = selector.select(_job(Market.SAUDI_ARABIA), tier=RoleTier.PRIMARY)
        assert result.cv_id == "gcc"

    def test_qatar_job_selects_gcc_cv(self, selector):
        result = selector.select(_job(Market.QATAR), tier=RoleTier.PRIMARY)
        assert result.cv_id == "gcc"

    def test_secondary_tier_job_still_gets_a_cv(self, selector):
        """Both real CVs currently cover both role tiers — a Data/BI-tier job in Poland still gets the Europe CV."""
        result = selector.select(_job(Market.POLAND), tier=RoleTier.SECONDARY)
        assert result.cv_id == "europe"

    def test_selection_result_includes_a_reason(self, selector):
        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert result.reason is not None
        assert "poland" in result.reason.lower() or "europe" in result.reason.lower()

    def test_selector_never_invents_a_path_not_in_config(self, selector):
        """The returned path must always be exactly one of the two configured, real files."""
        real_paths = {
            PROJECT_ROOT / "resumes" / "Europe" / "Badis_Moalla.pdf",
            PROJECT_ROOT / "resumes" / "GCC" / "Badis_Moalla CV.pdf",
        }
        for market in Market:
            result = selector.select(_job(market), tier=RoleTier.PRIMARY)
            if result.path is not None:
                assert result.path in real_paths


class TestCVSelectorExcludedTier:

    def test_excluded_tier_returns_no_cv(self, selector):
        result = selector.select(_job(Market.POLAND), tier=RoleTier.EXCLUDED)
        assert result.cv_id is None
        assert result.path is None
        assert result.review_required is True


class TestCVSelectorMissingFile:
    """Uses an isolated tmp config pointing at a file that doesn't exist — must fail safely, not fabricate."""

    def test_missing_cv_file_on_disk_returns_review_required(self, tmp_path):
        config = tmp_path / "cvs.json"
        config.write_text('{"cvs": [{"id": "ghost", "path": "resumes/DoesNotExist.pdf", '
                           '"markets": ["Poland"], "role_tiers": ["primary", "secondary"], "priority": 1}]}')
        selector = CVSelector(config_path=config, project_root=tmp_path)

        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert result.cv_id is None
        assert result.path is None
        assert result.review_required is True
        assert "does not exist" in result.reason.lower() or "not found" in result.reason.lower()

    def test_missing_cv_file_never_returns_a_fake_path(self, tmp_path):
        config = tmp_path / "cvs.json"
        config.write_text('{"cvs": [{"id": "ghost", "path": "resumes/DoesNotExist.pdf", '
                           '"markets": ["Poland"], "role_tiers": ["primary", "secondary"], "priority": 1}]}')
        selector = CVSelector(config_path=config, project_root=tmp_path)

        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert result.path is None  # never a fabricated Path object either


class TestCVSelectorNoMarketMatch:

    def test_market_not_in_any_cv_config_returns_review_required(self, tmp_path):
        cv_file = tmp_path / "resumes" / "Europe.pdf"
        cv_file.parent.mkdir(parents=True)
        cv_file.write_text("%PDF-1.0")
        config = tmp_path / "cvs.json"
        config.write_text('{"cvs": [{"id": "europe-only", "path": "resumes/Europe.pdf", '
                           '"markets": ["Poland"], "role_tiers": ["primary", "secondary"], "priority": 1}]}')
        selector = CVSelector(config_path=config, project_root=tmp_path)

        result = selector.select(_job(Market.UAE), tier=RoleTier.PRIMARY)
        assert result.cv_id is None
        assert result.review_required is True


class TestCVSelectorDeterminism:

    def test_same_input_always_gives_same_result(self, selector):
        results = [selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY) for _ in range(5)]
        cv_ids = {r.cv_id for r in results}
        assert len(cv_ids) == 1

    def test_deterministic_priority_ordering_when_multiple_candidates_match(self, tmp_path):
        """If two CVs both match a market, priority (then id) decides — not arbitrary dict ordering."""
        for name in ("a.pdf", "b.pdf"):
            f = tmp_path / "resumes" / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("%PDF-1.0")
        config = tmp_path / "cvs.json"
        config.write_text(
            '{"cvs": ['
            '{"id": "low_priority", "path": "resumes/a.pdf", "markets": ["Poland"], '
            '"role_tiers": ["primary", "secondary"], "priority": 5},'
            '{"id": "high_priority", "path": "resumes/b.pdf", "markets": ["Poland"], '
            '"role_tiers": ["primary", "secondary"], "priority": 1}'
            ']}'
        )
        selector = CVSelector(config_path=config, project_root=tmp_path)
        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert result.cv_id == "high_priority"


class TestCVSelectionResultType:

    def test_cv_selection_is_correct_dataclass_shape(self, selector):
        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert isinstance(result, CVSelection)
        assert hasattr(result, "cv_id")
        assert hasattr(result, "path")
        assert hasattr(result, "reason")
        assert hasattr(result, "review_required")

    def test_successful_selection_has_review_required_false(self, selector):
        result = selector.select(_job(Market.POLAND), tier=RoleTier.PRIMARY)
        assert result.review_required is False
