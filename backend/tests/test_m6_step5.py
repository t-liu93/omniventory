"""Tests for M6 Step 5: responsible-party reminder routing + per-user notification prefs.

Coverage
--------
Routing — date sources (best_before / warranty):
- Instance assigned to user A routes that lot's reminder to A only.
- Instance unassigned, definition assigned to B → routes to B only.
- Neither assigned → all active users (M4 broadcast, parity).
- Instance override beats definition default (instance→A, definition→B → A only).

Inactive / deleted responsible → fallback:
- A responsible user who is deactivated collapses to all active users.
- A responsible FK that was SET NULL (deleted user) collapses to all active users.

Low-stock routing (run_scan):
- Definition assigned to C → low-stock opener reaches C only.
- Unassigned definition → all active users.
- Event path (evaluate_low_stock) routes the single definition the same way.

Recovery close semantics:
- Phase 2 closes a previously-broadcast opener even when the definition's
  routing has since changed (Phase 2 uses global low_now, not routed set).

Per-user notification-pref gating:
- notify_email_digest=False: the in-app notification row is still created
  (and the inbox returns it), but the email digest is skipped for that user.
- notify_in_app=False: notification row IS created (feeds the email digest),
  but GET /notifications → [] and GET /notifications/unread-count → 0.
- Both off: NO notification row is created at all.
- Defaults (both True): M4 broadcast parity is reproduced exactly.

M4 parity:
- With no assignments and default prefs, run_scan produces the same
  notification rows as before M6.

Prefs round-trip:
- PATCH /auth/me sets / leaves the two booleans.
- UserResponse carries them.
- Omitted fields are no-ops.

Migration 0031:
- Upgrade cleanly from 0030; notify_in_app + notify_email_digest appear.
- Downgrade back to 0030 removes both columns.
- Existing users get true / true.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# In-memory session factory (for engine-level / unit tests)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[DBSession, object]:
    """Create a fresh in-memory SQLite session with all models registered.

    Reloads model modules so the in-memory engine picks up any changes made
    since the last test run.  FK enforcement is enabled via PRAGMA.
    """
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.barcode as barcode_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.notification_delivery as notif_delivery_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod

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
        notif_delivery_mod,
        media_file_mod,
        attachment_mod,
        tag_mod,
        note_mod,
        barcode_mod,
        user_token_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base as _Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
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
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def db_session() -> Generator[DBSession]:
    """Fresh in-memory SQLite session."""
    session, engine = _make_in_memory_session()
    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB patched into DATABASE_URL."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m6_step5_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m6-step5")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _reload_all_models() -> None:
    """Reload model modules to pick up fresh DB engine after monkeypatch."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.barcode as barcode_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod

    importlib.reload(db_base_mod)
    importlib.reload(hh_mod)
    importlib.reload(user_mod)
    importlib.reload(sess_mod)
    importlib.reload(app_config_mod)
    importlib.reload(cat_mod)
    importlib.reload(ikind_mod)
    importlib.reload(idef_mod)
    importlib.reload(stock_instance_mod)
    importlib.reload(stock_movement_mod)
    importlib.reload(loc_mod)
    importlib.reload(setting_mod)
    importlib.reload(notif_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)
    importlib.reload(tag_mod)
    importlib.reload(note_mod)
    importlib.reload(barcode_mod)
    importlib.reload(user_token_mod)


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, object]]:
    """Returns (unauthenticated TestClient, engine) with schema created."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# Engine-test helpers
# ---------------------------------------------------------------------------


def _seed_base(db: DBSession) -> tuple[object, object]:
    """Seed Household + ItemKind; return (household, kind).

    Must be called before creating any definitions or instances.
    """
    from app.models.household import Household
    from app.models.item_kind import ItemKind

    hh = Household(id=1, name="H", currency="USD", timezone="UTC")
    db.add(hh)
    db.flush()

    kind = ItemKind(code="durable", name="Durable", is_system=True)
    db.add(kind)
    db.flush()
    db.commit()
    return hh, kind


def _make_user(
    db: DBSession,
    email: str,
    is_active: bool = True,
    notify_in_app: bool = True,
    notify_email_digest: bool = True,
) -> object:
    """Create and return a User row."""
    from app.auth.passwords import hash_password
    from app.models.user import User

    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role="admin",
        is_active=is_active,
        notify_in_app=notify_in_app,
        notify_email_digest=notify_email_digest,
    )
    db.add(user)
    db.flush()
    db.commit()
    return user


def _make_definition(
    db: DBSession,
    kind_id: int,
    name: str = "Widget",
    responsible_user_id: int | None = None,
    min_stock: Decimal | None = None,
    stock_tracking_mode: str = "exact",
) -> object:
    """Create and return an ItemDefinition row."""
    from app.models.item_definition import ItemDefinition

    defn = ItemDefinition(
        name=name,
        kind_id=kind_id,
        responsible_user_id=responsible_user_id,
        min_stock=min_stock,
        stock_tracking_mode=stock_tracking_mode,
    )
    db.add(defn)
    db.flush()
    db.commit()
    return defn


def _make_instance(
    db: DBSession,
    definition_id: int,
    best_before_date: date | None = None,
    warranty_expires: date | None = None,
    responsible_user_id: int | None = None,
    quantity: Decimal = Decimal("1"),
) -> object:
    """Create and return a StockInstance row."""
    from app.models.stock_instance import StockInstance

    inst = StockInstance(
        definition_id=definition_id,
        best_before_date=best_before_date,
        warranty_expires=warranty_expires,
        responsible_user_id=responsible_user_id,
        quantity=quantity,
    )
    db.add(inst)
    db.flush()
    db.commit()
    return inst


def _notification_user_ids(db: DBSession, source: str) -> set[int]:
    """Return the set of user_ids for all notifications of a given source."""
    from app.models.notification import Notification

    rows = db.query(Notification).filter(Notification.source == source).all()
    return {r.user_id for r in rows}


def _notification_count(db: DBSession, source: str) -> int:
    """Return total notification count for a given source."""
    from app.models.notification import Notification

    return db.query(Notification).filter(Notification.source == source).count()


# ---------------------------------------------------------------------------
# API helpers (for base_client tests)
# ---------------------------------------------------------------------------

_SM = sessionmaker


def _make_db_from_engine(engine: object) -> DBSession:
    factory = _SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    return factory()


def _create_user_in_db(
    engine: object,
    email: str,
    role: str = "admin",
    is_active: bool = True,
    notify_in_app: bool = True,
    notify_email_digest: bool = True,
) -> int:
    db = _make_db_from_engine(engine)
    try:
        from app.auth.passwords import hash_password
        from app.models.user import User

        user = User(
            email=email,
            password_hash=hash_password("testpassword"),
            role=role,
            is_active=is_active,
            notify_in_app=notify_in_app,
            notify_email_digest=notify_email_digest,
        )
        db.add(user)
        db.flush()
        db.commit()
        return user.id
    finally:
        db.close()


def _seed_kinds_in_db(engine: object) -> None:
    db = _make_db_from_engine(engine)
    try:
        from app.models.item_kind import ItemKind

        for code, name in [
            ("durable", "Durable"),
            ("consumable", "Consumable"),
            ("perishable", "Perishable"),
        ]:
            db.add(ItemKind(code=code, name=name, is_system=True))
        db.commit()
    finally:
        db.close()


def _login(client: TestClient, email: str, password: str = "testpassword") -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.json()}"


def _setup(
    base_client: tuple[TestClient, object],
    email: str = "admin@example.com",
) -> tuple[TestClient, object, int]:
    client, engine = base_client
    _seed_kinds_in_db(engine)
    uid = _create_user_in_db(engine, email)
    _login(client, email)
    return client, engine, uid


# ---------------------------------------------------------------------------
# Tests: Date-source recipient routing (M6 §4.4)
# ---------------------------------------------------------------------------


class TestDateSourceRouting:
    """The reminder engine routes best_before/warranty reminders per-lot."""

    # Lead defaults: best_before=3 days, warranty=30 days.
    # Window = TARGET - lead.  TODAY must be >= window to fire.
    # Use TARGET=2026-02-01 (best_before window=2026-01-29; warranty window=2026-01-02)
    # and TODAY=2026-01-30, which is past both windows.
    TODAY = date(2026, 1, 30)
    TARGET = date(2026, 2, 1)

    def test_instance_assigned_to_user_a_routes_to_a_only(self, db_session: DBSession) -> None:
        """When instance.responsible_user_id = A, only A receives the reminder."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        _make_user(db_session, "b@example.com")  # second active user; must NOT receive
        defn = _make_definition(db_session, kind.id, responsible_user_id=None)
        _make_instance(
            db_session,
            defn.id,
            best_before_date=self.TARGET,
            responsible_user_id=user_a.id,
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        assert summary.best_before == 1

        recipients = _notification_user_ids(db_session, "best_before")
        assert recipients == {user_a.id}, f"Expected only A, got {recipients}"

    def test_definition_assigned_routes_to_that_user_when_instance_unassigned(
        self, db_session: DBSession
    ) -> None:
        """Instance unassigned, definition assigned to B → only B receives reminder."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(db_session, kind.id, responsible_user_id=user_b.id)
        _make_instance(
            db_session,
            defn.id,
            best_before_date=self.TARGET,
            responsible_user_id=None,  # unassigned at instance level
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "best_before")
        assert recipients == {user_b.id}, f"Expected only B, got {recipients}"
        assert user_a.id not in recipients

    def test_neither_assigned_broadcasts_to_all_active_users(self, db_session: DBSession) -> None:
        """Neither instance nor definition assigned → all active users receive (M4 parity)."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(db_session, kind.id, responsible_user_id=None)
        _make_instance(
            db_session,
            defn.id,
            best_before_date=self.TARGET,
            responsible_user_id=None,
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        assert summary.best_before == 2  # one per user

        recipients = _notification_user_ids(db_session, "best_before")
        assert recipients == {user_a.id, user_b.id}

    def test_instance_override_beats_definition_default(self, db_session: DBSession) -> None:
        """Instance assigned to A, definition to B → A only (instance wins)."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(db_session, kind.id, responsible_user_id=user_b.id)
        _make_instance(
            db_session,
            defn.id,
            best_before_date=self.TARGET,
            responsible_user_id=user_a.id,  # instance override
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "best_before")
        assert recipients == {user_a.id}, f"Expected only A (instance override), got {recipients}"

    def test_warranty_source_routes_the_same_way(self, db_session: DBSession) -> None:
        """Warranty source obeys the same instance→definition→all chain."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(db_session, kind.id, responsible_user_id=None)
        _make_instance(
            db_session,
            defn.id,
            warranty_expires=self.TARGET,
            responsible_user_id=user_a.id,
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "warranty")
        assert recipients == {user_a.id}
        assert user_b.id not in recipients


# ---------------------------------------------------------------------------
# Tests: Inactive / deleted responsible → fallback
# ---------------------------------------------------------------------------


class TestInactiveResponsibleFallback:
    """An inactive or SET NULL responsible collapses to the M4 broadcast."""

    TODAY = date(2026, 1, 30)
    TARGET = date(2026, 2, 1)

    def test_deactivated_responsible_falls_back_to_all_active(self, db_session: DBSession) -> None:
        """Deactivating the responsible user collapses to all remaining active users."""
        _hh, kind = _seed_base(db_session)
        user_active = _make_user(db_session, "active@example.com")
        # user_inactive is the "responsible" but is now deactivated
        user_inactive = _make_user(db_session, "inactive@example.com", is_active=False)
        defn = _make_definition(db_session, kind.id, responsible_user_id=user_inactive.id)
        _make_instance(db_session, defn.id, best_before_date=self.TARGET)

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "best_before")
        # user_inactive is not in active_users so fallback fires → active_users
        assert user_active.id in recipients, "active user must receive fallback broadcast"
        assert user_inactive.id not in recipients, "inactive user must not receive"

    def test_set_null_responsible_falls_back_to_all_active(self, db_session: DBSession) -> None:
        """NULL responsible_user_id (after SET NULL on user delete) → all active."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        # Definition explicitly unassigned (NULL) — simulates SET NULL after deletion
        defn = _make_definition(db_session, kind.id, responsible_user_id=None)
        _make_instance(db_session, defn.id, best_before_date=self.TARGET)

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "best_before")
        assert recipients == {user_a.id, user_b.id}, "Both users must receive when unassigned"


# ---------------------------------------------------------------------------
# Tests: Low-stock routing (run_scan)
# ---------------------------------------------------------------------------


class TestLowStockRouting:
    """Low-stock reminders are routed per definition's responsible party."""

    TODAY = date(2026, 1, 10)

    def _make_low_stock_setup(
        self,
        db: DBSession,
        responsible_user_id: int | None,
    ) -> tuple[object, object, object]:
        """Return (user_a, user_b, defn) with a low-stock situation."""
        _hh, kind = _seed_base(db)
        user_a = _make_user(db, "a@example.com")
        user_b = _make_user(db, "b@example.com")
        defn = _make_definition(
            db,
            kind.id,
            min_stock=Decimal("5"),
            stock_tracking_mode="exact",
            responsible_user_id=responsible_user_id,
        )
        # Create an instance with quantity below threshold (threshold=5, qty=2)
        _make_instance(db, defn.id, quantity=Decimal("2"))
        return user_a, user_b, defn

    def test_definition_assigned_to_c_routes_low_stock_to_c_only(
        self, db_session: DBSession
    ) -> None:
        """Low-stock opener goes to the definition's responsible user only."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(
            db_session,
            kind.id,
            min_stock=Decimal("5"),
            responsible_user_id=user_b.id,  # B is responsible
        )
        _make_instance(db_session, defn.id, quantity=Decimal("2"))

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "low_stock")
        assert recipients == {user_b.id}, f"Expected only B, got {recipients}"
        assert user_a.id not in recipients

    def test_unassigned_definition_broadcasts_low_stock_to_all(self, db_session: DBSession) -> None:
        """Unassigned definition → all active users get the low-stock notification."""
        user_a, user_b, _defn = self._make_low_stock_setup(db_session, responsible_user_id=None)

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "low_stock")
        assert recipients == {user_a.id, user_b.id}, f"Expected both, got {recipients}"

    def test_evaluate_low_stock_event_path_routes_correctly(self, db_session: DBSession) -> None:
        """evaluate_low_stock() routes the single definition the same way as run_scan."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(
            db_session,
            kind.id,
            min_stock=Decimal("5"),
            responsible_user_id=user_a.id,  # A is responsible
        )
        _make_instance(db_session, defn.id, quantity=Decimal("2"))

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        new_notifs = engine.evaluate_low_stock(defn.id, today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "low_stock")
        assert recipients == {user_a.id}, f"Event path: expected only A, got {recipients}"
        assert user_b.id not in recipients
        # The new_notifs list should contain exactly one notification
        assert len(new_notifs) == 1
        assert new_notifs[0].user_id == user_a.id

    def test_evaluate_low_stock_event_path_unassigned_broadcasts(
        self, db_session: DBSession
    ) -> None:
        """Unassigned definition via event path → all active users."""
        user_a, user_b, defn = self._make_low_stock_setup(db_session, responsible_user_id=None)

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.evaluate_low_stock(defn.id, today_local=self.TODAY)

        recipients = _notification_user_ids(db_session, "low_stock")
        assert recipients == {user_a.id, user_b.id}


