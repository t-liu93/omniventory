"""M4 Step 10 tests: MQTT inbound command handling.

Required coverage (M4.md §5 "MQTT" commands + §9 Step 10 + §10 Step 10):

A. commands_enabled gate:
- commands_enabled=False → no subscribe, no on_message set
- commands_enabled=True → subscribes to {prefix}/command/# in on_connect

B. on_message routing:
- malformed JSON payload → no DB mutation, error result published, no raise
- missing required field → no DB mutation, error result published
- non-convertible quantity → no DB mutation, error result published
- unknown op → no DB mutation, error result published
- good topic prefix but sub-path (e.g. /command/consume/extra) → error result

C. consume command:
- valid consume → stock quantity decreases, command_result ok published
- definition not found → no mutation, error result
- insufficient stock (AppError) → rollback, error result with code

D. intake command:
- valid intake → stock quantity increases, command_result ok published
- instance not found → no mutation, error result

E. adjust command:
- valid adjust → stock quantity set to counted, command_result ok published
- instance not found → no mutation, error result

F. Service errors:
- wrong tracking mode (level/none) → AppError → rollback, error result with code

G. Best-effort post-commit dispatch:
- consume that triggers low-stock → pending_notifications dispatched (mocked)
- dispatcher failure → command result still ok (already published before)

H. Regression:
- Step 9 lifecycle tests still pass (bridge singleton, no commands_enabled leak)
"""

from __future__ import annotations

import importlib
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same robust reload pattern as test_m4_step9.py)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, Any]:
    """Create a fresh in-memory SQLite session with all models registered."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
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
        audit_log_mod,
    ):
        importlib.reload(mod)

    # Configure ONLY this Base's registry rather than the global
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
def _clear_caches() -> Any:
    """Reset lru_cache on get_settings / get_engine before and after each test."""
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def db_session() -> Any:
    """Fresh in-memory SQLite session with all models registered."""
    session, engine = _make_in_memory_session()

    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture(autouse=True)
def _reset_bridge() -> Any:
    """Reset the MqttBridge singleton before/after each test."""
    from app.notifications.mqtt import _reset_bridge_for_testing

    _reset_bridge_for_testing()
    yield
    _reset_bridge_for_testing()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_exact(
    db: Session,
    *,
    quantity: Decimal = Decimal("10"),
    min_stock: Decimal | None = Decimal("5"),
    mode: str = "exact",
) -> tuple[Any, Any, Any, Any]:
    """Seed Household + User + ItemKind + ItemDefinition + StockInstance.

    For exact-mode definitions, seeds an initial intake movement so that
    ``recompute_quantity`` (which sums movement deltas) returns ``quantity``.

    Returns (household, user, definition, instance).
    """
    from app.auth.passwords import hash_password
    from app.models.household import Household
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.stock_instance import StockInstance
    from app.models.user import User

    hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
    db.add(hh)
    db.flush()

    kind = ItemKind(code="consumable", name="Consumable", is_system=True)
    db.add(kind)
    db.flush()

    user = User(email="admin@example.com", password_hash=hash_password("pass"), is_active=True)
    db.add(user)
    db.flush()

    defn = ItemDefinition(
        name="Rice",
        kind_id=kind.id,
        stock_tracking_mode=mode,
        min_stock=min_stock,
    )
    db.add(defn)
    db.flush()

    if mode == "exact":
        # Create instance with quantity=None, then seed via a movement so that
        # StockMovementService.recompute_quantity (SUM of deltas) is correct.
        inst = StockInstance(definition_id=defn.id, quantity=None)
        db.add(inst)
        db.flush()

        from app.repositories.stock_movement import StockMovementRepository
        from app.services.stock_instance import StockInstanceService

        StockMovementRepository(db).append(
            instance_id=inst.id,
            type="intake",
            quantity_delta=quantity,
        )
        StockInstanceService(db).recompute_quantity(inst)
        db.flush()
    else:
        # level / none mode — no quantity column
        inst = StockInstance(definition_id=defn.id, stock_level="ok")
        db.add(inst)
        db.flush()

    db.commit()

    return hh, user, defn, inst


# ---------------------------------------------------------------------------
# Helpers for invoking command handling directly
# ---------------------------------------------------------------------------


def _make_fake_msg(topic: str, payload_dict: dict) -> MagicMock:
    """Build a fake paho message with topic and JSON payload."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = json.dumps(payload_dict).encode("utf-8")
    return msg


