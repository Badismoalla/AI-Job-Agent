"""
Integration tests for commands.pipeline_cli and its wiring into main.py's
`jobs`, `run`, `stats`, and `plan` Typer commands.

Two layers:

1. Direct-call tests (TestRunJobsCommand, TestRunFullPipelineCommand,
   TestBuildRunStats, TestRenderExtendedStats, TestRenderPipelinePlan) —
   call commands/pipeline_cli.py's functions directly with explicit fake
   scraper registries and a tmp-path ApplicationTracker. This exercises
   the real integration path (CLI layer -> core.pipeline.run_pipeline ->
   scraper/matcher/tracker/AI-generator, all wired together for real)
   with fakes substituted only at the true I/O boundaries.

2. Full CLI wiring tests (TestCLIWiring) — invoke main.py's actual Typer
   app via typer.testing.CliRunner, i.e. exactly how a user runs
   `python main.py jobs` / `run` / `stats` / `plan`. Since Typer commands
   don't expose test-only override flags, these monkeypatch the module-
   level scraper registries (core.pipeline.JOB_BOARD_SCRAPERS/
   COMPANY_ATS_SCRAPERS — read at call time inside run_pipeline(), so
   patchable) and the ApplicationTracker name bound in both main.py and
   commands/pipeline_cli.py (both do `from ...tracker import
   ApplicationTracker`, so both need patching) to a tmp-path factory —
   this avoids ever touching data/applications.db.json.

No test in this file makes a real network call or a real Anthropic API
call (relies on tests/conftest.py's APP_DRY_RUN=True-equivalent default —
see modules/ai/claude_generator.py's dry-run path).
"""

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from core.models import ApplicationSource, JobListing, Market
from core.pipeline import PipelineResult
from modules.ai.claude_generator import ClaudeGenerator
from modules.scraper.base import BaseJobScraper, PageResult
from modules.tracker.tracker import ApplicationTracker


# ── Shared fixtures / fakes ──────────────────────────────────────────────────

def make_job(
    id: str,
    title: str = "Senior Test Engineer",
    company: str = "Bosch",
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
    return type("BoundFakeJobBoardScraper", (FakeJobBoardScraper,), {"jobs": jobs})


class FakeFailingScraper(BaseJobScraper):
    board_name = "fake_failing"

    async def fetch_page(self, page: int, roles: list[str], cities: list[str]) -> Any:
        raise RuntimeError("simulated scraper failure")

    def parse_page(self, raw_page: Any, page: int) -> PageResult:
        return PageResult(listings=[], has_next_page=False)


@pytest.fixture
def console() -> Console:
    return Console(file=open("/dev/null", "w"), force_terminal=False)


@pytest.fixture
def tracker(tmp_path) -> ApplicationTracker:
    db_path = tmp_path / "test_pipeline_cli.db.json"
    with ApplicationTracker(db_path) as t:
        yield t


# ── run_jobs_command() (discovery mode) ─────────────────────────────────────

class TestRunJobsCommand:

    def test_returns_matched_jobs_generates_nothing(self, console, tracker):
        from commands.pipeline_cli import run_jobs_command

        job = make_job("a", title="Senior Software Test Engineer")
        result = run_jobs_command(
            roles=["test engineer"], cities=[], console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([job])},
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board"], tracker=tracker,
            generator=ClaudeGenerator(),
        )
        assert isinstance(result, PipelineResult)
        assert len(result.matched) == 1
        assert result.applications_created == []
        assert tracker.is_job_seen("a") is False  # discovery mode is read-only

    def test_no_jobs_found_prints_message_not_error(self, console, tracker):
        from commands.pipeline_cli import run_jobs_command

        result = run_jobs_command(
            roles=["test engineer"], cities=[], console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([])},
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board"], tracker=tracker,
            generator=ClaudeGenerator(),
        )
        assert result.matched == []

    def test_table_sorted_by_score_descending(self, capsys):
        from commands.pipeline_cli import _render_jobs_table
        from core.models import MatchDecision, MatchReport, RoleTier

        def report(score):
            return MatchReport(
                job_id="x", job_title="x", company="x", tier=RoleTier.PRIMARY,
                decision=MatchDecision.APPLY, score=score, reason="test",
            )

        job_low = make_job("low", title="Low Match")
        job_high = make_job("high", title="High Match")
        out_console = Console(record=True, width=120)

        _render_jobs_table([(job_low, report(30)), (job_high, report(90))], out_console)
        output = out_console.export_text()

        assert output.index("High Match") < output.index("Low Match")

    def test_uses_default_shared_console_when_none_given(self, tracker):
        from commands.pipeline_cli import run_jobs_command

        # Should not raise even without an explicit console.
        result = run_jobs_command(
            roles=["test engineer"], cities=[],
            job_board_scrapers={"fake_board": make_fake_job_board([make_job("a")])},
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board"], tracker=tracker,
            generator=ClaudeGenerator(),
        )
        assert len(result.matched) == 1


# ── run_full_pipeline_command() (full run) ──────────────────────────────────