# ---------------------------------------------------------------------------
# Tests: Recovery close semantics (Phase 2 uses global low_now)
# ---------------------------------------------------------------------------


class TestRecoveryCloseSemantics:
    """Phase 2 closes episodes for ALL holders of an opener, regardless of routing."""

    TODAY = date(2026, 1, 10)
    TOMORROW = date(2026, 1, 11)

    def test_broadcast_opener_closes_on_recovery_regardless_of_routing(
        self, db_session: DBSession
    ) -> None:
        """
        Scenario:
        Day 0: both users A and B receive a low-stock opener (definition unassigned → broadcast).
        Day 1: definition is now assigned to B only, but the definition has recovered (not low).
        Expected: Phase 2 closes BOTH A's and B's openers — because Phase 2 checks
        low_now_global (all globally low defs), not the routed set.
        """
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")
        user_b = _make_user(db_session, "b@example.com")
        defn = _make_definition(
            db_session,
            kind.id,
            min_stock=Decimal("5"),
            responsible_user_id=None,  # initially unassigned → broadcast
        )
        inst = _make_instance(db_session, defn.id, quantity=Decimal("2"))  # below threshold

        from app.services.reminder_engine import ReminderEngine

        # Day 0: scan fires openers for both A and B (broadcast)
        engine0 = ReminderEngine(db_session)
        engine0.run_scan(today_local=self.TODAY)

        recipients_d0 = _notification_user_ids(db_session, "low_stock")
        assert recipients_d0 == {user_a.id, user_b.id}, "Day 0: both must have openers"

        # Now assign definition to B and stock recovers (quantity above threshold)
        defn.responsible_user_id = user_b.id
        inst.quantity = Decimal("10")  # above threshold of 5
        db_session.commit()

        # Day 1: scan — definition is no longer low; Phase 2 must close both openers
        engine1 = ReminderEngine(db_session)
        engine1.run_scan(today_local=self.TOMORROW)

        # Check that both A's and B's openers are now resolved
        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        # open_low_stock_openers returns only un-resolved openers
        a_open = repo.open_low_stock_openers(user_a.id)
        b_open = repo.open_low_stock_openers(user_b.id)
        assert a_open == [], f"A's opener must be closed on recovery, but found: {a_open}"
        assert b_open == [], f"B's opener must be closed on recovery, but found: {b_open}"


