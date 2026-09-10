"""
Tests for modules.ai.question_answerer.QuestionAnswerer.

Design grounded in the REAL data/profile.json (verified against source):
    - target.visa_required: true          -> answers visa/sponsorship questions
    - target.relocation: "immediate"       -> answers relocation questions
    - target.salary_expectations (per-market dict, keyed like
      "Poland_PLN_monthly") -> answers salary questions when market known
    - personal.languages (list of {name, level}) -> answers language questions
    - education (single dict)              -> answers education questions
    - certifications (list, each with status) -> answers certification questions
    - experience (list with start/end dates, current flag) -> answers
      "years of experience" / "current employer" questions

Categories with NO profile field (notice period, willingness to travel,
exact employer start date beyond month precision) must return
REVIEW_REQUIRED rather than being guessed by Claude.

Open-ended questions (motivation, technical narrative) fall through to
the EXISTING ClaudeGenerator.generate_application_answer() + AnswerCache
-- reused, not duplicated.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models import ApplicationSource, JobListing, Market
from modules.ai.question_answerer import (
    AnswerProvenance,
    ApplicationQuestion,
    QuestionAnswerer,
)


def _job(market=Market.POLAND) -> JobListing:
    return JobListing(
        id="test-job-1", title="Software Test Engineer", company="Test Corp",
        city="Warsaw", market=market, url="https://example.com/1",
        source=ApplicationSource.NOFLUFFJOBS,
    )


@pytest.fixture
def mock_generator():
    """A ClaudeGenerator double -- QuestionAnswerer must only call this for open-ended questions."""
    gen = MagicMock()
    gen.generate_application_answer = AsyncMock()
    return gen


class TestDeterministicProfileAnswers:
    """These must NEVER call the AI generator -- zero hallucination risk by construction."""

    @pytest.mark.asyncio
    async def test_visa_sponsorship_question_answered_from_profile(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Will you require visa sponsorship?", field_type="yes_no",
                                 options=["Yes", "No"])
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.PROFILE
        assert result.value == "Yes"  # profile.target.visa_required is True
        assert result.review_required is False
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_relocation_question_answered_from_profile(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Are you willing to relocate?", field_type="yes_no",
                                 options=["Yes", "No"])
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.PROFILE
        assert result.value == "Yes"  # profile.target.relocation == "immediate"
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_salary_expectation_answered_from_profile_for_known_market(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What are your salary expectations?", field_type="text")
        result = await answerer.answer(q, _job(Market.POLAND))

        assert result.provenance == AnswerProvenance.PROFILE
        assert result.value is not None
        assert "14000" in result.value or "20000" in result.value  # Poland_PLN_monthly range
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_education_question_answered_from_profile(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What is your highest level of education?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.PROFILE
        assert "ENET'Com" in result.value or "Industrial Computer Engineering" in result.value
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_language_proficiency_question_answered_from_profile(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What is your English proficiency level?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.PROFILE
        assert result.value is not None
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_years_of_experience_computed_from_profile(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="How many years of testing experience do you have?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.PROFILE
        assert result.value is not None
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_current_employer_answered_from_profile(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Who is your current employer?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.PROFILE
        assert "KPIT" in result.value
        mock_generator.generate_application_answer.assert_not_called()


class TestHallucinationPrevention:
    """Categories with no profile field must return REVIEW_REQUIRED, never a Claude guess."""

    @pytest.mark.asyncio
    async def test_notice_period_not_in_profile_returns_review_required(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What is your notice period?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.review_required is True
        assert result.provenance == AnswerProvenance.REVIEW_REQUIRED
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_willingness_to_travel_not_in_profile_returns_review_required(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Are you willing to travel up to 50%?", field_type="yes_no",
                                 options=["Yes", "No"])
        result = await answerer.answer(q, _job())

        assert result.review_required is True
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_exact_start_date_not_in_profile_returns_review_required(self, mock_generator):
        """Profile only stores month-level start/end dates, not exact days -- must not invent precision."""
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What is the exact start date (day) of your current role?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.review_required is True
        mock_generator.generate_application_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_salary_question_missing_market_data_returns_review_required(self, mock_generator, monkeypatch):
        """
        If a job's market genuinely has no salary_expectations entry in
        the profile (e.g. profile.json edited to remove one), never
        guess a number -- REVIEW_REQUIRED.
        """
        from core.profile import profile

        monkeypatch.setitem(profile.target, "salary_expectations", {})

        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What are your salary expectations?", field_type="text")
        result = await answerer.answer(q, _job(Market.POLAND))

        assert result.review_required is True
        mock_generator.generate_application_answer.assert_not_called()


class TestOpenEndedQuestionsUseAIWithCache:
    """Only genuinely open-ended questions fall through to the existing generator + cache."""

    @pytest.mark.asyncio
    async def test_motivation_question_calls_generator(self, mock_generator):
        from core.models import GeneratedMessage, MessageType
        mock_generator.generate_application_answer.return_value = GeneratedMessage(
            type=MessageType.APPLICATION_ANSWER, body="Because of the mission and the engineering challenge."
        )
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Why do you want to join our company?", field_type="textarea")
        result = await answerer.answer(q, _job())

        assert result.provenance == AnswerProvenance.AI_GENERATED
        assert result.review_required is False
        mock_generator.generate_application_answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_open_ended_question_reused_from_cache_second_time(self, mock_generator):
        """
        QuestionAnswerer must not duplicate caching logic -- it relies on
        ClaudeGenerator.generate_application_answer()'s own cache wiring.
        This proves it calls that method both times rather than bypassing it.
        """
        from core.models import GeneratedMessage, MessageType
        mock_generator.generate_application_answer.return_value = GeneratedMessage(
            type=MessageType.APPLICATION_ANSWER, body="Same cached answer."
        )
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Describe your testing philosophy.", field_type="textarea")

        r1 = await answerer.answer(q, _job())
        r2 = await answerer.answer(q, _job())

        assert r1.value == r2.value == "Same cached answer."
        assert mock_generator.generate_application_answer.call_count == 2  # cache dedup happens inside the generator


class TestOptionValidation:
    """Yes/No and dropdown answers must match an actual available option, or REVIEW_REQUIRED."""

    @pytest.mark.asyncio
    async def test_yes_no_answer_matches_available_options(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Will you require visa sponsorship?", field_type="yes_no",
                                 options=["Yes", "No"])
        result = await answerer.answer(q, _job())
        assert result.value in q.options

    @pytest.mark.asyncio
    async def test_yes_no_with_nonstandard_option_labels_still_validated(self, mock_generator):
        """If the form uses different option text (e.g. 'Y'/'N'), and it doesn't match, REVIEW_REQUIRED."""
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Will you require visa sponsorship?", field_type="yes_no",
                                 options=["Y", "N"])
        result = await answerer.answer(q, _job())
        # Either it maps "Yes" -> "Y" sensibly, or it safely bails to REVIEW_REQUIRED --
        # it must NEVER return a value outside the declared options.
        if not result.review_required:
            assert result.value in q.options

    @pytest.mark.asyncio
    async def test_dropdown_answer_must_be_one_of_available_options(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(
            label="Years of experience:", field_type="select",
            options=["0-1", "2-3", "4-5", "6+"],
        )
        result = await answerer.answer(q, _job())
        if not result.review_required:
            assert result.value in q.options

    @pytest.mark.asyncio
    async def test_dropdown_with_no_safe_option_returns_review_required(self, mock_generator):
        """If none of the offered options can be safely determined, never pick an arbitrary one."""
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(
            label="Preferred office location:", field_type="select",
            options=["London", "New York", "Singapore"],  # unrelated to any profile field
        )
        result = await answerer.answer(q, _job())
        assert result.review_required is True
        assert result.value is None


class TestAmbiguousQuestionsDefaultToReviewRequired:
    """
    Neither personal-factual nor a recognized open-ended pattern -> the
    system is not confident which field is being answered, so it must
    default to REVIEW_REQUIRED rather than guessing via AI.
    """

    @pytest.mark.asyncio
    async def test_unrecognized_question_never_calls_ai(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Reference code XZ-19", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.review_required is True
        assert result.value is None
        mock_generator.generate_application_answer.assert_not_called()


class TestAnswerResultShape:

    @pytest.mark.asyncio
    async def test_result_includes_reason_when_review_required(self, mock_generator):
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="What is your notice period?", field_type="text")
        result = await answerer.answer(q, _job())

        assert result.reason is not None
        assert len(result.reason) > 0

    @pytest.mark.asyncio
    async def test_result_never_none_value_without_review_required(self, mock_generator):
        """Invariant: value is None only when review_required is True."""
        answerer = QuestionAnswerer(generator=mock_generator)
        q = ApplicationQuestion(label="Will you require visa sponsorship?", field_type="yes_no",
                                 options=["Yes", "No"])
        result = await answerer.answer(q, _job())

        if result.value is None:
            assert result.review_required is True
