"""Application package preview workflow."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from rich.panel import Panel

from core.exceptions import JobSearchError
from core.logger import get_logger
from core.models import MatchDecision
from core.cv_selector import CVSelector
from modules.ai.claude_generator import ClaudeGenerator
from commands.pipeline import parse_and_match
from commands.display import console, decision_label, decision_border_style
from modules.package.writer import ApplicationPackage, PackageWriter

logger = get_logger(__name__)


def run_apply_preview(file_path: Path) -> None:
    """Parse, match, generate, and write one reviewable application package."""
    try:
        listing, report = parse_and_match(file_path)
    except JobSearchError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1) from exc

    console.print(
        Panel(
            f"[bold]{listing.title}[/bold]\n"
            f"[dim]{listing.company} · {listing.city} · {listing.market}[/dim]\n\n"
            f"Decision: {decision_label(report.decision)}\n"
            f"Match score: {report.score}/100\n"
            f"{report.reason}",
            title="Application Preview",
            border_style=decision_border_style(report.decision),
        )
    )

    if report.decision == MatchDecision.SKIP:
        console.print("[red]No package generated for this job.[/red]")
        return

    cv_selection = CVSelector().select(listing, tier=report.tier)
    if cv_selection.review_required:
        console.print(f"[yellow]⚠ No CV selected automatically:[/yellow] {cv_selection.reason}")
    else:
        console.print(f"[dim]CV selected: {cv_selection.cv_id} ({cv_selection.path.name})[/dim]")

    generated_at = datetime.now(timezone.utc)
    messages = asyncio.run(_generate_messages(listing, report))
    package = ApplicationPackage(
        listing=listing,
        report=report,
        cover_letter=messages["cover_letter"],
        recruiter_message=messages["recruiter_message"],
        hr_email=messages["hr_email"],
        application_answers={
            "status": "not_supplied",
            "message": "No application questions were supplied with the job description.",
            "questions": [],
        },
        generated_at=generated_at,
        cv_path=cv_selection.path,
        cv_id=cv_selection.cv_id,
    )
    package_dir = PackageWriter().write(package)

    if report.decision == MatchDecision.REVIEW:
        console.print(
            "[yellow]Manual review recommended:[/yellow] "
            f"{', '.join(report.skill_gaps) or 'borderline match'}"
        )
    console.print(f"[green]✓ Package generated:[/green] {package_dir}")


async def _generate_messages(listing, report) -> dict[str, str]:
    """Generate the three package messages through the existing AI module."""
    generator = ClaudeGenerator()
    cover_letter = await generator.generate_cover_letter(
        job=listing,
        match_report=report,
    )
    recruiter_message = await generator.generate_recruiter_message(
        job=listing,
        recruiter_name="Hiring Team",
        match_report=report,
    )
    hr_email = await generator.generate_hr_email(
        job=listing,
        match_report=report,
    )
    return {
        "cover_letter": cover_letter.body,
        "recruiter_message": recruiter_message.body,
        "hr_email": hr_email.body,
    }