"""
commands/analyze.py
-------------------
Orchestrates the full analyze pipeline and owns all Rich display logic.

Why extract from main.py?
- main.py stays thin — just CLI argument parsing and delegation
- display logic is independently readable and testable
- AI generation (async) is cleanly wrapped here without polluting main.py

Pipeline:
    1. Parse JD file → JobListing          (JobParser)
    2. Evaluate JobListing → MatchReport   (JobMatcher)
    3. Display full analysis               (Rich tables)
    4. If score >= threshold: generate     (ClaudeGenerator)
       - Cover letter
       - Recruiter InMail
    5. Display or save generated messages

The AI generation threshold is 70 as specified.
"""

import asyncio
from pathlib import Path

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from config.settings import settings
from core.logger import get_logger
from core.matcher import JobMatcher
from core.models import MatchDecision, MatchReport, RoleTier
from modules.analyzer.job_parser import JobParser, ParseError
from modules.ai.claude_generator import ClaudeGenerator

logger = get_logger(__name__)
console = Console()

# Score threshold above which AI messages are generated
_AI_GENERATION_THRESHOLD = 70

# Decision colors for Rich output
_DECISION_STYLE: dict[str, str] = {
    "APPLY": "bold green",
    "REVIEW": "bold yellow",
    "SKIP": "bold red",
}

_TIER_STYLE: dict[str, str] = {
    "primary": "cyan",
    "secondary": "blue",
    "excluded": "dim red",
}


def run_analyze(file_path: Path, save_output: bool = False) -> None:
    """
    Entry point called by main.py analyze command.
    Runs the full pipeline synchronously — wraps async AI calls with asyncio.run().

    Args:
        file_path: Path to the JD text file
        save_output: If True, save generated messages to files alongside the JD
    """
    console.print()

    # ── Step 1: Parse ─────────────────────────────────────────────────────────
    try:
        listing = JobParser.from_file(file_path)
    except ParseError as e:
        console.print(f"[bold red]Parse error:[/bold red] {e}")
        raise SystemExit(1)

    console.print(
        Panel.fit(
            f"[bold]{listing.title}[/bold]\n"
            f"[dim]{listing.company}  ·  {listing.city}  ·  {listing.market}[/dim]",
            title="[bold blue]Job Analysis[/bold blue]",
            border_style="blue",
        )
    )

    # ── Step 2: Evaluate ──────────────────────────────────────────────────────
    matcher = JobMatcher()
    report = matcher.evaluate(listing)

    # ── Step 3: Display analysis ──────────────────────────────────────────────
    _display_score_panel(report)
    _display_match_details(report)
    _display_gaps(report)
    _display_score_breakdown(report)

    logger.info(
        "Analysis complete | company={c} | score={s} | decision={d}",
        c=listing.company,
        s=report.score,
        d=report.decision,
    )

    # ── Step 4: Generate messages if score >= threshold ───────────────────────
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
    console.print()
    try:
        listing = JobParser.from_file(file_path)
    except ParseError as e:
        console.print(f"[bold red]Parse error:[/bold red] {e}")
        raise SystemExit(1)

    console.print(
        Panel.fit(
            f"[bold]{listing.title}[/bold]\n"
            f"[dim]{listing.company}  ·  {listing.city}  ·  {listing.market}[/dim]",
            title="[bold blue]Job Analysis (Forced)[/bold blue]",
            border_style="blue",
        )
    )

    matcher = JobMatcher()
    report = matcher.evaluate(listing)

    _display_score_panel(report)
    _display_match_details(report)
    _display_gaps(report)
    _display_score_breakdown(report)
    _generate_and_display(listing, report, file_path, save_output)


# ── Display helpers ───────────────────────────────────────────────────────────

def _display_score_panel(report: MatchReport) -> None:
    """Show the score, tier, and decision as a prominent panel."""
    decision = report.decision.value if hasattr(report.decision, 'value') else report.decision
    tier = report.tier.value if hasattr(report.tier, 'value') else report.tier

    decision_style = _DECISION_STYLE.get(decision, "white")
    tier_style = _TIER_STYLE.get(tier, "white")

    score_bar = _build_score_bar(report.score)

    console.print(
        Panel(
            f"[bold]Score:[/bold]  [{decision_style}]{report.score}/100[/{decision_style}]  "
            f"{score_bar}\n"
            f"[bold]Decision:[/bold]  [{decision_style}]{decision}[/{decision_style}]\n"
            f"[bold]Tier:[/bold]  [{tier_style}]{tier.upper()}[/{tier_style}]  "
            f"[dim](85% primary / 15% secondary rule)[/dim]\n\n"
            f"[dim]{report.reason}[/dim]",
            title="[bold]Match Result[/bold]",
            border_style=_border_color(decision),
        )
    )


