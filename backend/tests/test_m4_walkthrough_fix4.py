"""走查整改 #4 tests: live MQTT reconnect on settings save + MQTT test endpoint.

Required coverage:

A. MqttBridge.start() safe-to-call-repeatedly:
   - Calls connect_async + loop_start (non-blocking; no blocking connect).
   - When called a second time, stops the previous client first (no leak).
   - Uses connect_async, NOT connect.

B. reload_mqtt_bridge:
   - No-op when environment == "test".
   - When enabled + host: stops existing bridge then starts with new config.
   - When disabled (enabled=False): stops bridge, does NOT start.
   - When unconfigured (no host): stops bridge, does NOT start.
   - Best-effort: start() exception does NOT propagate.

C. PATCH /settings triggers reload when mqtt fields changed:
   - Mocked reload is called after commit when channels.mqtt.* is updated.
   - Non-mqtt change (reminders) does NOT require the reload.

D. POST /settings/mqtt/test endpoint:
   - Success: ok=true, topic set.
   - No host: ok=false, detail "not configured".
   - Helper raises: ok=false, detail from exception.
   - Unauthenticated: 401.

E. mqtt_send_test one-shot helper:
   - Happy path: publishes to {prefix}/test retained=True; returns topic.
   - Timeout path: raises TimeoutError when connect_event never fires.
   - rc != 0: raises RuntimeError.
   - Always calls loop_stop + disconnect in finally.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as other M4 step tests)
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


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_fix4_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def db_session() -> Generator[Session]:
    session, engine = _make_in_memory_session()
    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture(autouse=True)
def _reset_bridge() -> Generator[None]:
    from app.notifications.mqtt import _reset_bridge_for_testing

    _reset_bridge_for_testing()
    yield
    _reset_bridge_for_testing()


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-fix4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    from fastapi.testclient import TestClient

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

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    application = create_app()

    with TestClient(application, raise_server_exceptions=True) as client:
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.models.item_kind import ItemKind
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(email="admin@example.com", password_hash=hash_password("adminpass"))
            for code, name in [
                ("durable", "Durable"),
                ("consumable", "Consumable"),
                ("perishable", "Perishable"),
            ]:
                db.add(ItemKind(code=code, name=name, is_system=True))
            db.commit()
        finally:
            db.close()

        resp = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert resp.status_code == 200
        yield client

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# A. MqttBridge.start() safe to call repeatedly
# ---------------------------------------------------------------------------


class TestMqttBridgeStartRepeatedly:
    """MqttBridge.start() stops the previous client before starting a new one."""

    def test_start_uses_connect_async_not_connect(self) -> None:
        """start() calls connect_async (non-blocking) not the blocking connect()."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(MqttBridgeConfig(host="broker.example", port=1883, topic_prefix="omni"))

        mock_client.connect_async.assert_called_once_with("broker.example", 1883)
        mock_client.connect.assert_not_called()
        mock_client.loop_start.assert_called_once()

    def test_second_start_stops_previous_client(self) -> None:
        """A second start() call stops the previous paho client before starting a new one."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        first_client = MagicMock()
        second_client = MagicMock()

        clients = [first_client, second_client]
        call_idx = {"i": 0}

        def _make_client(**kwargs: object) -> MagicMock:
            idx = call_idx["i"]
            call_idx["i"] += 1
            return clients[idx]

        with patch("paho.mqtt.client.Client", side_effect=_make_client):
            bridge.start(MqttBridgeConfig(host="broker.example", port=1883, topic_prefix="omni"))
            bridge.start(MqttBridgeConfig(host="broker2.example", port=1884, topic_prefix="omni"))

        # The first client must have been stopped before the second start
        first_client.loop_stop.assert_called_once()
        first_client.disconnect.assert_called_once()
        # The second client is connected
        second_client.connect_async.assert_called_once_with("broker2.example", 1884)
        second_client.loop_start.assert_called_once()

    def test_first_start_with_no_previous_client_does_not_stop_anything(self) -> None:
        """First start() with no previous client skips the stop path (no NoneType error)."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()

        # Should not raise even though _client was None
        with patch("paho.mqtt.client.Client", return_value=mock_client):
            bridge.start(MqttBridgeConfig(host="b", port=1883, topic_prefix="omni"))

        mock_client.connect_async.assert_called_once()


