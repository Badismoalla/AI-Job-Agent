"""
core/models.py
--------------
Pydantic data models shared across all modules.

Why centralise models here?
- Single source of truth for data shapes
- Automatic validation when data flows between modules
- Self-documenting (field descriptions explain every attribute)
- Easy serialisation to/from JSON for storage

Every module imports from here. Nothing defines its own data shapes.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class RoleTier(str, Enum):
    """Whether a job belongs to the primary or secondary application tier."""
    PRIMARY = "primary"      # Testing / Validation / Automotive — always apply if score >= threshold
    SECONDARY = "secondary"  # Data/BI — apply only when domain + skill conditions pass
    EXCLUDED = "excluded"    # Never apply — ML, cloud arch, Spark, etc.


class MatchDecision(str, Enum):
    """The final application decision produced by the matcher."""
    APPLY = "APPLY"      # High confidence match — generate all messages and apply
    REVIEW = "REVIEW"    # Borderline — show to candidate before applying
    SKIP = "SKIP"        # Does not meet criteria — log and move on


class Market(str, Enum):
    POLAND = "Poland"
    NETHERLANDS = "Netherlands"
    LUXEMBOURG = "Luxembourg"
    UAE = "UAE"
    SAUDI_ARABIA = "Saudi Arabia"
    QATAR = "Qatar"


class ApplicationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    FOLLOW_UP_SENT = "follow_up_sent"
    WITHDRAWN = "withdrawn"


class ApplicationSource(str, Enum):
    PRACUJ = "Pracuj.pl"
    NOFLUFFJOBS = "NoFluffJobs"
    JUSTJOINIT = "JustJoinIT"
    BULLDOGJOB = "Bulldogjob"
    LINKEDIN = "LinkedIn"
    BAYT = "Bayt.com"
    CAREER_PAGE = "Career page"
    EMAIL = "Direct email"
    AGENCY = "Recruitment agency"
    REFERRAL = "Referral"
    MANUAL = "Manual (file)"  # Used by the analyze command — JD loaded from a text file


class MessageType(str, Enum):
    COVER_LETTER = "cover_letter"
    HR_EMAIL = "hr_email"
    RECRUITER_INMAIL = "recruiter_inmail"
    HIRING_MANAGER = "hiring_manager"
    FOLLOW_UP = "follow_up"
    APPLICATION_ANSWER = "application_answer"


# ── Job Models ────────────────────────────────────────────────────────────────

class JobListing(BaseModel):
    """A job listing found on any job board."""

    id: str = Field(description="Unique slug: company-role-city e.g. jsdsolutions-test-engineer-krakow")
    title: str = Field(description="Exact job title as posted")
    company: str = Field(description="Company name")
    city: str = Field(description="City of the role")
    market: Market = Field(description="Target market")
    url: str = Field(description="Direct URL to the job posting")
    source: ApplicationSource = Field(description="Which board found it")
    description: str | None = Field(default=None, description="Full job description text")
    salary_range: str | None = Field(default=None, description="Salary range if posted")
    visa_sponsorship: bool = Field(default=False, description="Does the posting mention visa sponsorship")
    posted_date: datetime | None = Field(default=None)
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    match_score: int | None = Field(default=None, description="0-100 relevance score assigned by AI")
    match_gaps: list[str] = Field(default_factory=list, description="Skills in JD not in profile")
    already_applied: bool = Field(default=False)
    duplicate_of: str | None = Field(default=None, description="ID of duplicate listing if detected")

    class Config:
        use_enum_values = True


# ── Match Report ─────────────────────────────────────────────────────────────

class MatchReport(BaseModel):
    """
    Full analysis of how well a job matches the candidate profile.
    Produced by core.matcher.JobMatcher for every scraped listing.
    Drives the APPLY / REVIEW / SKIP decision and seeds AI message generation.
    """

    job_id: str
    job_title: str
    company: str

    # Tier classification
    tier: RoleTier
    decision: MatchDecision

    # Scoring (0-100)
    score: int = Field(ge=0, le=100)
    score_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Score contribution by category: title_match, keyword_match, domain_match, protocol_match, tools_match"
    )

    # Match explanation
    matched_keywords: list[str] = Field(default_factory=list)
    matched_protocols: list[str] = Field(default_factory=list)
    matched_tools: list[str] = Field(default_factory=list)
    domain_match: bool = False
    domain_found: str | None = None

    # Gaps — what the JD asks for that the candidate lacks
    skill_gaps: list[str] = Field(default_factory=list)
    gap_mitigations: dict[str, str] = Field(
        default_factory=dict,
        description="For known gaps, the explanation to use in messages. e.g. CANoe -> DLT/Wireshark equivalent"
    )

    # Decision reasoning (human-readable, used in CLI output)
    reason: str = Field(description="One sentence explaining the APPLY/REVIEW/SKIP decision")

    # Flags
    has_visa_sponsorship: bool = False
    canoe_gap_mitigated: bool = Field(
        default=False,
        description="True when CANoe is listed as required but candidate's DLT/Wireshark experience covers it"
    )

    # Secondary role specific
    secondary_skills_matched: list[str] = Field(default_factory=list)
    secondary_domain_matched: bool = False

    evaluated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


# ── Application Models ────────────────────────────────────────────────────────

class GeneratedMessage(BaseModel):
    """A message generated by the AI module."""

    type: MessageType
    subject: str | None = None
    body: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    model_used: str | None = None
    tokens_used: int | None = None


class Application(BaseModel):
    """A job application — the core tracking unit."""

    id: str = Field(description="Unique application ID: app-{timestamp}")
    job: JobListing
    status: ApplicationStatus = Field(default=ApplicationStatus.PENDING)
    applied_at: datetime | None = None
    follow_up_sent_at: datetime | None = None
    interview_at: datetime | None = None
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    notes: str | None = None
    messages: list[GeneratedMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def needs_follow_up(self, after_days: int = 7) -> bool:
        """Return True if application was sent N+ days ago with no response."""
        if self.status != ApplicationStatus.SENT:
            return False
        if self.applied_at is None:
            return False
        delta = datetime.utcnow() - self.applied_at
        return delta.days >= after_days

    class Config:
        use_enum_values = True


# ── LinkedIn Models ───────────────────────────────────────────────────────────

class LinkedInProfileScore(BaseModel):
    """Scored snapshot of the LinkedIn profile."""

    section: str
    score: int = Field(ge=0, le=100)
    issue: str
    fix: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class RecruiterContact(BaseModel):
    """A recruiter found on LinkedIn to message."""

    name: str
    title: str | None = None
    company: str
    linkedin_url: str
    message_sent: bool = Field(default=False)
    message_sent_at: datetime | None = None
    replied: bool = Field(default=False)


# ── Daily Plan ────────────────────────────────────────────────────────────────

class DailyTask(BaseModel):
    """A single task in the daily plan."""

    id: str
    description: str
    priority: str = Field(description="high | medium | low")
    estimated_minutes: int
    completed: bool = Field(default=False)
    url: str | None = None


class DailyPlan(BaseModel):
    """The full daily job search plan."""

    date: datetime = Field(default_factory=datetime.utcnow)
    tasks: list[DailyTask] = Field(default_factory=list)
    jobs_to_apply: list[JobListing] = Field(default_factory=list)
    recruiters_to_message: list[RecruiterContact] = Field(default_factory=list)
    follow_ups_due: list[Application] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