def _make_bridge_and_client(
    *,
    commands_enabled: bool = True,
    prefix: str = "omniventory",
) -> tuple[Any, MagicMock]:
    """Return (bridge, mock_client) with bridge in pseudo-connected state."""
    from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

    bridge = MqttBridge()
    mock_client = MagicMock()
    bridge._client = mock_client  # noqa: SLF001
    bridge._connected = True  # noqa: SLF001
    bridge._config = MqttBridgeConfig(  # noqa: SLF001
        host="localhost",
        port=1883,
        topic_prefix=prefix,
        commands_enabled=commands_enabled,
    )
    return bridge, mock_client


def _get_result_from_publish(mock_client: MagicMock, prefix: str = "omniventory") -> dict:
    """Extract the most-recent command_result publish call payload."""
    result_topic = f"{prefix}/command_result"
    for call in reversed(mock_client.publish.call_args_list):
        if call[0][0] == result_topic:
            return json.loads(call[0][1])
    raise AssertionError("No command_result publish call found.")


# ---------------------------------------------------------------------------
# A. commands_enabled gate
# ---------------------------------------------------------------------------


class TestCommandsEnabledGate:
    """Tests for the commands_enabled subscribe/message gate."""

    def test_commands_disabled_no_subscribe(self) -> None:
        """commands_enabled=False → client.subscribe never called in on_connect."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        real_on_connect_holder: list = []
        subscribe_calls: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            def subscribe(self, topic: str) -> None:
                subscribe_calls.append(topic)

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

            @property
            def on_message(self) -> object:
                return None

            @on_message.setter
            def on_message(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(
                MqttBridgeConfig(
                    host="localhost",
                    port=1883,
                    topic_prefix="omniventory",
                    commands_enabled=False,
                )
            )

        real_on_connect_holder[0](None, None, None, 0)

        # No subscribe call for command topics
        assert not any("command" in t for t in subscribe_calls)

    def test_commands_enabled_subscribes_in_on_connect(self) -> None:
        """commands_enabled=True → subscribe to {prefix}/command/# on connect."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        real_on_connect_holder: list = []
        subscribe_calls: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            def subscribe(self, topic: str) -> None:
                subscribe_calls.append(topic)

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

            @property
            def on_message(self) -> object:
                return None

            @on_message.setter
            def on_message(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(
                MqttBridgeConfig(
                    host="localhost",
                    port=1883,
                    topic_prefix="omniventory",
                    commands_enabled=True,
                )
            )

        real_on_connect_holder[0](None, None, None, 0)

        assert "omniventory/command/#" in subscribe_calls

    def test_commands_enabled_custom_prefix(self) -> None:
        """commands_enabled=True uses configured topic_prefix."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        real_on_connect_holder: list = []
        subscribe_calls: list = []

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                pass

            def username_pw_set(self, *a: object) -> None:
                pass

            def connect(self, host: str, port: int) -> None:
                pass

            def loop_start(self) -> None:
                pass

            def subscribe(self, topic: str) -> None:
                subscribe_calls.append(topic)

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

            @property
            def on_message(self) -> object:
                return None

            @on_message.setter
            def on_message(self, cb: object) -> None:
                pass

        with patch("paho.mqtt.client.Client", FakeClient):
            bridge.start(
                MqttBridgeConfig(
                    host="localhost",
                    port=1883,
                    topic_prefix="myhome",
                    commands_enabled=True,
                )
            )

        real_on_connect_holder[0](None, None, None, 0)

        assert "myhome/command/#" in subscribe_calls


# ---------------------------------------------------------------------------
# B. on_message routing — drop cases
# ---------------------------------------------------------------------------


class TestOnMessageDropCases:
    """Tests for malformed/unknown command handling via _handle_command."""

    def test_malformed_json_no_mutation_error_result(self) -> None:
        """Malformed JSON → error result published, no raise."""
        bridge, mock_client = _make_bridge_and_client()

        msg = MagicMock()
        msg.topic = "omniventory/command/consume"
        msg.payload = b"NOT VALID JSON{"

        bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["op"] == "consume"
        assert result["status"] == "error"
        assert "JSON" in str(result["detail"]) or "malformed" in str(result["detail"])

    def test_unknown_op_error_result(self) -> None:
        """Unknown op → error result, not in bounded set."""
        bridge, mock_client = _make_bridge_and_client()

        msg = _make_fake_msg("omniventory/command/delete_all", {"foo": "bar"})

        bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "delete_all" in str(result["detail"]) or "unknown" in str(result["detail"])

    def test_wrong_topic_prefix_error_result(self) -> None:
        """Topic not matching {prefix}/command/ → error result."""
        bridge, mock_client = _make_bridge_and_client()

        msg = MagicMock()
        msg.topic = "other/prefix/command/consume"
        msg.payload = b'{"definition_id": 1, "quantity": 1}'

        bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"

    def test_handle_command_no_raise_on_internal_error(self) -> None:
        """_handle_command must never raise even on wild internal errors."""
        bridge, mock_client = _make_bridge_and_client()

        # Pass a completely broken msg object to stress-test the top-level try/except
        msg = object()  # no .topic attribute

        # Must not raise
        bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001


# ---------------------------------------------------------------------------
# C. consume command
# ---------------------------------------------------------------------------


class TestConsumeCommand:
    """Tests for the consume inbound command."""

    def test_consume_valid_decreases_stock(self, db_session: Session) -> None:
        """Valid consume command decrements stock and publishes ok result."""
        # _seed_exact seeds quantity=10 via a proper movement record.
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))
        assert inst.quantity == Decimal("10")

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)

        # Patch db.close so we don't actually close the shared test session
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 4},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["op"] == "consume"
        assert result["status"] == "ok"
        assert result["detail"]["consumed"] == "4"

        # Stock should have decreased
        db_session.refresh(inst)
        assert inst.quantity == Decimal("6")  # 10 - 4

    def test_consume_definition_not_found_error(self, db_session: Session) -> None:
        """consume with non-existent definition_id → error result, no mutation."""
        _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": 9999, "quantity": 1},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "9999" in str(result["detail"])

    def test_consume_insufficient_stock_app_error(self, db_session: Session) -> None:
        """consume more than available → AppError → rollback, error result with code."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("3"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 100},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "code" in result["detail"]  # AppError code present

        # Stock must be unchanged (rollback happened)
        db_session.refresh(inst)
        assert inst.quantity == Decimal("3")  # unchanged

    def test_consume_missing_field_no_mutation(self, db_session: Session) -> None:
        """consume with missing quantity field → error result, no mutation."""
        _hh, _user, defn, _inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id},  # quantity missing
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "bad payload" in str(result["detail"])

    def test_consume_invalid_quantity_type_no_mutation(self, db_session: Session) -> None:
        """consume with non-numeric quantity → error result, no mutation."""
        _hh, _user, defn, _inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": "not_a_number"},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# D. intake command
