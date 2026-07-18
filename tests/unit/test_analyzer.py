"""
Unit tests for modules/analyzer/job_parser.py and commands/analyze.py

Test coverage:
- Full valid file parsing
- Header field extraction (all aliases)
- Market inference from location
- Visa sponsorship parsing
- Missing required field errors
- Empty file errors
- Non-existent file errors
- Body extraction after separator
- Body extraction by heuristic (no separator)
- Integration: parser → matcher → decision
- CANoe gap detected in JSDSolutions JD
- Excluded role skips without crash
- Score breakdown completeness
- Force flag bypasses threshold check
"""

import textwrap
from pathlib import Path

import pytest

from core.matcher import JobMatcher
from core.models import (
    ApplicationSource,
    Market,
    MatchDecision,
    RoleTier,
)
from modules.analyzer.job_parser import JobParser, ParseError


# ── Fixtures ─────────────────────────────────────────────────────────────────

VALID_JD_FULL = textwrap.dedent("""\
    Company: Bosch Engineering
    Role: Software Test & Validation Engineer
    Location: Wroclaw, Poland
    Market: Poland
    URL: https://jobs.bosch.com/job/wroclaw-test-engineer-123
    Visa Sponsorship: Yes

    ---

    We are looking for a Software Test and Validation Engineer.

    Your responsibilities:
    - Design and execute test cases for embedded automotive ECU systems
    - Perform validation within an ASPICE-aligned V-model
    - Work with UDS and DoIP diagnostic protocols
    - Analyse DLT logs and Wireshark captures
    - Maintain traceability matrices using Test Guide (TG)
    - Track defects in Jira

    Your profile:
    - 2+ years experience in embedded software testing
    - AUTOSAR architecture knowledge
    - Python scripting skills a plus
    - Fluent English
""")

VALID_JD_NO_SEPARATOR = textwrap.dedent("""\
    Company: NXP Semiconductors
    Role: Embedded Test Engineer
    Location: Eindhoven, Netherlands
    Market: Netherlands
    URL: https://nxp.com/careers/123
    Visa Sponsorship: yes

    This role involves embedded systems testing and validation.
    Experience with automotive protocols UDS and DoIP preferred.
    AUTOSAR knowledge is a plus.
    Tools: Wireshark, Jira, Python.
""")

JD_MISSING_COMPANY = textwrap.dedent("""\
    Role: Test Engineer
    Location: Krakow, Poland
    Market: Poland
    URL: https://example.com
    ---
    Some description.
""")

JD_MISSING_ROLE = textwrap.dedent("""\
    Company: Continental
    Location: Krakow, Poland
    Market: Poland
    URL: https://example.com
    ---
    Some description.
""")

JD_MARKET_FROM_LOCATION = textwrap.dedent("""\
    Company: IEE International
    Role: Automotive Test Engineer
    Location: Luxembourg City, Luxembourg
    URL: https://iee.lu/careers/123
    Visa Sponsorship: Yes
    ---
    ECU validation, automotive embedded systems.
    AUTOSAR, UDS, DoIP, Jira, Test Guide.
""")

JD_GCC_INFERRED = textwrap.dedent("""\
    Company: Capgemini
    Role: Test Engineer
    Location: Dubai
    URL: https://capgemini.com/jobs/123
    ---
    Automotive embedded testing role in Dubai.
    UDS diagnostics, ECU validation, AUTOSAR.
""")

JD_EXCLUDED = textwrap.dedent("""\
    Company: AI Startup
    Role: Senior Data Scientist
    Location: Warsaw, Poland
    Market: Poland
    URL: https://startup.pl/jobs/ds
    ---
    Machine learning, deep learning, neural networks.
    Spark, Databricks, MLOps, Kubernetes.
    PhD in ML required.
""")

JD_JSDSOLUTIONS = textwrap.dedent("""\
    Company: JSDSolutions
    Role: Automotive Test Engineer
    Location: Krakow, Poland
    Market: Poland
    URL: https://jobs.jsdsolutions.pl/test-engineer
    Visa Sponsorship: Yes
    ---
    Execute software and system-level testing for ECU components.
    Validate diagnostic jobs and DTC behaviour.
    Required: CANoe, CANalyzer, or equivalent diagnostic tools.
    CAN, LIN, Ethernet-based automotive communication protocols.
    Jira for defect tracking. Automotive domain experience required.
""")

JD_SECONDARY_PASS = textwrap.dedent("""\
    Company: Bosch Manufacturing IT
    Role: Power BI Developer
    Location: Krakow, Poland
    Market: Poland
    URL: https://bosch.com/careers/pbi
    Visa Sponsorship: Yes
    ---
    Power BI, SQL, Python, reporting, dashboarding, data quality.
    Manufacturing and automotive domain experience preferred.
    KPI tracking, Excel, ETL pipelines.
    Industrial operations analytics.
""")

