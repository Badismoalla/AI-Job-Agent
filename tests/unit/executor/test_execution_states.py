"""
Phase 2D: Execution state models and result handling tests.

Tests for ExecutionMode, ExecutionStatus, ExecutionResult enums and dataclasses.
"""

import pytest
from datetime import datetime, timezone
from dataclasses import fields

# These imports will fail until core/executor/states.py is created
# That's expected TDD behavior
try:
    from core.executor.states import (
        ExecutionMode,
        ExecutionStatus,
        ExecutionResult,
        ValidationResult,
    )
    from core.models import utc_now
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


class TestExecutionModeEnum:
    """Tests for ExecutionMode enum."""

    def test_execution_mode_values(self):
        """All three execution modes defined."""
        assert ExecutionMode.PREPARE_ONLY.value == "prepare_only"
        assert ExecutionMode.REVIEW_REQUIRED.value == "review_required"
        assert ExecutionMode.AUTO_SUBMIT.value == "auto_submit"

    def test_execution_mode_count(self):
        """Exactly 3 modes defined."""
        modes = list(ExecutionMode)
        assert len(modes) == 3

    def test_execution_mode_is_string_enum(self):
        """ExecutionMode can be compared as strings."""
        mode = ExecutionMode.PREPARE_ONLY
        assert mode == "prepare_only"


class TestExecutionStatusEnum:
    """Tests for ExecutionStatus enum."""

    def test_success_status(self):
        """SUCCESS status defined."""
        assert ExecutionStatus.SUCCESS.value == "success"

    def test_validation_failure_statuses(self):
        """Pre-browser failure statuses defined."""
        assert ExecutionStatus.VALIDATION_FAILED.value == "validation_failed"
        assert ExecutionStatus.NOT_AUTHORIZED.value == "not_authorized"

    def test_unsupported_flow_status(self):
        """UNSUPPORTED_FLOW status for workflows executor cannot handle."""
        assert ExecutionStatus.UNSUPPORTED_FLOW.value == "unsupported_flow"

    def test_security_requirement_statuses(self):
        """CAPTCHA, MFA, authentication statuses defined."""
        assert ExecutionStatus.AUTHENTICATION_REQUIRED.value == "authentication_required"
        assert ExecutionStatus.CAPTCHA_REQUIRED.value == "captcha_required"
        assert ExecutionStatus.MFA_REQUIRED.value == "mfa_required"

    def test_form_error_status(self):
        """FORM_ERROR status for form filling failures."""
        assert ExecutionStatus.FORM_ERROR.value == "form_error"

    def test_review_required_status(self):
        """
        REVIEW_REQUIRED status: required candidate data is missing and
        cannot be safely inferred or fabricated (e.g. work authorization,
        salary expectation, visa sponsorship answer). Distinct from the
        REVIEW_REQUIRED *execution mode* — this is an outcome, not a mode.
        """
        assert ExecutionStatus.REVIEW_REQUIRED.value == "review_required"

    def test_submission_statuses(self):
        """Submission outcome statuses defined."""
        assert ExecutionStatus.SUBMISSION_FAILED.value == "submission_failed"
        assert ExecutionStatus.SUBMISSION_UNKNOWN.value == "submission_unknown"

    def test_browser_error_status(self):
        """BROWSER_ERROR status for browser crashes/timeouts."""
        assert ExecutionStatus.BROWSER_ERROR.value == "browser_error"

    def test_user_action_statuses(self):
        """USER_PAUSED and USER_CANCELLED statuses defined."""
        assert ExecutionStatus.USER_PAUSED.value == "user_paused"
        assert ExecutionStatus.USER_CANCELLED.value == "user_cancelled"


