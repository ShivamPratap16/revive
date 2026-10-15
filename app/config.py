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

    # Worker (Phase 2)
    lease_seconds: int = 300  # how long a worker may hold an attempt before it's reclaimed
    delivery_timeout_seconds: float = 10
    retry_base_seconds: float = 30
    retry_cap_seconds: float = 3600
    detector_batch_size: int = 500
    dispatch_batch_size: int = 100
    # Simulated providers: probability that a send fails temporarily (to watch retries).
    sim_failure_rate: float = 0.0

    # Voice provider (Phase 3). With no voice_api_url, voice uses the in-process simulator
    # (no webhooks). Point it at simulator/ (or a real provider) for the full call flow.
    voice_provider: str = "revive-sim"
    voice_api_url: str | None = None
    voice_api_key: str = "sim_key_dev"
    # Comma-separated. Every listed secret is accepted, so the secret can be rotated
    # without downtime: add the new one, switch the provider over, remove the old one.
    voice_webhook_secrets: str = "whsec_dev_only_change_me"
    webhook_tolerance_seconds: int = 300
    public_base_url: str = "http://localhost:8077"

    @property
    def webhook_secret_list(self) -> list[str]:
        return [s.strip() for s in self.voice_webhook_secrets.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    # lru_cache makes this a singleton: the env is parsed once, not on every request.
    return Settings()