# ---------------------------------------------------------------------------
# B. reload_mqtt_bridge
# ---------------------------------------------------------------------------


class TestReloadMqttBridge:
    """reload_mqtt_bridge behaves correctly in all scenarios."""

    def test_noop_in_test_environment(self, db_session: Session) -> None:
        """reload_mqtt_bridge is a no-op when environment == 'test'."""
        from app.notifications.mqtt import get_mqtt_bridge, reload_mqtt_bridge

        bridge = get_mqtt_bridge()

        with patch.object(bridge, "start") as mock_start, patch.object(bridge, "stop") as mock_stop:
            reload_mqtt_bridge(db_session, environment="test")

        mock_start.assert_not_called()
        mock_stop.assert_not_called()

    def test_starts_when_enabled_and_host_set(self, db_session: Session) -> None:
        """When enabled + host: reload calls bridge.start() with the new config.

        The stop of the old client happens INSIDE bridge.start() (the "safe
        to call repeatedly" guarantee in MqttBridge).  reload_mqtt_bridge
        itself does NOT call bridge.stop() when enabled — it delegates to
        bridge.start() which handles the teardown internally.
        """
        from app.notifications.mqtt import get_mqtt_bridge, reload_mqtt_bridge
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        SettingsService(db_session).apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(
                    mqtt=MqttChannelUpdate(
                        enabled=True,
                        host="broker.example",
                        port=1883,
                        topic_prefix="omni",
                    )
                )
            )
        )
        db_session.flush()

        bridge = get_mqtt_bridge()

        with patch.object(bridge, "start") as mock_start, patch.object(bridge, "stop") as mock_stop:
            reload_mqtt_bridge(db_session, environment="production")

        mock_start.assert_called_once()
        # reload does NOT call bridge.stop() when enabled; stop happens inside start()
        mock_stop.assert_not_called()
        cfg_passed = mock_start.call_args[0][0]
        assert cfg_passed.host == "broker.example"
        assert cfg_passed.port == 1883

    def test_stops_but_does_not_start_when_disabled(self, db_session: Session) -> None:
        """When enabled=False: stops the bridge, does NOT start."""
        from app.notifications.mqtt import get_mqtt_bridge, reload_mqtt_bridge
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        SettingsService(db_session).apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(
                    mqtt=MqttChannelUpdate(enabled=False, host="broker.example", port=1883)
                )
            )
        )
        db_session.flush()

        bridge = get_mqtt_bridge()

        with patch.object(bridge, "stop") as mock_stop, patch.object(bridge, "start") as mock_start:
            reload_mqtt_bridge(db_session, environment="production")

        mock_stop.assert_called_once()
        mock_start.assert_not_called()

    def test_stops_but_does_not_start_when_no_host(self, db_session: Session) -> None:
        """When enabled=True but host not set: stops bridge, does NOT start."""
        from app.notifications.mqtt import get_mqtt_bridge, reload_mqtt_bridge
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        SettingsService(db_session).apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(
                    mqtt=MqttChannelUpdate(enabled=True)  # no host
                )
            )
        )
        db_session.flush()

        bridge = get_mqtt_bridge()

        with patch.object(bridge, "stop") as mock_stop, patch.object(bridge, "start") as mock_start:
            reload_mqtt_bridge(db_session, environment="production")

        mock_stop.assert_called_once()
        mock_start.assert_not_called()

    def test_best_effort_start_exception_does_not_propagate(self, db_session: Session) -> None:
        """A start() exception during reload does not propagate to the caller."""
        from app.notifications.mqtt import get_mqtt_bridge, reload_mqtt_bridge
        from app.schemas.settings import ChannelsUpdate, MqttChannelUpdate, SettingsUpdate
        from app.services.settings import SettingsService

        SettingsService(db_session).apply_update(
            SettingsUpdate(
                channels=ChannelsUpdate(
                    mqtt=MqttChannelUpdate(enabled=True, host="broker.example", port=1883)
                )
            )
        )
        db_session.flush()

        bridge = get_mqtt_bridge()

        with (
            patch.object(bridge, "stop"),
            patch.object(bridge, "start", side_effect=RuntimeError("broker down")),
        ):
            # Must NOT raise — best-effort
            reload_mqtt_bridge(db_session, environment="production")


