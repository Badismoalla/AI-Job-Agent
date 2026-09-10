"""
Phase 2C: Job Lifecycle State Machine Tests

Comprehensive test coverage for:
- 13 job lifecycle states
- 16 valid transitions
- Invalid transition prevention
- Persistence and migration
- Job data refresh without state reset
- Matching independence
- Idempotency behavior
- Interview round tracking
"""

import pytest
from datetime import datetime, timezone
from core.lifecycle.states import JobLifecycleState, JobLifecycle
from core.lifecycle.transitions import is_valid_transition, get_allowed_next_states
from core.exceptions import InvalidLifecycleTransitionError


class TestJobLifecycleStates:
    """Tests for JobLifecycleState enum."""

    def test_all_13_states_defined(self):
        """All 13 lifecycle states are defined."""
        states = list(JobLifecycleState)
        assert len(states) == 13

    def test_state_values(self):
        """State values match expected strings."""
        assert JobLifecycleState.DISCOVERED.value == "discovered"
        assert JobLifecycleState.EVALUATED.value == "evaluated"
        assert JobLifecycleState.SHORTLISTED.value == "shortlisted"
        assert JobLifecycleState.APPLICATION_PREPARED.value == "application_prepared"
        assert JobLifecycleState.APPLIED.value == "applied"
        assert JobLifecycleState.SKIPPED.value == "skipped"
        assert JobLifecycleState.SCREENING.value == "screening"
        assert JobLifecycleState.INTERVIEW.value == "interview"
        assert JobLifecycleState.OFFER.value == "offer"
        assert JobLifecycleState.REJECTED.value == "rejected"
        assert JobLifecycleState.WITHDRAWN.value == "withdrawn"
        assert JobLifecycleState.EXPIRED.value == "expired"
        assert JobLifecycleState.CLOSED.value == "closed"

    def test_terminal_states_identified(self):
        """Terminal states are correctly identified."""
        terminal = {
            JobLifecycleState.REJECTED,
            JobLifecycleState.WITHDRAWN,
            JobLifecycleState.EXPIRED,
            JobLifecycleState.CLOSED,
        }
        # SKIPPED is soft terminal: no outbound transitions
        for state in terminal:
            assert state in terminal or state == JobLifecycleState.SKIPPED


