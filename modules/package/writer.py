"""Write reviewable application packages to disk."""

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.models import JobListing, MatchDecision, MatchReport


@dataclass(frozen=True)
class ApplicationPackage:
    """All content required to write one application preview package."""

    listing: JobListing
    report: MatchReport
    cover_letter: str
    recruiter_message: str
    hr_email: str
    application_answers: dict[str, Any]
    generated_at: datetime
    cv_path: Path | None = None
    cv_id: str | None = None


class PackageWriter:
    """Persist application preview artifacts; never parses, matches, or generates."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or Path(__file__).resolve().parents[2] / "output"

    def write(self, package: ApplicationPackage) -> Path:
        """Write all package files and return the package directory."""
        package_dir = self.output_dir / self._directory_name(package)
        package_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "cover_letter.md": package.cover_letter,
            "recruiter_message.md": package.recruiter_message,
            "hr_email.md": package.hr_email,
            "application_answers.json": json.dumps(package.application_answers, indent=2),
            "match_report.md": self._match_report_markdown(package),
            "metadata.json": json.dumps(self._metadata(package), indent=2),
            "README.md": self._readme(package),
        }
        for filename, content in files.items():
            (package_dir / filename).write_text(f"{content.rstrip()}\n", encoding="utf-8")

        self._copy_cv_if_present(package, package_dir)
        return package_dir

    @staticmethod
    def _copy_cv_if_present(package: "ApplicationPackage", package_dir: Path) -> None:
        """
        Copy the selected CV into the package as cv.pdf, if one was
        supplied and genuinely exists on disk. Never fabricates a file --
        a missing/absent cv_path simply means no cv.pdf is written, same
        as the pre-CV-selection behavior.
        """
        if package.cv_path is None:
            return
        if not package.cv_path.exists():
            return
        shutil.copyfile(package.cv_path, package_dir / "cv.pdf")

    def _directory_name(self, package: ApplicationPackage) -> str:
        date = package.generated_at.astimezone(timezone.utc).date().isoformat()
        company = _safe_component(package.listing.company)
        title = _safe_component(package.listing.title)
        return f"{date}_{company}_{title}"

    @staticmethod
    def _metadata(package: ApplicationPackage) -> dict[str, Any]:
        cv_used = package.cv_id if (package.cv_path is not None and package.cv_path.exists()) else None
        return {
            "generated_at": package.generated_at.isoformat(),
            "job": package.listing.model_dump(mode="json"),
            "match_report": package.report.model_dump(mode="json"),
            "cv_used": cv_used,
        }

    @staticmethod
    def _match_report_markdown(package: ApplicationPackage) -> str:
        report = package.report
        gaps = "\n".join(f"- {gap}" for gap in report.skill_gaps) or "- None identified"
        strengths = [
            *report.matched_keywords,
            *report.matched_protocols,
            *report.matched_tools,
        ]
        if report.domain_found:
            strengths.append(f"Domain: {report.domain_found}")
        strength_lines = "\n".join(f"- {item}" for item in strengths) or "- None identified"
        review_reason = (
            f"{report.reason} The score is in the REVIEW range, so the match should be assessed manually."
            if report.decision == MatchDecision.REVIEW
            else report.reason
        )
        review_guidance = (
            "Manual review is recommended before applying. Review the missing skills and confirm that the strengths outweigh the gaps."
            if report.decision == MatchDecision.REVIEW
            else "This package is recommended for application review."
        )
        return (
            f"# Match Report\n\n"
            f"- **Decision:** {report.decision}\n"
            f"- **Score:** {report.score}/100\n"
            f"- **Tier:** {report.tier}\n"
            f"- **Why:** {review_reason}\n\n"
            f"## Strengths\n\n{strength_lines}\n\n"
            f"## Skill Gaps\n\n{gaps}\n\n"
            f"## Review Guidance\n\n{review_guidance}"
        )

    @staticmethod
    def _readme(package: ApplicationPackage) -> str:
        generated_at = package.generated_at.isoformat()
        decision = package.report.decision
        files = (
            "- `cover_letter.md`\n"
            "- `recruiter_message.md`\n"
            "- `hr_email.md`\n"
            "- `application_answers.json`\n"
            "- `match_report.md`\n"
            "- `metadata.json`\n"
            "- `README.md`"
        )
        review_note = (
            "\n\nManual review is recommended. Review the gaps in `match_report.md` before applying."
            if decision == MatchDecision.REVIEW
            else ""
        )
        return (
            f"# Application Package\n\n"
            f"- **Company:** {package.listing.company}\n"
            f"- **Role:** {package.listing.title}\n"
            f"- **Decision:** {decision}\n"
            f"- **Match score:** {package.report.score}/100\n"
            f"- **Generation timestamp:** {generated_at}\n\n"
            f"## Generated Files\n\n{files}{review_note}"
        )


def _safe_component(value: str) -> str:
    """Convert a company or title into a readable filesystem-safe component."""
    component = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return component or "unknown"