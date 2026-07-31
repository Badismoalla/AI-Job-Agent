"""
core/pipeline.py
-----------------
The full application pipeline: run every enabled scraper, merge and
deduplicate results, score every job against the candidate profile, keep
only jobs that clear the configured threshold, generate AI application
messages for those, and store the result via ApplicationTracker.

Pipeline stages (in order):
    1. Build one scraper instance per enabled job board, and one instance
       per configured company on each enabled ATS platform.
    2. Run every instance concurrently. One failing scraper (bad network,
       a single company's ATS being down, a parse error) is caught and
       logged — it does not abort the rest of the pipeline.
    3. Deduplicate the combined results: exact URL match, or same
       company + city with high title similarity (catches the same role
       cross-posted to multiple boards with slightly different title
       formatting).
    4. Run JobMatcher on every remaining job.
    5. Keep only jobs scoring >= the configured threshold.
    6. Generate a cover letter + recruiter message for each accepted job
       not already tracked, and store an Application via ApplicationTracker.

Design notes:
- core/ intentionally does not import from commands/ — that keeps the
  dependency direction one-way (commands depends on core, never the
  reverse). This module builds its own Rich Console/Progress rather than
  reusing commands/display.py.
- Every external dependency (which scrapers run, the tracker, the AI
  generator, the console) is injectable via run_pipeline()'s parameters,
  so the whole pipeline can be exercised in tests with fakes and zero real
  network/API/disk I/O.
- This module only implements the pipeline engine — it is not wired into
  any CLI command. commands/ is untouched.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from config.settings import settings
from core.exceptions import DuplicateApplicationError, ScraperError
from core.logger import get_logger
from core.matcher import JobMatcher
from core.models import Application, ApplicationStatus, JobListing, MatchReport
from core.profile import profile
from modules.ai.claude_generator import ClaudeGenerator
from modules.scraper.base import BaseJobScraper
from modules.scraper.company.base import CompanyJobScraper, load_company_sources
from modules.scraper.company.greenhouse import GreenhouseScraper
from modules.scraper.company.lever import LeverScraper
from modules.scraper.company.smartrecruiters import SmartRecruitersScraper
from modules.scraper.company.workday import WorkdayScraper
from modules.scraper.justjoinit import JustJoinITScraper
from modules.scraper.nofluffjobs import NoFluffJobsScraper
from modules.scraper.pracuj import PracujScraper
from modules.tracker.tracker import ApplicationTracker

logger = get_logger(__name__)

# ── Scraper registries — overridable per-call for tests via run_pipeline()'s
# job_board_scrapers/company_ats_scrapers params ───────────────────────────

JOB_BOARD_SCRAPERS: dict[str, type[BaseJobScraper]] = {
    "nofluffjobs": NoFluffJobsScraper,
    "pracuj": PracujScraper,
    "justjoinit": JustJoinITScraper,
}

COMPANY_ATS_SCRAPERS: dict[str, type[CompanyJobScraper]] = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "workday": WorkdayScraper,
}


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ScraperRunError:
    """One scraper (or one company on an ATS platform) failing. Captured,
    not raised — the pipeline continues with every other source."""

    source: str
    error: str


@dataclass
class PipelineResult:
    """Summary of a full pipeline run — returned by run_pipeline()."""

    scraped: list[JobListing] = field(default_factory=list)
    deduplicated: list[JobListing] = field(default_factory=list)
    duplicates_removed: int = 0
    accepted: list[JobListing] = field(default_factory=list)
    applications_created: list[Application] = field(default_factory=list)
    skipped_already_applied: int = 0
    errors: list[ScraperRunError] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


# ── Deduplication ────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


_TITLE_NOISE_PATTERN = re.compile(
    r"\(\s*[mwfdMWFD]\s*/\s*[mwfdMWFD]\s*(?:/\s*[mwfdMWFD]\s*)?\)"
)


def _normalize_title_for_comparison(title: str) -> str:
    """
    Strip pure-noise gender-inclusive suffixes ubiquitous on EU job postings
    (e.g. "(m/f/d)", "(m/w/d)", "(f/m/d)") before similarity comparison —
    they carry zero semantic content but would otherwise make two postings
    of the *same* role look meaningfully different by simple string
    similarity, undermining exactly the kind of duplicate detection this
    is for.
    """
    without_noise = _TITLE_NOISE_PATTERN.sub("", title)
    return re.sub(r"\s+", " ", without_noise).strip().lower()


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_title_for_comparison(a), _normalize_title_for_comparison(b)).ratio()


def deduplicate_jobs(
    jobs: list[JobListing], title_similarity_threshold: float = 0.90,
) -> tuple[list[JobListing], int]:
    """
    Remove duplicate job listings using two independent signals:

    - Exact URL match (normalised: trimmed, trailing slash removed, lowercased)
    - Same company + same city, with title similarity >= threshold — catches
      the same role cross-posted to multiple boards with slightly different
      title formatting (e.g. "Senior Test Engineer" vs "Senior Test Engineer
      (m/f/d)"). Bucketed by (company, city) so this stays close to O(n)
      rather than comparing every job against every other job.

    First occurrence wins (scraper source order = priority order). Returns
    (deduplicated_jobs, number_removed).
    """
    seen_urls: set[str] = set()
    buckets: dict[tuple[str, str], list[JobListing]] = {}
    kept: list[JobListing] = []
    removed = 0

    for job in jobs:
        norm_url = _normalize_url(job.url)
        if norm_url in seen_urls:
            removed += 1
            continue

        bucket_key = (job.company.strip().lower(), job.city.strip().lower())
        bucket = buckets.setdefault(bucket_key, [])

        is_duplicate = any(
            _title_similarity(job.title, existing.title) >= title_similarity_threshold
            for existing in bucket
        )
        if is_duplicate:
            removed += 1
            continue

        seen_urls.add(norm_url)
        bucket.append(job)
        kept.append(job)

    return kept, removed


# ── Scraper instance construction ────────────────────────────────────────────

def _build_scraper_instances(
    enabled: list[str],
    job_board_scrapers: dict[str, type[BaseJobScraper]],
    company_ats_scrapers: dict[str, type[CompanyJobScraper]],
    company_sources: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, BaseJobScraper]]:
    """
    Build (label, scraper_instance) pairs for every enabled scraper — one
    instance per enabled job board, one instance per configured company on
    each enabled ATS platform.
    """
    instances: list[tuple[str, BaseJobScraper]] = []

    for key in enabled:
        if key in job_board_scrapers:
            instances.append((key, job_board_scrapers[key]()))
        elif key in company_ats_scrapers:
            scraper_cls = company_ats_scrapers[key]
            companies = company_sources.get(key, [])
            if not companies:
                logger.warning(
                    "No companies configured for ATS platform, skipping | platform={p}", p=key,
                )
            for company in companies:
                label = f"{key}.{company.get('name', 'unknown')}"
                instances.append((label, scraper_cls(company=company)))
        else:
            logger.warning("Unknown scraper key in enabled_scrapers, skipping | key={key}", key=key)

    return instances


# ── Scraping phase ───────────────────────────────────────────────────────────

async def _run_one_scraper(
    label: str, scraper: BaseJobScraper, roles: list[str], cities: list[str],
) -> tuple[str, list[JobListing] | None, str | None]:
    """
    Run one scraper end to end. Catches every exception deliberately —
    this is the "continue when one scraper fails" boundary. Returns
    (label, listings_or_None, error_message_or_None).
    """
    try:
        async with scraper:
            listings = await scraper.scrape(roles=roles, cities=cities)
        return label, listings, None
    except Exception as e:  # noqa: BLE001 - intentional: one source must never sink the pipeline
        logger.warning(
            "Scraper failed, continuing with other sources | source={label} | error={error}",
            label=label, error=str(e),
        )
        return label, None, str(e)


async def _scrape_all(
    instances: list[tuple[str, BaseJobScraper]],
    roles: list[str],
    cities: list[str],
    console: Console,
) -> tuple[list[JobListing], list[ScraperRunError]]:
    """Run every scraper instance concurrently, with a live Rich progress bar
    that advances as each one completes (not just at the very end)."""
    all_jobs: list[JobListing] = []
    errors: list[ScraperRunError] = []

    if not instances:
        return all_jobs, errors

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Scraping job sources...", total=len(instances))

        coroutines = [_run_one_scraper(label, scraper, roles, cities) for label, scraper in instances]
        for coro in asyncio.as_completed(coroutines):
            label, listings, error = await coro
            progress.advance(task_id)
            if error is not None:
                errors.append(ScraperRunError(source=label, error=error))
            elif listings:
                all_jobs.extend(listings)

    return all_jobs, errors


# ── Matching phase ───────────────────────────────────────────────────────────

def _match_all(jobs: list[JobListing], console: Console) -> list[tuple[JobListing, MatchReport]]:
    """
    Run JobMatcher on every job, annotating each JobListing's match_score/
    match_gaps in place (fields that already exist on the model for
    exactly this purpose).
    """
    matcher = JobMatcher()
    results: list[tuple[JobListing, MatchReport]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Matching jobs against profile...", total=len(jobs))
        for job in jobs:
            report = matcher.evaluate(job)
            job.match_score = report.score
            job.match_gaps = report.skill_gaps
            results.append((job, report))
            progress.advance(task_id)

    return results


def _filter_accepted(
    matched: list[tuple[JobListing, MatchReport]], threshold: int,
) -> list[tuple[JobListing, MatchReport]]:
    """Keep only jobs scoring >= threshold. Score-based, matching the same
    convention commands/analyze.py already uses for triggering AI generation."""
    return [(job, report) for job, report in matched if report.score >= threshold]


# ── Generation + storage phase ───────────────────────────────────────────────

async def _generate_and_store(
    accepted: list[tuple[JobListing, MatchReport]],
    tracker: ApplicationTracker,
    generator: ClaudeGenerator,
    console: Console,
) -> tuple[list[Application], int]:
    """
    For each accepted job not already tracked: generate a cover letter +
    recruiter message, build an Application, and store it. Every job seen
    (accepted or not reaching this stage) should be marked seen by the
    caller before this — see run_pipeline().

    Message-generation or storage failure for one job is caught and
    logged, matching the same fail-soft philosophy as the scraping phase —
    one bad job should not lose the rest of the batch.
    """
    applications: list[Application] = []
    skipped_already_applied = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Generating application messages...", total=len(accepted))

        for job, report in accepted:
            if tracker.already_applied(job.id):
                skipped_already_applied += 1
                progress.advance(task_id)
                continue

            try:
                cover_letter = await generator.generate_cover_letter(job=job, match_report=report)
                recruiter_message = await generator.generate_recruiter_message(
                    job=job, recruiter_name="Hiring Team", match_report=report,
                )
            except Exception as e:  # noqa: BLE001 - intentional: one bad job must not sink the batch
                logger.warning(
                    "Message generation failed, skipping application | job_id={id} | error={error}",
                    id=job.id, error=str(e),
                )
                progress.advance(task_id)
                continue

            application = Application(
                id=f"app-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{job.id}",
                job=job,
                status=ApplicationStatus.PENDING,
                messages=[cover_letter, recruiter_message],
            )

            try:
                tracker.add_application(application)
                applications.append(application)
            except DuplicateApplicationError:
                skipped_already_applied += 1
            except Exception as e:  # noqa: BLE001 - intentional: storage failure for one job must not sink the batch
                logger.warning(
                    "Failed to store application, continuing | job_id={id} | error={error}",
                    id=job.id, error=str(e),
                )

            progress.advance(task_id)

    return applications, skipped_already_applied


# ── Profile helpers ──────────────────────────────────────────────────────────

def _flatten_profile_cities() -> list[str]:
    """profile.target['cities'] is a dict of market -> [cities]; scrapers
    take a flat city list, so flatten it here."""
    cities_by_market = profile.target.get("cities", {})
    flat: list[str] = []
    for city_list in cities_by_market.values():
        flat.extend(city_list)
    return flat


# ── Orchestration ─────────────────────────────────────────────────────────────

async def run_pipeline(
    roles: list[str] | None = None,
    cities: list[str] | None = None,
    score_threshold: int | None = None,
    enabled_scrapers: list[str] | None = None,
    tracker: ApplicationTracker | None = None,
    generator: ClaudeGenerator | None = None,
    console: Console | None = None,
    job_board_scrapers: dict[str, type[BaseJobScraper]] | None = None,
    company_ats_scrapers: dict[str, type[CompanyJobScraper]] | None = None,
    company_sources: dict[str, list[dict[str, Any]]] | None = None,
) -> PipelineResult:
    """
    Run the full application pipeline end to end.

    All parameters are optional and fall back to sensible defaults (the
    candidate profile for roles/cities, config/settings.py for the
    threshold and enabled scrapers, config/company_sources.json for ATS
    company lists) — but every one of them can be overridden, which is
    what makes this fully testable with fakes and zero real I/O.

    Args:
        roles: Target role keywords. Defaults to profile.target_roles.
        cities: Target cities. Defaults to profile's flattened city list.
        score_threshold: Minimum JobMatcher score to accept a job. Defaults
            to settings.pipeline.score_threshold.
        enabled_scrapers: Which scraper keys to run. Defaults to
            settings.pipeline.enabled_scrapers_list.
        tracker: ApplicationTracker instance. Defaults to a new one (opened
            and closed by this call). Pass one in to reuse an existing
            connection or inject a fake for tests.
        generator: ClaudeGenerator instance. Defaults to a new one — with
            the project's own APP_DRY_RUN default of True, this makes no
            real API calls unless dry-run is explicitly disabled.
        console: Rich Console for progress output. Defaults to a new one.
        job_board_scrapers / company_ats_scrapers / company_sources:
            Override the scraper registries / company config — this is the
            seam tests use to inject fake scraper classes instead of
            hitting real job boards.

    Returns:
        PipelineResult summarising every stage.
    """
    console = console or Console()
    roles = roles if roles is not None else profile.target_roles
    cities = cities if cities is not None else _flatten_profile_cities()
    threshold = score_threshold if score_threshold is not None else settings.pipeline.score_threshold
    enabled = enabled_scrapers if enabled_scrapers is not None else settings.pipeline.enabled_scrapers_list

    job_board_scrapers = job_board_scrapers if job_board_scrapers is not None else JOB_BOARD_SCRAPERS
    company_ats_scrapers = (
        company_ats_scrapers if company_ats_scrapers is not None else COMPANY_ATS_SCRAPERS
    )
    if company_sources is None:
        try:
            company_sources = load_company_sources()
        except ScraperError as e:
            logger.warning("Could not load company_sources.json, no ATS companies will run | error={e}", e=str(e))
            company_sources = {}

    owns_tracker = tracker is None
    tracker = tracker or ApplicationTracker()
    generator = generator or ClaudeGenerator()

    result = PipelineResult()

    try:
        instances = _build_scraper_instances(enabled, job_board_scrapers, company_ats_scrapers, company_sources)

        console.print(f"\n[bold]Running {len(instances)} scraper source(s)...[/bold]")
        scraped, errors = await _scrape_all(instances, roles, cities, console)
        result.scraped = scraped
        result.errors = errors
        if errors:
            console.print(f"[yellow]{len(errors)} source(s) failed and were skipped:[/yellow]")
            for err in errors:
                console.print(f"  [yellow]- {err.source}: {err.error}[/yellow]")

        for job in scraped:
            tracker.mark_job_seen(job.id)

        deduped, removed = deduplicate_jobs(scraped)
        result.deduplicated = deduped
        result.duplicates_removed = removed
        console.print(
            f"[dim]{len(scraped)} scraped -> {len(deduped)} after dedup "
            f"({removed} duplicate(s) removed)[/dim]"
        )

        matched = _match_all(deduped, console)
        accepted = _filter_accepted(matched, threshold)
        result.accepted = [job for job, _ in accepted]
        console.print(f"[dim]{len(accepted)}/{len(deduped)} job(s) scored >= {threshold} threshold[/dim]")

        applications, skipped = await _generate_and_store(accepted, tracker, generator, console)
        result.applications_created = applications
        result.skipped_already_applied = skipped

        console.print(
            f"\n[bold green]Pipeline complete[/bold green] — "
            f"{len(applications)} application(s) created, {skipped} already applied (skipped)"
        )
    finally:
        result.finished_at = datetime.now(timezone.utc)
        if owns_tracker:
            tracker.close()

    return result
