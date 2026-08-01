"""
Unit tests for modules.application.base — the shared adapter interface,
ApplicationResult/AttachmentSpec, and the attachment/message-lookup helpers
every concrete adapter uses.

No test in this file touches a real network, sends anything, or performs
browser automation. File-writing tests use pytest's tmp_path.
"""

from pathlib import Path

import pytest

from core.models import ApplicationSource, GeneratedMessage, JobListing, Market, MessageType
from modules.application.base import (
    ApplicationAdapter,
    ApplicationResult,
    AttachmentSpec,
    candidate_info,
    find_message,
    materialize_cover_letter_attachment,
    resolve_cv_attachment,
)


def make_job(id: str = "a", title: str = "Senior Test Engineer", company: str = "Bosch") -> JobListing:
    return JobListing(
        id=id, title=title, company=company, city="Wroclaw", market=Market.POLAND,
        url=f"https://example.com/jobs/{id}", source=ApplicationSource.CAREER_PAGE,
    )


# ── ApplicationAdapter contract ─────────────────────────────────────────────

class TestApplicationAdapterContract:

    def test_cannot_instantiate_base_directly(self):
        with pytest.raises(TypeError):
            ApplicationAdapter()

    def test_only_supports_and_prepare_are_abstract(self):
        assert ApplicationAdapter.__abstractmethods__ == frozenset({"supports", "prepare"})


# ── find_message() ───────────────────────────────────────────────────────────

class TestFindMessage:

    def test_finds_exact_type(self):
        cl = GeneratedMessage(type=MessageType.COVER_LETTER, body="cover")
        hr = GeneratedMessage(type=MessageType.HR_EMAIL, body="hr")
        found = find_message([cl, hr], MessageType.HR_EMAIL)
        assert found is hr

    def test_priority_order_respected(self):
        hr = GeneratedMessage(type=MessageType.HR_EMAIL, body="hr")
        cl = GeneratedMessage(type=MessageType.COVER_LETTER, body="cover")
        # HR_EMAIL appears first in the list, but COVER_LETTER is checked first.
        found = find_message([hr, cl], MessageType.COVER_LETTER, MessageType.HR_EMAIL)
        assert found is cl

    def test_falls_back_to_second_type_when_first_absent(self):
        hr = GeneratedMessage(type=MessageType.HR_EMAIL, body="hr")
        found = find_message([hr], MessageType.COVER_LETTER, MessageType.HR_EMAIL)
        assert found is hr

    def test_returns_none_when_no_type_matches(self):
        follow_up = GeneratedMessage(type=MessageType.FOLLOW_UP, body="x")
        assert find_message([follow_up], MessageType.COVER_LETTER) is None

    def test_empty_message_list(self):
        assert find_message([], MessageType.COVER_LETTER) is None


# ── resolve_cv_attachment() ──────────────────────────────────────────────────

class TestResolveCvAttachment:

    def test_none_path_returns_none(self):
        assert resolve_cv_attachment(None) is None

    def test_existing_file(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        spec = resolve_cv_attachment(cv)
        assert spec.exists is True
        assert spec.filename == "resume.pdf"
        assert spec.content_type == "application/pdf"

    def test_nonexistent_file_still_returns_spec(self, tmp_path):
        cv = tmp_path / "missing.pdf"
        spec = resolve_cv_attachment(cv)
        assert spec.exists is False
        assert spec.path == cv

    @pytest.mark.parametrize(
        "suffix,expected_type",
        [
            (".pdf", "application/pdf"),
            (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            (".doc", "application/msword"),
            (".txt", "text/plain"),
            (".xyz", "application/octet-stream"),
        ],
    )
    def test_content_type_by_extension(self, tmp_path, suffix, expected_type):
        cv = tmp_path / f"resume{suffix}"
        spec = resolve_cv_attachment(cv)
        assert spec.content_type == expected_type

    def test_does_not_create_or_modify_the_file(self, tmp_path):
        cv = tmp_path / "resume.pdf"
        resolve_cv_attachment(cv)
        assert not cv.exists()  # resolving is read-only


# ── materialize_cover_letter_attachment() ───────────────────────────────────

class TestMaterializeCoverLetterAttachment:

    def test_dry_run_does_not_write_file(self, tmp_path):
        message = GeneratedMessage(type=MessageType.COVER_LETTER, body="Dear Hiring Team,")
        job = make_job()
        spec = materialize_cover_letter_attachment(message, job, tmp_path, dry_run=True)
        assert not spec.path.exists()
        assert spec.exists is False

    def test_non_dry_run_writes_file_with_correct_content(self, tmp_path):
        message = GeneratedMessage(type=MessageType.COVER_LETTER, body="Dear Hiring Team, I am excited.")
        job = make_job()
        spec = materialize_cover_letter_attachment(message, job, tmp_path, dry_run=False)
        assert spec.path.exists()
        assert spec.path.read_text(encoding="utf-8") == "Dear Hiring Team, I am excited."
        assert spec.exists is True

    def test_filename_derived_from_company_and_title(self, tmp_path):
        message = GeneratedMessage(type=MessageType.COVER_LETTER, body="x")
        job = make_job(title="Senior Test Engineer", company="Bosch Group")
        spec = materialize_cover_letter_attachment(message, job, tmp_path, dry_run=True)
        assert spec.filename == "bosch-group-senior-test-engineer-cover-letter.txt"

    def test_creates_output_dir_if_missing(self, tmp_path):
        nested_dir = tmp_path / "nested" / "output"
        message = GeneratedMessage(type=MessageType.COVER_LETTER, body="x")
        job = make_job()
        spec = materialize_cover_letter_attachment(message, job, nested_dir, dry_run=False)
        assert spec.path.exists()

    def test_content_type_is_text_plain(self, tmp_path):
        message = GeneratedMessage(type=MessageType.COVER_LETTER, body="x")
        spec = materialize_cover_letter_attachment(message, make_job(), tmp_path, dry_run=True)
        assert spec.content_type == "text/plain"


# ── candidate_info() ─────────────────────────────────────────────────────────

class TestCandidateInfo:

    def test_returns_expected_keys(self):
        info = candidate_info()
        assert set(info.keys()) == {
            "first_name", "last_name", "full_name", "email", "phone", "linkedin", "location",
        }

    def test_name_split_on_first_space(self):
        info = candidate_info()
        assert info["full_name"] == f"{info['first_name']} {info['last_name']}".strip() or (
            info["first_name"] == info["full_name"]
        )

    def test_email_matches_profile(self):
        from core.profile import profile
        assert candidate_info()["email"] == profile.email()


# ── ApplicationResult / AttachmentSpec construction ─────────────────────────

class TestApplicationResultDefaults:

    def test_minimal_construction(self):
        result = ApplicationResult(success=True, adapter_name="test", job_id="a", dry_run=True)
        assert result.payload == {}
        assert result.attachments == []
        assert result.notes == []
        assert result.error is None
        assert result.prepared_at is not None

    def test_failure_result_shape(self):
        result = ApplicationResult(
            success=False, adapter_name="test", job_id="a", dry_run=True, error="something went wrong",
        )
        assert result.success is False
        assert result.error == "something went wrong"


class TestAttachmentSpec:

    def test_construction(self, tmp_path):
        path = tmp_path / "resume.pdf"
        spec = AttachmentSpec(path=path, filename="resume.pdf", content_type="application/pdf", exists=False)
        assert spec.path == path
        assert spec.filename == "resume.pdf"