# ---------------------------------------------------------------------------
# C. PATCH /settings triggers reload when mqtt fields changed
# ---------------------------------------------------------------------------


class TestPatchSettingsMqttReload:
    """PATCH /settings calls reload_mqtt_bridge when channels.mqtt.* is updated."""

    def test_mqtt_update_triggers_reload(self, http_client: object) -> None:
        """Updating channels.mqtt.host calls reload_mqtt_bridge after commit.

        reload_mqtt_bridge is imported inside the patch_settings function body
        (lazy import pattern), so we patch it at the source module level.
        """
        with patch("app.notifications.mqtt.reload_mqtt_bridge") as mock_reload:
            resp = http_client.patch(  # type: ignore[attr-defined]
                "/api/settings",
                json={"channels": {"mqtt": {"host": "broker.example", "port": 1883}}},
            )

        assert resp.status_code == 200
        mock_reload.assert_called_once()

    def test_non_mqtt_update_does_not_require_reload(self, http_client: object) -> None:
        """Updating only reminders does not trigger reload_mqtt_bridge."""
        with patch("app.notifications.mqtt.reload_mqtt_bridge") as mock_reload:
            resp = http_client.patch(  # type: ignore[attr-defined]
                "/api/settings",
                json={"reminders": {"best_before_lead_days": 5}},
            )

        assert resp.status_code == 200
        mock_reload.assert_not_called()


# ---------------------------------------------------------------------------
# D. POST /settings/mqtt/test endpoint
# ---------------------------------------------------------------------------


class TestMqttTestEndpoint:
    """POST /settings/mqtt/test diagnostic endpoint behaviour."""

    def test_success_ok_true(self, http_client: object) -> None:
        """With host configured and mqtt_send_test mocked to succeed → ok=true, topic set."""
        http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={"channels": {"mqtt": {"host": "broker.example", "port": 1883}}},
        )

        # mqtt_send_test is imported lazily inside the endpoint; patch at source module.
        with patch("app.notifications.mqtt.mqtt_send_test", return_value="omniventory/test"):
            resp = http_client.post("/api/settings/mqtt/test")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["detail"] is None
        assert data["topic"] == "omniventory/test"

    def test_no_host_ok_false(self, http_client: object) -> None:
        """When no host is configured → ok=false, detail mentions 'not configured'."""
        # Do NOT configure a host (default state)
        resp = http_client.post("/api/settings/mqtt/test")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "not configured" in (data["detail"] or "").lower()
        assert data["topic"] == ""

    def test_helper_raises_ok_false_with_detail(self, http_client: object) -> None:
        """When mqtt_send_test raises → ok=false, detail is the exception message."""
        http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={"channels": {"mqtt": {"host": "broker.example", "port": 1883}}},
        )

        # mqtt_send_test is imported lazily inside the endpoint; patch at source module.
        with patch(
            "app.notifications.mqtt.mqtt_send_test",
            side_effect=TimeoutError("connection timeout"),
        ):
            resp = http_client.post("/api/settings/mqtt/test")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "timeout" in (data["detail"] or "").lower()
        assert data["topic"] == ""

    def test_unauthenticated_returns_401(self, http_client: object) -> None:
        """An unauthenticated request returns 401."""
        import httpx

        base_url = "http://testserver"
        with httpx.Client(
            base_url=base_url,
            transport=http_client._transport,  # type: ignore[attr-defined]
        ) as bare_client:
            resp = bare_client.post("/api/settings/mqtt/test")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# E. mqtt_send_test one-shot helper
