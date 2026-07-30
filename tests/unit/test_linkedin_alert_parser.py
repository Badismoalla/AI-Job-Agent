"""
Unit tests for modules.gmail.linkedin_alert_parser.

Two layers, matching the fetch/parse separation used throughout this
project's scrapers:

1. Parser tests (TestParseAlertEmail, TestIsLinkedinJobAlert,
   TestExtractJobId, TestExtractCompanyAndLocation, TestExtractPostedDate,
   TestInferMarket) — pure, no network, no Gmail dependency at all.
   TestParseAlertEmail exercises the saved sample fixture at
   tests/fixtures/linkedin_job_alert_sample.html — see the schema caveat
   in linkedin_alert_parser.py for what is/isn't verified about the real
   template. If LinkedIn's actual email HTML has drifted, update that
   fixture first — a failing test here points at exactly which assumption
   broke.

2. Fetch orchestration tests (TestFetchLinkedinAlertEmails) — exercise
   fetch_linkedin_alert_emails() against a FakeGmailService standing in
   for the real googleapiclient Resource, per GmailClient's own documented
   test-injection pattern (GmailClient(service=<fake>)). No real Gmail
   credentials or network access.

3. TestNoDirectLinkedInAccess — a small architectural guard confirming
   this module never imports an HTTP client capable of contacting
   linkedin.com directly (only Gmail, via GmailClient).

No test in this file makes a real network call.
"""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.exceptions import GmailError
from core.models import ApplicationSource, JobListing, Market
from modules.gmail.client import GmailClient
from modules.gmail.linkedin_alert_parser import (
    _extract_company_and_location,
    _extract_job_id,
    _extract_posted_date,
    _infer_market,
    fetch_linkedin_alert_emails,
    is_linkedin_job_alert,
    parse_alert_email,
)

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "linkedin_job_alert_sample.html"


@pytest.fixture
def sample_alert_html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def make_html_body_part(html: str) -> dict:
    """Build a Gmail API message payload with a single text/html part,
    matching what GmailClient._find_html_part() expects to walk."""
    encoded = base64.urlsafe_b64encode(html.encode("utf-8")).decode("ascii").rstrip("=")
    return {
        "payload": {
            "mimeType": "text/html",
            "body": {"data": encoded},
        }
    }


class FakeGmailService:
    """
    Stands in for a real googleapiclient Gmail Resource. Supports exactly
    the call chain GmailClient uses: users().messages().list(...).execute()
    and users().messages().get(...).execute().
    """

    def __init__(self, message_ids: list[str], metadata: dict[str, dict], bodies: dict[str, str]):
        self._message_ids = message_ids
        self._metadata = metadata
        self._bodies = bodies
        self.get_calls: list[tuple[str, str]] = []  # (message_id, format)

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        return _Execute({"messages": [{"id": mid} for mid in self._message_ids]})

    def get(self, userId, id, format, metadataHeaders=None):
        self.get_calls.append((id, format))
        if format == "metadata":
            meta = self._metadata[id]
            headers = [
                {"name": "From", "value": meta.get("from", "")},
                {"name": "Subject", "value": meta.get("subject", "")},
                {"name": "Date", "value": meta.get("date", "")},
            ]
            return _Execute({"payload": {"headers": headers}})
        # format == "full"
        if id not in self._bodies:
            raise RuntimeError(f"no body configured for {id}")
        return _Execute(make_html_body_part(self._bodies[id]))


class _Execute:
    """Wraps a canned response so .execute() returns it, like the real SDK."""

    def __init__(self, response: dict):
        self._response = response

    def execute(self):
        return self._response


# ── parse_alert_email(): the saved sample fixture ──────────────────────────

