"""M2 Step 5 tests: computed low-stock endpoint.

Required coverage (per M2.md §9 Step 5 / §10 blind-review points / §5 "Backend"):

Repository helpers (StockInstanceRepository):
- sum_quantity_for_definition: returns sum of lot quantities as Decimal.
- sum_quantity_for_definition: returns Decimal("0") when no lots exist.
- sum_quantity_for_definition: NULL quantities (level/none lots) are skipped.
- definition_has_low_level_lot: True when any lot has stock_level='low'.
- definition_has_low_level_lot: False when no lots exist.
- definition_has_low_level_lot: False when lots exist but none are 'low'.

Service unit tests (LowStockService.compute):
- exact below min_stock → flagged with reason='below_min_stock', correct current/threshold.
- exact at min_stock (total == min_stock) → NOT flagged (strictly-below boundary; §12).
- exact above min_stock → not flagged.
- exact with min_stock=None → never flagged (no threshold set).
- level with a lot at 'low' → flagged with reason='level_low', current=None, threshold=None.
- level with no low lot → not flagged.
- level with all lots at 'high'/'medium' → not flagged.
- none mode → never flagged.
- Mixed set of definitions → returns the right subset with the right reasons + numbers.
- Decimal precision: current/threshold are Decimal, not float.

HTTP API (end-to-end via TestClient):
- GET /low-stock returns 200 + correct JSON list.
- GET /low-stock returns 401 when unauthenticated.
- GET /low-stock returns [] when nothing is low.
- Response shape matches LowStockItem (definition_id, name, mode, reason, current, threshold).
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    """Clear lru_cache on get_settings / get_engine before and after each test."""
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def db_session() -> Generator[Session]:
    """In-memory SQLite session with all models, FK enforcement ON, kinds seeded."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
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

    from app.models.item_kind import ItemKind

    for code, name in [
        ("durable", "Durable"),
        ("consumable", "Consumable"),
        ("perishable", "Perishable"),
    ]:
        session.add(ItemKind(code=code, name=name, is_system=True))
    session.commit()

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


def _seed_definition(
    session: Session,
    *,
    mode: str = "exact",
    name: str | None = None,
    min_stock: Decimal | None = None,
) -> object:
    """Seed an ItemDefinition with the given tracking mode and optional min_stock."""
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind

    kind = session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
    assert kind is not None
    defn = ItemDefinition(
        name=name or f"TestDef-{mode}",
        unit="pcs",
        kind_id=kind.id,
        stock_tracking_mode=mode,
        min_stock=min_stock,
    )
    session.add(defn)
    session.flush()
    return defn


def _seed_exact_lot(
    session: Session,
    definition_id: int,
    quantity: Decimal,
) -> object:
    """Seed an exact-mode stock instance with the given quantity via the ledger."""
    from app.models.stock_instance import StockInstance
    from app.repositories.stock_movement import StockMovementRepository
    from app.services.stock_instance import StockInstanceService

    inst = StockInstance(
        definition_id=definition_id,
        quantity=None,
    )
    session.add(inst)
    session.flush()

    repo = StockMovementRepository(session)
    repo.append(
        instance_id=inst.id,
        type="intake",
        quantity_delta=quantity,
    )
    svc = StockInstanceService(session)
    svc.recompute_quantity(inst)
    session.flush()
    return inst


def _seed_level_lot(
    session: Session,
    definition_id: int,
    stock_level: str,
) -> object:
    """Seed a level-mode stock instance with the given stock_level."""
    from app.models.stock_instance import StockInstance

    inst = StockInstance(
        definition_id=definition_id,
        quantity=None,
        stock_level=stock_level,
    )
    session.add(inst)
    session.flush()
    return inst


def _seed_none_lot(session: Session, definition_id: int) -> object:
    """Seed a none-mode stock instance (no quantity, no stock_level)."""
    from app.models.stock_instance import StockInstance

    inst = StockInstance(
        definition_id=definition_id,
        quantity=None,
        stock_level=None,
    )
    session.add(inst)
    session.flush()
    return inst


