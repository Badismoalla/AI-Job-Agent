"""
core/lifecycle
--------------
Job Lifecycle State Machine for tracking jobs from discovery to resolution.
"""

from .states import JobLifecycleState, JobLifecycle
from .transitions import (
    is_valid_transition,
    get_allowed_next_states,
    is_terminal,
    is_hard_terminal,
    TERMINAL_STATES,
    SOFT_TERMINAL_STATES,
    VALID_TRANSITIONS,
)

__all__ = [
    "JobLifecycleState",
    "JobLifecycle",
    "is_valid_transition",
    "get_allowed_next_states",
    "is_terminal",
    "is_hard_terminal",
    "TERMINAL_STATES",
    "SOFT_TERMINAL_STATES",
    "VALID_TRANSITIONS",
]
