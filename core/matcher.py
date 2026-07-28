"""
core/matcher.py
---------------
Job matching engine — the brain of the system.

For every scraped JobListing, the matcher:
1. Classifies the role as PRIMARY / SECONDARY / EXCLUDED
2. Calculates a match score (0-100) with per-category breakdown
3. Identifies matched keywords, protocols, tools, and domain
4. Detects skill gaps and maps known mitigations (e.g. CANoe → DLT/Wireshark)
5. Issues a final APPLY / REVIEW / SKIP decision

Decision thresholds:
  PRIMARY  role: APPLY >= 65 | REVIEW 40-64 | SKIP < 40
  SECONDARY role: APPLY >= 75 | REVIEW 60-74 | SKIP < 60 (higher bar)
  EXCLUDED  role: always SKIP

Design principle: optimise for interview conversion, not application volume.
A rejected REVIEW is better than a wasted APPLY on the wrong role.

This module is a thin orchestrator only. Each concern of the pipeline lives
in its own component under core/matching/, independently readable and
testable:

    core/matching/rules.py          — loads config/matching_rules.json
    core/matching/classifier.py     — PRIMARY / SECONDARY / EXCLUDED tiering
    core/matching/scorer.py         — 0-100 score breakdown (5 categories)
    core/matching/gap_detector.py   — known skill gaps + mitigations
    core/matching/decision.py       — score -> APPLY/REVIEW/SKIP + secondary gate
    core/matching/reason_builder.py — human-readable explanation string

No keyword list, weight, or threshold is hardcoded in Python: every value
used by the components above comes from config/matching_rules.json, loaded
once when JobMatcher is constructed. Editing matching behaviour (adding a
keyword, moving a threshold, adding a gap mitigation) means editing that
JSON file — no Python change required.

JobMatcher.evaluate() sequences the components; it contains no scoring,
classification, or gap-detection logic of its own.
"""

from pathlib import Path

from core.logger import get_logger
from core.matching import classifier, gap_detector, reason_builder, scorer
from core.matching.decision import decide, secondary_gate
from core.matching.rules import MatchingRules, load_matching_rules
from core.matching.text_utils import normalise
from core.models import JobListing, MatchDecision, MatchReport, RoleTier

logger = get_logger(__name__)


class JobMatcher:
    """
    Scores and classifies job listings against the candidate profile.

    Orchestrates the classifier, scorer, gap detector, decision, and reason
    builder components from core/matching/ — see module docstring above.
    Matching rules (keyword lists, weights, thresholds) are loaded once from
    config/matching_rules.json when this class is constructed.

    Usage:
        matcher = JobMatcher()
        report = matcher.evaluate(job_listing)
        if report.decision == MatchDecision.APPLY:
            # proceed to generate messages

        # Or load rules from a different file (e.g. in tests):
        matcher = JobMatcher(rules_path=Path("tests/fixtures/custom_rules.json"))
    """

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules: MatchingRules = load_matching_rules(rules_path)

    def evaluate(self, job: JobListing) -> MatchReport:
        """
        Full evaluation pipeline for a single job listing.
        Returns a MatchReport with decision, score, gaps, and mitigations.
        """
        title = normalise(job.title)
        description = normalise(job.description or "")
        company = normalise(job.company)
        full_text = f"{title} {company} {description}"

        # ── Step 1: Classify tier ─────────────────────────────────────────
        tier = classifier.classify_tier(title, full_text, self.rules)

        if tier == RoleTier.EXCLUDED:
            return MatchReport(
                job_id=job.id,
                job_title=job.title,
                company=job.company,
                tier=RoleTier.EXCLUDED,
                decision=MatchDecision.SKIP,
                score=0,
                reason="Role is in the excluded list (ML/AI/cloud architecture/Spark). Automatically skipped.",
                has_visa_sponsorship=job.visa_sponsorship,
            )

        # ── Step 2: Score ─────────────────────────────────────────────────
        score_result = scorer.score(title, full_text, tier, self.rules)

        # ── Step 3: Gap analysis ─────────────────────────────────────────
        gap_result = gap_detector.detect_gaps(full_text, self.rules)

        # ── Step 4: Secondary role additional checks ──────────────────────
        secondary_skills: list[str] = []
        secondary_domain = False
        if tier == RoleTier.SECONDARY:
            gate = secondary_gate(full_text, self.rules)
            secondary_skills = gate.skills_matched
            secondary_domain = gate.domain_matched

            # Hard gate: secondary roles must match >= 3 skills AND domain
            if not gate.passed:
                return MatchReport(
                    job_id=job.id,
                    job_title=job.title,
                    company=job.company,
                    tier=RoleTier.SECONDARY,
                    decision=MatchDecision.SKIP,
                    score=score_result.total_score,
                    score_breakdown=score_result.score_breakdown,
                    skill_gaps=gap_result.gaps,
                    secondary_skills_matched=secondary_skills,
                    secondary_domain_matched=secondary_domain,
                    reason=(
                        f"Secondary data role did not meet gate: "
                        f"skills matched={len(secondary_skills)}/3 required, "
                        f"domain match={secondary_domain}. Skipped."
                    ),
                    has_visa_sponsorship=job.visa_sponsorship,
                )

        # ── Step 5: Final decision ────────────────────────────────────────
        decision = decide(score_result.total_score, tier, self.rules)

        reason = reason_builder.build_reason(
            decision, score_result.total_score, tier,
            score_result.title_matched, score_result.keyword_matched,
            score_result.domain_found, score_result.protocol_matched,
            gap_result.gaps, gap_result.canoe_mitigated,
        )

        report = MatchReport(
            job_id=job.id,
            job_title=job.title,
            company=job.company,
            tier=tier,
            decision=decision,
            score=score_result.total_score,
            score_breakdown=score_result.score_breakdown,
            matched_keywords=score_result.keyword_matched,
            matched_protocols=score_result.protocol_matched,
            matched_tools=score_result.tools_matched,
            domain_match=score_result.domain_found is not None,
            domain_found=score_result.domain_found,
            skill_gaps=gap_result.gaps,
            gap_mitigations=gap_result.mitigations,
            canoe_gap_mitigated=gap_result.canoe_mitigated,
            secondary_skills_matched=secondary_skills,
            secondary_domain_matched=secondary_domain,
            reason=reason,
            has_visa_sponsorship=job.visa_sponsorship,
        )

        logger.info(
            "Evaluated | company={company} | title={title} | tier={tier} | "
            "score={score} | decision={decision}",
            company=job.company,
            title=job.title,
            tier=tier.value,
            score=score_result.total_score,
            decision=decision.value,
        )

        return report