# ---------------------------------------------------------------------------
# 1. Repository helpers: sum_quantity_for_definition
# ---------------------------------------------------------------------------


class TestSumQuantityForDefinition:
    """StockInstanceRepository.sum_quantity_for_definition."""

    def test_returns_sum_of_lot_quantities(self, db_session: Session) -> None:
        """Sum across multiple lots returns the total as Decimal."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="exact")
        _seed_exact_lot(db_session, defn.id, Decimal("3"))
        _seed_exact_lot(db_session, defn.id, Decimal("7"))

        repo = StockInstanceRepository(db_session)
        total = repo.sum_quantity_for_definition(defn.id)

        assert total == Decimal("10")
        assert isinstance(total, Decimal)

    def test_returns_zero_when_no_lots(self, db_session: Session) -> None:
        """Definition with no lots returns Decimal('0'), not None."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="exact")

        repo = StockInstanceRepository(db_session)
        total = repo.sum_quantity_for_definition(defn.id)

        assert total == Decimal("0")
        assert isinstance(total, Decimal)

    def test_null_quantities_skipped(self, db_session: Session) -> None:
        """level/none lots with NULL quantity are skipped by SUM."""
        from app.repositories.stock_instance import StockInstanceRepository

        exact_defn = _seed_definition(db_session, mode="exact", name="ExactDef")
        level_defn = _seed_definition(db_session, mode="level", name="LevelDef")

        _seed_exact_lot(db_session, exact_defn.id, Decimal("5"))
        _seed_level_lot(db_session, level_defn.id, "low")

        repo = StockInstanceRepository(db_session)
        # Query exact_defn — should see 5, not 5+NULL
        assert repo.sum_quantity_for_definition(exact_defn.id) == Decimal("5")
        # Query level_defn — NULL quantities sum to 0
        assert repo.sum_quantity_for_definition(level_defn.id) == Decimal("0")

    def test_decimal_precision_preserved(self, db_session: Session) -> None:
        """Decimal precision is not lost through the SUM."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="exact")
        _seed_exact_lot(db_session, defn.id, Decimal("1.5"))
        _seed_exact_lot(db_session, defn.id, Decimal("2.25"))

        repo = StockInstanceRepository(db_session)
        total = repo.sum_quantity_for_definition(defn.id)

        assert total == Decimal("3.75")
        assert isinstance(total, Decimal)


# ---------------------------------------------------------------------------
# 2. Repository helpers: definition_has_low_level_lot
# ---------------------------------------------------------------------------


class TestDefinitionHasLowLevelLot:
    """StockInstanceRepository.definition_has_low_level_lot."""

    def test_true_when_lot_is_low(self, db_session: Session) -> None:
        """Returns True when at least one lot has stock_level='low'."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="level")
        _seed_level_lot(db_session, defn.id, "low")

        repo = StockInstanceRepository(db_session)
        assert repo.definition_has_low_level_lot(defn.id) is True

    def test_false_when_no_lots(self, db_session: Session) -> None:
        """Returns False when the definition has no lots at all."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="level")

        repo = StockInstanceRepository(db_session)
        assert repo.definition_has_low_level_lot(defn.id) is False

    def test_false_when_no_low_lots(self, db_session: Session) -> None:
        """Returns False when lots exist but none are 'low'."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="level")
        _seed_level_lot(db_session, defn.id, "high")
        _seed_level_lot(db_session, defn.id, "medium")

        repo = StockInstanceRepository(db_session)
        assert repo.definition_has_low_level_lot(defn.id) is False

    def test_true_when_mixed_levels_include_low(self, db_session: Session) -> None:
        """Returns True when at least one lot is 'low' even if others aren't."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="level")
        _seed_level_lot(db_session, defn.id, "high")
        _seed_level_lot(db_session, defn.id, "low")

        repo = StockInstanceRepository(db_session)
        assert repo.definition_has_low_level_lot(defn.id) is True


# ---------------------------------------------------------------------------
# 3. LowStockService.compute — per-mode rules
# ---------------------------------------------------------------------------


class TestLowStockServiceExact:
    """LowStockService.compute — exact mode rules."""

    def test_exact_below_min_stock_is_flagged(self, db_session: Session) -> None:
        """exact: total < min_stock → flagged with correct reason + numbers."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", name="Batteries", min_stock=Decimal("4"))
        _seed_exact_lot(db_session, defn.id, Decimal("3"))  # 3 < 4 → flagged

        svc = LowStockService(db_session)
        results = svc.compute()

        assert len(results) == 1
        item = results[0]
        assert item.definition_id == defn.id
        assert item.name == "Batteries"
        assert item.mode == "exact"
        assert item.reason == "below_min_stock"
        assert item.current == Decimal("3")
        assert item.threshold == Decimal("4")

    def test_exact_at_min_stock_not_flagged(self, db_session: Session) -> None:
        """exact: total == min_stock → NOT flagged (strictly-below boundary; M2 §12)."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=Decimal("4"))
        _seed_exact_lot(db_session, defn.id, Decimal("4"))  # 4 == 4 → not flagged

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_exact_above_min_stock_not_flagged(self, db_session: Session) -> None:
        """exact: total > min_stock → not flagged."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=Decimal("4"))
        _seed_exact_lot(db_session, defn.id, Decimal("10"))  # 10 > 4 → not flagged

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_exact_no_min_stock_never_flagged(self, db_session: Session) -> None:
        """exact with min_stock=None → never flagged regardless of quantity."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=None)
        _seed_exact_lot(db_session, defn.id, Decimal("0"))  # zero stock, no threshold

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_exact_no_lots_no_min_stock_not_flagged(self, db_session: Session) -> None:
        """exact with no lots and no min_stock → not flagged."""
        from app.services.low_stock import LowStockService

        _seed_definition(db_session, mode="exact", min_stock=None)

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_exact_current_and_threshold_are_decimal(self, db_session: Session) -> None:
        """current and threshold fields are Decimal, never float (roadmap §2.9)."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=Decimal("4.500000"))
        _seed_exact_lot(db_session, defn.id, Decimal("1.250000"))

        svc = LowStockService(db_session)
        results = svc.compute()

        assert len(results) == 1
        item = results[0]
        assert isinstance(item.current, Decimal)
        assert isinstance(item.threshold, Decimal)
        assert item.current == Decimal("1.25")
        assert item.threshold == Decimal("4.5")

    def test_exact_multi_lot_sum_below_threshold(self, db_session: Session) -> None:
        """exact: SUM across multiple lots is compared to min_stock."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=Decimal("10"))
        _seed_exact_lot(db_session, defn.id, Decimal("3"))
        _seed_exact_lot(db_session, defn.id, Decimal("4"))
        # total = 7 < 10 → flagged

        svc = LowStockService(db_session)
        results = svc.compute()

        assert len(results) == 1
        assert results[0].current == Decimal("7")

    def test_exact_multi_lot_sum_at_threshold(self, db_session: Session) -> None:
        """exact: SUM across multiple lots == min_stock → NOT flagged."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=Decimal("10"))
        _seed_exact_lot(db_session, defn.id, Decimal("6"))
        _seed_exact_lot(db_session, defn.id, Decimal("4"))
        # total = 10 == 10 → not flagged

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []


