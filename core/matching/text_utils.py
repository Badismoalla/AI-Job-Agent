"""
core/matching/text_utils.py
-----------------------------
Small, stateless text helpers shared by every matching component.
"""

import re
from datetime import date


def normalise(text: str) -> str:
    """Lowercase and strip punctuation for keyword matching."""
    return re.sub(r"[^a-z0-9 /.]", " ", text.lower())


def count_matches(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    """Return count and list of keywords found in text."""
    found = [kw for kw in keywords if kw.lower() in text]
    return len(found), found


def years_between(start: str, end: str | None) -> float:
    """Estimate elapsed years from YYYY-MM profile dates."""
    start_year, start_month = (int(part) for part in start[:7].split("-"))
    if end:
        end_year, end_month = (int(part) for part in end[:7].split("-"))
    else:
        today = date.today()
        end_year, end_month = today.year, today.month
    return max(0.0, (end_year - start_year) + (end_month - start_month) / 12)
