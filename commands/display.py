"""
commands/display.py
--------------------
Shared Rich display primitives for the command layer.

Why this module exists:
- commands/analyze.py and commands/apply_preview.py both rendered job
  headers, decision colors, and border colors independently, with the
  color maps drifting apart (different keys/casing) between the two files.
- Centralising every Rich color map, panel, and table builder here means
  there is exactly one place that decides what a decision "looks like".

Nothing in this module parses, matches, or generates anything — it only
turns already-computed data (JobListing / MatchReport) into Rich output.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.models import MatchReport

# Shared console instance — every command module should render through this
# one console rather than instantiating its own.
console = Console()

# ── Color maps (single source of truth) ────────────────────────────────────

# Style applied to decision *text* (e.g. inside "Decision: APPLY")
DECISION_TEXT_STYLE: dict[str, str] = {
    "APPLY": "bold green",
    "REVIEW": "bold yellow",
    "SKIP": "bold red",
}

# Border color for panels framed around a given decision
DECISION_BORDER_STYLE: dict[str, str] = {
    "APPLY": "green",
    "REVIEW": "yellow",
    "SKIP": "red",
}

# Style applied to a role tier label (PRIMARY / SECONDARY / EXCLUDED)
TIER_STYLE: dict[str, str] = {
    "primary": "cyan",
    "secondary": "blue",
    "excluded": "dim red",
}


def _decision_value(decision) -> str:
    """Normalise an Enum-or-str decision into its plain string value."""
    return decision.value if hasattr(decision, "value") else decision


def _tier_value(tier) -> str:
    """Normalise an Enum-or-str tier into its plain string value."""
    return tier.value if hasattr(tier, "value") else tier


def decision_text_style(decision) -> str:
    """Rich style string for decision text, e.g. 'bold green'."""
    return DECISION_TEXT_STYLE.get(_decision_value(decision), "white")


def decision_border_style(decision) -> str:
    """Rich border color for a decision, e.g. 'green'."""
    return DECISION_BORDER_STYLE.get(_decision_value(decision), "blue")


def decision_label(decision) -> str:
    """A ready-to-print styled decision label, e.g. '[bold green]APPLY[/bold green]'."""
    value = _decision_value(decision)
    style = decision_text_style(value)
    return f"[{style}]{value}[/{style}]"


def tier_style(tier) -> str:
    """Rich style string for a tier label."""
    return TIER_STYLE.get(_tier_value(tier), "white")


# ── Formatting utilities ────────────────────────────────────────────────────

def format_list(items: list[str], color: str) -> str:
    """Format a list of strings as coloured comma-separated text."""
    if not items:
        return ""
    return ", ".join(f"[{color}]{item}[/{color}]" for item in items)


def build_score_bar(score: int, width: int = 20) -> str:
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


def mini_bar(pct: float, width: int = 20) -> str:
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


# ── Panels ───────────────────────────────────────────────────────────────────

def job_header_panel(listing, title: str, border_style: str = "blue") -> Panel:
    """
    Build the standard "job at a glance" header panel:
    bold title line + dim company/city/market line.

    Used by both `analyze` (Job Analysis / Job Analysis (Forced)) and
    `apply-preview` (Application Preview) commands.
    """
    return Panel.fit(
        f"[bold]{listing.title}[/bold]\n"
        f"[dim]{listing.company}  ·  {listing.city}  ·  {listing.market}[/dim]",
        title=f"[bold blue]{title}[/bold blue]",
        border_style=border_style,
    )


def score_panel(report: MatchReport) -> Panel:
    """Show the score, tier, and decision as a prominent panel."""
    decision = _decision_value(report.decision)
    tier = _tier_value(report.tier)

    decision_style = decision_text_style(decision)
    t_style = tier_style(tier)

    score_bar = build_score_bar(report.score)

    return Panel(
        f"[bold]Score:[/bold]  [{decision_style}]{report.score}/100[/{decision_style}]  "
        f"{score_bar}\n"
        f"[bold]Decision:[/bold]  [{decision_style}]{decision}[/{decision_style}]\n"
        f"[bold]Tier:[/bold]  [{t_style}]{tier.upper()}[/{t_style}]  "
        f"[dim](85% primary / 15% secondary rule)[/dim]\n\n"
        f"[dim]{report.reason}[/dim]",
        title="[bold]Match Result[/bold]",
        border_style=decision_border_style(decision),
    )


# ── Tables ───────────────────────────────────────────────────────────────────

def match_details_table(report: MatchReport) -> Table:
    """Build the matched keywords/protocols/tools/domain table."""
    table = Table(
        title="Match Details",
        show_header=True,
        header_style="bold blue",
        show_lines=True,
    )
    table.add_column("Category", style="dim", width=20)
    table.add_column("Found in JD", width=60)

    tier = _tier_value(report.tier)

    if tier == "secondary":
        table.add_row(
            "Secondary skills matched",
            format_list(report.secondary_skills_matched, "green") or "[dim]none[/dim]",
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
            format_list(report.matched_keywords, "green") or "[dim]none[/dim]",
        )
        table.add_row(
            "Protocols / standards",
            format_list(report.matched_protocols, "cyan") or "[dim]none[/dim]",
        )
        table.add_row(
            "Tools",
            format_list(report.matched_tools, "blue") or "[dim]none[/dim]",
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

    return table


def gaps_table(report: MatchReport) -> Table | None:
    """Build the skill gaps & mitigations table, or None if there are no gaps."""
    if not report.skill_gaps:
        return None

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

    return table


_SCORE_BREAKDOWN_MAX = {
    "title_match": 35,
    "keyword_match": 25,
    "domain_match": 20,
    "protocol_match": 15,
    "tools_match": 5,
}


def score_breakdown_table(report: MatchReport) -> Table | None:
    """Build the per-category score breakdown table, or None if empty."""
    if not report.score_breakdown:
        return None

    table = Table(
        title="Score Breakdown",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Category", style="dim", width=22)
    table.add_column("Score", width=8)
    table.add_column("Max", width=6)
    table.add_column("Bar", width=26)

    for category, score in report.score_breakdown.items():
        max_score = _SCORE_BREAKDOWN_MAX.get(category, 10)
        pct = score / max_score if max_score > 0 else 0
        bar = mini_bar(pct, width=20)
        color = "green" if pct >= 0.7 else "yellow" if pct >= 0.4 else "red"
        table.add_row(
            category.replace("_", " ").title(),
            f"[{color}]{score}[/{color}]",
            str(max_score),
            bar,
        )

    return table
