"""
core/profile.py
---------------
Loads, validates, and exposes the candidate profile (profile.json).

Why a dedicated module?
- Every module needs profile data (scraper needs target roles, AI needs experience)
- Single load at startup — not re-read on every call
- Validates structure so bad data fails early, not mid-run

Usage:
    from core.profile import profile
    print(profile.personal["name"])
    print(profile.target["roles"])
"""

import json
from pathlib import Path
from typing import Any

from core.exceptions import ProfileNotFoundError
from core.logger import get_logger

logger = get_logger(__name__)

_PROFILE_PATH = Path(__file__).parent.parent / "data" / "profile.json"

_REQUIRED_KEYS = {"personal", "target", "experience", "skills", "education", "certifications"}


class CandidateProfile:
    """
    Wrapper around profile.json providing typed access to all sections.
    Loaded once at import time — treat as read-only.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def personal(self) -> dict[str, Any]:
        return self._data["personal"]

    @property
    def target(self) -> dict[str, Any]:
        return self._data["target"]

    @property
    def experience(self) -> list[dict[str, Any]]:
        return self._data["experience"]

    @property
    def skills(self) -> dict[str, list[str]]:
        return self._data["skills"]

    @property
    def education(self) -> dict[str, Any]:
        return self._data["education"]

    @property
    def certifications(self) -> list[dict[str, Any]]:
        return self._data["certifications"]

    @property
    def key_metrics(self) -> dict[str, str]:
        return self._data.get("key_metrics", {})

    @property
    def known_gaps(self) -> list[str]:
        return self._data.get("known_skill_gaps", [])

    @property
    def do_not_overstate(self) -> list[str]:
        return self._data.get("do_not_overstate", [])

    @property
    def all_skills_flat(self) -> list[str]:
        """Return all skills as a flat deduplicated list — used for matching."""
        skills = []
        for skill_group in self.skills.values():
            skills.extend(skill_group)
        return list(dict.fromkeys(skills))  # deduplicate preserving order

    @property
    def target_roles(self) -> list[str]:
        """
        Return all target roles as a flat list.
        Handles both old format (list) and new format (dict with primary/secondary keys).
        """
        roles = self.target["roles"]
        if isinstance(roles, list):
            return roles
        # New format: {"primary": [...], "secondary": [...]}
        primary = roles.get("primary", [])
        secondary = roles.get("secondary", [])
        return primary + secondary

    @property
    def primary_roles(self) -> list[str]:
        """Return only PRIMARY target roles."""
        roles = self.target["roles"]
        if isinstance(roles, list):
            return roles
        return roles.get("primary", [])

    @property
    def secondary_roles(self) -> list[str]:
        """Return only SECONDARY (data/BI) target roles."""
        roles = self.target["roles"]
        if isinstance(roles, list):
            return []
        return roles.get("secondary", [])

    @property
    def target_markets(self) -> list[str]:
        return self.target["markets"]

    # ── Phase 2A: Work authorization & visa sponsorship ─────────────────────
    @property
    def work_authorization_authorized(self) -> bool:
        """Is candidate already authorized to work (e.g., EU passport)?"""
        return self.personal.get("eu_passport", False)

    @property
    def visa_sponsorship_needed(self) -> bool:
        """Does candidate need visa sponsorship to work?"""
        visa_required = self.target.get("visa_required", None)
        if visa_required is None:
            # If not explicitly set, assume no sponsorship needed
            return False
        return visa_required

    # ── Phase 2A: Remote work preference ──────────────────────────────────────
    @property
    def remote_preference(self) -> list[str]:
        """
        Candidate's work-arrangement acceptability list, e.g.
        ["full-time", "on-site", "hybrid"]. Matches the real profile.json
        schema (target.work_type is a flat list of strings) -- this
        replaces a prior implementation that assumed a dict of boolean
        flags ({"remote_required": ..., "hybrid_acceptable": ...}) which
        never matched the actual data and always returned None.
        """
        work_type = self.target.get("work_type", [])
        if isinstance(work_type, list):
            return work_type
        return []

    # ── Phase 2A: Target locations ───────────────────────────────────────────
    @property
    def target_countries(self) -> list[str]:
        """Countries where candidate is willing to work."""
        # Extract from markets (infer countries) + explicit cities
        markets = self.target_markets
        # For now, just return market names as country proxies
        # Future: improve mapping
        return markets

    @property
    def target_cities(self) -> list[str]:
        """Cities where candidate is willing to work."""
        return self.target.get("cities", [])

    # ── Phase 2A: Salary expectations ────────────────────────────────────────
    _MARKET_TO_SALARY_KEY = {
        "Poland": "Poland_PLN_monthly",
        "Netherlands": "Netherlands_EUR_annual",
        "Luxembourg": "Luxembourg_EUR_annual",
        "UAE": "GCC_AED_monthly",
        "Saudi Arabia": "GCC_AED_monthly",
        "Qatar": "GCC_AED_monthly",
    }

    def salary_expectation_for_market(self, market: str) -> dict[str, str] | None:
        """
        Returns {"range": "14000-20000", "currency": "PLN", "period": "monthly", "key": "Poland_PLN_monthly"}
        for the given market, or None if not configured.

        Replaces the former salary_minimum_eur/salary_target_eur
        properties, which assumed a single universal EUR figure that
        does not exist in the real profile schema -- salary_expectations
        is keyed per market/currency/period (e.g. "Poland_PLN_monthly"),
        and GCC markets (UAE, Saudi Arabia, Qatar) share one combined
        "GCC_AED_monthly" key rather than one each). No currency
        conversion is performed here or by any caller of this method.
        """
        key = self._MARKET_TO_SALARY_KEY.get(market)
        if key is None:
            return None
        raw_range = self.target.get("salary_expectations", {}).get(key)
        if not raw_range:
            return None
        parts = key.split("_")
        currency = parts[-2] if len(parts) >= 2 else ""
        period = parts[-1] if len(parts) >= 1 else ""
        return {"range": raw_range, "currency": currency, "period": period, "key": key}

    # ── Phase 2A: Languages ──────────────────────────────────────────────────
    @property
    def languages(self) -> dict[str, str]:
        """Candidate's languages: {language_name -> proficiency_level}."""
        langs = self.personal.get("languages", [])
        if isinstance(langs, list):
            result = {}
            for lang_dict in langs:
                if isinstance(lang_dict, dict):
                    name = lang_dict.get("name", "").lower()
                    level = lang_dict.get("level", "").lower()
                    # Normalize level to CEFR (A1-C2, Native)
                    level_normalized = normalize_language_level(level)
                    if name and level_normalized:
                        result[name] = level_normalized
            return result
        return {}

    def name(self) -> str:
        return self.personal["name"]

    def email(self) -> str:
        return self.personal["email"]

    def __repr__(self) -> str:
        return f"CandidateProfile(name={self.name()}, markets={self.target_markets})"


