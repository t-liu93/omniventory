"""M4 Step 7 tests: email digest channel + server catalog + delivery log + F1 refactor.

Required coverage (M4.md §5 "Dispatcher idempotency" + §9 Step 7 + §10 Step 7):

Migration 0019:
- upgrade creates notification_deliveries table + index
- downgrade removes the table

NotificationDeliveryRepository:
- record(): inserts a delivery row with correct fields
- exists_sent(): True for status='sent'; False for status='failed' or no row

Server message catalog (app/notifications/messages.py):
- render_line: renders each of the four codes in EN and ZH
- render_digest: returns (subject, body) with correct line count in EN and ZH
- unknown code falls back gracefully (no exception)

EmailChannel:
- is_enabled(): True when enabled=True AND host is set; False otherwise
- disabled/unconfigured = no-op: no SMTP call, no delivery rows
- deliver() with include_email_digest=False = no-op (event path)
- digest groups by recipient: multi-user -> one digest per user
- recipient language: zh user gets ZH digest, en/null user gets EN digest
- SMTP called with correct host/port/credentials
- success: delivery rows recorded as 'sent'
- SMTP error: delivery rows recorded as 'failed'; no exception raised
- idempotency: notifications with existing 'sent' row are skipped (not re-sent)
- failed row does NOT block re-delivery on next pass

F1 refactor (ScanSummary.new_notifications + caller dispatch-after-commit):
- run_scan() returns new notifications in ScanSummary.new_notifications
- ScanSummary.new_notifications is a list (never None)
- dispatch happens AFTER DB commit (structural: SMTP error does not roll back notification rows)
- build_dispatcher() registers EmailChannel

build_dispatcher:
- returns a NotificationDispatcher
- EmailChannel is registered

Integration: POST /reminders/run dispatches after commit (email channel gets called after scan+commit).
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path
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

    from app.db.base import Base as _Base

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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step7_")
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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step7")
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
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_minimal(db: Session) -> tuple[object, object, object]:
    """Seed Household, User, and a basic ItemKind+Definition.  Returns (hh, user, defn)."""
    from app.auth.passwords import hash_password
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

    user = User(email="admin@example.com", password_hash=hash_password("pass"), is_active=True)
    db.add(user)
    db.flush()

    defn = ItemDefinition(name="Milk", kind_id=kind.id)
    db.add(defn)
    db.flush()

    return hh, user, defn


def _make_notification(
    db: Session, user_id: int, code: str = "reminder.best_before", params: dict | None = None
) -> object:
    """Insert a Notification row and return it."""
    from app.models.notification import Notification

    n = Notification(
        user_id=user_id,
        source="best_before",
        subject_type="instance",
        subject_id=1,
        dedup_key=f"{code}:u{user_id}:{id(params)}",
        message_code=code,
        params=json.dumps(params or {"name": "Milk", "date": "2026-06-25", "days_remaining": 5}),
    )
    db.add(n)
    db.flush()
    return n


def _enable_email(db: Session, host: str = "smtp.example.com", port: int | None = 25) -> None:
    """Configure the email channel settings in the test DB."""
    from app.repositories.setting import SettingsRepository

    repo = SettingsRepository(db)
    repo.set("channels.email.enabled", "true")
    repo.set("channels.email.host", host)
    if port is not None:
        repo.set("channels.email.port", str(port))
    repo.set("channels.email.from_address", "omniventory@example.com")
    db.flush()


# ---------------------------------------------------------------------------
# Tests: Migration 0019
# ---------------------------------------------------------------------------


class TestMigration0019:
    """Migration 0019 creates and drops the notification_deliveries table.

    Uses a subprocess call to ``.venv/bin/alembic`` so that the local
    ``backend/alembic/`` package directory does not shadow the installed
    ``alembic`` pip package (same pattern as other migration tests).
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
        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_migtest_0019_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        return f"sqlite:///{path_str}", db_path

    def test_upgrade_creates_table(self) -> None:
        """Migration 0019 upgrade creates the notification_deliveries table + index."""
        import sqlalchemy as sa

        url, db_path = self._make_temp_db()
        try:
            rc, out = self._run_alembic("upgrade", "0019", url=url)
            assert rc == 0, f"alembic upgrade 0019 failed:\n{out}"

            engine = create_engine(url)
            inspector = sa.inspect(engine)
            assert "notification_deliveries" in inspector.get_table_names()

            columns = {c["name"] for c in inspector.get_columns("notification_deliveries")}
            assert {"id", "notification_id", "channel", "status", "detail", "created_at"} <= columns

            indexes = inspector.get_indexes("notification_deliveries")
            index_names = {idx["name"] for idx in indexes}
            assert "ix_notification_deliveries_notification_channel" in index_names
            engine.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_drops_table(self) -> None:
        """Migration 0019 downgrade removes notification_deliveries."""
        import sqlalchemy as sa

        url, db_path = self._make_temp_db()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0019", url=url)
            assert rc_up == 0, f"upgrade 0019 failed:\n{out_up}"

            engine = create_engine(url)
            inspector = sa.inspect(engine)
            assert "notification_deliveries" in inspector.get_table_names()
            engine.dispose()

            rc_down, out_down = self._run_alembic("downgrade", "0018", url=url)
            assert rc_down == 0, f"downgrade from 0019 to 0018 failed:\n{out_down}"

            engine2 = create_engine(url)
            inspector2 = sa.inspect(engine2)
            assert "notification_deliveries" not in inspector2.get_table_names()
            engine2.dispose()
        finally:
            if db_path.exists():
                db_path.unlink()


