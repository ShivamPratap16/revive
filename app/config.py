"""Application settings, read from environment variables (and a local .env file).

Every setting has a type, so a typo like MAX_CLOCK_SKEW_SECONDS=abc fails at startup
instead of crashing later in the middle of a request.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://revive:revive@localhost:5442/revive"
    redis_url: str = "redis://localhost:6392/0"
    log_level: str = "INFO"
    max_clock_skew_seconds: int = 300
    max_events_per_batch: int = 100


@lru_cache
def get_settings() -> Settings:
    # lru_cache makes this a singleton: the env is parsed once, not on every request.
    return Settings()
