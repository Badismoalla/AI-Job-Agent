"""
modules/export/excel_exporter.py
-----------------------------------
Exports tracked applications to an .xlsx file, per docs/PROJECT_REQUIREMENTS.md
Section 3.5:

    Track: Company, Position, Country, Date applied, CV used, Cover
    letter used, Application URL, Current status.
    Export: Excel.

Note on "CV Used": populated from Application.cv_used (set by
core.cv_selector.CVSelector during package creation — see
core/cv_selector.py). Left blank for applications that predate this
field or where no CV was tracked, never fabricated.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from core.models import Application, MessageType

EXPECTED_HEADERS = [
    "Company",
    "Position",
    "Country",
    "Date Applied",
    "CV Used",
    "Cover Letter Used",
    "Application URL",
    "Status",
]


class ExcelExporter:
    """Writes a list of Application records to a single-sheet .xlsx file."""

    def export(self, applications: list[Application], output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wb = Workbook()
        ws = wb.active
        ws.title = "Applications"
        ws.append(EXPECTED_HEADERS)

        for app in applications:
            ws.append(self._row_for(app))

        self._autosize_columns(ws)
        wb.save(output_path)
        return output_path

    def _row_for(self, app: Application) -> list:
        return [
            app.job.company,
            app.job.title,
            self._country_value(app),
            self._date_applied_value(app),
            self._cv_used_value(app),
            self._cover_letter_used_value(app),
            app.job.url,
            self._status_value(app),
        ]

    def _country_value(self, app: Application) -> str:
        market = app.job.market
        return market.value if hasattr(market, "value") else str(market)

    def _date_applied_value(self, app: Application) -> str:
        if app.applied_at is None:
            return ""
        return app.applied_at.strftime("%Y-%m-%d")

    def _cv_used_value(self, app: Application) -> str:
        return app.cv_used or ""

    def _cover_letter_used_value(self, app: Application) -> str:
        has_cover_letter = any(m.type == MessageType.COVER_LETTER for m in app.messages)
        return "Yes" if has_cover_letter else "No"

    def _status_value(self, app: Application) -> str:
        status = app.status
        return status.value if hasattr(status, "value") else str(status)

    def _autosize_columns(self, ws) -> None:
        for column_cells in ws.columns:
            length = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=10)
            ws.column_dimensions[column_cells[0].column_letter].width = min(length + 2, 60)
