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
    print(settings.anthropic_api_key)
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Core application behaviour settings."""

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

    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL"
    )
    anthropic_max_tokens: int = Field(default=2000, alias="ANTHROPIC_MAX_TOKENS")

    # Optional OpenAI fallback
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")


class GmailSettings(BaseSettings):
    """Gmail API configuration."""

    client_id: str | None = Field(default=None, alias="GMAIL_CLIENT_ID")
    client_secret: str | None = Field(default=None, alias="GMAIL_CLIENT_SECRET")
    redirect_uri: str = Field(
        default="http://localhost", alias="GMAIL_REDIRECT_URI"
    )
    sender_email: str = Field(
        default="BadisMoalla@gmail.com", alias="GMAIL_SENDER_EMAIL"
    )


class LinkedInSettings(BaseSettings):
    """LinkedIn browser automation configuration."""

    email: str | None = Field(default=None, alias="LINKEDIN_EMAIL")
    password: str | None = Field(default=None, alias="LINKEDIN_PASSWORD")
    profile_url: str = Field(
        default="https://linkedin.com/in/badismoalla",
        alias="LINKEDIN_PROFILE_URL",
    )


class ScraperSettings(BaseSettings):
    """Job board scraping behaviour."""

    delay_min: float = Field(default=2.0, alias="SCRAPER_DELAY_MIN")
    delay_max: float = Field(default=5.0, alias="SCRAPER_DELAY_MAX")
    max_retries: int = Field(default=3, alias="SCRAPER_MAX_RETRIES")
    timeout: int = Field(default=30, alias="SCRAPER_TIMEOUT")
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


class Settings(BaseSettings):
    """
    Root settings object.
    Composes all sub-settings and loads from .env file.

    All sub-settings inherit from the same .env — pydantic-settings
    resolves the field aliases automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    ai: AISettings = AISettings()
    gmail: GmailSettings = GmailSettings()
    linkedin: LinkedInSettings = LinkedInSettings()
    scraper: ScraperSettings = ScraperSettings()
    tracker: TrackerSettings = TrackerSettings()


# Module-level singleton — import this everywhere
# Never instantiate Settings() more than once
settings = Settings()