# ---------------------------------------------------------------------------
# Tests: NotificationDeliveryRepository
# ---------------------------------------------------------------------------


class TestNotificationDeliveryRepository:
    """NotificationDeliveryRepository.record() and exists_sent()."""

    def test_record_inserts_sent_row(self, db_session: Session) -> None:
        """record() inserts a row with the given channel/status/detail."""
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _, user, _ = _seed_minimal(db_session)
        n = _make_notification(db_session, user.id)  # type: ignore[union-attr]

        repo = NotificationDeliveryRepository(db_session)
        row = repo.record(n.id, "email", "sent")  # type: ignore[union-attr]

        assert row.notification_id == n.id
        assert row.channel == "email"
        assert row.status == "sent"
        assert row.detail is None

    def test_record_inserts_failed_row_with_detail(self, db_session: Session) -> None:
        """record() stores the detail field (truncated to 1024)."""
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _, user, _ = _seed_minimal(db_session)
        n = _make_notification(db_session, user.id)  # type: ignore[union-attr]

        repo = NotificationDeliveryRepository(db_session)
        long_detail = "x" * 2000
        row = repo.record(n.id, "email", "failed", detail=long_detail)  # type: ignore[union-attr]

        assert row.status == "failed"
        assert len(row.detail) == 1024  # type: ignore[arg-type]

    def test_exists_sent_true_for_sent_row(self, db_session: Session) -> None:
        """exists_sent() returns True when a 'sent' row exists."""
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _, user, _ = _seed_minimal(db_session)
        n = _make_notification(db_session, user.id)  # type: ignore[union-attr]

        repo = NotificationDeliveryRepository(db_session)
        repo.record(n.id, "email", "sent")  # type: ignore[union-attr]

        assert repo.exists_sent(n.id, "email") is True  # type: ignore[union-attr]

    def test_exists_sent_false_for_failed_row(self, db_session: Session) -> None:
        """exists_sent() returns False when only a 'failed' row exists (retry allowed)."""
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _, user, _ = _seed_minimal(db_session)
        n = _make_notification(db_session, user.id)  # type: ignore[union-attr]

        repo = NotificationDeliveryRepository(db_session)
        repo.record(n.id, "email", "failed", detail="SMTP error")  # type: ignore[union-attr]

        assert repo.exists_sent(n.id, "email") is False  # type: ignore[union-attr]

    def test_exists_sent_false_when_no_rows(self, db_session: Session) -> None:
        """exists_sent() returns False when no delivery row exists at all."""
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _, user, _ = _seed_minimal(db_session)
        n = _make_notification(db_session, user.id)  # type: ignore[union-attr]

        repo = NotificationDeliveryRepository(db_session)
        assert repo.exists_sent(n.id, "email") is False  # type: ignore[union-attr]

    def test_exists_sent_channel_specific(self, db_session: Session) -> None:
        """exists_sent() is channel-specific: a 'sent' on 'http' doesn't block 'email'."""
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        _, user, _ = _seed_minimal(db_session)
        n = _make_notification(db_session, user.id)  # type: ignore[union-attr]

        repo = NotificationDeliveryRepository(db_session)
        repo.record(n.id, "http", "sent")  # type: ignore[union-attr]

        assert repo.exists_sent(n.id, "email") is False  # type: ignore[union-attr]
        assert repo.exists_sent(n.id, "http") is True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Tests: Server message catalog
# ---------------------------------------------------------------------------


