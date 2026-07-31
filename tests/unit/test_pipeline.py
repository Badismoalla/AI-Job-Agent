"""
Unit tests for core.pipeline — the full application pipeline.

No test in this file makes a real network call, calls a real AI API, or
depends on config/company_sources.json's live-network reachability. Every
external dependency is injected:

- Scrapers: fake in-memory BaseJobScraper/CompanyJobScraper subclasses
  (see FakeJobBoardScraper, FakeFailingScraper, FakeCompanyScraper below)
  registered via run_pipeline()'s job_board_scrapers/company_ats_scrapers/
  company_sources parameters — never the real NoFluffJobs/Greenhouse/etc.
- AI generation: relies on the project's own APP_DRY_RUN=True default
  (see tests/conftest.py), so ClaudeGenerator never calls the real
  Anthropic API; one test also injects a fully fake generator for
  fine-grained control over per-job failure.
- Storage: a real ApplicationTracker backed by a pytest tmp_path file
  (matching the existing tests/unit/test_tracker.py convention) — this is
  fast, in-process, and exercises real TinyDB behaviour without any
  network or shared state between tests.

Organised by pipeline stage, matching core/pipeline.py's own structure:
    TestDeduplication / TestTitleNormalization  -> deduplicate_jobs()
    TestBuildScraperInstances                   -> _build_scraper_instances()
    TestRunOneScraper / TestScrapeAll            -> scraping phase
    TestMatchAndFilter                          -> matching phase
    TestGenerateAndStore                        -> generation + storage phase
    TestRunPipelineIntegration                  -> the whole thing together
"""

from typing import Any

import pytest
from rich.console import Console

from core.exceptions import DuplicateApplicationError
from core.models import (
    Application,
    ApplicationSource,
    ApplicationStatus,
    GeneratedMessage,
    JobListing,
    Market,
    MessageType,
)
from core.pipeline import (
    PipelineResult,
    ScraperRunError,
    _build_scraper_instances,
    _filter_accepted,
    _generate_and_store,
    _match_all,
    _run_one_scraper,
    _scrape_all,
    deduplicate_jobs,
    run_pipeline,
)
from modules.ai.claude_generator import ClaudeGenerator
from modules.scraper.base import BaseJobScraper, PageResult
from modules.scraper.company.base import CompanyJobScraper
from modules.tracker.tracker import ApplicationTracker


# ── Shared fixtures / fakes ──────────────────────────────────────────────────

def make_job(
    id: str,
    title: str = "Senior Test Engineer",
    company: str = "TestCo",
    city: str = "Wroclaw",
    url: str | None = None,
) -> JobListing:
    return JobListing(
        id=id,
        title=title,
        company=company,
        city=city,
        market=Market.POLAND,
        url=url or f"https://example.com/jobs/{id}",
        source=ApplicationSource.NOFLUFFJOBS,
        description="Automotive embedded ECU AUTOSAR UDS DoIP testing validation",
    )


class FakeJobBoardScraper(BaseJobScraper):
    """A job-board-style scraper returning a canned, fixed set of jobs."""

    board_name = "fake_board"
    jobs: list[JobListing] = []

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        return self.jobs if page == 1 else []

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        return PageResult(listings=raw_page, has_next_page=False)


def make_fake_job_board(jobs: list[JobListing]) -> type[FakeJobBoardScraper]:
    """Build a fresh FakeJobBoardScraper subclass bound to a fixed job list,
    so it can be registered as a zero-arg-constructible 'class' in the
    scraper registry, same as the real scrapers."""
    return type("BoundFakeJobBoardScraper", (FakeJobBoardScraper,), {"jobs": jobs})


class FakeFailingScraper(BaseJobScraper):
    """A scraper whose first fetch always raises — used to test that one
    failing source doesn't abort the rest of the pipeline."""

    board_name = "fake_failing"

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        raise RuntimeError("simulated scraper failure")

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        return PageResult(listings=[], has_next_page=False)


class FakeCompanyScraper(CompanyJobScraper):
    """
    A company-ATS-style scraper returning whatever jobs are stashed under
    company["_test_jobs"] — keeps test data flowing through the normal
    company-config dict rather than needing shared class state.
    """

    platform_name = "fake_platform"

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        return self.company.get("_test_jobs", []) if page == 1 else []

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        return PageResult(listings=raw_page, has_next_page=False)


