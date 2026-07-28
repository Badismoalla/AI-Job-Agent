"""
core/matching/decision.py
----------------------------
Turns a score into a final APPLY / REVIEW / SKIP decision, and applies the
hard gate that SECONDARY (data/BI) roles must pass before a score is even
considered — they need >= 3 matched required skills AND a matching domain,
regardless of how high their score would otherwise be.

Required-skill/domain lists and decision thresholds all come from the
MatchingRules passed in — nothing is hardcoded here.
"""

from dataclasses import dataclass, field

from core.matching.rules import MatchingRules
from core.matching.text_utils import count_matches
from core.models import MatchDecision, RoleTier


@dataclass
class SecondaryGateResult:
    """Outcome of the SECONDARY-role hard gate check."""

    passed: bool
    skills_matched: list[str] = field(default_factory=list)
    domain_matched: bool = False


def secondary_gate(full_text: str, rules: MatchingRules) -> SecondaryGateResult:
    """
    Hard gate for SECONDARY (data/BI) roles: at least 3 required skills
    AND a matching domain, or the role is skipped regardless of score.
    """
    _, skills_matched = count_matches(full_text, rules.secondary_required_skills)
    _, sec_domains = count_matches(full_text, rules.secondary_domains)
    domain_matched = len(sec_domains) > 0
    passed = len(skills_matched) >= 3 and domain_matched
    return SecondaryGateResult(
        passed=passed,
        skills_matched=skills_matched,
        domain_matched=domain_matched,
    )


def decide(score: int, tier: RoleTier, rules: MatchingRules) -> MatchDecision:
    """Map score to APPLY / REVIEW / SKIP using tier-specific thresholds."""
    thresholds = (
        rules.primary_thresholds if tier == RoleTier.PRIMARY
        else rules.secondary_thresholds
    )
    if score >= thresholds["APPLY"]:
        return MatchDecision.APPLY
    if score >= thresholds["REVIEW"]:
        return MatchDecision.REVIEW
    return MatchDecision.SKIP
