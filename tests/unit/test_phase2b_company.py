"""
Phase 2B: Company Intelligence Assessment Tests

Tests for company-level intelligence (industry, type, sponsorship).
Comprehensive coverage of boundary cases and integration scenarios.
Evidence-based sponsorship assessment critical.
"""

import pytest
from core.models import JobListing, MatchReport, Market, ApplicationSource
from core.matching.compatibility import (
    IndustryRelevance,
    CompanyType,
    SponsorshipLikelihood,
)
from core.matching.assessments import (
    assess_company_industry_relevance,
    assess_company_type,
    assess_sponsorship_likelihood,
    extract_company_industry,
)
from core.profile import profile as candidate_profile
from core.matching.rules import load_matching_rules


def create_test_job(
    job_id: str,
    company: str,
    title: str,
    description: str,
    city: str = "Warsaw",
    market: Market = Market.POLAND,
    source: ApplicationSource = ApplicationSource.NOFLUFFJOBS,
    company_industry: str | None = None,
) -> JobListing:
    """Helper to create JobListing with required fields for testing."""
    return JobListing(
        id=job_id,
        company=company,
        title=title,
        description=description,
        city=city,
        market=market,
        url=f"https://example.com/{job_id}",
        source=source,
        company_industry=company_industry,
    )


class TestExtractCompanyIndustry:
    """Tests for extract_company_industry() helper function."""

    def test_extract_automotive_industry(self):
        """Extracts automotive from description."""
        desc = "Automotive testing engineer, working on ADAS systems"
        result = extract_company_industry(desc)
        assert result == "automotive"

    def test_extract_fintech_industry(self):
        """Extracts fintech from description."""
        desc = "Payment platform fintech company looking for engineers"
        result = extract_company_industry(desc)
        assert result == "fintech"

    def test_extract_none_when_no_industry(self):
        """Returns None when no industry keywords found."""
        desc = "Generic software engineer role at a company"
        result = extract_company_industry(desc)
        assert result is None

    def test_extract_returns_first_match(self):
        """Returns first industry found."""
        desc = "Automotive company with fintech integration"
        result = extract_company_industry(desc)
        assert result == "automotive"  # automotive comes first

    def test_extract_handles_none_description(self):
        """Handles None description gracefully."""
        result = extract_company_industry(None)
        assert result is None

    def test_extract_case_insensitive(self):
        """Extraction is case-insensitive."""
        desc = "AUTOMOTIVE engineer at a major car manufacturer"
        result = extract_company_industry(desc)
        assert result == "automotive"


