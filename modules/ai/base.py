"""
modules/ai/base.py
------------------
Abstract base for AI-powered message generation.

Concrete implementation (Claude API) goes in modules/ai/claude_generator.py
This base defines the contract every generator must fulfil:
- generate_cover_letter()
- generate_recruiter_message()
- generate_hr_email()
- generate_follow_up()
- generate_application_answer()
- score_job_match()

Why abstract?
- Swap Claude for GPT-4 or a local model without changing any call sites
- Test with a mock generator that returns fixed strings
"""

from abc import ABC, abstractmethod

from core.models import GeneratedMessage, JobListing, MessageType


class BaseMessageGenerator(ABC):
    """Abstract interface for all AI message generators."""

    @abstractmethod
    async def generate_cover_letter(
        self,
        job: JobListing,
        recruiter_name: str | None = None,
        company_detail: str | None = None,
    ) -> GeneratedMessage:
        """Generate a personalised cover letter for a specific job."""
        ...

    @abstractmethod
    async def generate_recruiter_message(
        self,
        job: JobListing,
        recruiter_name: str,
    ) -> GeneratedMessage:
        """Generate a LinkedIn InMail for a named recruiter."""
        ...

    @abstractmethod
    async def generate_hr_email(
        self,
        job: JobListing,
        recruiter_name: str | None = None,
    ) -> GeneratedMessage:
        """Generate a direct HR email."""
        ...

    @abstractmethod
    async def generate_follow_up(
        self,
        job: JobListing,
        recruiter_name: str | None = None,
        days_since_applied: int = 7,
    ) -> GeneratedMessage:
        """Generate a 7-day follow-up email."""
        ...

    @abstractmethod
    async def generate_application_answer(
        self,
        question: str,
        job: JobListing,
        max_words: int = 150,
    ) -> GeneratedMessage:
        """Answer a specific application question."""
        ...

    @abstractmethod
    async def score_job_match(
        self,
        job: JobListing,
    ) -> tuple[int, list[str]]:
        """
        Score how well this job matches the profile.

        Returns:
            (score 0-100, list of gap descriptions)
        """
        ...
