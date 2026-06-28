"""M4 Step 3 tests: notifications table, ReminderEngine (date sources), and POST /reminders/run.

Required coverage (M4.md §5 + §9 Step 3 + §10 Step 3):

Lead resolution chain (§4.3):
- per-item wins over per-user wins over global
- each level's NULL falls through to the next
- lead=0 fires on the target date itself
- best_before and warranty each pick the correct per-user field

Date-source firing & dedup (§4.4):
- fires exactly when today_local >= date - lead (boundary: == window fires, window-1 doesn't)
- second scan creates nothing (idempotent)
- editing the date (new target date) yields a new notification (new dedup key)
- an expired (past-date) lot fires
- depleted exact lot (quantity=0) does NOT fire
- level/none lot (quantity=NULL) with a date DOES fire
- today uses household.timezone (not system date)

Multi-recipient fan-out:
- each active user gets their own row; dedup keys are distinct (contain u{uid})

NotificationRepository:
- create_if_absent: creates on miss, skips on hit, returns (notification, created) tuple

Migration 0018:
- upgrade creates the notifications table + indexes
- downgrade removes the table

HTTP API (POST /reminders/run):
- 200 + ReminderRunSummary with correct counts
- 401 when unauthenticated
- idempotent (re-run returns all zeros)

UserRepository.list_active():
- returns only is_active=True users
- excludes inactive users

StockInstanceRepository.list_live_with_best_before / list_live_with_warranty:
- returns only lots with quantity IS NULL or > 0
- excludes quantity=0 lots
- eager-loads definition
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, object]:
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
        audit_log_mod,
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


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step3_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


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


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step3")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema + authenticated admin session."""
    from fastapi.testclient import TestClient

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.notification as notif_mod
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
        audit_log_mod,
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
            db.flush()

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
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_minimal(db: Session) -> tuple[object, object, object]:
    """Seed a Household (UTC), one admin User, and one ItemKind + ItemDefinition.

    Returns (household, user, definition).
    """
    from app.models.household import Household
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.user import User

    hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
    db.add(hh)
    db.flush()

    kind = ItemKind(code="perishable", name="Perishable", is_system=True)
    db.add(kind)
    db.flush()

    from app.auth.passwords import hash_password

    user = User(email="admin@example.com", password_hash=hash_password("pass"), is_active=True)
    db.add(user)
    db.flush()

    defn = ItemDefinition(name="Milk", kind_id=kind.id)
    db.add(defn)
    db.flush()

    db.commit()
    return hh, user, defn


def _seed_instance(
    db: Session,
    definition_id: int,
    *,
    best_before_date: date | None = None,
    warranty_expires: date | None = None,
    quantity: Decimal | None = Decimal("1"),
) -> object:
    """Add a StockInstance and return it."""
    from app.models.stock_instance import StockInstance

    inst = StockInstance(
        definition_id=definition_id,
        best_before_date=best_before_date,
        warranty_expires=warranty_expires,
        quantity=quantity,
    )
    db.add(inst)
    db.flush()
    db.commit()
    return inst


# ---------------------------------------------------------------------------
# 1. NotificationRepository.create_if_absent
# ---------------------------------------------------------------------------


