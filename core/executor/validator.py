"""
core/executor/validator.py
----------------------------
Validates an application package directory before browser execution.

The package shape checked here is the ACTUAL output of
modules.package.writer.PackageWriter (verified against source):

    cover_letter.md
    recruiter_message.md
    hr_email.md
    application_answers.json
    match_report.md
    metadata.json  = {"generated_at": ..., "job": <JobListing dump>, "match_report": {...}}
    README.md

Job identity is checked via metadata["job"]["id"], matching PackageWriter's
real nesting -- not a flat "job_id" key.

Validation is read-only and always collects every error found rather than
stopping at the first problem.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.executor.states import ValidationResult
from core.models import JobListing

REQUIRED_TEXT_FILES: dict[str, str] = {
    "cover_letter.md": "cover_letter.md is missing (cover letter)",
    "recruiter_message.md": "recruiter_message.md is missing (recruiter message)",
    "hr_email.md": "hr_email.md is missing (email content)",
    "match_report.md": "match_report.md is missing (match report)",
}


class PackageValidator:
    """Validates application package completeness, identity, and content before execution."""

    def validate(self, package_dir: Path, job: JobListing) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if not package_dir.exists() or not package_dir.is_dir():
            return ValidationResult(
                valid=False,
                errors=[f"Package directory does not exist: {package_dir}"],
                reason="Package directory does not exist.",
            )

        # Metadata / identity checks
        metadata = self._validate_metadata(package_dir, job, errors)

        # Required text files
        for filename, message in REQUIRED_TEXT_FILES.items():
            self._validate_nonempty_text_file(package_dir, filename, message, errors)

        # application_answers.json — required, must be valid JSON
        self._validate_application_answers(package_dir, errors)

        valid = len(errors) == 0
        reason = None if valid else "; ".join(errors)

        return ValidationResult(valid=valid, errors=errors, warnings=warnings, reason=reason)

    # ── internal checks ───────────────────────────────────────────────

    def _validate_metadata(self, package_dir: Path, job: JobListing, errors: list[str]) -> dict | None:
        metadata_path = package_dir / "metadata.json"
        if not metadata_path.exists():
            errors.append("metadata.json is missing")
            return None

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"metadata.json is not valid JSON: {e}")
            return None

        job_data = metadata.get("job")
        if job_data is None:
            errors.append("metadata.json is missing the 'job' key")
            return metadata

        package_job_id = job_data.get("id") if isinstance(job_data, dict) else None
        if package_job_id != job.id:
            errors.append(
                f"Package job id '{package_job_id}' does not match execution job id '{job.id}' "
                "(cross-application identity mismatch)"
            )

        return metadata

    def _validate_nonempty_text_file(
        self, package_dir: Path, filename: str, missing_message: str, errors: list[str]
    ) -> None:
        path = package_dir / filename
        if not path.exists():
            errors.append(missing_message)
            return
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            label = filename.replace(".md", "").replace("_", " ")
            errors.append(f"{filename} is empty ({label})")

    def _validate_application_answers(self, package_dir: Path, errors: list[str]) -> None:
        path = package_dir / "application_answers.json"
        if not path.exists():
            errors.append("application_answers.json is missing (application answers)")
            return
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"application_answers.json is not valid JSON (answer): {e}")
