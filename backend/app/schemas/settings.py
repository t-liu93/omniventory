"""Pydantic schemas for the settings API (M4 §4.11 / §7.3).

Two top-level schemas:
- ``SettingsResponse``  — returned by ``GET /settings`` (secrets masked as ``*_is_set`` booleans).
- ``SettingsUpdate``    — accepted by ``PATCH /settings`` (secrets accepted as plain text; set to
                          empty string or ``None`` to clear).

Structure
---------
Both schemas are organised into two groups:

``reminders``
    Global reminder lead-time configuration.
    - ``best_before_lead_days``  int, ≥ 0
    - ``warranty_lead_days``     int, ≥ 0
    - ``low_stock_repeat_days``  list[int], each ≥ 1
    - ``scan_time``              "HH:MM" string

``channels``
    External notification channels (email, HTTP, MQTT).  Each channel sub-object
    carries ``enabled`` plus channel-specific fields.

Write-only secrets (M4 §2 "Channel secrets are write-only"):
    ``channels.email.password``           → ``password_is_set`` in Response
    ``channels.mqtt.password``            → ``password_is_set`` in Response
    ``channels.http.integration_token``   → ``integration_token_is_set`` in Response

``auth_header`` note:
    ``channels.http.auth_header`` may contain credentials (e.g. "Bearer <token>").
    It is therefore also treated as write-only and masked in responses
    (``auth_header_is_set: bool``), consistent with the channel-secrets policy.

Validation:
    All validation is via Pydantic field constraints (ge, le, regex, …); a
    validation failure propagates as a ``RequestValidationError`` which the
    exception handler in ``create_app`` converts to ``validation.invalid_input``
    (existing code, no new error code needed — M4 §2 / §10 Step 1).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCAN_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _validate_scan_time(v: str) -> str:
    """Ensure scan_time is a valid HH:MM string (00:00–23:59)."""
    if not _SCAN_TIME_RE.match(v):
        raise ValueError("scan_time must be in HH:MM format")
    hours, minutes = int(v[:2]), int(v[3:])
    if hours > 23 or minutes > 59:
        raise ValueError("scan_time hour must be 0–23 and minute 0–59")
    return v


# ---------------------------------------------------------------------------
# Reminders sub-schemas
# ---------------------------------------------------------------------------


class RemindersSettings(BaseModel):
    """Global reminder configuration."""

    best_before_lead_days: int = Field(ge=0)
    warranty_lead_days: int = Field(ge=0)
    low_stock_repeat_days: list[int]
    scan_time: str

    @field_validator("low_stock_repeat_days")
    @classmethod
    def _validate_repeat_days(cls, v: list[int]) -> list[int]:
        for item in v:
            if item < 1:
                raise ValueError("each entry in low_stock_repeat_days must be ≥ 1")
        return v

    @field_validator("scan_time")
    @classmethod
    def _validate_scan_time(cls, v: str) -> str:
        return _validate_scan_time(v)


class RemindersUpdate(BaseModel):
    """Partial update for reminder configuration (all fields optional)."""

    best_before_lead_days: int | None = Field(default=None, ge=0)
    warranty_lead_days: int | None = Field(default=None, ge=0)
    low_stock_repeat_days: list[int] | None = None
    scan_time: str | None = None

    @field_validator("low_stock_repeat_days")
    @classmethod
    def _validate_repeat_days(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        for item in v:
            if item < 1:
                raise ValueError("each entry in low_stock_repeat_days must be ≥ 1")
        return v

    @field_validator("scan_time")
    @classmethod
    def _validate_scan_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_scan_time(v)


# ---------------------------------------------------------------------------
# Email channel sub-schemas
# ---------------------------------------------------------------------------


class EmailChannelResponse(BaseModel):
    """Email channel config returned to the client (password masked)."""

    enabled: bool
    host: str | None
    port: int | None
    username: str | None
    password_is_set: bool  # write-only secret masked as boolean
    encryption: Literal["none", "starttls", "ssl"]
    from_address: str | None
    from_name: str | None


class EmailChannelUpdate(BaseModel):
    """Partial update for email channel config."""

    enabled: bool | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None  # write-only; empty string = clear
    encryption: Literal["none", "starttls", "ssl"] | None = None
    from_address: str | None = None
    from_name: str | None = None


class EmailTestResult(BaseModel):
    """Result of a POST /settings/email/test request."""

    ok: bool
    detail: str | None
    recipient: str


# ---------------------------------------------------------------------------
# HTTP channel sub-schemas
# ---------------------------------------------------------------------------


class HttpChannelResponse(BaseModel):
    """HTTP channel config returned to the client (secrets masked)."""

    enabled: bool
    webhook_url: str | None
    auth_header_is_set: bool  # write-only (may contain credentials)
    integration_token_is_set: bool  # write-only


class HttpChannelUpdate(BaseModel):
    """Partial update for HTTP channel config."""

    enabled: bool | None = None
    webhook_url: str | None = None
    auth_header: str | None = None  # write-only; empty string = clear
    integration_token: str | None = None  # write-only; empty string = clear


# ---------------------------------------------------------------------------
# MQTT channel sub-schemas
# ---------------------------------------------------------------------------


class MqttChannelResponse(BaseModel):
    """MQTT channel config returned to the client (password masked)."""

    enabled: bool
    host: str | None
    port: int | None
    username: str | None
    password_is_set: bool  # write-only secret masked as boolean
    topic_prefix: str | None
    use_tls: bool
    discovery_enabled: bool
    commands_enabled: bool


class MqttChannelUpdate(BaseModel):
    """Partial update for MQTT channel config."""

    enabled: bool | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None  # write-only; empty string = clear
    topic_prefix: str | None = None
    use_tls: bool | None = None
    discovery_enabled: bool | None = None
    commands_enabled: bool | None = None


class MqttTestResult(BaseModel):
    """Result of a POST /settings/mqtt/test request."""

    ok: bool
    detail: str | None
    topic: str


# ---------------------------------------------------------------------------
# Channels container sub-schemas
# ---------------------------------------------------------------------------


class ChannelsResponse(BaseModel):
    """Container for all channel configs (read)."""

    email: EmailChannelResponse
    http: HttpChannelResponse
    mqtt: MqttChannelResponse


class ChannelsUpdate(BaseModel):
    """Container for partial channel config updates."""

    email: EmailChannelUpdate | None = None
    http: HttpChannelUpdate | None = None
    mqtt: MqttChannelUpdate | None = None


# ---------------------------------------------------------------------------
# Top-level settings schemas
# ---------------------------------------------------------------------------


class SettingsResponse(BaseModel):
    """Full settings payload returned by GET /settings."""

    reminders: RemindersSettings
    channels: ChannelsResponse


class SettingsUpdate(BaseModel):
    """Partial update payload accepted by PATCH /settings."""

    reminders: RemindersUpdate | None = None
    channels: ChannelsUpdate | None = None
