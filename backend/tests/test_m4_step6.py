"""M4 Step 6 tests: in-app notifications inbox API.

Required coverage (M4.md §5 + §9 Step 6 + §10 Step 6):

NotificationRepository new methods:
- list_for_user: returns newest-first; unread_only filter works; limit works
- unread_count: counts unread rows; decrements after mark_read
- mark_read: stamps read_at on correct row; idempotent (preserves original read_at)
- mark_read: returns None for wrong user_id or non-existent id
- mark_all_read: marks all unread rows for a user; returns affected count; does not
  touch another user's rows

NotificationService:
- list_for_user: params returned as parsed dict (not raw JSON string)
- mark_read: AppError(notification.not_found, 404) when repo returns None
- mark_all_read: returns affected count

HTTP API:
- GET /notifications: 200 newest-first; unread_only filter; limit query param
- GET /notifications: 401 when unauthenticated
- GET /notifications/unread-count: badge count; decrements after mark
- GET /notifications/unread-count: 401 when unauthenticated
- POST /notifications/{id}/read: marks read and returns NotificationResponse
- POST /notifications/{id}/read: 404 notification.not_found when id not found
- POST /notifications/{id}/read: 404 when id belongs to another user
- POST /notifications/{id}/read: idempotent (second call preserves original read_at)
- POST /notifications/read-all: marks all unread; returns {marked: N}
- POST /notifications/read-all: 401 when unauthenticated

Per-user isolation:
- list and count are scoped to the current user; another user's rows invisible
- mark_read on another user's notification returns 404

NotificationResponse schema:
- params is a parsed dict (not a JSON string)
- no server-rendered text fields

Error code:
- NOTIFICATION_NOT_FOUND = "notification.not_found" is registered in ErrorCode
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as prior M4 steps)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step6_")
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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step6")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient logged in as the primary admin user."""
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


def _seed_users_and_household(db: Session) -> tuple[Any, Any, Any]:
    """Seed Household + two users (user_a, user_b).  Returns (household, user_a, user_b)."""
    from app.auth.passwords import hash_password
    from app.models.household import Household
    from app.models.user import User

    hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
    db.add(hh)
    db.flush()

    user_a = User(email="a@example.com", password_hash=hash_password("pass"), is_active=True)
    user_b = User(email="b@example.com", password_hash=hash_password("pass"), is_active=True)
    db.add(user_a)
    db.add(user_b)
    db.flush()
    db.commit()
    return hh, user_a, user_b


def _insert_notification(
    db: Session,
    user_id: int,
    *,
    source: str = "best_before",
    subject_type: str = "instance",
    subject_id: int = 1,
    message_code: str = "reminder.best_before",
    params: dict[str, Any] | None = None,
    dedup_key: str | None = None,
    read_at: datetime | None = None,
) -> Any:
    """Insert a Notification row directly and return it."""
    from app.models.notification import Notification

    key = dedup_key or f"test:{user_id}:{source}:{subject_id}:{id(object())}"
    params_text = json.dumps(params) if params is not None else None
    n = Notification(
        user_id=user_id,
        source=source,
        subject_type=subject_type,
        subject_id=subject_id,
        dedup_key=key,
        message_code=message_code,
        params=params_text,
        read_at=read_at,
    )
    db.add(n)
    db.flush()
    return n


# ---------------------------------------------------------------------------
# 1. NotificationRepository new methods
# ---------------------------------------------------------------------------


