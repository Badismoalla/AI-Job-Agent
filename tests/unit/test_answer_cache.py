"""
Tests for modules.ai.cache.AnswerCache.

Per docs/PROJECT_REQUIREMENTS.md Section 3.4 ("AI Response Cache"):
    The system should avoid asking AI the same questions repeatedly.
    Example: "Why do you want to join our company?" -> stored locally,
    future similar questions reuse/adapt previous answers.
    Storage: cache/

This is scoped to exact-match caching (same question, normalized for
case/whitespace). "Similar" questions being fuzzy-matched to a stored
answer is a real future enhancement, not something invented here — see
module docstring in modules/ai/cache.py.
"""

import json
from pathlib import Path

import pytest

from modules.ai.cache import AnswerCache


@pytest.fixture
def cache_path(tmp_path) -> Path:
    return tmp_path / "questions.json"


class TestAnswerCacheMiss:

    def test_get_on_empty_cache_returns_none(self, cache_path):
        cache = AnswerCache(cache_path)
        assert cache.get("Why do you want to join our company?") is None

    def test_get_on_missing_file_returns_none_and_does_not_raise(self, tmp_path):
        cache = AnswerCache(tmp_path / "does_not_exist.json")
        assert cache.get("Any question") is None


class TestAnswerCacheSetAndGet:

    def test_set_then_get_returns_stored_answer(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Why do you want to join our company?", "Because of the mission.")
        assert cache.get("Why do you want to join our company?") == "Because of the mission."

    def test_get_is_case_insensitive(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Why do you want to join our company?", "Answer text")
        assert cache.get("WHY DO YOU WANT TO JOIN OUR COMPANY?") == "Answer text"

    def test_get_ignores_surrounding_whitespace(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Why do you want to join our company?", "Answer text")
        assert cache.get("   Why do you want to join our company?   ") == "Answer text"

    def test_different_question_is_a_cache_miss(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Why do you want to join our company?", "Answer A")
        assert cache.get("What's your salary expectation?") is None

    def test_set_persists_to_disk(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Question one?", "Answer one")

        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert any("question one" in k.lower() for k in data.keys())

    def test_new_cache_instance_reads_previously_persisted_answer(self, cache_path):
        AnswerCache(cache_path).set("Persisted question?", "Persisted answer")
        # Fresh instance, same file — simulates a new process run.
        reloaded = AnswerCache(cache_path)
        assert reloaded.get("Persisted question?") == "Persisted answer"


class TestAnswerCacheOverwrite:

    def test_set_same_question_twice_overwrites(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Question?", "First answer")
        cache.set("Question?", "Second answer")
        assert cache.get("Question?") == "Second answer"


class TestAnswerCacheCorruptFile:

    def test_corrupt_cache_file_treated_as_empty_not_crashed(self, cache_path):
        cache_path.write_text("{not valid json")
        cache = AnswerCache(cache_path)
        assert cache.get("Any question") is None

    def test_can_still_set_after_corrupt_file_recovery(self, cache_path):
        cache_path.write_text("{not valid json")
        cache = AnswerCache(cache_path)
        cache.set("New question?", "New answer")
        assert cache.get("New question?") == "New answer"


class TestAnswerCacheMetadata:

    def test_stored_record_includes_original_question_and_timestamp(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Why this role?", "Because it fits my background.")

        data = json.loads(cache_path.read_text())
        record = next(iter(data.values()))
        assert record["question"] == "Why this role?"
        assert record["answer"] == "Because it fits my background."
        assert "cached_at" in record

    def test_hit_count_increments_on_repeated_get(self, cache_path):
        cache = AnswerCache(cache_path)
        cache.set("Q?", "A")
        cache.get("Q?")
        cache.get("Q?")

        data = json.loads(cache_path.read_text())
        record = next(iter(data.values()))
        assert record["hit_count"] == 2
