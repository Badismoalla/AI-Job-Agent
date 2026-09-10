"""
Phase 2C: Lifecycle Tracker Integration Tests

Tests for ApplicationTracker lifecycle persistence and methods.
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from core.models import JobListing, Market, ApplicationSource
from core.lifecycle import JobLifecycleState
from core.exceptions import InvalidLifecycleTransitionError, TrackerError
from modules.tracker import ApplicationTracker


@pytest.fixture
def temp_db():
    """Create temporary database for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db.json"
        yield db_path


@pytest.fixture
def tracker(temp_db):
    """Create tracker instance with temp database."""
    return ApplicationTracker(db_path=temp_db)


def create_test_job(job_id: str = "test-job-1") -> JobListing:
    """Helper to create test JobListing."""
    return JobListing(
        id=job_id,
        company="Test Corp",
        title="Test Engineer",
        description="Testing role",
        city="Warsaw",
        market=Market.POLAND,
        url=f"https://example.com/{job_id}",
        source=ApplicationSource.NOFLUFFJOBS,
    )


class TestLifecycleCreation:
    """Tests for creating initial lifecycle records."""

    def test_create_job_lifecycle_discovered(self, tracker):
        """Can create initial lifecycle in DISCOVERED state."""
        job = create_test_job("test-1")
        record = tracker.create_job_lifecycle(job)
        
        assert record["job_id"] == "test-1"
        assert record["current_state"] == "discovered"
        assert record["previous_state"] is None
        assert record["last_event"] is None
        assert record["application_id"] is None
        assert "discovered_at" in record
        assert "state_changed_at" in record

    def test_create_multiple_jobs_independent(self, tracker):
        """Multiple jobs have independent lifecycle records."""
        job1 = create_test_job("job-1")
        job2 = create_test_job("job-2")
        
        tracker.create_job_lifecycle(job1)
        tracker.create_job_lifecycle(job2)
        
        record1 = tracker.get_job_lifecycle("job-1")
        record2 = tracker.get_job_lifecycle("job-2")
        
        assert record1["job_id"] == "job-1"
        assert record2["job_id"] == "job-2"


class TestLifecycleRetrieval:
    """Tests for retrieving lifecycle records."""

    def test_get_job_lifecycle_exists(self, tracker):
        """Can retrieve created lifecycle record."""
        job = create_test_job("test-1")
        created = tracker.create_job_lifecycle(job)
        retrieved = tracker.get_job_lifecycle("test-1")
        
        assert retrieved["job_id"] == created["job_id"]
        assert retrieved["current_state"] == created["current_state"]

    def test_get_job_lifecycle_not_found(self, tracker):
        """Returns None if job lifecycle doesn't exist."""
        result = tracker.get_job_lifecycle("nonexistent-job")
        assert result is None


