"""
Unit tests for modules.application.greenhouse.GreenhouseAdapter.

No test performs a real HTTP submission or browser automation — this
adapter has no network I/O at all (see module/package docstrings); it
only builds and returns a structured payload dict.
"""

import pytest

from core.models import ApplicationSource, GeneratedMessage, JobListing, Market, MessageType
from modules.application.greenhouse import GreenhouseAdapter


def make_job(url: str, id: str = "a", title: str = "Senior Test Engineer", company: str = "Stripe") -> JobListing:
    return JobListing(
        id=id, title=title, company=company, city="Wroclaw", market=Market.POLAND,
        url=url, source=ApplicationSource.CAREER_PAGE,
    )


def greenhouse_job(**kwargs) -> JobListing:
    return make_job(url="https://boards.greenhouse.io/stripe/jobs/12345", **kwargs)


def cover_letter(body: str = "Dear Hiring Team, I am excited to apply."):
    return GeneratedMessage(type=MessageType.COVER_LETTER, body=body)


class TestSupports:

    @pytest.mark.parametrize("url", [
        "https://boards.greenhouse.io/stripe/jobs/12345",
        "https://job-boards.greenhouse.io/airbnb/jobs/9999",
        "https://boards.greenhouse.io/stripe/jobs/12345?gh_src=abc",
    ])
    def test_supports_greenhouse_urls(self, url):
        adapter = GreenhouseAdapter()
        assert adapter.supports(make_job(url=url)) is True

    @pytest.mark.parametrize("url", [
        "https://jobs.lever.co/netflix/abc",
        "https://jobs.smartrecruiters.com/Visa/xyz",
        "https://acme.wd5.myworkdayjobs.com/en-US/ExternalCareerSite/job/x",
        "https://nofluffjobs.com/job/x",
    ])
    def test_does_not_support_other_platforms(self, url):
        adapter = GreenhouseAdapter()
        assert adapter.supports(make_job(url=url)) is False

    def test_case_insensitive_url_match(self):
        adapter = GreenhouseAdapter()
        assert adapter.supports(make_job(url="https://BOARDS.GREENHOUSE.IO/stripe/jobs/1")) is True


class TestPrepareValidation:

    def test_unsupported_job_fails_cleanly(self):
        adapter = GreenhouseAdapter()
        job = make_job(url="https://jobs.lever.co/netflix/abc")
        result = adapter.prepare(job, [cover_letter()], dry_run=True)
        assert result.success is False
        assert "doesn't look like a Greenhouse listing" in result.error

    def test_missing_cover_letter_fails_cleanly(self):
        adapter = GreenhouseAdapter()
        result = adapter.prepare(greenhouse_job(), [], dry_run=True)
        assert result.success is False
        assert "COVER_LETTER" in result.error

    def test_never_raises(self):
        adapter = GreenhouseAdapter()
        result = adapter.prepare(make_job(url="not-a-url-at-all"), [], dry_run=True)
        assert result.success is False


class TestPrepareSuccess:

    def test_builds_payload_with_candidate_info(self):
        adapter = GreenhouseAdapter()
        result = adapter.prepare(greenhouse_job(), [cover_letter(body="Body")], dry_run=True)
        assert result.success is True
        assert result.payload["email"]  # populated from profile
        assert result.payload["cover_letter"] == "Body"
        assert result.payload["job_url"] == "https://boards.greenhouse.io/stripe/jobs/12345"

    def test_resume_field_none_without_cv_path(self):
        adapter = GreenhouseAdapter()
        result = adapter.prepare(greenhouse_job(), [cover_letter()], dry_run=True)
        assert result.payload["resume"] is None
        assert any("without a resume attachment" in n for n in result.notes)

    def test_resume_field_set_with_cv_path(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        cv.write_bytes(b"%PDF-1.4")
        adapter = GreenhouseAdapter(cv_path=cv)
        result = adapter.prepare(greenhouse_job(), [cover_letter()], dry_run=True)
        assert result.payload["resume"] == "resume.pdf"
        assert len(result.attachments) == 1

    def test_dry_run_flag_passed_through(self):
        adapter = GreenhouseAdapter()
        result = adapter.prepare(greenhouse_job(), [cover_letter()], dry_run=False)
        assert result.dry_run is False

    def test_no_filesystem_side_effects_ever(self, tmp_path, monkeypatch):
        """Greenhouse adapter has no attachment-materialization step of its
        own (unlike email's cover-letter .txt) — dry_run or not, it never
        writes anything."""
        monkeypatch.chdir(tmp_path)
        adapter = GreenhouseAdapter()
        adapter.prepare(greenhouse_job(), [cover_letter()], dry_run=False)
        assert list(tmp_path.iterdir()) == []
