"""
Phase 2F Step 2 FINAL HARDENING tests.

Two invariants under test:

1. Fail-closed discovery: "zero recognized custom-question elements
   found" is never silently treated as "the form has no questions."
   Positive evidence of unrecognized fields, or a recognized field that
   can't be safely parsed, must produce DISCOVERY_UNCERTAIN ->
   REVIEW_REQUIRED, never a silent proceed.

2. Radio-group grouping: N <input type="radio"> elements sharing one
   `name` become ONE question with accumulated options, not N separate
   questions.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from core.executor.states import ExecutionMode, ExecutionStatus
    from core.executor.greenhouse_executor import GreenhouseExecutor
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


def _element(attrs: dict, value: str | None = None, tag: str | None = None) -> AsyncMock:
    """A mock DOM element with explicit attribute values (no silent tag/type inference unless requested)."""
    async def get_attribute(attr):
        if attr == "value":
            return value
        return attrs.get(attr)

    el = AsyncMock()
    el.get_attribute = AsyncMock(side_effect=get_attribute)
    if tag is not None:
        el.evaluate = AsyncMock(return_value=tag)
    return el


def _selector_aware_page(custom_question_elements=None, all_field_elements=None, confirmation_text="Thank you for applying!"):
    """
    A page whose query_selector_all response depends on the selector,
    matching how the real executor calls it (once for the recognized
    custom-question convention, once for a broader unrecognized-field scan).
    """
    custom_question_elements = custom_question_elements or []
    all_field_elements = all_field_elements or []

    async def query_selector_all(selector):
        if "custom_question" in selector:
            return custom_question_elements
        if selector == "input, textarea, select":
            return all_field_elements
        return []

    page = AsyncMock()
    page.url = "https://boards.greenhouse.io/testcorp/jobs/123"
    page.goto = AsyncMock(return_value=None)
    page.fill = AsyncMock(return_value=None)
    page.set_input_files = AsyncMock(return_value=None)
    page.click = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value=f"<html><body>{confirmation_text}</body></html>")
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(side_effect=query_selector_all)
    page.select_option = AsyncMock(return_value=None)
    page.check = AsyncMock(return_value=None)
    page.uncheck = AsyncMock(return_value=None)
    return page


@pytest.fixture
def mock_generator():
    gen = MagicMock()
    from core.models import GeneratedMessage, MessageType
    gen.generate_application_answer = AsyncMock(
        return_value=GeneratedMessage(type=MessageType.APPLICATION_ANSWER, body="AI answer.")
    )
    return gen


# ── Fail-closed discovery: the six required scenarios ─────────────────────────

class TestFailClosedDiscovery:

    @pytest.mark.asyncio
    async def test_1_confirmed_no_questions_is_safe_to_continue(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        """No custom-question elements AND no unrecognized extra fields -> genuinely NO_QUESTIONS."""
        page = _selector_aware_page(custom_question_elements=[], all_field_elements=[])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "no_questions_found"

    @pytest.mark.asyncio
    async def test_2_confirmed_recognized_question_is_questions_found(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        el = _element({"name": "custom_question_1", "data-label": "Why do you want this role?", "data-field-type": "textarea"})
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "answers_generated"

    @pytest.mark.asyncio
    async def test_3_unrecognized_dom_structure_is_discovery_uncertain(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        """An extra form field the platform doesn't recognize at all (not custom_question-named)."""
        extra_field = _element({"name": "some_screening_question_abc"}, tag="input")
        page = _selector_aware_page(custom_question_elements=[], all_field_elements=[extra_field])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.click.assert_not_called()
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "discovery_uncertain"

    @pytest.mark.asyncio
    async def test_4_unparseable_custom_field_is_review_required(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        """A custom_question-named element with NO extractable label at all (no data-label, no aria-label,
        no placeholder, no id) and an unrecognized tag -> can't safely interpret it."""
        el = _element({"name": "custom_question_2"})  # no tag set -> evaluate() returns a MagicMock, not a real tag
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.click.assert_not_called()
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "discovery_uncertain"
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_5_auto_submit_never_submits_on_uncertain_discovery(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        extra_field = _element({"name": "unknown_field_xyz"}, tag="input")
        page = _selector_aware_page(custom_question_elements=[], all_field_elements=[extra_field])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.click.assert_not_called()
        mock_tracker.update_job_lifecycle_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_6_review_required_mode_flags_human_review_on_uncertain_discovery(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        extra_field = _element({"name": "unknown_field_xyz"}, tag="input")
        page = _selector_aware_page(custom_question_elements=[], all_field_elements=[extra_field])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.REVIEW_REQUIRED,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.USER_PAUSED
        assert result.human_review_required is True
        page.click.assert_not_called()


# ── Radio-group grouping ────────────────────────────────────────────────────

class TestRadioGroupGrouping:

    @pytest.mark.asyncio
    async def test_two_option_radio_group_becomes_one_question(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        yes_el = _element(
            {"name": "custom_question_1", "data-label": "Will you require visa sponsorship?", "data-field-type": "yes_no"},
            value="Yes",
        )
        no_el = _element(
            {"name": "custom_question_1", "data-label": "Will you require visa sponsorship?", "data-field-type": "yes_no"},
            value="No",
        )
        page = _selector_aware_page(custom_question_elements=[yes_el, no_el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert len(answers["questions"]) == 1  # ONE question, not two

    @pytest.mark.asyncio
    async def test_three_plus_option_radio_group_becomes_one_question_with_all_options(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        label = "How would you rate your remote-work readiness?"
        elements = [
            _element({"name": "custom_question_3", "data-label": label, "data-field-type": "yes_no"}, value=v)
            for v in ("Low", "Medium", "High")
        ]
        page = _selector_aware_page(custom_question_elements=elements)
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert len(answers["questions"]) == 1
        # Either resolved with one of the 3 options, or safely bailed -- never fabricated outside the set.
        record = answers["questions"][0]
        if not record["review_required"]:
            assert record["answer"] in ("Low", "Medium", "High")

    @pytest.mark.asyncio
    async def test_required_radio_group_answer_matches_one_valid_option(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        elements = [
            _element({"name": "custom_question_1", "data-label": "Will you require visa sponsorship?", "data-field-type": "yes_no"}, value=v)
            for v in ("Yes", "No")
        ]
        page = _selector_aware_page(custom_question_elements=elements)
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        page.check.assert_called_once()
        checked_selector = page.check.call_args.args[0]
        assert 'value="Yes"' in checked_selector

    @pytest.mark.asyncio
    async def test_invalid_answer_for_radio_group_returns_review_required(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        """Options unrelated to any profile field or safe AI category -> never guess one."""
        elements = [
            _element({"name": "custom_question_4", "data-label": "Preferred parking spot letter:", "data-field-type": "yes_no"}, value=v)
            for v in ("A", "B", "C")
        ]
        page = _selector_aware_page(custom_question_elements=elements)
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.check.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_duplicate_question_records_for_radio_group(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        elements = [
            _element({"name": "custom_question_1", "data-label": "Will you require visa sponsorship?", "data-field-type": "yes_no"}, value=v)
            for v in ("Yes", "No")
        ]
        page = _selector_aware_page(custom_question_elements=elements)
        executor = GreenhouseExecutor()
        await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        labels = [q["question"] for q in answers["questions"]]
        assert labels.count("Will you require visa sponsorship?") == 1


class TestOtherFieldTypesUnaffectedByRadioFix:

    @pytest.mark.asyncio
    async def test_checkbox_still_works(self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator):
        el = _element({"name": "custom_question_5", "data-label": "Are you willing to relocate?", "data-field-type": "checkbox"})
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        assert page.check.called or page.uncheck.called

    @pytest.mark.asyncio
    async def test_select_still_works(self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator):
        el = _element(
            {"name": "custom_question_6", "data-label": "Years of experience:", "data-field-type": "select",
             "data-options": json.dumps(["0-1", "2-3", "4-5", "6+"])},
        )
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        record = answers["questions"][0]
        if not record["review_required"]:
            assert record["answer"] in ["0-1", "2-3", "4-5", "6+"]
            page.select_option.assert_called_once()

    @pytest.mark.asyncio
    async def test_normal_text_input_still_works(self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator):
        el = _element({"name": "custom_question_7", "data-label": "Why do you want this role?", "data-field-type": "textarea"})
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        mock_generator.generate_application_answer.assert_called_once()


class TestFileUploadQuestionSafety:
    """Stage B requirement: file-upload custom questions must remain REVIEW_REQUIRED, never filled."""

    @pytest.mark.asyncio
    async def test_file_question_never_filled_always_review_required(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        el = _element(
            {"name": "custom_question_8", "data-label": "Please attach your transcripts", "data-field-type": "file"},
        )
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.click.assert_not_called()
        mock_generator.generate_application_answer.assert_not_called()

        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        record = answers["questions"][0]
        assert record["field_type"] == "file"
        assert record["review_required"] is True
        assert record["answer"] is None


class TestMultiSelectCheckboxGroupSafety:
    """Stage B requirement: checkbox/multi-select groups must be represented correctly, never mis-toggled."""

    @pytest.mark.asyncio
    async def test_multiple_checkboxes_sharing_name_are_review_required_not_mis_toggled(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        elements = [
            _element({"name": "custom_question_10", "data-label": "Select all cities you'd relocate to:", "data-field-type": "checkbox"}, value=city)
            for city in ("Berlin", "Munich", "Zurich")
        ]
        page = _selector_aware_page(custom_question_elements=elements)
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        page.check.assert_not_called()
        page.uncheck.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_checkbox_still_works_normally(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        """A single checkbox (one element, not a group) is unaffected by the multi-select fail-closed check."""
        el = _element({"name": "custom_question_11", "data-label": "Are you willing to relocate?", "data-field-type": "checkbox"})
        page = _selector_aware_page(custom_question_elements=[el])
        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )
        assert result.status == ExecutionStatus.SUCCESS
        assert page.check.called or page.uncheck.called
    """
    Required scenario: ATS fixture -> custom question -> discovery cannot
    safely interpret it -> DISCOVERY_UNCERTAIN -> REVIEW_REQUIRED ->
    executor does NOT submit -> tracker does NOT become APPLIED.
    """

    @pytest.mark.asyncio
    async def test_uninterpretable_question_never_reaches_applied(
        self, greenhouse_job, valid_package_dir, mock_tracker, mock_generator
    ):
        # A custom-question element with a completely unrecognized tag/type
        # and no usable label source at all.
        uninterpretable = _element({"name": "custom_question_9"}, tag="div")  # not textarea/select/input
        page = _selector_aware_page(custom_question_elements=[uninterpretable])

        executor = GreenhouseExecutor()
        result = await executor.execute(
            greenhouse_job, valid_package_dir, ExecutionMode.AUTO_SUBMIT,
            tracker=mock_tracker, page=page, generator=mock_generator,
        )

        assert result.status == ExecutionStatus.REVIEW_REQUIRED
        assert result.human_review_required is True

        # No submission attempted
        page.click.assert_not_called()

        # Tracker never told to transition -- application never becomes APPLIED
        mock_tracker.update_job_lifecycle_state.assert_not_called()

        # application_answers.json correctly reflects the uncertain state
        answers = json.loads((valid_package_dir / "application_answers.json").read_text())
        assert answers["status"] == "discovery_uncertain"
