"""Notification hygiene hardening round — Step 1: backend soft-dismiss.

Required coverage (review-notes/notif-hygiene-design.md §3.7):

NotificationRepository:
- dismiss: stamps dismissed_at on the correct row; idempotent (preserves the
  original dismissed_at on a second call); returns None for wrong user_id or
  non-existent id.
- dismiss_all: soft-dismisses every currently-visible row for a user; returns
  the affected count; second call returns 0; does not touch another user's
  rows.
- list_for_user: excludes dismissed rows (with and without unread_only).
- unread_count: excludes dismissed rows, including an *unread* dismissed row.

NotificationService:
- dismiss: AppError(notification.not_found, 404) when repo returns None;
  returns (row, parsed_params) on success.
- dismiss_all: returns the affected count.

HTTP API:
- POST /notifications/{id}/dismiss: 200 + row hidden from subsequent
  GET /notifications + unread-count drops; idempotent; 404 for missing /
  foreign id; 401 unauthenticated.
- POST /notifications/dismiss-all: {dismissed: N}; hides all rows; second
  call returns {dismissed: 0}; only affects the caller's own rows; 401
  unauthenticated.

INVARIANT tests (critical — see design doc §1):
- dedup anchor preserved: a dismissed row still anchors create_if_absent, so
  a rescan with the same (user_id, dedup_key) does NOT create a duplicate.
- low-stock episode preserved: a dismissed opener (offset_days=0,
  resolved_at=None) is still returned by open_low_stock_opener, so a rescan
  would not open a duplicate episode.
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as test_m4_step6.py)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_notif_dismiss_")
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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-notif-dismiss")
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
    read_at: Any = None,
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
# 1. NotificationRepository.dismiss / dismiss_all
# ---------------------------------------------------------------------------


class TestRepositoryDismiss:
    def test_dismiss_stamps_dismissed_at(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        n = _insert_notification(db_session, user.id, dedup_key="d1")
        db_session.commit()

        assert n.dismissed_at is None
        repo = NotificationRepository(db_session)
        result = repo.dismiss(user.id, n.id)
        assert result is not None
        assert result.id == n.id
        assert result.dismissed_at is not None

    def test_dismiss_idempotent_preserves_timestamp(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        n = _insert_notification(db_session, user.id, dedup_key="d2")
        db_session.commit()

        repo = NotificationRepository(db_session)
        first = repo.dismiss(user.id, n.id)
        assert first is not None
        first_ts = first.dismissed_at

        second = repo.dismiss(user.id, n.id)
        assert second is not None
        assert second.dismissed_at == first_ts

    def test_dismiss_wrong_user_returns_none(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        n = _insert_notification(db_session, user_a.id, dedup_key="d3")
        db_session.commit()

        repo = NotificationRepository(db_session)
        result = repo.dismiss(user_b.id, n.id)
        assert result is None
        db_session.refresh(n)
        assert n.dismissed_at is None

    def test_dismiss_nonexistent_id_returns_none(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        repo = NotificationRepository(db_session)
        assert repo.dismiss(user.id, 99999) is None

    def test_dismiss_all_returns_affected_count(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        _insert_notification(db_session, user.id, dedup_key="da1")
        _insert_notification(db_session, user.id, dedup_key="da2")
        db_session.commit()

        repo = NotificationRepository(db_session)
        count = repo.dismiss_all(user.id)
        assert count == 2

    def test_dismiss_all_second_call_returns_zero(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        _insert_notification(db_session, user.id, dedup_key="da3")
        db_session.commit()

        repo = NotificationRepository(db_session)
        assert repo.dismiss_all(user.id) == 1
        assert repo.dismiss_all(user.id) == 0

    def test_dismiss_all_zero_when_nothing_visible(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        repo = NotificationRepository(db_session)
        assert repo.dismiss_all(user.id) == 0

    def test_dismiss_all_does_not_affect_other_user(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        nb = _insert_notification(db_session, user_b.id, dedup_key="da_b")
        _insert_notification(db_session, user_a.id, dedup_key="da_a")
        db_session.commit()

        repo = NotificationRepository(db_session)
        count = repo.dismiss_all(user_a.id)
        assert count == 1

        db_session.refresh(nb)
        assert nb.dismissed_at is None


# ---------------------------------------------------------------------------
# 2. list_for_user / unread_count exclude dismissed rows
# ---------------------------------------------------------------------------


class TestListAndCountExcludeDismissed:
    def test_list_for_user_excludes_dismissed(self, db_session: Session) -> None:
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        visible = _insert_notification(db_session, user.id, dedup_key="le1")
        dismissed = _insert_notification(db_session, user.id, dedup_key="le2")
        db_session.commit()

        repo = NotificationRepository(db_session)
        repo.dismiss(user.id, dismissed.id)

        results = repo.list_for_user(user.id)
        assert [r.id for r in results] == [visible.id]

    def test_list_for_user_unread_only_still_excludes_dismissed(self, db_session: Session) -> None:
        from datetime import UTC, datetime

        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        # unread + dismissed: must not appear even with unread_only=True
        unread_dismissed = _insert_notification(db_session, user.id, dedup_key="leu1")
        # read + not dismissed: must not appear with unread_only=True (unrelated filter)
        _insert_notification(
            db_session,
            user.id,
            dedup_key="leu2",
            read_at=datetime.now(tz=UTC),
        )
        # unread + not dismissed: the only row that should show up
        unread_visible = _insert_notification(db_session, user.id, dedup_key="leu3")
        db_session.commit()

        repo = NotificationRepository(db_session)
        repo.dismiss(user.id, unread_dismissed.id)

        results = repo.list_for_user(user.id, unread_only=True)
        assert [r.id for r in results] == [unread_visible.id]

    def test_unread_count_excludes_dismissed_row_even_if_unread(self, db_session: Session) -> None:
        """A dismissed row never counts toward the badge, read or not (design §3.3)."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        n_unread_dismissed = _insert_notification(db_session, user.id, dedup_key="uc1")
        _insert_notification(db_session, user.id, dedup_key="uc2")
        db_session.commit()

        repo = NotificationRepository(db_session)
        assert repo.unread_count(user.id) == 2

        repo.dismiss(user.id, n_unread_dismissed.id)
        assert repo.unread_count(user.id) == 1