# ---------------------------------------------------------------------------
# Tests: Per-user notification-pref gating
# ---------------------------------------------------------------------------


class TestPrefGating:
    """Per-user channel opt-outs gate row creation and delivery correctly."""

    TODAY = date(2026, 1, 30)
    TARGET = date(2026, 2, 1)

    def _setup_single_user_with_prefs(
        self,
        db: DBSession,
        notify_in_app: bool = True,
        notify_email_digest: bool = True,
    ) -> tuple[object, object, object]:
        """Seed base, create one user with specified prefs, return (user, defn, inst)."""
        _hh, kind = _seed_base(db)
        user = _make_user(
            db,
            "user@example.com",
            notify_in_app=notify_in_app,
            notify_email_digest=notify_email_digest,
        )
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id, best_before_date=self.TARGET)
        return user, defn, inst

    def test_both_off_no_notification_row_created(self, db_session: DBSession) -> None:
        """User with both notify prefs False gets no notification row at all."""
        user, _defn, _inst = self._setup_single_user_with_prefs(
            db_session, notify_in_app=False, notify_email_digest=False
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        assert summary.best_before == 0

        count = _notification_count(db_session, "best_before")
        assert count == 0, f"No row should be created when both prefs are False, got {count}"

    def test_notify_in_app_false_row_still_created_for_email(self, db_session: DBSession) -> None:
        """notify_in_app=False but notify_email_digest=True → row IS created (feeds email)."""
        user, _defn, _inst = self._setup_single_user_with_prefs(
            db_session, notify_in_app=False, notify_email_digest=True
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        # Row created (email digest needs it)
        assert summary.best_before == 1

        count = _notification_count(db_session, "best_before")
        assert count == 1, "Row must be created when notify_email_digest=True"

    def test_notify_email_digest_false_row_still_created_for_in_app(
        self, db_session: DBSession
    ) -> None:
        """notify_email_digest=False but notify_in_app=True → row IS created (in-app)."""
        user, _defn, _inst = self._setup_single_user_with_prefs(
            db_session, notify_in_app=True, notify_email_digest=False
        )

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        assert summary.best_before == 1

        count = _notification_count(db_session, "best_before")
        assert count == 1, "Row must be created when notify_in_app=True"

    def test_defaults_produce_m4_parity(self, db_session: DBSession) -> None:
        """Both prefs True (defaults) → same behavior as M4 broadcast."""
        _hh, kind = _seed_base(db_session)
        user_a = _make_user(db_session, "a@example.com")  # defaults: both True
        user_b = _make_user(db_session, "b@example.com")  # defaults: both True
        defn = _make_definition(db_session, kind.id)  # no assignment
        _make_instance(db_session, defn.id, best_before_date=self.TARGET)

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        assert summary.best_before == 2  # one per user

        recipients = _notification_user_ids(db_session, "best_before")
        assert recipients == {user_a.id, user_b.id}, "Both users must receive with default prefs"

    def test_low_stock_both_off_no_row(self, db_session: DBSession) -> None:
        """Both prefs False → no low-stock opener row either."""
        _hh, kind = _seed_base(db_session)
        _make_user(db_session, "user@example.com", notify_in_app=False, notify_email_digest=False)
        defn = _make_definition(db_session, kind.id, min_stock=Decimal("5"))
        _make_instance(db_session, defn.id, quantity=Decimal("2"))

        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=self.TODAY)
        assert summary.low_stock == 0
        assert _notification_count(db_session, "low_stock") == 0

    def test_email_digest_skip_no_delivery_record(self, db_session: DBSession) -> None:
        """When notify_email_digest=False, EmailChannel._deliver_to_recipient skips silently
        without creating any delivery records (and without raising)."""
        _hh, kind = _seed_base(db_session)
        user = _make_user(db_session, "user@example.com", notify_email_digest=False)
        defn = _make_definition(db_session, kind.id)
        _make_instance(db_session, defn.id, best_before_date=self.TARGET)

        # First run the engine so a notification row exists (notify_in_app=True by default,
        # so the row IS created — it just won't be emailed).
        from app.services.reminder_engine import ReminderEngine

        engine = ReminderEngine(db_session)
        engine.run_scan(today_local=self.TODAY)
        db_session.commit()

        # Confirm the notification row exists
        from app.models.notification import Notification

        notifs = db_session.query(Notification).filter(Notification.user_id == user.id).all()
        assert len(notifs) == 1, "Notification row must exist (in-app pref is True)"

        # Now call the email channel directly
        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)
        # This must not raise and must not create delivery records
        channel._deliver_to_recipient(user.id, notifs)

        from app.models.notification_delivery import NotificationDelivery

        deliveries = (
            db_session.query(NotificationDelivery)
            .filter(NotificationDelivery.notification_id.in_([n.id for n in notifs]))
            .all()
        )
        assert deliveries == [], "No delivery records expected when notify_email_digest=False"


