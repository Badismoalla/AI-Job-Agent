"""
Phase 2D: Retry safety tests.

Safe to retry: navigation/page-load timeouts, before any submission action.
Never auto-retried: the final submit action, or an uncertain (SUBMISSION_UNKNOWN)
outcome — retrying those could create a duplicate application.
"""

from unittest.mock import AsyncMock

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


class TestSafeRetry:
    """Operations before the final submit action may be retried."""

    @pytest.mark.asyncio
    async def test_navigation_timeout_is_retried_and_can_succeed(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """
        First page.goto() call times out, second succeeds. This is a
        pre-submission transient failure, so the executor may retry
        navigation and still reach SUCCESS.
        """
        mock_page.goto = AsyncMock(side_effect=[TimeoutError("nav timeout"), None])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert mock_page.goto.call_count >= 2
        assert result.status == ExecutionStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_navigation_retry_is_bounded(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """Navigation retries are not unbounded — persistent failure eventually surfaces as BROWSER_ERROR."""
        mock_page.goto = AsyncMock(side_effect=TimeoutError("nav timeout"))
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert mock_page.goto.call_count <= 5  # bounded, not infinite
        assert result.status == ExecutionStatus.BROWSER_ERROR
        mock_tracker.update_job_lifecycle_state.assert_not_called()


class TestDangerousRetryIsNotAttempted:
    """The final submit action, and uncertain outcomes, are never auto-retried within one execute() call."""

    @pytest.mark.asyncio
    async def test_submit_click_is_not_repeated_after_ambiguous_result(
        self, greenhouse_job, valid_package_dir, mock_page_unconfirmed, mock_tracker
    ):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        submit_clicks = [c for c in mock_page_unconfirmed.click.call_args_list
                          if c.args and "submit" in str(c.args[0]).lower()]
        assert len(submit_clicks) == 1
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN

    @pytest.mark.asyncio
    async def test_confirmation_check_error_after_submit_does_not_resubmit(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """
        page.content() raising after the submit click (e.g. page crashed
        mid-navigation) must not be treated as "try clicking submit again".
        """
        mock_page.content = AsyncMock(side_effect=Exception("page crashed"))
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        submit_clicks = [c for c in mock_page.click.call_args_list
                          if c.args and "submit" in str(c.args[0]).lower()]
        assert len(submit_clicks) <= 1
        assert result.status != ExecutionStatus.SUCCESS


class TestExecutionResultRetryFlags:
    """ExecutionResult.retry_safe accurately reflects whether a fresh call is safe."""

    @pytest.mark.asyncio
    async def test_submission_unknown_marks_retry_unsafe(self, greenhouse_job, valid_package_dir, mock_page_unconfirmed, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        assert result.retry_safe is False

    @pytest.mark.asyncio
    async def test_browser_error_before_submit_marks_retry_safe(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """A navigation-stage BROWSER_ERROR (before any submit attempt) is safe to retry from scratch."""
        mock_page.goto = AsyncMock(side_effect=TimeoutError("nav timeout"))
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.BROWSER_ERROR
        assert result.retry_safe is True

    @pytest.mark.asyncio
    async def test_success_result_is_not_marked_retry_safe(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """A confirmed SUCCESS should not invite retry at all (retry_safe is meaningless/False for terminal success)."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        assert result.retry_safe is False


class TestDuplicatePreventionOverridesRetryTemptation:
    """Idempotency checks take priority over any retry heuristic."""

    @pytest.mark.asyncio
    async def test_already_applied_is_never_retried_regardless_of_error_type(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied
    ):
        mock_page.goto = AsyncMock(side_effect=TimeoutError("would normally be retried"))
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        # Idempotency check happens before navigation, so the injected
        # timeout is never even reached.
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_page.goto.assert_not_called()
