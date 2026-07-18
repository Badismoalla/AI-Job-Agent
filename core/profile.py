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

    def name(self) -> str:
        return self.personal["name"]

    def email(self) -> str:
        return self.personal["email"]

    def __repr__(self) -> str:
        return f"CandidateProfile(name={self.name()}, markets={self.target_markets})"


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
