"""
modules/application/base.py
------------------------------
Shared interface and helpers for every application-preparation adapter.

ApplicationAdapter is deliberately small: two methods.
- supports(job) -> bool           : can this adapter handle this listing?
- prepare(job, messages, ...)     : build an ApplicationResult — never sends

There is no network I/O anywhere in this package (see package docstring),
so prepare() is synchronous, unlike the async scrapers/AI generator
elsewhere in this project — there's genuinely nothing to await.

"Reuse AttachmentBuilder if needed": no such module exists elsewhere in
this project yet, and the requested file list for this package doesn't
include one, so the minimal attachment-resolution pieces adapters actually
need (AttachmentSpec, resolve_cv_attachment, materialize_cover_letter_
attachment) live here instead of as a separate speculative module.

On dry_run: unlike elsewhere in this project (e.g. ClaudeGenerator, where
dry_run toggles whether a real external API call happens), no adapter here
ever performs a real external action regardless of dry_run — that's an
absolute constraint, not a mode. dry_run instead controls local filesystem
side effects: whether materialize_cover_letter_attachment() actually
writes the cover letter to disk (dry_run=True: describe the path it would
write to, without writing; dry_run=False: write it for real, so a human
can attach the file to a real, manually-sent application). Reading an
already-existing CV file's path is not a side effect, so that happens
either way.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slugify import slugify

from core.models import GeneratedMessage, JobListing, MessageType
from core.profile import profile


# ── Attachments ──────────────────────────────────────────────────────────────

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


@dataclass
class AttachmentSpec:
    """One file to attach to a prepared application."""

    path: Path
    filename: str
    content_type: str
    exists: bool


def resolve_cv_attachment(cv_path: Path | None) -> AttachmentSpec | None:
    """
    Resolve a candidate's CV file into an AttachmentSpec. Returns None if
    no path was given at all (caller decides whether that's an error —
    some adapters can still prepare a partial application without a CV,
    flagged via ApplicationResult.notes).

    Does not read or write the file — `exists` just reports whether it's
    there right now, so adapters can note a missing CV without raising.
    """
    if cv_path is None:
        return None
    return AttachmentSpec(
        path=cv_path,
        filename=cv_path.name,
        content_type=_CONTENT_TYPES.get(cv_path.suffix.lower(), "application/octet-stream"),
        exists=cv_path.exists(),
    )


def materialize_cover_letter_attachment(
    message: GeneratedMessage,
    job: JobListing,
    output_dir: Path,
    dry_run: bool = True,
) -> AttachmentSpec:
    """
    Turn a GeneratedMessage's body into a cover-letter .txt attachment.

    dry_run=True (default): compute the path that *would* be written, but
    don't touch the filesystem — `exists` reflects whatever's already
    there (almost always False for a fresh run).
    dry_run=False: actually write message.body to that path.
    """
    filename = f"{slugify(f'{job.company}-{job.title}', separator='-')}-cover-letter.txt"
    path = output_dir / filename

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(message.body, encoding="utf-8")

    return AttachmentSpec(
        path=path, filename=filename, content_type="text/plain", exists=path.exists(),
    )


def find_message(messages: list[GeneratedMessage], *types: MessageType) -> GeneratedMessage | None:
    """
    Find the first message matching any of the given types, checked in
    priority order (all messages checked against types[0] first, then
    types[1], etc.) — not just "first message in the list".
    """
    for message_type in types:
        for message in messages:
            if message.type == message_type:
                return message
    return None


def candidate_info() -> dict[str, str]:
    """
    Candidate contact fields shared by every ATS adapter's payload, pulled
    from the profile singleton (data/profile.json) — first/last name split
    naively on the first space, which is a reasonable simplification for
    this candidate's single-space "First Last" name and is noted here in
    case it's ever wrong for a different name format.
    """
    full_name = profile.name()
    first_name, _, last_name = full_name.partition(" ")
    return {
        "first_name": first_name,
        "last_name": last_name or first_name,
        "full_name": full_name,
        "email": profile.email(),
        "phone": profile.personal.get("phone", ""),
        "linkedin": profile.personal.get("linkedin", ""),
        "location": profile.personal.get("location", ""),
    }


# ── Result ───────────────────────────────────────────────────────────────────

@dataclass
class ApplicationResult:
    """
    What one adapter's prepare() call produced — always returned, whether
    preparation succeeded or not (check `success`/`error`, don't except).
    """

    success: bool
    adapter_name: str
    job_id: str
    dry_run: bool
    payload: dict[str, Any] = field(default_factory=dict)
    attachments: list[AttachmentSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    prepared_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Adapter interface ────────────────────────────────────────────────────────

class ApplicationAdapter(ABC):
    """
    Common interface every application-preparation adapter implements.

    Usage:
        adapter = GreenhouseAdapter()
        if adapter.supports(job):
            result = adapter.prepare(job, messages, dry_run=True)
    """

    adapter_name: str = "unknown"

    @abstractmethod
    def supports(self, job: JobListing) -> bool:
        """Can this adapter handle this job listing? Pure predicate — no
        side effects, safe to call speculatively against many adapters."""
        ...

    @abstractmethod
    def prepare(
        self,
        job: JobListing,
        messages: list[GeneratedMessage],
        dry_run: bool = True,
        **kwargs: Any,
    ) -> ApplicationResult:
        """
        Build an ApplicationResult for this job. Never sends/submits
        anything — see module and package docstrings. Should not raise for
        ordinary "can't prepare this" conditions (missing data, no
        matching message type, etc.) — return success=False with `error`
        set instead; raising is reserved for genuine programming errors.
        """
        ...
