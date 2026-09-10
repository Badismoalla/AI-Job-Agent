"""
Phase 2D: Idempotency and duplicate prevention tests.

Critical invariant: executing twice must not create two applications.
Durable application/lifecycle state takes priority over execution timestamps.
"""

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


class TestIdempotencyPrevention:
    """Tests that prevent duplicate application submission via lifecycle/tracker state."""

    @pytest.mark.asyncio
    async def test_cannot_execute_already_applied_job(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_cannot_execute_terminal_state_job(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_terminal_state):
        """REJECTED (a hard terminal state) blocks re-execution."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_terminal_state, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_application_record_blocks_execution(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """tracker.already_applied(job.id) == True blocks execution even if lifecycle state looks fine."""
        mock_tracker.already_applied.return_value = True
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotency_check_runs_before_browser_navigation(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        """Duplicate detection short-circuits before any browser I/O — no wasted navigation."""
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        mock_page.goto.assert_not_called()


class TestSubmissionUnknownHandling:
    """SUBMISSION_UNKNOWN must never be auto-resubmitted."""

    @pytest.mark.asyncio
    async def test_previous_submission_unknown_blocks_resubmission(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """
        If the tracker's lifecycle record shows the last known execution
        result was SUBMISSION_UNKNOWN, a new AUTO_SUBMIT attempt must be
        blocked rather than silently resubmitted.
        """
        mock_tracker.get_job_lifecycle.return_value = {
            "job_id": greenhouse_job.id,
            "current_state": "application_prepared",
            "last_event": "submit_attempted",
            "last_execution_status": "submission_unknown",
        }
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        assert result.human_review_required is True
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_submission_unknown_result_flags_human_review_not_retry(
        self, greenhouse_job, valid_package_dir, mock_page_unconfirmed, mock_tracker
    ):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        assert result.human_review_required is True
        assert result.retry_safe is False


class TestDurableStateFirstLogic:
    """Lifecycle state is checked ahead of any timestamp-based heuristics."""

    @pytest.mark.asyncio
    async def test_applied_state_blocks_regardless_of_recency(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        """
        Even if this were the very first execute() call in this process
        (no recent-execution window could apply), a durable APPLIED
        lifecycle state alone is sufficient to block execution.
        """
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED

    @pytest.mark.asyncio
    async def test_application_prepared_state_allows_execution(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """A job genuinely in APPLICATION_PREPARED with no existing application record is allowed to execute."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status != ExecutionStatus.NOT_AUTHORIZED


class TestIdempotencyReturnValues:
    """Idempotency checks return clear, actionable results."""

    @pytest.mark.asyncio
    async def test_duplicate_detection_returns_not_authorized_not_error(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        """Duplicate prevention is a clean NOT_AUTHORIZED, not BROWSER_ERROR or a raised exception."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED

    @pytest.mark.asyncio
    async def test_idempotency_result_includes_reason(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.error_reason is not None
        assert "applied" in result.error_reason.lower() or "duplicate" in result.error_reason.lower()
