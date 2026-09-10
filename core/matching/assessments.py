"""
Phase 2A: Assessment functions for work authorization, remote, language, salary, location.
Each function evaluates one dimension independently and returns a status string.

Design principle: Return UNKNOWN for ambiguous cases; only INCOMPATIBLE becomes a hard blocker.
All soft penalties are applied by matcher and must come from matching_rules.json config.
"""

from datetime import datetime, timedelta
import re

from core.matching.compatibility import (
    AuthorizationCompatibility,
    RemoteCompatibility,
    LanguageMatchStatus,
    SalaryCompatibility,
    LocationCompatibility,
    DeadlineType,
    # Phase 2B: Company intelligence
    IndustryRelevance,
    CompanyType,
    SponsorshipLikelihood,
)
from core.models import JobListing
from core.profile import CandidateProfile
from core.matching.rules import MatchingRules


def assess_work_authorization(
    job: JobListing,
    profile: CandidateProfile,
    rules: MatchingRules,
) -> AuthorizationCompatibility:
    """
    Match candidate's work authorization with employer's sponsorship availability.
    
    Returns COMPATIBLE/INCOMPATIBLE/REVIEW/NEUTRAL.
    Only INCOMPATIBLE is a hard blocker.
    """
    # If candidate is already authorized, there's no issue
    candidate_authorized = getattr(profile, "work_authorization_authorized", False)
    if candidate_authorized:
        return AuthorizationCompatibility.NEUTRAL
    
    # Candidate needs sponsorship - check employer availability
    # For now, use the existing visa_sponsorship flag
    # Future: extract more nuanced sponsorship info from description
    
    if job.visa_sponsorship:
        return AuthorizationCompatibility.COMPATIBLE
    
    # Check if description explicitly rejects sponsorship candidates
    if job.description:
        desc_lower = job.description.lower()
        if any(phrase in desc_lower for phrase in ["only eu citizens", "eu candidates only", "no sponsorship"]):
            return AuthorizationCompatibility.INCOMPATIBLE
    
    # Silent on sponsorship = need human review
    return AuthorizationCompatibility.REVIEW


def assess_remote_work(
    job: JobListing,
    profile: CandidateProfile,
) -> RemoteCompatibility:
    """
    Match candidate's remote preference with job's remote arrangement.
    
    Returns COMPATIBLE/MISMATCH_SLIGHT/MISMATCH_SIGNIFICANT/UNKNOWN.
    Only MISMATCH_SIGNIFICANT is a hard blocker.
    MISMATCH_SLIGHT triggers a soft score penalty.
    """
    # Extract remote policy from job description
    remote_policy = extract_remote_policy(job)
    
    # Get candidate preference (a flat list, e.g. ["full-time", "on-site", "hybrid"])
    work_type = getattr(profile, "remote_preference", [])
    if not work_type:
        return RemoteCompatibility.UNKNOWN

    wants_remote = "remote" in work_type
    accepts_onsite = "on-site" in work_type or "onsite" in work_type

    # Candidate requires remote (lists remote but not on-site)
    if wants_remote and not accepts_onsite:
        if remote_policy == "on_site":
            return RemoteCompatibility.MISMATCH_SIGNIFICANT
        elif remote_policy in ["fully_remote", "hybrid", "unknown"]:
            return RemoteCompatibility.COMPATIBLE

    # Candidate lists remote but also tolerates on-site -- treat as a strong preference, not a hard requirement
    elif wants_remote:
        if remote_policy == "on_site":
            return RemoteCompatibility.MISMATCH_SLIGHT
        else:
            return RemoteCompatibility.COMPATIBLE

    # Candidate doesn't list remote at all (e.g. accepts on-site/hybrid only)
    else:
        return RemoteCompatibility.COMPATIBLE


def assess_languages(
    job: JobListing,
    profile: CandidateProfile,
) -> dict[str, LanguageMatchStatus]:
    """
    For each language required in job, check candidate's proficiency.
    
    Returns dict: language -> MET/CLOSE/MISSING/UNKNOWN.
    """
    if not job.languages_required:
        return {}
    
    # Get candidate's languages from profile
    candidate_languages = getattr(profile, "languages", {})
    
    result = {}
    for lang_req in job.languages_required:
        lang = lang_req.get("language", "").lower()
        required_level = lang_req.get("required_level", "").lower()
        
        if lang not in candidate_languages:
            result[lang] = LanguageMatchStatus.MISSING.value
            continue
        
        candidate_level = candidate_languages[lang].lower()
        match = compare_language_levels(candidate_level, required_level)
        result[lang] = match.value
    
    return result


