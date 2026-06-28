"""Tests for M7 Step 2: auto-reconcile shopping list from low stock.

Required coverage (M7.md §5 / §9 Step 2 / §10 Step 2):

Reconcile algorithm (§4.3)
- open-one-per-low: a low definition with no auto row → one created
- idempotent re-run: re-running creates nothing (partial-unique backstop)
- recovery prunes open unchecked auto rows (definition no longer low)
- manual row untouched on recovery
- checked auto row survives recovery (not pruned)
- checked auto row blocks a duplicate open row for the same definition
- check-off → uncheck round-trip never collides (state-independent per-def uniqueness)
- gate off (auto_add_low_stock=false) → no-op, nothing created
- level-mode low definition → auto row with NULL desired_quantity

Settings
- auto_add_low_stock defaults to True (surfaced in GET /settings)
- PATCH /settings can toggle auto_add_low_stock; change is round-tripped

Refresh endpoint
- POST /shopping-list/refresh forces reconcile and returns the open list
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# In-memory session infrastructure (same pattern as test_m4_step4.py)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, Any]:
    """Create a fresh in-memory SQLite session with all models registered."""
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
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.shopping_list_item as sli_mod
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
        audit_log_mod,
        sli_mod,
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


# ---------------------------------------------------------------------------
# DB-level seed helpers
# ---------------------------------------------------------------------------


def _seed_exact_low(
    db: Session,
    *,
    min_stock: Decimal = Decimal("5"),
    quantity: Decimal = Decimal("3"),
    name: str = "Coffee",
) -> tuple[Any, Any, Any, Any]:
    """Seed Household, User, ItemKind, ItemDefinition (exact, below min_stock) + StockInstance.

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
        name=name,
        kind_id=kind.id,
        stock_tracking_mode="exact",
        min_stock=min_stock,
    )
    db.add(defn)
    db.flush()

    inst = StockInstance(definition_id=defn.id, quantity=quantity)
    db.add(inst)
    db.flush()
    db.commit()

    return hh, user, defn, inst


def _seed_level_low(db: Session) -> tuple[Any, Any, Any, Any]:
    """Seed Household, User, ItemKind, ItemDefinition (level-mode) + low StockInstance.

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

    inst = StockInstance(definition_id=defn.id, stock_level="low")
    db.add(inst)
    db.flush()
    db.commit()

    return hh, user, defn, inst


def _count_auto_rows(db: Session, definition_id: int) -> int:
    """Return the number of shopping_list_items rows with source='auto' for definition."""
    from sqlalchemy import func, select

    from app.models.shopping_list_item import ShoppingListItem

    stmt = (
        select(func.count())
        .select_from(ShoppingListItem)
        .where(
            ShoppingListItem.source == "auto",
            ShoppingListItem.definition_id == definition_id,
        )
    )
    return db.execute(stmt).scalar_one()


def _count_all_rows(db: Session) -> int:
    """Return the total number of rows in shopping_list_items."""
    from sqlalchemy import func, select

    from app.models.shopping_list_item import ShoppingListItem

    stmt = select(func.count()).select_from(ShoppingListItem)
    return db.execute(stmt).scalar_one()


# ---------------------------------------------------------------------------
# TestClient infrastructure (mirrors test_m7_step1.py for API-level tests)
# ---------------------------------------------------------------------------


def _reload_all_models() -> None:
    """Reload model modules to pick up fresh DB engine after monkeypatch."""
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


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB; patches DATABASE_URL so get_engine() uses it."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m7_step2_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m7-step2")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _seed_kinds(engine: Any) -> None:
    """Seed item kinds (required by item definitions)."""
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


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, Any]]:
    """TestClient + engine with schema created but no users."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_kinds(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


def _create_user_and_login(
    engine: Any,
    client: TestClient,
    email: str,
    password: str,
    role: str = "admin",
) -> None:
    """Insert a user with the given role and log in."""
    from sqlalchemy.orm import sessionmaker as SM

    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        repo = UserRepository(db)
        repo.create(email=email, password_hash=hash_password(password), role=role)
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.json()}"


