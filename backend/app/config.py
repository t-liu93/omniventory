"""Application settings loaded from environment / .env file.

No secrets are hardcoded here; every sensitive field is required at runtime.
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
    # Security (required — no defaults so misconfiguration fails loudly)   #
    # ------------------------------------------------------------------ #
    secret_key: str = Field(
        description=(
            "Secret key used for signing sessions and other cryptographic operations. "
            "Must be provided via environment variable or .env. "
            "No default — omitting it is a configuration error."
        ),
    )

    # ------------------------------------------------------------------ #
    # Persistence                                                           #
    # ------------------------------------------------------------------ #
    database_url: str = Field(
        default="sqlite:///./data/omniventory.db",
        description="SQLAlchemy database URL.",
    )

    # ------------------------------------------------------------------ #
    # Session cookie                                                        #
    # ------------------------------------------------------------------ #
    session_cookie_name: str = Field(
        default="omniventory_session",
        description="Name of the HttpOnly session cookie.",
    )

    # ------------------------------------------------------------------ #
    # Admin bootstrap                                                       #
    # ------------------------------------------------------------------ #
    admin_bootstrap_email: str | None = Field(
        default=None,
        description="Email for the bootstrapped admin user (first-run only).",
    )
    admin_bootstrap_password: str | None = Field(
        default=None,
        description="Password for the bootstrapped admin user (first-run only).",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Using ``lru_cache`` ensures the env / .env file is read exactly once per
    process, while keeping import-time side-effects out of module scope.
    Call ``get_settings.cache_clear()`` in tests to force a fresh read.
    """
    # pydantic-settings populates required fields (secret_key) from the
    # environment at runtime; mypy cannot infer this, so we suppress the
    # "missing argument" false-positive here.
    return Settings()  # type: ignore[call-arg]