# ---------------------------------------------------------------------------
# 3. NotificationService.dismiss / dismiss_all
# ---------------------------------------------------------------------------


class TestServiceDismiss:
    def test_dismiss_returns_row_and_parsed_params(self, db_session: Session) -> None:
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        params = {"name": "Milk"}
        n = _insert_notification(db_session, user.id, dedup_key="sd1", params=params)
        db_session.commit()

        svc = NotificationService(db_session)
        notification, parsed = svc.dismiss(user.id, n.id)
        assert notification.dismissed_at is not None
        assert parsed == params

    def test_dismiss_raises_404_for_nonexistent_id(self, db_session: Session) -> None:
        from app.core.errors import AppError, ErrorCode
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        svc = NotificationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.dismiss(user.id, 99999)

        err = exc_info.value
        assert err.code == ErrorCode.NOTIFICATION_NOT_FOUND
        assert err.status_code == 404
        assert err.params is not None
        assert err.params["id"] == 99999

    def test_dismiss_raises_404_for_foreign_id(self, db_session: Session) -> None:
        from app.core.errors import AppError, ErrorCode
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user_a = db_session.query(User).filter_by(email="a@example.com").one()
        user_b = db_session.query(User).filter_by(email="b@example.com").one()

        na = _insert_notification(db_session, user_a.id, dedup_key="sd_foreign")
        db_session.commit()

        svc = NotificationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.dismiss(user_b.id, na.id)
        assert exc_info.value.code == ErrorCode.NOTIFICATION_NOT_FOUND

    def test_dismiss_all_returns_affected_count(self, db_session: Session) -> None:
        from app.models.user import User
        from app.services.notification import NotificationService

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()

        _insert_notification(db_session, user.id, dedup_key="sda1")
        _insert_notification(db_session, user.id, dedup_key="sda2")
        db_session.commit()

        svc = NotificationService(db_session)
        assert svc.dismiss_all(user.id) == 2


# ---------------------------------------------------------------------------
# 4. INVARIANT tests (critical — design doc §1)
# ---------------------------------------------------------------------------