# ---------------------------------------------------------------------------
# Tests: Prefs round-trip via PATCH /auth/me and inbox gating
# ---------------------------------------------------------------------------


class TestPrefsRoundTrip:
    """API-level: PATCH /auth/me sets prefs; inbox endpoints honour them."""

    def test_patch_me_sets_notify_in_app_false(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """PATCH /auth/me with notify_in_app=false persists and is returned."""
        client, engine, _uid = _setup(base_client)

        resp = client.patch("/api/auth/me", json={"notify_in_app": False})
        assert resp.status_code == 200, resp.json()
        data = resp.json()["user"]
        assert data["notify_in_app"] is False
        assert data["notify_email_digest"] is True  # unchanged

    def test_patch_me_sets_notify_email_digest_false(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """PATCH /auth/me with notify_email_digest=false persists and is returned."""
        client, engine, _uid = _setup(base_client)

        resp = client.patch("/api/auth/me", json={"notify_email_digest": False})
        assert resp.status_code == 200, resp.json()
        data = resp.json()["user"]
        assert data["notify_email_digest"] is False
        assert data["notify_in_app"] is True  # unchanged

    def test_patch_me_omit_fields_is_noop(self, base_client: tuple[TestClient, object]) -> None:
        """Omitting the notify fields does not change their values."""
        client, engine, _uid = _setup(base_client)

        # First set both to non-default values
        client.patch(
            "/api/auth/me",
            json={"notify_in_app": False, "notify_email_digest": False},
        )
        # Now patch only preferred_language — notify fields must not change
        resp = client.patch("/api/auth/me", json={"preferred_language": "en"})
        assert resp.status_code == 200, resp.json()
        data = resp.json()["user"]
        assert data["notify_in_app"] is False, "Must remain False after unrelated patch"
        assert data["notify_email_digest"] is False, "Must remain False after unrelated patch"

    def test_patch_me_null_notify_in_app_is_noop(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """Explicit null for non-nullable bool pref is a no-op (not an error)."""
        client, engine, _uid = _setup(base_client)

        # Set to False first
        client.patch("/api/auth/me", json={"notify_in_app": False})

        # Explicit null → should be no-op (column is NOT NULL)
        resp = client.patch("/api/auth/me", json={"notify_in_app": None})
        assert resp.status_code == 200, resp.json()
        data = resp.json()["user"]
        assert data["notify_in_app"] is False, "Null must be a no-op for non-nullable bool"

    def test_get_me_returns_notify_prefs(self, base_client: tuple[TestClient, object]) -> None:
        """GET /auth/me response includes notify_in_app and notify_email_digest."""
        client, engine, _uid = _setup(base_client)

        resp = client.get("/api/auth/me")
        assert resp.status_code == 200, resp.json()
        data = resp.json()["user"]
        assert "notify_in_app" in data
        assert "notify_email_digest" in data
        assert data["notify_in_app"] is True  # default
        assert data["notify_email_digest"] is True  # default

    def test_inbox_empty_when_notify_in_app_false(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """GET /notifications returns [] when user has notify_in_app=False."""
        client, engine, uid = _setup(base_client)

        # Directly insert a notification row for this user into the DB
        db = _make_db_from_engine(engine)
        try:
            from app.models.notification import Notification

            n = Notification(
                user_id=uid,
                source="best_before",
                subject_type="instance",
                subject_id=1,
                dedup_key="test:dedup:key",
                message_code="reminder.best_before",
            )
            db.add(n)
            db.commit()
        finally:
            db.close()

        # With default prefs, inbox should return the row
        resp = client.get("/api/notifications")
        assert resp.status_code == 200
        assert len(resp.json()) == 1, "Notification should appear in inbox with default prefs"

        # Now opt out of in-app
        client.patch("/api/auth/me", json={"notify_in_app": False})

        # Inbox must be empty
        resp = client.get("/api/notifications")
        assert resp.status_code == 200
        assert resp.json() == [], "Inbox must be empty when notify_in_app=False"

    def test_unread_count_zero_when_notify_in_app_false(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """GET /notifications/unread-count returns 0 when user has notify_in_app=False."""
        client, engine, uid = _setup(base_client)

        # Insert an unread notification
        db = _make_db_from_engine(engine)
        try:
            from app.models.notification import Notification

            n = Notification(
                user_id=uid,
                source="low_stock",
                subject_type="definition",
                subject_id=1,
                dedup_key="test:low:dedup",
                message_code="reminder.low_stock",
            )
            db.add(n)
            db.commit()
        finally:
            db.close()

        # With default prefs, unread-count is 1
        resp = client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        # Opt out of in-app
        client.patch("/api/auth/me", json={"notify_in_app": False})

        # Now unread-count must be 0
        resp = client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0, "Unread count must be 0 when notify_in_app=False"

    def test_login_response_includes_notify_prefs(
        self, base_client: tuple[TestClient, object]
    ) -> None:
        """POST /auth/login response (UserResponse) includes the two notify fields."""
        client, engine, _uid = _setup(base_client)

        resp = client.post(
            "/api/auth/login", json={"email": "admin@example.com", "password": "testpassword"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "notify_in_app" in data
        assert "notify_email_digest" in data


# ---------------------------------------------------------------------------
# Tests: Migration 0031 round-trip
# ---------------------------------------------------------------------------


def test_migration_0031_roundtrip(tmp_path: Path) -> None:
    """Migration 0031 adds notify_in_app + notify_email_digest, then removes them on downgrade.

    Uses alembic as a subprocess to avoid the local ``alembic/`` package
    directory shadowing the installed alembic (same pattern as test_m6_step4.py).

    Sequence:
    1. Upgrade to 0030 (no notify columns).
    2. Insert an existing user row.
    3. Upgrade to 0031: assert both columns appear with default 1.
    4. Downgrade to 0030: assert both columns are gone.
    """
    import subprocess

    from sqlalchemy import inspect as sa_inspect

    db_path = tmp_path / "migration_test_step5.db"
    db_url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).parent.parent

    def _alembic(*args: str) -> tuple[int, str]:
        env = {**os.environ, "SECRET_KEY": "test-migration-key-step5", "DATABASE_URL": db_url}
        result = subprocess.run(
            [str(backend_root / ".venv/bin/alembic"), *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    # Step 1: Upgrade to 0030 (before the notify-pref columns).
    rc, out = _alembic("upgrade", "0030")
    assert rc == 0, f"alembic upgrade 0030 failed:\n{out}"

    # Verify notify columns are absent at 0030.
    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    user_cols_0030 = {c["name"] for c in insp.get_columns("users")}
    assert "notify_in_app" not in user_cols_0030, "Column must not exist before 0031"
    assert "notify_email_digest" not in user_cols_0030, "Column must not exist before 0031"

    # Insert an existing user row (simulates a pre-existing user).
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (email, password_hash, role, is_active) "
                "VALUES ('existing@example.com', 'hash', 'admin', 1)"
            )
        )
        conn.commit()
    engine.dispose()

    # Step 2: Upgrade to 0031.
    rc, out = _alembic("upgrade", "0031")
    assert rc == 0, f"alembic upgrade 0031 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    user_cols_0031 = {c["name"] for c in insp.get_columns("users")}
    assert "notify_in_app" in user_cols_0031, "Column must exist after 0031 upgrade"
    assert "notify_email_digest" in user_cols_0031, "Column must exist after 0031 upgrade"

    # Verify the existing user got the true/true defaults.
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT notify_in_app, notify_email_digest "
                "FROM users WHERE email='existing@example.com'"
            )
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1, "notify_in_app must default to true for existing user"
    assert int(row[1]) == 1, "notify_email_digest must default to true for existing user"
    engine.dispose()

    # Step 3: Downgrade back to 0030.
    rc, out = _alembic("downgrade", "0030")
    assert rc == 0, f"alembic downgrade 0030 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    user_cols_after = {c["name"] for c in insp.get_columns("users")}
    assert "notify_in_app" not in user_cols_after, "Column must be gone after downgrade to 0030"
    assert "notify_email_digest" not in user_cols_after, "Column must be gone after downgrade"
    engine.dispose()
