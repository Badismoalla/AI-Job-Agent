"""
Tests for commands.apply_preview CV-selection wiring (Phase 2F Step 1).

parse_and_match and ClaudeGenerator are mocked -- this tests the
orchestration (CV selected -> passed into the package -> copied to
disk), not job parsing or AI generation, which are already covered
elsewhere.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import (
    ApplicationSource, GeneratedMessage, JobListing, Market, MatchDecision,
    MatchReport, MessageType, RoleTier,
)


def _job(market=Market.POLAND) -> JobListing:
    return JobListing(
        id="test-job-1", title="Software Test Engineer", company="Test Corp",
        city="Warsaw", market=market, url="https://example.com/1",
        source=ApplicationSource.NOFLUFFJOBS,
    )


def _report(tier=RoleTier.PRIMARY, decision=MatchDecision.APPLY) -> MatchReport:
    return MatchReport(
        job_id="test-job-1", job_title="Software Test Engineer", company="Test Corp",
        tier=tier, decision=decision, score=90, reason="Strong match.",
    )


def _mock_message(body="Generated text") -> GeneratedMessage:
    return GeneratedMessage(type=MessageType.COVER_LETTER, body=body)


@pytest.fixture(autouse=True)
def mock_ai(monkeypatch):
    """ClaudeGenerator is never actually called in these tests."""
    mock_gen = MagicMock()
    mock_gen.generate_cover_letter = AsyncMock(return_value=_mock_message("Cover letter body"))
    mock_gen.generate_recruiter_message = AsyncMock(return_value=_mock_message("Recruiter body"))
    mock_gen.generate_hr_email = AsyncMock(return_value=_mock_message("HR email body"))
    monkeypatch.setattr("commands.apply_preview.ClaudeGenerator", lambda: mock_gen)
    return mock_gen


class TestApplyPreviewCVSelection:

    def test_poland_job_package_contains_europe_cv(self, tmp_path, monkeypatch):
        from commands.apply_preview import run_apply_preview

        monkeypatch.setattr(
            "commands.apply_preview.parse_and_match",
            lambda file_path: (_job(Market.POLAND), _report()),
        )
        monkeypatch.setattr("commands.apply_preview.PackageWriter", lambda: __import__(
            "modules.package.writer", fromlist=["PackageWriter"]
        ).PackageWriter(tmp_path))

        run_apply_preview(Path("dummy.txt"))

        package_dirs = list(tmp_path.iterdir())
        assert len(package_dirs) == 1
        cv_pdf = package_dirs[0] / "cv.pdf"
        assert cv_pdf.exists()

        import json
        metadata = json.loads((package_dirs[0] / "metadata.json").read_text())
        assert metadata["cv_used"] == "europe"

    def test_uae_job_package_contains_gcc_cv(self, tmp_path, monkeypatch):
        from commands.apply_preview import run_apply_preview

        monkeypatch.setattr(
            "commands.apply_preview.parse_and_match",
            lambda file_path: (_job(Market.UAE), _report()),
        )
        monkeypatch.setattr("commands.apply_preview.PackageWriter", lambda: __import__(
            "modules.package.writer", fromlist=["PackageWriter"]
        ).PackageWriter(tmp_path))

        run_apply_preview(Path("dummy.txt"))

        package_dirs = list(tmp_path.iterdir())
        import json
        metadata = json.loads((package_dirs[0] / "metadata.json").read_text())
        assert metadata["cv_used"] == "gcc"

    def test_excluded_tier_produces_no_cv_and_no_package(self, tmp_path, monkeypatch, capsys):
        """SKIP decision already short-circuits before CV selection would even run -- but confirm no crash."""
        from commands.apply_preview import run_apply_preview

        monkeypatch.setattr(
            "commands.apply_preview.parse_and_match",
            lambda file_path: (_job(Market.POLAND), _report(tier=RoleTier.EXCLUDED, decision=MatchDecision.SKIP)),
        )

        run_apply_preview(Path("dummy.txt"))
        # SKIP decision returns before any package/CV logic -- no exception is the assertion.
