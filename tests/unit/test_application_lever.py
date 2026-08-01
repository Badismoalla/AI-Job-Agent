"""
Unit tests for modules.application.lever.LeverAdapter.

No test performs a real HTTP submission — no network I/O in this adapter
at all (see module/package docstrings).
"""

import pytest

from core.models import ApplicationSource, GeneratedMessage, JobListing, Market, MessageType
from modules.application.lever import LeverAdapter


def make_job(url: str, id: str = "a", title: str = "Senior Test Engineer", company: str = "Netflix") -> JobListing:
    return JobListing(
        id=id, title=title, company=company, city="Wroclaw", market=Market.POLAND,
        url=url, source=ApplicationSource.CAREER_PAGE,
    )


def lever_job(**kwargs) -> JobListing:
    return make_job(url="https://jobs.lever.co/netflix/abc123", **kwargs)


def cover_letter(body: str = "Dear Hiring Team, I am excited to apply."):
    return GeneratedMessage(type=MessageType.COVER_LETTER, body=body)


class TestSupports:

    def test_supports_lever_url(self):
        adapter = LeverAdapter()
        assert adapter.supports(lever_job()) is True

    @pytest.mark.parametrize("url", [
        "https://boards.greenhouse.io/stripe/jobs/1",
        "https://jobs.smartrecruiters.com/Visa/xyz",
        "https://acme.wd5.myworkdayjobs.com/en-US/site/job/x",
    ])
    def test_does_not_support_other_platforms(self, url):
        adapter = LeverAdapter()
        assert adapter.supports(make_job(url=url)) is False


class TestPrepare:

    def test_unsupported_job_fails_cleanly(self):
        adapter = LeverAdapter()
        result = adapter.prepare(make_job(url="https://boards.greenhouse.io/x/1"), [cover_letter()], dry_run=True)
        assert result.success is False
        assert "Lever" in result.error

    def test_missing_cover_letter_fails_cleanly(self):
        adapter = LeverAdapter()
        result = adapter.prepare(lever_job(), [], dry_run=True)
        assert result.success is False

    def test_builds_correct_payload_fields(self):
        adapter = LeverAdapter()
        result = adapter.prepare(lever_job(), [cover_letter(body="My comments")], dry_run=True)
        assert result.success is True
        assert result.payload["name"]
        assert result.payload["email"]
        assert result.payload["comments"] == "My comments"
        assert "urls" in result.payload

    def test_linkedin_url_included_when_available(self):
        adapter = LeverAdapter()
        result = adapter.prepare(lever_job(), [cover_letter()], dry_run=True)
        assert "linkedin" in result.payload["urls"]

    def test_resume_none_without_cv(self):
        adapter = LeverAdapter()
        result = adapter.prepare(lever_job(), [cover_letter()], dry_run=True)
        assert result.payload["resume"] is None

    def test_resume_set_with_cv(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        cv.write_bytes(b"%PDF-1.4")
        adapter = LeverAdapter(cv_path=cv)
        result = adapter.prepare(lever_job(), [cover_letter()], dry_run=True)
        assert result.payload["resume"] == "resume.pdf"

    def test_never_raises(self):
        adapter = LeverAdapter()
        result = adapter.prepare(make_job(url=""), [], dry_run=True)
        assert result.success is False
