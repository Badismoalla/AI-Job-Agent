"""
Unit tests for modules.ai.claude_generator.ClaudeGenerator.

This is the highest-risk part of the codebase: it's the only module that
talks to an external API. No test in this file makes a real network call —
dry-run mode and a mocked anthropic client cover every path.

Coverage here:
- Prompt creation: the right job/match_report data is wired into each
  prompt builder (template content itself is covered in test_prompts.py)
- Dry-run path for every generate_* method (no network call, deterministic output)
- Retry/timeout behaviour: documents that ClaudeGenerator has no app-level
  retry loop or timeout override of its own — it relies entirely on the
  anthropic SDK's defaults
- Invalid/malformed API responses: documents what currently happens when a
  "successful" response doesn't have the expected shape
- Generated message validation: core.models.GeneratedMessage schema, plus
  whether real ClaudeGenerator output actually conforms to it
- Subject-line extraction/fallback behaviour
- The real API call path (_call), success + every documented error mapping,
  using a mocked anthropic client — no real network access
"""

from unittest.mock import MagicMock

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from config.settings import settings
from core.exceptions import AIGenerationError
from core.models import (
    ApplicationSource,
    GeneratedMessage,
    JobListing,
    Market,
    MatchDecision,
    MatchReport,
    MessageType,
    RoleTier,
)
from modules.ai.claude_generator import ClaudeGenerator


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def job() -> JobListing:
    return JobListing(
        id="testco-test-engineer-krakow",
        title="Automotive Test Engineer",
        company="TestCo",
        city="Krakow",
        market=Market.POLAND,
        url="https://example.com/jobs/testco-test-engineer-krakow",
        source=ApplicationSource.LINKEDIN,
        description="AUTOSAR automotive ECU UDS DoIP validation testing",
    )


@pytest.fixture
def match_report() -> MatchReport:
    return MatchReport(
        job_id="testco-test-engineer-krakow",
        job_title="Automotive Test Engineer",
        company="TestCo",
        tier=RoleTier.PRIMARY,
        decision=MatchDecision.APPLY,
        score=85,
        reason="Strong match.",
        matched_keywords=["automotive", "ecu"],
        matched_protocols=["uds", "doip"],
        gap_mitigations={"canoe": "DLT/Wireshark equivalent experience."},
    )


@pytest.fixture
def dry_run_generator(monkeypatch) -> ClaudeGenerator:
    """A generator with dry_run forced True — never touches the network."""
    monkeypatch.setattr(settings.app, "dry_run", True)
    return ClaudeGenerator()


@pytest.fixture
def live_generator(monkeypatch) -> ClaudeGenerator:
    """A generator with dry_run forced False, API client replaced with a mock."""
    monkeypatch.setattr(settings.app, "dry_run", False)
    gen = ClaudeGenerator()
    gen._client = MagicMock()
    return gen


def _mock_response(text: str = "Generated message body", input_tokens: int = 10, output_tokens: int = 20):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


# ── Dry-run behaviour for every generate_* method ──────────────────────────

class TestDryRunGeneration:

    @pytest.mark.asyncio
    async def test_cover_letter_dry_run(self, dry_run_generator, job, match_report):
        msg = await dry_run_generator.generate_cover_letter(job=job, match_report=match_report)
        assert msg.type == MessageType.COVER_LETTER
        assert "[DRY RUN — cover_letter]" in msg.body
        assert msg.subject == f"Application – {job.title} | Badis Moalla"
        assert msg.model_used == settings.ai.anthropic_model

    @pytest.mark.asyncio
    async def test_recruiter_message_dry_run(self, dry_run_generator, job, match_report):
        msg = await dry_run_generator.generate_recruiter_message(
            job=job, recruiter_name="Anna Kowalski", match_report=match_report
        )
        assert msg.type == MessageType.RECRUITER_INMAIL
        assert "[DRY RUN — recruiter_inmail]" in msg.body

    @pytest.mark.asyncio
    async def test_hr_email_dry_run(self, dry_run_generator, job, match_report):
        msg = await dry_run_generator.generate_hr_email(job=job, match_report=match_report)
        assert msg.type == MessageType.HR_EMAIL
        assert "[DRY RUN — hr_email]" in msg.body
        assert msg.subject == f"Application – {job.title} | Badis Moalla"

    @pytest.mark.asyncio
    async def test_follow_up_dry_run(self, dry_run_generator, job):
        msg = await dry_run_generator.generate_follow_up(job=job, days_since_applied=10)
        assert msg.type == MessageType.FOLLOW_UP
        assert msg.subject == f"Follow-up: {job.title} Application | Badis Moalla"
        assert "[DRY RUN — follow_up]" in msg.body

    @pytest.mark.asyncio
    async def test_hiring_manager_message_dry_run(self, dry_run_generator, job):
        msg = await dry_run_generator.generate_hiring_manager_message(
            job=job, manager_name="Jan Kowalski"
        )
        assert msg.type == MessageType.HIRING_MANAGER
        assert "[DRY RUN — hiring_manager]" in msg.body

    @pytest.mark.asyncio
    async def test_application_answer_dry_run(self, dry_run_generator, job):
        msg = await dry_run_generator.generate_application_answer(
            question="Why do you want this role?", job=job
        )
        assert msg.type == MessageType.APPLICATION_ANSWER
        assert "[DRY RUN — application_answer]" in msg.body

    @pytest.mark.asyncio
    async def test_explain_match_dry_run_returns_raw_string(
        self, dry_run_generator, job, match_report
    ):
        """explain_match is the odd one out — it returns a raw str, not a GeneratedMessage."""
        result = await dry_run_generator.explain_match(job=job, match_report=match_report)
        assert isinstance(result, str)
        assert "[DRY RUN — application_answer]" in result


