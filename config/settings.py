"""
config/settings.py
------------------
Single source of truth for all application configuration.

Uses pydantic-settings to:
- Load values from .env file automatically
- Validate every value at startup (wrong type = crash early, not silently)
- Provide typed access throughout the codebase (no os.getenv() scattered everywhere)

Usage:
    from config.settings import settings
    print(settings.ai.anthropic_api_key)

IMPORTANT — nested settings and .env loading:
Each sub-settings group is its own BaseSettings subclass with its own
SettingsConfigDict(env_file=".env"). This is deliberate: pydantic-settings
only reads `.env` for the model whose configuration points at it, and a
sub-settings instance created inside the root class body is built *before*
the root Settings ever gets a chance to load `.env`. Giving every nested
class its own env_file makes each one load `.env` directly at
instantiation time, and the root Settings constructs them via
Field(default_factory=...) so a fresh Settings() always builds fresh
sub-settings against the current environment rather than reusing stale
class-attribute defaults.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"
_ENV_FILE_ENCODING = "utf-8"


class AppSettings(BaseSettings):
    """Core application behaviour settings."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    dry_run: bool = Field(default=True, alias="APP_DRY_RUN")

    @field_validator("env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "production"}
        if v not in allowed:
            raise ValueError(f"APP_ENV must be one of {allowed}")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"APP_LOG_LEVEL must be one of {allowed}")
        return v.upper()


class AISettings(BaseSettings):
    """AI provider configuration."""

    # Optional so the CLI (e.g. `--help`) starts without a configured key.
    # Enforced when a live (non-dry-run) ClaudeGenerator is constructed —
    # see modules/ai/claude_generator.py.
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str | None = Field(
        default=None, alias="ANTHROPIC_API_KEY"
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL"
    )
    anthropic_max_tokens: int = Field(default=2000, alias="ANTHROPIC_MAX_TOKENS")

    # Optional OpenAI fallback
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")


class GmailSettings(BaseSettings):
    """Gmail API configuration."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    client_id: str | None = Field(default=None, alias="GMAIL_CLIENT_ID")
    client_secret: str | None = Field(default=None, alias="GMAIL_CLIENT_SECRET")
    redirect_uri: str = Field(
        default="http://localhost", alias="GMAIL_REDIRECT_URI"
    )
    sender_email: str = Field(
        default="BadisMoalla@gmail.com", alias="GMAIL_SENDER_EMAIL"
    )
    refresh_token: str | None = Field(
        default=None,
        alias="GMAIL_REFRESH_TOKEN",
        description="OAuth2 refresh token from a prior authorization flow.",
    )
    token_uri: str = Field(default="https://oauth2.googleapis.com/token", alias="GMAIL_TOKEN_URI")


class LinkedInSettings(BaseSettings):
    """LinkedIn browser automation configuration."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    email: str | None = Field(default=None, alias="LINKEDIN_EMAIL")
    password: str | None = Field(default=None, alias="LINKEDIN_PASSWORD")
    profile_url: str = Field(
        default="https://linkedin.com/in/badismoalla",
        alias="LINKEDIN_PROFILE_URL",
    )


