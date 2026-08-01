"""
modules/application/email.py
-------------------------------
Email application adapter — prepares an email application only. Never
connects to an SMTP server, never sends anything (see package docstring:
that's a deliberate scope boundary of this whole package, not this file
specifically).

Email is treated as the universal fallback channel: supports() returns
True for every job, since applying by email doesn't structurally depend
on which board/ATS a job came from — the only thing it depends on is
having a recipient address, which prepare() checks explicitly rather than
supports() guessing at it. JobListing doesn't carry a reliable "apply by
email" address across sources, so this adapter never invents one (e.g. by
guessing "careers@{company}.com", which would frequently be wrong) —
callers supply `recipient_email` explicitly to prepare(), typically
sourced from a company's published HR contact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import GeneratedMessage, JobListing, MessageType
from modules.application.base import (
    ApplicationAdapter,
    ApplicationResult,
    candidate_info,
    find_message,
    materialize_cover_letter_attachment,
    resolve_cv_attachment,
)

_DEFAULT_OUTPUT_DIR = Path("output/applications")


class EmailAdapter(ApplicationAdapter):
    """Prepares an email application: to/subject/body + attachments."""

    adapter_name = "email"

    def __init__(self, cv_path: Path | None = None, output_dir: Path | None = None) -> None:
        """
        Args:
            cv_path: Path to the candidate's CV file. If it doesn't exist
                (or isn't given), prepare() still succeeds but notes the
                missing CV rather than failing outright — a human
                reviewing the result can decide whether that's acceptable.
            output_dir: Where a materialized cover-letter attachment is
                written when dry_run=False. Defaults to output/applications/.
        """
        self.cv_path = cv_path
        self.output_dir = output_dir or _DEFAULT_OUTPUT_DIR

    def supports(self, job: JobListing) -> bool:
        return True

    def prepare(
        self,
        job: JobListing,
        messages: list[GeneratedMessage],
        dry_run: bool = True,
        recipient_email: str | None = None,
        attach_cover_letter: bool = True,
        cc: list[str] | None = None,
        **kwargs: Any,
    ) -> ApplicationResult:
        """
        Args:
            recipient_email: Where the application would be sent. Required
                — without it, preparation fails cleanly (success=False)
                rather than guessing an address.
            attach_cover_letter: If True, materialize the cover letter
                body as a .txt attachment (see materialize_cover_letter_
                attachment — respects dry_run for whether it's actually
                written to disk).
            cc: Optional CC list for the prepared email.
        """
        if not recipient_email:
            return ApplicationResult(
                success=False, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
                error="No recipient_email provided — cannot prepare an email application without one.",
            )

        body_message = find_message(messages, MessageType.COVER_LETTER, MessageType.HR_EMAIL)
        if body_message is None:
            return ApplicationResult(
                success=False, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
                error="No COVER_LETTER or HR_EMAIL message found in the provided messages.",
            )

        notes: list[str] = []
        attachments = []

        cv_attachment = resolve_cv_attachment(self.cv_path)
        if cv_attachment is None:
            notes.append("No CV path configured — prepared without a CV attachment.")
        elif not cv_attachment.exists:
            notes.append(f"CV path configured but file not found: {cv_attachment.path}")
            attachments.append(cv_attachment)
        else:
            attachments.append(cv_attachment)

        if attach_cover_letter:
            cover_letter_attachment = materialize_cover_letter_attachment(
                body_message, job, self.output_dir, dry_run=dry_run,
            )
            attachments.append(cover_letter_attachment)
            if dry_run:
                notes.append(f"Dry run — cover letter would be written to {cover_letter_attachment.path}")

        sender_name = candidate_info()["full_name"]
        subject = body_message.subject or f"Application – {job.title} | {sender_name}"

        payload = {
            "to": recipient_email,
            "cc": cc or [],
            "subject": subject,
            "body": body_message.body,
        }

        return ApplicationResult(
            success=True, adapter_name=self.adapter_name, job_id=job.id, dry_run=dry_run,
            payload=payload, attachments=attachments, notes=notes,
        )
