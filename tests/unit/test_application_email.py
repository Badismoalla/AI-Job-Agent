"""
Unit tests for modules.application.email.EmailAdapter.

No test sends a real email — EmailAdapter never connects to SMTP at all
(see module/package docstrings); it only builds and returns a payload
dict describing what an email application would contain.
"""

import pytest

from core.models import ApplicationSource, GeneratedMessage, JobListing, Market, MessageType
from modules.application.email import EmailAdapter


def make_job(id: str = "a", title: str = "Senior Test Engineer", company: str = "Bosch") -> JobListing:
    return JobListing(
        id=id, title=title, company=company, city="Wroclaw", market=Market.POLAND,
        url=f"https://example.com/jobs/{id}", source=ApplicationSource.CAREER_PAGE,
    )


def cover_letter(body: str = "Dear Hiring Team, I am excited to apply.", subject: str | None = None):
    return GeneratedMessage(type=MessageType.COVER_LETTER, subject=subject, body=body)


class TestSupports:

    def test_supports_every_job(self):
        adapter = EmailAdapter()
        for source in (ApplicationSource.NOFLUFFJOBS, ApplicationSource.CAREER_PAGE, ApplicationSource.LINKEDIN):
            job = make_job()
            job.source = source
            assert adapter.supports(job) is True


class TestPrepareValidation:

    def test_missing_recipient_email_fails_cleanly(self):
        adapter = EmailAdapter()
        result = adapter.prepare(make_job(), [cover_letter()], dry_run=True)
        assert result.success is False
        assert "recipient_email" in result.error

    def test_missing_message_fails_cleanly(self):
        adapter = EmailAdapter()
        result = adapter.prepare(make_job(), [], dry_run=True, recipient_email="hr@bosch.com")
        assert result.success is False
        assert "COVER_LETTER" in result.error

    def test_hr_email_type_accepted_as_fallback(self):
        adapter = EmailAdapter()
        hr_msg = GeneratedMessage(type=MessageType.HR_EMAIL, body="Dear HR,")
        result = adapter.prepare(make_job(), [hr_msg], dry_run=True, recipient_email="hr@bosch.com")
        assert result.success is True
        assert result.payload["body"] == "Dear HR,"

    def test_never_raises_on_bad_input(self):
        adapter = EmailAdapter()
        r1 = adapter.prepare(make_job(), [], dry_run=True)
        r2 = adapter.prepare(make_job(), [cover_letter()], dry_run=True, recipient_email="")
        assert r1.success is False
        assert r2.success is False


class TestPrepareSuccess:

    def test_builds_correct_payload(self):
        adapter = EmailAdapter()
        msg = cover_letter(body="Body text", subject="Custom Subject")
        result = adapter.prepare(
            make_job(), [msg], dry_run=True, recipient_email="hr@bosch.com", cc=["recruiter@bosch.com"],
        )
        assert result.success is True
        assert result.payload["to"] == "hr@bosch.com"
        assert result.payload["cc"] == ["recruiter@bosch.com"]
        assert result.payload["subject"] == "Custom Subject"
        assert result.payload["body"] == "Body text"

    def test_subject_fallback_when_message_has_none(self):
        adapter = EmailAdapter()
        msg = cover_letter(subject=None)
        result = adapter.prepare(make_job(title="X"), [msg], dry_run=True, recipient_email="hr@bosch.com")
        assert "X" in result.payload["subject"]

    def test_subject_fallback_uses_candidate_name_not_recipient_email(self):
        """Fallback subject must identify the candidate, not put the HR
        recipient's own address in the subject line."""
        adapter = EmailAdapter()
        msg = cover_letter(subject=None)
        result = adapter.prepare(make_job(title="X"), [msg], dry_run=True, recipient_email="hr@bosch.com")
        subject = result.payload["subject"]
        assert "hr@bosch.com" not in subject
        assert "Badis Moalla" in subject

    def test_dry_run_true_by_default(self):
        adapter = EmailAdapter()
        result = adapter.prepare(make_job(), [cover_letter()], recipient_email="hr@bosch.com")
        assert result.dry_run is True

    def test_result_job_id_and_adapter_name(self):
        adapter = EmailAdapter()
        result = adapter.prepare(make_job(id="xyz"), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com")
        assert result.job_id == "xyz"
        assert result.adapter_name == "email"


class TestAttachments:

    def test_no_cv_path_notes_missing_cv(self):
        adapter = EmailAdapter()
        result = adapter.prepare(
            make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com",
            attach_cover_letter=False,
        )
        assert any("No CV path configured" in n for n in result.notes)
        assert result.attachments == []

    def test_existing_cv_attached(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        cv.write_bytes(b"%PDF-1.4")
        adapter = EmailAdapter(cv_path=cv)
        result = adapter.prepare(
            make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com",
            attach_cover_letter=False,
        )
        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "resume.pdf"
        assert result.attachments[0].exists is True

    def test_missing_cv_file_noted_but_still_attached_as_reference(self, tmp_path):
        cv = tmp_path / "does_not_exist.pdf"
        adapter = EmailAdapter(cv_path=cv)
        result = adapter.prepare(
            make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com",
            attach_cover_letter=False,
        )
        assert any("file not found" in n for n in result.notes)

    def test_cover_letter_attached_by_default(self):
        adapter = EmailAdapter()
        result = adapter.prepare(make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com")
        assert len(result.attachments) == 1
        assert result.attachments[0].filename.endswith("cover-letter.txt")

    def test_cover_letter_attachment_not_written_in_dry_run(self, tmp_path):
        adapter = EmailAdapter(output_dir=tmp_path)
        result = adapter.prepare(make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com")
        assert result.attachments[0].exists is False
        assert not any(tmp_path.iterdir())

    def test_cover_letter_attachment_written_when_not_dry_run(self, tmp_path):
        adapter = EmailAdapter(output_dir=tmp_path)
        result = adapter.prepare(
            make_job(), [cover_letter(body="Real content")], dry_run=False, recipient_email="hr@bosch.com",
        )
        assert result.attachments[0].exists is True
        assert result.attachments[0].path.read_text() == "Real content"

    def test_attach_cover_letter_false_skips_it(self):
        adapter = EmailAdapter()
        result = adapter.prepare(
            make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com",
            attach_cover_letter=False,
        )
        assert result.attachments == []

    def test_cv_and_cover_letter_both_attached(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        cv.write_bytes(b"%PDF-1.4")
        adapter = EmailAdapter(cv_path=cv, output_dir=tmp_path)
        result = adapter.prepare(make_job(), [cover_letter()], dry_run=True, recipient_email="hr@bosch.com")
        assert len(result.attachments) == 2
