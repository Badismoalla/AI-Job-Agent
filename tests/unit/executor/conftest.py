"""
tests/unit/executor/conftest.py
--------------------------------
Shared fixtures for Phase 2D executor tests.

Package fixtures match the ACTUAL on-disk shape produced by
modules.package.writer.PackageWriter (verified against source, not assumed):

    metadata.json = {
        "generated_at": iso str,
        "job": <JobListing.model_dump(mode="json")>,   # NOT "job_id"/"company"/"title"
        "match_report": <MatchReport.model_dump(mode="json")>,
    }

Mocked browser fixtures use unittest.mock.AsyncMock to stand in for a
Playwright Page, since real browser automation must never run in the
automated test suite (see Phase 2D safety rules). Tests inject a fake
page rather than launching Chromium.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models import (
    ApplicationSource,
    JobListing,
    Market,
    MatchDecision,
    MatchReport,
    RoleTier,
    utc_now,
)


# ── Job fixtures (real JobListing instances, real URLs) ──────────────────────

@pytest.fixture
def greenhouse_job() -> JobListing:
    return JobListing(
        id="testcorp-engineer-warsaw",
        company="Test Corp",
        title="Engineer",
        city="Warsaw",
        market=Market.POLAND,
        url="https://boards.greenhouse.io/testcorp/jobs/123",
        source=ApplicationSource.NOFLUFFJOBS,
        description="Test position",
    )


@pytest.fixture
def lever_job() -> JobListing:
    return JobListing(
        id="testcorp-engineer-lever-warsaw",
        company="Test Corp",
        title="Engineer",
        city="Warsaw",
        market=Market.POLAND,
        url="https://jobs.lever.co/testcorp/abc123",
        source=ApplicationSource.NOFLUFFJOBS,
        description="Test position",
    )


@pytest.fixture
def email_job() -> JobListing:
    return JobListing(
        id="testcorp-engineer-email-warsaw",
        company="Test Corp",
        title="Engineer",
        city="Warsaw",
        market=Market.POLAND,
        url="https://example.com/careers/123",
        source=ApplicationSource.CAREER_PAGE,
        description="Test position",
    )


@pytest.fixture
def unsupported_job() -> JobListing:
    """A job URL none of the Phase 2D executors claim to support."""
    return JobListing(
        id="testcorp-engineer-workday-warsaw",
        company="Test Corp",
        title="Engineer",
        city="Warsaw",
        market=Market.POLAND,
        url="https://testcorp.myworkdayjobs.com/en-US/careers/job/123",
        source=ApplicationSource.CAREER_PAGE,
        description="Test position",
    )


# ── MatchReport fixture (real model, minimal required fields) ────────────────

def _make_match_report(job: JobListing, decision: MatchDecision = MatchDecision.APPLY) -> MatchReport:
    return MatchReport(
        job_id=job.id,
        job_title=job.title,
        company=job.company,
        tier=RoleTier.PRIMARY,
        decision=decision,
        score=85,
        reason="Strong keyword and protocol match.",
    )


# ── Package directory fixture (matches ACTUAL PackageWriter output shape) ────

def _write_valid_package(pkg_dir: Path, job: JobListing) -> None:
    """Write a package to pkg_dir using the real metadata schema."""
    pkg_dir.mkdir(parents=True, exist_ok=True)

    (pkg_dir / "cover_letter.md").write_text("# Cover Letter\n\nDear hiring team, I am interested...")
    (pkg_dir / "recruiter_message.md").write_text("Hi, I'm interested in this role and would love to connect.")
    (pkg_dir / "hr_email.md").write_text("Subject: Application\n\nDear hiring team, please find my application...")
    (pkg_dir / "application_answers.json").write_text(json.dumps({"status": "not_supplied", "questions": []}))

    report = _make_match_report(job)
    (pkg_dir / "match_report.md").write_text(
        f"# Match Report\n\n- **Decision:** {report.decision}\n- **Score:** {report.score}/100\n"
    )

    metadata = {
        "generated_at": utc_now().isoformat(),
        "job": job.model_dump(mode="json"),
        "match_report": report.model_dump(mode="json"),
    }
    (pkg_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))


@pytest.fixture
def valid_package_dir(greenhouse_job):
    """A complete, valid package on disk for greenhouse_job, matching real PackageWriter output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = Path(tmpdir) / "20260821_test_corp_engineer"
        _write_valid_package(pkg_dir, greenhouse_job)
        yield pkg_dir


