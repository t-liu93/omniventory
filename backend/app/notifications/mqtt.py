"""MQTT bridge — process-level singleton for the Home Assistant paho-mqtt connection (M4 §4.8 / §9 Step 9).

Architecture
------------
``MqttBridge`` manages a single long-lived paho-mqtt connection owned by the
FastAPI lifespan.  It is started when ``channels.mqtt.enabled`` is True and
the environment is not ``test``; it is stopped cleanly on application shutdown.

paho's ``loop_start()`` runs a background thread that handles reconnection
automatically — the bridge is passive after ``start()`` returns.

Process-level singleton
-----------------------
``get_mqtt_bridge()`` returns the module-level ``_bridge`` singleton so that
both the lifespan (start/stop) and any call site (scheduler job, route handler)
can reach the same instance without dependency injection.  The singleton is
replaced on each ``start()`` call (idempotent for the lifespan pattern).

Topic conventions (all configurable via ``channels.mqtt.topic_prefix``)
-----------------------------------------------------------------------
Reminder publish (instant, retained=False)::

    {prefix}/notifications/{source}
    payload: {"code": ..., "params": ..., "message": ...}

State topics (retained=True — HA sees last value on reconnect)::

    {prefix}/state/low_stock_count    integer value
    {prefix}/state/expiring_count     integer value
    {prefix}/state/expired_count      integer value

Home Assistant MQTT discovery (gated by ``discovery_enabled``)::

    homeassistant/sensor/omniventory_{metric}/config
    payload: HA-spec discovery JSON config

Best-effort
-----------
All publish and connect errors are caught, logged, and silenced.  A bridge
error must never crash a scan, a movement handler, or any other application
path.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.notification import Notification

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HA discovery config helpers
# ---------------------------------------------------------------------------

# The three state metrics and their Human-readable names.
_STATE_METRICS: list[tuple[str, str]] = [
    ("low_stock_count", "Low Stock Count"),
    ("expiring_count", "Expiring Count"),
    ("expired_count", "Expired Count"),
]


def _discovery_payload(metric: str, name: str, state_topic: str) -> dict[str, Any]:
    """Build a Home Assistant MQTT discovery payload for a single sensor.

    The payload follows the HA MQTT sensor discovery spec:
    https://www.home-assistant.io/integrations/sensor.mqtt/
    """
    return {
        "name": f"Omniventory {name}",
        "unique_id": f"omniventory_{metric}",
        "state_topic": state_topic,
        "icon": "mdi:package-variant",
        "value_template": "{{ value }}",
    }


# ---------------------------------------------------------------------------
# MqttChannelConfig (bridge-internal config snapshot)
# ---------------------------------------------------------------------------


@dataclass
class MqttBridgeConfig:
    """Snapshot of MQTT channel settings consumed by ``MqttBridge.start()``."""

    host: str
    port: int
    topic_prefix: str
    username: str | None = None
    password: str | None = None  # noqa: S105 — internal, never serialised
    use_tls: bool = False
    discovery_enabled: bool = False
    commands_enabled: bool = False  # default off (opt-in: mutates stock)


# ---------------------------------------------------------------------------
# MqttBridge
# ---------------------------------------------------------------------------


class MqttBridge:
    """paho-mqtt long-lived connection manager (process-level singleton).

    Lifecycle
    ---------
    1. ``start(config)`` — connect to the broker, start the paho background
       thread, and (if ``discovery_enabled``) publish HA discovery configs.
    2. Application serves requests; ``publish_notification`` / ``publish_state``
       are called best-effort from channel adapters and dispatch points.
    3. ``stop()`` — disconnect cleanly; paho background thread exits.

    The bridge exposes ``is_connected`` so callers can guard publish calls
    without importing paho themselves.
    """

    def __init__(self) -> None:
        self._client: Any = None  # paho.mqtt.client.Client | None
        self._config: MqttBridgeConfig | None = None
        self._connected = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, config: MqttBridgeConfig) -> None:
        """Connect to the broker and start the paho background thread.

        Parameters
        ----------
        config:
            Connection parameters read from ``SettingsService``.

        Notes
        -----
        paho's ``loop_start()`` spawns a daemon thread that handles
        PING/ACK and automatic reconnection.  This method returns as soon
        as ``connect_async()`` is called; the paho background thread
        performs the actual connection and sets ``is_connected`` via the
        ``on_connect`` callback.  Using ``connect_async`` (non-blocking)
        ensures that neither startup nor a settings-save reload can hang
        if the broker is unreachable.

        If a previous client is already running (safe to call repeatedly),
        it is stopped first so that no client/thread is leaked.

        On connect callback (``on_connect``) we publish HA discovery
        configs when ``config.discovery_enabled`` is True.
        """
        import paho.mqtt.client as mqtt

        with self._lock:
            # Stop any previously running client to avoid leaking threads.
            old_client = self._client
            self._client = None
            self._connected = False

        if old_client is not None:
            try:
                old_client.loop_stop()
                old_client.disconnect()
            except Exception:
                logger.exception("MqttBridge: error stopping previous client — ignoring.")

        with self._lock:
            self._config = config
            client_id = "omniventory"
            client = mqtt.Client(client_id=client_id)

            if config.username:
                client.username_pw_set(config.username, config.password)

            if config.use_tls:
                client.tls_set()

            bridge_ref = self  # capture self for the callback

            def on_connect(
                _client: Any,
                _userdata: Any,
                _flags: Any,
                rc: int,
            ) -> None:
                if rc == 0:
                    with bridge_ref._lock:
                        bridge_ref._connected = True
                    logger.info("MqttBridge: connected to broker.")
                    if config.discovery_enabled:
                        bridge_ref._publish_discovery_unsafe(client, config.topic_prefix)
                    if config.commands_enabled:
                        cmd_topic = f"{config.topic_prefix}/command/#"
                        # Use the captured ``client`` from the outer closure
                        # (same object as ``_client`` in real paho; tests may
                        # pass None as the first callback arg).
                        client.subscribe(cmd_topic)
                        logger.info("MqttBridge: subscribed to command topic %s.", cmd_topic)
                else:
                    logger.warning("MqttBridge: connection failed (rc=%d).", rc)

            def on_disconnect(_client: Any, _userdata: Any, rc: int) -> None:
                with bridge_ref._lock:
                    bridge_ref._connected = False
                if rc != 0:
                    logger.warning(
                        "MqttBridge: unexpected disconnect (rc=%d); paho will reconnect.", rc
                    )
                else:
                    logger.info("MqttBridge: disconnected cleanly.")

            def on_message(_client: Any, _userdata: Any, msg: Any) -> None:
                """Handle inbound commands on ``{prefix}/command/<op>``."""
                bridge_ref._handle_command(msg, _client, config.topic_prefix)

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            if config.commands_enabled:
                client.on_message = on_message

            try:
                client.connect_async(config.host, config.port)
                client.loop_start()
                self._client = client
            except Exception:
                logger.exception(
                    "MqttBridge: failed to initiate connection to %s:%d"
                    " — MQTT disabled for this session.",
                    config.host,
                    config.port,
                )
                self._client = None
                self._connected = False

    def stop(self) -> None:
        """Stop the background thread and disconnect from the broker cleanly."""
        with self._lock:
            client = self._client
            self._client = None
            self._connected = False

        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                logger.exception("MqttBridge: error during stop — ignoring.")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True when the broker connection is established."""
        with self._lock:
            return self._connected

    # ------------------------------------------------------------------
    # Publish helpers
    # ------------------------------------------------------------------

    def publish_notification(self, notification: Notification, message: str) -> None:
        """Publish a reminder notification event to ``{prefix}/notifications/{source}``.

        Parameters
        ----------
        notification:
            The committed ``Notification`` row.
        message:
            Pre-rendered human-readable text (from ``render_line``).

        Payload
        -------
        ``{"code": <message_code>, "params": <params dict>, "message": <message>}``
        ``retained=False`` — reminder events are ephemeral.
        """
        client, prefix = self._get_client_and_prefix()
        if client is None:
            return

        params: dict[str, object] = {}
        if notification.params:
            try:
                params = json.loads(notification.params)
            except (ValueError, TypeError):
                params = {}

        topic = f"{prefix}/notifications/{notification.source}"
        payload = json.dumps(
            {
                "code": notification.message_code,
                "params": params,
                "message": message,
            }
        )
        self._publish_safe(client, topic, payload, retain=False)

    def publish_state(self, counts: dict[str, int]) -> None:
        """Publish live state counts to the three retained state topics.

        Parameters
        ----------
        counts:
            Dict with keys ``low_stock_count``, ``expiring_count``,
            ``expired_count`` (all integers).

        Topics
        ------
        ``{prefix}/state/low_stock_count`` (retained=True)
        ``{prefix}/state/expiring_count``  (retained=True)
        ``{prefix}/state/expired_count``   (retained=True)
        """
        client, prefix = self._get_client_and_prefix()
        if client is None:
            return

        for key in ("low_stock_count", "expiring_count", "expired_count"):
            value = counts.get(key, 0)
            topic = f"{prefix}/state/{key}"
            self._publish_safe(client, topic, str(value), retain=True)

    def publish_discovery(self) -> None:
        """Publish Home Assistant MQTT discovery configs (if ``discovery_enabled``).

        Gated by ``channels.mqtt.discovery_enabled``.  Called on connect and
        optionally on configuration change.  No-op when not connected or when
        discovery is disabled.
        """
        with self._lock:
            client = self._client
            config = self._config

        if client is None or config is None:
            return
        if not config.discovery_enabled:
            return
        self._publish_discovery_unsafe(client, config.topic_prefix)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client_and_prefix(self) -> tuple[Any, str]:
        """Return (client, prefix) if connected, else (None, '')."""
        with self._lock:
            if not self._connected or self._client is None:
                return None, ""
            prefix = self._config.topic_prefix if self._config else "omniventory"
            return self._client, prefix

    def _publish_safe(self, client: Any, topic: str, payload: str, *, retain: bool) -> None:
        """Publish with best-effort error handling."""
        try:
            client.publish(topic, payload, retain=retain)
            logger.debug("MqttBridge: published to %s (retain=%s).", topic, retain)
        except Exception:
            logger.exception("MqttBridge: failed to publish to %s — ignored.", topic)

    def _publish_discovery_unsafe(self, client: Any, prefix: str) -> None:
        """Publish all HA discovery configs (caller holds no lock)."""
        for metric, name in _STATE_METRICS:
            state_topic = f"{prefix}/state/{metric}"
            discovery_topic = f"homeassistant/sensor/omniventory_{metric}/config"
            payload = json.dumps(_discovery_payload(metric, name, state_topic))
            self._publish_safe(client, discovery_topic, payload, retain=True)
        logger.info("MqttBridge: HA discovery configs published.")

    def _handle_command(self, msg: Any, client: Any, prefix: str) -> None:
        """Handle an inbound MQTT command message (called from the paho thread).

        Topic convention: ``{prefix}/command/<op>``

        Bounded op set: ``consume``, ``intake``, ``adjust``.  Any other op
        (unknown or malformed topic) is dropped with a log + error result.

        The entire handler is wrapped in a try/except so that *no exception*
        can propagate to the paho background thread and kill the connection.

        Security note: the MQTT broker is operator-trusted (see §2 and §12).
        ``commands_enabled`` defaults to ``False`` (opt-in).  Tighter
        command-level auth / allow-listing is deferred to M6.
        """
        result_topic = f"{prefix}/command_result"

        def _publish_result(op: str, status: str, detail: object) -> None:
            payload = json.dumps({"op": op, "status": status, "detail": detail})
            self._publish_safe(client, result_topic, payload, retain=False)

        try:
            # Extract the op from the trailing topic segment.
            topic: str = msg.topic if hasattr(msg, "topic") else ""
            # Topic is: {prefix}/command/<op>
            expected_prefix = f"{prefix}/command/"
            if not topic.startswith(expected_prefix):
                logger.warning("MqttBridge: unexpected command topic %r — dropped.", topic)
                _publish_result("unknown", "error", f"unexpected topic: {topic}")
                return

            op = topic[len(expected_prefix) :]
            if "/" in op or not op:
                logger.warning(
                    "MqttBridge: malformed command op %r in topic %r — dropped.", op, topic
                )
                _publish_result(op or "unknown", "error", "malformed op")
                return

            # Parse JSON payload.
            try:
                raw_payload = msg.payload
                if isinstance(raw_payload, (bytes, bytearray)):
                    raw_payload = raw_payload.decode("utf-8", errors="replace")
                payload_dict: dict[str, object] = json.loads(raw_payload)
            except (ValueError, TypeError) as exc:
                logger.warning("MqttBridge: malformed JSON in command %r — dropped. (%s)", op, exc)
                _publish_result(op, "error", f"malformed JSON: {exc}")
                return

            if op not in ("consume", "intake", "adjust"):
                logger.warning(
                    "MqttBridge: unknown command op %r — dropped (not in bounded set).", op
                )
                _publish_result(op, "error", f"unknown op: {op}")
                return

            # Dispatch to the command executor.
            self._execute_command(op, payload_dict, client, prefix)

        except Exception:
            logger.exception(
                "MqttBridge: unhandled exception in _handle_command — paho thread protected."
            )

    def _execute_command(
        self,
        op: str,
        payload: dict[str, object],
        client: Any,
        prefix: str,
    ) -> None:
        """Execute a bounded stock command in a fresh DB session as the system actor.

        Opens an independent SQLAlchemy session (paho callback thread, not the
        request thread), builds a system-actor ``RequestContext`` (``user=None``
        ⇒ ``movement.user_id=NULL``), delegates to ``StockMovementService``,
        commits on success, rolls back on ``AppError``.

        Post-commit (best-effort): dispatches pending low-stock notifications
        and publishes MQTT state — failures here must not affect the command
        result already sent.
        """
        import contextlib
        from decimal import Decimal, InvalidOperation

        from app.core.context import RequestContext
        from app.core.errors import AppError
        from app.db.base import get_session_factory
        from app.notifications.dispatcher import build_dispatcher, publish_mqtt_state
        from app.repositories.household import HouseholdRepository
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.stock_instance import StockInstanceRepository
        from app.services.stock_movement import StockMovementService

        result_topic = f"{prefix}/command_result"

        def _publish_result(status: str, detail: object) -> None:
            p = json.dumps({"op": op, "status": status, "detail": detail})
            self._publish_safe(client, result_topic, p, retain=False)

        db = get_session_factory()()
        try:
            household = HouseholdRepository(db).ensure()
            ctx = RequestContext(household=household, user=None)
            svc = StockMovementService(db, ctx)

            if op == "consume":
                # payload: {definition_id, quantity}
                try:
                    def_id = int(str(payload["definition_id"]))
                    qty = Decimal(str(payload["quantity"]))
                except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                    logger.warning("MqttBridge: bad payload for consume: %s — dropped.", exc)
                    _publish_result("error", f"bad payload: {exc}")
                    return

                defn_repo = ItemDefinitionRepository(db)
                defn = defn_repo.get(def_id)
                if defn is None:
                    _publish_result("error", f"definition {def_id} not found")
                    return

                try:
                    touched = svc.consume_fifo(defn, qty)
                    db.commit()
                    detail: object = {
                        "definition_id": def_id,
                        "consumed": str(qty),
                        "lots_touched": len(touched),
                    }
                    _publish_result("ok", detail)
                except AppError as exc:
                    db.rollback()
                    _publish_result("error", {"code": exc.code, "message": exc.message})
                    return

            elif op == "intake":
                # payload: {instance_id, quantity}
                try:
                    inst_id = int(str(payload["instance_id"]))
                    qty = Decimal(str(payload["quantity"]))
                except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                    logger.warning("MqttBridge: bad payload for intake: %s — dropped.", exc)
                    _publish_result("error", f"bad payload: {exc}")
                    return

                inst_repo = StockInstanceRepository(db)
                inst = inst_repo.get(inst_id)
                if inst is None:
                    _publish_result("error", f"instance {inst_id} not found")
                    return

                try:
                    svc.intake(inst, qty)
                    db.commit()
                    detail = {
                        "instance_id": inst_id,
                        "new_quantity": str(inst.quantity),
                    }
                    _publish_result("ok", detail)
                except AppError as exc:
                    db.rollback()
                    _publish_result("error", {"code": exc.code, "message": exc.message})
                    return

            elif op == "adjust":
                # payload: {instance_id, counted_quantity}
                try:
                    inst_id = int(str(payload["instance_id"]))
                    counted = Decimal(str(payload["counted_quantity"]))
                except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                    logger.warning("MqttBridge: bad payload for adjust: %s — dropped.", exc)
                    _publish_result("error", f"bad payload: {exc}")
                    return

                inst_repo = StockInstanceRepository(db)
                inst = inst_repo.get(inst_id)
                if inst is None:
                    _publish_result("error", f"instance {inst_id} not found")
                    return

                try:
                    svc.adjust(inst, counted)
                    db.commit()
                    detail = {
                        "instance_id": inst_id,
                        "new_quantity": str(inst.quantity),
                    }
                    _publish_result("ok", detail)
                except AppError as exc:
                    db.rollback()
                    _publish_result("error", {"code": exc.code, "message": exc.message})
                    return

            else:
                # Should not be reached (guarded in _handle_command), but defensive.
                _publish_result("error", f"unknown op: {op}")
                return

            # Best-effort post-commit: dispatch pending notifications + publish state.
            # Failures here must not alter the command result already published above.
            # The dispatch() call writes notification_deliveries rows (only flush,
            # no commit); we must commit here to persist those delivery rows and
            # preserve idempotency for future dispatches (mirrors the three existing
            # route dispatch sites: instances.py:228-229, :264-265,
            # definitions.py:171-172).
            try:
                if svc.pending_notifications:
                    build_dispatcher(db).dispatch(
                        svc.pending_notifications, include_email_digest=False
                    )
                    db.commit()
                publish_mqtt_state(db)
            except Exception:
                logger.exception(
                    "MqttBridge: post-commit dispatch/state failed (best-effort) — ignored."
                )

        except Exception:
            logger.exception("MqttBridge: unhandled exception in _execute_command — op=%r.", op)
            with contextlib.suppress(Exception):
                db.rollback()
        finally:
            with contextlib.suppress(Exception):
                db.close()


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_bridge: MqttBridge = MqttBridge()
_bridge_lock = threading.Lock()