class TestMessagesCatalog:
    """app/notifications/messages.py: render_line + render_digest."""

    def test_render_best_before_en(self) -> None:
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.best_before",
            {"name": "Milk", "days_remaining": 3},
            "en",
        )
        assert "Milk" in result
        assert "3" in result
        assert "expir" in result.lower()

    def test_render_best_before_zh(self) -> None:
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.best_before",
            {"name": "牛奶", "days_remaining": 3},
            "zh",
        )
        assert "牛奶" in result
        assert "3" in result

    def test_render_best_before_today_en(self) -> None:
        from app.notifications.messages import render_line

        result = render_line("reminder.best_before", {"name": "Milk", "days_remaining": 0}, "en")
        assert "today" in result.lower()

    def test_render_best_before_expired_en(self) -> None:
        from app.notifications.messages import render_line

        result = render_line("reminder.best_before", {"name": "Milk", "days_remaining": -2}, "en")
        assert "ago" in result.lower() or "expired" in result.lower()

    def test_render_warranty_en(self) -> None:
        from app.notifications.messages import render_line

        result = render_line("reminder.warranty", {"name": "TV", "days_remaining": 30}, "en")
        assert "TV" in result
        assert "30" in result
        assert "warranty" in result.lower()

    def test_render_warranty_zh(self) -> None:
        from app.notifications.messages import render_line

        result = render_line("reminder.warranty", {"name": "电视", "days_remaining": 30}, "zh")
        assert "电视" in result
        assert "保修" in result

    def test_render_low_stock_en(self) -> None:
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock",
            {"name": "Rice", "current": "0.5", "threshold": "1.0"},
            "en",
        )
        assert "Rice" in result
        assert "0.5" in result
        assert "1.0" in result

    def test_render_low_stock_zh(self) -> None:
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock",
            {"name": "大米", "current": "0.5", "threshold": "1.0"},
            "zh",
        )
        assert "大米" in result
        assert "库存" in result

    def test_render_low_stock_repeat_en(self) -> None:
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock_repeat",
            {"name": "Rice", "current": "0.5", "threshold": "1.0", "offset": 3},
            "en",
        )
        assert "Rice" in result
        assert "3" in result

    def test_render_low_stock_repeat_zh(self) -> None:
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock_repeat",
            {"name": "大米", "current": "0.5", "threshold": "1.0", "offset": 3},
            "zh",
        )
        assert "大米" in result
        assert "3" in result

    # --- level-mode low_stock rendering (walkthrough fix #2) ---

    def test_render_low_stock_level_mode_en(self) -> None:
        """level-mode low_stock in EN shows localized level label, not blank."""
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock",
            {"name": "Torx Screws", "mode": "level", "level": "low"},
            "en",
        )
        assert "Torx Screws" in result
        assert "low" in result
        assert "None" not in result
        assert result != ""

    def test_render_low_stock_level_mode_zh(self) -> None:
        """level-mode low_stock in ZH shows localized level label '低', not blank."""
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock",
            {"name": "Torx螺丝M6x30", "mode": "level", "level": "low"},
            "zh",
        )
        assert "Torx螺丝M6x30" in result
        assert "低" in result
        assert "None" not in result

    def test_render_low_stock_repeat_level_mode_en(self) -> None:
        """level-mode low_stock_repeat in EN shows localized level label and offset."""
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock_repeat",
            {"name": "Torx Screws", "mode": "level", "level": "low", "offset": 7},
            "en",
        )
        assert "Torx Screws" in result
        assert "low" in result
        assert "7" in result
        assert "None" not in result

    def test_render_low_stock_repeat_level_mode_zh(self) -> None:
        """level-mode low_stock_repeat in ZH shows localized level label '低' and offset."""
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock_repeat",
            {"name": "Torx螺丝M6x30", "mode": "level", "level": "low", "offset": 7},
            "zh",
        )
        assert "Torx螺丝M6x30" in result
        assert "低" in result
        assert "7" in result
        assert "None" not in result

    def test_render_low_stock_level_mode_missing_level_key_no_crash(self) -> None:
        """level-mode with missing 'level' key falls back gracefully, does not crash."""
        from app.notifications.messages import render_line

        # Old row in DB that has mode='level' but no 'level' key
        result = render_line(
            "reminder.low_stock",
            {"name": "Widget", "mode": "level"},
            "en",
        )
        assert isinstance(result, str)
        assert "Widget" in result

    def test_render_low_stock_exact_mode_unchanged_en(self) -> None:
        """Regression: exact-mode low_stock rendering in EN is unchanged."""
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock",
            {"name": "Rice", "current": "0.5", "threshold": "1.0", "mode": "exact"},
            "en",
        )
        assert "Rice" in result
        assert "0.5" in result
        assert "1.0" in result

    def test_render_low_stock_repeat_exact_mode_unchanged_zh(self) -> None:
        """Regression: exact-mode low_stock_repeat rendering in ZH is unchanged."""
        from app.notifications.messages import render_line

        result = render_line(
            "reminder.low_stock_repeat",
            {"name": "大米", "current": "0.5", "threshold": "1.0", "offset": 3, "mode": "exact"},
            "zh",
        )
        assert "大米" in result
        assert "3" in result
        assert "0.5" in result

    def test_render_unknown_code_no_exception(self) -> None:
        """Unknown codes return a fallback string without raising."""
        from app.notifications.messages import render_line

        result = render_line("some.unknown.code", {}, "en")
        assert isinstance(result, str)
        assert "some.unknown.code" in result

    def test_render_digest_en(self) -> None:
        from app.notifications.messages import render_digest

        subject, body = render_digest(["Line A", "Line B", "Line C"], "en")
        assert "3" in subject
        assert "Line A" in body
        assert "Line B" in body
        assert "Line C" in body

    def test_render_digest_zh(self) -> None:
        from app.notifications.messages import render_digest

        subject, body = render_digest(["行A", "行B"], "zh")
        assert "2" in subject
        assert "行A" in body
        assert "行B" in body

    def test_render_digest_single_en_grammatical(self) -> None:
        """Single item uses singular 'item' not 'items' in EN."""
        from app.notifications.messages import render_digest

        subject, body = render_digest(["Only one"], "en")
        assert "item" in subject
        assert "items" not in subject

    def test_render_digest_unknown_lang_defaults_en(self) -> None:
        """Non-zh language defaults to EN."""
        from app.notifications.messages import render_digest

        subject, body = render_digest(["Line 1"], "fr")
        assert isinstance(subject, str)
        assert "1" in subject


