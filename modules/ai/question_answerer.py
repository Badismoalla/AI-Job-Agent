"""
modules/ai/question_answerer.py
----------------------------------
Answers a single discovered ATS application question safely.

Two-tier design, grounded in the real data/profile.json:

TIER A -- "personal/factual" questions (visa, sponsorship, salary,
relocation, notice period, travel, current employer, start dates,
years of experience, education, certifications, languages, location).
These are NEVER routed to the AI generator. Each is answered strictly
from core.profile.profile data via a dedicated lookup; if the specific
fact isn't present (e.g. notice period -- no profile field exists for
this at all), the result is REVIEW_REQUIRED. This is what prevents
hallucination for exactly the categories Phase 2F Step 2D calls out.

TIER B -- everything else (motivation, technical narrative, "why do
you want to join us", etc.) is routed to the EXISTING
ClaudeGenerator.generate_application_answer(), which is already cache-
wired (modules/ai/cache.py). Nothing here duplicates that cache.

A third, safe default: a question matching NEITHER tier (ambiguous /
unrecognized) is REVIEW_REQUIRED directly -- never sent to the AI just
because it isn't personal-factual. "The system is not confident which
field is being answered" is itself a reason to stop.

For any question with a fixed set of options (yes_no, select), the
final answer -- from either tier -- is validated against those options
before being accepted. A value that doesn't match any option is never
returned; the result becomes REVIEW_REQUIRED instead of picking an
arbitrary option.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from core.logger import get_logger
from core.models import JobListing
from core.profile import profile

logger = get_logger(__name__)


class AnswerProvenance(str, Enum):
    PROFILE = "profile"
    CACHED = "cached"
    AI_GENERATED = "ai_generated"
    REVIEW_REQUIRED = "review_required"


@dataclass
class ApplicationQuestion:
    """A single question discovered on a live ATS form."""

    label: str
    field_type: str
    options: list[str] | None = None
    required: bool = True


@dataclass
class AnswerResult:
    value: str | None
    provenance: AnswerProvenance
    review_required: bool
    reason: str | None = None


_PERSONAL_FACTUAL_PATTERNS = (
    "visa", "sponsorship", "work authorization", "authorized to work",
    "relocat",
    "salary", "compensation", "pay expectation",
    "notice period",
    "travel",
    "current employer", "current company", "currently work",
    "start date", "employment date",
    "years of experience", "how many years",
    "education", "degree", "qualification",
    "certification", "certificate",
    "language", "proficiency", "fluent",
    "location", "based in", "where are you based",
)


def _is_personal_factual(label: str) -> bool:
    lowered = label.lower()
    return any(pattern in lowered for pattern in _PERSONAL_FACTUAL_PATTERNS)


_OPEN_ENDED_PATTERNS = (
    "why", "describe", "tell us", "tell me", "what makes you", "explain",
    "how would you", "what interests you", "what motivates", "what excites",
)


def _is_recognized_open_ended(label: str) -> bool:
    lowered = label.lower()
    return any(pattern in lowered for pattern in _OPEN_ENDED_PATTERNS)


class QuestionAnswerer:
    """Answers one ApplicationQuestion at a time, safely."""

    def __init__(self, generator: Any) -> None:
        self._generator = generator

    async def answer(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult:
        if _is_personal_factual(question.label):
            result = self._answer_from_profile(question, job)
        elif _is_recognized_open_ended(question.label):
            result = await self._answer_from_ai(question, job)
        else:
            result = AnswerResult(
                value=None, provenance=AnswerProvenance.REVIEW_REQUIRED, review_required=True,
                reason=f"Question does not match a known personal/factual category or a recognized "
                       f"open-ended pattern -- not confident which field is being answered: {question.label!r}",
            )

        return self._validate_against_options(result, question)

    def _answer_from_profile(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult:
        handlers = (
            self._try_visa_sponsorship,
            self._try_relocation,
            self._try_salary,
            self._try_education,
            self._try_certifications,
            self._try_language,
            self._try_years_experience,
            self._try_current_employer,
            self._try_location,
        )
        for handler in handlers:
            result = handler(question, job)
            if result is not None:
                return result

        return AnswerResult(
            value=None, provenance=AnswerProvenance.REVIEW_REQUIRED, review_required=True,
            reason=f"This question requires personal information not available in the candidate profile: {question.label!r}",
        )

    def _try_visa_sponsorship(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("visa", "sponsorship", "work authorization", "authorized to work")):
            return None
        needs_sponsorship = profile.visa_sponsorship_needed
        value = "Yes" if needs_sponsorship else "No"
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason=f"From profile.target.visa_required={needs_sponsorship!r}",
        )

    def _try_relocation(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        if "relocat" not in question.label.lower():
            return None
        relocation = profile.target.get("relocation")
        if not relocation:
            return None
        value = "Yes" if relocation else "No"
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason=f"From profile.target.relocation={relocation!r}",
        )

    def _try_salary(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("salary", "compensation", "pay expectation")):
            return None
        market_value = job.market.value if hasattr(job.market, "value") else str(job.market)
        expectation = profile.salary_expectation_for_market(market_value)
        if expectation is None:
            return AnswerResult(
                value=None, provenance=AnswerProvenance.REVIEW_REQUIRED, review_required=True,
                reason=f"No salary expectation configured for market '{market_value}'.",
            )
        value = f"{expectation['range']} {expectation['currency']} ({expectation['period']})"
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason=f"From profile.salary_expectation_for_market({market_value!r}) [{expectation['key']}]",
        )

    def _try_education(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("degree", "education", "qualification")):
            return None
        edu = profile.education
        value = f"{edu.get('degree', '')}, {edu.get('institution', '')}".strip(", ")
        if not value:
            return None
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason="From profile.education",
        )

    def _try_certifications(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("certification", "certificate")):
            return None
        completed = [c["name"] for c in profile.certifications if c.get("status") == "completed"]
        if not completed:
            return None
        value = ", ".join(completed)
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason="From profile.certifications (status=completed only)",
        )

    def _try_language(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("language", "proficiency", "fluent")):
            return None
        langs = profile.languages

        for lang_name, level in langs.items():
            if lang_name in l:
                display = "Native" if level == "native" else level.upper()
                return AnswerResult(
                    value=display, provenance=AnswerProvenance.PROFILE, review_required=False,
                    reason=f"From profile.languages[{lang_name!r}]",
                )

        if "what language" in l or "which language" in l:
            value = ", ".join(
                f"{name.capitalize()} ({'Native' if level == 'native' else level.upper()})"
                for name, level in langs.items()
            )
            return AnswerResult(
                value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
                reason="From profile.languages (all)",
            )

        return None

    def _try_years_experience(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("years of experience", "how many years")):
            return None
        starts = [e["start"] for e in profile.experience if e.get("start")]
        if not starts:
            return None
        earliest = min(starts)
        try:
            start_date = datetime.strptime(earliest, "%Y-%m")
        except ValueError:
            return None
        years = (datetime.now() - start_date).days / 365.25
        value = f"~{round(years)} years"
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason=f"Computed from profile.experience earliest start ({earliest})",
        )

    def _try_current_employer(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("current employer", "current company", "currently work")):
            return None
        current = next((e for e in profile.experience if e.get("current")), None)
        if current is None:
            return None
        return AnswerResult(
            value=current["company"], provenance=AnswerProvenance.PROFILE, review_required=False,
            reason="From profile.experience (current=true)",
        )

    def _try_location(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult | None:
        l = question.label.lower()
        if not any(k in l for k in ("location", "based in", "where are you based")):
            return None
        if "preferred" in l or "which office" in l:
            return None
        value = profile.personal.get("location")
        if not value:
            return None
        return AnswerResult(
            value=value, provenance=AnswerProvenance.PROFILE, review_required=False,
            reason="From profile.personal.location",
        )

    async def _answer_from_ai(self, question: ApplicationQuestion, job: JobListing) -> AnswerResult:
        message = await self._generator.generate_application_answer(question=question.label, job=job)
        return AnswerResult(
            value=message.body, provenance=AnswerProvenance.AI_GENERATED, review_required=False,
            reason="Generated by ClaudeGenerator.generate_application_answer (cache-wired)",
        )

    def _validate_against_options(self, result: AnswerResult, question: ApplicationQuestion) -> AnswerResult:
        if result.review_required or result.value is None:
            return result
        if not question.options:
            return result

        matched = self._match_option(result.value, question.options)
        if matched is None:
            return AnswerResult(
                value=None, provenance=AnswerProvenance.REVIEW_REQUIRED, review_required=True,
                reason=f"Answer {result.value!r} does not match any available option {question.options!r}.",
            )
        if matched != result.value:
            result = AnswerResult(
                value=matched, provenance=result.provenance, review_required=False, reason=result.reason,
            )
        return result

    @staticmethod
    def _match_option(value: str, options: list[str]) -> str | None:
        if value in options:
            return value

        lowered_map = {opt.lower(): opt for opt in options}
        if value.lower() in lowered_map:
            return lowered_map[value.lower()]

        if value.lower() in ("yes", "true"):
            for opt in options:
                if opt.lower() in ("yes", "y", "true"):
                    return opt
        if value.lower() in ("no", "false"):
            for opt in options:
                if opt.lower() in ("no", "n", "false"):
                    return opt

        return None