class TestNotificationRepositoryListAndCount:
    """Tests for list_for_user, unread_count, mark_read, mark_all_read."""

    def test_list_for_user_empty(self, db_session: Session) -> None:
        """list_for_user returns [] when the user has no notifications."""
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        # Fetch the seeded user
        from app.models.user import User

        user = db_session.query(User).filter_by(email="a@example.com").one()
        repo = NotificationRepository(db_session)
        assert repo.list_for_user(user.id) == []

    def test_list_for_user_newest_first(self, db_session: Session) -> None:
        """list_for_user returns rows newest-first (created_at desc, id desc)."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        n1 = _insert_notification(db_session, user.id, dedup_key="k1")
        n2 = _insert_notification(db_session, user.id, dedup_key="k2")
        n3 = _insert_notification(db_session, user.id, dedup_key="k3")
        db_session.commit()

        repo = NotificationRepository(db_session)
        results = repo.list_for_user(user.id)
        ids = [r.id for r in results]
        # n3 was inserted last → should come first; exact id ordering depends
        # on assignment order (newer id = higher), so newest-first = desc id here.
        assert ids == sorted([n1.id, n2.id, n3.id], reverse=True)

    def test_list_for_user_unread_only_filter(self, db_session: Session) -> None:
        """unread_only=True excludes read rows."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        now = datetime(2026, 1, 1, 12, 0, 0)
        _insert_notification(db_session, user.id, dedup_key="read", read_at=now)
        n_unread = _insert_notification(db_session, user.id, dedup_key="unread")
        db_session.commit()

        repo = NotificationRepository(db_session)
        results = repo.list_for_user(user.id, unread_only=True)
        assert [r.id for r in results] == [n_unread.id]

    def test_list_for_user_limit(self, db_session: Session) -> None:
        """limit restricts the number of rows returned."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        for i in range(5):
            _insert_notification(db_session, user.id, dedup_key=f"kl{i}")
        db_session.commit()

        repo = NotificationRepository(db_session)
        assert len(repo.list_for_user(user.id, limit=3)) == 3
        assert len(repo.list_for_user(user.id, limit=10)) == 5

    def test_unread_count_zero(self, db_session: Session) -> None:
        """unread_count returns 0 when all are read or there are none."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        repo = NotificationRepository(db_session)
        assert repo.unread_count(user.id) == 0

    def test_unread_count_correct(self, db_session: Session) -> None:
        """unread_count counts only unread rows."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        now = datetime(2026, 1, 1, 12, 0, 0)
        _insert_notification(db_session, user.id, dedup_key="r1", read_at=now)
        _insert_notification(db_session, user.id, dedup_key="u1")
        _insert_notification(db_session, user.id, dedup_key="u2")
        db_session.commit()

        repo = NotificationRepository(db_session)
        assert repo.unread_count(user.id) == 2

    def test_mark_read_stamps_read_at(self, db_session: Session) -> None:
        """mark_read sets read_at and returns the notification."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        n = _insert_notification(db_session, user.id, dedup_key="mr1")
        db_session.commit()

        assert n.read_at is None
        repo = NotificationRepository(db_session)
        result = repo.mark_read(user.id, n.id)
        assert result is not None
        assert result.id == n.id
        assert result.read_at is not None

    def test_mark_read_idempotent_preserves_timestamp(self, db_session: Session) -> None:
        """mark_read on an already-read notification preserves the original read_at."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        original_ts = datetime(2025, 6, 1, 10, 0, 0)
        n = _insert_notification(db_session, user.id, dedup_key="mr2", read_at=original_ts)
        db_session.commit()

        repo = NotificationRepository(db_session)
        result = repo.mark_read(user.id, n.id)
        assert result is not None
        # Original read_at must be preserved (not refreshed to now).
        assert result.read_at is not None
        # Compare as naive datetimes (SQLite strips tz info on round-trip).
        result_naive = (
            result.read_at.replace(tzinfo=None) if result.read_at.tzinfo else result.read_at
        )
        assert result_naive == original_ts

    def test_mark_read_wrong_user_returns_none(self, db_session: Session) -> None:
        """mark_read returns None when the notification belongs to a different user."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        n = _insert_notification(db_session, user_a.id, dedup_key="foreign")
        db_session.commit()

        repo = NotificationRepository(db_session)
        # user_b tries to mark user_a's notification → None
        result = repo.mark_read(user_b.id, n.id)
        assert result is None
        # The row must still be unread
        db_session.refresh(n)
        assert n.read_at is None

    def test_mark_read_nonexistent_id_returns_none(self, db_session: Session) -> None:
        """mark_read returns None for a non-existent notification id."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        repo = NotificationRepository(db_session)
        result = repo.mark_read(user.id, 99999)
        assert result is None

    def test_mark_all_read_returns_affected_count(self, db_session: Session) -> None:
        """mark_all_read returns the number of rows actually updated."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        now = datetime(2026, 1, 1, 12, 0, 0)
        _insert_notification(db_session, user.id, dedup_key="ar_read", read_at=now)
        _insert_notification(db_session, user.id, dedup_key="ar_u1")
        _insert_notification(db_session, user.id, dedup_key="ar_u2")
        db_session.commit()

        repo = NotificationRepository(db_session)
        count = repo.mark_all_read(user.id)
        assert count == 2

    def test_mark_all_read_zero_when_all_read(self, db_session: Session) -> None:
        """mark_all_read returns 0 when there are no unread rows."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        repo = NotificationRepository(db_session)
        assert repo.mark_all_read(user.id) == 0

    def test_mark_all_read_does_not_affect_other_user(self, db_session: Session) -> None:
        """mark_all_read for user_a leaves user_b's notifications untouched."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        nb = _insert_notification(db_session, user_b.id, dedup_key="b_unread")
        _insert_notification(db_session, user_a.id, dedup_key="a_unread")
        db_session.commit()

        repo = NotificationRepository(db_session)
        count = repo.mark_all_read(user_a.id)
        assert count == 1

        # user_b's notification must still be unread
        db_session.refresh(nb)
        assert nb.read_at is None

    def test_unread_count_decrements_after_mark_read(self, db_session: Session) -> None:
        """unread_count decreases after mark_read is called."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        n = _insert_notification(db_session, user.id, dedup_key="dec1")
        db_session.commit()

        repo = NotificationRepository(db_session)
        assert repo.unread_count(user.id) == 1
        repo.mark_read(user.id, n.id)
        assert repo.unread_count(user.id) == 0


# ---------------------------------------------------------------------------
# 2. Per-user isolation (repository level)
# ---------------------------------------------------------------------------


class TestPerUserIsolation:
    """list_for_user and unread_count must never see another user's rows."""

    def test_list_scoped_to_user(self, db_session: Session) -> None:
        """user_a's list does not include user_b's notifications."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        na = _insert_notification(db_session, user_a.id, dedup_key="pa")
        _insert_notification(db_session, user_b.id, dedup_key="pb")
        db_session.commit()

        repo = NotificationRepository(db_session)
        results_a = repo.list_for_user(user_a.id)
        assert [r.id for r in results_a] == [na.id]
        results_b = repo.list_for_user(user_b.id)
        assert len(results_b) == 1
        assert results_b[0].user_id == user_b.id

    def test_count_scoped_to_user(self, db_session: Session) -> None:
        """unread_count returns independent counts per user."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        _insert_notification(db_session, user_a.id, dedup_key="ca1")
        _insert_notification(db_session, user_a.id, dedup_key="ca2")
        _insert_notification(db_session, user_b.id, dedup_key="cb1")
        db_session.commit()

        repo = NotificationRepository(db_session)
        assert repo.unread_count(user_a.id) == 2
        assert repo.unread_count(user_b.id) == 1


