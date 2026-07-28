"""
Unit tests for modules.ai.prompts — the actual prompt template functions.

These test "prompt creation" at the template level: given a set of inputs,
does each builder produce a string containing the right facts, in the right
conditional sections (gap handling, visa phrasing, fallback defaults)?

No network access — these are pure string-building functions.
"""

from modules.ai.prompts import (
    SYSTEM_PROMPT,
    HIRING_PERSONA,
    application_answer_prompt,
    cover_letter_prompt,
    follow_up_prompt,
    hiring_manager_prompt,
    hr_email_prompt,
    job_score_explanation_prompt,
    recruiter_inmail_prompt,
)


# ── System prompt / persona ──────────────────────────────────────────────────

class TestSystemPrompt:

    def test_system_prompt_embeds_hiring_persona(self):
        assert HIRING_PERSONA in SYSTEM_PROMPT

    def test_system_prompt_states_core_identity(self):
        assert "Software Test & Validation Engineer" in SYSTEM_PROMPT
        assert "BMW Group" in SYSTEM_PROMPT

    def test_system_prompt_forbids_disclaimers(self):
        assert "no disclaimers" in SYSTEM_PROMPT.lower() or "disclaimers" in SYSTEM_PROMPT.lower()


# ── cover_letter_prompt ──────────────────────────────────────────────────────

class TestCoverLetterPrompt:

    def _build(self, **overrides):
        defaults = dict(
            job_title="Test Engineer",
            company="Bosch",
            city="Wroclaw",
            market="Poland",
            recruiter_name=None,
            company_detail=None,
            gap_mitigations={},
            matched_keywords=[],
            matched_protocols=[],
        )
        defaults.update(overrides)
        return cover_letter_prompt(**defaults)

    def test_includes_core_job_facts(self):
        prompt = self._build(job_title="Software Test Engineer", company="Bosch", city="Wroclaw")
        assert "Software Test Engineer" in prompt
        assert "Bosch" in prompt
        assert "Wroclaw" in prompt

    def test_recruiter_name_defaults_to_hiring_team(self):
        prompt = self._build(recruiter_name=None)
        assert "Dear Hiring Team," in prompt

    def test_recruiter_name_used_when_provided(self):
        prompt = self._build(recruiter_name="Anna Kowalski")
        assert "Dear Anna Kowalski," in prompt

    def test_gap_section_absent_when_no_gaps(self):
        prompt = self._build(gap_mitigations={})
        assert "SKILL GAP HANDLING" not in prompt

    def test_gap_section_present_when_gaps_exist(self):
        prompt = self._build(gap_mitigations={"canoe": "Use DLT/Wireshark instead."})
        assert "SKILL GAP HANDLING" in prompt
        assert "canoe" in prompt
        assert "Use DLT/Wireshark instead." in prompt

    def test_matched_keywords_truncated_to_six(self):
        keywords = [f"kw{i}" for i in range(10)]
        prompt = self._build(matched_keywords=keywords)
        for kw in keywords[:6]:
            assert kw in prompt
        for kw in keywords[6:]:
            assert kw not in prompt

    def test_matched_protocols_truncated_to_four(self):
        protocols = [f"proto{i}" for i in range(8)]
        prompt = self._build(matched_protocols=protocols)
        for p in protocols[:4]:
            assert p in prompt
        for p in protocols[4:]:
            assert p not in prompt

    def test_empty_matched_lists_fall_back_to_placeholder_text(self):
        prompt = self._build(matched_keywords=[], matched_protocols=[])
        assert "general testing/validation match" in prompt
        assert "none specific" in prompt

    def test_visa_phrase_per_known_market(self):
        for market in ("Poland", "Netherlands", "Luxembourg", "UAE", "Saudi Arabia", "Qatar"):
            prompt = self._build(market=market)
            assert market in prompt

    def test_visa_phrase_fallback_for_unknown_market(self):
        prompt = self._build(market="Atlantis")
        assert "I require work permit sponsorship and am available to relocate immediately." in prompt

    def test_company_detail_placeholder_when_absent(self):
        prompt = self._build(company_detail=None)
        assert "do not invent specific details" in prompt.lower()

    def test_company_detail_used_when_provided(self):
        prompt = self._build(company_detail="their new ADAS platform for BMW")
        assert "their new ADAS platform for BMW" in prompt


# ── recruiter_inmail_prompt ──────────────────────────────────────────────────

class TestRecruiterInmailPrompt:

    def test_includes_core_facts(self):
        prompt = recruiter_inmail_prompt(
            job_title="Test Engineer",
            company="Bosch",
            city="Wroclaw",
            recruiter_name="Anna Kowalski",
            gap_mitigations={},
        )
        assert "Test Engineer" in prompt
        assert "Bosch" in prompt
        assert "Wroclaw" in prompt
        assert "Anna Kowalski" in prompt

    def test_enforces_word_limit_and_no_visa_mention_rule(self):
        prompt = recruiter_inmail_prompt(
            job_title="Test Engineer", company="Bosch", city="Wroclaw",
            recruiter_name="Anna", gap_mitigations={},
        )
        assert "120 words" in prompt
        assert "Do NOT mention visa sponsorship" in prompt