def get_mqtt_bridge() -> MqttBridge:
    """Return the process-level ``MqttBridge`` singleton.

    Both the FastAPI lifespan (start/stop) and any dispatch site (scheduler
    job, route handler) should call this to access the shared bridge instance.
    """
    return _bridge


def _reset_bridge_for_testing() -> None:
    """Replace the singleton with a fresh instance — **test use only**.

    Tests that need a clean bridge state call this before their fixture
    to avoid cross-test state leakage.
    """
    global _bridge  # noqa: PLW0603
    _bridge = MqttBridge()


# ---------------------------------------------------------------------------
# reload_mqtt_bridge — shared reload helper (lifespan + settings-save path)
# ---------------------------------------------------------------------------


def reload_mqtt_bridge(db: object, *, environment: str) -> None:
    """Stop the current bridge and restart it from the latest DB settings.

    This is the single function that both the FastAPI lifespan startup and
    the settings-save route use so that an MQTT config change takes effect
    immediately without requiring an app restart.

    Guard conditions (same as the lifespan):
    - ``environment == "test"`` — no-op; never open real connections in CI/pytest.
    - ``channels.mqtt.enabled`` is False or host is not set — stops the bridge
      and leaves it stopped (does not start a new connection).

    Parameters
    ----------
    db:
        An open SQLAlchemy session used only to read ``SettingsService`` values.
        The caller is responsible for its lifecycle (open/close).
    environment:
        Value from ``get_settings().environment``.  Passed explicitly so this
        function has no import-time dependency on the global settings cache.

    Notes
    -----
    This function is **best-effort**: any exception is caught and logged so
    that a reload failure never propagates into the calling request or scan.
    """
    if environment == "test":
        logger.debug("reload_mqtt_bridge: environment=test — MQTT reload suppressed.")
        return

    try:
        from app.services.settings import SettingsService

        cfg = SettingsService(db).mqtt_channel_config()  # type: ignore[arg-type]
        bridge = get_mqtt_bridge()

        if not cfg.enabled or not cfg.host:
            # Disabled or unconfigured — just stop whatever is running.
            bridge.stop()
            logger.info("reload_mqtt_bridge: MQTT disabled or unconfigured — bridge stopped.")
            return

        port = cfg.port or 1883
        bridge_cfg = MqttBridgeConfig(
            host=cfg.host,
            port=port,
            username=cfg.username,
            password=cfg.password,
            topic_prefix=cfg.topic_prefix or "omniventory",
            use_tls=cfg.use_tls,
            discovery_enabled=cfg.discovery_enabled,
            commands_enabled=cfg.commands_enabled,
        )
        bridge.start(bridge_cfg)
        logger.info(
            "reload_mqtt_bridge: MQTT bridge reloaded (host=%s, port=%d).",
            cfg.host,
            port,
        )
    except Exception:
        logger.exception(
            "reload_mqtt_bridge: unexpected error during reload — ignored (best-effort)."
        )


