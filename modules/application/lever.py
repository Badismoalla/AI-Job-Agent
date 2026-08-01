"""
modules/application/lever.py
-------------------------------
Lever application-preparation adapter.

Not to be confused with modules/scraper/company/lever.py (fetches job
*listings*) — this prepares an application *payload*. See package
docstring.

supports(job) matches on URL: Lever-hosted job pages consistently live
under jobs.lever.co.

payload caveat: same as greenhouse.py — Lever's real apply-form submission
isn't a documented public API, and this module makes no network calls at
all. Field names here (name, email, phone, resume, comments/cover letter,
urls) match Lever's commonly-documented "postings apply" form fields, as a
structured, human-reviewable preparation artifact, not a verified
submission wire format.
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

_URL_MARKER = "jobs.lever.co"


class LeverAdapter(ApplicationAdapter):
    """Prepares a structured application payload for a Lever-hosted job."""

    adapter_name = "lever"

    def __init__(self, cv_path=None) -> None:
        self.cv_path = cv_path

    def supports(self, job: JobListing) -> bool:
        return _URL_MARKER in job.url.lower()

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
                error=f"Job URL doesn't look like a Lever listing: {job.url}",
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
            "name": info["full_name"],
            "email": info["email"],
            "phone": info["phone"],
            "resume": cv_attachment.filename if cv_attachment else None,
            "urls": {"linkedin": info["linkedin"]} if info["linkedin"] else {},
            "comments": cover_letter.body,
        }

        return ApplicationResult(
            success=True, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
            payload=payload, attachments=attachments, notes=notes,
        )
