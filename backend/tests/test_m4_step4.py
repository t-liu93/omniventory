"""M4 Step 4 tests: low-stock reminders with repeat episodes and event trigger.

Required coverage (M4.md §5 + §9 Step 4 + §10 Step 4):

Low-stock episodes (§4.5):
- opener fires exactly once on first detection (same-day rescan idempotent)
- repeats fire at each configured offset (elapsed >= offset), each exactly once
- a missed scan day catches up (all offsets whose elapsed >= offset fire together)
- recovery (definition no longer low) closes the episode (resolved_at stamped)
- going low again after recovery opens a new episode with a new anchor date

Event-trigger vs daily scan:
- event hook (via consume_fifo / discard / adjust) produces the same rows as scan
- no double-insert if event fires first, then scan runs

Event hook best-effort:
- if evaluate_low_stock raises internally, movement still succeeds
- savepoint isolation: IntegrityError in notification insert does not roll back movement

Decimal params:
- exact mode: current + threshold stored as strings, parseable back to Decimal
- level mode: current + threshold are None in params

F2 savepoint fix:
- create_if_absent with duplicate dedup key rolls back only the savepoint
- outer transaction (movement data) survives the unique-constraint violation

NotificationRepository new methods:
- open_low_stock_opener: returns open opener or None
- open_low_stock_openers: returns all open openers for user
- mark_resolved: stamps opener + all open repeats
"""

from __future__ import annotations

import importlib
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as test_m4_step3.py)
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    """Reset lru_cache before and after each test."""
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_minimal_exact(
    db: Session,
    *,
    min_stock: Decimal | None = Decimal("5"),
    quantity: Decimal = Decimal("3"),
) -> tuple[Any, Any, Any, Any]:
    """Seed Household, User, ItemKind, ItemDefinition (exact mode) + one StockInstance.

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
        name="Coffee",
        kind_id=kind.id,
        stock_tracking_mode="exact",
        min_stock=min_stock,
    )
    db.add(defn)
    db.flush()

    inst = StockInstance(
        definition_id=defn.id,
        quantity=quantity,
    )
    db.add(inst)
    db.flush()
    db.commit()

    return hh, user, defn, inst


def _seed_minimal_level(db: Session) -> tuple[Any, Any, Any, Any]:
    """Seed Household, User, ItemKind, ItemDefinition (level mode) + one StockInstance.

    The instance has stock_level='low' to trigger the low-stock condition.

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
        name="Paper",
        kind_id=kind.id,
        stock_tracking_mode="level",
    )
    db.add(defn)
    db.flush()

    inst = StockInstance(
        definition_id=defn.id,
        stock_level="low",
    )
    db.add(inst)
    db.flush()
    db.commit()

    return hh, user, defn, inst


def _count_notifications(db: Session, user_id: int, def_id: int) -> list[Any]:
    """Return all notification rows for (user, definition, low_stock source)."""
    from app.models.notification import Notification

    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.source == "low_stock",
        Notification.subject_id == def_id,
    )
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 1. NotificationRepository new methods
# ---------------------------------------------------------------------------


