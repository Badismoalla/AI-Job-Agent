"""
core/matching/gap_detector.py
--------------------------------
Detects skill gaps mentioned in a job description and maps each known
gap to the candidate's mitigation story (e.g. CANoe -> DLT/Wireshark).

Every gap check (trigger keyword, gap label, mitigation text, and whether it
counts toward the "CANoe mitigated" flag) comes from rules.gap_checks —
nothing is hardcoded here.
"""

from dataclasses import dataclass, field

from core.matching.rules import MatchingRules


@dataclass
class GapResult:
    """Skill gaps found in a JD, with their mitigations."""

    gaps: list[str] = field(default_factory=list)
    mitigations: dict[str, str] = field(default_factory=dict)
    canoe_mitigated: bool = False


def detect_gaps(full_text: str, rules: MatchingRules) -> GapResult:
    """
    Detect skill gaps from the JD and map known mitigations.
    Returns a GapResult(gaps, mitigations, canoe_mitigated).
    """
    gaps: list[str] = []
    mitigations: dict[str, str] = {}
    canoe_mitigated = False

    for check in rules.gap_checks:
        gap_name = check["gap_name"]
        trigger = check["trigger"]
        mitigation = check["mitigation"]

        if trigger in full_text:
            gaps.append(gap_name)
            mitigations[gap_name] = mitigation
            if check.get("canoe_mitigation", False):
                canoe_mitigated = True

    return GapResult(gaps=gaps, mitigations=mitigations, canoe_mitigated=canoe_mitigated)