def normalize_language_level(level_str: str) -> str | None:
    """
    Convert various language level formats to CEFR standard (a1-c2, native).
    Examples: "C1 - Fluent" -> "c1", "Native" -> "native", "Advanced" -> "c1".
    """
    if not level_str:
        return None
    
    level_lower = level_str.lower().strip()
    
    # Handle CEFR codes directly
    for cefr in ["a1", "a2", "b1", "b2", "c1", "c2"]:
        if cefr in level_lower:
            return cefr
    
    # Handle descriptive terms
    if "native" in level_lower or "mother tongue" in level_lower:
        return "native"
    elif "proficient" in level_lower or "c2" in level_lower:
        return "c2"
    elif "advanced" in level_lower or "c1" in level_lower:
        return "c1"
    elif "fluent" in level_lower or "upper" in level_lower or "b2" in level_lower:
        return "b2"
    elif "intermediate" in level_lower or "b1" in level_lower:
        return "b1"
    elif "elementary" in level_lower or "a" in level_lower:
        # Default elementary to A2
        return "a2" if "a2" in level_lower else "a1" if "a1" in level_lower else "a2"
    elif "basic" in level_lower:
        return "a2"
    
    return None


def _load_profile(path: Path) -> CandidateProfile:
    """Load and validate profile.json. Raises ProfileNotFoundError if invalid."""
    if not path.exists():
        raise ProfileNotFoundError(
            f"profile.json not found at {path}. "
            "Copy data/profile.json.example and fill in your details."
        )

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ProfileNotFoundError(f"profile.json is not valid JSON: {e}") from e

    missing = _REQUIRED_KEYS - set(data.keys())
    if missing:
        raise ProfileNotFoundError(
            f"profile.json is missing required sections: {missing}"
        )

    logger.info("Profile loaded | name={name}", name=data["personal"].get("name", "unknown"))
    return CandidateProfile(data)


# Module-level singleton — import this everywhere
profile = _load_profile(_PROFILE_PATH)
