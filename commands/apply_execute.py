"""
commands/apply_execute.py
---------------------------
Execute (or prepare/review) a previously generated application package
via the Phase 2D executors.

This is the missing link between core/executor (implemented and tested
in isolation) and something Badis can actually run: it loads the job
from a package's metadata.json, picks the right executor by platform,
launches a real Playwright browser only when the mode actually needs
one, and prints a structured result.

Platform dispatch order: Greenhouse, Lever, then Email as the universal
fallback (mirrors EmailAdapter.supports(), which is unconditionally True).
SmartRecruiters and Workday are not wired here — out of Phase 2D scope.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.panel import Panel

from commands.display import console
from core.executor import (
    ApplicationExecutor,
    EmailExecutor,
    ExecutionMode,
    ExecutionStatus,
    GreenhouseExecutor,
    LeverExecutor,
    SmartRecruitersExecutor,
)
from core.logger import get_logger
from core.models import JobListing
from modules.tracker.tracker import ApplicationTracker

logger = get_logger(__name__)

_EXECUTORS: list[ApplicationExecutor] = [
    GreenhouseExecutor(),
    LeverExecutor(),
    SmartRecruitersExecutor(),
    EmailExecutor(),  # universal fallback — must stay last
]

_STATUS_STYLE = {
    ExecutionStatus.SUCCESS: "bold green",
    ExecutionStatus.USER_PAUSED: "yellow",
    ExecutionStatus.REVIEW_REQUIRED: "yellow",
    ExecutionStatus.SUBMISSION_UNKNOWN: "bold red",
}


def _load_job_from_package(package_dir: Path) -> JobListing:
    metadata_path = package_dir / "metadata.json"
    if not metadata_path.exists():
        raise SystemExit(f"metadata.json not found in {package_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    job_data = metadata.get("job")
    if job_data is None:
        raise SystemExit(f"metadata.json in {package_dir} has no 'job' section")
    return JobListing.model_validate(job_data)


def _pick_executor(job: JobListing) -> ApplicationExecutor:
    for executor in _EXECUTORS:
        if executor.supports(job):
            return executor
    # Unreachable in practice — EmailExecutor.supports() is always True —
    # but fail loudly rather than silently if that ever changes.
    raise SystemExit(f"No executor available for job URL: {job.url}")


def _resolve_chromium_executable(browser_type) -> str | None:
    """
    Playwright's default executable_path is locked to the exact browser
    build version bundled with the installed `playwright` pip package.
    If the environment has a different (e.g. newer) Chromium build
    installed under PLAYWRIGHT_BROWSERS_PATH — a real version-skew
    situation, not hypothetical — that default path won't exist even
    though a perfectly usable browser is sitting right there.

    Returns an explicit executable path to fall back to, or None to let
    Playwright use its own default (the common case, when versions match).
    """
    import os

    default_path = Path(browser_type.executable_path)
    if default_path.exists():
        return None

    browsers_root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / ".cache" / "ms-playwright"))
    if not browsers_root.exists():
        return None

    # Prefer a full chromium build over headless_shell — closer to a
    # real user-facing browser, fewer feature gaps.
    for pattern in ("chromium-*/chrome-linux/chrome", "chromium_headless_shell-*/chrome-linux/headless_shell"):
        matches = sorted(browsers_root.glob(pattern), reverse=True)
        if matches:
            logger.warning(
                "Playwright's expected Chromium build not found at {expected}; "
                "falling back to installed build at {found}",
                expected=str(default_path), found=str(matches[0]),
            )
            return str(matches[0])

    return None


async def _launch_page_if_needed(executor: ApplicationExecutor, execution_mode: ExecutionMode):
    """
    Only browser-based executors (Greenhouse/Lever/SmartRecruiters) in a
    browser-touching mode need a real page. EmailExecutor never does;
    PREPARE_ONLY never does either — avoid paying the cost (or requiring
    Chromium to be installed) otherwise.
    """
    if isinstance(executor, EmailExecutor) or execution_mode == ExecutionMode.PREPARE_ONLY:
        return None, None

    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    executable_path = _resolve_chromium_executable(playwright.chromium)
    browser = await playwright.chromium.launch(headless=True, executable_path=executable_path)
    page = await browser.new_page()
    return page, (playwright, browser)


async def _run(package_dir: Path, execution_mode: ExecutionMode, recipient_email: str | None) -> None:
    job = _load_job_from_package(package_dir)
    executor = _pick_executor(job)

    console.print(
        Panel(
            f"[bold]{job.title}[/bold]\n"
            f"[dim]{job.company} · {job.city} · {job.market}[/dim]\n\n"
            f"Platform: {executor.platform_name}\n"
            f"Mode: {execution_mode.value}",
            title="Application Execution",
            border_style="blue",
        )
    )

    page, browser_handles = await _launch_page_if_needed(executor, execution_mode)
    context = {"recipient_email": recipient_email} if recipient_email else None

    try:
        with ApplicationTracker() as tracker:
            result = await executor.execute(
                job, package_dir, execution_mode,
                tracker=tracker, page=page, context=context,
            )
    finally:
        if browser_handles is not None:
            playwright, browser = browser_handles
            await browser.close()
            await playwright.stop()

    style = _STATUS_STYLE.get(result.status, "red")
    console.print(f"[{style}]Status: {result.status.value}[/{style}]")
    if result.external_application_id:
        console.print(f"External application ID: {result.external_application_id}")
    if result.error_reason:
        console.print(f"[dim]{result.error_reason}[/dim]")
    if result.workflow_error:
        console.print(f"[dim]{result.workflow_error}[/dim]")
    if result.human_review_required:
        console.print("[yellow]⚠ Human review required before proceeding further.[/yellow]")


def run_apply_execute(
    package_dir: Path,
    mode: str = "review_required",
    recipient_email: str | None = None,
) -> None:
    """Entry point called from main.py."""
    try:
        execution_mode = ExecutionMode(mode)
    except ValueError as exc:
        raise SystemExit(
            f"Invalid mode '{mode}'. Choose one of: "
            f"{', '.join(m.value for m in ExecutionMode)}"
        ) from exc

    if not package_dir.exists() or not package_dir.is_dir():
        raise SystemExit(f"Package directory not found: {package_dir}")

    asyncio.run(_run(package_dir, execution_mode, recipient_email))