def assess_salary(
    job: JobListing,
    profile: CandidateProfile,
) -> SalaryCompatibility:
    """
    Check if job salary matches candidate's expectations.
    
    Returns WITHIN_RANGE/BELOW_MINIMUM/UNKNOWN/CURRENCY_MISMATCH.
    """
    # Job must have salary posted
    if not job.salary_min and not job.salary_max:
        return SalaryCompatibility.UNKNOWN
    
    # Get candidate minimum (stored in EUR/year)
    market_value = job.market.value if hasattr(job.market, "value") else str(job.market)
    expectation = profile.salary_expectation_for_market(market_value)
    if expectation is None:
        return SalaryCompatibility.UNKNOWN

    job_currency = (job.salary_currency or "").upper()
    if job_currency != expectation["currency"].upper():
        # No cross-currency conversion is performed -- see
        # core/profile.py salary_expectation_for_market docstring.
        return SalaryCompatibility.CURRENCY_MISMATCH

    try:
        candidate_min = int(expectation["range"].split("-")[0].strip())
    except (ValueError, IndexError, AttributeError):
        return SalaryCompatibility.UNKNOWN

    job_amount = job.salary_min or job.salary_max
    job_yearly = _to_yearly(job_amount, (job.salary_period or "unknown").lower())
    candidate_yearly = _to_yearly(candidate_min, expectation["period"])

    if job_yearly is None or candidate_yearly is None:
        return SalaryCompatibility.UNKNOWN

    if job_yearly >= candidate_yearly:
        return SalaryCompatibility.WITHIN_RANGE
    else:
        return SalaryCompatibility.BELOW_MINIMUM


def _to_yearly(amount: int | None, period: str) -> int | None:
    """
    Period-only normalization to a yearly figure -- reuses the same
    conversion factors as normalize_salary() below. Never performs
    currency conversion; callers must confirm matching currencies first.
    """
    if not amount:
        return None
    period = period.lower()
    if period in ("per_year", "annual", "yearly"):
        return amount
    if period in ("per_month", "monthly"):
        return amount * 12
    if period in ("per_hour", "hourly"):
        return amount * 2000
    if period in ("per_day", "daily"):
        return amount * 250
    return None


def assess_location(
    job: JobListing,
    profile: CandidateProfile,
) -> LocationCompatibility:
    """
    Check if job location matches candidate's target countries/regions.
    
    Returns COMPATIBLE/REMOTE_EU/UNKNOWN/INCOMPATIBLE.
    Only INCOMPATIBLE is a hard blocker.
    """
    # Get candidate targets
    target_countries = getattr(profile, "target_countries", [])
    
    # If job is remote, check remote region
    if job.remote_policy == "fully_remote":
        remote_region = job.remote_region or "unknown"
        if remote_region.lower() in ["europe", "emea"] and "europe" in str(target_countries).lower():
            return LocationCompatibility.REMOTE_EU
        elif remote_region.lower() == "worldwide":
            return LocationCompatibility.COMPATIBLE
        else:
            return LocationCompatibility.UNKNOWN
    
    # On-site or hybrid: check country
    job_country = job.location_country
    if not job_country:
        return LocationCompatibility.UNKNOWN
    
    if job_country.lower() in [c.lower() for c in target_countries]:
        return LocationCompatibility.COMPATIBLE
    else:
        return LocationCompatibility.INCOMPATIBLE


def assess_deadline(job: JobListing) -> tuple[DeadlineType, int | None]:
    """
    Extract application deadline type and days remaining.
    
    Returns (DeadlineType, days_remaining).
    """
    if not job.description:
        return DeadlineType.UNKNOWN, None
    
    desc_lower = job.description.lower()
    
    # Check for explicit deadline
    deadline_patterns = [
        r"deadline[:\s]+([a-z\s0-9,]+)",
        r"application closes[:\s]+([a-z\s0-9,]+)",
        r"apply by[:\s]+([a-z\s0-9,]+)",
    ]
    
    for pattern in deadline_patterns:
        match = re.search(pattern, desc_lower)
        if match:
            # Found explicit deadline text but don't parse date (too risky)
            # Return EXPLICIT without days_remaining
            return DeadlineType.EXPLICIT, None
    
    # Check for rolling deadline
    rolling_phrases = ["apply anytime", "rolling basis", "open until filled"]
    if any(phrase in desc_lower for phrase in rolling_phrases):
        return DeadlineType.ROLLING, None
    
    return DeadlineType.UNKNOWN, None


# ─ Helper functions ────────────────────────────────────────────────────────────