@pytest.fixture()
def admin_client(base_client: tuple[TestClient, Any]) -> tuple[TestClient, Any]:
    """TestClient + engine authenticated as an admin user."""
    client, engine = base_client
    _create_user_and_login(engine, client, "admin@test.com", "adminpass", "admin")
    return client, engine


def _create_definition(
    client: TestClient,
    *,
    name: str = "Widget",
    unit: str = "pcs",
    tracking_mode: str = "exact",
    min_stock: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "unit": unit,
        "stock_tracking_mode": tracking_mode,
    }
    if min_stock is not None:
        payload["min_stock"] = min_stock
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient, definition_id: int, location_id: int | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"definition_id": definition_id}
    if location_id is not None:
        payload["location_id"] = location_id
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == 201, f"create_instance failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_location(client: TestClient, name: str = "Pantry") -> dict[str, Any]:
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, f"create_location failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _intake(client: TestClient, instance_id: int, quantity: str) -> None:
    resp = client.post(f"/api/instances/{instance_id}/intake", json={"quantity": quantity})
    assert resp.status_code == 200, f"intake failed: {resp.json()}"


def _add_manual_item(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/api/shopping-list", json=payload)
    assert resp.status_code == 201, f"add_manual_item failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _check_off(client: TestClient, item_id: int) -> dict[str, Any]:
    resp = client.post(f"/api/shopping-list/{item_id}/check")
    assert resp.status_code == 200, f"check_off failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _uncheck(client: TestClient, item_id: int) -> dict[str, Any]:
    resp = client.post(f"/api/shopping-list/{item_id}/uncheck")
    assert resp.status_code == 200, f"uncheck failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _list_items(client: TestClient, include_purchased: bool = False) -> list[dict[str, Any]]:
    params = {"include_purchased": "true" if include_purchased else "false"}
    resp = client.get("/api/shopping-list", params=params)
    assert resp.status_code == 200
    return resp.json()  # type: ignore[return-value]


def _refresh(client: TestClient) -> list[dict[str, Any]]:
    resp = client.post("/api/shopping-list/refresh")
    assert resp.status_code == 200, f"refresh failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Reconcile algorithm — unit tests (in-memory session)
# ---------------------------------------------------------------------------


class TestReconcileAlgorithm:
    """Direct service-level tests for reconcile_auto_items() (§4.3)."""

    def test_open_one_per_low(self, db_session: Session) -> None:
        """A low definition with no auto row → exactly one auto row is created."""
        _, _, defn, _ = _seed_exact_low(db_session)
        from app.services.shopping_list import ShoppingListService

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()

        assert _count_auto_rows(db_session, defn.id) == 1

    def test_auto_row_has_null_desired_quantity_for_exact_mode(self, db_session: Session) -> None:
        """Auto rows always carry NULL desired_quantity (M7 §4.3 — "desired_quantity=None")."""
        _, _, defn, _ = _seed_exact_low(db_session)
        from sqlalchemy import select

        from app.models.shopping_list_item import ShoppingListItem
        from app.services.shopping_list import ShoppingListService

        ShoppingListService(db_session).reconcile_auto_items()
        db_session.flush()

        row = db_session.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.source == "auto",
                ShoppingListItem.definition_id == defn.id,
            )
        ).scalar_one()
        assert row.desired_quantity is None

    def test_idempotent_rerun(self, db_session: Session) -> None:
        """Running reconcile twice creates exactly one auto row, not two."""
        _, _, defn, _ = _seed_exact_low(db_session)
        from app.services.shopping_list import ShoppingListService

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()
        svc.reconcile_auto_items()  # second run — must not insert a duplicate
        db_session.flush()

        assert _count_auto_rows(db_session, defn.id) == 1
        assert _count_all_rows(db_session) == 1

    def test_recovery_prunes_open_unchecked_auto_row(self, db_session: Session) -> None:
        """When the definition recovers above min_stock, the open auto row is pruned."""
        _, _, defn, inst = _seed_exact_low(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        from app.services.shopping_list import ShoppingListService

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()
        assert _count_auto_rows(db_session, defn.id) == 1

        # Simulate recovery: raise quantity above min_stock.
        inst.quantity = Decimal("10")
        db_session.flush()
        db_session.commit()

        svc.reconcile_auto_items()
        db_session.flush()

        # Open auto row for the recovered definition must be gone.
        assert _count_auto_rows(db_session, defn.id) == 0
        assert _count_all_rows(db_session) == 0

    def test_manual_row_untouched_on_recovery(self, db_session: Session) -> None:
        """A manual shopping-list row is never pruned by reconcile, even on recovery."""
        _, _, defn, inst = _seed_exact_low(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        from app.models.shopping_list_item import ShoppingListItem
        from app.repositories.shopping_list import ShoppingListRepository
        from app.services.shopping_list import ShoppingListService

        # Create a manual row for the same definition.
        repo = ShoppingListRepository(db_session)
        repo.create(source="manual", definition_id=defn.id, desired_quantity=Decimal("2"))
        db_session.flush()

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()

        # Simulate recovery.
        inst.quantity = Decimal("10")
        db_session.flush()
        db_session.commit()

        svc.reconcile_auto_items()
        db_session.flush()

        # Manual row must survive; auto row for recovered def must be gone.
        from sqlalchemy import select

        rows = list(db_session.execute(select(ShoppingListItem)).scalars().all())
        assert len(rows) == 1, f"Expected 1 manual row, got {len(rows)}"
        assert rows[0].source == "manual"

    def test_checked_auto_row_survives_recovery(self, db_session: Session) -> None:
        """A checked (purchased) auto row is NOT pruned when the definition recovers."""
        _, _, defn, inst = _seed_exact_low(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        from app.repositories.shopping_list import ShoppingListRepository
        from app.services.shopping_list import ShoppingListService

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()

        # Check off the auto row.
        repo = ShoppingListRepository(db_session)
        auto_row = repo.get_auto_item(defn.id)
        assert auto_row is not None
        from datetime import UTC, datetime

        repo.stamp_purchased(auto_row, datetime.now(tz=UTC))
        db_session.flush()

        # Simulate recovery.
        inst.quantity = Decimal("10")
        db_session.flush()
        db_session.commit()

        # Reconcile prunes OPEN unchecked auto rows only.
        svc.reconcile_auto_items()
        db_session.flush()

        # The checked auto row must still exist (1 row total).
        assert _count_auto_rows(db_session, defn.id) == 1
        assert _count_all_rows(db_session) == 1

    def test_checked_auto_row_blocks_duplicate_open_row(self, db_session: Session) -> None:
        """A checked auto row blocks reconcile from creating a duplicate open row.

        This verifies the state-independent uniqueness: even though the checked
        auto row is not 'open', it still counts as the existing auto row for its
        definition and prevents a second row from being inserted.
        """
        _, _, defn, inst = _seed_exact_low(
            db_session, min_stock=Decimal("5"), quantity=Decimal("3")
        )
        from app.repositories.shopping_list import ShoppingListRepository
        from app.services.shopping_list import ShoppingListService

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()

        # Check off the auto row (definition still low).
        repo = ShoppingListRepository(db_session)
        auto_row = repo.get_auto_item(defn.id)
        assert auto_row is not None
        from datetime import UTC, datetime

        repo.stamp_purchased(auto_row, datetime.now(tz=UTC))
        db_session.flush()

        # Run reconcile again while definition is STILL low.
        # The checked auto row must block a second row from being created.
        svc.reconcile_auto_items()
        db_session.flush()

        # Still exactly 1 auto row (the checked one), not 2.
        assert _count_auto_rows(db_session, defn.id) == 1
        assert _count_all_rows(db_session) == 1

    def test_check_off_uncheck_round_trip_no_collision(self, db_session: Session) -> None:
        """Check → uncheck → reconcile never produces a duplicate auto row.

        The partial-unique index is state-independent (WHERE source='auto'), so
        clearing purchased_at can never create a second auto row and reconcile
        must not add one either (the one unchecked row IS the auto row).
        """
        _, _, defn, _ = _seed_exact_low(db_session, min_stock=Decimal("5"), quantity=Decimal("3"))
        from app.repositories.shopping_list import ShoppingListRepository
        from app.services.shopping_list import ShoppingListService

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()

        # Check → uncheck the auto row.
        repo = ShoppingListRepository(db_session)
        auto_row = repo.get_auto_item(defn.id)
        assert auto_row is not None
        from datetime import UTC, datetime

        repo.stamp_purchased(auto_row, datetime.now(tz=UTC))
        db_session.flush()
        repo.clear_purchased_at(auto_row)
        db_session.flush()

        # Run reconcile again (definition is still low after uncheck).
        svc.reconcile_auto_items()
        db_session.flush()

        # Must still be exactly 1 auto row — no collision from the round-trip.
        assert _count_auto_rows(db_session, defn.id) == 1
        assert _count_all_rows(db_session) == 1

    def test_gate_off_no_op(self, db_session: Session) -> None:
        """When auto_add_low_stock=false the reconcile does nothing (no rows created)."""
        _, _, defn, _ = _seed_exact_low(db_session)
        from app.models.setting import Setting
        from app.services.shopping_list import ShoppingListService

        # Disable the setting directly in the DB.
        db_session.add(Setting(key="shopping_list.auto_add_low_stock", value="false"))
        db_session.flush()

        svc = ShoppingListService(db_session)
        svc.reconcile_auto_items()
        db_session.flush()

        # No auto row should have been created.
        assert _count_auto_rows(db_session, defn.id) == 0
        assert _count_all_rows(db_session) == 0

    def test_level_mode_auto_row_with_null_quantity(self, db_session: Session) -> None:
        """Level-mode low definition → auto row is created with NULL desired_quantity."""
        _, _, defn, _ = _seed_level_low(db_session)
        from sqlalchemy import select

        from app.models.shopping_list_item import ShoppingListItem
        from app.services.shopping_list import ShoppingListService

        ShoppingListService(db_session).reconcile_auto_items()
        db_session.flush()

        assert _count_auto_rows(db_session, defn.id) == 1

        row = db_session.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.source == "auto",
                ShoppingListItem.definition_id == defn.id,
            )
        ).scalar_one()
        assert row.desired_quantity is None  # NULL = "buy some" — no exact qty for level mode

    def test_not_low_definition_produces_no_auto_row(self, db_session: Session) -> None:
        """A definition above its min_stock threshold does not get an auto row."""
        _, _, defn, _ = _seed_exact_low(
            db_session,
            min_stock=Decimal("5"),
            quantity=Decimal("10"),  # above threshold
        )
        from app.services.shopping_list import ShoppingListService

        ShoppingListService(db_session).reconcile_auto_items()
        db_session.flush()

        assert _count_auto_rows(db_session, defn.id) == 0

    def test_multiple_low_definitions_each_get_one_row(self, db_session: Session) -> None:
        """Multiple low definitions each get exactly one auto row."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.services.shopping_list import ShoppingListService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="consumable", name="Consumable", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="admin@example.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        def1 = ItemDefinition(
            name="Coffee", kind_id=kind.id, stock_tracking_mode="exact", min_stock=Decimal("5")
        )
        def2 = ItemDefinition(
            name="Tea", kind_id=kind.id, stock_tracking_mode="exact", min_stock=Decimal("10")
        )
        db_session.add_all([def1, def2])
        db_session.flush()

        db_session.add(StockInstance(definition_id=def1.id, quantity=Decimal("2")))  # below 5
        db_session.add(StockInstance(definition_id=def2.id, quantity=Decimal("3")))  # below 10
        db_session.flush()
        db_session.commit()

        ShoppingListService(db_session).reconcile_auto_items()
        db_session.flush()

        assert _count_auto_rows(db_session, def1.id) == 1
        assert _count_auto_rows(db_session, def2.id) == 1
        assert _count_all_rows(db_session) == 2


# ---------------------------------------------------------------------------
# 2. Settings: auto_add_low_stock (unit tests)
# ---------------------------------------------------------------------------


class TestSettingsAutoAddLowStock:
    """SettingsService accessor and schema round-trip for auto_add_low_stock."""

    def test_default_is_true(self, db_session: Session) -> None:
        """shopping_list_auto_add() returns True when no override is set."""
        from app.models.household import Household
        from app.services.settings import SettingsService

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        db_session.commit()

        assert SettingsService(db_session).shopping_list_auto_add() is True

    def test_can_be_set_to_false(self, db_session: Session) -> None:
        """Setting override to false makes shopping_list_auto_add() return False."""
        from app.models.household import Household
        from app.models.setting import Setting
        from app.services.settings import SettingsService

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        db_session.add(Setting(key="shopping_list.auto_add_low_stock", value="false"))
        db_session.flush()
        db_session.commit()

        assert SettingsService(db_session).shopping_list_auto_add() is False

    def test_get_settings_includes_shopping_list(self, db_session: Session) -> None:
        """SettingsService.get_settings() includes the shopping_list section."""
        from app.models.household import Household
        from app.services.settings import SettingsService

        db_session.add(Household(id=1, name="H", currency="USD", timezone="UTC"))
        db_session.flush()
        db_session.commit()

        resp = SettingsService(db_session).get_settings()
        assert resp.shopping_list.auto_add_low_stock is True


# ---------------------------------------------------------------------------
# 3. Settings API-level tests (TestClient)
# ---------------------------------------------------------------------------


class TestSettingsApi:
    """API-level tests for GET/PATCH /settings with shopping_list section."""

    def test_get_settings_has_shopping_list_section(
        self, admin_client: tuple[TestClient, Any]
    ) -> None:
        """GET /settings returns shopping_list.auto_add_low_stock (default true)."""
        client, _ = admin_client
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "shopping_list" in data
        assert data["shopping_list"]["auto_add_low_stock"] is True

    def test_patch_auto_add_low_stock_to_false(self, admin_client: tuple[TestClient, Any]) -> None:
        """PATCH /settings with shopping_list.auto_add_low_stock=false persists the change."""
        client, _ = admin_client
        resp = client.patch(
            "/api/settings",
            json={"shopping_list": {"auto_add_low_stock": False}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["shopping_list"]["auto_add_low_stock"] is False

    def test_patch_auto_add_low_stock_round_trips(
        self, admin_client: tuple[TestClient, Any]
    ) -> None:
        """PATCH false then PATCH true round-trips correctly."""
        client, _ = admin_client
        # Disable
        resp = client.patch("/api/settings", json={"shopping_list": {"auto_add_low_stock": False}})
        assert resp.json()["shopping_list"]["auto_add_low_stock"] is False
        # Re-enable
        resp = client.patch("/api/settings", json={"shopping_list": {"auto_add_low_stock": True}})
        assert resp.json()["shopping_list"]["auto_add_low_stock"] is True


# ---------------------------------------------------------------------------
# 4. Refresh endpoint tests (TestClient)
# ---------------------------------------------------------------------------


class TestRefreshEndpoint:
    """POST /shopping-list/refresh tests."""

    def _setup_low_stock(
        self, client: TestClient, engine: Any, quantity: str = "2", min_stock: str = "5"
    ) -> dict[str, Any]:
        """Create a definition with min_stock, a location, an instance, and intake."""
        loc = _create_location(client)
        defn = _create_definition(client, name="Milk", tracking_mode="exact", min_stock=min_stock)
        inst = _create_instance(client, defn["id"], loc["id"])
        _intake(client, inst["id"], quantity)
        return defn

    def test_refresh_returns_auto_row_for_low_definition(
        self, admin_client: tuple[TestClient, Any]
    ) -> None:
        """POST /refresh creates an auto row for a low-stock definition and returns it."""
        client, engine = admin_client
        defn = self._setup_low_stock(client, engine, quantity="2", min_stock="5")

        items = _refresh(client)
        auto_items = [i for i in items if i["source"] == "auto"]
        assert len(auto_items) == 1
        assert auto_items[0]["definition_id"] == defn["id"]
        assert auto_items[0]["purchased_at"] is None  # open

    def test_refresh_returns_empty_list_when_nothing_low(
        self, admin_client: tuple[TestClient, Any]
    ) -> None:
        """POST /refresh returns empty list when no definitions are low."""
        client, engine = admin_client
        # Create a definition well ABOVE min_stock.
        self._setup_low_stock(client, engine, quantity="20", min_stock="5")

        items = _refresh(client)
        auto_items = [i for i in items if i["source"] == "auto"]
        assert auto_items == []

    def test_refresh_idempotent(self, admin_client: tuple[TestClient, Any]) -> None:
        """POST /refresh called twice yields exactly one auto row (idempotent)."""
        client, engine = admin_client
        defn = self._setup_low_stock(client, engine, quantity="2", min_stock="5")

        _refresh(client)
        items = _refresh(client)
        auto_items = [
            i for i in items if i["source"] == "auto" and i["definition_id"] == defn["id"]
        ]
        assert len(auto_items) == 1

    def test_refresh_prunes_recovered_auto_row(self, admin_client: tuple[TestClient, Any]) -> None:
        """POST /refresh removes the auto row when the definition recovers above threshold."""
        client, engine = admin_client
        loc = _create_location(client)
        defn = _create_definition(client, name="Sugar", tracking_mode="exact", min_stock="5")
        inst = _create_instance(client, defn["id"], loc["id"])
        _intake(client, inst["id"], "2")  # below threshold

        _refresh(client)
        items = _refresh(client)
        auto_items_before = [i for i in items if i["source"] == "auto"]
        assert len(auto_items_before) == 1

        # Intake more to go above min_stock.
        _intake(client, inst["id"], "10")

        items_after = _refresh(client)
        auto_items_after = [
            i for i in items_after if i["source"] == "auto" and i["definition_id"] == defn["id"]
        ]
        assert auto_items_after == []

    def test_refresh_only_returns_open_items(self, admin_client: tuple[TestClient, Any]) -> None:
        """POST /refresh returns only open (unchecked) items, not purchased ones."""
        client, engine = admin_client
        self._setup_low_stock(client, engine, quantity="2", min_stock="5")

        # Refresh to create the auto row.
        items = _refresh(client)
        auto_item = next(i for i in items if i["source"] == "auto")

        # Check off the auto item.
        _check_off(client, auto_item["id"])

        # Refresh again — should return empty open list
        # (the checked auto row is present but not returned).
        items_after_check = _refresh(client)
        open_auto = [i for i in items_after_check if i["source"] == "auto"]
        assert open_auto == []

    def test_refresh_requires_edit_permission(self, base_client: tuple[TestClient, Any]) -> None:
        """POST /shopping-list/refresh requires EDIT permission (viewer → 403)."""
        client, engine = base_client
        # Create admin and viewer users.
        _create_user_and_login(engine, client, "admin@test.com", "adminpass", "admin")
        # Log in as viewer (not possible via API in single-client; switch session).
        # Instead: log out then log in as viewer.
        client.post("/api/auth/logout")
        from sqlalchemy.orm import sessionmaker as SM

        from app.auth.passwords import hash_password
        from app.repositories.user import UserRepository

        factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
        db = factory()
        try:
            UserRepository(db).create(
                email="viewer@test.com", password_hash=hash_password("vpass"), role="viewer"
            )
            db.commit()
        finally:
            db.close()

        resp = client.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "vpass"}
        )
        assert resp.status_code == 200

        resp = client.post("/api/shopping-list/refresh")
        assert resp.status_code == 403

    def test_refresh_gate_off_returns_empty_no_auto_rows(
        self, admin_client: tuple[TestClient, Any]
    ) -> None:
        """When auto_add_low_stock=false, POST /refresh doesn't create auto rows."""
        client, engine = admin_client
        self._setup_low_stock(client, engine, quantity="2", min_stock="5")

        # Disable auto-add.
        resp = client.patch("/api/settings", json={"shopping_list": {"auto_add_low_stock": False}})
        assert resp.status_code == 200

        items = _refresh(client)
        auto_items = [i for i in items if i["source"] == "auto"]
        assert auto_items == []