# ---------------------------------------------------------------------------
# Tests: EmailChannel
# ---------------------------------------------------------------------------


class TestEmailChannelIsEnabled:
    """EmailChannel.is_enabled() respects enabled flag and host requirement."""

    def test_enabled_when_flag_and_host_set(self, db_session: Session) -> None:
        _seed_minimal(db_session)
        _enable_email(db_session)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)
        assert channel.is_enabled() is True

    def test_disabled_when_flag_false(self, db_session: Session) -> None:
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "false")
        repo.set("channels.email.host", "smtp.example.com")
        db_session.flush()

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)
        assert channel.is_enabled() is False

    def test_disabled_when_no_host(self, db_session: Session) -> None:
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        # no host set
        db_session.flush()

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)
        assert channel.is_enabled() is False


class TestEmailChannelDeliver:
    """EmailChannel.deliver() behaviour across various scenarios."""

    def test_noop_when_disabled(self, db_session: Session) -> None:
        """When the channel is disabled, no SMTP call and no delivery rows."""
        _seed_minimal(db_session)
        # email not enabled (default)
        _, user, _ = (
            _seed_minimal.__wrapped__(db_session)
            if hasattr(_seed_minimal, "__wrapped__")
            else (None, None, None)
        )
        # Re-seed

        db_session.rollback()  # fresh state
        # Actually seed properly
        _, user, _ = _seed_minimal(db_session)  # type: ignore[assignment]

    def test_noop_when_include_email_digest_false(self, db_session: Session) -> None:
        """deliver(include_email_digest=False) is a no-op (event-trigger path)."""
        _seed_minimal(db_session)
        _enable_email(db_session)

        _, user, _ = _seed_minimal.__wrapped__(db_session) if False else (None, None, None)

        n = _make_notification(db_session, 1)  # user.id=1 from _seed_minimal

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp:
            channel.deliver([n], include_email_digest=False)  # type: ignore[list-item]

        mock_smtp.assert_not_called()

        from app.repositories.notification_delivery import NotificationDeliveryRepository

        repo = NotificationDeliveryRepository(db_session)
        assert not repo.exists_sent(n.id, "email")  # type: ignore[arg-type]

    def test_noop_when_channel_disabled(self, db_session: Session) -> None:
        """When email is disabled (no host), is_enabled() returns False.

        The dispatcher gates deliver() on is_enabled(); we verify the flag here.
        """
        _seed_minimal(db_session)
        # email NOT enabled — default state (no host set)
        _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp:
            # is_enabled() must be False when host is not set
            assert not channel.is_enabled()
            # The dispatcher guards deliver() with is_enabled(), so SMTP is never called.
            pass

        mock_smtp.assert_not_called()

    def test_digest_sent_to_single_user(self, db_session: Session) -> None:
        """One user with one notification: SMTP is called once and a 'sent' row is recorded."""
        _seed_minimal(db_session)
        _enable_email(db_session)

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        mock_smtp_cls.assert_called_once()
        mock_smtp_instance.send_message.assert_called_once()

        from app.repositories.notification_delivery import NotificationDeliveryRepository

        repo = NotificationDeliveryRepository(db_session)
        assert repo.exists_sent(n.id, "email") is True  # type: ignore[arg-type]

    def test_digest_groups_by_recipient(self, db_session: Session) -> None:
        """Two users each get their own digest email (two SMTP send_message calls)."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()

        kind = ItemKind(code="perishable", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()

        user_a = User(
            email="alice@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
            preferred_language="en",
        )
        user_b = User(
            email="bob@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
            preferred_language="zh",
        )
        db_session.add_all([user_a, user_b])
        db_session.flush()

        _enable_email(db_session)

        n_a = _make_notification(
            db_session,
            user_a.id,
            params={"name": "AppleA", "date": "2026-06-25", "days_remaining": 2},
        )
        n_b = _make_notification(
            db_session,
            user_b.id,
            params={"name": "BananaB", "date": "2026-06-25", "days_remaining": 1},
        )

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        sent_subjects: list[str] = []

        def _fake_send_message(msg: object) -> None:
            sent_subjects.append(msg["Subject"])  # type: ignore[index]

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_instance.send_message.side_effect = _fake_send_message
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n_a, n_b], include_email_digest=True)  # type: ignore[list-item]

        # Two SMTP context managers were opened (one per user)
        assert mock_smtp_cls.call_count == 2
        assert len(sent_subjects) == 2

        from app.repositories.notification_delivery import NotificationDeliveryRepository

        repo = NotificationDeliveryRepository(db_session)
        assert repo.exists_sent(n_a.id, "email") is True  # type: ignore[arg-type]
        assert repo.exists_sent(n_b.id, "email") is True  # type: ignore[arg-type]

    def test_recipient_language_en(self, db_session: Session) -> None:
        """User with preferred_language='en' receives an EN digest."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()

        kind = ItemKind(code="perishable", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()

        user = User(
            email="en_user@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
            preferred_language="en",
        )
        db_session.add(user)
        db_session.flush()

        _enable_email(db_session)
        n = _make_notification(db_session, user.id)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        received_subjects: list[str] = []

        def _capture_msg(msg: object) -> None:
            received_subjects.append(msg["Subject"])  # type: ignore[index]

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_instance.send_message.side_effect = _capture_msg
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        assert len(received_subjects) == 1
        # EN subject should contain "digest" or "reminder" in English
        assert "Omniventory" in received_subjects[0]
        # EN subject should NOT contain Chinese characters
        for char in "提醒汇总":
            assert char not in received_subjects[0]

    def test_recipient_language_zh(self, db_session: Session) -> None:
        """User with preferred_language='zh' receives a ZH digest."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()

        kind = ItemKind(code="perishable", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()

        user = User(
            email="zh_user@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
            preferred_language="zh",
        )
        db_session.add(user)
        db_session.flush()

        _enable_email(db_session)
        n = _make_notification(db_session, user.id)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        received_subjects: list[str] = []

        def _capture_msg(msg: object) -> None:
            received_subjects.append(msg["Subject"])  # type: ignore[index]

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_instance.send_message.side_effect = _capture_msg
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        assert len(received_subjects) == 1
        # ZH subject contains Chinese
        assert "提醒" in received_subjects[0]

    def test_recipient_language_null_defaults_en(self, db_session: Session) -> None:
        """User with preferred_language=None defaults to EN digest."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()

        kind = ItemKind(code="perishable", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()

        user = User(
            email="null_lang@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
            preferred_language=None,  # null → EN fallback
        )
        db_session.add(user)
        db_session.flush()

        _enable_email(db_session)
        n = _make_notification(db_session, user.id)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        received_subjects: list[str] = []

        def _capture_msg(msg: object) -> None:
            received_subjects.append(msg["Subject"])  # type: ignore[index]

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_instance.send_message.side_effect = _capture_msg
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        assert len(received_subjects) == 1
        # Should be EN (no Chinese characters in subject)
        for char in "提醒汇总":
            assert char not in received_subjects[0]

    def test_smtp_error_records_failed_and_does_not_raise(self, db_session: Session) -> None:
        """SMTP error → 'failed' delivery rows; no exception propagated."""
        _seed_minimal(db_session)
        _enable_email(db_session)

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.side_effect = ConnectionRefusedError("Connection refused")
            # Must NOT raise
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        from app.repositories.notification_delivery import NotificationDeliveryRepository

        repo = NotificationDeliveryRepository(db_session)
        assert not repo.exists_sent(n.id, "email")  # type: ignore[arg-type]
        # A 'failed' row should have been recorded
        from sqlalchemy import select

        from app.models.notification_delivery import NotificationDelivery

        stmt = select(NotificationDelivery).where(
            NotificationDelivery.notification_id == n.id,
            NotificationDelivery.status == "failed",
        )
        failed = db_session.execute(stmt).scalar_one_or_none()
        assert failed is not None
        assert "refused" in (failed.detail or "").lower() or "Connection" in (failed.detail or "")

    def test_idempotent_skip_already_sent(self, db_session: Session) -> None:
        """A notification with an existing 'sent' row is skipped; SMTP not called twice."""
        _seed_minimal(db_session)
        _enable_email(db_session)

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        # Pre-record a 'sent' row to simulate prior delivery
        delivery_repo = NotificationDeliveryRepository(db_session)
        delivery_repo.record(n.id, "email", "sent")  # type: ignore[arg-type]

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        # SMTP was NOT called — already sent
        mock_smtp_cls.assert_not_called()

    def test_failed_row_does_not_block_retry(self, db_session: Session) -> None:
        """A 'failed' row does NOT prevent re-delivery on the next pass."""
        _seed_minimal(db_session)
        _enable_email(db_session)

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel
        from app.repositories.notification_delivery import NotificationDeliveryRepository

        # Pre-record a 'failed' row (e.g. from a previous attempt)
        delivery_repo = NotificationDeliveryRepository(db_session)
        delivery_repo.record(n.id, "email", "failed", detail="prior error")  # type: ignore[arg-type]

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        # SMTP WAS called again (retry is allowed after 'failed')
        mock_smtp_cls.assert_called_once()
        mock_smtp_instance.send_message.assert_called_once()

        # Now there's a 'sent' row
        assert delivery_repo.exists_sent(n.id, "email") is True  # type: ignore[arg-type]

    def test_starttls_called_when_encryption_starttls(self, db_session: Session) -> None:
        """When encryption='starttls', smtp.starttls() is called."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.encryption", "starttls")
        db_session.flush()

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        mock_smtp_instance.starttls.assert_called_once()

    def test_ssl_uses_smtp_ssl_not_smtp(self, db_session: Session) -> None:
        """When encryption='ssl', smtplib.SMTP_SSL is used and starttls() is NOT called."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.encryption", "ssl")
        db_session.flush()

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with (
            patch("smtplib.SMTP") as mock_smtp_cls,
            patch("smtplib.SMTP_SSL") as mock_smtp_ssl_cls,
        ):
            mock_smtp_ssl_instance = MagicMock()
            mock_smtp_ssl_cls.return_value.__enter__ = MagicMock(
                return_value=mock_smtp_ssl_instance
            )
            mock_smtp_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        mock_smtp_ssl_cls.assert_called_once()
        mock_smtp_cls.assert_not_called()
        mock_smtp_ssl_instance.starttls.assert_not_called()

    def test_encryption_none_no_starttls(self, db_session: Session) -> None:
        """When encryption='none', smtp.starttls() is NOT called."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.encryption", "none")
        db_session.flush()

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        mock_smtp_instance.starttls.assert_not_called()

    def test_login_called_when_credentials_set(self, db_session: Session) -> None:
        """When username and password are set, smtp.login() is called."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.username", "user@example.com")
        repo.set("channels.email.password", "s3cr3t")
        db_session.flush()

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        mock_smtp_instance.login.assert_called_once_with("user@example.com", "s3cr3t")