class TestLifecycleTransitions:
    """Tests for state transitions."""

    def test_valid_transition_discovered_to_evaluated(self, tracker):
        """Can transition DISCOVERED → EVALUATED."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        
        updated = tracker.update_job_lifecycle_state(
            "test-1",
            "evaluate",
            JobLifecycleState.EVALUATED,
        )
        
        assert updated["current_state"] == "evaluated"
        assert updated["previous_state"] == "discovered"
        assert updated["last_event"] == "evaluate"

    def test_valid_transition_evaluated_to_shortlisted(self, tracker):
        """Can transition EVALUATED → SHORTLISTED."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        tracker.update_job_lifecycle_state("test-1", "evaluate", JobLifecycleState.EVALUATED)
        
        updated = tracker.update_job_lifecycle_state(
            "test-1",
            "shortlist",
            JobLifecycleState.SHORTLISTED,
        )
        
        assert updated["current_state"] == "shortlisted"
        assert updated["previous_state"] == "evaluated"

    def test_invalid_transition_rejected_to_applied_raises(self, tracker):
        """Invalid transition raises error without changing state."""
        job = create_test_job("test-1")
        lifecycle = tracker.create_job_lifecycle(job)
        
        # Manually set to REJECTED (simulate existing rejected job)
        from tinydb import Query
        tracker._jobs_lifecycle.update(
            {"current_state": "rejected"},
            Query().job_id == "test-1"
        )
        
        with pytest.raises(InvalidLifecycleTransitionError):
            tracker.update_job_lifecycle_state(
                "test-1",
                "apply",
                JobLifecycleState.APPLIED,
            )
        
        # State should not have changed
        record = tracker.get_job_lifecycle("test-1")
        assert record["current_state"] == "rejected"

    def test_full_application_pipeline(self, tracker):
        """Full pipeline: discovered → evaluated → shortlisted → applied."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        
        # DISCOVERED → EVALUATED
        tracker.update_job_lifecycle_state("test-1", "evaluate", JobLifecycleState.EVALUATED)
        record = tracker.get_job_lifecycle("test-1")
        assert record["current_state"] == "evaluated"
        
        # EVALUATED → SHORTLISTED
        tracker.update_job_lifecycle_state("test-1", "shortlist", JobLifecycleState.SHORTLISTED)
        record = tracker.get_job_lifecycle("test-1")
        assert record["current_state"] == "shortlisted"
        
        # SHORTLISTED → APPLICATION_PREPARED
        tracker.update_job_lifecycle_state(
            "test-1",
            "prepare_application",
            JobLifecycleState.APPLICATION_PREPARED,
        )
        record = tracker.get_job_lifecycle("test-1")
        assert record["current_state"] == "application_prepared"
        
        # APPLICATION_PREPARED → APPLIED
        tracker.update_job_lifecycle_state("test-1", "submit", JobLifecycleState.APPLIED)
        record = tracker.get_job_lifecycle("test-1")
        assert record["current_state"] == "applied"


class TestJobDataPersistence:
    """Tests for job data snapshots independent of lifecycle."""

    def test_update_job_data_stores_snapshot(self, tracker):
        """Can store job data snapshot."""
        job = create_test_job("test-1")
        
        record = tracker.update_job_data("test-1", job)
        
        assert record["job_id"] == "test-1"
        assert "last_updated" in record
        assert "job_listing" in record

    def test_update_job_data_without_resetting_state(self, tracker):
        """Updating job data doesn't reset lifecycle state."""
        job = create_test_job("test-1")
        
        # Create and transition to APPLIED
        tracker.create_job_lifecycle(job)
        tracker.update_job_lifecycle_state("test-1", "evaluate", JobLifecycleState.EVALUATED)
        tracker.update_job_lifecycle_state("test-1", "shortlist", JobLifecycleState.SHORTLISTED)
        tracker.update_job_lifecycle_state(
            "test-1",
            "prepare_application",
            JobLifecycleState.APPLICATION_PREPARED,
        )
        tracker.update_job_lifecycle_state("test-1", "submit", JobLifecycleState.APPLIED)
        
        # Update job data (e.g., salary changed)
        updated_job = create_test_job("test-1")
        updated_job.salary_min = 1000  # Different data
        tracker.update_job_data("test-1", updated_job)
        
        # Lifecycle state should not have changed
        lifecycle = tracker.get_job_lifecycle("test-1")
        assert lifecycle["current_state"] == "applied"

    def test_get_job_data_retrieves_snapshot(self, tracker):
        """Can retrieve stored job data."""
        job = create_test_job("test-1")
        stored = tracker.update_job_data("test-1", job)
        retrieved = tracker.get_job_data("test-1")
        
        assert retrieved["job_id"] == stored["job_id"]
        assert retrieved["job_listing"]["id"] == "test-1"

    def test_get_job_data_not_found(self, tracker):
        """Returns None if job data doesn't exist."""
        result = tracker.get_job_data("nonexistent")
        assert result is None