def _display_match_details(report: MatchReport) -> None:
    """Show matched keywords, protocols, tools, and domain."""
    table = Table(
        title="Match Details",
        show_header=True,
        header_style="bold blue",
        show_lines=True,
    )
    table.add_column("Category", style="dim", width=20)
    table.add_column("Found in JD", width=60)

    tier = report.tier.value if hasattr(report.tier, 'value') else report.tier

    if tier == "secondary":
        table.add_row(
            "Secondary skills matched",
            _format_list(report.secondary_skills_matched, "green") or "[dim]none[/dim]",
        )
        table.add_row(
            "Domain match",
            f"[green]YES — {report.domain_found}[/green]"
            if report.secondary_domain_matched
            else "[red]NO domain match[/red]",
        )
    else:
        table.add_row(
            "Keywords",
            _format_list(report.matched_keywords, "green") or "[dim]none[/dim]",
        )
        table.add_row(
            "Protocols / standards",
            _format_list(report.matched_protocols, "cyan") or "[dim]none[/dim]",
        )
        table.add_row(
            "Tools",
            _format_list(report.matched_tools, "blue") or "[dim]none[/dim]",
        )
        table.add_row(
            "Domain",
            f"[green]{report.domain_found}[/green]"
            if report.domain_found
            else "[dim]not detected[/dim]",
        )
        if report.canoe_gap_mitigated:
            table.add_row(
                "CANoe gap",
                "[yellow]Detected — mitigated by DLT/Wireshark experience[/yellow]",
            )

    console.print(table)


def _display_gaps(report: MatchReport) -> None:
    """Show skill gaps and their mitigations."""
    if not report.skill_gaps:
        console.print("[green]✓ No significant skill gaps detected.[/green]")
        return

    table = Table(
        title="Skill Gaps & Mitigations",
        show_header=True,
        header_style="bold yellow",
        show_lines=True,
    )
    table.add_column("Gap", style="yellow", width=22)
    table.add_column("How to handle in messages", width=58)

    for gap in report.skill_gaps:
        mitigation = report.gap_mitigations.get(gap, "Do not mention — no equivalent experience.")
        # Truncate long mitigations for display (full version goes to AI)
        display_mitigation = mitigation[:200] + "..." if len(mitigation) > 200 else mitigation
        table.add_row(gap, f"[dim]{display_mitigation}[/dim]")

    console.print(table)


def _display_score_breakdown(report: MatchReport) -> None:
    """Show the per-category score breakdown."""
    if not report.score_breakdown:
        return

    table = Table(
        title="Score Breakdown",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Category", style="dim", width=22)
    table.add_column("Score", width=8)
    table.add_column("Max", width=6)
    table.add_column("Bar", width=26)

    max_scores = {
        "title_match": 35,
        "keyword_match": 25,
        "domain_match": 20,
        "protocol_match": 15,
        "tools_match": 5,
    }

    for category, score in report.score_breakdown.items():
        max_score = max_scores.get(category, 10)
        pct = score / max_score if max_score > 0 else 0
        bar = _mini_bar(pct, width=20)
        color = "green" if pct >= 0.7 else "yellow" if pct >= 0.4 else "red"
        table.add_row(
            category.replace("_", " ").title(),
            f"[{color}]{score}[/{color}]",
            str(max_score),
            bar,
        )

    console.print(table)


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


# ── Formatting utilities ──────────────────────────────────────────────────────

def _format_list(items: list[str], color: str) -> str:
    """Format a list of strings as coloured comma-separated text."""
    if not items:
        return ""
    return ", ".join(f"[{color}]{item}[/{color}]" for item in items)


def _build_score_bar(score: int, width: int = 20) -> str:
    """Build a visual score bar like [████████░░░░░░░░░░░░]."""
    filled = int((score / 100) * width)
    empty = width - filled
    if score >= 70:
        color = "green"
    elif score >= 40:
        color = "yellow"
    else:
        color = "red"
    return f"[{color}][{'█' * filled}{'░' * empty}][/{color}]"


def _mini_bar(pct: float, width: int = 20) -> str:
    """Build a small percentage bar for the score breakdown table."""
    filled = int(pct * width)
    empty = width - filled
    if pct >= 0.7:
        color = "green"
    elif pct >= 0.4:
        color = "yellow"
    else:
        color = "red"
    return f"[{color}]{'█' * filled}{'░' * empty}[/{color}]"


def _border_color(decision: str) -> str:
    return {"APPLY": "green", "REVIEW": "yellow", "SKIP": "red"}.get(decision, "blue")
