"""
commands/export.py
---------------------
Export all tracked applications to an Excel file, per
docs/PROJECT_REQUIREMENTS.md Section 3.5.
"""

from __future__ import annotations

from pathlib import Path

from commands.display import console
from core.logger import get_logger
from modules.export.excel_exporter import ExcelExporter
from modules.tracker.tracker import ApplicationTracker

logger = get_logger(__name__)

_DEFAULT_OUTPUT = Path("output") / "applications_export.xlsx"


def run_export_excel(output: Path | None = None) -> None:
    """Entry point called from main.py."""
    output_path = output or _DEFAULT_OUTPUT

    with ApplicationTracker() as tracker:
        applications = tracker.get_all_applications()

    if not applications:
        console.print("[yellow]No applications tracked yet — exporting an empty file with headers only.[/yellow]")

    result_path = ExcelExporter().export(applications, output_path)
    console.print(f"[bold green]Exported {len(applications)} application(s) to {result_path}[/bold green]")
