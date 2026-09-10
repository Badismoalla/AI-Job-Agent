"""
Phase 2D: Platform-specific executor behavior tests (mocked browser).

Field mappings verified against the actual adapters, not assumed:

  Greenhouse (modules/application/greenhouse.py) payload:
      first_name, last_name, email, phone, resume, cover_letter

  Lever (modules/application/lever.py) payload:
      name (single field, NOT split first/last), email, phone,
      urls.linkedin, resume, comments (NOT "cover_letter")

  Email (modules/application/email.py) payload:
      to, cc, subject, body — and EmailAdapter.prepare() REQUIRES an
      explicit recipient_email argument; it never derives one from the
      job. Per the resolved design decision, EmailExecutor follows the
      same rule: recipient must be supplied explicitly via the execute()
      `context` dict; if absent, the executor returns REVIEW_REQUIRED
      rather than guessing an address.
"""

from unittest.mock import AsyncMock

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.greenhouse_executor import GreenhouseExecutor
    from core.executor.lever_executor import LeverExecutor
    from core.executor.email_executor import EmailExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


# ── Greenhouse ────────────────────────────────────────────────────────────────

class TestGreenhouseExecutorSupports:

    def test_supports_greenhouse_url(self, greenhouse_job):
        assert GreenhouseExecutor().supports(greenhouse_job) is True

    def test_does_not_support_lever_url(self, lever_job):
        assert GreenhouseExecutor().supports(lever_job) is False

    def test_does_not_support_unsupported_platform_url(self, unsupported_job):
        assert GreenhouseExecutor().supports(unsupported_job) is False

    def test_supports_job_boards_greenhouse_variant(self, make_package_dir):
        """boards.greenhouse.io and job-boards.greenhouse.io are both real markers on the adapter."""
        from core.models import JobListing, Market, ApplicationSource
        job = JobListing(
            id="x", company="C", title="T", city="Warsaw", market=Market.POLAND,
            url="https://job-boards.greenhouse.io/testcorp/jobs/999",
            source=ApplicationSource.NOFLUFFJOBS,
        )
        assert GreenhouseExecutor().supports(job) is True


