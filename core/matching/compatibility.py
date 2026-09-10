"""
Phase 2A: Minimal compatibility enums for work authorization, remote, language, salary, location.
Only created enums that can't be represented as strings and have multiple discrete states.
"""

from enum import Enum


class AuthorizationStatus(str, Enum):
    """Candidate's existing right to work (independent of sponsorship)."""
    AUTHORIZED = "authorized"
    SPONSORSHIP_NEEDED = "sponsorship_needed"
    UNKNOWN = "unknown"


class SponsorshipAvailability(str, Enum):
    """Employer's sponsorship availability (from job posting)."""
    OFFERED = "offered"
    NOT_OFFERED = "not_offered"
    UNKNOWN = "unknown"


class AuthorizationCompatibility(str, Enum):
    """Result of matching candidate auth + employer sponsorship."""
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"  # Hard blocker
    REVIEW = "review"  # Escalate to human
    NEUTRAL = "neutral"


class RemoteCompatibility(str, Enum):
    """Matching result for remote work preference + job arrangement."""
    COMPATIBLE = "compatible"
    MISMATCH_SLIGHT = "mismatch_slight"  # Score penalty only
    MISMATCH_SIGNIFICANT = "mismatch_significant"  # Hard blocker
    UNKNOWN = "unknown"


class LanguageMatchStatus(str, Enum):
    """How well does candidate meet a language requirement."""
    MET = "met"
    CLOSE = "close"  # Within 1 CEFR level
    MISSING = "missing"
    UNKNOWN = "unknown"


class SalaryCompatibility(str, Enum):
    """Does job salary match candidate expectations."""
    WITHIN_RANGE = "within_range"
    BELOW_MINIMUM = "below_minimum"
    UNKNOWN = "unknown"
    CURRENCY_MISMATCH = "currency_mismatch"  # Can't compare


class LocationCompatibility(str, Enum):
    """Does job location match candidate's targets."""
    COMPATIBLE = "compatible"
    REMOTE_EU = "remote_eu"  # Remote within Europe (OK for EU target)
    UNKNOWN = "unknown"
    INCOMPATIBLE = "incompatible"


class DeadlineType(str, Enum):
    """Is there an application deadline."""
    EXPLICIT = "explicit"
    ROLLING = "rolling"
    UNKNOWN = "unknown"


# ── Phase 2B: Company Intelligence Enums ──────────────────────────────────

class IndustryRelevance(str, Enum):
    """Company industry relevance to candidate's target roles."""
    RELEVANT = "relevant"       # Industry matches candidate's focus
    IRRELEVANT = "irrelevant"   # Industry is known to be irrelevant
    UNKNOWN = "unknown"         # Industry could not be determined


class CompanyType(str, Enum):
    """Type of company: product, consulting, ESN, other."""
    PRODUCT = "product"         # Product/SaaS company (in-house tech)
    CONSULTING = "consulting"   # Consulting/staffing (client-side, billable)
    ESN = "esn"                 # European Staffing Network
    OTHER = "other"             # Other type (public sector, NGO, etc.)
    UNKNOWN = "unknown"         # Could not determine


class SponsorshipLikelihood(str, Enum):
    """
    Evidence-based assessment of sponsorship likelihood.
    
    CRITICAL: Based only on explicit evidence in job/company data.
    - Absence of sponsorship information ≠ evidence against sponsorship
    - Company type/size/multinational alone never determines sponsorship
    """
    LIKELY = "likely"           # Explicit sponsorship/visa/relocation support
    POSSIBLE = "possible"       # Implicit signals (international hiring, etc.)
    UNLIKELY = "unlikely"       # Explicit rejection or work auth requirement
    UNKNOWN = "unknown"         # No meaningful evidence either way

