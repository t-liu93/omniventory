"""Settings service — typed accessor layer for user-facing configuration (M4 §4.1 / §9 Step 1).

``SettingsService`` provides:
- Code-defined **defaults**: un-set keys return their default value; the
  ``settings`` table stores ONLY user-overridden keys.
- **Typed accessors** for all reminders and channel configuration groups.
- **Validation** (delegated to the Pydantic schemas in ``app/schemas/settings.py``).
- **Write-only secret handling**: passwords and the integration token are
  stored in the KV store but are NEVER echoed in read paths; the response
  schema substitutes ``*_is_set`` boolean flags.
- **Public channel config getters** (Step 8): ``email_channel_config()`` and
  ``http_channel_config()`` return typed dataclasses consumed by channel
  adapters — never exposed via the API in plain text.

Key-name conventions (dot-namespaced, matching §3.1):
    reminders.best_before_lead_days
    reminders.warranty_lead_days
    reminders.low_stock_repeat_days  (JSON list of ints)
    reminders.scan_time

    channels.email.enabled
    channels.email.host
    channels.email.port
    channels.email.username
    channels.email.password          ← write-only secret
    channels.email.use_tls
    channels.email.from_address

    channels.http.enabled
    channels.http.webhook_url
    channels.http.auth_header        ← write-only (may contain credentials)
    channels.http.integration_token  ← write-only secret

    channels.mqtt.enabled
    channels.mqtt.host
    channels.mqtt.port
    channels.mqtt.username
    channels.mqtt.password           ← write-only secret
    channels.mqtt.topic_prefix
    channels.mqtt.use_tls
    channels.mqtt.discovery_enabled
    channels.mqtt.commands_enabled

All DB access is via ``SettingsRepository``; no raw queries here.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.repositories.setting import SettingsRepository
from app.schemas.settings import (
    ChannelsResponse,
    ChannelsUpdate,
    EmailChannelResponse,
    EmailChannelUpdate,
    HttpChannelResponse,
    HttpChannelUpdate,
    MqttChannelResponse,
    MqttChannelUpdate,
    RemindersSettings,
    RemindersUpdate,
    SettingsResponse,
    SettingsUpdate,
    ShoppingListSettings,
    ShoppingListUpdate,
)

# ---------------------------------------------------------------------------
# Code-defined defaults (table stores overrides only)
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    # Reminders
    "reminders.best_before_lead_days": 3,
    "reminders.warranty_lead_days": 30,
    "reminders.low_stock_repeat_days": [1, 3, 7],
    "reminders.scan_time": "08:00",
    # Shopping list
    "shopping_list.auto_add_low_stock": True,
    # Email channel
    "channels.email.enabled": False,
    "channels.email.host": None,
    "channels.email.port": None,
    "channels.email.username": None,
    "channels.email.password": None,  # secret — never echoed
    "channels.email.encryption": "none",
    "channels.email.from_address": None,
    "channels.email.from_name": None,
    # HTTP channel
    "channels.http.enabled": False,
    "channels.http.webhook_url": None,
    "channels.http.auth_header": None,  # secret — never echoed
    "channels.http.integration_token": None,  # secret — never echoed
    # MQTT channel
    "channels.mqtt.enabled": False,
    "channels.mqtt.host": None,
    "channels.mqtt.port": None,
    "channels.mqtt.username": None,
    "channels.mqtt.password": None,  # secret — never echoed
    "channels.mqtt.topic_prefix": "omniventory",
    "channels.mqtt.use_tls": False,
    "channels.mqtt.discovery_enabled": False,
    "channels.mqtt.commands_enabled": False,
}

# Keys that hold write-only secrets (never echoed in read paths)
_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "channels.email.password",
        "channels.http.auth_header",
        "channels.http.integration_token",
        "channels.mqtt.password",
    }
)


# ---------------------------------------------------------------------------
# Channel config dataclasses (for adapter use only — never echoed via API)
# ---------------------------------------------------------------------------


@dataclass
class EmailChannelConfig:
    """Full (decrypted) email channel configuration consumed by EmailChannel.

    Never returned from API routes — use SettingsService.email_channel_config()
    inside channel adapter code only.
    """

    enabled: bool
    host: str | None
    port: int | None
    username: str | None
    password: str | None  # noqa: S105 — internal use only, never serialised
    encryption: Literal["none", "starttls", "ssl"]
    from_address: str | None
    from_name: str | None


@dataclass
class HttpChannelConfig:
    """Full (decrypted) HTTP channel configuration consumed by HttpChannel.

    Never returned from API routes — use SettingsService.http_channel_config()
    inside channel adapter code only.
    """

    enabled: bool
    webhook_url: str | None
    auth_header: str | None  # noqa: S105 — internal use only, never serialised
    integration_token: str | None  # noqa: S105 — internal use only, never serialised


@dataclass
class MqttChannelConfig:
    """Full (decrypted) MQTT channel configuration consumed by MqttBridge / MqttChannel.

    Never returned from API routes — use SettingsService.mqtt_channel_config()
    inside the bridge and channel adapter only.
    """

    enabled: bool
    host: str | None
    port: int | None
    username: str | None
    password: str | None  # noqa: S105 — internal use only, never serialised
    topic_prefix: str
    use_tls: bool
    discovery_enabled: bool
    commands_enabled: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _encode(value: Any) -> str:
    """Encode a Python value to its text storage representation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    if value is None:
        return ""
    return str(value)


