"""
core/matching/scorer.py
--------------------------
Computes the 0-100 match score across five weighted categories:
title (35), keywords (25), domain (20), protocols (15), tools (5).

Each `score_*` function is independently testable; `score()` combines
them into a single ScoreResult for the orchestrator.

All keyword lists come from the MatchingRules passed in — nothing is
hardcoded here.
"""

from dataclasses import dataclass, field

from core.matching.rules import MatchingRules
from core.matching.text_utils import count_matches
from core.models import RoleTier


@dataclass
class ScoreResult:
    """Combined output of every scoring dimension."""

    score_breakdown: dict[str, int] = field(default_factory=dict)
    total_score: int = 0
    title_matched: list[str] = field(default_factory=list)
    keyword_matched: list[str] = field(default_factory=list)
    domain_found: str | None = None
    protocol_matched: list[str] = field(default_factory=list)
    tools_matched: list[str] = field(default_factory=list)


def score_title(title: str, tier: RoleTier, rules: MatchingRules) -> tuple[int, list[str]]:
    """Score 0-35 based on title keyword match."""
    kws = (
        rules.primary_title_keywords if tier == RoleTier.PRIMARY
        else rules.secondary_title_keywords
    )
    count, matched = count_matches(title, kws)
    if count == 0:
        return 0, []
    # 1 match = 20pts, 2+ matches = 35pts
    score = min(35, 20 + (count - 1) * 15)
    return score, matched


def score_keywords(full_text: str, tier: RoleTier, rules: MatchingRules) -> tuple[int, list[str]]:
    """Score 0-25 based on body keyword density."""
    kws = (
        rules.primary_body_keywords if tier == RoleTier.PRIMARY
        else rules.secondary_required_skills
    )
    count, matched = count_matches(full_text, kws)
    # 1 kw = 5pts, scales to 25pts at 5+ keywords
    score = min(25, count * 5)
    return score, matched


def score_domain(full_text: str, rules: MatchingRules) -> tuple[int, str | None]:
    """Score 0-20 based on company/sector domain match."""
    count, matched = count_matches(full_text, rules.domain_keywords)
    if count == 0:
        return 0, None
    return 20, matched[0]


def score_protocols(full_text: str, rules: MatchingRules) -> tuple[int, list[str]]:
    """Score 0-15 based on diagnostic protocol keyword match."""
    count, matched = count_matches(full_text, rules.protocol_keywords)
    score = min(15, count * 5)
    return score, matched


def score_tools(full_text: str, rules: MatchingRules) -> tuple[int, list[str]]:
    """Score 0-5 based on tool overlap."""
    count, matched = count_matches(full_text, rules.candidate_tools)
    return min(5, count * 1), matched


def score(title: str, full_text: str, tier: RoleTier, rules: MatchingRules) -> ScoreResult:
    """Run every scoring dimension and return the combined breakdown."""
    breakdown: dict[str, int] = {}

    title_score, title_matched = score_title(title, tier, rules)
    breakdown["title_match"] = title_score

    kw_score, kw_matched = score_keywords(full_text, tier, rules)
    breakdown["keyword_match"] = kw_score

    domain_score, domain_found = score_domain(full_text, rules)
    breakdown["domain_match"] = domain_score

    proto_score, proto_matched = score_protocols(full_text, rules)
    breakdown["protocol_match"] = proto_score

    tools_score, tools_matched = score_tools(full_text, rules)
    breakdown["tools_match"] = tools_score

    total_score = min(100, sum(breakdown.values()))

    return ScoreResult(
        score_breakdown=breakdown,
        total_score=total_score,
        title_matched=title_matched,
        keyword_matched=kw_matched,
        domain_found=domain_found,
        protocol_matched=proto_matched,
        tools_matched=tools_matched,
    )