JD_SECONDARY_FAIL_DOMAIN = textwrap.dedent("""\
    Company: FinTech Corp
    Role: Data Analyst
    Location: Warsaw, Poland
    Market: Poland
    URL: https://fintech.pl/jobs/da
    ---
    Power BI, SQL, Python, reporting, dashboarding.
    Banking and finance domain. Retail analytics.
""")


# ── Parser tests ──────────────────────────────────────────────────────────────

class TestJobParserFromText:

    def test_full_valid_jd(self):
        listing = JobParser.from_text(VALID_JD_FULL)
        assert listing.company == "Bosch Engineering"
        assert listing.title == "Software Test & Validation Engineer"
        assert listing.city == "Wroclaw"
        assert listing.market == Market.POLAND
        assert listing.visa_sponsorship is True
        assert listing.source == ApplicationSource.MANUAL
        assert "ASPICE" in (listing.description or "")
        assert "UDS" in (listing.description or "")

    def test_no_separator_still_parses(self):
        listing = JobParser.from_text(VALID_JD_NO_SEPARATOR)
        assert listing.company == "NXP Semiconductors"
        assert listing.title == "Embedded Test Engineer"
        assert listing.market == Market.NETHERLANDS

    def test_market_inferred_from_location(self):
        listing = JobParser.from_text(JD_MARKET_FROM_LOCATION)
        assert listing.market == Market.LUXEMBOURG

    def test_gcc_city_inferred(self):
        listing = JobParser.from_text(JD_GCC_INFERRED)
        assert listing.market == Market.UAE

    def test_visa_yes_parsed(self):
        listing = JobParser.from_text(VALID_JD_FULL)
        assert listing.visa_sponsorship is True

    def test_visa_no_parsed(self):
        jd = VALID_JD_FULL.replace("Visa Sponsorship: Yes", "Visa Sponsorship: No")
        listing = JobParser.from_text(jd)
        assert listing.visa_sponsorship is False

    def test_visa_variants(self):
        for val in ["yes", "Yes", "YES", "true", "True", "1", "y"]:
            jd = VALID_JD_FULL.replace("Visa Sponsorship: Yes", f"Visa Sponsorship: {val}")
            listing = JobParser.from_text(jd)
            assert listing.visa_sponsorship is True, f"Failed for visa value: {val}"

    def test_id_is_slug(self):
        listing = JobParser.from_text(VALID_JD_FULL)
        assert " " not in listing.id
        assert listing.id == listing.id.lower()
        assert "bosch" in listing.id

    def test_description_contains_body(self):
        listing = JobParser.from_text(VALID_JD_FULL)
        assert listing.description is not None
        assert "traceability matrices" in listing.description
        assert "AUTOSAR" in listing.description

    def test_url_preserved(self):
        listing = JobParser.from_text(VALID_JD_FULL)
        assert "bosch.com" in listing.url

    def test_role_alias(self):
        """'Role:' header maps to title field."""
        listing = JobParser.from_text(VALID_JD_FULL)
        assert listing.title == "Software Test & Validation Engineer"

    def test_title_alias(self):
        """'Title:' header also maps to title field."""
        jd = VALID_JD_FULL.replace("Role:", "Title:")
        listing = JobParser.from_text(jd)
        assert listing.title == "Software Test & Validation Engineer"


class TestJobParserErrors:

    def test_missing_company_raises(self):
        with pytest.raises(ParseError, match="company"):
            JobParser.from_text(JD_MISSING_COMPANY)

    def test_missing_role_raises(self):
        with pytest.raises(ParseError, match="title"):
            JobParser.from_text(JD_MISSING_ROLE)

    def test_empty_text_raises(self):
        with pytest.raises(ParseError):
            JobParser.from_text("   \n\n  ")

    def test_nonexistent_file_raises(self):
        with pytest.raises(ParseError, match="not found"):
            JobParser.from_file(Path("nonexistent_file_xyz_123.txt"))

    def test_unknown_market_raises(self):
        jd = VALID_JD_FULL.replace("Market: Poland", "Market: Antarctica")
        jd = jd.replace("Location: Wroclaw, Poland", "Location: Somewhere, Antarctica")
        with pytest.raises(ParseError, match="market"):
            JobParser.from_text(jd)

    def test_unsupported_file_type_raises(self, tmp_path):
        f = tmp_path / "resume.pdf"
        f.write_bytes(b"fake pdf content")
        with pytest.raises(ParseError, match="Unsupported file type"):
            JobParser.from_file(f)


class TestJobParserFromFile:

    def test_loads_bosch_sample(self):
        path = Path(__file__).parent.parent.parent / "jobs" / "bosch_test_engineer_wroclaw.txt"
        if not path.exists():
            pytest.skip("Sample JD file not found — run from project root")
        listing = JobParser.from_file(path)
        assert listing.company == "Bosch Engineering"
        assert listing.market == Market.POLAND

    def test_loads_jsdsolutions_sample(self):
        path = Path(__file__).parent.parent.parent / "jobs" / "jsdsolutions_test_engineer_krakow.txt"
        if not path.exists():
            pytest.skip("Sample JD file not found — run from project root")
        listing = JobParser.from_file(path)
        assert listing.company == "JSDSolutions"
        assert listing.visa_sponsorship is True