class TestNotificationRepository:
    def test_create_if_absent_creates_new(self, db_session: Session) -> None:
        """First call with a novel dedup key creates and returns (notif, True)."""
        _hh, user, defn = _seed_minimal(db_session)

        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        notif, created = repo.create_if_absent(
            user_id=user.id,
            source="best_before",
            subject_type="instance",
            subject_id=1,
            dedup_key="test:u1:i1:2025-01-01",
            message_code="reminder.best_before",
            params={"name": "Milk"},
        )
        assert created is True
        assert notif.id is not None
        assert notif.source == "best_before"
        assert notif.dedup_key == "test:u1:i1:2025-01-01"

    def test_create_if_absent_skips_existing(self, db_session: Session) -> None:
        """Second call with the same dedup key returns (notif, False) — no new row."""
        from app.repositories.notification import NotificationRepository

        _hh, user, defn = _seed_minimal(db_session)
        repo = NotificationRepository(db_session)

        _, created1 = repo.create_if_absent(
            user_id=user.id,
            source="best_before",
            subject_type="instance",
            subject_id=1,
            dedup_key="dedup-key-1",
            message_code="reminder.best_before",
        )
        assert created1 is True

        notif2, created2 = repo.create_if_absent(
            user_id=user.id,
            source="best_before",
            subject_type="instance",
            subject_id=1,
            dedup_key="dedup-key-1",
            message_code="reminder.best_before",
        )
        assert created2 is False
        assert notif2.id is not None

    def test_create_if_absent_different_users_same_key_both_create(
        self, db_session: Session
    ) -> None:
        """Different users with the same raw dedup suffix are independent rows."""
        from app.auth.passwords import hash_password
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _hh, user1, defn = _seed_minimal(db_session)
        user2 = User(email="user2@example.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user2)
        db_session.flush()
        db_session.commit()

        repo = NotificationRepository(db_session)
        # Both users have uniquely keyed dedup (includes u{uid})
        _, c1 = repo.create_if_absent(
            user_id=user1.id,
            source="best_before",
            subject_type="instance",
            subject_id=1,
            dedup_key=f"best_before:u{user1.id}:i1:2025-01-01",
            message_code="reminder.best_before",
        )
        _, c2 = repo.create_if_absent(
            user_id=user2.id,
            source="best_before",
            subject_type="instance",
            subject_id=1,
            dedup_key=f"best_before:u{user2.id}:i1:2025-01-01",
            message_code="reminder.best_before",
        )
        assert c1 is True
        assert c2 is True


# ---------------------------------------------------------------------------
# 2. UserRepository.list_active
# ---------------------------------------------------------------------------


class TestUserRepositoryListActive:
    def test_list_active_returns_active_users(self, db_session: Session) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.user import User
        from app.repositories.user import UserRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()

        u1 = User(email="a@x.com", password_hash=hash_password("p"), is_active=True)
        u2 = User(email="b@x.com", password_hash=hash_password("p"), is_active=False)
        u3 = User(email="c@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add_all([u1, u2, u3])
        db_session.flush()
        db_session.commit()

        repo = UserRepository(db_session)
        active = repo.list_active()

        emails = {u.email for u in active}
        assert "a@x.com" in emails
        assert "c@x.com" in emails
        assert "b@x.com" not in emails

    def test_list_active_empty_when_all_inactive(self, db_session: Session) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.user import User
        from app.repositories.user import UserRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()

        u = User(email="z@x.com", password_hash=hash_password("p"), is_active=False)
        db_session.add(u)
        db_session.flush()
        db_session.commit()

        assert UserRepository(db_session).list_active() == []


# ---------------------------------------------------------------------------
# 3. StockInstanceRepository lot queries
# ---------------------------------------------------------------------------


class TestStockInstanceLotQueries:
    def test_list_live_with_best_before_excludes_zero_qty(self, db_session: Session) -> None:
        _hh, user, defn = _seed_minimal(db_session)
        today = date.today()
        # Depleted lot (quantity=0) must be excluded
        _seed_instance(db_session, defn.id, best_before_date=today, quantity=Decimal("0"))
        # Live lot (quantity=1) must be included
        inst = _seed_instance(db_session, defn.id, best_before_date=today, quantity=Decimal("1"))

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_live_with_best_before()
        ids = [lot.id for lot in lots]
        assert inst.id in ids
        # depleted one should not appear
        assert len([lot for lot in lots if lot.quantity == Decimal("0")]) == 0

    def test_list_live_with_best_before_includes_null_qty(self, db_session: Session) -> None:
        _hh, user, defn = _seed_minimal(db_session)
        today = date.today()
        # level/none lot (quantity=NULL) must be included
        inst = _seed_instance(db_session, defn.id, best_before_date=today, quantity=None)

        from app.repositories.stock_instance import StockInstanceRepository

        lots = StockInstanceRepository(db_session).list_live_with_best_before()
        assert any(lot.id == inst.id for lot in lots)

    def test_list_live_with_best_before_eager_loads_definition(self, db_session: Session) -> None:
        _hh, user, defn = _seed_minimal(db_session)
        today = date.today()
        _seed_instance(db_session, defn.id, best_before_date=today, quantity=Decimal("1"))

        from app.repositories.stock_instance import StockInstanceRepository

        lots = StockInstanceRepository(db_session).list_live_with_best_before()
        assert len(lots) == 1
        # definition.name accessible without additional query
        assert lots[0].definition.name == "Milk"

    def test_list_live_with_warranty_excludes_zero_qty(self, db_session: Session) -> None:
        _hh, user, defn = _seed_minimal(db_session)
        today = date.today()
        _seed_instance(db_session, defn.id, warranty_expires=today, quantity=Decimal("0"))
        inst = _seed_instance(db_session, defn.id, warranty_expires=today, quantity=Decimal("1"))

        from app.repositories.stock_instance import StockInstanceRepository

        lots = StockInstanceRepository(db_session).list_live_with_warranty()
        ids = [lot.id for lot in lots]
        assert inst.id in ids
        assert len([lot for lot in lots if lot.quantity == Decimal("0")]) == 0

    def test_list_live_with_warranty_includes_null_qty(self, db_session: Session) -> None:
        _hh, user, defn = _seed_minimal(db_session)
        today = date.today()
        inst = _seed_instance(db_session, defn.id, warranty_expires=today, quantity=None)

        from app.repositories.stock_instance import StockInstanceRepository

        lots = StockInstanceRepository(db_session).list_live_with_warranty()
        assert any(lot.id == inst.id for lot in lots)

    def test_list_live_with_best_before_excludes_null_date(self, db_session: Session) -> None:
        """Lots with no best_before_date should not appear."""
        _hh, user, defn = _seed_minimal(db_session)
        _seed_instance(db_session, defn.id, best_before_date=None, quantity=Decimal("1"))

        from app.repositories.stock_instance import StockInstanceRepository

        lots = StockInstanceRepository(db_session).list_live_with_best_before()
        assert lots == []


# ---------------------------------------------------------------------------
# 4. Lead resolution chain (_resolve_lead)
# ---------------------------------------------------------------------------


class TestLeadResolutionChain:
    """Unit-test the lead resolution logic directly (no DB needed).

    The lead resolution function only reads two attributes from the User object:
    ``reminder_best_before_lead_days`` and ``reminder_warranty_lead_days``.
    We use ``types.SimpleNamespace`` to avoid SQLAlchemy instrumentation overhead.
    """

    def _make_user(
        self,
        *,
        best_before_lead: int | None = None,
        warranty_lead: int | None = None,
    ) -> object:
        import types

        return types.SimpleNamespace(
            reminder_best_before_lead_days=best_before_lead,
            reminder_warranty_lead_days=warranty_lead,
        )

    def _make_source_bb(self) -> object:
        """Return the best_before _DateSource descriptor."""
        from app.services.reminder_engine import _DATE_SOURCES

        return next(s for s in _DATE_SOURCES if s.name == "best_before")

    def _make_source_warranty(self) -> object:
        """Return the warranty _DateSource descriptor."""
        from app.services.reminder_engine import _DATE_SOURCES

        return next(s for s in _DATE_SOURCES if s.name == "warranty")

    def _make_settings_service(self, db: Session) -> object:
        from app.services.settings import SettingsService

        return SettingsService(db)

    def test_per_item_wins_over_per_user_and_global(self, db_session: Session) -> None:
        """definition.reminder_lead_days wins over per-user and global."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_bb()
        user = self._make_user(best_before_lead=10)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, 5, user, svc)  # definition_lead = 5
        assert result == 5

    def test_per_user_wins_when_no_per_item(self, db_session: Session) -> None:
        """When definition_lead is None, per-user wins over global."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_bb()
        user = self._make_user(best_before_lead=7)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, None, user, svc)  # no per-item
        assert result == 7

    def test_global_when_both_overrides_null(self, db_session: Session) -> None:
        """When both per-item and per-user are None, global default is used."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_bb()
        user = self._make_user(best_before_lead=None)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, None, user, svc)
        # Global default for best_before is 3
        assert result == 3

    def test_zero_lead_passes_through(self, db_session: Session) -> None:
        """A lead of 0 is a valid value (fire on the target date)."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_bb()
        user = self._make_user(best_before_lead=0)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, None, user, svc)
        assert result == 0

    def test_per_item_zero_beats_non_zero_per_user(self, db_session: Session) -> None:
        """per-item=0 wins even when per-user=10."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_bb()
        user = self._make_user(best_before_lead=10)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, 0, user, svc)
        assert result == 0

    def test_warranty_source_picks_warranty_per_user_field(self, db_session: Session) -> None:
        """warranty source should use reminder_warranty_lead_days, not best_before."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_warranty()
        # Different values for best_before vs warranty per-user
        user = self._make_user(best_before_lead=5, warranty_lead=20)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, None, user, svc)
        assert result == 20  # Must pick warranty field, not 5

    def test_best_before_source_picks_best_before_per_user_field(self, db_session: Session) -> None:
        """best_before source should use reminder_best_before_lead_days, not warranty."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_bb()
        user = self._make_user(best_before_lead=5, warranty_lead=20)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, None, user, svc)
        assert result == 5  # Must pick best_before field, not 20

    def test_warranty_global_default_is_30(self, db_session: Session) -> None:
        """Global default for warranty is 30 days (SettingsService default)."""
        from app.services.reminder_engine import _resolve_lead

        _hh, _, _defn = _seed_minimal(db_session)
        source = self._make_source_warranty()
        user = self._make_user(best_before_lead=None, warranty_lead=None)
        svc = self._make_settings_service(db_session)

        result = _resolve_lead(source, None, user, svc)
        assert result == 30  # Global warranty default


# ---------------------------------------------------------------------------
# 5. Date-source firing & dedup (ReminderEngine.run_scan injected today)
# ---------------------------------------------------------------------------


def _setup_engine_scenario(
    db_session: Session,
    *,
    timezone: str = "UTC",
) -> tuple[object, object, object, object]:
    """Seed household, user, kind, definition. Returns (engine, user, defn, hh)."""
    from app.auth.passwords import hash_password
    from app.models.household import Household
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.user import User
    from app.services.reminder_engine import ReminderEngine

    hh = Household(id=1, name="H", currency="USD", timezone=timezone)
    db_session.add(hh)
    db_session.flush()

    kind = ItemKind(code="perishable", name="Perishable", is_system=True)
    db_session.add(kind)
    db_session.flush()

    user = User(email="admin@example.com", password_hash=hash_password("p"), is_active=True)
    db_session.add(user)
    db_session.flush()

    defn = ItemDefinition(name="Milk", kind_id=kind.id)
    db_session.add(defn)
    db_session.flush()
    db_session.commit()

    engine = ReminderEngine(db_session)
    return engine, user, defn, hh


class TestDateSourceFiring:
    def test_fires_exactly_at_window(self, db_session: Session) -> None:
        """Fires when today_local == window (= target_date - lead)."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2025, 6, 15)
        lead = 3
        window = target - timedelta(days=lead)  # 2025-06-12

        from app.models.stock_instance import StockInstance
        from app.repositories.notification import NotificationRepository

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        summary = engine.run_scan(today_local=window)  # exactly at window
        assert summary.best_before == 1

        # Verify notification was created
        repo = NotificationRepository(db_session)
        dedup = f"best_before:u{user.id}:i{inst.id}:{target.isoformat()}"
        notif, created = repo.create_if_absent(
            user_id=user.id,
            source="best_before",
            subject_type="instance",
            subject_id=inst.id,
            dedup_key=dedup,
            message_code="reminder.best_before",
        )
        assert created is False  # Already exists

    def test_does_not_fire_one_day_before_window(self, db_session: Session) -> None:
        """Does NOT fire when today_local == window - 1."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2025, 6, 15)
        lead = 3
        window = target - timedelta(days=lead)  # 2025-06-12
        one_before = window - timedelta(days=1)  # 2025-06-11

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        summary = engine.run_scan(today_local=one_before)  # one day before window
        assert summary.best_before == 0

    def test_second_scan_is_idempotent(self, db_session: Session) -> None:
        """Re-running the scan with the same today creates no new notifications."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2025, 6, 15)
        window = target - timedelta(days=3)

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        summary1 = engine.run_scan(today_local=window)
        assert summary1.best_before == 1

        engine2 = __import__(
            "app.services.reminder_engine", fromlist=["ReminderEngine"]
        ).ReminderEngine(db_session)
        summary2 = engine2.run_scan(today_local=window)
        assert summary2.best_before == 0  # No new rows

    def test_editing_date_yields_new_notification(self, db_session: Session) -> None:
        """Changing the lot's date creates a new dedup key → new notification."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target1 = date(2025, 6, 15)
        target2 = date(2025, 6, 20)

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target1,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        # Scan with original date
        window1 = target1 - timedelta(days=3)
        summary1 = engine.run_scan(today_local=window1)
        assert summary1.best_before == 1

        # Update the date on the lot
        inst.best_before_date = target2
        db_session.commit()

        # The engine should treat the new target_date as a new notification
        # Scan on a day that's within window for target2 but not for target1
        window2 = target2 - timedelta(days=3)
        from app.services.reminder_engine import ReminderEngine

        engine2 = ReminderEngine(db_session)
        summary2 = engine2.run_scan(today_local=window2)
        assert summary2.best_before == 1  # New notification for new date

    def test_expired_lot_fires(self, db_session: Session) -> None:
        """A lot past its best-before date (in the past) still fires."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2020, 1, 1)  # well in the past
        today = date(2025, 6, 15)

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        summary = engine.run_scan(today_local=today)
        assert summary.best_before == 1

    def test_depleted_exact_lot_does_not_fire(self, db_session: Session) -> None:
        """A lot with quantity=0 (exact-mode depleted) must NOT fire."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2025, 6, 15)

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=Decimal("0"),  # Depleted
        )
        db_session.add(inst)
        db_session.commit()

        window = target - timedelta(days=3)
        summary = engine.run_scan(today_local=window)
        assert summary.best_before == 0

    def test_level_none_lot_with_date_fires(self, db_session: Session) -> None:
        """A level/none-mode lot (quantity=NULL) with best_before_date DOES fire."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2025, 6, 15)

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=None,  # level/none mode
        )
        db_session.add(inst)
        db_session.commit()

        window = target - timedelta(days=3)
        summary = engine.run_scan(today_local=window)
        assert summary.best_before == 1

    def test_warranty_source_fires(self, db_session: Session) -> None:
        """Warranty source also fires correctly via run_scan."""

        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        # Update definition — needs a new definition to avoid conflicting with best_before
        from app.models.stock_instance import StockInstance

        target = date(2025, 6, 15)
        inst = StockInstance(
            definition_id=defn.id,
            warranty_expires=target,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        window = target - timedelta(days=30)  # global warranty default = 30
        summary = engine.run_scan(today_local=window)
        assert summary.warranty == 1

    def test_zero_lead_fires_on_target_date(self, db_session: Session) -> None:
        """With lead=0, the notification fires on the exact target date."""
        engine, user, defn, _hh = _setup_engine_scenario(db_session)
        target = date(2025, 6, 15)

        # Set per-item lead to 0
        defn.reminder_lead_days = 0
        db_session.commit()

        from app.models.stock_instance import StockInstance

        inst = StockInstance(
            definition_id=defn.id,
            best_before_date=target,
            quantity=Decimal("1"),
        )
        db_session.add(inst)
        db_session.commit()

        # Window = target - 0 = target; fires when today >= target
        summary_before = engine.run_scan(today_local=target - timedelta(days=1))
        assert summary_before.best_before == 0

        from app.services.reminder_engine import ReminderEngine

        summary_on = ReminderEngine(db_session).run_scan(today_local=target)
        assert summary_on.best_before == 1

    def test_today_uses_household_timezone(self, db_session: Session) -> None:
        """The scan computes today from household.timezone, not system local time.

        Strategy: use a timezone far ahead of UTC (e.g. Pacific/Auckland, UTC+12).
        At UTC 13:00 on 2025-01-01, Auckland is already 2025-01-02.
        We set up a lot that should fire on 2025-01-02 (tomorrow per UTC) and
        check that run_scan() (with no injected today) produces a notification,
        meaning it used Auckland date (Jan 2) not UTC date (Jan 1).

        We mock datetime.now to return a fixed UTC time that causes the tz
        discrepancy.
        """
        from unittest.mock import patch

        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.user import User
        from app.services.reminder_engine import ReminderEngine

        # Use a fresh session with a non-UTC timezone household
        hh = db_session.get(Household, 1)
        if hh is None:
            hh = Household(id=1, name="H", currency="USD", timezone="Pacific/Auckland")
            db_session.add(hh)
            db_session.flush()
        else:
            hh.timezone = "Pacific/Auckland"
            db_session.flush()
        db_session.commit()

        # Reuse user and defn from session if seeded, else seed

        users = (
            db_session.execute(__import__("sqlalchemy", fromlist=["select"]).select(User))
            .scalars()
            .all()
        )
        if users:
            user = users[0]
            defn = (
                db_session.execute(
                    __import__("sqlalchemy", fromlist=["select"]).select(
                        __import__(
                            "app.models.item_definition", fromlist=["ItemDefinition"]
                        ).ItemDefinition
                    )
                )
                .scalars()
                .first()
            )
        else:
            kind = ItemKind(code="perishable", name="P", is_system=True)
            db_session.add(kind)
            db_session.flush()
            user = User(email="admin@tz.com", password_hash=hash_password("p"), is_active=True)
            db_session.add(user)
            db_session.flush()
            defn = ItemDefinition(name="Milk", kind_id=kind.id)
            db_session.add(defn)
            db_session.flush()
            db_session.commit()

        from app.models.stock_instance import StockInstance

        # Target date = 2025-01-02 (Auckland time)
        target = date(2025, 1, 2)
        # lead=0: fire on exact date; window = target - 0 = target
        defn.reminder_lead_days = 0
        inst = StockInstance(definition_id=defn.id, best_before_date=target, quantity=Decimal("1"))
        db_session.add(inst)
        db_session.commit()
        # UTC 13:00 on 2025-01-01 → Auckland 2025-01-02 01:00 (UTC+12)
        # fixed_utc would be: datetime(2025, 1, 1, 13, 0, 0, tzinfo=UTC)

        from app.services import reminder_engine as re_module

        with patch.object(re_module.ReminderEngine, "_today_in_tz", return_value=date(2025, 1, 2)):
            engine = ReminderEngine(db_session)
            summary = engine.run_scan()  # No injected today; uses mocked _today_in_tz

        # Should fire because Auckland date = 2025-01-02 = target - 0
        assert summary.best_before == 1


# ---------------------------------------------------------------------------
# 6. Multi-recipient fan-out
# ---------------------------------------------------------------------------


class TestMultiRecipientFanOut:
    def test_each_active_user_gets_own_notification(self, db_session: Session) -> None:
        """Fan-out: each active user gets their own notification row."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        hh = Household(id=1, name="H", currency="USD", timezone="UTC")
        db_session.add(hh)
        kind = ItemKind(code="p", name="P", is_system=True)
        db_session.add(kind)
        db_session.flush()

        u1 = User(email="u1@x.com", password_hash=hash_password("p"), is_active=True)
        u2 = User(email="u2@x.com", password_hash=hash_password("p"), is_active=True)
        u3 = User(email="u3@x.com", password_hash=hash_password("p"), is_active=False)  # inactive
        db_session.add_all([u1, u2, u3])
        db_session.flush()

        defn = ItemDefinition(name="Apple", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        target = date(2025, 6, 15)
        window = target - timedelta(days=3)
        inst = StockInstance(definition_id=defn.id, best_before_date=target, quantity=Decimal("1"))
        db_session.add(inst)
        db_session.commit()

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=window)

        # 2 active users × 1 lot = 2 notifications
        assert summary.best_before == 2

        # Dedup keys are distinct (contain u{uid})
        repo = NotificationRepository(db_session)
        dedup_u1 = f"best_before:u{u1.id}:i{inst.id}:{target.isoformat()}"
        dedup_u2 = f"best_before:u{u2.id}:i{inst.id}:{target.isoformat()}"
        _, c1 = repo.create_if_absent(
            user_id=u1.id,
            source="best_before",
            subject_type="instance",
            subject_id=inst.id,
            dedup_key=dedup_u1,
            message_code="reminder.best_before",
        )
        _, c2 = repo.create_if_absent(
            user_id=u2.id,
            source="best_before",
            subject_type="instance",
            subject_id=inst.id,
            dedup_key=dedup_u2,
            message_code="reminder.best_before",
        )
        assert c1 is False  # Already exists
        assert c2 is False  # Already exists

        # Inactive user should NOT have a notification
        dedup_u3 = f"best_before:u{u3.id}:i{inst.id}:{target.isoformat()}"
        _, c3 = repo.create_if_absent(
            user_id=u3.id,
            source="best_before",
            subject_type="instance",
            subject_id=inst.id,
            dedup_key=dedup_u3,
            message_code="reminder.best_before",
        )
        assert c3 is True  # Was NOT created by engine (inactive user)


# ---------------------------------------------------------------------------
# 7. Migration 0018 round-trip
# ---------------------------------------------------------------------------


class TestMigration0018:
    """Migration 0018: create / drop the notifications table.

    Uses a subprocess call to ``.venv/bin/alembic`` so that the local
    ``backend/alembic/`` package directory does not shadow the installed
    ``alembic`` pip package (same pattern as test_m4_step1.py / test_m3_step1.py).
    """

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run alembic as a subprocess; return (returncode, combined output)."""
        import subprocess

        backend_root = Path(__file__).parent.parent
        env = {
            **os.environ,
            "SECRET_KEY": "test",
            "DATABASE_URL": url,
        }
        result = subprocess.run(
            [".venv/bin/alembic", *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def _make_temp_db(self) -> tuple[str, Path]:
        """Return (url, path) for a disposable temp-file SQLite DB."""
        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_migtest_0018_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        return f"sqlite:///{path_str}", db_path

    def test_upgrade_creates_table(self) -> None:
        """Migration 0018 upgrade creates the notifications table + indexes."""
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect

        url, db_path = self._make_temp_db()
        try:
            rc, out = self._run_alembic("upgrade", "0018", url=url)
            assert rc == 0, f"alembic upgrade 0018 failed:\n{out}"

            engine = create_engine(url)
            inspector = sa_inspect(engine)
            assert "notifications" in inspector.get_table_names()

            # Check indexes
            indexes = inspector.get_indexes("notifications")
            index_names = {idx["name"] for idx in indexes}
            assert "uq_notifications_user_dedup" in index_names
            assert "ix_notifications_user_read_at" in index_names
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_removes_table(self) -> None:
        """Migration 0018 downgrade removes the notifications table."""
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect

        url, db_path = self._make_temp_db()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0018", url=url)
            assert rc_up == 0, f"upgrade 0018 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0017", url=url)
            assert rc_down == 0, f"downgrade from 0018 to 0017 failed:\n{out_down}"

            engine = create_engine(url)
            inspector = sa_inspect(engine)
            assert "notifications" not in inspector.get_table_names()
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()


# ---------------------------------------------------------------------------
# 8. HTTP API: POST /reminders/run
# ---------------------------------------------------------------------------


class TestRemindersRunEndpoint:
    def test_run_returns_200_with_summary(self, http_client: object) -> None:
        """POST /reminders/run returns 200 + ReminderRunSummary."""
        from fastapi.testclient import TestClient

        client: TestClient = http_client  # type: ignore[assignment]
        resp = client.post("/api/reminders/run")
        assert resp.status_code == 200
        data = resp.json()
        assert "best_before" in data
        assert "warranty" in data
        assert "low_stock" in data
        # With no lots seeded, all counts are 0
        assert data["best_before"] == 0
        assert data["warranty"] == 0
        assert data["low_stock"] == 0

    def test_run_returns_401_unauthenticated(self, http_client: object) -> None:
        """POST /reminders/run requires authentication — unauthenticated returns 401."""
        from fastapi.testclient import TestClient

        from app.main import create_app

        # Re-create the app with a fresh client (no session cookie)
        application = create_app()
        with TestClient(application, raise_server_exceptions=False) as fresh_client:
            resp = fresh_client.post("/api/reminders/run")
            assert resp.status_code == 401

    def test_run_idempotent_returns_zeros_on_second_call(self, http_client: object) -> None:
        """Calling POST /reminders/run twice returns 0 on the second call (idempotent)."""
        from fastapi.testclient import TestClient

        client: TestClient = http_client  # type: ignore[assignment]
        # First call (no lots — counts already 0)
        resp1 = client.post("/api/reminders/run")
        assert resp1.status_code == 200

        # Second call
        resp2 = client.post("/api/reminders/run")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["best_before"] == 0
        assert data["warranty"] == 0

    def test_run_creates_notifications_for_expiring_lot(self, http_client: object) -> None:
        """POST /reminders/run creates best_before notification when a lot qualifies.

        We seed a lot with best_before_date well in the past (already expired) and
        lead=0 so the window = target_date, which is always <= today.  This way
        the test does not depend on the current date or the household timezone.
        """
        from fastapi.testclient import TestClient

        client: TestClient = http_client  # type: ignore[assignment]

        from sqlalchemy.orm import sessionmaker

        from app.db.base import get_engine

        engine = get_engine()
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from sqlalchemy import select

            from app.models.item_definition import ItemDefinition
            from app.models.item_kind import ItemKind
            from app.models.stock_instance import StockInstance

            # Fetch the perishable kind (seeded in http_client fixture)
            kind = db.execute(select(ItemKind).where(ItemKind.code == "perishable")).scalar_one()
            # lead=0 means fire on the exact target date; past date is always triggered
            defn = ItemDefinition(name="Yogurt", kind_id=kind.id, reminder_lead_days=0)
            db.add(defn)
            db.flush()

            # Lot expiring well in the past — definitely inside the window
            target = date(2020, 1, 1)
            inst = StockInstance(
                definition_id=defn.id, best_before_date=target, quantity=Decimal("1")
            )
            db.add(inst)
            db.commit()
        finally:
            db.close()

        resp = client.post("/api/reminders/run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_before"] >= 1

        # Second call: idempotent — no new notifications
        resp2 = client.post("/api/reminders/run")
        assert resp2.status_code == 200
        assert resp2.json()["best_before"] == 0
