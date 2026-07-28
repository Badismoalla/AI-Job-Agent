"""
core/matching/
--------------
Component modules that make up the job matching engine. core.matcher.JobMatcher
orchestrates these; each module owns exactly one concern:

    keywords.py       — keyword lists, gap mitigations, weights, thresholds (data)
    text_utils.py      — small text-normalisation helpers shared by every component
    classifier.py      — PRIMARY / SECONDARY / EXCLUDED tier classification
    scorer.py          — 0-100 score breakdown across five categories
    gap_detector.py    — known skill gaps + candidate mitigations
    decision.py        — score -> APPLY/REVIEW/SKIP, plus the secondary-role gate
    reason_builder.py  — one-sentence human-readable explanation of the decision
"""
