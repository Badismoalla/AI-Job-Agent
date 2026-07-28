"""
Unit tests for commands.display.

Covers:
- Decision/border/tier color maps stay in sync for known + unknown values
- Panel/table builders don't crash and honour the "no data" branches
  (no gaps, no score breakdown, secondary vs primary tier)
"""

import pytest
from rich.panel import Panel
from rich.table import Table

from commands.display import (
    decision_border_style,
    decision_label,
    decision_text_style,
    tier_style,
    format_list,
    build_score_bar,
    mini_bar,
    job_header_panel,
    score_panel,
    match_details_table,
    gaps_table,
    score_breakdown_table,
)
from core.models import (
    ApplicationSource,
    JobListing,
    Market,
    MatchDecision,
    MatchReport,
    RoleTier,
)


def make_job() -> JobListing:
    return JobListing(
        id="testco-test-engineer-krakow",
        title="Test Engineer",
        company="TestCo",
        city="Krakow",
        market=Market.POLAND,
        url="https://example.com/jobs/testco-test-engineer-krakow",
        source=ApplicationSource.LINKEDIN,
    )


def make_report(**overrides) -> MatchReport:
    defaults = dict(
        job_id="testco-test-engineer-krakow",
        job_title="Test Engineer",
        company="TestCo",
        tier=RoleTier.PRIMARY,
        decision=MatchDecision.APPLY,
        score=80,
        reason="Strong match.",
    )
    defaults.update(overrides)
    return MatchReport(**defaults)


# ── Color maps ───────────────────────────────────────────────────────────────

class TestDecisionStyling:

    @pytest.mark.parametrize(
        "decision,expected_text,expected_border",
        [
            ("APPLY", "bold green", "green"),
            ("REVIEW", "bold yellow", "yellow"),
            ("SKIP", "bold red", "red"),
        ],
    )
    def test_known_decisions(self, decision, expected_text, expected_border):
        assert decision_text_style(decision) == expected_text
        assert decision_border_style(decision) == expected_border
        assert decision_label(decision) == f"[{expected_text}]{decision}[/{expected_text}]"

    def test_accepts_enum_or_string(self):
        assert decision_text_style(MatchDecision.APPLY) == decision_text_style("APPLY")
        assert decision_border_style(MatchDecision.SKIP) == decision_border_style("SKIP")

    def test_unknown_decision_falls_back(self):
        assert decision_text_style("UNKNOWN") == "white"
        assert decision_border_style("UNKNOWN") == "blue"


class TestTierStyling:

    @pytest.mark.parametrize(
        "tier,expected",
        [("primary", "cyan"), ("secondary", "blue"), ("excluded", "dim red")],
    )
    def test_known_tiers(self, tier, expected):
        assert tier_style(tier) == expected

    def test_unknown_tier_falls_back(self):
        assert tier_style("unknown") == "white"


# ── Formatting utilities ────────────────────────────────────────────────────

class TestFormattingUtils:

    def test_format_list_empty(self):
        assert format_list([], "green") == ""

    def test_format_list_wraps_each_item(self):
        assert format_list(["a", "b"], "green") == "[green]a[/green], [green]b[/green]"

    def test_build_score_bar_length(self):
        bar = build_score_bar(50, width=20)
        assert "█" in bar and "░" in bar

    def test_mini_bar_full(self):
        bar = mini_bar(1.0, width=10)
        assert bar.count("█") == 10


# ── Panels / Tables ──────────────────────────────────────────────────────────

class TestPanelsAndTables:

    def test_job_header_panel_returns_panel(self):
        panel = job_header_panel(make_job(), title="Job Analysis")
        assert isinstance(panel, Panel)

    def test_score_panel_returns_panel(self):
        panel = score_panel(make_report())
        assert isinstance(panel, Panel)

    def test_match_details_table_primary_tier(self):
        table = match_details_table(make_report(tier=RoleTier.PRIMARY))
        assert isinstance(table, Table)

    def test_match_details_table_secondary_tier(self):
        table = match_details_table(
            make_report(
                tier=RoleTier.SECONDARY,
                secondary_skills_matched=["power bi", "sql"],
                secondary_domain_matched=True,
                domain_found="automotive",
            )
        )
        assert isinstance(table, Table)

    def test_gaps_table_none_when_no_gaps(self):
        assert gaps_table(make_report(skill_gaps=[])) is None

    def test_gaps_table_present_when_gaps_exist(self):
        table = gaps_table(
            make_report(
                skill_gaps=["canoe"],
                gap_mitigations={"canoe": "Equivalent DLT/Wireshark experience."},
            )
        )
        assert isinstance(table, Table)

    def test_score_breakdown_table_none_when_empty(self):
        assert score_breakdown_table(make_report(score_breakdown={})) is None

    def test_score_breakdown_table_present(self):
        table = score_breakdown_table(
            make_report(score_breakdown={"title_match": 35, "keyword_match": 20})
        )
        assert isinstance(table, Table)