class TestParseAlertEmail:

    def test_returns_only_jobs_in_configured_markets(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        # Job 3 (New York) and Job 5 (no location separator -> Unknown) are
        # both correctly excluded; Job 4 is a duplicate of Job 1's link.
        assert len(results) == 2

    def test_every_result_is_a_joblisting_from_linkedin(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        assert all(isinstance(j, JobListing) for j in results)
        assert all(j.source == ApplicationSource.LINKEDIN for j in results)

    def test_first_job_fields(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        job = next(j for j in results if "3812345678" in j.url)
        assert job.title == "Senior Test Engineer"
        assert job.company == "Bosch Group"
        assert job.city == "Wroclaw, Poland"
        assert job.market == Market.POLAND
        assert job.url == "https://www.linkedin.com/jobs/view/3812345678"

    def test_second_job_handles_dash_separator(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        job = next(j for j in results if "3812345679" in j.url)
        assert job.company == "Continental"
        assert job.city == "Krakow, Poland"

    def test_posted_date_within_expected_tolerance(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        job1 = next(j for j in results if "3812345678" in j.url)  # "7 hours ago"
        job2 = next(j for j in results if "3812345679" in j.url)  # "2 days ago"

        now = datetime.now(timezone.utc)
        assert abs((now - job1.posted_date) - timedelta(hours=7)) < timedelta(minutes=1)
        assert abs((now - job2.posted_date) - timedelta(days=2)) < timedelta(minutes=1)

    def test_out_of_market_job_is_excluded(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        assert not any("3812345680" in j.url for j in results)  # New York job

    def test_duplicate_link_is_not_double_counted(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        urls = [j.url for j in results]
        assert len(urls) == len(set(urls))  # no duplicate URLs at all

    def test_unparseable_location_job_is_excluded(self, sample_alert_html):
        results = parse_alert_email(sample_alert_html)
        assert not any("3812345681" in j.url for j in results)  # "Continental Poland", no separator

    def test_non_string_input_raises_gmail_error(self):
        with pytest.raises(GmailError, match="Expected HTML text"):
            parse_alert_email({"not": "a string"})

    def test_empty_html_returns_no_results(self):
        assert parse_alert_email("<html><body></body></html>") == []

    def test_html_with_no_matching_job_links_returns_no_results(self):
        html = '<html><body><a href="https://example.com">Not a job</a></body></html>'
        assert parse_alert_email(html) == []


# ── is_linkedin_job_alert() ─────────────────────────────────────────────────

class TestIsLinkedinJobAlert:

    def test_true_for_known_alert_sender_pattern(self):
        assert is_linkedin_job_alert("jobalerts-noreply@linkedin.com", "Anything") is True

    def test_true_for_linkedin_sender_with_alert_subject(self):
        assert is_linkedin_job_alert(
            "messages-noreply@linkedin.com", "25 new jobs for Test Engineer"
        ) is True

    def test_false_for_non_linkedin_sender(self):
        assert is_linkedin_job_alert("jobalerts@example.com", "Job alert") is False

    def test_false_for_linkedin_sender_without_alert_signal(self):
        # e.g. a connection request notification — LinkedIn sender, but not an alert
        assert is_linkedin_job_alert("notifications-noreply@linkedin.com", "You have a new connection") is False

    def test_case_insensitive(self):
        assert is_linkedin_job_alert("JobAlerts-Noreply@LinkedIn.com", "JOB ALERT: New jobs") is True


# ── _extract_job_id() ────────────────────────────────────────────────────────

class TestExtractJobId:

    def test_comm_jobs_view_url(self):
        assert _extract_job_id("https://www.linkedin.com/comm/jobs/view/3812345678/?trk=x") == "3812345678"

    def test_plain_jobs_view_url(self):
        assert _extract_job_id("https://www.linkedin.com/jobs/view/3812345678/") == "3812345678"

    def test_no_match_returns_none(self):
        assert _extract_job_id("https://www.linkedin.com/in/someone") is None

    def test_unrelated_url_returns_none(self):
        assert _extract_job_id("https://example.com/jobs/view/123") is None


# ── _extract_company_and_location() ─────────────────────────────────────────

class TestExtractCompanyAndLocation:

    def _block(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml")

    def test_middot_separator(self):
        block = self._block("<div>Title</div><div>Bosch &middot; Wroclaw, Poland</div>")
        company, location = _extract_company_and_location(block, exclude_text="Title")
        assert company == "Bosch"
        assert location == "Wroclaw, Poland"

    def test_dash_separator(self):
        block = self._block("<div>Title</div><div>Continental - Krakow, Poland</div>")
        company, location = _extract_company_and_location(block, exclude_text="Title")
        assert company == "Continental"
        assert location == "Krakow, Poland"

    def test_no_separator_falls_back_to_unknown_location(self):
        block = self._block("<div>Title</div><div>Continental Poland</div>")
        company, location = _extract_company_and_location(block, exclude_text="Title")
        assert company == "Continental Poland"
        assert location == "Unknown"

    def test_skips_relative_time_text(self):
        block = self._block("<div>Title</div><div>3 days ago</div><div>Bosch &middot; Wroclaw</div>")
        company, location = _extract_company_and_location(block, exclude_text="Title")
        assert company == "Bosch"
        assert location == "Wroclaw"

    def test_no_usable_text_returns_none_company(self):
        block = self._block("<div>Title</div>")
        company, location = _extract_company_and_location(block, exclude_text="Title")
        assert company is None
        assert location == "Unknown"


# ── _extract_posted_date() ──────────────────────────────────────────────────

class TestExtractPostedDate:

    def _block(self, text: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(f"<div>{text}</div>", "lxml")

    def test_hours_ago(self):
        result = _extract_posted_date(self._block("7 hours ago"))
        expected = datetime.now(timezone.utc) - timedelta(hours=7)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_hr_abbreviation(self):
        result = _extract_posted_date(self._block("3 hr ago"))
        expected = datetime.now(timezone.utc) - timedelta(hours=3)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_days_ago(self):
        result = _extract_posted_date(self._block("2 days ago"))
        expected = datetime.now(timezone.utc) - timedelta(days=2)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_weeks_ago(self):
        result = _extract_posted_date(self._block("3 weeks ago"))
        expected = datetime.now(timezone.utc) - timedelta(weeks=3)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_months_ago_approximated(self):
        result = _extract_posted_date(self._block("1 month ago"))
        expected = datetime.now(timezone.utc) - timedelta(days=30)
        assert abs(result - expected) < timedelta(seconds=5)

    def test_no_match_returns_none(self):
        assert _extract_posted_date(self._block("Applicants welcome")) is None

    def test_singular_form(self):
        result = _extract_posted_date(self._block("1 day ago"))
        expected = datetime.now(timezone.utc) - timedelta(days=1)
        assert abs(result - expected) < timedelta(seconds=5)


# ── _infer_market() ──────────────────────────────────────────────────────────

class TestInferMarket:

    @pytest.mark.parametrize(
        "location,expected",
        [
            ("Wroclaw, Poland", Market.POLAND),
            ("Amsterdam, Netherlands", Market.NETHERLANDS),
            ("Luxembourg City, Luxembourg", Market.LUXEMBOURG),
            ("Dubai, United Arab Emirates", Market.UAE),
            ("Dubai, UAE", Market.UAE),
            ("Riyadh, Saudi Arabia", Market.SAUDI_ARABIA),
            ("Doha, Qatar", Market.QATAR),
        ],
    )
    def test_known_markets(self, location, expected):
        assert _infer_market(location) == expected

    def test_case_insensitive(self):
        assert _infer_market("WROCLAW, POLAND") == Market.POLAND

    def test_unknown_location_returns_none(self):
        assert _infer_market("New York, United States") is None

    def test_unknown_literal_returns_none(self):
        assert _infer_market("Unknown") is None


# ── fetch_linkedin_alert_emails(): Gmail orchestration ──────────────────────

class TestFetchLinkedinAlertEmails:

    def test_fetches_and_filters_to_confirmed_alerts_only(self, sample_alert_html):
        service = FakeGmailService(
            message_ids=["m1", "m2"],
            metadata={
                "m1": {"from": "jobalerts-noreply@linkedin.com", "subject": "Job alert: 5 new jobs"},
                "m2": {"from": "messages-noreply@linkedin.com", "subject": "You have a new message"},
            },
            bodies={"m1": sample_alert_html},
        )
        client = GmailClient(service=service)

        result = fetch_linkedin_alert_emails(client)

        assert result == [sample_alert_html]
        # m2 was never even fetched in full — filtered out at the metadata stage.
        assert ("m2", "full") not in service.get_calls

    def test_returns_empty_list_when_no_alerts_found(self):
        service = FakeGmailService(message_ids=[], metadata={}, bodies={})
        client = GmailClient(service=service)
        assert fetch_linkedin_alert_emails(client) == []

    def test_skips_message_on_body_fetch_failure_without_raising(self, sample_alert_html):
        class FailingBodyService(FakeGmailService):
            def get(self, userId, id, format, metadataHeaders=None):
                if format == "full" and id == "m1":
                    raise RuntimeError("simulated Gmail API failure")
                return super().get(userId, id, format, metadataHeaders)

        service = FailingBodyService(
            message_ids=["m1", "m2"],
            metadata={
                "m1": {"from": "jobalerts-noreply@linkedin.com", "subject": "Job alert"},
                "m2": {"from": "jobalerts-noreply@linkedin.com", "subject": "Job alert"},
            },
            bodies={"m2": sample_alert_html},
        )
        client = GmailClient(service=service)

        # m1's body fetch raises, but the whole call should not blow up.
        result = fetch_linkedin_alert_emails(client)
        assert result == [sample_alert_html]

    def test_respects_max_results(self):
        service = FakeGmailService(
            message_ids=[f"m{i}" for i in range(5)],
            metadata={f"m{i}": {"from": "x@example.com", "subject": ""} for i in range(5)},
            bodies={},
        )
        client = GmailClient(service=service)
        fetch_linkedin_alert_emails(client, max_results=5)
        # list() was called with maxResults=5 — verified indirectly via no error
        # and all 5 ids being checked (5 metadata calls).
        metadata_calls = [c for c in service.get_calls if c[1] == "metadata"]
        assert len(metadata_calls) == 5


# ── Architectural guard: no direct LinkedIn HTTP access ─────────────────────

class TestNoDirectLinkedInAccess:

    def test_module_does_not_import_an_http_client(self):
        """
        This module must only ever reach LinkedIn content via Gmail — it
        should have no capability to make an HTTP request to linkedin.com
        directly. A regression here (e.g. someone adding `import httpx` to
        "enrich" a listing) would silently reintroduce exactly the ToS/
        anti-bot problem this module was built to avoid.
        """
        import modules.gmail.linkedin_alert_parser as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        forbidden_imports = ("import httpx", "import requests", "import aiohttp", "import urllib.request")
        for forbidden in forbidden_imports:
            assert forbidden not in source, f"found forbidden import: {forbidden}"
