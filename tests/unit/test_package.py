"""Tests for application preview package writing."""

import json
from datetime import datetime, timezone

from core.models import MatchDecision, MatchReport, RoleTier
from modules.package.writer import ApplicationPackage, PackageWriter


def test_writer_creates_dated_complete_package(tmp_path, sample_job):
    report = MatchReport(
        job_id=sample_job.id,
        job_title=sample_job.title,
        company=sample_job.company,
        tier=RoleTier.PRIMARY,
        decision=MatchDecision.APPLY,
        score=88,
        reason="Strong testing match.",
    )
    package = ApplicationPackage(
        listing=sample_job,
        report=report,
        cover_letter="Cover letter",
        recruiter_message="Recruiter message",
        hr_email="HR email",
        application_answers={
            "status": "not_supplied",
            "message": "No application questions were supplied with the job description.",
            "questions": [],
        },
        generated_at=datetime(2026, 7, 21, 12, 30, tzinfo=timezone.utc),
    )

    package_dir = PackageWriter(tmp_path).write(package)

    assert package_dir.name == "2026-07-21_JSDSolutions_Automotive_Test_Engineer"
    expected = {
        "README.md",
        "cover_letter.md",
        "recruiter_message.md",
        "hr_email.md",
        "application_answers.json",
        "match_report.md",
        "metadata.json",
    }
    assert {path.name for path in package_dir.iterdir()} == expected
    readme = (package_dir / "README.md").read_text()
    assert "**Company:** JSDSolutions" in readme
    assert "**Generation timestamp:** 2026-07-21T12:30:00+00:00" in readme
    assert json.loads((package_dir / "application_answers.json").read_text())["status"] == "not_supplied"


def test_writer_without_cv_creates_no_cv_pdf(tmp_path, sample_job):
    """Backward compatibility: a package built with no cv_path (the old call shape) never creates cv.pdf."""
    report = MatchReport(
        job_id=sample_job.id, job_title=sample_job.title, company=sample_job.company,
        tier=RoleTier.PRIMARY, decision=MatchDecision.APPLY, score=88, reason="Strong testing match.",
    )
    package = ApplicationPackage(
        listing=sample_job, report=report,
        cover_letter="Cover letter", recruiter_message="Recruiter message", hr_email="HR email",
        application_answers={"status": "not_supplied", "questions": []},
        generated_at=datetime.now(timezone.utc),
    )
    package_dir = PackageWriter(tmp_path).write(package)
    assert not (package_dir / "cv.pdf").exists()

    metadata = json.loads((package_dir / "metadata.json").read_text())
    assert metadata.get("cv_used") is None


def test_writer_with_real_cv_path_copies_to_cv_pdf(tmp_path, sample_job):
    """When cv_path is supplied and the file exists, it's copied into the package as cv.pdf."""
    source_cv = tmp_path / "source" / "MyCV.pdf"
    source_cv.parent.mkdir(parents=True)
    source_cv.write_bytes(b"%PDF-1.0\nfake pdf content")

    report = MatchReport(
        job_id=sample_job.id, job_title=sample_job.title, company=sample_job.company,
        tier=RoleTier.PRIMARY, decision=MatchDecision.APPLY, score=88, reason="Strong testing match.",
    )
    package = ApplicationPackage(
        listing=sample_job, report=report,
        cover_letter="Cover letter", recruiter_message="Recruiter message", hr_email="HR email",
        application_answers={"status": "not_supplied", "questions": []},
        generated_at=datetime.now(timezone.utc),
        cv_path=source_cv, cv_id="europe",
    )
    package_dir = PackageWriter(tmp_path).write(package)

    cv_pdf = package_dir / "cv.pdf"
    assert cv_pdf.exists()
    assert cv_pdf.read_bytes() == b"%PDF-1.0\nfake pdf content"


def test_writer_records_cv_used_in_metadata(tmp_path, sample_job):
    source_cv = tmp_path / "source" / "MyCV.pdf"
    source_cv.parent.mkdir(parents=True)
    source_cv.write_bytes(b"%PDF-1.0")

    report = MatchReport(
        job_id=sample_job.id, job_title=sample_job.title, company=sample_job.company,
        tier=RoleTier.PRIMARY, decision=MatchDecision.APPLY, score=88, reason="Strong testing match.",
    )
    package = ApplicationPackage(
        listing=sample_job, report=report,
        cover_letter="Cover letter", recruiter_message="Recruiter message", hr_email="HR email",
        application_answers={"status": "not_supplied", "questions": []},
        generated_at=datetime.now(timezone.utc),
        cv_path=source_cv, cv_id="europe",
    )
    package_dir = PackageWriter(tmp_path).write(package)

    metadata = json.loads((package_dir / "metadata.json").read_text())
    assert metadata["cv_used"] == "europe"


def test_writer_with_missing_cv_path_does_not_fabricate_cv_pdf(tmp_path, sample_job):
    """cv_path pointing at a file that doesn't exist -- never silently create a fake cv.pdf."""
    ghost_cv = tmp_path / "does_not_exist.pdf"

    report = MatchReport(
        job_id=sample_job.id, job_title=sample_job.title, company=sample_job.company,
        tier=RoleTier.PRIMARY, decision=MatchDecision.APPLY, score=88, reason="Strong testing match.",
    )
    package = ApplicationPackage(
        listing=sample_job, report=report,
        cover_letter="Cover letter", recruiter_message="Recruiter message", hr_email="HR email",
        application_answers={"status": "not_supplied", "questions": []},
        generated_at=datetime.now(timezone.utc),
        cv_path=ghost_cv, cv_id="ghost",
    )
    package_dir = PackageWriter(tmp_path).write(package)

    assert not (package_dir / "cv.pdf").exists()
    metadata = json.loads((package_dir / "metadata.json").read_text())
    assert metadata.get("cv_used") is None


def test_writer_includes_review_guidance(tmp_path, sample_job):
    report = MatchReport(
        job_id=sample_job.id,
        job_title=sample_job.title,
        company=sample_job.company,
        tier=RoleTier.PRIMARY,
        decision=MatchDecision.REVIEW,
        score=55,
        reason="Some gaps require review.",
        skill_gaps=["CANoe"],
        matched_keywords=["embedded testing"],
        matched_protocols=["UDS"],
        matched_tools=["Wireshark"],
        domain_found="automotive",
    )
    package = ApplicationPackage(
        listing=sample_job,
        report=report,
        cover_letter="Cover letter",
        recruiter_message="Recruiter message",
        hr_email="HR email",
        application_answers={"status": "not_supplied", "questions": []},
        generated_at=datetime.now(timezone.utc),
    )

    package_dir = PackageWriter(tmp_path).write(package)

    report_text = (package_dir / "match_report.md").read_text()
    assert "Some gaps require review." in report_text
    assert "embedded testing" in report_text
    assert "UDS" in report_text
    assert "Wireshark" in report_text
    assert "CANoe" in report_text
    assert "Manual review is recommended" in report_text
    assert "Manual review is recommended" in (package_dir / "README.md").read_text()