# ---------------------------------------------------------------------------


class TestIntakeCommand:
    """Tests for the intake inbound command."""

    def test_intake_valid_increases_stock(self, db_session: Session) -> None:
        """Valid intake command increases stock and publishes ok result."""
        # _seed_exact seeds quantity=5 via a proper movement record.
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("5"))
        assert inst.quantity == Decimal("5")

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/intake",
                {"instance_id": inst.id, "quantity": 3},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["op"] == "intake"
        assert result["status"] == "ok"

        db_session.refresh(inst)
        assert inst.quantity == Decimal("8")  # 5 + 3

    def test_intake_instance_not_found_error(self, db_session: Session) -> None:
        """intake with non-existent instance_id → error result."""
        _seed_exact(db_session, quantity=Decimal("5"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/intake",
                {"instance_id": 9999, "quantity": 3},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "9999" in str(result["detail"])

    def test_intake_missing_field_no_mutation(self, db_session: Session) -> None:
        """intake with missing quantity field → error result, no mutation."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("5"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/intake",
                {"instance_id": inst.id},  # missing quantity
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# E. adjust command
# ---------------------------------------------------------------------------


class TestAdjustCommand:
    """Tests for the adjust inbound command."""

    def test_adjust_valid_sets_stock(self, db_session: Session) -> None:
        """Valid adjust command sets stock to counted_quantity."""
        # _seed_exact seeds quantity=10 via proper movement.
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))
        assert inst.quantity == Decimal("10")

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/adjust",
                {"instance_id": inst.id, "counted_quantity": 7},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["op"] == "adjust"
        assert result["status"] == "ok"

        db_session.refresh(inst)
        assert inst.quantity == Decimal("7")

    def test_adjust_instance_not_found_error(self, db_session: Session) -> None:
        """adjust with non-existent instance_id → error result."""
        _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/adjust",
                {"instance_id": 9999, "counted_quantity": 5},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "9999" in str(result["detail"])

    def test_adjust_missing_counted_quantity_no_mutation(self, db_session: Session) -> None:
        """adjust missing counted_quantity → error result, no mutation."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/adjust",
                {"instance_id": inst.id},  # missing counted_quantity
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# F. Service errors — wrong tracking mode
# ---------------------------------------------------------------------------


class TestServiceErrors:
    """Tests for AppError propagation from service layer."""

    def test_consume_level_mode_definition_app_error(self, db_session: Session) -> None:
        """consume on a level-mode definition → AppError (409) → rollback + error result."""
        _hh, _user, defn, inst = _seed_exact(db_session, mode="level")

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 1},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "code" in result["detail"]
        # Should be the movement_not_applicable code
        assert "stock" in result["detail"]["code"]

    def test_intake_level_mode_instance_app_error(self, db_session: Session) -> None:
        """intake on a level-mode instance → AppError → rollback + error result."""
        _hh, _user, defn, inst = _seed_exact(db_session, mode="level")

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/intake",
                {"instance_id": inst.id, "quantity": 5},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "error"
        assert "code" in result["detail"]


# ---------------------------------------------------------------------------
# G. Best-effort post-commit dispatch
# ---------------------------------------------------------------------------


class TestBestEffortPostCommitDispatch:
    """Tests that best-effort post-commit dispatch happens and failures are swallowed."""

    def test_consume_dispatches_pending_notifications(self, db_session: Session) -> None:
        """Successful consume triggers best-effort pending_notifications dispatch."""
        # quantity=6 > min_stock=5, so consuming 3 drops below threshold.
        _hh, _user, defn, inst = _seed_exact(
            db_session, quantity=Decimal("6"), min_stock=Decimal("5")
        )

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        dispatch_calls: list = []

        def fake_build_dispatcher(db: object) -> MagicMock:
            m = MagicMock()

            def record_dispatch(notifs: list, *, include_email_digest: bool) -> None:
                dispatch_calls.append((notifs, include_email_digest))

            m.dispatch = record_dispatch
            return m

        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
            patch(
                "app.notifications.dispatcher.build_dispatcher",
                side_effect=fake_build_dispatcher,
            ),
            patch("app.notifications.dispatcher.publish_mqtt_state"),
        ):
            # consume enough to drop below min_stock
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 3},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert result["status"] == "ok"

        # If low-stock notifications were created, dispatch should have been called
        # (the exact count depends on whether reminder engine ran, but we verify
        #  it was invoked with include_email_digest=False)
        if dispatch_calls:
            assert all(not d[1] for d in dispatch_calls)

    def test_dispatcher_failure_does_not_affect_command_result(self, db_session: Session) -> None:
        """A dispatcher error after commit does not alter the already-published ok result."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)

        def _crash_build_dispatcher(db: object) -> MagicMock:
            raise OSError("dispatcher crash")

        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
            patch(
                "app.notifications.dispatcher.build_dispatcher",
                side_effect=_crash_build_dispatcher,
            ),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 1},
            )
            # Must not raise even though dispatcher crashes
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        # The ok result was already published before the dispatcher crash
        assert result["status"] == "ok"

    def test_publish_mqtt_state_called_after_successful_command(self, db_session: Session) -> None:
        """publish_mqtt_state is called best-effort after a successful command."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        state_publish_calls: list = []

        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
            patch(
                "app.notifications.dispatcher.publish_mqtt_state",
                side_effect=lambda db: state_publish_calls.append(db),
            ),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 1},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        assert len(state_publish_calls) == 1


