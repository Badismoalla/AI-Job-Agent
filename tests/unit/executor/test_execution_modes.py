"""
Phase 2D: Execution mode behavior tests.

Tests for PREPARE_ONLY, REVIEW_REQUIRED, and AUTO_SUBMIT mode semantics,
exercised against GreenhouseExecutor with a mocked Playwright Page (no real
browser is ever launched in this suite).

Mandatory invariant under test:
    PREPARE_ONLY and REVIEW_REQUIRED must NEVER call page.click() on a
    submit control. Only AUTO_SUBMIT may do so, and only when authorized.
"""

import pytest

try:
    from core.executor.states import ExecutionMode, ExecutionStatus
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


def submit_was_clicked(mock_page) -> bool:
    """True if any page.click() call targeted a submit-like selector."""
    for call in mock_page.click.call_args_list:
        args = call.args or ()
        target = str(args[0]) if args else ""
        if "submit" in target.lower():
            return True
    return False


class TestPrepareOnlyMode:
    """PREPARE_ONLY must never perform final submission."""

    @pytest.mark.asyncio
    async def test_prepare_only_does_not_click_submit(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, page=mock_page,
        )
        assert submit_was_clicked(mock_page) is False

    @pytest.mark.asyncio
    async def test_prepare_only_does_not_navigate_browser(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """PREPARE_ONLY validates and writes locally; it must not touch the browser at all."""
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, page=mock_page,
        )
        mock_page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_prepare_only_returns_success_without_external_id(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        assert result.external_application_id is None

    @pytest.mark.asyncio
    async def test_prepare_only_does_not_call_lifecycle_update(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, page=mock_page,
        )
        mock_tracker.update_job_lifecycle_state.assert_not_called()


class TestReviewRequiredMode:
    """
    REVIEW_REQUIRED must never perform final submission.

    The executor may validate, navigate, and fill the form for human
    review, but must stop before the submit action.
    """

    @pytest.mark.asyncio
    async def test_review_required_does_not_click_submit(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=mock_page,
        )
        assert submit_was_clicked(mock_page) is False

    @pytest.mark.asyncio
    async def test_review_required_fills_form_for_human_review(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """Unlike PREPARE_ONLY, REVIEW_REQUIRED does fill the form (navigates + fills)."""
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=mock_page,
        )
        mock_page.goto.assert_called_once()
        assert mock_page.fill.call_count > 0

    @pytest.mark.asyncio
    async def test_review_required_returns_user_paused_status(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.USER_PAUSED

    @pytest.mark.asyncio
    async def test_review_required_does_not_transition_lifecycle(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """No persistent session/resume mechanism is assumed — pausing must not touch lifecycle state."""
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=mock_page,
        )
        mock_tracker.update_job_lifecycle_state.assert_not_called()


class TestAutoSubmitMode:
    """AUTO_SUBMIT may submit, but only when every authorization condition is met."""

    @pytest.mark.asyncio
    async def test_auto_submit_clicks_submit_when_authorized(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert submit_was_clicked(mock_page) is True

    @pytest.mark.asyncio
    async def test_auto_submit_blocked_by_invalid_package(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """Invalid package (missing cover letter) blocks AUTO_SUBMIT before any browser action."""
        (valid_package_dir / "cover_letter.md").unlink()
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.VALIDATION_FAILED
        mock_page.goto.assert_not_called()
        assert submit_was_clicked(mock_page) is False

    @pytest.mark.asyncio
    async def test_auto_submit_blocked_by_wrong_lifecycle_state(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_already_applied):
        """Job already in APPLIED state: AUTO_SUBMIT refuses to run, does not touch the browser."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_page.goto.assert_not_called()
        assert submit_was_clicked(mock_page) is False

    @pytest.mark.asyncio
    async def test_auto_submit_blocked_by_terminal_lifecycle_state(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker_terminal_state):
        """Job in a hard terminal state (rejected): AUTO_SUBMIT refuses to run."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_terminal_state, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        assert submit_was_clicked(mock_page) is False

    @pytest.mark.asyncio
    async def test_auto_submit_succeeds_transitions_lifecycle(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """Authorized AUTO_SUBMIT with confirmed success requests the APPLIED transition via the tracker."""
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        mock_tracker.update_job_lifecycle_state.assert_called_once()
        _, kwargs = mock_tracker.update_job_lifecycle_state.call_args
        assert kwargs.get("event") == "submit" or "submit" in str(mock_tracker.update_job_lifecycle_state.call_args)


class TestModeIsolation:
    """PREPARE_ONLY and REVIEW_REQUIRED behave identically with respect to the submit action, regardless of package validity."""

    @pytest.mark.asyncio
    async def test_prepare_only_and_review_required_never_submit_even_with_valid_package(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        executor = GreenhouseExecutor()
        for mode in (ExecutionMode.PREPARE_ONLY, ExecutionMode.REVIEW_REQUIRED):
            page = mock_page
            page.click.reset_mock()
            await executor.execute(greenhouse_job, valid_package_dir, mode, tracker=mock_tracker, page=page)
            assert submit_was_clicked(page) is False, f"{mode} must never click submit"