class FakeGenerator:
    """
    Minimal stand-in for ClaudeGenerator — implements only the two methods
    _generate_and_store() actually calls. `fail_for_job_ids` lets a test
    make generation fail for specific jobs while succeeding for others.
    """

    def __init__(self, fail_for_job_ids: set[str] | None = None):
        self.fail_for_job_ids = fail_for_job_ids or set()
        self.calls: list[str] = []

    async def generate_cover_letter(self, job, match_report=None, **kwargs):
        self.calls.append(job.id)
        if job.id in self.fail_for_job_ids:
            raise RuntimeError(f"simulated generation failure for {job.id}")
        return GeneratedMessage(type=MessageType.COVER_LETTER, body=f"Cover letter for {job.title}")

    async def generate_recruiter_message(self, job, recruiter_name, match_report=None, **kwargs):
        return GeneratedMessage(type=MessageType.RECRUITER_INMAIL, body=f"InMail for {job.title}")


@pytest.fixture
def console() -> Console:
    """A Console that writes to an in-memory buffer — no real terminal needed."""
    return Console(file=open("/dev/null", "w"), force_terminal=False)


@pytest.fixture
def tracker(tmp_path) -> ApplicationTracker:
    db_path = tmp_path / "test_pipeline.db.json"
    with ApplicationTracker(db_path) as t:
        yield t


# ── deduplicate_jobs() ───────────────────────────────────────────────────────

