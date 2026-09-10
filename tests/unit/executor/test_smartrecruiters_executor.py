"""
Phase 2E: SmartRecruitersExecutor tests (mocked browser).

Field mapping verified against modules/application/smartrecruiters.py:
    firstName, lastName, email, phone, resumeFileName, coverLetter
(camelCase — distinct from both Greenhouse's snake_case cover_letter
and Lever's single name/comments fields.)

URL markers verified against the same adapter:
    jobs.smartrecruiters.com, careers.smartrecruiters.com
"""

from unittest.mock import AsyncMock

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.smartrecruiters_executor import SmartRecruitersExecutor
except ImportError:
    pytest.skip("core/executor/smartrecruiters_executor not yet implemented", allow_module_level=True)


@pytest.fixture
def smartrecruiters_job():
    from core.models import JobListing, Market, ApplicationSource
    return JobListing(
        id="testcorp-engineer-sr-warsaw",
        company="Test Corp",
        title="Engineer",
        city="Warsaw",
        market=Market.POLAND,
        url="https://jobs.smartrecruiters.com/testcorp/123-engineer",
        source=ApplicationSource.NOFLUFFJOBS,
        description="Test position",
    )


@pytest.fixture
def smartrecruiters_careers_job():
    from core.models import JobListing, Market, ApplicationSource
    return JobListing(
        id="testcorp-engineer-sr-careers-warsaw",
        company="Test Corp",
        title="Engineer",
        city="Warsaw",
        market=Market.POLAND,
        url="https://careers.smartrecruiters.com/testcorp/123-engineer",
        source=ApplicationSource.NOFLUFFJOBS,
        description="Test position",
    )


class TestSmartRecruitersExecutorSupports:

    def test_supports_jobs_subdomain(self, smartrecruiters_job):
        assert SmartRecruitersExecutor().supports(smartrecruiters_job) is True

    def test_supports_careers_subdomain(self, smartrecruiters_careers_job):
        assert SmartRecruitersExecutor().supports(smartrecruiters_careers_job) is True

    def test_does_not_support_greenhouse_url(self, greenhouse_job):
        assert SmartRecruitersExecutor().supports(greenhouse_job) is False

    def test_does_not_support_lever_url(self, lever_job):
        assert SmartRecruitersExecutor().supports(lever_job) is False


class TestSmartRecruitersExecutorFormFill:

    @pytest.mark.asyncio
    async def test_fills_camelcase_first_last_name_fields(
        self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker
    ):
        """SmartRecruiters' real payload keys are firstName/lastName (camelCase), not snake_case."""
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg1")
        executor = SmartRecruitersExecutor()
        await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_selectors = [str(c.args[0]) for c in mock_page.fill.call_args_list if c.args]
        joined = " ".join(filled_selectors)
        assert "firstName" in joined
        assert "lastName" in joined

    @pytest.mark.asyncio
    async def test_fills_cover_letter_field_named_coverletter_camelcase(
        self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker
    ):
        """SmartRecruiters' real field name is coverLetter (camelCase), not cover_letter or comments."""
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg2")
        executor = SmartRecruitersExecutor()
        await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_selectors = [str(c.args[0]) for c in mock_page.fill.call_args_list if c.args]
        assert any("coverLetter" in s for s in filled_selectors)
        assert not any(s == "cover_letter" or "comments" in s.lower() for s in filled_selectors)

    @pytest.mark.asyncio
    async def test_uploads_resume_via_resumefilename_field(
        self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker
    ):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg3")
        # Add a CV file so upload path is exercised.
        (pkg_dir / "cv.pdf").write_text("%PDF-1.0\n...")
        executor = SmartRecruitersExecutor()
        await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert mock_page.set_input_files.called
        upload_selector = str(mock_page.set_input_files.call_args.args[0])
        assert "resume" in upload_selector.lower()

    @pytest.mark.asyncio
    async def test_uses_different_selectors_than_greenhouse(
        self, smartrecruiters_job, make_package_dir, greenhouse_job, valid_package_dir, mock_tracker
    ):
        """Direct proof SmartRecruiters isn't just copy-pasting Greenhouse's snake_case selectors."""
        from core.executor.greenhouse_executor import GreenhouseExecutor

        sr_page = AsyncMock()
        sr_page.url = smartrecruiters_job.url
        sr_page.content = AsyncMock(return_value="<html><body>Thank you for applying!</body></html>")
        sr_page.query_selector = AsyncMock(return_value=None)
        sr_page.query_selector_all = AsyncMock(return_value=[])

        sr_pkg = make_package_dir(smartrecruiters_job, subdir="sr_pkg4")
        sr_executor = SmartRecruitersExecutor()
        await sr_executor.execute(smartrecruiters_job, sr_pkg, ExecutionMode.AUTO_SUBMIT, tracker=mock_tracker, page=sr_page)
        sr_selectors = {str(c.args[0]) for c in sr_page.fill.call_args_list if c.args}

        gh_page = AsyncMock()
        gh_page.url = greenhouse_job.url
        gh_page.content = AsyncMock(return_value="<html><body>Thank you for applying!</body></html>")
        gh_page.query_selector = AsyncMock(return_value=None)
        gh_page.query_selector_all = AsyncMock(return_value=[])

        gh_executor = GreenhouseExecutor()
        await gh_executor.execute(greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT, tracker=mock_tracker, page=gh_page)
        gh_selectors = {str(c.args[0]) for c in gh_page.fill.call_args_list if c.args}

        assert sr_selectors != gh_selectors


