"""
modules/application/workday.py
---------------------------------
Workday application-preparation adapter.

Not to be confused with modules/scraper/company/workday.py (fetches job
*listings* via Workday's CXS search endpoint) — this prepares an
application *payload*. See package docstring.

supports(job) matches on URL: Workday-hosted job pages consistently live
under *.myworkdayjobs.com.

payload caveat, stronger than the other three ATS adapters: Workday's real
"Apply" flow is not a single form submission at all — it's a multi-step
wizard (account creation/login, personal info, work experience, education,
voluntary disclosures, review) spread across several pages, each often
requiring its own request. There is no single flat payload that
faithfully represents "a Workday application". What's built here is
therefore intentionally scoped down further than the other adapters: the
core candidate-facing fields (name/contact info) and the cover letter
text, labeled clearly as a *first-step preparation artifact* for a human
to carry into that wizard manually — not a claim that this is what
Workday's form actually looks like end to end.
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

_URL_MARKER = "myworkdayjobs.com"


class WorkdayAdapter(ApplicationAdapter):
    """
    Prepares a first-step application payload for a Workday-hosted job —
    see module docstring for why this is intentionally partial (Workday's
    real apply flow is a multi-step wizard, not a single form).
    """

    adapter_name = "workday"

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
                error=f"Job URL doesn't look like a Workday listing: {job.url}",
            )

        cover_letter = find_message(messages, MessageType.COVER_LETTER)
        if cover_letter is None:
            return ApplicationResult(
                success=False, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
                error="No COVER_LETTER message found in the provided messages.",
            )

        notes: list[str] = [
            "Workday applications are a multi-step wizard on the live site — this payload "
            "covers only the initial candidate-info + cover letter step, for manual completion.",
        ]
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
            "legalName": {"firstName": info["first_name"], "lastName": info["last_name"]},
            "email": info["email"],
            "phone": info["phone"],
            "resume": cv_attachment.filename if cv_attachment else None,
            "coverLetter": cover_letter.body,
        }

        return ApplicationResult(
            success=True, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
            payload=payload, attachments=attachments, notes=notes,
        )
