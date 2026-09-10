"""
core/executor/email_executor.py
----------------------------------
User-assisted email submission executor. No browser is ever launched —
this repo has no credentialed email-send path, so a human must always
be the one to actually send the message.

Design decision (resolved during Step 3 review): EmailExecutor never
derives a recipient address from the job listing, mirroring
EmailAdapter.prepare()'s own rule that recipient_email must be supplied
explicitly and is never guessed (e.g. "careers@{company}.com"). A
recipient must be passed via execute(..., context={"recipient_email": ...}).
If absent, the result is ExecutionStatus.REVIEW_REQUIRED, not a fabricated
address.

No mode can result in SUCCESS for Email (except PREPARE_ONLY, which just
means "content prepared"), because there is no way for this executor to
confirm a human actually sent the message. AUTO_SUBMIT and REVIEW_REQUIRED
both stop at ExecutionStatus.USER_PAUSED.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.executor.base import ApplicationExecutor
from core.executor.states import ExecutionMode, ExecutionResult, ExecutionStatus
from core.executor.validator import PackageValidator
from core.models import JobListing


class EmailExecutor(ApplicationExecutor):
    platform_name = "email"

    def supports(self, job: JobListing) -> bool:
        # Universal fallback, mirroring EmailAdapter.supports().
        return True

    async def execute(
        self,
        job: JobListing,
        package_dir: Path,
        execution_mode: ExecutionMode,
        tracker: Any | None = None,
        page: Any | None = None,
        context: dict | None = None,
    ) -> ExecutionResult:
        base_kwargs = dict(
            job_id=job.id,
            package_id=package_dir.name,
            platform=self.platform_name,
            execution_mode=execution_mode,
        )

        if tracker is not None:
            blocked = self._check_idempotency(tracker, job, base_kwargs)
            if blocked is not None:
                return blocked

        validation = PackageValidator().validate(package_dir, job)
        if not validation.valid:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.VALIDATION_FAILED,
                validation_errors=validation.errors,
                workflow_error=validation.reason,
            )

        recipient_email = (context or {}).get("recipient_email")
        if not recipient_email:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.REVIEW_REQUIRED,
                error_reason="No recipient email address supplied; never guessed from the job listing",
                human_review_required=True,
            )

        cover_letter = (package_dir / "cover_letter.md").read_text(encoding="utf-8")
        recruiter_message = (package_dir / "recruiter_message.md").read_text(encoding="utf-8")
        body = f"{cover_letter}\n\n---\n\n{recruiter_message}"
        subject = f"Application - {job.title} | {job.company}"

        confirmation_details = {
            "to": recipient_email,
            "subject": subject,
            "body": body,
        }

        if execution_mode == ExecutionMode.PREPARE_ONLY:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.SUCCESS,
                confirmation_details=confirmation_details,
            )

        # REVIEW_REQUIRED and AUTO_SUBMIT both stop here: no credentialed
        # send path exists, so a human must send the email themselves.
        return ExecutionResult(
            **base_kwargs,
            status=ExecutionStatus.USER_PAUSED,
            confirmation_details=confirmation_details,
            workflow_error="Email prepared; a human must send it manually (no auto-send capability)",
        )
