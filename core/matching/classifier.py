"""
core/matching/classifier.py
------------------------------
Classifies a job as PRIMARY, SECONDARY, or EXCLUDED based on title and
body keyword matches. This is always the first step of evaluation — an
EXCLUDED classification short-circuits the rest of the pipeline.

All keyword lists come from the MatchingRules passed in — nothing is
hardcoded here.
"""

from core.matching.rules import MatchingRules
from core.matching.text_utils import count_matches
from core.models import RoleTier


def classify_tier(title: str, full_text: str, rules: MatchingRules) -> RoleTier:
    """Classify job as PRIMARY, SECONDARY, or EXCLUDED."""

    # Check exclusions first — hard stop
    excl_title, _ = count_matches(title, rules.excluded_title_keywords)
    excl_body, _ = count_matches(full_text, rules.excluded_body_keywords)
    senior_data_engineer = "senior data engineer" in title
    senior_data_engineer_excluded = senior_data_engineer and any(
        keyword in full_text
        for keyword in rules.senior_data_engineer_exclusion_keywords
    )
    if (excl_title > 0 and not senior_data_engineer) or senior_data_engineer_excluded or excl_body >= 2:
        return RoleTier.EXCLUDED

    # Check primary
    prim_title, _ = count_matches(title, rules.primary_title_keywords)
    if prim_title > 0:
        return RoleTier.PRIMARY

    # Check secondary
    sec_title, _ = count_matches(title, rules.secondary_title_keywords)
    if sec_title > 0:
        return RoleTier.SECONDARY

    # Default: if body has strong primary signals, treat as primary
    prim_body, prim_kws = count_matches(full_text, rules.primary_body_keywords)
    if prim_body >= 3:
        return RoleTier.PRIMARY

    return RoleTier.EXCLUDED  # Unknown role type — skip