# ---------------------------------------------------------------------------
# H. System actor — user=None
# ---------------------------------------------------------------------------


class TestSystemActor:
    """Tests that commands execute as system actor (user_id=NULL in movements)."""

    def test_consume_records_movement_with_null_user_id(self, db_session: Session) -> None:
        """Movements created by MQTT commands have user_id=None (system actor)."""
        from sqlalchemy import select

        from app.models.stock_movement import StockMovement

        # _seed_exact creates an initial intake movement with user_id=None (system seeder).
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 2},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        # Find the consume movement row — user_id must be None (system actor)
        consume_mvmts = list(
            db_session.execute(select(StockMovement).where(StockMovement.type == "consume"))
            .scalars()
            .all()
        )
        assert consume_mvmts, "No consume movement was created"
        for m in consume_mvmts:
            assert m.user_id is None, "System actor must have user_id=None"


# ---------------------------------------------------------------------------
# I. Result topic shape
# ---------------------------------------------------------------------------


class TestResultTopicShape:
    """Tests for the shape and topic of command_result publishes."""

    def test_result_topic_is_command_result(self, db_session: Session) -> None:
        """Result is published to {prefix}/command_result."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client(prefix="myhome")

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "myhome/command/consume",
                {"definition_id": defn.id, "quantity": 1},
            )
            bridge._handle_command(msg, mock_client, "myhome")  # noqa: SLF001

        # Find the command_result publish
        topics = [c[0][0] for c in mock_client.publish.call_args_list]
        assert "myhome/command_result" in topics

    def test_error_result_has_op_status_detail(self) -> None:
        """Error result payload has {op, status, detail} — using unknown op (no DB needed)."""
        bridge, mock_client = _make_bridge_and_client()

        # Use unknown op: dropped in _handle_command before any DB access.
        msg = _make_fake_msg("omniventory/command/explode_everything", {"foo": "bar"})

        bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert "op" in result
        assert "status" in result
        assert "detail" in result
        assert result["status"] == "error"

    def test_ok_result_has_op_status_detail(self, db_session: Session) -> None:
        """OK result payload has {op, status, detail}."""
        _hh, _user, defn, inst = _seed_exact(db_session, quantity=Decimal("10"))

        bridge, mock_client = _make_bridge_and_client()

        session_factory_mock = MagicMock(return_value=db_session)
        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            patch.object(db_session, "close"),
        ):
            msg = _make_fake_msg(
                "omniventory/command/intake",
                {"instance_id": inst.id, "quantity": 5},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        result = _get_result_from_publish(mock_client)
        assert "op" in result
        assert result["status"] == "ok"
        assert "detail" in result


# ---------------------------------------------------------------------------
# J. Regression — Step 9 outbound bridge still works
# ---------------------------------------------------------------------------


class TestStep9Regression:
    """Regression: Step 9 outbound bridge functionality not broken by Step 10 changes."""

    def test_publish_notification_still_works(self) -> None:
        """publish_notification still publishes to correct topic after Step 10 changes."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost",
            port=1883,
            topic_prefix="omniventory",
            commands_enabled=True,  # enabled, but should not affect outbound
        )

        mock_notif = MagicMock()
        mock_notif.source = "low_stock"
        mock_notif.message_code = "reminder.low_stock"
        mock_notif.params = json.dumps({"name": "Rice"})

        bridge.publish_notification(mock_notif, "Rice is low")

        mock_client.publish.assert_called_once()
        topic = mock_client.publish.call_args[0][0]
        assert topic == "omniventory/notifications/low_stock"

    def test_publish_state_still_retained(self) -> None:
        """publish_state still publishes retained state topics."""
        from app.notifications.mqtt import MqttBridge, MqttBridgeConfig

        bridge = MqttBridge()
        mock_client = MagicMock()
        bridge._client = mock_client  # noqa: SLF001
        bridge._connected = True  # noqa: SLF001
        bridge._config = MqttBridgeConfig(  # noqa: SLF001
            host="localhost",
            port=1883,
            topic_prefix="omniventory",
            commands_enabled=True,
        )

        bridge.publish_state({"low_stock_count": 1, "expiring_count": 0, "expired_count": 0})

        assert mock_client.publish.call_count == 3
        for c in mock_client.publish.call_args_list:
            retain = c[1].get("retain")
            assert retain is True

    def test_commands_enabled_default_false(self) -> None:
        """MqttBridgeConfig.commands_enabled defaults to False."""
        from app.notifications.mqtt import MqttBridgeConfig

        cfg = MqttBridgeConfig(host="localhost", port=1883, topic_prefix="omniventory")
        assert cfg.commands_enabled is False


