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

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def normalize_datetime(value: datetime | None) -> datetime | None:
    """Normalize naive datetimes to UTC and preserve timezone-aware values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime(value: datetime | str | None) -> datetime | None:
    """Parse a stored datetime value from either a string or a datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return normalize_datetime(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return normalize_datetime(parsed)
    return None


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
    scraped_at: datetime = Field(default_factory=utc_now)
    match_score: int | None = Field(default=None, description="0-100 relevance score assigned by AI")
    match_gaps: list[str] = Field(default_factory=list, description="Skills in JD not in profile")
    already_applied: bool = Field(default=False)
    duplicate_of: str | None = Field(default=None, description="ID of duplicate listing if detected")
    
    # Phase 2A: Extracted structured fields (all optional for backward compatibility)
    remote_policy: str | None = Field(
        default=None,
        description="FULLY_REMOTE/HYBRID/ON_SITE/UNKNOWN extracted from description"
    )
    languages_required: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {language, required_level, is_mandatory} dicts extracted from description"
    )
    salary_min: int | None = Field(default=None, description="Salary minimum (original currency/period)")
    salary_max: int | None = Field(default=None, description="Salary maximum (original currency/period)")
    salary_currency: str | None = Field(default=None, description="Currency code (EUR, PLN, USD, etc.)")
    salary_period: str | None = Field(default=None, description="PER_YEAR/PER_MONTH/PER_HOUR/PER_DAY/UNKNOWN")
    salary_original_text: str | None = Field(default=None, description="Original salary text from posting")
    application_deadline: str | None = Field(
        default=None,
        description="ISO date string if explicit deadline found"
    )
    location_country: str | None = Field(default=None, description="Country extracted or inferred from location")
    remote_region: str | None = Field(
        default=None,
        description="Region if remote (e.g., 'Europe', 'EMEA', 'Worldwide')"
    )
    
    # Phase 2B: Company Intelligence (all optional, inferred from description/source)
    company_domain: str | None = Field(
        default=None,
        description="Company domain/website inferred from career page URL or known companies"
    )
    company_industry: str | None = Field(
        default=None,
        description="Industry keyword inferred from job description (e.g., 'automotive', 'fintech')"
    )
    company_size: str | None = Field(
        default=None,
        description="Company size category: SMALL/MEDIUM/LARGE or None if unknown"
    )
    company_description: str | None = Field(
        default=None,
        description="Short company description extracted from job posting (if available)"
    )

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

    # Phase 2A: Work authorization, remote, language, salary, location assessment
    # All default to empty/UNKNOWN for backward compatibility
    work_authorization_compatibility: str | None = Field(
        default=None,
        description="COMPATIBLE/INCOMPATIBLE/REVIEW/NEUTRAL from compatibility.AuthorizationCompatibility"
    )
    remote_compatibility: str | None = Field(
        default=None,
        description="COMPATIBLE/MISMATCH_SLIGHT/MISMATCH_SIGNIFICANT/UNKNOWN from compatibility.RemoteCompatibility"
    )
    language_requirements: dict[str, str] = Field(
        default_factory=dict,
        description="Language -> MET/CLOSE/MISSING/UNKNOWN from compatibility.LanguageMatchStatus"
    )
    salary_compatibility: str | None = Field(
        default=None,
        description="WITHIN_RANGE/BELOW_MINIMUM/UNKNOWN/CURRENCY_MISMATCH from compatibility.SalaryCompatibility"
    )
    location_compatibility: str | None = Field(
        default=None,
        description="COMPATIBLE/REMOTE_EU/UNKNOWN/INCOMPATIBLE from compatibility.LocationCompatibility"
    )
    
    # Hard blockers: only certain incompatibilities that justify SKIP/REVIEW
    hard_blockers: list[str] = Field(
        default_factory=list,
        description="Definite incompatibilities (e.g., visa sponsorship required but not offered)"
    )
    
    # Score adjustments: explain soft penalties (e.g., remote preference mismatch)
    score_adjustments: dict[str, int] = Field(
        default_factory=dict,
        description="Score penalty reason -> points deducted (e.g., {'remote_preference_mismatch': 10})"
    )
    
    # Phase 2B: Company Intelligence Assessment Results
    # All default to None/empty for backward compatibility
    company_industry: str | None = Field(
        default=None,
        description="Industry category inferred for this job's company"
    )
    industry_relevance: str | None = Field(
        default=None,
        description="RELEVANT/IRRELEVANT/UNKNOWN from compatibility.IndustryRelevance"
    )
    company_type: str | None = Field(
        default=None,
        description="PRODUCT/CONSULTING/ESN/OTHER/UNKNOWN from compatibility.CompanyType"
    )
    company_type_match: str | None = Field(
        default=None,
        description="PREFERRED/ACCEPTABLE/UNPREFERRED/UNKNOWN (candidate preference vs inferred type)"
    )
    sponsorship_likelihood: str | None = Field(
        default=None,
        description="LIKELY/POSSIBLE/UNLIKELY/UNKNOWN from compatibility.SponsorshipLikelihood"
    )

    # Secondary role specific
    secondary_skills_matched: list[str] = Field(default_factory=list)
    secondary_domain_matched: bool = False

    evaluated_at: datetime = Field(default_factory=utc_now)

    class Config:
        use_enum_values = True


# ── Application Models ────────────────────────────────────────────────────────

class GeneratedMessage(BaseModel):
    """A message generated by the AI module."""

    type: MessageType
    subject: str | None = None
    body: str
    generated_at: datetime = Field(default_factory=utc_now)
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
    cv_used: str | None = Field(
        default=None,
        description="CV identifier actually selected for this application (core.cv_selector.CVSelection.cv_id), e.g. 'europe' or 'gcc'. None if no CV was selected/tracked.",
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def needs_follow_up(self, after_days: int = 7) -> bool:
        """Return True if application was sent N+ days ago with no response."""
        if self.status != ApplicationStatus.SENT:
            return False
        if self.applied_at is None:
            return False
        delta = utc_now() - normalize_datetime(self.applied_at)
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
    checked_at: datetime = Field(default_factory=utc_now)


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

    date: datetime = Field(default_factory=utc_now)
    tasks: list[DailyTask] = Field(default_factory=list)
    jobs_to_apply: list[JobListing] = Field(default_factory=list)
    recruiters_to_message: list[RecruiterContact] = Field(default_factory=list)
    follow_ups_due: list[Application] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