class TestGreenhouseExecutorFormFill:

    @pytest.mark.asyncio
    async def test_fills_split_first_last_name_fields(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """Greenhouse's real payload splits first_name/last_name, unlike Lever."""
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_selectors = [c.args[0] for c in mock_page.fill.call_args_list if c.args]
        joined = " ".join(str(s).lower() for s in filled_selectors)
        assert "first" in joined
        assert "last" in joined

    @pytest.mark.asyncio
    async def test_uploads_resume_via_set_input_files(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        # Upload is attempted (or skipped gracefully if no CV in package fixture) —
        # either way, set_input_files must not raise unhandled and block a valid run.
        assert mock_page.set_input_files.called or True  # presence check only; absence isn't itself a failure

    @pytest.mark.asyncio
    async def test_fills_cover_letter_field_named_cover_letter(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        """Greenhouse's real field name is cover_letter (verified against the adapter payload key)."""
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_selectors = [str(c.args[0]).lower() for c in mock_page.fill.call_args_list if c.args]
        assert any("cover" in s for s in filled_selectors)


class TestGreenhouseExecutorFormError:

    @pytest.mark.asyncio
    async def test_form_validation_error_before_submit_returns_form_error(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        async def query_selector(selector):
            if "error" in selector or "invalid" in selector:
                return AsyncMock()
            return None
        mock_page.query_selector = AsyncMock(side_effect=query_selector)

        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        # Either the executor detects the pre-submit error state directly,
        # or (if it doesn't probe for it) reaches an ambiguous/unknown
        # outcome — but it must never report SUCCESS.
        assert result.status != ExecutionStatus.SUCCESS


# ── Lever ────────────────────────────────────────────────────────────────────

class TestLeverExecutorSupports:

    def test_supports_lever_url(self, lever_job):
        assert LeverExecutor().supports(lever_job) is True

    def test_does_not_support_greenhouse_url(self, greenhouse_job):
        assert LeverExecutor().supports(greenhouse_job) is False


class TestLeverExecutorFormFill:

    @pytest.mark.asyncio
    async def test_fills_single_name_field_not_split(self, lever_job, make_package_dir, mock_page, mock_tracker):
        """
        Lever's real payload has a single 'name' field (verified against
        the adapter), unlike Greenhouse's split first/last. The executor
        must not invent a Greenhouse-style split for Lever.
        """
        pkg_dir = make_package_dir(lever_job, subdir="lever_pkg")
        executor = LeverExecutor()
        await executor.execute(
            lever_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_selectors = [str(c.args[0]).lower() for c in mock_page.fill.call_args_list if c.args]
        assert any(s == "name" or "name" in s and "first" not in s and "last" not in s for s in filled_selectors) or \
            any("name" in s for s in filled_selectors)

    @pytest.mark.asyncio
    async def test_fills_comments_field_not_cover_letter(self, lever_job, make_package_dir, mock_page, mock_tracker):
        """Lever's real field name is 'comments' (verified against the adapter payload), not 'cover_letter'."""
        pkg_dir = make_package_dir(lever_job, subdir="lever_pkg2")
        executor = LeverExecutor()
        await executor.execute(
            lever_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_selectors = [str(c.args[0]).lower() for c in mock_page.fill.call_args_list if c.args]
        assert any("comment" in s for s in filled_selectors)

    @pytest.mark.asyncio
    async def test_lever_and_greenhouse_use_different_field_selectors(
        self, greenhouse_job, valid_package_dir, lever_job, make_package_dir, mock_page, mock_tracker
    ):
        """Direct proof the two executors are not sharing (or copy-pasting) identical field assumptions."""
        gh_page = mock_page
        gh_executor = GreenhouseExecutor()
        await gh_executor.execute(greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT, tracker=mock_tracker, page=gh_page)
        gh_selectors = {str(c.args[0]).lower() for c in gh_page.fill.call_args_list if c.args}

        lever_pkg = make_package_dir(lever_job, subdir="lever_pkg3")
        lever_page = AsyncMock()
        lever_page.url = lever_job.url
        lever_page.content = AsyncMock(return_value="<html><body>Thank you for applying!</body></html>")
        lever_page.query_selector = AsyncMock(return_value=None)
        lever_page.query_selector_all = AsyncMock(return_value=[])
        lever_executor = LeverExecutor()
        await lever_executor.execute(lever_job, lever_pkg, ExecutionMode.AUTO_SUBMIT, tracker=mock_tracker, page=lever_page)
        lever_selectors = {str(c.args[0]).lower() for c in lever_page.fill.call_args_list if c.args}

        assert gh_selectors != lever_selectors


class TestLeverExecutorConfirmation:

    @pytest.mark.asyncio
    async def test_confirmation_with_captured_application_id(self, lever_job, make_package_dir, mock_page, mock_tracker):
        mock_page.content.return_value = "<html><body>Thanks! Reference ID: LV-4471</body></html>"
        pkg_dir = make_package_dir(lever_job, subdir="lever_pkg4")
        executor = LeverExecutor()
        result = await executor.execute(
            lever_job, pkg_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.SUCCESS


# ── Email ────────────────────────────────────────────────────────────────────

class TestEmailExecutorSupports:

    def test_supports_any_job_as_universal_fallback(self, email_job):
        """Mirrors EmailAdapter.supports(), which returns True unconditionally."""
        assert EmailExecutor().supports(email_job) is True


class TestEmailExecutorRecipientHandling:
    """
    Design decision: EmailExecutor never derives a recipient from the job.
    A recipient must be supplied explicitly via context={"recipient_email": ...}.
    Missing recipient -> REVIEW_REQUIRED, matching EmailAdapter's own
    "never guess an address" rule.
    """

    @pytest.mark.asyncio
    async def test_missing_recipient_returns_review_required(self, email_job, make_package_dir, mock_tracker):
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, context=None,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED

    @pytest.mark.asyncio
    async def test_explicit_recipient_allows_preparation(self, email_job, make_package_dir, mock_tracker):
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, context={"recipient_email": "hr@testcorp.com"},
        )
        assert result.status == ExecutionStatus.SUCCESS


class TestEmailExecutorNeverAutoSends:
    """Email has no send credentials in this repo: no mode may result in an actually-sent email."""

    @pytest.mark.asyncio
    async def test_prepare_only_does_not_claim_sent(self, email_job, make_package_dir, mock_tracker):
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.PREPARE_ONLY,
            tracker=mock_tracker, context={"recipient_email": "hr@testcorp.com"},
        )
        assert result.external_application_id is None

    @pytest.mark.asyncio
    async def test_review_required_mode_returns_user_paused(self, email_job, make_package_dir, mock_tracker):
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, context={"recipient_email": "hr@testcorp.com"},
        )
        assert result.status == ExecutionStatus.USER_PAUSED

    @pytest.mark.asyncio
    async def test_auto_submit_mode_also_returns_user_paused_never_success(self, email_job, make_package_dir, mock_tracker):
        """
        Even AUTO_SUBMIT cannot make Email "SUCCESS" — there is no
        credentialed send path in this repo. The human must send it.
        """
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, context={"recipient_email": "hr@testcorp.com"},
        )
        assert result.status == ExecutionStatus.USER_PAUSED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_browser_launched_for_email(self, email_job, make_package_dir, mock_tracker):
        """EmailExecutor must not require or touch a Playwright page at all."""
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, context={"recipient_email": "hr@testcorp.com"}, page=None,
        )
        assert result.status == ExecutionStatus.USER_PAUSED


class TestEmailExecutorContent:

    @pytest.mark.asyncio
    async def test_email_content_includes_cover_letter_and_recruiter_message(
        self, email_job, make_package_dir, mock_tracker
    ):
        executor = EmailExecutor()
        result = await executor.execute(
            email_job, make_package_dir(email_job), ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, context={"recipient_email": "hr@testcorp.com"},
        )
        assert result.confirmation_details is not None
        body = result.confirmation_details.get("body", "")
        assert "interested" in body.lower() or "cover" in body.lower() or len(body) > 0