class TestLowStockServiceLevel:
    """LowStockService.compute — level mode rules."""

    def test_level_with_low_lot_is_flagged(self, db_session: Session) -> None:
        """level: a lot at 'low' → flagged with reason='level_low'."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="level", name="Screws")
        _seed_level_lot(db_session, defn.id, "low")

        svc = LowStockService(db_session)
        results = svc.compute()

        assert len(results) == 1
        item = results[0]
        assert item.definition_id == defn.id
        assert item.name == "Screws"
        assert item.mode == "level"
        assert item.reason == "level_low"
        assert item.current is None
        assert item.threshold is None

    def test_level_no_low_lot_not_flagged(self, db_session: Session) -> None:
        """level: no lot at 'low' → not flagged."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="level")
        _seed_level_lot(db_session, defn.id, "high")
        _seed_level_lot(db_session, defn.id, "medium")

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_level_no_lots_not_flagged(self, db_session: Session) -> None:
        """level: no lots at all → not flagged."""
        from app.services.low_stock import LowStockService

        _seed_definition(db_session, mode="level")

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_level_mixed_levels_only_one_low(self, db_session: Session) -> None:
        """level: at least one lot at 'low' among others → flagged once."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="level")
        _seed_level_lot(db_session, defn.id, "high")
        _seed_level_lot(db_session, defn.id, "low")

        svc = LowStockService(db_session)
        results = svc.compute()

        assert len(results) == 1  # flagged once, not twice


class TestLowStockServiceNone:
    """LowStockService.compute — none mode is never flagged."""

    def test_none_mode_never_flagged(self, db_session: Session) -> None:
        """none: even with lots, the definition is never included."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="none")
        _seed_none_lot(db_session, defn.id)

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []

    def test_none_mode_no_lots_not_flagged(self, db_session: Session) -> None:
        """none: no lots → not flagged."""
        from app.services.low_stock import LowStockService

        _seed_definition(db_session, mode="none")

        svc = LowStockService(db_session)
        results = svc.compute()

        assert results == []