class TestDeduplication:

    def test_exact_url_duplicate_removed(self):
        jobs = [make_job("a", url="https://x.com/1"), make_job("b", url="https://x.com/1")]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 1
        assert removed == 1

    def test_url_normalisation_trailing_slash_and_case(self):
        jobs = [make_job("a", url="https://X.com/Job/1"), make_job("b", url="https://x.com/job/1/")]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 1
        assert removed == 1

    def test_similar_title_same_company_city_is_duplicate(self):
        jobs = [
            make_job("a", title="Senior Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/1"),
            make_job("b", title="Senior Test Engineer (m/f/d)", company="Bosch", city="Wroclaw", url="https://y.com/2"),
        ]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 1
        assert removed == 1

    def test_different_company_not_merged(self):
        jobs = [
            make_job("a", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/1"),
            make_job("b", title="Test Engineer", company="Continental", city="Wroclaw", url="https://y.com/2"),
        ]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 2
        assert removed == 0

    def test_different_city_not_merged(self):
        jobs = [
            make_job("a", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/1"),
            make_job("b", title="Test Engineer", company="Bosch", city="Krakow", url="https://y.com/2"),
        ]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 2
        assert removed == 0

    def test_dissimilar_titles_same_company_city_not_merged(self):
        jobs = [
            make_job("a", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/1"),
            make_job("b", title="Data Analyst", company="Bosch", city="Wroclaw", url="https://y.com/2"),
        ]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 2
        assert removed == 0

    def test_empty_list(self):
        kept, removed = deduplicate_jobs([])
        assert kept == []
        assert removed == 0

    def test_no_duplicates_returns_everything(self):
        jobs = [
            make_job(str(i), title=f"Role {i}", company=f"Company {i}", url=f"https://x.com/{i}")
            for i in range(5)
        ]
        kept, removed = deduplicate_jobs(jobs)
        assert len(kept) == 5
        assert removed == 0

    def test_first_occurrence_wins(self):
        first = make_job("a", url="https://x.com/1", title="Original Title")
        second = make_job("b", url="https://x.com/1", title="Different Title")
        kept, _ = deduplicate_jobs([first, second])
        assert kept[0].id == "a"

    def test_custom_similarity_threshold(self):
        jobs = [
            make_job("a", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/1"),
            make_job("b", title="Senior Test Engineer", company="Bosch", city="Wroclaw", url="https://y.com/2"),
        ]
        # Loose threshold merges them; strict threshold doesn't.
        kept_loose, removed_loose = deduplicate_jobs(jobs, title_similarity_threshold=0.5)
        kept_strict, removed_strict = deduplicate_jobs(jobs, title_similarity_threshold=0.99)
        assert removed_loose == 1
        assert removed_strict == 0


# ── Title normalisation (gender-inclusive suffix stripping) ────────────────

class TestTitleNormalization:

    @pytest.mark.parametrize("suffix", ["(m/f/d)", "(m/w/d)", "(f/m/d)", "(w/m/d)", "( m / f / d )"])
    def test_gender_suffix_variants_stripped(self, suffix):
        jobs = [
            make_job("a", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/1"),
            make_job("b", title=f"Test Engineer {suffix}", company="Bosch", city="Wroclaw", url="https://y.com/2"),
        ]
        kept, removed = deduplicate_jobs(jobs)
        assert removed == 1


# ── _build_scraper_instances() ──────────────────────────────────────────────

class TestBuildScraperInstances:

    def test_job_board_key_produces_one_instance(self):
        board_cls = make_fake_job_board([])
        instances = _build_scraper_instances(
            enabled=["fake_board"],
            job_board_scrapers={"fake_board": board_cls},
            company_ats_scrapers={},
            company_sources={},
        )
        assert len(instances) == 1
        assert instances[0][0] == "fake_board"

    def test_company_platform_produces_one_instance_per_company(self):
        instances = _build_scraper_instances(
            enabled=["fake_platform"],
            job_board_scrapers={},
            company_ats_scrapers={"fake_platform": FakeCompanyScraper},
            company_sources={"fake_platform": [{"name": "Acme"}, {"name": "Beta"}]},
        )
        assert len(instances) == 2
        labels = {label for label, _ in instances}
        assert labels == {"fake_platform.Acme", "fake_platform.Beta"}

    def test_platform_with_no_configured_companies_produces_nothing(self):
        instances = _build_scraper_instances(
            enabled=["fake_platform"],
            job_board_scrapers={},
            company_ats_scrapers={"fake_platform": FakeCompanyScraper},
            company_sources={},
        )
        assert instances == []

    def test_unknown_key_skipped_without_raising(self):
        instances = _build_scraper_instances(
            enabled=["totally_unknown_scraper"],
            job_board_scrapers={},
            company_ats_scrapers={},
            company_sources={},
        )
        assert instances == []

    def test_mixed_job_board_and_company_platforms(self):
        board_cls = make_fake_job_board([])
        instances = _build_scraper_instances(
            enabled=["fake_board", "fake_platform"],
            job_board_scrapers={"fake_board": board_cls},
            company_ats_scrapers={"fake_platform": FakeCompanyScraper},
            company_sources={"fake_platform": [{"name": "Acme"}]},
        )
        assert len(instances) == 2


# ── _run_one_scraper() ───────────────────────────────────────────────────────

class TestRunOneScraper:

    @pytest.mark.asyncio
    async def test_successful_scraper_returns_listings(self):
        jobs = [make_job("a")]
        scraper = make_fake_job_board(jobs)()
        label, listings, error = await _run_one_scraper("fake_board", scraper, [], [])
        assert label == "fake_board"
        assert listings == jobs
        assert error is None

    @pytest.mark.asyncio
    async def test_failing_scraper_returns_error_not_raises(self):
        scraper = FakeFailingScraper()
        label, listings, error = await _run_one_scraper("fake_failing", scraper, [], [])
        assert label == "fake_failing"
        assert listings is None
        assert "simulated scraper failure" in error


# ── _scrape_all() ────────────────────────────────────────────────────────────

class TestScrapeAll:

    @pytest.mark.asyncio
    async def test_collects_jobs_from_all_successful_scrapers(self, console):
        jobs_a = [make_job("a1"), make_job("a2")]
        jobs_b = [make_job("b1")]
        instances = [
            ("board_a", make_fake_job_board(jobs_a)()),
            ("board_b", make_fake_job_board(jobs_b)()),
        ]
        all_jobs, errors = await _scrape_all(instances, [], [], console)
        assert len(all_jobs) == 3
        assert errors == []

    @pytest.mark.asyncio
    async def test_one_failing_scraper_does_not_abort_others(self, console):
        jobs_a = [make_job("a1")]
        instances = [
            ("board_a", make_fake_job_board(jobs_a)()),
            ("failing", FakeFailingScraper()),
        ]
        all_jobs, errors = await _scrape_all(instances, [], [], console)
        assert len(all_jobs) == 1
        assert len(errors) == 1
        assert errors[0] == ScraperRunError(source="failing", error="simulated scraper failure")

    @pytest.mark.asyncio
    async def test_empty_instances_returns_empty(self, console):
        all_jobs, errors = await _scrape_all([], [], [], console)
        assert all_jobs == []
        assert errors == []

    @pytest.mark.asyncio
    async def test_all_scrapers_failing_returns_no_jobs_and_all_errors(self, console):
        instances = [("f1", FakeFailingScraper()), ("f2", FakeFailingScraper())]
        all_jobs, errors = await _scrape_all(instances, [], [], console)
        assert all_jobs == []
        assert len(errors) == 2


# ── Matching phase ───────────────────────────────────────────────────────────

class TestMatchAndFilter:

    def test_match_all_annotates_jobs_with_score_and_gaps(self, console):
        job = make_job("a", title="Senior Test Engineer")
        matched = _match_all([job], console)
        assert len(matched) == 1
        result_job, report = matched[0]
        assert result_job.match_score == report.score
        assert result_job.match_gaps == report.skill_gaps

    def test_match_all_empty_list(self, console):
        assert _match_all([], console) == []

    def test_filter_accepted_keeps_only_above_threshold(self):
        from core.models import MatchDecision, MatchReport, RoleTier

        def report(score):
            return MatchReport(
                job_id="x", job_title="x", company="x", tier=RoleTier.PRIMARY,
                decision=MatchDecision.APPLY, score=score, reason="test",
            )

        job_high = make_job("high")
        job_low = make_job("low")
        matched = [(job_high, report(85)), (job_low, report(30))]

        accepted = _filter_accepted(matched, threshold=70)
        assert [j.id for j, _ in accepted] == ["high"]

    def test_filter_accepted_boundary_is_inclusive(self):
        from core.models import MatchDecision, MatchReport, RoleTier

        job = make_job("boundary")
        report = MatchReport(
            job_id="x", job_title="x", company="x", tier=RoleTier.PRIMARY,
            decision=MatchDecision.APPLY, score=70, reason="test",
        )
        accepted = _filter_accepted([(job, report)], threshold=70)
        assert len(accepted) == 1


# ── Generation + storage phase ──────────────────────────────────────────────

class TestGenerateAndStore:

    def _report(self, job_id: str, score: int = 90):
        from core.models import MatchDecision, MatchReport, RoleTier
        return MatchReport(
            job_id=job_id, job_title="x", company="x", tier=RoleTier.PRIMARY,
            decision=MatchDecision.APPLY, score=score, reason="test",
        )

    @pytest.mark.asyncio
    async def test_creates_and_stores_application_for_accepted_job(self, tracker, console):
        job = make_job("a")
        generator = FakeGenerator()
        applications, skipped = await _generate_and_store(
            [(job, self._report(job.id))], tracker, generator, console,
        )
        assert len(applications) == 1
        assert applications[0].job.id == "a"
        assert len(applications[0].messages) == 2
        assert skipped == 0
        assert tracker.already_applied("a") is True

    @pytest.mark.asyncio
    async def test_already_applied_job_is_skipped_not_regenerated(self, tracker, console):
        job = make_job("a")
        existing = Application(id="app-existing", job=job, status=ApplicationStatus.SENT)
        tracker.add_application(existing)

        generator = FakeGenerator()
        applications, skipped = await _generate_and_store(
            [(job, self._report(job.id))], tracker, generator, console,
        )
        assert applications == []
        assert skipped == 1
        assert generator.calls == []  # never even attempted generation

    @pytest.mark.asyncio
    async def test_generation_failure_for_one_job_does_not_block_others(self, tracker, console):
        job_bad = make_job("bad")
        job_good = make_job("good")
        generator = FakeGenerator(fail_for_job_ids={"bad"})

        applications, skipped = await _generate_and_store(
            [(job_bad, self._report("bad")), (job_good, self._report("good"))],
            tracker, generator, console,
        )
        assert len(applications) == 1
        assert applications[0].job.id == "good"

    @pytest.mark.asyncio
    async def test_empty_accepted_list(self, tracker, console):
        applications, skipped = await _generate_and_store([], tracker, FakeGenerator(), console)
        assert applications == []
        assert skipped == 0

    @pytest.mark.asyncio
    async def test_real_dry_run_claude_generator_produces_valid_messages(self, tracker, console):
        """Integration point with the real generator, relying on APP_DRY_RUN=True
        (set globally in tests/conftest.py) — no real API call happens."""
        job = make_job("a")
        generator = ClaudeGenerator()
        applications, skipped = await _generate_and_store(
            [(job, self._report(job.id))], tracker, generator, console,
        )
        assert len(applications) == 1
        assert all(isinstance(m, GeneratedMessage) for m in applications[0].messages)


# ── Full run_pipeline() integration ─────────────────────────────────────────

class TestRunPipelineIntegration:

    @pytest.mark.asyncio
    async def test_full_pipeline_end_to_end(self, tracker, console):
        strong_job = make_job(
            "strong", title="Senior Software Test Engineer",
            company="Bosch", city="Wroclaw", url="https://board-a.com/strong",
        )
        weak_job = make_job(
            "weak", title="Marketing Intern",
            company="Random Co", city="Berlin", url="https://board-a.com/weak",
        )

        result = await run_pipeline(
            roles=["test engineer"],
            cities=["wroclaw"],
            score_threshold=50,
            enabled_scrapers=["fake_board"],
            tracker=tracker,
            generator=ClaudeGenerator(),  # dry-run, per conftest
            console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([strong_job, weak_job])},
            company_ats_scrapers={},
            company_sources={},
        )

        assert isinstance(result, PipelineResult)
        assert len(result.scraped) == 2
        assert len(result.deduplicated) == 2
        assert result.duplicates_removed == 0
        assert result.finished_at is not None
        assert result.finished_at >= result.started_at
        # Only the strong automotive-domain match should clear a 50 threshold;
        # a marketing role should score far below it.
        accepted_ids = {j.id for j in result.accepted}
        assert "strong" in accepted_ids

    @pytest.mark.asyncio
    async def test_pipeline_continues_when_one_scraper_fails(self, tracker, console):
        good_job = make_job("good", title="Senior Test Engineer", url="https://x.com/good")

        result = await run_pipeline(
            roles=["test engineer"],
            cities=[],
            score_threshold=0,  # accept everything for this test
            enabled_scrapers=["fake_board", "fake_failing"],
            tracker=tracker,
            generator=ClaudeGenerator(),
            console=console,
            job_board_scrapers={
                "fake_board": make_fake_job_board([good_job]),
                "fake_failing": FakeFailingScraper,
            },
            company_ats_scrapers={},
            company_sources={},
        )

        assert len(result.errors) == 1
        assert result.errors[0].source == "fake_failing"
        assert any(j.id == "good" for j in result.scraped)

    @pytest.mark.asyncio
    async def test_pipeline_with_company_ats_scrapers(self, tracker, console):
        company_job = make_job(
            "co1", title="Senior Test Engineer", company="Acme", url="https://acme.example/co1",
        )

        result = await run_pipeline(
            roles=["test engineer"],
            cities=[],
            score_threshold=0,
            enabled_scrapers=["fake_platform"],
            tracker=tracker,
            generator=ClaudeGenerator(),
            console=console,
            job_board_scrapers={},
            company_ats_scrapers={"fake_platform": FakeCompanyScraper},
            company_sources={"fake_platform": [{"name": "Acme", "_test_jobs": [company_job]}]},
        )

        assert len(result.scraped) == 1
        assert result.scraped[0].id == "co1"

    @pytest.mark.asyncio
    async def test_no_jobs_clear_threshold_produces_no_applications(self, tracker, console):
        weak_job = make_job("weak", title="Marketing Intern", company="Random Co", city="Berlin")

        result = await run_pipeline(
            roles=["test engineer"],
            cities=[],
            score_threshold=95,
            enabled_scrapers=["fake_board"],
            tracker=tracker,
            generator=ClaudeGenerator(),
            console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([weak_job])},
            company_ats_scrapers={},
            company_sources={},
        )
        assert result.applications_created == []

    @pytest.mark.asyncio
    async def test_default_roles_and_cities_come_from_profile_when_not_given(self, tracker, console):
        """Passing no roles/cities should not raise — falls back to profile."""
        result = await run_pipeline(
            score_threshold=0,
            enabled_scrapers=["fake_board"],
            tracker=tracker,
            generator=ClaudeGenerator(),
            console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([make_job("a")])},
            company_ats_scrapers={},
            company_sources={},
        )
        assert isinstance(result, PipelineResult)

    @pytest.mark.asyncio
    async def test_pipeline_marks_all_scraped_jobs_seen(self, tracker, console):
        job = make_job("seen-me")
        await run_pipeline(
            roles=["test engineer"], cities=[], score_threshold=0,
            enabled_scrapers=["fake_board"], tracker=tracker, generator=ClaudeGenerator(), console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([job])},
            company_ats_scrapers={}, company_sources={},
        )
        assert tracker.is_job_seen("seen-me") is True

    @pytest.mark.asyncio
    async def test_pipeline_deduplicates_before_matching(self, tracker, console):
        dup1 = make_job("d1", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/same")
        dup2 = make_job("d2", title="Test Engineer", company="Bosch", city="Wroclaw", url="https://x.com/same")

        result = await run_pipeline(
            roles=["test engineer"], cities=[], score_threshold=0,
            enabled_scrapers=["fake_board"], tracker=tracker, generator=ClaudeGenerator(), console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([dup1, dup2])},
            company_ats_scrapers={}, company_sources={},
        )
        assert len(result.scraped) == 2
        assert len(result.deduplicated) == 1
        assert result.duplicates_removed == 1