# ── Subject-line extraction ─────────────────────────────────────────────────

class TestExtractSubject:

    def test_extracts_subject_when_present(self):
        body = "Subject: Application for Test Engineer\n\nDear Hiring Team,..."
        assert ClaudeGenerator._extract_subject(body) == "Application for Test Engineer"

    def test_case_insensitive(self):
        body = "subject: Quick note\nBody text follows."
        assert ClaudeGenerator._extract_subject(body) == "Quick note"

    def test_returns_none_when_absent(self):
        body = "Dear Hiring Team,\nI'm excited to apply.\nBest, Badis"
        assert ClaudeGenerator._extract_subject(body) is None

    def test_only_checks_first_three_lines(self):
        body = "Line one\nLine two\nLine three\nSubject: Too late\n"
        assert ClaudeGenerator._extract_subject(body) is None


# ── Real API call path (mocked client) ─────────────────────────────────────

class TestCallErrorHandling:

    @pytest.mark.asyncio
    async def test_successful_call_returns_text(self, live_generator):
        live_generator._client.messages.create.return_value = _mock_response("Hello world")
        text = await live_generator._call("some prompt", MessageType.COVER_LETTER)
        assert text == "Hello world"

    @pytest.mark.asyncio
    async def test_authentication_error_wrapped(self, live_generator):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(401, request=request)
        live_generator._client.messages.create.side_effect = anthropic.AuthenticationError(
            "bad key", response=response, body=None
        )
        with pytest.raises(AIGenerationError) as exc_info:
            await live_generator._call("some prompt", MessageType.COVER_LETTER)
        assert "Invalid API key" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_rate_limit_error_wrapped(self, live_generator):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(429, request=request)
        live_generator._client.messages.create.side_effect = anthropic.RateLimitError(
            "too many requests", response=response, body=None
        )
        with pytest.raises(AIGenerationError) as exc_info:
            await live_generator._call("some prompt", MessageType.COVER_LETTER)
        assert "rate limit" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_generic_api_error_wrapped(self, live_generator):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        live_generator._client.messages.create.side_effect = anthropic.APIConnectionError(
            request=request
        )
        with pytest.raises(AIGenerationError) as exc_info:
            await live_generator._call("some prompt", MessageType.COVER_LETTER)
        assert "Claude API error" in str(exc_info.value)


# ── Prompt creation: correct data is wired into each prompt builder ────────