class TestLowStockServiceMixed:
    """LowStockService.compute — mixed set of definitions."""

    def test_mixed_returns_only_low_definitions(self, db_session: Session) -> None:
        """Mixed set: only the truly-low definitions appear; the rest are absent."""
        from app.services.low_stock import LowStockService

        # exact, below threshold → FLAGGED
        exact_low = _seed_definition(
            db_session, mode="exact", name="Batteries", min_stock=Decimal("4")
        )
        _seed_exact_lot(db_session, exact_low.id, Decimal("2"))

        # exact, at threshold → NOT flagged
        exact_ok = _seed_definition(db_session, mode="exact", name="Bulbs", min_stock=Decimal("5"))
        _seed_exact_lot(db_session, exact_ok.id, Decimal("5"))

        # exact, no threshold → NOT flagged
        exact_no_threshold = _seed_definition(
            db_session, mode="exact", name="Cables", min_stock=None
        )
        _seed_exact_lot(db_session, exact_no_threshold.id, Decimal("0"))

        # level, has a low lot → FLAGGED
        level_low = _seed_definition(db_session, mode="level", name="Screws")
        _seed_level_lot(db_session, level_low.id, "low")

        # level, no low lots → NOT flagged
        level_ok = _seed_definition(db_session, mode="level", name="Bolts")
        _seed_level_lot(db_session, level_ok.id, "high")

        # none → NOT flagged
        none_defn = _seed_definition(db_session, mode="none", name="Wall Art")
        _seed_none_lot(db_session, none_defn.id)

        svc = LowStockService(db_session)
        results = svc.compute()

        flagged_ids = {r.definition_id for r in results}
        assert exact_low.id in flagged_ids
        assert level_low.id in flagged_ids
        assert exact_ok.id not in flagged_ids
        assert exact_no_threshold.id not in flagged_ids
        assert level_ok.id not in flagged_ids
        assert none_defn.id not in flagged_ids
        assert len(results) == 2

    def test_mixed_reasons_are_correct(self, db_session: Session) -> None:
        """Each flagged item carries the right reason string."""
        from app.services.low_stock import LowStockService

        exact_defn = _seed_definition(
            db_session, mode="exact", name="ExactLow", min_stock=Decimal("10")
        )
        _seed_exact_lot(db_session, exact_defn.id, Decimal("1"))

        level_defn = _seed_definition(db_session, mode="level", name="LevelLow")
        _seed_level_lot(db_session, level_defn.id, "low")

        svc = LowStockService(db_session)
        results = svc.compute()

        by_id = {r.definition_id: r for r in results}
        assert by_id[exact_defn.id].reason == "below_min_stock"
        assert by_id[level_defn.id].reason == "level_low"

    def test_empty_when_nothing_is_low(self, db_session: Session) -> None:
        """Returns empty list when all definitions are sufficiently stocked."""
        from app.services.low_stock import LowStockService

        defn = _seed_definition(db_session, mode="exact", min_stock=Decimal("5"))
        _seed_exact_lot(db_session, defn.id, Decimal("10"))

        svc = LowStockService(db_session)
        assert svc.compute() == []