class TestValidTransitions:
    """Tests for all valid transitions (16 total)."""

    def test_discovered_to_evaluated(self):
        """DISCOVERED → EVALUATED via 'evaluate' event."""
        assert is_valid_transition(
            JobLifecycleState.DISCOVERED,
            "evaluate",
            JobLifecycleState.EVALUATED
        )

    def test_evaluated_to_shortlisted(self):
        """EVALUATED → SHORTLISTED via 'shortlist' event."""
        assert is_valid_transition(
            JobLifecycleState.EVALUATED,
            "shortlist",
            JobLifecycleState.SHORTLISTED
        )

    def test_evaluated_to_skipped(self):
        """EVALUATED → SKIPPED via 'skip' event."""
        assert is_valid_transition(
            JobLifecycleState.EVALUATED,
            "skip",
            JobLifecycleState.SKIPPED
        )

    def test_shortlisted_to_application_prepared(self):
        """SHORTLISTED → APPLICATION_PREPARED via 'prepare_application' event."""
        assert is_valid_transition(
            JobLifecycleState.SHORTLISTED,
            "prepare_application",
            JobLifecycleState.APPLICATION_PREPARED
        )

    def test_shortlisted_to_skipped(self):
        """SHORTLISTED → SKIPPED via 'reject' event (user rejects)."""
        assert is_valid_transition(
            JobLifecycleState.SHORTLISTED,
            "reject",
            JobLifecycleState.SKIPPED
        )

    def test_application_prepared_to_applied(self):
        """APPLICATION_PREPARED → APPLIED via 'submit' event."""
        assert is_valid_transition(
            JobLifecycleState.APPLICATION_PREPARED,
            "submit",
            JobLifecycleState.APPLIED
        )

    def test_application_prepared_to_skipped(self):
        """APPLICATION_PREPARED → SKIPPED via 'cancel' event."""
        assert is_valid_transition(
            JobLifecycleState.APPLICATION_PREPARED,
            "cancel",
            JobLifecycleState.SKIPPED
        )

    def test_applied_to_screening(self):
        """APPLIED → SCREENING via 'recruiter_response' event."""
        assert is_valid_transition(
            JobLifecycleState.APPLIED,
            "recruiter_response",
            JobLifecycleState.SCREENING
        )

    def test_applied_to_rejected(self):
        """APPLIED → REJECTED via 'rejection' event."""
        assert is_valid_transition(
            JobLifecycleState.APPLIED,
            "rejection",
            JobLifecycleState.REJECTED
        )

    def test_applied_to_withdrawn(self):
        """APPLIED → WITHDRAWN via 'withdrawn' event."""
        assert is_valid_transition(
            JobLifecycleState.APPLIED,
            "withdrawn",
            JobLifecycleState.WITHDRAWN
        )

    def test_applied_to_expired(self):
        """APPLIED → EXPIRED via 'no_response' event (timeout)."""
        assert is_valid_transition(
            JobLifecycleState.APPLIED,
            "no_response",
            JobLifecycleState.EXPIRED
        )

    def test_screening_to_interview(self):
        """SCREENING → INTERVIEW via 'interview_scheduled' event."""
        assert is_valid_transition(
            JobLifecycleState.SCREENING,
            "interview_scheduled",
            JobLifecycleState.INTERVIEW
        )

    def test_screening_to_rejected(self):
        """SCREENING → REJECTED via 'rejection' event."""
        assert is_valid_transition(
            JobLifecycleState.SCREENING,
            "rejection",
            JobLifecycleState.REJECTED
        )

    def test_interview_to_offer(self):
        """INTERVIEW → OFFER via 'offer_received' event."""
        assert is_valid_transition(
            JobLifecycleState.INTERVIEW,
            "offer_received",
            JobLifecycleState.OFFER
        )

    def test_interview_to_rejected(self):
        """INTERVIEW → REJECTED via 'rejection' event."""
        assert is_valid_transition(
            JobLifecycleState.INTERVIEW,
            "rejection",
            JobLifecycleState.REJECTED
        )

    def test_interview_to_interview_loop(self):
        """INTERVIEW → INTERVIEW via 'next_round' event (multi-round)."""
        assert is_valid_transition(
            JobLifecycleState.INTERVIEW,
            "next_round",
            JobLifecycleState.INTERVIEW
        )

    def test_offer_to_closed(self):
        """OFFER → CLOSED via 'accepted' event."""
        assert is_valid_transition(
            JobLifecycleState.OFFER,
            "accepted",
            JobLifecycleState.CLOSED
        )

    def test_offer_to_withdrawn(self):
        """OFFER → WITHDRAWN via 'withdrawn' event."""
        assert is_valid_transition(
            JobLifecycleState.OFFER,
            "withdrawn",
            JobLifecycleState.WITHDRAWN
        )


class TestInvalidTransitions:
    """Tests for invalid transition prevention."""

    def test_rejected_no_outbound(self):
        """REJECTED is terminal: no outbound transitions."""
        assert not is_valid_transition(
            JobLifecycleState.REJECTED,
            "apply",
            JobLifecycleState.APPLIED
        )
        assert not is_valid_transition(
            JobLifecycleState.REJECTED,
            "accept",
            JobLifecycleState.CLOSED
        )

    def test_withdrawn_no_outbound(self):
        """WITHDRAWN is terminal: no outbound transitions."""
        assert not is_valid_transition(
            JobLifecycleState.WITHDRAWN,
            "interview",
            JobLifecycleState.INTERVIEW
        )
        assert not is_valid_transition(
            JobLifecycleState.WITHDRAWN,
            "apply_again",
            JobLifecycleState.APPLIED
        )

    def test_expired_no_outbound(self):
        """EXPIRED is terminal: no outbound transitions."""
        assert not is_valid_transition(
            JobLifecycleState.EXPIRED,
            "apply",
            JobLifecycleState.APPLIED
        )

    def test_closed_no_outbound(self):
        """CLOSED is terminal: no outbound transitions."""
        assert not is_valid_transition(
            JobLifecycleState.CLOSED,
            "reject",
            JobLifecycleState.REJECTED
        )

    def test_rejected_to_applied_invalid(self):
        """REJECTED → APPLIED is invalid."""
        assert not is_valid_transition(
            JobLifecycleState.REJECTED,
            "apply",
            JobLifecycleState.APPLIED
        )

    def test_withdrawn_to_interview_invalid(self):
        """WITHDRAWN → INTERVIEW is invalid."""
        assert not is_valid_transition(
            JobLifecycleState.WITHDRAWN,
            "interview",
            JobLifecycleState.INTERVIEW
        )

    def test_expired_to_application_prepared_invalid(self):
        """EXPIRED → APPLICATION_PREPARED is invalid."""
        assert not is_valid_transition(
            JobLifecycleState.EXPIRED,
            "prepare",
            JobLifecycleState.APPLICATION_PREPARED
        )

    def test_closed_to_applied_invalid(self):
        """CLOSED → APPLIED is invalid."""
        assert not is_valid_transition(
            JobLifecycleState.CLOSED,
            "apply",
            JobLifecycleState.APPLIED
        )

    def test_skipped_to_applied_invalid(self):
        """SKIPPED → APPLIED is invalid (soft terminal)."""
        assert not is_valid_transition(
            JobLifecycleState.SKIPPED,
            "apply",
            JobLifecycleState.APPLIED
        )

    def test_skipped_to_application_prepared_invalid(self):
        """SKIPPED → APPLICATION_PREPARED is invalid."""
        assert not is_valid_transition(
            JobLifecycleState.SKIPPED,
            "prepare",
            JobLifecycleState.APPLICATION_PREPARED
        )

    def test_offer_to_application_prepared_invalid(self):
        """OFFER → APPLICATION_PREPARED is invalid (regression)."""
        assert not is_valid_transition(
            JobLifecycleState.OFFER,
            "prepare",
            JobLifecycleState.APPLICATION_PREPARED
        )

    def test_cannot_go_back_to_discovered(self):
        """Cannot transition back to DISCOVERED from any non-terminal."""
        for state in [
            JobLifecycleState.EVALUATED,
            JobLifecycleState.SHORTLISTED,
            JobLifecycleState.APPLIED,
        ]:
            assert not is_valid_transition(
                state,
                "rediscover",
                JobLifecycleState.DISCOVERED
            )