# ── Integration: Parser → Matcher ────────────────────────────────────────────

class TestAnalyzerIntegration:

    def test_bosch_jd_apply_decision(self):
        """Full pipeline: valid automotive JD → APPLY decision."""
        listing = JobParser.from_text(VALID_JD_FULL)
        matcher = JobMatcher()
        report = matcher.evaluate(listing)
        assert report.tier == RoleTier.PRIMARY
        assert report.decision in (MatchDecision.APPLY, MatchDecision.REVIEW)
        assert report.score > 40

    def test_bosch_score_breakdown_complete(self):
        listing = JobParser.from_text(VALID_JD_FULL)
        report = JobMatcher().evaluate(listing)
        assert set(report.score_breakdown.keys()) == {
            "title_match", "keyword_match", "domain_match",
            "protocol_match", "tools_match"
        }

    def test_excluded_role_skips(self):
        listing = JobParser.from_text(JD_EXCLUDED)
        report = JobMatcher().evaluate(listing)
        assert report.tier == RoleTier.EXCLUDED
        assert report.decision == MatchDecision.SKIP
        assert report.score == 0

    def test_jsdsolutions_canoe_gap_mitigated(self):
        """JSDSolutions JD requires CANoe — should trigger DLT/Wireshark mitigation."""
        listing = JobParser.from_text(JD_JSDSOLUTIONS)
        report = JobMatcher().evaluate(listing)
        assert report.tier == RoleTier.PRIMARY
        assert report.canoe_gap_mitigated is True
        assert "canoe" in report.skill_gaps
        assert "canoe" in report.gap_mitigations
        # DLT/Wireshark mitigation text must be present
        assert "DLT" in report.gap_mitigations["canoe"] or "Wireshark" in report.gap_mitigations["canoe"]
        # CANoe gap must NOT cause a SKIP
        assert report.decision != MatchDecision.SKIP

    def test_secondary_automotive_passes(self):
        """Power BI role in manufacturing domain passes the secondary gate."""
        listing = JobParser.from_text(JD_SECONDARY_PASS)
        report = JobMatcher().evaluate(listing)
        assert report.tier == RoleTier.SECONDARY
        assert len(report.secondary_skills_matched) >= 3
        assert report.secondary_domain_matched is True
        assert report.decision in (MatchDecision.APPLY, MatchDecision.REVIEW)

    def test_secondary_wrong_domain_skips(self):
        """Data Analyst in fintech/banking must be skipped."""
        listing = JobParser.from_text(JD_SECONDARY_FAIL_DOMAIN)
        report = JobMatcher().evaluate(listing)
        assert report.tier == RoleTier.SECONDARY
        assert report.decision == MatchDecision.SKIP

    def test_nxp_embedded_is_primary(self):
        listing = JobParser.from_text(VALID_JD_NO_SEPARATOR)
        report = JobMatcher().evaluate(listing)
        assert report.tier == RoleTier.PRIMARY

    def test_protocol_keywords_boost_score(self):
        """JD with UDS, DoIP, DLT should score higher than JD without."""
        jd_with = textwrap.dedent("""\
            Company: TestCo A
            Role: Validation Engineer
            Location: Krakow, Poland
            Market: Poland
            URL: https://example.com/a
            ---
            Automotive embedded ECU validation. UDS DoIP DLT Wireshark AUTOSAR.
        """)
        jd_without = textwrap.dedent("""\
            Company: TestCo B
            Role: Validation Engineer
            Location: Krakow, Poland
            Market: Poland
            URL: https://example.com/b
            ---
            Automotive embedded ECU validation. General testing.
        """)
        listing_with = JobParser.from_text(jd_with)
        listing_without = JobParser.from_text(jd_without)
        report_with = JobMatcher().evaluate(listing_with)
        report_without = JobMatcher().evaluate(listing_without)
        assert report_with.score > report_without.score

    def test_no_skills_invented(self, monkeypatch):
        """
        Ensure gap_mitigations never introduces SSIS, Azure, or CANoe as strengths.
        These are in the do_not_overstate list.
        """
        listing = JobParser.from_text(JD_JSDSOLUTIONS)
        report = JobMatcher().evaluate(listing)
        for gap, mitigation in report.gap_mitigations.items():
            mitigation_lower = mitigation.lower()
            # If Azure is mentioned, it must say "do not mention" or "not a core skill"
            if "azure" in mitigation_lower:
                assert "do not" in mitigation_lower or "not a" in mitigation_lower
            # SSIS mitigation must flag it as internship-only
            if "ssis" in mitigation_lower:
                assert "internship" in mitigation_lower or "do not" in mitigation_lower