class TestNotificationRepositoryNewMethods:
    def test_open_low_stock_opener_returns_none_when_absent(self, db_session: Session) -> None:
        """open_low_stock_opener returns None when no episode is open."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user)
        db_session.flush()
        db_session.commit()

        repo = NotificationRepository(db_session)
        result = repo.open_low_stock_opener(user.id, definition_id=999)
        assert result is None

    def test_open_low_stock_opener_returns_opener_when_present(self, db_session: Session) -> None:
        """open_low_stock_opener returns the open opener row."""
        from app.repositories.notification import NotificationRepository

        _hh, user, defn, _inst = _seed_minimal_exact(db_session)
        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)

        opener, created = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert created is True

        found = repo.open_low_stock_opener(user.id, defn.id)
        assert found is not None
        assert found.id == opener.id
        assert found.offset_days == 0

    def test_open_low_stock_opener_ignores_resolved(self, db_session: Session) -> None:
        """open_low_stock_opener ignores rows that have been resolved."""
        from datetime import UTC, datetime

        from app.repositories.notification import NotificationRepository

        _hh, user, defn, _inst = _seed_minimal_exact(db_session)
        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)

        opener, _ = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        # Resolve it manually
        opener.resolved_at = datetime.now(tz=UTC)
        db_session.flush()

        result = repo.open_low_stock_opener(user.id, defn.id)
        assert result is None

    def test_open_low_stock_openers_returns_all_open(self, db_session: Session) -> None:
        """open_low_stock_openers returns all open openers for a user."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user)
        db_session.flush()
        defn1 = ItemDefinition(name="D1", kind_id=kind.id)
        defn2 = ItemDefinition(name="D2", kind_id=kind.id)
        db_session.add_all([defn1, defn2])
        db_session.flush()
        db_session.commit()

        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)

        repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn1.id,
            dedup_key=f"low_stock:u{user.id}:d{defn1.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn2.id,
            dedup_key=f"low_stock:u{user.id}:d{defn2.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )

        openers = repo.open_low_stock_openers(user.id)
        assert len(openers) == 2
        subject_ids = {o.subject_id for o in openers}
        assert defn1.id in subject_ids
        assert defn2.id in subject_ids

    def test_mark_resolved_stamps_opener_and_repeats(self, db_session: Session) -> None:
        """mark_resolved stamps resolved_at on opener and all open sibling repeats."""
        from app.repositories.notification import NotificationRepository

        _hh, user, defn, _inst = _seed_minimal_exact(db_session)
        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)

        # Create opener + two repeats
        opener, _ = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        repeat1, _ = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o1",
            message_code="reminder.low_stock_repeat",
            episode_started_on=today,
            offset_days=1,
        )
        repeat3, _ = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o3",
            message_code="reminder.low_stock_repeat",
            episode_started_on=today,
            offset_days=3,
        )

        repo.mark_resolved(opener)
        db_session.expire_all()

        from app.models.notification import Notification

        for row_id in [opener.id, repeat1.id, repeat3.id]:
            row = db_session.get(Notification, row_id)
            assert row is not None
            assert row.resolved_at is not None, (
                f"Row {row_id} (offset={row.offset_days}) should be resolved"
            )

    def test_mark_resolved_leaves_other_user_rows_intact(self, db_session: Session) -> None:
        """mark_resolved does not touch another user's episode rows."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.notification import Notification
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        u1 = User(email="u1@x.com", password_hash=hash_password("p"), is_active=True)
        u2 = User(email="u2@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add_all([u1, u2])
        db_session.flush()
        defn = ItemDefinition(name="D", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()
        db_session.commit()

        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)

        opener_u1, _ = repo.create_if_absent(
            user_id=u1.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{u1.id}:d{defn.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        opener_u2, _ = repo.create_if_absent(
            user_id=u2.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{u2.id}:d{defn.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )

        repo.mark_resolved(opener_u1)
        db_session.expire_all()

        row_u2 = db_session.get(Notification, opener_u2.id)
        assert row_u2 is not None
        assert row_u2.resolved_at is None  # u2's episode untouched


# ---------------------------------------------------------------------------
# 2. F2 savepoint fix: create_if_absent does not roll back outer transaction
# ---------------------------------------------------------------------------


class TestSavepointFix:
    def test_duplicate_dedup_does_not_rollback_outer_data(self, db_session: Session) -> None:
        """When create_if_absent hits a unique constraint, only the savepoint rolls back.

        We simulate this by directly adding a Notification with a dedup_key that
        will conflict on the second create_if_absent call, verify the outer
        transaction still has the first notification and a separately-added
        sentinel row.
        """
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification import Notification
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user)
        db_session.flush()
        db_session.commit()

        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)
        dedup = f"low_stock:u{user.id}:d1:{today.isoformat()}:o0"

        # First insert: succeeds
        n1, created1 = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=1,
            dedup_key=dedup,
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert created1 is True

        # Second insert with same dedup key: hits SELECT-first fast path,
        # returns (existing, False) without even trying the SAVEPOINT.
        n2, created2 = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=1,
            dedup_key=dedup,
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert created2 is False
        assert n2.id == n1.id

        # The outer transaction is still healthy: the first notification is
        # accessible and the session is not invalidated.
        stmt = select(Notification).where(Notification.user_id == user.id)
        rows = db_session.execute(stmt).scalars().all()
        assert len(rows) == 1
        assert rows[0].dedup_key == dedup

    def test_savepoint_isolates_insert_from_outer_movement_data(self, db_session: Session) -> None:
        """Verify the savepoint approach: outer data survives an IntegrityError in the savepoint.

        We manually trigger the IntegrityError path by inserting two rows with
        the same dedup_key using begin_nested() directly, then verifying that
        a pre-existing row in the session (simulating a movement) is still there.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification import Notification
        from app.models.user import User

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user)
        db_session.flush()

        # Simulate "outer data" that must survive: a sentinel object already
        # added to the session (like a movement row).
        sentinel = Notification(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=1,
            dedup_key="sentinel-unique-key",
            message_code="reminder.low_stock",
            episode_started_on=date(2025, 6, 1),
            offset_days=0,
        )
        db_session.add(sentinel)
        db_session.flush()

        # Insert a duplicate notification inside a SAVEPOINT -- this should raise
        # IntegrityError, roll back the savepoint only, and leave the sentinel.
        dup = Notification(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=1,
            dedup_key="sentinel-unique-key",  # same key as sentinel
            message_code="reminder.low_stock",
            episode_started_on=date(2025, 6, 1),
            offset_days=0,
        )
        try:
            with db_session.begin_nested():
                db_session.add(dup)
                db_session.flush()
            # If we reach here somehow, the test is invalid; but we expect an error.
        except SAIntegrityError:
            pass  # Expected: savepoint rolled back

        # The outer transaction is still valid and the sentinel is still there.
        db_session.expire_all()
        rows = (
            db_session.execute(select(Notification).where(Notification.user_id == user.id))
            .scalars()
            .all()
        )
        # Only the sentinel should exist (the duplicate insertion was rolled back).
        assert len(rows) == 1
        assert rows[0].dedup_key == "sentinel-unique-key"

    def test_create_if_absent_integrity_error_branch_returns_existing_and_preserves_outer_data(
        self, db_session: Session
    ) -> None:
        """create_if_absent IntegrityError branch: savepoint rolls back, existing row returned.

        This test forces the exact IntegrityError path inside create_if_absent
        by simulating a race condition:

        1. Insert N1 via create_if_absent (succeeds, written to DB via flush).
        2. Add a sentinel Notification in the same session to represent "outer data"
           that must survive the conflict.
        3. Monkeypatch _get_by_dedup so the *first* call (the SELECT-first guard
           inside the second create_if_absent) returns None -- simulating the race
           where a concurrent writer inserted N1 *between* the SELECT and INSERT.
           All subsequent calls delegate to the real implementation so the fallback
           _get_by_dedup inside the except block can retrieve N1.
        4. Call create_if_absent again with the same (user_id, dedup_key).
           Because _get_by_dedup returned None, the code skips the fast path and
           attempts INSERT inside a SAVEPOINT -> flush raises IntegrityError ->
           context manager auto-rolls back the savepoint -> except block calls
           _get_by_dedup a second time (real lookup) -> returns N1.

        Assertions:
        - No exception is raised.
        - Returns created=False and the same row as N1.
        - Sentinel (outer data) still exists in the session (outer transaction intact).
        """
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification import Notification
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user)
        db_session.flush()
        db_session.commit()

        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)
        dedup = f"low_stock:u{user.id}:d999:{today.isoformat()}:o0"

        # Step 1: insert N1 via a normal create_if_absent; flush writes it to the DB
        # so the unique index is live (even without commit).
        n1, created1 = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=999,
            dedup_key=dedup,
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert created1 is True

        # Step 2: add a sentinel row in the same session to represent "outer data"
        # (e.g. a stock movement) that must survive the conflict below.
        sentinel = Notification(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=888,
            dedup_key="outer-sentinel-key",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        db_session.add(sentinel)
        db_session.flush()

        # Step 3: build a patched _get_by_dedup that returns None exactly once
        # (simulating the race-condition window) and then delegates to the real method.
        real_get_by_dedup = repo._get_by_dedup
        call_count: list[int] = [0]

        def _patched_get_by_dedup(user_id_arg: int, dedup_key_arg: str) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                # Simulate SELECT miss: pretend the row doesn't exist yet,
                # so create_if_absent proceeds to the INSERT path.
                return None
            # All subsequent calls (including the fallback inside except) use the
            # real lookup so the existing row can be found.
            return real_get_by_dedup(user_id_arg, dedup_key_arg)

        # Step 4: patch and call create_if_absent with the same (user_id, dedup_key).
        with patch.object(repo, "_get_by_dedup", side_effect=_patched_get_by_dedup):
            n2, created2 = repo.create_if_absent(
                user_id=user.id,
                source="low_stock",
                subject_type="definition",
                subject_id=999,
                dedup_key=dedup,
                message_code="reminder.low_stock",
                episode_started_on=today,
                offset_days=0,
            )

        # Assertion 1: the IntegrityError branch returned created=False and the existing row.
        assert created2 is False
        assert n2.id == n1.id
        assert n2.dedup_key == dedup

        # Assertion 2: outer data is intact (no full transaction rollback occurred).
        db_session.expire_all()
        sentinel_row = db_session.get(Notification, sentinel.id)
        assert sentinel_row is not None, "Sentinel (outer data) was unexpectedly destroyed"
        assert sentinel_row.dedup_key == "outer-sentinel-key"

        # Assertion 3: the session remains usable (no exception leaked out).
        rows = (
            db_session.execute(select(Notification).where(Notification.user_id == user.id))
            .scalars()
            .all()
        )
        # N1 and sentinel should be present; the failed duplicate INSERT was rolled back.
        dedup_keys = {r.dedup_key for r in rows}
        assert dedup in dedup_keys
        assert "outer-sentinel-key" in dedup_keys
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# 3. Low-stock episode logic via ReminderEngine.run_scan
# ---------------------------------------------------------------------------


class TestLowStockEpisodes:
    def _make_engine(self, db: Session) -> Any:
        from app.services.reminder_engine import ReminderEngine

        return ReminderEngine(db)

    def test_opener_fires_once_on_going_low(self, db_session: Session) -> None:
        """Going low opens an episode (opener row); re-scan is idempotent."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        summary = engine.run_scan(today_local=today)
        assert summary.low_stock == 1

        # Opener should exist
        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None
        assert opener.offset_days == 0
        assert opener.episode_started_on == today
        assert opener.resolved_at is None

        # Re-scan same day: no new rows
        from app.services.reminder_engine import ReminderEngine

        summary2 = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary2.low_stock == 0

    def test_repeat_fires_at_offset(self, db_session: Session) -> None:
        """Repeat fires when elapsed >= offset (default [1,3,7])."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Day 0: opener
        summary0 = engine.run_scan(today_local=day0)
        assert summary0.low_stock == 1

        # Day 1: elapsed=1, repeat o=1 should fire (o <= 1)
        from app.services.reminder_engine import ReminderEngine

        day1 = day0 + timedelta(days=1)
        summary1 = ReminderEngine(db_session).run_scan(today_local=day1)
        assert summary1.low_stock == 1  # one repeat (o=1)

        # Day 3: elapsed=3, repeat o=3 should fire; o=1 already fired
        day3 = day0 + timedelta(days=3)
        summary3 = ReminderEngine(db_session).run_scan(today_local=day3)
        assert summary3.low_stock == 1  # one repeat (o=3)

        # Day 7: elapsed=7, repeat o=7 should fire; others already fired
        day7 = day0 + timedelta(days=7)
        summary7 = ReminderEngine(db_session).run_scan(today_local=day7)
        assert summary7.low_stock == 1  # one repeat (o=7)

        # Day 8: all offsets already fired, no new rows
        day8 = day0 + timedelta(days=8)
        summary8 = ReminderEngine(db_session).run_scan(today_local=day8)
        assert summary8.low_stock == 0

    def test_missed_scan_catches_up_multiple_offsets(self, db_session: Session) -> None:
        """If a scan is missed, all elapsed offsets fire together in one scan."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Day 0: opener
        engine.run_scan(today_local=day0)

        # Skip days 1-2; on day 3, elapsed=3 means offsets 1 and 3 should both fire.
        from app.services.reminder_engine import ReminderEngine

        day3 = day0 + timedelta(days=3)
        summary = ReminderEngine(db_session).run_scan(today_local=day3)
        assert summary.low_stock == 2  # o=1 and o=3 both fire

    def test_missed_scan_catches_up_all_offsets(self, db_session: Session) -> None:
        """Scanning on day 7 (after missing days 1-6) catches up all 3 offsets."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Day 0: opener
        engine.run_scan(today_local=day0)

        # Day 7: elapsed=7, offsets 1, 3, 7 all fire in one pass
        from app.services.reminder_engine import ReminderEngine

        day7 = day0 + timedelta(days=7)
        summary = ReminderEngine(db_session).run_scan(today_local=day7)
        assert summary.low_stock == 3  # o=1, o=3, o=7

    def test_recovery_closes_episode(self, db_session: Session) -> None:
        """When definition is no longer low, the episode is closed (resolved_at set)."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Open episode
        engine.run_scan(today_local=day0)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None

        # Replenish: raise quantity above min_stock
        inst.quantity = Decimal("10")
        db_session.commit()

        # Next scan: definition no longer low -> episode closed
        day1 = day0 + timedelta(days=1)
        summary = ReminderEngine(db_session).run_scan(today_local=day1)
        assert summary.low_stock == 0  # no new notifications

        # opener should now be resolved
        db_session.expire_all()
        from app.models.notification import Notification

        opener_row = db_session.get(Notification, opener.id)
        assert opener_row is not None
        assert opener_row.resolved_at is not None

    def test_recovery_stops_repeats(self, db_session: Session) -> None:
        """After an episode is closed, no further repeat rows are created."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Open episode and fire o=1 repeat
        engine.run_scan(today_local=day0)
        from app.services.reminder_engine import ReminderEngine

        day1 = day0 + timedelta(days=1)
        ReminderEngine(db_session).run_scan(today_local=day1)  # fires o=1

        # Replenish
        inst.quantity = Decimal("10")
        db_session.commit()

        # Close episode
        day2 = day0 + timedelta(days=2)
        ReminderEngine(db_session).run_scan(today_local=day2)

        # Day 3: o=3 would fire but episode is closed -> nothing
        day3 = day0 + timedelta(days=3)
        summary = ReminderEngine(db_session).run_scan(today_local=day3)
        assert summary.low_stock == 0

    def test_re_low_opens_new_episode(self, db_session: Session) -> None:
        """Going low again after recovery opens a brand-new episode (new anchor)."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # First episode
        engine.run_scan(today_local=day0)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)
        first_opener = repo.open_low_stock_opener(user.id, defn.id)
        assert first_opener is not None

        # Replenish -> close episode
        inst.quantity = Decimal("10")
        db_session.commit()
        day1 = day0 + timedelta(days=1)
        ReminderEngine(db_session).run_scan(today_local=day1)

        # Confirm episode closed
        db_session.expire_all()
        from app.models.notification import Notification

        first_row = db_session.get(Notification, first_opener.id)
        assert first_row is not None
        assert first_row.resolved_at is not None

        # Go low again
        inst.quantity = Decimal("2")
        db_session.commit()

        day5 = day0 + timedelta(days=5)
        summary = ReminderEngine(db_session).run_scan(today_local=day5)
        assert summary.low_stock == 1  # New opener

        # New opener has different anchor
        new_opener = repo.open_low_stock_opener(user.id, defn.id)
        assert new_opener is not None
        assert new_opener.id != first_opener.id
        assert new_opener.episode_started_on == day5  # new anchor

    def test_level_mode_triggers_opener(self, db_session: Session) -> None:
        """Level-mode definition (any lot with stock_level='low') also triggers opener."""
        _hh, user, defn, _inst = _seed_minimal_level(db_session)
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        summary = engine.run_scan(today_local=today)
        assert summary.low_stock == 1

        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None

    def test_not_low_produces_no_opener(self, db_session: Session) -> None:
        """When stock is above min_stock, no opener is created."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("10")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        summary = engine.run_scan(today_local=today)
        assert summary.low_stock == 0

        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        assert repo.open_low_stock_opener(user.id, defn.id) is None


# ---------------------------------------------------------------------------
# 3b. Same-day reopen fix (walkthrough fix #3)
# ---------------------------------------------------------------------------


class TestSameDayReopen:
    """Tests for the same-day low→recovered→low reopen bug fix.

    The fix: when a definition goes low again on the SAME calendar day after
    recovery, the engine computes seq = count_low_stock_openers_on(...) and
    appends "#<seq>" to the base dedup key, so the new opener never collides
    with the already-resolved opener from the earlier episode.
    """

    def _make_engine(self, db: Session) -> Any:
        from app.services.reminder_engine import ReminderEngine

        return ReminderEngine(db)

    def test_count_low_stock_openers_on_repo_method(self, db_session: Session) -> None:
        """count_low_stock_openers_on counts ALL openers (open + resolved) for (user, def, date)."""
        from datetime import UTC, datetime

        from app.repositories.notification import NotificationRepository

        _hh, user, defn, _inst = _seed_minimal_exact(db_session)
        repo = NotificationRepository(db_session)
        today = date(2025, 6, 1)

        # Zero before any openers exist
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 0

        # Insert an opener row
        opener, _ = repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o0",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 1

        # Resolve it -- count should still be 1 (counts regardless of resolved_at)
        opener.resolved_at = datetime.now(tz=UTC)
        db_session.flush()
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 1

        # Insert a second opener with a #1 suffix
        repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o0#1",
            message_code="reminder.low_stock",
            episode_started_on=today,
            offset_days=0,
        )
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 2

        # A different date should not be counted
        other_day = today + timedelta(days=1)
        assert repo.count_low_stock_openers_on(user.id, defn.id, other_day) == 0

        # A repeat row (offset_days=1) should NOT count
        repo.create_if_absent(
            user_id=user.id,
            source="low_stock",
            subject_type="definition",
            subject_id=defn.id,
            dedup_key=f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o1",
            message_code="reminder.low_stock_repeat",
            episode_started_on=today,
            offset_days=1,
        )
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 2  # still 2

    def test_same_day_reopen_creates_new_opener_with_suffix(self, db_session: Session) -> None:
        """The core bug fix: same-day low→recover→low creates a new opener with #1 suffix."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        # Step 1: go low → opener created (legacy key, no suffix)
        summary1 = engine.run_scan(today_local=today)
        assert summary1.low_stock == 1

        from app.models.notification import Notification
        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)
        first_opener = repo.open_low_stock_opener(user.id, defn.id)
        assert first_opener is not None
        assert "#" not in first_opener.dedup_key  # legacy bare key

        # Step 2: recover (no longer low)
        inst.quantity = Decimal("10")
        db_session.commit()

        # Same-day scan closes the episode
        summary2 = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary2.low_stock == 0  # no new notifications

        # Confirm first episode is closed
        db_session.expire_all()
        first_row = db_session.get(Notification, first_opener.id)
        assert first_row is not None
        assert first_row.resolved_at is not None

        # Step 3: go low AGAIN the same day
        inst.quantity = Decimal("2")
        db_session.commit()

        summary3 = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary3.low_stock == 1, "Re-opened episode must produce a new opener"

        # Exactly 2 opener rows for (user, def, today): first (resolved) + second (open)
        all_openers_today = _count_openers_on(db_session, user.id, defn.id, today)
        assert len(all_openers_today) == 2

        # New opener has #1 suffix and is open
        new_opener = repo.open_low_stock_opener(user.id, defn.id)
        assert new_opener is not None
        assert new_opener.id != first_opener.id
        assert new_opener.dedup_key.endswith("#1"), (
            f"Expected dedup_key to end with '#1', got: {new_opener.dedup_key!r}"
        )
        assert new_opener.episode_started_on == today
        assert new_opener.resolved_at is None

    def test_first_episode_uses_legacy_key_no_suffix(self, db_session: Session) -> None:
        """The very first episode of a (user, def, day) always uses the bare legacy key."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        engine.run_scan(today_local=today)

        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None
        expected_key = f"low_stock:u{user.id}:d{defn.id}:{today.isoformat()}:o0"
        assert opener.dedup_key == expected_key

    def test_open_episode_rescan_idempotent_no_double_opener(self, db_session: Session) -> None:
        """While the episode is STILL OPEN, re-scanning does NOT create a duplicate opener."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        # First scan: opener created
        summary1 = engine.run_scan(today_local=today)
        assert summary1.low_stock == 1

        # Second scan same day, still low: must be idempotent
        from app.services.reminder_engine import ReminderEngine

        summary2 = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary2.low_stock == 0  # no duplicate

        # Still exactly 1 opener row
        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        all_openers = _count_openers_on(db_session, user.id, defn.id, today)
        assert len(all_openers) == 1
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 1

    def test_seq_increments_second_same_day_reopen(self, db_session: Session) -> None:
        """A second same-day reopen (low→recover→low→recover→low) produces #2 suffix."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)

        # Episode 1
        engine.run_scan(today_local=today)
        opener1 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener1 is not None
        assert "#" not in opener1.dedup_key  # bare key (seq==0)

        # Recover → close episode 1
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        # Episode 2 (re-open #1)
        inst.quantity = Decimal("2")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)
        opener2 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener2 is not None
        assert opener2.dedup_key.endswith("#1")

        # Recover → close episode 2
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        # Episode 3 (re-open #2)
        inst.quantity = Decimal("2")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)
        opener3 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener3 is not None
        assert opener3.dedup_key.endswith("#2")

        # 3 opener rows total for today
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 3

    def test_cross_day_reopen_uses_legacy_key(self, db_session: Session) -> None:
        """Cross-day reopen: new anchor date means seq==0, so legacy bare key is used."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Day 0: first episode
        engine.run_scan(today_local=day0)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)

        # Recover on day0
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=day0)

        # Go low again on day1 (DIFFERENT day)
        inst.quantity = Decimal("2")
        db_session.commit()
        day1 = day0 + timedelta(days=1)
        summary = ReminderEngine(db_session).run_scan(today_local=day1)
        assert summary.low_stock == 1

        new_opener = repo.open_low_stock_opener(user.id, defn.id)
        assert new_opener is not None
        assert new_opener.episode_started_on == day1
        # seq==0 on day1 (no previous openers for day1)
        assert "#" not in new_opener.dedup_key
        expected_key = f"low_stock:u{user.id}:d{defn.id}:{day1.isoformat()}:o0"
        assert new_opener.dedup_key == expected_key

    def test_reopened_episode_repeat_fires_correctly(self, db_session: Session) -> None:
        """A reopened (#1) episode still fires its repeat notifications at the right offsets."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)

        # Episode 1: open and close same day
        engine.run_scan(today_local=today)
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        # Episode 2 (reopen on same day)
        inst.quantity = Decimal("2")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        opener2 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener2 is not None
        assert opener2.dedup_key.endswith("#1")

        # Day 1 after the anchor: repeat o=1 should fire for episode 2
        day1 = today + timedelta(days=1)
        summary = ReminderEngine(db_session).run_scan(today_local=day1)
        assert summary.low_stock == 1, "Repeat o=1 must fire for the reopened episode"

        # Verify the repeat row has episode_started_on == today (same anchor as opener2)
        from app.models.notification import Notification

        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.source == "low_stock",
            Notification.subject_id == defn.id,
            Notification.offset_days == 1,
            Notification.resolved_at.is_(None),
        )
        repeat_row = db_session.execute(stmt).scalar_one_or_none()
        assert repeat_row is not None
        assert repeat_row.episode_started_on == today

    def test_reopened_episode_repeat_keys_no_collision_with_prior_episode(
        self, db_session: Session
    ) -> None:
        """Repeat dedup keys for the reopened episode do not collide with the prior episode.

        Episode 1 anchor == episode 2 anchor == today (same-day reopen).
        The prior episode 1 was resolved without firing any repeats (recovered too
        quickly).  Episode 2 (opener key ends in #1) fires repeat o=1 on day1.
        The repeat dedup for episode 2 is:
            "low_stock:u{uid}:d{def}:{today}:o1"
        Episode 1's repeat dedup would have been the same string -- BUT episode 1
        resolved before any repeats fired, so that row does NOT exist.  Episode 2's
        repeat row is therefore inserted cleanly (created=True).

        This test asserts that created=True (no collision) and that the repeat row
        has episode_started_on == today.
        """
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)

        # Episode 1: open and close the same day WITHOUT firing any repeats
        # (so no repeat row for "...:{today}:o1" is written for episode 1)
        engine.run_scan(today_local=today)
        first_opener = repo.open_low_stock_opener(user.id, defn.id)
        assert first_opener is not None
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        # Episode 2: reopen the same day
        inst.quantity = Decimal("2")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)
        opener2 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener2 is not None

        # Day 1: repeat o=1 fires for episode 2
        day1 = today + timedelta(days=1)
        from app.models.notification import Notification

        before_count = len(
            db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.source == "low_stock",
                    Notification.subject_id == defn.id,
                    Notification.offset_days == 1,
                )
            )
            .scalars()
            .all()
        )
        assert before_count == 0, "No repeat rows should exist yet"

        summary = ReminderEngine(db_session).run_scan(today_local=day1)
        assert summary.low_stock == 1  # repeat o=1 created

        after_rows = (
            db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.source == "low_stock",
                    Notification.subject_id == defn.id,
                    Notification.offset_days == 1,
                )
            )
            .scalars()
            .all()
        )
        assert len(after_rows) == 1, "Exactly one repeat row should be created"
        assert after_rows[0].episode_started_on == today

    def test_three_same_day_reopens_produce_exactly_one_repeat(self, db_session: Session) -> None:
        """Three same-day reopens (A→close→B→close→C, still open) produce exactly one repeat.

        Episodes A and B both resolve the same day their anchor was created
        (elapsed=0 < every repeat offset>=1), so they never write a repeat row.
        Only episode C (still open at D+1) fires repeat o=1.  There must be
        exactly one offset_days==1 row for (user, def), and it belongs to
        episode C (episode_started_on == today).
        """
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)

        # Episode A: go low → opener (bare key), recover → close same day
        engine.run_scan(today_local=today)
        opener_a = repo.open_low_stock_opener(user.id, defn.id)
        assert opener_a is not None
        assert "#" not in opener_a.dedup_key
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        # Episode B: go low again same day → opener (#1), recover → close same day
        inst.quantity = Decimal("2")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)
        opener_b = repo.open_low_stock_opener(user.id, defn.id)
        assert opener_b is not None
        assert opener_b.dedup_key.endswith("#1")
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)

        # Episode C: go low a third time same day → opener (#2), stays open
        inst.quantity = Decimal("2")
        db_session.commit()
        ReminderEngine(db_session).run_scan(today_local=today)
        opener_c = repo.open_low_stock_opener(user.id, defn.id)
        assert opener_c is not None
        assert opener_c.dedup_key.endswith("#2")
        assert opener_c.resolved_at is None

        # 3 opener rows total on day D (A resolved, B resolved, C open)
        all_openers = _count_openers_on(db_session, user.id, defn.id, today)
        assert len(all_openers) == 3
        assert repo.count_low_stock_openers_on(user.id, defn.id, today) == 3

        # Advance to D+1 with definition still low; repeat o=1 must fire
        from app.models.notification import Notification

        day1 = today + timedelta(days=1)
        summary = ReminderEngine(db_session).run_scan(today_local=day1)
        assert summary.low_stock >= 1, "Repeat o=1 must fire for episode C"

        # Exactly ONE offset_days==1 row for (user, def) -- no duplicates/collisions
        repeat_rows = (
            db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.source == "low_stock",
                    Notification.subject_id == defn.id,
                    Notification.offset_days == 1,
                )
            )
            .scalars()
            .all()
        )
        assert len(repeat_rows) == 1, (
            f"Expected exactly 1 repeat row at offset_days=1, got {len(repeat_rows)}"
        )
        # The repeat belongs to episode C (same anchor date = today)
        assert repeat_rows[0].episode_started_on == today

    def test_event_trigger_same_day_reopen(self, db_session: Session) -> None:
        """evaluate_low_stock (event trigger) also produces correct same-day reopen behavior."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        from app.repositories.notification import NotificationRepository
        from app.services.reminder_engine import ReminderEngine

        repo = NotificationRepository(db_session)

        # Episode 1 via event trigger
        new_notifs = engine.evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs) == 1
        opener1 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener1 is not None
        assert "#" not in opener1.dedup_key  # bare key

        # Recover via event trigger (close episode)
        inst.quantity = Decimal("10")
        db_session.commit()
        ReminderEngine(db_session).evaluate_low_stock(defn.id, today_local=today)

        db_session.expire_all()
        from app.models.notification import Notification

        opener1_row = db_session.get(Notification, opener1.id)
        assert opener1_row is not None
        assert opener1_row.resolved_at is not None

        # Go low again → event trigger → must create a new opener with #1
        inst.quantity = Decimal("2")
        db_session.commit()
        new_notifs2 = ReminderEngine(db_session).evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs2) == 1, "Re-opened episode must produce a new opener notification"

        opener2 = repo.open_low_stock_opener(user.id, defn.id)
        assert opener2 is not None
        assert opener2.id != opener1.id
        assert opener2.dedup_key.endswith("#1")
        assert opener2.resolved_at is None

        # No double-insert: calling event trigger again with still-open episode
        new_notifs3 = ReminderEngine(db_session).evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs3) == 0  # idempotent


# Helper for same-day reopen tests
def _count_openers_on(db: Session, user_id: int, def_id: int, anchor_date: date) -> list[Any]:
    """Return all opener (offset_days=0) rows for (user, definition, anchor_date)."""
    from app.models.notification import Notification

    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.source == "low_stock",
        Notification.subject_id == def_id,
        Notification.offset_days == 0,
        Notification.episode_started_on == anchor_date,
    )
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 4. Decimal params handling
# ---------------------------------------------------------------------------


class TestDecimalParams:
    def test_exact_mode_params_are_strings(self, db_session: Session) -> None:
        """exact mode: current and threshold stored as strings in params JSON."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5.5"), quantity=Decimal("3.2")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        engine.run_scan(today_local=today)

        from app.models.notification import Notification

        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.source == "low_stock",
            Notification.offset_days == 0,
        )
        notif = db_session.execute(stmt).scalar_one()
        assert notif.params is not None
        params = json.loads(notif.params)
        # Both current and threshold must be strings (not Decimal, not None for exact mode)
        assert isinstance(params["current"], str), "current should be a string"
        assert isinstance(params["threshold"], str), "threshold should be a string"
        # Must be parseable back to Decimal
        assert Decimal(params["current"]) == Decimal("3.2")
        assert Decimal(params["threshold"]) == Decimal("5.5")

    def test_level_mode_params_carry_level_code(self, db_session: Session) -> None:
        """level mode: params carry mode='level' and level='low'; no numeric current/threshold."""
        _hh, user, defn, _inst = _seed_minimal_level(db_session)
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        engine.run_scan(today_local=today)

        from app.models.notification import Notification

        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.source == "low_stock",
            Notification.offset_days == 0,
        )
        notif = db_session.execute(stmt).scalar_one()
        assert notif.params is not None
        params = json.loads(notif.params)
        # Level mode carries the qualitative level code, not blank numeric fields
        assert params["mode"] == "level"
        assert params["level"] == "low"
        assert "current" not in params
        assert "threshold" not in params

    def test_level_mode_repeat_carries_level_and_offset(self, db_session: Session) -> None:
        """level mode repeat: params carry mode='level', level='low', and offset; no numeric fields."""
        _hh, user, defn, _inst = _seed_minimal_level(db_session)
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)
        engine.run_scan(today_local=day0)

        from app.models.notification import Notification
        from app.services.reminder_engine import ReminderEngine

        day1 = day0 + timedelta(days=1)
        ReminderEngine(db_session).run_scan(today_local=day1)

        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.source == "low_stock",
            Notification.offset_days == 1,
        )
        repeat_notif = db_session.execute(stmt).scalar_one()
        params = json.loads(repeat_notif.params)
        assert params["mode"] == "level"
        assert params["level"] == "low"
        assert params["offset"] == 1
        assert "current" not in params
        assert "threshold" not in params

    def test_repeat_params_include_offset(self, db_session: Session) -> None:
        """Repeat notifications include the 'offset' key in params."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)
        engine.run_scan(today_local=day0)

        from app.models.notification import Notification
        from app.services.reminder_engine import ReminderEngine

        day1 = day0 + timedelta(days=1)
        ReminderEngine(db_session).run_scan(today_local=day1)

        stmt = select(Notification).where(
            Notification.user_id == user.id,
            Notification.source == "low_stock",
            Notification.offset_days == 1,
        )
        repeat_notif = db_session.execute(stmt).scalar_one()
        params = json.loads(repeat_notif.params)
        assert params["offset"] == 1

    def _make_engine(self, db: Session) -> Any:
        from app.services.reminder_engine import ReminderEngine

        return ReminderEngine(db)


# ---------------------------------------------------------------------------
# 5. Event hook: evaluate_low_stock
# ---------------------------------------------------------------------------


class TestEvaluateLowStock:
    def _make_engine(self, db: Session) -> Any:
        from app.services.reminder_engine import ReminderEngine

        return ReminderEngine(db)

    def test_evaluate_low_stock_opens_episode_when_low(self, db_session: Session) -> None:
        """evaluate_low_stock creates an opener when the definition is low."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        new_notifs = engine.evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs) == 1

        from app.repositories.notification import NotificationRepository

        opener = NotificationRepository(db_session).open_low_stock_opener(user.id, defn.id)
        assert opener is not None
        assert opener.offset_days == 0

    def test_evaluate_low_stock_no_opener_when_not_low(self, db_session: Session) -> None:
        """evaluate_low_stock does nothing when the definition is not low."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("10")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        new_notifs = engine.evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs) == 0

    def test_event_and_scan_produce_same_rows_no_duplicate(self, db_session: Session) -> None:
        """Event hook then scan: no duplicate rows created."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        today = date(2025, 6, 1)

        # Event fires first
        new_notifs_event = engine.evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs_event) == 1

        # Daily scan runs: should not create another opener
        from app.services.reminder_engine import ReminderEngine

        summary = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary.low_stock == 0  # already exists

        # Only one notification row should exist
        from app.models.notification import Notification

        rows = (
            db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.source == "low_stock",
                    Notification.subject_id == defn.id,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    def test_scan_then_event_no_duplicate(self, db_session: Session) -> None:
        """Scan fires first, then event: no duplicate rows."""
        _hh, user, defn, _inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        today = date(2025, 6, 1)

        from app.services.reminder_engine import ReminderEngine

        # Scan first
        summary = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary.low_stock == 1

        # Event fires: should be idempotent (returns empty list when no new rows)
        engine = ReminderEngine(db_session)
        new_notifs = engine.evaluate_low_stock(defn.id, today_local=today)
        assert len(new_notifs) == 0

    def test_evaluate_low_stock_closes_episode_when_recovered(self, db_session: Session) -> None:
        """evaluate_low_stock closes an open episode when definition is no longer low."""
        _hh, user, defn, inst = _seed_minimal_exact(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        engine = self._make_engine(db_session)
        day0 = date(2025, 6, 1)

        # Open episode
        engine.evaluate_low_stock(defn.id, today_local=day0)

        from app.models.notification import Notification
        from app.repositories.notification import NotificationRepository

        opener = NotificationRepository(db_session).open_low_stock_opener(user.id, defn.id)
        assert opener is not None

        # Replenish
        inst.quantity = Decimal("10")
        db_session.commit()

        # Event fires for this definition after replenishment
        from app.services.reminder_engine import ReminderEngine

        ReminderEngine(db_session).evaluate_low_stock(defn.id, today_local=day0 + timedelta(days=1))

        # Episode should be closed
        db_session.expire_all()
        opener_row = db_session.get(Notification, opener.id)
        assert opener_row is not None
        assert opener_row.resolved_at is not None

    def test_evaluate_low_stock_does_not_touch_other_definitions(self, db_session: Session) -> None:
        """evaluate_low_stock(def_A) does not close episodes for def_B."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.repositories.notification import NotificationRepository

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u@x.com", password_hash=hash_password("p"), is_active=True)
        db_session.add(user)
        db_session.flush()

        defn_a = ItemDefinition(
            name="A", kind_id=kind.id, stock_tracking_mode="exact", min_stock=Decimal("5")
        )
        defn_b = ItemDefinition(
            name="B", kind_id=kind.id, stock_tracking_mode="exact", min_stock=Decimal("5")
        )
        db_session.add_all([defn_a, defn_b])
        db_session.flush()

        # Both definitions are low
        db_session.add(StockInstance(definition_id=defn_a.id, quantity=Decimal("2")))
        db_session.add(StockInstance(definition_id=defn_b.id, quantity=Decimal("2")))
        db_session.flush()
        db_session.commit()

        from app.services.reminder_engine import ReminderEngine

        today = date(2025, 6, 1)
        # Open episodes for both
        ReminderEngine(db_session).run_scan(today_local=today)

        repo = NotificationRepository(db_session)
        opener_a = repo.open_low_stock_opener(user.id, defn_a.id)
        opener_b = repo.open_low_stock_opener(user.id, defn_b.id)
        assert opener_a is not None
        assert opener_b is not None

        # Replenish A only
        from sqlalchemy import select

        from app.models.stock_instance import StockInstance as SI

        inst_a = db_session.execute(select(SI).where(SI.definition_id == defn_a.id)).scalar_one()
        inst_a.quantity = Decimal("10")
        db_session.commit()

        # evaluate_low_stock for A: should close A but NOT B
        ReminderEngine(db_session).evaluate_low_stock(
            defn_a.id, today_local=today + timedelta(days=1)
        )

        db_session.expire_all()
        from app.models.notification import Notification

        opener_a_row = db_session.get(Notification, opener_a.id)
        opener_b_row = db_session.get(Notification, opener_b.id)
        assert opener_a_row is not None
        assert opener_a_row.resolved_at is not None  # A closed
        assert opener_b_row is not None
        assert opener_b_row.resolved_at is None  # B untouched


# ---------------------------------------------------------------------------
# 6. Event hook via StockMovementService (consumer pathway)
# ---------------------------------------------------------------------------


class TestEventHookViaMovementService:
    def _seed_for_movement(self, db: Session) -> tuple[Any, Any, Any, Any]:
        """Seed Household, User, ItemKind, ItemDefinition + StockInstance for movements.

        For exact-mode, the quantity is tracked via movements (not a bare column
        value).  We seed the instance with quantity=None and then add an intake
        movement + recompute, which is what StockInstanceService.create does.
        """
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.repositories.stock_movement import StockMovementRepository
        from app.services.stock_instance import StockInstanceService

        hh = Household(id=1, name="H", currency="USD", timezone="UTC")
        db.add(hh)
        db.flush()

        kind = ItemKind(code="consumable", name="Consumable", is_system=True)
        db.add(kind)
        db.flush()

        user = User(email="admin@example.com", password_hash=hash_password("p"), is_active=True)
        db.add(user)
        db.flush()

        defn = ItemDefinition(
            name="Coffee",
            kind_id=kind.id,
            stock_tracking_mode="exact",
            min_stock=Decimal("5"),
        )
        db.add(defn)
        db.flush()

        # Create instance with quantity=None (correct for exact mode), then record
        # the initial intake movement and recompute the cached quantity.
        inst = StockInstance(definition_id=defn.id, quantity=None)
        db.add(inst)
        db.flush()

        movement_repo = StockMovementRepository(db)
        movement_repo.append(
            instance_id=inst.id,
            type="intake",
            quantity_delta=Decimal("10"),
        )
        StockInstanceService(db).recompute_quantity(inst)
        db.flush()
        db.commit()

        return hh, user, defn, inst

    def _make_svc(self, db: Session, user: Any, hh: Any) -> Any:
        from app.core.context import RequestContext
        from app.services.stock_movement import StockMovementService

        ctx = RequestContext(household=hh, user=user)
        return StockMovementService(db, ctx)

    def test_consume_fifo_triggers_low_stock_opener(self, db_session: Session) -> None:
        """consume_fifo below min_stock immediately creates a low-stock opener."""
        hh, user, defn, inst = self._seed_for_movement(db_session)
        svc = self._make_svc(db_session, user, hh)

        # Consume enough to go below min_stock=5 (current=10, consume 6 -> 4 < 5)
        svc.consume_fifo(defn, Decimal("6"))

        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None
        assert opener.offset_days == 0

    def test_discard_triggers_low_stock_opener(self, db_session: Session) -> None:
        """discard below min_stock immediately creates a low-stock opener."""
        hh, user, defn, inst = self._seed_for_movement(db_session)
        svc = self._make_svc(db_session, user, hh)

        # Discard enough to go below min_stock=5 (current=10, discard 7 -> 3 < 5)
        svc.discard(inst, Decimal("7"))

        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None

    def test_adjust_up_closes_episode(self, db_session: Session) -> None:
        """adjust upward closes an open low-stock episode."""
        hh, user, defn, inst = self._seed_for_movement(db_session)
        svc = self._make_svc(db_session, user, hh)

        # Go low: consume to 4
        svc.consume_fifo(defn, Decimal("6"))

        from app.models.notification import Notification
        from app.repositories.notification import NotificationRepository

        repo = NotificationRepository(db_session)
        opener = repo.open_low_stock_opener(user.id, defn.id)
        assert opener is not None

        # Adjust back up above min_stock
        db_session.refresh(inst)
        svc.adjust(inst, Decimal("10"))

        db_session.expire_all()
        opener_row = db_session.get(Notification, opener.id)
        assert opener_row is not None
        assert opener_row.resolved_at is not None  # Episode closed

    def test_consume_fifo_event_and_scan_no_duplicate(self, db_session: Session) -> None:
        """consume_fifo event fires opener; subsequent scan is idempotent (no duplicate)."""
        hh, user, defn, inst = self._seed_for_movement(db_session)
        svc = self._make_svc(db_session, user, hh)

        # Consume to go low (event fires)
        svc.consume_fifo(defn, Decimal("6"))

        from app.models.notification import Notification
        from app.services.reminder_engine import ReminderEngine

        today = date(2025, 6, 1)
        summary = ReminderEngine(db_session).run_scan(today_local=today)
        assert summary.low_stock == 0  # already exists from event

        rows = (
            db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.source == "low_stock",
                    Notification.subject_id == defn.id,
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    def test_event_hook_best_effort_movement_succeeds_on_engine_error(
        self, db_session: Session
    ) -> None:
        """If evaluate_low_stock raises, the movement still succeeds (best-effort)."""
        from unittest.mock import patch

        hh, user, defn, inst = self._seed_for_movement(db_session)
        svc = self._make_svc(db_session, user, hh)

        from app.services import reminder_engine as re_module

        # Make the engine raise an exception during evaluate_low_stock
        with patch.object(
            re_module.ReminderEngine, "evaluate_low_stock", side_effect=RuntimeError("boom")
        ):
            # The consume should succeed despite the hook failure
            touched = svc.consume_fifo(defn, Decimal("6"))
            assert len(touched) == 1

        # Verify the movement was committed to the session (quantity updated)
        db_session.expire_all()
        from app.models.stock_instance import StockInstance

        updated = db_session.get(StockInstance, inst.id)
        assert updated is not None
        assert updated.quantity == Decimal("4")  # 10 - 6


# ---------------------------------------------------------------------------
# 7. Multi-recipient fan-out for low-stock
# ---------------------------------------------------------------------------


class TestLowStockMultiRecipient:
    def test_two_active_users_each_get_opener(self, db_session: Session) -> None:
        """Each active user gets their own low-stock opener row."""
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
        db_session.flush()

        kind = ItemKind(code="c", name="C", is_system=True)
        db_session.add(kind)
        db_session.flush()

        u1 = User(email="u1@x.com", password_hash=hash_password("p"), is_active=True)
        u2 = User(email="u2@x.com", password_hash=hash_password("p"), is_active=True)
        u_inactive = User(email="u3@x.com", password_hash=hash_password("p"), is_active=False)
        db_session.add_all([u1, u2, u_inactive])
        db_session.flush()

        defn = ItemDefinition(
            name="D", kind_id=kind.id, stock_tracking_mode="exact", min_stock=Decimal("5")
        )
        db_session.add(defn)
        db_session.flush()

        db_session.add(StockInstance(definition_id=defn.id, quantity=Decimal("3")))
        db_session.flush()
        db_session.commit()

        today = date(2025, 6, 1)
        summary = ReminderEngine(db_session).run_scan(today_local=today)
        # 2 active users -> 2 openers
        assert summary.low_stock == 2

        repo = NotificationRepository(db_session)
        assert repo.open_low_stock_opener(u1.id, defn.id) is not None
        assert repo.open_low_stock_opener(u2.id, defn.id) is not None
        assert repo.open_low_stock_opener(u_inactive.id, defn.id) is None