class TestCompanyIndustryRelevance:
    """Tests for assess_company_industry_relevance() assessment."""

    def test_automotive_industry_relevant(self):
        """Automotive industry is relevant to candidate."""
        job = create_test_job(
            "test-1",
            "Bosch",
            "Test Engineer",
            "Automotive testing engineer",
            company_industry="automotive",
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.RELEVANT

    def test_embedded_systems_relevant(self):
        """Embedded systems industry is relevant."""
        job = create_test_job(
            "test-2",
            "Company",
            "Embedded Test Engineer",
            "Embedded systems testing",
            company_industry="embedded",
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.RELEVANT

    def test_generic_tech_relevant(self):
        """Generic tech industry is relevant (no explicit exclusion)."""
        job = create_test_job(
            "test-3",
            "TechCorp",
            "Software Test Engineer",
            "Software testing for a tech company",
            company_industry="software",
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.RELEVANT

    def test_fashion_industry_irrelevant(self):
        """Fashion industry is explicitly irrelevant."""
        job = create_test_job(
            "test-4",
            "FashionBrand",
            "QA Engineer",
            "Quality testing for fashion e-commerce",
            company_industry="fashion",
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.IRRELEVANT

    def test_hospitality_industry_irrelevant(self):
        """Hospitality industry is explicitly irrelevant."""
        job = create_test_job(
            "test-5",
            "HotelChain",
            "System Tester",
            "Testing hotel management system",
            company_industry="hospitality",
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.IRRELEVANT

    def test_unknown_industry_returns_unknown(self):
        """Unknown industry returns UNKNOWN, not IRRELEVANT."""
        job = create_test_job(
            "test-6",
            "SomeCompany",
            "Test Engineer",
            "Testing role",
            company_industry=None,
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.UNKNOWN

    def test_rare_industry_unknown_not_irrelevant(self):
        """Rare industry returns UNKNOWN, not IRRELEVANT."""
        job = create_test_job(
            "test-7",
            "SpaceCompany",
            "Systems Engineer",
            "Aerospace systems validation",
            company_industry="aerospace",
        )
        rules = load_matching_rules()
        result = assess_company_industry_relevance(job, candidate_profile, rules)
        assert result == IndustryRelevance.UNKNOWN


class TestCompanyType:
    """Tests for assess_company_type() assessment."""

    def test_product_company_detected(self):
        """Product company indicators detected."""
        job = create_test_job(
            "test-8",
            "SaaS Company",
            "QA Engineer",
            "We are building our own product platform. Our in-house team develops the core system.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.PRODUCT

    def test_consulting_company_detected(self):
        """Consulting company indicators detected."""
        job = create_test_job(
            "test-9",
            "Consulting Firm",
            "Test Engineer",
            "We provide consulting services. You will work on client sites, engaging with our billable projects.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.CONSULTING

    def test_esn_company_detected(self):
        """ESN (European Staffing Network) detected."""
        job = create_test_job(
            "test-10",
            "European Staffing",
            "Test Engineer",
            "European Staffing Network providing staff augmentation and consulting services.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.ESN

    def test_esn_takes_precedence_over_consulting(self):
        """ESN designation takes precedence over generic consulting."""
        job = create_test_job(
            "test-11",
            "ESN Consulting",
            "Test Engineer",
            "ESN company providing european staffing network consulting with client sites.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.ESN

    def test_no_company_type_signals_returns_unknown(self):
        """No clear type indicators returns UNKNOWN."""
        job = create_test_job(
            "test-12",
            "SomeCompany",
            "QA Engineer",
            "We are looking for a QA engineer with 5+ years experience.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.UNKNOWN

    def test_product_vs_consulting_product_wins(self):
        """When product keywords outnumber consulting, product wins."""
        job = create_test_job(
            "test-13",
            "ProductCorp",
            "QA Engineer",
            "Our product platform is built in-house. We develop our own codebase.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.PRODUCT

    def test_consulting_vs_product_consulting_wins(self):
        """When consulting keywords outnumber product, consulting wins."""
        job = create_test_job(
            "test-14",
            "ConsultingCorp",
            "Test Engineer",
            "Consulting firm placing engineers on client sites for billable projects.",
        )
        result = assess_company_type(job, candidate_profile)
        assert result == CompanyType.CONSULTING


class TestSponsorshipLikelihood:
    """
    Tests for assess_sponsorship_likelihood() - evidence-based assessment.
    
    CRITICAL DESIGN:
    - Sponsorship likelihood based ONLY on explicit evidence
    - Absence of sponsorship info ≠ evidence against sponsorship
    - Company type/size/international NOT used to infer sponsorship
    - Boundary cases: ESN/multinational/generic with no sponsorship info → UNKNOWN
    """

    # ─── EXPLICIT SPONSORSHIP SIGNALS ────────────────────────────────

    def test_explicit_visa_sponsorship_likely(self):
        """Explicit visa sponsorship language → LIKELY."""
        job = create_test_job(
            "test-15",
            "Company",
            "QA Engineer",
            "Visa sponsorship available for qualified candidates.",
        )
        rules = load_matching_rules()
        result = assess_sponsorship_likelihood(job, candidate_profile, rules)
        assert result == SponsorshipLikelihood.LIKELY

    def test_explicit_visa_support_likely(self):
        """Explicit visa support language → LIKELY."""
        job = create_test_job(
            "test-16",
            "Company",
            "QA Engineer",
            "We offer visa support and relocation package for international candidates.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.LIKELY

    def test_explicit_can_sponsor_likely(self):
        """Explicit 'can sponsor' or 'willing to sponsor' → LIKELY."""
        job = create_test_job(
            "test-17",
            "Company",
            "Test Engineer",
            "We are willing to sponsor visa for qualified engineers.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.LIKELY

    # ─── EXPLICIT REJECTION ─────────────────────────────────────────

    def test_explicit_no_sponsorship_unlikely(self):
        """Explicit 'no sponsorship' → UNLIKELY."""
        job = create_test_job(
            "test-18",
            "Company",
            "QA Engineer",
            "Looking for candidates. Note: No sponsorship available.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.UNLIKELY

    def test_explicit_eu_citizens_only_unlikely(self):
        """Explicit 'EU citizens only' → UNLIKELY."""
        job = create_test_job(
            "test-19",
            "Company",
            "Test Engineer",
            "We are looking for EU citizens only.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.UNLIKELY

    def test_explicit_unrestricted_work_auth_required_unlikely(self):
        """Explicit work auth requirement → UNLIKELY."""
        job = create_test_job(
            "test-20",
            "Company",
            "QA Engineer",
            "Candidates must already have unrestricted work authorization.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.UNLIKELY

    # ─── INTERNATIONAL HIRING SIGNALS (NO EXPLICIT SPONSORSHIP) ──────

    def test_international_hiring_signal_possible(self):
        """International hiring language (no sponsorship mention) → POSSIBLE."""
        job = create_test_job(
            "test-21",
            "Company",
            "QA Engineer",
            "We are a global team. We hire internationally from many countries.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.POSSIBLE

    def test_multinational_no_sponsorship_stated_possible_not_likely(self):
        """Multinational with no sponsorship info → POSSIBLE (NOT auto-LIKELY)."""
        job = create_test_job(
            "test-22",
            "Multinational Corp",
            "QA Engineer",
            "We are a multinational company with offices in 15 countries.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        # Multinational alone is weak signal: POSSIBLE, not LIKELY
        assert result == SponsorshipLikelihood.POSSIBLE

    # ─── BOUNDARY CASE: ESN/CONSULTING WITH NO SPONSORSHIP INFO ──────

    def test_esn_no_sponsorship_evidence_unknown(self):
        """ESN company but no explicit sponsorship → UNKNOWN (NOT auto-LIKELY)."""
        job = create_test_job(
            "test-23",
            "ESN Company",
            "Test Engineer",
            "European Staffing Network providing staff augmentation for client projects.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        # Company type INDEPENDENT from sponsorship likelihood
        assert result == SponsorshipLikelihood.UNKNOWN

    def test_consulting_no_sponsorship_evidence_unknown(self):
        """Consulting company with no sponsorship info → UNKNOWN."""
        job = create_test_job(
            "test-24",
            "Consulting Firm",
            "QA Engineer",
            "Consulting firm providing technical services to clients.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        # No sponsorship evidence → UNKNOWN, not POSSIBLE
        assert result == SponsorshipLikelihood.UNKNOWN

    def test_product_company_no_sponsorship_evidence_unknown(self):
        """Product company with no sponsorship info → UNKNOWN."""
        job = create_test_job(
            "test-25",
            "SaaS Startup",
            "QA Engineer",
            "We are building our own product platform.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        # No sponsorship evidence → UNKNOWN
        assert result == SponsorshipLikelihood.UNKNOWN

    # ─── ABSENCE OF INFORMATION IS NOT NEGATIVE ──────────────────────

    def test_no_sponsorship_information_unknown(self):
        """Job with no sponsorship info → UNKNOWN (NOT UNLIKELY)."""
        job = create_test_job(
            "test-26",
            "Company",
            "QA Engineer",
            "Looking for a QA engineer with 5+ years experience in testing.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.UNKNOWN

    def test_generic_job_posting_no_sponsorship_mentioned_unknown(self):
        """Generic job posting with no sponsorship mention → UNKNOWN."""
        job = create_test_job(
            "test-27",
            "TechCorp",
            "Test Engineer",
            "We are hiring test engineers. Responsibilities include automation, testing, and validation.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.UNKNOWN

    # ─── COMPANY TYPE & SPONSORSHIP INDEPENDENCE ────────────────────

    def test_company_type_and_sponsorship_independent(self):
        """Company type assessment independent from sponsorship."""
        job = create_test_job(
            "test-28",
            "ProductCorp",
            "QA Engineer",
            "We build our own product. No sponsorship available.",
        )
        company_type = assess_company_type(job, candidate_profile)
        sponsorship = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())

        assert company_type == CompanyType.PRODUCT
        assert sponsorship == SponsorshipLikelihood.UNLIKELY

    # ─── RELOCATION WITHOUT EXPLICIT SPONSORSHIP ────────────────────

    def test_relocation_without_sponsorship_possible(self):
        """Relocation offer without explicit sponsorship → POSSIBLE."""
        job = create_test_job(
            "test-29",
            "Company",
            "QA Engineer",
            "We offer relocation assistance for candidates.",
        )
        result = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())
        assert result == SponsorshipLikelihood.POSSIBLE


class TestPhase2BIntegration:
    """Integration tests: company intelligence in matching pipeline."""

    def test_extract_and_assess_company_industry(self):
        """Industry extraction and assessment together."""
        job = create_test_job(
            "test-30",
            "Bosch",
            "Test Engineer",
            "Automotive embedded systems testing at our company.",
        )
        extracted = extract_company_industry(job.description)
        assert extracted == "automotive"

        # Set extracted industry on job (as matcher would do)
        job.company_industry = extracted

        relevance = assess_company_industry_relevance(
            job, candidate_profile, load_matching_rules()
        )
        assert relevance == IndustryRelevance.RELEVANT

    def test_unknown_company_data_not_blocking(self):
        """Missing company data must not fail assessments."""
        job = create_test_job(
            "test-31",
            "Unknown",
            "QA Engineer",
            "Generic testing role.",
        )

        industry = assess_company_industry_relevance(
            job, candidate_profile, load_matching_rules()
        )
        company_type = assess_company_type(job, candidate_profile)
        sponsorship = assess_sponsorship_likelihood(job, candidate_profile, load_matching_rules())

        assert industry == IndustryRelevance.UNKNOWN
        assert company_type == CompanyType.UNKNOWN
        assert sponsorship == SponsorshipLikelihood.UNKNOWN

    def test_all_assessments_return_valid_enums(self):
        """All assessments return proper enum values."""
        job = create_test_job(
            "test-32",
            "SaaS Inc",
            "QA Engineer",
            "We build our platform. Visa sponsorship available for international candidates.",
        )
        rules = load_matching_rules()

        industry = assess_company_industry_relevance(job, candidate_profile, rules)
        company_type = assess_company_type(job, candidate_profile)
        sponsorship = assess_sponsorship_likelihood(job, candidate_profile, rules)

        assert isinstance(industry, IndustryRelevance)
        assert isinstance(company_type, CompanyType)
        assert isinstance(sponsorship, SponsorshipLikelihood)

    def test_match_report_company_fields_optional(self):
        """MatchReport company fields are optional."""
        report = MatchReport(
            job_id="test-33",
            job_title="QA Engineer",
            company="Test",
            decision="SKIP",
            score=0,
            tier="excluded",
            reason="Not a match",
        )

        assert report.company_industry is None
        assert report.industry_relevance is None
        assert report.company_type is None
        assert report.company_type_match is None
        assert report.sponsorship_likelihood is None

    def test_match_report_company_fields_populated(self):
        """MatchReport company fields can be populated."""
        report = MatchReport(
            job_id="test-34",
            job_title="QA Engineer",
            company="Test",
            decision="APPLY",
            score=85,
            tier="primary",
            reason="Good match",
            company_industry="automotive",
            industry_relevance="relevant",
            company_type="product",
            company_type_match="preferred",
            sponsorship_likelihood="likely",
        )

        assert report.company_industry == "automotive"
        assert report.industry_relevance == "relevant"
        assert report.company_type == "product"
        assert report.company_type_match == "preferred"
        assert report.sponsorship_likelihood == "likely"

    def test_irrelevant_industry_assessed(self):
        """Irrelevant industry properly assessed."""
        job = create_test_job(
            "test-35",
            "FashionCorp",
            "QA Engineer",
            "Fashion e-commerce platform testing.",
            company_industry="fashion",
        )
        rules = load_matching_rules()
        relevance = assess_company_industry_relevance(job, candidate_profile, rules)
        assert relevance == IndustryRelevance.IRRELEVANT

    def test_backward_compatibility_phase2a_unchanged(self):
        """Phase 2B doesn't break Phase 2A fields."""
        job = create_test_job(
            "test-36",
            "Company",
            "Test Engineer",
            "Testing role",
        )
        # Phase 2A fields still work
        assert job.company == "Company"
        # Phase 2B fields default to None
        assert job.company_industry is None
        assert job.company_size is None
