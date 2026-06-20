"""Settings configuration endpoint (M4 §4.10 / §9 Step 1).

Routes (all under the api_prefix, e.g. /api; all session-authenticated):
    GET   /settings         Return the full reminders + channels configuration.
                            Secrets are masked as ``*_is_set`` boolean flags.
    PATCH /settings         Apply a partial update (only supplied fields change).
                            Write-only secrets are accepted here and stored.
    POST  /settings/email/test
                            Send a test email to the currently-authenticated user.
                            Always returns HTTP 200 (diagnostic endpoint).
    POST  /settings/mqtt/test
                            Publish a test message to the MQTT broker using
                            currently-saved settings.  Always returns HTTP 200
                            (diagnostic endpoint).

Error contract:
    401  No/invalid session.
    422  Pydantic validation failure → ``validation.invalid_input`` (existing
         handler in ``create_app``; no new error code needed for this step).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.schemas.settings import EmailTestResult, MqttTestResult, SettingsResponse, SettingsUpdate
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
}

router = APIRouter(tags=["settings"], responses=_ERROR_RESPONSES)


def _get_service(db: Session = Depends(get_db)) -> SettingsService:
    """Dependency: build and return a SettingsService."""
    return SettingsService(db)


@router.get("/settings", response_model=SettingsResponse)
def get_settings(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[SettingsService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> SettingsResponse:
    """Return the current reminders and channels configuration.

    Secrets (SMTP password, MQTT password, integration token, auth header)
    are never echoed; each is replaced by a ``*_is_set`` boolean flag.
    Un-set keys return their code-defined defaults (the table only stores
    user overrides).

    **Integration token auto-generation (Step 8):** when the HTTP channel is
    enabled and no ``integration_token`` has been set, this endpoint generates
    one and persists it so that the next ``GET /settings`` returns
    ``integration_token_is_set: True``.  The token itself is never echoed;
    the caller can retrieve it via ``PATCH /settings`` flow or from the
    Configuration UI (Step 12).
    """
    # Auto-generate the integration token when the HTTP channel is enabled
    # and no token exists yet.  This makes ``integration_token_is_set`` flip
    # to True on the first GET after enabling the channel, so HA users can
    # immediately see that a token is available.
    cfg = service.http_channel_config()
    if cfg.enabled and not cfg.integration_token:
        service.get_or_create_integration_token()
    return service.get_settings()


@router.patch("/settings", response_model=SettingsResponse)
def patch_settings(
    payload: SettingsUpdate,
    request: Request,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[SettingsService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> SettingsResponse:
    """Apply a partial update to the reminders and channels configuration.

    Only fields explicitly supplied in the payload are changed; omitted
    fields are left at their current value.  Validation errors are handled
    by the existing ``RequestValidationError`` handler (→ ``validation.invalid_input``).

    To set a secret supply the new value; to clear it supply an explicit
    empty string (``""``) or ``null``.

    **Live MQTT reconnect**: when the update touches any ``channels.mqtt.*``
    field, the MQTT bridge is reloaded after the settings are committed so
    the new config takes effect without an app restart.  This is best-effort
    — a reload failure does not fail the save.
    """
    mqtt_touched = payload.channels is not None and payload.channels.mqtt is not None
    result = service.apply_update(payload)

    if mqtt_touched:
        # The DB session is flushed (but not committed) by apply_update; the
        # get_db dependency commits after this function returns.  To ensure
        # reload_mqtt_bridge reads the *committed* values, we commit here
        # before calling it, then let get_db skip its own commit (idempotent).
        try:
            db.commit()
        except Exception:
            logger.exception("patch_settings: commit before MQTT reload failed — continuing.")

        try:
            from app.config import get_settings as _get_app_settings
            from app.notifications.mqtt import reload_mqtt_bridge

            _env = _get_app_settings().environment
            reload_mqtt_bridge(db, environment=_env)
        except Exception:
            logger.exception(
                "patch_settings: MQTT reload failed after settings save — ignored (best-effort)."
            )

    return result


@router.post("/settings/email/test", response_model=EmailTestResult)
def test_email(
    ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[SettingsService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> EmailTestResult:
    """Send a test email to the currently-authenticated user.

    **Diagnostic semantics — always returns HTTP 200 when authenticated.**
    A failed SMTP connection is an expected diagnostic outcome, not an API
    error.  The ``ok`` field in the response indicates success or failure.

    The test uses the currently-saved email settings and **ignores the
    ``enabled`` flag** — this allows the user to verify the SMTP settings
    before enabling the channel.  Only ``host`` is required.

    The test email is sent in the user's preferred language (or EN if unset).
    """
    # ctx.user is the authenticated User (never None on authenticated routes).
    current_user = ctx.user
    recipient = current_user.email if current_user else ""
    lang = (current_user.preferred_language or "en") if current_user else "en"

    # Guard: host must be configured.
    cfg = service.email_channel_config()
    if not cfg.host:
        return EmailTestResult(
            ok=False,
            detail="SMTP host is not configured",
            recipient=recipient,
        )

    # Attempt the test send — surface the SMTP error as diagnostic detail.
    # Import the channel adapter lazily (matches build_dispatcher's convention:
    # channel adapters are never imported at route module load time).
    from app.notifications.channels.email import EmailChannel

    channel = EmailChannel(db)
    try:
        channel.send_test(to_address=recipient, lang=lang)
        logger.info("Email test send succeeded to %s.", recipient)
        return EmailTestResult(ok=True, detail=None, recipient=recipient)
    except Exception as exc:
        detail = str(exc)
        logger.warning("Email test send failed to %s: %s", recipient, detail)
        return EmailTestResult(ok=False, detail=detail, recipient=recipient)


@router.post("/settings/mqtt/test", response_model=MqttTestResult)
def test_mqtt(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[SettingsService, Depends(_get_service)],
) -> MqttTestResult:
    """Publish a test message to the MQTT broker using currently-saved settings.

    **Diagnostic semantics — always returns HTTP 200 when authenticated.**
    A failed MQTT connection is an expected diagnostic outcome, not an API
    error.  The ``ok`` field in the response indicates success or failure.

    The test uses the currently-saved MQTT settings and **ignores the
    ``enabled`` flag** — this allows the user to verify the broker settings
    before enabling the channel.  Only ``host`` is required.

    The helper creates an independent short-lived paho client (distinct from
    the long-lived bridge singleton) and publishes a retained test message
    to ``{prefix}/test``.
    """
    # Guard: host must be configured.
    cfg = service.mqtt_channel_config()
    if not cfg.host:
        return MqttTestResult(
            ok=False,
            detail="MQTT host is not configured",
            topic="",
        )

    # Attempt the test publish — surface the broker error as diagnostic detail.
    # Import the helper lazily to avoid perturbing import-time state.
    from app.notifications.mqtt import MqttBridgeConfig, mqtt_send_test

    bridge_cfg = MqttBridgeConfig(
        host=cfg.host,
        port=cfg.port or 1883,
        username=cfg.username,
        password=cfg.password,
        topic_prefix=cfg.topic_prefix or "omniventory",
        use_tls=cfg.use_tls,
    )
    try:
        topic = mqtt_send_test(bridge_cfg)
        logger.info("MQTT test publish succeeded to %s.", topic)
        return MqttTestResult(ok=True, detail=None, topic=topic)
    except Exception as exc:
        detail = str(exc)
        logger.warning("MQTT test publish failed: %s", detail)
        return MqttTestResult(ok=False, detail=detail, topic="")
