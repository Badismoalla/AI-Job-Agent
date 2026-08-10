"""
commands/pipeline_cli.py
--------------------------
CLI-facing wrappers around core/pipeline.py for the `jobs`, `run`, `stats`,
and `plan` commands.

Why a separate module rather than putting this logic in main.py?
Same reasoning as commands/analyze.py and commands/apply_preview.py — Typer
command functions in main.py should stay thin (argument parsing +
delegation); the actual behaviour lives here, where it's directly callable
and testable without going through Typer at all.

Every function here takes optional `console`/`tracker`/registry-override
parameters purely for testability (mirroring core.pipeline.run_pipeline's
own pattern) — main.py's Typer commands call these with no overrides, using
real defaults.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.console import Console
from rich.table import Table

from commands.display import console as shared_console
from commands.display import decision_label
from config.settings import settings
from core.discovery import GMAIL_PROVIDER_KEY, GmailAlertProvider
from core.exceptions import ScraperError
from core.models import JobListing, MatchReport
from core.pipeline import COMPANY_ATS_SCRAPERS, JOB_BOARD_SCRAPERS, PipelineResult, run_pipeline
from core.profile import profile
from modules.scraper.company.base import load_company_sources
from modules.tracker.tracker import ApplicationTracker


# ── `jobs` command: discovery mode ──────────────────────────────────────────

def run_jobs_command(
    roles: list[str] | None = None,
    cities: list[str] | None = None,
    console: Console | None = None,
    **pipeline_kwargs: Any,
) -> PipelineResult:
    """
    Run the pipeline in discovery mode: scrape every enabled source,
    deduplicate, score against the profile, and display every result —
    no AI messages generated, no applications stored, no tracker writes.
    """
    console = console or shared_console

    result = asyncio.run(
        run_pipeline(
            roles=roles, cities=cities, console=console,
            generate_messages=False, **pipeline_kwargs,
        )
    )

    _render_jobs_table(result.matched, console)
    return result


def _render_jobs_table(matched: list[tuple[JobListing, MatchReport]], console: Console) -> None:
    if not matched:
        console.print("\n[yellow]No jobs found.[/yellow]")
        return

    table = Table(title=f"Discovered Jobs ({len(matched)})", show_header=True, header_style="bold blue")
    table.add_column("Company", width=22)
    table.add_column("Title", width=32)
    table.add_column("Location", width=16)
    table.add_column("Source", width=14)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Decision", width=10)

    # Highest-scoring first, so the most promising leads are visible without scrolling.
    for job, report in sorted(matched, key=lambda pair: pair[1].score, reverse=True):
        source = getattr(job.source, "value", job.source)
        table.add_row(
            job.company, job.title, job.city, str(source),
            str(report.score), decision_label(report.decision),
        )

    console.print()
    console.print(table)


# ── `run` command: the full pipeline ────────────────────────────────────────

def run_full_pipeline_command(
    roles: list[str] | None = None,
    cities: list[str] | None = None,
    console: Console | None = None,
    tracker: ApplicationTracker | None = None,
    **pipeline_kwargs: Any,
) -> PipelineResult:
    """
    Run the full pipeline: scrape, dedupe, match, filter by threshold,
    generate AI messages for accepted jobs, and store them. Records a
    summary of this run to the tracker (see modules.tracker.tracker.
    ApplicationTracker.record_pipeline_run) so `stats` can report on it
    later — Application records alone only ever cover *accepted* jobs.
    """
    console = console or shared_console
    owns_tracker = tracker is None
    tracker = tracker or ApplicationTracker()

    try:
        result = asyncio.run(
            run_pipeline(
                roles=roles, cities=cities, console=console, tracker=tracker,
                **pipeline_kwargs,
            )
        )
        tracker.record_pipeline_run(_build_run_stats(result))
        _render_run_summary(result, console)
        return result
    finally:
        if owns_tracker:
            tracker.close()


def _build_run_stats(result: PipelineResult) -> dict:
    """Build the dict stored via ApplicationTracker.record_pipeline_run()."""
    per_source: dict[str, int] = {}
    for job in result.scraped:
        source = str(getattr(job.source, "value", job.source))
        per_source[source] = per_source.get(source, 0) + 1

    messages_generated = sum(len(app.messages) for app in result.applications_created)

    return {
        "scraped": len(result.scraped),
        "deduplicated": len(result.deduplicated),
        "duplicates_removed": result.duplicates_removed,
        "accepted": len(result.accepted),
        "rejected": len(result.deduplicated) - len(result.accepted),
        "messages_generated": messages_generated,
        "applications_created": len(result.applications_created),
        "skipped_already_applied": result.skipped_already_applied,
        "failed_scrapers": [err.source for err in result.errors],
        "per_source": per_source,
    }


def _render_run_summary(result: PipelineResult, console: Console) -> None:
    rejected = len(result.deduplicated) - len(result.accepted)
    messages_generated = sum(len(app.messages) for app in result.applications_created)

    table = Table(title="Pipeline Run Summary", show_header=False)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Scraped jobs", str(len(result.scraped)))
    table.add_row("Duplicates removed", str(result.duplicates_removed))
    table.add_row("Accepted", f"[green]{len(result.accepted)}[/green]")
    table.add_row("Rejected", f"[red]{rejected}[/red]")
    table.add_row("AI messages generated", str(messages_generated))
    table.add_row("Stored applications", str(len(result.applications_created)))
    table.add_row(
        "Failed scrapers",
        f"[yellow]{len(result.errors)}[/yellow]" if result.errors else "0",
    )

    console.print()
    console.print(table)

    if result.errors:
        console.print("\n[yellow]Failed sources:[/yellow]")
        for err in result.errors:
            console.print(f"  [yellow]- {err.source}: {err.error}[/yellow]")


# ── `stats` command extension ────────────────────────────────────────────────

def render_extended_stats(console: Console | None = None, tracker: ApplicationTracker | None = None) -> None:
    """
    Render the full extended `stats` command: applications-generated/-sent
    (from ApplicationTracker.daily_stats(), which already tracks this —
    "generated" is the pre-existing total_applications count, since
    Application records are only ever created for accepted jobs), plus
    the most recently recorded pipeline run (scraped/accepted/rejected/
    duplicates/per-source — data that only exists via `run`, since
    Application records alone never cover rejected/duplicate jobs).

    If `run` has never been executed, the pipeline-run section says so
    plainly rather than showing misleading zeros; the applications
    section always renders from whatever's in the tracker already.
    """
    console = console or shared_console
    owns_tracker = tracker is None
    tracker = tracker or ApplicationTracker()

    try:
        daily = tracker.daily_stats()
        last_run = tracker.get_last_pipeline_run()
    finally:
        if owns_tracker:
            tracker.close()

    apps_table = Table(title="Applications", show_header=False)
    apps_table.add_column("Metric", style="dim")
    apps_table.add_column("Value", style="bold")
    apps_table.add_row("Applications generated", str(daily["total_applications"]))
    apps_table.add_row("Applications sent", f"[green]{daily['sent']}[/green]")
    console.print()
    console.print(apps_table)

    if last_run is None:
        console.print(
            "\n[dim]No pipeline run recorded yet — run `python main.py run` "
            "to populate scrape/accept/reject/duplicate stats.[/dim]"
        )
        return

    run_table = Table(title=f"Last Pipeline Run ({last_run.get('recorded_at', '?')[:19]})", show_header=False)
    run_table.add_column("Metric", style="dim")
    run_table.add_column("Value", style="bold")
    run_table.add_row("Total scraped jobs", str(last_run.get("scraped", 0)))
    run_table.add_row("Duplicate count", str(last_run.get("duplicates_removed", 0)))
    run_table.add_row("Accepted jobs", f"[green]{last_run.get('accepted', 0)}[/green]")
    run_table.add_row("Rejected jobs", f"[red]{last_run.get('rejected', 0)}[/red]")
    failed = last_run.get("failed_scrapers", [])
    run_table.add_row("Failed scrapers", f"[yellow]{len(failed)}[/yellow]" if failed else "0")

    console.print()
    console.print(run_table)

    per_source: dict[str, int] = last_run.get("per_source", {})
    if per_source:
        source_table = Table(title="Per-Source Statistics", show_header=True, header_style="bold blue")
        source_table.add_column("Source")
        source_table.add_column("Jobs Scraped", justify="right")
        for source, count in sorted(per_source.items(), key=lambda kv: kv[1], reverse=True):
            source_table.add_row(source, str(count))
        console.print()
        console.print(source_table)


# ── `plan` command extension ─────────────────────────────────────────────────

def render_pipeline_plan(console: Console | None = None) -> None:
    """
    Render the dynamic portion of the extended `plan` command: which
    scrapers/company sources are actually enabled right now, the score
    threshold, and today's concrete execution plan (every source `run`
    would actually hit).
    """
    console = console or shared_console

    enabled = settings.pipeline.enabled_scrapers_list
    enabled_job_boards = [key for key in enabled if key in JOB_BOARD_SCRAPERS]
    enabled_ats_platforms = [key for key in enabled if key in COMPANY_ATS_SCRAPERS]
    gmail_enabled = GMAIL_PROVIDER_KEY in enabled
    unknown = [
        key for key in enabled
        if key not in JOB_BOARD_SCRAPERS and key not in COMPANY_ATS_SCRAPERS and key != GMAIL_PROVIDER_KEY
    ]

    try:
        company_sources = load_company_sources()
    except ScraperError:
        company_sources = {}

    info_table = Table(title="Pipeline Configuration", show_header=False)
    info_table.add_column("Setting", style="dim")
    info_table.add_column("Value", style="bold")
    info_table.add_row("Score threshold", str(settings.pipeline.score_threshold))
    info_table.add_row(
        "Enabled job boards",
        ", ".join(enabled_job_boards) if enabled_job_boards else "[dim]none[/dim]",
    )
    info_table.add_row(
        "Enabled company ATS platforms",
        ", ".join(enabled_ats_platforms) if enabled_ats_platforms else "[dim]none[/dim]",
    )
    if gmail_enabled:
        gmail_status = (
            "[green]configured[/green]"
            if GmailAlertProvider().is_available()
            else "[yellow]enabled, not configured[/yellow]"
        )
        info_table.add_row("Gmail/LinkedIn alert discovery", gmail_status)
    else:
        info_table.add_row("Gmail/LinkedIn alert discovery", "[dim]disabled[/dim]")
    if unknown:
        info_table.add_row("Unknown scraper keys (ignored)", f"[yellow]{', '.join(unknown)}[/yellow]")

    console.print()
    console.print(info_table)

    # Today's concrete execution plan: every source `run`/`jobs` would hit.
    exec_table = Table(title="Today's Execution Plan", show_header=True, header_style="bold blue")
    exec_table.add_column("#", width=4)
    exec_table.add_column("Source")
    exec_table.add_column("Type")

    row_num = 1
    for key in enabled_job_boards:
        exec_table.add_row(str(row_num), key, "Job board")
        row_num += 1
    for key in enabled_ats_platforms:
        for company in company_sources.get(key, []):
            exec_table.add_row(str(row_num), f"{key}.{company.get('name', 'unknown')}", "Company ATS")
            row_num += 1
    if gmail_enabled:
        exec_table.add_row(str(row_num), "gmail_linkedin", "Gmail/LinkedIn alert")
        row_num += 1

    if row_num == 1:
        console.print("\n[yellow]No scraper sources are enabled — check PIPELINE_ENABLED_SCRAPERS.[/yellow]")
        return

    console.print()
    console.print(exec_table)
    console.print(
        f"\n[dim]Target roles: {', '.join(profile.target_roles) or '(none configured)'}[/dim]"
    )