# ── hr_email_prompt ──────────────────────────────────────────────────────────

class TestHrEmailPrompt:

    def _build(self, **overrides):
        defaults = dict(
            job_title="Test Engineer",
            company="Bosch",
            city="Wroclaw",
            market="Poland",
            recruiter_name=None,
            company_detail=None,
            gap_mitigations={},
        )
        defaults.update(overrides)
        return hr_email_prompt(**defaults)

    def test_subject_line_format(self):
        prompt = self._build(job_title="Test Engineer")
        assert "Application – Test Engineer | Badis Moalla" in prompt

    def test_recruiter_name_defaults_to_hr_team(self):
        prompt = self._build(recruiter_name=None)
        assert "TO: HR Team" in prompt

    def test_gap_section_present_only_with_gaps(self):
        no_gaps = self._build(gap_mitigations={})
        assert "HANDLE THESE GAPS AS FOLLOWS" not in no_gaps

        with_gaps = self._build(gap_mitigations={"ssis": "Internship only, do not oversell."})
        assert "HANDLE THESE GAPS AS FOLLOWS" in with_gaps
        assert "ssis" in with_gaps


# ── follow_up_prompt ─────────────────────────────────────────────────────────

class TestFollowUpPrompt:

    def test_includes_days_since_applied(self):
        prompt = follow_up_prompt(
            job_title="Test Engineer", company="Bosch", recruiter_name="Anna", days_since_applied=10
        )
        assert "DAYS SINCE APPLIED: 10" in prompt

    def test_subject_line_format(self):
        prompt = follow_up_prompt(
            job_title="Test Engineer", company="Bosch", recruiter_name=None, days_since_applied=7
        )
        assert "Subject line: Follow-up: Test Engineer Application | Badis Moalla" in prompt

    def test_contact_defaults_to_hiring_team(self):
        prompt = follow_up_prompt(
            job_title="Test Engineer", company="Bosch", recruiter_name=None, days_since_applied=7
        )
        assert "CONTACT: Hiring Team" in prompt


# ── hiring_manager_prompt ─────────────────────────────────────────────────────

class TestHiringManagerPrompt:

    def test_team_context_defaults_when_absent(self):
        prompt = hiring_manager_prompt(
            job_title="Test Engineer", company="Bosch", manager_name="Jan", team_context=None
        )
        assert "TEAM/CONTEXT: engineering/validation team" in prompt

    def test_team_context_used_when_provided(self):
        prompt = hiring_manager_prompt(
            job_title="Test Engineer", company="Bosch", manager_name="Jan",
            team_context="the ADAS validation team",
        )
        assert "the ADAS validation team" in prompt


# ── application_answer_prompt ─────────────────────────────────────────────────

class TestApplicationAnswerPrompt:

    def test_includes_question_and_word_limit(self):
        prompt = application_answer_prompt(
            question="Why do you want this role?",
            job_title="Test Engineer",
            company="Bosch",
            max_words=150,
        )
        assert "Why do you want this role?" in prompt
        assert "MAXIMUM WORDS: 150" in prompt
        assert "Under 150 words" in prompt


# ── job_score_explanation_prompt ──────────────────────────────────────────────

class TestJobScoreExplanationPrompt:

    def test_includes_score_decision_tier(self):
        prompt = job_score_explanation_prompt(
            job_title="Test Engineer", company="Bosch", score=85, decision="APPLY", tier="primary",
            matched_keywords=["automotive"], gaps=["canoe"], mitigations={"canoe": "x" * 200},
        )
        assert "85/100" in prompt
        assert "DECISION: APPLY" in prompt
        assert "TIER: primary" in prompt

    def test_empty_lists_fall_back_to_none(self):
        prompt = job_score_explanation_prompt(
            job_title="Test Engineer", company="Bosch", score=0, decision="SKIP", tier="excluded",
            matched_keywords=[], gaps=[], mitigations={},
        )
        assert "MATCHED KEYWORDS: none" in prompt
        assert "SKILL GAPS: none" in prompt
        assert "MITIGATIONS: none" in prompt

    def test_mitigation_text_truncated_to_80_chars(self):
        long_text = "x" * 200
        prompt = job_score_explanation_prompt(
            job_title="Test Engineer", company="Bosch", score=50, decision="REVIEW", tier="primary",
            matched_keywords=[], gaps=["canoe"], mitigations={"canoe": long_text},
        )
        # Only the first 80 chars of the mitigation text should appear, followed by "..."
        assert long_text[:80] + "..." in prompt
        assert long_text[81:] not in prompt
