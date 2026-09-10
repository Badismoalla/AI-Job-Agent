"""
Phase 2D: Submission confirmation tests.

Critical invariant:
    click submit != confirmed application

Only transition APPLICATION_PREPARED -> APPLIED after reliable confirmation.
Never mark APPLIED merely because the submit button was clicked.
"""

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


class TestSubmissionConfirmationBehavior:
    """Tests for submission confirmation detection and lifecycle updates."""

    @pytest.mark.asyncio
    async def test_success_with_confirmation_transitions_to_applied(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """
        mock_page's default content ("Thank you for applying!") is a
        recognizable confirmation signal -> SUCCESS + APPLIED transition.
        """
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        mock_tracker.update_job_lifecycle_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_with_uncertain_confirmation_returns_unknown(
        self, greenhouse_job, valid_package_dir, mock_page_unconfirmed, mock_tracker
    ):
        """
        Submit is clicked, but the page shows no success signal, no
        redirect, no captured application ID -> SUBMISSION_UNKNOWN, and
        the lifecycle must NOT transition to APPLIED.
        """
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        mock_tracker.update_job_lifecycle_state.assert_not_called()
        assert result.human_review_required is True

    @pytest.mark.asyncio
    async def test_submit_failed_form_error_returns_submission_failed(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """A visible form validation error after submit -> SUBMISSION_FAILED, no transition."""
        mock_page.content.return_value = "<html><body>Error: required field missing</body></html>"
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUBMISSION_FAILED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_auto_retry_on_submission_unknown(
        self, greenhouse_job, valid_package_dir, mock_page_unconfirmed, mock_tracker
    ):
        """
        A single execute() call resulting in SUBMISSION_UNKNOWN must not
        itself click submit more than once (no internal blind retry of
        the final submission).
        """
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        submit_clicks = [c for c in mock_page_unconfirmed.click.call_args_list
                          if c.args and "submit" in str(c.args[0]).lower()]
        assert len(submit_clicks) <= 1
        assert result.retry_safe is False


class TestLifecycleTransitionGuards:
    """Lifecycle transitions are guarded strictly by ExecutionStatus."""

    @pytest.mark.asyncio
    async def test_validation_failed_no_transition(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        (valid_package_dir / "cover_letter.md").unlink()
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.VALIDATION_FAILED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_authorized_no_transition(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_tracker_already_applied.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_captcha_required_no_transition(self, greenhouse_job, valid_package_dir, mock_page_with_captcha, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_captcha,
        )
        assert result.status == ExecutionStatus.CAPTCHA_REQUIRED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_mfa_required_no_transition(self, greenhouse_job, valid_package_dir, mock_page_with_mfa, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_mfa,
        )
        assert result.status == ExecutionStatus.MFA_REQUIRED
        mock_tracker.update_job_lifecycle_state.assert_not_called()


class TestConfirmationDetectionMechanisms:
    """Tests for how the executor detects submission success."""

    @pytest.mark.asyncio
    async def test_confirm_via_success_message_text(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        mock_page.content.return_value = "<html><body>Your application has been received. Thank you!</body></html>"
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_cannot_confirm_without_any_signal_returns_unknown(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """Neutral/blank page content after submit, no URL change -> SUBMISSION_UNKNOWN, never guessed as SUCCESS."""
        mock_page.content.return_value = "<html><body></body></html>"
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        assert result.status != ExecutionStatus.SUCCESS


class TestConfirmationIsNotGuessing:
    """Timeouts and ambiguous browser states must never be treated as success."""

    @pytest.mark.asyncio
    async def test_navigation_error_after_submit_is_not_confirmation(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """If page.content() raises after submit (e.g. navigation error), do not assume success."""
        mock_page.content.side_effect = Exception("Navigation timeout")
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status in (ExecutionStatus.SUBMISSION_UNKNOWN, ExecutionStatus.BROWSER_ERROR)
        assert result.status != ExecutionStatus.SUCCESS
        mock_tracker.update_job_lifecycle_state.assert_not_called()
