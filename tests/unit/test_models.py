"""Unit tests for core data models."""

from datetime import datetime, timedelta

import pytest

from core.models import Application, ApplicationStatus, JobListing, Market, ApplicationSource


def test_job_listing_valid(sample_job):
    assert sample_job.company == "JSDSolutions"
    assert sample_job.market == Market.POLAND
    assert sample_job.visa_sponsorship is True


def test_application_needs_follow_up(sample_job):
    app = Application(
        id="app-001",
        job=sample_job,
        status=ApplicationStatus.SENT,
        applied_at=datetime.utcnow() - timedelta(days=8),
    )
    assert app.needs_follow_up(after_days=7) is True


def test_application_no_follow_up_needed(sample_job):
    app = Application(
        id="app-002",
        job=sample_job,
        status=ApplicationStatus.SENT,
        applied_at=datetime.utcnow() - timedelta(days=3),
    )
    assert app.needs_follow_up(after_days=7) is False


def test_application_no_follow_up_interview(sample_job):
    """Interview status should not trigger follow-up."""
    app = Application(
        id="app-003",
        job=sample_job,
        status=ApplicationStatus.INTERVIEW,
        applied_at=datetime.utcnow() - timedelta(days=10),
    )
    assert app.needs_follow_up(after_days=7) is False
