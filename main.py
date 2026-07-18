"""
main.py
-------
Application entry point.

Responsibilities:
1. Parse CLI commands via Typer
2. Initialise logging and configuration
3. Run the requested command (daily plan, scrape, apply, track, etc.)
4. Handle top-level errors gracefully

Usage:
    python main.py run          # Run full daily job search cycle
    python main.py plan         # Show today's plan only (no applications)
    python main.py stats        # Show tracker statistics
    python main.py jobs         # Scrape and show new jobs
    python main.py follow-ups   # Show and send follow-up emails
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import settings
from core.logger import setup_logging, get_logger
from core.profile import profile
from modules.tracker.tracker import ApplicationTracker

# Initialise logging before anything else
setup_logging(
    log_level=settings.app.log_level,
    log_dir=Path(__file__).parent / "logs",
)

logger = get_logger(__name__)
console = Console()
app = typer.Typer(
    name="job-search",
    help="AI Job Search Assistant — Badis Moalla",
    add_completion=False,
)


def _print_header() -> None:
    console.print(
        Panel.fit(
            f"[bold blue]AI Job Search Assistant[/bold blue]\n"
            f"[dim]Candidate: {profile.name()} | "
            f"Markets: {', '.join(profile.target_markets)}[/dim]\n"
            f"[dim]Mode: {'DRY RUN' if settings.app.dry_run else 'LIVE'} | "
            f"Env: {settings.app.env}[/dim]",
            border_style="blue",
        )
    )


@app.command()
def run() -> None:
    """Run the full daily job search cycle."""
    _print_header()
    logger.info("Starting full daily cycle")
    console.print("\n[yellow]Full cycle not yet implemented.[/yellow]")
    console.print(
        "Foundation ready. Next: implement scraper → AI generator → tracker cycle."
    )
    plan()


@app.command()
def plan() -> None:
    """Show today's job search plan."""
    _print_header()

    table = Table(title="Today's Plan", show_header=True, header_style="bold blue")
    table.add_column("Priority", style="dim", width=8)
    table.add_column("Task", width=50)
    table.add_column("Time", width=8)

    tasks = [
        ("HIGH", "Log in to LinkedIn (refreshes search rank)", "2 min"),
        ("HIGH", "Check emails + LinkedIn messages — reply within 2h", "5 min"),
        ("HIGH", "Scrape Pracuj.pl for Test Engineer roles", "auto"),
        ("HIGH", "Scrape NoFluffJobs for QA/Validation roles", "auto"),
        ("HIGH", "Scrape JustJoinIT for Embedded Test Engineer", "auto"),
        ("MED",  "Scrape Bulldogjob for automotive QA roles", "auto"),
        ("HIGH", "Apply to 3 company career pages (Continental, Aptiv, Bosch)", "15 min"),
        ("HIGH", "Send 3 LinkedIn InMails to recruiters", "10 min"),
        ("HIGH", "Email hr@jsdsolutions.pl if not yet sent", "5 min"),
        ("MED",  "Message your friend in Poland — weekly check-in", "2 min"),
        ("MED",  "Log all applications in tracker", "5 min"),
        ("MED",  "Check follow-ups due (sent 7+ days ago)", "5 min"),
        ("LOW",  "Apply to 5 more jobs on LinkedIn Jobs", "15 min"),
        ("LOW",  "Search Bayt.com for GCC roles", "10 min"),
    ]

    priority_color = {"HIGH": "red", "MED": "yellow", "LOW": "green"}
    for priority, task, time in tasks:
        color = priority_color[priority]
        table.add_row(f"[{color}]{priority}[/{color}]", task, time)

    console.print(table)


@app.command()
def stats() -> None:
    """Show application tracker statistics."""
    _print_header()

    with ApplicationTracker() as tracker:
        data = tracker.daily_stats()

    table = Table(title="Application Statistics", show_header=False)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Total applications", str(data["total_applications"]))
    table.add_row("Applied today", str(data["today_applications"]))
    table.add_row("Interviews", f"[green]{data['interviews']}[/green]")
    table.add_row("Offers", f"[bold green]{data['offers']}[/bold green]")
    table.add_row("Rejected", f"[red]{data['rejected']}[/red]")
    table.add_row("Follow-ups due", f"[yellow]{data['pending_follow_ups']}[/yellow]")

    console.print(table)


@app.command(name="follow-ups")
def follow_ups() -> None:
    """Show applications due for a follow-up email."""
    _print_header()

    with ApplicationTracker() as tracker:
        due = tracker.get_follow_ups_due(
            after_days=settings.tracker.follow_up_days
        )

    if not due:
        console.print("[green]No follow-ups due.[/green]")
        return

    table = Table(title=f"Follow-ups Due ({len(due)})", show_header=True)
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Applied")
    table.add_column("Days ago")

    from datetime import datetime
    for record in due:
        applied = record.get("applied_at", "unknown")
        days = "?"
        if applied != "unknown":
            delta = datetime.utcnow() - datetime.fromisoformat(applied)
            days = str(delta.days)
        table.add_row(
            record.get("job", {}).get("company", "?"),
            record.get("job", {}).get("title", "?"),
            applied[:10] if applied != "unknown" else "?",
            days,
        )

    console.print(table)


@app.command()
def jobs() -> None:
    """Scrape all boards and show new job listings. (Scraper not yet implemented.)"""
    _print_header()
    console.print("[yellow]Job scraping not yet implemented.[/yellow]")
    console.print("Foundation ready. Next step: implement modules/scraper/pracuj.py")


if __name__ == "__main__":
    app()