# ---------------------------------------------------------------------------
# 3. NotificationService
# ---------------------------------------------------------------------------


class TestNotificationService:
    """Tests for NotificationService param deserialization and error raising."""

    def test_list_for_user_params_deserialized(self, db_session: Session) -> None:
        """Service.list_for_user returns (notification, dict) — not raw JSON string."""
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        params = {"name": "Milk", "days_remaining": 2}
        _insert_notification(db_session, user.id, dedup_key="sp1", params=params)
        db_session.commit()

        svc = NotificationService(db_session)
        pairs = svc.list_for_user(user.id)
        assert len(pairs) == 1
        _n, parsed = pairs[0]
        assert isinstance(parsed, dict)
        assert parsed["name"] == "Milk"
        assert parsed["days_remaining"] == 2

    def test_list_for_user_null_params_returns_none(self, db_session: Session) -> None:
        """Service returns None for params when the column is NULL."""
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        _insert_notification(db_session, user.id, dedup_key="sp_null", params=None)
        db_session.commit()

        svc = NotificationService(db_session)
        pairs = svc.list_for_user(user.id)
        _n, parsed = pairs[0]
        assert parsed is None

    def test_mark_read_raises_404_for_foreign_id(self, db_session: Session) -> None:
        """mark_read raises AppError(notification.not_found, 404) for another user's id."""
        from app.core.errors import AppError, ErrorCode
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        na = _insert_notification(db_session, user_a.id, dedup_key="sv_foreign")
        db_session.commit()

        svc = NotificationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.mark_read(user_b.id, na.id)

        err = exc_info.value
        assert err.code == ErrorCode.NOTIFICATION_NOT_FOUND
        assert err.status_code == 404
        assert err.params is not None
        assert err.params["id"] == na.id

    def test_mark_read_raises_404_for_nonexistent_id(self, db_session: Session) -> None:
        """mark_read raises AppError(notification.not_found, 404) for a missing id."""
        from app.core.errors import AppError, ErrorCode
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        svc = NotificationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.mark_read(user.id, 99999)

        err = exc_info.value
        assert err.code == ErrorCode.NOTIFICATION_NOT_FOUND
        assert err.status_code == 404

    def test_mark_all_read_returns_affected_count(self, db_session: Session) -> None:
        """mark_all_read returns the number of rows updated."""
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        _insert_notification(db_session, user.id, dedup_key="sv_mar1")
        _insert_notification(db_session, user.id, dedup_key="sv_mar2")
        db_session.commit()

        svc = NotificationService(db_session)
        count = svc.mark_all_read(user.id)
        assert count == 2


