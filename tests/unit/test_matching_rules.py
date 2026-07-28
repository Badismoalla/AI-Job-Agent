"""
Unit tests for core.matching.rules — the config/matching_rules.json loader.

Covers:
- Default config loads successfully and produces a fully-populated MatchingRules
- JobMatcher() loads rules at construction time from the default file
- A custom rules_path can be supplied (proves behaviour is config-driven,
  not hardcoded — changing the file changes matcher output)
- Missing file / malformed JSON / missing required keys all raise
  ConfigurationError with a clear message
"""

import json

import pytest

from core.exceptions import ConfigurationError
from core.matcher import JobMatcher
from core.matching.rules import MatchingRules, load_matching_rules
from core.models import ApplicationSource, JobListing, Market, MatchDecision, RoleTier


def make_job(title: str, description: str = "") -> JobListing:
    return JobListing(
        id="testco-role-krakow",
        title=title,
        company="TestCo",
        city="Krakow",
        market=Market.POLAND,
        url="https://example.com/jobs/testco-role-krakow",
        source=ApplicationSource.LINKEDIN,
        description=description,
    )


MINIMAL_VALID_RULES: dict = {
    "primary_title_keywords": ["test engineer"],
    "primary_body_keywords": ["automotive", "embedded", "ecu"],
    "domain_keywords": ["automotive"],
    "protocol_keywords": ["uds"],
    "candidate_tools": ["python"],
    "secondary_title_keywords": ["data analyst"],
    "secondary_required_skills": ["sql", "python", "power bi"],
    "secondary_domains": ["automotive"],
    "excluded_title_keywords": ["data scientist"],
    "excluded_body_keywords": ["machine learning"],
    "senior_data_engineer_exclusion_keywords": ["spark"],
    "language_keywords": ["english"],
    "seniority_levels": {"junior": 1, "senior": 3},
    "gap_checks": [
        {"gap_name": "canoe", "trigger": "canoe", "mitigation": "Use DLT/Wireshark instead.", "canoe_mitigation": True},
    ],
    "weights": {"role_match": 25},
    "primary_thresholds": {"APPLY": 65, "REVIEW": 40},
    "secondary_thresholds": {"APPLY": 75, "REVIEW": 60},
}


# ── Default config loading ──────────────────────────────────────────────────

class TestDefaultConfigLoading:

    def test_load_matching_rules_default_path_succeeds(self):
        rules = load_matching_rules()
        assert isinstance(rules, MatchingRules)

    def test_default_rules_populate_every_field(self):
        rules = load_matching_rules()
        assert len(rules.primary_title_keywords) > 0
        assert len(rules.excluded_title_keywords) > 0
        assert len(rules.gap_checks) > 0
        assert "APPLY" in rules.primary_thresholds
        assert "APPLY" in rules.secondary_thresholds

    def test_job_matcher_loads_rules_on_construction(self):
        """JobMatcher() must load rules at __init__ time, not lazily."""
        matcher = JobMatcher()
        assert isinstance(matcher.rules, MatchingRules)
        assert len(matcher.rules.primary_title_keywords) > 0


# ── Custom config path proves behaviour is config-driven ───────────────────

class TestCustomConfigPath:

    def test_custom_rules_change_matcher_behaviour(self, tmp_path):
        """
        Point JobMatcher at a minimal custom rules file and confirm the
        decision changes accordingly — proving keywords/thresholds are read
        from config, not hardcoded in Python.
        """
        custom_path = tmp_path / "custom_rules.json"
        custom_path.write_text(json.dumps(MINIMAL_VALID_RULES), encoding="utf-8")

        matcher = JobMatcher(rules_path=custom_path)
        job = make_job(
            "Test Engineer",
            description="Automotive embedded ECU UDS testing python",
        )
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.PRIMARY
        assert report.decision in (MatchDecision.APPLY, MatchDecision.REVIEW)

    def test_custom_thresholds_are_respected(self, tmp_path):
        """Lowering the APPLY threshold in config should change the decision
        for a job that would otherwise land in REVIEW."""
        rules_data = dict(MINIMAL_VALID_RULES)
        rules_data["primary_thresholds"] = {"APPLY": 0, "REVIEW": 0}
        custom_path = tmp_path / "custom_rules.json"
        custom_path.write_text(json.dumps(rules_data), encoding="utf-8")

        matcher = JobMatcher(rules_path=custom_path)
        job = make_job("Test Engineer", description="no relevant keywords here at all")
        report = matcher.evaluate(job)
        assert report.decision == MatchDecision.APPLY

    def test_excluded_keywords_are_config_driven(self, tmp_path):
        """A title keyword only present in our custom exclusion list should
        get excluded, even though it's not excluded by the default config."""
        rules_data = dict(MINIMAL_VALID_RULES)
        rules_data["excluded_title_keywords"] = ["test engineer"]
        custom_path = tmp_path / "custom_rules.json"
        custom_path.write_text(json.dumps(rules_data), encoding="utf-8")

        matcher = JobMatcher(rules_path=custom_path)
        job = make_job("Test Engineer", description="automotive embedded ecu")
        report = matcher.evaluate(job)
        assert report.tier == RoleTier.EXCLUDED
        assert report.decision == MatchDecision.SKIP


# ── Error handling ───────────────────────────────────────────────────────────

class TestConfigErrorHandling:

    def test_missing_file_raises_configuration_error(self, tmp_path):
        missing_path = tmp_path / "does_not_exist.json"
        with pytest.raises(ConfigurationError, match="not found"):
            load_matching_rules(missing_path)

    def test_malformed_json_raises_configuration_error(self, tmp_path):
        bad_path = tmp_path / "bad.json"
        bad_path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not valid JSON"):
            load_matching_rules(bad_path)

    def test_non_object_json_raises_configuration_error(self, tmp_path):
        bad_path = tmp_path / "array.json"
        bad_path.write_text(json.dumps(["just", "a", "list"]), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="JSON object"):
            load_matching_rules(bad_path)

    def test_missing_required_key_raises_configuration_error(self, tmp_path):
        incomplete = dict(MINIMAL_VALID_RULES)
        del incomplete["gap_checks"]
        bad_path = tmp_path / "incomplete.json"
        bad_path.write_text(json.dumps(incomplete), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="gap_checks"):
            load_matching_rules(bad_path)

    def test_job_matcher_propagates_config_errors(self, tmp_path):
        """JobMatcher(rules_path=...) should fail fast at construction, not
        silently fall back to defaults."""
        missing_path = tmp_path / "does_not_exist.json"
        with pytest.raises(ConfigurationError):
            JobMatcher(rules_path=missing_path)
