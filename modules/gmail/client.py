"""
modules/gmail/client.py
--------------------------
Minimal Gmail API client wrapper.

Deliberately thin: this only does what modules/gmail/linkedin_alert_parser.py
needs — search messages by query, fetch a message's HTML body — not a
general-purpose Gmail SDK wrapper. Read-only scope only
(gmail.readonly): this module never sends, modifies, or deletes anything.

Two ways to get a GmailClient:
    1. GmailClient.from_settings() — builds real OAuth2 Credentials from
       config.settings.gmail (requires GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN
       to already be configured, e.g. from a prior one-time auth flow).
    2. GmailClient(service=<fake>) — inject a fake/mock googleapiclient
       Resource directly. This is how every test in this project exercises
       Gmail-dependent code without real credentials or network access.
"""

from __future__ import annotations

import base64
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from config.settings import settings
from core.exceptions import GmailError
from core.logger import get_logger

logger = get_logger(__name__)

# Read-only: this client only ever lists/fetches messages.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailClient:
    """Thin, read-only wrapper around the Gmail API."""

    def __init__(self, service: Resource | None = None, credentials: Credentials | None = None) -> None:
        """
        Args:
            service: Inject an existing googleapiclient Resource (e.g. a
                fake in tests). If given, credentials are never touched.
            credentials: Inject Credentials to build a real service from
                lazily, bypassing config-based token loading.
        """
        self._service = service
        self._credentials = credentials

    @classmethod
    def from_settings(cls) -> "GmailClient":
        """
        Build a GmailClient from config.settings.gmail. Raises GmailError
        immediately if required OAuth2 fields aren't configured, rather
        than failing confusingly on first use.
        """
        gmail_settings = settings.gmail
        if not gmail_settings.client_id or not gmail_settings.client_secret:
            raise GmailError(
                "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be configured to connect to Gmail."
            )
        if not gmail_settings.refresh_token:
            raise GmailError(
                "GMAIL_REFRESH_TOKEN must be configured — run the one-time OAuth2 "
                "authorization flow first to obtain one."
            )

        credentials = Credentials(
            token=None,
            refresh_token=gmail_settings.refresh_token,
            token_uri=gmail_settings.token_uri,
            client_id=gmail_settings.client_id,
            client_secret=gmail_settings.client_secret,
            scopes=SCOPES,
        )
        return cls(credentials=credentials)

    def _get_service(self) -> Resource:
        if self._service is not None:
            return self._service
        if self._credentials is None:
            raise GmailError("GmailClient has no service or credentials configured.")
        self._service = build("gmail", "v1", credentials=self._credentials)
        return self._service

    def search_messages(self, query: str, max_results: int = 50) -> list[str]:
        """
        Return message IDs matching a Gmail search query (the same query
        syntax as the Gmail search box, e.g. 'from:x@y.com subject:hello').
        """
        try:
            service = self._get_service()
            response = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            return [m["id"] for m in response.get("messages", [])]
        except GmailError:
            raise
        except Exception as e:
            raise GmailError(f"Gmail search failed for query '{query}': {e}") from e

    def get_message_metadata(self, message_id: str) -> dict[str, str]:
        """Return a small dict of header values: from, subject, date."""
        try:
            service = self._get_service()
            message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="metadata",
                     metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )
            headers = {
                h["name"].lower(): h["value"]
                for h in message.get("payload", {}).get("headers", [])
            }
            return {
                "from": headers.get("from", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
            }
        except GmailError:
            raise
        except Exception as e:
            raise GmailError(f"Failed to fetch metadata for message {message_id}: {e}") from e

    def get_message_html(self, message_id: str) -> str:
        """Fetch a message's HTML body by ID."""
        try:
            service = self._get_service()
            message = (
                service.users().messages().get(userId="me", id=message_id, format="full").execute()
            )
        except GmailError:
            raise
        except Exception as e:
            raise GmailError(f"Failed to fetch Gmail message {message_id}: {e}") from e

        html = self._find_html_part(message.get("payload", {}))
        if html is None:
            raise GmailError(f"No HTML part found in message {message_id}")
        return html

    @staticmethod
    def _find_html_part(part: dict[str, Any]) -> str | None:
        """Recursively walk a Gmail MIME payload tree for the text/html part."""
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")

        if mime_type == "text/html" and body_data:
            padded = body_data + "=" * (-len(body_data) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")

        for subpart in part.get("parts", []) or []:
            found = GmailClient._find_html_part(subpart)
            if found is not None:
                return found

        return None