class TestDismissInvariants:
    """Dismiss must never be visible to the dedup or low-stock episode queries."""

    def test_invariant_dedup_anchor_preserved_after_dismiss(self, db_session: Session) -> None:
        """A dismissed row still anchors create_if_absent -- no duplicate is created."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        repo = NotificationRepository(db_session)

        first, created1 = repo.create_if_absent(
            user_id=user.id,
            source="best_before",
            subject_type="instance",
            subject_id=42,
            dedup_key="inv:dedup:1",
            message_code="reminder.best_before",
        )
        assert created1 is True
        db_session.commit()

        dismissed = repo.dismiss(user.id, first.id)
        assert dismissed is not None
        assert dismissed.dismissed_at is not None
        db_session.commit()

        # A rescan with the SAME dedup key must NOT create a new row -- the
        # dismissed row still anchors the dedup lookup.
        second, created2 = repo.create_if_absent(
            user_id=user.id,
            source="best_before",
            subject_type="instance",
            subject_id=42,
            dedup_key="inv:dedup:1",
            message_code="reminder.best_before",
        )
        assert created2 is False
        assert second.id == first.id

        # Exactly one row exists for this dedup key.
        from sqlalchemy import func, select

        from app.models.notification import Notification

        count = db_session.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.dedup_key == "inv:dedup:1",
            )
        ).scalar_one()
        assert count == 1

    def test_invariant_low_stock_episode_preserved_after_dismiss(self, db_session: Session) -> None:
        """A dismissed low-stock opener is still returned by open_low_stock_opener."""
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        _seed_users_and_household(db_session)
        user = db_session.query(User).filter_by(email="a@example.com").one()
        repo = NotificationRepository(db_session)
        today = date(2026, 7, 1)
        definition_id = 7

        opener, created = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=definition_id,
            dedup_key=f"low_stock:u{user.id}:d{definition_id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert created is True
        db_session.commit()

        dismissed = repo.dismiss(user.id, opener.id)
        assert dismissed is not None
        db_session.commit()

        # The episode must still be considered "open" -- a rescan should not
        # open a duplicate episode for this definition.
        found = repo.open_low_stock_opener(user.id, definition_id)
        assert found is not None
        assert found.id == opener.id
        assert found.resolved_at is None

        # It must also still show up in the bulk "all open openers" query.
        all_openers = repo.open_low_stock_openers(user.id)
        assert opener.id in [o.id for o in all_openers]

        # And it must NOT be visible in the inbox listing (soft-dismiss did
        # its job on the inbox-facing read).
        visible = repo.list_for_user(user.id)
        assert opener.id not in [n.id for n in visible]


# ---------------------------------------------------------------------------
# 5. HTTP API tests
# ---------------------------------------------------------------------------


class TestDismissHTTPAPI:
    def _seed_notifications(self, engine: Any, user_id: int, count: int = 3) -> list[int]:
        """Directly insert `count` unread notifications for user_id.  Returns ids."""
        from sqlalchemy.orm import sessionmaker as sm_maker

        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            ids = []
            for i in range(count):
                from app.models.notification import Notification as N

                n = N(
                    user_id=user_id,
                    source="best_before",
                    subject_type="instance",
                    subject_id=i + 1,
                    dedup_key=f"http_dismiss_test:{user_id}:{i}",
                    message_code="reminder.best_before",
                    params=json.dumps({"name": f"Item {i}"}),
                )
                db.add(n)
                db.flush()
                ids.append(n.id)
            db.commit()
            return ids
        finally:
            db.close()

    def _get_admin_user_id(self, engine: Any) -> int:
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.models.user import User

        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            return db.query(User).filter_by(email="admin@example.com").one().id
        finally:
            db.close()

    # ---- POST /notifications/{id}/dismiss -----------------------------------

    def test_dismiss_returns_200_and_hides_row(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        (nid,) = self._seed_notifications(engine, user_id, count=1)

        resp = http_client.post(f"/api/notifications/{nid}/dismiss")
        assert resp.status_code == 200
        assert resp.json()["id"] == nid

        list_resp = http_client.get("/api/notifications")
        assert list_resp.json() == []

    def test_dismiss_decrements_unread_count(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        ids = self._seed_notifications(engine, user_id, count=2)

        before = http_client.get("/api/notifications/unread-count").json()["count"]
        assert before == 2

        http_client.post(f"/api/notifications/{ids[0]}/dismiss")

        after = http_client.get("/api/notifications/unread-count").json()["count"]
        assert after == 1

    def test_dismiss_idempotent(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        (nid,) = self._seed_notifications(engine, user_id, count=1)

        resp1 = http_client.post(f"/api/notifications/{nid}/dismiss")
        assert resp1.status_code == 200
        resp2 = http_client.post(f"/api/notifications/{nid}/dismiss")
        assert resp2.status_code == 200

    def test_dismiss_404_nonexistent(self, http_client: Any) -> None:
        resp = http_client.post("/api/notifications/99999/dismiss")
        assert resp.status_code == 404
        assert resp.json()["code"] == "notification.not_found"

    def test_dismiss_404_for_other_users_notification(
        self, http_client: Any, temp_db: Path
    ) -> None:
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.auth.passwords import hash_password
        from app.db.base import get_engine
        from app.models.notification import Notification as N
        from app.models.user import User

        engine = get_engine()
        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            u2 = User(
                email="user2@example.com", password_hash=hash_password("pass"), is_active=True
            )
            db.add(u2)
            db.flush()
            n = N(
                user_id=u2.id,
                source="best_before",
                subject_type="instance",
                subject_id=1,
                dedup_key="foreign_dismiss",
                message_code="reminder.best_before",
            )
            db.add(n)
            db.flush()
            db.commit()
            n_id = n.id
        finally:
            db.close()

        resp = http_client.post(f"/api/notifications/{n_id}/dismiss")
        assert resp.status_code == 404
        assert resp.json()["code"] == "notification.not_found"

    def test_dismiss_401_when_unauthenticated(self, http_client: Any) -> None:
        from fastapi.testclient import TestClient

        app = http_client.app
        with TestClient(app, raise_server_exceptions=True) as anon_client:
            resp = anon_client.post("/api/notifications/1/dismiss")
        assert resp.status_code == 401

    # ---- POST /notifications/dismiss-all -------------------------------------

    def test_dismiss_all_returns_count_and_hides_rows(
        self, http_client: Any, temp_db: Path
    ) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(engine, user_id, count=3)

        resp = http_client.post("/api/notifications/dismiss-all")
        assert resp.status_code == 200
        assert resp.json() == {"dismissed": 3}

        list_resp = http_client.get("/api/notifications")
        assert list_resp.json() == []

        count_resp = http_client.get("/api/notifications/unread-count")
        assert count_resp.json()["count"] == 0

    def test_dismiss_all_second_call_returns_zero(self, http_client: Any, temp_db: Path) -> None:
        from app.db.base import get_engine

        engine = get_engine()
        user_id = self._get_admin_user_id(engine)
        self._seed_notifications(engine, user_id, count=2)

        first = http_client.post("/api/notifications/dismiss-all")
        assert first.json()["dismissed"] == 2

        second = http_client.post("/api/notifications/dismiss-all")
        assert second.json()["dismissed"] == 0

    def test_dismiss_all_returns_zero_when_nothing_to_dismiss(self, http_client: Any) -> None:
        resp = http_client.post("/api/notifications/dismiss-all")
        assert resp.status_code == 200
        assert resp.json()["dismissed"] == 0

    def test_dismiss_all_only_affects_caller(self, http_client: Any, temp_db: Path) -> None:
        """dismiss-all for admin must not touch a second user's rows."""
        from sqlalchemy.orm import sessionmaker as sm_maker

        from app.auth.passwords import hash_password
        from app.db.base import get_engine
        from app.models.notification import Notification as N
        from app.models.user import User

        engine = get_engine()
        admin_id = self._get_admin_user_id(engine)
        self._seed_notifications(engine, admin_id, count=2)

        factory = sm_maker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            u2 = User(
                email="user2@example.com", password_hash=hash_password("pass"), is_active=True
            )
            db.add(u2)
            db.flush()
            n = N(
                user_id=u2.id,
                source="best_before",
                subject_type="instance",
                subject_id=1,
                dedup_key="u2_untouched",
                message_code="reminder.best_before",
            )
            db.add(n)
            db.flush()
            db.commit()
            u2_notification_id = n.id
        finally:
            db.close()

        resp = http_client.post("/api/notifications/dismiss-all")
        assert resp.json()["dismissed"] == 2

        db2 = factory()
        try:
            from app.models.notification import Notification as N2

            row = db2.get(N2, u2_notification_id)
            assert row is not None
            assert row.dismissed_at is None
        finally:
            db2.close()

    def test_dismiss_all_401_when_unauthenticated(self, http_client: Any) -> None:
        from fastapi.testclient import TestClient

        app = http_client.app
        with TestClient(app, raise_server_exceptions=True) as anon_client:
            resp = anon_client.post("/api/notifications/dismiss-all")
        assert resp.status_code == 401