# ---------------------------------------------------------------------------
# mqtt_send_test — one-shot connectivity test (independent of long-lived bridge)
# ---------------------------------------------------------------------------


def mqtt_send_test(config: MqttBridgeConfig) -> str:
    """Publish a single retained test message and return the topic on success.

    This helper creates a **fresh, independent** paho client (client_id
    ``omniventory-test``) that is distinct from the long-lived bridge
    singleton.  It connects with a bounded timeout, publishes one retained
    message to ``{prefix}/test``, waits for publish hand-off, then always
    disconnects in a ``finally`` block.

    Parameters
    ----------
    config:
        MQTT connection parameters (host, port, credentials, TLS, prefix).

    Returns
    -------
    str
        The test topic string on success (``"{prefix}/test"``).

    Raises
    ------
    TimeoutError
        If the broker does not accept the connection within ~5 seconds.
    RuntimeError
        If the broker refuses the connection (``rc != 0``).
    Exception
        Any other paho or OS-level error is re-raised directly.

    Notes
    -----
    ``wait_for_publish`` may raise ``RuntimeError`` if the message was never
    queued (e.g. client disconnected before publish) — that is also
    propagated.  The route handler catches all exceptions and converts them
    to a diagnostic ``ok=false`` response.
    """
    import datetime
    import threading

    import paho.mqtt.client as mqtt

    topic = f"{config.topic_prefix}/test"
    connect_event = threading.Event()
    connect_rc: list[int] = []  # mutable container to share rc from callback

    client = mqtt.Client(client_id="omniventory-test")

    if config.username:
        client.username_pw_set(config.username, config.password)
    if config.use_tls:
        client.tls_set()

    def _on_connect(_client: Any, _userdata: Any, _flags: Any, rc: int) -> None:
        connect_rc.append(rc)
        connect_event.set()

    client.on_connect = _on_connect

    try:
        client.connect_async(config.host, config.port)
        client.loop_start()

        if not connect_event.wait(timeout=5.0):
            raise TimeoutError(
                f"MQTT connection to {config.host}:{config.port} timed out after 5 s"
            )

        rc = connect_rc[0] if connect_rc else -1
        if rc != 0:
            raise RuntimeError(
                f"MQTT broker at {config.host}:{config.port} refused connection (rc={rc})"
            )

        payload = json.dumps(
            {
                "status": "ok",
                "ts": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        msg_info = client.publish(topic, payload, retain=True)
        msg_info.wait_for_publish(timeout=5.0)
        logger.info("mqtt_send_test: test message published to %s.", topic)
        return topic
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            logger.exception("mqtt_send_test: error during cleanup — ignoring.")