# ---------------------------------------------------------------------------


class TestMqttSendTest:
    """mqtt_send_test() unit tests — never open a real MQTT socket."""

    def _make_config(self) -> object:
        from app.notifications.mqtt import MqttBridgeConfig

        return MqttBridgeConfig(
            host="broker.example",
            port=1883,
            topic_prefix="omniventory",
        )

    def test_happy_path_publishes_retained_to_prefix_test(self) -> None:
        """On success: publishes retained to {prefix}/test, returns the topic."""
        from app.notifications.mqtt import mqtt_send_test

        mock_client = MagicMock()

        # Simulate on_connect being called with rc=0 immediately after connect_async
        def _connect_async(host: str, port: int) -> None:
            # Trigger the on_connect callback that mqtt_send_test registered
            on_connect = mock_client.on_connect
            if callable(on_connect):
                on_connect(mock_client, None, None, 0)

        mock_client.connect_async.side_effect = _connect_async
        mock_client.loop_start = MagicMock()
        mock_client.loop_stop = MagicMock()
        mock_client.disconnect = MagicMock()

        mock_msg_info = MagicMock()
        mock_msg_info.wait_for_publish = MagicMock(return_value=None)
        mock_client.publish.return_value = mock_msg_info

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            topic = mqtt_send_test(self._make_config())  # type: ignore[arg-type]

        assert topic == "omniventory/test"
        # Must have published with retain=True to the test topic
        publish_call = mock_client.publish.call_args
        assert publish_call[0][0] == "omniventory/test"
        assert publish_call[1].get("retain") is True
        # wait_for_publish was called
        mock_msg_info.wait_for_publish.assert_called_once()

    def test_always_calls_loop_stop_and_disconnect_in_finally(self) -> None:
        """loop_stop() and disconnect() are always called even on success."""
        from app.notifications.mqtt import mqtt_send_test

        mock_client = MagicMock()

        def _connect_async(host: str, port: int) -> None:
            on_connect = mock_client.on_connect
            if callable(on_connect):
                on_connect(mock_client, None, None, 0)

        mock_client.connect_async.side_effect = _connect_async

        mock_msg_info = MagicMock()
        mock_msg_info.wait_for_publish = MagicMock()
        mock_client.publish.return_value = mock_msg_info

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            mqtt_send_test(self._make_config())  # type: ignore[arg-type]

        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    def test_timeout_raises_timeout_error(self) -> None:
        """When the broker does not respond within 5 s → raises TimeoutError."""
        from app.notifications.mqtt import mqtt_send_test

        mock_client = MagicMock()
        # connect_async does NOT trigger on_connect, so the event never fires.
        mock_client.connect_async = MagicMock()

        # Patch threading.Event.wait to return False immediately (simulate timeout).
        class _FakeEvent:
            def set(self) -> None:
                pass

            def wait(self, timeout: float = 0) -> bool:
                return False  # always "timed out"

        with (
            patch("threading.Event", return_value=_FakeEvent()),
            patch("paho.mqtt.client.Client", return_value=mock_client),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            mqtt_send_test(self._make_config())  # type: ignore[arg-type]

        # cleanup still happens
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()

    def test_rc_nonzero_raises_runtime_error(self) -> None:
        """When broker returns rc != 0 → raises RuntimeError with rc info."""
        from app.notifications.mqtt import mqtt_send_test

        mock_client = MagicMock()

        def _connect_async(host: str, port: int) -> None:
            on_connect = mock_client.on_connect
            if callable(on_connect):
                on_connect(mock_client, None, None, 5)  # rc=5 = not authorised

        mock_client.connect_async.side_effect = _connect_async

        with (
            patch("paho.mqtt.client.Client", return_value=mock_client),
            pytest.raises(RuntimeError, match="rc=5"),
        ):
            mqtt_send_test(self._make_config())  # type: ignore[arg-type]

        # cleanup still happens
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