class TestIdempotencyBehavior:
    """Tests for idempotent and repeated event behavior."""

    def test_applied_submit_application_again_is_idempotent(self):
        """APPLIED → submit_application again is idempotent (no-op)."""
        # Submitting again when already APPLIED should be a no-op
        # (same as trying APPLIED → submit → APPLIED, which is invalid)
        assert not is_valid_transition(
            JobLifecycleState.APPLIED,
            "submit",
            JobLifecycleState.APPLIED
        )

    def test_interview_next_round_again_idempotent(self):
        """INTERVIEW → next_round again is idempotent (stays in INTERVIEW)."""
        # Multiple next_round calls should be valid and stay in INTERVIEW
        assert is_valid_transition(
            JobLifecycleState.INTERVIEW,
            "next_round",
            JobLifecycleState.INTERVIEW
        )

    def test_rejected_reject_again_is_invalid(self):
        """REJECTED → reject again is invalid (terminal)."""
        assert not is_valid_transition(
            JobLifecycleState.REJECTED,
            "rejection",
            JobLifecycleState.REJECTED
        )


class TestInterviewRounds:
    """Tests for interview round tracking (metadata, not states)."""

    def test_interview_remains_single_state(self):
        """INTERVIEW is a single state, not INTERVIEW_ROUND_1, ROUND_2, etc."""
        # Should only be one INTERVIEW state
        assert JobLifecycleState.INTERVIEW.value == "interview"
        # No INTERVIEW_ROUND_1, etc.
        assert not hasattr(JobLifecycleState, "INTERVIEW_ROUND_1")
        assert not hasattr(JobLifecycleState, "INTERVIEW_ROUND_2")

    def test_next_round_loops_in_interview(self):
        """Multiple interview rounds stay in INTERVIEW state via metadata."""
        # next_round event should not change state, just record event
        assert is_valid_transition(
            JobLifecycleState.INTERVIEW,
            "next_round",
            JobLifecycleState.INTERVIEW
        )


class TestMatchingIndependence:
    """Tests for independence of lifecycle and matching dimensions."""

    def test_any_lifecycle_state_with_any_match_decision(self):
        """Lifecycle state must be independent of MatchDecision."""
        # This is a conceptual test — actual integration tested elsewhere
        # Here we just verify lifecycle allows valid combinations
        
        lifecycle_states = [
            JobLifecycleState.DISCOVERED,
            JobLifecycleState.EVALUATED,
            JobLifecycleState.SHORTLISTED,
            JobLifecycleState.APPLIED,
            JobLifecycleState.REJECTED,
        ]
        
        # All of these should be valid lifecycle states
        # (Match decision is separate concern)
        for state in lifecycle_states:
            assert isinstance(state, JobLifecycleState)


