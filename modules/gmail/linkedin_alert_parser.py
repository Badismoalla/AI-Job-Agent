"""
modules/gmail/linkedin_alert_parser.py
-----------------------------------------
Parses LinkedIn "Job Alert" emails (fetched via the Gmail module) into
JobListing objects. This never talks to linkedin.com directly — every job
title/company/location comes from the alert email content itself, which
LinkedIn sent to the user's own inbox for a search they configured.

Why Gmail instead of scraping LinkedIn?
LinkedIn's User Agreement prohibits scraping, and their public job search
is deliberately bot-hardened. Reading your own inbox has none of that
conflict — LinkedIn already agreed to put this data there.

╔══════════════════════════════════════════════════════════════════════════╗
║ SCHEMA CAVEAT — read before relying on this in production                ║
║                                                                          ║
║ This sandbox has no email account to pull a real LinkedIn Job Alert      ║
║ from, so the exact live HTML template could not be fetched and          ║
║ verified. What IS corroborated by external research: LinkedIn job       ║
║ alert emails come from an address containing "linkedin.com" with        ║
║ "jobalerts" in the sender, and link to job postings at a URL matching   ║
║ linkedin.com/comm/jobs/view/<id> or linkedin.com/jobs/view/<id> (the    ║
║ numeric job ID is the one stable, verifiable anchor across template     ║
║ changes — confirmed via a blog post describing exactly this parsing    ║
║ problem). Company/location extraction below is a best-effort match to  ║
║ LinkedIn's well-known "Company · Location" text convention used across ║
║ their job cards — not verified against a live email.                    ║
║                                                                          ║
║ Parsing is written defensively: it anchors on job URLs (the verified    ║
║ part) rather than CSS classes or table structure (the unverified        ║
║ part), and a genuinely unparseable job block is skipped with a logged   ║
║ warning rather than crashing the whole email. tests/unit/               ║
║ test_linkedin_alert_parser.py encodes the assumed shape explicitly as   ║
║ a saved sample email fixture. Before production use: save one real      ║
║ LinkedIn Job Alert email as HTML, diff it against that fixture, and     ║
║ adjust _extract_company_and_location() / _extract_posted_date() if      ║
║ needed — the job-URL-based extraction itself should be resilient to     ║
║ most template changes.                                                  ║
╚══════════════════════════════════════════════════════════════════════════╝

Usage:
    from modules.gmail.client import GmailClient
    from modules.gmail.linkedin_alert_parser import fetch_linkedin_alert_emails, parse_alert_email

    client = GmailClient.from_settings()
    listings = []
    for html in fetch_linkedin_alert_emails(client):
        listings.extend(parse_alert_email(html))
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from bs4.element import Tag
from slugify import slugify

from core.exceptions import GmailError
from core.logger import get_logger
from core.models import ApplicationSource, JobListing, Market
from modules.gmail.client import GmailClient

logger = get_logger(__name__)

# The Gmail search query used to find candidate emails — sender-scoped so
# we never even fetch the HTML of unrelated LinkedIn emails (connection
# requests, message notifications, etc.).
_GMAIL_SEARCH_QUERY = 'from:linkedin.com subject:(job alert OR "jobs for" OR "new jobs")'

# Confirms this is actually a LinkedIn Job Alert, not some other LinkedIn
# email that happened to match the broader Gmail search query above.
_SENDER_MUST_CONTAIN = "linkedin.com"
_SENDER_ALERT_HINTS = ("jobalerts", "jobs-noreply", "job-alerts")
_SUBJECT_ALERT_KEYWORDS = ("job alert", "jobs for", "new jobs", "jobs matching")

# The one stable, externally-verified anchor: LinkedIn job URLs always
# contain a numeric job ID, in emails often wrapped in a /comm/ tracking
# redirect. Company/location text conventions may drift across template
# redesigns; this pattern is what's actually documented to be stable.
_JOB_URL_PATTERN = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")

# "Company · Location" is LinkedIn's cross-surface convention for pairing
# these two facts in one line; also tolerate a plain "-" separator.
_COMPANY_LOCATION_SPLIT = re.compile(r"\s*[·•]\s*|\s+-\s+")

_RELATIVE_TIME_PATTERN = re.compile(
    r"(\d+)\s*(hour|hr|day|week|wk|month)s?\s*ago", re.IGNORECASE
)

_RELATIVE_TIME_UNITS = {
    "hour": "hours", "hr": "hours",
    "day": "days",
    "week": "weeks", "wk": "weeks",
    "month": "days",  # approximate a month as 30 days — good enough for staleness checks
}

_MONTH_DAY_FACTOR = {"months": 30}

_MARKET_KEYWORDS: dict[str, Market] = {
    "poland": Market.POLAND,
    "netherlands": Market.NETHERLANDS,
    "luxembourg": Market.LUXEMBOURG,
    "united arab emirates": Market.UAE,
    "uae": Market.UAE,
    "saudi arabia": Market.SAUDI_ARABIA,
    "qatar": Market.QATAR,
}


# ── fetch(): I/O only, via the Gmail module ─────────────────────────────────

def fetch_linkedin_alert_emails(gmail_client: GmailClient, max_results: int = 20) -> list[str]:
    """
    Search Gmail for candidate emails, filter to confirmed LinkedIn Job
    Alert emails only, and return each one's HTML body.

    This is the only function in this module that touches the network
    (via GmailClient) — it never contacts linkedin.com directly.
    """
    message_ids = gmail_client.search_messages(_GMAIL_SEARCH_QUERY, max_results=max_results)

    alert_html_bodies: list[str] = []
    for message_id in message_ids:
        metadata = gmail_client.get_message_metadata(message_id)
        if not is_linkedin_job_alert(metadata.get("from", ""), metadata.get("subject", "")):
            logger.debug(
                "Skipping non-alert email | message_id={id} | from={from_} | subject={subject}",
                id=message_id, from_=metadata.get("from", ""), subject=metadata.get("subject", ""),
            )
            continue

        try:
            html = gmail_client.get_message_html(message_id)
        except GmailError as e:
            logger.warning(
                "Could not fetch alert email body | message_id={id} | error={error}",
                id=message_id, error=str(e),
            )
            continue

        alert_html_bodies.append(html)

    logger.info(
        "Fetched LinkedIn Job Alert emails | searched={searched} | confirmed_alerts={confirmed}",
        searched=len(message_ids), confirmed=len(alert_html_bodies),
    )
    return alert_html_bodies


def is_linkedin_job_alert(sender: str, subject: str) -> bool:
    """
    True only for emails that are confirmed LinkedIn Job Alerts — both a
    LinkedIn sender AND an alert-shaped subject, so we don't misparse an
    unrelated LinkedIn email (a connection request, a message notification)
    that happened to match the broader Gmail search query.
    """
    sender_l = sender.lower()
    subject_l = subject.lower()

    is_linkedin_sender = _SENDER_MUST_CONTAIN in sender_l
    has_alert_sender_hint = any(hint in sender_l for hint in _SENDER_ALERT_HINTS)
    has_alert_subject = any(kw in subject_l for kw in _SUBJECT_ALERT_KEYWORDS)

    return is_linkedin_sender and (has_alert_sender_hint or has_alert_subject)


# ── parse(): pure, no I/O ──────────────────────────────────────────────────

def parse_alert_email(html: str) -> list[JobListing]:
    """
    Parse a single LinkedIn Job Alert email's HTML into JobListing objects.

    Deliberately synchronous and side-effect-free (besides logging a
    warning per skipped job) — see BaseJobScraper's docstring in
    modules/scraper/base.py for why fetch/parse separation matters for
    testability; the same principle applies here even though this module
    doesn't inherit from BaseJobScraper (Gmail ingestion isn't a paginated
    web scrape, so it doesn't fit that framework's contract).
    """
    if not isinstance(html, str):
        raise GmailError(f"Expected HTML text to parse, got {type(html).__name__}")

    soup = BeautifulSoup(html, "lxml")

    listings: list[JobListing] = []
    seen_job_ids: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        job_id = _extract_job_id(anchor["href"])
        if job_id is None or job_id in seen_job_ids:
            continue

        title = anchor.get_text(strip=True)
        if not title:
            # Likely a thumbnail/image wrapper linking to the same job,
            # not the title link — skip, don't treat as a parse failure.
            continue

        block = _find_job_block(anchor)
        company, location = _extract_company_and_location(block, exclude_text=title)

        if not company:
            logger.warning(
                "Skipping unparseable job block — no company text found | job_id={id} | title={title}",
                id=job_id, title=title,
            )
            continue

        market = _infer_market(location)
        if market is None:
            logger.warning(
                "Skipping job outside configured markets | job_id={id} | title={title} | location={loc}",
                id=job_id, title=title, loc=location,
            )
            continue

        seen_job_ids.add(job_id)
        posted_date = _extract_posted_date(block)

        listings.append(
            JobListing(
                id=slugify(f"{company}-{title}-{location}", separator="-"),
                title=title,
                company=company,
                city=location,
                market=market,
                url=f"https://www.linkedin.com/jobs/view/{job_id}",
                source=ApplicationSource.LINKEDIN,
                posted_date=posted_date,
            )
        )

    return listings


# ── Parsing helpers (each isolated + independently testable) ───────────────

def _extract_job_id(href: str) -> str | None:
    match = _JOB_URL_PATTERN.search(href)
    return match.group(1) if match else None


def _find_job_block(anchor: Tag) -> Tag:
    """
    Walk up from a job-title anchor to a reasonably-scoped container
    (closest table cell/row, or the anchor's own parent) to search for
    nearby company/location/date text.
    """
    for ancestor in anchor.parents:
        if ancestor.name in ("td", "tr", "div"):
            return ancestor
    return anchor.parent or anchor


def _extract_company_and_location(block: Tag, exclude_text: str) -> tuple[str | None, str]:
    """
    Find the company/location line within a job block: LinkedIn's
    "Company · Location" (or "Company - Location") convention. Falls back
    to treating the whole remaining text as the company with an unknown
    location if no separator is found.
    """
    for candidate in block.find_all(string=True):
        text = candidate.strip()
        if not text or text == exclude_text:
            continue
        if _RELATIVE_TIME_PATTERN.fullmatch(text):
            continue  # this text node is just the "posted X ago" line

        parts = _COMPANY_LOCATION_SPLIT.split(text, maxsplit=1)
        if len(parts) == 2:
            company, location = (p.strip() for p in parts)
            if company:
                return company, location or "Unknown"

        # No separator found — treat as company-only text with unknown location.
        return text, "Unknown"

    return None, "Unknown"


def _extract_posted_date(block: Tag) -> datetime | None:
    """Estimate posted_date from a 'X days/hours/weeks ago' style string."""
    text = block.get_text(" ", strip=True)
    match = _RELATIVE_TIME_PATTERN.search(text)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2).lower()
    unit_kwarg = _RELATIVE_TIME_UNITS.get(unit)
    if unit_kwarg is None:
        return None

    if unit == "month":
        amount *= _MONTH_DAY_FACTOR["months"]

    delta = timedelta(**{unit_kwarg: amount})
    return datetime.now(timezone.utc) - delta


def _infer_market(location: str) -> Market | None:
    """Infer a configured target Market from free-text location, or None
    if it doesn't match any of the candidate's configured markets."""
    location_l = location.lower()
    for keyword, market in _MARKET_KEYWORDS.items():
        if keyword in location_l:
            return market
    return None
