"""
tests/conftest.py
-----------------
Shared pytest fixtures available to all tests.
"""

import pytest
from pathlib import Path
from core.models import JobListing, Market, ApplicationSource


@pytest.fixture
def sample_job() -> JobListing:
    """A realistic job listing for use in tests."""
    return JobListing(
        id="jsdsolutions-automotive-test-engineer-krakow",
        title="Automotive Test Engineer",
        company="JSDSolutions",
        city="Krakow",
        market=Market.POLAND,
        url="https://jobs.jsdsolutions.pl",
        source=ApplicationSource.CAREER_PAGE,
        visa_sponsorship=True,
        match_score=97,
        match_gaps=["CANoe/CANalyzer (use Wireshark/DLT angle)"],
    )


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """A temporary database path for tracker tests."""
    return tmp_path / "test_applications.db.json"
