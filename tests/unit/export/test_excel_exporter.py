"""
Tests for modules.export.excel_exporter.

Required columns per docs/PROJECT_REQUIREMENTS.md §3.5:
    Company, Position, Country, Date Applied, CV Used, Cover Letter Used,
    Application URL, Status

Note: the Application model has no field recording which CV file was
used for a given application (no writer in the codebase populates one).
"CV Used" is therefore exported as blank rather than fabricated — see
test_missing_cv_field_is_blank_not_fabricated.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.models import Application, ApplicationStatus, ApplicationSource, JobListing, Market, MessageType, GeneratedMessage
from modules.export.excel_exporter import ExcelExporter, EXPECTED_HEADERS


def _job(company="Test Corp", title="Engineer", **overrides) -> JobListing:
    defaults = dict(
        id=f"{company.lower().replace(' ', '-')}-{title.lower().replace(' ', '-')}",
        title=title,
        company=company,
        city="Warsaw",
        market=Market.POLAND,
        url="https://example.com/job/1",
        source=ApplicationSource.NOFLUFFJOBS,
    )
    defaults.update(overrides)
    return JobListing(**defaults)


def _application(job: JobListing, status=ApplicationStatus.SENT, with_cover_letter=False, **overrides) -> Application:
    messages = []
    if with_cover_letter:
        messages.append(GeneratedMessage(type=MessageType.COVER_LETTER, body="Dear hiring team..."))
    defaults = dict(
        id=f"app-{job.id}",
        job=job,
        status=status,
        applied_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        messages=messages,
    )
    defaults.update(overrides)
    return Application(**defaults)


class TestExcelExporterBasics:

    def test_export_creates_xlsx_file(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        ExcelExporter().export([_application(_job())], out_path)
        assert out_path.exists()

    def test_export_empty_list_still_creates_file_with_headers(self, tmp_path):
        out_path = tmp_path / "empty.xlsx"
        ExcelExporter().export([], out_path)
        wb = load_workbook(out_path)
        ws = wb.active
        header_row = [cell.value for cell in ws[1]]
        assert header_row == EXPECTED_HEADERS

    def test_export_header_row_matches_required_columns(self, tmp_path):
        """Columns required by docs/PROJECT_REQUIREMENTS.md §3.5."""
        out_path = tmp_path / "applications.xlsx"
        ExcelExporter().export([_application(_job())], out_path)
        wb = load_workbook(out_path)
        ws = wb.active
        header_row = [cell.value for cell in ws[1]]
        for required in ("Company", "Position", "Country", "Date Applied", "CV Used",
                          "Cover Letter Used", "Application URL", "Status"):
            assert required in header_row


class TestExcelExporterContent:

    def test_export_row_contains_correct_company_and_position(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        job = _job(company="Bosch", title="Test Engineer")
        ExcelExporter().export([_application(job)], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row = [c.value for c in ws[2]]
        row_by_header = dict(zip(headers, row))

        assert row_by_header["Company"] == "Bosch"
        assert row_by_header["Position"] == "Test Engineer"

    def test_export_country_from_market(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        job = _job(market=Market.NETHERLANDS)
        ExcelExporter().export([_application(job)], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert "Netherlands" in str(row_by_header["Country"]) or row_by_header["Country"] == Market.NETHERLANDS.value

    def test_export_date_applied_is_readable_date_not_raw_datetime_object_string(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        app = _application(_job(), applied_at=datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc))
        ExcelExporter().export([app], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert "2026-08-20" in str(row_by_header["Date Applied"])

    def test_export_cover_letter_used_yes_when_message_present(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        app = _application(_job(), with_cover_letter=True)
        ExcelExporter().export([app], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["Cover Letter Used"] == "Yes"

    def test_export_cover_letter_used_no_when_no_message(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        app = _application(_job(), with_cover_letter=False)
        ExcelExporter().export([app], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["Cover Letter Used"] == "No"

    def test_export_status_is_human_readable(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        app = _application(_job(), status=ApplicationStatus.INTERVIEW)
        ExcelExporter().export([app], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["Status"] == "interview"

    def test_export_application_url_present(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        job = _job(**{"url": "https://boards.greenhouse.io/testco/jobs/999"})
        ExcelExporter().export([_application(job)], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["Application URL"] == "https://boards.greenhouse.io/testco/jobs/999"

    def test_missing_cv_field_is_blank_not_fabricated(self, tmp_path):
        """
        If cv_used was never set on the Application (no CV was tracked
        for it), the export must leave this blank rather than guess.
        """
        out_path = tmp_path / "applications.xlsx"
        ExcelExporter().export([_application(_job())], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["CV Used"] in (None, "")

    def test_cv_used_exported_when_present(self, tmp_path):
        """When Application.cv_used is set (core.cv_selector.CVSelection.cv_id), it appears in the export."""
        out_path = tmp_path / "applications.xlsx"
        app = _application(_job(), cv_used="europe")
        ExcelExporter().export([app], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["CV Used"] == "europe"


class TestExcelExporterMultipleRows:

    def test_export_multiple_applications_all_present(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        apps = [_application(_job(company=f"Company{i}", title="Engineer")) for i in range(5)]
        ExcelExporter().export(apps, out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        # header + 5 data rows
        assert ws.max_row == 6

    def test_export_no_applied_at_leaves_date_applied_blank(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        app = _application(_job(), applied_at=None, status=ApplicationStatus.PENDING)
        ExcelExporter().export([app], out_path)

        wb = load_workbook(out_path)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        row_by_header = dict(zip(headers, [c.value for c in ws[2]]))
        assert row_by_header["Date Applied"] in (None, "")


class TestExcelExporterOutputPath:

    def test_export_creates_parent_directories_if_missing(self, tmp_path):
        out_path = tmp_path / "nested" / "dir" / "applications.xlsx"
        ExcelExporter().export([_application(_job())], out_path)
        assert out_path.exists()

    def test_export_returns_the_output_path(self, tmp_path):
        out_path = tmp_path / "applications.xlsx"
        result = ExcelExporter().export([_application(_job())], out_path)
        assert result == out_path