def extract_remote_policy(job: JobListing) -> str:
    """Infer remote policy from description."""
    if job.remote_policy:
        return job.remote_policy.lower()
    
    if not job.description:
        return "unknown"
    
    desc_lower = job.description.lower()
    
    if any(phrase in desc_lower for phrase in ["fully remote", "100% remote", "work from home"]):
        return "fully_remote"
    elif any(phrase in desc_lower for phrase in ["hybrid", "2-3 days", "3 days"]):
        return "hybrid"
    elif any(phrase in desc_lower for phrase in ["on-site", "office based", "at our office"]):
        return "on_site"
    
    return "unknown"


def compare_language_levels(candidate_level: str, required_level: str) -> LanguageMatchStatus:
    """
    Compare two CEFR proficiency levels.
    Levels: a1, a2, b1, b2, c1, c2, native.
    """
    level_order = ["a1", "a2", "b1", "b2", "c1", "c2", "native"]
    
    try:
        candidate_idx = level_order.index(candidate_level)
    except ValueError:
        return LanguageMatchStatus.UNKNOWN
    
    try:
        required_idx = level_order.index(required_level)
    except ValueError:
        return LanguageMatchStatus.UNKNOWN
    
    if candidate_idx >= required_idx:
        return LanguageMatchStatus.MET
    elif candidate_idx == required_idx - 1:
        return LanguageMatchStatus.CLOSE
    else:
        return LanguageMatchStatus.MISSING


def normalize_salary(amount: int | None, period: str, currency: str) -> int | None:
    """
    Convert salary to EUR/year if possible.
    Returns None if can't normalize (e.g., unknown currency).
    """
    if not amount:
        return None
    
    # Only normalize EUR; other currencies require exchange rates
    if currency != "EUR":
        return None
    
    if period == "per_year":
        return amount
    elif period == "per_month":
        return amount * 12
    elif period == "per_hour":
        return amount * 2000  # ~40 hrs/week * 50 weeks/year
    elif period == "per_day":
        return amount * 250  # ~250 working days/year
    else:
        return None


# ── Phase 2B: Company Intelligence Assessment Functions ────────────────────


def extract_company_industry(description: str | None) -> str | None:
    """
    Extract company industry keyword from job description.
    
    Returns first matching industry keyword, or None.
    MVP: Uses hardcoded industries (can move to config later).
    """
    if not description:
        return None
    
    description_lower = description.lower()
    
    # MVP industries (hardcoded for simplicity)
    industries = {
        "automotive": ["automotive", "adas", "ecu", "vehicle", "car", "automotive validation"],
        "fintech": ["fintech", "finance", "payment", "trading", "banking"],
        "medtech": ["medical", "healthcare", "pharma", "biotech"],
        "saas": ["saas", "cloud", "subscription"],
    }
    
    for industry, keywords in industries.items():
        for kw in keywords:
            if kw in description_lower:
                return industry
    
    return None


def assess_company_industry_relevance(
    job: "JobListing",
    profile: "CandidateProfile",
    rules: "MatchingRules"
) -> "IndustryRelevance":
    """
    Determine if company's industry is relevant to candidate's target roles.
    
    Conservative principle:
    - RELEVANT only when there is sufficient keyword/evidence support
    - IRRELEVANT only when there is meaningful evidence of mismatch
    - UNKNOWN when industry cannot be determined reliably
    
    Never classify as IRRELEVANT merely because it's not in preferred list.
    """
    from core.matching.compatibility import IndustryRelevance
    
    if not job.company_industry:
        return IndustryRelevance.UNKNOWN
    
    industry_lower = job.company_industry.lower()
    
    # Candidate's preferred industries (Badis: automotive/embedded focus)
    preferred_industries = ["automotive", "embedded", "iot", "vehicle", "adas", "ecu"]
    
    # Industries explicitly irrelevant (fashion, retail, etc.)
    excluded_industries = ["fashion", "retail", "hospitality", "entertainment"]
    
    # Strong positive signal
    for keyword in preferred_industries:
        if keyword in industry_lower:
            return IndustryRelevance.RELEVANT
    
    # Strong negative signal (explicit mismatch)
    for keyword in excluded_industries:
        if keyword in industry_lower:
            return IndustryRelevance.IRRELEVANT
    
    # Weak positive: any tech/software domain
    tech_keywords = ["tech", "software", "it", "fintech", "saas", "platform"]
    for keyword in tech_keywords:
        if keyword in industry_lower:
            return IndustryRelevance.RELEVANT
    
    # No definitive signals
    return IndustryRelevance.UNKNOWN


