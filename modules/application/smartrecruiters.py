"""
modules/application/smartrecruiters.py
------------------------------------------
SmartRecruiters application-preparation adapter.

Not to be confused with modules/scraper/company/smartrecruiters.py
(fetches job *listings*) — this prepares an application *payload*. See
package docstring.

supports(job) matches on URL: SmartRecruiters-hosted job pages
consistently live under jobs.smartrecruiters.com or
careers.smartrecruiters.com.

payload caveat: same as greenhouse.py/lever.py — no network calls, no
verified submission wire format; field names here follow SmartRecruiters'
commonly-documented candidate-profile fields (firstName, lastName, email,
phone, resumeFileName, coverLetter) as a structured, human-reviewable
preparation artifact.
"""

from __future__ import annotations

from typing import Any

from core.models import GeneratedMessage, JobListing, MessageType
from modules.application.base import (
    ApplicationAdapter,
    ApplicationResult,
    candidate_info,
    find_message,
    resolve_cv_attachment,
)

_URL_MARKERS = ("jobs.smartrecruiters.com", "careers.smartrecruiters.com")


class SmartRecruitersAdapter(ApplicationAdapter):
    """Prepares a structured application payload for a SmartRecruiters-hosted job."""

    adapter_name = "smartrecruiters"

    def __init__(self, cv_path=None) -> None:
        self.cv_path = cv_path

    def supports(self, job: JobListing) -> bool:
        return any(marker in job.url.lower() for marker in _URL_MARKERS)

    def prepare(
        self,
        job: JobListing,
        messages: list[GeneratedMessage],
        dry_run: bool = True,
        **kwargs: Any,
    ) -> ApplicationResult:
        if not self.supports(job):
            return ApplicationResult(
                success=False, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
                error=f"Job URL doesn't look like a SmartRecruiters listing: {job.url}",
            )

        cover_letter = find_message(messages, MessageType.COVER_LETTER)
        if cover_letter is None:
            return ApplicationResult(
                success=False, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
                error="No COVER_LETTER message found in the provided messages.",
            )

        notes: list[str] = []
        attachments = []
        cv_attachment = resolve_cv_attachment(self.cv_path)
        if cv_attachment is None:
            notes.append("No CV path configured — prepared without a resume attachment.")
        else:
            attachments.append(cv_attachment)
            if not cv_attachment.exists:
                notes.append(f"CV path configured but file not found: {cv_attachment.path}")

        info = candidate_info()
        payload = {
            "job_url": job.url,
            "firstName": info["first_name"],
            "lastName": info["last_name"],
            "email": info["email"],
            "phone": info["phone"],
            "resumeFileName": cv_attachment.filename if cv_attachment else None,
            "coverLetter": cover_letter.body,
        }

        return ApplicationResult(
            success=True, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
            payload=payload, attachments=attachments, notes=notes,
        )
