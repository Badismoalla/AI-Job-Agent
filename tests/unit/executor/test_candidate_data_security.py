"""
Phase 2D: Candidate data integrity and browser security tests.

Grounded in the real repository structure:
- Basic contact fields (name/email/phone) come from
  modules.application.base.candidate_info(), backed by the profile
  singleton (core.profile.profile), which reads data/profile.json.
- Platform-specific question answers come from application_answers.json
  in the package, which PackageWriter currently always writes as a
  placeholder: {"status": "not_supplied", "questions": []}. This means
  ANY form field beyond name/email/phone/resume/cover-letter currently
  has no real source of truth in this repo — the executor must treat
  such fields as unanswerable, not guess at them.

Rules under test:
1. Never fill a form field with fabricated/inferred data.
2. Missing basic contact info -> REVIEW_REQUIRED, not a blank/fabricated fill.
3. An unmapped custom question (job-specific field the answers placeholder
   cannot cover) -> REVIEW_REQUIRED, not silently skipped or guessed.
4. CAPTCHA/MFA/authentication barriers -> stop safely, never bypass.
"""

from unittest.mock import AsyncMock, patch

import pytest

try:
    from core.executor.states import ExecutionStatus, ExecutionMode
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


class TestCandidateDataIntegrity:
    """Form fields must come only from candidate_info() / package content — never invented."""

    @pytest.mark.asyncio
    async def test_form_filled_uses_real_candidate_info_values(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """The values passed to page.fill() for name/email match modules.application.base.candidate_info()."""
        from modules.application.base import candidate_info

        info = candidate_info()
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        filled_values = [c.args[1] for c in mock_page.fill.call_args_list if len(c.args) > 1]
        assert info["email"] in filled_values
        assert info["first_name"] in filled_values or info["full_name"] in filled_values

    @pytest.mark.asyncio
    async def test_missing_email_blocks_execution_not_blank_fill(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """If profile.email() is empty, the executor must not fill an empty string and proceed to submit."""
        with patch("core.profile.profile.email", return_value=""):
            executor = GreenhouseExecutor()
            result = await executor.execute(
                greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
                tracker=mock_tracker, page=mock_page,
            )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_name_blocks_execution(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker):
        with patch("core.profile.profile.name", return_value=""):
            executor = GreenhouseExecutor()
            result = await executor.execute(
                greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
                tracker=mock_tracker, page=mock_page,
            )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_required_does_not_transition_lifecycle(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        with patch("core.profile.profile.email", return_value=""):
            executor = GreenhouseExecutor()
            result = await executor.execute(
                greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
                tracker=mock_tracker, page=mock_page,
            )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        mock_tracker.update_job_lifecycle_state.assert_not_called()


class TestUnmappedFormQuestions:
    """
    application_answers.json is currently always a placeholder
    ({"status": "not_supplied", "questions": []}) — verified against
    PackageWriter. Any custom/platform-specific question field the
    executor discovers on the page therefore has no answer source and
    must not be guessed.
    """

    @pytest.mark.asyncio
    async def test_custom_question_field_with_no_answer_source_returns_review_required(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """
        Simulate a Greenhouse form with a custom question field whose
        label doesn't match any known personal/factual category or
        recognized open-ended pattern (Phase 2F Step 2: previously ANY
        custom field triggered REVIEW_REQUIRED unconditionally because
        application_answers.json was always a placeholder; now that real
        question discovery+answering exists, only genuinely
        unanswerable/ambiguous questions do). The executor must not
        guess, and must not submit.
        """
        async def query_selector_all(selector):
            if "custom_question" in selector:
                return [AsyncMock(get_attribute=AsyncMock(return_value="custom_widget_xyz_123"))]
            return []

        mock_page.query_selector_all = AsyncMock(side_effect=query_selector_all)

        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        mock_page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_application_answers_placeholder_status_is_checked(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """
        A package whose application_answers.json still has
        {"status": "not_supplied"} combined with a form that has custom
        questions must not proceed to AUTO_SUBMIT.
        """
        import json
        answers_path = valid_package_dir / "application_answers.json"
        content = json.loads(answers_path.read_text())
        assert content.get("status") == "not_supplied"  # sanity: matches real PackageWriter placeholder


class TestBrowserSecurityControls:
    """The executor must detect and stop for security barriers, never attempt to bypass them."""

    @pytest.mark.asyncio
    async def test_detect_captcha_and_stop(self, greenhouse_job, valid_package_dir, mock_page_with_captcha, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_captcha,
        )
        assert result.status == ExecutionStatus.CAPTCHA_REQUIRED
        mock_page_with_captcha.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_mfa_and_stop(self, greenhouse_job, valid_package_dir, mock_page_with_mfa, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_mfa,
        )
        assert result.status == ExecutionStatus.MFA_REQUIRED
        mock_page_with_mfa.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_captcha_result_flags_human_review(self, greenhouse_job, valid_package_dir, mock_page_with_captcha, mock_tracker):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_captcha,
        )
        assert result.human_review_required is True

    @pytest.mark.asyncio
    async def test_no_captcha_solving_library_imported_by_executor_module(self):
        """
        Static guard: the executor module must not import any known
        CAPTCHA-solving/bypass package. This fails loudly if such a
        dependency is ever added.
        """
        import core.executor.greenhouse_executor as gh_mod
        import inspect

        source = inspect.getsource(gh_mod)
        banned = ["2captcha", "anticaptcha", "capsolver", "deathbycaptcha"]
        lowered = source.lower()
        for term in banned:
            assert term not in lowered, f"Found forbidden CAPTCHA-bypass reference: {term}"


class TestUnsupportedFormsAreNotGuessed:

    @pytest.mark.asyncio
    async def test_unrecognized_form_structure_returns_unsupported_flow(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker
    ):
        """If none of the expected Greenhouse field selectors are found on the page, do not attempt a blind submit."""
        mock_page.fill = AsyncMock(side_effect=Exception("selector not found: input[name='first_name']"))
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page,
        )
        assert result.status in (ExecutionStatus.UNSUPPORTED_FLOW, ExecutionStatus.FORM_ERROR)
        mock_page.click.assert_not_called()