def assess_company_type(
    job: "JobListing",
    profile: "CandidateProfile"
) -> "CompanyType":
    """
    Determine company type: product vs consulting vs ESN.
    
    Based on keyword patterns in job description.
    ESN takes precedence over generic consulting.
    
    Returns CompanyType enum.
    """
    from core.matching.compatibility import CompanyType
    
    description = (job.description or "").lower()
    
    # Consulting/staffing indicators
    consulting_keywords = [
        "consulting", "client sites", "staffing", "billable", "client project",
        "on-site at client", "professional services", "contractor"
    ]
    
    # Product company indicators
    product_keywords = [
        "in-house", "our platform", "our product", "internal", "saas",
        "product development", "own product", "product team", "own codebase"
    ]
    
    # ESN indicators (European Staffing Network)
    esn_keywords = ["european staffing", "staffing network", "esn"]
    
    # Count keyword matches
    consulting_count = sum(1 for kw in consulting_keywords if kw in description)
    product_count = sum(1 for kw in product_keywords if kw in description)
    esn_count = sum(1 for kw in esn_keywords if kw in description)
    
    # ESN takes precedence if present with consulting signals
    if esn_count > 0 and consulting_count > 0:
        return CompanyType.ESN
    
    # Product vs consulting decision
    if product_count > consulting_count:
        return CompanyType.PRODUCT
    elif consulting_count > product_count:
        return CompanyType.CONSULTING
    
    # Weak signals: check for any type indicator
    if any(kw in description for kw in consulting_keywords):
        return CompanyType.CONSULTING
    if any(kw in description for kw in product_keywords):
        return CompanyType.PRODUCT
    
    return CompanyType.UNKNOWN


def assess_sponsorship_likelihood(
    job: "JobListing",
    profile: "CandidateProfile",
    rules: "MatchingRules"
) -> "SponsorshipLikelihood":
    """
    Evidence-based assessment of sponsorship likelihood.
    
    CRITICAL PRINCIPLE: Based ONLY on explicit evidence in job/company data.
    - Absence of sponsorship information ≠ evidence against sponsorship
    - Company type/size/multinational alone NEVER determines sponsorship
    - ESN/consulting with no sponsorship mention → UNKNOWN (not LIKELY)
    - Multinational with no sponsorship mention → POSSIBLE at best
    - Small company with no mention → UNKNOWN (not UNLIKELY)
    
    Returns:
        LIKELY: Only explicit sponsorship/visa/relocation support
        POSSIBLE: International signals without explicit sponsorship
        UNLIKELY: Explicit rejection or work auth requirement
        UNKNOWN: No meaningful evidence either way
    """
    from core.matching.compatibility import SponsorshipLikelihood
    
    description = (job.description or "").lower()
    
    # ── Explicit rejection signals (CHECK FIRST - highest confidence) ──
    sponsorship_rejected = [
        "no sponsorship", "no visa", "eu citizens only", "local candidates only",
        "citizenship required", "must have work authorization",
        "must already have", "must have unrestricted"
    ]
    
    # Check for explicit rejection FIRST (UNLIKELY)
    # Important: check multi-word phrases first to avoid matching "sponsorship" in "no sponsorship"
    if any(kw in description for kw in sponsorship_rejected):
        return SponsorshipLikelihood.UNLIKELY
    
    # ── Explicit sponsorship signals (HIGH confidence) ─────────────────
    sponsorship_offered = [
        "visa sponsorship", "sponsorship available", "sponsor visa",
        "visa support", "willing to sponsor", "can sponsor"
    ]
    
    # Check for explicit sponsorship offer (LIKELY)
    # Note: relocation alone is POSSIBLE, not LIKELY
    if any(kw in description for kw in sponsorship_offered):
        return SponsorshipLikelihood.LIKELY
    
    # ── International hiring signals (POSSIBLE, not automatic LIKELY) ──
    international_signals = [
        "multinational", "global", "international offices", "worldwide presence",
        "multiple countries", "global team", "international candidates"
    ]
    
    international_count = sum(1 for kw in international_signals if kw in description)
    
    # International signals suggest POSSIBLE (but not LIKELY without explicit sponsorship)
    if international_count > 0:
        return SponsorshipLikelihood.POSSIBLE
    
    # ── Relocation language without explicit sponsorship mention ───────
    # Relocation is ambiguous: could mean company pays for relocation of local candidate
    # or could mean visa sponsorship included. Without explicit sponsorship mention, treat as POSSIBLE.
    relocation_keywords = ["relocation package", "relocation support", "relocation assistance", "relocate"]
    if any(kw in description for kw in relocation_keywords):
        # Relocation mention without explicit sponsorship is POSSIBLE (ambiguous)
        return SponsorshipLikelihood.POSSIBLE
    
    # ── No meaningful signals → UNKNOWN ──────────────────────────────
    # NOTE: Absence of information ≠ evidence against sponsorship
    return SponsorshipLikelihood.UNKNOWN


