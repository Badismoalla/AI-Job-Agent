"""
core/executor/smartrecruiters_executor.py
--------------------------------------------
Browser executor for SmartRecruiters (jobs.smartrecruiters.com /
careers.smartrecruiters.com).

Field selectors mirror the real SmartRecruitersAdapter payload
(modules/application/smartrecruiters.py), verified against source:
    firstName, lastName, email, phone, resumeFileName, coverLetter
(camelCase — distinct from Greenhouse's snake_case and Lever's single
name/comments fields; do not assume they share a convention.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.executor.base import ApplicationExecutor
from core.models import JobListing

_URL_MARKERS = ("jobs.smartrecruiters.com", "careers.smartrecruiters.com")


class SmartRecruitersExecutor(ApplicationExecutor):
    platform_name = "smartrecruiters"

    _KNOWN_FIELD_NAMES = ("firstName", "lastName", "email", "phone", "coverLetter", "resumeFileName")

    def supports(self, job: JobListing) -> bool:
        return any(marker in job.url.lower() for marker in _URL_MARKERS)

    def _submit_selector(self) -> str:
        return 'button[type="submit"]'

    async def _fill_form(self, page: Any, job: JobListing, package_dir: Path, generator: Any | None = None) -> bool:
        from modules.application.base import candidate_info

        info = candidate_info()
        cover_letter = (package_dir / "cover_letter.md").read_text(encoding="utf-8")

        await page.fill('input[name="firstName"]', info["first_name"])
        await page.fill('input[name="lastName"]', info["last_name"])
        await page.fill('input[name="email"]', info["email"])
        await page.fill('input[name="phone"]', info.get("phone", ""))
        await page.fill('textarea[name="coverLetter"]', cover_letter)

        cv_path = package_dir / "cv.pdf"
        if cv_path.exists():
            await page.set_input_files('input[name="resumeFileName"]', str(cv_path))

        return await self._discover_and_resolve_custom_questions(
            page, job, package_dir,
            known_field_names=self._KNOWN_FIELD_NAMES, generator=generator,
        )