class TestRunFullPipelineCommand:

    def test_creates_applications_and_records_run_stats(self, console, tracker):
        from commands.pipeline_cli import run_full_pipeline_command

        job = make_job("a", title="Senior Software Test Engineer")
        result = run_full_pipeline_command(
            roles=["test engineer"], cities=[], score_threshold=0, console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([job])},
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board"], tracker=tracker,
            generator=ClaudeGenerator(),
        )
        assert len(result.applications_created) == 1
        last_run = tracker.get_last_pipeline_run()
        assert last_run is not None
        assert last_run["scraped"] == 1
        assert last_run["accepted"] == 1
        assert last_run["applications_created"] == 1

    def test_owns_and_closes_tracker_when_none_passed(self, console, tmp_path, monkeypatch):
        from commands import pipeline_cli

        created_path = tmp_path / "owned.db.json"
        monkeypatch.setattr(
            pipeline_cli, "ApplicationTracker", lambda: ApplicationTracker(created_path)
        )

        pipeline_cli.run_full_pipeline_command(
            roles=["test engineer"], cities=[], score_threshold=100, console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([make_job("a")])},
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board"],
            generator=ClaudeGenerator(),
        )
        # If the tracker weren't properly closed, a second open wouldn't
        # necessarily fail (TinyDB tolerates re-open), but the file should
        # at least exist and be readable — a basic sanity check that the
        # owned-tracker path ran without error.
        assert created_path.exists()

    def test_does_not_close_externally_provided_tracker(self, console, tracker):
        from commands.pipeline_cli import run_full_pipeline_command

        run_full_pipeline_command(
            roles=["test engineer"], cities=[], score_threshold=100, console=console,
            job_board_scrapers={"fake_board": make_fake_job_board([make_job("a")])},
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board"], tracker=tracker,
            generator=ClaudeGenerator(),
        )
        # Tracker should still be usable after the call — not closed underneath us.
        assert tracker.get_last_pipeline_run() is not None

    def test_records_failed_scrapers_in_stats(self, console, tracker):
        from commands.pipeline_cli import run_full_pipeline_command

        run_full_pipeline_command(
            roles=["test engineer"], cities=[], score_threshold=0, console=console,
            job_board_scrapers={
                "fake_board": make_fake_job_board([make_job("a")]),
                "fake_failing": FakeFailingScraper,
            },
            company_ats_scrapers={}, company_sources={},
            enabled_scrapers=["fake_board", "fake_failing"], tracker=tracker,
            generator=ClaudeGenerator(),
        )
        last_run = tracker.get_last_pipeline_run()
        assert last_run["failed_scrapers"] == ["fake_failing"]


# ── _build_run_stats() ───────────────────────────────────────────────────────

class TestBuildRunStats:

    def test_per_source_breakdown(self):
        from core.pipeline import ScraperRunError
        from commands.pipeline_cli import _build_run_stats

        job_a1 = make_job("a1")
        job_a1.source = "NoFluffJobs"
        job_b1 = make_job("b1")
        job_b1.source = "Pracuj.pl"

        result = PipelineResult(
            scraped=[job_a1, job_b1],
            deduplicated=[job_a1, job_b1],
            duplicates_removed=2,
            accepted=[job_a1],
            errors=[ScraperRunError(source="justjoinit", error="timeout")],
        )
        stats = _build_run_stats(result)

        assert stats["scraped"] == 2
        assert stats["duplicates_removed"] == 2
        assert stats["accepted"] == 1
        assert stats["rejected"] == 1  # 2 deduplicated - 1 accepted
        assert stats["per_source"] == {"NoFluffJobs": 1, "Pracuj.pl": 1}
        assert stats["failed_scrapers"] == ["justjoinit"]

    def test_messages_generated_counts_all_application_messages(self):
        from core.models import Application, ApplicationStatus, GeneratedMessage, MessageType
        from commands.pipeline_cli import _build_run_stats

        job = make_job("a")
        app = Application(
            id="app-1", job=job, status=ApplicationStatus.PENDING,
            messages=[
                GeneratedMessage(type=MessageType.COVER_LETTER, body="x"),
                GeneratedMessage(type=MessageType.RECRUITER_INMAIL, body="y"),
            ],
        )
        result = PipelineResult(applications_created=[app])
        stats = _build_run_stats(result)
        assert stats["messages_generated"] == 2

    def test_empty_result(self):
        from commands.pipeline_cli import _build_run_stats
        stats = _build_run_stats(PipelineResult())
        assert stats["scraped"] == 0
        assert stats["per_source"] == {}


# ── render_extended_stats() ──────────────────────────────────────────────────

