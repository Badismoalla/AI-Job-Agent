"""
modules/application/greenhouse.py
------------------------------------
Greenhouse application-preparation adapter.

Not to be confused with modules/scraper/company/greenhouse.py (fetches
job *listings* from Greenhouse's public search API) — this module
prepares an application *payload* for a Greenhouse-hosted job the
candidate has decided to apply to. See package docstring for why these
are deliberately separate concerns.

supports(job) matches on URL: Greenhouse-hosted job pages consistently
live under boards.greenhouse.io or job-boards.greenhouse.io — this is a
reliable, verifiable signal (it's literally the URL every Greenhouse job
listing has), unlike attempting to guess a submission API shape.

payload caveat: Greenhouse's actual "Apply" form (rendered client-side on
the job page) is not a documented public API the way its job-search API
is — submitting to it for real would require obtaining a session/CSRF
token from the live apply page, which this module deliberately does not
do (no network I/O at all — see package docstring). The payload built
here uses field names matching Greenhouse's commonly-documented applicant
fields (first_name, last_name, email, phone, resume, cover_letter) as a
structured, human-reviewable preparation artifact — not a verified,
submission-ready wire format.
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

_URL_MARKERS = ("boards.greenhouse.io", "job-boards.greenhouse.io")


class GreenhouseAdapter(ApplicationAdapter):
    """Prepares a structured application payload for a Greenhouse-hosted job."""

    adapter_name = "greenhouse"

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
                error=f"Job URL doesn't look like a Greenhouse listing: {job.url}",
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
            "first_name": info["first_name"],
            "last_name": info["last_name"],
            "email": info["email"],
            "phone": info["phone"],
            "resume": cv_attachment.filename if cv_attachment else None,
            "cover_letter": cover_letter.body,
        }

        return ApplicationResult(
            success=True, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
            payload=payload, attachments=attachments, notes=notes,
        )
