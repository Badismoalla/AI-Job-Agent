"""
Unit tests for modules.application.workday.WorkdayAdapter.

No test performs a real HTTP submission or browser automation — no
network I/O in this adapter at all (see module/package docstrings). This
adapter's payload is deliberately partial (see its module docstring) since
Workday's real apply flow is a multi-step wizard, not a single form.
"""

import pytest

from core.models import ApplicationSource, GeneratedMessage, JobListing, Market, MessageType
from modules.application.workday import WorkdayAdapter


def make_job(url: str, id: str = "a", title: str = "Senior Test Engineer", company: str = "HP") -> JobListing:
    return JobListing(
        id=id, title=title, company=company, city="Wroclaw", market=Market.POLAND,
        url=url, source=ApplicationSource.CAREER_PAGE,
    )


def workday_job(**kwargs) -> JobListing:
    return make_job(url="https://hp.wd5.myworkdayjobs.com/en-US/ExternalCareerSite/job/x", **kwargs)


def cover_letter(body: str = "Dear Hiring Team, I am excited to apply."):
    return GeneratedMessage(type=MessageType.COVER_LETTER, body=body)


class TestSupports:

    def test_supports_workday_url(self):
        adapter = WorkdayAdapter()
        assert adapter.supports(workday_job()) is True

    @pytest.mark.parametrize("url", [
        "https://boards.greenhouse.io/stripe/jobs/1",
        "https://jobs.lever.co/netflix/abc",
        "https://jobs.smartrecruiters.com/Visa/xyz",
    ])
    def test_does_not_support_other_platforms(self, url):
        adapter = WorkdayAdapter()
        assert adapter.supports(make_job(url=url)) is False


class TestPrepare:

    def test_unsupported_job_fails_cleanly(self):
        adapter = WorkdayAdapter()
        result = adapter.prepare(make_job(url="https://jobs.lever.co/x/1"), [cover_letter()], dry_run=True)
        assert result.success is False
        assert "Workday" in result.error

    def test_missing_cover_letter_fails_cleanly(self):
        adapter = WorkdayAdapter()
        result = adapter.prepare(workday_job(), [], dry_run=True)
        assert result.success is False

    def test_builds_correct_payload_fields(self):
        adapter = WorkdayAdapter()
        result = adapter.prepare(workday_job(), [cover_letter(body="Cover text")], dry_run=True)
        assert result.success is True
        assert result.payload["legalName"]["firstName"]
        assert result.payload["legalName"]["lastName"]
        assert result.payload["email"]
        assert result.payload["coverLetter"] == "Cover text"

    def test_multi_step_wizard_caveat_always_present(self):
        """This adapter's payload is deliberately partial — the caveat note
        should always be there, not just on success/failure edge cases."""
        adapter = WorkdayAdapter()
        result = adapter.prepare(workday_job(), [cover_letter()], dry_run=True)
        assert any("multi-step wizard" in n for n in result.notes)

    def test_resume_set_with_cv(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        cv.write_bytes(b"%PDF-1.4")
        adapter = WorkdayAdapter(cv_path=cv)
        result = adapter.prepare(workday_job(), [cover_letter()], dry_run=True)
        assert result.payload["resume"] == "resume.pdf"

    def test_never_raises(self):
        adapter = WorkdayAdapter()
        result = adapter.prepare(make_job(url=""), [], dry_run=True)
        assert result.success is False
