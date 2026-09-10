"""
modules/ai/cache.py
----------------------
Local cache for AI-generated application answers, per
docs/PROJECT_REQUIREMENTS.md Section 3.4:

    The system should avoid asking AI the same questions repeatedly.
    Example: "Why do you want to join our company?" -> the answer
    should be stored locally. Future similar questions should reuse or
    adapt previous answers. Storage: cache/

Scope note: this implements exact-match caching only (question text
normalized for case and surrounding whitespace). The doc's "future
similar questions... reuse or adapt" implies fuzzy/semantic matching
of near-duplicate questions — that's a real but separate enhancement
(would need embedding similarity or a normalization heuristic beyond
whitespace/case), not implemented here to avoid inventing scope beyond
what was asked. An exact-match cache already eliminates the concrete
example in the doc (the same literal question asked across multiple
applications).

Storage format (cache/questions.json):
    {
        "<normalized question key>": {
            "question": "<original question text>",
            "answer": "<cached answer>",
            "cached_at": "<ISO timestamp>",
            "hit_count": <int>
        },
        ...
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from core.logger import get_logger
from core.models import utc_now

logger = get_logger(__name__)

_DEFAULT_CACHE_PATH = Path(__file__).parent.parent.parent / "cache" / "questions.json"


def _normalize(question: str) -> str:
    return question.strip().lower()


class AnswerCache:
    """Local, file-backed cache of application-question answers."""

    def __init__(self, cache_path: Path = _DEFAULT_CACHE_PATH) -> None:
        self._path = Path(cache_path)

    def get(self, question: str) -> str | None:
        """Return the cached answer for this question, or None on a miss."""
        data = self._load()
        key = _normalize(question)
        record = data.get(key)
        if record is None:
            return None

        record["hit_count"] = record.get("hit_count", 0) + 1
        data[key] = record
        self._save(data)

        logger.debug("Answer cache hit | question={question!r}", question=question)
        return record["answer"]

    def set(self, question: str, answer: str) -> None:
        """Store (or overwrite) the answer for this question."""
        data = self._load()
        key = _normalize(question)
        data[key] = {
            "question": question,
            "answer": answer,
            "cached_at": utc_now().isoformat(),
            "hit_count": data.get(key, {}).get("hit_count", 0),
        }
        self._save(data)
        logger.debug("Answer cached | question={question!r}", question=question)

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Answer cache file is corrupt, treating as empty | path={path}", path=str(self._path))
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