class ScraperSettings(BaseSettings):
    """Job board scraping behaviour."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    delay_min: float = Field(default=2.0, alias="SCRAPER_DELAY_MIN")
    delay_max: float = Field(default=5.0, alias="SCRAPER_DELAY_MAX")
    max_retries: int = Field(default=3, alias="SCRAPER_MAX_RETRIES")
    timeout: int = Field(default=30, alias="SCRAPER_TIMEOUT")
    max_pages: int = Field(default=5, alias="SCRAPER_MAX_PAGES")
    requests_per_minute: int | None = Field(
        default=None,
        alias="SCRAPER_REQUESTS_PER_MINUTE",
        description="Optional hard cap on requests/minute, on top of the polite delay_min/delay_max.",
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        alias="SCRAPER_USER_AGENT",
    )

    # Job board base URLs
    pracuj_url: str = Field(
        default="https://www.pracuj.pl", alias="PRACUJ_BASE_URL"
    )
    nofluffjobs_url: str = Field(
        default="https://nofluffjobs.com", alias="NOFLUFFJOBS_BASE_URL"
    )
    justjoinit_url: str = Field(
        default="https://justjoin.it", alias="JUSTJOINIT_BASE_URL"
    )
    bulldogjob_url: str = Field(
        default="https://bulldogjob.pl", alias="BULLDOGJOB_BASE_URL"
    )
    bayt_url: str = Field(
        default="https://www.bayt.com", alias="BAYT_BASE_URL"
    )


class TrackerSettings(BaseSettings):
    """Application tracking behaviour."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    daily_min_applications: int = Field(
        default=10, alias="DAILY_MIN_APPLICATIONS"
    )
    daily_max_applications: int = Field(
        default=20, alias="DAILY_MAX_APPLICATIONS"
    )
    follow_up_days: int = Field(default=7, alias="FOLLOW_UP_DAYS")
    notification_email: str = Field(
        default="BadisMoalla@gmail.com", alias="NOTIFICATION_EMAIL"
    )


class PipelineSettings(BaseSettings):
    """
    Full application-pipeline behaviour (core/pipeline.py): which scrapers
    run on a normal pipeline execution, and the score cutoff for
    generating AI messages.

    enabled_scrapers is comma-separated (not a JSON list) so it's easy to
    set from a plain .env line. Defaults to the three job-board scrapers
    only — company ATS scrapers (greenhouse, lever, smartrecruiters,
    workday) require config/company_sources.json entries to do anything
    useful, and enabling them by default would mean a first-run pipeline
    silently hits real companies' ATS endpoints using the sample/unverified
    entries in that file. Opt in explicitly once you've reviewed and
    verified your own company list. The Gmail/LinkedIn-alert provider
    ("gmail_linkedin") is likewise opt-in — it requires GMAIL_CLIENT_ID/
    SECRET/REFRESH_TOKEN to be configured; if enabled without them it fails
    softly per run (recorded like any other failed source), not at startup.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    score_threshold: int = Field(
        default=70,
        alias="PIPELINE_SCORE_THRESHOLD",
        description="Minimum JobMatcher score (0-100) for a job to be accepted and get AI messages generated.",
    )
    enabled_scrapers: str = Field(
        default="nofluffjobs,pracuj,justjoinit",
        alias="PIPELINE_ENABLED_SCRAPERS",
        description=(
            "Comma-separated discovery provider keys to run. Job boards: nofluffjobs, pracuj, "
            "justjoinit (public scrapers — see known live-reliability limitations in README). "
            "Company ATS platforms (need config/company_sources.json): "
            "greenhouse, lever, smartrecruiters, workday. Gmail/LinkedIn Job Alert ingestion "
            "(needs Gmail OAuth2 credentials): gmail_linkedin."
        ),
    )

    @property
    def enabled_scrapers_list(self) -> list[str]:
        return [key.strip().lower() for key in self.enabled_scrapers.split(",") if key.strip()]


class Settings(BaseSettings):
    """
    Root settings object.
    Composes all sub-settings and loads from .env file.

    Each sub-settings group is built via Field(default_factory=...) so a
    fresh Settings() always constructs fresh nested instances that read
    `.env` at construction time (see module docstring for why).
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        case_sensitive=False,
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    ai: AISettings = Field(default_factory=AISettings)
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    linkedin: LinkedInSettings = Field(default_factory=LinkedInSettings)
    scraper: ScraperSettings = Field(default_factory=ScraperSettings)
    tracker: TrackerSettings = Field(default_factory=TrackerSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)


# Module-level singleton — import this everywhere
# Never instantiate Settings() more than once
settings = Settings()