# ---------------------------------------------------------------------------
# 4. Error code registration
# ---------------------------------------------------------------------------


class TestErrorCodeRegistration:
    def test_notification_not_found_registered(self) -> None:
        """NOTIFICATION_NOT_FOUND constant has the expected string value."""
        from app.core.errors import ErrorCode

        assert ErrorCode.NOTIFICATION_NOT_FOUND == "notification.not_found"

    def test_notification_not_found_has_default_message(self) -> None:
        """AppError with NOTIFICATION_NOT_FOUND resolves to a human dev message."""
        from app.core.errors import AppError, ErrorCode

        err = AppError(ErrorCode.NOTIFICATION_NOT_FOUND, status_code=404)
        # The message must not fall back to the bare code string.
        assert err.message != ErrorCode.NOTIFICATION_NOT_FOUND
        assert len(err.message) > 10


# ---------------------------------------------------------------------------
# 5. HTTP API tests
# ---------------------------------------------------------------------------


class TestNotificationsHTTPAPI:
    """Integration tests for all four notification endpoints."""

    # ---- helpers ----------------------------------------------------------------

    def _seed_notifications(
        self, client: Any, engine: Any, user_id: int, count: int = 3
    ) -> list[int]:
        """Directly insert `count` unread notifications for user_id.  Returns ids."""
        from sqlalchemy.orm import sessionmaker as sm_maker

        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            ids = []
            for i in range(count):
                from app.models.notification import Notification as N

                params_data = {"name": f"Item {i}", "days_remaining": i}
                n = N(
                    user_id=user_id,
                    source="best_before",
                    subject_type="instance",
                    subject_id=i + 1,
                    dedup_key=f"http_test:{user_id}:{i}",
                    message_code="reminder.best_before",
                    params=json.dumps(params_data),
                )
                db.add(n)
                db.flush()
                ids.append(n.id)
            db.commit()
            return ids
        finally:
            db.close()

    def _get_admin_user_id(self, engine: Any) -> int:
        """Return the id of the seeded admin user."""
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.models.user import User

        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            return db.query(User).filter_by(email="admin@example.com").one().id
        finally:
            db.close()

    # ---- GET /notifications -------------------------------------------------------

    def test_list_returns_200_and_newest_first(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        ids = self._seed_notifications(http_client, engine, user_id, count=3)

        resp = http_client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        returned_ids = [d["id"] for d in data]
        # Newest-first: highest id first.
        assert returned_ids == sorted(ids, reverse=True)

    def test_list_unread_only_filter(self, http_client: Any, temp_db: Path) -> None:
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)

        # Insert one read and one unread.
        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.models.notification import Notification as N

            read_n = N(
                user_id=user_id,
                source="best_before",
                subject_type="instance",
                subject_id=1,
                dedup_key="uo_read",
                message_code="reminder.best_before",
                read_at=datetime(2026, 1, 1, 0, 0, 0),
            )
            unread_n = N(
                user_id=user_id,
                source="best_before",
                subject_type="instance",
                subject_id=2,
                dedup_key="uo_unread",
                message_code="reminder.best_before",
            )
            db.add(read_n)
            db.add(unread_n)
            db.commit()
            unread_id = unread_n.id
        finally:
            db.close()

        resp = http_client.get("/api/notifications?unread_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == unread_id
        assert data[0]["read_at"] is None

    def test_list_limit_param(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(http_client, engine, user_id, count=5)

        resp = http_client.get("/api/notifications?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_params_returned_as_dict(self, http_client: Any, temp_db: Path) -> None:
        """params field in response is a JSON object (dict), not a string."""
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(http_client, engine, user_id, count=1)

        resp = http_client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        params = data[0]["params"]
        assert isinstance(params, dict)
        assert "name" in params

    def test_list_no_server_text_field(self, http_client: Any, temp_db: Path) -> None:
        """NotificationResponse carries message_code + params but no server-rendered text."""
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(http_client, engine, user_id, count=1)

        resp = http_client.get("/api/notifications")
        data = resp.json()[0]
        # Required fields
        assert "message_code" in data
        assert "params" in data
        # Must NOT contain server-rendered text
        assert "message" not in data
        assert "text" not in data

    def test_list_401_when_unauthenticated(self, http_client: Any) -> None:
        # Make a fresh client without a session cookie.
        from fastapi.testclient import TestClient

        app = http_client.app
        with TestClient(app, raise_server_exceptions=True) as anon_client:
            resp = anon_client.get("/api/notifications")
        assert resp.status_code == 401

    # ---- GET /notifications/unread-count -----------------------------------------

    def test_unread_count_badge(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(http_client, engine, user_id, count=3)

        resp = http_client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json() == {"count": 3}

    def test_unread_count_zero(self, http_client: Any) -> None:
        resp = http_client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0}

    def test_unread_count_401_when_unauthenticated(self, http_client: Any) -> None:
        from fastapi.testclient import TestClient

        app = http_client.app
        with TestClient(app, raise_server_exceptions=True) as anon_client:
            resp = anon_client.get("/api/notifications/unread-count")
        assert resp.status_code == 401

    # ---- POST /notifications/{id}/read -------------------------------------------

    def test_mark_read_returns_updated_notification(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        (nid,) = self._seed_notifications(http_client, engine, user_id, count=1)

        resp = http_client.post(f"/api/notifications/{nid}/read")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == nid
        assert data["read_at"] is not None

    def test_mark_read_idempotent(self, http_client: Any, temp_db: Path) -> None:
        """Second mark-read preserves the first read_at timestamp."""
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        (nid,) = self._seed_notifications(http_client, engine, user_id, count=1)

        resp1 = http_client.post(f"/api/notifications/{nid}/read")
        assert resp1.status_code == 200
        read_at_1 = resp1.json()["read_at"]

        resp2 = http_client.post(f"/api/notifications/{nid}/read")
        assert resp2.status_code == 200
        read_at_2 = resp2.json()["read_at"]

        # Normalise the UTC suffix before comparing: the first response carries the
        # datetime straight from the Python ``datetime.now(tz=UTC)`` object (aware,
        # serialised with a trailing "Z"), while the second response re-reads the
        # value from SQLite which stores timezone-naive text (no "Z").  The actual
        # point-in-time is identical; stripping the suffix lets us assert equality
        # of the value without coupling to SQLite's naive-datetime round-trip.
        assert read_at_1.rstrip("Z") == read_at_2.rstrip("Z")

    def test_mark_read_404_nonexistent(self, http_client: Any) -> None:
        resp = http_client.post("/api/notifications/99999/read")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "notification.not_found"

    def test_mark_read_decrements_unread_count(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        ids = self._seed_notifications(http_client, engine, user_id, count=2)

        before = http_client.get("/api/notifications/unread-count").json()["count"]
        assert before == 2

        http_client.post(f"/api/notifications/{ids[0]}/read")

        after = http_client.get("/api/notifications/unread-count").json()["count"]
        assert after == 1

    # ---- POST /notifications/read-all --------------------------------------------

    def test_read_all_marks_all_unread(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(http_client, engine, user_id, count=3)

        resp = http_client.post("/api/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["marked"] == 3

        count_resp = http_client.get("/api/notifications/unread-count")
        assert count_resp.json()["count"] == 0

    def test_read_all_returns_zero_when_nothing_unread(self, http_client: Any) -> None:
        resp = http_client.post("/api/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["marked"] == 0

    def test_read_all_401_when_unauthenticated(self, http_client: Any) -> None:
        from fastapi.testclient import TestClient

        app = http_client.app
        with TestClient(app, raise_server_exceptions=True) as anon_client:
            resp = anon_client.post("/api/notifications/read-all")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. Per-user isolation — HTTP level (two-user scenario)
# ---------------------------------------------------------------------------


class TestPerUserIsolationHTTP:
    """Two-user isolation at the HTTP level.

    The http_client fixture logs in as 'admin@example.com'.  We create a second
    user and give them notifications; the admin must not see them.
    """

    def _create_second_user_and_notifications(
        self, engine: Any, count: int = 2
    ) -> tuple[int, list[int]]:
        """Create 'user2@example.com' and seed notifications for them."""
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.auth.passwords import hash_password
        from app.models.notification import Notification as N
        from app.models.user import User

        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            u2 = User(
                email="user2@example.com",
                password_hash=hash_password("pass"),
                is_active=True,
            )
            db.add(u2)
            db.flush()
            ids = []
            for i in range(count):
                n = N(
                    user_id=u2.id,
                    source="best_before",
                    subject_type="instance",
                    subject_id=i + 1,
                    dedup_key=f"u2:{i}",
                    message_code="reminder.best_before",
                )
                db.add(n)
                db.flush()
                ids.append(n.id)
            db.commit()
            return u2.id, ids
        finally:
            db.close()

    def test_list_only_sees_own_notifications(self, http_client: Any, temp_db: Path) -> None:
        """Admin's GET /notifications does not include user2's notifications."""

        from app.db.base import get_engine

        engine = get_engine()
        _u2_id, u2_ids = self._create_second_user_and_notifications(engine, count=2)

        # Admin has no notifications of their own.
        resp = http_client.get("/api/notifications")
        assert resp.status_code == 200
        returned_ids = [d["id"] for d in resp.json()]
        for u2_id in u2_ids:
            assert u2_id not in returned_ids

    def test_unread_count_only_counts_own(self, http_client: Any, temp_db: Path) -> None:
        """Admin's /unread-count does not include user2's unread count."""
        from app.db.base import get_engine

        engine = get_engine()
        self._create_second_user_and_notifications(engine, count=3)

        resp = http_client.get("/api/notifications/unread-count")
        assert resp.json()["count"] == 0

    def test_mark_read_on_other_users_notification_returns_404(
        self, http_client: Any, temp_db: Path
    ) -> None:
        """Admin POSTing /notifications/{u2_id}/read returns 404 notification.not_found."""
        from app.db.base import get_engine

        engine = get_engine()
        _u2_id, u2_ids = self._create_second_user_and_notifications(engine, count=1)

        resp = http_client.post(f"/api/notifications/{u2_ids[0]}/read")
        assert resp.status_code == 404
        assert resp.json()["code"] == "notification.not_found"

    def test_read_all_does_not_affect_other_user(self, http_client: Any, temp_db: Path) -> None:
        """Admin's /read-all does not mark user2's notifications as read."""
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.db.base import get_engine

        engine = get_engine()
        _u2_id, u2_ids = self._create_second_user_and_notifications(engine, count=2)

        # Admin marks all — should affect 0 (admin has no notifications).
        resp = http_client.post("/api/notifications/read-all")
        assert resp.json()["marked"] == 0

        # Verify user2's notifications are still unread.
        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.models.notification import Notification as N

            for nid in u2_ids:
                row = db.get(N, nid)
                assert row is not None
                assert row.read_at is None
        finally:
            db.close()
