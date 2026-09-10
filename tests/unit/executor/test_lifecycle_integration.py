"""
Phase 2D: Lifecycle integration with the Phase 2C state machine.

Verified against the actual tracker signature (modules/tracker/tracker.py):

    update_job_lifecycle_state(job_id: str, event: str,
                                next_state: JobLifecycleState, data: dict | None = None)

and the actual transition table (core/lifecycle/transitions.py), which defines
event "submit" as the only valid trigger from APPLICATION_PREPARED to APPLIED.

Rule under test: the executor must call this tracker method to request a
transition — it must never mutate lifecycle state by any other means.
"""

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.greenhouse_executor import GreenhouseExecutor
    from core.lifecycle import JobLifecycleState
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


class TestLifecycleTransitionOnSuccess:

    @pytest.mark.asyncio
    async def test_success_calls_tracker_with_submit_event_and_applied_state(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        mock_tracker.update_job_lifecycle_state.assert_called_once()
        call = mock_tracker.update_job_lifecycle_state.call_args
        kwargs = call.kwargs
        assert kwargs["job_id"] == greenhouse_job.id
        assert kwargs["event"] == "submit"
        assert kwargs["next_state"] == JobLifecycleState.APPLIED

    @pytest.mark.asyncio
    async def test_success_persists_external_application_id_in_data(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        mock_page.content.return_value = "<html><body>Thank you! Application ID: GH-98765</body></html>"
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.external_application_id is not None
        kwargs = mock_tracker.update_job_lifecycle_state.call_args.kwargs
        assert kwargs["data"] is not None
        assert kwargs["data"].get("external_application_id") == result.external_application_id


class TestNoTransitionOnNonSuccess:
    """
    For every non-SUCCESS status, update_job_lifecycle_state must not be
    called at all — the job stays in APPLICATION_PREPARED by omission,
    not by an explicit "no-op" transition call.
    """

    @pytest.mark.asyncio
    async def test_validation_failed_no_tracker_call(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        (valid_package_dir / "cover_letter.md").unlink()
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.VALIDATION_FAILED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_authorized_no_tracker_call(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_tracker_already_applied.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_submission_unknown_no_tracker_call(self, greenhouse_job, valid_package_dir, mock_page_unconfirmed, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_captcha_required_no_tracker_call(self, greenhouse_job, valid_package_dir, mock_page_with_captcha, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_captcha,
        )
        assert result.status == ExecutionStatus.CAPTCHA_REQUIRED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_paused_no_tracker_call(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.USER_PAUSED
        mock_tracker.update_job_lifecycle_state.assert_not_called()


class TestPreconditionCheckUsesLifecycleReadState:
    """Executor reads lifecycle state via tracker.get_job_lifecycle() before deciding whether to run."""

    @pytest.mark.asyncio
    async def test_executor_reads_lifecycle_before_executing(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        mock_tracker.get_job_lifecycle.assert_called()

    @pytest.mark.asyncio
    async def test_executor_checks_already_applied_before_executing(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        mock_tracker.already_applied.assert_called_with(greenhouse_job.id)


class TestTransitionErrorHandledGracefully:

    @pytest.mark.asyncio
    async def test_invalid_transition_error_from_tracker_does_not_crash_executor(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """
        If the tracker raises InvalidLifecycleTransitionError (e.g. a race
        condition changed state between the read and the write), the
        executor must catch it and report a structured failure rather
        than propagating an unhandled exception or silently claiming SUCCESS.
        """
        from core.exceptions import InvalidLifecycleTransitionError

        mock_tracker.update_job_lifecycle_state.side_effect = InvalidLifecycleTransitionError(
            current_state="applied", event="submit", reason="already applied"
        )
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status != ExecutionStatus.SUCCESS
        assert result.human_review_required is True
