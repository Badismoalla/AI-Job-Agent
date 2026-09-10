"""
core/lifecycle/transitions.py
-----------------------------
State transition validation: 16 valid transitions, explicit blocking of invalid.
"""

from typing import Dict, Set, Tuple, Optional
from core.lifecycle.states import JobLifecycleState


# Terminal states: no outbound transitions allowed
TERMINAL_STATES: Set[JobLifecycleState] = {
    JobLifecycleState.REJECTED,
    JobLifecycleState.WITHDRAWN,
    JobLifecycleState.EXPIRED,
    JobLifecycleState.CLOSED,
}

# Soft terminal: SKIPPED (abandoned, no outbound transitions)
SOFT_TERMINAL_STATES: Set[JobLifecycleState] = {
    JobLifecycleState.SKIPPED,
}

# Valid transitions: current_state → {(event, next_state), ...}
VALID_TRANSITIONS: Dict[JobLifecycleState, Set[Tuple[str, JobLifecycleState]]] = {
    JobLifecycleState.DISCOVERED: {
        ("evaluate", JobLifecycleState.EVALUATED),
    },
    JobLifecycleState.EVALUATED: {
        ("shortlist", JobLifecycleState.SHORTLISTED),
        ("skip", JobLifecycleState.SKIPPED),
    },
    JobLifecycleState.SHORTLISTED: {
        ("prepare_application", JobLifecycleState.APPLICATION_PREPARED),
        ("reject", JobLifecycleState.SKIPPED),
    },
    JobLifecycleState.APPLICATION_PREPARED: {
        ("submit", JobLifecycleState.APPLIED),
        ("cancel", JobLifecycleState.SKIPPED),
    },
    JobLifecycleState.APPLIED: {
        ("recruiter_response", JobLifecycleState.SCREENING),
        ("rejection", JobLifecycleState.REJECTED),
        ("withdrawn", JobLifecycleState.WITHDRAWN),
        ("no_response", JobLifecycleState.EXPIRED),
    },
    JobLifecycleState.SCREENING: {
        ("interview_scheduled", JobLifecycleState.INTERVIEW),
        ("rejection", JobLifecycleState.REJECTED),
    },
    JobLifecycleState.INTERVIEW: {
        ("offer_received", JobLifecycleState.OFFER),
        ("rejection", JobLifecycleState.REJECTED),
        ("next_round", JobLifecycleState.INTERVIEW),  # Multi-round loop
    },
    JobLifecycleState.OFFER: {
        ("accepted", JobLifecycleState.CLOSED),
        ("withdrawn", JobLifecycleState.WITHDRAWN),
    },
    # Terminal and soft-terminal states: no outbound transitions
    JobLifecycleState.REJECTED: set(),
    JobLifecycleState.WITHDRAWN: set(),
    JobLifecycleState.EXPIRED: set(),
    JobLifecycleState.CLOSED: set(),
    JobLifecycleState.SKIPPED: set(),
}


def is_valid_transition(
    current_state: JobLifecycleState,
    event: str,
    next_state: JobLifecycleState,
) -> bool:
    """
    Check if a state transition is valid.
    
    Args:
        current_state: Current JobLifecycleState
        event: Event name that triggers transition
        next_state: Proposed next state
    
    Returns:
        True if transition is valid, False otherwise
    """
    if current_state not in VALID_TRANSITIONS:
        return False
    
    valid_next = VALID_TRANSITIONS[current_state]
    return (event, next_state) in valid_next


def get_allowed_next_states(
    current_state: JobLifecycleState,
) -> Set[Tuple[str, JobLifecycleState]]:
    """
    Get all valid (event, next_state) tuples from current state.
    
    Args:
        current_state: Current JobLifecycleState
    
    Returns:
        Set of valid (event, next_state) tuples, or empty set if terminal
    """
    return VALID_TRANSITIONS.get(current_state, set()).copy()


def is_terminal(state: JobLifecycleState) -> bool:
    """
    Check if a state is terminal (hard or soft).
    
    Terminal states:
    - Hard: REJECTED, WITHDRAWN, EXPIRED, CLOSED (no recovery)
    - Soft: SKIPPED (abandoned, requires explicit re-engagement)
    """
    return state in TERMINAL_STATES or state in SOFT_TERMINAL_STATES


def is_hard_terminal(state: JobLifecycleState) -> bool:
    """Check if a state is a hard terminal (no recovery possible)."""
    return state in TERMINAL_STATES