class TestJobsInState:
    """Tests for querying jobs by lifecycle state."""

    def test_get_jobs_in_discovered_state(self, tracker):
        """Can retrieve jobs in DISCOVERED state."""
        job1 = create_test_job("job-1")
        job2 = create_test_job("job-2")
        
        tracker.create_job_lifecycle(job1)  # DISCOVERED
        tracker.create_job_lifecycle(job2)  # DISCOVERED
        
        discovered = tracker.get_jobs_in_state(JobLifecycleState.DISCOVERED)
        assert len(discovered) == 2

    def test_get_jobs_in_applied_state(self, tracker):
        """Can retrieve only APPLIED jobs."""
        job1 = create_test_job("job-1")
        job2 = create_test_job("job-2")
        job3 = create_test_job("job-3")
        
        # job1: DISCOVERED → EVALUATED
        tracker.create_job_lifecycle(job1)
        tracker.update_job_lifecycle_state("job-1", "evaluate", JobLifecycleState.EVALUATED)
        
        # job2: DISCOVERED → EVALUATED → SHORTLISTED
        tracker.create_job_lifecycle(job2)
        tracker.update_job_lifecycle_state("job-2", "evaluate", JobLifecycleState.EVALUATED)
        tracker.update_job_lifecycle_state("job-2", "shortlist", JobLifecycleState.SHORTLISTED)
        
        # job3: DISCOVERED → EVALUATED → SHORTLISTED → APPLICATION_PREPARED → APPLIED
        tracker.create_job_lifecycle(job3)
        tracker.update_job_lifecycle_state("job-3", "evaluate", JobLifecycleState.EVALUATED)
        tracker.update_job_lifecycle_state("job-3", "shortlist", JobLifecycleState.SHORTLISTED)
        tracker.update_job_lifecycle_state(
            "job-3",
            "prepare_application",
            JobLifecycleState.APPLICATION_PREPARED,
        )
        tracker.update_job_lifecycle_state("job-3", "submit", JobLifecycleState.APPLIED)
        
        applied = tracker.get_jobs_in_state(JobLifecycleState.APPLIED)
        assert len(applied) == 1
        assert applied[0]["job_id"] == "job-3"


class TestTerminalStates:
    """Tests for terminal state behavior."""

    def test_rejected_is_terminal(self, tracker):
        """REJECTED state is terminal."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        
        # Manually set to REJECTED
        from tinydb import Query
        tracker._jobs_lifecycle.update(
            {"current_state": "rejected"},
            Query().job_id == "test-1"
        )
        
        # Any transition from REJECTED should fail
        with pytest.raises(InvalidLifecycleTransitionError):
            tracker.update_job_lifecycle_state(
                "test-1",
                "apply",
                JobLifecycleState.APPLIED,
            )

    def test_withdrawn_is_terminal(self, tracker):
        """WITHDRAWN state is terminal."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        
        # Manually set to WITHDRAWN
        from tinydb import Query
        tracker._jobs_lifecycle.update(
            {"current_state": "withdrawn"},
            Query().job_id == "test-1"
        )
        
        # Any transition from WITHDRAWN should fail
        with pytest.raises(InvalidLifecycleTransitionError):
            tracker.update_job_lifecycle_state(
                "test-1",
                "interview",
                JobLifecycleState.INTERVIEW,
            )

    def test_expired_is_terminal(self, tracker):
        """EXPIRED state is terminal."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        
        from tinydb import Query
        tracker._jobs_lifecycle.update(
            {"current_state": "expired"},
            Query().job_id == "test-1"
        )
        
        with pytest.raises(InvalidLifecycleTransitionError):
            tracker.update_job_lifecycle_state(
                "test-1",
                "apply",
                JobLifecycleState.APPLIED,
            )

    def test_closed_is_terminal(self, tracker):
        """CLOSED state is terminal."""
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        
        from tinydb import Query
        tracker._jobs_lifecycle.update(
            {"current_state": "closed"},
            Query().job_id == "test-1"
        )
        
        with pytest.raises(InvalidLifecycleTransitionError):
            tracker.update_job_lifecycle_state(
                "test-1",
                "apply",
                JobLifecycleState.APPLIED,
            )


class TestPersistenceAcrossRestarts:
    """Tests for state surviving database close/reopen."""

    def test_lifecycle_state_persists(self, temp_db):
        """Lifecycle state survives tracker close/reopen."""
        # Create tracker, add job, transition
        tracker = ApplicationTracker(db_path=temp_db)
        job = create_test_job("test-1")
        tracker.create_job_lifecycle(job)
        tracker.update_job_lifecycle_state("test-1", "evaluate", JobLifecycleState.EVALUATED)
        tracker.close()
        
        # Reopen tracker
        tracker2 = ApplicationTracker(db_path=temp_db)
        record = tracker2.get_job_lifecycle("test-1")
        tracker2.close()
        
        assert record["current_state"] == "evaluated"
        assert record["job_id"] == "test-1"

    def test_job_data_persists(self, temp_db):
        """Job data survives tracker close/reopen."""
        tracker = ApplicationTracker(db_path=temp_db)
        job = create_test_job("test-1")
        tracker.update_job_data("test-1", job)
        tracker.close()
        
        tracker2 = ApplicationTracker(db_path=temp_db)
        record = tracker2.get_job_data("test-1")
        tracker2.close()
        
        assert record["job_id"] == "test-1"
        assert record["job_listing"]["id"] == "test-1"
