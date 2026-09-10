"""
core/matching/rules.py
--------------------------
Loads and validates config/matching_rules.json — the single source of truth
for every keyword list, gap mitigation, score weight, and decision threshold
used by the matching engine.

No keyword list, weight, or threshold is hardcoded in Python. Every matching
component (classifier, scorer, gap_detector, decision) receives its data
through a MatchingRules instance, loaded once when JobMatcher is constructed
and passed down through evaluate().

To change matching behaviour (add a keyword, adjust a threshold, add a gap
mitigation), edit config/matching_rules.json — no Python change required.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.exceptions import ConfigurationError

# Default location — repo_root/config/matching_rules.json
_DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "matching_rules.json"

_REQUIRED_KEYS: set[str] = {
    "primary_title_keywords",
    "primary_body_keywords",
    "domain_keywords",
    "protocol_keywords",
    "candidate_tools",
    "secondary_title_keywords",
    "secondary_required_skills",
    "secondary_domains",
    "excluded_title_keywords",
    "excluded_body_keywords",
    "senior_data_engineer_exclusion_keywords",
    "language_keywords",
    "seniority_levels",
    "gap_checks",
    "weights",
    "primary_thresholds",
    "secondary_thresholds",
}


@dataclass(frozen=True)
class MatchingRules:
    """Typed, read-only view over config/matching_rules.json."""

    primary_title_keywords: list[str]
    primary_body_keywords: list[str]
    domain_keywords: list[str]
    protocol_keywords: list[str]
    candidate_tools: list[str]
    secondary_title_keywords: list[str]
    secondary_required_skills: list[str]
    secondary_domains: list[str]
    excluded_title_keywords: list[str]
    excluded_body_keywords: list[str]
    senior_data_engineer_exclusion_keywords: list[str]
    language_keywords: list[str]
    seniority_levels: dict[str, int]
    gap_checks: list[dict[str, Any]]
    weights: dict[str, int]
    primary_thresholds: dict[str, int]
    secondary_thresholds: dict[str, int]
    
    # Phase 2A: Optional fields with defaults for backward compatibility
    soft_penalties: dict[str, int] = field(default_factory=dict)
    remote_policy_keywords: dict[str, list[str]] = field(default_factory=dict)
    language_levels: dict[str, list[str]] = field(default_factory=dict)
    deadline_keywords: dict[str, list[str]] = field(default_factory=dict)
    salary_period_keywords: dict[str, list[str]] = field(default_factory=dict)
    sponsorship_keywords: list[str] = field(default_factory=list)
    sponsorship_exclusions: list[str] = field(default_factory=list)


def load_matching_rules(path: Path | None = None) -> MatchingRules:
    """
    Load and validate the matching rules config file.

    Args:
        path: Optional override path (used by tests to load a fixture
              config). Defaults to config/matching_rules.json.

    Raises:
        ConfigurationError: if the file is missing, is not valid JSON, or is
        missing any required key.
    """
    rules_path = path or _DEFAULT_RULES_PATH

    if not rules_path.exists():
        raise ConfigurationError(f"Matching rules file not found: {rules_path}")

    try:
        raw_text = rules_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigurationError(
            f"Could not read matching rules file: {rules_path} ({e})"
        ) from e

    try:
        data: dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ConfigurationError(
            f"Matching rules file is not valid JSON: {rules_path} ({e})"
        ) from e

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Matching rules file must contain a JSON object at the top level: {rules_path}"
        )

    missing = _REQUIRED_KEYS - data.keys()
    if missing:
        raise ConfigurationError(
            f"Matching rules file is missing required keys {sorted(missing)}: {rules_path}"
        )

    # Load required keys + optional Phase 2A fields
    rules_dict = {key: data[key] for key in _REQUIRED_KEYS}
    
    # Add optional Phase 2A fields if present
    optional_fields = [
        "soft_penalties",
        "remote_policy_keywords",
        "language_levels",
        "deadline_keywords",
        "salary_period_keywords",
        "sponsorship_keywords",
        "sponsorship_exclusions",
    ]
    for opt_field in optional_fields:
        if opt_field in data:
            rules_dict[opt_field] = data[opt_field]
    
    return MatchingRules(**rules_dict)
