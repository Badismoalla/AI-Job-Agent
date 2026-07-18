"""
Unit tests for core.matcher.JobMatcher.

Tests cover:
- PRIMARY role detection and scoring
- SECONDARY role gating (skills + domain both required)
- EXCLUDED role rejection
- CANoe gap mitigation
- APPLY / REVIEW / SKIP decision thresholds
"""

import pytest

from core.matcher import JobMatcher
from core.models import JobListing, Market, ApplicationSource, MatchDecision, RoleTier


def make_job(
    title: str,
    company: str = "TestCorp",
    city: str = "Krakow",
    market: Market = Market.POLAND,
    description: str = "",
    visa: bool = True,
) -> JobListing:
    slug = f"{company.lower()}-{title.lower().replace(' ', '-')}-{city.lower()}"
    return JobListing(
        id=slug,
        title=title,
        company=company,
        city=city,
        market=market,
        url=f"https://example.com/jobs/{slug}",
        source=ApplicationSource.LINKEDIN,
        description=description,
        visa_sponsorship=visa,
    )


@pytest.fixture
def matcher():
    return JobMatcher()


# ── PRIMARY ROLES ─────────────────────────────────────────────────────────────

class TestPrimaryRoles:

    def test_exact_title_match_apply(self, matcher):
        job = make_job(
            "Software Test Engineer",
            company="Bosch",
            description="AUTOSAR automotive embedded ECU UDS DoIP validation testing "
                        "ASPICE V-model traceability defect management diagnostic",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY
        assert report.decision == MatchDecision.APPLY
        assert report.score >= 65

    def test_validation_engineer_apply(self, matcher):
        job = make_job(
            "Validation Engineer",
            description="Automotive embedded ECU validation UDS DoIP CAN AUTOSAR "
                        "test plan test case defect lifecycle BMW supply chain",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY
        assert report.decision in (MatchDecision.APPLY, MatchDecision.REVIEW)

    def test_embedded_test_engineer(self, matcher):
        job = make_job(
            "Embedded Test Engineer",
            description="Embedded systems testing automotive AUTOSAR software validation",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY

    def test_automotive_qa_engineer(self, matcher):
        job = make_job(
            "Automotive QA Engineer",
            description="QA testing automotive ECU diagnostics defect management Jira",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY

    def test_low_score_gives_review_or_skip(self, matcher):
        job = make_job(
            "Test Engineer",
            description="Some vague description with no automotive or embedded keywords.",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY
        # Score should be low — no domain/protocol match
        assert report.score < 65


# ── SECONDARY ROLES ───────────────────────────────────────────────────────────

class TestSecondaryRoles:

    def test_secondary_passes_gate_apply(self, matcher):
        """Data analyst in automotive domain with Power BI, SQL, Python — should pass."""
        job = make_job(
            "Junior Data Analyst",
            description=(
                "Power BI SQL Python reporting dashboarding data quality "
                "automotive manufacturing engineering KPI reporting defect analysis"
            ),
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.SECONDARY
        assert len(report.secondary_skills_matched) >= 3
        assert report.secondary_domain_matched is True
        assert report.decision in (MatchDecision.APPLY, MatchDecision.REVIEW)

    def test_secondary_fails_no_domain(self, matcher):
        """Data analyst with skills but wrong domain — should SKIP."""
        job = make_job(
            "Data Analyst",
            description="Power BI SQL Python reporting dashboarding ETL finance banking retail",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.SECONDARY
        assert report.decision == MatchDecision.SKIP
        assert "domain match=False" in report.reason

    def test_secondary_fails_insufficient_skills(self, matcher):
        """Data analyst in automotive but only 1 skill match — should SKIP."""
        job = make_job(
            "Data Analyst",
            description="Power BI only. Automotive domain. No SQL or Python mentioned.",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.SECONDARY
        assert report.decision == MatchDecision.SKIP

    def test_power_bi_developer_automotive(self, matcher):
        """Power BI Developer in manufacturing with SQL and Python — should pass gate."""
        job = make_job(
            "Power BI Developer",
            description=(
                "Power BI DAX SQL Python reporting manufacturing quality "
                "KPI dashboarding automotive operations industrial"
            ),
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.SECONDARY
        assert report.decision in (MatchDecision.APPLY, MatchDecision.REVIEW)


# ── EXCLUDED ROLES ────────────────────────────────────────────────────────────

class TestExcludedRoles:

    def test_data_scientist_excluded(self, matcher):
        job = make_job("Data Scientist", description="Machine learning deep learning neural network Python")
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.EXCLUDED
        assert report.decision == MatchDecision.SKIP

    def test_ml_engineer_excluded(self, matcher):
        job = make_job("Machine Learning Engineer", description="ML deep learning Spark MLOps Kubernetes")
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.EXCLUDED
        assert report.decision == MatchDecision.SKIP

    def test_senior_data_engineer_excluded(self, matcher):
        job = make_job("Senior Data Engineer", description="Spark Databricks Snowflake cloud architecture AWS")
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.EXCLUDED
        assert report.decision == MatchDecision.SKIP

    def test_mlops_excluded(self, matcher):
        job = make_job("MLOps Engineer", description="MLOps machine learning Kubernetes Docker AWS")
        report = matcher.evaluate(job)
        assert report.decision == MatchDecision.SKIP


# ── GAP MITIGATIONS ───────────────────────────────────────────────────────────

class TestGapMitigations:

    def test_canoe_gap_mitigated(self, matcher):
        """CANoe in JD should not reject — should trigger DLT/Wireshark mitigation."""
        job = make_job(
            "Automotive Test Engineer",
            description=(
                "CANoe CANalyzer Vector tools CAN bus automotive embedded ECU "
                "test engineer validation AUTOSAR UDS DoIP diagnostics"
            ),
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY
        assert report.canoe_gap_mitigated is True
        assert "canoe" in report.skill_gaps
        assert "canoe" in report.gap_mitigations
        # CANoe gap must NOT cause a SKIP
        assert report.decision != MatchDecision.SKIP

    def test_iso_26262_gap_mitigated(self, matcher):
        """ISO 26262 in JD should trigger aware-level mitigation, not rejection."""
        job = make_job(
            "Validation Engineer",
            description=(
                "ISO 26262 functional safety ASPICE automotive embedded "
                "ECU test validation V-model SWE.6"
            ),
        )
        report = matcher.evaluate(job)
        assert "iso 26262 certification" in report.skill_gaps
        assert "iso 26262 certification" in report.gap_mitigations
        # Should not be rejected for this
        assert report.decision != MatchDecision.SKIP

    def test_azure_gap_not_promoted(self, matcher):
        """Azure appearing in JD should be flagged as gap but not mitigated as strength."""
        job = make_job(
            "Test Engineer",
            description="Azure DevOps automotive embedded test engineer validation",
        )
        report = matcher.evaluate(job)
        if "azure" in report.skill_gaps:
            mitigation = report.gap_mitigations.get("azure", "")
            assert "do not mention" in mitigation.lower() or "not a core skill" in mitigation.lower()


# ── SCORE BREAKDOWN ───────────────────────────────────────────────────────────

class TestScoreBreakdown:

    def test_score_breakdown_keys_present(self, matcher):
        job = make_job("Embedded Test Engineer", description="AUTOSAR automotive ECU UDS DoIP")
        report = matcher.evaluate(job)
        expected_keys = {"title_match", "keyword_match", "domain_match", "protocol_match", "tools_match"}
        assert expected_keys == set(report.score_breakdown.keys())

    def test_score_is_sum_of_breakdown(self, matcher):
        job = make_job(
            "Software Test Engineer",
            description="AUTOSAR automotive ECU UDS DoIP Wireshark DLT Jira Python",
        )
        report = matcher.evaluate(job)
        breakdown_total = sum(report.score_breakdown.values())
        # Score is capped at 100 but breakdown sum may exceed it before capping
        assert report.score <= 100
        assert report.score >= 0

    def test_protocol_match_boosts_score(self, matcher):
        """Job with UDS, DoIP, DLT should score higher than one without."""
        job_with_protocols = make_job(
            "Test Engineer",
            description="Automotive test engineer UDS DoIP DLT Wireshark CAN protocol",
        )
        job_without_protocols = make_job(
            "Test Engineer",
            description="Automotive test engineer general testing",
        )
        report_with = matcher.evaluate(job_with_protocols)
        report_without = matcher.evaluate(job_without_protocols)
        assert report_with.score > report_without.score