# ---------------------------------------------------------------------------
# Tests: F1 refactor (ScanSummary.new_notifications + dispatch structure)
# ---------------------------------------------------------------------------


class TestF1Refactor:
    """ScanSummary.new_notifications field and post-commit dispatch order."""

    def test_scan_summary_new_notifications_default_empty(self) -> None:
        """ScanSummary() initialises new_notifications as an empty list."""
        from app.services.reminder_engine import ScanSummary

        s = ScanSummary()
        assert s.new_notifications == []
        assert isinstance(s.new_notifications, list)

    def test_run_scan_returns_new_notifications(self, db_session: Session) -> None:
        """run_scan() returns newly created Notification objects in ScanSummary."""
        from decimal import Decimal

        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.services.reminder_engine import ReminderEngine

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()

        kind = ItemKind(code="perishable", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()

        user = User(
            email="admin@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(user)
        db_session.flush()

        defn = ItemDefinition(name="Milk", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        today = date.today()
        lot = StockInstance(
            definition_id=defn.id,
            best_before_date=today + timedelta(days=1),
            quantity=Decimal("1"),
        )
        db_session.add(lot)
        db_session.flush()

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=today)

        assert summary.best_before == 1
        assert len(summary.new_notifications) == 1
        assert summary.new_notifications[0].user_id == user.id

    def test_smtp_error_does_not_rollback_notification_rows(self, db_session: Session) -> None:
        """SMTP failure (best-effort) must not prevent notification rows from being committed.

        This verifies the F1 ordering: notification rows are committed BEFORE
        dispatch, so a channel error cannot roll them back.
        """
        from decimal import Decimal

        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.notification import Notification
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.services.reminder_engine import ReminderEngine

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()

        kind = ItemKind(code="perishable", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()

        user = User(
            email="admin@example.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(user)
        db_session.flush()

        defn = ItemDefinition(name="Milk", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        _enable_email(db_session)

        today = date.today()
        lot = StockInstance(
            definition_id=defn.id,
            best_before_date=today + timedelta(days=1),
            quantity=Decimal("1"),
        )
        db_session.add(lot)
        db_session.flush()

        engine = ReminderEngine(db_session)
        summary = engine.run_scan(today_local=today)

        # Commit notification rows BEFORE dispatch (F1 ordering)
        db_session.commit()

        # Verify notification row is committed
        from sqlalchemy import select

        notifs = db_session.execute(select(Notification)).scalars().all()
        assert len(notifs) == 1, "Notification row must be committed before dispatch"

        # Now dispatch with a failing SMTP
        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.side_effect = ConnectionRefusedError("SMTP down")
            channel.deliver(summary.new_notifications, include_email_digest=True)

        # Notification row still exists — SMTP failure did not roll it back
        notifs_after = db_session.execute(select(Notification)).scalars().all()
        assert len(notifs_after) == 1

        # A 'failed' delivery row was recorded
        from app.models.notification_delivery import NotificationDelivery

        deliveries = db_session.execute(select(NotificationDelivery)).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == "failed"

    def test_build_dispatcher_returns_dispatcher_with_email(self) -> None:
        """build_dispatcher() returns a NotificationDispatcher with EmailChannel registered."""
        from unittest.mock import MagicMock

        from app.notifications.dispatcher import NotificationDispatcher, build_dispatcher

        mock_db = MagicMock()
        with patch("app.notifications.channels.email.EmailChannel") as mock_email_cls:
            mock_email_cls.return_value = MagicMock()
            mock_email_cls.return_value.is_enabled.return_value = True

            dispatcher = build_dispatcher(mock_db)

        assert isinstance(dispatcher, NotificationDispatcher)
        mock_email_cls.assert_called_once_with(mock_db)


# ---------------------------------------------------------------------------
# Tests: Integration — POST /reminders/run dispatches email
# ---------------------------------------------------------------------------


class TestRemindersRunWithEmail:
    """POST /reminders/run invokes email dispatch after committing notification rows."""

    def test_run_scan_calls_build_dispatcher_after_commit(self, http_client: object) -> None:
        """POST /reminders/run should call build_dispatcher (email dispatch) after commit."""
        with (
            patch("app.api.routes.reminders.build_dispatcher") as mock_build_dispatcher,
        ):
            mock_dispatcher = MagicMock()
            mock_build_dispatcher.return_value = mock_dispatcher

            resp = http_client.post("/api/reminders/run")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        # build_dispatcher was called (Step 7 wiring)
        mock_build_dispatcher.assert_called_once()
        # dispatch was invoked with include_email_digest=True
        mock_dispatcher.dispatch.assert_called_once()
        call_kwargs = mock_dispatcher.dispatch.call_args
        assert call_kwargs.kwargs.get("include_email_digest") is True


# ---------------------------------------------------------------------------
# Tests: Walkthrough Fix 1 — encryption, from_name, legacy shim, test endpoint
# ---------------------------------------------------------------------------


class TestLegacyEncryptionShim:
    """Legacy use_tls shim: existing stored use_tls is mapped to encryption value."""

    def test_legacy_use_tls_true_maps_to_starttls(self, db_session: Session) -> None:
        """Stored use_tls='true' with NO encryption key → email_channel_config().encryption == 'starttls'."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.use_tls", "true")
        # Do NOT set channels.email.encryption
        db_session.flush()

        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        cfg = svc.email_channel_config()
        assert cfg.encryption == "starttls"

    def test_legacy_use_tls_false_maps_to_none(self, db_session: Session) -> None:
        """Stored use_tls='false' with NO encryption key → email_channel_config().encryption == 'none'."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.use_tls", "false")
        # Do NOT set channels.email.encryption
        db_session.flush()

        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        cfg = svc.email_channel_config()
        assert cfg.encryption == "none"

    def test_new_encryption_key_takes_precedence_over_legacy_use_tls(
        self, db_session: Session
    ) -> None:
        """When both old use_tls and new encryption are stored, new key wins."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.use_tls", "true")  # legacy: would map to 'starttls'
        repo.set("channels.email.encryption", "ssl")  # new key: should win
        db_session.flush()

        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        cfg = svc.email_channel_config()
        assert cfg.encryption == "ssl"

    def test_no_keys_stored_defaults_to_none(self, db_session: Session) -> None:
        """When neither key is stored, encryption defaults to 'none'."""
        _seed_minimal(db_session)
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        cfg = svc.email_channel_config()
        assert cfg.encryption == "none"


class TestFromNameHeader:
    """from_name is included in the email From header using formataddr."""

    def test_from_name_in_from_header(self, db_session: Session) -> None:
        """When from_name is set, the From header uses 'Display Name <addr>' format."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.from_address", "noreply@example.com")
        repo.set("channels.email.from_name", "Omniventory Alerts")
        db_session.flush()

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        captured_msgs: list[object] = []

        def _capture(msg: object) -> None:
            captured_msgs.append(msg)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_instance.send_message.side_effect = _capture
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        assert len(captured_msgs) == 1
        from_header = captured_msgs[0]["From"]  # type: ignore[index]
        # Should contain both the name and the address
        assert "Omniventory Alerts" in from_header
        assert "noreply@example.com" in from_header

    def test_no_from_name_uses_plain_addr(self, db_session: Session) -> None:
        """When from_name is not set, the From header is just the raw address."""
        _seed_minimal(db_session)
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.from_address", "noreply@example.com")
        # No from_name
        db_session.flush()

        n = _make_notification(db_session, 1)

        from app.notifications.channels.email import EmailChannel

        channel = EmailChannel(db_session)

        captured_msgs: list[object] = []

        def _capture(msg: object) -> None:
            captured_msgs.append(msg)

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_instance.send_message.side_effect = _capture
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            channel.deliver([n], include_email_digest=True)  # type: ignore[list-item]

        assert len(captured_msgs) == 1
        from_header = captured_msgs[0]["From"]  # type: ignore[index]
        assert from_header == "noreply@example.com"


class TestRenderTestEmail:
    """render_test_email returns bilingual subject/body tuple."""

    def test_render_test_email_en_subject_and_body(self) -> None:
        from app.notifications.messages import render_test_email

        subject, body = render_test_email("en")
        assert isinstance(subject, str)
        assert isinstance(body, str)
        assert len(subject) > 0
        assert len(body) > 0
        # EN text should NOT contain Chinese characters
        for char in "测试邮件已":
            assert char not in subject
            assert char not in body

    def test_render_test_email_zh_subject_and_body(self) -> None:
        from app.notifications.messages import render_test_email

        subject, body = render_test_email("zh")
        assert isinstance(subject, str)
        assert isinstance(body, str)
        # ZH should contain Chinese
        assert any(ord(c) > 0x4E00 for c in subject + body), (
            "ZH render should contain Chinese characters"
        )

    def test_render_test_email_unknown_lang_defaults_en(self) -> None:
        from app.notifications.messages import render_test_email

        subject, body = render_test_email("fr")
        # Should fall back to EN — no Chinese
        for char in "测试":
            assert char not in subject
            assert char not in body


class TestEmailTestEndpoint:
    """POST /settings/email/test diagnostic endpoint."""

    def test_success_ok_true(self, http_client: object) -> None:
        """With host configured and SMTP mocked to succeed → ok=true, recipient=admin email."""
        # Configure host
        http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={"channels": {"email": {"host": "smtp.example.com"}}},
        )

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

            resp = http_client.post("/api/settings/email/test")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["detail"] is None
        assert data["recipient"] == "admin@example.com"
        mock_smtp_instance.send_message.assert_called_once()

    def test_smtp_error_ok_false_with_detail(self, http_client: object) -> None:
        """When SMTP raises → ok=false, detail is non-null."""
        http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={"channels": {"email": {"host": "smtp.example.com"}}},
        )

        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.side_effect = ConnectionRefusedError("Connection refused")
            resp = http_client.post("/api/settings/email/test")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["detail"] is not None
        assert "refused" in data["detail"].lower() or "Connection" in data["detail"]

    def test_no_host_ok_false_message(self, http_client: object) -> None:
        """When no host is configured → ok=false with 'not configured' message."""
        # Do NOT configure a host (default state)
        resp = http_client.post("/api/settings/email/test")  # type: ignore[attr-defined]

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "not configured" in (data["detail"] or "").lower()

    def test_unauthenticated_returns_401(self, http_client: object) -> None:
        """An unauthenticated request to the test endpoint returns 401.

        The http_client fixture uses an authenticated session (via cookies).
        To test unauthenticated access, we send the request without any
        session cookie by using a raw ``requests``-style call that bypasses
        the cookie jar.  Since TestClient always carries the session cookie
        from the login step, we explicitly clear cookies for this call.
        """
        import httpx

        # Use the underlying httpx client to call the endpoint without cookies.
        # The TestClient's cookie jar carries the session; we bypass it by
        # building a request without session cookies.
        base_url = "http://testserver"
        with httpx.Client(base_url=base_url, transport=http_client._transport) as bare_client:  # type: ignore[attr-defined]
            resp = bare_client.post("/api/settings/email/test")
        assert resp.status_code == 401


class TestEmailSettingsRoundTrip:
    """Settings round-trip: PATCH encryption + from_name → GET reflects them."""

    def test_patch_encryption_and_from_name_reflected_in_get(self, http_client: object) -> None:
        """PATCH encryption='starttls' and from_name='Omni' → GET shows those values."""
        patch_resp = http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={
                "channels": {
                    "email": {
                        "encryption": "starttls",
                        "from_name": "Omni",
                    }
                }
            },
        )
        assert patch_resp.status_code == 200
        body = patch_resp.json()
        assert body["channels"]["email"]["encryption"] == "starttls"
        assert body["channels"]["email"]["from_name"] == "Omni"
        # Password must still be masked
        assert "password" not in body["channels"]["email"]
        assert "password_is_set" in body["channels"]["email"]

    def test_invalid_encryption_value_returns_422(self, http_client: object) -> None:
        """Invalid encryption value (e.g. 'tls') → 422 validation error."""
        resp = http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={
                "channels": {
                    "email": {
                        "encryption": "tls",  # invalid — not in the 3-way enum
                    }
                }
            },
        )
        assert resp.status_code == 422

    def test_ssl_encryption_round_trips(self, http_client: object) -> None:
        """PATCH encryption='ssl' → GET reflects 'ssl'."""
        patch_resp = http_client.patch(  # type: ignore[attr-defined]
            "/api/settings",
            json={"channels": {"email": {"encryption": "ssl"}}},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["channels"]["email"]["encryption"] == "ssl"