# ---------------------------------------------------------------------------
# 4. HTTP API (end-to-end via TestClient)
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m2step5_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


@pytest.fixture()
def temp_db_step5(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests (Step 5)."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m2-step5")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db_step5: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema + authenticated admin session (Step 5)."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
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


@pytest.fixture()
def http_client_no_auth(temp_db_step5: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient without any authentication (for 401 tests)."""
    from fastapi.testclient import TestClient

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
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
        audit_log_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    application = create_app()

    with TestClient(application, raise_server_exceptions=True) as client:
        yield client

    drop_all_sqlite(Base, engine)


class TestLowStockEndpoint:
    """GET /low-stock HTTP API tests."""

    def test_unauthenticated_returns_401(self, http_client_no_auth: object) -> None:
        """GET /low-stock without a session cookie returns 401."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client_no_auth, TestClient)
        resp = http_client_no_auth.get("/api/low-stock")
        assert resp.status_code == 401

    def test_empty_when_nothing_low(self, http_client: object) -> None:
        """GET /low-stock returns [] when no definitions are low."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/low-stock")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_flagged_exact_definition(self, http_client: object) -> None:
        """GET /low-stock returns a flagged exact-mode definition with correct shape."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        # Create an exact-mode definition with min_stock via the API.
        kind_resp = http_client.get("/api/kinds")
        assert kind_resp.status_code == 200
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "consumable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "AA Batteries",
                "kind_id": kind_id,
                "unit": "pcs",
                "stock_tracking_mode": "exact",
                "min_stock": "4",
            },
        )
        assert defn_resp.status_code == 201
        defn_id = defn_resp.json()["id"]

        # Create a lot with quantity 3 (< min_stock=4).
        inst_resp = http_client.post(
            "/api/instances",
            json={
                "definition_id": defn_id,
                "quantity": "3",
            },
        )
        assert inst_resp.status_code == 201

        resp = http_client.get("/api/low-stock")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 1
        item = data[0]
        assert item["definition_id"] == defn_id
        assert item["name"] == "AA Batteries"
        assert item["mode"] == "exact"
        assert item["reason"] == "below_min_stock"
        # Wire format: Decimal serialised as string; SQLite Numeric(18,6) may
        # produce trailing zeros (e.g. "3.000000") — compare as Decimal.
        assert Decimal(item["current"]) == Decimal("3")
        assert Decimal(item["threshold"]) == Decimal("4")

    def test_returns_flagged_level_definition(self, http_client: object) -> None:
        """GET /low-stock returns a flagged level-mode definition with null current/threshold."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        kind_resp = http_client.get("/api/kinds")
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "consumable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "Assorted Screws",
                "kind_id": kind_id,
                "unit": "bag",
                "stock_tracking_mode": "level",
            },
        )
        assert defn_resp.status_code == 201
        defn_id = defn_resp.json()["id"]

        http_client.post(
            "/api/instances",
            json={
                "definition_id": defn_id,
                "stock_level": "low",
            },
        )

        resp = http_client.get("/api/low-stock")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 1
        item = data[0]
        assert item["definition_id"] == defn_id
        assert item["mode"] == "level"
        assert item["reason"] == "level_low"
        assert item["current"] is None
        assert item["threshold"] is None

    def test_exact_at_threshold_not_in_response(self, http_client: object) -> None:
        """GET /low-stock: exact at min_stock (total == min_stock) → not returned."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        kind_resp = http_client.get("/api/kinds")
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "consumable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "Threshold Item",
                "kind_id": kind_id,
                "unit": "pcs",
                "stock_tracking_mode": "exact",
                "min_stock": "5",
            },
        )
        assert defn_resp.status_code == 201
        defn_id = defn_resp.json()["id"]

        # Exactly at threshold → not low.
        http_client.post(
            "/api/instances",
            json={"definition_id": defn_id, "quantity": "5"},
        )

        resp = http_client.get("/api/low-stock")
        assert resp.status_code == 200
        assert resp.json() == []
