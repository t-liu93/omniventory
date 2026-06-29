"""Tests for M7 Step 5: maintenance-due reminder source (additive engine pass).

Coverage (M7.md §5 / §9 Step 5 / §10 Step 5)
----------------------------------------------

Server message catalog:
- render_line("reminder.maintenance", ...) EN: future / today / overdue variants.
- render_line("reminder.maintenance", ...) ZH: future / today / overdue variants.

Engine firing & dedup:
- Fires at today == window (== next_due_date - lead): boundary-ON.
- Does NOT fire at today == window - 1: boundary-OFF.
- Overdue schedule (today > next_due_date) fires with negative days_remaining.
- Per-schedule lead_days overrides global default.
- lead_days = 0 fires on the due date itself.
- Long-lead schedule (large lead_days, next_due_date far in the future) whose
  window is already open fires — proves no DB horizon drops it (B-class test).
- Paused (is_active=False) schedule never fires.
- Re-running the scan creates nothing new (dedup).

Routing & pref gate:
- Instance assigned to a member → only that member receives the notification.
- Instance + definition both unassigned → all active users receive it.
- User with both notify_in_app=False and notify_email_digest=False is skipped.

Regression (existing sources untouched):
- A scan with best_before + warranty + low_stock + maintenance all present
  returns unchanged counts for the three existing sources; maintenance is
  purely additive.

HTTP / API:
- ReminderRunSummary.maintenance is returned by POST /reminders/run.
- maintenance_lead_days default = 7 in GET /settings.
- maintenance_lead_days round-trips via PATCH /settings (ge=0 validation).

Settings schema:
- PATCH /settings with negative maintenance_lead_days → 422.
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# In-memory session factory (engine-level / unit tests)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[DBSession, object]:
    """Create a fresh in-memory SQLite session with ALL models registered.

    Includes maintenance_schedule and shopping_list_item (M7 tables) plus the
    maintenance_schedule repository, which must be reloaded AFTER model modules
    to avoid SQLAlchemy class-identity mismatches when tests run in sequence.
    """
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.audit_log as audit_log_mod
    import app.models.barcode as barcode_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.maintenance_schedule as ms_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.notification_delivery as notif_delivery_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.shopping_list_item as sli_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod
    import app.repositories.maintenance_schedule as ms_repo_mod

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
        audit_log_mod,
        sli_mod,
        ms_mod,
    ):
        importlib.reload(mod)

    # Reload repository AFTER models so its class references are fresh.
    importlib.reload(ms_repo_mod)

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
def db() -> Generator[DBSession]:
    """Fresh in-memory SQLite session with all models (engine-level tests)."""
    session, engine = _make_in_memory_session()
    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB patched into DATABASE_URL (HTTP-level tests)."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m7_step5_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m7-step5")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _reload_all_models_for_http() -> None:
    """Reload model modules for HTTP-level tests (same list as M7 Step 4)."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.audit_log as audit_log_mod
    import app.models.barcode as barcode_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.maintenance_schedule as ms_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.shopping_list_item as sli_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod
    import app.repositories.maintenance_schedule as ms_repo_mod

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
    importlib.reload(audit_log_mod)
    importlib.reload(sli_mod)
    importlib.reload(ms_mod)
    importlib.reload(ms_repo_mod)


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, object]]:
    """TestClient + engine with schema created, no users."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models_for_http()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_item_kinds(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


def _seed_item_kinds(engine: object) -> None:
    from sqlalchemy.orm import sessionmaker as SM

    from app.models.item_kind import ItemKind

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        for code, name in [
            ("durable", "Durable"),
            ("consumable", "Consumable"),
            ("perishable", "Perishable"),
        ]:
            db.add(ItemKind(code=code, name=name, is_system=True))
        db.commit()
    finally:
        db.close()


def _create_user_and_login(
    engine: object, client: TestClient, email: str, password: str, role: str = "admin"
) -> None:
    from sqlalchemy.orm import sessionmaker as SM

    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        UserRepository(db).create(email=email, password_hash=hash_password(password), role=role)
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.json()}"


@pytest.fixture()
def admin_client(base_client: tuple[TestClient, object]) -> TestClient:
    client, engine = base_client
    _create_user_and_login(engine, client, "admin@test.com", "adminpass", "admin")
    return client


# ---------------------------------------------------------------------------
# Engine-test helpers
# ---------------------------------------------------------------------------


def _seed_base(session: DBSession) -> tuple[object, object]:
    """Seed Household (UTC) + ItemKind(durable); return (household, kind)."""
    from app.models.household import Household
    from app.models.item_kind import ItemKind

    hh = Household(id=1, name="Test Home", currency="USD", timezone="UTC")
    session.add(hh)
    session.flush()
    kind = ItemKind(code="durable", name="Durable", is_system=True)
    session.add(kind)
    session.flush()
    kind_c = ItemKind(code="consumable", name="Consumable", is_system=True)
    session.add(kind_c)
    session.flush()
    kind_p = ItemKind(code="perishable", name="Perishable", is_system=True)
    session.add(kind_p)
    session.flush()
    session.commit()
    return hh, kind


def _make_user(
    session: DBSession,
    email: str,
    *,
    is_active: bool = True,
    notify_in_app: bool = True,
    notify_email_digest: bool = True,
) -> object:
    from app.auth.passwords import hash_password
    from app.models.user import User

    u = User(
        email=email,
        password_hash=hash_password("pw"),
        role="admin",
        is_active=is_active,
        notify_in_app=notify_in_app,
        notify_email_digest=notify_email_digest,
    )
    session.add(u)
    session.flush()
    session.commit()
    return u


def _make_definition(
    session: DBSession,
    kind_id: int,
    name: str = "Durable Item",
    responsible_user_id: int | None = None,
) -> object:
    from app.models.item_definition import ItemDefinition

    d = ItemDefinition(
        name=name,
        kind_id=kind_id,
        responsible_user_id=responsible_user_id,
        stock_tracking_mode="exact",
        unit="unit",
    )
    session.add(d)
    session.flush()
    session.commit()
    return d


def _make_instance(
    session: DBSession,
    definition_id: int,
    responsible_user_id: int | None = None,
    best_before_date: date | None = None,
    warranty_expires: date | None = None,
    quantity: Decimal = Decimal("1"),
) -> object:
    from app.models.stock_instance import StockInstance

    inst = StockInstance(
        definition_id=definition_id,
        responsible_user_id=responsible_user_id,
        best_before_date=best_before_date,
        warranty_expires=warranty_expires,
        quantity=quantity,
    )
    session.add(inst)
    session.flush()
    session.commit()
    return inst


def _make_schedule(
    session: DBSession,
    instance_id: int,
    name: str = "Change filter",
    next_due_date: date | None = None,
    lead_days: int | None = None,
    is_active: bool = True,
    interval_unit: str = "month",
    interval_count: int = 3,
) -> object:
    from app.models.maintenance_schedule import MaintenanceSchedule

    if next_due_date is None:
        next_due_date = date.today() + timedelta(days=30)

    s = MaintenanceSchedule(
        instance_id=instance_id,
        name=name,
        interval_unit=interval_unit,
        interval_count=interval_count,
        next_due_date=next_due_date,
        lead_days=lead_days,
        is_active=is_active,
    )
    session.add(s)
    session.flush()
    session.commit()
    return s


def _maintenance_count(session: DBSession) -> int:
    from app.models.notification import Notification

    return session.query(Notification).filter(Notification.source == "maintenance").count()


def _notification_count_by_source(session: DBSession, source: str) -> int:
    from app.models.notification import Notification

    return session.query(Notification).filter(Notification.source == source).count()


def _maintenance_notifs(session: DBSession) -> list[object]:
    from app.models.notification import Notification

    return session.query(Notification).filter(Notification.source == "maintenance").all()


# ---------------------------------------------------------------------------
# 1. Server message catalog — render_line
# ---------------------------------------------------------------------------


class TestRenderMaintenance:
    """Catalog rendering for reminder.maintenance (M7 §4.5)."""

    def _render(self, days_remaining: int, lang: str) -> str:
        from app.notifications.messages import render_line

        params = {
            "name": "Replace filter",
            "instance_name": "Air Conditioner",
            "next_due_date": "2026-07-15",
            "days_remaining": days_remaining,
        }
        return render_line("reminder.maintenance", params, lang)

    # English

    def test_en_future(self) -> None:
        result = self._render(5, "en")
        assert "Air Conditioner" in result
        assert "Replace filter" in result
        assert "5 day(s)" in result
        assert "overdue" not in result.lower()

    def test_en_today(self) -> None:
        result = self._render(0, "en")
        assert "due today" in result
        assert "Air Conditioner" in result
        assert "Replace filter" in result

    def test_en_overdue(self) -> None:
        result = self._render(-3, "en")
        assert "overdue" in result.lower()
        assert "3 day(s)" in result
        assert "Air Conditioner" in result

    # Chinese

    def test_zh_future(self) -> None:
        result = self._render(10, "zh")
        assert "维护提醒" in result
        assert "Air Conditioner" in result
        assert "Replace filter" in result
        assert "10 天" in result

    def test_zh_today(self) -> None:
        result = self._render(0, "zh")
        assert "今天到期" in result

    def test_zh_overdue(self) -> None:
        result = self._render(-7, "zh")
        assert "逾期" in result
        assert "7" in result

    def test_unknown_code_fallback(self) -> None:
        """Unknown codes still return a non-crashing line."""
        from app.notifications.messages import render_line

        result = render_line("reminder.nonexistent", {}, "en")
        assert "reminder.nonexistent" in result


# ---------------------------------------------------------------------------
# 2. Engine: firing, dedup, overdue, lead resolution
# ---------------------------------------------------------------------------


class TestMaintenanceFiring:
    """Engine fires/skips correctly based on the due-window."""

    def test_boundary_on_window_fires(self, db: DBSession) -> None:
        """today == window (== next_due_date - lead) → fires."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        lead = 7
        next_due = today + timedelta(days=lead)  # window == today exactly

        _make_schedule(db, inst.id, next_due_date=next_due, lead_days=lead)

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 1
        assert _maintenance_count(db) == 1

    def test_boundary_before_window_does_not_fire(self, db: DBSession) -> None:
        """today == window - 1 → does NOT fire."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 9)
        lead = 7
        next_due = today + timedelta(days=lead + 1)  # window = next_due - lead = today + 1

        _make_schedule(db, inst.id, next_due_date=next_due, lead_days=lead)

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 0
        assert _maintenance_count(db) == 0

    def test_overdue_fires_with_negative_days(self, db: DBSession) -> None:
        """today > next_due_date → fires with negative days_remaining."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 20)
        next_due = date(2026, 7, 15)  # 5 days in the past

        _make_schedule(db, inst.id, next_due_date=next_due, lead_days=3)

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 1

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        params = json.loads(notifs[0].params)
        assert params["days_remaining"] == -5  # (next_due - today).days = -5
        assert params["instance_id"] == inst.id  # type: ignore[attr-defined]

    def test_lead_zero_fires_on_due_date(self, db: DBSession) -> None:
        """lead_days=0 fires exactly on the due date."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 15)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 1

    def test_per_schedule_lead_overrides_global(self, db: DBSession) -> None:
        """Per-schedule lead_days overrides global reminders.maintenance.lead_days."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        # Global default is 7.  Use per-schedule lead=14.
        today = date(2026, 7, 10)
        per_schedule_lead = 14
        next_due = today + timedelta(days=per_schedule_lead)  # window == today
        _make_schedule(db, inst.id, next_due_date=next_due, lead_days=per_schedule_lead)

        # With per-schedule lead=14, window opens 14 days before due → fires.
        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 1

        # But with global lead=7, today would be too early (due is 14 days away).
        # Verify: no notification for a different schedule with only global lead.
        defn2 = _make_definition(db, kind.id, name="Widget2")
        inst2 = _make_instance(db, defn2.id)
        _make_schedule(db, inst2.id, next_due_date=next_due, lead_days=None)  # inherits global=7

        db.query(
            __import__("app.models.notification", fromlist=["Notification"]).Notification
        ).filter(
            __import__("app.models.notification", fromlist=["Notification"]).Notification.source
            == "maintenance"
        ).delete()
        db.commit()

        summary2 = ReminderEngine(db).run_scan(today_local=today)
        # Only schedule 1 fires (per-schedule lead=14 → window open).
        # Schedule 2 has global lead=7, next_due is 14 days away → window opens in 7 days, not yet.
        assert summary2.maintenance == 1

    def test_paused_never_fires(self, db: DBSession) -> None:
        """is_active=False → engine skips it."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0, is_active=False)

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 0
        assert _maintenance_count(db) == 0

    def test_dedup_on_rescan(self, db: DBSession) -> None:
        """Re-running the scan creates nothing new."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)

        from app.services.reminder_engine import ReminderEngine

        summary1 = ReminderEngine(db).run_scan(today_local=today)
        assert summary1.maintenance == 1

        summary2 = ReminderEngine(db).run_scan(today_local=today)
        assert summary2.maintenance == 0  # already exists → no new row
        assert _maintenance_count(db) == 1  # still exactly one row

    def test_long_lead_schedule_fires(self, db: DBSession) -> None:
        """Long-lead schedule whose window is already open fires.

        This is the B-class test: a schedule with lead_days=365 and next_due_date
        200 days from today has a window of 165 days AGO, which is already open.
        A fixed DB-side horizon would have silently dropped it.
        """
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        per_schedule_lead = 365  # huge lead
        next_due = today + timedelta(days=200)  # far in the future
        # window = next_due - lead = today + 200 - 365 = today - 165  (165 days ago)
        # → window is already open

        _make_schedule(db, inst.id, next_due_date=next_due, lead_days=per_schedule_lead)

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 1

        notifs = _maintenance_notifs(db)
        params = json.loads(notifs[0].params)
        # days_remaining = (next_due - today).days = 200 (positive, window was opened long ago)
        assert params["days_remaining"] == 200
        assert params["instance_id"] == inst.id  # type: ignore[attr-defined]

    def test_params_include_instance_id(self, db: DBSession) -> None:
        """Maintenance notification params carry instance_id for frontend deep-linking."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.maintenance == 1

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        params = json.loads(notifs[0].params)
        # instance_id must equal the owning stock instance's id so the frontend
        # can link maintenance notifications to /instances/{instance_id}.
        assert "instance_id" in params
        assert params["instance_id"] == inst.id  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 3. Engine: routing and pref gate
# ---------------------------------------------------------------------------


class TestMaintenanceRouting:
    """Routing via _effective_responsible_for_lot + _recipients_for + pref gate."""

    def test_instance_assigned_routes_to_member_only(self, db: DBSession) -> None:
        """Instance responsible_user_id = A → only A receives notification."""
        _, kind = _seed_base(db)
        user_a = _make_user(db, "a@test.com")
        user_b = _make_user(db, "b@test.com")

        defn = _make_definition(db, kind.id, responsible_user_id=None)
        inst = _make_instance(db, defn.id, responsible_user_id=user_a.id)  # type: ignore[attr-defined]

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        assert notifs[0].user_id == user_a.id  # type: ignore[attr-defined]
        assert all(n.user_id != user_b.id for n in notifs)  # type: ignore[attr-defined]

    def test_definition_assigned_routes_to_member(self, db: DBSession) -> None:
        """Instance unassigned, definition.responsible_user_id=B → only B."""
        _, kind = _seed_base(db)
        user_a = _make_user(db, "a@test.com")
        user_b = _make_user(db, "b@test.com")

        defn = _make_definition(db, kind.id, responsible_user_id=user_b.id)  # type: ignore[attr-defined]
        inst = _make_instance(db, defn.id, responsible_user_id=None)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        user_ids = {n.user_id for n in notifs}
        assert user_b.id in user_ids  # type: ignore[attr-defined]
        assert user_a.id not in user_ids  # type: ignore[attr-defined]

    def test_unassigned_reaches_all_users(self, db: DBSession) -> None:
        """Both instance and definition unassigned → all active users receive it."""
        _, kind = _seed_base(db)
        user_a = _make_user(db, "a@test.com")
        user_b = _make_user(db, "b@test.com")

        defn = _make_definition(db, kind.id, responsible_user_id=None)
        inst = _make_instance(db, defn.id, responsible_user_id=None)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        user_ids = {n.user_id for n in notifs}
        assert user_a.id in user_ids  # type: ignore[attr-defined]
        assert user_b.id in user_ids  # type: ignore[attr-defined]
        assert len(notifs) == 2

    def test_pref_gate_both_off_skipped(self, db: DBSession) -> None:
        """User with notify_in_app=False and notify_email_digest=False is skipped."""
        _, kind = _seed_base(db)
        user_a = _make_user(db, "a@test.com")  # normal
        _make_user(db, "b@test.com", notify_in_app=False, notify_email_digest=False)  # opted out

        defn = _make_definition(db, kind.id, responsible_user_id=None)  # broadcast
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        # Only user_a gets the notification; opted-out user_b is skipped.
        assert len(notifs) == 1
        assert notifs[0].user_id == user_a.id  # type: ignore[attr-defined]

    def test_instance_override_beats_definition(self, db: DBSession) -> None:
        """instance.responsible_user_id=A beats definition.responsible_user_id=B."""
        _, kind = _seed_base(db)
        user_a = _make_user(db, "a@test.com")
        user_b = _make_user(db, "b@test.com")

        defn = _make_definition(db, kind.id, responsible_user_id=user_b.id)  # type: ignore[attr-defined]
        inst = _make_instance(db, defn.id, responsible_user_id=user_a.id)  # type: ignore[attr-defined]

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        assert notifs[0].user_id == user_a.id  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 4. Regression: existing sources unchanged when maintenance is present
# ---------------------------------------------------------------------------


class TestMaintenanceRegression:
    """Prove that best_before + warranty + low_stock counts are not affected
    when maintenance schedules are also present (additive, not a fork)."""

    def test_existing_sources_unchanged(self, db: DBSession) -> None:
        """Run a scan with all four sources; the three existing counts are unaffected."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")

        today = date(2026, 7, 10)

        # --- best_before source ---
        from app.models.item_kind import ItemKind

        kind_p = db.query(ItemKind).filter(ItemKind.code == "perishable").first()
        defn_bb = _make_definition(db, kind_p.id, name="Milk")  # type: ignore[union-attr]
        _make_instance(
            db, defn_bb.id, best_before_date=today
        )  # due today → fires  # type: ignore[attr-defined]

        # --- warranty source ---
        defn_w = _make_definition(db, kind.id, name="Laptop")
        _make_instance(
            db, defn_w.id, warranty_expires=today
        )  # due today → fires  # type: ignore[attr-defined]

        # --- low_stock source ---
        # We need a consumable definition with min_stock set.
        kind_c = db.query(ItemKind).filter(ItemKind.code == "consumable").first()
        from app.models.item_definition import ItemDefinition

        defn_ls = ItemDefinition(
            name="Paper",
            kind_id=kind_c.id,  # type: ignore[union-attr]
            stock_tracking_mode="exact",
            unit="pack",
            min_stock=Decimal("5"),
        )
        db.add(defn_ls)
        db.flush()
        db.commit()

        # Create an instance with quantity=2 (below min_stock=5 → low)
        from app.models.stock_instance import StockInstance

        inst_ls = StockInstance(definition_id=defn_ls.id, quantity=Decimal("2"))
        db.add(inst_ls)
        db.flush()
        db.commit()

        # --- maintenance source (additive) ---
        defn_m = _make_definition(db, kind.id, name="Air Conditioner")
        inst_m = _make_instance(db, defn_m.id)
        _make_schedule(db, inst_m.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)

        # Best_before and warranty each fire exactly once.
        assert summary.best_before == 1
        assert summary.warranty == 1
        # Low_stock fires exactly once (opener).
        assert summary.low_stock == 1
        # Maintenance also fires (additive, not disrupting the above).
        assert summary.maintenance == 1

        # Verify at the DB level: each source has exactly one row.
        for source, expected in [
            ("best_before", 1),
            ("warranty", 1),
            ("low_stock", 1),
            ("maintenance", 1),
        ]:
            count = _notification_count_by_source(db, source)
            assert count == expected, f"source={source}: expected {expected}, got {count}"

    def test_maintenance_does_not_touch_date_sources(self, db: DBSession) -> None:
        """Maintenance schedules in the DB do not affect best_before/warranty counts."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")

        today = date(2026, 7, 10)

        # Only best_before (no maintenance schedule scheduled to fire)
        defn = _make_definition(db, kind.id)
        _make_instance(db, defn.id, best_before_date=today)

        # A maintenance schedule far in the future (window not open yet)
        defn_m = _make_definition(db, kind.id, name="Widget2")
        inst_m = _make_instance(db, defn_m.id)
        _make_schedule(
            db,
            inst_m.id,  # type: ignore[attr-defined]
            next_due_date=today + timedelta(days=60),
            lead_days=7,  # window opens in 53 days
        )

        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db).run_scan(today_local=today)
        assert summary.best_before == 1
        assert summary.warranty == 0
        assert summary.low_stock == 0
        assert summary.maintenance == 0  # far-future schedule not yet firing


# ---------------------------------------------------------------------------
# 5. HTTP — ReminderRunSummary.maintenance + settings round-trip
# ---------------------------------------------------------------------------


class TestMaintenanceSummaryHTTP:
    """HTTP-level tests for the maintenance field in the scan summary."""

    def test_run_returns_maintenance_field(self, admin_client: TestClient) -> None:
        """POST /reminders/run returns maintenance count (0 when no schedules)."""
        resp = admin_client.post("/api/reminders/run")
        assert resp.status_code == 200
        data = resp.json()
        assert "maintenance" in data
        assert data["maintenance"] == 0
        # Existing fields still present
        assert "best_before" in data
        assert "warranty" in data
        assert "low_stock" in data

    def test_maintenance_lead_days_default_in_settings(self, admin_client: TestClient) -> None:
        """GET /settings returns maintenance_lead_days=7 (the code default)."""
        resp = admin_client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reminders"]["maintenance_lead_days"] == 7

    def test_maintenance_lead_days_round_trip(self, admin_client: TestClient) -> None:
        """PATCH /settings updates maintenance_lead_days; GET reflects the new value."""
        patch_resp = admin_client.patch(
            "/api/settings",
            json={"reminders": {"maintenance_lead_days": 14}},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["reminders"]["maintenance_lead_days"] == 14

        get_resp = admin_client.get("/api/settings")
        assert get_resp.json()["reminders"]["maintenance_lead_days"] == 14

    def test_maintenance_lead_days_zero_allowed(self, admin_client: TestClient) -> None:
        """PATCH maintenance_lead_days=0 is accepted (ge=0)."""
        resp = admin_client.patch(
            "/api/settings",
            json={"reminders": {"maintenance_lead_days": 0}},
        )
        assert resp.status_code == 200
        assert resp.json()["reminders"]["maintenance_lead_days"] == 0

    def test_maintenance_lead_days_negative_rejected(self, admin_client: TestClient) -> None:
        """PATCH maintenance_lead_days=-1 → 422 (ge=0 validation)."""
        resp = admin_client.patch(
            "/api/settings",
            json={"reminders": {"maintenance_lead_days": -1}},
        )
        assert resp.status_code == 422

    def test_existing_settings_fields_unaffected(self, admin_client: TestClient) -> None:
        """PATCH maintenance_lead_days does not disturb other settings fields."""
        admin_client.patch(
            "/api/settings",
            json={"reminders": {"best_before_lead_days": 5}},
        )
        admin_client.patch(
            "/api/settings",
            json={"reminders": {"maintenance_lead_days": 10}},
        )
        data = admin_client.get("/api/settings").json()
        assert data["reminders"]["best_before_lead_days"] == 5
        assert data["reminders"]["maintenance_lead_days"] == 10


# ---------------------------------------------------------------------------
# 6. ScanSummary.maintenance field existence (unit)
# ---------------------------------------------------------------------------


class TestScanSummaryField:
    """ScanSummary and ReminderRunSummary carry the maintenance field."""

    def test_scan_summary_has_maintenance(self) -> None:
        from app.services.reminder_engine import ScanSummary

        s = ScanSummary()
        assert hasattr(s, "maintenance")
        assert s.maintenance == 0

    def test_reminder_run_summary_has_maintenance(self) -> None:
        from app.schemas.reminders import ReminderRunSummary

        r = ReminderRunSummary(best_before=1, warranty=2, low_stock=3, maintenance=4)
        assert r.maintenance == 4

    def test_params_stored_correctly(self, db: DBSession) -> None:
        """Notification params include all required keys."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id, name="TV")
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        _make_schedule(db, inst.id, name="Screen cleaning", next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        params = json.loads(notifs[0].params)
        assert params["name"] == "Screen cleaning"
        assert params["instance_name"] == "TV"
        assert params["next_due_date"] == today.isoformat()
        assert params["days_remaining"] == 0
        assert "location_id" in params

    def test_notification_fields(self, db: DBSession) -> None:
        """Notification row has correct source, subject_type, message_code."""
        _, kind = _seed_base(db)
        _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id, name="Car")
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        s = _make_schedule(db, inst.id, name="Oil change", next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        n = notifs[0]
        assert n.source == "maintenance"
        assert n.subject_type == "maintenance_schedule"
        assert n.subject_id == s.id  # type: ignore[attr-defined]
        assert n.message_code == "reminder.maintenance"
        # low_stock-only columns stay at defaults (maintenance is NOT an episode)
        assert n.episode_started_on is None
        assert n.offset_days is None


# ---------------------------------------------------------------------------
# 7. Dedup key format
# ---------------------------------------------------------------------------


class TestDedupKey:
    """Dedup key is maintenance:u{uid}:s{sid}:{next_due_date}."""

    def test_dedup_key_format(self, db: DBSession) -> None:
        _, kind = _seed_base(db)
        user = _make_user(db, "a@test.com")
        defn = _make_definition(db, kind.id)
        inst = _make_instance(db, defn.id)

        today = date(2026, 7, 10)
        s = _make_schedule(db, inst.id, next_due_date=today, lead_days=0)  # type: ignore[attr-defined]

        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db).run_scan(today_local=today)

        notifs = _maintenance_notifs(db)
        assert len(notifs) == 1
        expected_key = f"maintenance:u{user.id}:s{s.id}:{today.isoformat()}"  # type: ignore[attr-defined]
        assert notifs[0].dedup_key == expected_key