class TestSmartRecruitersExecutorModesAndSafety:
    """SmartRecruiters must obey the same mode/safety invariants as every other Phase 2D/2E executor."""

    @pytest.mark.asyncio
    async def test_prepare_only_never_submits(self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg5")
        executor = SmartRecruitersExecutor()
        result = await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        mock_page.goto.assert_not_called()
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_required_fills_but_never_submits(self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg6")
        executor = SmartRecruitersExecutor()
        result = await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.USER_PAUSED
        assert mock_page.fill.call_count > 0
        submit_clicks = [c for c in mock_page.click.call_args_list if c.args and "submit" in str(c.args[0]).lower()]
        assert len(submit_clicks) == 0

    @pytest.mark.asyncio
    async def test_auto_submit_success_transitions_lifecycle(self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg7")
        executor = SmartRecruitersExecutor()
        result = await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS
        mock_tracker.update_job_lifecycle_state.assert_called_once()
        kwargs = mock_tracker.update_job_lifecycle_state.call_args.kwargs
        assert kwargs["event"] == "submit"

    @pytest.mark.asyncio
    async def test_already_applied_blocks_execution(self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker_already_applied):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg8")
        executor = SmartRecruitersExecutor()
        result = await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker_already_applied, page=mock_page,
        )
        assert result.status == ExecutionStatus.NOT_AUTHORIZED
        mock_page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_captcha_detected_stops_execution(self, smartrecruiters_job, make_package_dir, mock_page_with_captcha, mock_tracker):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg9")
        executor = SmartRecruitersExecutor()
        result = await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_captcha,
        )
        assert result.status == ExecutionStatus.CAPTCHA_REQUIRED
        mock_page_with_captcha.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_uncertain_confirmation_returns_submission_unknown(
        self, smartrecruiters_job, make_package_dir, mock_page_unconfirmed, mock_tracker
    ):
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg10")
        executor = SmartRecruitersExecutor()
        result = await executor.execute(
            smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_unconfirmed,
        )
        assert result.status == ExecutionStatus.SUBMISSION_UNKNOWN
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_candidate_email_returns_review_required(
        self, smartrecruiters_job, make_package_dir, mock_page, mock_tracker
    ):
        from unittest.mock import patch
        pkg_dir = make_package_dir(smartrecruiters_job, subdir="sr_pkg11")
        with patch("core.profile.profile.email", return_value=""):
            executor = SmartRecruitersExecutor()
            result = await executor.execute(
                smartrecruiters_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
                tracker=mock_tracker, page=mock_page,
            )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        mock_page.click.assert_not_called()