def _decode(raw: str, default: Any) -> Any:
    """Decode a stored text value back to Python using the default's type as a hint."""
    if default is None:
        # Stored as empty string when cleared, or may not exist
        return raw if raw else None
    if isinstance(default, bool):
        return raw.lower() == "true"
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, list):
        return json.loads(raw)
    # fallback: str
    return raw


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SettingsService:
    """Typed read/write access to the user-facing settings KV store."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = SettingsRepository(db)

    # ------------------------------------------------------------------
    # Internal: get with default
    # ------------------------------------------------------------------

    def _get_value(self, key: str) -> Any:
        """Return the typed value for ``key``, falling back to the code default."""
        raw = self._repo.get(key)
        default = _DEFAULTS[key]
        if raw is None:
            return default
        if raw == "" and default is None:
            return None
        return _decode(raw, default)

    def _set_value(self, key: str, value: Any) -> None:
        """Persist a (possibly None) value for ``key``."""
        if value is None:
            # Explicit clear — store empty string (sentinel for "user cleared this")
            self._repo.set(key, "")
        else:
            self._repo.set(key, _encode(value))

    # ------------------------------------------------------------------
    # Read: full settings response
    # ------------------------------------------------------------------

    def get_settings(self) -> SettingsResponse:
        """Return the full typed settings payload (secrets masked)."""
        return SettingsResponse(
            reminders=self._build_reminders_response(),
            channels=ChannelsResponse(
                email=self._build_email_response(),
                http=self._build_http_response(),
                mqtt=self._build_mqtt_response(),
            ),
            shopping_list=self._build_shopping_list_response(),
        )

    def _build_shopping_list_response(self) -> ShoppingListSettings:
        return ShoppingListSettings(
            auto_add_low_stock=self._get_value("shopping_list.auto_add_low_stock"),
        )

    def _build_reminders_response(self) -> RemindersSettings:
        return RemindersSettings(
            best_before_lead_days=self._get_value("reminders.best_before_lead_days"),
            warranty_lead_days=self._get_value("reminders.warranty_lead_days"),
            low_stock_repeat_days=self._get_value("reminders.low_stock_repeat_days"),
            scan_time=self._get_value("reminders.scan_time"),
        )

    def _email_encryption(self) -> Literal["none", "starttls", "ssl"]:
        """Return the effective encryption mode for the email channel.

        Resolution order (legacy shim for upgrade compatibility):
        1. If ``channels.email.encryption`` is explicitly stored → return it.
        2. Else if legacy ``channels.email.use_tls`` is explicitly stored →
           map ``"true"`` → ``"starttls"``; anything else → ``"none"``.
        3. Else → ``"none"`` (the new default).

        This allows an instance that had ``use_tls=true`` saved before the
        upgrade to keep its STARTTLS behaviour without a data migration.
        """
        # 1. Check for the new key first.
        raw_encryption = self._repo.get("channels.email.encryption")
        if raw_encryption is not None:
            # Stored value is present; validate it.
            val = raw_encryption.strip()
            if val == "starttls":
                return "starttls"
            if val == "ssl":
                return "ssl"
            # Stored value is "none" or unrecognised (fall through to default).
            return "none"

        # 2. Legacy shim: check the old use_tls key.
        raw_use_tls = self._repo.get("channels.email.use_tls")
        if raw_use_tls is not None:
            return "starttls" if raw_use_tls.lower() == "true" else "none"

        # 3. Default.
        return "none"

    def _build_email_response(self) -> EmailChannelResponse:
        return EmailChannelResponse(
            enabled=self._get_value("channels.email.enabled"),
            host=self._get_value("channels.email.host"),
            port=self._get_value("channels.email.port"),
            username=self._get_value("channels.email.username"),
            password_is_set=bool(self._get_value("channels.email.password")),
            encryption=self._email_encryption(),
            from_address=self._get_value("channels.email.from_address"),
            from_name=self._get_value("channels.email.from_name"),
        )

    def _build_http_response(self) -> HttpChannelResponse:
        return HttpChannelResponse(
            enabled=self._get_value("channels.http.enabled"),
            webhook_url=self._get_value("channels.http.webhook_url"),
            auth_header_is_set=bool(self._get_value("channels.http.auth_header")),
            integration_token_is_set=bool(self._get_value("channels.http.integration_token")),
        )

    def _build_mqtt_response(self) -> MqttChannelResponse:
        return MqttChannelResponse(
            enabled=self._get_value("channels.mqtt.enabled"),
            host=self._get_value("channels.mqtt.host"),
            port=self._get_value("channels.mqtt.port"),
            username=self._get_value("channels.mqtt.username"),
            password_is_set=bool(self._get_value("channels.mqtt.password")),
            topic_prefix=self._get_value("channels.mqtt.topic_prefix"),
            use_tls=self._get_value("channels.mqtt.use_tls"),
            discovery_enabled=self._get_value("channels.mqtt.discovery_enabled"),
            commands_enabled=self._get_value("channels.mqtt.commands_enabled"),
        )

    # ------------------------------------------------------------------
    # Write: apply a partial update
    # ------------------------------------------------------------------

    def apply_update(self, update: SettingsUpdate) -> SettingsResponse:
        """Apply a partial update and return the new full settings response.

        Only fields that are explicitly set in the update payload are written;
        omitted (``None``) fields are left unchanged.  Secrets (passwords,
        tokens) are written as-is when provided; empty string or ``None``
        clears them.

        Flushes the session before returning the updated state so that
        ``db.get()`` / ``_get_value()`` can see the merged rows within the
        same transaction (without requiring a full commit first).

        The ``get_db`` dependency commits after the route handler returns.
        """
        if update.reminders is not None:
            self._apply_reminders_update(update.reminders)
        if update.channels is not None:
            self._apply_channels_update(update.channels)
        if update.shopping_list is not None:
            self._apply_shopping_list_update(update.shopping_list)
        # Flush so the identity map reflects the merged rows before we read
        # back the full settings in the same transaction.
        self._db.flush()
        return self.get_settings()

    def _apply_shopping_list_update(self, upd: ShoppingListUpdate) -> None:
        if upd.auto_add_low_stock is not None:
            self._set_value("shopping_list.auto_add_low_stock", upd.auto_add_low_stock)

    def _apply_reminders_update(self, upd: RemindersUpdate) -> None:
        if upd.best_before_lead_days is not None:
            self._set_value("reminders.best_before_lead_days", upd.best_before_lead_days)
        if upd.warranty_lead_days is not None:
            self._set_value("reminders.warranty_lead_days", upd.warranty_lead_days)
        if upd.low_stock_repeat_days is not None:
            self._set_value("reminders.low_stock_repeat_days", upd.low_stock_repeat_days)
        if upd.scan_time is not None:
            self._set_value("reminders.scan_time", upd.scan_time)

    def _apply_channels_update(self, upd: ChannelsUpdate) -> None:
        if upd.email is not None:
            self._apply_email_update(upd.email)
        if upd.http is not None:
            self._apply_http_update(upd.http)
        if upd.mqtt is not None:
            self._apply_mqtt_update(upd.mqtt)

    def _apply_email_update(self, upd: EmailChannelUpdate) -> None:
        if upd.enabled is not None:
            self._set_value("channels.email.enabled", upd.enabled)
        if upd.host is not None:
            self._set_value("channels.email.host", upd.host)
        if upd.port is not None:
            self._set_value("channels.email.port", upd.port)
        if upd.username is not None:
            self._set_value("channels.email.username", upd.username)
        # Secret: always process (None = clear, "" = clear, non-empty = set)
        if upd.password is not None:
            self._set_value("channels.email.password", upd.password if upd.password else None)
        if upd.encryption is not None:
            self._repo.set("channels.email.encryption", upd.encryption)
        if upd.from_address is not None:
            self._set_value("channels.email.from_address", upd.from_address)
        # from_name: accept explicit None to clear, or a string to set
        if "from_name" in upd.model_fields_set:
            self._set_value("channels.email.from_name", upd.from_name)

    def _apply_http_update(self, upd: HttpChannelUpdate) -> None:
        if upd.enabled is not None:
            self._set_value("channels.http.enabled", upd.enabled)
        if upd.webhook_url is not None:
            self._set_value("channels.http.webhook_url", upd.webhook_url)
        # Secrets: always process when provided
        if upd.auth_header is not None:
            self._set_value(
                "channels.http.auth_header", upd.auth_header if upd.auth_header else None
            )
        if upd.integration_token is not None:
            self._set_value(
                "channels.http.integration_token",
                upd.integration_token if upd.integration_token else None,
            )

    def _apply_mqtt_update(self, upd: MqttChannelUpdate) -> None:
        if upd.enabled is not None:
            self._set_value("channels.mqtt.enabled", upd.enabled)
        if upd.host is not None:
            self._set_value("channels.mqtt.host", upd.host)
        if upd.port is not None:
            self._set_value("channels.mqtt.port", upd.port)
        if upd.username is not None:
            self._set_value("channels.mqtt.username", upd.username)
        # Secret
        if upd.password is not None:
            self._set_value("channels.mqtt.password", upd.password if upd.password else None)
        if upd.topic_prefix is not None:
            self._set_value("channels.mqtt.topic_prefix", upd.topic_prefix)
        if upd.use_tls is not None:
            self._set_value("channels.mqtt.use_tls", upd.use_tls)
        if upd.discovery_enabled is not None:
            self._set_value("channels.mqtt.discovery_enabled", upd.discovery_enabled)
        if upd.commands_enabled is not None:
            self._set_value("channels.mqtt.commands_enabled", upd.commands_enabled)

    # ------------------------------------------------------------------
    # Convenience accessors (used by other services in later steps)
    # ------------------------------------------------------------------

    def best_before_lead_days(self) -> int:
        """Return the global best-before lead in days."""
        return int(self._get_value("reminders.best_before_lead_days"))

    def warranty_lead_days(self) -> int:
        """Return the global warranty lead in days."""
        return int(self._get_value("reminders.warranty_lead_days"))

    def low_stock_repeat_days(self) -> list[int]:
        """Return the low-stock repeat schedule (sorted, deduped)."""
        raw: list[int] = self._get_value("reminders.low_stock_repeat_days")
        return sorted(set(raw))

    def scan_time(self) -> str:
        """Return the daily scan time as HH:MM."""
        return str(self._get_value("reminders.scan_time"))

    def best_before_lead_days_value(self) -> int:
        """Alias matching the reminder engine accessor name pattern."""
        return self.best_before_lead_days()

    def shopping_list_auto_add(self) -> bool:
        """Return whether auto-adding low-stock items to the shopping list is enabled.

        Default is ``True``; can be toggled via ``PATCH /settings`` with
        ``{shopping_list: {auto_add_low_stock: false}}``.
        """
        return bool(self._get_value("shopping_list.auto_add_low_stock"))

    # ------------------------------------------------------------------
    # Public channel config getters (Step 8)
    # These return typed dataclasses for use by channel adapters only —
    # they must NEVER be serialised and returned via the API in plain text.
    # ------------------------------------------------------------------

    def email_channel_config(self) -> EmailChannelConfig:
        """Return the full (decrypted) email channel config for adapter use."""
        return EmailChannelConfig(
            enabled=self._get_value("channels.email.enabled"),
            host=self._get_value("channels.email.host"),
            port=self._get_value("channels.email.port"),
            username=self._get_value("channels.email.username"),
            password=self._get_value("channels.email.password"),
            encryption=self._email_encryption(),
            from_address=self._get_value("channels.email.from_address"),
            from_name=self._get_value("channels.email.from_name"),
        )

    def http_channel_config(self) -> HttpChannelConfig:
        """Return the full (decrypted) HTTP channel config for adapter use."""
        return HttpChannelConfig(
            enabled=self._get_value("channels.http.enabled"),
            webhook_url=self._get_value("channels.http.webhook_url"),
            auth_header=self._get_value("channels.http.auth_header"),
            integration_token=self._get_value("channels.http.integration_token"),
        )

    def mqtt_channel_config(self) -> MqttChannelConfig:
        """Return the full (decrypted) MQTT channel config for adapter/bridge use."""
        raw_port = self._get_value("channels.mqtt.port")
        # Port is stored as text (default is None); cast to int when present.
        port: int | None = int(raw_port) if raw_port is not None else None
        return MqttChannelConfig(
            enabled=self._get_value("channels.mqtt.enabled"),
            host=self._get_value("channels.mqtt.host"),
            port=port,
            username=self._get_value("channels.mqtt.username"),
            password=self._get_value("channels.mqtt.password"),
            topic_prefix=self._get_value("channels.mqtt.topic_prefix"),
            use_tls=self._get_value("channels.mqtt.use_tls"),
            discovery_enabled=self._get_value("channels.mqtt.discovery_enabled"),
            commands_enabled=self._get_value("channels.mqtt.commands_enabled"),
        )

    def get_or_create_integration_token(self) -> str:
        """Return the integration token, generating and persisting one if absent.

        Called during ``GET /settings`` when ``channels.http.enabled`` is True
        and no token has been set yet.  The generated token is a 32-byte
        URL-safe secret stored in the ``settings`` KV table (same as any other
        channel secret — write-only on the API surface).

        Returns
        -------
        str
            The token value (plain text — for internal use only; never echoed
            in API responses, only ``integration_token_is_set: bool`` is).
        """
        existing = self._get_value("channels.http.integration_token")
        if existing:
            return str(existing)
        token = secrets.token_urlsafe(32)
        self._set_value("channels.http.integration_token", token)
        self._db.flush()
        return token