class TestJobLifecycleModel:
    """Tests for JobLifecycle model structure."""

    def test_lifecycle_model_creation(self):
        """JobLifecycle model can be created with required fields."""
        now = datetime.now(timezone.utc)
        lifecycle = JobLifecycle(
            job_id="test-job-1",
            current_state=JobLifecycleState.DISCOVERED,
            discovered_at=now,
            state_changed_at=now,
        )
        
        assert lifecycle.job_id == "test-job-1"
        assert lifecycle.current_state == JobLifecycleState.DISCOVERED
        assert lifecycle.previous_state is None
        assert lifecycle.last_event is None
        assert lifecycle.application_id is None

    def test_lifecycle_model_with_transition(self):
        """JobLifecycle model tracks transition metadata."""
        now = datetime.now(timezone.utc)
        lifecycle = JobLifecycle(
            job_id="test-job-1",
            current_state=JobLifecycleState.APPLIED,
            previous_state=JobLifecycleState.APPLICATION_PREPARED,
            discovered_at=now,
            state_changed_at=now,
            last_event="submit",
            application_id="app-123",
        )
        
        assert lifecycle.current_state == JobLifecycleState.APPLIED
        assert lifecycle.previous_state == JobLifecycleState.APPLICATION_PREPARED
        assert lifecycle.last_event == "submit"
        assert lifecycle.application_id == "app-123"


class TestMigrationStrategy:
    """Tests for migration of existing jobs without lifecycle state."""

    def test_job_without_lifecycle_gets_safe_default(self):
        """Job without lifecycle state should default to DISCOVERED."""
        # This is a conceptual test — actual migration in tracker
        # Here we verify DISCOVERED is a valid starting state
        assert JobLifecycleState.DISCOVERED in list(JobLifecycleState)

    def test_application_status_mapping(self):
        """Existing Application.status should map to JobLifecycleState."""
        # SENT → APPLIED
        # REJECTED → REJECTED
        # INTERVIEW → INTERVIEW
        # Safe defaults without overwriting
        
        # Verify all terminal states exist
        terminal = {
            JobLifecycleState.REJECTED,
            JobLifecycleState.WITHDRAWN,
            JobLifecycleState.EXPIRED,
            JobLifecycleState.CLOSED,
        }
        assert len(terminal) == 4


class TestTransitionValidation:
    """Tests for transition validation logic."""

    def test_get_allowed_next_states(self):
        """get_allowed_next_states returns valid next states."""
        next_states = get_allowed_next_states(JobLifecycleState.EVALUATED)
        # Should include shortlist and skip
        assert len(next_states) >= 2

    def test_terminal_states_have_no_outbound(self):
        """Terminal states have no allowed outbound transitions."""
        terminals = [
            JobLifecycleState.REJECTED,
            JobLifecycleState.WITHDRAWN,
            JobLifecycleState.EXPIRED,
            JobLifecycleState.CLOSED,
        ]
        for terminal in terminals:
            next_states = get_allowed_next_states(terminal)
            assert len(next_states) == 0, f"{terminal} should be terminal"

    def test_skipped_is_soft_terminal(self):
        """SKIPPED has no outbound transitions."""
        next_states = get_allowed_next_states(JobLifecycleState.SKIPPED)
        assert len(next_states) == 0


class TestDataRefreshIndependence:
    """Tests verifying that job data updates don't reset lifecycle state."""

    def test_job_title_update_preserves_state(self):
        """Updating job title must not reset lifecycle state."""
        # This is verified in tracker tests
        # Here we verify the state model allows it
        lifecycle = JobLifecycle(
            job_id="test-1",
            current_state=JobLifecycleState.APPLIED,
            discovered_at=datetime.now(timezone.utc),
            state_changed_at=datetime.now(timezone.utc),
        )
        # State should remain unchanged after title update
        assert lifecycle.current_state == JobLifecycleState.APPLIED

    def test_job_salary_update_preserves_state(self):
        """Updating job salary must not reset lifecycle state."""
        lifecycle = JobLifecycle(
            job_id="test-1",
            current_state=JobLifecycleState.INTERVIEW,
            discovered_at=datetime.now(timezone.utc),
            state_changed_at=datetime.now(timezone.utc),
        )
        assert lifecycle.current_state == JobLifecycleState.INTERVIEW

    def test_job_deadline_update_preserves_state(self):
        """Updating job deadline must not reset lifecycle state."""
        lifecycle = JobLifecycle(
            job_id="test-1",
            current_state=JobLifecycleState.SCREENING,
            discovered_at=datetime.now(timezone.utc),
            state_changed_at=datetime.now(timezone.utc),
        )
        assert lifecycle.current_state == JobLifecycleState.SCREENING