@pytest.fixture
def make_package_dir(tmp_path):
    """Factory fixture: make_package_dir(job) -> Path, for tests needing custom jobs."""
    def _make(job: JobListing, subdir: str = "pkg") -> Path:
        pkg_dir = tmp_path / subdir
        _write_valid_package(pkg_dir, job)
        return pkg_dir
    return _make


# ── Mocked Playwright Page ────────────────────────────────────────────────────
# Real browser automation must never run in the automated test suite. All
# "browser" interaction in Phase 2D tests goes through this fake, which mimics
# the small subset of the Playwright Page API executors are expected to use.

@pytest.fixture
def mock_page():
    """
    An AsyncMock standing in for a Playwright Page.

    Defaults represent a "happy path" Greenhouse/Lever form: navigable,
    fillable, no CAPTCHA/MFA, submit succeeds, confirmation text present.
    Individual tests override specific methods/attributes to simulate
    CAPTCHA, MFA, timeouts, form errors, etc.
    """
    page = AsyncMock()
    page.url = "https://boards.greenhouse.io/testcorp/jobs/123"
    page.goto = AsyncMock(return_value=None)
    page.fill = AsyncMock(return_value=None)
    page.set_input_files = AsyncMock(return_value=None)
    page.click = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value="<html><body>Thank you for applying!</body></html>")
    page.query_selector = AsyncMock(return_value=None)  # no CAPTCHA/MFA/error elements by default
    page.query_selector_all = AsyncMock(return_value=[])  # no unmapped custom fields by default
    page.wait_for_selector = AsyncMock(return_value=MagicMock())
    return page


@pytest.fixture
def mock_page_with_captcha(mock_page):
    """Mocked page where a reCAPTCHA element is present on the form."""
    async def _query_selector(selector: str):
        if "recaptcha" in selector or "captcha" in selector:
            return MagicMock()
        return None
    mock_page.query_selector = AsyncMock(side_effect=_query_selector)
    return mock_page


@pytest.fixture
def mock_page_with_mfa(mock_page):
    """Mocked page where an MFA/2FA prompt is present."""
    async def _query_selector(selector: str):
        if "mfa" in selector or "2fa" in selector:
            return MagicMock()
        return None
    mock_page.query_selector = AsyncMock(side_effect=_query_selector)
    return mock_page


@pytest.fixture
def mock_page_unconfirmed(mock_page):
    """Mocked page where submit appears to run but no confirmation signal is present."""
    mock_page.content = AsyncMock(return_value="<html><body>Loading...</body></html>")
    mock_page.url = "https://boards.greenhouse.io/testcorp/jobs/123"  # unchanged, no redirect
    return mock_page


# ── Mocked ApplicationTracker ─────────────────────────────────────────────────

@pytest.fixture
def mock_tracker():
    """
    A MagicMock standing in for ApplicationTracker.

    Defaults represent a job in APPLICATION_PREPARED state with no existing
    application record — i.e. safe/expected to execute. Tests override
    specific return values to simulate APPLIED, terminal states, existing
    applications, etc.
    """
    tracker = MagicMock()
    tracker.get_job_lifecycle.return_value = {
        "job_id": "testcorp-engineer-warsaw",
        "current_state": "application_prepared",
        "previous_state": "shortlisted",
    }
    tracker.already_applied.return_value = False
    tracker.update_job_lifecycle_state.return_value = {
        "job_id": "testcorp-engineer-warsaw",
        "current_state": "applied",
        "previous_state": "application_prepared",
    }
    return tracker


@pytest.fixture
def mock_tracker_already_applied(mock_tracker):
    """Tracker reporting the job is already in APPLIED state."""
    mock_tracker.get_job_lifecycle.return_value = {
        "job_id": "testcorp-engineer-warsaw",
        "current_state": "applied",
        "previous_state": "application_prepared",
    }
    mock_tracker.already_applied.return_value = True
    return mock_tracker


@pytest.fixture
def mock_tracker_terminal_state(mock_tracker):
    """Tracker reporting the job is in a hard terminal state (rejected)."""
    mock_tracker.get_job_lifecycle.return_value = {
        "job_id": "testcorp-engineer-warsaw",
        "current_state": "rejected",
        "previous_state": "applied",
    }
    return mock_tracker
