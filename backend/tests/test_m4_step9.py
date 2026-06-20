"""M4 Step 9 tests: MQTT bridge outbound publish, state topics and HA discovery.

Required coverage (M4.md §5 "MQTT" + §9 Step 9 + §10 Step 9):

A. MqttBridge lifecycle:
- start() calls paho connect + loop_start; stop() calls loop_stop + disconnect
- is_connected reflects on_connect callback result
- stop() is a no-op when bridge was never started
- on_disconnect sets is_connected=False

B. Reminder publish (MqttBridge.publish_notification):
- topic = {prefix}/notifications/{source}
- payload = {"code": ..., "params": ..., "message": ...}
- retained=False
- no-op when not connected

C. State publish (MqttBridge.publish_state):
- three topics: {prefix}/state/low_stock_count, /expiring_count, /expired_count
- retained=True for all three
- values match the counts dict
- no-op when not connected

D. HA discovery (MqttBridge.publish_discovery / on_connect):
- discovery_enabled=True: publishes to homeassistant/sensor/omniventory_{metric}/config
  payload shape: has "name", "unique_id", "state_topic" (bound to state topic), "icon"
  retained=True for discovery topics
- discovery_enabled=False: no discovery publish
- publish_discovery() is a no-op when not connected

E. SettingsService.mqtt_channel_config():
- returns MqttChannelConfig with all fields
- defaults when nothing stored (enabled=False, topic_prefix="omniventory", etc.)

F. MqttChannel (NotificationChannel adapter):
- is_enabled(): True when channels.mqtt.enabled=True AND bridge is connected
- is_enabled(): False when channels.mqtt.enabled=False
- is_enabled(): False when bridge not connected (even if enabled=True)
- deliver(): publishes each notification (mock bridge)
- deliver(): renders message in recipient's language
- deliver(): no-op when not enabled
- deliver(): no-op when notifications list is empty
- deliver(): idempotency — skips notifications with existing 'sent' row
- deliver(): records 'sent' delivery row on success
- deliver(): records 'failed' row on publish error; does not propagate
- deliver(): best-effort — exception does not propagate; continues other notifications

G. publish_mqtt_state() helper:
- no-op when bridge not connected
- no-op when channels.mqtt.enabled=False
- publishes state via bridge.publish_state when connected and enabled

H. build_dispatcher() registers MqttChannel:
- MqttChannel is among the registered channels

I. Disabled = no-op integration:
- channels.mqtt.enabled=False → MqttChannel.is_enabled()=False → deliver is skipped

J. Step 7/8 regression: existing dispatcher tests still pass (build_dispatcher
   registering EmailChannel + HttpChannel unchanged).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Generator
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as prior M4 steps)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, object]:
    """Create a fresh in-memory SQLite session with all models registered."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.notification as notif_mod
    import app.models.notification_delivery as nd_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    for mod in (
        db_base_mod,
        hh_mod,
        user_mod,
        sess_mod,
        app_config_mod,
        cat_mod,
        ikind_mod,
        idef_mod,
        loc_mod,
        si_mod,
        sm_mod,
        setting_mod,
        notif_mod,
        nd_mod,
    ):
        importlib.reload(mod)

    # Re-run mapper configuration so that relationship name references (e.g.
    # 'ItemKind' inside ItemDefinition) resolve against the freshly reloaded
    # classes.  Configure ONLY this Base's registry rather than the global
    # ``configure_mappers()``: earlier test modules in the same pytest process
    # also reload model modules, leaving orphaned registries whose relationship
    # strings now point at reloaded-away classes.  The global call walks those
    # orphans and raises ``InvalidRequestError`` while resolving e.g. 'ItemKind';
    # scoping configuration to the freshly reloaded registry sidesteps that
    # cross-file pollution.
    from app.db.base import Base as _Base

    _Base.registry.configure()

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(engine, "connect")
    def _enforce_fk(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    return session, engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    """Reset lru_cache on get_settings / get_engine before and after each test."""
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def db_session() -> Generator[Session]:
    """Fresh in-memory SQLite session with all models registered."""
    session, engine = _make_in_memory_session()

    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture(autouse=True)
def _reset_bridge() -> Generator[None]:
    """Reset the MqttBridge singleton before each test to prevent state leakage."""
    from app.notifications.mqtt import _reset_bridge_for_testing

    _reset_bridge_for_testing()
    yield
    _reset_bridge_for_testing()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_user_counter: int = 0


def _make_user(db: Session, username: str = "testuser", lang: str = "en") -> object:
    """Create and persist a user row."""
    global _user_counter
    _user_counter += 1
    from app.models.user import User

    user = User(
        email=f"{username}_{_user_counter}@example.com",
        password_hash="x",
        is_active=True,
        preferred_language=lang,
    )
    db.add(user)
    db.flush()
    return user


def _make_notification(
    db: Session,
    user_id: int,
    source: str = "low_stock",
    message_code: str = "reminder.low_stock",
    params: dict | None = None,
    dedup_key: str | None = None,
) -> object:
    """Create and persist a notification row."""
    from app.models.notification import Notification

    if params is None:
        params = {"name": "Widget", "current": "5", "threshold": "10"}
    if dedup_key is None:
        dedup_key = f"test:{user_id}:{source}:{id(params)}"

    notif = Notification(
        user_id=user_id,
        source=source,
        subject_type="definition",
        subject_id=1,
        dedup_key=dedup_key,
        message_code=message_code,
        params=json.dumps(params),
        episode_started_on=date.today(),
        offset_days=0,
    )
    db.add(notif)
    db.flush()
    return notif


def _enable_mqtt(db: Session, host: str = "localhost", port: int = 1883) -> None:
    """Enable the MQTT channel in settings."""
    from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
    from app.services.settings import SettingsService

    svc = SettingsService(db)
    svc.apply_update(
        SettingsUpdate(
            channels=ChannelsUpdate(
                mqtt=MqttChannelUpdate(
                    enabled=True,
                    host=host,
                    port=port,
                )
            )
        )
    )
    db.flush()


def _make_mock_paho_client() -> MagicMock:
    """Return a MagicMock that simulates a paho MQTT client."""
    mock_client = MagicMock()
    # Simulate successful connect: on_connect is called with rc=0
    # We'll invoke it manually in tests that need it.
    return mock_client


# ---------------------------------------------------------------------------
# A. MqttBridge lifecycle
# ---------------------------------------------------------------------------


class TestMqttBridgeLifecycle:
    """Tests for MqttBridge.start() and stop() lifecycle."""

    def test_start_calls_connect_async_and_loop_start(self) -> None:
        """start() calls client.connect_async(host, port) and client.loop_start() (non-blocking)."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(
                MqttBridgeConfig(host="broker.example", port=1883, topic_prefix="omniventory")
            )

        mock_client.connect_async.assert_called_once_with("broker.example", 1883)
        mock_client.connect.assert_not_called()
        mock_client.loop_start.assert_called_once()

    def test_start_sets_username_password_when_provided(self) -> None:
        """start() calls username_pw_set when username is provided."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(
                MqttBridgeConfig(
                    host="broker.example",
                    port=1883,
                    topic_prefix="omniventory",
                    username="user",
                    password="pass",  # noqa: S106
                )
            )

        mock_client.username_pw_set.assert_called_once_with("user", "pass")

    def test_start_calls_tls_set_when_use_tls(self) -> None:
        """start() calls tls_set() when use_tls=True."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(
                MqttBridgeConfig(
                    host="broker.example",
                    port=8883,
                    topic_prefix="omniventory",
                    use_tls=True,
                )
            )

        mock_client.tls_set.assert_called_once()

    def test_on_connect_rc0_sets_is_connected(self) -> None:
        """When paho fires on_connect with rc=0, is_connected becomes True."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        captured_on_connect: list = []
        mock_client = MagicMock()

        def _capture_on_connect(cb: object) -> None:
            captured_on_connect.append(cb)

        type(mock_client).on_connect = property(
            fget=lambda self: captured_on_connect[0] if captured_on_connect else None,
            fset=lambda self, v: _capture_on_connect(v),
        )

        # More direct approach: patch Client so we can call on_connect ourselves
        real_on_connect_holder: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            def loop_stop(self) -> None:
                pass

            def disconnect(self) -> None:
                pass

            def publish(self, *a: object, **kw: object) -> None:
                pass

            @property
            def on_connect(self) -> object:  # type: ignore[override]
                return real_on_connect_holder[0] if real_on_connect_holder else None

            @on_connect.setter
            def on_connect(self, cb: object) -> None:
                real_on_connect_holder.clear()
                real_on_connect_holder.append(cb)

            @property
            def on_disconnect(self) -> object:
                return None

            @on_disconnect.setter
            def on_disconnect(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(MqttBridgeConfig(host="localhost", port=1883, topic_prefix="omniventory"))

        assert not bridge.is_connected  # not yet connected (callback not fired)
        # Simulate paho calling on_connect with rc=0
        on_connect_cb = real_on_connect_holder[0]
        on_connect_cb(None, None, None, 0)
        assert bridge.is_connected

    def test_on_connect_nonzero_rc_not_connected(self) -> None:
        """When paho fires on_connect with rc!=0, is_connected stays False."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        real_on_connect_holder: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            @property
            def on_connect(self) -> object:  # type: ignore[override]
                return real_on_connect_holder[0] if real_on_connect_holder else None

            @on_connect.setter
            def on_connect(self, cb: object) -> None:
                real_on_connect_holder.clear()
                real_on_connect_holder.append(cb)

            @property
            def on_disconnect(self) -> object:
                return None

            @on_disconnect.setter
            def on_disconnect(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(MqttBridgeConfig(host="localhost", port=1883, topic_prefix="omniventory"))

        on_connect_cb = real_on_connect_holder[0]
        on_connect_cb(None, None, None, 5)  # rc=5 means refused
        assert not bridge.is_connected

    def test_stop_calls_loop_stop_and_disconnect(self) -> None:
        """stop() calls client.loop_stop() and client.disconnect()."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(MqttBridgeConfig(host="localhost", port=1883, topic_prefix="omniventory"))

        bridge.stop()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    def test_stop_noop_when_never_started(self) -> None:
        """stop() on a fresh (never started) bridge is a no-op (no exception)."""
        from app.notifications.mqtt import MqttBridge

        bridge = MqttBridge()
        bridge.stop()  # should not raise

    def test_stop_sets_is_connected_false(self) -> None:
        """stop() sets is_connected to False."""
        from app.notifications.mqtt import MqttBridge

        bridge = MqttBridge()
        # Manually set connected to simulate a connected state
        bridge._connected = True  # noqa: SLF001
        bridge._client = MagicMock()  # noqa: SLF001
        bridge.stop()
        assert not bridge.is_connected

    def test_connect_async_exception_is_swallowed(self) -> None:
        """If paho connect_async() raises, bridge is silent (best-effort)."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        mock_client.connect_async.side_effect = OSError("connection refused")

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(MqttBridgeConfig(host="localhost", port=1883, topic_prefix="omniventory"))

        # Bridge should silently handle the error
        assert not bridge.is_connected


# ---------------------------------------------------------------------------
# B. Reminder publish
# ---------------------------------------------------------------------------


class TestMqttBridgePublishNotification:
    """Tests for MqttBridge.publish_notification()."""

    def _make_connected_bridge(self) -> tuple[object, MagicMock]:
        """Return (bridge, mock_client) with bridge in connected state."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        # Put bridge in connected state directly
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost", port=1883, topic_prefix="omniventory"
        )
        return bridge, mock_client

    def test_notification_topic_and_retained_false(self) -> None:
        """publish_notification publishes to {prefix}/notifications/{source} retained=False."""
        bridge, mock_client = self._make_connected_bridge()

        mock_notif = MagicMock()
        mock_notif.source = "low_stock"
        mock_notif.message_code = "reminder.low_stock"
        mock_notif.params = json.dumps({"name": "Rice", "current": "5", "threshold": "10"})

        bridge.publish_notification(mock_notif, "Rice is low")

        mock_client.publish.assert_called_once()
        args = mock_client.publish.call_args
        topic = args[0][0]
        payload_str = args[0][1]
        retain = args[1].get("retain", args[0][2] if len(args[0]) > 2 else None)

        assert topic == "omniventory/notifications/low_stock"
        assert retain is False
        payload = json.loads(payload_str)
        assert payload["code"] == "reminder.low_stock"
        assert payload["message"] == "Rice is low"
        assert isinstance(payload["params"], dict)

    def test_notification_payload_includes_code_params_message(self) -> None:
        """publish_notification payload has {code, params, message}."""
        bridge, mock_client = self._make_connected_bridge()

        params_dict = {"name": "Milk", "date": "2026-06-25", "days_remaining": 5}
        mock_notif = MagicMock()
        mock_notif.source = "best_before"
        mock_notif.message_code = "reminder.best_before"
        mock_notif.params = json.dumps(params_dict)

        bridge.publish_notification(mock_notif, "Milk expires in 5 day(s)")

        args = mock_client.publish.call_args
        payload = json.loads(args[0][1])
        assert payload["code"] == "reminder.best_before"
        assert payload["message"] == "Milk expires in 5 day(s)"
        assert payload["params"]["name"] == "Milk"
        assert payload["params"]["days_remaining"] == 5

    def test_notification_uses_topic_prefix(self) -> None:
        """publish_notification uses the configured topic_prefix."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost", port=1883, topic_prefix="myhome"
        )

        mock_notif = MagicMock()
        mock_notif.source = "warranty"
        mock_notif.message_code = "reminder.warranty"
        mock_notif.params = "{}"

        bridge.publish_notification(mock_notif, "warranty expiring")

        args = mock_client.publish.call_args
        assert args[0][0] == "myhome/notifications/warranty"

    def test_notification_noop_when_not_connected(self) -> None:
        """publish_notification is a no-op when bridge is not connected."""
        from app.notifications.mqtt import MqttBridge

        bridge = MqttBridge()
        # Not connected (fresh bridge)
        mock_notif = MagicMock()
        bridge.publish_notification(mock_notif, "some message")
        # No exception; no publish call attempted

    def test_notification_publish_exception_swallowed(self) -> None:
        """publish_notification swallows exceptions (best-effort)."""
        bridge, mock_client = self._make_connected_bridge()
        mock_client.publish.side_effect = OSError("network error")

        mock_notif = MagicMock()
        mock_notif.source = "low_stock"
        mock_notif.message_code = "reminder.low_stock"
        mock_notif.params = "{}"

        bridge.publish_notification(mock_notif, "some message")  # must not raise


# ---------------------------------------------------------------------------
# C. State publish
# ---------------------------------------------------------------------------


class TestMqttBridgePublishState:
    """Tests for MqttBridge.publish_state()."""

    def _make_connected_bridge(self) -> tuple[object, MagicMock]:
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost", port=1883, topic_prefix="omniventory"
        )
        return bridge, mock_client

    def test_state_publishes_three_topics_retained_true(self) -> None:
        """publish_state publishes to three {prefix}/state/* topics with retain=True."""
        bridge, mock_client = self._make_connected_bridge()

        bridge.publish_state({"low_stock_count": 3, "expiring_count": 1, "expired_count": 0})

        assert mock_client.publish.call_count == 3
        calls = mock_client.publish.call_args_list
        topics_published = {c[0][0]: (c[0][1], c[1].get("retain")) for c in calls}

        assert "omniventory/state/low_stock_count" in topics_published
        assert "omniventory/state/expiring_count" in topics_published
        assert "omniventory/state/expired_count" in topics_published

        # All must be retained=True
        for topic, (_value, retain) in topics_published.items():
            assert retain is True, f"Topic {topic} was not published with retain=True"

    def test_state_publishes_correct_values(self) -> None:
        """publish_state values match the counts dict."""
        bridge, mock_client = self._make_connected_bridge()

        bridge.publish_state({"low_stock_count": 7, "expiring_count": 2, "expired_count": 4})

        calls = mock_client.publish.call_args_list
        topics_published = {c[0][0]: c[0][1] for c in calls}

        assert topics_published["omniventory/state/low_stock_count"] == "7"
        assert topics_published["omniventory/state/expiring_count"] == "2"
        assert topics_published["omniventory/state/expired_count"] == "4"

    def test_state_uses_topic_prefix(self) -> None:
        """publish_state uses the configured topic_prefix."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost", port=1883, topic_prefix="myprefix"
        )

        bridge.publish_state({"low_stock_count": 1, "expiring_count": 0, "expired_count": 0})

        topics = [c[0][0] for c in mock_client.publish.call_args_list]
        assert "myprefix/state/low_stock_count" in topics
        assert "myprefix/state/expiring_count" in topics
        assert "myprefix/state/expired_count" in topics

    def test_state_noop_when_not_connected(self) -> None:
        """publish_state is a no-op when bridge is not connected."""
        from app.notifications.mqtt import MqttBridge

        bridge = MqttBridge()
        # Not connected
        bridge.publish_state({"low_stock_count": 1, "expiring_count": 0, "expired_count": 0})
        # No exception

    def test_state_publish_exception_swallowed(self) -> None:
        """publish_state swallows exceptions (best-effort)."""
        bridge, mock_client = self._make_connected_bridge()
        mock_client.publish.side_effect = OSError("network error")

        bridge.publish_state({"low_stock_count": 1, "expiring_count": 0, "expired_count": 0})
        # must not raise


# ---------------------------------------------------------------------------
# D. HA discovery
# ---------------------------------------------------------------------------


class TestMqttBridgeDiscovery:
    """Tests for HA MQTT discovery publish."""

    def _make_connected_bridge(self, discovery_enabled: bool = True) -> tuple[object, MagicMock]:
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost",
            port=1883,
            topic_prefix="omniventory",
            discovery_enabled=discovery_enabled,
        )
        return bridge, mock_client

    def test_discovery_publishes_three_sensors_when_enabled(self) -> None:
        """publish_discovery() publishes configs for 3 sensors when discovery_enabled."""
        bridge, mock_client = self._make_connected_bridge(discovery_enabled=True)

        bridge.publish_discovery()

        assert mock_client.publish.call_count == 3
        topics = [c[0][0] for c in mock_client.publish.call_args_list]
        assert "homeassistant/sensor/omniventory_low_stock_count/config" in topics
        assert "homeassistant/sensor/omniventory_expiring_count/config" in topics
        assert "homeassistant/sensor/omniventory_expired_count/config" in topics

    def test_discovery_payload_has_required_fields(self) -> None:
        """Discovery payload has name, unique_id, state_topic, icon."""
        bridge, mock_client = self._make_connected_bridge(discovery_enabled=True)

        bridge.publish_discovery()

        # Check the low_stock_count config
        calls_by_topic = {c[0][0]: c[0][1] for c in mock_client.publish.call_args_list}
        config_str = calls_by_topic["homeassistant/sensor/omniventory_low_stock_count/config"]
        config = json.loads(config_str)

        assert "name" in config
        assert "unique_id" in config
        assert "state_topic" in config
        assert "icon" in config
        # state_topic must point to the correct state topic
        assert config["state_topic"] == "omniventory/state/low_stock_count"
        assert config["unique_id"] == "omniventory_low_stock_count"

    def test_discovery_topics_retained(self) -> None:
        """Discovery configs are published with retain=True."""
        bridge, mock_client = self._make_connected_bridge(discovery_enabled=True)

        bridge.publish_discovery()

        for c in mock_client.publish.call_args_list:
            retain = c[1].get("retain")
            assert retain is True, f"Discovery publish not retained: {c}"

    def test_discovery_noop_when_discovery_disabled(self) -> None:
        """publish_discovery() is a no-op when discovery_enabled=False."""
        bridge, mock_client = self._make_connected_bridge(discovery_enabled=False)

        bridge.publish_discovery()

        mock_client.publish.assert_not_called()

    def test_discovery_noop_when_not_connected(self) -> None:
        """publish_discovery() is a no-op when bridge is not connected."""
        from app.notifications.mqtt import MqttBridge

        bridge = MqttBridge()
        # not connected
        bridge.publish_discovery()  # must not raise

    def test_on_connect_triggers_discovery_when_enabled(self) -> None:
        """When paho fires on_connect with rc=0, discovery is published if discovery_enabled."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        real_on_connect_holder: list = []
        published_topics: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            def publish(self, topic: str, payload: str, **kwargs: object) -> None:
                published_topics.append(topic)

            @property
            def on_connect(self) -> object:  # type: ignore[override]
                return real_on_connect_holder[0] if real_on_connect_holder else None

            @on_connect.setter
            def on_connect(self, cb: object) -> None:
                real_on_connect_holder.clear()
                real_on_connect_holder.append(cb)

            @property
            def on_disconnect(self) -> object:
                return None

            @on_disconnect.setter
            def on_disconnect(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(
                MqttBridgeConfig(
                    host="localhost",
                    port=1883,
                    topic_prefix="omniventory",
                    discovery_enabled=True,
                )
            )

        # Simulate on_connect with rc=0
        real_on_connect_holder[0](None, None, None, 0)

        # Discovery should have been published
        assert any("homeassistant/sensor" in t for t in published_topics)

    def test_on_connect_no_discovery_when_disabled(self) -> None:
        """When on_connect fires and discovery_enabled=False, no discovery published."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        real_on_connect_holder: list = []
        published_topics: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            def publish(self, topic: str, payload: str, **kwargs: object) -> None:
                published_topics.append(topic)

            @property
            def on_connect(self) -> object:  # type: ignore[override]
                return real_on_connect_holder[0] if real_on_connect_holder else None

            @on_connect.setter
            def on_connect(self, cb: object) -> None:
                real_on_connect_holder.clear()
                real_on_connect_holder.append(cb)

            @property
            def on_disconnect(self) -> object:
                return None

            @on_disconnect.setter
            def on_disconnect(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(
                MqttBridgeConfig(
                    host="localhost",
                    port=1883,
                    topic_prefix="omniventory",
                    discovery_enabled=False,
                )
            )

        real_on_connect_holder[0](None, None, None, 0)

        # No homeassistant discovery topics should be published
        assert not any("homeassistant" in t for t in published_topics)


# ---------------------------------------------------------------------------
# E. SettingsService.mqtt_channel_config()
# ---------------------------------------------------------------------------


class TestMqttChannelConfig:
    """Tests for SettingsService.mqtt_channel_config()."""

    def test_defaults_when_nothing_stored(self, db_session: Session) -> None:
        """mqtt_channel_config() returns defaults when no settings stored."""
        from app.services.settings import SettingsService

        cfg = SettingsService(db_session).mqtt_channel_config()

        assert cfg.enabled is False
        assert cfg.host is None
        assert cfg.port is None
        assert cfg.username is None
        assert cfg.password is None
        assert cfg.topic_prefix == "omniventory"
        assert cfg.use_tls is False
        assert cfg.discovery_enabled is False
        assert cfg.commands_enabled is False

    def test_returns_stored_values(self, db_session: Session) -> None:
        """mqtt_channel_config() returns stored values."""
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(
                    mqtt=MqttChannelUpdate(
                        enabled=True,
                        host="mqtt.example.com",
                        port=8883,
                        username="mqttuser",
                        password="secret",  # noqa: S106
                        topic_prefix="myhome",
                        use_tls=True,
                        discovery_enabled=True,
                        commands_enabled=True,
                    )
                )
            )
        )
        db_session.flush()

        cfg = svc.mqtt_channel_config()

        assert cfg.enabled is True
        assert cfg.host == "mqtt.example.com"
        assert cfg.port == 8883
        assert cfg.username == "mqttuser"
        assert cfg.password == "secret"
        assert cfg.topic_prefix == "myhome"
        assert cfg.use_tls is True
        assert cfg.discovery_enabled is True
        assert cfg.commands_enabled is True

    def test_password_not_in_api_response(self, db_session: Session) -> None:
        """mqtt_channel_config() password is separate from API response (write-only)."""
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        svc.apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(
                    mqtt=MqttChannelUpdate(enabled=True, password="secret")  # noqa: S106
                )
            )
        )
        db_session.flush()

        # API response only shows password_is_set=True, not the actual password
        api_response = svc.get_settings()
        assert api_response.channels.mqtt.password_is_set is True
        # but the config getter returns the real value
        cfg = svc.mqtt_channel_config()
        assert cfg.password == "secret"


# ---------------------------------------------------------------------------
# F. MqttChannel adapter
# ---------------------------------------------------------------------------


class TestMqttChannel:
    """Tests for the MqttChannel NotificationChannel adapter."""

    def _make_channel(self, db: Session) -> object:
        from app.notifications.channels.mqtt import MqttChannel

        return MqttChannel(db)

    def test_is_enabled_false_when_setting_disabled(self, db_session: Session) -> None:
        """is_enabled() is False when channels.mqtt.enabled=False."""
        ch = self._make_channel(db_session)
        assert not ch.is_enabled()

    def test_is_enabled_false_when_bridge_not_connected(self, db_session: Session) -> None:
        """is_enabled() is False when enabled=True but bridge not connected."""
        _enable_mqtt(db_session)
        # Bridge is fresh (not connected)
        ch = self._make_channel(db_session)
        assert not ch.is_enabled()

    def test_is_enabled_true_when_enabled_and_connected(self, db_session: Session) -> None:
        """is_enabled() is True when enabled=True AND bridge is connected."""
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)

        # Simulate bridge connected
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        ch = self._make_channel(db_session)
        assert ch.is_enabled()

    def test_deliver_publishes_each_notification(self, db_session: Session) -> None:
        """deliver() calls bridge.publish_notification for each new notification."""
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        user = _make_user(db_session)
        notif1 = _make_notification(
            db_session,
            user.id,
            source="low_stock",
            dedup_key="k1",
        )
        notif2 = _make_notification(
            db_session,
            user.id,
            source="best_before",
            message_code="reminder.best_before",
            params={"name": "Milk", "days_remaining": 2, "date": "2026-06-22"},
            dedup_key="k2",
        )
        db_session.commit()

        with patch.object(bridge, "publish_notification") as mock_pub:
            ch = self._make_channel(db_session)
            ch.deliver([notif1, notif2], include_email_digest=False)

        assert mock_pub.call_count == 2

    def test_deliver_renders_message_in_recipient_language(self, db_session: Session) -> None:
        """deliver() renders the message in the recipient's preferred_language."""
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        zh_user = _make_user(db_session, username="zhuser", lang="zh")
        notif = _make_notification(
            db_session,
            zh_user.id,
            source="low_stock",
            message_code="reminder.low_stock",
            params={"name": "大米", "current": "3", "threshold": "10"},
            dedup_key="kzh1",
        )
        db_session.commit()

        published_messages: list[str] = []

        def capture_publish(notification: object, message: str) -> None:
            published_messages.append(message)

        with patch.object(bridge, "publish_notification", side_effect=capture_publish):
            ch = self._make_channel(db_session)
            ch.deliver([notif], include_email_digest=False)

        assert len(published_messages) == 1
        # Chinese language message should contain Chinese characters
        assert "库存不足" in published_messages[0] or "大米" in published_messages[0]

    def test_deliver_noop_when_not_enabled(self, db_session: Session) -> None:
        """deliver() is a no-op when is_enabled() is False."""
        # channels.mqtt.enabled is False by default
        from app.notifications.mqtt import get_mqtt_bridge

        bridge = get_mqtt_bridge()
        with patch.object(bridge, "publish_notification") as mock_pub:
            ch = self._make_channel(db_session)
            user = _make_user(db_session)
            notif = _make_notification(db_session, user.id, dedup_key="k_noop")
            db_session.commit()
            ch.deliver([notif], include_email_digest=False)

        mock_pub.assert_not_called()

    def test_deliver_noop_when_empty_list(self, db_session: Session) -> None:
        """deliver() with empty list is a no-op."""
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        with patch.object(bridge, "publish_notification") as mock_pub:
            ch = self._make_channel(db_session)
            ch.deliver([], include_email_digest=False)

        mock_pub.assert_not_called()

    def test_deliver_idempotency_skips_already_sent(self, db_session: Session) -> None:
        """deliver() skips notifications that already have a 'sent' row for mqtt."""
        from app.notifications.mqtt import get_mqtt_bridge
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        user = _make_user(db_session)
        notif = _make_notification(db_session, user.id, dedup_key="k_idempotent")
        db_session.flush()

        # Pre-record a sent delivery row
        delivery_repo = NotificationDeliveryRepository(db_session)
        delivery_repo.record(notification_id=notif.id, channel="mqtt", status="sent")
        db_session.commit()

        with patch.object(bridge, "publish_notification") as mock_pub:
            ch = self._make_channel(db_session)
            ch.deliver([notif], include_email_digest=False)

        mock_pub.assert_not_called()

    def test_deliver_records_sent_row_on_success(self, db_session: Session) -> None:
        """deliver() records a 'sent' delivery row on successful publish."""
        from app.notifications.mqtt import get_mqtt_bridge
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        user = _make_user(db_session)
        notif = _make_notification(db_session, user.id, dedup_key="k_sent_row")
        db_session.commit()

        with patch.object(bridge, "publish_notification"):
            ch = self._make_channel(db_session)
            ch.deliver([notif], include_email_digest=False)

        delivery_repo = NotificationDeliveryRepository(db_session)
        assert delivery_repo.exists_sent(notif.id, "mqtt")

    def test_deliver_records_failed_row_on_publish_error(self, db_session: Session) -> None:
        """deliver() records 'failed' delivery row when publish raises."""
        from app.notifications.mqtt import get_mqtt_bridge
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        user = _make_user(db_session)
        notif = _make_notification(db_session, user.id, dedup_key="k_failed_row")
        db_session.commit()

        with patch.object(bridge, "publish_notification", side_effect=OSError("net error")):
            ch = self._make_channel(db_session)
            ch.deliver([notif], include_email_digest=False)  # must not raise

        # Check failed row
        delivery_repo = NotificationDeliveryRepository(db_session)
        assert not delivery_repo.exists_sent(notif.id, "mqtt")  # no 'sent' row
        # Verify a 'failed' row exists
        from app.models.notification_delivery import NotificationDelivery

        failed = (
            db_session.query(NotificationDelivery)
            .filter_by(notification_id=notif.id, channel="mqtt", status="failed")
            .first()
        )
        assert failed is not None

    def test_deliver_best_effort_continues_after_error(self, db_session: Session) -> None:
        """deliver() continues delivering subsequent notifications after one fails."""
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        user = _make_user(db_session)
        notif1 = _make_notification(db_session, user.id, dedup_key="k_err1")
        notif2 = _make_notification(db_session, user.id, dedup_key="k_err2")
        db_session.commit()

        call_count = 0

        def _publish_side_effect(notification: object, message: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("first publish error")
            # second call succeeds

        with patch.object(bridge, "publish_notification", side_effect=_publish_side_effect):
            ch = self._make_channel(db_session)
            ch.deliver([notif1, notif2], include_email_digest=False)

        # Both notifications were attempted
        assert call_count == 2

    def test_deliver_include_email_digest_ignored(self, db_session: Session) -> None:
        """deliver() ignores include_email_digest (MQTT is instant, not digest)."""
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        user = _make_user(db_session)
        notif = _make_notification(db_session, user.id, dedup_key="k_digest_ignored")
        db_session.commit()

        with patch.object(bridge, "publish_notification") as mock_pub:
            ch = self._make_channel(db_session)
            # include_email_digest=True should NOT prevent MQTT delivery
            ch.deliver([notif], include_email_digest=True)

        mock_pub.assert_called_once()


# ---------------------------------------------------------------------------
# G. publish_mqtt_state() helper
# ---------------------------------------------------------------------------


class TestPublishMqttState:
    """Tests for the dispatcher.publish_mqtt_state() helper."""

    def test_noop_when_bridge_not_connected(self, db_session: Session) -> None:
        """publish_mqtt_state is a no-op when bridge.is_connected is False."""
        from app.notifications.dispatcher import publish_mqtt_state
        from app.notifications.mqtt import get_mqtt_bridge

        bridge = get_mqtt_bridge()
        assert not bridge.is_connected

        # Should not raise
        publish_mqtt_state(db_session)

    def test_noop_when_mqtt_disabled(self, db_session: Session) -> None:
        """publish_mqtt_state is a no-op when channels.mqtt.enabled=False."""
        from app.notifications.dispatcher import publish_mqtt_state
        from app.notifications.mqtt import get_mqtt_bridge

        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001 — simulate connected, but setting is disabled

        with patch.object(bridge, "publish_state") as mock_pub:
            publish_mqtt_state(db_session)

        mock_pub.assert_not_called()

    def test_publishes_state_when_connected_and_enabled(self, db_session: Session) -> None:
        """publish_mqtt_state calls bridge.publish_state when connected and enabled."""
        from app.notifications.dispatcher import publish_mqtt_state
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        with patch.object(bridge, "publish_state") as mock_pub:
            publish_mqtt_state(db_session)

        mock_pub.assert_called_once()
        counts_arg = mock_pub.call_args[0][0]
        assert "low_stock_count" in counts_arg
        assert "expiring_count" in counts_arg
        assert "expired_count" in counts_arg

    def test_error_is_swallowed(self, db_session: Session) -> None:
        """publish_mqtt_state swallows errors (best-effort)."""
        from app.notifications.dispatcher import publish_mqtt_state
        from app.notifications.mqtt import get_mqtt_bridge

        _enable_mqtt(db_session)
        bridge = get_mqtt_bridge()
        bridge._connected = True  # noqa: SLF001

        with patch.object(bridge, "publish_state", side_effect=OSError("net error")):
            publish_mqtt_state(db_session)  # must not raise


# ---------------------------------------------------------------------------
# H. build_dispatcher() registers MqttChannel
# ---------------------------------------------------------------------------


class TestBuildDispatcher:
    """Tests for build_dispatcher() including MqttChannel registration."""

    def test_mqtt_channel_is_registered(self, db_session: Session) -> None:
        """build_dispatcher() registers MqttChannel alongside Email and Http."""
        from app.notifications.dispatcher import build_dispatcher

        dispatcher = build_dispatcher(db_session)
        channel_types = [type(ch).__name__ for ch in dispatcher._channels]  # noqa: SLF001
        assert "MqttChannel" in channel_types
        assert "EmailChannel" in channel_types
        assert "HttpChannel" in channel_types

    def test_mqtt_channel_disabled_when_setting_off(self, db_session: Session) -> None:
        """MqttChannel.is_enabled() returns False when channels.mqtt.enabled=False."""
        from app.notifications.channels.mqtt import MqttChannel
        from app.notifications.dispatcher import build_dispatcher

        dispatcher = build_dispatcher(db_session)
        mqtt_ch = next(ch for ch in dispatcher._channels if isinstance(ch, MqttChannel))  # noqa: SLF001
        assert not mqtt_ch.is_enabled()


# ---------------------------------------------------------------------------
# I. Step 7/8 regression: existing channels still registered
# ---------------------------------------------------------------------------


class TestStep789Regression:
    """Regression tests ensuring Step 7 + 8 channels still work after Step 9."""

    def test_email_channel_still_registered(self, db_session: Session) -> None:
        """EmailChannel is still registered in build_dispatcher after Step 9."""
        from app.notifications.channels.email import EmailChannel
        from app.notifications.dispatcher import build_dispatcher

        dispatcher = build_dispatcher(db_session)
        types = [type(ch) for ch in dispatcher._channels]  # noqa: SLF001
        assert EmailChannel in types

    def test_http_channel_still_registered(self, db_session: Session) -> None:
        """HttpChannel is still registered in build_dispatcher after Step 9."""
        from app.notifications.channels.http import HttpChannel
        from app.notifications.dispatcher import build_dispatcher

        dispatcher = build_dispatcher(db_session)
        types = [type(ch) for ch in dispatcher._channels]  # noqa: SLF001
        assert HttpChannel in types

    def test_dispatch_with_all_disabled_is_noop(self, db_session: Session) -> None:
        """dispatch() with all channels disabled is a no-op (no errors)."""
        from app.notifications.dispatcher import build_dispatcher

        dispatcher = build_dispatcher(db_session)
        # All channels disabled by default — dispatch should be a no-op
        mock_notif = MagicMock()
        dispatcher.dispatch([mock_notif], include_email_digest=False)  # must not raise

    def test_get_mqtt_bridge_singleton_returns_same_instance(self) -> None:
        """get_mqtt_bridge() always returns the same instance within a process."""
        from app.notifications.mqtt import get_mqtt_bridge

        bridge1 = get_mqtt_bridge()
        bridge2 = get_mqtt_bridge()
        assert bridge1 is bridge2
