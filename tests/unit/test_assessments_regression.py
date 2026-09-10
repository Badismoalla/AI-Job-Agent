"""
Regression tests for core.matching.assessments.assess_remote_work and
assess_salary after the Phase 2F Step 2 hardening pass fixed the broken
profile accessors these functions depend on. Previously these accessors
always returned None/broken values, so both functions always returned
UNKNOWN in practice -- dead code paths despite passing tests. These tests
prove the paths are now genuinely reachable and correct against the real
profile.
"""

from core.matching.assessments import assess_remote_work, assess_salary
from core.matching.compatibility import RemoteCompatibility, SalaryCompatibility
from core.models import ApplicationSource, JobListing, Market
from core.profile import profile


def _job(**overrides) -> JobListing:
    defaults = dict(
        id="test-job", title="Engineer", company="Test Corp",
        city="Warsaw", market=Market.POLAND, url="https://example.com/1",
        source=ApplicationSource.NOFLUFFJOBS,
    )
    defaults.update(overrides)
    return JobListing(**defaults)


class TestAssessRemoteWork:
    """profile.remote_preference is now a real list (["full-time","on-site","hybrid"]) -- no 'remote' entry."""

    def test_no_longer_returns_unknown_by_default(self):
        """Previously always UNKNOWN due to the broken accessor -- now genuinely evaluated."""
        job = _job(description="On-site role, no remote work.")
        result = assess_remote_work(job, profile)
        assert result != RemoteCompatibility.UNKNOWN

    def test_candidate_without_remote_listed_is_compatible_with_onsite(self):
        """Real profile doesn't list 'remote' -- candidate accepts on-site/hybrid, so always compatible."""
        job = _job(description="This is a fully on-site position.")
        result = assess_remote_work(job, profile)
        assert result == RemoteCompatibility.COMPATIBLE

    def test_candidate_compatible_with_hybrid_too(self):
        job = _job(description="Hybrid role, 3 days in office.")
        result = assess_remote_work(job, profile)
        assert result == RemoteCompatibility.COMPATIBLE


class TestAssessSalary:
    """profile.salary_expectation_for_market is now market-aware, currency-safe."""

    def test_no_job_salary_posted_returns_unknown(self):
        job = _job(salary_min=None, salary_max=None)
        result = assess_salary(job, profile)
        assert result == SalaryCompatibility.UNKNOWN

    def test_matching_currency_within_range(self):
        """Poland_PLN_monthly range low end -- posting well above it should be WITHIN_RANGE."""
        expectation = profile.salary_expectation_for_market("Poland")
        low = int(expectation["range"].split("-")[0])
        job = _job(
            market=Market.POLAND,
            salary_min=(low + 5000), salary_currency="PLN", salary_period="PER_MONTH",
        )
        result = assess_salary(job, profile)
        assert result == SalaryCompatibility.WITHIN_RANGE

    def test_matching_currency_below_minimum(self):
        expectation = profile.salary_expectation_for_market("Poland")
        low = int(expectation["range"].split("-")[0])
        job = _job(
            market=Market.POLAND,
            salary_min=max(low - 5000, 1000), salary_currency="PLN", salary_period="PER_MONTH",
        )
        result = assess_salary(job, profile)
        assert result == SalaryCompatibility.BELOW_MINIMUM

    def test_mismatched_currency_returns_currency_mismatch_not_conversion(self):
        """Job posts in USD against a PLN-configured market -- never silently converted."""
        job = _job(market=Market.POLAND, salary_min=5000, salary_currency="USD", salary_period="PER_MONTH")
        result = assess_salary(job, profile)
        assert result == SalaryCompatibility.CURRENCY_MISMATCH

    def test_gcc_market_uses_aed(self):
        job = _job(
            market=Market.UAE, salary_min=20000, salary_currency="AED", salary_period="PER_MONTH",
        )
        result = assess_salary(job, profile)
        assert result in (SalaryCompatibility.WITHIN_RANGE, SalaryCompatibility.BELOW_MINIMUM)

    def test_never_returns_a_fabricated_conversion(self):
        """No test here should ever see a value that required inventing an exchange rate."""
        job = _job(market=Market.POLAND, salary_min=5000, salary_currency="USD", salary_period="PER_MONTH")
        result = assess_salary(job, profile)
        assert result == SalaryCompatibility.CURRENCY_MISMATCH