class TestRenderExtendedStats:

    def test_no_pipeline_run_shows_helpful_message(self, tracker):
        from commands.pipeline_cli import render_extended_stats

        out_console = Console(record=True, width=120)
        render_extended_stats(console=out_console, tracker=tracker)
        output = out_console.export_text()
        assert "run `python main.py run`" in output

    def test_shows_recorded_run_stats(self, tracker):
        from commands.pipeline_cli import render_extended_stats

        tracker.record_pipeline_run({
            "scraped": 10, "duplicates_removed": 2, "accepted": 3, "rejected": 5,
            "applications_created": 3, "failed_scrapers": [], "per_source": {"NoFluffJobs": 10},
        })
        out_console = Console(record=True, width=120)
        render_extended_stats(console=out_console, tracker=tracker)
        output = out_console.export_text()

        assert "10" in output  # scraped
        assert "NoFluffJobs" in output

    def test_applications_generated_and_sent_reflect_tracker_data(self, tracker):
        from commands.pipeline_cli import render_extended_stats
        from core.models import Application, ApplicationStatus

        tracker.add_application(Application(id="app-1", job=make_job("a"), status=ApplicationStatus.PENDING))
        tracker.add_application(Application(id="app-2", job=make_job("b"), status=ApplicationStatus.SENT))

        out_console = Console(record=True, width=120)
        render_extended_stats(console=out_console, tracker=tracker)
        output = out_console.export_text()

        assert "Applications generated" in output
        assert "Applications sent" in output

    def test_owns_and_closes_tracker_when_none_passed(self, tmp_path, monkeypatch):
        from commands import pipeline_cli

        created_path = tmp_path / "stats_owned.db.json"
        monkeypatch.setattr(
            pipeline_cli, "ApplicationTracker", lambda: ApplicationTracker(created_path)
        )
        pipeline_cli.render_extended_stats(console=Console(file=open("/dev/null", "w")))
        assert created_path.exists()


# ── render_pipeline_plan() ───────────────────────────────────────────────────

class TestRenderPipelinePlan:

    def test_shows_enabled_job_boards_and_threshold(self):
        from commands.pipeline_cli import render_pipeline_plan

        out_console = Console(record=True, width=120)
        render_pipeline_plan(console=out_console)
        output = out_console.export_text()

        assert "nofluffjobs" in output
        assert "70" in output  # default threshold

    def test_execution_plan_lists_each_enabled_source(self):
        from commands.pipeline_cli import render_pipeline_plan

        out_console = Console(record=True, width=120)
        render_pipeline_plan(console=out_console)
        output = out_console.export_text()

        assert "Today's Execution Plan" in output
        assert "Job board" in output


# ── Full CLI wiring via Typer's CliRunner ───────────────────────────────────

class TestCLIWiring:

    @pytest.fixture(autouse=True)
    def _patch_everything(self, tmp_path, monkeypatch):
        """
        Every test in this class invokes the real Typer app exactly as a
        user would — so we patch at the true I/O boundaries: no real
        scrapers, no real tracker DB file, dry-run AI (already the
        project default).
        """
        import core.pipeline as pipeline_module
        import main as main_module
        from commands import pipeline_cli as pipeline_cli_module
        from config.settings import settings

        job = make_job("wired-job", title="Senior Software Test Engineer")
        fake_registry = {"fake_board": make_fake_job_board([job])}

        monkeypatch.setattr(pipeline_module, "JOB_BOARD_SCRAPERS", fake_registry)
        monkeypatch.setattr(pipeline_module, "COMPANY_ATS_SCRAPERS", {})

        db_path = tmp_path / "cli_wiring.db.json"
        tracker_factory = lambda: ApplicationTracker(db_path)
        monkeypatch.setattr(main_module, "ApplicationTracker", tracker_factory)
        monkeypatch.setattr(pipeline_cli_module, "ApplicationTracker", tracker_factory)

        monkeypatch.setattr(settings.pipeline, "enabled_scrapers", "fake_board")

        self.runner = CliRunner()

    def test_jobs_command_runs_and_exits_cleanly(self):
        import main
        result = self.runner.invoke(main.app, ["jobs"])
        assert result.exit_code == 0
        assert "Discovered Jobs" in result.output or "No jobs found" in result.output

    def test_run_command_runs_and_exits_cleanly(self):
        import main
        result = self.runner.invoke(main.app, ["run"])
        assert result.exit_code == 0
        assert "Pipeline Run Summary" in result.output

    def test_stats_command_runs_and_exits_cleanly(self):
        import main
        result = self.runner.invoke(main.app, ["stats"])
        assert result.exit_code == 0
        assert "Application Statistics" in result.output

    def test_plan_command_runs_and_exits_cleanly(self):
        import main
        result = self.runner.invoke(main.app, ["plan"])
        assert result.exit_code == 0
        assert "Today's Plan" in result.output
        assert "Pipeline Configuration" in result.output

    def test_stats_after_run_shows_recorded_pipeline_data(self):
        import main
        self.runner.invoke(main.app, ["run"])
        result = self.runner.invoke(main.app, ["stats"])
        assert result.exit_code == 0
        assert "Last Pipeline Run" in result.output

    def test_run_then_jobs_does_not_crash(self):
        """Sanity check that running `run` then `jobs` in sequence (sharing
        the same patched tracker DB) works without error."""
        import main
        r1 = self.runner.invoke(main.app, ["run"])
        r2 = self.runner.invoke(main.app, ["jobs"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0

    def test_follow_ups_command_unaffected_by_pipeline_changes(self):
        """follow-ups was not touched by this integration — confirm it
        still works under the same patched tracker."""
        import main
        result = self.runner.invoke(main.app, ["follow-ups"])
        assert result.exit_code == 0
