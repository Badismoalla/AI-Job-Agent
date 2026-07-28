"""
commands/analyze.py
-------------------
Orchestrates the full analyze pipeline. Rich display work is delegated to
commands/display.py — this module owns pipeline sequencing and AI generation
only.

Why extract from main.py?
- main.py stays thin — just CLI argument parsing and delegation
- display logic is independently readable and testable (commands/display.py)
- AI generation (async) is cleanly wrapped here without polluting main.py

Pipeline:
    1. Parse JD file → JobListing          (JobParser)
    2. Evaluate JobListing → MatchReport   (JobMatcher)
    3. Display full analysis               (commands/display.py)
    4. If score >= threshold: generate     (ClaudeGenerator)
       - Cover letter
       - Recruiter InMail
    5. Display or save generated messages

The AI generation threshold is 70 as specified.
"""

import asyncio
from pathlib import Path

from rich.panel import Panel
from rich.rule import Rule

from config.settings import settings
from core.logger import get_logger
from core.models import MatchDecision, MatchReport
from modules.analyzer.job_parser import ParseError
from modules.ai.claude_generator import ClaudeGenerator
from commands.pipeline import parse_and_match
from commands.display import (
    console,
    job_header_panel,
    score_panel,
    match_details_table,
    gaps_table,
    score_breakdown_table,
)

logger = get_logger(__name__)

# Score threshold above which AI messages are generated
_AI_GENERATION_THRESHOLD = 70


def run_analyze(file_path: Path, save_output: bool = False) -> None:
    """
    Entry point called by main.py analyze command.
    Runs the full pipeline synchronously — wraps async AI calls with asyncio.run().

    Args:
        file_path: Path to the JD text file
        save_output: If True, save generated messages to files alongside the JD
    """
    listing, report = _parse_and_display(file_path, forced=False)
    if listing is None:
        return

    logger.info(
        "Analysis complete | company={c} | score={s} | decision={d}",
        c=listing.company,
        s=report.score,
        d=report.decision,
    )

    # ── Generate messages if score >= threshold ────────────────────────────
    if report.score >= _AI_GENERATION_THRESHOLD:
        console.print()
        console.print(
            Rule(
                f"[bold green]Score {report.score}/100 ≥ {_AI_GENERATION_THRESHOLD} "
                f"— Generating messages[/bold green]"
            )
        )
        _generate_and_display(listing, report, file_path, save_output)
    elif report.decision == MatchDecision.REVIEW:
        console.print()
        console.print(
            Panel(
                f"[yellow]Score {report.score}/100 is in REVIEW range "
                f"(40-{_AI_GENERATION_THRESHOLD - 1}).\n"
                f"Messages not generated automatically.\n"
                f"Use [bold]--force[/bold] flag to generate anyway:[/bold]\n"
                f"  python main.py analyze {file_path} --force[/yellow]",
                title="[yellow]Manual Review Required[/yellow]",
                border_style="yellow",
            )
        )
    else:
        console.print()
        console.print(
            Panel(
                f"[red]Score {report.score}/100 — below threshold. No messages generated.[/red]\n"
                f"[dim]{report.reason}[/dim]",
                title="[red]SKIP[/red]",
                border_style="red",
            )
        )


def run_analyze_forced(file_path: Path, save_output: bool = False) -> None:
    """Same as run_analyze but forces message generation regardless of score."""
    listing, report = _parse_and_display(file_path, forced=True)
    if listing is None:
        return

    _generate_and_display(listing, report, file_path, save_output)


# ── Shared pipeline + display sequencing ────────────────────────────────────

def _parse_and_display(
    file_path: Path, forced: bool
) -> tuple[object, MatchReport] | tuple[None, None]:
    """
    Parse the JD, evaluate it, and print the full analysis (header, score
    panel, match details, gaps, score breakdown). Shared by run_analyze and
    run_analyze_forced so the display sequence is defined in exactly one place.

    Returns (listing, report), or (None, None) on a parse error (after
    printing the error and raising SystemExit).
    """
    console.print()

    try:
        listing, report = parse_and_match(file_path)
    except ParseError as e:
        console.print(f"[bold red]Parse error:[/bold red] {e}")
        raise SystemExit(1)

    header_title = "Job Analysis (Forced)" if forced else "Job Analysis"
    console.print(job_header_panel(listing, title=header_title))

    console.print(score_panel(report))
    console.print(match_details_table(report))

    gaps_view = gaps_table(report)
    if gaps_view is not None:
        console.print(gaps_view)
    else:
        console.print("[green]✓ No significant skill gaps detected.[/green]")

    breakdown_view = score_breakdown_table(report)
    if breakdown_view is not None:
        console.print(breakdown_view)

    return listing, report


# ── AI Generation ─────────────────────────────────────────────────────────────

def _generate_and_display(
    listing,
    report: MatchReport,
    file_path: Path,
    save_output: bool,
) -> None:
    """Generate cover letter and recruiter InMail, display, and optionally save."""
    gen = ClaudeGenerator()

    dry_run = settings.app.dry_run
    mode_label = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]LIVE[/green]"

    console.print(f"\n[bold]Generating messages[/bold]  {mode_label}")
    console.print("[dim]Model: " + settings.ai.anthropic_model + "[/dim]\n")

    # Generate cover letter
    with console.status("[bold blue]Generating cover letter...[/bold blue]"):
        cover = asyncio.run(
            gen.generate_cover_letter(
                job=listing,
                match_report=report,
            )
        )

    console.print(
        Panel(
            cover.body,
            title=f"[bold green]Cover Letter[/bold green]  [dim]{cover.subject or ''}[/dim]",
            border_style="green",
            padding=(1, 2),
        )
    )

    # Generate recruiter InMail
    with console.status("[bold blue]Generating recruiter InMail...[/bold blue]"):
        inmail = asyncio.run(
            gen.generate_recruiter_message(
                job=listing,
                recruiter_name="Hiring Team",
                match_report=report,
            )
        )

    console.print(
        Panel(
            inmail.body,
            title="[bold cyan]Recruiter InMail (LinkedIn)[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # Optionally save to files
    if save_output:
        _save_messages(file_path, cover.body, inmail.body, report)


def _save_messages(
    source_path: Path,
    cover_letter: str,
    recruiter_msg: str,
    report: MatchReport,
) -> None:
    """Save generated messages to files next to the source JD."""
    stem = source_path.stem
    out_dir = source_path.parent

    cover_path = out_dir / f"{stem}_cover_letter.txt"
    inmail_path = out_dir / f"{stem}_recruiter_inmail.txt"

    cover_path.write_text(cover_letter, encoding="utf-8")
    inmail_path.write_text(recruiter_msg, encoding="utf-8")

    console.print(f"\n[green]✓ Saved:[/green] {cover_path}")
    console.print(f"[green]✓ Saved:[/green] {inmail_path}")
    logger.info("Messages saved | cover={c} | inmail={i}", c=str(cover_path), i=str(inmail_path))
