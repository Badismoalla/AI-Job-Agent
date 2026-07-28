"""
core/matching/reason_builder.py
----------------------------------
Builds the one-sentence, human-readable explanation of why a job got the
decision it did. Used directly in the CLI (report.reason) and fed into
AI message generation as context.
"""

from core.models import MatchDecision, RoleTier


def build_reason(
    decision: MatchDecision,
    score: int,
    tier: RoleTier,
    title_matched: list[str],
    kw_matched: list[str],
    domain_found: str | None,
    proto_matched: list[str],
    gaps: list[str],
    canoe_mitigated: bool,
) -> str:
    """Build a one-sentence human-readable explanation of the decision."""
    parts = []

    if decision == MatchDecision.APPLY:
        parts.append(f"Strong {tier.value} role match (score {score}/100).")
        if title_matched:
            parts.append(f"Title matches: {', '.join(title_matched[:2])}.")
        if domain_found:
            parts.append(f"Domain confirmed: {domain_found}.")
        if proto_matched:
            parts.append(f"Protocol overlap: {', '.join(proto_matched[:3])}.")
        if canoe_mitigated:
            parts.append("CANoe gap mitigated by DLT/Wireshark experience.")

    elif decision == MatchDecision.REVIEW:
        parts.append(f"Partial match (score {score}/100) — review before applying.")
        if gaps:
            parts.append(f"Gaps: {', '.join(gaps[:3])}.")

    else:  # SKIP
        parts.append(f"Below threshold (score {score}/100).")
        if not title_matched:
            parts.append("No primary title keywords matched.")
        if gaps:
            parts.append(f"Unmitigated gaps: {', '.join(gaps[:2])}.")

    return " ".join(parts)
