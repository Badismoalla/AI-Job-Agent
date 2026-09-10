"""
Phase 2D: Application package validation tests.

Tests for PackageValidator ensuring packages are valid and complete before
browser execution. Fixtures use the ACTUAL on-disk package shape produced
by modules.package.writer.PackageWriter, verified against source:

    metadata.json = {"generated_at": ..., "job": <JobListing dump>, "match_report": {...}}

Job identity is therefore checked via metadata["job"]["id"], not a flat
"job_id" key.
"""

import json

import pytest

try:
    from core.executor.validator import PackageValidator
    from core.executor.states import ValidationResult
except ImportError:
    pytest.skip("core/executor not yet implemented", allow_module_level=True)


@pytest.fixture
def validator():
    return PackageValidator()


class TestPackageValidationBasics:
    """Tests for basic package structure validation."""

    def test_validate_complete_valid_package(self, validator, valid_package_dir, greenhouse_job):
        """Valid complete package passes validation."""
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is True
        assert result.errors == []

    def test_validate_missing_package_directory(self, validator, greenhouse_job, tmp_path):
        """Missing package directory fails validation."""
        missing_dir = tmp_path / "does-not-exist"
        result = validator.validate(missing_dir, greenhouse_job)
        assert result.valid is False
        assert any("exist" in err.lower() for err in result.errors)

    def test_validate_missing_metadata_json(self, validator, valid_package_dir, greenhouse_job):
        """Missing metadata.json fails validation."""
        (valid_package_dir / "metadata.json").unlink()
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("metadata" in err.lower() for err in result.errors)

    def test_validate_invalid_metadata_json(self, validator, valid_package_dir, greenhouse_job):
        """Invalid JSON in metadata fails validation."""
        (valid_package_dir / "metadata.json").write_text("not valid json {]")
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("json" in err.lower() or "metadata" in err.lower() for err in result.errors)

    def test_validate_wrong_job_id_in_metadata(self, validator, valid_package_dir, greenhouse_job):
        """
        Package whose metadata['job']['id'] does not match the job being
        executed fails validation. This is the cross-application guard:
        it must be impossible to submit package A's content for job B.
        """
        meta = json.loads((valid_package_dir / "metadata.json").read_text())
        meta["job"]["id"] = "some-other-job-id"
        (valid_package_dir / "metadata.json").write_text(json.dumps(meta))

        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("job" in err.lower() and "id" in err.lower() for err in result.errors)

    def test_validate_metadata_missing_job_key_entirely(self, validator, valid_package_dir, greenhouse_job):
        """metadata.json missing the 'job' key entirely fails validation (not silently skipped)."""
        meta = json.loads((valid_package_dir / "metadata.json").read_text())
        del meta["job"]
        (valid_package_dir / "metadata.json").write_text(json.dumps(meta))

        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("job" in err.lower() for err in result.errors)


class TestPackageValidationFiles:
    """Tests for required file presence and content."""

    @pytest.mark.parametrize(
        "filename,keyword",
        [
            ("cover_letter.md", "cover"),
            ("recruiter_message.md", "recruiter"),
            ("hr_email.md", "email"),
            ("application_answers.json", "answer"),
            ("match_report.md", "report"),
        ],
    )
    def test_validate_missing_required_file(self, validator, valid_package_dir, greenhouse_job, filename, keyword):
        """Each required file, if missing, produces a specific validation error."""
        (valid_package_dir / filename).unlink()
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any(keyword in err.lower() for err in result.errors), (
            f"Expected an error mentioning '{keyword}' when {filename} is missing, got: {result.errors}"
        )

    def test_validate_empty_cover_letter(self, validator, valid_package_dir, greenhouse_job):
        """Empty cover_letter.md fails validation (present but content-free is not valid)."""
        (valid_package_dir / "cover_letter.md").write_text("")
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("cover" in err.lower() for err in result.errors)

    def test_validate_whitespace_only_recruiter_message(self, validator, valid_package_dir, greenhouse_job):
        """Whitespace-only recruiter_message.md is treated as empty, not valid content."""
        (valid_package_dir / "recruiter_message.md").write_text("   \n\n   ")
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("recruiter" in err.lower() for err in result.errors)

    def test_validate_invalid_application_answers_json(self, validator, valid_package_dir, greenhouse_job):
        """Malformed application_answers.json fails validation."""
        (valid_package_dir / "application_answers.json").write_text("{not valid json}")
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert any("json" in err.lower() or "answer" in err.lower() for err in result.errors)


class TestPackageValidationErrorCollection:
    """Tests that multiple validation errors are collected, not failed early on the first problem."""

    def test_validate_collects_all_missing_files(self, validator, valid_package_dir, greenhouse_job):
        """Multiple missing files are all reported in a single validation pass."""
        (valid_package_dir / "cover_letter.md").unlink()
        (valid_package_dir / "recruiter_message.md").unlink()
        (valid_package_dir / "application_answers.json").unlink()

        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        assert len(result.errors) >= 3
        joined = " ".join(result.errors).lower()
        assert "cover" in joined
        assert "recruiter" in joined
        assert "answer" in joined

    def test_validate_does_not_short_circuit_on_first_error(self, validator, valid_package_dir, greenhouse_job):
        """
        A wrong job ID AND a missing file are both reported —
        validation doesn't stop after the identity check fails.
        """
        meta = json.loads((valid_package_dir / "metadata.json").read_text())
        meta["job"]["id"] = "wrong-id"
        (valid_package_dir / "metadata.json").write_text(json.dumps(meta))
        (valid_package_dir / "cover_letter.md").unlink()

        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is False
        joined = " ".join(result.errors).lower()
        assert "id" in joined
        assert "cover" in joined


class TestValidationResultContent:
    """Tests for ValidationResult structure and content."""

    def test_validation_result_type(self, validator, valid_package_dir, greenhouse_job):
        """validate() returns a ValidationResult instance."""
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert isinstance(result, ValidationResult)

    def test_validation_result_has_reason_on_failure(self, validator, valid_package_dir, greenhouse_job):
        """ValidationResult includes a human-readable reason when invalid."""
        (valid_package_dir / "cover_letter.md").unlink()
        result = validator.validate(valid_package_dir, greenhouse_job)

        assert result.valid is False
        assert result.reason is not None
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_validation_result_no_errors_when_valid(self, validator, valid_package_dir, greenhouse_job):
        """A valid package produces an empty errors list, not just valid=True."""
        result = validator.validate(valid_package_dir, greenhouse_job)
        assert result.valid is True
        assert result.errors == []

    def test_validation_does_not_mutate_package_files(self, validator, valid_package_dir, greenhouse_job):
        """Validation is read-only: it must not modify or delete package files."""
        before = {p.name: p.read_text() for p in valid_package_dir.iterdir()}
        validator.validate(valid_package_dir, greenhouse_job)
        after = {p.name: p.read_text() for p in valid_package_dir.iterdir()}
        assert before == after
