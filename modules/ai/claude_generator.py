"""
modules/ai/claude_generator.py
-------------------------------
Claude API implementation of BaseMessageGenerator.

Uses the Anthropic Python SDK to generate all candidate messages.
All prompts are loaded from modules/ai/prompts.py — never hardcoded here.

Dry-run mode (APP_DRY_RUN=true):
  Returns a placeholder GeneratedMessage without calling the API.
  Safe for development and testing.

Usage:
    async with ClaudeGenerator() as gen:
        msg = await gen.generate_cover_letter(job, recruiter_name="Anna Kowalski")
        print(msg.body)
"""

import anthropic

from config.settings import settings
from core.exceptions import AIGenerationError
from core.logger import get_logger
from core.models import GeneratedMessage, JobListing, MatchReport, MessageType
from modules.ai.base import BaseMessageGenerator
from modules.ai.prompts import (
    SYSTEM_PROMPT,
    application_answer_prompt,
    cover_letter_prompt,
    follow_up_prompt,
    hiring_manager_prompt,
    hr_email_prompt,
    job_score_explanation_prompt,
    recruiter_inmail_prompt,
)

logger = get_logger(__name__)


class ClaudeGenerator(BaseMessageGenerator):
    """
    Generates all candidate messages using the Claude API.
    Implements BaseMessageGenerator — swap with MockGenerator for tests.
    """

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(
            api_key=settings.ai.anthropic_api_key
        )
        self._model = settings.ai.anthropic_model
        self._max_tokens = settings.ai.anthropic_max_tokens
        self._dry_run = settings.app.dry_run

    async def generate_cover_letter(
        self,
        job: JobListing,
        recruiter_name: str | None = None,
        company_detail: str | None = None,
        match_report: MatchReport | None = None,
    ) -> GeneratedMessage:
        """Generate a personalised cover letter tailored to the job and match report."""
        prompt = cover_letter_prompt(
            job_title=job.title,
            company=job.company,
            city=job.city,
            market=job.market,
            recruiter_name=recruiter_name,
            company_detail=company_detail,
            gap_mitigations=match_report.gap_mitigations if match_report else {},
            matched_keywords=match_report.matched_keywords if match_report else [],
            matched_protocols=match_report.matched_protocols if match_report else [],
        )

        body = await self._call(prompt, MessageType.COVER_LETTER)
        subject = self._extract_subject(body) or f"Application – {job.title} | Badis Moalla"

        return GeneratedMessage(
            type=MessageType.COVER_LETTER,
            subject=subject,
            body=body,
            model_used=self._model,
        )

    async def generate_recruiter_message(
        self,
        job: JobListing,
        recruiter_name: str,
        match_report: MatchReport | None = None,
    ) -> GeneratedMessage:
        """Generate a LinkedIn InMail for a named recruiter."""
        prompt = recruiter_inmail_prompt(
            job_title=job.title,
            company=job.company,
            city=job.city,
            recruiter_name=recruiter_name,
            gap_mitigations=match_report.gap_mitigations if match_report else {},
        )

        body = await self._call(prompt, MessageType.RECRUITER_INMAIL)

        return GeneratedMessage(
            type=MessageType.RECRUITER_INMAIL,
            body=body,
            model_used=self._model,
        )

    async def generate_hr_email(
        self,
        job: JobListing,
        recruiter_name: str | None = None,
        company_detail: str | None = None,
        match_report: MatchReport | None = None,
    ) -> GeneratedMessage:
        """Generate a direct HR email."""
        prompt = hr_email_prompt(
            job_title=job.title,
            company=job.company,
            city=job.city,
            market=job.market,
            recruiter_name=recruiter_name,
            company_detail=company_detail,
            gap_mitigations=match_report.gap_mitigations if match_report else {},
        )

        body = await self._call(prompt, MessageType.HR_EMAIL)
        subject = self._extract_subject(body) or f"Application – {job.title} | Badis Moalla"

        return GeneratedMessage(
            type=MessageType.HR_EMAIL,
            subject=subject,
            body=body,
            model_used=self._model,
        )

    async def generate_follow_up(
        self,
        job: JobListing,
        recruiter_name: str | None = None,
        days_since_applied: int = 7,
    ) -> GeneratedMessage:
        """Generate a 7-day follow-up email."""
        prompt = follow_up_prompt(
            job_title=job.title,
            company=job.company,
            recruiter_name=recruiter_name,
            days_since_applied=days_since_applied,
        )

        body = await self._call(prompt, MessageType.FOLLOW_UP)
        subject = f"Follow-up: {job.title} Application | Badis Moalla"

        return GeneratedMessage(
            type=MessageType.FOLLOW_UP,
            subject=subject,
            body=body,
            model_used=self._model,
        )

    async def generate_hiring_manager_message(
        self,
        job: JobListing,
        manager_name: str,
        team_context: str | None = None,
    ) -> GeneratedMessage:
        """Generate a direct hiring manager LinkedIn message."""
        prompt = hiring_manager_prompt(
            job_title=job.title,
            company=job.company,
            manager_name=manager_name,
            team_context=team_context,
        )

        body = await self._call(prompt, MessageType.HIRING_MANAGER)

        return GeneratedMessage(
            type=MessageType.HIRING_MANAGER,
            body=body,
            model_used=self._model,
        )

    async def generate_application_answer(
        self,
        question: str,
        job: JobListing,
        max_words: int = 150,
    ) -> GeneratedMessage:
        """Answer a specific application question."""
        prompt = application_answer_prompt(
            question=question,
            job_title=job.title,
            company=job.company,
            max_words=max_words,
        )

        body = await self._call(prompt, MessageType.APPLICATION_ANSWER)

        return GeneratedMessage(
            type=MessageType.APPLICATION_ANSWER,
            body=body,
            model_used=self._model,
        )

    async def explain_match(
        self,
        job: JobListing,
        match_report: MatchReport,
    ) -> str:
        """
        Generate a human-readable explanation of the match decision.
        Used in CLI output to help the candidate understand why a job was scored as it was.
        """
        prompt = job_score_explanation_prompt(
            job_title=job.title,
            company=job.company,
            score=match_report.score,
            decision=match_report.decision,
            tier=match_report.tier,
            matched_keywords=match_report.matched_keywords,
            gaps=match_report.skill_gaps,
            mitigations=match_report.gap_mitigations,
        )

        return await self._call(prompt, MessageType.APPLICATION_ANSWER)

    async def score_job_match(
        self,
        job: JobListing,
    ) -> tuple[int, list[str]]:
        """
        Return (score, gaps) for a job.
        Note: primary scoring is done by core.matcher.JobMatcher (rule-based, fast, free).
        This method provides an AI-augmented score for REVIEW-tier jobs.
        """
        from core.matcher import JobMatcher
        matcher = JobMatcher()
        report = matcher.evaluate(job)
        return report.score, report.skill_gaps

    # ── Internal API call ─────────────────────────────────────────────────────

    async def _call(self, user_prompt: str, message_type: MessageType) -> str:
        """
        Make a Claude API call.
        Returns the text content of the first content block.
        Raises AIGenerationError on failure.
        Respects dry_run mode — returns placeholder without calling API.
        """
        if self._dry_run:
            logger.info(
                "DRY RUN | type={type} | prompt_chars={chars}",
                type=message_type.value,
                chars=len(user_prompt),
            )
            return (
                f"[DRY RUN — {message_type.value}]\n"
                f"This message would be generated by {self._model}.\n"
                f"Set APP_DRY_RUN=false in .env to generate real messages."
            )

        try:
            logger.debug(
                "Calling Claude | type={type} | model={model}",
                type=message_type.value,
                model=self._model,
            )

            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            text = response.content[0].text
            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            logger.info(
                "Claude response | type={type} | tokens={tokens} | chars={chars}",
                type=message_type.value,
                tokens=tokens_used,
                chars=len(text),
            )

            return text

        except anthropic.AuthenticationError as e:
            raise AIGenerationError(
                f"Invalid API key. Check ANTHROPIC_API_KEY in .env",
                message_type=message_type.value,
            ) from e
        except anthropic.RateLimitError as e:
            raise AIGenerationError(
                f"Claude rate limit hit. Wait before retrying.",
                message_type=message_type.value,
            ) from e
        except anthropic.APIError as e:
            raise AIGenerationError(
                f"Claude API error: {e}",
                message_type=message_type.value,
            ) from e

    @staticmethod
    def _extract_subject(body: str) -> str | None:
        """Extract subject line from generated body if present."""
        lines = body.strip().splitlines()
        for line in lines[:3]:
            if line.lower().startswith("subject:"):
                return line[8:].strip()
        return None
