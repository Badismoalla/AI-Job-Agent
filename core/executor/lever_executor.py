"""
core/executor/lever_executor.py
----------------------------------
Browser executor for Lever (jobs.lever.co).

Field selectors mirror the real LeverAdapter payload
(modules/application/lever.py), verified against source:
    name (single field, NOT split first/last), email, phone,
    urls.linkedin, resume, comments (NOT "cover_letter")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.executor.base import ApplicationExecutor
from core.models import JobListing

_URL_MARKER = "jobs.lever.co"


class LeverExecutor(ApplicationExecutor):
    platform_name = "lever"

    _KNOWN_FIELD_NAMES = ("name", "email", "phone", "comments", "resume")

    def supports(self, job: JobListing) -> bool:
        return _URL_MARKER in job.url.lower()

    def _submit_selector(self) -> str:
        return 'button[type="submit"]'

    async def _fill_form(self, page: Any, job: JobListing, package_dir: Path, generator: Any | None = None) -> bool:
        from modules.application.base import candidate_info

        info = candidate_info()
        recruiter_message = (package_dir / "recruiter_message.md").read_text(encoding="utf-8")

        await page.fill('input[name="name"]', info["full_name"])
        await page.fill('input[name="email"]', info["email"])
        await page.fill('input[name="phone"]', info.get("phone", ""))
        await page.fill('textarea[name="comments"]', recruiter_message)

        cv_path = package_dir / "cv.pdf"
        if cv_path.exists():
            await page.set_input_files('input[name="resume"]', str(cv_path))

        return await self._discover_and_resolve_custom_questions(
            page, job, package_dir,
            known_field_names=self._KNOWN_FIELD_NAMES, generator=generator,
        )
