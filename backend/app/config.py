"""Application settings loaded from environment / .env file.

No secrets are hardcoded here; sensitive fields either come from the
environment or are auto-generated and persisted at first run.
``get_settings()`` is a cached accessor so nothing is read at import time.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pydantic-settings configuration for Omniventory.

    Values are loaded from environment variables or a ``.env`` file in the
    backend working directory.  Field names map 1-to-1 to env var names
    (case-insensitive by default in pydantic-settings).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Extra env vars are silently ignored — don't fail on unrecognised vars.
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # API surface                                                           #
    # ------------------------------------------------------------------ #
    api_prefix: str = Field(default="/api", description="Mount prefix for all API routers.")
    api_version: int = Field(
        default=1,
        description=(
            "Integer compatibility version surfaced in /health. "
            "Clients use this to detect API compatibility without URL versioning."
        ),
    )

    # ------------------------------------------------------------------ #
    # Runtime environment                                                   #
    # ------------------------------------------------------------------ #
    environment: str = Field(
        default="development",
        description="Runtime environment: development | production | test.",
    )

    # ------------------------------------------------------------------ #
    # Security (optional — auto-generated & persisted on first run)        #
    # ------------------------------------------------------------------ #
    secret_key: str | None = Field(
        default=None,
        description=(
            "Secret key used for signing sessions and other cryptographic operations. "
            "Leave unset (None / blank) to auto-generate and persist in the app_config "
            "table on first run.  Set explicitly to override or to rotate the key "
            "(rotating invalidates all existing sessions)."
        ),
    )

    # ------------------------------------------------------------------ #
    # Persistence                                                           #
    # ------------------------------------------------------------------ #
    database_url: str = Field(
        default="sqlite:///./data/omniventory.db",
        description="SQLAlchemy database URL.",
    )
    data_dir: str = Field(
        default="./data",
        description=(
            "Root data directory for persistent storage.  SQLite DB lives here by default.  "
            "Media files are stored under ``<data_dir>/media/``.  "
            "Maps to the bind-mounted ``DATA_DIR`` in the Docker Compose setup."
        ),
    )

    # ------------------------------------------------------------------ #
    # Session cookie                                                        #
    # ------------------------------------------------------------------ #
    session_cookie_name: str = Field(
        default="omniventory_session",
        description="Name of the HttpOnly session cookie.",
    )

    # ------------------------------------------------------------------ #
    # Scheduler                                                             #
    # ------------------------------------------------------------------ #
    scheduler_enabled: bool = Field(
        default=True,
        description=(
            "Enable the APScheduler background scheduler for daily reminder scans. "
            "Set to false in CI or tests to prevent background threads from starting. "
            "The scheduler is also suppressed when environment == 'test' regardless "
            "of this flag."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Using ``lru_cache`` ensures the env / .env file is read exactly once per
    process, while keeping import-time side-effects out of module scope.
    Call ``get_settings.cache_clear()`` in tests to force a fresh read.
    """
    return Settings()
