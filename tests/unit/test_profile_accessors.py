"""
Regression tests for core.profile accessors fixed during Phase 2F Step 2
hardening. Previously salary_minimum_eur/salary_target_eur/remote_preference
silently returned None/broken values against the real profile.json shape.

Run against the REAL profile singleton (core.profile.profile) -- these are
genuine proofs against actual data/profile.json, not synthetic fixtures.
"""

from core.profile import profile


class TestRemotePreference:

    def test_returns_a_list_matching_real_schema(self):
        result = profile.remote_preference
        assert isinstance(result, list)

    def test_matches_real_work_type_field(self):
        assert profile.remote_preference == profile.target.get("work_type", [])

    def test_contains_expected_real_values(self):
        # Verified against the real data/profile.json: on-site/hybrid, no remote.
        assert "on-site" in profile.remote_preference
        assert "hybrid" in profile.remote_preference
        assert "remote" not in profile.remote_preference


class TestSalaryExpectationForMarket:

    def test_poland_returns_correct_currency_and_period(self):
        result = profile.salary_expectation_for_market("Poland")
        assert result is not None
        assert result["currency"] == "PLN"
        assert result["period"] == "monthly"
        assert "-" in result["range"]

    def test_gcc_markets_share_the_same_combined_key(self):
        """UAE, Saudi Arabia, and Qatar all resolve to the same GCC_AED_monthly entry."""
        uae = profile.salary_expectation_for_market("UAE")
        saudi = profile.salary_expectation_for_market("Saudi Arabia")
        qatar = profile.salary_expectation_for_market("Qatar")

        assert uae is not None
        assert uae == saudi == qatar
        assert uae["currency"] == "AED"
        assert uae["key"] == "GCC_AED_monthly"

    def test_netherlands_and_luxembourg_use_annual_eur(self):
        nl = profile.salary_expectation_for_market("Netherlands")
        lux = profile.salary_expectation_for_market("Luxembourg")
        assert nl["currency"] == "EUR"
        assert nl["period"] == "annual"
        assert lux["currency"] == "EUR"
        assert lux["period"] == "annual"

    def test_unknown_market_returns_none(self):
        assert profile.salary_expectation_for_market("Germany") is None

    def test_never_performs_currency_conversion(self):
        """The range value is the raw string from profile.json, untouched."""
        result = profile.salary_expectation_for_market("Poland")
        raw = profile.target["salary_expectations"]["Poland_PLN_monthly"]
        assert result["range"] == raw

    def test_broken_old_properties_no_longer_exist(self):
        """salary_minimum_eur/salary_target_eur are removed, not left broken."""
        assert not hasattr(profile, "salary_minimum_eur")
        assert not hasattr(profile, "salary_target_eur")
