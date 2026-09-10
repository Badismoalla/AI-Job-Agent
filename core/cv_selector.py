"""
core/cv_selector.py
----------------------
Deterministic CV selection.

Grounded in the real repository state (verified against resumes/ on
disk, not assumed): today there are exactly two CV files, split by
market region, not by role tier --

    resumes/Europe/Badis_Moalla.pdf   -> Poland, Netherlands, Luxembourg
    resumes/GCC/Badis_Moalla CV.pdf   -> UAE, Saudi Arabia, Qatar

Both currently cover both RoleTier.PRIMARY and RoleTier.SECONDARY, since
there is no separate "QA CV" vs "Data CV" file in this repository. The
config format (config/cvs.json) still carries a role_tiers list per CV
so that a future role-specific CV can be added without changing this
selector's logic -- but nothing here invents that split today.

Selection is by market first (the only real axis of variation), then
filtered by role_tiers, then ordered deterministically by
(priority, id) when more than one candidate matches. A CV is only ever
returned if its configured file genuinely exists on disk at selection
time -- a missing file is always REVIEW_REQUIRED, never silently
skipped or fabricated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.models import JobListing, Market, RoleTier

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "cvs.json"


@dataclass
class CVSelection:
    """Result of a CV selection attempt."""

    cv_id: str | None
    path: Path | None
    reason: str
    review_required: bool = False


class CVSelector:
    """Deterministic, market-driven CV selection against config/cvs.json."""

    def __init__(self, config_path: Path | None = None, project_root: Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._project_root = Path(project_root) if project_root else _PROJECT_ROOT
        self._cvs = self._load_config()

    def select(self, job: JobListing, tier: RoleTier) -> CVSelection:
        """
        Select the CV for a given job and role tier. Never fabricates a
        path -- returns review_required=True with cv_id/path=None whenever
        there is no safe, real answer.
        """
        if tier == RoleTier.EXCLUDED:
            return CVSelection(
                cv_id=None, path=None, review_required=True,
                reason="Role tier is EXCLUDED -- no application should be prepared, so no CV is selected.",
            )

        market_value = job.market.value if hasattr(job.market, "value") else str(job.market)
        tier_value = tier.value if hasattr(tier, "value") else str(tier)

        candidates = [
            cv for cv in self._cvs
            if market_value in cv.get("markets", []) and tier_value in cv.get("role_tiers", [])
        ]
        if not candidates:
            return CVSelection(
                cv_id=None, path=None, review_required=True,
                reason=f"No CV configured for market '{market_value}' and tier '{tier_value}' in {self._config_path}.",
            )

        candidates.sort(key=lambda c: (c.get("priority", 999), c.get("id", "")))
        chosen = candidates[0]

        full_path = self._project_root / chosen["path"]
        if not full_path.exists():
            return CVSelection(
                cv_id=None, path=None, review_required=True,
                reason=f"Configured CV '{chosen['id']}' does not exist on disk at {full_path}.",
            )

        return CVSelection(
            cv_id=chosen["id"], path=full_path, review_required=False,
            reason=f"Matched market '{market_value}' (tier '{tier_value}') to CV '{chosen['id']}'.",
        )

    def _load_config(self) -> list[dict]:
        if not self._config_path.exists():
            return []
        data = json.loads(self._config_path.read_text(encoding="utf-8"))
        return data.get("cvs", [])
