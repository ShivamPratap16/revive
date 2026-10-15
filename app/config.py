"""Application settings, read from environment variables (and a local .env file).

Every setting has a type, so a typo like MAX_CLOCK_SKEW_SECONDS=abc fails at startup
instead of crashing later in the middle of a request.
"""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalise_database_url(url: str) -> str:
    """Accept the URL that hosted Postgres providers (Neon, Render, Supabase) hand out.

    They give a libpq-style URL: postgresql://user:pw@host/db?sslmode=require&channel_binding=...
    The async driver (asyncpg, via SQLAlchemy) needs a different spelling:
      * scheme postgresql+asyncpg://
      * ssl=require instead of sslmode=require; libpq-only options (channel_binding) dropped
    Neon's "-pooler" host goes through PgBouncer in transaction mode, which breaks asyncpg's
    prepared statements, so we connect to the direct host (same name without "-pooler").
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode in ("require", "verify-ca", "verify-full"):
        query["ssl"] = "require"
    netloc = parts.netloc.replace("-pooler.", ".")
    return urlunsplit((scheme, netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://revive:revive@localhost:5442/revive"
    db_pool_size: int = 10  # small hosted plans allow few connections; lower it there
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

    # All-in-one mode, for hosting on a single free web service: the API process also
    # runs the worker loop and serves the voice simulator at /sim. Production would run
    # the worker as its own service (it shouldn't sleep when the web service does).
    run_worker_in_process: bool = False
    mount_simulator: bool = False

    # Call analysis (Phase 4). Without a key, the rule-based analyzer is used.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_rpm: float = 10  # stay under the free tier's per-minute limit
    gemini_rpd: int = 1000  # and its per-day limit
    gemini_timeout_seconds: float = 20
    analysis_batch_size: int = 5
    analysis_max_tries: int = 4  # transient LLM failures before falling back to rules
    breaker_failure_threshold: int = 5
    breaker_cooldown_seconds: float = 60

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        return normalise_database_url(v)

    @property
    def webhook_secret_list(self) -> list[str]:
        return [s.strip() for s in self.voice_webhook_secrets.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    # lru_cache makes this a singleton: the env is parsed once, not on every request.
    return Settings()
