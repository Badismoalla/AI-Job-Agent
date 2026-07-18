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
"""

import re
from dataclasses import dataclass

from core.logger import get_logger
from core.models import JobListing, MatchDecision, MatchReport, RoleTier

logger = get_logger(__name__)

# ── Constants — sourced from profile.json strategy ───────────────────────────

# Title keywords that immediately classify a role as PRIMARY
_PRIMARY_TITLE_KEYWORDS: list[str] = [
    "test engineer", "validation engineer", "validation engineer",
    "embedded test", "automotive test", "qa engineer", "ecу test",
    "ecu test", "system test", "integration test", "software quality",
    "test automation", "verification engineer", "v&v engineer",
    "autosar", "software tester", "quality engineer", "test developer",
    "sw test", "software test", "hw test", "hardware test",
]

# Body/description keywords that boost PRIMARY score
_PRIMARY_BODY_KEYWORDS: list[str] = [
    "test engineer", "validation", "verification", "embedded",
    "automotive", "ecu", "autosar", "uds", "doip", "diagnostics",
    "aspice", "swe.6", "software quality", "integration testing",
    "system testing", "v-model", "traceability", "test case",
    "test plan", "defect", "test guide", "dlt", "wireshark",
    "can bus", "can protocol", "iso 26262", "functional safety",
]

# Domain keywords — automotive/embedded/semiconductor companies
_DOMAIN_KEYWORDS: list[str] = [
    "automotive", "embedded", "semiconductor", "mobility", "vehicle",
    "ecu", "powertrain", "chassis", "adas", "connected car",
    "electric vehicle", "ev", "oem", "tier 1", "tier1",
    "bosch", "continental", "aptiv", "nxp", "valeo", "lear",
    "bmw", "volkswagen", "stellantis", "volvo", "renault",
    "engineering services", "industrial", "mechatronics",
]

# Diagnostic/protocol keywords — candidate's core differentiator
_PROTOCOL_KEYWORDS: list[str] = [
    "uds", "doip", "can", "canbus", "lin", "some/ip", "someip",
    "ethernet", "tcp/ip", "dlt", "autosar adaptive", "autosar classic",
    "iso 14229", "iso 13400", "iso 26262",
]

# Tools candidate actually has
_CANDIDATE_TOOLS: list[str] = [
    "wireshark", "dlt", "test guide", "jira", "python", "power bi",
    "git", "autosar", "uds", "doip", "sql", "excel",
]

# Secondary data roles — title keywords
_SECONDARY_TITLE_KEYWORDS: list[str] = [
    "data analyst", "bi developer", "power bi developer",
    "data quality analyst", "reporting analyst", "business analyst",
    "bi analyst", "data reporting",
]

# Secondary role required skills (must match >= 3)
_SECONDARY_REQUIRED_SKILLS: list[str] = [
    "power bi", "sql", "python", "data quality", "reporting",
    "dashboarding", "etl", "kpi", "tableau", "excel",
]

# Secondary domain — where data roles are acceptable
_SECONDARY_DOMAINS: list[str] = [
    "automotive", "manufacturing", "engineering", "industrial",
    "quality", "operations", "business intelligence", "production",
    "supply chain", "logistics",
]

# Roles and keywords to NEVER apply for
_EXCLUDED_TITLE_KEYWORDS: list[str] = [
    "data scientist", "machine learning", "ml engineer", "ai engineer",
    "senior data engineer", "cloud data engineer", "data platform",
    "mlops", "deep learning", "nlp engineer", "research scientist",
    "big data", "data architect",
]

_EXCLUDED_BODY_KEYWORDS: list[str] = [
    "machine learning", "deep learning", "neural network",
    "llm", "generative ai", "mlops", "spark", "hadoop",
    "databricks", "snowflake", "kubernetes", "aws architect",
    "azure architect", "cloud architecture", "terraform",
]

# Known gap mitigations — used in AI-generated messages
KNOWN_GAP_MITIGATIONS: dict[str, str] = {
    "canoe": (
        "Candidate has equivalent automotive diagnostic debugging experience using "
        "DLT Viewer and Wireshark for AUTOSAR Adaptive ECU analysis, with hands-on "
        "UDS (ISO 14229) and DoIP (ISO 13400) protocol validation. "
        "DLT/Wireshark toolchain covers the same diagnostic use cases as CANoe in "
        "an Adaptive AUTOSAR environment."
    ),
    "canalyzer": (
        "Equivalent experience via Wireshark packet capture and DLT log analysis "
        "for automotive diagnostic protocol validation."
    ),
    "vector tools": (
        "Candidate's Wireshark + DLT toolchain provides equivalent diagnostic "
        "analysis capability for AUTOSAR Adaptive platforms."
    ),
    "iso 26262": (
        "Candidate worked within an ISO 26262-aware development environment at "
        "KPIT Engineering (BMW Group supply chain). Formal certification not held "
        "but functional safety principles applied throughout ASPICE-aligned V-model work."
    ),
    "autosar classic": (
        "Candidate has hands-on AUTOSAR Adaptive experience. AUTOSAR Classic shares "
        "architectural concepts and the candidate's ECU validation methodology transfers directly."
    ),
}

# Score weights per category (must sum to 100)
_WEIGHTS = {
    "title_match":    35,  # Title match is the strongest signal
    "keyword_match":  25,  # JD body keywords
    "domain_match":   20,  # Company/sector domain
    "protocol_match": 15,  # UDS / DoIP / DLT / CAN — candidate's differentiator
    "tools_match":    5,   # Specific tool overlap
}

# Decision thresholds
_PRIMARY_THRESHOLDS = {"APPLY": 65, "REVIEW": 40}
_SECONDARY_THRESHOLDS = {"APPLY": 75, "REVIEW": 60}


def _normalise(text: str) -> str:
    """Lowercase and strip punctuation for keyword matching."""
    return re.sub(r"[^a-z0-9 /.]", " ", text.lower())


def _count_matches(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    """Return count and list of keywords found in text."""
    found = [kw for kw in keywords if kw.lower() in text]
    return len(found), found


class JobMatcher:
    """
    Scores and classifies job listings against the candidate profile.

    Usage:
        matcher = JobMatcher()
        report = matcher.evaluate(job_listing)
        if report.decision == MatchDecision.APPLY:
            # proceed to generate messages
    """

    def evaluate(self, job: JobListing) -> MatchReport:
        """
        Full evaluation pipeline for a single job listing.
        Returns a MatchReport with decision, score, gaps, and mitigations.
        """
        title = _normalise(job.title)
        description = _normalise(job.description or "")
        company = _normalise(job.company)
        full_text = f"{title} {company} {description}"

        # ── Step 1: Classify tier ─────────────────────────────────────────
        tier = self._classify_tier(title, full_text)

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
        score_breakdown: dict[str, int] = {}

        # Title match (35 pts)
        title_score, title_matched = self._score_title(title, tier)
        score_breakdown["title_match"] = title_score

        # Keyword match (25 pts)
        kw_score, kw_matched = self._score_keywords(full_text, tier)
        score_breakdown["keyword_match"] = kw_score

        # Domain match (20 pts)
        domain_score, domain_found = self._score_domain(full_text)
        score_breakdown["domain_match"] = domain_score

        # Protocol match (15 pts) — candidate's strongest differentiator
        proto_score, proto_matched = self._score_protocols(full_text)
        score_breakdown["protocol_match"] = proto_score

        # Tools match (5 pts)
        tools_score, tools_matched = self._score_tools(full_text)
        score_breakdown["tools_match"] = tools_score

        total_score = sum(score_breakdown.values())
        total_score = min(100, total_score)

        # ── Step 3: Gap analysis ─────────────────────────────────────────
        gaps, mitigations, canoe_mitigated = self._detect_gaps(full_text)

        # ── Step 4: Secondary role additional checks ──────────────────────
        secondary_skills: list[str] = []
        secondary_domain = False
        if tier == RoleTier.SECONDARY:
            _, secondary_skills = _count_matches(full_text, _SECONDARY_REQUIRED_SKILLS)
            _, sec_domains = _count_matches(full_text, _SECONDARY_DOMAINS)
            secondary_domain = len(sec_domains) > 0

            # Hard gate: secondary roles must match >= 3 skills AND domain
            if len(secondary_skills) < 3 or not secondary_domain:
                return MatchReport(
                    job_id=job.id,
                    job_title=job.title,
                    company=job.company,
                    tier=RoleTier.SECONDARY,
                    decision=MatchDecision.SKIP,
                    score=total_score,
                    score_breakdown=score_breakdown,
                    skill_gaps=gaps,
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
        thresholds = (
            _PRIMARY_THRESHOLDS if tier == RoleTier.PRIMARY
            else _SECONDARY_THRESHOLDS
        )
        decision = self._decide(total_score, thresholds)

        reason = self._build_reason(
            decision, total_score, tier,
            title_matched, kw_matched, domain_found,
            proto_matched, gaps, canoe_mitigated,
        )

        report = MatchReport(
            job_id=job.id,
            job_title=job.title,
            company=job.company,
            tier=tier,
            decision=decision,
            score=total_score,
            score_breakdown=score_breakdown,
            matched_keywords=kw_matched,
            matched_protocols=proto_matched,
            matched_tools=tools_matched,
            domain_match=domain_found is not None,
            domain_found=domain_found,
            skill_gaps=gaps,
            gap_mitigations=mitigations,
            canoe_gap_mitigated=canoe_mitigated,
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
            score=total_score,
            decision=decision.value,
        )

        return report

    # ── Private scoring methods ───────────────────────────────────────────────

    def _classify_tier(self, title: str, full_text: str) -> RoleTier:
        """Classify job as PRIMARY, SECONDARY, or EXCLUDED."""

        # Check exclusions first — hard stop
        excl_title, _ = _count_matches(title, _EXCLUDED_TITLE_KEYWORDS)
        excl_body, _ = _count_matches(full_text, _EXCLUDED_BODY_KEYWORDS)
        if excl_title > 0 or excl_body >= 2:
            return RoleTier.EXCLUDED

        # Check primary
        prim_title, _ = _count_matches(title, _PRIMARY_TITLE_KEYWORDS)
        if prim_title > 0:
            return RoleTier.PRIMARY

        # Check secondary
        sec_title, _ = _count_matches(title, _SECONDARY_TITLE_KEYWORDS)
        if sec_title > 0:
            return RoleTier.SECONDARY

        # Default: if body has strong primary signals, treat as primary
        prim_body, prim_kws = _count_matches(full_text, _PRIMARY_BODY_KEYWORDS)
        if prim_body >= 3:
            return RoleTier.PRIMARY

        return RoleTier.EXCLUDED  # Unknown role type — skip

    def _score_title(
        self, title: str, tier: RoleTier
    ) -> tuple[int, list[str]]:
        """Score 0-35 based on title keyword match."""
        keywords = (
            _PRIMARY_TITLE_KEYWORDS if tier == RoleTier.PRIMARY
            else _SECONDARY_TITLE_KEYWORDS
        )
        count, matched = _count_matches(title, keywords)
        if count == 0:
            return 0, []
        # 1 match = 20pts, 2+ matches = 35pts
        score = min(35, 20 + (count - 1) * 15)
        return score, matched

    def _score_keywords(
        self, full_text: str, tier: RoleTier
    ) -> tuple[int, list[str]]:
        """Score 0-25 based on body keyword density."""
        keywords = (
            _PRIMARY_BODY_KEYWORDS if tier == RoleTier.PRIMARY
            else _SECONDARY_REQUIRED_SKILLS
        )
        count, matched = _count_matches(full_text, keywords)
        # 1 kw = 5pts, scales to 25pts at 5+ keywords
        score = min(25, count * 5)
        return score, matched

    def _score_domain(self, full_text: str) -> tuple[int, str | None]:
        """Score 0-20 based on company/sector domain match."""
        count, matched = _count_matches(full_text, _DOMAIN_KEYWORDS)
        if count == 0:
            return 0, None
        return 20, matched[0]

    def _score_protocols(self, full_text: str) -> tuple[int, list[str]]:
        """Score 0-15 based on diagnostic protocol keyword match."""
        count, matched = _count_matches(full_text, _PROTOCOL_KEYWORDS)
        score = min(15, count * 5)
        return score, matched

    def _score_tools(self, full_text: str) -> tuple[int, list[str]]:
        """Score 0-5 based on tool overlap."""
        count, matched = _count_matches(full_text, _CANDIDATE_TOOLS)
        return min(5, count * 1), matched

    def _detect_gaps(
        self, full_text: str
    ) -> tuple[list[str], dict[str, str], bool]:
        """
        Detect skill gaps from the JD and map known mitigations.
        Returns (gaps, mitigations dict, canoe_mitigated flag).
        """
        gaps: list[str] = []
        mitigations: dict[str, str] = {}
        canoe_mitigated = False

        gap_checks = {
            "canoe": ("canoe", KNOWN_GAP_MITIGATIONS["canoe"]),
            "canalyzer": ("canalyzer", KNOWN_GAP_MITIGATIONS["canalyzer"]),
            "vector tools": ("vector", KNOWN_GAP_MITIGATIONS["vector tools"]),
            "iso 26262 certification": ("iso 26262", KNOWN_GAP_MITIGATIONS["iso 26262"]),
            "autosar classic": ("autosar classic", KNOWN_GAP_MITIGATIONS["autosar classic"]),
            "azure": ("azure", "Azure is not a core skill. Do not mention in messages."),
            "ssis": ("ssis", "SSIS was used during internship only. Do not oversell."),
        }

        for gap_name, (keyword, mitigation) in gap_checks.items():
            if keyword in full_text:
                gaps.append(gap_name)
                mitigations[gap_name] = mitigation
                if gap_name in ("canoe", "canalyzer", "vector tools"):
                    canoe_mitigated = True

        return gaps, mitigations, canoe_mitigated

    def _decide(self, score: int, thresholds: dict) -> MatchDecision:
        """Map score to APPLY / REVIEW / SKIP."""
        if score >= thresholds["APPLY"]:
            return MatchDecision.APPLY
        if score >= thresholds["REVIEW"]:
            return MatchDecision.REVIEW
        return MatchDecision.SKIP

    def _build_reason(
        self,
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
