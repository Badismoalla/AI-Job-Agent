"""
core/executor/states.py
------------------------
State models for Phase 2D application execution.

ExecutionMode governs what an executor is *allowed* to do:
    PREPARE_ONLY    -> never touches the browser, never submits
    REVIEW_REQUIRED -> fills the form for human review, never submits
    AUTO_SUBMIT     -> may submit, only when every authorization
                       condition is satisfied

ExecutionStatus is the *actual outcome* of a single execute() call.
Note the deliberate distinction between the REVIEW_REQUIRED execution
*mode* and the REVIEW_REQUIRED *status*: the mode is a policy the caller
chooses; the status is an outcome the executor reports when required
candidate data is missing and cannot be safely inferred or fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from core.models import utc_now


class ExecutionMode(str, Enum):
    PREPARE_ONLY = "prepare_only"
    REVIEW_REQUIRED = "review_required"
    AUTO_SUBMIT = "auto_submit"


class ExecutionStatus(str, Enum):
    # Success
    SUCCESS = "success"

    # Pre-browser failures
    VALIDATION_FAILED = "validation_failed"
    NOT_AUTHORIZED = "not_authorized"

    # Browser / workflow failures
    UNSUPPORTED_FLOW = "unsupported_flow"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CAPTCHA_REQUIRED = "captcha_required"
    MFA_REQUIRED = "mfa_required"
    FORM_ERROR = "form_error"

    # Missing candidate data that must not be fabricated
    REVIEW_REQUIRED = "review_required"

    # Submission uncertainty
    SUBMISSION_FAILED = "submission_failed"
    SUBMISSION_UNKNOWN = "submission_unknown"

    # System failures
    BROWSER_ERROR = "browser_error"

    # User actions
    USER_PAUSED = "user_paused"
    USER_CANCELLED = "user_cancelled"


@dataclass
class ExecutionResult:
    """
    Complete, structured outcome of a single executor.execute() call.
    Always returned; callers check `status` rather than catching exceptions.
    """

    job_id: str
    package_id: str
    platform: str
    execution_mode: ExecutionMode
    status: ExecutionStatus

    # Success details
    external_application_id: str | None = None
    submission_url: str | None = None
    confirmation_details: dict | None = None

    # Failure details
    validation_errors: list[str] = field(default_factory=list)
    workflow_error: str | None = None
    error_reason: str | None = None

    # Timing
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    duration_seconds: float | None = None

    # Audit / safety flags
    human_review_required: bool = False
    retry_safe: bool = False

    def __post_init__(self) -> None:
        if self.completed_at is not None and self.started_at is not None:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()

    def finalize(self) -> "ExecutionResult":
        """Stamp completed_at now and (re)compute duration. Returns self for chaining."""
        self.completed_at = utc_now()
        self.duration_seconds = (self.completed_at - self.started_at).total_seconds()
        return self


@dataclass
class ValidationResult:
    """Outcome of PackageValidator.validate()."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None