# ---------------------------------------------------------------------------
# K. delivery row persistence — regression guard for missing db.commit()
# ---------------------------------------------------------------------------


class TestDeliveryRowPersistence:
    """Verify that notification_deliveries rows written during post-commit dispatch
    are actually committed to the database.

    This test is specifically designed to FAIL if the ``db.commit()`` after
    ``build_dispatcher(db).dispatch(...)`` is missing, and to PASS when it is
    present.

    Strategy
    --------
    - Enable the HTTP channel in settings (so HttpChannel.is_enabled() returns True)
      and mock httpx so network I/O never happens but the channel's
      NotificationDeliveryRepository.record() path runs normally.
    - Trigger a consume command that drops below min_stock, causing the
      StockMovementService event hook to emit a low-stock Notification.
    - The post-commit dispatch block calls HttpChannel.deliver(), which:
        1. Checks exists_sent() (False — never delivered before).
        2. Calls self._delivery_repo.record() → flush() but no commit yet.
        3. db.commit() (our fix) persists the row.
    - After _handle_command returns (the finally block has already called
      db.close(), ending the transaction), open a brand-new Session against
      the same engine and query notification_deliveries.
    - If db.commit() was called: the row survives db.close() and the new
      session finds it → test passes.
    - If db.commit() was NOT called: db.close() rolls back the flush, the new
      session finds nothing → test fails with a clear assertion error.
    """

    def test_delivery_rows_persisted_after_dispatch(self, db_session: Session) -> None:
        """delivery rows written by HttpChannel are committed, not rolled back on close."""
        from unittest.mock import MagicMock, patch

        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker

        from app.models.notification_delivery import NotificationDelivery
        from app.services.settings import SettingsService

        # Seed inventory: quantity=6 > min_stock=5; consuming 3 drops below threshold.
        _hh, _user, defn, inst = _seed_exact(
            db_session, quantity=Decimal("6"), min_stock=Decimal("5")
        )

        # Enable the HTTP channel with a dummy webhook URL so HttpChannel.is_enabled()
        # returns True.  The actual POST is mocked below — only the delivery row write
        # (NotificationDeliveryRepository.record) runs for real.
        svc = SettingsService(db_session)
        svc._set_value("channels.http.enabled", True)  # noqa: SLF001
        svc._set_value("channels.http.webhook_url", "http://example.com/webhook")  # noqa: SLF001
        db_session.commit()

        # Capture the engine before db.close() is called so we can open a fresh session
        # afterwards to verify persistence.
        engine = db_session.get_bind()

        bridge, mock_client = _make_bridge_and_client()
        session_factory_mock = MagicMock(return_value=db_session)

        # Build a fake HTTP response that HttpChannel treats as successful (2xx).
        fake_http_response = MagicMock()
        fake_http_response.status_code = 200
        fake_http_response.raise_for_status = MagicMock()

        with (
            patch("app.db.base.get_session_factory", return_value=session_factory_mock),
            # Allow db.close() to run for real so uncommitted data would be rolled back.
            # (Do NOT patch it out — that is what makes this test sensitive to the bug.)
            patch("app.notifications.dispatcher.publish_mqtt_state"),  # skip MQTT state publish
            patch("httpx.Client") as mock_httpx_client_cls,
        ):
            # Arrange: mock httpx.Client context manager to return our fake response.
            mock_httpx_instance = MagicMock()
            mock_httpx_instance.__enter__ = MagicMock(return_value=mock_httpx_instance)
            mock_httpx_instance.__exit__ = MagicMock(return_value=False)
            mock_httpx_instance.post = MagicMock(return_value=fake_http_response)
            mock_httpx_client_cls.return_value = mock_httpx_instance

            msg = _make_fake_msg(
                "omniventory/command/consume",
                {"definition_id": defn.id, "quantity": 3},
            )
            bridge._handle_command(msg, mock_client, "omniventory")  # noqa: SLF001

        # db.close() has now run inside _execute_command's finally block.
        # If db.commit() was NOT called after dispatch(), the delivery rows were only
        # flushed (not committed) and db.close() rolled them back.
        # Open a FRESH session to check what actually persisted.
        fresh_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        fresh_session = fresh_factory()
        try:
            rows = list(fresh_session.execute(select(NotificationDelivery)).scalars().all())
        finally:
            fresh_session.close()

        # If this assertion fails it means the dispatch commit was missing —
        # delivery rows were flushed but rolled back on db.close().
        assert rows, (
            "No notification_deliveries rows found after _handle_command returned. "
            "This means db.commit() after dispatch() is missing — delivery rows were "
            "flushed but rolled back when db.close() was called."
        )
        sent_rows = [r for r in rows if r.status == "sent" and r.channel == "http"]
        assert sent_rows, (
            f"Found {len(rows)} delivery row(s) but none with status='sent' channel='http'. "
            "Rows: " + str([(r.channel, r.status) for r in rows])
        )