class TestPromptCreation:
    """
    modules/ai/prompts.py owns the actual template text (see test_prompts.py).
    These tests cover the other half of "prompt creation": that ClaudeGenerator
    passes the *right* data from JobListing/MatchReport into each builder.
    """

    @pytest.mark.asyncio
    async def test_cover_letter_prompt_receives_job_and_report_data(
        self, dry_run_generator, job, match_report, monkeypatch
    ):
        captured = {}

        def fake_prompt(**kwargs):
            captured.update(kwargs)
            return "prompt text"

        monkeypatch.setattr("modules.ai.claude_generator.cover_letter_prompt", fake_prompt)

        await dry_run_generator.generate_cover_letter(
            job=job, recruiter_name="Anna", company_detail="detail", match_report=match_report
        )

        assert captured["job_title"] == job.title
        assert captured["company"] == job.company
        assert captured["city"] == job.city
        assert captured["market"] == job.market
        assert captured["recruiter_name"] == "Anna"
        assert captured["company_detail"] == "detail"
        assert captured["gap_mitigations"] == match_report.gap_mitigations
        assert captured["matched_keywords"] == match_report.matched_keywords
        assert captured["matched_protocols"] == match_report.matched_protocols

    @pytest.mark.asyncio
    async def test_cover_letter_prompt_defaults_when_no_match_report(
        self, dry_run_generator, job, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.cover_letter_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.generate_cover_letter(job=job)
        assert captured["gap_mitigations"] == {}
        assert captured["matched_keywords"] == []
        assert captured["matched_protocols"] == []

    @pytest.mark.asyncio
    async def test_recruiter_message_prompt_receives_correct_data(
        self, dry_run_generator, job, match_report, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.recruiter_inmail_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.generate_recruiter_message(
            job=job, recruiter_name="Anna", match_report=match_report
        )
        assert captured["job_title"] == job.title
        assert captured["recruiter_name"] == "Anna"
        assert captured["gap_mitigations"] == match_report.gap_mitigations

    @pytest.mark.asyncio
    async def test_hr_email_prompt_receives_correct_data(
        self, dry_run_generator, job, match_report, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.hr_email_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.generate_hr_email(job=job, match_report=match_report)
        assert captured["job_title"] == job.title
        assert captured["market"] == job.market
        assert captured["gap_mitigations"] == match_report.gap_mitigations

    @pytest.mark.asyncio
    async def test_follow_up_prompt_receives_days_since_applied(
        self, dry_run_generator, job, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.follow_up_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.generate_follow_up(job=job, days_since_applied=14)
        assert captured["days_since_applied"] == 14

    @pytest.mark.asyncio
    async def test_hiring_manager_prompt_receives_correct_data(
        self, dry_run_generator, job, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.hiring_manager_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.generate_hiring_manager_message(
            job=job, manager_name="Jan", team_context="ADAS team"
        )
        assert captured["manager_name"] == "Jan"
        assert captured["team_context"] == "ADAS team"

    @pytest.mark.asyncio
    async def test_application_answer_prompt_receives_question_and_max_words(
        self, dry_run_generator, job, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.application_answer_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.generate_application_answer(
            question="Why this role?", job=job, max_words=200
        )
        assert captured["question"] == "Why this role?"
        assert captured["max_words"] == 200

    @pytest.mark.asyncio
    async def test_explain_match_prompt_receives_report_data(
        self, dry_run_generator, job, match_report, monkeypatch
    ):
        captured = {}
        monkeypatch.setattr(
            "modules.ai.claude_generator.job_score_explanation_prompt",
            lambda **kwargs: captured.update(kwargs) or "prompt text",
        )
        await dry_run_generator.explain_match(job=job, match_report=match_report)
        assert captured["score"] == match_report.score
        assert captured["decision"] == match_report.decision
        assert captured["tier"] == match_report.tier
        assert captured["gaps"] == match_report.skill_gaps


# ── Retry / timeout behaviour ───────────────────────────────────────────────

class TestRetryAndTimeoutBehaviour:
    """
    ClaudeGenerator implements no application-level retry loop or timeout
    configuration of its own — it constructs `anthropic.Anthropic(api_key=...)`
    with every other parameter left at the SDK's defaults (max_retries=2,
    timeout=library default). These tests document that current behaviour
    precisely, so it fails loudly (and intentionally) if someone later adds
    an app-level retry/timeout layer without updating this test.
    """

    def test_client_constructed_without_custom_retry_or_timeout(self, monkeypatch):
        captured_kwargs = {}
        original_init = anthropic.Anthropic.__init__

        def spy_init(self, **kwargs):
            captured_kwargs.update(kwargs)
            original_init(self, **kwargs)

        monkeypatch.setattr(anthropic.Anthropic, "__init__", spy_init)
        ClaudeGenerator()

        assert set(captured_kwargs.keys()) == {"api_key"}
        assert "max_retries" not in captured_kwargs
        assert "timeout" not in captured_kwargs

    @pytest.mark.asyncio
    async def test_call_does_not_retry_after_a_single_failure(self, live_generator):
        """
        _call() itself makes no retry attempt: one failure from the client
        means exactly one call was made, then an immediate raise. Any retrying
        that happens for real network errors happens inside the anthropic SDK
        transport layer, which this mock bypasses entirely.
        """
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(429, request=request)
        live_generator._client.messages.create.side_effect = anthropic.RateLimitError(
            "too many requests", response=response, body=None
        )

        with pytest.raises(AIGenerationError):
            await live_generator._call("some prompt", MessageType.COVER_LETTER)

        assert live_generator._client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_api_timeout_error_is_wrapped_not_retried(self, live_generator):
        """anthropic.APITimeoutError is a subclass of APIError — it's caught
        by the generic branch and wrapped, not specially retried."""
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        live_generator._client.messages.create.side_effect = anthropic.APITimeoutError(
            request=request
        )

        with pytest.raises(AIGenerationError) as exc_info:
            await live_generator._call("some prompt", MessageType.COVER_LETTER)

        assert "Claude API error" in str(exc_info.value)
        assert live_generator._client.messages.create.call_count == 1


# ── Invalid / malformed API responses ───────────────────────────────────────

class TestInvalidResponseHandling:
    """
    _call() only catches anthropic's own exception types (AuthenticationError,
    RateLimitError, APIError). It does NOT validate the *shape* of a successful
    response before indexing into it. These tests document the current,
    unguarded behaviour for malformed-but-"successful" responses — this is a
    real gap (raw IndexError/AttributeError leaks past the AIGenerationError
    boundary) rather than a demonstration of graceful handling.
    """

    @pytest.mark.asyncio
    async def test_empty_content_list_raises_unwrapped_index_error(self, live_generator):
        response = MagicMock()
        response.content = []
        live_generator._client.messages.create.return_value = response

        with pytest.raises(IndexError):
            await live_generator._call("some prompt", MessageType.COVER_LETTER)

    @pytest.mark.asyncio
    async def test_content_block_without_text_raises_unwrapped_attribute_error(
        self, live_generator
    ):
        response = MagicMock()
        block = MagicMock(spec=[])  # no .text attribute at all
        response.content = [block]

        live_generator._client.messages.create.return_value = response

        with pytest.raises(AttributeError):
            await live_generator._call("some prompt", MessageType.COVER_LETTER)

    @pytest.mark.asyncio
    async def test_missing_usage_raises_unwrapped_attribute_error(self, live_generator):
        response = MagicMock()
        response.content = [MagicMock(text="Hello")]
        response.usage = None  # .input_tokens access below will fail

        live_generator._client.messages.create.return_value = response

        with pytest.raises(AttributeError):
            await live_generator._call("some prompt", MessageType.COVER_LETTER)


# ── Generated message validation ────────────────────────────────────────────

class TestGeneratedMessageValidation:
    """core.models.GeneratedMessage is the schema every generate_* method must
    satisfy. These tests cover the schema itself, plus whether ClaudeGenerator's
    real output actually conforms to it."""

    def test_body_is_required(self):
        with pytest.raises(ValidationError):
            GeneratedMessage(type=MessageType.COVER_LETTER)

    def test_type_must_be_a_valid_message_type(self):
        with pytest.raises(ValidationError):
            GeneratedMessage(type="not_a_real_type", body="text")

    def test_optional_fields_default_correctly(self):
        msg = GeneratedMessage(type=MessageType.COVER_LETTER, body="text")
        assert msg.subject is None
        assert msg.model_used is None
        assert msg.tokens_used is None
        assert msg.generated_at is not None

    @pytest.mark.asyncio
    async def test_dry_run_output_conforms_to_schema(self, dry_run_generator, job, match_report):
        msg = await dry_run_generator.generate_cover_letter(job=job, match_report=match_report)
        # Round-trips through the schema's own validator without error.
        GeneratedMessage.model_validate(msg.model_dump())
        assert isinstance(msg.body, str) and len(msg.body) > 0
        assert msg.type == MessageType.COVER_LETTER

    @pytest.mark.asyncio
    async def test_tokens_used_is_never_populated_even_on_a_real_call(self, live_generator):
        """
        Known gap: _call() computes tokens_used internally (for the log line)
        but never returns it, so every generate_* method's GeneratedMessage
        always has tokens_used=None — even for a real, successful API call.
        """
        live_generator._client.messages.create.return_value = _mock_response(
            "Generated body", input_tokens=100, output_tokens=50
        )
        msg = await live_generator.generate_recruiter_message(
            job=JobListing(
                id="x", title="Test Engineer", company="X", city="Y",
                market=Market.POLAND, url="https://example.com/x",
                source=ApplicationSource.LINKEDIN,
            ),
            recruiter_name="Anna",
        )
        assert msg.tokens_used is None
