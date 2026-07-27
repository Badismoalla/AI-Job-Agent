"""Shared job-description analysis pipeline."""

from pathlib import Path

from core.matcher import JobMatcher
from core.models import JobListing, MatchReport
from modules.analyzer.job_parser import JobParser


def parse_and_match(file_path: Path) -> tuple[JobListing, MatchReport]:
    """Parse a job description and evaluate it against the candidate profile."""
    listing = JobParser.from_file(file_path)
    report = JobMatcher().evaluate(listing)
    return listing, report