class TestExecutionResultModel:
    """Tests for ExecutionResult dataclass."""

    def test_execution_result_required_fields(self):
        """ExecutionResult requires job_id, package_id, platform, execution_mode, status."""
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.PREPARE_ONLY,
            status=ExecutionStatus.SUCCESS,
        )
        assert result.job_id == "test-job-1"
        assert result.package_id == "pkg-1"
        assert result.platform == "greenhouse"
        assert result.execution_mode == ExecutionMode.PREPARE_ONLY
        assert result.status == ExecutionStatus.SUCCESS

    def test_execution_result_optional_fields_default_none(self):
        """Optional fields default to None or empty."""
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.PREPARE_ONLY,
            status=ExecutionStatus.SUCCESS,
        )
        assert result.external_application_id is None
        assert result.submission_url is None
        assert result.confirmation_details is None
        assert result.workflow_error is None
        assert result.error_reason is None
        assert result.validation_errors == []

    def test_execution_result_with_success_details(self):
        """Can populate success details."""
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.AUTO_SUBMIT,
            status=ExecutionStatus.SUCCESS,
            external_application_id="app-12345",
            submission_url="https://example.com/confirm?app_id=12345",
            confirmation_details={"confirmation_page": "found", "app_id": "12345"},
        )
        assert result.external_application_id == "app-12345"
        assert result.submission_url is not None
        assert result.confirmation_details["app_id"] == "12345"

    def test_execution_result_with_validation_errors(self):
        """Can populate validation errors."""
        errors = ["Missing CV", "Invalid JSON in answers"]
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.PREPARE_ONLY,
            status=ExecutionStatus.VALIDATION_FAILED,
            validation_errors=errors,
        )
        assert result.validation_errors == errors
        assert len(result.validation_errors) == 2

    def test_execution_result_with_workflow_error(self):
        """Can populate workflow error and reason."""
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.AUTO_SUBMIT,
            status=ExecutionStatus.CAPTCHA_REQUIRED,
            workflow_error="CAPTCHA detected on form",
            error_reason="reCAPTCHA v2 iframe found",
        )
        assert result.workflow_error == "CAPTCHA detected on form"
        assert result.error_reason == "reCAPTCHA v2 iframe found"

    def test_execution_result_timestamps(self):
        """started_at set automatically, completed_at optional."""
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.PREPARE_ONLY,
            status=ExecutionStatus.SUCCESS,
        )
        assert result.started_at is not None
        assert isinstance(result.started_at, datetime)
        assert result.completed_at is None

    def test_execution_result_duration_calculation(self):
        """Duration calculated from timestamps."""
        now = utc_now()
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.PREPARE_ONLY,
            status=ExecutionStatus.SUCCESS,
            started_at=now,
            completed_at=now,  # Same time
        )
        assert result.duration_seconds == 0.0

    def test_execution_result_human_review_flagging(self):
        """human_review_required and retry_safe fields."""
        result = ExecutionResult(
            job_id="test-job-1",
            package_id="pkg-1",
            platform="greenhouse",
            execution_mode=ExecutionMode.AUTO_SUBMIT,
            status=ExecutionStatus.SUBMISSION_UNKNOWN,
            human_review_required=True,
            retry_safe=False,
        )
        assert result.human_review_required is True
        assert result.retry_safe is False


class TestValidationResultModel:
    """Tests for ValidationResult dataclass."""

    def test_validation_result_valid_package(self):
        """Valid package result."""
        result = ValidationResult(
            valid=True,
            errors=[],
            warnings=[],
            reason=None,
        )
        assert result.valid is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_validation_result_with_errors(self):
        """Validation result with errors."""
        errors = ["Missing CV file", "Invalid JSON in answers.json"]
        result = ValidationResult(
            valid=False,
            errors=errors,
            warnings=[],
            reason="Package validation failed",
        )
        assert result.valid is False
        assert len(result.errors) == 2
        assert result.reason == "Package validation failed"

    def test_validation_result_with_warnings(self):
        """Validation result with warnings (still valid)."""
        result = ValidationResult(
            valid=True,
            errors=[],
            warnings=["CV file not found but job doesn't require it"],
            reason=None,
        )
        assert result.valid is True
        assert len(result.warnings) == 1

    def test_validation_result_all_errors_collected(self):
        """Multiple validation errors collected, not failed early."""
        errors = [
            "Missing cover_letter.md",
            "Missing recruiter_message.md",
            "Invalid metadata.json",
            "Wrong job ID in metadata",
        ]
        result = ValidationResult(
            valid=False,
            errors=errors,
            warnings=[],
            reason="Multiple validation issues",
        )
        assert len(result.errors) == 4
