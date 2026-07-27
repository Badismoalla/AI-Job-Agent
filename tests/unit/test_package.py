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