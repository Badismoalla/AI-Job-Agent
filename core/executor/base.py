"""
core/executor/base.py
-----------------------
Abstract base for Phase 2D application executors.

execute() is a template method. Shared, platform-independent logic lives
here (idempotency, package validation, candidate-data checks, mode
gating, navigation retry, security detection, submission confirmation,
lifecycle coordination). Platform-specific behavior (which selectors to
fill, what "confirmed" looks like) is delegated to hook methods that
subclasses override.

Ordering (each step can short-circuit and return early):
    1. Idempotency check (durable tracker state) -- before any browser I/O
    2. Package validation
    3. Candidate data check (never fabricate missing info)
    4. PREPARE_ONLY stops here (no browser)
    5. Navigate (bounded retry on transient errors)
    6. Security checks (CAPTCHA / MFA) -- never bypassed
    7. Fill form (platform hook) -- unmapped/custom questions -> REVIEW_REQUIRED
    8. Pre-submit error-state check
    9. REVIEW_REQUIRED stops here (no submit)
   10. AUTO_SUBMIT clicks submit (exactly once, no blind retry)
   11. Detect confirmation (platform hook) -- never guessed
   12. On confirmed SUCCESS only: request the Phase 2C "submit" transition
       via the tracker; never mutate lifecycle state directly.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.exceptions import InvalidLifecycleTransitionError
from core.executor.states import ExecutionMode, ExecutionResult, ExecutionStatus
from core.executor.validator import PackageValidator
from core.lifecycle import JobLifecycleState
from core.models import JobListing

_TERMINAL_LIFECYCLE_STATES = {"rejected", "withdrawn", "expired", "closed"}

_CAPTCHA_SELECTOR = 'iframe[src*="recaptcha"], .g-recaptcha, [class*="captcha"]'
_MFA_SELECTOR = '[class*="mfa"], [class*="2fa"]'
_ERROR_SELECTOR = '[class*="error"], [class*="invalid"], [aria-invalid="true"]'

_SUCCESS_KEYWORDS = (
    "thank you",
    "thanks!",
    "application has been received",
    "successfully submitted",
    "we received your application",
    "application received",
)
_FAILURE_KEYWORDS = ("error", "invalid", "failed", "required field")

_MAX_NAVIGATION_ATTEMPTS = 3


class ApplicationExecutor(ABC):
    """Abstract executor for browser-based (or user-assisted) application submission."""

    platform_name: str = "unknown"

    # ── platform contract ────────────────────────────────────────────

    @abstractmethod
    def supports(self, job: JobListing) -> bool:
        """Pure predicate: can this executor handle this job? No I/O."""
        ...

    async def _fill_form(self, page: Any, job: JobListing, package_dir: Path, generator: Any | None = None) -> bool:
        """
        Fill platform-specific form fields. Returns True if any
        discovered application question remains unresolved
        (REVIEW_REQUIRED), in which case the caller must not submit.
        Subclasses override this.
        """
        raise NotImplementedError

    def _submit_selector(self) -> str:
        """Selector for the final submit control. Must contain 'submit'."""
        return 'button[type="submit"]'

    # ── shared template method ───────────────────────────────────────

    async def execute(
        self,
        job: JobListing,
        package_dir: Path,
        execution_mode: ExecutionMode,
        tracker: Any | None = None,
        page: Any | None = None,
        context: dict | None = None,
        generator: Any | None = None,
    ) -> ExecutionResult:
        base_kwargs = dict(
            job_id=job.id,
            package_id=package_dir.name,
            platform=self.platform_name,
            execution_mode=execution_mode,
        )

        # 1. Idempotency (durable state first)
        if tracker is not None:
            blocked = self._check_idempotency(tracker, job, base_kwargs)
            if blocked is not None:
                return blocked

        # 2. Package validation
        validation = PackageValidator().validate(package_dir, job)
        if not validation.valid:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.VALIDATION_FAILED,
                validation_errors=validation.errors,
                workflow_error=validation.reason,
            )

        # 3. Candidate data check (never fabricate)
        missing_field = self._check_candidate_data()
        if missing_field is not None:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.REVIEW_REQUIRED,
                error_reason=f"Missing required candidate information: {missing_field}",
                human_review_required=True,
            )

        # 4. PREPARE_ONLY stops here — no browser interaction at all
        if execution_mode == ExecutionMode.PREPARE_ONLY:
            return ExecutionResult(**base_kwargs, status=ExecutionStatus.SUCCESS)

        if page is None:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.BROWSER_ERROR,
                workflow_error="No browser page available for execution",
                retry_safe=False,
            )

        # 5. Navigate (bounded retry on transient errors)
        nav_error = await self._navigate_with_retry(page, job)
        if nav_error is not None:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.BROWSER_ERROR,
                workflow_error=str(nav_error),
                retry_safe=True,
            )

        # 6. Security checks — never bypassed
        security_status = await self._check_security(page)
        if security_status is not None:
            return ExecutionResult(
                **base_kwargs,
                status=security_status,
                human_review_required=True,
                retry_safe=False,
            )

        # 7. Fill form (platform hook)
        try:
            has_unmapped_questions = await self._fill_form(page, job, package_dir, generator)
        except Exception as e:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.UNSUPPORTED_FLOW,
                workflow_error=f"Form filling failed: {e}",
                human_review_required=True,
                retry_safe=False,
            )

        if has_unmapped_questions and execution_mode == ExecutionMode.AUTO_SUBMIT:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.REVIEW_REQUIRED,
                error_reason="Form contains a question with no answer source in the package",
                human_review_required=True,
            )

        # 8. Pre-submit error-state check
        error_element = await page.query_selector(_ERROR_SELECTOR)
        if error_element is not None:
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.FORM_ERROR,
                workflow_error="Form shows a validation error before submission",
                human_review_required=True,
                retry_safe=False,
            )

        # 9. REVIEW_REQUIRED stops here — form filled, never submitted
        if execution_mode == ExecutionMode.REVIEW_REQUIRED:
            return ExecutionResult(
                **base_kwargs, status=ExecutionStatus.USER_PAUSED,
                human_review_required=has_unmapped_questions,
            )

        # 10. AUTO_SUBMIT: click submit exactly once
        await page.click(self._submit_selector())

        # 11. Detect confirmation — never guessed
        result = await self._detect_confirmation(page, base_kwargs)

        # 12. Lifecycle transition only on confirmed SUCCESS
        if result.status == ExecutionStatus.SUCCESS and tracker is not None:
            result = self._request_applied_transition(tracker, job, result)

        return result

    # ── shared internals ─────────────────────────────────────────────

    def _check_idempotency(self, tracker: Any, job: JobListing, base_kwargs: dict) -> ExecutionResult | None:
        lifecycle = tracker.get_job_lifecycle(job.id)
        if lifecycle is not None:
            current_state = lifecycle.get("current_state")
            if current_state == "applied":
                return ExecutionResult(
                    **base_kwargs,
                    status=ExecutionStatus.NOT_AUTHORIZED,
                    error_reason="Job already in APPLIED state",
                )
            if current_state in _TERMINAL_LIFECYCLE_STATES:
                return ExecutionResult(
                    **base_kwargs,
                    status=ExecutionStatus.NOT_AUTHORIZED,
                    error_reason=f"Job is in a terminal lifecycle state: {current_state}",
                )
            if lifecycle.get("last_execution_status") == "submission_unknown":
                return ExecutionResult(
                    **base_kwargs,
                    status=ExecutionStatus.NOT_AUTHORIZED,
                    error_reason="Previous submission status is unknown; human review required before resubmission",
                    human_review_required=True,
                )

        if tracker.already_applied(job.id):
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.NOT_AUTHORIZED,
                error_reason="Application record already exists for this job (duplicate)",
            )

        return None

    def _check_candidate_data(self) -> str | None:
        """Returns the name of the first missing required field, or None if all present."""
        from modules.application.base import candidate_info

        info = candidate_info()
        if not info.get("full_name") and not info.get("first_name"):
            return "name"
        if not info.get("email"):
            return "email"
        return None

    async def _navigate_with_retry(self, page: Any, job: JobListing) -> Exception | None:
        last_error: Exception | None = None
        for _ in range(_MAX_NAVIGATION_ATTEMPTS):
            try:
                await page.goto(job.url)
                return None
            except Exception as e:  # noqa: BLE001 - navigation errors are broadly transient
                last_error = e
        return last_error

    async def _check_security(self, page: Any) -> ExecutionStatus | None:
        if await page.query_selector(_CAPTCHA_SELECTOR) is not None:
            return ExecutionStatus.CAPTCHA_REQUIRED
        if await page.query_selector(_MFA_SELECTOR) is not None:
            return ExecutionStatus.MFA_REQUIRED
        return None

    async def _detect_confirmation(self, page: Any, base_kwargs: dict) -> ExecutionResult:
        try:
            content = await page.content()
        except Exception as e:  # noqa: BLE001
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.SUBMISSION_UNKNOWN,
                workflow_error=f"Could not verify submission outcome: {e}",
                human_review_required=True,
                retry_safe=False,
            )

        lowered = (content or "").lower()

        if any(kw in lowered for kw in _SUCCESS_KEYWORDS):
            external_id = self._extract_application_id(content)
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.SUCCESS,
                external_application_id=external_id,
                submission_url=getattr(page, "url", None),
                confirmation_details={"body": content},
            )

        if any(kw in lowered for kw in _FAILURE_KEYWORDS):
            return ExecutionResult(
                **base_kwargs,
                status=ExecutionStatus.SUBMISSION_FAILED,
                workflow_error="Form reported an error after submission",
            )

        return ExecutionResult(
            **base_kwargs,
            status=ExecutionStatus.SUBMISSION_UNKNOWN,
            workflow_error="No reliable confirmation signal found after submission",
            human_review_required=True,
            retry_safe=False,
        )

    def _extract_application_id(self, content: str) -> str | None:
        import re

        match = re.search(r"(?:application id|reference id)\s*[:#]?\s*([A-Za-z0-9\-]+)", content, re.IGNORECASE)
        return match.group(1) if match else None

    # ── Question discovery / answering / write-back ──────────────────
    #
    # Shared across all browser executors. See modules/ai/question_answerer.py
    # for the safety-first answering logic.
    #
    # Discovery is fail-closed: "found zero recognized custom-question
    # elements" is NOT treated as proof the form has no questions. A
    # broader scan for ANY unrecognized input/textarea/select field
    # (beyond the platform's known standard fields and the recognized
    # custom-question convention) provides positive evidence either way.
    # If evidence of unrecognized fields exists, or a recognized field's
    # label/type cannot be safely extracted, the result is
    # DISCOVERY_UNCERTAIN -> REVIEW_REQUIRED, never a silent "proceed".
    #
    # The recognized-question DOM convention (name*="custom_question",
    # optional data-label/data-field-type/data-options attributes, with
    # real-DOM fallback via tag name / input type / associated <label>)
    # has NOT been verified against any real live ATS platform's actual
    # DOM (network-restricted sandbox) -- see the Phase 2F report.
    #
    # Radio buttons sharing one `name` are grouped into a single
    # question with accumulated options, not one question per button.
    #
    # "file" and multi-element checkbox groups are recognized field
    # types but always resolve to REVIEW_REQUIRED -- no safe automated
    # answer exists for an arbitrary file-upload question, and this
    # project's answer model only supports a single value per question
    # (not a multi-select).

    _KNOWN_FIELD_TYPES = ("text", "textarea", "yes_no", "select", "checkbox", "number", "file")

    async def _discover_and_resolve_custom_questions(
        self,
        page: Any,
        job: JobListing,
        package_dir: Path,
        known_field_names: tuple[str, ...] = (),
        generator: Any | None = None,
        custom_question_selector: str = '[name*="custom_question"]',
    ) -> bool:
        from modules.ai.question_answerer import ApplicationQuestion, QuestionAnswerer

        custom_elements = await page.query_selector_all(custom_question_selector)
        grouped, extraction_uncertain = await self._extract_and_group_questions(page, custom_elements)

        unrecognized_extra = await self._scan_for_unrecognized_fields(
            page, known_field_names, custom_question_selector,
        )

        if not grouped and not unrecognized_extra and not extraction_uncertain:
            self._write_application_answers(package_dir, status="no_questions_found", questions=[])
            return False

        if extraction_uncertain or unrecognized_extra:
            uncertain_records = [
                {
                    "question": g.get("label") or f"(unlabeled field: {g['name']})",
                    "field_type": g.get("field_type"),
                    "answer": None,
                    "provenance": "review_required",
                    "review_required": True,
                    "reason": "Could not safely extract this question's label/type.",
                }
                for g in grouped if g.get("uncertain")
            ]
            reason = (
                f"Discovery uncertain: found {len(unrecognized_extra)} unrecognized form "
                f"field(s) ({unrecognized_extra}) and/or could not safely parse "
                f"{len(uncertain_records)} custom question(s). Treating as unresolved "
                f"rather than assuming no questions exist."
            )
            self._write_application_answers(
                package_dir, status="discovery_uncertain", questions=uncertain_records, reason=reason,
            )
            return True

        if generator is None:
            from modules.ai.claude_generator import ClaudeGenerator
            generator = ClaudeGenerator()
        answerer = QuestionAnswerer(generator=generator)

        records: list[dict] = []
        has_unresolved = False

        for g in grouped:
            if g["field_type"] == "file":
                # A "file" custom question (e.g. transcripts, recommendation
                # letters) has no safe automated answer -- this project
                # never fabricates or reuses an arbitrary attachment for
                # an unknown question. Always REVIEW_REQUIRED.
                records.append({
                    "question": g["label"],
                    "field_type": "file",
                    "answer": None,
                    "provenance": "review_required",
                    "review_required": True,
                    "reason": "File-upload custom questions require a human to attach the correct document.",
                })
                has_unresolved = True
                continue

            question = ApplicationQuestion(
                label=g["label"], field_type=g["field_type"], options=g["options"] or None,
            )
            result = await answerer.answer(question, job)

            record = {
                "question": g["label"],
                "field_type": g["field_type"],
                "answer": result.value,
                "provenance": result.provenance.value,
                "review_required": result.review_required,
            }
            if result.reason:
                record["reason"] = result.reason
            records.append(record)

            if result.review_required:
                has_unresolved = True
                continue

            await self._fill_question_answer(page, g["name"], g["field_type"], result.value)

        self._write_application_answers(package_dir, status="answers_generated", questions=records)
        return has_unresolved

    async def _extract_and_group_questions(self, page: Any, elements: list) -> tuple[list[dict], bool]:
        """
        Groups elements sharing a `name` (radio buttons) into one question
        spec each. Returns (groups, any_extraction_uncertain).
        """
        groups: dict[str, dict | None] = {}
        order: list[str] = []
        any_uncertain = False

        for element in elements:
            name_attr = await element.get_attribute("name")
            if not name_attr:
                any_uncertain = True
                continue

            field_type = await self._infer_field_type(element)
            if field_type is None:
                any_uncertain = True
                if name_attr not in groups:
                    groups[name_attr] = {"name": name_attr, "label": None, "field_type": None, "uncertain": True}
                    order.append(name_attr)
                continue

            if name_attr not in groups:
                label = await self._extract_label(page, element)
                if not label:
                    any_uncertain = True
                    groups[name_attr] = {"name": name_attr, "label": None, "field_type": field_type, "uncertain": True}
                    order.append(name_attr)
                    continue
                groups[name_attr] = {
                    "name": name_attr, "label": label, "field_type": field_type,
                    "options": [], "uncertain": False, "element_count": 0,
                }
                order.append(name_attr)

            group = groups[name_attr]
            if group is None or group.get("uncertain"):
                continue

            group["element_count"] = group.get("element_count", 0) + 1

            if field_type == "select":
                group["options"] = await self._extract_select_options(element)
            elif field_type == "yes_no":
                await self._accumulate_yes_no_option(element, group)

        # A checkbox "group" with more than one constituent element is a
        # real multi-select pattern (confirmed via live Lever/Greenhouse
        # forms during the Phase 2F Step 2.5 audit: e.g. "select all
        # cities that apply"). This project's answer model only supports
        # a single value per question -- fail closed rather than
        # mis-toggle an arbitrary subset or throw an ambiguous-selector
        # error deep in Playwright.
        for name in order:
            g = groups[name]
            if g is not None and not g.get("uncertain") and g["field_type"] == "checkbox" and g.get("element_count", 0) > 1:
                g["uncertain"] = True
                any_uncertain = True

        resolved = [groups[name] for name in order]
        return resolved, any_uncertain

    async def _infer_field_type(self, element: Any) -> str | None:
        """
        Prefers the data-field-type hint if present and recognized (the
        test-fixture convention); falls back to real DOM signals (tag
        name, input type attribute) for elements without that hint --
        the realistic case for an actual live ATS platform.
        """
        hint = await element.get_attribute("data-field-type")
        if hint in self._KNOWN_FIELD_TYPES:
            return hint

        try:
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = None

        if tag == "textarea":
            return "textarea"
        if tag == "select":
            return "select"
        if tag == "input":
            input_type = await element.get_attribute("type")
            if input_type == "radio":
                return "yes_no"
            if input_type == "checkbox":
                return "checkbox"
            if input_type == "number":
                return "number"
            if input_type == "file":
                return "file"
            return "text"

        return None

    async def _extract_label(self, page: Any, element: Any) -> str | None:
        for attr in ("data-label", "aria-label", "placeholder"):
            value = await element.get_attribute(attr)
            if value:
                return value

        element_id = await element.get_attribute("id")
        if element_id:
            try:
                label_el = await page.query_selector(f'label[for="{element_id}"]')
            except Exception:
                label_el = None
            if label_el is not None:
                try:
                    text = await label_el.inner_text()
                except Exception:
                    text = None
                if text:
                    return text.strip()

        return None

    async def _extract_select_options(self, element: Any) -> list[str]:
        options_raw = await element.get_attribute("data-options")
        if options_raw:
            parsed = self._parse_options_json(options_raw)
            if parsed is not None:
                return parsed

        try:
            option_elements = await element.query_selector_all("option")
        except Exception:
            option_elements = []
        options = []
        for opt in option_elements:
            val = await opt.get_attribute("value")
            if not val:
                try:
                    val = await opt.inner_text()
                except Exception:
                    val = None
            if val:
                options.append(val)
        return options

    async def _accumulate_yes_no_option(self, element: Any, group: dict) -> None:
        options_raw = await element.get_attribute("data-options")
        if options_raw and not group["options"]:
            parsed = self._parse_options_json(options_raw)
            if parsed is not None:
                group["options"] = parsed

        value = await element.get_attribute("value")
        if value and value not in group["options"]:
            group["options"].append(value)

    @staticmethod
    def _parse_options_json(raw: str) -> list[str] | None:
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                return [str(o) for o in parsed]
        except (ValueError, TypeError):
            pass
        return None

    async def _scan_for_unrecognized_fields(
        self, page: Any, known_field_names: tuple[str, ...], custom_question_selector: str,
    ) -> list[str]:
        """
        Positive evidence check: any input/textarea/select on the page
        whose name isn't one of the platform's known standard fields and
        doesn't match the platform's recognized custom-question
        convention. Presence of any such field means discovery cannot be
        trusted as complete.
        """
        try:
            all_fields = await page.query_selector_all("input, textarea, select")
            recognized_custom = await page.query_selector_all(custom_question_selector)
        except Exception:
            return []

        recognized_names = set()
        for el in recognized_custom:
            n = await el.get_attribute("name")
            if n:
                recognized_names.add(n)

        unrecognized = []
        for element in all_fields:
            name = await element.get_attribute("name")
            if not name:
                continue
            if name in known_field_names:
                continue
            if name in recognized_names:
                continue
            unrecognized.append(name)
        return unrecognized

    async def _fill_question_answer(self, page: Any, name_attr: str, field_type: str, value: str) -> None:
        selector = f'[name="{name_attr}"]'
        if field_type in ("text", "number", "textarea"):
            await page.fill(selector, value)
        elif field_type == "select":
            await page.select_option(selector, value)
        elif field_type == "yes_no":
            await page.check(f'{selector}[value="{value}"]')
        elif field_type == "checkbox":
            if str(value).lower() in ("yes", "true"):
                await page.check(selector)
            else:
                await page.uncheck(selector)

    def _write_application_answers(
        self, package_dir: Path, status: str, questions: list[dict], reason: str | None = None,
    ) -> None:
        import json as _json
        path = package_dir / "application_answers.json"
        data = {"status": status, "questions": questions}
        if reason:
            data["reason"] = reason
        path.write_text(_json.dumps(data, indent=2), encoding="utf-8")

    def _request_applied_transition(self, tracker: Any, job: JobListing, result: ExecutionResult) -> ExecutionResult:
        data = {}
        if result.external_application_id:
            data["external_application_id"] = result.external_application_id
        if result.submission_url:
            data["submission_url"] = result.submission_url

        try:
            tracker.update_job_lifecycle_state(
                job_id=job.id,
                event="submit",
                next_state=JobLifecycleState.APPLIED,
                data=data or None,
            )
        except InvalidLifecycleTransitionError as e:
            result.status = ExecutionStatus.SUBMISSION_UNKNOWN
            result.workflow_error = f"Submission succeeded but lifecycle transition failed: {e}"
            result.human_review_required = True

        return result
