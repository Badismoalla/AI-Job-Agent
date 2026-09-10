"""
core/executor/greenhouse_executor.py
--------------------------------------
Browser executor for Greenhouse (boards.greenhouse.io / job-boards.greenhouse.io).

Field selectors mirror the real GreenhouseAdapter payload
(modules/application/greenhouse.py), verified against source:
    first_name, last_name, email, phone, resume, cover_letter
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.executor.base import ApplicationExecutor
from core.models import JobListing

_URL_MARKERS = ("boards.greenhouse.io", "job-boards.greenhouse.io")


class GreenhouseExecutor(ApplicationExecutor):
    platform_name = "greenhouse"

    _KNOWN_FIELD_NAMES = (
        "job_application[first_name]", "job_application[last_name]",
        "job_application[email]", "job_application[phone]",
        "job_application[cover_letter]", "job_application[resume]",
    )

    def supports(self, job: JobListing) -> bool:
        return any(marker in job.url.lower() for marker in _URL_MARKERS)

    def _submit_selector(self) -> str:
        return '#submit_app'

    async def _fill_form(self, page: Any, job: JobListing, package_dir: Path, generator: Any | None = None) -> bool:
        from modules.application.base import candidate_info

        info = candidate_info()
        cover_letter = (package_dir / "cover_letter.md").read_text(encoding="utf-8")

        await page.fill('input[name="job_application[first_name]"]', info["first_name"])
        await page.fill('input[name="job_application[last_name]"]', info["last_name"])
        await page.fill('input[name="job_application[email]"]', info["email"])
        await page.fill('input[name="job_application[phone]"]', info.get("phone", ""))
        await page.fill('textarea[name="job_application[cover_letter]"]', cover_letter)

        cv_path = package_dir / "cv.pdf"
        if cv_path.exists():
            await page.set_input_files('input[name="job_application[resume]"]', str(cv_path))

        return await self._discover_and_resolve_custom_questions(
            page, job, package_dir,
            known_field_names=self._KNOWN_FIELD_NAMES, generator=generator,
        )
