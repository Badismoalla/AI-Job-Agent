"""
Phase 2F Step 2: executor-level question discovery, answering, filling,
and application_answers.json write-back.

DOM convention used by these fixtures (documented limitation -- not
verified against any real live ATS platform, see the Phase 2F report):
each discovered question element carries name*="custom_question",
data-label (question text), data-field-type (text/textarea/yes_no/
select/checkbox/number), and data-options (JSON array, for yes_no/select).
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from core.executor.states import ExecutionMode, ExecutionStatus
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


def _question_element(name: str, label: str, field_type: str, options: list | None = None) -> AsyncMock:
    """Build a mock element matching the discovery DOM convention."""
    async def get_attribute(attr: str):
        return {
            "name": name,
            "data-label": label,
            "data-field-type": field_type,
            "data-options": json.dumps(options) if options is not None else None,
        }.get(attr)

    element = AsyncMock()
    element.get_attribute = AsyncMock(side_effect=get_attribute)
    return element


@pytest.fixture
def mock_generator():
    """Never touches the real cache/API -- injected explicitly into execute()."""
    gen = MagicMock()
    from core.models import GeneratedMessage, MessageType
    gen.generate_application_answer = AsyncMock(
        return_value=GeneratedMessage(type=MessageType.APPLICATION_ANSWER, body="Because I love solving hard technical problems.")
    )
    return gen


class TestNoCustomQuestions:
    """Scenario 1: no custom questions -- application proceeds normally."""

    @pytest.mark.asyncio
    async def test_no_questions_writes_no_questions_found(self, greenhouse_job, valid_package_dir, mock_page, mock_tracker, mock_generator):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS

        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "no_questions_found"
        assert answers["questions"] == []
        mock_generator.generate_application_answer.assert_not_called()


class TestRequiredTextQuestion:
    """Scenario 2: open-ended text question -- Claude called, answer filled and cached."""

    @pytest.mark.asyncio
    async def test_open_ended_question_discovered_answered_and_filled(
        self, greenhouse_job, valid_package_dir, mock_page, mock_tracker, mock_generator
    ):
        mock_page.query_selector_all = AsyncMock(
            return_value=[_question_element("custom_question_1", "Why are you interested in this position?", "textarea")]
        )
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page, generator=mock_generator,
        )

        assert result.status == ExecutionStatus.SUCCESS
        mock_generator.generate_application_answer.assert_called_once()

        fill_calls = [c.args for c in mock_page.fill.call_args_list]
        assert any(c[0] == '[name="custom_question_1"]' for c in fill_calls)

        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "answers_generated"
        assert len(answers["questions"]) == 1
        assert answers["questions"][0]["provenance"] == "ai_generated"
        assert answers["questions"][0]["review_required"] is False


class TestRepeatedQuestionUsesCache:
    """Scenario 3: same question across two executions -- second run is a cache hit, Claude not called again."""

    @pytest.mark.asyncio
    async def test_second_execution_reuses_cached_answer(
        self, greenhouse_job, make_package_dir, mock_tracker, tmp_path
    ):
        from modules.ai.claude_generator import ClaudeGenerator
        from config.settings import settings

        settings.ai.anthropic_api_key = "test-key-for-cache-proof"
        settings.app.dry_run = False
        cache_path = tmp_path / "shared_test_cache.json"

        real_generator = ClaudeGenerator(cache_path=cache_path)
        real_generator._client = MagicMock()
        real_generator._client.messages.create = MagicMock(return_value=_mock_anthropic_response("A real generated answer."))

        page1 = _fresh_mock_page_with_question("Why do you want this role?", "textarea")
        pkg1 = make_package_dir(greenhouse_job, subdir="q_repeat_1")
        executor = GreenhouseExecutor()
        result1 = await executor.execute(
            greenhouse_job, pkg1, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page1, generator=real_generator,
        )
        assert result1.status == ExecutionStatus.SUCCESS
        assert real_generator._client.messages.create.call_count == 1

        # Fresh generator instance, same cache file -- simulates a later run
        real_generator2 = ClaudeGenerator(cache_path=cache_path)
        real_generator2._client = MagicMock()
        real_generator2._client.messages.create = MagicMock(return_value=_mock_anthropic_response("Different text, should be ignored."))

        page2 = _fresh_mock_page_with_question("Why do you want this role?", "textarea")
        pkg2 = make_package_dir(greenhouse_job, subdir="q_repeat_2")
        result2 = await executor.execute(
            greenhouse_job, pkg2, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page2, generator=real_generator2,
        )
        assert result2.status == ExecutionStatus.SUCCESS
        assert real_generator2._client.messages.create.call_count == 0  # cache hit, API never called

        settings.app.dry_run = True


def _mock_anthropic_response(text: str):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = 10
    response.usage.output_tokens = 15
    return response


def _fresh_mock_page_with_question(label: str, field_type: str, options=None) -> AsyncMock:
    page = AsyncMock()
    page.url = "https://boards.greenhouse.io/testcorp/jobs/123"
    page.goto = AsyncMock(return_value=None)
    page.fill = AsyncMock(return_value=None)
    page.set_input_files = AsyncMock(return_value=None)
    page.click = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value="<html><body>Thank you for applying!</body></html>")
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[_question_element("custom_question_1", label, field_type, options)])
    page.select_option = AsyncMock(return_value=None)
    page.check = AsyncMock(return_value=None)
    page.uncheck = AsyncMock(return_value=None)
    return page


class TestYesNoQuestion:
    """Scenario 4: visa sponsorship yes/no -- answered from profile, valid option selected."""

    @pytest.mark.asyncio
    async def test_visa_yes_no_answered_from_profile_and_checked(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        page = _fresh_mock_page_with_question("Will you require visa sponsorship?", "yes_no", ["Yes", "No"])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )

        assert result.status == ExecutionStatus.SUCCESS
        mock_generator.generate_application_answer.assert_not_called()  # profile-derived, never AI

        page.check.assert_called_once()
        checked_selector = page.check.call_args.args[0]
        assert 'value="Yes"' in checked_selector  # profile.target.visa_required is True

        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["questions"][0]["provenance"] == "profile"
        assert answers["questions"][0]["answer"] == "Yes"


class TestDropdownQuestion:
    """Scenario 5: dropdown -- only a valid available option may be selected."""

    @pytest.mark.asyncio
    async def test_years_experience_dropdown_selects_valid_option_only(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        page = _fresh_mock_page_with_question(
            "Years of experience:", "select", ["0-1", "2-3", "4-5", "6+"]
        )
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )

        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        record = answers["questions"][0]

        if record["review_required"]:
            page.select_option.assert_not_called()
            assert result.status == ExecutionStatus.REVIEW_REQUIRED
        else:
            assert record["answer"] in ["0-1", "2-3", "4-5", "6+"]
            page.select_option.assert_called_once()
            selected_value = page.select_option.call_args.args[1]
            assert selected_value in ["0-1", "2-3", "4-5", "6+"]

    @pytest.mark.asyncio
    async def test_dropdown_with_unrelated_options_never_picks_arbitrary_choice(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        """Options with no relation to any profile field -- must not guess one."""
        page = _fresh_mock_page_with_question(
            "Preferred office location:", "select", ["London", "New York", "Singapore"]
        )
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )

        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.select_option.assert_not_called()
        page.click.assert_not_called()  # never reaches submit


class TestMissingCandidateInformation:
    """Scenario 6: question requires info not in the profile -- REVIEW_REQUIRED, no submission."""

    @pytest.mark.asyncio
    async def test_notice_period_question_blocks_submission(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        page = _fresh_mock_page_with_question("What is your notice period?", "text")
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )

        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.click.assert_not_called()
        mock_tracker.update_job_lifecycle_state.assert_not_called()

        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["questions"][0]["review_required"] is True
        assert answers["questions"][0]["answer"] is None


class TestAmbiguousQuestion:
    """Scenario 7: system cannot safely interpret the question -- REVIEW_REQUIRED."""

    @pytest.mark.asyncio
    async def test_unrecognized_question_blocks_submission(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        page = _fresh_mock_page_with_question("Reference code ZX-99", "text")
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )

        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.click.assert_not_called()
        mock_generator.generate_application_answer.assert_not_called()


class TestCaptchaMfaUnaffected:
    """Scenario 8: existing CAPTCHA/MFA safety behavior is unchanged by question discovery."""

    @pytest.mark.asyncio
    async def test_captcha_still_blocks_before_question_discovery(
        self, greenhouse_job, valid_package_dir, mock_page_with_captcha, mock_tracker, mock_generator
    ):
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=mock_page_with_captcha, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.CAPTCHA_REQUIRED
        mock_generator.generate_application_answer.assert_not_called()
        mock_page_with_captcha.click.assert_not_called()


class TestReviewRequiredModeNeverFillsQuestionsAnswer:
    """A discovered-but-unresolved question in REVIEW_REQUIRED mode still never submits."""

    @pytest.mark.asyncio
    async def test_review_required_mode_with_unresolved_question(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        page = _fresh_mock_page_with_question("What is your notice period?", "text")
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        # REVIEW_REQUIRED mode never submits regardless -- USER_PAUSED, not blocked by the question itself,
        # but flagged for human review since a question remains unresolved.
        assert result.status == ExecutionStatus.USER_PAUSED
        assert result.human_review_required is True
        page.click.assert_not_called()
