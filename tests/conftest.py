"""
tests/conftest.py
-----------------
Shared pytest fixtures available to all tests.
"""

import os

# config.settings.Settings() is instantiated at *import* time and requires
# ANTHROPIC_API_KEY. Any test module that imports modules.ai.claude_generator
# (directly or transitively) would otherwise fail at collection time in an
# environment with no .env file. Set a harmless placeholder before any test
# module is imported; real dry-run/mocked tests never make a network call
# with it.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

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
