"""
Unit tests for modules.application.smartrecruiters.SmartRecruitersAdapter.

No test performs a real HTTP submission — no network I/O in this adapter
at all (see module/package docstrings).
"""

import pytest

from core.models import ApplicationSource, GeneratedMessage, JobListing, Market, MessageType
from modules.application.smartrecruiters import SmartRecruitersAdapter


def make_job(url: str, id: str = "a", title: str = "Senior Test Engineer", company: str = "Visa") -> JobListing:
    return JobListing(
        id=id, title=title, company=company, city="Wroclaw", market=Market.POLAND,
        url=url, source=ApplicationSource.CAREER_PAGE,
    )


def sr_job(**kwargs) -> JobListing:
    return make_job(url="https://jobs.smartrecruiters.com/Visa/abc123", **kwargs)


def cover_letter(body: str = "Dear Hiring Team, I am excited to apply."):
    return GeneratedMessage(type=MessageType.COVER_LETTER, body=body)


class TestSupports:

    @pytest.mark.parametrize("url", [
        "https://jobs.smartrecruiters.com/Visa/abc123",
        "https://careers.smartrecruiters.com/Visa/abc123",
    ])
    def test_supports_smartrecruiters_urls(self, url):
        adapter = SmartRecruitersAdapter()
        assert adapter.supports(make_job(url=url)) is True

    @pytest.mark.parametrize("url", [
        "https://boards.greenhouse.io/stripe/jobs/1",
        "https://jobs.lever.co/netflix/abc",
        "https://acme.wd5.myworkdayjobs.com/en-US/site/job/x",
    ])
    def test_does_not_support_other_platforms(self, url):
        adapter = SmartRecruitersAdapter()
        assert adapter.supports(make_job(url=url)) is False


class TestPrepare:

    def test_unsupported_job_fails_cleanly(self):
        adapter = SmartRecruitersAdapter()
        result = adapter.prepare(make_job(url="https://jobs.lever.co/x/1"), [cover_letter()], dry_run=True)
        assert result.success is False
        assert "SmartRecruiters" in result.error

    def test_missing_cover_letter_fails_cleanly(self):
        adapter = SmartRecruitersAdapter()
        result = adapter.prepare(sr_job(), [], dry_run=True)
        assert result.success is False

    def test_builds_correct_payload_fields(self):
        adapter = SmartRecruitersAdapter()
        result = adapter.prepare(sr_job(), [cover_letter(body="Cover text")], dry_run=True)
        assert result.success is True
        assert result.payload["firstName"]
        assert result.payload["lastName"]
        assert result.payload["email"]
        assert result.payload["coverLetter"] == "Cover text"

    def test_resume_field_name_is_camel_case(self):
        adapter = SmartRecruitersAdapter()
        result = adapter.prepare(sr_job(), [cover_letter()], dry_run=True)
        assert "resumeFileName" in result.payload

    def test_resume_set_with_cv(self, tmp_path):
        cv = tmp_path / "resume.docx"
        cv.write_bytes(b"fake docx")
        adapter = SmartRecruitersAdapter(cv_path=cv)
        result = adapter.prepare(sr_job(), [cover_letter()], dry_run=True)
        assert result.payload["resumeFileName"] == "resume.docx"

    def test_never_raises(self):
        adapter = SmartRecruitersAdapter()
        result = adapter.prepare(make_job(url=""), [], dry_run=True)
        assert result.success is False
