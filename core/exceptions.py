"""
core/exceptions.py
------------------
Custom exception hierarchy for the entire application.

Why custom exceptions?
- Catch specific errors without catching everything
- Add context (which board failed, which job, which module)
- Cleaner error handling in main.py and CLI
"""


class JobSearchError(Exception):
    """Base exception for all application errors."""
    pass


class ConfigurationError(JobSearchError):
    """Raised when required configuration is missing or invalid."""
    pass


class ScraperError(JobSearchError):
    """Base for all scraping errors."""
    def __init__(self, message: str, board: str | None = None, url: str | None = None):
        self.board = board
        self.url = url
        super().__init__(f"[{board or 'unknown'}] {message}" + (f" | url={url}" if url else ""))


class ScraperRateLimitError(ScraperError):
    """Raised when a job board returns 429 Too Many Requests."""
    pass


class ScraperBlockedError(ScraperError):
    """Raised when a job board blocks the scraper (Captcha, 403, etc.)."""
    pass


class AIGenerationError(JobSearchError):
    """Raised when the AI fails to generate a message."""
    def __init__(self, message: str, message_type: str | None = None):
        self.message_type = message_type
        super().__init__(f"AI generation failed [{message_type or 'unknown'}]: {message}")


class TrackerError(JobSearchError):
    """Raised when the application tracker database fails."""
    pass


class DuplicateApplicationError(JobSearchError):
    """Raised when trying to apply to a job already in the tracker."""
    def __init__(self, job_id: str, company: str):
        self.job_id = job_id
        self.company = company
        super().__init__(f"Already applied to {company} (job_id={job_id})")


class GmailError(JobSearchError):
    """Raised when Gmail API calls fail."""
    pass


class LinkedInError(JobSearchError):
    """Raised when LinkedIn browser automation fails."""
    pass


class ProfileNotFoundError(JobSearchError):
    """Raised when profile.json is missing or malformed."""
    pass
