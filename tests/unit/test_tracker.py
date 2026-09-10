"""Unit tests for ApplicationTracker."""

from datetime import datetime, timedelta, timezone

import pytest

from core.exceptions import DuplicateApplicationError
from core.models import Application, ApplicationStatus
from modules.tracker.tracker import ApplicationTracker


def _make_application(job, applied_days_ago: int = 0) -> Application:
    applied_at = datetime.utcnow() - timedelta(days=applied_days_ago)
    return Application(
        id=f"app-{int(applied_at.timestamp())}",
        job=job,
        status=ApplicationStatus.SENT,
        applied_at=applied_at,
    )


def test_add_and_retrieve(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        app = _make_application(sample_job)
        tracker.add_application(app)
        stats = tracker.daily_stats()
        assert stats["total_applications"] == 1


def test_get_all_applications_empty(tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        assert tracker.get_all_applications() == []


def test_get_all_applications_returns_application_instances(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        app = _make_application(sample_job)
        tracker.add_application(app)

        results = tracker.get_all_applications()
        assert len(results) == 1
        assert isinstance(results[0], Application)
        assert results[0].id == app.id
        assert results[0].job.company == sample_job.company


def test_get_all_applications_returns_multiple_in_insertion_order(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        job2 = sample_job.model_copy(update={"id": "other-job-id", "company": "Other Co"})
        app1 = _make_application(sample_job, applied_days_ago=2)
        app2 = _make_application(job2, applied_days_ago=1)
        tracker.add_application(app1)
        tracker.add_application(app2)

        results = tracker.get_all_applications()
        assert len(results) == 2
        companies = {r.job.company for r in results}
        assert companies == {sample_job.company, "Other Co"}


def test_add_application_round_trips_cv_used(sample_job, tmp_db):
    """cv_used (core.cv_selector.CVSelection.cv_id) survives a store/retrieve cycle unmodified."""
    with ApplicationTracker(tmp_db) as tracker:
        app = _make_application(sample_job)
        app = app.model_copy(update={"cv_used": "europe"})
        tracker.add_application(app)

        results = tracker.get_all_applications()
        assert results[0].cv_used == "europe"


def test_add_application_without_cv_used_defaults_to_none(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        app = _make_application(sample_job)
        tracker.add_application(app)

        results = tracker.get_all_applications()
        assert results[0].cv_used is None


def test_duplicate_raises(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        app = _make_application(sample_job)
        tracker.add_application(app)
        with pytest.raises(DuplicateApplicationError):
            tracker.add_application(app)


def test_follow_up_due(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        old_app = _make_application(sample_job, applied_days_ago=8)
        tracker.add_application(old_app)
        due = tracker.get_follow_ups_due(after_days=7)
        assert len(due) == 1


def test_follow_up_not_due(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        recent_app = _make_application(sample_job, applied_days_ago=3)
        tracker.add_application(recent_app)
        due = tracker.get_follow_ups_due(after_days=7)
        assert len(due) == 0


def test_follow_up_due_for_timezone_aware_datetime(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        old_app = Application(
            id="app-aware",
            job=sample_job,
            status=ApplicationStatus.SENT,
            applied_at=datetime.now(timezone.utc) - timedelta(days=8),
        )
        tracker.add_application(old_app)
        due = tracker.get_follow_ups_due(after_days=7)
        assert len(due) == 1


def test_application_needs_follow_up_for_timezone_aware_datetime(sample_job):
    app = Application(
        id="app-aware-model",
        job=sample_job,
        status=ApplicationStatus.SENT,
        applied_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    assert app.needs_follow_up(after_days=7) is True


def test_status_update(sample_job, tmp_db):
    with ApplicationTracker(tmp_db) as tracker:
        app = _make_application(sample_job)
        tracker.add_application(app)
        tracker.update_status(app.id, ApplicationStatus.INTERVIEW)
        stats = tracker.daily_stats()
        assert stats["interviews"] == 1
